"""Ed25519 signing primitives for the HSK vocabulary catalog pointer.

The module is deliberately side-effect free: importing it never creates a key,
touches Supabase, or writes to the repository.  Production key creation is an
explicit UI action; tests may pass a deterministic seed directly.
"""

from __future__ import annotations

import base64
import hashlib
import json
import re
import secrets
from datetime import datetime, timezone
from pathlib import Path
from typing import Mapping
from urllib.parse import urlparse

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey


POINTER_SCHEMA_VERSION = 1
DEFAULT_KEY_ID = "vocab-ed25519-v1"
DEFAULT_KEY_PATH = Path.home() / "Library" / "Application Support" / "HSKVocabZipTool" / "keys" / "vocab-ed25519-v1.seed"
POINTER_CURRENT_OBJECT = "catalogs/vocab/current.json"


class SigningValidationError(ValueError):
    """Invalid key, pointer metadata, or signature."""


def compact_json_bytes(value: object) -> bytes:
    """Serialize without spaces/newline, preserving insertion order."""
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), allow_nan=False).encode("utf-8")


def pointer_payload(pointer: Mapping[str, object]) -> list[object]:
    fields = (
        "schemaVersion", "pointerRevision", "catalogRevision", "catalogUrl",
        "catalogSha256", "catalogBytes", "catalogSchemaVersion", "minAppBuild",
        "publishedAt", "keyId",
    )
    missing = [field for field in fields if field not in pointer]
    if missing:
        raise SigningValidationError("Pointer thiếu field: " + ", ".join(missing))
    return [pointer[field] for field in fields]


def pointer_payload_bytes(pointer: Mapping[str, object]) -> bytes:
    return compact_json_bytes(pointer_payload(pointer))


def pointer_payload_sha256(pointer: Mapping[str, object]) -> str:
    return hashlib.sha256(pointer_payload_bytes(pointer)).hexdigest()


def _decode_signature(value: object) -> bytes:
    if not isinstance(value, str) or not value:
        raise SigningValidationError("signature phải là base64 string.")
    try:
        raw = base64.b64decode(value, validate=True)
    except Exception as exc:
        raise SigningValidationError("signature không phải standard base64.") from exc
    if len(raw) != 64:
        raise SigningValidationError("signature phải decode đúng 64 byte.")
    return raw


def validate_pointer(pointer: Mapping[str, object]) -> dict[str, object]:
    if not isinstance(pointer, Mapping):
        raise SigningValidationError("Pointer root phải là object.")
    required = {
        "schemaVersion", "pointerRevision", "catalogRevision", "catalogUrl",
        "catalogSha256", "catalogBytes", "catalogSchemaVersion", "minAppBuild",
        "publishedAt", "keyId", "signature",
    }
    missing = sorted(required.difference(pointer))
    if missing:
        raise SigningValidationError("Pointer thiếu field: " + ", ".join(missing))
    if pointer["schemaVersion"] != POINTER_SCHEMA_VERSION:
        raise SigningValidationError("schemaVersion pointer không hợp lệ.")
    for field in ("pointerRevision", "catalogRevision"):
        if not isinstance(pointer[field], int) or isinstance(pointer[field], bool) or pointer[field] <= 0:
            raise SigningValidationError(f"{field} phải là integer dương.")
    url = pointer["catalogUrl"]
    parsed = urlparse(str(url))
    if not isinstance(url, str) or parsed.scheme != "https" or not parsed.netloc:
        raise SigningValidationError("catalogUrl phải là HTTPS URL có host.")
    sha = pointer["catalogSha256"]
    if not isinstance(sha, str) or not re.fullmatch(r"[0-9a-f]{64}", sha):
        raise SigningValidationError("catalogSha256 phải là lowercase SHA-256.")
    if not isinstance(pointer["catalogBytes"], int) or isinstance(pointer["catalogBytes"], bool) or pointer["catalogBytes"] <= 0:
        raise SigningValidationError("catalogBytes phải > 0.")
    if pointer["catalogSchemaVersion"] != 1:
        raise SigningValidationError("catalogSchemaVersion phải là 1.")
    if not isinstance(pointer["minAppBuild"], int) or isinstance(pointer["minAppBuild"], bool) or pointer["minAppBuild"] < 0:
        raise SigningValidationError("minAppBuild phải >= 0.")
    published = pointer["publishedAt"]
    if not isinstance(published, str) or not published.endswith("Z"):
        raise SigningValidationError("publishedAt phải là ISO-8601 UTC kết thúc bằng Z.")
    try:
        parsed_date = datetime.fromisoformat(published[:-1] + "+00:00")
    except ValueError as exc:
        raise SigningValidationError("publishedAt không phải ISO-8601 hợp lệ.") from exc
    if parsed_date.tzinfo is None or parsed_date.utcoffset() != timezone.utc.utcoffset(parsed_date):
        raise SigningValidationError("publishedAt phải có timezone UTC.")
    if not isinstance(pointer["keyId"], str) or not pointer["keyId"].strip():
        raise SigningValidationError("keyId không được rỗng.")
    _decode_signature(pointer["signature"])
    # Return a plain copy so callers cannot mutate the caller's mapping during
    # signature verification.
    return dict(pointer)


