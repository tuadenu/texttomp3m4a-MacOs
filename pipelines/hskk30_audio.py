"""HSKK 3.0 oral audio source parsing and validation.

This profile is intentionally separate from the HSK listening pipeline.  It
contains no fixed-asset guesses and never writes audio from unreviewed assets.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import re
import shutil
import subprocess
import tempfile
import unicodedata
import zipfile
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

from openpyxl import load_workbook
from pydub import AudioSegment
from pydub.silence import detect_nonsilent


BASE_DIR = Path(__file__).resolve().parents[1]
HSKK30_ROOT = BASE_DIR / "testdata" / "hskk" / "hskk3"
HSKK3_DEFAULT_EXAM_SOURCE = HSKK30_ROOT / unicodedata.normalize("NFD", "audio đề thi hsk 3 30.xlsx")
HSKK3_DEFAULT_ANSWER_SOURCE = HSKK30_ROOT / unicodedata.normalize("NFD", "audio đáp án hskk 3-30.xlsx")
HSKK3_DEFAULT_ASSET_ZIP = BASE_DIR / "assets" / "hskk30" / "hskk3" / "hskk3_30_fixed_assets_review_v1.zip"
HSKK3_SAMPLE_ROOT = BASE_DIR / "assets" / "hskk30" / "hskk3"
HSKK3_DEFAULT_SAMPLE_AUDIO = next(HSKK3_SAMPLE_ROOT.rglob("*.mp3"), HSKK3_SAMPLE_ROOT / "sample.mp3")
HSKK3_DEFAULT_SAMPLE_PDF = next(HSKK3_SAMPLE_ROOT.rglob("*.pdf"), HSKK3_SAMPLE_ROOT / "sample.pdf")
HSKK3_DEFAULT_SAMPLE_TRANSCRIPT = next(HSKK3_SAMPLE_ROOT.rglob("*.docx"), HSKK3_SAMPLE_ROOT / "sample-transcript.docx")
HSKK3_REVIEWED_SAMPLE_PDF_SHA256 = "68d0dbb4597bf3283a979a8922dbec7eb27a0e6ee205125ab4526142851d1913"
HSKK3_REVIEWED_TRANSCRIPT_SHA256 = "762698e692759d72d60c3439241f688bf5ae9abb5c5dd29df20912ae0d147ff2"

HSKK3_AUDIO_PROFILE = {
    "level": "HSKK3",
    "container": "m4a",
    "codec": "aac_low",
    "bitrate_kbps": 96,
    "sample_rate_hz": 48000,
    "channels": 1,
    "speed": 1.0,
    "true_peak_dbtp_max": -1.0,
    "speech_to_beep_ms": 2200,
    "repeat_pause_ms": 10580,
    "repeat_pause_q8_ms": 11090,
    "number_to_content_ms": None,
    "part2_answer_pause_ms": 15190,
    "part3_q14_prepare_ms": 180660,
    "part3_q15_prepare_ms": 180610,
    "part3_q14_answer_ms": 90180,
    "part3_q15_answer_ms": 90190,
    "combined_answer_gap_ms": 450,
    "cache_profile": "hskk3_pcm_48k_v1",
    "read_part3_questions": False,
    "production_batch_approved": False,
}

# This verification is tied to the exact provisional archive and MP3 hashes.
# A changed ZIP/source must be re-audited before it can be used for a golden.
HSKK3_REVIEWED_SOURCE_SHA256 = "844271e97474431fdf9bf77e3321c6891010a313f3466017d483f84ed55ab152"
HSKK3_REVIEWED_ASSET_ZIP_SHA256 = "9846170ec6966e84b384a4f3eefc929a3b8330ed734b16ae7b90f630bbf0dbb6"
HSKK3_REVIEWED_REVIEW_ASSETS = {
    "hskk3_q13_end_q14_prepare_REVIEW.m4a": "第13题结束，现在开始准备第14题，可以在试卷上写提纲，准备时间为三分钟。",
    "hskk3_q14_end_q15_prepare_REVIEW.m4a": "第十四题结束，现在开始准备第十五题，可以在试卷上写提纲，准备时间为三分钟。",
}
HSKK3_INTRO_ASR_TEXT = (
    "你叫什么名字？你是哪国人？你的准考证号码后五位是多少？"
)
HSKK3_REVIEW_METHOD = (
    "Local Whisper base ASR compared with the official sample PDF and transcript. "
    "Human listening has not been completed; fixed-asset boundary sign-off and production batch remain locked."
)
HSKK3_REQUIRED_ASSET_FILES = frozenset({
    "hskk3_intro_part1_q1.m4a",
    "hskk3_q2_number.m4a",
    "hskk3_q3_number.m4a",
    "hskk3_q4_number.m4a",
    "hskk3_q5_number.m4a",
    "hskk3_q6_number.m4a",
    "hskk3_q7_number.m4a",
    "hskk3_q8_number.m4a",
    "hskk3_repeat_beep.m4a",
    "hskk3_part2_q9_start.m4a",
    "hskk3_q9_end_q10_start.m4a",
    "hskk3_q10_end_q11_start.m4a",
    "hskk3_q11_end_q12_start.m4a",
    "hskk3_q12_end_q13_start.m4a",
    "hskk3_q13_end_q14_prepare_REVIEW.m4a",
    "hskk3_q14_answer_start.m4a",
    "hskk3_q14_end_q15_prepare_REVIEW.m4a",
    "hskk3_q15_answer_start.m4a",
    "hskk3_q15_end_outro.m4a",
})

HSKK3_LEVELS = ("HSK3", "HSK4", "HSK5", "HSK6", "HSK7–9")
HSKK3_ACTIVE_LEVELS = frozenset({"HSK3"})
HSKK3_PART_BY_QUESTION = {**{number: 1 for number in range(1, 9)},
                          **{number: 2 for number in range(9, 14)},
                          **{number: 3 for number in range(14, 16)}}
_AAC_ENCODER_CACHE: Optional[Tuple[str, List[str], str]] = None

_HEADER_ALIASES = {
    "exam_code": ("ma de", "exam code", "exam id", "ma bai thi"),
    "skill": ("ky nang", "skill"),
    "part": ("phan thi", "phan", "part"),
    "task_type": ("dang bai", "task type", "question type"),
    "question_number": ("so cau", "cau so", "question number", "question no"),
    "content": (
        "noi dung nghe", "dap an tham khao", "noi dung", "content",
        "reference answer", "answer text",
    ),
}
_EXPECTED_TASK_TYPES = {
    1: ("nghe va nhac lai", "听后重复"),
    2: ("nhin tranh noi", "看图说话"),
    3: ("tra loi cau hoi", "回答问题"),
}


def _plain(value: Any) -> str:
    if value is None:
        return ""
    text = unicodedata.normalize("NFC", str(value))
    text = text.replace("\u00a0", " ").replace("\u3000", " ")
    return re.sub(r"\s+", " ", text).strip()


def _header_key(value: Any) -> str:
    text = unicodedata.normalize("NFD", _plain(value).casefold().replace("đ", "d"))
    text = "".join(char for char in text if unicodedata.category(char) != "Mn")
    return re.sub(r"[^a-z0-9\u3400-\u9fff]+", " ", text).strip()


def _cell_key(value: Any) -> str:
    return re.sub(r"\s+", " ", _plain(value)).strip()


def _exam_key(value: Any) -> str:
    return re.sub(r"[\s\u00a0\u3000]+", "", _plain(value)).casefold()


def _question_number(value: Any) -> Optional[int]:
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float) and value.is_integer():
        return int(value)
    match = re.search(r"(?<!\d)(\d{1,2})(?!\d)", _plain(value))
    return int(match.group(1)) if match else None


def _part_number(value: Any) -> Optional[int]:
    text = _plain(value).casefold()
    match = re.search(r"(?:part|phan|p)\s*([1-3])\b", text)
    if match:
        return int(match.group(1))
    match = re.search(r"(?:第\s*)?([一二三123])(?:\s*部分|\s*part)?", text)
    if not match:
        return None
    return {"一": 1, "二": 2, "三": 3, "1": 1, "2": 2, "3": 3}[match.group(1)]


def _find_header_row(rows: Iterable[Tuple[Any, ...]]) -> Tuple[int, Dict[str, int]]:
    for row_index, row in enumerate(rows):
        by_name: Dict[str, int] = {}
        normalized = [_header_key(value) for value in row]
        for field, aliases in _HEADER_ALIASES.items():
            alias_keys = tuple(_header_key(alias) for alias in aliases)
            for column_index, key in enumerate(normalized):
                if key and any(key == alias or key.startswith(alias + " ") for alias in alias_keys):
                    by_name[field] = column_index
                    break
        if len(by_name) == len(_HEADER_ALIASES):
            return row_index, by_name
        if row_index >= 20:
            break
    raise ValueError("Không tìm thấy header đủ 6 cột HSKK3 trong 21 dòng đầu.")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _load_workbook_rows(path: str | Path, kind: str) -> Dict[str, Any]:
    source = Path(path)
    if not source.is_file():
        raise FileNotFoundError(f"Không tìm thấy Excel {kind}: {source}")
    workbook = load_workbook(source, read_only=True, data_only=True)
    try:
        chosen = None
        header_row_index = -1
        columns: Dict[str, int] = {}
        for sheet in workbook.worksheets:
            candidate_rows = sheet.iter_rows(values_only=True)
            preview = list()
            for _ in range(21):
                try:
                    preview.append(next(candidate_rows))
                except StopIteration:
                    break
            try:
                header_row_index, columns = _find_header_row(preview)
                chosen = sheet
                break
            except ValueError:
                continue
        if chosen is None:
            raise ValueError(f"Excel {kind} không có sheet chứa đúng header HSKK3.")

        rows: List[Dict[str, Any]] = []
        for row_number, values in enumerate(chosen.iter_rows(values_only=True), start=1):
            if row_number <= header_row_index + 1:
                continue
            values = tuple(values or ())
            selected = {
                field: _plain(values[index]) if index < len(values) else ""
                for field, index in columns.items()
            }
            if not any(selected.values()):
                continue
            question_number = _question_number(selected["question_number"])
            if question_number is None:
                rows.append({**selected, "row_number": row_number, "question_number_value": None})
                continue
            rows.append({**selected, "row_number": row_number, "question_number_value": question_number})
        return {
            "path": str(source.resolve()),
            "sha256": _sha256(source),
            "sheet": chosen.title,
            "header_row": header_row_index + 1,
            "columns": {field: index for field, index in columns.items()},
            "rows": rows,
        }
    finally:
        workbook.close()


def _validate_order_and_rows(dataset: Dict[str, Any], kind: str, issues: List[str]) -> Dict[str, Dict[int, Dict[str, Any]]]:
    rows = dataset["rows"]
    if len(rows) != 450:
        issues.append(f"{kind}: cần đúng 450 dòng dữ liệu, hiện có {len(rows)}.")
    by_exam: Dict[str, Dict[int, Dict[str, Any]]] = {}
    display_codes: Dict[str, str] = {}
    encountered: List[str] = []
    last_exam_key = None
    last_question = 0

    for row in rows:
        code = row["exam_code"]
        key = _exam_key(code)
        qnum = row["question_number_value"]
        if not code:
            issues.append(f"{kind}, dòng Excel {row['row_number']}: thiếu mã đề.")
            continue
        if not key:
            issues.append(f"{kind}, dòng Excel {row['row_number']}: mã đề không hợp lệ.")
            continue
        if key not in by_exam:
            by_exam[key] = {}
            display_codes[key] = code
            encountered.append(key)
        if qnum is None or qnum not in HSKK3_PART_BY_QUESTION:
            issues.append(f"{kind}, dòng Excel {row['row_number']}: số câu không thuộc Q1–15.")
            continue
        if qnum in by_exam[key]:
            issues.append(f"{kind}: trùng mã đề + số câu {code} / Q{qnum:02d}.")
            continue
        by_exam[key][qnum] = row

        if last_exam_key != key:
            if key in encountered[:-1]:
                issues.append(f"{kind}: mã đề {code} xuất hiện thành nhiều cụm dòng.")
            last_exam_key = key
            last_question = 0
        if qnum != last_question + 1:
            issues.append(
                f"{kind}, dòng Excel {row['row_number']}: sai thứ tự câu ở {code}; "
                f"cần Q{last_question + 1:02d}, gặp Q{qnum:02d}."
            )
        last_question = qnum

        expected_part = HSKK3_PART_BY_QUESTION[qnum]
        actual_part = _part_number(row["part"])
        if actual_part != expected_part:
            issues.append(
                f"{kind}, dòng Excel {row['row_number']}: Q{qnum:02d} phải thuộc Part {expected_part}, "
                f"giá trị header là {row['part']!r}."
            )
        expected_task_aliases = _EXPECTED_TASK_TYPES[expected_part]
        task_key = _header_key(row["task_type"])
        if not any(_header_key(alias) in task_key for alias in expected_task_aliases):
            issues.append(
                f"{kind}, dòng Excel {row['row_number']}: Q{qnum:02d} cần dạng bài "
                f"{expected_task_aliases[0]}/{expected_task_aliases[1]}, gặp {row['task_type']!r}."
            )
        skill_key = _header_key(row["skill"])
        if not any(token in skill_key for token in ("hskk3", "khau ngu", "口语", "noi")):
            issues.append(f"{kind}, dòng Excel {row['row_number']}: kỹ năng không phải khẩu ngữ HSKK3.")
        if kind == "đề thi" and qnum <= 8 and not row["content"]:
            issues.append(f"{kind}: thiếu nội dung câu Q{qnum:02d} của {code}.")
        if kind == "đáp án" and not row["content"]:
            issues.append(f"{kind}: thiếu nội dung Q{qnum:02d} của {code}.")

    if len(by_exam) != 30:
        issues.append(f"{kind}: cần đúng 30 mã đề, hiện có {len(by_exam)}.")
    expected_questions = list(range(1, 16))
    for key, questions in by_exam.items():
        display = display_codes[key]
        missing = sorted(set(expected_questions) - set(questions))
        extra = sorted(set(questions) - set(expected_questions))
        if len(questions) != 15 or missing or extra:
            issues.append(f"{kind}: {display} cần đủ 15 câu Q1–15; thiếu={missing}, thừa={extra}.")
    if encountered != sorted(encountered, key=lambda key: _exam_ordinal(display_codes[key])):
        issues.append(f"{kind}: thứ tự mã đề trong workbook không theo thứ tự 001–030.")
    ordinals = sorted(_exam_ordinal(display_codes[key]) for key in by_exam)
    if ordinals != list(range(1, 31)):
        issues.append(f"{kind}: mã đề phải có số thứ tự đủ 001–030; hiện có {ordinals}.")
    return by_exam


def _exam_ordinal(value: str) -> int:
    found = re.findall(r"\d+", _plain(value))
    return int(found[-1]) if found else 0


def validate_hskk3_workbooks(exam_path: str | Path, answer_path: str | Path) -> Dict[str, Any]:
    """Validate the paired HSKK3 exam and answer sheets without synthesizing audio."""
    exam = _load_workbook_rows(exam_path, "đề thi")
    answer = _load_workbook_rows(answer_path, "đáp án")
    issues: List[str] = []
    exam_index = _validate_order_and_rows(exam, "đề thi", issues)
    answer_index = _validate_order_and_rows(answer, "đáp án", issues)

    exam_keys = set(exam_index)
    answer_keys = set(answer_index)
    if exam_keys != answer_keys:
        missing_answers = sorted(exam_keys - answer_keys)
        missing_exams = sorted(answer_keys - exam_keys)
        issues.append(f"Mã đề không ghép được: thiếu đáp án={missing_answers}; thiếu đề thi={missing_exams}.")

    q1_q8_match_count = 0
    q1_q8_mismatches: List[Dict[str, Any]] = []
    for key in sorted(exam_keys & answer_keys, key=lambda item: _exam_ordinal(exam_index[item][1]["exam_code"])):
        for qnum in range(1, 9):
            exam_row = exam_index[key].get(qnum)
            answer_row = answer_index[key].get(qnum)
            if not exam_row or not answer_row:
                continue
            if _cell_key(exam_row["content"]) == _cell_key(answer_row["content"]):
                q1_q8_match_count += 1
            else:
                q1_q8_mismatches.append({
                    "exam_code": exam_row["exam_code"],
                    "question": qnum,
                    "exam_text": exam_row["content"],
                    "answer_text": answer_row["content"],
                })
    if q1_q8_match_count != 240:
        issues.append(f"Q1–8 đề/đáp án khớp {q1_q8_match_count}/240, cần đúng 240.")

    exam_blank_q9_q13 = sum(
        not row["content"]
        for questions in exam_index.values()
        for qnum, row in questions.items()
        if 9 <= qnum <= 13
    )
    if exam_blank_q9_q13 != 150:
        issues.append(
            f"Excel đề phải để trống đúng 150 ô Q9–13; hiện có {exam_blank_q9_q13}."
        )
    answer_content_count = sum(
        bool(row["content"])
        for questions in answer_index.values()
        for row in questions.values()
    )
    if answer_content_count != 450:
        issues.append(f"Excel đáp án có {answer_content_count}/450 nội dung không rỗng.")

    exams: Dict[str, Any] = {}
    for key in sorted(exam_keys & answer_keys, key=lambda item: _exam_ordinal(exam_index[item][1]["exam_code"])):
        exam_questions = exam_index[key]
        answer_questions = answer_index[key]
        code = exam_questions.get(1, {}).get("exam_code", answer_questions.get(1, {}).get("exam_code", key))
        number = _exam_ordinal(code)
        items = []
        for qnum in range(1, 16):
            exam_row = exam_questions.get(qnum, {})
            answer_row = answer_questions.get(qnum, {})
            items.append({
                "question_number": qnum,
                "part": HSKK3_PART_BY_QUESTION[qnum],
                "task_type": exam_row.get("task_type", ""),
                "exam_text": exam_row.get("content", ""),
                "answer_text": answer_row.get("content", ""),
            })
        exams[code] = {
            "exam_number": number,
            "output_stem": f"hskk3-30-{number:03d}",
            "questions": items,
        }

    return {
        "status": "PASS" if not issues else "FAIL",
        "level": "HSKK3",
        "exam_source": {key: value for key, value in exam.items() if key != "rows"},
        "answer_source": {key: value for key, value in answer.items() if key != "rows"},
        "exam_count": len(exam_index),
        "exam_rows": len(exam["rows"]),
        "answer_rows": len(answer["rows"]),
        "questions_per_exam": {key: len(value) for key, value in exam_index.items()},
        "q1_q8_match_count": q1_q8_match_count,
        "q1_q8_mismatches": q1_q8_mismatches,
        "exam_part2_blank_cells": exam_blank_q9_q13,
        "exam_part2_non_empty_cells": 150 - exam_blank_q9_q13,
        "answer_non_empty_content_count": answer_content_count,
        "part_counts": {1: 8, 2: 5, 3: 2},
        "exams": exams,
        "issues": issues,
        "tts_calls": 0,
        "production_batch_approved": False,
        "fixed_assets_reviewed": False,
        "read_part3_questions": False,
    }


def parse_hskk3_exam(exam_path: str | Path, answer_path: str | Path,
                     exam_code: Optional[str] = None) -> Dict[str, Any]:
    """Return validated paired source data for one exam; never calls TTS."""
    report = validate_hskk3_workbooks(exam_path, answer_path)
    if report["status"] != "PASS":
        return report
    if exam_code is None:
        selected = next(iter(report["exams"]))
    else:
        key = _exam_key(exam_code)
        selected = next((code for code in report["exams"] if _exam_key(code) == key), None)
        if selected is None:
            report["status"] = "FAIL"
            report["issues"].append(f"Không tìm thấy mã đề {exam_code!r} trong cặp Excel.")
            return report
    report["selected_exam"] = selected
    report["selected_exam_data"] = report["exams"][selected]
    return report


def inspect_hskk3_asset_archive(zip_path: str | Path, source_audio_path: Optional[str | Path] = None) -> Dict[str, Any]:
    """Inspect ZIP inventory and expose manifest/README text without extracting it."""
    source = Path(zip_path)
    if not source.is_file():
        raise FileNotFoundError(f"Không tìm thấy ZIP fixed assets HSKK3: {source}")
    with zipfile.ZipFile(source, "r") as archive:
        damaged = archive.testzip()
        if damaged:
            raise ValueError(f"ZIP lỗi CRC tại {damaged}.")
        files = [item for item in archive.infolist() if not item.is_dir()]
        names = [item.filename for item in files]
        audio_names = [name for name in names if Path(name).suffix.casefold() in {".m4a", ".mp3", ".wav", ".aac"}]
        readmes = [name for name in names if Path(name).name.casefold().startswith("readme")]
        manifests = [name for name in names if "manifest" in Path(name).name.casefold()]
        documents: Dict[str, str] = {}
        for name in readmes + manifests:
            try:
                documents[name] = archive.read(name).decode("utf-8-sig")
            except UnicodeDecodeError:
                documents[name] = archive.read(name).decode("utf-8", errors="replace")
        review_names = [name for name in audio_names if "review" in Path(name).stem.casefold()]
        manifest = None
        for name in manifests:
            try:
                candidate = json.loads(documents[name])
            except Exception:
                continue
            if isinstance(candidate, dict) and isinstance(candidate.get("assets"), list):
                manifest = candidate
                break
        manifest_entries = {Path(item.get("file", "")).name: item for item in (manifest or {}).get("assets", [])}
        asset_hash_checks = []
        asset_duration_checks = []
        for name in audio_names:
            entry = manifest_entries.get(Path(name).name)
            if not entry:
                asset_hash_checks.append({"file": name, "ok": False, "reason": "missing manifest entry"})
                continue
            actual_hash = hashlib.sha256(archive.read(name)).hexdigest()
            asset_hash_checks.append({
                "file": name,
                "expected": entry.get("sha256"),
                "actual": actual_hash,
                "ok": actual_hash == entry.get("sha256"),
            })
            try:
                start = float(entry["start"])
                end = float(entry["end"])
                encoded = float(entry["encoded_duration"])
                asset_duration_checks.append({
                    "file": name,
                    "cut_duration_seconds": round(end - start, 6),
                    "encoded_duration_seconds": encoded,
                    "ok": abs((end - start) - encoded) <= 0.002,
                })
            except Exception:
                asset_duration_checks.append({"file": name, "ok": False, "reason": "invalid time range"})
        sample_hash = None
        sample_hash_matches = None
        if source_audio_path is not None:
            sample_path = Path(source_audio_path)
            if not sample_path.is_file():
                raise FileNotFoundError(f"Không tìm thấy MP3 mẫu HSKK3: {sample_path}")
            sample_hash = _sha256(sample_path)
            sample_hash_matches = sample_hash == (manifest or {}).get("source_sha256")
        return {
            "path": str(source.resolve()),
            "sha256": _sha256(source),
            "zip_test": "PASS",
            "files": names,
            "audio_count": len(audio_names),
            "audio_files": audio_names,
            "readme_files": readmes,
            "manifest_files": manifests,
            "documents": documents,
            "manifest": manifest,
            "manifest_status": (manifest or {}).get("status"),
            "review_audio_files": review_names,
            "asset_hash_checks": asset_hash_checks,
            "asset_hashes_ok": bool(asset_hash_checks) and all(item["ok"] for item in asset_hash_checks),
            "asset_duration_checks": asset_duration_checks,
            "asset_durations_ok": bool(asset_duration_checks) and all(item["ok"] for item in asset_duration_checks),
            "source_audio_path": str(Path(source_audio_path).resolve()) if source_audio_path is not None else None,
            "source_audio_sha256": sample_hash,
            "source_audio_hash_matches_manifest": sample_hash_matches,
            "expected_audio_count": 19,
            "inventory_ok": len(audio_names) == 19 and len(manifest_entries) == 19 and bool(readmes) and bool(manifests),
            "required_filenames_ok": {Path(name).name for name in audio_names} == HSKK3_REQUIRED_ASSET_FILES,
            "review_asset_content_verified": False,
            "production_batch_approved": False,
        }


def inspect_hskk3_reference_documents(pdf_path: str | Path, transcript_path: str | Path) -> Dict[str, Any]:
    """Extract sample-paper and transcript text for review-only boundary checks."""
    pdf_file = Path(pdf_path)
    transcript_file = Path(transcript_path)
    if not pdf_file.is_file():
        raise FileNotFoundError(f"Không tìm thấy PDF mẫu: {pdf_file}")
    if not transcript_file.is_file():
        raise FileNotFoundError(f"Không tìm thấy transcript DOCX: {transcript_file}")
    try:
        from PyPDF2 import PdfReader
    except ImportError:
        from pypdf import PdfReader

    pdf_reader = PdfReader(str(pdf_file))
    pdf_pages = [page.extract_text() or "" for page in pdf_reader.pages]
    with zipfile.ZipFile(transcript_file) as archive:
        root = ET.fromstring(archive.read("word/document.xml"))
    word_namespace = {"w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main"}
    transcript_paragraphs = []
    for paragraph in root.findall(".//w:p", word_namespace):
        value = "".join(node.text or "" for node in paragraph.findall(".//w:t", word_namespace)).strip()
        if value:
            transcript_paragraphs.append(value)
    transcript_text = "\n".join(transcript_paragraphs)
    pdf_text = "\n".join(pdf_pages)
    return {
        "pdf_path": str(pdf_file.resolve()),
        "pdf_sha256": _sha256(pdf_file),
        "pdf_sha256_matches_reviewed": _sha256(pdf_file) == HSKK3_REVIEWED_SAMPLE_PDF_SHA256,
        "pdf_pages": len(pdf_pages),
        "pdf_text": pdf_text,
        "transcript_path": str(transcript_file.resolve()),
        "transcript_sha256": _sha256(transcript_file),
        "transcript_sha256_matches_reviewed": _sha256(transcript_file) == HSKK3_REVIEWED_TRANSCRIPT_SHA256,
        "transcript_text": transcript_text,
        "part3_question_texts_on_paper": [
            "请介绍一下你的一位家人。",
            "你喜欢做什么样的工作？为什么？",
        ],
        "part3_questions_read_in_sample_audio": False,
        "part3_audio_evidence": (
            "Local ASR on the two full transition windows and the two REVIEW clips contains only "
            "the question-number transition and three-minute preparation instructions."
        ),
    }


def hskk3_exam_output_name(exam_number: int) -> str:
    if not 1 <= int(exam_number) <= 30:
        raise ValueError("HSKK3 exam number must be between 1 and 30.")
    return f"hskk3-30-{int(exam_number):03d}.m4a"


def hskk3_answer_output_names(exam_number: int) -> Dict[str, Any]:
    if not 1 <= int(exam_number) <= 30:
        raise ValueError("HSKK3 exam number must be between 1 and 30.")
    stem = f"hskk3-30-{int(exam_number):03d}"
    return {
        "combined": f"{stem}-answer.m4a",
        "questions": [f"{stem}-q{question:02d}-answer.m4a" for question in range(1, 16)],
    }


def _speech_edges_ms(segment: AudioSegment) -> Tuple[int, int]:
    spans = detect_nonsilent(segment, min_silence_len=15, silence_thresh=-45.0, seek_step=5)
    if not spans:
        return len(segment), 0
    return int(spans[0][0]), int(len(segment) - spans[-1][1])


def _load_manifest_pcm_segments(source_audio_path: str | Path, asset_report: Dict[str, Any]) -> Dict[str, AudioSegment]:
    source_path = Path(source_audio_path)
    if (not asset_report.get("inventory_ok") or not asset_report.get("required_filenames_ok")
            or not asset_report.get("asset_hashes_ok") or not asset_report.get("asset_durations_ok")):
        raise ValueError("ZIP HSKK3 phải có đúng manifest, README, 19 audio và hash/range hợp lệ.")
    if not asset_report.get("source_audio_hash_matches_manifest"):
        raise ValueError("MP3 mẫu không khớp source_sha256 trong manifest HSKK3.")
    if not asset_report.get("source_audio_sha256") == HSKK3_REVIEWED_SOURCE_SHA256:
        raise ValueError("MP3 mẫu đã đổi so với nguồn đã audit ranh giới HSKK3.")
    if not asset_report.get("sha256") == HSKK3_REVIEWED_ASSET_ZIP_SHA256:
        raise ValueError("ZIP fixed assets đã đổi so với bộ REVIEW đã audit.")
    manifest = asset_report.get("manifest") or {}
    if manifest.get("source_sha256") != HSKK3_REVIEWED_SOURCE_SHA256:
        raise ValueError("Manifest không trỏ tới đúng MP3 HSKK3 đã audit.")
    if manifest.get("status") != "PROVISIONAL_AUDIT_ONLY_NOT_APPROVED_FOR_BATCH":
        raise ValueError(f"Trạng thái fixed assets không mong đợi: {manifest.get('status')!r}.")

    source = AudioSegment.from_file(source_path)
    segments: Dict[str, AudioSegment] = {}
    for item in manifest["assets"]:
        filename = Path(item["file"]).name
        start_ms = round(float(item["start"]) * 1000)
        end_ms = round(float(item["end"]) * 1000)
        if start_ms < 0 or end_ms <= start_ms or end_ms > len(source):
            raise ValueError(f"Manifest time range bất hợp lệ: {filename} {start_ms}–{end_ms}ms.")
        segment = source[start_ms:end_ms].set_frame_rate(48000).set_channels(1).set_sample_width(2)
        if abs(len(segment) - round(float(item["encoded_duration"]) * 1000)) > 3:
            raise ValueError(f"Thời lượng PCM từ MP3 không khớp manifest: {filename}.")
        segments[filename] = segment

    if set(HSKK3_REVIEWED_REVIEW_ASSETS) - set(segments):
        raise ValueError("Thiếu một trong hai asset REVIEW bắt buộc.")
    return segments


def build_hskk3_exam(
    validation_report: Dict[str, Any],
    asset_zip_path: str | Path,
    source_audio_path: str | Path,
    output_root: str | Path,
    *,
    engine: str,
    voice: str,
    speed: float = 1.0,
    bitrate_kbps: int = 96,
    tts_provider,
    cache_dir: Optional[str | Path] = None,
    read_part3_questions: Optional[bool] = False,
    exam_number: int = 1,
    progress_callback=None,
) -> Dict[str, Any]:
    """Build one review-only HSKK3 exam from the verified source cuts and PCM TTS."""
    if validation_report.get("status") != "PASS":
        raise ValueError("Không build HSKK3 khi validation hai Excel chưa PASS.")
    if int(exam_number) != 1:
        raise ValueError("Giai đoạn hiện tại chỉ cho xuất golden HSKK3-30-001.")
    if not 0.5 <= float(speed) <= 2.0:
        raise ValueError("Tốc độ đọc HSKK3 phải trong khoảng 0.50x–2.00x.")
    if read_part3_questions is not False:
        raise ValueError("MP3 mẫu đã xác nhận Q14–15 không được đọc thành tiếng; đặt read_part3_questions=False.")
    selected = next((item for item in validation_report["exams"].values() if item["exam_number"] == 1), None)
    if not selected or len(selected.get("questions", [])) != 15:
        raise ValueError("Không tìm thấy đủ 15 câu cho HSKK3-30-001.")
    if any(item.get("exam_text") for item in selected["questions"][8:13]):
        raise ValueError("Q9–13 Excel đề phải để trống; không được TTS mô tả tranh hoặc đáp án.")

    asset_report = inspect_hskk3_asset_archive(asset_zip_path, source_audio_path)
    pcm_assets = _load_manifest_pcm_segments(source_audio_path, asset_report)
    output_dir = Path(output_root) / "exam"
    output_path = output_dir / hskk3_exam_output_name(1)
    output_dir.mkdir(parents=True, exist_ok=True)
    if output_path.exists():
        raise FileExistsError(f"Không ghi đè golden đang có: {output_path}")

    cache_path = Path(cache_dir) if cache_dir else BASE_DIR / "AppData" / "hskk30_audio_cache" / "pcm" / HSKK3_AUDIO_PROFILE["cache_profile"]
    counters = {"cache_reused": 0, "cache_new": 0, "unique_tts_requests": 0}
    question_audio: Dict[int, AudioSegment] = {}
    for item in selected["questions"][:8]:
        number = int(item["question_number"])
        segment, reused, _ = _synthesize_pcm_segment(
            item["exam_text"], engine=engine, voice=voice, speed=float(speed),
            cache_dir=cache_path, tts_provider=tts_provider,
        )
        question_audio[number] = segment
        if reused:
            counters["cache_reused"] += 1
        else:
            counters["cache_new"] += 1
            counters["unique_tts_requests"] += 1
        if progress_callback:
            progress_callback(f"Exam Q{number:02d}/08: {'cache' if reused else 'TTS + PCM cache'}")

    pieces: List[AudioSegment] = []
    timeline: List[Dict[str, Any]] = []
    cursor_ms = 0
    last_segment: Optional[AudioSegment] = None
    warnings: List[str] = []

    def append_segment(name: str, segment: AudioSegment, kind: str, **details: Any) -> None:
        nonlocal cursor_ms, last_segment
        segment = _as_hskk_pcm(segment)
        start_ms = cursor_ms
        pieces.append(segment)
        cursor_ms += len(segment)
        timeline.append({
            "kind": kind,
            "name": name,
            "start_ms": start_ms,
            "end_ms": cursor_ms,
            "duration_ms": len(segment),
            **details,
        })
        last_segment = segment

    def append_target_gap(next_segment: AudioSegment, target_ms: int, label: str) -> None:
        nonlocal cursor_ms
        next_segment = _as_hskk_pcm(next_segment)
        trailing_ms = _speech_edges_ms(last_segment)[1] if last_segment is not None else 0
        leading_ms = _speech_edges_ms(next_segment)[0]
        natural_ms = trailing_ms + leading_ms
        inserted_ms = max(0, int(target_ms) - natural_ms)
        actual_ms = natural_ms + inserted_ms
        if actual_ms > int(target_ms):
            warnings.append(f"{label}: silence tự nhiên {actual_ms}ms vượt mục tiêu {target_ms}ms; giữ nguyên, không trim asset.")
        if inserted_ms:
            start_ms = cursor_ms
            silence = AudioSegment.silent(duration=inserted_ms, frame_rate=48000).set_channels(1).set_sample_width(2)
            pieces.append(silence)
            cursor_ms += inserted_ms
            timeline.append({
                "kind": "silence_gap",
                "name": label,
                "start_ms": start_ms,
                "end_ms": cursor_ms,
                "duration_ms": inserted_ms,
                "target_active_to_active_ms": int(target_ms),
                "natural_tail_ms": trailing_ms,
                "natural_head_ms": leading_ms,
                "actual_active_to_active_ms": actual_ms,
            })
        else:
            timeline.append({
                "kind": "silence_gap",
                "name": label,
                "start_ms": cursor_ms,
                "end_ms": cursor_ms,
                "duration_ms": 0,
                "target_active_to_active_ms": int(target_ms),
                "natural_tail_ms": trailing_ms,
                "natural_head_ms": leading_ms,
                "actual_active_to_active_ms": actual_ms,
            })

    def add_fixed(filename: str) -> None:
        if filename not in pcm_assets:
            raise ValueError(f"Thiếu fixed asset trong manifest: {filename}")
        manifest_item = next(
            item for item in asset_report["manifest"]["assets"]
            if Path(item["file"]).name == filename
        )
        append_segment(
            filename,
            pcm_assets[filename],
            "fixed_asset_pcm_from_source_mp3",
            source_cut={"start": manifest_item["start"], "end": manifest_item["end"]},
        )

    def append_natural_gap(next_segment: AudioSegment, label: str) -> None:
        next_segment = _as_hskk_pcm(next_segment)
        trailing_ms = _speech_edges_ms(last_segment)[1] if last_segment is not None else 0
        leading_ms = _speech_edges_ms(next_segment)[0]
        timeline.append({
            "kind": "natural_gap",
            "name": label,
            "start_ms": cursor_ms,
            "end_ms": cursor_ms,
            "duration_ms": 0,
            "inserted_silence_ms": 0,
            "natural_tail_ms": trailing_ms,
            "natural_head_ms": leading_ms,
            "actual_active_to_active_ms": trailing_ms + leading_ms,
            "target_active_to_active_ms": None,
        })

    def add_question(number: int) -> None:
        append_segment(
            f"Q{number:02d} stimulus TTS", question_audio[number], "tts_question",
            question_number=number,
            source_text=selected["questions"][number - 1]["exam_text"],
            voice=voice,
            speed=float(speed),
            engine=engine,
            cache_profile=HSKK3_AUDIO_PROFILE["cache_profile"],
        )

    def pace_question(number: int, next_filename: str, repeat_ms: int) -> None:
        beep = pcm_assets["hskk3_repeat_beep.m4a"]
        append_target_gap(beep, HSKK3_AUDIO_PROFILE["speech_to_beep_ms"], f"Q{number:02d}: speech end to original beep")
        add_fixed("hskk3_repeat_beep.m4a")
        append_target_gap(pcm_assets[next_filename], repeat_ms, f"Q{number:02d}: repeat time to next cue")

    add_fixed("hskk3_intro_part1_q1.m4a")
    if progress_callback:
        progress_callback("Đã nạp intro, ba câu xác nhận thí sinh và hướng dẫn Part 1.")
    append_natural_gap(question_audio[1], "Q01 number to stimulus")
    add_question(1)
    pace_question(1, "hskk3_q2_number.m4a", HSKK3_AUDIO_PROFILE["repeat_pause_ms"])

    for number in range(2, 9):
        number_asset = f"hskk3_q{number}_number.m4a"
        add_fixed(number_asset)
        append_natural_gap(question_audio[number], f"Q{number:02d} number to stimulus")
        add_question(number)
        next_asset = f"hskk3_q{number + 1}_number.m4a" if number < 8 else "hskk3_part2_q9_start.m4a"
        pause_ms = HSKK3_AUDIO_PROFILE["repeat_pause_ms"] if number < 8 else HSKK3_AUDIO_PROFILE["repeat_pause_q8_ms"]
        pace_question(number, next_asset, pause_ms)

    # The Q1 number is spoken inside the intro asset, so its number-to-stimulus
    # spacing is measured from that asset's final speech edge, not reinserted.
    part2_blocks = [
        "hskk3_part2_q9_start.m4a",
        "hskk3_q9_end_q10_start.m4a",
        "hskk3_q10_end_q11_start.m4a",
        "hskk3_q11_end_q12_start.m4a",
        "hskk3_q12_end_q13_start.m4a",
        "hskk3_q13_end_q14_prepare_REVIEW.m4a",
    ]
    add_fixed(part2_blocks[0])
    for index, filename in enumerate(part2_blocks[1:], start=1):
        append_target_gap(pcm_assets[filename], HSKK3_AUDIO_PROFILE["part2_answer_pause_ms"], f"Part 2 answer window {index}/5")
        add_fixed(filename)

    append_target_gap(pcm_assets["hskk3_q14_answer_start.m4a"], HSKK3_AUDIO_PROFILE["part3_q14_prepare_ms"], "Q14 preparation")
    add_fixed("hskk3_q14_answer_start.m4a")
    append_target_gap(pcm_assets["hskk3_q14_end_q15_prepare_REVIEW.m4a"], HSKK3_AUDIO_PROFILE["part3_q14_answer_ms"], "Q14 answer window")
    add_fixed("hskk3_q14_end_q15_prepare_REVIEW.m4a")
    append_target_gap(pcm_assets["hskk3_q15_answer_start.m4a"], HSKK3_AUDIO_PROFILE["part3_q15_prepare_ms"], "Q15 preparation")
    add_fixed("hskk3_q15_answer_start.m4a")
    append_target_gap(pcm_assets["hskk3_q15_end_outro.m4a"], HSKK3_AUDIO_PROFILE["part3_q15_answer_ms"], "Q15 answer window")
    add_fixed("hskk3_q15_end_outro.m4a")

    combined_audio = sum(pieces, AudioSegment.silent(duration=0, frame_rate=48000).set_channels(1).set_sample_width(2))
    fixed_order = [
        item["name"] for item in timeline
        if item["kind"] == "fixed_asset_pcm_from_source_mp3"
    ]
    expected_fixed_order = [
        "hskk3_intro_part1_q1.m4a",
        "hskk3_repeat_beep.m4a",
        "hskk3_q2_number.m4a", "hskk3_repeat_beep.m4a",
        "hskk3_q3_number.m4a", "hskk3_repeat_beep.m4a",
        "hskk3_q4_number.m4a", "hskk3_repeat_beep.m4a",
        "hskk3_q5_number.m4a", "hskk3_repeat_beep.m4a",
        "hskk3_q6_number.m4a", "hskk3_repeat_beep.m4a",
        "hskk3_q7_number.m4a", "hskk3_repeat_beep.m4a",
        "hskk3_q8_number.m4a", "hskk3_repeat_beep.m4a",
        "hskk3_part2_q9_start.m4a",
        "hskk3_q9_end_q10_start.m4a",
        "hskk3_q10_end_q11_start.m4a",
        "hskk3_q11_end_q12_start.m4a",
        "hskk3_q12_end_q13_start.m4a",
        "hskk3_q13_end_q14_prepare_REVIEW.m4a",
        "hskk3_q14_answer_start.m4a",
        "hskk3_q14_end_q15_prepare_REVIEW.m4a",
        "hskk3_q15_answer_start.m4a",
        "hskk3_q15_end_outro.m4a",
    ]
    tts_items = [item for item in timeline if item["kind"] == "tts_question"]
    gap_items = [item for item in timeline if item["kind"] == "silence_gap"]
    natural_gaps = [item for item in timeline if item["kind"] == "natural_gap"]
    qa_checks = {
        "fixed_asset_order_ok": fixed_order == expected_fixed_order,
        "candidate_check_intro_count": 3,
        "candidate_check_intro_asr": HSKK3_INTRO_ASR_TEXT,
        "spoken_exam_questions_q1_q8": len(tts_items),
        "original_beeps": fixed_order.count("hskk3_repeat_beep.m4a"),
        "speech_to_beep_gaps": sum(item["name"].endswith("speech end to original beep") for item in gap_items),
        "repeat_intervals_q1_q8": sum(item["name"].endswith("repeat time to next cue") for item in gap_items),
        "question_number_to_content_gaps": sum(item["name"].endswith("number to stimulus") for item in natural_gaps),
        "part2_response_windows": sum(item["name"].startswith("Part 2 answer window") for item in gap_items),
        "part3_prepare_windows": sum("preparation" in item["name"].casefold() for item in gap_items),
        "part3_response_windows": sum(
            item["name"] in {"Q14 answer window", "Q15 answer window"}
            for item in gap_items
        ),
        "intro_count": fixed_order.count("hskk3_intro_part1_q1.m4a"),
        "outro_count": fixed_order.count("hskk3_q15_end_outro.m4a"),
        "spoken_q9_q15": sum(int(item.get("question_number", 0)) >= 9 for item in tts_items),
        "read_part3_questions": False,
    }
    if (
        not qa_checks["fixed_asset_order_ok"]
        or qa_checks["spoken_exam_questions_q1_q8"] != 8
        or qa_checks["original_beeps"] != 8
        or qa_checks["speech_to_beep_gaps"] != 8
        or qa_checks["repeat_intervals_q1_q8"] != 8
        or qa_checks["question_number_to_content_gaps"] != 8
        or qa_checks["part2_response_windows"] != 5
        or qa_checks["part3_prepare_windows"] != 2
        or qa_checks["part3_response_windows"] != 2
        or qa_checks["intro_count"] != 1
        or qa_checks["outro_count"] != 1
        or qa_checks["spoken_q9_q15"] != 0
    ):
        raise RuntimeError(f"Golden timeline QA không PASS: {qa_checks}")
    output_metadata = _export_hskk_m4a(combined_audio, output_path, bitrate_kbps)
    join_quality = _audit_audio_joins(
        output_path, timeline, output_metadata["encoder_edge_guard_ms"]
    )
    if join_quality["clipping_detected"] or not join_quality["join_discontinuities_pass"]:
        raise RuntimeError(f"Final M4A clipping/join QA failed: {join_quality}")
    return {
        "status": "GOLDEN_EXPERIMENTAL",
        "exam": "HSKK3-01",
        "exam_number": 1,
        "output_file": str(output_path),
        "output_metadata": output_metadata,
        "timeline": timeline,
        "qa_checks": qa_checks,
        "join_quality": join_quality,
        "duration_ms": len(combined_audio),
        "source_excel_sha256": validation_report["exam_source"]["sha256"],
        "answer_excel_sha256": validation_report["answer_source"]["sha256"],
        "source_audio_sha256": asset_report["source_audio_sha256"],
        "asset_zip_sha256": asset_report["sha256"],
        "asset_review_asr": HSKK3_REVIEWED_REVIEW_ASSETS,
        "asset_review_method": HSKK3_REVIEW_METHOD,
        "fixed_asset_boundary_status": "ASR_ONLY_USER_LISTEN_REQUIRED",
        "production_gate_reason": (
            "The two REVIEW clips contain no recognized sample question, but all cut boundaries still require "
            "user listening approval before any production batch."
        ),
        "fixed_asset_review_sources": asset_report["documents"],
        "fixed_asset_manifest_status": asset_report["manifest_status"],
        "fixed_asset_hashes_ok": asset_report["asset_hashes_ok"],
        "fixed_asset_durations_ok": asset_report["asset_durations_ok"],
        "part3_questions_read": False,
        "read_part3_questions": read_part3_questions,
        "engine": engine,
        "voice": voice,
        "speed": float(speed),
        "bitrate_kbps": int(bitrate_kbps),
        "sample_rate_hz": 48000,
        "channels": 1,
        "tts_cache_profile": HSKK3_AUDIO_PROFILE["cache_profile"],
        "cache_new_segments": counters["cache_new"],
        "cache_reused_segments": counters["cache_reused"],
        "unique_tts_requests": counters["unique_tts_requests"],
        "fixed_asset_input": "Decoded from exact MP3 source cuts; ZIP 32 kbps AAC not used as PCM cache.",
        "added_common_hsk_music": False,
        "production_batch_approved": False,
        "warnings": warnings,
    }


def _pcm_cache_key(text: str, *, engine: str, voice: str, speed: float) -> str:
    key_data = "\n".join((
        HSKK3_AUDIO_PROFILE["cache_profile"],
        _plain(engine).casefold(),
        _plain(voice),
        f"{float(speed):.3f}",
        _cell_key(text),
    ))
    return hashlib.sha256(key_data.encode("utf-8")).hexdigest()


def _as_hskk_pcm(segment: AudioSegment) -> AudioSegment:
    if not isinstance(segment, AudioSegment):
        raise TypeError("HSKK3 TTS provider phải trả về AudioSegment PCM.")
    return segment.set_frame_rate(48000).set_channels(1).set_sample_width(2)


def _synthesize_pcm_segment(
    text: str,
    *,
    engine: str,
    voice: str,
    speed: float,
    cache_dir: Path,
    tts_provider,
) -> Tuple[AudioSegment, bool, str]:
    normalized_text = _plain(text)
    if not normalized_text:
        return AudioSegment.silent(duration=0, frame_rate=48000).set_channels(1), True, ""
    cache_dir.mkdir(parents=True, exist_ok=True)
    key = _pcm_cache_key(normalized_text, engine=engine, voice=voice, speed=speed)
    wav_path = cache_dir / f"{key}.wav"
    if wav_path.is_file() and wav_path.stat().st_size:
        return _as_hskk_pcm(AudioSegment.from_file(wav_path)), True, str(wav_path)
    segment = tts_provider(
        normalized_text,
        engine=engine,
        voice=voice,
        speed=float(speed),
        sample_rate_hz=48000,
        sample_format="LINEAR16",
    )
    segment = _as_hskk_pcm(segment)
    temp_wav = wav_path.with_suffix(".tmp.wav")
    try:
        segment.export(temp_wav, format="wav")
        os.replace(temp_wav, wav_path)
    finally:
        temp_wav.unlink(missing_ok=True)
    return segment, False, str(wav_path)


def _ffprobe_audio(path: Path) -> Dict[str, Any]:
    ffprobe = shutil.which("ffprobe")
    if not ffprobe:
        raise RuntimeError("Không tìm thấy ffprobe để xác minh M4A HSKK3.")
    result = subprocess.run(
        [ffprobe, "-v", "error", "-select_streams", "a:0", "-show_entries",
         "stream=codec_name,profile,bit_rate,sample_rate,channels,duration", "-of", "json", str(path)],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, check=False,
    )
    if result.returncode:
        raise RuntimeError(f"ffprobe lỗi: {result.stderr.strip()}")
    payload = json.loads(result.stdout or "{}")
    streams = payload.get("streams") or []
    if not streams:
        raise RuntimeError("M4A xuất ra không có audio stream.")
    stream = streams[0]
    return {
        "codec": stream.get("codec_name"),
        "profile": stream.get("profile"),
        "bitrate_bps": int(stream["bit_rate"]) if str(stream.get("bit_rate", "")).isdigit() else None,
        "sample_rate_hz": int(stream.get("sample_rate", 0) or 0),
        "channels": int(stream.get("channels", 0) or 0),
        "duration_seconds": float(stream.get("duration", 0) or 0),
    }


def _aac_encoder_config() -> Tuple[str, List[str], str]:
    """Prefer platform AAC CBR when available; otherwise use FFmpeg native AAC."""
    global _AAC_ENCODER_CACHE
    if _AAC_ENCODER_CACHE is not None:
        return _AAC_ENCODER_CACHE
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        raise RuntimeError("Không tìm thấy ffmpeg để encode AAC HSKK3.")
    result = subprocess.run(
        [ffmpeg, "-hide_banner", "-encoders"],
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, check=False,
    )
    if result.returncode == 0 and re.search(r"\baac_at\b", result.stdout):
        _AAC_ENCODER_CACHE = ("aac_at", ["-aac_at_mode", "cbr"], "AudioToolbox CBR")
    else:
        _AAC_ENCODER_CACHE = ("aac", ["-profile:a", "aac_low"], "FFmpeg native AAC target bitrate")
    return _AAC_ENCODER_CACHE


def _measure_audio_levels(path: Path) -> Dict[str, Optional[float]]:
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        return {"true_peak_dbtp": None, "integrated_loudness_lufs": None, "loudness_range_lu": None}
    result = subprocess.run(
        [ffmpeg, "-hide_banner", "-nostats", "-i", str(path), "-af", "ebur128=peak=true",
         "-f", "null", "-"],
        stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True, check=False,
    )
    text = result.stderr or ""
    summary = text.split("Summary:", 1)[-1]

    def measured(pattern: str) -> Optional[float]:
        match = re.search(pattern, summary, flags=re.IGNORECASE | re.MULTILINE)
        return float(match.group(1)) if match else None

    return {
        "true_peak_dbtp": measured(r"^\s*Peak:\s*(-?\d+(?:\.\d+)?)\s*dBFS"),
        "integrated_loudness_lufs": measured(r"^\s*I:\s*(-?\d+(?:\.\d+)?)\s*LUFS"),
        "loudness_range_lu": measured(r"^\s*LRA:\s*(-?\d+(?:\.\d+)?)\s*LU"),
    }


def _audit_audio_joins(path: Path, timeline: List[Dict[str, Any]], edge_guard_ms: int) -> Dict[str, Any]:
    """Decode final AAC and inspect each fixed/TTS/silence boundary."""
    decoded = AudioSegment.from_file(path)
    if decoded.frame_rate != 48000 or decoded.channels != 1:
        raise RuntimeError("Join audit expects final 48 kHz mono AAC decode.")
    samples = decoded.get_array_of_samples()
    segments = [
        item for item in timeline
        if item.get("duration_ms", 0) > 0
        and item.get("kind") in {
            "fixed_asset_pcm_from_source_mp3",
            "tts_question",
            "silence_gap",
        }
    ]
    boundaries = []
    for left, right in zip(segments, segments[1:]):
        boundary_ms = int(right["start_ms"])
        sample_index = round((int(edge_guard_ms) + boundary_ms) * decoded.frame_rate / 1000)
        if sample_index <= 0 or sample_index >= len(samples):
            continue
        jump = abs(int(samples[sample_index]) - int(samples[sample_index - 1]))
        jump_dbfs = 20 * math.log10(max(jump, 1) / 32768)
        window = decoded[max(0, edge_guard_ms + boundary_ms - 10):edge_guard_ms + boundary_ms + 10]
        boundaries.append({
            "at_ms": boundary_ms,
            "from": left["name"],
            "to": right["name"],
            "sample_step_dbfs": round(jump_dbfs, 2),
            "local_20ms_peak_dbfs": round(window.max_dBFS, 2) if window.rms else None,
        })
    worst = max(boundaries, key=lambda item: item["sample_step_dbfs"], default=None)
    max_step = worst["sample_step_dbfs"] if worst else None
    return {
        "decoded_sample_rate_hz": decoded.frame_rate,
        "decoded_channels": decoded.channels,
        "max_sample_peak_dbfs": round(decoded.max_dBFS, 2),
        "clipping_detected": decoded.max_dBFS >= 0,
        "join_count": len(boundaries),
        "max_sample_step_dbfs": max_step,
        "worst_boundary": worst,
        "boundaries": boundaries,
        "join_discontinuities_pass": max_step is None or max_step <= -12.0,
    }


def _export_hskk_m4a(audio: AudioSegment, output_path: Path, bitrate_kbps: int) -> Dict[str, Any]:
    if bitrate_kbps < 32 or bitrate_kbps > 320:
        raise ValueError("Bitrate AAC phải nằm trong khoảng 32–320 kbps.")
    output_path.parent.mkdir(parents=True, exist_ok=True)
    source_duration_ms = len(audio)
    pcm = _as_hskk_pcm(audio)
    # Keep a full AAC frame of edge room so encoder priming/padding cannot create
    # a decoder pop at the beginning or end of a standalone file.
    edge_guard_ms = 25
    guard = AudioSegment.silent(duration=edge_guard_ms, frame_rate=48000).set_channels(1).set_sample_width(2)
    pcm = guard + pcm + guard
    # Leave headroom for AAC inter-sample peaks, then verify the encoded file.
    pcm_peak_ceiling_dbfs = -2.0
    if pcm.max_dBFS > pcm_peak_ceiling_dbfs:
        pcm = pcm.apply_gain(pcm_peak_ceiling_dbfs - pcm.max_dBFS)
    fd, temp_name = tempfile.mkstemp(prefix=f".{output_path.stem}-", suffix=".tmp.m4a", dir=output_path.parent)
    os.close(fd)
    temp_path = Path(temp_name)
    try:
        encoder_name, encoder_parameters, encoder_label = _aac_encoder_config()
        parameters = [
            "-ar", "48000", "-ac", "1",
            *encoder_parameters,
            "-map_metadata", "-1", "-movflags", "+faststart",
        ]
        encoded = pcm.export(
            str(temp_path), format="mp4", codec=encoder_name, bitrate=f"{int(bitrate_kbps)}k",
            parameters=parameters,
        )
        encoded.close()
        metadata = _ffprobe_audio(temp_path)
        if metadata["codec"] != "aac" or metadata["sample_rate_hz"] != 48000 or metadata["channels"] != 1:
            raise RuntimeError(f"Thông số M4A HSKK3 không đúng: {metadata}")
        if metadata["profile"] and str(metadata["profile"]).casefold() not in {"lc", "aac lc"}:
            raise RuntimeError(f"Profile AAC không phải AAC-LC: {metadata['profile']}")
        levels = _measure_audio_levels(temp_path)
        peak = levels["true_peak_dbtp"]
        if peak is None:
            raise RuntimeError("Không đo được true peak bằng FFmpeg ebur128.")
        if peak > -1.0:
            raise RuntimeError(f"True peak {peak:.2f} dBTP vượt mục tiêu -1 dBTP.")
        os.replace(temp_path, output_path)
        return {
            **metadata,
            **levels,
            "encoder": encoder_name,
            "encoder_mode": encoder_label,
            "target_bitrate_kbps": int(bitrate_kbps),
            "average_bitrate_kbps": round((metadata["bitrate_bps"] or 0) / 1000, 2),
            "bytes": output_path.stat().st_size,
            "duration_ms": len(pcm),
            "source_pcm_duration_ms": source_duration_ms,
            "encoder_edge_guard_ms": edge_guard_ms,
            "pcm_peak_ceiling_dbfs": pcm_peak_ceiling_dbfs,
            "output_file": str(output_path),
        }
    except Exception:
        temp_path.unlink(missing_ok=True)
        raise


def build_hskk3_answers(
    validation_report: Dict[str, Any],
    output_root: str | Path,
    *,
    engine: str,
    voice: str,
    speed: float = 1.0,
    bitrate_kbps: int = 96,
    tts_provider,
    cache_dir: Optional[str | Path] = None,
    export_combined: bool = True,
    export_questions: bool = True,
    exam_number: Optional[int] = None,
    progress_callback=None,
) -> Dict[str, Any]:
    """Build one exam's answer audio from paired Excel data, using PCM cache."""
    if validation_report.get("status") != "PASS":
        raise ValueError("Không xuất đáp án HSKK3 khi validation hai Excel chưa PASS.")
    if not export_combined and not export_questions:
        raise ValueError("Chọn audio đáp án tổng hợp, file từng câu hoặc cả hai.")
    if not 0.5 <= float(speed) <= 2.0:
        raise ValueError("Tốc độ đọc HSKK3 phải trong khoảng 0.50x–2.00x.")
    selected_code = validation_report.get("selected_exam")
    if exam_number is not None:
        selected_code = next((
            code for code, item in validation_report["exams"].items()
            if item["exam_number"] == int(exam_number)
        ), None)
    if not selected_code or selected_code not in validation_report.get("exams", {}):
        raise ValueError("Chưa chọn mã đề hợp lệ trong kết quả parser-only HSKK3.")
    exam_data = validation_report["exams"][selected_code]
    if exam_data["exam_number"] != 1:
        raise ValueError("Giai đoạn hiện tại chỉ cho xuất golden HSKK3-30-001.")

    output_root = Path(output_root)
    answer_dir = output_root / "answers"
    answer_dir.mkdir(parents=True, exist_ok=True)
    cache_path = Path(cache_dir) if cache_dir else BASE_DIR / "AppData" / "hskk30_audio_cache" / "pcm" / HSKK3_AUDIO_PROFILE["cache_profile"]
    counts = {"reused_segments": 0, "generated_segments": 0, "unique_tts_requests": 0}
    segments: List[AudioSegment] = []
    individual: List[Dict[str, Any]] = []
    selected_items = exam_data["questions"]
    for index, item in enumerate(selected_items):
        text = item["answer_text"]
        segment, reused, cache_file = _synthesize_pcm_segment(
            text, engine=engine, voice=voice, speed=float(speed), cache_dir=cache_path,
            tts_provider=tts_provider,
        )
        segments.append(segment)
        if text.strip():
            counts["reused_segments" if reused else "generated_segments"] += 1
            if not reused:
                counts["unique_tts_requests"] += 1
        if export_questions:
            question_path = answer_dir / hskk3_answer_output_names(exam_data["exam_number"])["questions"][index]
            individual.append({
                **_export_hskk_m4a(segment, question_path, bitrate_kbps),
                "question_number": item["question_number"],
                "cache_reused": reused,
                "cache_file": cache_file,
            })
        if progress_callback:
            progress_callback(f"Đáp án Q{index + 1:02d}/15: {'cache' if reused else 'TTS + PCM cache'}")

    combined_metadata = None
    if export_combined:
        pieces: List[AudioSegment] = []
        for index, segment in enumerate(segments):
            if index:
                pieces.append(AudioSegment.silent(duration=450, frame_rate=48000).set_channels(1).set_sample_width(2))
            pieces.append(segment)
        combined = sum(pieces, AudioSegment.silent(duration=0, frame_rate=48000).set_channels(1).set_sample_width(2))
        combined_path = answer_dir / hskk3_answer_output_names(exam_data["exam_number"])["combined"]
        combined_metadata = _export_hskk_m4a(combined, combined_path, bitrate_kbps)

    return {
        "status": "GOLDEN_EXPERIMENTAL",
        "exam": selected_code,
        "exam_number": exam_data["exam_number"],
        "output_files": [item["output_file"] for item in individual] + ([combined_metadata["output_file"]] if combined_metadata else []),
        "individual_files": individual,
        "combined_file": combined_metadata,
        "answer_count": len(selected_items),
        "engine": engine,
        "voice": voice,
        "speed": float(speed),
        "bitrate_kbps": int(bitrate_kbps),
        "sample_rate_hz": 48000,
        "channels": 1,
        "cache_profile": HSKK3_AUDIO_PROFILE["cache_profile"],
        "cache_reused_segments": counts["reused_segments"],
        "cache_generated_segments": counts["generated_segments"],
        "unique_tts_requests": counts["unique_tts_requests"],
        "exam_source_sha256": validation_report["exam_source"]["sha256"],
        "answer_source_sha256": validation_report["answer_source"]["sha256"],
        "production_batch_approved": False,
    }


