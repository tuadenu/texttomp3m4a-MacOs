import json
import hashlib
import shutil
import tempfile
import unittest
import sys
import types
from pathlib import Path

import pandas as pd

if "pypinyin" not in sys.modules:
    fake_pypinyin = types.ModuleType("pypinyin")
    class _FakeStyle:
        NORMAL = "NORMAL"
    fake_pypinyin.Style = _FakeStyle
    fake_pypinyin.lazy_pinyin = lambda text, *args, **kwargs: [str(text)]
    sys.modules["pypinyin"] = fake_pypinyin

from pipelines import vocab_zip_deploy as deploy
from pipelines.vocab_catalog_publish import (
    initialize_pointer_with_client,
    pointer_archive_path,
    publish_signed_catalog_with_client,
    read_verified_pointer_status,
    update_pointer_with_client,
)
from pipelines.vocab_catalog_signing import public_key_b64
from pipelines.vocab_zip_builder import SourceVocab, audio_cache_key, build_hsk30
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey


class VocabCatalogPublishTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.root = Path(__file__).resolve().parents[1]
        cls.seed = (cls.root / deploy.SEED_CATALOG_PATH).read_bytes()

    def setUp(self):
        self.temp = Path(tempfile.mkdtemp(prefix="vocab-pointer-test-"))
        self.excel = self.temp / "source.xlsx"
        rows = [{"index": i, "word": f"词{i}", "meaning_vi": f"nghia {i}", "example_zh": f"例子{i}", "example_vi": f"vi du {i}"} for i in range(1, 53)]
        pd.DataFrame(rows).to_excel(self.excel, sheet_name="hsk2_30", index=False)
        self.output = self.temp / "output"
        audio = self.output / "vocab" / "3.0" / "hsk2" / "audio_cache"
        audio.mkdir(parents=True)
        for row in rows:
            item = SourceVocab(row["index"], row["word"], row["meaning_vi"], row["example_zh"], row["example_vi"])
            name = audio_cache_key(item, engine="gTTS", speed="Bình thường", voice="Mặc định", profile="", bitrate="32k", audio_mode="zh_vi") + ".m4a"
            (audio / name).write_bytes(f"audio-{row['index']}".encode())
        result = build_hsk30(self.excel, "hsk2_30", "hsk2", self.output, generate_missing=False)
        fingerprint = deploy.input_fingerprint(self.excel, "hsk2_30", "hsk2", self.output, bitrate="32k")
        profile = {"SUPABASE_URL": "https://example.supabase.co", "SUPABASE_BUCKET": deploy.STAGING_BUCKET, "SUPABASE_SERVICE_ROLE_KEY": "fixture-only"}
        self.plan = deploy.build_plan(result, (str(self.excel), "hsk2_30", "hsk2", str(self.output)), fingerprint, profile, profile_name="fixture")
        source = self.output / "vocab" / "catalog_revisions" / "v1" / "vocab_pack_catalog_20_30_v1.json"
        source.parent.mkdir(parents=True)
        source.write_bytes(self.seed)
        self.client = deploy.MemoryStorageClient({(deploy.STAGING_BUCKET, deploy.catalog_object_path(1)): self.seed})
        deploy.stage_packs_with_client(self.client, self.plan, confirmation=deploy.stage_confirmation_phrase("hsk2"), output_directory=self.output)
        self.key_path = self.temp / "seed"
        self.key_path.write_bytes(bytes(range(1, 33)))

    def tearDown(self):
        shutil.rmtree(self.temp)

    def test_signed_publish_catalog_archive_then_current(self):
        result = publish_signed_catalog_with_client(
            self.client,
            output_directory=self.output,
            confirmation=deploy.CATALOG_PUBLISH_CONFIRMATION,
            private_key_path=self.key_path,
            published_at="2026-07-15T00:00:00Z",
        )
        self.assertEqual("PUBLISHED", result["status"])
        self.assertEqual(2, result["catalogRevision"])
        self.assertEqual(1, result["pointerRevision"])
        self.assertEqual(14, result["entryCount"])
        self.assertIn((deploy.STAGING_BUCKET, pointer_archive_path(1)), self.client.objects)
        self.assertIn((deploy.STAGING_BUCKET, "catalogs/vocab/current.json"), self.client.objects)
        self.assertGreaterEqual([call[0] for call in self.client.calls].index("UPDATE"), [call[0] for call in self.client.calls].index("CREATE"))
        receipt = json.loads(deploy.deploy_receipt_path(self.output, "hsk2", "3.0").read_text(encoding="utf-8"))
        self.assertTrue(receipt["catalogPublished"])

    def test_read_verified_pointer_status_is_get_only_and_reports_active(self):
        publish_signed_catalog_with_client(
            self.client,
            output_directory=self.output,
            confirmation=deploy.CATALOG_PUBLISH_CONFIRMATION,
            private_key_path=self.key_path,
            published_at="2026-07-15T00:00:00Z",
        )
        self.client.calls.clear()
        status = read_verified_pointer_status(self.client, private_key_path=self.key_path)
        self.assertEqual("POINTER ACTIVE", status["status"])
        self.assertEqual(1, status["pointerRevision"])
        self.assertEqual(2, status["catalogRevision"])
        self.assertEqual(14, status["entryCount"])
        self.assertTrue(all(call[0] == "GET" for call in self.client.calls))

    def test_read_verified_pointer_status_distinguishes_absent(self):
        status = read_verified_pointer_status(self.client, private_key_path=self.key_path)
        self.assertEqual("POINTER NOT INITIALIZED", status["status"])
        self.assertTrue(all(call[0] == "GET" for call in self.client.calls if call[2] == "catalogs/vocab/current.json"))

    def test_initialize_pointer_targets_bootstrap_catalog(self):
        result = initialize_pointer_with_client(
            self.client,
            catalog_revision=1,
            catalog_object=deploy.catalog_object_path(1),
            expected_bytes=len(self.seed),
            expected_sha256=hashlib.sha256(self.seed).hexdigest(),
            confirmation="INITIALIZE VOCAB POINTER",
            private_key_path=self.key_path,
            published_at="2026-07-15T00:00:00Z",
        )
        self.assertEqual("POINTER ACTIVE", result["status"])
        self.assertEqual(1, result["pointerRevision"])
        self.assertEqual(pointer_archive_path(1), result["archivePath"])

    def test_pointer_rerun_is_noop_and_rollback_increments_pointer_revision(self):
        published = publish_signed_catalog_with_client(
            self.client, output_directory=self.output, confirmation=deploy.CATALOG_PUBLISH_CONFIRMATION,
            private_key_path=self.key_path, published_at="2026-07-15T00:00:00Z",
        )
        target_path = published["catalogObjectPath"]
        target_payload = self.client.objects[(deploy.STAGING_BUCKET, target_path)]
        same = update_pointer_with_client(
            self.client, catalog_revision=published["catalogRevision"], catalog_object=target_path,
            catalog_payload=target_payload, confirmation=deploy.CATALOG_PUBLISH_CONFIRMATION,
            private_key_path=self.key_path, published_at="2026-07-15T00:00:00Z",
        )
        self.assertEqual("ALREADY PUBLISHED", same["status"])
        rollback = update_pointer_with_client(
            self.client, catalog_revision=1, catalog_object=deploy.catalog_object_path(1), catalog_payload=self.seed,
            confirmation=deploy.CATALOG_PUBLISH_CONFIRMATION, private_key_path=self.key_path,
            published_at="2026-07-15T00:00:01Z",
        )
        self.assertEqual("POINTER ACTIVE", rollback["status"])
        self.assertEqual(2, rollback["pointerRevision"])
        self.assertEqual(1, rollback["catalogRevision"])


if __name__ == "__main__":
    unittest.main()
