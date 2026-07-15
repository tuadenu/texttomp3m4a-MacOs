"""HSK 3.0 Phase 2 deploy/verify/publish primitives.

This module is deliberately independent from the legacy HSK 2.0 importer.  It
contains the gate, deterministic catalog merge and create-only storage
orchestration, but it does not create a network client by default.  Production
network access must be explicitly injected by a later, approved UI action.
"""

from __future__ import annotations

import copy
import hashlib
import json
import os
import re
import tempfile
import time
import ssl
from urllib import error as urlerror
from urllib import parse as urlparse
from urllib import request as urlrequest
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Mapping, Protocol

from pipelines.vocab_zip_builder import sha256_file, verify_pack_pair


PILOT_LEVEL = "hsk1"
PILOT_BUCKET = "vocab-pack-staging"
CATALOG_SOURCE_PATH = "input/vocab_pack_catalog_all_enabled_rollout.json"
CATALOG_SOURCE_URL = CATALOG_SOURCE_PATH
CATALOG_SOURCE_BYTES = 6277
CATALOG_SOURCE_SHA256 = "18cd2d70a0c90187bf32e7fb21c0249db6b6bc64bbc7207385d04ee2c0ef8c98"
CATALOG_TARGET_PATH = "catalogs/vocab/combined/v1/vocab_pack_catalog_20_30_v1.json"
BASE_OBJECT_PATH = "vocab/3.0/hsk1/base/v1/vocab_hsk1_30_base_v1.zip"
PLUS_OBJECT_PATH = "vocab/3.0/hsk1/plus/v1/vocab_hsk1_30_plus_v1.zip"
CONFIRMATION_PHRASE = "PUBLISH HSK1 3.0"


class DeployValidationError(ValueError):
    """A local deploy gate or catalog validation failure."""


class CompatibilityRuleUnavailable(DeployValidationError):
    """The existing catalog's compatibilityHash rule has not been proven."""


class StorageNotFound(FileNotFoundError):
    def __init__(self, path: str, http_status: int = 404):
        super().__init__(path)
        self.path = path
        self.http_status = http_status


class StorageConflict(RuntimeError):
    pass


class PartialDeployError(DeployValidationError):
    """Remote ZIPs may exist while the catalog activation is still absent."""

    def __init__(self, message: str, completed_objects: list[str]):
        super().__init__(message)
        self.completed_objects = tuple(completed_objects)
        self.status = "PARTIAL"


class StorageClient(Protocol):
    """Minimal storage contract; implementations are injected, never guessed."""

    def get_object(self, bucket: str, object_path: str) -> bytes: ...

    def create_object(self, bucket: str, object_path: str, payload: bytes, content_type: str) -> None: ...

    def get_url(self, url: str) -> bytes: ...


class ReadOnlyStorageClient(Protocol):
    """The deliberately smaller contract used by the preflight audit."""

    methods: list[str]

    def get_object(self, bucket: str, object_path: str) -> bytes: ...

    def get_url(self, url: str) -> bytes: ...


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
    base_ordered_ids: tuple[str, ...]
    catalog_source_url: str
    catalog_source_bytes: int
    catalog_source_sha256: str
    catalog_target_path: str


def _sha256_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _http_error_body(exc: urlerror.HTTPError) -> tuple[dict[str, object] | None, str]:
    try:
        body = exc.read(4096).decode("utf-8", errors="replace")
    except Exception:
        return None, ""
    try:
        parsed = json.loads(body)
    except Exception:
        parsed = None
    return parsed if isinstance(parsed, dict) else None, body


def _is_object_not_found_response(status: int, parsed: dict[str, object] | None, body: str) -> bool:
    if status == 404:
        return True
    if status != 400:
        return False
    status_code = parsed.get("statusCode") if parsed else None
    error_code = parsed.get("error") if parsed else None
    message = str(parsed.get("message", "")) if parsed else body
    return status_code == 404 or status_code == "404" or error_code == "not_found" or "Object not found" in message


def _http_status_from_error(exc: BaseException) -> object:
    match = re.search(r"HTTP (\d+)", str(exc))
    return int(match.group(1)) if match else "unknown"


def _probe_log(progress: Callable[[str], None] | None, object_path: str, status: object, classification: str) -> None:
    if progress:
        progress(
            f"step=probe_object object={object_path} http_status={status} classification={classification}"
        )


def _canonical_json_bytes(value: object) -> bytes:
    return (json.dumps(value, ensure_ascii=False, separators=(",", ":")) + "\n").encode("utf-8")


def compatibility_hash_from_ids(ids: list[str]) -> str:
    """Hash BASE stable IDs exactly as the Flutter/HSK2 contract specifies."""
    if not isinstance(ids, list) or any(not isinstance(value, str) or not value for value in ids):
        raise DeployValidationError("BASE orderedVocabIds phải là list string không rỗng.")
    payload = json.dumps(
        ids,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=False,
    ).encode("utf-8")
    return _sha256_bytes(payload)


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
    if len(ordered) != len(vocab):
        raise DeployValidationError(f"{label} orderedVocabIds không khớp vocab count.")
    if len(set(ordered)) != len(ordered):
        raise DeployValidationError(f"{label} orderedVocabIds bị trùng.")
    try:
        sorted_vocab = sorted(vocab, key=lambda item: int(item["index"]))
    except (KeyError, TypeError, ValueError) as exc:
        raise DeployValidationError(f"{label} vocab thiếu index hợp lệ.") from exc
    expected = []
    for item in sorted_vocab:
        item_id = item.get("id")
        if not isinstance(item_id, str) or not item_id:
            raise DeployValidationError(f"{label} vocab ID phải là string không rỗng.")
        expected.append(item_id)
    if ordered != expected:
        raise DeployValidationError(f"{label} orderedVocabIds không đúng thứ tự/index stable ID.")
    return ordered