def build_hskk3_golden(
    validation_report: Dict[str, Any],
    asset_zip_path: str | Path,
    source_audio_path: str | Path,
    output_parent: str | Path,
    *,
    engine: str,
    voice: str,
    speed: float = 1.0,
    bitrate_kbps: int = 96,
    tts_provider,
    cache_dir: Optional[str | Path] = None,
    export_exam: bool = True,
    export_answers: bool = True,
    read_part3_questions: Optional[bool] = False,
    sample_pdf_path: str | Path = HSKK3_DEFAULT_SAMPLE_PDF,
    sample_transcript_path: str | Path = HSKK3_DEFAULT_SAMPLE_TRANSCRIPT,
    progress_callback=None,
) -> Dict[str, Any]:
    """Atomically publish the selected golden-001 files after all exports pass."""
    if not export_exam and not export_answers:
        raise ValueError("Chọn ít nhất audio đề hoặc audio đáp án.")
    if validation_report.get("status") != "PASS":
        raise ValueError("Golden HSKK3 chỉ chạy sau khi parser-only validation PASS.")
    reference_report = inspect_hskk3_reference_documents(sample_pdf_path, sample_transcript_path)
    if not reference_report["pdf_sha256_matches_reviewed"] or not reference_report["transcript_sha256_matches_reviewed"]:
        raise ValueError("PDF mẫu hoặc transcript đã đổi so với tài liệu dùng để audit read_part3_questions.")
    if read_part3_questions is not False or reference_report["part3_questions_read_in_sample_audio"]:
        raise ValueError("Cần bằng chứng đối chiếu rõ ràng cho read_part3_questions trước golden.")
    output_parent = Path(output_parent)
    output_parent.mkdir(parents=True, exist_ok=True)
    final_dir = output_parent / "hskk3-golden-001"
    if final_dir.exists():
        raise FileExistsError(f"Không ghi đè golden đang có: {final_dir}")
    staging_dir = Path(tempfile.mkdtemp(prefix=".hskk3-001-", suffix=".tmp", dir=output_parent))
    try:
        exam_report = None
        answer_report = None
        if export_answers:
            answer_report = build_hskk3_answers(
                validation_report, staging_dir,
                engine=engine, voice=voice, speed=speed, bitrate_kbps=bitrate_kbps,
                tts_provider=tts_provider, cache_dir=cache_dir,
                export_combined=True, export_questions=True, exam_number=1,
                progress_callback=progress_callback,
            )
        if export_exam:
            exam_report = build_hskk3_exam(
                validation_report, asset_zip_path, source_audio_path, staging_dir,
                engine=engine, voice=voice, speed=speed, bitrate_kbps=bitrate_kbps,
                tts_provider=tts_provider, cache_dir=cache_dir,
                read_part3_questions=read_part3_questions, exam_number=1,
                progress_callback=progress_callback,
            )

        report: Dict[str, Any] = {
            "status": "GOLDEN_EXPERIMENTAL",
            "exam": "HSKK3-01",
            "output_root": str(final_dir.resolve()),
            "export_exam": bool(export_exam),
            "export_answers": bool(export_answers),
            "exam_audio": exam_report,
            "answer_audio": answer_report,
            "source_validation": {
                "status": validation_report["status"],
                "exam_source": validation_report["exam_source"],
                "answer_source": validation_report["answer_source"],
                "exam_count": validation_report["exam_count"],
                "exam_rows": validation_report["exam_rows"],
                "answer_rows": validation_report["answer_rows"],
                "q1_q8_match_count": validation_report["q1_q8_match_count"],
                "exam_part2_blank_cells": validation_report["exam_part2_blank_cells"],
                "answer_non_empty_content_count": validation_report["answer_non_empty_content_count"],
            },
            "reference_review": {
                key: value for key, value in reference_report.items()
                if key not in {"pdf_text", "transcript_text"}
            },
            "engine": engine,
            "voice": voice,
            "speed": float(speed),
            "bitrate_kbps": int(bitrate_kbps),
            "sample_rate_hz": 48000,
            "channels": 1,
            "aac_profile": "AAC-LC",
            "fixed_assets_source": "decoded from exact cuts in supplied original MP3; ZIP 32 kbps audio not used",
            "fixed_asset_boundary_status": (
                exam_report.get("fixed_asset_boundary_status") if exam_report
                else "REFERENCE_SOURCE_ONLY"
            ),
            "production_gate_reason": exam_report.get("production_gate_reason") if exam_report else (
                "Audio answer only; exam fixed-asset boundary approval is not represented."
            ),
            "fixed_asset_review": exam_report.get("fixed_asset_review_sources") if exam_report else None,
            "fixed_asset_manifest_status": exam_report.get("fixed_asset_manifest_status") if exam_report else None,
            "fixed_asset_hashes_ok": exam_report.get("fixed_asset_hashes_ok") if exam_report else None,
            "fixed_asset_durations_ok": exam_report.get("fixed_asset_durations_ok") if exam_report else None,
            "read_part3_questions": read_part3_questions,
            "production_batch_approved": False,
        }
        report_path = staging_dir / "golden-report.json"
        report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        if exam_report is not None:
            import csv

            with (staging_dir / "exam-timeline.csv").open("w", encoding="utf-8-sig", newline="") as timeline_file:
                fieldnames = ("kind", "name", "start_ms", "end_ms", "duration_ms", "details")
                writer = csv.DictWriter(timeline_file, fieldnames=fieldnames)
                writer.writeheader()
                for item in exam_report["timeline"]:
                    writer.writerow({
                        **{key: item.get(key, "") for key in fieldnames[:-1]},
                        "details": json.dumps(
                            {key: value for key, value in item.items() if key not in fieldnames[:-1]},
                            ensure_ascii=False,
                        ),
                    })
        os.replace(staging_dir, final_dir)

        def remap_outputs(value: Any) -> Any:
            if isinstance(value, dict):
                return {key: remap_outputs(item) for key, item in value.items()}
            if isinstance(value, list):
                return [remap_outputs(item) for item in value]
            if isinstance(value, str) and value.startswith(str(staging_dir)):
                return str(final_dir) + value[len(str(staging_dir)):]
            return value

        report = remap_outputs(report)
        (final_dir / "golden-report.json").write_text(
            json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        report["report_file"] = str(final_dir / "golden-report.json")
        if progress_callback:
            progress_callback(f"Golden 001 hoàn tất: {final_dir}")
        return report
    except Exception:
        shutil.rmtree(staging_dir, ignore_errors=True)
        raise


def format_hskk3_validation_report(report: Dict[str, Any]) -> str:
    lines = [
        f"HSKK 3.0 source validation: {report.get('status', 'UNKNOWN')}",
        f"Exam rows: {report.get('exam_rows', 0)} / 450",
        f"Answer rows: {report.get('answer_rows', 0)} / 450",
        f"Exams: {report.get('exam_count', 0)} / 30",
        f"Q1–8 paired text: {report.get('q1_q8_match_count', 0)} / 240",
        f"Exam Q9–13 blank cells: {report.get('exam_part2_blank_cells', 0)} / 150",
        f"Non-empty answer content: {report.get('answer_non_empty_content_count', 0)} / 450",
        f"TTS requests: {report.get('tts_calls', 0)} (parser-only)",
    ]
    for name in ("exam_source", "answer_source"):
        source = report.get(name, {})
        lines.append(f"{name}: {source.get('path', '—')} · SHA256 {source.get('sha256', '—')}")
    if report.get("issues"):
        lines.append("Issues:")
        lines.extend(f"- {issue}" for issue in report["issues"])
    selected = report.get("selected_exam_data")
    if selected:
        lines.extend(["", f"Preview {report.get('selected_exam')}: {selected['output_stem']}"])
        for item in selected["questions"]:
            lines.append(
                f"Q{item['question_number']:02d} · Part {item['part']} · "
                f"exam={item['exam_text'] or '[trống]'} · answer={item['answer_text'] or '[trống]'}"
            )
    return "\n".join(lines)
