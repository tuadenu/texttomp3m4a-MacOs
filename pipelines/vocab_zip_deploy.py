"""Generic HSK 3.0 stage-pack and catalog-revision primitives.

This module is intentionally independent of the HSK 2.0 importer.  Staging
ZIP packs and publishing a catalog revision are separate, explicitly
confirmed operations.  Network access is injected by the UI only after user
confirmation; unit tests use ``MemoryStorageClient``.
"""

from __future__ import annotations

import copy
import hashlib
import json
import re
import ssl
import tempfile
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Mapping, Protocol
from urllib import error as urlerror
from urllib import parse as urlparse
from urllib import request as urlrequest

from pipelines.vocab_zip_builder import sha256_file, verify_pack, verify_pack_pair


SUPPORTED_LEVELS = ("hsk1", "hsk2", "hsk3", "hsk4", "hsk5", "hsk6", "hsk7_9")
STAGING_BUCKET = "vocab-pack-staging"
STANDARD_VERSION = "3.0"
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
        raise DeployValidationError(f"Level HSK 3.0 không hợp lệ: {level!r}")
    return normalized


def pack_id(level: str, segment: str) -> str:
    level = _validate_level(level)
    if segment not in {"base", "plus"}:
        raise DeployValidationError("segment phải là base hoặc plus.")
    return f"vocab:3.0:{level}:{segment}:v1"


def collection_id(level: str, segment: str) -> str:
    level = _validate_level(level)
    if segment not in {"base", "plus"}:
        raise DeployValidationError("segment phải là base hoặc plus.")
    return f"vocab_level::3.0::{level}::{segment}::v1"


def pack_object_path(level: str, segment: str) -> str:
    level = _validate_level(level)
    if segment not in {"base", "plus"}:
        raise DeployValidationError("segment phải là base hoặc plus.")
    return f"vocab/3.0/{level}/{segment}/v1/vocab_{level}_30_{segment}_v1.zip"


def catalog_object_path(revision: int) -> str:
    if int(revision) < 1:
        raise DeployValidationError("Catalog revision phải >= 1.")
    revision = int(revision)
    return f"catalogs/vocab/combined/v{revision}/vocab_pack_catalog_20_30_v{revision}.json"


def stage_confirmation_phrase(level: str) -> str:
    return f"STAGE {_validate_level(level).upper()} 3.0"


def require_stage_confirmation(level: str, value: str) -> None:
    expected = stage_confirmation_phrase(level)
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


def validate_compatibility_contract(base_zip: str | Path, plus_zip: str | Path, level: str | None = None) -> dict[str, object]:
    """Validate one BASE/PLUS pair and derive its BASE-only compatibility hash."""
    base_manifest, base_vocab = _read_pack_json(base_zip)
    plus_manifest, plus_vocab = _read_pack_json(plus_zip)
    detected_level = _validate_level(str(base_manifest.get("level", "")))
    if level is not None and detected_level != _validate_level(level):
        raise DeployValidationError("Level manifest không khớp build receipt.")
    if plus_manifest.get("level") != detected_level:
        raise DeployValidationError("BASE/PLUS manifest level không khớp.")
    if base_manifest.get("segment") != "base" or plus_manifest.get("segment") != "plus":
        raise DeployValidationError("BASE/PLUS manifest segment không đúng.")
    base_ids = _validate_ordered_ids(base_manifest, base_vocab, "BASE")
    plus_ids = _validate_ordered_ids(plus_manifest, plus_vocab, "PLUS")
    if len(base_ids) != 50 or set(base_ids).intersection(plus_ids):
        raise DeployValidationError("BASE phải có đúng 50 ID và không overlap PLUS.")
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
        "status": "PASS", "level": detected_level, "compatibilityHash": calculated,
        "baseIds": base_ids, "plusIds": plus_ids,
        "baseManifest": base_manifest, "plusManifest": plus_manifest,
    }


