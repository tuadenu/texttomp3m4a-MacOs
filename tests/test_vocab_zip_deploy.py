import hashlib
import io
import json
import shutil
import tempfile
import unittest
import sys
import types
from pathlib import Path
from unittest.mock import patch
from urllib import error as urlerror

import pandas as pd

if "pypinyin" not in sys.modules:
    fake_pypinyin = types.ModuleType("pypinyin")

    class _FakeStyle:
        NORMAL = "NORMAL"

    def _fake_lazy_pinyin(text, *args, **kwargs):
        return [str(text)]

    fake_pypinyin.Style = _FakeStyle
    fake_pypinyin.lazy_pinyin = _fake_lazy_pinyin
    sys.modules["pypinyin"] = fake_pypinyin

from pipelines import vocab_zip_deploy as deploy
from pipelines.vocab_zip_builder import SourceVocab, audio_cache_key, build_hsk30, build_vocab_pack


class VocabZipDeployTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.repo_root = Path(__file__).resolve().parents[1]
        cls.seed_catalog_path = cls.repo_root / deploy.SEED_CATALOG_PATH
        cls.seed_catalog_bytes = cls.seed_catalog_path.read_bytes()
        cls.seed_catalog_sha = hashlib.sha256(cls.seed_catalog_bytes).hexdigest()

    def setUp(self):
        self.temp_dir = Path(tempfile.mkdtemp(prefix="vocab-deploy-test-"))
        self.excel = self.temp_dir / "source.xlsx"
        self.rows = self._rows_for_total(52)
        pd.DataFrame(self.rows).to_excel(self.excel, sheet_name="hsk1_30", index=False)
        self.output_root = self.temp_dir / "output"
        self.profile = {
            "SUPABASE_URL": "https://example.supabase.co",
            "SUPABASE_BUCKET": deploy.STAGING_BUCKET,
            "SUPABASE_SERVICE_ROLE_KEY": "test-only-secret",
        }
        self._build_cache = {}

    def tearDown(self):
        shutil.rmtree(self.temp_dir)

    def _entries_key(self, catalog):
        for key in ("entries", "packs", "collections"):
            if isinstance(catalog.get(key), list):
                return key
        raise AssertionError("catalog thiếu field entry list")

    def _rows_for_total(self, total):
        return [
            {
                "index": i,
                "word": f"词{i}",
                "meaning_vi": f"nghia {i}",
                "example_zh": f"例子{i}",
                "example_vi": f"vi du {i}",
            }
            for i in range(1, total + 1)
        ]

    def _sheet_name(self, level, version):
        return f"{level}_{'20' if version == '2.0' else '30'}"

    def _seed_audio(self, level, version="3.0", sheet=None, rows=None):
        sheet = sheet or self._sheet_name(level, version)
        rows = rows or self.rows
        audio_root = self.output_root / "vocab" / version / level / "audio_cache"
        audio_root.mkdir(parents=True, exist_ok=True)
        for row in rows:
            item = SourceVocab(row["index"], row["word"], row["meaning_vi"], row["example_zh"], row["example_vi"])
            name = audio_cache_key(item, engine="gTTS", speed="Bình thường", voice="Mặc định", profile="", bitrate="32k", audio_mode="zh_vi") + ".m4a"
            (audio_root / name).write_bytes(f"audio-{version}-{level}-{row['index']}".encode("utf-8"))

    def _build(self, level, version="3.0", rows=None):
        cache_key = (version, level, len(rows or self.rows))
        if cache_key in self._build_cache:
            return self._build_cache[cache_key]
        rows = rows or self.rows
        sheet = self._sheet_name(level, version)
        pd.DataFrame(rows).to_excel(self.excel, sheet_name=sheet, index=False)
        self._seed_audio(level, version=version, sheet=sheet, rows=rows)
        result = build_vocab_pack(self.excel, sheet, level, self.output_root, version=version, generate_missing=False)
        self._build_cache[cache_key] = result
        return result

    def _plan(self, level, version="3.0", rows=None):
        result = self._build(level, version=version, rows=rows)
        bitrate = str(result.get("ttsConfig", {}).get("m4a", {}).get("bitrate", "32k"))
        receipt_fingerprint = deploy.input_fingerprint(self.excel, self._sheet_name(level, version), level, self.output_root, version=version, bitrate=bitrate)
        return deploy.build_plan(
            result,
            (str(self.excel), self._sheet_name(level, version), version, level, str(self.output_root)),
            receipt_fingerprint,
            self.profile,
            profile_name="dev",
        )

    def _write_source_snapshot(self):
        source_snapshot = self.output_root / "vocab" / "catalog_revisions" / "v1" / "vocab_pack_catalog_20_30_v1.json"
        source_snapshot.parent.mkdir(parents=True, exist_ok=True)
        source_snapshot.write_bytes(self.seed_catalog_bytes)
        return source_snapshot

    def _write_revision_snapshot(self, revision: int, payload: bytes):
        snapshot = self.output_root / "vocab" / "catalog_revisions" / f"v{revision}" / f"vocab_pack_catalog_20_30_v{revision}.json"
        snapshot.parent.mkdir(parents=True, exist_ok=True)
        snapshot.write_bytes(payload)
        return snapshot

    def _stage(self, level, client=None):
        plan = self._plan(level)
        client = client or deploy.MemoryStorageClient()
        logs = []
        result = deploy.stage_packs_with_client(
            client,
            plan,
            confirmation=deploy.stage_confirmation_phrase(level),
            output_directory=self.output_root,
            progress=logs.append,
        )
        return plan, client, logs, result

    def _write_receipts_for_levels(self, levels):
        for level in levels:
            self._stage(level)

    def test_seed_catalog_is_verified_and_has_12_enabled_entries(self):
        catalog = deploy.load_seed_catalog(self.repo_root)[1]
        key = self._entries_key(catalog)
        entries = catalog[key]
        self.assertEqual(12, len(entries))
        self.assertTrue(all(entry.get("enabled") is True for entry in entries))
        self.assertEqual(self.seed_catalog_sha, hashlib.sha256(self.seed_catalog_bytes).hexdigest())

    def test_compatibility_hash_from_base_ids_matches_audited_value(self):
        ids = [str(index) for index in range(1, 51)]
        self.assertEqual("9b1b99d5d0172de2b1ee78c385b51ebc8ac652508ed5cb500982fc0618283fdf", deploy.compatibility_hash_from_ids(ids))
        compact = json.dumps(ids, ensure_ascii=False, separators=(",", ":"), sort_keys=False).encode("utf-8")
        self.assertNotIn(b" ", compact)
        self.assertFalse(compact.endswith(b"\n"))

    def test_generic_object_paths_and_confirmation_phrases(self):
        self.assertEqual("vocab/3.0/hsk7_9/base/v1/vocab_hsk7_9_30_base_v1.zip", deploy.pack_object_path("hsk7_9", "base"))
        self.assertEqual("vocab/3.0/hsk2/plus/v1/vocab_hsk2_30_plus_v1.zip", deploy.pack_object_path("hsk2", "plus"))
        self.assertEqual("STAGE HSK7_9 3.0", deploy.stage_confirmation_phrase("hsk7_9"))
        self.assertEqual("PUBLISH VOCAB CATALOG", deploy.CATALOG_PUBLISH_CONFIRMATION)

    def test_stage_supports_generic_levels_and_writes_receipts(self):
        for level in ("hsk2", "hsk7_9"):
            with self.subTest(level=level):
                plan, client, logs, result = self._stage(level)
                receipt_path = deploy.deploy_receipt_path(self.output_root, level)
                self.assertEqual("REMOTE PACKS VERIFIED", result["status"])
                self.assertTrue(receipt_path.is_file())
                self.assertIn("classification=ABSENT", "\n".join(logs))
                expected_parts = 3 if level == "hsk7_9" else 2
                self.assertEqual(expected_parts * 2, len([call for call in client.calls if call[0] == "GET"]))
                self.assertEqual(expected_parts, len([call for call in client.calls if call[0] == "CREATE"]))
                self.assertEqual(plan.base_object_path, result["receipt"]["base"]["objectPath"])
                if level == "hsk7_9":
                    self.assertIn("plus1", result["receipt"])
                    self.assertIn("plus2", result["receipt"])
                    self.assertNotIn("plus", result["receipt"])
                else:
                    self.assertEqual(plan.plus_object_path, result["receipt"]["plus"]["objectPath"])
                self.assertFalse((self.output_root / "vocab" / "catalog_revisions").exists())

    def test_hsk79_missing_plus2_remote_verification_blocks_receipt(self):
        _, _, _, result = self._stage("hsk7_9")
        receipt = dict(result["receipt"])
        receipt["plus2RemoteVerified"] = False
        with self.assertRaises(deploy.DeployValidationError):
            deploy.validate_deploy_receipt(receipt)

    def test_stage_hsk20_uses_2_0_namespace_and_does_not_cross_versions(self):
        rows = self._rows_for_total(152)
        self._write_source_snapshot()
        plan = self._plan("hsk1", version="2.0", rows=rows)
        client = deploy.MemoryStorageClient()
        logs = []
        result = deploy.stage_packs_with_client(
            client,
            plan,
            confirmation=deploy.stage_confirmation_phrase("hsk1", "2.0"),
            output_directory=self.output_root,
            progress=logs.append,
        )
        self.assertEqual("REMOTE PACKS VERIFIED", result["status"])
        self.assertEqual("vocab/2.0/hsk1/base/v1/vocab_hsk1_20_base_v1.zip", plan.base_object_path)
        self.assertEqual("vocab/2.0/hsk1/plus/v1/vocab_hsk1_20_plus_v1.zip", plan.plus_object_path)
        self.assertEqual(self.output_root / "vocab" / "2.0" / "hsk1" / "deploy_receipt.json", deploy.deploy_receipt_path(self.output_root, "hsk1", "2.0"))
        self.assertTrue(deploy.deploy_receipt_path(self.output_root, "hsk1", "2.0").is_file())
        self.assertIn("GET verify HSK1 2.0 BASE: PASS", "\n".join(logs))
        self.assertEqual(4, len([call for call in client.calls if call[0] == "GET"]))
        self.assertEqual(2, len([call for call in client.calls if call[0] == "CREATE"]))
        self.assertTrue(all(path.startswith(("vocab/2.0/",)) for path in (result["receipt"]["base"]["objectPath"], result["receipt"]["plus"]["objectPath"])))
        receipts = deploy.collect_deploy_receipts(self.output_root, versions={"2.0"})
        self.assertEqual(1, len(receipts))
        self.assertEqual("2.0", receipts[0]["version"])

    def test_stage_bad_confirmation_does_not_call_network(self):
        plan = self._plan("hsk2")
        client = deploy.MemoryStorageClient()
        with self.assertRaises(deploy.DeployValidationError):
            deploy.stage_packs_with_client(client, plan, confirmation="WRONG", output_directory=self.output_root)
        self.assertEqual([], client.calls)

    def test_stage_present_match_reuses_remote_objects(self):
        plan = self._plan("hsk2")
        base_bytes = Path(plan.base_local_path).read_bytes()
        plus_bytes = Path(plan.plus_local_path).read_bytes()
        client = deploy.MemoryStorageClient(objects={
            (plan.bucket, plan.base_object_path): base_bytes,
            (plan.bucket, plan.plus_object_path): plus_bytes,
        })
        logs = []
        deploy.stage_packs_with_client(
            client,
            plan,
            confirmation=deploy.stage_confirmation_phrase("hsk2"),
            output_directory=self.output_root,
            progress=logs.append,
        )
        self.assertIn("classification=PRESENT_MATCH", "\n".join(logs))
        self.assertEqual([], [call for call in client.calls if call[0] == "CREATE"])

    def test_stage_present_conflict_is_rejected_without_upload(self):
        plan = self._plan("hsk2")
        client = deploy.MemoryStorageClient(objects={(plan.bucket, plan.base_object_path): b"different"})
        logs = []
        with self.assertRaises(deploy.DeployValidationError):
            deploy.stage_packs_with_client(
                client,
                plan,
                confirmation=deploy.stage_confirmation_phrase("hsk2"),
                output_directory=self.output_root,
                progress=logs.append,
            )
        self.assertIn("classification=PRESENT_CONFLICT", "\n".join(logs))
        self.assertEqual([], [call for call in client.calls if call[0] == "CREATE"])

    def test_stage_plus_failure_does_not_write_receipt_or_catalog(self):
        plan = self._plan("hsk2")

        class FailingPlus(deploy.MemoryStorageClient):
            def create_object(self, bucket, object_path, payload, content_type):
                if object_path == plan.plus_object_path:
                    raise RuntimeError("simulated plus failure")
                return super().create_object(bucket, object_path, payload, content_type)

        client = FailingPlus()
        with self.assertRaises(deploy.PartialDeployError):
            deploy.stage_packs_with_client(
                client,
                plan,
                confirmation=deploy.stage_confirmation_phrase("hsk2"),
                output_directory=self.output_root,
            )
        self.assertFalse(deploy.deploy_receipt_path(self.output_root, "hsk2").exists())
        self.assertFalse(any(path.name.startswith("vocab_pack_catalog") for path in (self.output_root / "vocab").rglob("*.json")))

    def test_http_400_not_found_and_404_are_absent(self):
        client = deploy.SupabaseStorageRestClient("https://example.supabase.co", "secret", network_enabled=True, retries=0)
        bodies = [
            b'{"statusCode":404,"message":"Object not found"}',
            b'{"statusCode":"404","message":"missing"}',
            b'{"error":"not_found","message":"Object not found"}',
        ]
        for body in bodies:
            err = urlerror.HTTPError("https://example", 400, "bad", {}, io.BytesIO(body))
            with self.subTest(body=body):
                with patch.object(deploy.urlrequest, "urlopen", side_effect=err):
                    with self.assertRaises(deploy.StorageNotFound) as caught:
                        client.get_object("bucket", "object")
                self.assertEqual(400, caught.exception.http_status)
        err = urlerror.HTTPError("https://example", 404, "missing", {}, io.BytesIO(b""))
        with patch.object(deploy.urlrequest, "urlopen", side_effect=err):
            with self.assertRaises(deploy.StorageNotFound) as caught:
                client.get_object("bucket", "object")
        self.assertEqual(404, caught.exception.http_status)

    def test_http_401_403_500_are_errors(self):
        for status in (401, 403, 500):
            client = deploy.SupabaseStorageRestClient("https://example.supabase.co", "secret", network_enabled=True, retries=0)
            err = urlerror.HTTPError("https://example", status, "error", {}, io.BytesIO(b'{"error":"denied"}'))
            with self.subTest(status=status):
                with patch.object(deploy.urlrequest, "urlopen", side_effect=err):
                    with self.assertRaises(deploy.DeployValidationError):
                        client.get_object("bucket", "object")

    def test_publish_catalog_requires_staged_receipts_and_preserves_source(self):
        self._write_source_snapshot()
        self._write_receipts_for_levels(("hsk2", "hsk7_9"))
        source_bytes = self.seed_catalog_bytes
        source_path = deploy.catalog_object_path(1)
        client = deploy.MemoryStorageClient(objects={(deploy.STAGING_BUCKET, source_path): source_bytes})
        logs = []
        result = deploy.publish_catalog_with_client(
            client,
            output_directory=self.output_root,
            confirmation=deploy.CATALOG_PUBLISH_CONFIRMATION,
            progress=logs.append,
        )
        self.assertEqual("CATALOG PUBLISHED", result["status"])
        self.assertEqual(2, result["revision"])
        self.assertEqual(deploy.catalog_object_path(2), result["objectPath"])
        self.assertEqual(17, result["entryCount"])
        self.assertIn("classification=ABSENT", "\n".join(logs))
        snapshot = deploy.load_catalog_source_snapshot(self.output_root)
        self.assertEqual(2, snapshot.revision)
        key = self._entries_key(snapshot.catalog)
        entries = snapshot.catalog[key]
        seed_catalog = json.loads(source_bytes.decode("utf-8"))
        seed_key = self._entries_key(seed_catalog)
        self.assertEqual(17, len(entries))
        legacy_entries = entries[:12]
        self.assertEqual(seed_catalog[seed_key], legacy_entries)
        for level in ("hsk2", "hsk7_9"):
            receipt = json.loads(deploy.deploy_receipt_path(self.output_root, level).read_text(encoding="utf-8"))
            self.assertTrue(receipt["catalogPublished"])

    def test_publish_does_not_upload_zip_objects(self):
        self._write_source_snapshot()
        self._write_receipts_for_levels(("hsk2",))
        source_path = deploy.catalog_object_path(1)
        client = deploy.MemoryStorageClient(objects={(deploy.STAGING_BUCKET, source_path): self.seed_catalog_bytes})
        result = deploy.publish_catalog_with_client(
            client,
            output_directory=self.output_root,
            confirmation=deploy.CATALOG_PUBLISH_CONFIRMATION,
        )
        create_paths = [call[2] for call in client.calls if call[0] == "CREATE"]
        self.assertEqual([result["objectPath"]], create_paths)
        self.assertTrue(all(not path.endswith(".zip") for path in create_paths))

    def test_publish_reads_current_revision_and_hsk1_receipt_is_noop(self):
        self._write_revision_snapshot(1, self.seed_catalog_bytes)
        current_v2 = (self.repo_root / "output" / "vocab" / "3.0" / "hsk1" / "deploy_preflight" / "combined_catalog_dry_run.json").read_bytes()
        self._write_revision_snapshot(2, current_v2)
        self._stage("hsk2")
        current_v2_catalog = json.loads(current_v2.decode("utf-8"))
        v2_key = self._entries_key(current_v2_catalog)
        hsk1_entries = [entry for entry in current_v2_catalog[v2_key] if entry["version"] == "3.0" and entry["level"] == "hsk1"]
        hsk1_receipt = {
            "schemaVersion": 1,
            "version": deploy.STANDARD_VERSION,
            "level": "hsk1",
            "packVersion": deploy.PACK_VERSION,
            "compatibilityHash": hsk1_entries[0]["compatibilityHash"],
            "base": {**hsk1_entries[0], "remoteVerified": True},
            "plus": {**hsk1_entries[1], "remoteVerified": True},
        }
        deploy.deploy_receipt_path(self.output_root, "hsk1").parent.mkdir(parents=True, exist_ok=True)
        deploy.deploy_receipt_path(self.output_root, "hsk1").write_text(json.dumps(hsk1_receipt, ensure_ascii=False, separators=(",", ":")) + "\n", encoding="utf-8")
        source_path = deploy.catalog_object_path(2)
        client = deploy.MemoryStorageClient(objects={(deploy.STAGING_BUCKET, source_path): current_v2})
        result = deploy.publish_catalog_with_client(
            client,
            output_directory=self.output_root,
            confirmation=deploy.CATALOG_PUBLISH_CONFIRMATION,
        )
        self.assertEqual(3, result["revision"])
        self.assertEqual(16, result["entryCount"])
        snapshot = deploy.load_catalog_source_snapshot(self.output_root)
        self.assertEqual(3, snapshot.revision)
        self.assertEqual(16, snapshot.entry_count)
        self.assertEqual(16, len(snapshot.catalog[self._entries_key(snapshot.catalog)]))

    def test_catalog_revision_progresses_14_to_16_to_26_and_preserves_previous_entries(self):
        self._write_revision_snapshot(1, self.seed_catalog_bytes)
        current_v2 = (self.repo_root / "output" / "vocab" / "3.0" / "hsk1" / "deploy_preflight" / "combined_catalog_dry_run.json").read_bytes()
        self._write_revision_snapshot(2, current_v2)
        seed_catalog = json.loads(self.seed_catalog_bytes.decode("utf-8"))
        seed_key = self._entries_key(seed_catalog)
        current_v2_catalog = json.loads(current_v2.decode("utf-8"))
        v2_key = self._entries_key(current_v2_catalog)
        v2_entries = current_v2_catalog[v2_key]

        self._stage("hsk2")
        client_v3 = deploy.MemoryStorageClient(objects={(deploy.STAGING_BUCKET, deploy.catalog_object_path(2)): current_v2})
        result_v3 = deploy.publish_catalog_with_client(client_v3, output_directory=self.output_root, confirmation=deploy.CATALOG_PUBLISH_CONFIRMATION)
        self.assertEqual(3, result_v3["revision"])
        self.assertEqual(16, result_v3["entryCount"])

        self._stage("hsk3")
        self._stage("hsk4")
        self._stage("hsk5")
        self._stage("hsk6")
        self._stage("hsk7_9")
        client_v4 = deploy.MemoryStorageClient(objects={(deploy.STAGING_BUCKET, deploy.catalog_object_path(3)): (self.output_root / "vocab" / "catalog_revisions" / "v3" / "vocab_pack_catalog_20_30_v3.json").read_bytes()})
        result_v4 = deploy.publish_catalog_with_client(client_v4, output_directory=self.output_root, confirmation=deploy.CATALOG_PUBLISH_CONFIRMATION)
        self.assertEqual(4, result_v4["revision"])
        self.assertEqual(27, result_v4["entryCount"])
        snapshot_v4 = deploy.load_catalog_source_snapshot(self.output_root)
        self.assertEqual(4, snapshot_v4.revision)
        self.assertEqual(27, snapshot_v4.entry_count)
        merged_entries = snapshot_v4.catalog[self._entries_key(snapshot_v4.catalog)]
        self.assertEqual(v2_entries, merged_entries[:14])
        self.assertEqual(seed_catalog[seed_key], merged_entries[:12])
        self.assertEqual(len(merged_entries), len({(e["version"], e["level"], e["segment"]) for e in merged_entries}))
        hsk79_segments = {(e["version"], e["level"], e["segment"]) for e in merged_entries if e["level"] == "hsk7_9"}
        self.assertEqual({("3.0", "hsk7_9", "base"), ("3.0", "hsk7_9", "plus1"), ("3.0", "hsk7_9", "plus2")}, hsk79_segments)
        self.assertEqual(27, len(merged_entries))

    def test_publish_reuses_existing_catalog_when_sha_matches(self):
        self._write_source_snapshot()
        self._write_receipts_for_levels(("hsk2",))
        source_path = deploy.catalog_object_path(1)
        target_path = deploy.catalog_object_path(2)
        target_bytes = deploy._canonical_json_bytes(
            deploy.merge_catalog(
                deploy.load_catalog_source_snapshot(self.output_root).catalog,
                [deploy.catalog_entry_from_plan(self._plan("hsk2"), "base"), deploy.catalog_entry_from_plan(self._plan("hsk2"), "plus")],
            )
        )
        client = deploy.MemoryStorageClient(objects={
            (deploy.STAGING_BUCKET, source_path): self.seed_catalog_bytes,
            (deploy.STAGING_BUCKET, target_path): target_bytes,
        })
        logs = []
        result = deploy.publish_catalog_with_client(
            client,
            output_directory=self.output_root,
            confirmation=deploy.CATALOG_PUBLISH_CONFIRMATION,
            progress=logs.append,
        )
        self.assertIn("classification=PRESENT_MATCH", "\n".join(logs))
        self.assertEqual(target_path, result["objectPath"])

    def test_duplicate_receipt_additions_are_rejected(self):
        self._write_source_snapshot()
        plan = self._plan("hsk2")
        receipt = {
            "schemaVersion": 1,
            "version": deploy.STANDARD_VERSION,
            "level": "hsk2",
            "packVersion": deploy.PACK_VERSION,
            "compatibilityHash": plan.compatibility_hash,
            "base": {**deploy.catalog_entry_from_plan(plan, "base"), "remoteVerified": True},
            "plus": {**deploy.catalog_entry_from_plan(plan, "plus"), "remoteVerified": True},
        }
        with patch.object(deploy, "collect_deploy_receipts", return_value=[deploy.validate_deploy_receipt(receipt), deploy.validate_deploy_receipt(receipt)]):
            with self.assertRaises(deploy.DeployValidationError):
                deploy.prepare_catalog_publish(self.output_root)

    def test_duplicate_packid_and_collectionid_are_rejected_in_catalog_validation(self):
        base = deploy.catalog_entry_from_plan(self._plan("hsk2"), "base")
        bad_catalog = json.loads(self.seed_catalog_bytes.decode("utf-8"))
        key = self._entries_key(bad_catalog)
        bad_catalog[key].append(dict(bad_catalog[key][0]))
        with self.assertRaises(deploy.DeployValidationError):
            deploy.verify_catalog_payload(deploy._canonical_json_bytes(bad_catalog))

    def test_build_plan_does_not_mutate_profile_or_expose_secret(self):
        plan = self._plan("hsk2")
        original = dict(self.profile)
        deploy.build_plan(
            self._build("hsk2"),
            (str(self.excel), "hsk1_30", "hsk2", str(self.output_root)),
            deploy.input_fingerprint(self.excel, "hsk1_30", "hsk2", self.output_root, bitrate="32k"),
            self.profile,
            profile_name="dev",
        )
        self.assertEqual(original, self.profile)
        self.assertNotIn("test-only-secret", repr(plan))

    def test_create_only_upload_uses_post_and_sets_upsert_false(self):
        client = deploy.SupabaseStorageRestClient("https://example.supabase.co", "secret", network_enabled=True, retries=0)
        captured = {}

        class FakeResponse:
            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return False

            def read(self):
                return b"ok"

        def fake_urlopen(request, timeout=None, context=None):
            captured["method"] = request.get_method()
            captured["headers"] = {k.lower(): v for k, v in request.header_items()}
            captured["url"] = request.full_url
            return FakeResponse()

        with patch.object(deploy.urlrequest, "urlopen", side_effect=fake_urlopen):
            client.create_object("bucket", "path/object.zip", b"data", "application/zip")
        self.assertEqual("POST", captured["method"])
        self.assertEqual("false", captured["headers"].get("x-upsert"))
        self.assertEqual("application/zip", captured["headers"].get("content-type"))

    def test_catalog_serialization_is_deterministic(self):
        self._write_revision_snapshot(1, self.seed_catalog_bytes)
        current_v2 = (self.repo_root / "output" / "vocab" / "3.0" / "hsk1" / "deploy_preflight" / "combined_catalog_dry_run.json").read_bytes()
        self._write_revision_snapshot(2, current_v2)
        self._stage("hsk2")
        plan = deploy.prepare_catalog_publish(self.output_root)
        merged_a = deploy.merge_catalog(plan.source.catalog, list(plan.additions))
        merged_b = deploy.merge_catalog(plan.source.catalog, list(plan.additions))
        bytes_a = deploy._canonical_json_bytes(merged_a)
        bytes_b = deploy._canonical_json_bytes(merged_b)
        self.assertEqual(bytes_a, bytes_b)
        self.assertEqual(hashlib.sha256(bytes_a).hexdigest(), hashlib.sha256(bytes_b).hexdigest())

    def test_pack_and_catalog_object_paths_are_level_generic(self):
        for level in ("hsk1", "hsk2", "hsk3", "hsk4", "hsk5", "hsk6", "hsk7_9"):
            with self.subTest(level=level):
                self.assertEqual(f"vocab/3.0/{level}/base/v1/vocab_{level}_30_base_v1.zip", deploy.pack_object_path(level, "base"))
                self.assertEqual(f"vocab/3.0/{level}/plus/v1/vocab_{level}_30_plus_v1.zip", deploy.pack_object_path(level, "plus"))

    def test_no_legacy_importer_is_referenced_in_deploy_module(self):
        source = Path(deploy.__file__).read_text(encoding="utf-8")
        self.assertNotIn("import_hsk1_to_supabase", source)


if __name__ == "__main__":
    unittest.main()
