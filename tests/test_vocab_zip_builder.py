import json
import shutil
import tempfile
import unittest
import zipfile
import sys
import types
from unittest.mock import patch
from pathlib import Path

import pandas as pd
from pydub import AudioSegment

if "pypinyin" not in sys.modules:
    fake_pypinyin = types.ModuleType("pypinyin")

    class _FakeStyle:
        NORMAL = "NORMAL"

    def _fake_lazy_pinyin(text, *args, **kwargs):
        return [str(text)]

    fake_pypinyin.Style = _FakeStyle
    fake_pypinyin.lazy_pinyin = _fake_lazy_pinyin
    sys.modules["pypinyin"] = fake_pypinyin

from pipelines.vocab_zip_builder import BuildValidationError, SourceVocab, _split_plus1_plus2, audio_cache_key, build_hsk30, build_vocab_pack, deployment_allowed, verify_pack, verify_pack_pair, verify_pack_parts
from pipelines import vocab_pipeline


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

    def _write_excel(self, rows=None, sheet="hsk1_30"):
        pd.DataFrame(rows or self.rows).to_excel(self.excel, sheet_name=sheet, index=False)

    def _seed_audio(self, root, level="hsk1", sheet="hsk1_30", pack_version=1, version="3.0", speed="Bình thường", voice="Mặc định", bitrate="32k"):
        audio = root / "vocab" / version / level / "audio_cache"
        audio.mkdir(parents=True, exist_ok=True)
        for row in self.rows:
            # The builder derives a pinyin filename; copy one nonempty test M4A
            # to each expected filename after an initial generated-name lookup.
            item = SourceVocab(row["index"], row["word"], row["meaning_vi"], row["example_zh"], row["example_vi"])
            name = audio_cache_key(item, engine="gTTS", speed=speed, voice=voice, profile="", bitrate=bitrate, audio_mode="zh_vi") + ".m4a"
            (audio / name).write_bytes(f"m4a-{version}-{level}-{row['index']}-{speed}-{voice}-{bitrate}".encode())

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

    def test_hsk79_builds_plus1_plus2_and_reuses_all_cached_audio(self):
        rows = self.rows
        self._write_excel(rows, sheet="hsk7_9_30")
        out = self.temp_dir / "out"
        self._seed_audio(out, level="hsk7_9", sheet="hsk7_9_30")
        with patch.object(vocab_pipeline, "_build_word_audio", side_effect=AssertionError("TTS must not run")):
            result = build_vocab_pack(self.excel, "hsk7_9_30", "hsk7_9", out, version="3.0", generate_missing=False)
        self.assertEqual(0, result["audioGenerated"])
        self.assertIn("plus1", result)
        self.assertIn("plus2", result)
        self.assertNotIn("plus", result)
        self.assertEqual("PASS", verify_pack_parts(result["base"]["zip"], result["plus1"]["zip"], result["plus2"]["zip"], "hsk7_9")["status"])

    def test_plus_split_selects_closest_cumulative_boundary(self):
        items = [SourceVocab(i, f"词{i}", f"nghia {i}", "例", "vi") for i in range(1, 7)]
        audio_root = self.temp_dir / "audio"
        audio_root.mkdir()
        for item, size in zip(items, (10, 11, 9, 10, 11, 9)):
            path = audio_root / (audio_cache_key(item, engine="gTTS", speed="Bình thường", voice="Mặc định", profile="", bitrate="32k", audio_mode="zh_vi") + ".m4a")
            path.write_bytes(b"x" * size)
        left, right = _split_plus1_plus2(items, audio_root, "hsk7_9_30", engine="gTTS", speed="Bình thường", voice="Mặc định", profile="", bitrate="32k", audio_mode="zh_vi")
        self.assertEqual([1, 2, 3], [item.index for item in left])
        self.assertEqual([4, 5, 6], [item.index for item in right])

    def test_audio_unchanged_keeps_alias_and_changed_audio_gets_content_identity(self):
        self._write_excel()
        out = self.temp_dir / "out"
        self._seed_audio(out)
        first = build_hsk30(self.excel, "hsk1_30", "hsk1", out, generate_missing=False)
        first_vocab = first["base"]["vocab"][0]["audio_url"]
        second = build_hsk30(self.excel, "hsk1_30", "hsk1", out, generate_missing=False)
        self.assertEqual(first_vocab, second["base"]["vocab"][0]["audio_url"])
        item = SourceVocab(1, "词1", "nghĩa 1", "例子1", "ví dụ 1")
        audio = out / "vocab" / "3.0" / "hsk1" / "audio_cache"
        name = audio_cache_key(item, engine="gTTS", speed="Bình thường", voice="Mặc định", profile="", bitrate="32k", audio_mode="zh_vi") + ".m4a"
        (audio / name).write_bytes(b"changed-audio")
        third = build_hsk30(self.excel, "hsk1_30", "hsk1", out, generate_missing=False)
        changed_url = third["base"]["vocab"][0]["audio_url"]
        self.assertNotEqual(first_vocab, changed_url)
        self.assertRegex(changed_url, r"^vocab://3\.0/hsk1/1/audio/[0-9a-f]{64}$")

    def test_pack_version_changes_immutable_output_paths_and_identity(self):
        self._write_excel()
        out = self.temp_dir / "out"
        self._seed_audio(out)
        v1 = build_hsk30(self.excel, "hsk1_30", "hsk1", out, generate_missing=False, pack_version=1)
        v2 = build_hsk30(self.excel, "hsk1_30", "hsk1", out, generate_missing=False, pack_version=2)
        self.assertEqual(2, v2["packVersion"])
        self.assertIn("/base/v2/vocab_hsk1_30_base_v2.zip", v2["base"]["zip"])
        self.assertEqual("vocab:3.0:hsk1:base:v2", v2["base"]["manifest"]["packId"])
        self.assertEqual("vocab:3.0:hsk1:plus:v2", v2["plus"]["manifest"]["packId"])
        self.assertEqual([item["id"] for item in v1["base"]["vocab"]], [item["id"] for item in v2["base"]["vocab"]])
        self.assertEqual(v1["base"]["vocab"][0]["audio_url"], v2["base"]["vocab"][0]["audio_url"])
        self.assertNotEqual(v1["base"]["zip"], v2["base"]["zip"])

    def test_voice_change_regenerates_audio_identity_without_overwriting_v1(self):
        self._write_excel()
        out = self.temp_dir / "out"
        self._seed_audio(out, voice="Mặc định")
        v1 = build_hsk30(self.excel, "hsk1_30", "hsk1", out, generate_missing=False, pack_version=1, voice="Mặc định")
        self._seed_audio(out, voice="Nữ")
        v2 = build_hsk30(self.excel, "hsk1_30", "hsk1", out, generate_missing=False, pack_version=2, voice="Nữ")
        self.assertNotEqual(v1["base"]["vocab"][0]["audio_url"], v2["base"]["vocab"][0]["audio_url"])
        self.assertRegex(v2["base"]["vocab"][0]["audio_url"], r"^vocab://3\.0/hsk1/1/audio/[0-9a-f]{64}$")
        self.assertTrue(Path(v1["base"]["zip"]).is_file())
        self.assertTrue(Path(v2["base"]["zip"]).is_file())

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
        self._seed_audio(out, speed="Chậm", voice="Nữ", bitrate="26k")
        audio = out / "vocab" / "3.0" / "hsk1" / "audio_cache"
        next(audio.iterdir()).unlink()
        with self.assertRaises(BuildValidationError):
            build_hsk30(self.excel, "hsk1_30", "hsk1", out, generate_missing=False)

    def test_zero_byte_audio_is_rejected(self):
        self._write_excel()
        out = self.temp_dir / "out"
        self._seed_audio(out)
        audio = out / "vocab" / "3.0" / "hsk1" / "audio_cache"
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
        self._seed_audio(out, speed="Chậm", voice="Nữ", bitrate="26k")
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

    def test_polly_branch_uses_gtts_directly_for_vi_and_on_polly_failure(self):
        project = Path(__file__).resolve().parents[1]
        source = (project / "pipelines" / "vocab_pipeline.py").read_text(encoding="utf-8")
        polly_branch = source.split('if engine_clean == "polly":', 1)[1].split(
            "# Google Cloud (and legacy/unrecognised values):", 1
        )[0]
        self.assertIn("Polly does not support lang={lang_code}; using gTTS", polly_branch)
        self.assertIn("Polly failed for lang={lang_code}: {exc}; falling back to gTTS", polly_branch)
        self.assertNotIn("_tts_segment_google", polly_branch)

    def test_vocab_audio_mode_zh_only_skips_vietnamese_segment(self):
        segment = AudioSegment.silent(duration=100)
        with patch.object(vocab_pipeline, "_tts_segment", return_value=(segment, None)) as tts:
            vocab_pipeline._build_word_audio("爱", "yêu", "gTTS", audio_mode="zh_only")
        self.assertEqual(1, tts.call_count)
        self.assertEqual("zh-CN", tts.call_args.args[1])

    def test_vocab_audio_mode_zh_vi_keeps_two_segments(self):
        segment = AudioSegment.silent(duration=100)
        with patch.object(vocab_pipeline, "_tts_segment", return_value=(segment, None)) as tts:
            vocab_pipeline._build_word_audio("爱", "yêu", "gTTS", audio_mode="zh_vi")
        self.assertEqual(2, tts.call_count)
        self.assertEqual(["zh-CN", "vi"], [call.args[1] for call in tts.call_args_list])

    def test_dialogue_voice_pair_reverse_order_maps_zh_to_female_and_vi_to_male(self):
        segment = AudioSegment.silent(duration=100)
        with patch.object(vocab_pipeline, "_tts_segment", return_value=(segment, None)) as tts:
            vocab_pipeline._build_word_audio(
                "爱",
                "yêu",
                "gTTS",
                voice="Hội thoại 1 câu nữ - 1 câu nam",
                audio_mode="zh_vi",
            )
        self.assertEqual(["Nữ", "Nam"], [call.args[5] for call in tts.call_args_list])

    def test_hsk7_9_identity_is_never_split_into_hsk7_hsk8_hsk9(self):
        self._write_excel(sheet="hsk7_9_30")
        out = self.temp_dir / "out"
        self._seed_audio(out, level="hsk7_9", sheet="hsk7_9_30")
        result = build_hsk30(self.excel, "hsk7_9_30", "hsk7_9", out, generate_missing=False)
        self.assertEqual("vocab:3.0:hsk7_9:base:v1", result["base"]["manifest"]["packId"])
        self.assertEqual("vocab://3.0/hsk7_9/1/audio", result["base"]["vocab"][0]["audio_url"])
        self.assertIn("vocab/3.0/hsk7_9/base/v1/", result["objectPaths"]["base"])
        self.assertFalse((out / "vocab" / "3.0" / "hsk7").exists())
        self.assertFalse((out / "vocab" / "3.0" / "hsk8").exists())
        self.assertFalse((out / "vocab" / "3.0" / "hsk9").exists())

    def test_hsk20_sheet_mapping_paths_and_stable_ids(self):
        self.rows = [
            {"index": index, "word": f"词{index}", "meaning_vi": f"nghĩa {index}", "example_zh": f"例子{index}", "example_vi": f"ví dụ {index}"}
            for index in range(1, 153)
        ]
        self._write_excel(sheet="hsk1_20")
        out = self.temp_dir / "out"
        self._seed_audio(out, sheet="hsk1_20", version="2.0")
        result = build_vocab_pack(self.excel, "hsk1_20", "hsk1", out, version="2.0", generate_missing=False)
        self.assertEqual("2.0", result["version"])
        self.assertEqual("1", result["base"]["vocab"][0]["id"])
        self.assertIn("/vocab/2.0/hsk1/base/v1/", result["base"]["zip"])
        self.assertEqual("vocab/2.0/hsk1/base/v1/vocab_hsk1_20_base_v1.zip", result["objectPaths"]["base"])

    def test_sheet_version_or_level_mismatch_hard_fails(self):
        self._write_excel(sheet="hsk2_20")
        with self.assertRaises(BuildValidationError):
            build_vocab_pack(self.excel, "hsk2_20", "hsk2", self.temp_dir / "out", version="3.0", generate_missing=False)
        with self.assertRaises(BuildValidationError):
            build_vocab_pack(self.excel, "hsk2_20", "hsk3", self.temp_dir / "out", version="2.0", generate_missing=False)

    def test_hsk6_20_mapping_is_canonical(self):
        from pipelines.vocab_zip_builder import resolve_sheet_selection
        self.assertEqual(("2.0", "hsk6"), resolve_sheet_selection("hsk6_20"))

    def test_hsk7_9_alias_sheet_name_maps_to_canonical_level(self):
        from pipelines.vocab_zip_builder import resolve_sheet_selection
        self.assertEqual(("3.0", "hsk7_9"), resolve_sheet_selection("hsk7-9_30"))

    def test_changed_voice_uses_distinct_audio_cache_identity(self):
        item = SourceVocab(1, "词1", "nghĩa 1", "例子1", "ví dụ 1")
        one = audio_cache_key(item, engine="gTTS", speed="Bình thường", voice="Nữ", profile="female", bitrate="32k", audio_mode="zh_vi")
        two = audio_cache_key(item, engine="gTTS", speed="Bình thường", voice="Nam", profile="male", bitrate="32k", audio_mode="zh_vi")
        self.assertNotEqual(one, two)

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
        builder_source = (project / "pipelines" / "vocab_zip_builder.py").read_text(encoding="utf-8")
        self.assertIn("legacy_bitrate_var", app_source)
        self.assertIn("builder_bitrate_var", app_source)
        self.assertIn('collect_vocab_tts_config(cfg_win, legacy_bitrate_var.get(), legacy_audio_mode_var.get())', app_source)
        self.assertIn('collect_vocab_tts_config(builder_win, builder_bitrate_var.get(), builder_audio_mode_var.get())', app_source)
        self.assertIn('"--bitrate", vocab_tts["bitrate"]', app_source)
        self.assertIn('builder_bitrate_var.trace_add("write", refresh_tts_summary)', app_source)
        self.assertIn('textvariable=tts_summary_var', app_source)
        self.assertIn("Compatibility hash:", app_source)
        self.assertIn('text="Help"', app_source)
        self.assertIn("open_hsk30_help", app_source)
        self.assertIn("Cách kích hoạt cấp độ mới trong app", app_source)
        self.assertIn('text="? Publish Help"', app_source)
        self.assertIn("Lên đầu", app_source)
        self.assertIn("Xuống cuối", app_source)
        self.assertIn('state="disabled", command=stage_packs_pending', app_source)
        self.assertIn('state="disabled", command=publish_catalog_pending', app_source)
        self.assertIn('text="Refresh Pointer Status"', app_source)
        self.assertIn('read_verified_pointer_status', app_source)
        self.assertIn('POINTER STATUS UNKNOWN', app_source)
        self.assertIn('POINTER ALREADY INITIALIZED', app_source)
        self.assertIn('publish_gate_var', app_source)
        self.assertIn('publish_controls', app_source)
        self.assertIn('stage_btn.config(state="normal")', app_source)
        self.assertIn('publish_btn.config(state="normal")', app_source)
        self.assertGreaterEqual(app_source.count('text="Chất lượng M4A:"'), 2)
        self.assertNotIn('Chất lượng M4A vocab (HSK 2.0 / 3.0)', app_source)
        self.assertIn('DEFAULT_VOCAB_M4A_BITRATE = "32k"', app_source)
        self.assertIn('return "26k" if', app_source)
        self.assertGreaterEqual(app_source.count('text="Nội dung audio:"'), 2)
        self.assertIn('"Chỉ đọc tiếng Trung"', app_source)
        self.assertIn('"Đọc tiếng Trung + Tiếng Việt"', app_source)
        self.assertIn('"--audio-mode", vocab_tts["audio_mode"]', app_source)
        self.assertIn('"TTS_AUDIO_MODE": snapshot["audio_mode"]', app_source)
        self.assertIn('"audioMode": audio_mode', builder_source)

    def test_google_tts_popup_supports_per_language_gender_slots_and_wider_window(self):
        project = Path(__file__).resolve().parents[1]
        app_source = (project / "app.pyw").read_text(encoding="utf-8")
        self.assertIn('GOOGLE_TTS_SLOT_LABELS = ("Mặc định", "Nam", "Nữ", "Trung tính")', app_source)
        self.assertIn("Mỗi ngôn ngữ có thể lưu riêng giọng Nam/Nữ/Trung tính", app_source)
        self.assertIn('popup_google.geometry("940x650")', app_source)
        self.assertIn("sync_google_voice_selection", app_source)
        self.assertIn("_google_tts_resolve_selection", app_source)
        self.assertIn('_save_google_tts_profiles_to_config()', app_source)

    def test_secret_save_popups_update_shared_config_before_writing_file(self):
        project = Path(__file__).resolve().parents[1]
        app_source = (project / "app.pyw").read_text(encoding="utf-8")
        self.assertIn("global config", app_source.split("def sua_key_don", 1)[1].split("def sua_key_nhom", 1)[0])
        self.assertIn("global config", app_source.split("def sua_key_nhom", 1)[1].split("#==============", 1)[0])


if __name__ == "__main__":
    unittest.main()