def input_fingerprint(excel_path: str | Path, sheet: str, level: str, output_directory: str | Path, *, bitrate: str = "32k") -> dict[str, object]:
    level = _validate_level(level)
    excel = Path(excel_path).expanduser().resolve()
    output = Path(output_directory).expanduser().resolve()
    if not excel.is_file():
        raise DeployValidationError(f"Excel không tồn tại: {excel}")
    source_audio = output / "vocab" / "3.0" / level / "source_audio"
    files = [
        {"name": path.name, "bytes": path.stat().st_size, "sha256": sha256_file(path)}
        for path in sorted(source_audio.glob("*.m4a")) if path.is_file()
    ] if source_audio.is_dir() else []
    return {
        "excelPath": str(excel), "excelBytes": excel.stat().st_size, "excelSha256": sha256_file(excel),
        "sheet": sheet, "level": level, "outputDirectory": str(output), "bitrate": bitrate,
        "sourceAudio": {"directory": str(source_audio), "files": files, "sha256": _sha256_bytes(_canonical_json_bytes(files))},
    }


def validate_local_receipt(result: Mapping[str, object] | None, config: tuple[str, str, str, str], receipt_fingerprint: Mapping[str, object] | None) -> dict[str, object]:
    if not isinstance(result, Mapping) or result.get("status") != "PASS":
        raise DeployValidationError("Phase 1 chưa PASS.")
    excel, sheet, level, output = config
    level = _validate_level(level)
    if receipt_fingerprint is None:
        raise DeployValidationError("Thiếu build receipt/input fingerprint; hãy build lại Phase 1.")
    current = input_fingerprint(excel, sheet, level, output, bitrate=str(result.get("ttsConfig", {}).get("m4a", {}).get("bitrate", "32k")))
    if dict(current) != dict(receipt_fingerprint):
        raise DeployValidationError("Excel, audio source hoặc build input đã thay đổi sau Phase 1.")
    validation_path = Path(output) / "vocab" / "3.0" / level / "validation_report.json"
    try:
        validation = json.loads(validation_path.read_text(encoding="utf-8"))
    except Exception as exc:
        raise DeployValidationError(f"Không đọc được validation_report.json: {exc}") from exc
    if validation.get("status") != "PASS":
        raise DeployValidationError("validation_report.json không PASS.")
    packs: dict[str, dict[str, object]] = {}
    for segment in ("base", "plus"):
        pack = result.get(segment)
        if not isinstance(pack, Mapping):
            raise DeployValidationError(f"Thiếu build receipt {segment.upper()}.")
        local_path = Path(str(pack.get("zip", "")))
        actual_bytes = local_path.stat().st_size if local_path.is_file() else -1
        actual_sha = sha256_file(local_path) if local_path.is_file() else ""
        if actual_bytes != pack.get("bytes") or actual_sha != pack.get("sha256"):
            raise DeployValidationError(f"ZIP {segment.upper()} không khớp bytes/SHA trong receipt.")
        if str(result.get("objectPaths", {}).get(segment, "")) != pack_object_path(level, segment):
            raise DeployValidationError(f"Object path {segment.upper()} không đúng level.")
        packs[segment] = {"localPath": str(local_path), "bytes": actual_bytes, "sha256": actual_sha}
    try:
        verify_pack_pair(packs["base"]["localPath"], packs["plus"]["localPath"], level)
    except Exception as exc:
        raise DeployValidationError(f"BASE/PLUS verify thất bại: {exc}") from exc
    contract = validate_compatibility_contract(packs["base"]["localPath"], packs["plus"]["localPath"], level)
    return {"status": "PASS", "fingerprint": current, "packs": packs, "compatibility": contract}