def validate_compatibility_contract(base_zip: str | Path, plus_zip: str | Path) -> dict[str, object]:
    """Verify BASE/PLUS IDs and derive one compatibility hash from BASE only."""
    base_manifest, base_vocab = _read_pack_json(base_zip)
    plus_manifest, plus_vocab = _read_pack_json(plus_zip)
    if base_manifest.get("segment") != "base" or plus_manifest.get("segment") != "plus":
        raise DeployValidationError("BASE/PLUS manifest segment không đúng.")
    if base_manifest.get("level") != PILOT_LEVEL or plus_manifest.get("level") != PILOT_LEVEL:
        raise DeployValidationError("Compatibility pilot chỉ hỗ trợ hsk1.")
    base_ids = _validate_ordered_ids(base_manifest, base_vocab, "BASE")
    plus_ids = _validate_ordered_ids(plus_manifest, plus_vocab, "PLUS")
    if len(base_ids) != 50:
        raise DeployValidationError("BASE orderedVocabIds phải có đúng 50 ID.")
    if set(base_ids).intersection(plus_ids):
        raise DeployValidationError("BASE/PLUS stable IDs bị overlap.")
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
        "status": "PASS",
        "compatibilityHash": calculated,
        "baseIds": base_ids,
        "plusIds": plus_ids,
        "baseManifest": base_manifest,
        "plusManifest": plus_manifest,
    }


def _redact(value: object) -> str:
    text = str(value)
    return "<redacted>" if text else "<missing>"


def input_fingerprint(
    excel_path: str | Path,
    sheet: str,
    level: str,
    output_directory: str | Path,
    *,
    bitrate: str = "32k",
) -> dict[str, object]:
    """Hash all build inputs used by the deploy gate, including source audio."""
    excel = Path(excel_path).expanduser().resolve()
    output = Path(output_directory).expanduser().resolve()
    if not excel.is_file():
        raise DeployValidationError(f"Excel không tồn tại: {excel}")
    source_audio = output / "vocab" / "3.0" / level / "source_audio"
    audio_files: list[dict[str, object]] = []
    if source_audio.is_dir():
        for path in sorted(source_audio.glob("*.m4a")):
            if path.is_file():
                audio_files.append(
                    {"name": path.name, "bytes": path.stat().st_size, "sha256": sha256_file(path)}
                )
    audio_digest = _sha256_bytes(_canonical_json_bytes(audio_files))
    return {
        "excelPath": str(excel),
        "excelBytes": excel.stat().st_size,
        "excelSha256": sha256_file(excel),
        "sheet": sheet,
        "level": level,
        "outputDirectory": str(output),
        "bitrate": bitrate,
        "sourceAudio": {"directory": str(source_audio), "files": audio_files, "sha256": audio_digest},
    }


def _profile_value(profile: Mapping[str, object], key: str) -> str:
    return str(profile.get(key, "") or "").strip()


