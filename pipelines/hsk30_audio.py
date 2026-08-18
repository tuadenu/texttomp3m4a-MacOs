from __future__ import annotations

import io
import hashlib
import json
import math
import os
import re
import shutil
import subprocess
import zipfile
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Dict, Iterable, List, Optional, Tuple
import unicodedata
import tempfile

from pydub import AudioSegment
from pydub.generators import Sine

try:
    from gtts import gTTS
except Exception:  # pragma: no cover - optional dependency at runtime
    gTTS = None

try:
    import boto3
except Exception:  # pragma: no cover - optional dependency at runtime
    boto3 = None

try:
    from google.cloud import texttospeech
except Exception:  # pragma: no cover - optional dependency at runtime
    texttospeech = None

from services.tts_service import load_audio_segment_from_mp3


BASE_DIR = Path(__file__).resolve().parents[1]
HSK30_AUDIO_ROOT = BASE_DIR / "assets" / "hsk30_audio"
HSK30_DEFAULT_SOURCE = BASE_DIR / "testdata" / "hsk30" / "hsk1" / "ghiam_HSK1_30.xlsx"
HSK30_COMMON_ASSET = HSK30_AUDIO_ROOT / "common" / "hsk_1_6_intro_music.m4a"
HSK30_HSK1_ASSETS = {
    "intro": HSK30_AUDIO_ROOT / "hsk1" / "hsk1_intro.m4a",
    "part1": HSK30_AUDIO_ROOT / "hsk1" / "hsk1_part1.m4a",
    "part2": HSK30_AUDIO_ROOT / "hsk1" / "hsk1_part2.m4a",
    "part3": HSK30_AUDIO_ROOT / "hsk1" / "hsk1_part3.m4a",
    "part4": HSK30_AUDIO_ROOT / "hsk1" / "hsk1_part4.m4a",
}

XML_NS = {
    "a": "http://schemas.openxmlformats.org/spreadsheetml/2006/main",
    "r": "http://schemas.openxmlformats.org/officeDocument/2006/relationships",
    "p": "http://schemas.openxmlformats.org/package/2006/relationships",
}

PART_LABELS = {
    1: "Phần 1",
    2: "Phần 2",
    3: "Phần 3",
    4: "Phần 4",
}

PART_HEADING_MAP = {
    "第一部分": 1,
    "第二部分": 2,
    "第三部分": 3,
    "第四部分": 4,
}

QUESTION_PREFIXES = {
    "女：": ("NỮ", "女"),
    "男：": ("NAM", "男"),
}


@dataclass(frozen=True)
class XlsxRow:
    row_number: int
    cells: Dict[str, str]


def _clean_text(value: object) -> str:
    if value is None:
        return ""
    text = str(value)
    text = text.replace("\u00a0", " ").replace("\u3000", " ").strip()
    text = re.sub(r"[ \t]+", " ", text)
    return text


def _col_name(cell_ref: str) -> str:
    match = re.match(r"([A-Z]+)", cell_ref or "")
    return match.group(1) if match else ""


def _read_shared_strings(zf: zipfile.ZipFile) -> List[str]:
    try:
        raw = zf.read("xl/sharedStrings.xml")
    except KeyError:
        return []

    root = ET.fromstring(raw)
    values: List[str] = []
    for item in root.findall("a:si", XML_NS):
        parts: List[str] = []
        for node in item.iterfind(".//a:t", XML_NS):
            parts.append(node.text or "")
        values.append("".join(parts))
    return values


def _sheet_target_map(zf: zipfile.ZipFile) -> Dict[str, str]:
    workbook = ET.fromstring(zf.read("xl/workbook.xml"))
    rels = ET.fromstring(zf.read("xl/_rels/workbook.xml.rels"))
    rel_targets = {rel.attrib["Id"]: rel.attrib["Target"] for rel in rels.findall("p:Relationship", XML_NS)}

    mapping: Dict[str, str] = {}
    for sheet in workbook.find("a:sheets", XML_NS).findall("a:sheet", XML_NS):  # type: ignore[union-attr]
        name = sheet.attrib.get("name", "")
        rid = sheet.attrib.get("{http://schemas.openxmlformats.org/officeDocument/2006/relationships}id", "")
        target = rel_targets.get(rid, "")
        if target:
            mapping[name] = target if target.startswith("xl/") else f"xl/{target}"
    return mapping


def _cell_value(cell: ET.Element, shared_strings: List[str]) -> str:
    cell_type = cell.attrib.get("t", "")
    value = cell.find("a:v", XML_NS)
    if cell_type == "s" and value is not None and value.text is not None:
        try:
            return shared_strings[int(value.text)]
        except Exception:
            return value.text
    if cell_type == "inlineStr":
        return "".join((node.text or "") for node in cell.iterfind(".//a:t", XML_NS))
    if value is not None and value.text is not None:
        return value.text
    is_node = cell.find("a:is", XML_NS)
    if is_node is not None:
        return "".join((node.text or "") for node in is_node.iterfind(".//a:t", XML_NS))
    return ""


def read_xlsx_rows(excel_path: str | Path, sheet_name: str = "Sheet1") -> List[XlsxRow]:
    path = Path(excel_path)
    if not path.is_file():
        raise FileNotFoundError(str(path))

    with zipfile.ZipFile(path) as zf:
        mapping = _sheet_target_map(zf)
        if sheet_name not in mapping:
            raise ValueError(f"Không tìm thấy sheet '{sheet_name}' trong workbook")
        sheet_target = mapping[sheet_name]
        if not zf.namelist() or sheet_target not in zf.namelist():
            raise ValueError(f"Không đọc được sheet target: {sheet_target}")
        shared_strings = _read_shared_strings(zf)
        root = ET.fromstring(zf.read(sheet_target))

    rows: List[XlsxRow] = []
    for row in root.findall(".//a:sheetData/a:row", XML_NS):
        row_number = int(row.attrib.get("r", "0") or 0)
        cells: Dict[str, str] = {}
        for cell in row.findall("a:c", XML_NS):
            ref = cell.attrib.get("r", "")
            column = _col_name(ref)
            if not column:
                continue
            cells[column] = _clean_text(_cell_value(cell, shared_strings))
        if any(value for value in cells.values()):
            rows.append(XlsxRow(row_number=row_number, cells=cells))
    return rows


def _normalize_exam_code(exam_code: str) -> str:
    return _clean_text(exam_code).upper()


def _parse_part_label(value: str) -> Optional[int]:
    clean = _clean_text(value)
    for prefix, part_number in PART_HEADING_MAP.items():
        if clean.startswith(prefix):
            return part_number
    match = re.search(r"Phần\s*(\d+)", clean, re.IGNORECASE)
    if match:
        try:
            part_number = int(match.group(1))
            if part_number in PART_LABELS:
                return part_number
        except Exception:
            return None
    return None


