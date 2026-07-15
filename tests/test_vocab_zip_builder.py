import json
import shutil
import tempfile
import unittest
import zipfile
import sys
import types
from pathlib import Path

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

from pipelines.vocab_zip_builder import BuildValidationError, build_hsk30, deployment_allowed, verify_pack, verify_pack_pair


class VocabZipBuilderTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = Path(tempfile.mkdtemp(prefix="vocab-zip-test-"))
        self.excel = self.temp_dir / "source.xlsx"
        self.rows = [
            {"index": index, "word": f"词{index}", "meaning_vi": f"nghĩa {index}", "example_zh": f"例子{index}", "example_vi": f"ví dụ {index}"}
            for index in range(1, 53)
        ]

    def tearDown(self):
        shutil.rmtree(self.temp_dir)

    def _write_excel(self, rows=None):
        pd.DataFrame(rows or self.rows).to_excel(self.excel, sheet_name="hsk1_30", index=False)

    def _seed_audio(self, root, level="hsk1", sheet="hsk1_30"):
        audio = root / "vocab" / "3.0" / level / "source_audio"
        audio.mkdir(parents=True, exist_ok=True)
        for row in self.rows:
            # The builder derives a pinyin filename; copy one nonempty test M4A
            # to each expected filename after an initial generated-name lookup.
            from pipelines.vocab_zip_builder import SourceVocab, audio_filename
            item = SourceVocab(row["index"], row["word"], row["meaning_vi"], row["example_zh"], row["example_vi"])
            (audio / audio_filename(sheet, item)).write_bytes(f"m4a-{row['index']}".encode())

    def _build(self, out):
        self._write_excel()
        self._seed_audio(out)
        return build_hsk30(self.excel, "hsk1_30", "hsk1", out, generate_missing=False)

    def _replace_manifest(self, zip_path, mutate):
        zip_path = Path(zip_path)
        replacement = zip_path.with_suffix(".replacement.zip")
        with zipfile.ZipFile(zip_path, "r") as source, zipfile.ZipFile(replacement, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as target:
            for info in source.infolist():
                data = source.read(info.filename)
                if info.filename == "manifest.json":
                    manifest = json.loads(data)
                    mutate(manifest)
                    data = (json.dumps(manifest, ensure_ascii=False, separators=(",", ":")) + "\n").encode("utf-8")
                target.writestr(info, data, compress_type=info.compress_type, compresslevel=9)
        replacement.replace(zip_path)

    def test_valid_excel_creates_correct_base_and_plus(self):
        result = self._build(self.temp_dir / "out")
        self.assertEqual("PASS", result["status"])
        self.assertEqual(50, result["base"]["manifest"]["vocabCount"])
        self.assertEqual(2, result["plus"]["manifest"]["vocabCount"])
        self.assertFalse(deployment_allowed(result))
        self.assertTrue(Path(result["base"]["zip"]).is_file())

    def test_index_gap_is_rejected(self):
        rows = list(self.rows)
        rows[4]["index"] = 8
        self._write_excel(rows)
        with self.assertRaises(BuildValidationError):
            build_hsk30(self.excel, "hsk1_30", "hsk1", self.temp_dir / "out", generate_missing=False)

    def test_duplicate_index_is_rejected(self):
        rows = list(self.rows)
        rows[1]["index"] = 1
        self._write_excel(rows)
        with self.assertRaises(BuildValidationError):
            build_hsk30(self.excel, "hsk1_30", "hsk1", self.temp_dir / "out", generate_missing=False)

    def test_missing_and_empty_audio_are_rejected(self):
        self._write_excel()
        out = self.temp_dir / "out"
        self._seed_audio(out)
        audio = out / "vocab" / "3.0" / "hsk1" / "source_audio"
        next(audio.iterdir()).unlink()
        with self.assertRaises(BuildValidationError):
            build_hsk30(self.excel, "hsk1_30", "hsk1", out, generate_missing=False)

    def test_zero_byte_audio_is_rejected(self):
        self._write_excel()
        out = self.temp_dir / "out"
        self._seed_audio(out)
        audio = out / "vocab" / "3.0" / "hsk1" / "source_audio"
        next(audio.iterdir()).write_bytes(b"")
        with self.assertRaises(BuildValidationError):
            build_hsk30(self.excel, "hsk1_30", "hsk1", out, generate_missing=False)

    def test_deterministic_zip_and_reopen_verify(self):
        one = self._build(self.temp_dir / "one")
        two = self._build(self.temp_dir / "two")
        self.assertEqual(one["base"]["sha256"], two["base"]["sha256"])
        self.assertEqual(one["plus"]["sha256"], two["plus"]["sha256"])
        self.assertEqual("PASS", verify_pack(one["base"]["zip"], "hsk1", "base")["status"])
        self.assertEqual("PASS", verify_pack_pair(one["base"]["zip"], one["plus"]["zip"], "hsk1")["status"])

    def test_audio_url_manifest_mismatch_is_rejected(self):
        result = self._build(self.temp_dir / "out")
        self._replace_manifest(result["base"]["zip"], lambda manifest: manifest["resources"][1].update({"canonicalSource": "vocab://3.0/hsk1/not-the-id/audio"}))
        with self.assertRaises(BuildValidationError):
            verify_pack(result["base"]["zip"], "hsk1", "base")

    def test_resource_sha_mismatch_is_rejected(self):
        result = self._build(self.temp_dir / "out")
        self._replace_manifest(result["base"]["zip"], lambda manifest: manifest["resources"][1].update({"sha256": "0" * 64}))
        with self.assertRaises(BuildValidationError):
            verify_pack(result["base"]["zip"], "hsk1", "base")

    def test_plus_base_compatibility_mismatch_is_rejected(self):
        result = self._build(self.temp_dir / "out")
        self._replace_manifest(result["plus"]["zip"], lambda manifest: manifest.update({"baseOrderedVocabIdsSha256": "0" * 64}))
        with self.assertRaises(BuildValidationError):
            verify_pack_pair(result["base"]["zip"], result["plus"]["zip"], "hsk1")

    def test_deploy_gate_is_disabled_before_or_after_local_pass(self):
        self.assertFalse(deployment_allowed(None))
        self.assertFalse(deployment_allowed({"status": "PASS"}))
        self.assertFalse(deployment_allowed(self._build(self.temp_dir / "out")))

    def test_hsk20_legacy_pipeline_and_importer_are_not_modified_by_build(self):
        project = Path(__file__).resolve().parents[1]
        legacy_files = [project / "scripts" / "import_hsk1_to_supabase.js"]
        before = [path.read_bytes() for path in legacy_files]
        self._build(self.temp_dir / "out")
        self.assertEqual(before, [path.read_bytes() for path in legacy_files])

    def test_hsk30_requires_confirmed_vi_zh_and_records_selected_m4a_quality(self):
        self._write_excel()
        out = self.temp_dir / "out"
        self._seed_audio(out)
        with self.assertRaises(BuildValidationError):
            build_hsk30(self.excel, "hsk1_30", "hsk1", out, generate_missing=False, config_confirmed=False)
        with self.assertRaises(BuildValidationError):
            build_hsk30(self.excel, "hsk1_30", "hsk1", out, generate_missing=False, languages=("zh",))
        result = build_hsk30(
            self.excel,
            "hsk1_30",
            "hsk1",
            out,
            generate_missing=False,
            speed="Chậm",
            voice="Nữ",
            bitrate="26k",
            languages=("vi", "zh"),
        )
        self.assertEqual("26k", result["ttsConfig"]["m4a"]["bitrate"])
        self.assertEqual(["vi", "zh"], result["ttsConfig"]["languages"])

    def test_vocab_pipeline_has_explicit_gtts_path_and_no_silent_tts_fallback(self):
        project = Path(__file__).resolve().parents[1]
        source = (project / "pipelines" / "vocab_pipeline.py").read_text(encoding="utf-8")
        self.assertIn('if engine_clean == "gtts":', source)
        self.assertIn("gTTS is an explicit user choice. Do not probe Google Cloud first.", source)
        self.assertIn("raise TTSGenerationError", source)
        self.assertIn('SUPPORTED_M4A_BITRATES = {"26k", "32k"}', source)

    def test_hsk7_9_identity_is_never_split_into_hsk7_hsk8_hsk9(self):
        self._write_excel()
        out = self.temp_dir / "out"
        self._seed_audio(out, level="hsk7_9")
        result = build_hsk30(self.excel, "hsk1_30", "hsk7_9", out, generate_missing=False)
        self.assertEqual("vocab:3.0:hsk7_9:base:v1", result["base"]["manifest"]["packId"])
        self.assertEqual("vocab://3.0/hsk7_9/1/audio", result["base"]["vocab"][0]["audio_url"])
        self.assertIn("vocab/3.0/hsk7_9/base/v1/", result["objectPaths"]["base"])
        self.assertFalse((out / "vocab" / "3.0" / "hsk7").exists())
        self.assertFalse((out / "vocab" / "3.0" / "hsk8").exists())
        self.assertFalse((out / "vocab" / "3.0" / "hsk9").exists())

    def test_ui_has_one_hsk7_9_option_and_builder_never_calls_legacy_importer(self):
        project = Path(__file__).resolve().parents[1]
        app_source = (project / "app.pyw").read_text(encoding="utf-8")
        builder_source = (project / "pipelines" / "vocab_zip_builder.py").read_text(encoding="utf-8")
        self.assertEqual(1, app_source.count('"HSK 7–9": "hsk7_9"'))
        self.assertNotIn('"HSK 7": "hsk7"', app_source)
        self.assertNotIn("import_hsk1_to_supabase", builder_source)

    def test_both_vocab_workflow_windows_have_direct_m4a_quality_selectors(self):
        project = Path(__file__).resolve().parents[1]
        app_source = (project / "app.pyw").read_text(encoding="utf-8")
        self.assertIn("legacy_bitrate_var", app_source)
        self.assertIn("builder_bitrate_var", app_source)
        self.assertIn('collect_vocab_tts_config(cfg_win, legacy_bitrate_var.get())', app_source)
        self.assertIn('collect_vocab_tts_config(builder_win, builder_bitrate_var.get())', app_source)
        self.assertIn('"--bitrate", vocab_tts["bitrate"]', app_source)
        self.assertIn('builder_bitrate_var.trace_add("write", refresh_tts_summary)', app_source)
        self.assertIn('textvariable=tts_summary_var', app_source)
        self.assertIn("Compatibility hash:", app_source)
        self.assertIn('state="disabled", command=stage_packs_pending', app_source)
        self.assertIn('state="disabled", command=publish_catalog_pending', app_source)
        self.assertIn('stage_btn.config(state="normal")', app_source)
        self.assertIn('publish_btn.config(state="normal")', app_source)
        self.assertGreaterEqual(app_source.count('text="Chất lượng M4A:"'), 2)
        self.assertNotIn('Chất lượng M4A vocab (HSK 2.0 / 3.0)', app_source)
        self.assertIn('DEFAULT_VOCAB_M4A_BITRATE = "32k"', app_source)
        self.assertIn('return "26k" if', app_source)


if __name__ == "__main__":
    unittest.main()
