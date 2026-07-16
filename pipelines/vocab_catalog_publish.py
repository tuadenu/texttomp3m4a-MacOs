"""Signed immutable catalog and mutable current-pointer workflow.

All network operations are injected through the storage client.  This module
does not create a production key on import and has no default network client,
which keeps coding/tests local-only.
"""

from __future__ import annotations

import copy
import json
import stat
from pathlib import Path
from typing import Callable, Mapping
from urllib.parse import unquote, urlparse

from pipelines.vocab_catalog_signing import (
    DEFAULT_KEY_ID,
    DEFAULT_KEY_PATH,
    POINTER_CURRENT_OBJECT,
    SigningValidationError,
    create_private_key,
    load_private_key,
    make_pointer,
    pointer_json_bytes,
    public_key_b64,
    utc_timestamp,
    verify_pointer,
)
from pipelines.vocab_zip_deploy import (
    CATALOG_PUBLISH_CONFIRMATION,
    DeployValidationError,
    StorageClient,
    StorageConflict,
    StorageNotFound,
    _canonical_json_bytes,
    _sha256_bytes,
    _validate_catalog_entries,
    catalog_object_path,
    mark_receipts_catalog_published,
    prepare_catalog_publish,
    verify_catalog_payload,
)


POINTER_ARCHIVE_PREFIX = "catalogs/vocab/pointers"


def pointer_archive_path(pointer_revision: int) -> str:
    if int(pointer_revision) < 1:
        raise DeployValidationError("pointerRevision phải >= 1.")
    revision = int(pointer_revision)
    return f"{POINTER_ARCHIVE_PREFIX}/v{revision}/vocab_catalog_pointer_v{revision}.json"


def pointer_public_url(project_url: str, bucket: str, object_path: str) -> str:
    return f"{project_url.rstrip('/')}/storage/v1/object/public/{bucket}/{object_path}"


def _get_optional(client: StorageClient, bucket: str, object_path: str) -> bytes | None:
    try:
        return client.get_object(bucket, object_path)
    except StorageNotFound:
        return None


def signing_status(key_path: str | Path = DEFAULT_KEY_PATH, key_id: str = DEFAULT_KEY_ID) -> dict[str, object]:
    path = Path(key_path).expanduser()
    if not path.is_file():
        return {"status": "SIGNING KEY NOT INITIALIZED", "keyId": key_id, "path": str(path)}
    try:
        if stat.S_IMODE(path.stat().st_mode) != 0o600:
            return {"status": "SIGNING KEY INVALID", "keyId": key_id, "path": str(path), "error": "private seed permission phải là 0600"}
        private_key = load_private_key(path)
        return {"status": "SIGNING KEY READY", "keyId": key_id, "path": str(path), "publicKeyB64": public_key_b64(private_key)}
    except SigningValidationError as exc:
        return {"status": "SIGNING KEY INVALID", "keyId": key_id, "path": str(path), "error": str(exc)}


def initialize_signing_key(key_path: str | Path = DEFAULT_KEY_PATH, *, confirmation: str, seed: bytes | None = None) -> dict[str, object]:
    if confirmation != "INITIALIZE VOCAB SIGNING KEY":
        raise DeployValidationError("Xác nhận khởi tạo signing key không đúng.")
    path = create_private_key(key_path, seed=seed)
    private_key = load_private_key(path)
    return {"status": "SIGNING KEY READY", "keyId": DEFAULT_KEY_ID, "path": str(path), "publicKeyB64": public_key_b64(private_key)}


def _load_verified_current_pointer(client: StorageClient, *, bucket: str, public_key, expected_key_id: str) -> dict[str, object] | None:
    payload = _get_optional(client, bucket, POINTER_CURRENT_OBJECT)
    if payload is None:
        return None
    try:
        pointer = json.loads(payload.decode("utf-8"))
    except Exception as exc:
        raise DeployValidationError(f"current.json không phải JSON hợp lệ: {exc}") from exc
    if not isinstance(pointer, dict):
        raise DeployValidationError("current.json phải là object.")
    if pointer.get("keyId") != expected_key_id:
        raise DeployValidationError("current.json keyId không khớp signing key hiện hành.")
    try:
        verify_pointer(pointer, public_key)
    except SigningValidationError as exc:
        raise DeployValidationError(f"current.json signature không hợp lệ: {exc}") from exc
    return pointer