def build_plan(result: Mapping[str, object] | None, config: tuple[str, str, str, str], receipt_fingerprint: Mapping[str, object] | None, profile: Mapping[str, object], *, profile_name: str = "") -> DeployPlan:
    verified = validate_local_receipt(result, config, receipt_fingerprint)
    url = str(profile.get("SUPABASE_URL", "") or "").strip().rstrip("/")
    bucket = str(profile.get("SUPABASE_BUCKET", "") or "").strip()
    key = str(profile.get("SUPABASE_SERVICE_ROLE_KEY", "") or "").strip()
    if not url or not bucket or not key:
        raise DeployValidationError("Supabase profile thiếu URL, bucket hoặc service-role key.")
    level = str(verified["compatibility"]["level"])
    base, plus = verified["packs"]["base"], verified["packs"]["plus"]
    contract = verified["compatibility"]
    return DeployPlan(
        profile_name=profile_name, project_url=url, bucket=STAGING_BUCKET, level=level, pack_version=PACK_VERSION,
        base_local_path=base["localPath"], base_bytes=base["bytes"], base_sha256=base["sha256"], base_object_path=pack_object_path(level, "base"),
        plus_local_path=plus["localPath"], plus_bytes=plus["bytes"], plus_sha256=plus["sha256"], plus_object_path=pack_object_path(level, "plus"),
        compatibility_hash=str(contract["compatibilityHash"]), base_manifest=dict(contract["baseManifest"]), plus_manifest=dict(contract["plusManifest"]),
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
    if len(identities) != len(set(identities)) or len(pack_ids) != len(set(pack_ids)) or len(collection_ids) != len(set(collection_ids)):
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
    manifest = plan.base_manifest if segment == "base" else plan.plus_manifest
    return {
        "version": STANDARD_VERSION, "level": plan.level, "segment": segment,
        "packId": pack_id(plan.level, segment), "collectionId": collection_id(plan.level, segment),
        "packVersion": int(manifest.get("packVersion", 0)), "vocabCount": int(manifest.get("vocabCount", 0)),
        "audioCount": sum(1 for resource in manifest.get("resources", []) if isinstance(resource, Mapping) and resource.get("type") == "vocab_audio"),
        "objectPath": pack_object_path(plan.level, segment), "filename": f"vocab_{plan.level}_30_{segment}_v1.zip",
        "sha256": plan.base_sha256 if segment == "base" else plan.plus_sha256,
        "zipBytes": plan.base_bytes if segment == "base" else plan.plus_bytes,
        "compatibilityHash": plan.compatibility_hash, "accessTier": "base" if segment == "base" else "vip", "enabled": True,
    }


def deploy_receipt_path(output_directory: str | Path, level: str) -> Path:
    return Path(output_directory) / "vocab" / "3.0" / _validate_level(level) / "deploy_receipt.json"


def _write_deploy_receipt(output_directory: str | Path, plan: DeployPlan) -> dict[str, object]:
    base_entry, plus_entry = catalog_entry_from_plan(plan, "base"), catalog_entry_from_plan(plan, "plus")
    receipt = {
        "schemaVersion": 1, "version": STANDARD_VERSION, "level": plan.level, "packVersion": plan.pack_version,
        "timestamp": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"), "compatibilityHash": plan.compatibility_hash,
        "base": {**base_entry, "remoteVerified": True}, "plus": {**plus_entry, "remoteVerified": True},
    }
    path = deploy_receipt_path(output_directory, plan.level)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(_canonical_json_bytes(receipt))
    return receipt


def validate_deploy_receipt(receipt: Mapping[str, object]) -> dict[str, object]:
    if receipt.get("schemaVersion") != 1 or receipt.get("version") != STANDARD_VERSION:
        raise DeployValidationError("Deploy receipt sai schema/version.")
    level = _validate_level(str(receipt.get("level", "")))
    if receipt.get("packVersion") != PACK_VERSION:
        raise DeployValidationError("Deploy receipt packVersion không hỗ trợ.")
    compatibility = receipt.get("compatibilityHash")
    if not isinstance(compatibility, str) or len(compatibility) != 64:
        raise DeployValidationError("Deploy receipt thiếu compatibilityHash.")
    entries: list[dict[str, object]] = []
    for segment in ("base", "plus"):
        entry = receipt.get(segment)
        if not isinstance(entry, Mapping) or entry.get("remoteVerified") is not True:
            raise DeployValidationError(f"Deploy receipt {segment.upper()} chưa remoteVerified.")
        required = {"packId": pack_id(level, segment), "collectionId": collection_id(level, segment), "objectPath": pack_object_path(level, segment)}
        if any(entry.get(key) != value for key, value in required.items()):
            raise DeployValidationError(f"Deploy receipt {segment.upper()} identity/path không đúng.")
        if entry.get("compatibilityHash") != compatibility or not isinstance(entry.get("sha256"), str) or not isinstance(entry.get("zipBytes"), int):
            raise DeployValidationError(f"Deploy receipt {segment.upper()} thiếu SHA/bytes/hash.")
        item = {key: value for key, value in entry.items() if key != "remoteVerified"}
        entries.append(item)
    if entries[0]["compatibilityHash"] != entries[1]["compatibilityHash"]:
        raise DeployValidationError("Deploy receipt BASE/PLUS compatibilityHash không giống nhau.")
    return {"level": level, "entries": entries, "receipt": dict(receipt)}


def collect_deploy_receipts(output_directory: str | Path) -> list[dict[str, object]]:
    root = Path(output_directory) / "vocab" / "3.0"
    receipts: list[dict[str, object]] = []
    for level in SUPPORTED_LEVELS:
        path = root / level / "deploy_receipt.json"
        if not path.is_file():
            continue
        try:
            receipt = json.loads(path.read_text(encoding="utf-8"))
        except Exception as exc:
            raise DeployValidationError(f"Không đọc được deploy receipt {path}: {exc}") from exc
        validated = validate_deploy_receipt(receipt)
        validated["path"] = str(path)
        receipts.append(validated)
    return receipts


def _snapshot_path(output_directory: str | Path, revision: int) -> Path:
    return Path(output_directory) / "vocab" / "3.0" / "catalog_revisions" / f"v{revision}" / f"vocab_pack_catalog_20_30_v{revision}.json"


def load_catalog_source_snapshot(output_directory: str | Path) -> CatalogSnapshot:
    root = Path(output_directory) / "vocab" / "3.0"
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
        revision, path = 1, root / "hsk1" / "deploy_preflight" / "combined_catalog_dry_run.json"
    try:
        payload = path.read_bytes()
    except OSError as exc:
        raise DeployValidationError(f"Thiếu snapshot catalog revision local: {path}") from exc
    catalog = verify_catalog_payload(payload)
    return CatalogSnapshot(revision, str(path), catalog_object_path(revision), payload, _sha256_bytes(payload), len(_validate_catalog_entries(catalog)), catalog)


def merge_catalog(source: Mapping[str, object], additions: list[Mapping[str, object]]) -> dict[str, object]:
    """Deep-copy source exactly, then append entries that have new identities."""
    result = copy.deepcopy(dict(source))
    key = _entries_key(result)
    entries = result[key]
    assert isinstance(entries, list)
    if not additions:
        raise DeployValidationError("Không có receipt staged mới để publish catalog.")
    result[key] = entries + [dict(entry) for entry in additions]
    _validate_catalog_entries(result)
    if result[key][:len(entries)] != entries:
        raise DeployValidationError("Catalog nguồn bị thay đổi khi merge.")
    return result


def prepare_catalog_publish(output_directory: str | Path) -> CatalogPublishPlan:
    source = load_catalog_source_snapshot(output_directory)
    source_entries = _validate_catalog_entries(source.catalog)
    by_identity = {_identity(entry): dict(entry) for entry in source_entries}
    additions: list[dict[str, object]] = []
    receipts = collect_deploy_receipts(output_directory)
    for receipt in receipts:
        for entry in receipt["entries"]:
            identity = _identity(entry)
            existing = by_identity.get(identity)
            if existing is not None:
                if existing != entry:
                    raise DeployValidationError(f"Receipt conflict với catalog hiện hành: {identity}")
                continue
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


def _verify_zip_payload(payload: bytes, expected_sha: str, expected_bytes: int, level: str, segment: str) -> None:
    _verify_bytes(payload, expected_sha, expected_bytes, f"ZIP {segment}")
    with tempfile.NamedTemporaryFile(prefix=f"hsk30-remote-{level}-{segment}-", suffix=".zip") as temp_file:
        temp_file.write(payload)
        temp_file.flush()
        verify_pack(temp_file.name, level, segment)


def _probe_log(progress: Callable[[str], None] | None, object_path: str, status: object, classification: str) -> None:
    if progress:
        progress(f"step=probe_object object={object_path} http_status={status} classification={classification}")


def _http_status_from_error(exc: BaseException) -> object:
    match = re.search(r"HTTP (\d+)", str(exc))
    return int(match.group(1)) if match else "unknown"


def _ensure_zip_object(client: StorageClient, plan: DeployPlan, segment: str, progress: Callable[[str], None] | None = None) -> None:
    object_path = plan.base_object_path if segment == "base" else plan.plus_object_path
    local_path = plan.base_local_path if segment == "base" else plan.plus_local_path
    expected_sha = plan.base_sha256 if segment == "base" else plan.plus_sha256
    expected_bytes = plan.base_bytes if segment == "base" else plan.plus_bytes
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
            _verify_zip_payload(existing, expected_sha, expected_bytes, plan.level, segment)
        except Exception:
            _probe_log(progress, object_path, 200, "PRESENT_CONFLICT")
            raise
        _probe_log(progress, object_path, 200, "PRESENT_MATCH")
    downloaded = client.get_object(plan.bucket, object_path)
    _verify_zip_payload(downloaded, expected_sha, expected_bytes, plan.level, segment)
    if progress:
        progress(f"GET verify {plan.level.upper()} {segment.upper()}: PASS")


def stage_packs_with_client(client: StorageClient, plan: DeployPlan, *, confirmation: str, output_directory: str | Path, progress: Callable[[str], None] | None = None) -> dict[str, object]:
    """Stage BASE and PLUS only.  It never creates or uploads a catalog."""
    require_stage_confirmation(plan.level, confirmation)
    completed: list[str] = []
    try:
        _ensure_zip_object(client, plan, "base", progress)
        completed.append(plan.base_object_path)
        _ensure_zip_object(client, plan, "plus", progress)
        completed.append(plan.plus_object_path)
    except Exception as exc:
        if completed:
            raise PartialDeployError(f"PARTIAL: staged {completed}; catalog chưa publish: {exc}", completed) from exc
        raise
    receipt = _write_deploy_receipt(output_directory, plan)
    if progress:
        progress(f"REMOTE PACKS VERIFIED: {plan.level.upper()} | CATALOG NOT PUBLISHED")
    return {"status": "REMOTE PACKS VERIFIED", "receipt": receipt, "receiptPath": str(deploy_receipt_path(output_directory, plan.level))}


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
                raise DeployValidationError(f"Supabase storage HTTP {exc.code}.") from None
            except (urlerror.URLError, TimeoutError, OSError) as exc:
                if retry_get and attempt < self.retries:
                    time.sleep(0.25 * (attempt + 1))
                    continue
                raise DeployValidationError(f"Supabase storage request failed: {type(exc).__name__}.") from None
        raise DeployValidationError("Supabase storage request failed.")

    def get_object(self, bucket: str, object_path: str) -> bytes:
        return self._request("GET", self._storage_url(bucket, object_path), retry_get=True)

    def create_object(self, bucket: str, object_path: str, payload: bytes, content_type: str) -> None:
        self._request("POST", self._storage_url(bucket, object_path), payload=payload, content_type=content_type, extra_headers={"x-upsert": "false"})
