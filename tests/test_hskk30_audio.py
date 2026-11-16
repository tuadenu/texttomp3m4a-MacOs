import tempfile
import unittest
from pathlib import Path

from openpyxl import Workbook, load_workbook

from pipelines.hskk30_audio import (
    HSKK3_AUDIO_PROFILE,
    HSKK3_LEVELS,
    HSKK3_PART_BY_QUESTION,
    parse_hskk3_exam,
    validate_hskk3_workbooks,
)


HEADERS = ["Mã đề", "Kỹ năng", "Phần thi", "Dạng bài", "Số câu", "Nội dung nghe (中文)"]
ANSWER_HEADERS = ["Mã đề", "Kỹ năng", "Phần thi", "Dạng bài", "Số câu", "Đáp án tham khảo (中文)"]


def _part_label(question):
    return f"Part {HSKK3_PART_BY_QUESTION[question]}"


def _write_pair(folder: Path):
    exam_path = folder / "audio đề thi hsk 3 30.xlsx"
    answer_path = folder / "audio đáp án hskk 3-30.xlsx"
    for path, headers, is_answer in (
        (exam_path, HEADERS, False),
        (answer_path, ANSWER_HEADERS, True),
    ):
        workbook = Workbook()
        sheet = workbook.active
        sheet.title = "Dữ liệu HSKK3"
        sheet.append(headers)
        for exam_number in range(1, 31):
            exam_code = f"HSKK3-30-{exam_number:03d}"
            for question in range(1, 16):
                if is_answer:
                    content = f"参考答案{exam_number:03d}第{question}题。"
                    if question <= 8:
                        content = f"听力句子{exam_number:03d}第{question}题。"
                else:
                    content = f"听力句子{exam_number:03d}第{question}题。" if question <= 8 else ""
                sheet.append([
                    exam_code,
                    "口语",
                    _part_label(question),
                    "听后重复" if question <= 8 else ("看图说话" if question <= 13 else "回答问题"),
                    question,
                    content,
                ])
        workbook.save(path)
    return exam_path, answer_path


class Hskk30AudioSourceTests(unittest.TestCase):
    def test_profile_is_separate_and_defaults_to_review_only(self):
        self.assertEqual("hskk3_pcm_48k_v1", HSKK3_AUDIO_PROFILE["cache_profile"])
        self.assertEqual(96000, HSKK3_AUDIO_PROFILE["bitrate_kbps"] * 1000)
        self.assertEqual(48000, HSKK3_AUDIO_PROFILE["sample_rate_hz"])
        self.assertEqual(1, HSKK3_AUDIO_PROFILE["channels"])
        self.assertFalse(HSKK3_AUDIO_PROFILE["read_part3_questions"])
        self.assertFalse(HSKK3_AUDIO_PROFILE["production_batch_approved"])
        self.assertEqual(("HSK3", "HSK4", "HSK5", "HSK6", "HSK7–9"), HSKK3_LEVELS)

    def test_parser_validates_all_30_and_joins_by_exam_and_question(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            exam_path, answer_path = _write_pair(Path(temp_dir))
            report = validate_hskk3_workbooks(exam_path, answer_path)
            self.assertEqual("PASS", report["status"], report["issues"])
            self.assertEqual(30, report["exam_count"])
            self.assertEqual(450, report["exam_rows"])
            self.assertEqual(450, report["answer_rows"])
            self.assertEqual(240, report["q1_q8_match_count"])
            self.assertEqual(150, report["exam_part2_blank_cells"])
            self.assertEqual(450, report["answer_non_empty_content_count"])
            self.assertEqual(0, report["tts_calls"])

            preview = parse_hskk3_exam(exam_path, answer_path, "HSKK3-30-001")
            self.assertEqual("PASS", preview["status"])
            selected = preview["selected_exam_data"]
            self.assertEqual("hskk3-30-001", selected["output_stem"])
            self.assertEqual(15, len(selected["questions"]))
            self.assertEqual("听力句子001第1题。", selected["questions"][0]["answer_text"])
            self.assertEqual("参考答案001第9题。", selected["questions"][8]["answer_text"])
            self.assertEqual("", selected["questions"][8]["exam_text"])

    def test_q1_q8_mismatch_fails_even_when_row_order_is_same(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            exam_path, answer_path = _write_pair(Path(temp_dir))
            workbook = load_workbook(answer_path)
            workbook.active["F2"] = "不同内容。"
            workbook.save(answer_path)
            report = validate_hskk3_workbooks(exam_path, answer_path)
            self.assertEqual("FAIL", report["status"])
            self.assertEqual(239, report["q1_q8_match_count"])
            self.assertTrue(any("239/240" in issue for issue in report["issues"]))

    def test_duplicate_missing_and_wrong_order_are_reported(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            exam_path, answer_path = _write_pair(Path(temp_dir))
            workbook = load_workbook(exam_path)
            sheet = workbook.active
            sheet["E3"] = 1  # duplicate Q1 in exam 001
            workbook.save(exam_path)
            report = validate_hskk3_workbooks(exam_path, answer_path)
            self.assertEqual("FAIL", report["status"])
            self.assertTrue(any("trùng mã đề + số câu" in issue for issue in report["issues"]))
            self.assertTrue(any("sai thứ tự câu" in issue for issue in report["issues"]))

    def test_missing_answer_and_part_mismatch_are_blockers(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            exam_path, answer_path = _write_pair(Path(temp_dir))
            workbook = load_workbook(answer_path)
            workbook.active["F2"] = None
            workbook.active["C10"] = "Part 1"
            workbook.save(answer_path)
            report = validate_hskk3_workbooks(exam_path, answer_path)
            self.assertEqual("FAIL", report["status"])
            self.assertTrue(any("thiếu nội dung Q01" in issue for issue in report["issues"]))
            self.assertTrue(any("Q09 phải thuộc Part 2" in issue for issue in report["issues"]))

    def test_exam_part2_must_remain_blank(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            exam_path, answer_path = _write_pair(Path(temp_dir))
            workbook = load_workbook(exam_path)
            workbook.active["F10"] = "描述样例图片。"
            workbook.save(exam_path)
            report = validate_hskk3_workbooks(exam_path, answer_path)
            self.assertEqual("FAIL", report["status"])
            self.assertEqual(149, report["exam_part2_blank_cells"])
            self.assertTrue(any("để trống đúng 150 ô Q9–13" in issue for issue in report["issues"]))


if __name__ == "__main__":
    unittest.main()