def read_verified_pointer_status(client: StorageClient, *, bucket: str = "vocab-pack-staging",
                                 private_key_path: str | Path = DEFAULT_KEY_PATH,
                                 key_id: str = DEFAULT_KEY_ID) -> dict[str, object]:
    """Read and verify the production pointer and its catalog without writes.

    This is intentionally a GET-only operation for the UI refresh action.  A
    missing ``current.json`` is reported as ``POINTER NOT INITIALIZED``;
    transport or validation errors are raised so callers do not confuse a
    temporary read failure with an uninitialized production pointer.
    """
    private_key = load_private_key(private_key_path)
    if key_id != DEFAULT_KEY_ID:
        raise DeployValidationError("keyId không được thay đổi trong production contract.")
    pointer = _load_verified_current_pointer(
        client,
        bucket=bucket,
        public_key=private_key.public_key(),
        expected_key_id=key_id,
    )
    if pointer is None:
        return {"status": "POINTER NOT INITIALIZED", "bucket": bucket}
    catalog_url = str(pointer.get("catalogUrl", ""))
    parsed = urlparse(catalog_url)
    marker = f"/object/public/{bucket}/"
    if marker not in parsed.path:
        raise DeployValidationError("current.json catalogUrl không trỏ vào bucket hiện hành.")
    catalog_object = unquote(parsed.path.split(marker, 1)[1])
    catalog_payload = client.get_object(bucket, catalog_object)
    catalog = verify_catalog_payload(
        catalog_payload,
        expected_sha256=str(pointer["catalogSha256"]),
        expected_bytes=int(pointer["catalogBytes"]),
    )
    entry_count = len(_validate_catalog_entries(catalog))
    return {
        "status": "POINTER ACTIVE",
        "pointerRevision": int(pointer["pointerRevision"]),
        "catalogRevision": int(pointer["catalogRevision"]),
        "catalogBytes": int(pointer["catalogBytes"]),
        "catalogSha256": str(pointer["catalogSha256"]),
        "catalogObjectPath": catalog_object,
        "entryCount": entry_count,
        # Keep the verified catalog available to the local UI so it can
        # reconcile a receipt after a publish retry without another write.
        "catalog": catalog,
        "pointer": pointer,
    }


def _create_or_reuse(client: StorageClient, bucket: str, object_path: str, payload: bytes, content_type: str) -> None:
    try:
        existing = client.get_object(bucket, object_path)
    except StorageNotFound:
        client.create_object(bucket, object_path, payload, content_type)
        return
    if existing != payload:
        raise StorageConflict(f"Immutable object conflict: {object_path}")


def _verify_object(client: StorageClient, bucket: str, object_path: str, expected: bytes) -> bytes:
    payload = client.get_object(bucket, object_path)
    if payload != expected:
        raise DeployValidationError(f"GET verify sai bytes/SHA: {object_path}")
    return payload


def _catalog_payload_for_plan(output_directory: str | Path, levels: set[str] | None = None, versions: set[str] | None = None):
    plan = prepare_catalog_publish(output_directory, levels=levels, versions=versions)
    combined = copy.deepcopy(plan.source.catalog)
    entries_key = next(key for key in ("entries", "packs", "collections") if isinstance(combined.get(key), list))
    entries = combined[entries_key]
    assert isinstance(entries, list)
    # Use the deploy module's replacement-aware merge without exposing a new
    # public API solely for the signed publisher.
    from pipelines.vocab_zip_deploy import merge_catalog
    combined = merge_catalog(plan.source.catalog, list(plan.additions))
    payload = _canonical_json_bytes(combined)
    return plan, combined, payload


