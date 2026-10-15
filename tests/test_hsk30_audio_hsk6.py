import ast
import copy
import json
import unittest
import unicodedata
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from pydub import AudioSegment
from pydub.generators import Sine
from google.api_core.exceptions import InvalidArgument

from pipelines import hsk30_audio as audio


class Hsk6AudioTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.state = {"engine": "Google Cloud TTS", "speech_rate": 1.00,
                     "zh_female_voice": "cmn-CN-Standard-A", "zh_male_voice": "cmn-CN-Standard-C",
                     "zh_narrator_voice": "cmn-CN-Standard-C", "hsk_speed_profile": "HSK6"}
        cls.structure = audio.validate_hsk6_workbook(audio.HSK30_HSK6_SOURCE)
        cls.preview = audio.parse_hsk6_phase1(audio.HSK30_HSK6_SOURCE, lang_state=cls.state)

    def test_all_18_exams_and_162_group_owners_split(self):
        self.assertEqual("PASS", self.structure["status"])
        self.assertEqual((18, 720, 162), tuple(self.structure[key] for key in
                         ("exam_count", "row_count", "first_group_rows_split")))
        self.assertEqual("02 xuất mp3 hsk6 30.xlsx", unicodedata.normalize("NFC", audio.HSK30_HSK6_SOURCE.name))
        self.assertEqual(414, self.structure["later_question_rows"])
        self.assertEqual(44, len(self.structure["no_space_first_group_rows"]))
        self.assertEqual([10, 13, 16, 19, 23, 27], self.structure["no_space_first_group_rows"][:6])
        self.assertEqual(54, self.structure["interview_groups"])
        self.assertEqual({10: 54}, self.structure["interview_turn_counts"])
        self.assertEqual({"NAM": 270, "NỮ": 270}, self.structure["interview_speaker_counts"])
        self.assertEqual({"主持人": 270, "嘉宾": 270}, self.structure["interview_role_counts"])
        for exam, items in self.structure["parsed_items"].items():
            with self.subTest(exam=exam):
                self.assertEqual(list(range(1, 41)), [item["question_number"] for item in items])
                self.assertEqual({1: 8, 2: 20, 3: 12}, self.structure["exams"][exam]["part_counts"])
                self.assertEqual(9, len(self.structure["exams"][exam]["shared_groups"]))
                for item in items[8:]:
                    first = audio._hsk4_group_range(item["group"])[0]
                    self.assertEqual(item["question_number"] == first, bool(item["stimulus_segments"]))
                    self.assertTrue(item["question_source"].endswith("？"))
                    if item["stimulus_segments"]:
                        self.assertTrue(item["stimulus_source"].endswith("。"))
                        self.assertEqual(item["source_text"].strip(),
                                         item["stimulus_source"] + item["source_text"][len(item["stimulus_source"]):])
                        self.assertEqual(item["source_text"].rsplit("。", 1)[1].strip(), item["question_source"])

    def test_part1_is_eight_independent_complete_narrations(self):
        for number, item in enumerate(self.preview["preview_items"][:8], 1):
            self.assertFalse(item["is_shared"])
            self.assertEqual("1–8", item["group"])
            self.assertIsNone(item["question_segment"])
            self.assertEqual(item["source_text"], item["stimulus_source"])
            self.assertEqual("NAM" if number % 2 else "NỮ", item["stimulus_segments"][0]["speaker_label"])
            self.assertEqual([], item["repeat2"])

    def test_split_without_space_and_keep_internal_natural_questions(self):
        for whitespace in ("", " ", "\n", "　"):
            passage = "导演问：“故事怎么调整？”学生说要改结尾。最后一句不能丢。"
            actual = audio._hsk6_split_content(passage + whitespace + "老师认为什么重要？", owns_passage=True)
            self.assertEqual((passage, "老师认为什么重要？"), actual)
        self.assertEqual(("", "作者为什么这样说？"),
                         audio._hsk6_split_content("作者为什么这样说？", owns_passage=False))

    def test_uncertain_split_or_new_later_stimulus_fails(self):
        for source in ("只有一个问题？", "。问题？", "段落。？", "段落。问题。", "段落。问题？另一问？"):
            with self.subTest(source=source), self.assertRaises(ValueError):
                audio._hsk6_split_content(source, owns_passage=True)
        for source in ("新段落。问题？", "？", "两个问题？还有问题？"):
            with self.assertRaises(ValueError):
                audio._hsk6_split_content(source, owns_passage=False)

    def test_validation_errors_include_source_row(self):
        original = audio.read_xlsx_rows(audio.HSK30_HSK6_SOURCE)
        for index, column, value in ((9, "G", "没有段落可以拆分？"), (10, "G", "新段落。问题？"),
                                     (9, "E", "9–12"), (10, "F", "九"), (9, "G", "")):
            rows = copy.deepcopy(original)
            rows[index].cells[column] = value
            with patch.object(audio, "read_xlsx_rows", return_value=rows):
                result = audio.validate_hsk6_workbook(audio.HSK30_HSK6_SOURCE)
                self.assertEqual("FAIL", result["status"])
                if column != "F":
                    self.assertTrue(any(f"source row {index + 1}" in error for error in result["invalid_rows"]))
                with self.assertRaises(ValueError):
                    audio.parse_hsk6_phase1(audio.HSK30_HSK6_SOURCE)

    def test_group_voices_and_question_number_from_source(self):
        self.assertEqual({
            "29–32": {"主持人": "NAM", "嘉宾": "NỮ"},
            "33–36": {"主持人": "NỮ", "嘉宾": "NAM"},
            "37–40": {"主持人": "NAM", "嘉宾": "NỮ"},
        }, audio.HSK6_AUDIO_PROFILE["interview_role_voices_by_group"])
        for item in self.preview["preview_items"]:
            self.assertEqual(audio._spoken_question_number(item["source_number"]), item["question_number_spoken"])
            self.assertEqual("NỮ", item["number_speaker_label"])
            if item["is_shared"]:
                self.assertEqual("NỮ", item["question_segment"]["speaker_label"])
                if item["stimulus_segments"] and not item["is_interview"]:
                    self.assertEqual(audio.HSK6_AUDIO_PROFILE["narration_group_voices"][item["group"]],
                                     item["stimulus_segments"][0]["speaker_label"])

    def test_assets_output_names_and_missing_asset_fail_before_tts(self):
        self.assertEqual(13, len(audio.HSK30_HSK6_ASSETS))
        self.assertTrue(audio.validate_hsk30_assets(level="HSK6")["ok"])
        self.assertEqual("hsk6-30-001.m4a", audio.phase1_output_filename("HSK6-30-01"))
        self.assertEqual("hsk6-30-018.m4a", audio.phase1_output_filename("HSK6-30-18"))
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            status = audio.validate_hsk30_assets(root, "hsk6")
            for relative in status["missing"]:
                target = root / relative
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(b"fixture")
            for key in audio.HSK30_HSK6_ASSETS:
                target = root / "hsk6" / f"hsk6_{key}.m4a"
                target.unlink()
                status = audio.validate_hsk30_assets(root, "hsk6")
                self.assertEqual([f"hsk6/hsk6_{key}.m4a"], status["missing"])
                with patch.object(audio, "validate_hsk30_assets", return_value=status), \
                     patch.object(audio, "_synthesize_tts_segment", side_effect=AssertionError("no TTS")):
                    with self.assertRaisesRegex(FileNotFoundError, f"hsk6_{key}"):
                        audio.build_hsk6_phase1b(self.preview, root / "output.m4a", lang_state=self.state)
                target.write_bytes(b"fixture")

    def test_timing_accounts_for_two_safety_edges(self):
        profile = audio.HSK6_AUDIO_PROFILE
        for part, measured in profile["audible_answer_pause_by_part"].items():
            self.assertEqual(measured, profile["answer_pause_by_part"][part] + 2 * audio.HSK3_EDGE_SAFETY_PAD_MS)
        self.assertEqual(0, profile["part_start_gap_ms"])
        self.assertEqual(0, profile["cue_to_stimulus_ms"])
        self.assertEqual(1, profile["repetitions"])
        self.assertTrue(audio.HSK6_BATCH_APPROVED)

    def test_build_exact_order_and_normalized_cache_reuse(self):
        calls = []
        tone = Sine(440).to_audio_segment(duration=120)
        raw = AudioSegment.silent(400) + tone + AudioSegment.silent(240) + tone + AudioSegment.silent(600)
        fixed = Sine(660).to_audio_segment(duration=37)

        def fake_tts(text, **kwargs):
            calls.append((text, kwargs["voice_name"]))
            return raw

        def fake_export(segment, path, options):
            path.write_bytes(b"fixture")
            return len(segment)

        with TemporaryDirectory() as tmp, patch.object(audio, "_make_tts_cache_dir", return_value=Path(tmp) / "cache"), \
             patch.object(audio, "_load_audio_segment", return_value=fixed), \
             patch.object(audio, "_export_audio_atomically", side_effect=fake_export), \
             patch.object(audio, "_trim_tts_edge_silence", wraps=audio._trim_tts_edge_silence) as trim:
            result = audio.build_hsk6_phase1b(self.preview, Path(tmp) / "first.m4a", lang_state=self.state, tts_provider=fake_tts)
            count = len(calls)
            self.assertEqual(count, trim.call_count)
            self.assertTrue(all(call.args[0] is raw for call in trim.call_args_list))
            again = audio.build_hsk6_phase1b(self.preview, Path(tmp) / "second.m4a", lang_state=self.state, tts_provider=fake_tts)
            self.assertEqual(count, len(calls))
            self.assertEqual(0, again["unique_tts_requests"])
            self.assertEqual(result["expected_speech_segments"], again["reused_segments"])
            self.assertEqual(count, trim.call_count)
            self.assertTrue(all(metric["active_speech_ms"] >= 480 for metric in result["speech_metrics"]))
            self.assertTrue(all(metric["retained_edge_silence_ms"] == 160 for metric in result["speech_metrics"]))
        self.assertEqual(self.preview["proposed_timeline"], result["timeline"])
        self.assertTrue(all(result["timeline_checks"].values()))
        self.assertTrue(all(audio.audit_hsk6_timeline(json.loads(json.dumps(result))).values()))
        self.assertEqual(116, result["expected_speech_segments"])
        self.assertEqual(116, len(result["speech_metrics"]))
        self.assertEqual({"NAM": 15, "NỮ": 15}, result["interview_speaker_counts"])
        self.assertEqual({"主持人": 15, "嘉宾": 15}, result["interview_role_counts"])
        self.assertEqual(8 * 14009 + 20 * 13791 + 12 * 13864, result["answer_pause_ms"])
        self.assertEqual({1: 0, 2: 0, 3: 0}, result["part_start_gaps_ms"])
        for item in self.preview["preview_items"]:
            self.assertIn((item["question_number_spoken"], "cmn-CN-Standard-A"), calls)
            for segment in item["repeat1"]:
                voice = "cmn-CN-Standard-A" if segment["speaker_label"] == "NỮ" else "cmn-CN-Standard-C"
                self.assertIn((segment["text"], voice), calls)
        for boundary in result["fixed_asset_boundaries"]:
            self.assertEqual(boundary["asset_end_ms"], boundary["passage_start_ms"])
        damaged = copy.deepcopy(result)
        damaged["timeline"].insert(damaged["timeline"].index("number10"), "shared_passage_9–11")
        with self.assertRaises(ValueError):
            audio.audit_hsk6_timeline(damaged)

    def test_cache_version_and_plain_narration(self):
        args = ("Google Cloud TTS", "cmn-CN-Standard-A", "1.00x", "zh-CN", "plain", "内容", "内容", "NỮ")
        self.assertNotEqual(audio._tts_cache_key(*args), audio._tts_cache_key(*args, audio_postprocess_version="edge_trim_v1"))
        self.assertIn("导演问", audio._hsk6_segment("导演问：为什么？", "NAM")["text"])
        self.assertEqual(0, self.preview["tts_requests_made"])
        text = audio.format_hsk6_preview_report(self.preview)
        self.assertIn("162/162", text)
        self.assertIn("NOT shared", text)

    def test_ui_hsk6_routes_and_batch_guard(self):
        tree = ast.parse((audio.BASE_DIR / "app.pyw").read_text())
        functions = {node.name: node for node in ast.walk(tree) if isinstance(node, ast.FunctionDef)}
        for name, target in (("_hsk30_default_source", "HSK30_HSK6_SOURCE"),
                             ("_hsk30_parser", "parse_hsk6_phase1"),
                             ("_hsk30_builder", "build_hsk6_phase1b")):
            node = functions[name]
            values = [value.id for d in ast.walk(node) if isinstance(d, ast.Dict)
                      for key, value in zip(d.keys, d.values) if isinstance(key, ast.Constant)
                      and key.value == "hsk6" and isinstance(value, ast.Name)]
            self.assertEqual([target], values)
        for name in ("export_hsk30_audio_batch", "refresh_level_labels"):
            node = functions[name]
            guards = [stmt for stmt in ast.walk(node) if isinstance(stmt, ast.If)
                      and "HSK6_BATCH_APPROVED" in ast.unparse(stmt.test)]
            self.assertEqual(1, len(guards))
            self.assertTrue(any(isinstance(stmt, ast.Return) for stmt in guards[0].body))

    def test_new_and_old_headers_and_part1_labels(self):
        rows = audio.read_xlsx_rows(audio.HSK30_HSK6_SOURCE)
        for header in ("Nội dung nghe", "NỘI DUNG PHẦN NGHE"):
            changed = copy.deepcopy(rows)
            changed[0].cells["G"] = header
            with patch.object(audio, "read_xlsx_rows", return_value=changed):
                result = audio.validate_hsk6_workbook(audio.HSK30_HSK6_SOURCE)
            self.assertEqual("PASS", result["status"])
            self.assertEqual("G", result["source_columns"]["content"])
            items = result["parsed_items"]["HSK6-30-01"][:8]
            self.assertEqual(8, len(items))
            self.assertTrue(all(item["item_type"] == "Đoạn văn ngắn – Chọn phát biểu đúng" for item in items))
            self.assertEqual(136, result["item_type_counts"]["Nghe đoạn văn ngắn – Chọn phát biểu đúng"])

    def test_all_interview_turns_preserved_without_metadata_speaking(self):
        total = 0
        for items in self.structure["parsed_items"].values():
            for item in items:
                if not item["is_interview"] or not item["stimulus_segments"]:
                    continue
                passage = item["stimulus_source"]
                lines = [line.strip() for line in passage.splitlines() if line.strip()]
                self.assertEqual(10, len(lines))
                total += len(lines)
                for line, segment in zip(lines, item["stimulus_segments"]):
                    match = audio.HSK6_INTERVIEW_TURN_RE.fullmatch(line)
                    self.assertIsNotNone(match)
                    self.assertEqual(match.group(2).strip(), segment["source_text"])
                    self.assertEqual(audio._normalize_tts_text(segment["source_text"]), segment["text"])
                    self.assertEqual(match.group(1), segment["speaker_token"])
                    self.assertEqual(audio.HSK6_AUDIO_PROFILE["interview_role_voices_by_group"][item["group"]][match.group(1)],
                                     segment["speaker_label"])
                    self.assertFalse(segment["text"].startswith(("主持人:", "嘉宾:", "主持人：", "嘉宾：")))
                self.assertEqual(passage, item["source_text"].rsplit("。", 1)[0].strip() + "。")
                self.assertGreaterEqual(passage.count("？") + passage.count("?"), 1)
        self.assertEqual(540, total)
        for number in (29, 33, 37):
            item = self.preview["preview_items"][number - 1]
            self.assertEqual(item["stimulus_segments"], audio._hsk6_interview_segments(" " + item["stimulus_source"] + " ", item["group"]))

    def test_ten_turns_and_internal_interview_question_not_exam(self):
        source = "\n".join(f"{'主持人' if n % 2 == 0 else '嘉宾'}：{'采访中为什么这样说？' if n % 2 == 0 else '他说：自然的冒号也保留。'}" for n in range(10))
        passage, question = audio._hsk6_split_content(source + "\n\n最终问题是什么？", owns_passage=True)
        self.assertEqual("最终问题是什么？", question)
        for group, mapping in audio.HSK6_AUDIO_PROFILE["interview_role_voices_by_group"].items():
            segments = audio._hsk6_interview_segments(passage, group)
            self.assertEqual(10, len(segments))
            self.assertEqual(["主持人", "嘉宾"] * 5, [segment["speaker_token"] for segment in segments])
            self.assertEqual([mapping["主持人"], mapping["嘉宾"]] * 5,
                             [segment["speaker_label"] for segment in segments])
            self.assertIn("采访中为什么这样说？", segments[0]["source_text"])
        for damaged in (source.rsplit("\n", 1)[0], source.replace("主持人：", "嘉宾：", 1),
                        "旁白。\n" + source, source.replace("\n", "\n无标签叙述。\n", 1),
                        source.replace("主持人：", "男：", 1)):
            with self.subTest(damaged=damaged[:40]), self.assertRaises(ValueError):
                audio._hsk6_interview_segments(damaged, "29–32")
        with patch.dict(audio.HSK6_AUDIO_PROFILE["interview_role_voices_by_group"], {"29–32": {}}, clear=False):
            with self.assertRaisesRegex(ValueError, "voice mapping"):
                audio._hsk6_interview_segments(source, "29–32")
        rows = audio.read_xlsx_rows(audio.HSK30_HSK6_SOURCE)
        rows[29].cells["G"] = "未标注叙述。" + rows[29].cells["G"]
        with patch.object(audio, "read_xlsx_rows", return_value=rows):
            result = audio.validate_hsk6_workbook(audio.HSK30_HSK6_SOURCE)
        self.assertEqual("FAIL", result["status"])
        self.assertTrue(any("HSK6-30-01 source row 30 Q29" in error for error in result["invalid_rows"]))

    def test_six_corrected_exam_questions_are_spoken_from_new_workbook(self):
        expected = {
            ("HSK6-30-15", 11): "团队请从未接触过机械钟的学生参加测试，主要是为了什么？",
            ("HSK6-30-15", 14): "老师为什么安排喷漆表面和粗糙金属进行对照？",
            ("HSK6-30-15", 21): "导师在培训结束前设置两种异常情况，主要是为了训练学生什么能力？",
            ("HSK6-30-17", 11): "新实训项目对学生进入企业实习产生了什么影响？",
            ("HSK6-30-17", 25): "工厂采用什么方式验证清洁方法的稳定性？",
            ("HSK6-30-17", 28): "主办方怎样验证棋钟设置说明是否清楚？",
        }
        selected = []
        for (exam, number), text in expected.items():
            item = self.structure["parsed_items"][exam][number - 1]
            self.assertEqual(text, item["question_source"])
            self.assertEqual(audio._normalize_tts_text(text), item["question_segment"]["text"])
            self.assertEqual("NỮ", item["question_segment"]["speaker_label"])
            selected.append(item["question_segment"])
        calls = []
        counts = {"unique_tts_requests": 0, "reused_segments": 0, "generated_segments": 0,
                  "generated_speech_ms": 0}
        tone = Sine(440).to_audio_segment(duration=150)
        def fake_tts(text, **kwargs):
            calls.append((text, kwargs["voice_name"]))
            return tone
        with TemporaryDirectory() as tmp:
            for segment in selected + selected:
                audio._hsk4_tts_segment(segment, engine="Google Cloud TTS", speed="1.00x",
                                        lang_state=self.state, cache_dir=Path(tmp), google_api_key="",
                                        tts_provider=fake_tts, counts=counts)
        self.assertEqual([(audio._normalize_tts_text(text), "cmn-CN-Standard-A") for text in expected.values()], calls)
        self.assertEqual(6, counts["unique_tts_requests"])
        self.assertEqual(6, counts["reused_segments"])

    def test_text_voice_changes_cannot_reuse_old_cache_key(self):
        args = ("Google Cloud TTS", "cmn-CN-Standard-A", "1.00x", "zh-CN", "plain", "旧内容", "旧内容", "NỮ")
        old_key = audio._tts_cache_key(*args, audio_postprocess_version="edge_trim_v1")
        changed_text = (*args[:5], "新内容", "新内容", args[7])
        changed_voice = (args[0], "cmn-CN-Standard-C", *args[2:7], "NAM")
        self.assertNotEqual(old_key, audio._tts_cache_key(*changed_text, audio_postprocess_version="edge_trim_v1"))
        self.assertNotEqual(old_key, audio._tts_cache_key(*changed_voice, audio_postprocess_version="edge_trim_v1"))

    def test_hsk6_retries_only_transient_google_voice_failure(self):
        class FlakyClient:
            def __init__(self, message):
                self.message = message
                self.calls = 0

            def synthesize_speech(self, **kwargs):
                self.calls += 1
                if self.calls == 1:
                    raise InvalidArgument(self.message)
                return kwargs

        client = FlakyClient("Failed to synthesize with voice cmn-CN-Standard-A")
        with patch.object(audio.time, "sleep") as sleep:
            result = audio._hsk6_google_synthesize_with_retry(client, "text", "voice", "config")
        self.assertEqual(2, client.calls)
        self.assertEqual({"input": "text", "voice": "voice", "audio_config": "config"}, result)
        sleep.assert_called_once_with(1)

        client = FlakyClient("Malformed input")
        with patch.object(audio.time, "sleep") as sleep, self.assertRaises(InvalidArgument):
            audio._hsk6_google_synthesize_with_retry(client, "text", "voice", "config")
        self.assertEqual(1, client.calls)
        sleep.assert_not_called()

    def test_approved_batch_continues_after_failure_and_resumes_verified_outputs(self):
        boundaries = [{"group": group, "manual_gap_ms": 0, "asset_end_ms": 40,
                       "passage_start_ms": 40}
                      for group in audio.HSK6_AUDIO_PROFILE["expected_shared_groups"]]

        def builder(preview, output, **kwargs):
            if preview["detected_exam"] == "HSK6-30-01":
                raise RuntimeError("Test failure")
            output.write_bytes(b"audio")
            return {"timeline": preview["proposed_timeline"], "repeat_gap_ms": 0,
                    "part_start_gaps_ms": {1: 0, 2: 0, 3: 0},
                    "fixed_asset_boundaries": boundaries, "status": "INFO",
                    "actual_duration_ms": 1000, "file_size_bytes": 5,
                    "unique_tts_requests": 1, "reused_segments": 0}

        with TemporaryDirectory() as tmpdir, \
             patch.object(audio, "build_hsk6_phase1b", side_effect=builder) as build, \
             patch.object(audio, "_probe_audio_duration_ms", return_value=1000):
            results = audio.build_hsk6_batch(audio.HSK30_HSK6_SOURCE, tmpdir, lang_state=self.state)
            self.assertEqual("FAIL", results[0]["status"])
            self.assertEqual(17, sum(result["status"] == "INFO" for result in results))
            self.assertEqual("HSK6-30-18", results[-1]["exam"])
            build.reset_mock()
            resumed = audio.build_hsk6_batch(audio.HSK30_HSK6_SOURCE, tmpdir, lang_state=self.state)
            self.assertEqual(1, build.call_count)
            self.assertEqual(17, sum(result["status"] == "SKIP_VERIFIED" for result in resumed))


if __name__ == "__main__":
    unittest.main()
