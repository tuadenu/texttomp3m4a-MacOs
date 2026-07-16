"""Generic HSK 2.0/3.0 stage-pack and catalog-revision primitives.

This module is intentionally independent of the HSK 2.0 importer.  Staging
ZIP packs and publishing a catalog revision are separate, explicitly
confirmed operations.  Network access is injected by the UI only after user
confirmation; unit tests use ``MemoryStorageClient``.
"""

from __future__ import annotations

import copy
import base64
import hashlib
import json
import os
import re
import ssl
import tempfile
import time
import uuid
import zipfile
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Mapping, Protocol
from urllib import error as urlerror
from urllib import parse as urlparse
from urllib import request as urlrequest

from pipelines.vocab_zip_builder import SUPPORTED_VERSIONS, sha256_file, verify_pack, verify_pack_pair, verify_pack_parts


SUPPORTED_LEVELS = ("hsk1", "hsk2", "hsk3", "hsk4", "hsk5", "hsk6", "hsk7_9")
STAGING_BUCKET = "vocab-pack-staging"
STANDARD_VERSION = "3.0"  # compatibility default for callers that omit version
PACK_VERSION = 1
SEED_CATALOG_PATH = "input/vocab_pack_catalog_all_enabled_rollout.json"
SEED_CATALOG_BYTES = 6277
SEED_CATALOG_SHA256 = "18cd2d70a0c90187bf32e7fb21c0249db6b6bc64bbc7207385d04ee2c0ef8c98"
CATALOG_PUBLISH_CONFIRMATION = "PUBLISH VOCAB CATALOG"


class DeployValidationError(ValueError):
    """A local stage/catalog validation failure."""


class StorageNotFound(FileNotFoundError):
    def __init__(self, path: str, http_status: int = 404):
        super().__init__(path)
        self.path = path
        self.http_status = http_status


class StorageConflict(RuntimeError):
    pass


class PartialDeployError(DeployValidationError):
    """One or more packs may have staged; no catalog was published."""

    def __init__(self, message: str, completed_objects: list[str]):
        super().__init__(message)
        self.completed_objects = tuple(completed_objects)
        self.status = "PARTIAL"


class StorageClient(Protocol):
    def get_object(self, bucket: str, object_path: str) -> bytes: ...

    def create_object(self, bucket: str, object_path: str, payload: bytes, content_type: str) -> None: ...


@dataclass(frozen=True)
class DeployPlan:
    profile_name: str
    project_url: str
    bucket: str
    version: str
    level: str
    pack_version: int
    base_local_path: str
    base_bytes: int
    base_sha256: str
    base_object_path: str
    plus_local_path: str
    plus_bytes: int
    plus_sha256: str
    plus_object_path: str
    compatibility_hash: str
    base_manifest: dict[str, object]
    plus_manifest: dict[str, object]
    segment_packs: dict[str, dict[str, object]] = field(default_factory=dict)


@dataclass(frozen=True)
class CatalogSnapshot:
    revision: int
    local_path: str
    object_path: str
    payload: bytes
    sha256: str
    entry_count: int
    catalog: dict[str, object]


@dataclass(frozen=True)
class CatalogPublishPlan:
    source: CatalogSnapshot
    target_revision: int
    target_object_path: str
    additions: tuple[dict[str, object], ...]
    receipts: tuple[dict[str, object], ...]


def _sha256_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _canonical_json_bytes(value: object) -> bytes:
    return (json.dumps(value, ensure_ascii=False, separators=(",", ":")) + "\n").encode("utf-8")


def _validate_level(level: str) -> str:
    normalized = str(level or "").strip().lower()
    if normalized not in SUPPORTED_LEVELS:
        raise DeployValidationError(f"Level không hợp lệ: {level!r}")
    return normalized


def _validate_version(version: str) -> str:
    value = str(version or "").strip()
    if value not in SUPPORTED_VERSIONS:
        raise DeployValidationError(f"Vocab version không hợp lệ: {version!r}")
    return value


def pack_id(level: str, segment: str, pack_version: int = PACK_VERSION, version: str = STANDARD_VERSION) -> str:
    level = _validate_level(level)
    version = _validate_version(version)
    if segment not in {"base", "plus", "plus1", "plus2"}:
        raise DeployValidationError("segment phải là base, plus, plus1 hoặc plus2.")
    if int(pack_version) < 1:
        raise DeployValidationError("packVersion phải >= 1.")
    return f"vocab:{version}:{level}:{segment}:v{int(pack_version)}"


def collection_id(level: str, segment: str, pack_version: int = PACK_VERSION, version: str = STANDARD_VERSION) -> str:
    level = _validate_level(level)
    if segment not in {"base", "plus", "plus1", "plus2"}:
        raise DeployValidationError("segment phải là base, plus, plus1 hoặc plus2.")
    if int(pack_version) < 1:
        raise DeployValidationError("packVersion phải >= 1.")
    version = _validate_version(version)
    return f"vocab_level::{version}::{level}::{segment}::v{int(pack_version)}"


def pack_object_path(level: str, segment: str, pack_version: int = PACK_VERSION, version: str = STANDARD_VERSION) -> str:
    level = _validate_level(level)
    if segment not in {"base", "plus", "plus1", "plus2"}:
        raise DeployValidationError("segment phải là base, plus, plus1 hoặc plus2.")
    if int(pack_version) < 1:
        raise DeployValidationError("packVersion phải >= 1.")
    standard_version = _validate_version(version)
    pack_number = int(pack_version)
    return f"vocab/{standard_version}/{level}/{segment}/v{pack_number}/vocab_{level}_{standard_version.replace('.', '')}_{segment}_v{pack_number}.zip"


def catalog_object_path(revision: int) -> str:
    if int(revision) < 1:
        raise DeployValidationError("Catalog revision phải >= 1.")
    revision = int(revision)
    return f"catalogs/vocab/combined/v{revision}/vocab_pack_catalog_20_30_v{revision}.json"


def stage_confirmation_phrase(level: str, version: str = STANDARD_VERSION) -> str:
    return f"STAGE {_validate_level(level).upper()} {_validate_version(version)}"


def require_stage_confirmation(level: str, value: str, version: str = STANDARD_VERSION) -> None:
    expected = stage_confirmation_phrase(level, version)
    if value != expected:
        raise DeployValidationError("Xác nhận stage không đúng; chưa có remote request nào được gọi.")