def validate_local_receipt(
    result: Mapping[str, object] | None,
    config: tuple[str, str, str, str],
    receipt_fingerprint: Mapping[str, object] | None,
) -> dict[str, object]:
    """Revalidate the build receipt and reject any changed input or artifact."""
    if not isinstance(result, Mapping) or result.get("status") != "PASS":
        raise DeployValidationError("Phase 1 chưa PASS.")
    excel, sheet, level, output = config
    if level != PILOT_LEVEL:
        raise DeployValidationError("Pilot Phase 2 hiện chỉ cho phép level hsk1.")
    if receipt_fingerprint is None:
        raise DeployValidationError("Thiếu build receipt/input fingerprint; hãy build lại Phase 1.")
    current_fingerprint = input_fingerprint(
        excel, sheet, level, output, bitrate=str(result.get("ttsConfig", {}).get("m4a", {}).get("bitrate", "32k"))
    )
    if dict(current_fingerprint) != dict(receipt_fingerprint):
        raise DeployValidationError("Excel, audio source hoặc build input đã thay đổi sau Phase 1.")
    validation_path = Path(output) / "vocab" / "3.0" / level / "validation_report.json"
    if not validation_path.is_file():
        raise DeployValidationError("Thiếu validation_report.json.")
    try:
        validation = json.loads(validation_path.read_text(encoding="utf-8"))
    except Exception as exc:
        raise DeployValidationError(f"Không đọc được validation_report.json: {exc}") from exc
    if validation.get("status") != "PASS":
        raise DeployValidationError("validation_report.json không PASS.")

    packs: dict[str, dict[str, object]] = {}
    for segment, expected_path, expected_sha, expected_bytes in (
        ("base", BASE_OBJECT_PATH, result.get("base", {}).get("sha256"), result.get("base", {}).get("bytes")),
        ("plus", PLUS_OBJECT_PATH, result.get("plus", {}).get("sha256"), result.get("plus", {}).get("bytes")),
    ):
        pack = result.get(segment)
        if not isinstance(pack, Mapping):
            raise DeployValidationError(f"Thiếu build receipt {segment.upper()}.")
        local_path = Path(str(pack.get("zip", "")))
        if not local_path.is_file():
            raise DeployValidationError(f"ZIP {segment.upper()} không tồn tại.")
        actual_bytes = local_path.stat().st_size
        actual_sha = sha256_file(local_path)
        if actual_bytes != expected_bytes or actual_sha != expected_sha:
            raise DeployValidationError(f"ZIP {segment.upper()} không khớp bytes/SHA trong receipt.")
        if str(result.get("objectPaths", {}).get(segment, "")) != expected_path:
            raise DeployValidationError(f"Object path {segment.upper()} không đúng pilot.")
        packs[segment] = {
            "localPath": str(local_path),
            "bytes": actual_bytes,
            "sha256": actual_sha,
            "manifest": pack.get("manifest"),
            "vocab": pack.get("vocab"),
        }
    try:
        verify_pack_pair(packs["base"]["localPath"], packs["plus"]["localPath"], PILOT_LEVEL)
    except Exception as exc:
        raise DeployValidationError(f"BASE/PLUS verify thất bại: {exc}") from exc
    compatibility = validate_compatibility_contract(packs["base"]["localPath"], packs["plus"]["localPath"])
    return {"status": "PASS", "fingerprint": dict(current_fingerprint), "packs": packs, "compatibility": compatibility}


def build_plan(
    result: Mapping[str, object] | None,
    config: tuple[str, str, str, str],
    receipt_fingerprint: Mapping[str, object] | None,
    profile: Mapping[str, object],
    *,
    profile_name: str = "",
) -> DeployPlan:
    verified = validate_local_receipt(result, config, receipt_fingerprint)
    url = _profile_value(profile, "SUPABASE_URL").rstrip("/")
    profile_bucket = _profile_value(profile, "SUPABASE_BUCKET")
    key = _profile_value(profile, "SUPABASE_SERVICE_ROLE_KEY")
    if not url:
        raise DeployValidationError("Supabase URL đang trống.")
    if not profile_bucket:
        raise DeployValidationError("Supabase bucket đang trống.")
    if not key:
        raise DeployValidationError("Supabase service-role key đang trống.")
    base = verified["packs"]["base"]
    plus = verified["packs"]["plus"]
    compatibility = verified["compatibility"]
    return DeployPlan(
        profile_name=profile_name,
        project_url=url,
        # HSK 3.0 uses its explicitly approved staging bucket.  The legacy
        # profile's bucket (often ``audio``) is read-only metadata here and is
        # not changed or reused for the new pack workflow.
        bucket=PILOT_BUCKET,
        level=PILOT_LEVEL,
        pack_version=1,
        base_local_path=base["localPath"],
        base_bytes=base["bytes"],
        base_sha256=base["sha256"],
        base_object_path=BASE_OBJECT_PATH,
        plus_local_path=plus["localPath"],
        plus_bytes=plus["bytes"],
        plus_sha256=plus["sha256"],
        plus_object_path=PLUS_OBJECT_PATH,
        compatibility_hash=compatibility["compatibilityHash"],
        base_ordered_ids=tuple(compatibility["baseIds"]),
        catalog_source_url=CATALOG_SOURCE_URL,
        catalog_source_bytes=CATALOG_SOURCE_BYTES,
        catalog_source_sha256=CATALOG_SOURCE_SHA256,
        catalog_target_path=CATALOG_TARGET_PATH,
    )


def require_confirmation(value: str) -> None:
    if value != CONFIRMATION_PHRASE:
        raise DeployValidationError("Xác nhận không đúng; chưa có remote request nào được gọi.")


def _entries_key(catalog: Mapping[str, object]) -> str:
    candidates = [key for key in ("entries", "packs", "collections") if isinstance(catalog.get(key), list)]
    if len(candidates) != 1:
        raise DeployValidationError("Không xác định duy nhất field entry của catalog hiện hành.")
    return candidates[0]


