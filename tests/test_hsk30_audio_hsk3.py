import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from pydub import AudioSegment
from pydub.generators import Sine
from pydub.silence import detect_nonsilent

from pipelines.hsk30_audio import (
    HSK30_HSK3_SOURCE,
    HSK3_TTS_POSTPROCESS_VERSION,
    _parse_hsk3_segments,
    _normalize_tts_text,
    _hsk3_segment_gaps,
    _hsk3_required_columns,
    _trim_tts_edge_silence,
    _validate_hsk3_shared_pair,
    build_hsk3_phase1b,
    hsk3_shared_block_timeline,
    hsk3_repeat_audit,
    parse_hsk3_phase1,
    phase1_output_filename,
    validate_hsk3_workbook,
    validate_hsk30_assets,
)


class Hsk30AudioHsk3Tests(unittest.TestCase):
    def _lang_state(self):
        return {
            "engine": "Google Cloud TTS",
            "speed": "Chậm",
            "voice": "Hội thoại 1 câu nữ - 1 câu nam",
            "zh_male_voice": "cmn-CN-Standard-C",
            "zh_female_voice": "cmn-CN-Standard-A",
            "zh_narrator_voice": "cmn-CN-Standard-A",
            "hsk_speed_profile": "HSK3",
            "speech_rate": 0.75,
        }

    def test_workbook_forward_fill_and_all_exam_semantics(self):
        structure = validate_hsk3_workbook(HSK30_HSK3_SOURCE)
        self.assertEqual("PASS", structure["status"])
        self.assertIn(structure["sheet"], {"Sheet1", "ghi am"})
        self.assertEqual("A", structure["source_columns"]["exam"])
        self.assertIn(structure["source_columns"]["question_number"], {"B", "F"})
        self.assertIn(structure["source_columns"]["content"], {"C", "G"})
        self.assertEqual(16, structure["exam_count"])
        self.assertEqual(30, structure["exams"]["HSK3-30-01"]["row_count"])
        self.assertGreaterEqual(structure["ignored_example_rows"], 0)
        self.assertEqual({1: 10, 2: 10, 3: 10}, structure["exams"]["HSK3-30-01"]["part_counts"])
        self.assertEqual({"女→男": 83, "男→女": 77}, structure["part1_patterns"])
        self.assertEqual({"女→男→问": 75, "男→女→问": 74, "女→男→女→问": 7, "男→女→男→问": 4}, structure["part2_patterns"])
        self.assertEqual(160, structure["part3_parsed"])

    def test_preview_voice_rules_and_shared_pair(self):
        report = parse_hsk3_phase1(HSK30_HSK3_SOURCE, lang_state=self._lang_state())
        self.assertEqual("READY_FOR_REVIEW", report["status"])
        self.assertEqual(30, report["question_count"])
        self.assertEqual({1: 10, 2: 10, 3: 10}, report["part_counts"])
        self.assertEqual("一。", report["preview_items"][0]["question_number_spoken"])
        self.assertEqual("三十。", report["preview_items"][-1]["question_number_spoken"])

        part1 = report["preview_items"][0]
        self.assertEqual(["NỮ", "NAM"], [segment["speaker_label"] for segment in part1["repeat1"]])
        self.assertEqual(part1["repeat1"], part1["repeat2"])

        part2 = report["preview_items"][10]
        self.assertGreaterEqual(len(part2["repeat1"]), 3)
        self.assertEqual("NỮ", part2["repeat1"][-1]["speaker_label"])
        self.assertEqual("问", part2["repeat1"][-1]["speaker_token"])
        self.assertNotIn("问：", part2["repeat1"][-1]["text"])

        self.assertEqual("NỮ", report["preview_items"][20]["repeat1"][0]["speaker_label"])
        self.assertEqual("NAM", report["preview_items"][21]["repeat1"][0]["speaker_label"])
        self.assertEqual(2, len(report["preview_items"][28]["repeat1"]))
        self.assertEqual(1, len(report["preview_items"][29]["repeat1"]))
        self.assertTrue(report["shared_q29_q30_ok"])

    def test_dynamic_part2_and_shared_pair_validation(self):
        self.assertEqual(
            {"exam": "A", "question_number": "F", "content": "G"},
            _hsk3_required_columns({"A": "Mã đề", "B": "Kỹ năng", "F": "Câu", "G": "Nội dung phần nghe"}),
        )
        four = _parse_hsk3_segments("女：甲。男：乙。女：丙。问：谁？", require_question=True)
        self.assertEqual(["女", "男", "女", "问"], [segment["speaker_token"] for segment in four])
        self.assertEqual([600, 600, 2150], _hsk3_segment_gaps(2, four))
        with self.assertRaises(ValueError):
            _parse_hsk3_segments("女：甲。男：乙。", require_question=True)
        self.assertEqual(("一段话。", "问题一?", "问题二?"), _validate_hsk3_shared_pair("一段话。问：问题一？", "问：问题二？"))
        self.assertEqual(("一段话很长。", "问题一?", "问题二?"), _validate_hsk3_shared_pair("一段话很长。问：问题一？", "一段话很长。问：问题二？"))
        with self.assertRaises(ValueError):
            _validate_hsk3_shared_pair("问：问题一？", "问：问题二？")
        with self.assertRaises(ValueError):
            _validate_hsk3_shared_pair("一段话。问：问题一？", "另一段。问：问题二？")

    def test_assets_names_and_special_timeline(self):
        assets = validate_hsk30_assets(level="hsk3")
        self.assertTrue(assets["ok"])
        self.assertIn("hsk3/hsk3_cue_q29_30.m4a", assets["present"])
        self.assertEqual("hsk3-30-001.m4a", phase1_output_filename("HSK3-30-01"))
        self.assertEqual("hsk3-30-016.m4a", phase1_output_filename("HSK3-30-16"))
        self.assertEqual(2, hsk3_shared_block_timeline().count("shared_passage"))
        self.assertEqual(2, hsk3_shared_block_timeline().count("number29"))
        self.assertEqual(2, hsk3_shared_block_timeline().count("number30"))
        self.assertEqual("cue_q29_30", hsk3_shared_block_timeline()[0])
        self.assertEqual("outro", hsk3_shared_block_timeline()[-1])
        timeline = hsk3_shared_block_timeline()
        expected = [
            "cue_q29_30", "shared_cue_to_passage_gap",
            "shared_passage", "passage_to_number_gap", "number29", "number_to_question_gap", "question29",
            "repeat_gap",
            "shared_passage", "passage_to_number_gap", "number29", "number_to_question_gap", "question29", "answer_pause",
            "number30", "number_to_question_gap", "question30",
            "repeat_gap",
            "number30", "number_to_question_gap", "question30", "answer_pause",
            "outro",
        ]
        self.assertEqual(expected, timeline)
        semantic = [token for token in timeline if token not in {"cue_q29_30", "shared_cue_to_passage_gap", "passage_to_number_gap", "number_to_question_gap", "repeat_gap"}]
        self.assertEqual(["shared_passage", "number29", "question29", "shared_passage", "number29", "question29", "answer_pause", "number30", "question30", "number30", "question30", "answer_pause", "outro"], semantic)
        self.assertEqual(2, timeline.count("shared_passage"))
        self.assertEqual(2, timeline.count("number29"))
        self.assertEqual(2, timeline.count("question29"))
        self.assertEqual(2, timeline.count("number30"))
        self.assertEqual(2, timeline.count("question30"))
        self.assertEqual(2, timeline.count("answer_pause"))
        self.assertEqual(2, timeline.count("repeat_gap"))
        second_q29 = max(index for index, token in enumerate(timeline) if token == "question29")
        first_q30 = min(index for index, token in enumerate(timeline) if token == "number30")
        self.assertFalse(any(token == "shared_passage" for token in timeline[second_q29 + 1:first_q30]))

    def test_edge_trim_preserves_speech_and_internal_pause(self):
        speech = Sine(440).to_audio_segment(duration=400)
        source = AudioSegment.silent(duration=140) + speech + AudioSegment.silent(duration=180) + speech + AudioSegment.silent(duration=360)
        trimmed = _trim_tts_edge_silence(source)
        self.assertLess(len(trimmed), len(source))
        spans = detect_nonsilent(trimmed, min_silence_len=20, silence_thresh=-45.0)
        self.assertGreaterEqual(len(spans), 2)
        self.assertGreaterEqual(spans[0][0], 60)
        self.assertGreaterEqual(len(trimmed) - spans[-1][1], 60)
        self.assertGreaterEqual(spans[1][0] - spans[0][1], 160)

    def test_hsk3_cache_version_reuses_normalized_clip(self):
        from pipelines.hsk30_audio import _synthesize_tts_segment

        raw = AudioSegment.silent(duration=120) + Sine(440).to_audio_segment(duration=400) + AudioSegment.silent(duration=300)
        calls = []

        def fake_tts(text, **kwargs):
            calls.append(text)
            return raw

        with TemporaryDirectory() as tmpdir:
            cache_dir = Path(tmpdir)
            first, first_reused = _synthesize_tts_segment(
                "今天星期几？", engine="Google Cloud TTS", speed="0.75x", voice_name="cmn-CN-Standard-A",
                speaker_label="NỮ", cache_dir=cache_dir, tts_provider=fake_tts,
                audio_postprocess_version=HSK3_TTS_POSTPROCESS_VERSION,
            )
            second, second_reused = _synthesize_tts_segment(
                "今天星期几？", engine="Google Cloud TTS", speed="0.75x", voice_name="cmn-CN-Standard-A",
                speaker_label="NỮ", cache_dir=cache_dir, tts_provider=fake_tts,
                audio_postprocess_version=HSK3_TTS_POSTPROCESS_VERSION,
            )
        self.assertFalse(first_reused)
        self.assertTrue(second_reused)
        self.assertEqual(1, len(calls))
        self.assertEqual(len(first), len(second))
        self.assertLess(len(first), len(raw))

    def test_hsk3_repeat_audit_counts_shared_block(self):
        report = parse_hsk3_phase1(HSK30_HSK3_SOURCE, lang_state=self._lang_state())
        audit = hsk3_repeat_audit(report["preview_items"])
        self.assertEqual(2, audit["part1_questions"]["repeats_per_question"])
        self.assertEqual(2, audit["part2_questions"]["repeats_per_question"])
        self.assertEqual(2, audit["part3_questions_21_28"]["repeats_per_question"])
        self.assertEqual(2, audit["q29_q30_shared_block"]["passages"])
        self.assertEqual(2, audit["q29_q30_shared_block"]["q29_questions"])
        self.assertEqual(2, audit["q29_q30_shared_block"]["q30_questions"])
        self.assertEqual(["q29_pass_1", "q29_pass_2", "q30_pass_1", "q30_pass_2"], audit["q29_q30_shared_block"]["order"])

    def test_fake_golden_build_reuses_shared_passage(self):
        report = parse_hsk3_phase1(HSK30_HSK3_SOURCE, lang_state=self._lang_state())
        tts_calls = []

        def fake_tts(text, **kwargs):
            tts_calls.append(text)
            return Sine(440).to_audio_segment(duration=40 + len(text) % 7).set_channels(2).set_frame_rate(44100)

        shared_passage = report["preview_items"][28]["repeat1"][0]["text"]
        with TemporaryDirectory() as tmpdir:
            output = Path(tmpdir) / "hsk3-30-001.m4a"
            with patch("pipelines.hsk30_audio._make_tts_cache_dir", return_value=Path(tmpdir) / "cache"):
                result = build_hsk3_phase1b(report, output, lang_state=report["lang_state"], tts_provider=fake_tts)
            self.assertTrue(output.is_file())

        self.assertEqual("HSK3-30-01", result["exam"])
        self.assertEqual("INFO", result["status"])
        self.assertEqual(1, tts_calls.count(_normalize_tts_text(shared_passage)))
        self.assertTrue(result["q29_q30_shared_passage_reused"])
        self.assertEqual(hsk3_shared_block_timeline(), result["shared_block_timeline"])
        self.assertTrue(all(not text.startswith(("男：", "女：", "问：")) for text in tts_calls))
        self.assertEqual({1, 2, 3}, set(result["part_breakdown"]))


if __name__ == "__main__":
    unittest.main()