def require_catalog_confirmation(value: str) -> None:
    if value != CATALOG_PUBLISH_CONFIRMATION:
        raise DeployValidationError("Xác nhận publish catalog không đúng; chưa có remote request nào được gọi.")


def compatibility_hash_from_ids(ids: list[str]) -> str:
    if not isinstance(ids, list) or any(not isinstance(value, str) or not value for value in ids):
        raise DeployValidationError("BASE orderedVocabIds phải là list string không rỗng.")
    return _sha256_bytes(
        json.dumps(ids, ensure_ascii=False, separators=(",", ":"), sort_keys=False).encode("utf-8")
    )


def _read_pack_json(zip_path: str | Path) -> tuple[dict[str, object], list[dict[str, object]]]:
    import zipfile

    try:
        with zipfile.ZipFile(zip_path, "r") as archive:
            manifest = json.loads(archive.read("manifest.json").decode("utf-8"))
            vocab = json.loads(archive.read("vocab.json").decode("utf-8"))
    except Exception as exc:
        raise DeployValidationError(f"Không đọc được manifest/vocab từ ZIP: {exc}") from exc
    if not isinstance(manifest, dict) or not isinstance(vocab, list) or any(not isinstance(item, dict) for item in vocab):
        raise DeployValidationError("manifest/vocab trong ZIP sai kiểu dữ liệu.")
    return manifest, vocab


def _validate_ordered_ids(manifest: Mapping[str, object], vocab: list[dict[str, object]], label: str) -> list[str]:
    ordered = manifest.get("orderedVocabIds")
    if not isinstance(ordered, list) or any(not isinstance(value, str) or not value for value in ordered):
        raise DeployValidationError(f"{label} orderedVocabIds phải là list string không rỗng.")
    if len(ordered) != len(vocab) or len(set(ordered)) != len(ordered):
        raise DeployValidationError(f"{label} orderedVocabIds không khớp vocab hoặc bị trùng.")
    try:
        expected = [str(item["id"]) for item in sorted(vocab, key=lambda item: int(item["index"]))]
    except (KeyError, TypeError, ValueError) as exc:
        raise DeployValidationError(f"{label} vocab thiếu index/id hợp lệ.") from exc
    if any(not value for value in expected) or ordered != expected:
        raise DeployValidationError(f"{label} orderedVocabIds không đúng thứ tự/index stable ID.")
    return ordered


def validate_compatibility_contract(base_zip: str | Path, plus_zip: str | Path, level: str | None = None, version: str | None = None) -> dict[str, object]:
    """Validate one BASE/PLUS pair and derive its BASE-only compatibility hash."""
    base_manifest, base_vocab = _read_pack_json(base_zip)
    plus_manifest, plus_vocab = _read_pack_json(plus_zip)
    detected_level = _validate_level(str(base_manifest.get("level", "")))
    detected_version = _validate_version(str(base_manifest.get("standardVersion", "")))
    if level is not None and detected_level != _validate_level(level):
        raise DeployValidationError("Level manifest không khớp build receipt.")
    if plus_manifest.get("level") != detected_level:
        raise DeployValidationError("BASE/PLUS manifest level không khớp.")
    if plus_manifest.get("standardVersion") != detected_version:
        raise DeployValidationError("BASE/PLUS manifest version không khớp.")
    if version is not None and detected_version != _validate_version(version):
        raise DeployValidationError("Version manifest không khớp build receipt.")
    if base_manifest.get("segment") != "base" or plus_manifest.get("segment") != "plus":
        raise DeployValidationError("BASE/PLUS manifest segment không đúng.")
    base_ids = _validate_ordered_ids(base_manifest, base_vocab, "BASE")
    plus_ids = _validate_ordered_ids(plus_manifest, plus_vocab, "PLUS")
    if len(base_ids) != int(base_manifest.get("previewWordCount", 0) or 0) or not base_ids or set(base_ids).intersection(plus_ids):
        raise DeployValidationError("BASE count/previewWordCount không đúng hoặc overlap PLUS.")
    calculated = compatibility_hash_from_ids(base_ids)
    if base_manifest.get("orderedVocabIdsSha256") != calculated:
        raise DeployValidationError("BASE orderedVocabIdsSha256 không khớp hash tự tính.")
    if plus_manifest.get("baseOrderedVocabIdsSha256") != calculated:
        raise DeployValidationError("PLUS baseOrderedVocabIdsSha256 không khớp hash BASE.")
    if plus_manifest.get("requiresPackId") != base_manifest.get("packId"):
        raise DeployValidationError("PLUS requiresPackId không trỏ tới BASE.")
    if plus_manifest.get("compatibleBaseVersion") != base_manifest.get("packVersion"):
        raise DeployValidationError("PLUS compatibleBaseVersion không khớp BASE.")
    return {
        "status": "PASS", "version": detected_version, "level": detected_level, "compatibilityHash": calculated,
        "baseIds": base_ids, "plusIds": plus_ids,
        "baseManifest": base_manifest, "plusManifest": plus_manifest,
    }


def input_fingerprint(
    excel_path: str | Path,
    sheet: str,
    level: str,
    output_directory: str | Path,
    *,
    version: str = STANDARD_VERSION,
    bitrate: str = "32k",
    pack_version: int = 1,
) -> dict[str, object]:
    version = _validate_version(version)
    level = _validate_level(level)
    excel = Path(excel_path).expanduser().resolve()
    output = Path(output_directory).expanduser().resolve()
    if not excel.is_file():
        raise DeployValidationError(f"Excel không tồn tại: {excel}")
    source_audio = output / "vocab" / version / level / "audio_cache"
    files = [
        {"name": path.name, "bytes": path.stat().st_size, "sha256": sha256_file(path)}
        for path in sorted(source_audio.glob("*.m4a")) if path.is_file()
    ] if source_audio.is_dir() else []
    return {
        "excelPath": str(excel), "excelBytes": excel.stat().st_size, "excelSha256": sha256_file(excel),
        "sheet": sheet, "version": version, "level": level, "outputDirectory": str(output), "bitrate": bitrate,
        "sourceAudio": {"directory": str(source_audio), "files": files, "sha256": _sha256_bytes(_canonical_json_bytes(files))},
    }


def _unpack_config(config: tuple[str, ...], result: Mapping[str, object]) -> tuple[str, str, str, str, str]:
    """Accept the former four-item HSK3 tuple and the generic five-item tuple."""
    if len(config) == 4:
        excel, sheet, level, output = config
        return excel, sheet, str(result.get("version", STANDARD_VERSION)), level, output
    if len(config) == 5:
        excel, sheet, version, level, output = config
        return excel, sheet, version, level, output
    raise DeployValidationError("Build config phải gồm excel, sheet, version, level, output.")