def verify_source_catalog(payload: bytes) -> dict[str, object]:
    if len(payload) != CATALOG_SOURCE_BYTES or _sha256_bytes(payload) != CATALOG_SOURCE_SHA256:
        raise DeployValidationError("Catalog nguồn sai bytes/SHA; dừng trước mọi upload.")
    try:
        catalog = json.loads(payload.decode("utf-8"))
    except Exception as exc:
        raise DeployValidationError(f"Catalog nguồn không phải JSON hợp lệ: {exc}") from exc
    if not isinstance(catalog, dict) or catalog.get("schemaVersion") != 1:
        raise DeployValidationError("Catalog nguồn phải có schemaVersion 1.")
    key = _entries_key(catalog)
    entries = catalog[key]
    if not isinstance(entries, list) or len(entries) != 12:
        raise DeployValidationError("Catalog nguồn phải có đúng 12 entry HSK 2.0 hiện hành.")
    if any(not isinstance(entry, Mapping) or entry.get("enabled") is not True for entry in entries):
        raise DeployValidationError("Catalog nguồn phải có đủ 12 entry enabled=true.")
    pack_ids = [entry.get("packId") for entry in entries]
    collection_ids = [entry.get("collectionId") for entry in entries]
    if any(not isinstance(value, str) or not value for value in pack_ids):
        raise DeployValidationError("Catalog nguồn thiếu packId hợp lệ.")
    if any(not isinstance(value, str) or not value for value in collection_ids):
        raise DeployValidationError("Catalog nguồn thiếu collectionId hợp lệ.")
    if len(set(pack_ids)) != len(pack_ids) or len(set(collection_ids)) != len(collection_ids):
        raise DeployValidationError("Catalog nguồn bị duplicate packId/collectionId.")
    identities = [_identity(entry) for entry in entries if isinstance(entry, Mapping)]
    if len(identities) != len(entries) or len(identities) != len(set(identities)):
        raise DeployValidationError("Catalog nguồn có entry sai kiểu hoặc duplicate identity.")
    return catalog


def load_local_source_catalog(base_directory: str | Path = ".") -> tuple[bytes, dict[str, object]]:
    path = Path(base_directory) / CATALOG_SOURCE_PATH
    try:
        payload = path.read_bytes()
    except OSError as exc:
        raise DeployValidationError(f"Thiếu catalog HSK 2.0 local: {path}") from exc
    return payload, verify_source_catalog(payload)


def _identity(entry: Mapping[str, object]) -> tuple[object, object, object]:
    return (entry.get("version"), entry.get("level"), entry.get("segment"))


def catalog_entry_from_manifest(
    manifest: Mapping[str, object],
    *,
    sha256: str,
    zip_bytes: int,
    compatibility_hash: str | None,
) -> dict[str, object]:
    if not compatibility_hash:
        raise CompatibilityRuleUnavailable(
            "Chưa chứng minh được rule compatibilityHash từ catalog HSK 2.0 thật; không tạo entry/upload."
        )
    segment = str(manifest.get("segment", ""))
    if segment not in {"base", "plus"}:
        raise DeployValidationError("Manifest segment không hợp lệ.")
    level = str(manifest.get("level", ""))
    count = int(manifest.get("vocabCount", 0))
    return {
        "version": "3.0",
        "level": level,
        "segment": segment,
        "packId": manifest.get("packId"),
        "collectionId": f"vocab_level::3.0::{level}::{segment}::v1",
        "packVersion": int(manifest.get("packVersion", 0)),
        "vocabCount": count,
        "audioCount": sum(1 for resource in manifest.get("resources", []) if resource.get("type") == "vocab_audio"),
        "objectPath": f"vocab/3.0/{level}/{segment}/v1/vocab_{level}_30_{segment}_v1.zip",
        "filename": f"vocab_{level}_30_{segment}_v1.zip",
        "sha256": sha256,
        "zipBytes": zip_bytes,
        "compatibilityHash": compatibility_hash,
        "accessTier": "base" if segment == "base" else "vip",
        "enabled": True,
    }


def merge_catalog(
    source: Mapping[str, object],
    base_entry: Mapping[str, object],
    plus_entry: Mapping[str, object],
) -> dict[str, object]:
    """Copy the verified source catalog and append exactly two new entries."""
    result = copy.deepcopy(dict(source))
    key = _entries_key(result)
    entries = result[key]
    assert isinstance(entries, list)
    existing_ids = {_identity(entry) for entry in entries if isinstance(entry, Mapping)}
    additions = [dict(base_entry), dict(plus_entry)]
    if any(_identity(entry) in existing_ids for entry in additions):
        raise DeployValidationError("Catalog đã có identity HSK1 3.0; không overwrite.")
    if _identity(additions[0]) == _identity(additions[1]):
        raise DeployValidationError("Hai entry HSK1 3.0 bị trùng identity.")
    hashes = {str(entry.get("compatibilityHash", "")) for entry in additions}
    if len(hashes) != 1 or "" in hashes:
        raise DeployValidationError("BASE/PLUS catalog compatibilityHash phải giống nhau.")
    result[key] = entries + additions
    validate_combined_catalog(result, source, additions)
    return result