def load_private_key(path: str | Path = DEFAULT_KEY_PATH) -> Ed25519PrivateKey:
    key_path = Path(path).expanduser()
    try:
        seed = key_path.read_bytes()
    except OSError as exc:
        raise SigningValidationError(f"Không đọc được private seed: {key_path}") from exc
    if len(seed) != 32:
        raise SigningValidationError("Private Ed25519 seed phải đúng 32 byte.")
    return Ed25519PrivateKey.from_private_bytes(seed)


def create_private_key(path: str | Path = DEFAULT_KEY_PATH, *, seed: bytes | None = None) -> Path:
    """Explicit key setup action; never called implicitly by the app/tests."""
    key_path = Path(path).expanduser()
    seed = bytes(seed) if seed is not None else secrets.token_bytes(32)
    if len(seed) != 32:
        raise SigningValidationError("Private Ed25519 seed phải đúng 32 byte.")
    if key_path.exists():
        raise SigningValidationError(f"Private seed đã tồn tại; không overwrite: {key_path}")
    key_path.parent.mkdir(parents=True, exist_ok=True)
    key_path.write_bytes(seed)
    try:
        key_path.chmod(0o600)
    except OSError as exc:
        raise SigningValidationError(f"Không đặt được permission 0600 cho private seed: {key_path}") from exc
    return key_path


def raw_public_key(private_key: Ed25519PrivateKey) -> bytes:
    return private_key.public_key().public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    )


def public_key_b64(private_key: Ed25519PrivateKey) -> str:
    return base64.b64encode(raw_public_key(private_key)).decode("ascii")


def sign_pointer(pointer_without_signature: Mapping[str, object], private_key: Ed25519PrivateKey) -> dict[str, object]:
    pointer = dict(pointer_without_signature)
    pointer.pop("signature", None)
    # Validate all signed metadata before signing it.
    pointer["signature"] = base64.b64encode(private_key.sign(pointer_payload_bytes(pointer))).decode("ascii")
    validate_pointer(pointer)
    return pointer


def verify_pointer(pointer: Mapping[str, object], public_key: Ed25519PublicKey | bytes) -> bool:
    checked = validate_pointer(pointer)
    if isinstance(public_key, bytes):
        if len(public_key) != 32:
            raise SigningValidationError("Raw public key phải đúng 32 byte.")
        public_key = Ed25519PublicKey.from_public_bytes(public_key)
    try:
        public_key.verify(_decode_signature(checked["signature"]), pointer_payload_bytes(checked))
    except Exception as exc:
        raise SigningValidationError("Pointer signature verify thất bại.") from exc
    return True


def make_pointer(*, pointer_revision: int, catalog_revision: int, catalog_url: str, catalog_sha256: str,
                 catalog_bytes: int, min_app_build: int = 1, key_id: str = DEFAULT_KEY_ID,
                 private_key: Ed25519PrivateKey, published_at: str) -> dict[str, object]:
    pointer = {
        "schemaVersion": POINTER_SCHEMA_VERSION,
        "pointerRevision": pointer_revision,
        "catalogRevision": catalog_revision,
        "catalogUrl": catalog_url,
        "catalogSha256": catalog_sha256,
        "catalogBytes": catalog_bytes,
        "catalogSchemaVersion": 1,
        "minAppBuild": min_app_build,
        "publishedAt": published_at,
        "keyId": key_id,
    }
    return sign_pointer(pointer, private_key)


def utc_timestamp() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def pointer_json_bytes(pointer: Mapping[str, object]) -> bytes:
    validate_pointer(pointer)
    return compact_json_bytes(pointer)