def validate_local_receipt(result: Mapping[str, object] | None, config: tuple[str, ...], receipt_fingerprint: Mapping[str, object] | None) -> dict[str, object]:
    if not isinstance(result, Mapping) or result.get("status") != "PASS":
        raise DeployValidationError("Phase 1 chưa PASS.")
    excel, sheet, version, level, output = _unpack_config(config, result)
    version = _validate_version(version)
    level = _validate_level(level)
    if receipt_fingerprint is None:
        raise DeployValidationError("Thiếu build receipt/input fingerprint; hãy build lại Phase 1.")
    current = input_fingerprint(
        excel,
        sheet,
        level,
        output,
        version=version,
        bitrate=str(result.get("ttsConfig", {}).get("m4a", {}).get("bitrate", "32k")),
        pack_version=int(result.get("packVersion", 1) or 1),
    )
    if dict(current) != dict(receipt_fingerprint):
        raise DeployValidationError("Excel, audio source hoặc build input đã thay đổi sau Phase 1.")
    if result.get("version") != version:
        raise DeployValidationError("Build result version không khớp selection hiện tại.")
    validation_path = Path(output) / "vocab" / version / level / "builds" / f"v{int(result.get('packVersion', 1) or 1)}" / "validation_report.json"
    try:
        validation = json.loads(validation_path.read_text(encoding="utf-8"))
    except Exception as exc:
        raise DeployValidationError(f"Không đọc được validation_report.json: {exc}") from exc
    if validation.get("status") != "PASS":
        raise DeployValidationError("validation_report.json không PASS.")
    split_hsk79 = version == "3.0" and level == "hsk7_9" and "plus1" in result
    segments = ("base", "plus1", "plus2") if split_hsk79 else ("base", "plus")
    packs: dict[str, dict[str, object]] = {}
    for segment in segments:
        pack = result.get(segment)
        if not isinstance(pack, Mapping):
            raise DeployValidationError(f"Thiếu build receipt {segment.upper()}.")
        local_path = Path(str(pack.get("zip", "")))
        actual_bytes = local_path.stat().st_size if local_path.is_file() else -1
        actual_sha = sha256_file(local_path) if local_path.is_file() else ""
        if actual_bytes != pack.get("bytes") or actual_sha != pack.get("sha256"):
            raise DeployValidationError(f"ZIP {segment.upper()} không khớp bytes/SHA trong receipt.")
        if str(result.get("objectPaths", {}).get(segment, "")) != pack_object_path(level, segment, int(result.get("packVersion", 1) or 1), version):
            raise DeployValidationError(f"Object path {segment.upper()} không đúng level.")
        with zipfile.ZipFile(local_path, "r") as archive:
            manifest = json.loads(archive.read("manifest.json").decode("utf-8"))
        packs[segment] = {"localPath": str(local_path), "bytes": actual_bytes, "sha256": actual_sha, "manifest": manifest}
    try:
        if split_hsk79:
            verify_pack_parts(packs["base"]["localPath"], packs["plus1"]["localPath"], packs["plus2"]["localPath"], level, expected_version=version)
        else:
            verify_pack_pair(packs["base"]["localPath"], packs["plus"]["localPath"], level, expected_version=version)
    except Exception as exc:
        raise DeployValidationError(f"BASE/PLUS verify thất bại: {exc}") from exc
    if split_hsk79:
        with zipfile.ZipFile(packs["base"]["localPath"], "r") as archive:
            base_manifest = json.loads(archive.read("manifest.json").decode("utf-8"))
        contract = {"version": version, "level": level, "compatibilityHash": str(base_manifest.get("orderedVocabIdsSha256", "")), "baseManifest": base_manifest, "plusManifest": {}}
    else:
        contract = validate_compatibility_contract(packs["base"]["localPath"], packs["plus"]["localPath"], level, version)
    base_version = int(contract["baseManifest"].get("packVersion", 0) or 0)
    plus_versions = [int(packs[segment].get("manifest", {}).get("packVersion", base_version) or 0) for segment in segments[1:]]
    if base_version < 1 or any(value != base_version for value in plus_versions):
        raise DeployValidationError("BASE/PLUS packVersion không khớp hoặc không hợp lệ.")
    return {"status": "PASS", "version": version, "fingerprint": current, "packs": packs, "packVersion": base_version, "compatibility": contract}


def build_plan(result: Mapping[str, object] | None, config: tuple[str, ...], receipt_fingerprint: Mapping[str, object] | None, profile: Mapping[str, object], *, profile_name: str = "") -> DeployPlan:
    verified = validate_local_receipt(result, config, receipt_fingerprint)
    url = str(profile.get("SUPABASE_URL", "") or "").strip().rstrip("/")
    bucket = str(profile.get("SUPABASE_BUCKET", "") or "").strip()
    key = str(profile.get("SUPABASE_SERVICE_ROLE_KEY", "") or "").strip()
    if not url or not bucket or not key:
        raise DeployValidationError("Supabase profile thiếu URL, bucket hoặc service-role key.")
    level = str(verified["compatibility"]["level"])
    version = str(verified["version"])
    base = verified["packs"]["base"]
    split_hsk79 = version == "3.0" and level == "hsk7_9" and "plus1" in verified["packs"]
    plus = verified["packs"]["plus1"] if split_hsk79 else verified["packs"]["plus"]
    contract = verified["compatibility"]
    pack_version = int(verified.get("packVersion", PACK_VERSION))
    segment_packs = {
        segment: {**dict(pack), "objectPath": pack_object_path(level, segment, pack_version, version)}
        for segment, pack in verified["packs"].items()
    }
    return DeployPlan(
        profile_name=profile_name, project_url=url, bucket=STAGING_BUCKET, version=version, level=level, pack_version=pack_version,
        base_local_path=base["localPath"], base_bytes=base["bytes"], base_sha256=base["sha256"], base_object_path=pack_object_path(level, "base", pack_version, version),
        plus_local_path=plus["localPath"], plus_bytes=plus["bytes"], plus_sha256=plus["sha256"], plus_object_path=pack_object_path(level, "plus", pack_version, version),
        compatibility_hash=str(contract["compatibilityHash"]), base_manifest=dict(contract["baseManifest"]), plus_manifest=dict(plus.get("manifest", contract.get("plusManifest", {}))), segment_packs=segment_packs,
    )


