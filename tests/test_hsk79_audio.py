import ast
import copy
import json
import re
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from pydub import AudioSegment
from pydub.generators import Sine

from pipelines import hsk79_audio as audio


class Hsk79AudioTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.legacy_source = audio.shared.BASE_DIR / "testdata/hsk30/hsk79/file-xuat-audio-hsk79.xlsx"
        cls.structure = audio.validate_hsk79_workbook(cls.legacy_source)
        cls.voices = audio.load_hsk79_voice_profile()
        cls.state = {"engine": "Google Cloud TTS", "speech_rate": 1.0,
                     "zh_female_voice": "cmn-CN-Standard-A", "zh_male_voice": "cmn-CN-Standard-C"}
        cls.preview = audio.parse_hsk79_phase1(cls.legacy_source, lang_state=cls.state)

    def test_all_17_exams_and_seven_shared_groups(self):
        self.assertEqual("PASS", self.structure["status"])
        self.assertEqual((17, 680), (self.structure["exam_count"], self.structure["row_count"]))
        self.assertEqual({"group_starts": 119, "internal_blank_line_owners": 35,
                          "missing_number_prefix": 78, "writing_fields_removed": 81, "inline_blanks": 4},
                         self.structure["stats"])
        for exam, items in self.structure["parsed_items"].items():
            with self.subTest(exam=exam):
                self.assertEqual(list(range(1, 41)), [i["question_number"] for i in items])
                self.assertEqual({1: 10, 2: 12, 3: 18}, self.structure["exams"][exam]["part_counts"])
                self.assertEqual(7, sum(i["owns_passage"] for i in items))
                for item in items:
                    self.assertEqual(item["owns_passage"], bool(item["stimulus_segments"]))
                    self.assertEqual(item["question_number"] > 10, item["spoken"])

    def test_new_0211_workbook_parts_and_repeated_shared_passages(self):
        source = audio.shared.BASE_DIR / "testdata/hsk30/hsk79/xuat audi hsk79 0211.xlsx"
        structure = audio.validate_hsk79_workbook(source)
        self.assertEqual("PASS", structure["status"], structure["invalid_rows"][:10])
        self.assertEqual((17, 680), (structure["exam_count"], structure["row_count"]))
        self.assertEqual(425, structure["stats"]["repeated_passage_rows"])
        self.assertEqual(7, structure["stats"]["repeated_passage_variants"])
        for exam, items in structure["parsed_items"].items():
            with self.subTest(exam=exam):
                self.assertEqual(list(range(1, 41)), [item["question_number"] for item in items])
                self.assertEqual({1: 10, 2: 12, 3: 18}, structure["exams"][exam]["part_counts"])
                self.assertEqual(7, sum(item["owns_passage"] for item in items))
        q12 = structure["parsed_items"]["HSK79-01"][11]
        self.assertFalse(q12["stimulus_segments"])
        self.assertEqual("在大棚里，地表看似干燥，深层土壤的含水量却可能接近什么状态？",
                         q12["question_spoken"])
        q33 = structure["parsed_items"]["HSK79-01"][32]
        self.assertFalse(q33["stimulus_segments"])
        self.assertEqual("教练为什么要体谅发生争论的队员？", q33["question_spoken"])

    def test_latest_workbook_is_valid_and_default_source(self):
        source = audio.shared.BASE_DIR / "testdata/hsk30/hsk79/xuat audio hsk79 02112016.xlsx"
        structure = audio.validate_hsk79_workbook(source)
        self.assertEqual("PASS", structure["status"], structure["invalid_rows"][:10])
        self.assertEqual((17, 680), (structure["exam_count"], structure["row_count"]))
        self.assertEqual(0, len(structure["passage_copy_variants"]))
        self.assertEqual(source.resolve(), audio.HSK30_HSK79_SOURCE.resolve())
        self.assertEqual(119, structure["stats"]["group_starts"])

    def test_batch_builds_every_exam_even_when_old_outputs_exist(self):
        with TemporaryDirectory() as temp:
            root = Path(temp)
            source = root / "source.xlsx"
            source.write_bytes(b"validated source snapshot")
            output_dir = root / "out"
            output_dir.mkdir()
            old_output = output_dir / "hsk79-001.m4a"
            old_output.write_bytes(b"old audio")
            exams = {f"HSK79-{number:02d}": {} for number in range(1, 18)}

            def fake_build(preview, output, **kwargs):
                Path(output).write_bytes(f"new-{preview['detected_exam']}".encode())
                return {"exam": preview["detected_exam"], "output_file": str(Path(output).resolve()),
                        "actual_duration_ms": 1000, "file_size_bytes": Path(output).stat().st_size,
                        "unique_tts_requests": 0, "reused_segments": 2, "status": "INFO",
                        "asset_sha256": {key: audio._digest(path) for key, path in audio.HSK79_ASSETS.items()},
                        "speech_rate": 1.0, "tts_engine": "Google Cloud TTS",
                        "tts_voices": {"male": "cmn-CN-Standard-C", "female": "cmn-CN-Standard-A"},
                        "timeline_checks": {"exact_order": True}}

            with patch.object(audio, "validate_hsk79_workbook", return_value={
                    "status": "PASS", "invalid_rows": [], "exams": exams}), \
                    patch.object(audio, "validate_hsk79_assets", return_value={"ok": True}), \
                    patch.object(audio, "parse_hsk79_phase1", side_effect=lambda _source, exam_code, **_kwargs: {
                        "detected_exam": exam_code, "workbook": str(source), "sheet": "Sheet1"}), \
                    patch.object(audio.shared, "_probe_audio_duration_ms", return_value=1000), \
                    patch.object(audio, "build_hsk79_exam", side_effect=fake_build) as build:
                results = audio.build_hsk79_batch(source, output_dir, lang_state=self.state)
                resumed = audio.build_hsk79_batch(source, output_dir, lang_state=self.state)

            self.assertEqual(17, len(results))
            self.assertEqual(17, build.call_count)
            self.assertTrue(all(item["status"] == "INFO" for item in results))
            self.assertTrue(all(item["status"] == "SKIP_VERIFIED" for item in resumed))
            self.assertEqual(17, build.call_count)
            self.assertEqual("new-HSK79-01", old_output.read_text())
            self.assertEqual("hsk79-017.m4a", Path(results[-1]["output_file"]).name)

    def test_hsk79_part_labels_accept_chinese_and_legacy_vietnamese(self):
        for label, expected in (("第一部分", 1), ("第二部分", 2), ("第三部分", 3),
                                ("Phần 1", 1), (" Phần 2 ", 2), ("Phần 3", 3)):
            with self.subTest(label=label):
                self.assertEqual(expected, audio.parse_hsk79_part(label))
        self.assertIsNone(audio.parse_hsk79_part("第四部分"))

    def test_final_block_preserves_internal_blank_lines_and_questions(self):
        for items in self.structure["parsed_items"].values():
            for item in items:
                if not item["owns_passage"]:
                    continue
                source = item["source_text"]
                passage = item["stimulus_source"]
                self.assertEqual(source[:source.rfind("\n\n")].strip(), passage)
                self.assertTrue(source.startswith(passage))
        source = "主持人：中途的问题？\n专家：第一段。\n\n主持人：另一个问题？\n专家：最后一句。\n\n11.真正的考试问题？"
        passage, question, marked = audio.split_hsk79_owner(source, 11, 2)
        self.assertIn("\n\n主持人：另一个问题？", passage)
        self.assertTrue(passage.endswith("专家：最后一句。"))
        self.assertEqual("真正的考试问题？", question)
        self.assertTrue(marked)

    def test_multiline_exam_question_keeps_cloze_continuation(self):
        source = "主持人：前文内容。\n专家： passage ending。\n\n20.关于社区医疗，说话人最想强调什么？\n志愿者负责记录，专业机构承担______。"
        passage, question, marked = audio.split_hsk79_owner(source, 20, 2)
        self.assertTrue(marked)
        self.assertTrue(passage.endswith("passage ending。"))
        self.assertEqual("关于社区医疗，说话人最想强调什么？ 志愿者负责记录，专业机构承担______。", question)
        cleaned, parts, _ = audio.hsk79_question_parts(question)
        self.assertEqual(question, cleaned)
        self.assertEqual(1, sum(part["type"] == "blank" for part in parts))
        self.assertIn("志愿者负责记录", "".join(part.get("text", "") for part in parts))

        with self.assertRaisesRegex(ValueError, "continuation lines must contain answer blanks"):
            audio.normalize_hsk79_question_layout("考试问题？\n无标记的额外内容。")

    def test_missing_numeric_prefix_is_not_a_missing_question(self):
        for exam in ("HSK79-10", "HSK79-12"):
            items = self.structure["parsed_items"][exam]
            self.assertEqual(40, len(items))
            self.assertEqual(30, sum(i["spoken"] for i in items))
            self.assertEqual("十一。", items[10]["question_number_spoken"])
        with self.assertRaisesRegex(ValueError, "differs"):
            audio._strip_number("12.错误的编号？", 11)

    def test_inline_blanks_keep_both_sides_and_never_speak_underscores(self):
        for number in (16, 19, 34, 37):
            item = self.structure["parsed_items"]["HSK79-06"][number - 1]
            parts = item["question_parts"]
            self.assertEqual(1, sum(p["type"] == "blank" for p in parts))
            text_parts = [p["text"] for p in parts if p["type"] == "speech"]
            self.assertTrue(all("_" not in text for text in text_parts))
            self.assertEqual(re.sub(r"_{2,}", "", item["question_spoken"]), "".join(text_parts))
        text, parts, writing = audio.hsk79_question_parts("14. 需要什么？\n答：____________")
        self.assertEqual("14. 需要什么？", text)
        self.assertTrue(writing)
        self.assertEqual(1, len(parts))

    def test_writing_lines_are_not_inline_slots(self):
        text, parts, removed = audio.hsk79_question_parts("事故原因借助什么调查？（简答）________")
        self.assertEqual("事故原因借助什么调查？", text)
        self.assertTrue(removed)
        self.assertFalse(any(p["type"] == "blank" for p in parts))

    def test_dynamic_speaker_roles_and_unknown_role_validation(self):
        item = self.structure["parsed_items"]["HSK79-09"][10]
        roles = [s["speaker_token"] for s in item["stimulus_segments"]]
        self.assertIn("记者", roles)
        self.assertIn("导演", roles)
        self.assertIn("主持人", roles)
        self.assertEqual(20, len(roles))
        for segment in item["stimulus_segments"]:
            self.assertFalse(audio.ROLE_LINE.match(segment["text"]))
        with self.assertRaisesRegex(ValueError, "Unmapped speaker"):
            audio.parse_hsk79_speakers("新嘉宾：未配置角色。", "11–16", self.voices)
        with self.assertRaisesRegex(ValueError, "Empty speaker"):
            audio.parse_hsk79_speakers("主持人：", "11–16", self.voices)
        with self.assertRaisesRegex(ValueError, "Unmapped speaker"):
            audio.parse_hsk79_speakers("主持人：先说一句。\n顾问：回答。", "11–16", self.voices)
        mapped = audio.parse_hsk79_speakers("卖方：先介绍。\n买方：再回应。", "17–22", self.voices)
        self.assertEqual(["NỮ", "NAM"], [segment["speaker_label"] for segment in mapped])

    def test_narrative_colons_and_unlabelled_paragraph_remain_narration(self):
        item = self.structure["parsed_items"]["HSK79-12"][10]
        last = item["stimulus_segments"][-1]
        self.assertEqual("NARRATION", last["speaker_token"])
        self.assertTrue(last["source_text"].startswith("现场走访并不轻松："))
        self.assertEqual(self.voices["groups"]["11–16"]["narration"], last["speaker_label"])
        passage = "调查的结果如下：一共有三项。"
        parsed = audio.parse_hsk79_speakers(passage, "23–28", self.voices)
        self.assertEqual(passage, parsed[0]["text"])
        self.assertEqual("NARRATION", parsed[0]["speaker_token"])
        short_heading = audio.parse_hsk79_speakers("调查发现：共有三项。", "23–28", self.voices)
        self.assertEqual("调查发现：共有三项。", short_heading[0]["text"])

    def test_chunking_preserves_exact_content_and_request_limit(self):
        source = "一句完整的话。\n" * 80
        chunks = audio.chunk_hsk79_speech(source, max_bytes=120)
        self.assertGreater(len(chunks), 1)
        self.assertEqual(source, "".join(chunks))
        self.assertTrue(all(len(c.encode("utf-8")) <= 120 for c in chunks))
        with self.assertRaisesRegex(ValueError, "exceeds"):
            audio.chunk_hsk79_speech("字" * 100, max_bytes=20)

    def test_every_passage_character_is_preserved_except_role_metadata(self):
        profile = self.voices["groups"]
        for items in self.structure["parsed_items"].values():
            for item in items:
                if not item["owns_passage"]:
                    continue
                roles = {"男", "女", *profile[item["group"]].get("roles", {})}
                expected = []
                for line in item["stimulus_source"].splitlines():
                    match = audio.ROLE_LINE.match(line)
                    expected.append(match[2] if match and match[1] in roles else line)
                actual = "".join(s["source_text"] for s in item["stimulus_segments"])
                self.assertEqual(re.sub(r"\s+", "", "".join(expected)), re.sub(r"\s+", "", actual))
                if all(s["speaker_token"] == "NARRATION" for s in item["stimulus_segments"]):
                    self.assertEqual(1, len(item["stimulus_segments"]))

    def test_errors_identify_exam_question_source_row(self):
        rows = audio.shared.read_xlsx_rows(audio.HSK30_HSK79_SOURCE)
        for index, text in ((11, "不能拆分的段落。11.问题？"), (12, "新段落。\n新问题？"),
                            (11, "未知角色：内容。\n\n11.问题？")):
            damaged = copy.deepcopy(rows)
            damaged[index].cells["G"] = text
            with patch.object(audio.shared, "read_xlsx_rows", return_value=damaged):
                result = audio.validate_hsk79_workbook(audio.HSK30_HSK79_SOURCE)
            self.assertEqual("FAIL", result["status"])
            self.assertTrue(any(f"source row {index + 1}" in error and "HSK79-01" in error for error in result["invalid_rows"]))

    def test_assets_names_hashes_and_preflight_fail_without_tts(self):
        self.assertEqual(11, len(audio.HSK79_ASSETS))
        self.assertTrue(audio.validate_hsk79_assets()["ok"])
        self.assertEqual("hsk79-001.m4a", audio.hsk79_output_filename("HSK79-01"))
        self.assertEqual("hsk79-017.m4a", audio.hsk79_output_filename("HSK79-17"))
        with TemporaryDirectory() as tmp:
            assets = audio.validate_hsk79_assets(tmp)
            self.assertIn("hsk79_cue_q35_40.m4a", assets["missing"])
            with patch.object(audio, "validate_hsk79_assets", return_value=assets), \
                 patch.object(audio.shared, "_synthesize_tts_segment", side_effect=AssertionError("No TTS")):
                with self.assertRaisesRegex(FileNotFoundError, "hsk79_cue_q35_40"):
                    audio.build_hsk79_golden(self.preview, Path(tmp) / "output.m4a", lang_state=self.state)

    def test_full_assembly_and_reuse_without_part1_statements_or_common_assets(self):
        raw = AudioSegment.silent(350) + Sine(440).to_audio_segment(duration=170) + AudioSegment.silent(450)
        fixed = Sine(660).to_audio_segment(duration=33)
        calls = []

        def fake_tts(text, **kwargs):
            calls.append((text, kwargs["voice_name"]))
            return raw

        def export(segment, path, options):
            self.assertEqual(("ipod", "aac", "32k"), tuple(options[k] for k in ("format", "codec", "bitrate")))
            path.write_bytes(b"fixture")
            return len(segment)

        with TemporaryDirectory() as tmp, \
             patch.object(audio.shared, "_make_tts_cache_dir", return_value=Path(tmp)), \
             patch.object(audio.shared, "_load_audio_segment", return_value=fixed) as load, \
             patch.object(audio.shared, "_export_audio_atomically", side_effect=export), \
             patch.object(audio.shared, "_trim_tts_edge_silence", wraps=audio.shared._trim_tts_edge_silence) as trim:
            result = audio.build_hsk79_golden(self.preview, Path(tmp) / "one.m4a", lang_state=self.state, tts_provider=fake_tts)
            count = len(calls)
            self.assertEqual(count, trim.call_count)
            again = audio.build_hsk79_golden(self.preview, Path(tmp) / "two.m4a", lang_state=self.state, tts_provider=fake_tts)
            self.assertEqual(count, len(calls))
            self.assertEqual(0, again["unique_tts_requests"])
            self.assertEqual(count, trim.call_count)
            self.assertEqual(set(audio.HSK79_ASSETS.values()), {c.args[0] for c in load.call_args_list})
        self.assertTrue(all(audio.audit_hsk79_timeline(result).values()))
        events = result["events"]
        self.assertEqual(30, sum(e["type"] == "answer_pause" for e in events))
        self.assertEqual([74539, 74388], [e["end_ms"] - e["start_ms"] for e in events if e["type"] == "work_pause"])
        self.assertEqual(7, len(result["passages"]))
        self.assertEqual(11, sum(e["type"] == "fixed_asset" for e in events))
        self.assertTrue(all("_" not in text for text, voice in calls))
        for item in self.preview["preview_items"][:10]:
            self.assertNotIn(item["question_spoken"], [text for text, voice in calls])
            self.assertNotIn(item["question_number_spoken"], [text for text, voice in calls])
        for previous, following in zip(events, events[1:]):
            self.assertEqual(previous["end_ms"], following["start_ms"])
        damaged = copy.deepcopy(result)
        damaged["timeline"].insert(0, "music")
        with self.assertRaisesRegex(ValueError, "timeline FAIL"):
            audio.audit_hsk79_timeline(damaged)

    def test_golden_only_and_ui_routes(self):
        self.assertTrue(audio.HSK79_BATCH_APPROVED)
        damaged = dict(self.preview, detected_exam="HSK79-02")
        with self.assertRaisesRegex(ValueError, "golden-only"):
            audio.build_hsk79_golden(damaged, "never.m4a", lang_state=self.state)
        tree = ast.parse((audio.shared.BASE_DIR / "app.pyw").read_text())
        functions = {node.name: node for node in ast.walk(tree) if isinstance(node, ast.FunctionDef)}
        for name, target in (("_hsk30_default_source", "HSK30_HSK79_SOURCE"),
                             ("_hsk30_parser", "parse_hsk79_phase1"), ("_hsk30_builder", "build_hsk79_golden")):
            self.assertIn(target, ast.unparse(functions[name]))
        for name in ("export_hsk30_audio_batch", "refresh_level_labels"):
            self.assertIn("HSK79_BATCH_APPROVED", ast.unparse(functions[name]))
        self.assertIn("build_hsk79_batch", ast.unparse(functions["export_hsk30_audio_batch"]))

    def test_ui_and_cli_share_google_voice_preflight(self):
        for changed in ({"engine": "gTTS"}, {"zh_male_voice": "cmn-CN-Standard-A"},
                        {"zh_female_voice": ""}):
            state = self.state | changed
            with patch.object(audio.shared, "_synthesize_tts_segment", side_effect=AssertionError("No TTS")):
                with self.assertRaisesRegex(ValueError, "Standard-C and female Standard-A"):
                    audio.build_hsk79_golden(self.preview, "never.m4a", lang_state=state)


if __name__ == "__main__":
    unittest.main()