def _speaker_from_prefix(text: str) -> Tuple[str, str]:
    clean = _clean_text(text)
    for prefix, (speaker_label, speaker_token) in QUESTION_PREFIXES.items():
        if clean.startswith(prefix):
            return speaker_label, clean[len(prefix):].strip(), speaker_token
    return "NỮ", clean, "女"


def _render_segment(speaker_label: str, text: str) -> str:
    return f"{speaker_label} [{text}]"


def _format_speech_rate(rate: object) -> str:
    try:
        return f"{float(rate):.2f}x"
    except Exception:
        return "1.00x"


def _format_hsk_speed_label(profile: object, rate: object) -> str:
    try:
        numeric_rate = round(float(rate), 2)
    except Exception:
        numeric_rate = 0.90
    profile_label = _clean_text(profile) or "HSK1"
    preset_label = {
        0.60: "thử chậm",
        0.70: "thử chậm",
        0.88: "chậm hơn",
        0.90: "chuẩn",
        0.92: "nhanh hơn",
        0.97: "chậm",
        0.98: "chuẩn",
        1.00: "chuẩn",
        1.02: "nhanh hơn",
        1.05: "nhanh hơn",
    }.get(numeric_rate, "custom")
    return f"{profile_label} {preset_label} — {numeric_rate:.2f}x"


def _parse_speech_rate(rate: object, default: float = 1.0) -> float:
    if rate is None:
        return default
    if isinstance(rate, (int, float)):
        return float(rate)
    text = _clean_text(rate)
    if not text:
        return default
    text = text.lower().replace("x", "").strip()
    try:
        return float(text)
    except Exception:
        return default


def _spoken_question_number(number_text: str) -> str:
    cleaned = _clean_text(number_text)
    cleaned = _normalize_tts_text(cleaned)
    if not cleaned:
        return ""
    if cleaned[-1] in "。！？!?.，,；;:":
        return cleaned
    return f"{cleaned}。"


def _build_item_preview(item: Dict[str, object]) -> str:
    lines = [
        f"Q{item['question_number']:02d} — Part {item['part']}",
        "",
        f"Number spoken: {item.get('number_speaker_label', 'NỮ')} [{item.get('question_number_spoken', '')}]",
        "",
        "repeat1:",
    ]
    if item.get("part") == 4 and "source_h" in item and "spoken_h" in item:
        lines.insert(2, f"Source H: {item.get('source_h', '')}")
        lines.insert(3, f"Spoken H: {item.get('spoken_h', '')}")
        lines.insert(4, f"Source J: {item.get('source_j', '')}")
        lines.insert(5, f"Spoken J: {item.get('spoken_j', '')}")
        lines.insert(6, "")
    for segment in item["repeat1"]:  # type: ignore[index]
        lines.append(f"{_render_segment(segment['speaker_label'], segment['text'])}")
    lines.extend(["", "repeat2:"])
    for segment in item["repeat2"]:  # type: ignore[index]
        lines.append(f"{_render_segment(segment['speaker_label'], segment['text'])}")
    return "\n".join(lines)


def validate_hsk30_assets(asset_root: str | Path | None = None) -> Dict[str, object]:
    root = Path(asset_root) if asset_root is not None else HSK30_AUDIO_ROOT
    required = {
        "common/hsk_1_6_intro_music.m4a": root / "common" / "hsk_1_6_intro_music.m4a",
        "hsk1/hsk1_intro.m4a": root / "hsk1" / "hsk1_intro.m4a",
        "hsk1/hsk1_part1.m4a": root / "hsk1" / "hsk1_part1.m4a",
        "hsk1/hsk1_part2.m4a": root / "hsk1" / "hsk1_part2.m4a",
        "hsk1/hsk1_part3.m4a": root / "hsk1" / "hsk1_part3.m4a",
        "hsk1/hsk1_part4.m4a": root / "hsk1" / "hsk1_part4.m4a",
    }
    missing = [name for name, path in required.items() if not path.is_file()]
    present = [name for name in required if name not in missing]
    return {
        "root": str(root),
        "required": required,
        "missing": missing,
        "present": present,
        "ok": not missing,
    }


def phase1_output_filename(exam_code: str) -> str:
    normalized = _normalize_exam_code(exam_code)
    match = re.fullmatch(r"HSK1(\d{3})", normalized)
    if not match:
        raise ValueError(f"Không hỗ trợ exam code: {exam_code}")
    return f"hsk1-30-{match.group(1)}.m4a"