def _entries_key(catalog: Mapping[str, object]) -> str:
    candidates = [key for key in ("entries", "packs", "collections") if isinstance(catalog.get(key), list)]
    if len(candidates) != 1:
        raise DeployValidationError("Không xác định duy nhất field entry của catalog.")
    return candidates[0]


def _identity(entry: Mapping[str, object]) -> tuple[object, object, object]:
    return (entry.get("version"), entry.get("level"), entry.get("segment"))


def _validate_catalog_entries(catalog: Mapping[str, object]) -> list[Mapping[str, object]]:
    if catalog.get("schemaVersion") != 1:
        raise DeployValidationError("Catalog phải có schemaVersion 1.")
    entries = catalog[_entries_key(catalog)]
    if not isinstance(entries, list) or any(not isinstance(entry, Mapping) for entry in entries):
        raise DeployValidationError("Catalog entries không hợp lệ.")
    identities = [_identity(entry) for entry in entries]
    pack_ids = [entry.get("packId") for entry in entries]
    collection_ids = [entry.get("collectionId") for entry in entries]
    object_paths = [entry.get("objectPath") for entry in entries]
    if len(identities) != len(set(identities)) or len(pack_ids) != len(set(pack_ids)) or len(collection_ids) != len(set(collection_ids)) or len(object_paths) != len(set(object_paths)):
        raise DeployValidationError("Catalog có duplicate identity, packId hoặc collectionId.")
    return entries


def verify_catalog_payload(payload: bytes, *, expected_sha256: str | None = None, expected_bytes: int | None = None) -> dict[str, object]:
    if expected_bytes is not None and len(payload) != expected_bytes:
        raise DeployValidationError("Catalog sai bytes expected.")
    if expected_sha256 is not None and _sha256_bytes(payload) != expected_sha256:
        raise DeployValidationError("Catalog sai SHA-256 expected.")
    try:
        catalog = json.loads(payload.decode("utf-8"))
    except Exception as exc:
        raise DeployValidationError(f"Catalog không phải JSON hợp lệ: {exc}") from exc
    if not isinstance(catalog, dict):
        raise DeployValidationError("Catalog root phải là object.")
    _validate_catalog_entries(catalog)
    return catalog


def load_seed_catalog(base_directory: str | Path = ".") -> tuple[bytes, dict[str, object]]:
    path = Path(base_directory) / SEED_CATALOG_PATH
    try:
        payload = path.read_bytes()
    except OSError as exc:
        raise DeployValidationError(f"Thiếu catalog HSK 2.0 local: {path}") from exc
    catalog = verify_catalog_payload(payload, expected_sha256=SEED_CATALOG_SHA256, expected_bytes=SEED_CATALOG_BYTES)
    entries = _validate_catalog_entries(catalog)
    if len(entries) != 12 or any(entry.get("version") != "2.0" or entry.get("enabled") is not True for entry in entries):
        raise DeployValidationError("Seed catalog phải có đúng 12 entry HSK 2.0 enabled=true.")
    return payload, catalog


def catalog_entry_from_plan(plan: DeployPlan, segment: str) -> dict[str, object]:
    pack = plan.segment_packs.get(segment, {})
    manifest = plan.base_manifest if segment == "base" else (pack.get("manifest") or plan.plus_manifest)
    sha = plan.base_sha256 if segment == "base" else str(pack.get("sha256", plan.plus_sha256))
    size = plan.base_bytes if segment == "base" else int(pack.get("bytes", plan.plus_bytes))
    return {
        "version": plan.version, "level": plan.level, "segment": segment,
        "packId": pack_id(plan.level, segment, plan.pack_version, plan.version), "collectionId": collection_id(plan.level, segment, plan.pack_version, plan.version),
        "packVersion": int(manifest.get("packVersion", 0)), "vocabCount": int(manifest.get("vocabCount", 0)),
        "audioCount": sum(1 for resource in manifest.get("resources", []) if isinstance(resource, Mapping) and resource.get("type") == "vocab_audio"),
        "objectPath": pack_object_path(plan.level, segment, plan.pack_version, plan.version), "filename": f"vocab_{plan.level}_{plan.version.replace('.', '')}_{segment}_v{plan.pack_version}.zip",
        "sha256": sha,
        "zipBytes": size,
        "compatibilityHash": plan.compatibility_hash, "accessTier": "base" if segment == "base" else "vip", "enabled": True,
    }


def deploy_receipt_path(output_directory: str | Path, level: str, version: str = STANDARD_VERSION) -> Path:
    return Path(output_directory) / "vocab" / _validate_version(version) / _validate_level(level) / "deploy_receipt.json"


def _write_deploy_receipt(output_directory: str | Path, plan: DeployPlan) -> dict[str, object]:
    segments = ("base", "plus1", "plus2") if "plus1" in plan.segment_packs else ("base", "plus")
    entries = {segment: catalog_entry_from_plan(plan, segment) for segment in segments}
    now = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    build_report = Path(output_directory) / "vocab" / plan.version / plan.level / "builds" / f"v{plan.pack_version}" / "build_report.json"
    source_build_sha = sha256_file(build_report) if build_report.is_file() else ""
    tts_config: dict[str, object] | None = None
    if build_report.is_file():
        try:
            build_payload = json.loads(build_report.read_text(encoding="utf-8"))
            if isinstance(build_payload, dict) and isinstance(build_payload.get("ttsConfig"), dict):
                tts_config = dict(build_payload["ttsConfig"])
        except Exception:
            tts_config = None
    receipt = {
        "schemaVersion": 1, "dataVersion": plan.version, "version": plan.version,
        "level": plan.level, "packVersion": plan.pack_version, "timestamp": now,
        "remoteVerifiedAt": now, "compatibilityHash": plan.compatibility_hash,
        "sourceBuildReceiptSha256": source_build_sha,
        "ttsConfig": tts_config,
        **{f"{segment}RemoteVerified": True for segment in segments},
        "catalogPublished": False,
        **{segment: {**entries[segment], "remoteVerified": True} for segment in segments},
    }
    path = deploy_receipt_path(output_directory, plan.level, plan.version)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(_canonical_json_bytes(receipt))
    return receipt