def validate_combined_catalog(
    catalog: Mapping[str, object],
    source: Mapping[str, object],
    additions: list[Mapping[str, object]],
) -> None:
    if catalog.get("schemaVersion") != 1:
        raise DeployValidationError("Catalog combined sai schemaVersion.")
    source_key = _entries_key(source)
    key = _entries_key(catalog)
    source_entries = source[source_key]
    entries = catalog[key]
    if not isinstance(source_entries, list) or not isinstance(entries, list):
        raise DeployValidationError("Catalog entries sai kiểu.")
    if entries[: len(source_entries)] != source_entries:
        raise DeployValidationError("Entry HSK 2.0 không được giữ nguyên thứ tự/nội dung.")
    if len(entries) != len(source_entries) + 2:
        raise DeployValidationError("Catalog combined phải thêm đúng hai entry.")
    identities = [_identity(entry) for entry in entries if isinstance(entry, Mapping)]
    if len(identities) != len(set(identities)):
        raise DeployValidationError("Catalog có duplicate identity.")
    pack_ids = [entry.get("packId") for entry in entries if isinstance(entry, Mapping)]
    collection_ids = [entry.get("collectionId") for entry in entries if isinstance(entry, Mapping)]
    if len(pack_ids) != len(set(pack_ids)) or len(collection_ids) != len(set(collection_ids)):
        raise DeployValidationError("Catalog có duplicate packId/collectionId.")
    expected = {(_identity(entry)) for entry in additions}
    actual = {identity for identity in identities if identity[0] == "3.0" and identity[1] == "hsk1"}
    if actual != expected:
        raise DeployValidationError("Catalog thiếu hoặc sai entry HSK1 3.0.")
    for entry in additions:
        if not entry.get("enabled") or not entry.get("compatibilityHash"):
            raise DeployValidationError("Entry HSK1 3.0 thiếu enabled/compatibilityHash.")
    if len({entry.get("compatibilityHash") for entry in additions}) != 1:
        raise DeployValidationError("BASE/PLUS catalog compatibilityHash phải giống nhau.")


def build_catalog_dry_run(
    source_payload: bytes,
    base_zip: str | Path,
    plus_zip: str | Path,
    *,
    base_sha256: str | None = None,
    plus_sha256: str | None = None,
) -> dict[str, object]:
    """Build a deterministic combined catalog in memory; never calls storage."""
    source = verify_source_catalog(source_payload)
    contract = validate_compatibility_contract(base_zip, plus_zip)
    base_sha256 = base_sha256 or sha256_file(Path(base_zip))
    plus_sha256 = plus_sha256 or sha256_file(Path(plus_zip))
    base_entry = catalog_entry_from_manifest(
        contract["baseManifest"],
        sha256=base_sha256,
        zip_bytes=Path(base_zip).stat().st_size,
        compatibility_hash=contract["compatibilityHash"],
    )
    plus_entry = catalog_entry_from_manifest(
        contract["plusManifest"],
        sha256=plus_sha256,
        zip_bytes=Path(plus_zip).stat().st_size,
        compatibility_hash=contract["compatibilityHash"],
    )
    combined = merge_catalog(source, base_entry, plus_entry)
    payload = _canonical_json_bytes(combined)
    entries = combined[_entries_key(combined)]
    hsk30_entries = [entry for entry in entries if isinstance(entry, Mapping) and entry.get("version") == "3.0" and entry.get("level") == PILOT_LEVEL]
    identities = [_identity(entry) for entry in entries if isinstance(entry, Mapping)]
    return {
        "status": "PASS",
        "catalog": combined,
        "bytes": len(payload),
        "sha256": _sha256_bytes(payload),
        "payload": payload,
        "legacyEntryCount": len(entries) - len(hsk30_entries),
        "hsk30EntryCount": len(hsk30_entries),
        "duplicateIdentityCount": len(identities) - len(set(identities)),
        "compatibilityHash": contract["compatibilityHash"],
        "baseIds": contract["baseIds"],
    }


def _verify_bytes(actual: bytes, expected_sha: str, expected_bytes: int, label: str) -> None:
    if len(actual) != expected_bytes or _sha256_bytes(actual) != expected_sha:
        raise DeployValidationError(f"Remote {label} sai bytes/SHA; không overwrite.")


def _verify_zip_payload(payload: bytes, expected_sha: str, expected_bytes: int, segment: str) -> None:
    _verify_bytes(payload, expected_sha, expected_bytes, f"ZIP {segment}")
    with tempfile.NamedTemporaryFile(prefix=f"hsk30-remote-{segment}-", suffix=".zip") as temp_file:
        temp_file.write(payload)
        temp_file.flush()
        from pipelines.vocab_zip_builder import verify_pack

        verify_pack(temp_file.name, PILOT_LEVEL, segment)


def _ensure_object(client: StorageClient, plan: DeployPlan, object_path: str, local_path: str, expected_sha: str, expected_bytes: int, label: str, segment: str, progress: Callable[[str], None] | None = None) -> None:
    try:
        existing = client.get_object(plan.bucket, object_path)
    except StorageNotFound as exc:
        _probe_log(progress, object_path, exc.http_status, "ABSENT")
        payload = Path(local_path).read_bytes()
        _verify_bytes(payload, expected_sha, expected_bytes, f"local {label}")
        client.create_object(plan.bucket, object_path, payload, "application/zip")
    except Exception as exc:
        _probe_log(progress, object_path, _http_status_from_error(exc), "ERROR")
        raise
    else:
        try:
            _verify_zip_payload(existing, expected_sha, expected_bytes, segment)
        except Exception:
            _probe_log(progress, object_path, 200, "PRESENT_CONFLICT")
            raise
        _probe_log(progress, object_path, 200, "PRESENT_MATCH")
        if progress:
            progress(f"Remote {label} đã tồn tại cùng SHA — reuse")
    downloaded = client.get_object(plan.bucket, object_path)
    _verify_zip_payload(downloaded, expected_sha, expected_bytes, segment)
    if progress:
        progress(f"GET verify {label}: PASS")