def parse_hsk1_phase1(
    excel_path: str | Path,
    sheet_name: str = "Sheet1",
    exam_code: str = "HSK1001",
    asset_root: str | Path | None = None,
    lang_state: Optional[Dict[str, str]] = None,
) -> Dict[str, object]:
    rows = read_xlsx_rows(excel_path, sheet_name=sheet_name)
    normalized_exam = _normalize_exam_code(exam_code)
    question_rows: List[XlsxRow] = []
    fixed_rows: List[int] = []
    invalid_rows: List[str] = []
    detected_exam = None
    pending_fixed_rows: List[int] = []
    in_exam_block = False

    for row in rows:
        exam_value = _clean_text(row.cells.get("A", ""))
        part_heading = _parse_part_label(row.cells.get("H", ""))

        if row.row_number == 1 or exam_value == "Mã đề":
            continue

        if in_exam_block and len(question_rows) >= 20:
            break

        if not exam_value and part_heading is not None:
            if in_exam_block:
                fixed_rows.append(row.row_number)
            else:
                pending_fixed_rows.append(row.row_number)
            continue

        if not exam_value:
            continue

        if not re.fullmatch(r"HSK\d{4}", exam_value):
            invalid_rows.append(f"Dòng {row.row_number}: mã đề không hợp lệ '{exam_value}'")
            continue

        if exam_value == normalized_exam:
            detected_exam = detected_exam or exam_value
            if not in_exam_block:
                fixed_rows.extend(pending_fixed_rows)
                pending_fixed_rows = []
                in_exam_block = True
            question_rows.append(row)
            continue

        if in_exam_block:
            break

    if not question_rows:
        raise ValueError(f"Không tìm thấy dữ liệu cho {normalized_exam}")

    preview_items: List[Dict[str, object]] = []
    part_counts = {1: 0, 2: 0, 3: 0, 4: 0}
    hsk_speed_profile = _clean_text((lang_state or {}).get("hsk_speed_profile", "HSK1")) or "HSK1"
    speech_rate = _parse_speech_rate((lang_state or {}).get("speech_rate", 0.90), default=0.90)

    for global_index, row in enumerate(question_rows, start=1):
        part = _parse_part_label(row.cells.get("C", "")) or _parse_part_label(row.cells.get("D", ""))
        if part not in PART_LABELS:
            invalid_rows.append(f"Dòng {row.row_number}: không nhận diện được part")
            continue

        part_counts[part] += 1
        source_number = _clean_text(row.cells.get("F", ""))
        spoken_question_number = _spoken_question_number(source_number)
        h_text = _clean_text(row.cells.get("H", ""))
        j_text = _clean_text(row.cells.get("J", ""))

        if part in (1, 2):
            speaker_1 = "NỮ" if global_index % 2 else "NAM"
            speaker_2 = "NAM" if speaker_1 == "NỮ" else "NỮ"
            repeat1 = [{"speaker_label": speaker_1, "text": h_text}]
            repeat2 = [{"speaker_label": speaker_2, "text": h_text}]
        elif part == 3:
            speaker_h, spoken_h, token_h = _speaker_from_prefix(h_text)
            speaker_j, spoken_j, token_j = _speaker_from_prefix(j_text)
            repeat1 = [
                {"speaker_label": speaker_h, "text": spoken_h},
                {"speaker_label": speaker_j, "text": spoken_j},
            ]
            repeat2 = [
                {"speaker_label": speaker_h, "text": spoken_h},
                {"speaker_label": speaker_j, "text": spoken_j},
            ]
        else:
            spoken_h = _strip_speaker_prefix(h_text)
            spoken_j = _strip_speaker_prefix(j_text)
            statement_speaker_1 = "NỮ"
            question_speaker_1 = "NAM"
            statement_speaker_2 = "NAM"
            question_speaker_2 = "NỮ"
            repeat1 = [
                {"speaker_label": statement_speaker_1, "text": spoken_h},
                {"speaker_label": question_speaker_1, "text": spoken_j},
            ]
            repeat2 = [
                {"speaker_label": statement_speaker_2, "text": spoken_h},
                {"speaker_label": question_speaker_2, "text": spoken_j},
            ]

        preview_items.append(
            {
                "question_number": global_index,
                "part": part,
                "part_label": PART_LABELS[part],
                "source_row": row.row_number,
                "source_exam": normalized_exam,
                "source_number": source_number,
                "question_number_spoken": spoken_question_number,
                "number_speaker_label": "NỮ",
                "text_h": h_text,
                "text_j": j_text,
                "spoken_h": spoken_h if part == 4 else h_text,
                "spoken_j": spoken_j if part == 4 else j_text,
                "repeat1": repeat1,
                "repeat2": repeat2,
                "preview": _build_item_preview(
                    {
                        "question_number": global_index,
                        "part": part,
                        "question_number_spoken": spoken_question_number,
                        "number_speaker_label": "NỮ",
                        "source_h": h_text,
                        "source_j": j_text,
                        "spoken_h": spoken_h if part == 4 else h_text,
                        "spoken_j": spoken_j if part == 4 else j_text,
                        "repeat1": repeat1,
                        "repeat2": repeat2,
                    }
                ),
            }
        )

    asset_status = validate_hsk30_assets(asset_root=asset_root)
    lang_state = lang_state or {}
    summary = {
        "workbook": str(Path(excel_path)),
        "sheet": sheet_name,
        "detected_exam": detected_exam or normalized_exam,
        "question_count": len(preview_items),
        "part_counts": part_counts,
        "fixed_instruction_rows_ignored": fixed_rows,
        "missing_invalid_rows": invalid_rows,
        "asset_status": asset_status,
        "lang_state": {
            "engine": lang_state.get("engine", ""),
            "speed": lang_state.get("speed", ""),
            "voice": lang_state.get("voice", ""),
            "zh_male_voice": lang_state.get("zh_male_voice", ""),
            "zh_female_voice": lang_state.get("zh_female_voice", ""),
            "zh_narrator_voice": lang_state.get("zh_narrator_voice", ""),
            "hsk_speed_profile": hsk_speed_profile,
            "speech_rate": speech_rate,
            "speech_rate_label": _format_hsk_speed_label(hsk_speed_profile, speech_rate),
        },
        "output_filename": phase1_output_filename(normalized_exam),
        "tts_requests_made": 0,
        "preview_items": preview_items,
        "status": "READY_FOR_REVIEW" if not invalid_rows else "REVIEW_WITH_WARNINGS",
    }
    return summary


def format_phase1_preview_report(report: Dict[str, object]) -> str:
    lines: List[str] = []
    lines.append(f"{report.get('detected_exam', 'HSK1001')} parse summary")
    lines.append(f"Workbook: {report.get('workbook', '')}")
    lines.append(f"Sheet: {report.get('sheet', '')}")
    lines.append(f"Detected exam: {report.get('detected_exam', '')}")
    lines.append(f"Question count: {report.get('question_count', 0)}")
    part_counts = report.get("part_counts", {})
    lines.append(f"Part 1: {part_counts.get(1, 0)}")
    lines.append(f"Part 2: {part_counts.get(2, 0)}")
    lines.append(f"Part 3: {part_counts.get(3, 0)}")
    lines.append(f"Part 4: {part_counts.get(4, 0)}")
    fixed_rows = report.get("fixed_instruction_rows_ignored", [])
    lines.append(f"Fixed instruction rows ignored: {len(fixed_rows)}")
    invalid_rows = report.get("missing_invalid_rows", [])
    lines.append(f"Missing/invalid rows: {len(invalid_rows)}")
    if invalid_rows:
        for row in invalid_rows:
            lines.append(f"- {row}")
    asset_status = report.get("asset_status", {})
    lines.append("Asset status: " + ("OK" if asset_status.get("ok") else "MISSING"))
    if asset_status.get("missing"):
        lines.append("Missing assets:")
        for item in asset_status.get("missing", []):
            lines.append(f"- {item}")
    lang_state = report.get("lang_state", {})
    lines.append(f"Lang engine: {lang_state.get('engine', '')}")
    lines.append(f"Male zh voice: {lang_state.get('zh_male_voice', '')}")
    lines.append(f"Female zh voice: {lang_state.get('zh_female_voice', '')}")
    lines.append(f"Narrator zh voice: {lang_state.get('zh_narrator_voice', '')}")
    lines.append(f"HSK speed profile: {lang_state.get('hsk_speed_profile', '')}")
    lines.append(
        f"Speech rate: {lang_state.get('speech_rate_label', '') or _format_hsk_speed_label(lang_state.get('hsk_speed_profile', ''), lang_state.get('speech_rate', ''))}"
    )
    lines.append("TTS requests made: 0")
    lines.append("")

    for item in report.get("preview_items", []):
        lines.append(f"Q{item['question_number']:02d} — {PART_LABELS[item['part']]}")
        lines.append(f"Source row: {item['source_row']}")
        lines.append(f"Number spoken: {item.get('number_speaker_label', 'NỮ')} [{item.get('question_number_spoken', '')}]")
        if item.get("part") in (1, 2):
            lines.append(f"repeat1: {_render_segment(item['repeat1'][0]['speaker_label'], item['repeat1'][0]['text'])}")
            lines.append(f"repeat2: {_render_segment(item['repeat2'][0]['speaker_label'], item['repeat2'][0]['text'])}")
        else:
            if item.get("part") == 4:
                lines.append(f"Source H: {item.get('text_h', '')}")
                lines.append(f"Spoken H: {item.get('spoken_h', item.get('text_h', ''))}")
                lines.append(f"Source J: {item.get('text_j', '')}")
                lines.append(f"Spoken J: {item.get('spoken_j', item.get('text_j', ''))}")
            lines.append("repeat1:")
            for segment in item["repeat1"]:
                lines.append(f"  {_render_segment(segment['speaker_label'], segment['text'])}")
            lines.append("repeat2:")
            for segment in item["repeat2"]:
                lines.append(f"  {_render_segment(segment['speaker_label'], segment['text'])}")
        lines.append("")

    return "\n".join(lines).rstrip() + "\n"