def validate_deploy_receipt(receipt: Mapping[str, object]) -> dict[str, object]:
    if receipt.get("schemaVersion") != 1:
        raise DeployValidationError("Deploy receipt sai schema/version.")
    version = _validate_version(str(receipt.get("version", receipt.get("dataVersion", ""))))
    level = _validate_level(str(receipt.get("level", "")))
    pack_version = receipt.get("packVersion")
    if not isinstance(pack_version, int) or pack_version < 1:
        raise DeployValidationError("Deploy receipt packVersion không hợp lệ.")
    compatibility = receipt.get("compatibilityHash")
    if not isinstance(compatibility, str) or len(compatibility) != 64:
        raise DeployValidationError("Deploy receipt thiếu compatibilityHash.")
    segments = ("base", "plus1", "plus2") if "plus1" in receipt else ("base", "plus")
    entries: list[dict[str, object]] = []
    for segment in segments:
        entry = receipt.get(segment)
        if not isinstance(entry, Mapping) or entry.get("remoteVerified") is not True:
            raise DeployValidationError(f"Deploy receipt {segment.upper()} chưa remoteVerified.")
        required = {"packId": pack_id(level, segment, pack_version, version), "collectionId": collection_id(level, segment, pack_version, version), "objectPath": pack_object_path(level, segment, pack_version, version)}
        if any(entry.get(key) != value for key, value in required.items()):
            raise DeployValidationError(f"Deploy receipt {segment.upper()} identity/path không đúng.")
        if entry.get("compatibilityHash") != compatibility or not isinstance(entry.get("sha256"), str) or not isinstance(entry.get("zipBytes"), int):
            raise DeployValidationError(f"Deploy receipt {segment.upper()} thiếu SHA/bytes/hash.")
        item = {key: value for key, value in entry.items() if key != "remoteVerified"}
        entries.append(item)
    if any(item["compatibilityHash"] != entries[0]["compatibilityHash"] for item in entries[1:]):
        raise DeployValidationError("Deploy receipt BASE/PLUS compatibilityHash không giống nhau.")
    if any(receipt.get(f"{segment}RemoteVerified", True) is not True for segment in segments):
        raise DeployValidationError("Deploy receipt chưa remoteVerified đủ các segment.")
    return {"version": version, "level": level, "packVersion": pack_version, "entries": entries, "receipt": dict(receipt)}


def catalog_matches_receipt(receipt: Mapping[str, object], catalog: Mapping[str, object]) -> bool:
    """Check that all receipt segments are the descriptors active in catalog."""
    try:
        validated = validate_deploy_receipt(receipt)
        active = {_identity(entry): entry for entry in _validate_catalog_entries(catalog)}
    except (DeployValidationError, TypeError, KeyError):
        return False
    fields = ("packId", "collectionId", "packVersion", "objectPath", "sha256", "zipBytes", "vocabCount", "compatibilityHash")
    return all(
        (actual := active.get(_identity(expected))) is not None
        and all(actual.get(field) == expected.get(field) for field in fields)
        for expected in validated["entries"]
    )


def collect_deploy_receipts(output_directory: str | Path, levels: set[str] | None = None, versions: set[str] | None = None) -> list[dict[str, object]]:
    receipts: list[dict[str, object]] = []
    for version in SUPPORTED_VERSIONS:
        if versions is not None and version not in versions:
            continue
        for level in SUPPORTED_LEVELS:
            if levels is not None and level not in levels:
                continue
            path = Path(output_directory) / "vocab" / version / level / "deploy_receipt.json"
            if not path.is_file():
                continue
            try:
                receipt = json.loads(path.read_text(encoding="utf-8"))
            except Exception as exc:
                raise DeployValidationError(f"Không đọc được deploy receipt {path}: {exc}") from exc
            validated = validate_deploy_receipt(receipt)
            if validated["version"] != version:
                raise DeployValidationError(f"Deploy receipt bị đặt nhầm namespace: {path}")
            validated["path"] = str(path)
            validated["receipt"]["_receiptPath"] = str(path)
            receipts.append(validated)
    return receipts


def mark_receipts_catalog_published(receipts: tuple[Mapping[str, object], ...] | list[Mapping[str, object]], *, catalog_revision: int, pointer_revision: int | None = None) -> None:
    """The final local write in publishing: only verified selected receipts change."""
    for receipt in receipts:
        version = _validate_version(str(receipt.get("version", "")))
        level = _validate_level(str(receipt.get("level", "")))
        path_hint = receipt.get("_receiptPath") or receipt.get("path")
        if not path_hint and isinstance(receipt.get("receipt"), Mapping):
            path_hint = receipt["receipt"].get("_receiptPath")
        path = Path(str(path_hint)) if path_hint else None
        if path is None or not path.is_file():
            raise DeployValidationError(f"Không tìm thấy receipt để đánh dấu published: HSK {version} {level}")
        payload = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(payload, dict):
            raise DeployValidationError(f"Deploy receipt không hợp lệ: {path}")
        validate_deploy_receipt(payload)
        payload["catalogPublished"] = True
        payload["catalogPublishedAt"] = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
        payload["catalogRevision"] = int(catalog_revision)
        if pointer_revision is not None:
            payload["pointerRevision"] = int(pointer_revision)
        path.write_bytes(_canonical_json_bytes(payload))


def _snapshot_path(output_directory: str | Path, revision: int) -> Path:
    return Path(output_directory) / "vocab" / "catalog_revisions" / f"v{revision}" / f"vocab_pack_catalog_20_30_v{revision}.json"


def load_catalog_source_snapshot(output_directory: str | Path) -> CatalogSnapshot:
    root = Path(output_directory) / "vocab"
    revisions: list[tuple[int, Path]] = []
    revision_root = root / "catalog_revisions"
    if revision_root.is_dir():
        for path in revision_root.glob("v*/vocab_pack_catalog_20_30_v*.json"):
            match = re.search(r"/v(\d+)/", path.as_posix())
            if match:
                revisions.append((int(match.group(1)), path))
    if revisions:
        revision, path = max(revisions, key=lambda item: item[0])
    else:
        revision = 1
        path = Path(SEED_CATALOG_PATH)
        if not path.is_file():
            path = Path(__file__).resolve().parents[1] / SEED_CATALOG_PATH
    try:
        payload = path.read_bytes()
    except OSError as exc:
        raise DeployValidationError(f"Thiếu snapshot catalog revision local: {path}") from exc
    catalog = verify_catalog_payload(payload)
    return CatalogSnapshot(revision, str(path), catalog_object_path(revision), payload, _sha256_bytes(payload), len(_validate_catalog_entries(catalog)), catalog)


