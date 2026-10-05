import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from pydub.generators import Sine

from pipelines.hsk30_audio import (
    HSK30_HSK4_SOURCE,
    _load_hsk4_exam_rows,
    _hsk4_parse_content,
    build_hsk4_phase1b,
    format_hsk4_preview_report,
    parse_hsk4_phase1,
    phase1_output_filename,
    validate_hsk30_assets,
    validate_hsk4_workbook,
)


class Hsk30AudioHsk4Tests(unittest.TestCase):
    def _lang_state(self):
        return {
            "engine": "Google Cloud TTS",
            "speed": "Bình thường",
            "voice": "Hội thoại 1 câu nữ - 1 câu nam",
            "zh_male_voice": "cmn-CN-Standard-C",
            "zh_female_voice": "cmn-CN-Standard-A",
            "zh_narrator_voice": "cmn-CN-Standard-A",
            "hsk_speed_profile": "HSK4",
            "speech_rate": 0.95,
        }

    def test_workbook_has_17_exams_and_hsk4_structure(self):
        structure = validate_hsk4_workbook(HSK30_HSK4_SOURCE)
        self.assertEqual("PASS", structure["status"])
        self.assertEqual(17, structure["exam_count"])
        self.assertEqual(32, structure["exams"]["HSK4-30-01"]["row_count"])
        self.assertEqual({1: 14, 2: 18}, structure["exams"]["HSK4-30-01"]["part_counts"])
        self.assertEqual(6, len(structure["exams"]["HSK4-30-01"]["shared_groups"]))
        self.assertEqual({"20–21", "22–23", "24–25", "26–27", "28–29", "30–32"}, set(structure["shared_group_counts"]))

    def test_parse_strips_embedded_question_number_and_handles_hsk4017(self):
        report = parse_hsk4_phase1(HSK30_HSK4_SOURCE, lang_state=self._lang_state())
        self.assertEqual("READY_FOR_REVIEW", report["status"])
        self.assertEqual({1: 14, 2: 18}, report["part_counts"])
        self.assertEqual("一。", report["preview_items"][0]["question_number_spoken"])
        self.assertEqual("关于这家商场,可以知道什么?", report["preview_items"][19]["question_spoken"])
        self.assertNotIn("20", report["preview_items"][19]["question_spoken"])
        self.assertFalse(report["preview_items"][20]["stimulus_segments"])
        report9 = parse_hsk4_phase1(HSK30_HSK4_SOURCE, exam_code="HSK4-30-09", lang_state=self._lang_state())
        self.assertEqual(3, len(report9["preview_items"][8]["stimulus_segments"]))

        report17 = parse_hsk4_phase1(HSK30_HSK4_SOURCE, exam_code="HSK4-30-17", lang_state=self._lang_state())
        self.assertEqual([(number, "Độc lập") for number in range(15, 20)], [
            (item["question_number"], item["group"]) for item in report17["preview_items"][14:19]
        ])
        self.assertTrue(report17["preview_items"][14]["stimulus_segments"])
        self.assertTrue(report17["preview_items"][15]["stimulus_segments"])

        preview = format_hsk4_preview_report(report)
        self.assertIn("Repetitions: 1 (no repeat2)", preview)
        self.assertIn("Embedded Arabic markers stripped:", preview)
        self.assertIn("Spoken question: 关于这家商场,可以知道什么?", preview)

    def test_groups_before_question20_follow_group_column(self):
        loaded = _load_hsk4_exam_rows(HSK30_HSK4_SOURCE, "Sheet1")
        rows = loaded["exams"]["HSK4-30-17"]
        for start, group in ((15, "15–16"), (17, "17–18")):
            rows[start - 1].cells["_hsk4_group"] = group
            follower = rows[start]
            follower.cells["_hsk4_group"] = group
            parsed = _hsk4_parse_content(follower.cells["_hsk4_content"])
            follower.cells["_hsk4_content"] = "问：" + parsed["question"]["text"]
        with patch("pipelines.hsk30_audio._load_hsk4_exam_rows", return_value=loaded):
            report = parse_hsk4_phase1(HSK30_HSK4_SOURCE, exam_code="HSK4-30-17")
        self.assertEqual(8, report["shared_group_count"])
        self.assertEqual(1, report["independent_count"])
        self.assertEqual([(15, "15–16"), (16, "15–16"), (17, "17–18"), (18, "17–18"), (19, "Độc lập")], [
            (item["question_number"], item["group"]) for item in report["preview_items"][14:19]
        ])
        self.assertTrue(report["preview_items"][14]["stimulus_segments"])
        self.assertFalse(report["preview_items"][15]["stimulus_segments"])
        self.assertTrue(report["preview_items"][16]["stimulus_segments"])
        self.assertFalse(report["preview_items"][17]["stimulus_segments"])

    def test_assets_and_output_names(self):
        assets = validate_hsk30_assets(level="hsk4")
        self.assertTrue(assets["ok"])
        self.assertEqual("hsk4-30-001.m4a", phase1_output_filename("HSK4-30-01"))
        self.assertEqual("hsk4-30-017.m4a", phase1_output_filename("HSK4-30-17"))

    def test_fake_build_plays_each_question_once_and_shared_passage_once(self):
        report = parse_hsk4_phase1(HSK30_HSK4_SOURCE, lang_state=self._lang_state())
        tts_calls = []

        def fake_tts(text, **kwargs):
            tts_calls.append(text)
            return Sine(440).to_audio_segment(duration=45 + len(text) % 8).set_channels(2).set_frame_rate(44100)

        with TemporaryDirectory() as tmpdir:
            output_path = Path(tmpdir) / "hsk4-30-001.m4a"
            with patch("pipelines.hsk30_audio._make_tts_cache_dir", return_value=Path(tmpdir) / "cache"):
                result = build_hsk4_phase1b(report, output_path, lang_state=report["lang_state"], tts_provider=fake_tts)
            self.assertTrue(output_path.is_file())

        self.assertEqual("HSK4-30-01", result["exam"])
        self.assertEqual("INFO", result["status"])
        self.assertTrue(result["one_playback"])
        self.assertTrue(result["no_repeat2"])
        self.assertEqual(0, result["repeat_gap_ms"])
        self.assertEqual(32, result["answer_pause_ms"] // 16180)
        self.assertEqual(32, result["timeline"].count("answer_pause"))
        self.assertEqual(0, sum(1 for token in result["timeline"] if token == "repeat_gap"))
        self.assertEqual(32, sum(1 for token in result["timeline"] if token.startswith("number") and not token.endswith("_gap")))
        self.assertEqual(32, sum(1 for token in result["timeline"] if token.startswith("question")))
        self.assertEqual(6, result["shared_passage_count"])
        self.assertTrue(all(not text.startswith(("男:", "女:", "问:", "男：", "女：", "问：")) for text in tts_calls))

        timeline = result["timeline"]
        group_start = timeline.index("shared_passage_20–21")
        self.assertEqual(
            ["shared_passage_20–21", "shared_passage_to_number_gap", "number20", "number_to_question_gap", "question20", "answer_pause", "number21", "number_to_question_gap", "question21", "answer_pause"],
            timeline[group_start:group_start + 10],
        )


if __name__ == "__main__":
    unittest.main()