def deploy_with_client(
    client: StorageClient,
    plan: DeployPlan,
    *,
    source_catalog_payload: bytes | None,
    confirmation: str,
    progress: Callable[[str], None] | None = None,
) -> dict[str, object]:
    """Execute the ordered sequence against an injected client.

    The Tk UI in this phase never supplies a network client.  Tests use a
    memory client, and a future approved deploy can inject a real REST client.
    """
    require_confirmation(confirmation)
    source_payload = source_catalog_payload if source_catalog_payload is not None else client.get_url(plan.catalog_source_url)
    source = verify_source_catalog(source_payload)
    if progress:
        progress("Fetch + verify catalog nguồn: PASS")
    compatibility = validate_compatibility_contract(plan.base_local_path, plan.plus_local_path)
    base_manifest = compatibility["baseManifest"]
    plus_manifest = compatibility["plusManifest"]
    calculated_hash = compatibility["compatibilityHash"]
    base_entry = catalog_entry_from_manifest(base_manifest, sha256=plan.base_sha256, zip_bytes=plan.base_bytes, compatibility_hash=calculated_hash)
    plus_entry = catalog_entry_from_manifest(plus_manifest, sha256=plan.plus_sha256, zip_bytes=plan.plus_bytes, compatibility_hash=calculated_hash)
    completed: list[str] = []
    try:
        _ensure_object(client, plan, plan.base_object_path, plan.base_local_path, plan.base_sha256, plan.base_bytes, "BASE", "base", progress)
        completed.append(plan.base_object_path)
        _ensure_object(client, plan, plan.plus_object_path, plan.plus_local_path, plan.plus_sha256, plan.plus_bytes, "PLUS", "plus", progress)
        completed.append(plan.plus_object_path)
    except Exception as exc:
        if completed:
            raise PartialDeployError(f"PARTIAL: ZIP remote đã hoàn tất {completed}; catalog chưa publish: {exc}", completed) from exc
        raise
    combined = merge_catalog(source, base_entry, plus_entry)
    payload = _canonical_json_bytes(combined)
    try:
        try:
            existing = client.get_object(plan.bucket, plan.catalog_target_path)
        except StorageNotFound as exc:
            _probe_log(progress, plan.catalog_target_path, exc.http_status, "ABSENT")
            client.create_object(plan.bucket, plan.catalog_target_path, payload, "application/json")
        except Exception as exc:
            _probe_log(progress, plan.catalog_target_path, _http_status_from_error(exc), "ERROR")
            raise
        else:
            try:
                _verify_bytes(existing, _sha256_bytes(payload), len(payload), "catalog remote")
            except Exception:
                _probe_log(progress, plan.catalog_target_path, 200, "PRESENT_CONFLICT")
                raise
            _probe_log(progress, plan.catalog_target_path, 200, "PRESENT_MATCH")
        downloaded = client.get_object(plan.bucket, plan.catalog_target_path)
        _verify_bytes(downloaded, _sha256_bytes(payload), len(payload), "catalog GET")
        downloaded_catalog = json.loads(downloaded.decode("utf-8"))
        validate_combined_catalog(downloaded_catalog, source, [base_entry, plus_entry])
    except PartialDeployError:
        raise
    except Exception as exc:
        raise PartialDeployError(f"PARTIAL: ZIP remote đã tồn tại nhưng catalog chưa PASS: {exc}", completed) from exc
    if progress:
        progress("GET verify catalog: PASS")
    return {
        "status": "PUBLISH PASS",
        "catalogBytes": len(payload),
        "catalogSha256": _sha256_bytes(payload),
        "catalog": combined,
    }


class MemoryStorageClient:
    """Fake storage for tests; it never performs HTTP."""

    def __init__(self, objects: Mapping[tuple[str, str], bytes] | None = None, urls: Mapping[str, bytes] | None = None):
        self.objects = dict(objects or {})
        self.urls = dict(urls or {})
        self.calls: list[tuple[str, str, str]] = []

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

    def get_url(self, url: str) -> bytes:
        self.calls.append(("GET_URL", "", url))
        try:
            return self.urls[url]
        except KeyError as exc:
            raise StorageNotFound(url) from exc