def merge_catalog(source: Mapping[str, object], additions: list[Mapping[str, object]]) -> dict[str, object]:
    """Deep-copy source, append new identities, or replace a newer pack version."""
    result = copy.deepcopy(dict(source))
    key = _entries_key(result)
    entries = result[key]
    assert isinstance(entries, list)
    if not additions:
        raise DeployValidationError("Không có receipt staged mới để publish catalog.")
    merged = [dict(entry) for entry in entries]
    positions = {_identity(entry): index for index, entry in enumerate(merged)}
    for addition in additions:
        identity = _identity(addition)
        existing_index = positions.get(identity)
        if existing_index is None:
            positions[identity] = len(merged)
            merged.append(dict(addition))
            continue
        existing = merged[existing_index]
        if existing == addition:
            continue
        old_version = existing.get("packVersion")
        new_version = addition.get("packVersion")
        if not isinstance(old_version, int) or not isinstance(new_version, int) or new_version <= old_version:
            raise DeployValidationError(f"Catalog identity conflict: {identity}")
        if existing.get("objectPath") == addition.get("objectPath"):
            raise DeployValidationError("Rebuild phải dùng objectPath mới.")
        merged[existing_index] = dict(addition)
    result[key] = merged
    _validate_catalog_entries(result)
    # Entries unrelated to the requested identities are byte-for-byte stable.
    touched = {_identity(entry) for entry in additions}
    for before, after in zip(entries, result[key]):
        if _identity(before) not in touched and before != after:
            raise DeployValidationError("Catalog nguồn bị thay đổi khi merge.")
    return result


def prepare_catalog_publish(output_directory: str | Path, levels: set[str] | None = None, versions: set[str] | None = None) -> CatalogPublishPlan:
    source = load_catalog_source_snapshot(output_directory)
    source_entries = _validate_catalog_entries(source.catalog)
    by_identity = {_identity(entry): dict(entry) for entry in source_entries}
    additions: list[dict[str, object]] = []
    receipts = collect_deploy_receipts(output_directory, levels=levels, versions=versions)
    for receipt in receipts:
        for entry in receipt["entries"]:
            identity = _identity(entry)
            existing = by_identity.get(identity)
            if existing is not None:
                if existing == entry:
                    continue
                old_version = existing.get("packVersion")
                new_version = entry.get("packVersion")
                if not isinstance(old_version, int) or not isinstance(new_version, int) or new_version <= old_version:
                    raise DeployValidationError(f"Receipt conflict với catalog hiện hành: {identity}")
            if identity in {_identity(value) for value in additions}:
                raise DeployValidationError(f"Duplicate identity giữa deploy receipt: {identity}")
            additions.append(dict(entry))
    if not additions:
        raise DeployValidationError("Chưa có deploy receipt mới để publish catalog.")
    combined = merge_catalog(source.catalog, additions)
    _validate_catalog_entries(combined)
    return CatalogPublishPlan(source, source.revision + 1, catalog_object_path(source.revision + 1), tuple(additions), tuple(item["receipt"] for item in receipts))


def _verify_bytes(payload: bytes, expected_sha: str, expected_bytes: int, label: str) -> None:
    if len(payload) != expected_bytes or _sha256_bytes(payload) != expected_sha:
        raise DeployValidationError(f"Remote {label} sai bytes/SHA; không overwrite.")


def _verify_zip_payload(payload: bytes, expected_sha: str, expected_bytes: int, level: str, segment: str, version: str) -> None:
    _verify_bytes(payload, expected_sha, expected_bytes, f"ZIP {segment}")
    with tempfile.NamedTemporaryFile(prefix=f"hsk30-remote-{level}-{segment}-", suffix=".zip") as temp_file:
        temp_file.write(payload)
        temp_file.flush()
        verify_pack(temp_file.name, level, segment, expected_version=version)


def _probe_log(progress: Callable[[str], None] | None, object_path: str, status: object, classification: str) -> None:
    if progress:
        progress(f"step=probe_object object={object_path} http_status={status} classification={classification}")


def _http_status_from_error(exc: BaseException) -> object:
    match = re.search(r"HTTP (\d+)", str(exc))
    return int(match.group(1)) if match else "unknown"


def _ensure_zip_object(client: StorageClient, plan: DeployPlan, segment: str, progress: Callable[[str], None] | None = None) -> None:
    pack = plan.segment_packs.get(segment, {})
    object_path = pack.get("objectPath") or pack_object_path(plan.level, segment, plan.pack_version, plan.version)
    local_path = pack.get("localPath") or (plan.base_local_path if segment == "base" else plan.plus_local_path)
    expected_sha = pack.get("sha256") or (plan.base_sha256 if segment == "base" else plan.plus_sha256)
    expected_bytes = int(pack.get("bytes") or (plan.base_bytes if segment == "base" else plan.plus_bytes))
    try:
        existing = client.get_object(plan.bucket, object_path)
    except StorageNotFound as exc:
        _probe_log(progress, object_path, exc.http_status, "ABSENT")
        payload = Path(local_path).read_bytes()
        _verify_bytes(payload, expected_sha, expected_bytes, f"local {segment}")
        client.create_object(plan.bucket, object_path, payload, "application/zip")
    except Exception as exc:
        _probe_log(progress, object_path, _http_status_from_error(exc), "ERROR")
        raise
    else:
        try:
            _verify_zip_payload(existing, expected_sha, expected_bytes, plan.level, segment, plan.version)
        except Exception:
            _probe_log(progress, object_path, 200, "PRESENT_CONFLICT")
            raise
        _probe_log(progress, object_path, 200, "PRESENT_MATCH")
    downloaded = client.get_object(plan.bucket, object_path)
    _verify_zip_payload(downloaded, expected_sha, expected_bytes, plan.level, segment, plan.version)
    if progress:
        progress(f"GET verify {plan.level.upper()} {plan.version} {segment.upper()}: PASS")


