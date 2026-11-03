import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from openpyxl import Workbook
from pydub import AudioSegment
from pydub.generators import Sine

from pipelines.hsk30_audio import (
    HSK30_DEFAULT_SOURCE,
    _build_question_slot,
    build_hsk1_phase1b,
    format_phase1_preview_report,
    format_phase1b_build_report,
    parse_hsk1_phase1,
    phase1_output_filename,
    _strip_speaker_prefix,
    validate_hsk30_assets,
)


class Hsk30AudioPhase1Tests(unittest.TestCase):
    @staticmethod
    def _write_minimal_hsk1_workbook(path, exam_id):
        workbook = Workbook()
        sheet = workbook.active
        sheet.title = "Sheet1"
        sheet.append(["Mã đề", "Kỹ năng", "Phần", "Dạng bài", "Nhóm câu", "Số câu", "", "Phụ đề", "", "", ""])
        for index in range(1, 21):
            part = (index - 1) // 5 + 1
            content_h = "女：你好。" if part >= 3 else "三本新书"
            content_j = "男：你好。" if part == 3 else ("问：学校在哪里？" if part == 4 else "")
            sheet.append([
                exam_id,
                "Nghe",
                f"Phần {part}",
                "Dạng bài",
                "1–5",
                "一",
                "。",
                content_h,
                "。",
                content_j,
                "。",
            ])
        workbook.save(path)

    def test_hsk1001_preview_summary_and_patterns(self):
        report = parse_hsk1_phase1(
            HSK30_DEFAULT_SOURCE,
            lang_state={
                "engine": "Google Cloud TTS",
                "speed": 0.90,
                "voice": "Hội thoại 1 câu nam - 1 câu nữ",
                "zh_male_voice": "cmn-CN-Standard-C",
                "zh_female_voice": "cmn-CN-Wavenet-A",
                "hsk_speed_profile": "HSK1",
                "speech_rate": 0.90,
            },
        )

        self.assertEqual(20, report["question_count"])
        self.assertEqual({1: 5, 2: 5, 3: 5, 4: 5}, report["part_counts"])
        self.assertEqual([2, 8, 14, 20], report["fixed_instruction_rows_ignored"])
        self.assertEqual("hsk1-30-001.m4a", report["output_filename"])
        self.assertTrue(report["asset_status"]["ok"])

        first = report["preview_items"][0]
        self.assertEqual(1, first["part"])
        self.assertEqual("三本新书", first["text_h"])

        part3 = report["preview_items"][10]
        self.assertEqual(3, part3["part"])
        self.assertEqual("NỮ", part3["repeat1"][0]["speaker_label"])
        self.assertEqual("NAM", part3["repeat1"][1]["speaker_label"])

        part4 = report["preview_items"][15]
        self.assertEqual(4, part4["part"])
        self.assertEqual("NỮ", part4["repeat1"][0]["speaker_label"])
        self.assertEqual("NAM", part4["repeat1"][1]["speaker_label"])
        self.assertEqual("NAM", part4["repeat2"][0]["speaker_label"])
        self.assertEqual("NỮ", part4["repeat2"][1]["speaker_label"])
        self.assertEqual("我家在学校东边。", part4["text_h"])
        self.assertEqual("我家在学校东边。", part4["spoken_h"])
        self.assertEqual("学校在哪里?", _strip_speaker_prefix("问：学校在哪里？"))
        self.assertEqual("一。", first["question_number_spoken"])

        preview = format_phase1_preview_report(report)
        self.assertIn("HSK1001 parse summary", preview)
        self.assertIn("TTS requests made: 0", preview)
        self.assertIn("HSK speed profile: HSK1", preview)
        self.assertIn("Speech rate: HSK1 chuẩn — 0.90x", preview)
        self.assertIn("Number spoken: NỮ [一。]", preview)
        self.assertIn("Q11 — Phần 3", preview)
        self.assertIn("Q16 — Phần 4", preview)
        self.assertIn("Source H: 我家在学校东边。", preview)
        self.assertIn("Spoken H: 我家在学校东边。", preview)

    def test_asset_manifest_matches_user_paths(self):
        status = validate_hsk30_assets()
        self.assertTrue(status["ok"])
        self.assertIn("common/hsk_1_6_intro_music.m4a", status["present"])
        self.assertIn("hsk1/hsk1_intro.m4a", status["present"])

    def test_output_filename_mapping(self):
        self.assertEqual("hsk1-30-007.m4a", phase1_output_filename("HSK1007"))
        self.assertEqual("hsk1-30-007.m4a", phase1_output_filename("HSK1007-30"))

    def test_hsk1_parser_accepts_legacy_and_30_suffix_exam_ids(self):
        with TemporaryDirectory() as tmpdir:
            legacy_path = Path(tmpdir) / "legacy.xlsx"
            current_path = Path(tmpdir) / "current.xlsx"
            self._write_minimal_hsk1_workbook(legacy_path, "HSK1001")
            self._write_minimal_hsk1_workbook(current_path, "HSK1001-30")

            legacy_report = parse_hsk1_phase1(legacy_path, exam_code="HSK1001")
            current_report = parse_hsk1_phase1(current_path, exam_code="HSK1001")
            suffixed_request_report = parse_hsk1_phase1(current_path, exam_code="HSK1001-30")

        for report in (legacy_report, current_report, suffixed_request_report):
            self.assertEqual("HSK1001", report["detected_exam"])
            self.assertEqual(20, report["question_count"])
            self.assertEqual({1: 5, 2: 5, 3: 5, 4: 5}, report["part_counts"])
            self.assertEqual("hsk1-30-001.m4a", report["output_filename"])

    def test_phase1b_build_report_hits_pass_range_with_cached_reuse(self):
        report = parse_hsk1_phase1(
            HSK30_DEFAULT_SOURCE,
            lang_state={
                "engine": "Google Cloud TTS",
                "speed": 0.90,
                "voice": "Hội thoại 1 câu nam - 1 câu nữ",
                "zh_male_voice": "cmn-CN-Standard-C",
                "zh_female_voice": "cmn-CN-Wavenet-A",
                "hsk_speed_profile": "HSK1",
                "speech_rate": 0.90,
            },
        )

        tts_calls = []

        def fake_tts(text, **kwargs):
            tts_calls.append(text)
            duration = 90 + (len(text) % 5) * 15
            return Sine(440).to_audio_segment(duration=duration).set_channels(2).set_frame_rate(44100)

        with TemporaryDirectory() as tmpdir:
            output_path = Path(tmpdir) / "hsk1-30-001.m4a"
            with patch("pipelines.hsk30_audio._make_tts_cache_dir", return_value=Path(tmpdir) / "cache"):
                result = build_hsk1_phase1b(
                    report,
                    output_path,
                    lang_state=report["lang_state"],
                    tts_provider=fake_tts,
                )

            self.assertEqual("HSK1001", result["exam"])
            self.assertEqual("INFO", result["status"])
            self.assertTrue(Path(result["output_file"]).is_file())
            self.assertEqual(70, result["unique_tts_requests"])
            self.assertEqual(5, result["reused_segments"])
            self.assertEqual(70, result["generated_segments"])
            self.assertTrue(all(not text.startswith("问：") for text in tts_calls))

            build_report = format_phase1b_build_report(result)
            self.assertIn("Exam: HSK1001", build_report)
            self.assertIn("Status: INFO", build_report)
            self.assertIn("Part1 duration:", build_report)
            self.assertIn("Unique TTS requests: 70", build_report)
            self.assertIn("HSK speed profile: HSK1", build_report)
            self.assertIn("Speech rate: HSK1 chuẩn — 0.90x", build_report)
            self.assertIn("Reference duration:", build_report)
            self.assertIn("Actual audio duration:", build_report)
            self.assertIn("Difference:", build_report)

    def test_part4_builder_strips_question_prefix_before_tts(self):
        tts_calls = []

        def fake_tts(text, **kwargs):
            tts_calls.append(text)
            return AudioSegment.silent(duration=120).set_channels(2).set_frame_rate(44100)

        item = {
            "part": 4,
            "repeat1": [
                {"speaker_label": "NỮ", "text": "问：学校在哪里？"},
                {"speaker_label": "NAM", "text": "问：爸爸几点下班？"},
            ],
            "repeat2": [
                {"speaker_label": "NAM", "text": "问：学校在哪里？"},
                {"speaker_label": "NỮ", "text": "问：爸爸几点下班？"},
            ],
            "text_h": "问：学校在哪里？",
            "text_j": "问：爸爸几点下班？",
        }

        with TemporaryDirectory() as tmpdir:
            slot, repeat1, internal_pause_ms = _build_question_slot(
                item,
                engine="Google Cloud TTS",
                speed=0.90,
                lang_state={
                    "zh_male_voice": "cmn-CN-Standard-C",
                    "zh_female_voice": "cmn-CN-Wavenet-A",
                    "zh_narrator_voice": "cmn-CN-Wavenet-A",
                },
                cache_dir=Path(tmpdir) / "cache",
                google_api_key="",
                tts_provider=fake_tts,
                counts={
                    "unique_tts_requests": 0,
                    "reused_segments": 0,
                    "generated_segments": 0,
                    "reused_question_blocks": 0,
                    "internal_pauses_ms": 0,
                },
            )

            self.assertGreater(len(slot), 0)
            self.assertGreater(len(repeat1), 0)
            self.assertGreater(internal_pause_ms, 0)
            self.assertTrue(all(not text.startswith("问：") for text in tts_calls))
            self.assertIn("学校在哪里?", tts_calls)
            self.assertIn("爸爸几点下班?", tts_calls)


if __name__ == "__main__":
    unittest.main()