class SupabaseStorageRestClient:
    """Explicit opt-in REST client for a later approved deploy action.

    ``network_enabled`` defaults to False so importing or constructing this
    class during Phase 1/automated tests cannot perform a request.
    """

    def __init__(self, project_url: str, service_role_key: str, *, network_enabled: bool = False, timeout: float = 20.0, retries: int = 2):
        self.project_url = project_url.rstrip("/")
        self._service_role_key = service_role_key
        self.network_enabled = network_enabled
        self.timeout = timeout
        self.retries = max(0, min(int(retries), 3))

    def _ensure_enabled(self, *, require_key: bool = True):
        if not self.network_enabled:
            raise DeployValidationError("Supabase network đang disabled trong phase coding/test.")
        if not self.project_url or (require_key and not self._service_role_key):
            raise DeployValidationError("Thiếu Supabase URL hoặc service-role key.")

    def _headers(self, content_type: str | None = None) -> dict[str, str]:
        # Never include this mapping or the key value in an exception/log.
        headers = {"apikey": self._service_role_key, "Authorization": f"Bearer {self._service_role_key}"}
        if content_type:
            headers["Content-Type"] = content_type
        return headers

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

    def _public_storage_url(self, bucket: str, object_path: str) -> str:
        quoted_bucket = urlparse.quote(bucket, safe="")
        quoted_path = "/".join(urlparse.quote(part, safe="") for part in object_path.split("/"))
        return f"{self.project_url}/storage/v1/object/public/{quoted_bucket}/{quoted_path}"

    def _request(self, method: str, url: str, *, payload: bytes | None = None, content_type: str | None = None, retry_get: bool = False, extra_headers: Mapping[str, str] | None = None) -> bytes:
        self._ensure_enabled()
        headers = self._headers(content_type)
        if extra_headers:
            headers.update(extra_headers)
        request = urlrequest.Request(url, data=payload, headers=headers, method=method)
        attempts = self.retries + 1 if retry_get else 1
        for attempt in range(attempts):
            try:
                with urlrequest.urlopen(request, timeout=self.timeout, context=self._ssl_context()) as response:
                    return response.read()
            except urlerror.HTTPError as exc:
                parsed, body = _http_error_body(exc)
                if _is_object_not_found_response(exc.code, parsed, body):
                    raise StorageNotFound(url, http_status=exc.code) from None
                if exc.code == 409:
                    raise StorageConflict(url) from None
                if retry_get and exc.code >= 500 and attempt + 1 < attempts:
                    time.sleep(0.25 * (attempt + 1))
                    continue
                detail = " Object not found." if "Object not found" in body or "not_found" in body else ""
                raise DeployValidationError(f"Supabase storage HTTP {exc.code}.{detail}") from None
            except (urlerror.URLError, TimeoutError, OSError) as exc:
                if retry_get and attempt + 1 < attempts:
                    time.sleep(0.25 * (attempt + 1))
                    continue
                raise DeployValidationError(f"Supabase storage request failed: {type(exc).__name__}.") from None
        raise DeployValidationError("Supabase storage request failed.")

    def get_object(self, bucket: str, object_path: str) -> bytes:
        return self._request("GET", self._storage_url(bucket, object_path), retry_get=True)

    def create_object(self, bucket: str, object_path: str, payload: bytes, content_type: str) -> None:
        self._request(
            "POST",
            self._storage_url(bucket, object_path),
            payload=payload,
            content_type=content_type,
            extra_headers={"x-upsert": "false"},
        )

    def get_url(self, url: str) -> bytes:
        # Public catalog GET still requires explicit network opt-in.
        self._ensure_enabled(require_key=False)
        request = urlrequest.Request(url, headers={"Accept": "application/json"}, method="GET")
        attempts = self.retries + 1
        for attempt in range(attempts):
            try:
                with urlrequest.urlopen(request, timeout=self.timeout, context=self._ssl_context()) as response:
                    return response.read()
            except urlerror.HTTPError as exc:
                parsed, body = _http_error_body(exc)
                if _is_object_not_found_response(exc.code, parsed, body):
                    raise StorageNotFound(url, http_status=exc.code) from None
                if exc.code >= 500 and attempt + 1 < attempts:
                    time.sleep(0.25 * (attempt + 1))
                    continue
                detail = " Object not found." if "Object not found" in body or "not_found" in body else ""
                raise DeployValidationError(f"Catalog GET HTTP {exc.code}.{detail}") from None
            except (urlerror.URLError, TimeoutError, OSError) as exc:
                if attempt + 1 < attempts:
                    time.sleep(0.25 * (attempt + 1))
                    continue
                raise DeployValidationError(f"Catalog GET failed: {type(exc).__name__}.") from None
        raise DeployValidationError("Catalog GET failed.")


class ReadOnlySupabaseStorageClient:
    """Public-storage client for preflight; it has no write implementation."""

    def __init__(self, project_url: str, *, timeout: float = 20.0, retries: int = 2):
        self.methods: list[str] = []
        self._rest = SupabaseStorageRestClient(
            project_url,
            "",
            network_enabled=True,
            timeout=timeout,
            retries=retries,
        )

    def get_url(self, url: str) -> bytes:
        self.methods.append("GET")
        return self._rest.get_url(url)

    def get_object(self, bucket: str, object_path: str) -> bytes:
        self.methods.append("GET")
        return self._rest.get_url(self._rest._public_storage_url(bucket, object_path))

    def create_object(self, *args: object, **kwargs: object) -> None:
        raise DeployValidationError("Read-only preflight không cho phép create/upload.")

    def update_object(self, *args: object, **kwargs: object) -> None:
        raise DeployValidationError("Read-only preflight không cho phép update/upsert.")

    def delete_object(self, *args: object, **kwargs: object) -> None:
        raise DeployValidationError("Read-only preflight không cho phép delete.")