def stage_packs_with_client(client: StorageClient, plan: DeployPlan, *, confirmation: str, output_directory: str | Path, progress: Callable[[str], None] | None = None) -> dict[str, object]:
    """Stage BASE and PLUS only.  It never creates or uploads a catalog."""
    require_stage_confirmation(plan.level, confirmation, plan.version)
    completed: list[str] = []
    try:
        segments = ("base", "plus1", "plus2") if "plus1" in plan.segment_packs else ("base", "plus")
        for segment in segments:
            _ensure_zip_object(client, plan, segment, progress)
            completed.append(str(plan.segment_packs.get(segment, {}).get("objectPath") or pack_object_path(plan.level, segment, plan.pack_version, plan.version)))
    except Exception as exc:
        if completed:
            raise PartialDeployError(f"PARTIAL: staged {completed}; catalog chưa publish: {exc}", completed) from exc
        raise
    receipt = _write_deploy_receipt(output_directory, plan)
    if progress:
        progress(f"REMOTE PACKS VERIFIED: {plan.level.upper()} {plan.version} | CATALOG NOT PUBLISHED")
    return {"status": "REMOTE PACKS VERIFIED", "receipt": receipt, "receiptPath": str(deploy_receipt_path(output_directory, plan.level, plan.version))}


def publish_catalog_with_client(client: StorageClient, *, output_directory: str | Path, confirmation: str, progress: Callable[[str], None] | None = None) -> dict[str, object]:
    """Publish one new immutable catalog revision from verified stage receipts."""
    require_catalog_confirmation(confirmation)
    publish_plan = prepare_catalog_publish(output_directory)
    source = publish_plan.source
    remote_source = client.get_object(STAGING_BUCKET, source.object_path)
    verify_catalog_payload(remote_source, expected_sha256=source.sha256, expected_bytes=len(source.payload))
    if progress:
        progress(f"Verify catalog source v{source.revision}: PASS")
    combined = merge_catalog(source.catalog, list(publish_plan.additions))
    payload = _canonical_json_bytes(combined)
    target = publish_plan.target_object_path
    try:
        existing = client.get_object(STAGING_BUCKET, target)
    except StorageNotFound as exc:
        _probe_log(progress, target, exc.http_status, "ABSENT")
        client.create_object(STAGING_BUCKET, target, payload, "application/json")
    except Exception as exc:
        _probe_log(progress, target, _http_status_from_error(exc), "ERROR")
        raise
    else:
        try:
            _verify_bytes(existing, _sha256_bytes(payload), len(payload), "catalog remote")
        except Exception:
            _probe_log(progress, target, 200, "PRESENT_CONFLICT")
            raise
        _probe_log(progress, target, 200, "PRESENT_MATCH")
    downloaded = client.get_object(STAGING_BUCKET, target)
    _verify_bytes(downloaded, _sha256_bytes(payload), len(payload), "catalog GET")
    verified = verify_catalog_payload(downloaded, expected_sha256=_sha256_bytes(payload), expected_bytes=len(payload))
    snapshot_path = _snapshot_path(output_directory, publish_plan.target_revision)
    snapshot_path.parent.mkdir(parents=True, exist_ok=True)
    snapshot_path.write_bytes(downloaded)
    public_url = f"{getattr(client, 'project_url', '').rstrip('/')}/storage/v1/object/public/{STAGING_BUCKET}/{target}"
    result = {
        "status": "CATALOG PUBLISHED", "revision": publish_plan.target_revision, "objectPath": target,
        "publicUrl": public_url, "bytes": len(downloaded), "sha256": _sha256_bytes(downloaded),
        "entryCount": len(_validate_catalog_entries(verified)), "sourceRevision": source.revision,
        "sourceEntryCount": source.entry_count, "addedEntryCount": len(publish_plan.additions), "snapshotPath": str(snapshot_path),
    }
    mark_receipts_catalog_published(publish_plan.receipts, catalog_revision=publish_plan.target_revision)
    if progress:
        progress(f"CATALOG PUBLISHED v{publish_plan.target_revision}: {result['sha256']}")
    return result


def _http_error_body(exc: urlerror.HTTPError) -> tuple[dict[str, object] | None, str]:
    try:
        body = exc.read(4096).decode("utf-8", errors="replace")
    except Exception:
        return None, ""
    try:
        value = json.loads(body)
    except Exception:
        value = None
    return value if isinstance(value, dict) else None, body


def _is_object_not_found_response(status: int, parsed: dict[str, object] | None, body: str) -> bool:
    if status == 404:
        return True
    if status != 400:
        return False
    status_code = parsed.get("statusCode") if parsed else None
    error_code = parsed.get("error") if parsed else None
    message = str(parsed.get("message", "")) if parsed else body
    return status_code == 404 or status_code == "404" or error_code == "not_found" or "Object not found" in message


class MemoryStorageClient:
    """Fake storage for tests; never performs HTTP."""

    def __init__(self, objects: Mapping[tuple[str, str], bytes] | None = None):
        self.objects = dict(objects or {})
        self.calls: list[tuple[str, str, str]] = []
        self.project_url = "https://example.supabase.co"

    def get_object(self, bucket: str, object_path: str) -> bytes:
        self.calls.append(("GET", bucket, object_path))
        try:
            return self.objects[(bucket, object_path)]
        except KeyError as exc:
            raise StorageNotFound(object_path) from exc

    def create_object(self, bucket: str, object_path: str, payload: bytes, content_type: str) -> None:
        self.calls.append(("CREATE", bucket, object_path))
        if (bucket, object_path) in self.objects:
            raise StorageConflict(object_path)
        self.objects[(bucket, object_path)] = bytes(payload)

    def update_object(self, bucket: str, object_path: str, payload: bytes, content_type: str) -> None:
        self.calls.append(("UPDATE", bucket, object_path))
        self.objects[(bucket, object_path)] = bytes(payload)