ANSWER_SILENCE_MIN_MS = 5000
ANSWER_SILENCE_MAX_MS = 23400
PART_INTRO_SILENCE_MIN_MS = 1500
PART_INTRO_SILENCE_MAX_MS = 4000
PART_GAP_SILENCE_MIN_MS = 2000
PART_GAP_SILENCE_MAX_MS = 5000
FINAL_SILENCE_MIN_MS = 1500
FINAL_SILENCE_MAX_MS = 4000
MUSIC_TO_INTRO_MS = 1500
INTRO_TO_PART1_MS = 800
REPEAT_GAP_MS = 900
QUESTION_NUMBER_PAUSE_MS = 300
SPEAKER_GAP_MS = 1150
PART4_QUESTION_GAP_MS = 1050
REFERENCE_DURATION_MS = 712000
EXPORT_SAFETY_MS = 2000
PASS_MIN_MS = 700000
PASS_MAX_MS = 720000
WARN_MIN_MS = 690000
FINAL_BITRATE = "32k"
FINAL_SAMPLE_RATE = 48000
FINAL_CHANNELS = 1

ANSWER_SILENCE_BASE_BY_PART = {
    1: 18000,
    2: 19000,
    3: 19000,
    4: 20000,
}
ANSWER_SILENCE_SPEECH_FACTOR = 0.35


def _silent_segment(duration_ms: int) -> AudioSegment:
    return AudioSegment.silent(duration=max(int(duration_ms), 0), frame_rate=44100).set_channels(2)


def _internal_pause_segment(duration_ms: int) -> AudioSegment:
    duration = max(int(duration_ms), 0)
    if duration <= 0:
        return _silent_segment(0)
    # Use a near-silent waveform so encoded duration survives AAC muxing reliably.
    return (
        Sine(1000)
        .to_audio_segment(duration=duration)
        .apply_gain(-80)
        .set_frame_rate(44100)
        .set_channels(2)
    )


def _answer_silence_for_item(part: int, speech_ms: int) -> int:
    base_ms = ANSWER_SILENCE_BASE_BY_PART.get(int(part), 18000)
    silence_ms = base_ms - int(max(speech_ms, 0) * ANSWER_SILENCE_SPEECH_FACTOR)
    return max(ANSWER_SILENCE_MIN_MS, min(silence_ms, ANSWER_SILENCE_MAX_MS))


def _normalize_tts_text(text: str) -> str:
    cleaned = _clean_text(text)
    cleaned = unicodedata.normalize("NFKC", cleaned)
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    return cleaned


def _strip_speaker_prefix(text: str) -> str:
    cleaned = _normalize_tts_text(text)
    for prefix in ("女：", "男：", "问：", "問：", "女:", "男:", "问:", "問:"):
        if cleaned.startswith(prefix):
            cleaned = cleaned[len(prefix):].strip()
    return cleaned


def _google_tts_lang_code(lang_value: str = "zh") -> str:
    lang_clean = (lang_value or "zh").lower().strip()
    if lang_clean.startswith("vi"):
        return "vi-VN"
    if lang_clean.startswith("en"):
        return "en-US"
    if lang_clean.startswith("ja"):
        return "ja-JP"
    if lang_clean.startswith(("zh", "zh-cn", "zh_tw", "zh-hk")):
        return "cmn-CN"
    return "cmn-CN"


def _speaker_voice_name(lang_state: Dict[str, object], speaker_label: str) -> str:
    label = _clean_text(speaker_label)
    if label == "NỮ":
        return _clean_text(lang_state.get("zh_female_voice", ""))
    if label == "NAM":
        return _clean_text(lang_state.get("zh_male_voice", ""))
    return _clean_text(lang_state.get("zh_female_voice", "")) or _clean_text(lang_state.get("zh_male_voice", ""))


def _narrator_voice_name(lang_state: Dict[str, object]) -> str:
    narrator = _clean_text(lang_state.get("zh_narrator_voice", ""))
    if narrator:
        return narrator
    return _speaker_voice_name(lang_state, "NỮ")


def _make_tts_cache_dir() -> Path:
    cache_dir = BASE_DIR / "AppData" / "hsk30_audio_cache" / "phase1b"
    cache_dir.mkdir(parents=True, exist_ok=True)
    return cache_dir


def _probe_audio_duration_ms(path: str | Path) -> int:
    probe_path = Path(path)
    try:
        return len(AudioSegment.from_file(str(probe_path)))
    except Exception:
        ffmpeg_path = shutil.which("ffmpeg")
        if not ffmpeg_path:
            bundled = BASE_DIR / "ffmpeg_bin" / "bin" / "ffmpeg"
            if bundled.is_file():
                ffmpeg_path = str(bundled)
        if not ffmpeg_path:
            raise

        result = subprocess.run(
            [ffmpeg_path, "-y", "-i", str(probe_path), "-f", "wav", "-"],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=False,
        )
        if result.returncode != 0 or not result.stdout:
            stderr_text = result.stderr.decode("utf-8", errors="replace") if result.stderr else ""
            raise RuntimeError(f"Không probe được audio: {stderr_text}")

        return len(AudioSegment.from_file(io.BytesIO(result.stdout), format="wav"))


def _load_audio_segment(path: str | Path) -> AudioSegment:
    audio_path = Path(path)
    try:
        return AudioSegment.from_file(str(audio_path))
    except Exception:
        ffmpeg_path = shutil.which("ffmpeg")
        if not ffmpeg_path:
            bundled = BASE_DIR / "ffmpeg_bin" / "bin" / "ffmpeg"
            if bundled.is_file():
                ffmpeg_path = str(bundled)
        if not ffmpeg_path:
            raise

        result = subprocess.run(
            [ffmpeg_path, "-y", "-i", str(audio_path), "-f", "wav", "-"],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=False,
        )
        if result.returncode != 0 or not result.stdout:
            stderr_text = result.stderr.decode("utf-8", errors="replace") if result.stderr else ""
            raise RuntimeError(f"Không nạp được audio: {stderr_text}")
        return AudioSegment.from_file(io.BytesIO(result.stdout), format="wav")