def _inspect_remote_object(
    client: ReadOnlyStorageClient,
    bucket: str,
    object_path: str,
    *,
    expected_sha: str,
    expected_bytes: int,
    kind: str,
    expected_catalog: Mapping[str, object] | None = None,
    source_catalog: Mapping[str, object] | None = None,
    additions: list[Mapping[str, object]] | None = None,
) -> dict[str, object]:
    """GET one public object and classify it without ever writing."""
    try:
        payload = client.get_object(bucket, object_path)
    except StorageNotFound:
        return {"status": "ABSENT", "path": object_path}
    except Exception as exc:
        return {"status": "ERROR", "path": object_path, "error": str(exc)}
    actual_sha = _sha256_bytes(payload)
    actual_bytes = len(payload)
    matches = actual_bytes == expected_bytes and actual_sha == expected_sha
    error_message = ""
    if matches and kind == "zip":
        segment = "base" if "/base/" in object_path else "plus"
        try:
            _verify_zip_payload(payload, expected_sha, expected_bytes, segment)
        except Exception as exc:
            matches = False
            error_message = str(exc)
    elif matches and kind == "catalog":
        try:
            remote_catalog = json.loads(payload.decode("utf-8"))
            if source_catalog is None or additions is None:
                raise DeployValidationError("Thiếu dữ liệu catalog để verify remote.")
            validate_combined_catalog(remote_catalog, source_catalog, additions)
        except Exception as exc:
            matches = False
            error_message = str(exc)
    return {
        "status": "PRESENT_MATCH" if matches else "PRESENT_CONFLICT",
        "path": object_path,
        "bytes": actual_bytes,
        "sha256": actual_sha,
        **({"error": error_message} if error_message else {}),
    }


def run_hsk1_read_only_preflight(
    result: Mapping[str, object],
    config: tuple[str, str, str, str],
    receipt_fingerprint: Mapping[str, object],
    profile: Mapping[str, object],
    *,
    client: ReadOnlyStorageClient,
    output_directory: str | Path,
) -> dict[str, object]:
    """Revalidate local artifacts and inspect production with GET only."""
    deploy_root = Path(output_directory) / "vocab" / "3.0" / PILOT_LEVEL / "deploy_preflight"
    deploy_root.mkdir(parents=True, exist_ok=True)
    report: dict[str, object] = {"status": "FAIL", "httpMethods": list(getattr(client, "methods", []))}
    try:
        plan = build_plan(result, config, receipt_fingerprint, profile, profile_name="preflight")
        verified = validate_local_receipt(result, config, receipt_fingerprint)
        base = verified["packs"]["base"]
        plus = verified["packs"]["plus"]
        report.update({
            "local": {
                "base": {"bytes": base["bytes"], "sha256": base["sha256"]},
                "plus": {"bytes": plus["bytes"], "sha256": plus["sha256"]},
            },
            "compatibilityHash": verified["compatibility"]["compatibilityHash"],
            "baseOrderedIds": verified["compatibility"]["baseIds"],
        })
        source_payload = client.get_url(CATALOG_SOURCE_URL)
        source = verify_source_catalog(source_payload)
        (deploy_root / "source_catalog_verified.json").write_bytes(source_payload)
        dry_run = build_catalog_dry_run(
            source_payload,
            base["localPath"],
            plus["localPath"],
            base_sha256=base["sha256"],
            plus_sha256=plus["sha256"],
        )
        (deploy_root / "combined_catalog_dry_run.json").write_bytes(dry_run["payload"])
        entries_key = _entries_key(source)
        source_entries = source[entries_key]
        report["sourceCatalog"] = {
            "bytes": len(source_payload),
            "sha256": _sha256_bytes(source_payload),
            "entryCount": len(source_entries),
        }
        report["combinedCatalog"] = {
            "bytes": dry_run["bytes"],
            "sha256": dry_run["sha256"],
            "entryCount": len(dry_run["catalog"][entries_key]),
            "legacyEntryCount": dry_run["legacyEntryCount"],
            "hsk30EntryCount": dry_run["hsk30EntryCount"],
            "duplicateIdentityCount": dry_run["duplicateIdentityCount"],
        }
        additions = [
            entry for entry in dry_run["catalog"][entries_key]
            if isinstance(entry, Mapping) and entry.get("version") == "3.0" and entry.get("level") == PILOT_LEVEL
        ]
        remote = {
            "base": _inspect_remote_object(
                client, PILOT_BUCKET, BASE_OBJECT_PATH,
                expected_sha=base["sha256"], expected_bytes=base["bytes"], kind="zip",
            ),
            "plus": _inspect_remote_object(
                client, PILOT_BUCKET, PLUS_OBJECT_PATH,
                expected_sha=plus["sha256"], expected_bytes=plus["bytes"], kind="zip",
            ),
            "catalog": _inspect_remote_object(
                client, PILOT_BUCKET, CATALOG_TARGET_PATH,
                expected_sha=dry_run["sha256"], expected_bytes=dry_run["bytes"], kind="catalog",
                source_catalog=source, additions=additions,
            ),
        }
        report["remote"] = remote
        report["httpMethods"] = list(getattr(client, "methods", []))
        statuses = {item["status"] for item in remote.values()}
        report["status"] = "PASS" if statuses <= {"ABSENT", "PRESENT_MATCH"} else "BLOCKED"
    except Exception as exc:
        report["status"] = "BLOCKED"
        report["error"] = str(exc)
        report["httpMethods"] = list(getattr(client, "methods", []))
    report["remoteWrite"] = False
    report["profileKeyLogged"] = False
    (deploy_root / "preflight_report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return report