class SupabaseStorageRestClient:
    """Create-only storage REST client; network defaults to disabled."""

    def __init__(self, project_url: str, service_role_key: str, *, network_enabled: bool = False, timeout: float = 20.0, retries: int = 2):
        self.project_url = project_url.rstrip("/")
        self._service_role_key = service_role_key
        self.network_enabled = network_enabled
        self.timeout = timeout
        self.retries = max(0, min(int(retries), 3))

    def _ensure_enabled(self) -> None:
        if not self.network_enabled:
            raise DeployValidationError("Supabase network đang disabled trong phase coding/test.")
        if not self.project_url or not self._service_role_key:
            raise DeployValidationError("Thiếu Supabase URL hoặc service-role key.")

    @staticmethod
    def _ssl_context() -> ssl.SSLContext:
        try:
            import certifi
            return ssl.create_default_context(cafile=certifi.where())
        except Exception:
            return ssl.create_default_context()

    def _storage_url(self, bucket: str, object_path: str) -> str:
        quoted_bucket = urlparse.quote(bucket, safe="")
        quoted_path = "/".join(urlparse.quote(part, safe="") for part in object_path.split("/"))
        return f"{self.project_url}/storage/v1/object/{quoted_bucket}/{quoted_path}"

    def _request(self, method: str, url: str, *, payload: bytes | None = None, content_type: str | None = None, retry_get: bool = False, extra_headers: Mapping[str, str] | None = None) -> bytes:
        self._ensure_enabled()
        headers = {"apikey": self._service_role_key, "Authorization": f"Bearer {self._service_role_key}"}
        if content_type:
            headers["Content-Type"] = content_type
        if extra_headers:
            headers.update(extra_headers)
        request = urlrequest.Request(url, data=payload, headers=headers, method=method)
        for attempt in range(self.retries + 1 if retry_get else 1):
            try:
                with urlrequest.urlopen(request, timeout=self.timeout, context=self._ssl_context()) as response:
                    return response.read()
            except urlerror.HTTPError as exc:
                parsed, body = _http_error_body(exc)
                if _is_object_not_found_response(exc.code, parsed, body):
                    raise StorageNotFound(url, http_status=exc.code) from None
                if exc.code == 409:
                    raise StorageConflict(url) from None
                if retry_get and exc.code >= 500 and attempt < self.retries:
                    time.sleep(0.25 * (attempt + 1))
                    continue
                detail = (body.decode("utf-8", errors="replace") if isinstance(body, bytes) else str(body)).strip()[:500]
                suffix = f" body={detail}" if detail else ""
                raise DeployValidationError(f"Supabase storage HTTP {exc.code}.{suffix}") from None
            except (urlerror.URLError, TimeoutError, OSError) as exc:
                if retry_get and attempt < self.retries:
                    time.sleep(0.25 * (attempt + 1))
                    continue
                raise DeployValidationError(f"Supabase storage request failed: {type(exc).__name__}.") from None
        raise DeployValidationError("Supabase storage request failed.")

    def get_object(self, bucket: str, object_path: str) -> bytes:
        url = self._storage_url(bucket, object_path)
        # current.json is the sole mutable object.  Bypass intermediary/CDN
        # caches after a pointer update so the final GET verifies new bytes.
        if object_path == "catalogs/vocab/current.json":
            url += f"?__pointer_verify={uuid.uuid4().hex}"
        return self._request("GET", url, retry_get=True, extra_headers={"Cache-Control": "no-cache", "Pragma": "no-cache"} if object_path == "catalogs/vocab/current.json" else None)

    def create_object(self, bucket: str, object_path: str, payload: bytes, content_type: str) -> None:
        # Supabase's regular multipart/raw POST is subject to a per-request
        # size limit.  Use the Storage TUS endpoint for large immutable ZIPs.
        resumable_threshold = int(os.environ.get("SUPABASE_RESUMABLE_THRESHOLD", str(8 * 1024 * 1024)))
        if len(payload) >= resumable_threshold:
            self._create_object_resumable(bucket, object_path, payload, content_type)
            return
        self._request("POST", self._storage_url(bucket, object_path), payload=payload, content_type=content_type, extra_headers={"x-upsert": "false"})

    def _create_object_resumable(self, bucket: str, object_path: str, payload: bytes, content_type: str) -> None:
        self._ensure_enabled()
        headers = {
            "apikey": self._service_role_key,
            "Authorization": f"Bearer {self._service_role_key}",
            "Tus-Resumable": "1.0.0",
            "Upload-Length": str(len(payload)),
            "Upload-Metadata": ",".join(
                f"{key} {base64.b64encode(value.encode('utf-8')).decode('ascii')}"
                for key, value in (
                    ("bucketName", bucket),
                    ("objectName", object_path),
                    ("contentType", content_type),
                    ("cacheControl", "3600"),
                )
            ),
            "x-upsert": "false",
            "Content-Type": "application/offset+octet-stream",
        }
        endpoint = f"{self.project_url}/storage/v1/upload/resumable"
        try:
            request = urlrequest.Request(endpoint, data=b"", headers=headers, method="POST")
            with urlrequest.urlopen(request, timeout=self.timeout, context=self._ssl_context()) as response:
                location = response.headers.get("Location")
        except urlerror.HTTPError as exc:
            parsed, body = _http_error_body(exc)
            if _is_object_not_found_response(exc.code, parsed, body):
                raise StorageNotFound(endpoint, http_status=exc.code) from None
            if exc.code == 409:
                raise StorageConflict(endpoint) from None
            detail = (body.decode("utf-8", errors="replace") if isinstance(body, bytes) else str(body)).strip()[:500]
            raise DeployValidationError(f"Supabase resumable upload HTTP {exc.code}: {detail}") from None
        if not location:
            raise DeployValidationError("Supabase resumable upload không trả Location.")
        upload_url = urlparse.urljoin(endpoint, location)
        offset = 0
        chunk_size = int(os.environ.get("SUPABASE_RESUMABLE_CHUNK_BYTES", str(8 * 1024 * 1024)))
        while offset < len(payload):
            chunk = payload[offset:offset + chunk_size]
            patch_headers = {
                "apikey": self._service_role_key,
                "Authorization": f"Bearer {self._service_role_key}",
                "Tus-Resumable": "1.0.0",
                "Upload-Offset": str(offset),
                "Content-Type": "application/offset+octet-stream",
            }
            try:
                patch_request = urlrequest.Request(upload_url, data=chunk, headers=patch_headers, method="PATCH")
                with urlrequest.urlopen(patch_request, timeout=self.timeout, context=self._ssl_context()) as response:
                    returned_offset = response.headers.get("Upload-Offset")
            except urlerror.HTTPError as exc:
                raw_detail = exc.read()
                detail = (raw_detail.decode("utf-8", errors="replace") if isinstance(raw_detail, bytes) else str(raw_detail)).strip()[:500]
                raise DeployValidationError(f"Supabase resumable PATCH HTTP {exc.code}: {detail}") from None
            offset = int(returned_offset) if returned_offset is not None else offset + len(chunk)

    def update_object(self, bucket: str, object_path: str, payload: bytes, content_type: str) -> None:
        # The signed pointer is the sole mutable object.  Callers must never
        # use this method for ZIPs, immutable catalogs, or pointer archives.
        if object_path != "catalogs/vocab/current.json":
            raise DeployValidationError("Chỉ current.json được phép update/upsert.")
        self._request("PUT", self._storage_url(bucket, object_path), payload=payload, content_type=content_type, extra_headers={"x-upsert": "true", "Cache-Control": "no-cache"})
