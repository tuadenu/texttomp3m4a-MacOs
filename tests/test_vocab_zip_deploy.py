import copy
import hashlib
import io
import json
import shutil
import tempfile
import unittest
from urllib import error as urlerror
from pathlib import Path
from unittest.mock import patch

from pipelines import vocab_zip_deploy as deploy
from pipelines.vocab_zip_builder import build_hsk30


class VocabZipDeployTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = Path(tempfile.mkdtemp(prefix="vocab-deploy-test-"))
        self.excel = self.temp_dir / "source.xlsx"
        rows = [
            {"index": i, "word": f"词{i}", "meaning_vi": f"nghia {i}", "example_zh": f"例子{i}", "example_vi": f"vi du {i}"}
            for i in range(1, 53)
        ]
        import pandas as pd

        pd.DataFrame(rows).to_excel(self.excel, sheet_name="hsk1_30", index=False)
        self.output = self.temp_dir / "out"
        from pipelines.vocab_zip_builder import SourceVocab, audio_filename

        audio_root = self.output / "vocab" / "3.0" / "hsk1" / "source_audio"
        audio_root.mkdir(parents=True)
        for row in rows:
            item = SourceVocab(row["index"], row["word"], row["meaning_vi"], row["example_zh"], row["example_vi"])
            (audio_root / audio_filename("hsk1_30", item)).write_bytes(f"audio-{row['index']}".encode())
        self.result = build_hsk30(self.excel, "hsk1_30", "hsk1", self.output, generate_missing=False)
        self.receipt_fingerprint = deploy.input_fingerprint(self.excel, "hsk1_30", "hsk1", self.output, bitrate="32k")
        self.profile = {
            "SUPABASE_URL": "https://example.supabase.co",
            "SUPABASE_BUCKET": "vocab-pack-staging",
            "SUPABASE_SERVICE_ROLE_KEY": "test-only-secret",
        }
        self.plan = deploy.build_plan(
            self.result,
            (str(self.excel), "hsk1_30", "hsk1", str(self.output)),
            self.receipt_fingerprint,
            self.profile,
            profile_name="dev",
        )
        self.source_catalog = {
            "schemaVersion": 1,
            "entries": [
                {
                    "version": "2.0",
                    "level": f"hsk{(index // 2) + 1}",
                    "segment": "base" if index % 2 else "plus",
                    "packId": f"vocab:2.0:hsk{(index // 2) + 1}:{'base' if index % 2 else 'plus'}:v1",
                    "collectionId": f"vocab_level::2.0::hsk{(index // 2) + 1}::{'base' if index % 2 else 'plus'}::v1",
                    "enabled": True,
                    "objectPath": f"legacy/{index}.zip",
                    "sha256": f"legacy-{index}",
                    "packVersion": 1,
                }
                for index in range(12)
            ],
        }
        self.source_bytes = (json.dumps(self.source_catalog, ensure_ascii=False, separators=(",", ":")) + "\n").encode()

    def tearDown(self):
        shutil.rmtree(self.temp_dir)

    def _source_patch(self):
        return patch.object(deploy, "CATALOG_SOURCE_BYTES", len(self.source_bytes)), patch.object(
            deploy, "CATALOG_SOURCE_SHA256", hashlib.sha256(self.source_bytes).hexdigest()
        )

    def _run(self, client=None, confirmation=None, **kwargs):
        client = client or deploy.MemoryStorageClient(urls={deploy.CATALOG_SOURCE_URL: self.source_bytes})
        p1, p2 = self._source_patch()
        with p1, p2:
            return deploy.deploy_with_client(
                client,
                self.plan,
                source_catalog_payload=self.source_bytes,
                confirmation=confirmation or deploy.CONFIRMATION_PHRASE,
                **kwargs,
            )

    def _contract(self, base_manifest=None, plus_manifest=None, base_vocab=None, plus_vocab=None):
        base_manifest = copy.deepcopy(base_manifest or self.result["base"]["manifest"])
        plus_manifest = copy.deepcopy(plus_manifest or self.result["plus"]["manifest"])
        base_vocab = copy.deepcopy(base_vocab or self.result["base"]["vocab"])
        plus_vocab = copy.deepcopy(plus_vocab or self.result["plus"]["vocab"])
        with patch.object(deploy, "_read_pack_json", side_effect=[(base_manifest, base_vocab), (plus_manifest, plus_vocab)]):
            return deploy.validate_compatibility_contract("base.zip", "plus.zip")

    def test_base_ids_hash_matches_audited_contract_without_newline(self):
        ids = [str(index) for index in range(1, 51)]
        self.assertEqual("9b1b99d5d0172de2b1ee78c385b51ebc8ac652508ed5cb500982fc0618283fdf", deploy.compatibility_hash_from_ids(ids))
        compact = json.dumps(ids, ensure_ascii=False, separators=(",", ":"), sort_keys=False).encode("utf-8")
        self.assertNotIn(b" ", compact)
        self.assertFalse(compact.endswith(b"\n"))

    def test_compatibility_hash_rejects_number_ids_and_order_changes_hash(self):
        with self.assertRaises(deploy.DeployValidationError):
            deploy.compatibility_hash_from_ids([1, "2"])
        ordered = [str(index) for index in range(1, 51)]
        reordered = ordered[:]
        reordered[0], reordered[1] = reordered[1], reordered[0]
        self.assertNotEqual(deploy.compatibility_hash_from_ids(ordered), deploy.compatibility_hash_from_ids(reordered))

    def test_contract_returns_same_hash_for_base_manifest_plus_base_hash_and_catalog(self):
        contract = self._contract()
        self.assertEqual(self.plan.compatibility_hash, contract["compatibilityHash"])
        self.assertEqual(contract["compatibilityHash"], self.result["base"]["manifest"]["orderedVocabIdsSha256"])
        self.assertEqual(contract["compatibilityHash"], self.result["plus"]["manifest"]["baseOrderedVocabIdsSha256"])
        self.assertEqual(contract["compatibilityHash"], "9b1b99d5d0172de2b1ee78c385b51ebc8ac652508ed5cb500982fc0618283fdf")

    def test_contract_rejects_missing_or_duplicate_base_id(self):
        base_manifest = copy.deepcopy(self.result["base"]["manifest"])
        base_manifest["orderedVocabIds"] = base_manifest["orderedVocabIds"][:-1]
        with self.assertRaises(deploy.DeployValidationError):
            self._contract(base_manifest=base_manifest)
        base_manifest = copy.deepcopy(self.result["base"]["manifest"])
        base_manifest["orderedVocabIds"][-1] = base_manifest["orderedVocabIds"][-2]
        with self.assertRaises(deploy.DeployValidationError):
            self._contract(base_manifest=base_manifest)

    def test_contract_rejects_manifest_relationship_mismatches(self):
        base_manifest = copy.deepcopy(self.result["base"]["manifest"])
        base_manifest["orderedVocabIdsSha256"] = "0" * 64
        with self.assertRaises(deploy.DeployValidationError):
            self._contract(base_manifest=base_manifest)
        plus_manifest = copy.deepcopy(self.result["plus"]["manifest"])
        plus_manifest["baseOrderedVocabIdsSha256"] = "0" * 64
        with self.assertRaises(deploy.DeployValidationError):
            self._contract(plus_manifest=plus_manifest)
        plus_manifest["baseOrderedVocabIdsSha256"] = self.result["plus"]["manifest"]["baseOrderedVocabIdsSha256"]
        plus_manifest["requiresPackId"] = "wrong"
        with self.assertRaises(deploy.DeployValidationError):
            self._contract(plus_manifest=plus_manifest)
        plus_manifest["requiresPackId"] = self.result["plus"]["manifest"]["requiresPackId"]
        plus_manifest["compatibleBaseVersion"] = 99
        with self.assertRaises(deploy.DeployValidationError):
            self._contract(plus_manifest=plus_manifest)

    def test_contract_rejects_base_plus_overlap_and_does_not_use_plus_hash(self):
        plus_vocab = copy.deepcopy(self.result["plus"]["vocab"])
        plus_vocab[0]["id"] = "1"
        plus_manifest = copy.deepcopy(self.result["plus"]["manifest"])
        plus_manifest["orderedVocabIds"][0] = "1"
        with self.assertRaises(deploy.DeployValidationError):
            self._contract(plus_manifest=plus_manifest, plus_vocab=plus_vocab)
        contract = self._contract()
        self.assertNotEqual(contract["compatibilityHash"], self.result["plus"]["manifest"]["orderedVocabIdsSha256"])

    def test_catalog_rejects_different_base_plus_compatibility_hash(self):
        base = deploy.catalog_entry_from_manifest(self.result["base"]["manifest"], sha256=self.plan.base_sha256, zip_bytes=self.plan.base_bytes, compatibility_hash="base-hash")
        plus = deploy.catalog_entry_from_manifest(self.result["plus"]["manifest"], sha256=self.plan.plus_sha256, zip_bytes=self.plan.plus_bytes, compatibility_hash="plus-hash")
        with self.assertRaises(deploy.DeployValidationError):
            deploy.merge_catalog(self.source_catalog, base, plus)

    def test_catalog_dry_run_reports_counts_sha_and_preserves_legacy(self):
        p1, p2 = self._source_patch()
        with p1, p2:
            report = deploy.build_catalog_dry_run(
                self.source_bytes,
                self.plan.base_local_path,
                self.plan.plus_local_path,
                base_sha256=self.plan.base_sha256,
                plus_sha256=self.plan.plus_sha256,
            )
        self.assertEqual("PASS", report["status"])
        self.assertEqual(12, report["legacyEntryCount"])
        self.assertEqual(2, report["hsk30EntryCount"])
        self.assertEqual(0, report["duplicateIdentityCount"])
        self.assertEqual(self.plan.compatibility_hash, report["compatibilityHash"])
        self.assertEqual(report["sha256"], hashlib.sha256(report["payload"]).hexdigest())
        self.assertEqual(self.source_catalog["entries"], report["catalog"]["entries"][:12])

    def test_confirmation_wrong_does_not_call_network(self):
        client = deploy.MemoryStorageClient(urls={deploy.CATALOG_SOURCE_URL: self.source_bytes})
        with self.assertRaises(deploy.DeployValidationError):
            self._run(client, confirmation="wrong")
        self.assertEqual([], client.calls)

    def test_cancel_is_local_only(self):
        client = deploy.MemoryStorageClient(urls={deploy.CATALOG_SOURCE_URL: self.source_bytes})
        with self.assertRaises(deploy.DeployValidationError):
            deploy.require_confirmation("")
        self.assertEqual([], client.calls)

    def test_source_sha_mismatch_rejected_before_upload(self):
        bad = b"bad catalog"
        client = deploy.MemoryStorageClient(urls={deploy.CATALOG_SOURCE_URL: bad})
        with patch.object(deploy, "CATALOG_SOURCE_BYTES", len(bad)), patch.object(deploy, "CATALOG_SOURCE_SHA256", "0" * 64):
            with self.assertRaises(deploy.DeployValidationError):
                deploy.deploy_with_client(client, self.plan, source_catalog_payload=bad, confirmation=deploy.CONFIRMATION_PHRASE)
        self.assertFalse(any(call[0] == "CREATE" for call in client.calls))

    def test_upload_and_get_verify_success(self):
        client = deploy.MemoryStorageClient()
        result = self._run(client)
        self.assertEqual("PUBLISH PASS", result["status"])
        self.assertEqual(3, len([call for call in client.calls if call[0] == "CREATE"]))
        self.assertEqual(6, len([call for call in client.calls if call[0] == "GET"]))

    def test_source_catalog_is_fetched_before_zip_upload(self):
        client = deploy.MemoryStorageClient(urls={deploy.CATALOG_SOURCE_URL: self.source_bytes})
        p1, p2 = self._source_patch()
        with p1, p2:
            deploy.deploy_with_client(
                client,
                self.plan,
                source_catalog_payload=None,
                confirmation=deploy.CONFIRMATION_PHRASE,
            )
        self.assertEqual("GET_URL", client.calls[0][0])
        first_create = next(index for index, call in enumerate(client.calls) if call[0] == "CREATE")
        self.assertLess(0, first_create)

    def test_existing_same_sha_is_reused(self):
        client = deploy.MemoryStorageClient()
        client.objects[(self.plan.bucket, self.plan.base_object_path)] = Path(self.plan.base_local_path).read_bytes()
        client.objects[(self.plan.bucket, self.plan.plus_object_path)] = Path(self.plan.plus_local_path).read_bytes()
        self._run(client)
        creates = [call for call in client.calls if call[0] == "CREATE"]
        self.assertEqual(1, len(creates))
        self.assertEqual(self.plan.catalog_target_path, creates[0][2])

    def test_existing_different_sha_is_rejected_without_overwrite(self):
        client = deploy.MemoryStorageClient(objects={(self.plan.bucket, self.plan.base_object_path): b"different"})
        with self.assertRaises(deploy.DeployValidationError):
            self._run(client)
        self.assertEqual([], [call for call in client.calls if call[0] == "CREATE"])

    def test_plus_failure_does_not_publish_catalog(self):
        class FailingPlus(deploy.MemoryStorageClient):
            def create_object(self, bucket, object_path, payload, content_type):
                if object_path == deploy.PLUS_OBJECT_PATH:
                    raise RuntimeError("simulated PLUS failure")
                return super().create_object(bucket, object_path, payload, content_type)

        client = FailingPlus()
        with self.assertRaises(deploy.PartialDeployError):
            self._run(client)
        self.assertIn((self.plan.bucket, self.plan.base_object_path), client.objects)
        self.assertNotIn((self.plan.bucket, self.plan.catalog_target_path), client.objects)

    def test_remote_catalog_mismatch_fails_after_zips_without_overwrite(self):
        client = deploy.MemoryStorageClient(objects={(self.plan.bucket, self.plan.catalog_target_path): b"old"})
        with self.assertRaises(deploy.DeployValidationError):
            self._run(client)
        self.assertEqual(b"old", client.objects[(self.plan.bucket, self.plan.catalog_target_path)])

    def test_catalog_preserves_legacy_and_adds_exactly_two(self):
        base = deploy.catalog_entry_from_manifest(self.result["base"]["manifest"], sha256=self.plan.base_sha256, zip_bytes=self.plan.base_bytes, compatibility_hash="hash")
        plus = deploy.catalog_entry_from_manifest(self.result["plus"]["manifest"], sha256=self.plan.plus_sha256, zip_bytes=self.plan.plus_bytes, compatibility_hash="hash")
        merged = deploy.merge_catalog(self.source_catalog, base, plus)
        self.assertEqual(self.source_catalog["entries"], merged["entries"][:12])
        self.assertEqual({"base", "plus"}, {entry["segment"] for entry in merged["entries"] if entry["version"] == "3.0"})

    def test_duplicate_catalog_identity_rejected(self):
        base = deploy.catalog_entry_from_manifest(self.result["base"]["manifest"], sha256=self.plan.base_sha256, zip_bytes=self.plan.base_bytes, compatibility_hash="hash")
        source = copy.deepcopy(self.source_catalog)
        source["entries"].append(base)
        with self.assertRaises(deploy.DeployValidationError):
            deploy.merge_catalog(source, base, base)

    def test_build_gate_rejects_changed_input(self):
        changed = deploy.input_fingerprint(self.excel, "hsk1_30", "hsk1", self.output, bitrate="26k")
        with self.assertRaises(deploy.DeployValidationError):
            deploy.build_plan(self.result, (str(self.excel), "hsk1_30", "hsk1", str(self.output)), changed, self.profile)

    def test_build_gate_rejects_missing_profile_and_wrong_bucket(self):
        with self.assertRaises(deploy.DeployValidationError):
            deploy.build_plan(self.result, (str(self.excel), "hsk1_30", "hsk1", str(self.output)), self.receipt_fingerprint, {"SUPABASE_BUCKET": "", "SUPABASE_URL": "x", "SUPABASE_SERVICE_ROLE_KEY": "fake"})

    def test_legacy_profile_bucket_is_not_reused_for_hsk30(self):
        profile = dict(self.profile, SUPABASE_BUCKET="audio")
        plan = deploy.build_plan(self.result, (str(self.excel), "hsk1_30", "hsk1", str(self.output)), self.receipt_fingerprint, profile)
        self.assertEqual(deploy.PILOT_BUCKET, plan.bucket)

    def test_only_zip_and_catalog_objects_are_created(self):
        client = deploy.MemoryStorageClient()
        self._run(client)
        created = {call[2] for call in client.calls if call[0] == "CREATE"}
        self.assertEqual({self.plan.base_object_path, self.plan.plus_object_path, self.plan.catalog_target_path}, created)
        self.assertFalse(any(path.endswith((".xlsx", ".csv", "vocab.json", "manifest.json", ".m4a")) for path in created))

    def test_no_legacy_importer_in_deploy_module(self):
        source = Path(deploy.__file__).read_text(encoding="utf-8")
        self.assertNotIn("import_hsk1_to_supabase", source)

    def test_rest_client_is_network_disabled_by_default_and_plan_does_not_expose_key(self):
        client = deploy.SupabaseStorageRestClient("https://example.supabase.co", "test-only-secret")
        with self.assertRaises(deploy.DeployValidationError):
            client.get_url("https://example.invalid/catalog.json")
        self.assertNotIn("test-only-secret", repr(self.plan))

    def test_read_only_client_has_no_write_path(self):
        client = deploy.ReadOnlySupabaseStorageClient("https://example.supabase.co")
        self.assertEqual([], client.methods)
        with self.assertRaises(deploy.DeployValidationError):
            client.create_object("bucket", "path", b"x", "application/octet-stream")
        with self.assertRaises(deploy.DeployValidationError):
            client.update_object("bucket", "path", b"x")
        with self.assertRaises(deploy.DeployValidationError):
            client.delete_object("bucket", "path")
        self.assertEqual([], client.methods)

    def test_http_400_not_found_body_is_absent(self):
        client = deploy.SupabaseStorageRestClient("https://example.supabase.co", "secret", network_enabled=True, retries=0)
        cases = [
            b'{"statusCode":404,"message":"Object not found"}',
            b'{"error":"not_found","message":"Object not found"}',
        ]
        for body in cases:
            error = urlerror.HTTPError("https://example", 400, "bad", {}, io.BytesIO(body))
            with patch.object(deploy.urlrequest, "urlopen", side_effect=error):
                with self.assertRaises(deploy.StorageNotFound) as caught:
                    client.get_object("bucket", "object")
            self.assertEqual(400, caught.exception.http_status)

    def test_http_404_is_absent_but_auth_and_server_errors_are_not(self):
        for status in (401, 403, 500):
            client = deploy.SupabaseStorageRestClient("https://example.supabase.co", "secret", network_enabled=True, retries=0)
            error = urlerror.HTTPError("https://example", status, "error", {}, io.BytesIO(b'{"error":"denied"}'))
            with patch.object(deploy.urlrequest, "urlopen", side_effect=error):
                with self.assertRaises(deploy.DeployValidationError):
                    client.get_object("bucket", "object")
        client = deploy.SupabaseStorageRestClient("https://example.supabase.co", "secret", network_enabled=True, retries=0)
        error = urlerror.HTTPError("https://example", 404, "missing", {}, io.BytesIO(b""))
        with patch.object(deploy.urlrequest, "urlopen", side_effect=error):
            with self.assertRaises(deploy.StorageNotFound) as caught:
                client.get_object("bucket", "object")
        self.assertEqual(404, caught.exception.http_status)

    def test_absent_probe_uploads_create_only_and_logs(self):
        client = deploy.MemoryStorageClient()
        logs = []
        self._run(client, progress=logs.append)
        self.assertIn("classification=ABSENT", "\n".join(logs))
        self.assertEqual(3, len([call for call in client.calls if call[0] == "CREATE"]))

    def test_present_conflict_logs_and_does_not_upload(self):
        client = deploy.MemoryStorageClient(objects={(self.plan.bucket, self.plan.base_object_path): b"different"})
        logs = []
        with self.assertRaises(deploy.DeployValidationError):
            self._run(client, progress=logs.append)
        self.assertIn("classification=PRESENT_CONFLICT", "\n".join(logs))
        self.assertFalse(any(call[0] == "CREATE" for call in client.calls))

    def test_read_only_preflight_absent_objects_is_local_pass_and_get_only(self):
        class AbsentReadOnly:
            def __init__(self, payload):
                self.payload = payload
                self.methods = []

            def get_url(self, url):
                self.methods.append("GET")
                return self.payload

            def get_object(self, bucket, object_path):
                self.methods.append("GET")
                raise deploy.StorageNotFound(object_path)

        client = AbsentReadOnly(self.source_bytes)
        p1, p2 = self._source_patch()
        with p1, p2:
            report = deploy.run_hsk1_read_only_preflight(
                self.result,
                (str(self.excel), "hsk1_30", "hsk1", str(self.output)),
                self.receipt_fingerprint,
                self.profile,
                client=client,
                output_directory=self.output,
            )
        self.assertEqual("PASS", report["status"], report)
        self.assertEqual(["ABSENT", "ABSENT", "ABSENT"], [report["remote"][name]["status"] for name in ("base", "plus", "catalog")])
        self.assertEqual(["GET", "GET", "GET", "GET"], client.methods)
        self.assertTrue((self.output / "vocab/3.0/hsk1/deploy_preflight/preflight_report.json").is_file())

    def test_partial_error_reports_completed_remote_objects(self):
        class FailingCatalog(deploy.MemoryStorageClient):
            def create_object(self, bucket, object_path, payload, content_type):
                if object_path == deploy.CATALOG_TARGET_PATH:
                    raise RuntimeError("catalog unavailable")
                return super().create_object(bucket, object_path, payload, content_type)

        client = FailingCatalog()
        with self.assertRaises(deploy.PartialDeployError) as caught:
            self._run(client)
        self.assertEqual((deploy.BASE_OBJECT_PATH, deploy.PLUS_OBJECT_PATH), caught.exception.completed_objects)


if __name__ == "__main__":
    unittest.main()