def _sync_current_catalog_snapshot(client: StorageClient, *, output_directory: str | Path,
                                   bucket: str, public_key, key_id: str) -> None:
    """Make the locally selected source follow a verified signed current pointer."""
    current = _load_verified_current_pointer(client, bucket=bucket, public_key=public_key, expected_key_id=key_id)
    if current is None:
        return
    parsed = urlparse(str(current["catalogUrl"]))
    marker = f"/object/public/{bucket}/"
    if marker not in parsed.path:
        raise DeployValidationError("current.json catalogUrl không trỏ vào bucket hiện hành.")
    object_path = unquote(parsed.path.split(marker, 1)[1])
    payload = client.get_object(bucket, object_path)
    verify_catalog_payload(payload, expected_sha256=current["catalogSha256"], expected_bytes=current["catalogBytes"])
    from pipelines.vocab_zip_deploy import _snapshot_path
    snapshot = _snapshot_path(output_directory, int(current["catalogRevision"]))
    snapshot.parent.mkdir(parents=True, exist_ok=True)
    snapshot.write_bytes(payload)


def publish_signed_catalog_with_client(client: StorageClient, *, output_directory: str | Path,
                                       confirmation: str, private_key_path: str | Path = DEFAULT_KEY_PATH,
                                       key_id: str = DEFAULT_KEY_ID, min_app_build: int = 1,
                                       levels: set[str] | None = None,
                                       versions: set[str] | None = None,
                                       published_at: str | None = None,
                                       progress: Callable[[str], None] | None = None) -> dict[str, object]:
    """Publish immutable catalog, archive signed pointer, then update current."""
    if confirmation != CATALOG_PUBLISH_CONFIRMATION:
        raise DeployValidationError("Xác nhận publish catalog không đúng.")
    private_key = load_private_key(private_key_path)
    if key_id != DEFAULT_KEY_ID:
        raise DeployValidationError("keyId không được thay đổi trong production contract.")
    bucket = getattr(client, "bucket", "vocab-pack-staging")
    current_before = _load_verified_current_pointer(client, bucket=bucket, public_key=private_key.public_key(), expected_key_id=key_id)
    _sync_current_catalog_snapshot(client, output_directory=output_directory, bucket=bucket, public_key=private_key.public_key(), key_id=key_id)
    try:
        plan, combined, catalog_payload = _catalog_payload_for_plan(output_directory, levels=levels, versions=versions)
    except DeployValidationError as exc:
        if current_before is not None and "Chưa có deploy receipt mới" in str(exc):
            return {
                "status": "ALREADY PUBLISHED", "pointerRevision": int(current_before["pointerRevision"]),
                "catalogRevision": int(current_before["catalogRevision"]), "catalogUrl": current_before["catalogUrl"],
                "catalogBytes": int(current_before["catalogBytes"]), "catalogSha256": current_before["catalogSha256"],
                "pointer": current_before,
            }
        raise
    source_remote = client.get_object(bucket, plan.source.object_path)
    verify_catalog_payload(source_remote, expected_sha256=plan.source.sha256, expected_bytes=len(plan.source.payload))
    if progress:
        progress(f"Verify catalog source v{plan.source.revision}: PASS")

    target_object = plan.target_object_path
    _create_or_reuse(client, bucket, target_object, catalog_payload, "application/json")
    verified_catalog = _verify_object(client, bucket, target_object, catalog_payload)
    verify_catalog_payload(verified_catalog, expected_sha256=_sha256_bytes(catalog_payload), expected_bytes=len(catalog_payload))
    if progress:
        progress(f"Immutable catalog v{plan.target_revision}: GET verify PASS")

    public_catalog_url = pointer_public_url(getattr(client, "project_url", ""), bucket, target_object)
    current = _load_verified_current_pointer(client, bucket=bucket, public_key=private_key.public_key(), expected_key_id=key_id)
    expected_url = public_catalog_url
    if current and int(current["catalogRevision"]) == plan.target_revision and current.get("catalogSha256") == _sha256_bytes(catalog_payload) and current.get("catalogBytes") == len(catalog_payload) and current.get("catalogUrl") == expected_url:
        # The remote publish may have completed before a client/UI crash.  A
        # verified matching pointer is enough to reconcile local receipts; do
        # not create a new catalog or pointer revision.
        mark_receipts_catalog_published(
            plan.receipts,
            catalog_revision=plan.target_revision,
            pointer_revision=int(current["pointerRevision"]),
        )
        return {
            "status": "ALREADY PUBLISHED", "catalogRevision": plan.target_revision,
            "pointerRevision": int(current["pointerRevision"]), "catalogObjectPath": target_object,
            "catalogUrl": expected_url, "catalogBytes": len(catalog_payload),
            "catalogSha256": _sha256_bytes(catalog_payload), "pointer": current,
            "entryCount": len(_validate_catalog_entries(combined)),
        }
    pointer_revision = int(current["pointerRevision"]) + 1 if current else 1
    pointer = make_pointer(
        pointer_revision=pointer_revision,
        catalog_revision=plan.target_revision,
        catalog_url=public_catalog_url,
        catalog_sha256=_sha256_bytes(catalog_payload),
        catalog_bytes=len(catalog_payload),
        min_app_build=min_app_build,
        key_id=key_id,
        private_key=private_key,
        published_at=published_at or utc_timestamp(),
    )
    verify_pointer(pointer, private_key.public_key())
    pointer_payload = pointer_json_bytes(pointer)
    archive_path = pointer_archive_path(pointer_revision)
    _create_or_reuse(client, bucket, archive_path, pointer_payload, "application/json")
    _verify_object(client, bucket, archive_path, pointer_payload)
    if progress:
        progress(f"Pointer archive v{pointer_revision}: GET verify PASS")

    # current.json is the only mutable object.  A rerun with identical bytes is
    # a safe no-op; otherwise update_object is the injected, explicit operation.
    current_payload = _get_optional(client, bucket, POINTER_CURRENT_OBJECT)
    if current_payload == pointer_payload:
        pass
    else:
        updater = getattr(client, "update_object", None)
        if updater is None:
            raise DeployValidationError("Storage client thiếu update_object cho current.json.")
        updater(bucket, POINTER_CURRENT_OBJECT, pointer_payload, "application/json")
    verified_current = _verify_object(client, bucket, POINTER_CURRENT_OBJECT, pointer_payload)
    verify_pointer(json.loads(verified_current.decode("utf-8")), private_key.public_key())
    # This is deliberately last: a receipt is never marked published before
    # immutable catalog, pointer archive, mutable current.json and GET verify.
    mark_receipts_catalog_published(
        plan.receipts,
        catalog_revision=plan.target_revision,
        pointer_revision=pointer_revision,
    )
    if progress:
        progress(f"POINTER ACTIVE: pointerRevision={pointer_revision} catalogRevision={plan.target_revision}")
    return {
        "status": "PUBLISHED", "catalogRevision": plan.target_revision, "pointerRevision": pointer_revision,
        "catalogObjectPath": target_object, "catalogUrl": public_catalog_url,
        "catalogBytes": len(catalog_payload), "catalogSha256": _sha256_bytes(catalog_payload),
        "pointerArchivePath": archive_path, "pointerObjectPath": POINTER_CURRENT_OBJECT,
        "pointerBytes": len(pointer_payload), "pointer": pointer,
        "entryCount": len(_validate_catalog_entries(combined)),
    }