def _tts_cache_key(engine: str, voice_name: str, speed: str, text: str, speaker_label: str) -> str:
    payload = json.dumps(
        {
            "engine": _clean_text(engine).lower(),
            "voice": _clean_text(voice_name),
            "speed": _clean_text(speed),
            "text": _normalize_tts_text(text),
            "speaker": _clean_text(speaker_label),
        },
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _allocate_weighted(extras_ms: int, weights: List[float], caps_ms: List[int]) -> List[int]:
    if extras_ms <= 0 or not weights:
        return [0 for _ in weights]
    allocations = [0 for _ in weights]
    remaining = float(extras_ms)
    active = {idx for idx, cap in enumerate(caps_ms) if cap > 0}
    while remaining > 0.5 and active:
        total_weight = sum(max(weights[idx], 0.0001) for idx in active)
        changed = False
        for idx in list(active):
            cap_left = caps_ms[idx] - allocations[idx]
            if cap_left <= 0:
                active.discard(idx)
                continue
            share = remaining * max(weights[idx], 0.0001) / total_weight
            add = min(cap_left, int(round(share)))
            if add <= 0 and cap_left > 0:
                add = 1
            add = min(add, cap_left)
            if add > 0:
                allocations[idx] += add
                remaining -= add
                changed = True
            if allocations[idx] >= caps_ms[idx]:
                active.discard(idx)
        if not changed:
            break
    if remaining > 0.5:
        for idx in range(len(allocations)):
            cap_left = caps_ms[idx] - allocations[idx]
            if cap_left <= 0:
                continue
            add = min(cap_left, int(round(remaining)))
            allocations[idx] += add
            remaining -= add
            if remaining <= 0.5:
                break
    return allocations


def _deallocate_weighted(deficit_ms: int, values_ms: List[int], mins_ms: List[int], weights: List[float]) -> int:
    if deficit_ms <= 0 or not values_ms:
        return 0
    remaining = int(deficit_ms)
    active = {idx for idx, value in enumerate(values_ms) if value > mins_ms[idx]}
    while remaining > 0 and active:
        total_weight = sum(max(weights[idx], 0.0001) for idx in active)
        changed = False
        for idx in list(active):
            slack = values_ms[idx] - mins_ms[idx]
            if slack <= 0:
                active.discard(idx)
                continue
            share = remaining * max(weights[idx], 0.0001) / total_weight
            dec = min(slack, max(1, int(round(share))))
            if dec <= 0:
                continue
            values_ms[idx] -= dec
            remaining -= dec
            changed = True
            if values_ms[idx] <= mins_ms[idx]:
                active.discard(idx)
            if remaining <= 0:
                break
        if not changed:
            break
    return remaining


def _synthesize_tts_segment(
    text: str,
    *,
    engine: str,
    speed: str,
    voice_name: str,
    speaker_label: str,
    cache_dir: Path,
    google_api_key: str = "",
    tts_provider: Optional[Callable[..., AudioSegment]] = None,
) -> Tuple[AudioSegment, bool]:
    normalized_text = _normalize_tts_text(text)
    if not normalized_text:
        return _silent_segment(0), True

    cache_dir.mkdir(parents=True, exist_ok=True)
    cache_key = _tts_cache_key(engine, voice_name, speed, normalized_text, speaker_label)
    cache_path = cache_dir / f"{cache_key}.mp3"

    if cache_path.is_file() and cache_path.stat().st_size > 0:
        return load_audio_segment_from_mp3(str(cache_path)).set_frame_rate(44100).set_channels(2), True

    if tts_provider is not None:
        segment = tts_provider(
            normalized_text,
            engine=engine,
            speed=speed,
            voice_name=voice_name,
            speaker_label=speaker_label,
        )
        if not isinstance(segment, AudioSegment):
            raise TypeError("tts_provider phải trả về AudioSegment")
        export_handle = segment.export(str(cache_path), format="mp3")
        try:
            export_handle.close()
        except Exception:
            pass
        return segment.set_frame_rate(44100).set_channels(2), False

    engine_clean = _clean_text(engine).lower()
    speech_rate = _parse_speech_rate(speed, default=1.0)
    if engine_clean == "gtts":
        if gTTS is None:
            raise RuntimeError("gTTS không khả dụng")
        temp_mp3 = cache_path.with_suffix(".tmp.mp3")
        gTTS(text=normalized_text, lang="zh", slow=speech_rate < 0.95).save(str(temp_mp3))
        shutil.move(str(temp_mp3), str(cache_path))
        return load_audio_segment_from_mp3(str(cache_path)).set_frame_rate(44100).set_channels(2), False

    if engine_clean == "polly":
        if boto3 is None:
            raise RuntimeError("boto3 không khả dụng")
        if texttospeech is None:
            pass
        temp_mp3 = cache_path.with_suffix(".tmp.mp3")
        voice_id = "Zhiyu"
        if _clean_text(speaker_label) == "NAM":
            voice_id = "Zhiyu"
        polly_client = boto3.Session(
            aws_access_key_id=os.environ.get("AWS_ACCESS_KEY_ID"),
            aws_secret_access_key=os.environ.get("AWS_SECRET_ACCESS_KEY"),
            region_name=os.environ.get("AWS_REGION") or os.environ.get("AWS_DEFAULT_REGION") or "ap-southeast-1",
        ).client("polly")
        text_type = "text"
        tts_text = normalized_text
        if abs(speech_rate - 1.0) > 0.001:
            tts_text = f"<speak><prosody rate='{int(round(speech_rate * 100))}%'>{normalized_text}</prosody></speak>"
            text_type = "ssml"
        response = polly_client.synthesize_speech(
            VoiceId=voice_id,
            OutputFormat="mp3",
            Text=tts_text,
            TextType=text_type,
        )
        with open(temp_mp3, "wb") as fp:
            fp.write(response["AudioStream"].read())
        shutil.move(str(temp_mp3), str(cache_path))
        return load_audio_segment_from_mp3(str(cache_path)).set_frame_rate(44100).set_channels(2), False

    if texttospeech is None:
        raise RuntimeError("google-cloud-texttospeech not installed")

    client_options = None
    api_key = _clean_text(google_api_key) or _clean_text(os.environ.get("GOOGLE_TTS_API_KEY", ""))
    if api_key:
        from google.api_core.client_options import ClientOptions
        client_options = ClientOptions(api_key=api_key)

    client = texttospeech.TextToSpeechClient(client_options=client_options) if client_options else texttospeech.TextToSpeechClient()
    synthesis_input = texttospeech.SynthesisInput(text=normalized_text)
    voice_kwargs = {"language_code": _google_tts_lang_code("zh")}
    selected_voice = _clean_text(voice_name)
    if selected_voice:
        voice_kwargs["name"] = selected_voice
    else:
        gender_map = {
            "NAM": texttospeech.SsmlVoiceGender.MALE,
            "NỮ": texttospeech.SsmlVoiceGender.FEMALE,
        }
        gender_enum = gender_map.get(_clean_text(speaker_label), texttospeech.SsmlVoiceGender.SSML_VOICE_GENDER_UNSPECIFIED)
        if gender_enum != texttospeech.SsmlVoiceGender.SSML_VOICE_GENDER_UNSPECIFIED:
            voice_kwargs["ssml_gender"] = gender_enum
    audio_kwargs = {"audio_encoding": texttospeech.AudioEncoding.MP3}
    if abs(speech_rate - 1.0) > 0.001:
        audio_kwargs["speaking_rate"] = speech_rate
    response = client.synthesize_speech(
        input=synthesis_input,
        voice=texttospeech.VoiceSelectionParams(**voice_kwargs),
        audio_config=texttospeech.AudioConfig(**audio_kwargs),
    )
    temp_mp3 = cache_path.with_suffix(".tmp.mp3")
    with open(temp_mp3, "wb") as fp:
        fp.write(response.audio_content)
    shutil.move(str(temp_mp3), str(cache_path))
    return load_audio_segment_from_mp3(str(cache_path)).set_frame_rate(44100).set_channels(2), False


def _dialogue_audio(
    segments: List[Dict[str, str]],
    *,
    engine: str,
    speed: str,
    lang_state: Dict[str, object],
    cache_dir: Path,
    google_api_key: str,
    tts_provider: Optional[Callable[..., AudioSegment]],
    counts: Dict[str, int],
    gap_ms: int,
    voice_override: Optional[Dict[str, str]] = None,
) -> AudioSegment:
    pieces: List[AudioSegment] = []
    for index, segment in enumerate(segments):
        voice_name = _speaker_voice_name(lang_state, segment["speaker_label"])
        if voice_override and segment["speaker_label"] in voice_override:
            voice_name = voice_override[segment["speaker_label"]]
        audio, reused = _synthesize_tts_segment(
            segment["text"],
            engine=engine,
            speed=speed,
            voice_name=voice_name,
            speaker_label=segment["speaker_label"],
            cache_dir=cache_dir,
            google_api_key=google_api_key,
            tts_provider=tts_provider,
        )
        pieces.append(audio)
        if reused:
            counts["reused_segments"] += 1
        else:
            counts["unique_tts_requests"] += 1
            counts["generated_segments"] += 1
        if index < len(segments) - 1:
            pieces.append(_internal_pause_segment(gap_ms))
            counts["internal_pauses_ms"] += gap_ms
    if not pieces:
        return _silent_segment(0)
    return sum(pieces, _silent_segment(0))


def _build_question_slot(
    item: Dict[str, object],
    *,
    engine: str,
    speed: str,
    lang_state: Dict[str, object],
    cache_dir: Path,
    google_api_key: str,
    tts_provider: Optional[Callable[..., AudioSegment]],
    counts: Dict[str, int],
) -> Tuple[AudioSegment, AudioSegment, int]:
    part = int(item["part"])
    number_text = _clean_text(item.get("question_number_spoken", ""))
    narrator_voice = _narrator_voice_name(lang_state)
    repeat1_segments = item["repeat1"]
    repeat2_segments = item["repeat2"]
    number_audio = _silent_segment(0)
    internal_pause_ms = 0
    if number_text:
        number_audio, number_reused = _synthesize_tts_segment(
            number_text,
            engine=engine,
            speed=speed,
            voice_name=narrator_voice,
            speaker_label="NARRATOR",
            cache_dir=cache_dir,
            google_api_key=google_api_key,
            tts_provider=tts_provider,
        )
        if number_reused:
            counts["reused_segments"] += 1
        else:
            counts["unique_tts_requests"] += 1
            counts["generated_segments"] += 1
        number_audio += _internal_pause_segment(QUESTION_NUMBER_PAUSE_MS)
        internal_pause_ms += QUESTION_NUMBER_PAUSE_MS
        counts["internal_pauses_ms"] += QUESTION_NUMBER_PAUSE_MS

    if part in (1, 2):
        q_num = int(item["question_number"])
        first_speaker = "NỮ" if q_num % 2 == 1 else "NAM"
        second_speaker = "NAM" if first_speaker == "NỮ" else "NỮ"
        repeat1_segments = [{"speaker_label": first_speaker, "text": item["text_h"]}]
        repeat2_segments = [{"speaker_label": second_speaker, "text": item["text_h"]}]
        repeat1 = _dialogue_audio(
            repeat1_segments,
            engine=engine,
            speed=speed,
            lang_state=lang_state,
            cache_dir=cache_dir,
            google_api_key=google_api_key,
            tts_provider=tts_provider,
            counts=counts,
            gap_ms=0,
        )
        repeat2, reused = _synthesize_tts_segment(
            item["text_h"],
            engine=engine,
            speed=speed,
            voice_name=_speaker_voice_name(lang_state, second_speaker),
            speaker_label=second_speaker,
            cache_dir=cache_dir,
            google_api_key=google_api_key,
            tts_provider=tts_provider,
        )
        if reused:
            counts["reused_segments"] += 1
        else:
            counts["unique_tts_requests"] += 1
            counts["generated_segments"] += 1
        slot = number_audio + repeat1 + _internal_pause_segment(REPEAT_GAP_MS) + repeat2
        internal_pause_ms += REPEAT_GAP_MS
        counts["internal_pauses_ms"] += REPEAT_GAP_MS
        return (
            slot.set_frame_rate(44100).set_channels(2),
            repeat1.set_frame_rate(44100).set_channels(2),
            internal_pause_ms,
        )

    if part == 3:
        internal_pause_ms += max(len(repeat1_segments) - 1, 0) * SPEAKER_GAP_MS
        internal_pause_ms += REPEAT_GAP_MS
        repeat1 = _dialogue_audio(
            repeat1_segments,
            engine=engine,
            speed=speed,
            lang_state=lang_state,
            cache_dir=cache_dir,
            google_api_key=google_api_key,
            tts_provider=tts_provider,
            counts=counts,
            gap_ms=SPEAKER_GAP_MS,
        )
        slot = repeat1 + _internal_pause_segment(REPEAT_GAP_MS) + repeat1
        slot = number_audio + slot
        counts["internal_pauses_ms"] += REPEAT_GAP_MS
        counts["reused_question_blocks"] += 1
        return (
            slot.set_frame_rate(44100).set_channels(2),
            repeat1.set_frame_rate(44100).set_channels(2),
            internal_pause_ms,
        )

    if part == 4:
        internal_pause_ms += max(len(repeat1_segments) - 1, 0) * PART4_QUESTION_GAP_MS
        internal_pause_ms += max(len(repeat2_segments) - 1, 0) * PART4_QUESTION_GAP_MS
        internal_pause_ms += REPEAT_GAP_MS
        spoken_repeat1_segments = [
            {"speaker_label": segment["speaker_label"], "text": _strip_speaker_prefix(segment["text"])}
            for segment in repeat1_segments
        ]
        spoken_repeat2_segments = [
            {"speaker_label": segment["speaker_label"], "text": _strip_speaker_prefix(segment["text"])}
            for segment in repeat2_segments
        ]
        repeat1 = _dialogue_audio(
            spoken_repeat1_segments,
            engine=engine,
            speed=speed,
            lang_state=lang_state,
            cache_dir=cache_dir,
            google_api_key=google_api_key,
            tts_provider=tts_provider,
            counts=counts,
            gap_ms=PART4_QUESTION_GAP_MS,
        )
        repeat2 = _dialogue_audio(
            spoken_repeat2_segments,
            engine=engine,
            speed=speed,
            lang_state=lang_state,
            cache_dir=cache_dir,
            google_api_key=google_api_key,
            tts_provider=tts_provider,
            counts=counts,
            gap_ms=PART4_QUESTION_GAP_MS,
        )
        slot = number_audio + repeat1 + _internal_pause_segment(REPEAT_GAP_MS) + repeat2
        counts["internal_pauses_ms"] += REPEAT_GAP_MS
        return (
            slot.set_frame_rate(44100).set_channels(2),
            repeat1.set_frame_rate(44100).set_channels(2),
            internal_pause_ms,
        )

    raise ValueError(f"Part không hỗ trợ: {part}")


def format_phase1b_build_report(report: Dict[str, object]) -> str:
    lines = [
        f"Exam: {report.get('exam', 'HSK1001')}",
        f"Output: {report.get('output_file', '')}",
        "",
        f"Music: {report.get('music_ms', 0)} ms",
        f"Fixed intro/part assets: {report.get('fixed_assets_ms', 0)} ms",
        f"Generated speech: {report.get('generated_speech_ms', 0)} ms",
        f"Internal pauses: {report.get('internal_pauses_ms', 0)} ms",
        f"Answer silence: {report.get('answer_silence_ms', 0)} ms",
        f"Part transitions: {report.get('part_transitions_ms', 0)} ms",
        f"Final silence: {report.get('final_silence_ms', 0)} ms",
        "",
        f"Unique TTS requests: {report.get('unique_tts_requests', 0)}",
        f"Reused segments: {report.get('reused_segments', 0)}",
        f"Total generated segments: {report.get('generated_segments', 0)}",
        f"HSK speed profile: {report.get('hsk_speed_profile', report.get('lang_state', {}).get('hsk_speed_profile', ''))}",
        f"Speech rate: {report.get('speech_rate_label', report.get('lang_state', {}).get('speech_rate_label', '')) or _format_hsk_speed_label(report.get('hsk_speed_profile', report.get('lang_state', {}).get('hsk_speed_profile', '')), report.get('speech_rate', report.get('lang_state', {}).get('speech_rate', '')))}",
        f"Reference duration: {report.get('reference_duration_ms', REFERENCE_DURATION_MS)} ms",
        f"Actual audio duration: {report.get('actual_duration_ms', report.get('final_duration_ms', 0))} ms",
        f"Difference: {report.get('difference_ms', report.get('final_duration_ms', 0) - report.get('reference_duration_ms', REFERENCE_DURATION_MS))} ms",
        "",
    ]
    part_breakdown = report.get("part_breakdown", {})
    for part in (1, 2, 3, 4):
        lines.append(f"Part{part} duration: {part_breakdown.get(part, 0)} ms")
    lines.append("")
    lines.append(f"Reference duration: {report.get('reference_duration_ms', REFERENCE_DURATION_MS)} ms")
    lines.append(f"Final duration: {report.get('final_duration_ms', 0)} ms")
    lines.append(f"Status: {report.get('status', 'UNKNOWN')}")
    return "\n".join(lines).rstrip() + "\n"


def build_hsk1_phase1b(
    report: Dict[str, object],
    output_file: str | Path,
    *,
    lang_state: Optional[Dict[str, object]] = None,
    tts_provider: Optional[Callable[..., AudioSegment]] = None,
    progress_callback: Optional[Callable[[str], None]] = None,
) -> Dict[str, object]:
    if not report or int(report.get("question_count", 0)) != 20:
        raise ValueError("Preview report không hợp lệ hoặc chưa đủ 20 câu.")

    asset_status = validate_hsk30_assets()
    if not asset_status.get("ok"):
        raise FileNotFoundError("Thiếu asset HSK1: " + ", ".join(asset_status.get("missing", [])))

    lang_state = dict(lang_state or report.get("lang_state", {}) or {})
    engine = _clean_text(lang_state.get("engine", "")) or "Google Cloud TTS"
    hsk_speed_profile = _clean_text(lang_state.get("hsk_speed_profile", "HSK1")) or "HSK1"
    speech_rate = _parse_speech_rate(lang_state.get("speech_rate", 0.90), default=0.90)
    speed = _format_speech_rate(speech_rate)
    google_api_key = _clean_text(lang_state.get("google_api_key", ""))
    if not _clean_text(lang_state.get("zh_male_voice", "")) and not _clean_text(lang_state.get("zh_female_voice", "")):
        raise ValueError("Thiếu voice zh nam/nữ từ Lang.")

    out_path = Path(output_file)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    if progress_callback:
        progress_callback("Đang tải asset cố định...")

    music = _load_audio_segment(HSK30_COMMON_ASSET)
    intro = _load_audio_segment(HSK30_HSK1_ASSETS["intro"])
    part_assets = {
        1: _load_audio_segment(HSK30_HSK1_ASSETS["part1"]),
        2: _load_audio_segment(HSK30_HSK1_ASSETS["part2"]),
        3: _load_audio_segment(HSK30_HSK1_ASSETS["part3"]),
        4: _load_audio_segment(HSK30_HSK1_ASSETS["part4"]),
    }

    cache_dir = _make_tts_cache_dir()
    counts = {
        "unique_tts_requests": 0,
        "reused_segments": 0,
        "generated_segments": 0,
        "reused_question_blocks": 0,
        "internal_pauses_ms": 0,
    }

    question_plan: List[Dict[str, object]] = []
    part_breakdown_ms = {1: 0, 2: 0, 3: 0, 4: 0}
    generated_speech_ms = 0

    preview_items = report.get("preview_items", [])
    for item in preview_items:
        slot_audio, repeat1_audio, internal_pause_ms = _build_question_slot(
            item,
            engine=engine,
            speed=speed,
            lang_state=lang_state,
            cache_dir=cache_dir,
            google_api_key=google_api_key,
            tts_provider=tts_provider,
            counts=counts,
        )
        question_ms = len(slot_audio)
        generated_speech_ms += max(question_ms - internal_pause_ms, 0)
        part_breakdown_ms[int(item["part"])] += question_ms
        question_plan.append(
            {
                "question_number": int(item["question_number"]),
                "part": int(item["part"]),
                "slot_audio": slot_audio,
                "speech_ms": question_ms,
                "internal_pause_ms": internal_pause_ms,
                "item": item,
            }
        )

    fixed_assets_ms = len(music) + len(intro) + sum(len(audio) for audio in part_assets.values())
    internal_pauses_ms = counts["internal_pauses_ms"]

    part_intro_silences = [
        ("part1_to_q1", PART_INTRO_SILENCE_MIN_MS, PART_INTRO_SILENCE_MAX_MS),
        ("part2_to_q6", PART_INTRO_SILENCE_MIN_MS, PART_INTRO_SILENCE_MAX_MS),
        ("part3_to_q11", PART_INTRO_SILENCE_MIN_MS, PART_INTRO_SILENCE_MAX_MS),
        ("part4_to_q16", PART_INTRO_SILENCE_MIN_MS, PART_INTRO_SILENCE_MAX_MS),
    ]
    part_gap_silences = [
        ("part1_to_part2", PART_GAP_SILENCE_MIN_MS, PART_GAP_SILENCE_MAX_MS),
        ("part2_to_part3", PART_GAP_SILENCE_MIN_MS, PART_GAP_SILENCE_MAX_MS),
        ("part3_to_part4", PART_GAP_SILENCE_MIN_MS, PART_GAP_SILENCE_MAX_MS),
    ]
    final_silence_spec = ("final_silence", FINAL_SILENCE_MIN_MS, FINAL_SILENCE_MAX_MS)

    answer_silences_ms = [
        _answer_silence_for_item(int(entry["part"]), int(entry["speech_ms"]))
        for entry in question_plan
    ]
    transition_silence_ms = {
        "music_to_intro": MUSIC_TO_INTRO_MS,
        "intro_to_part1": INTRO_TO_PART1_MS,
        "part1_to_q1": PART_INTRO_SILENCE_MIN_MS,
        "part2_to_q6": PART_INTRO_SILENCE_MIN_MS,
        "part3_to_q11": PART_INTRO_SILENCE_MIN_MS,
        "part4_to_q16": PART_INTRO_SILENCE_MIN_MS,
        "part1_to_part2": PART_GAP_SILENCE_MIN_MS,
        "part2_to_part3": PART_GAP_SILENCE_MIN_MS,
        "part3_to_part4": PART_GAP_SILENCE_MIN_MS,
        "final_silence": FINAL_SILENCE_MIN_MS,
    }
    question_index = 0
    part_to_items = {
        1: [entry for entry in question_plan if entry["part"] == 1],
        2: [entry for entry in question_plan if entry["part"] == 2],
        3: [entry for entry in question_plan if entry["part"] == 3],
        4: [entry for entry in question_plan if entry["part"] == 4],
    }

    part_intro_keys = {
        1: "part1_to_q1",
        2: "part2_to_q6",
        3: "part3_to_q11",
        4: "part4_to_q16",
    }
    part_gap_keys = {
        1: "part1_to_part2",
        2: "part2_to_part3",
        3: "part3_to_part4",
    }

    def compose_final_audio(
        answer_values_ms: List[int],
        transition_values_ms: Dict[str, int],
    ) -> Tuple[AudioSegment, Dict[str, int]]:
        final_audio = _silent_segment(0)
        local_breakdown = {1: 0, 2: 0, 3: 0, 4: 0}
        answer_index = 0

        def append_track(segment: AudioSegment) -> None:
            nonlocal final_audio
            final_audio += segment

        append_track(music)
        append_track(_silent_segment(transition_values_ms["music_to_intro"]))
        append_track(intro)
        append_track(_silent_segment(transition_values_ms["intro_to_part1"]))

        for part in (1, 2, 3, 4):
            append_track(part_assets[part])
            append_track(_silent_segment(transition_values_ms[part_intro_keys[part]]))
            part_start = len(final_audio)
            for entry in part_to_items[part]:
                append_track(entry["slot_audio"])
                append_track(_silent_segment(answer_values_ms[answer_index]))
                answer_index += 1
            local_breakdown[part] = len(final_audio) - part_start
            if part in part_gap_keys:
                append_track(_silent_segment(transition_values_ms[part_gap_keys[part]]))

        append_track(_silent_segment(transition_values_ms["final_silence"]))
        return final_audio, local_breakdown

    final_audio, part_breakdown_rendered = compose_final_audio(answer_silences_ms, transition_silence_ms)
    normalized_audio = final_audio.set_channels(FINAL_CHANNELS).set_frame_rate(FINAL_SAMPLE_RATE)
    peak = normalized_audio.max_dBFS
    if peak and peak != float("-inf") and peak > -4.0:
        normalized_audio = normalized_audio.apply_gain(-4.0 - peak)

    export_kwargs = {
        "format": "ipod",
        "codec": "aac",
        "bitrate": FINAL_BITRATE,
        "parameters": ["-ac", str(FINAL_CHANNELS), "-ar", str(FINAL_SAMPLE_RATE)],
    }

    def _export_and_probe(audio_segment: AudioSegment) -> int:
        export_handle = audio_segment.export(str(out_path), **export_kwargs)
        try:
            export_handle.close()
        except Exception:
            pass
        return _probe_audio_duration_ms(out_path)

    initial_final_duration_ms = _export_and_probe(normalized_audio)
    final_duration_ms = initial_final_duration_ms
    reference_duration_ms = REFERENCE_DURATION_MS
    difference_ms = final_duration_ms - reference_duration_ms
    status = "INFO"

    adjusted_answer_total = sum(answer_silences_ms)
    adjusted_transition_total = sum(transition_silence_ms.values()) - transition_silence_ms["final_silence"]

    breakdown = {
        "music_ms": len(music),
        "fixed_assets_ms": fixed_assets_ms - len(music),
        "generated_speech_ms": generated_speech_ms,
        "internal_pauses_ms": internal_pauses_ms,
        "answer_silence_ms": adjusted_answer_total,
        "part_transitions_ms": adjusted_transition_total,
        "final_silence_ms": transition_silence_ms["final_silence"],
        "part_breakdown": part_breakdown_rendered,
        "reference_duration_ms": reference_duration_ms,
    }

    result = {
        "exam": "HSK1001",
        "output_file": str(out_path),
        "music_ms": len(music),
        "fixed_assets_ms": fixed_assets_ms - len(music),
        "generated_speech_ms": generated_speech_ms,
        "internal_pauses_ms": internal_pauses_ms,
        "answer_silence_ms": adjusted_answer_total,
        "part_transitions_ms": adjusted_transition_total,
        "final_silence_ms": transition_silence_ms["final_silence"],
        "part_breakdown": part_breakdown_rendered,
        "unique_tts_requests": counts["unique_tts_requests"],
        "reused_segments": counts["reused_segments"] + counts["reused_question_blocks"],
        "generated_segments": counts["generated_segments"],
        "reference_duration_ms": reference_duration_ms,
        "hsk_speed_profile": hsk_speed_profile,
        "speech_rate": speech_rate,
        "speech_rate_label": _format_hsk_speed_label(hsk_speed_profile, speech_rate),
        "allocated_answer_silence_total_ms": adjusted_answer_total,
        "difference_ms": difference_ms,
        "final_duration_ms": final_duration_ms,
        "status": status,
        "audio_path": str(out_path),
        "transition_silences": transition_silence_ms,
        "answer_silences": answer_silences_ms,
    }
    return result
