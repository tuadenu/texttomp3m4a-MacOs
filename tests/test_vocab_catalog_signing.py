import base64
import json
import os
import stat
import tempfile
import unittest
from pathlib import Path

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from pipelines.vocab_catalog_signing import (
    DEFAULT_KEY_ID,
    SigningValidationError,
    create_private_key,
    pointer_payload_bytes,
    pointer_payload_sha256,
    public_key_b64,
    sign_pointer,
    verify_pointer,
)


class VocabCatalogSigningTests(unittest.TestCase):
    def _vector(self):
        return {
            "schemaVersion": 1,
            "pointerRevision": 7,
            "catalogRevision": 3,
            "catalogUrl": "https://example.test/storage/v1/object/public/vocab-pack-staging/catalogs/vocab/combined/v2/vocab_pack_catalog_30_v1.json",
            "catalogSha256": "cf66d214c6648db686b6eed7d7d885c551aaf156b4cf9d2548faed943321f0db",
            "catalogBytes": 1032,
            "catalogSchemaVersion": 1,
            "minAppBuild": 1,
            "publishedAt": "2026-07-15T00:00:00Z",
            "keyId": DEFAULT_KEY_ID,
        }

    def test_cross_language_vector_is_byte_exact(self):
        key = Ed25519PrivateKey.from_private_bytes(bytes(range(1, 33)))
        pointer = sign_pointer(self._vector(), key)
        self.assertEqual(
            b'[1,7,3,"https://example.test/storage/v1/object/public/vocab-pack-staging/catalogs/vocab/combined/v2/vocab_pack_catalog_30_v1.json","cf66d214c6648db686b6eed7d7d885c551aaf156b4cf9d2548faed943321f0db",1032,1,1,"2026-07-15T00:00:00Z","vocab-ed25519-v1"]',
            pointer_payload_bytes(pointer),
        )
        self.assertEqual("11da3de1bf241a7eab2006d64b512268d60b6c47e13ff06bea2e166fd08118c7", pointer_payload_sha256(pointer))
        self.assertEqual("ebVWLo/mVPlAeLES6KmLp5AfhTrmlb7X4OORC60ElmQ=", public_key_b64(key))
        self.assertEqual("qnbDe8InwQ8DFpPdJqA5HvuYzysKPBYGtZcqnElND9RT+ahpei1qO5KcZT94vTTIyU+xJnRd51GeMLleNI4ZAQ==", pointer["signature"])
        self.assertTrue(verify_pointer(pointer, key.public_key()))

    def test_tamper_public_key_key_id_and_signature_fail(self):
        key = Ed25519PrivateKey.from_private_bytes(bytes(range(1, 33)))
        pointer = sign_pointer(self._vector(), key)
        for field, value in (("catalogRevision", 4), ("keyId", "wrong"), ("signature", "bad")):
            with self.subTest(field=field):
                tampered = dict(pointer)
                tampered[field] = value
                with self.assertRaises(SigningValidationError):
                    verify_pointer(tampered, key.public_key())
        other = Ed25519PrivateKey.from_private_bytes(bytes(range(2, 34)))
        with self.assertRaises(SigningValidationError):
            verify_pointer(pointer, other.public_key())

    def test_signature_must_decode_to_64_bytes(self):
        key = Ed25519PrivateKey.from_private_bytes(bytes(range(1, 33)))
        pointer = sign_pointer(self._vector(), key)
        pointer["signature"] = base64.b64encode(b"short").decode("ascii")
        with self.assertRaises(SigningValidationError):
            verify_pointer(pointer, key.public_key())

    def test_key_setup_is_explicit_and_0600(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "keys" / "seed"
            create_private_key(path, seed=bytes(range(1, 33)))
            self.assertEqual(32, path.stat().st_size)
            self.assertEqual(0o600, stat.S_IMODE(path.stat().st_mode))
            self.assertEqual("ebVWLo/mVPlAeLES6KmLp5AfhTrmlb7X4OORC60ElmQ=", public_key_b64(Ed25519PrivateKey.from_private_bytes(path.read_bytes())))


if __name__ == "__main__":
    unittest.main()