def initialize_pointer_with_client(client: StorageClient, *, catalog_revision: int, catalog_object: str,
                                   expected_bytes: int, expected_sha256: str, confirmation: str,
                                   private_key_path: str | Path = DEFAULT_KEY_PATH,
                                   min_app_build: int = 1, published_at: str | None = None,
                                   progress: Callable[[str], None] | None = None) -> dict[str, object]:
    if confirmation != "INITIALIZE VOCAB POINTER":
        raise DeployValidationError("Xác nhận khởi tạo pointer không đúng.")
    private_key = load_private_key(private_key_path)
    bucket = getattr(client, "bucket", "vocab-pack-staging")
    catalog_payload = client.get_object(bucket, catalog_object)
    verify_catalog_payload(catalog_payload, expected_sha256=expected_sha256, expected_bytes=expected_bytes)
    if _get_optional(client, bucket, POINTER_CURRENT_OBJECT) is not None:
        raise DeployValidationError("current.json đã tồn tại; không chạy initialize.")
    catalog_url = pointer_public_url(getattr(client, "project_url", ""), bucket, catalog_object)
    pointer = make_pointer(
        pointer_revision=1, catalog_revision=catalog_revision, catalog_url=catalog_url,
        catalog_sha256=expected_sha256, catalog_bytes=expected_bytes, min_app_build=min_app_build,
        key_id=DEFAULT_KEY_ID, private_key=private_key, published_at=published_at or utc_timestamp(),
    )
    verify_pointer(pointer, private_key.public_key())
    payload = pointer_json_bytes(pointer)
    archive = pointer_archive_path(1)
    _create_or_reuse(client, bucket, archive, payload, "application/json")
    _verify_object(client, bucket, archive, payload)
    updater = getattr(client, "update_object", None)
    if updater is None:
        raise DeployValidationError("Storage client thiếu update_object cho current.json.")
    updater(bucket, POINTER_CURRENT_OBJECT, payload, "application/json")
    _verify_object(client, bucket, POINTER_CURRENT_OBJECT, payload)
    if progress:
        progress("POINTER ACTIVE: revision=1")
    return {"status": "POINTER ACTIVE", "pointerRevision": 1, "catalogRevision": catalog_revision, "pointer": pointer, "archivePath": archive}


