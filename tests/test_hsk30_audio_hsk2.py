import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from pydub.generators import Sine

from pipelines.hsk30_audio import (
    HSK30_HSK2_SOURCE,
    _parse_hsk2_segments,
    build_hsk2_phase1b,
    format_hsk2_preview_report,
    parse_hsk2_phase1,
    phase1_output_filename,
    validate_hsk2_workbook,
    validate_hsk30_assets,
)


class Hsk30AudioHsk2Tests(unittest.TestCase):
    def _lang_state(self):
        return {
            "engine": "Google Cloud TTS",
            "speed": "Chậm",
            "voice": "Hội thoại 1 câu nữ - 1 câu nam",
            "zh_male_voice": "cmn-CN-Standard-C",
            "zh_female_voice": "cmn-CN-Standard-A",
            "zh_narrator_voice": "cmn-CN-Standard-A",
            "hsk_speed_profile": "HSK2",
            "speech_rate": 0.70,
        }

    def test_workbook_structure_has_sixteen_exams(self):
        structure = validate_hsk2_workbook(HSK30_HSK2_SOURCE)
        self.assertEqual("PASS", structure["status"])
        self.assertEqual(16, structure["exam_count"])
        self.assertEqual({"row_count": 28, "example_count": 3, "real_question_count": 25, "part_counts": {1: 5, 2: 10, 3: 10}}, structure["exams"]["HSK2001"])

    def test_hsk2001_preview_uses_number_and_part_rules(self):
        report = parse_hsk2_phase1(HSK30_HSK2_SOURCE, lang_state=self._lang_state())
        self.assertEqual("HSK2", report["level"])
        self.assertEqual("HSK2001", report["detected_exam"])
        self.assertEqual(25, report["question_count"])
        self.assertEqual({1: 5, 2: 10, 3: 10}, report["part_counts"])
        self.assertEqual(3, len(report["fixed_instruction_rows_ignored"]))
        self.assertEqual("一。", report["preview_items"][0]["question_number_spoken"])
        self.assertEqual("二十五。", report["preview_items"][-1]["question_number_spoken"])

        part2 = report["preview_items"][5]
        self.assertEqual(["NAM", "NỮ"], [segment["speaker_label"] for segment in part2["repeat1"]])
        self.assertEqual("这些碗多少钱？", part2["repeat1"][0]["text"])

        part3 = report["preview_items"][15]
        self.assertEqual(["NỮ", "NAM", "NỮ"], [segment["speaker_label"] for segment in part3["repeat1"]])
        self.assertEqual(["NỮ", "NAM", "NAM"], [segment["speaker_label"] for segment in part3["repeat2"]])
        self.assertEqual("王经理在哪儿？", part3["repeat1"][2]["text"])
        self.assertNotIn("问：", part3["repeat1"][2]["text"])

        preview = format_hsk2_preview_report(report)
        self.assertIn("Level: HSK2", preview)
        self.assertIn("Questions: 25", preview)
        self.assertIn("TTS requests in dry run: 0", preview)
        self.assertIn("Source number: 一", preview)

    def test_hsk2_assets_and_output_names(self):
        status = validate_hsk30_assets(level="hsk2")
        self.assertTrue(status["ok"])
        self.assertIn("hsk2/hsk2_intro.m4a", status["present"])
        self.assertIn("common/hsk_outtro.m4a", status["present"])
        self.assertEqual("hsk2-30-001.m4a", phase1_output_filename("HSK2001"))
        self.assertEqual("hsk2-30-016.m4a", phase1_output_filename("HSK2016"))

    def test_malformed_dialogue_is_rejected(self):
        with self.assertRaises(ValueError):
            _parse_hsk2_segments("男：只有一个人。", require_question=False)
        with self.assertRaises(ValueError):
            _parse_hsk2_segments("男：甲。女：乙。", require_question=True)

    def test_hsk2001_fake_build_reuses_repeat_cache_and_strips_metadata(self):
        report = parse_hsk2_phase1(HSK30_HSK2_SOURCE, lang_state=self._lang_state())
        tts_calls = []

        def fake_tts(text, **kwargs):
            tts_calls.append(text)
            return Sine(440).to_audio_segment(duration=75 + (len(text) % 5) * 10).set_channels(2).set_frame_rate(44100)

        with TemporaryDirectory() as tmpdir:
            output_path = Path(tmpdir) / "hsk2-30-001.m4a"
            with patch("pipelines.hsk30_audio._make_tts_cache_dir", return_value=Path(tmpdir) / "cache"):
                result = build_hsk2_phase1b(report, output_path, lang_state=report["lang_state"], tts_provider=fake_tts)
            self.assertTrue(output_path.is_file())

        self.assertEqual("HSK2001", result["exam"])
        self.assertEqual("INFO", result["status"])
        self.assertGreater(result["unique_tts_requests"], 0)
        self.assertGreater(result["reused_segments"], 0)
        self.assertEqual(result["unique_tts_requests"], result["generated_segments"])
        self.assertGreater(result["outtro_ms"], 0)
        self.assertTrue(all(not text.startswith(("男：", "女：", "问：")) for text in tts_calls))
        self.assertEqual({1, 2, 3}, set(result["part_breakdown"]))
        self.assertEqual(7500 * 5 + 11300 * 10 + 13900 * 10, result["answer_silence_ms"])


if __name__ == "__main__":
    unittest.main()