def update_pointer_with_client(client: StorageClient, *, catalog_revision: int, catalog_object: str,
                               catalog_payload: bytes, confirmation: str,
                               private_key_path: str | Path = DEFAULT_KEY_PATH,
                               min_app_build: int = 1, published_at: str | None = None) -> dict[str, object]:
    """Advance the pointer to any already verified catalog revision.

    ``catalog_revision`` may be lower than the current catalog revision for a
    rollback, but pointerRevision always increases.  This helper never uploads
    or overwrites the catalog itself.
    """
    if confirmation != "PUBLISH VOCAB CATALOG":
        raise DeployValidationError("Xác nhận publish catalog không đúng.")
    private_key = load_private_key(private_key_path)
    bucket = getattr(client, "bucket", "vocab-pack-staging")
    verify_catalog_payload(catalog_payload, expected_sha256=_sha256_bytes(catalog_payload))
    current = _load_verified_current_pointer(client, bucket=bucket, public_key=private_key.public_key(), expected_key_id=DEFAULT_KEY_ID)
    catalog_url = pointer_public_url(getattr(client, "project_url", ""), bucket, catalog_object)
    if current and current.get("catalogRevision") == catalog_revision and current.get("catalogSha256") == _sha256_bytes(catalog_payload) and current.get("catalogUrl") == catalog_url and current.get("catalogBytes") == len(catalog_payload):
        return {"status": "ALREADY PUBLISHED", "pointer": current, "pointerRevision": current["pointerRevision"], "catalogRevision": catalog_revision}
    pointer_revision = int(current["pointerRevision"]) + 1 if current else 1
    pointer = make_pointer(pointer_revision=pointer_revision, catalog_revision=catalog_revision, catalog_url=catalog_url,
                            catalog_sha256=_sha256_bytes(catalog_payload), catalog_bytes=len(catalog_payload),
                            min_app_build=min_app_build, key_id=DEFAULT_KEY_ID, private_key=private_key,
                            published_at=published_at or utc_timestamp())
    archive = pointer_archive_path(pointer_revision)
    payload = pointer_json_bytes(pointer)
    _create_or_reuse(client, bucket, archive, payload, "application/json")
    _verify_object(client, bucket, archive, payload)
    updater = getattr(client, "update_object", None)
    if updater is None:
        raise DeployValidationError("Storage client thiếu update_object cho current.json.")
    updater(bucket, POINTER_CURRENT_OBJECT, payload, "application/json")
    _verify_object(client, bucket, POINTER_CURRENT_OBJECT, payload)
    return {"status": "POINTER ACTIVE", "pointer": pointer, "pointerRevision": pointer_revision, "catalogRevision": catalog_revision, "archivePath": archive}
