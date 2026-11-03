from __future__ import annotations

import io
import hashlib
import json
import math
import os
import re
import shutil
import subprocess
import time
import zipfile
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from difflib import SequenceMatcher
from pathlib import Path
from typing import Callable, Dict, Iterable, List, Optional, Tuple
import unicodedata
import tempfile
from collections import Counter

from pydub import AudioSegment
from pydub.generators import Sine
from pydub.silence import detect_nonsilent

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

from pipelines.pronunciation_overrides import prepare_spoken_text
from services.tts_service import load_audio_segment_from_mp3


BASE_DIR = Path(__file__).resolve().parents[1]
HSK30_AUDIO_ROOT = BASE_DIR / "assets" / "hsk30_audio"
HSK30_DEFAULT_SOURCE = BASE_DIR / "testdata" / "hsk30" / "hsk1" / "ghiam_HSK1_30.xlsx"
HSK30_HSK2_SOURCE = BASE_DIR / "testdata" / "hsk30" / "hsk2" / "phan_nghe_HSK2_30.xlsx"
HSK30_HSK3_SOURCE = BASE_DIR / "testdata" / "hsk30" / "hsk3" / "hsk3-30-ghi-am-nghe.xlsx"
HSK30_HSK4_SOURCE = BASE_DIR / "testdata" / "hsk30" / "hsk4" / "xuat audio hsk 4 đúng (1).xlsx"
HSK30_HSK5_SOURCE = BASE_DIR / "testdata" / "hsk30" / "hsk5" / "xuat_audio_hsk5.xlsx"
HSK30_HSK6_SOURCE = BASE_DIR / "testdata" / "hsk30" / "hsk6" / unicodedata.normalize("NFD", "02 xuất mp3 hsk6 30.xlsx")
if not HSK30_HSK3_SOURCE.is_file():
    HSK30_HSK3_SOURCE = BASE_DIR / "testdata" / "hsk30" / "hsk3" / "FILE XUAT NHAC 1.xlsx"
HSK30_COMMON_ASSET = HSK30_AUDIO_ROOT / "common" / "hsk_1_6_intro_music.m4a"
HSK30_COMMON_OUTTRO = HSK30_AUDIO_ROOT / "common" / "hsk_outtro.m4a"
HSK30_HSK1_ASSETS = {
    "intro": HSK30_AUDIO_ROOT / "hsk1" / "hsk1_intro.m4a",
    "part1": HSK30_AUDIO_ROOT / "hsk1" / "hsk1_part1.m4a",
    "part2": HSK30_AUDIO_ROOT / "hsk1" / "hsk1_part2.m4a",
    "part3": HSK30_AUDIO_ROOT / "hsk1" / "hsk1_part3.m4a",
    "part4": HSK30_AUDIO_ROOT / "hsk1" / "hsk1_part4.m4a",
}
HSK30_HSK2_ASSETS = {
    "intro": HSK30_AUDIO_ROOT / "hsk2" / "hsk2_intro.m4a",
    "part1": HSK30_AUDIO_ROOT / "hsk2" / "hsk2_part1.m4a",
    "part2": HSK30_AUDIO_ROOT / "hsk2" / "hsk2_part2.m4a",
    "part3": HSK30_AUDIO_ROOT / "hsk2" / "hsk2_part3.m4a",
}
HSK30_HSK3_ASSETS = {
    "intro": HSK30_AUDIO_ROOT / "hsk3" / "hsk3_intro.m4a",
    "part1": HSK30_AUDIO_ROOT / "hsk3" / "hsk3_part1.m4a",
    "part2": HSK30_AUDIO_ROOT / "hsk3" / "hsk3_part2.m4a",
    "part3": HSK30_AUDIO_ROOT / "hsk3" / "hsk3_part3.m4a",
    "cue_q1_5": HSK30_AUDIO_ROOT / "hsk3" / "hsk3_cue_q1_5.m4a",
    "cue_q6_10": HSK30_AUDIO_ROOT / "hsk3" / "hsk3_cue_q6_10.m4a",
    "cue_q11_20": HSK30_AUDIO_ROOT / "hsk3" / "hsk3_cue_q11_20.m4a",
    "cue_q21_28": HSK30_AUDIO_ROOT / "hsk3" / "hsk3_cue_q21_28.m4a",
    "cue_q29_30": HSK30_AUDIO_ROOT / "hsk3" / "hsk3_cue_q29_30.m4a",
}
HSK30_HSK4_ASSETS = {
    "intro": HSK30_AUDIO_ROOT / "hsk4" / "hsk4_intro.m4a",
    "part1": HSK30_AUDIO_ROOT / "hsk4" / "hsk4_part1.m4a",
    "part2": HSK30_AUDIO_ROOT / "hsk4" / "hsk4_part2.m4a",
    **{f"cue_q{group}": HSK30_AUDIO_ROOT / "hsk4" / f"hsk4_cue_q{group}.m4a"
       for group in ("20_21", "22_23", "24_25", "26_27", "28_29", "30_32")},
}
HSK30_HSK5_ASSET_DIRECTORY = "hsk5_30_fixed_assets_v2"
HSK30_HSK5_ASSETS = {
    name: HSK30_AUDIO_ROOT / "hsk5" / HSK30_HSK5_ASSET_DIRECTORY / f"hsk5_{name}.m4a"
    for name in ("intro", "part1", "cue_q11_12", "cue_q13_14", "cue_q15_16", "cue_q17_19",
                 "part2", "cue_q22_23", "cue_q24_26", "cue_q27_29", "cue_q30_32", "cue_q33_35")
}
HSK5_BATCH_APPROVED = True
HSK6_BATCH_APPROVED = True
HSK30_HSK6_ASSETS = {
    name: HSK30_AUDIO_ROOT / "hsk6" / f"hsk6_{name}.m4a"
    for name in ("intro", "part1", "part2", "part3", "cue_q9_11", "cue_q12_14",
                 "cue_q15_17", "cue_q18_21", "cue_q22_25", "cue_q26_28",
                 "cue_q29_32", "cue_q33_36", "cue_q37_40")
}

HSK2_AUDIO_PROFILE = {
    "level": "HSK2",
    "reference_duration_ms": 17 * 60 * 1000,
    "number_to_content_ms": 1000,
    "speaker_gap_ms": 950,
    "question_gap_ms": 950,
    "repeat_gap_ms": 2200,
    "answer_pause_by_part": {1: 7500, 2: 11300, 3: 13900},
    "music_to_intro_ms": 1500,
    "intro_to_part1_ms": 800,
    "part_intro_ms": 1500,
    "part_gap_ms": 2500,
    "final_silence_ms": 2000,
}

HSK3_AUDIO_PROFILE = {
    "level": "HSK3",
    "reference_duration_ms": 23 * 60 * 1000,
    "sample_reference_duration_ms": 1_447_700,
    "number_to_content_ms": 1150,
    "speaker_gap_ms": 600,
    "question_gap_ms": 2150,
    "repeat_gap_ms": 2170,
    "answer_pause_by_part": {1: 6190, 2: 12170, 3: 12220},
    "cue_to_number_ms": 3180,
    "shared_cue_to_passage_ms": 1220,
    "passage_to_number_ms": 2130,
    "number_to_question_ms": 1200,
    "music_to_intro_ms": 1500,
    "intro_to_part1_ms": 800,
    "part_intro_ms": 1500,
    "part_gap_ms": 2500,
    "final_silence_ms": 0,
}

HSK4_AUDIO_PROFILE = {
    "level": "HSK4",
    "reference_duration_ms": 20 * 60 * 1000,
    "sample_reference_duration_ms": 21 * 60 * 1000 + 9 * 1000,
    "part_counts": {1: 14, 2: 18},
    "repetitions": 1,
    "music_to_intro_ms": 1020,
    "intro_to_part_ms": 930,
    "part_start_gap_ms": 3140,
    "number_to_content_ms": 1200,
    "speaker_gap_ms": 610,
    "content_to_question_ms": 2040,
    "shared_passage_to_number_ms": 2240,
    "number_to_question_ms": 900,
    "answer_pause_ms": 16180,
    "cue_to_stimulus_ms": 0,
    "cues_before_question": {20: "cue_q20_21", 22: "cue_q22_23", 24: "cue_q24_25",
                             26: "cue_q26_27", 28: "cue_q28_29", 30: "cue_q30_32"},
    "final_silence_ms": 0,
}

HSK5_AUDIO_PROFILE = {
    "level": "HSK5", "part_counts": {1: 19, 2: 16}, "repetitions": 1,
    "default_rate": 1.00,
    "reference_duration_ms": 1_500_000,
    "sample_reference_duration_ms": 1_591_980,
    "meaningful_sample_duration_ms": 1_571_700,
    "music_to_intro_ms": 1020, "intro_to_part_ms": 0,
    "part_start_gap_ms": 3150, "cue_to_stimulus_ms": 0,
    "part_start_gap_by_part": {1: 3150, 2: 0},
    "fixed_assets_version": "hsk5_fixed_assets_v2",
    "number_to_content_ms": 1150, "speaker_gap_ms": 650,
    "content_to_question_ms": 2020, "shared_passage_to_number_ms": 2250,
    "number_to_question_by_part": {1: 740, 2: 760},
    "answer_pause_by_part": {1: 14900, 2: 15860},
    "final_silence_ms": 0,
    "cues_before_question": {11: "cue_q11_12", 13: "cue_q13_14", 15: "cue_q15_16", 17: "cue_q17_19",
                             22: "cue_q22_23", 24: "cue_q24_26", 27: "cue_q27_29",
                             30: "cue_q30_32", 33: "cue_q33_35"},
    "part1_narration_fallback": "NỮ",
    "narration_group_voices": {
        "20–21": "NAM", "22–23": "NAM", "24–26": "NỮ",
        "27–29": "NỮ", "30–32": "NAM", "33–35": "NỮ",
    },
    "expected_shared_groups": ("11–12", "13–14", "15–16", "17–19",
                               "20–21", "22–23", "24–26", "27–29", "30–32", "33–35"),
}

# HSK3 keeps explicit rhythm pauses in the builder. Google TTS clips are
# normalized first so their codec padding does not get added to those pauses.
HSK3_TTS_POSTPROCESS_VERSION = "edge_trim_v1"
HSK3_EDGE_SILENCE_THRESHOLD_DB = -45.0
HSK3_EDGE_MIN_SILENCE_MS = 20
HSK3_EDGE_SAFETY_PAD_MS = 80
HSK3_SPEED_COMPARISON_SEPARATOR_MS = 700

HSK6_AUDIO_PROFILE = {
    "level": "HSK6", "part_counts": {1: 8, 2: 20, 3: 12}, "repetitions": 1,
    "default_rate": 1.00, "sample_reference_duration_ms": 1_895_523,
    "reference_duration_ms": 1_895_523,
    "music_to_intro_ms": 1229, "intro_to_part_ms": 0,
    "part_start_gap_ms": 0, "cue_to_stimulus_ms": 0,
    "fixed_assets_version": "hsk6_fixed_assets_v1",
    # The sample's audible gaps include both 80 ms TTS safety edges.
    "audible_answer_pause_by_part": {1: 14169, 2: 13951, 3: 14024},
    "answer_pause_by_part": {1: 14169 - 160, 2: 13951 - 160, 3: 14024 - 160},
    "number_to_content_ms": 1350 - 160,
    "shared_passage_to_number_ms": 2450 - 160,
    "number_to_question_ms": 650 - 160,
    "speaker_gap_ms": 0, "final_silence_ms": 0,
    "expected_shared_groups": ("9–11", "12–14", "15–17", "18–21", "22–25",
                               "26–28", "29–32", "33–36", "37–40"),
    "cues_before_question": {9: "cue_q9_11", 12: "cue_q12_14", 15: "cue_q15_17",
                             18: "cue_q18_21", 22: "cue_q22_25", 26: "cue_q26_28",
                             29: "cue_q29_32", 33: "cue_q33_36", 37: "cue_q37_40"},
    "narration_group_voices": {"9–11": "NỮ", "12–14": "NAM", "15–17": "NỮ",
                               "18–21": "NAM", "22–25": "NỮ", "26–28": "NAM",
                               "29–32": "NỮ", "33–36": "NỮ", "37–40": "NAM"},
    "interview_role_voices_by_group": {
        "29–32": {"主持人": "NAM", "嘉宾": "NỮ"},
        "33–36": {"主持人": "NỮ", "嘉宾": "NAM"},
        "37–40": {"主持人": "NAM", "嘉宾": "NỮ"},
    },
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
            target = target.lstrip("/")
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


def _normalize_hsk1_exam_id(exam_code: str) -> str:
    """Accept legacy HSK1 IDs and the newer ``-30`` workbook suffix."""
    normalized = _normalize_exam_code(exam_code)
    match = re.fullmatch(r"(HSK\d{4})(?:-30)?", normalized)
    return match.group(1) if match else normalized


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
    if profile_label == "HSK6":
        label = {1.00: "chuẩn", 1.03: "nhanh hơn", 1.05: "nhanh hơn"}.get(numeric_rate, "custom")
        return f"{profile_label} {label} — {numeric_rate:.2f}x"
    if profile_label == "HSK5":
        label = {0.95: "chậm hơn", 1.00: "chuẩn", 1.05: "nhanh hơn"}.get(numeric_rate, "custom")
        return f"{profile_label} {label} — {numeric_rate:.2f}x"
    if profile_label == "HSK4":
        hsk4_label = {0.90: "chậm hơn", 0.95: "chuẩn", 1.00: "nhanh hơn"}.get(numeric_rate, "custom")
        return f"{profile_label} {hsk4_label} — {numeric_rate:.2f}x"
    if profile_label == "HSK3":
        hsk3_label = {0.70: "chậm", 0.72: "chậm hơn", 0.75: "chuẩn", 0.80: "nhanh hơn"}.get(numeric_rate, "custom")
        return f"{profile_label} {hsk3_label} — {numeric_rate:.2f}x"
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


def validate_hsk30_assets(asset_root: str | Path | None = None, level: str = "hsk1") -> Dict[str, object]:
    root = Path(asset_root) if asset_root is not None else HSK30_AUDIO_ROOT
    level_key = _clean_text(level).lower().replace(" ", "")
    required = {
        "common/hsk_1_6_intro_music.m4a": root / "common" / "hsk_1_6_intro_music.m4a",
        "common/hsk_outtro.m4a": root / "common" / "hsk_outtro.m4a",
    }
    if level_key in {"hsk6", "6"}:
        required.update({
            f"hsk6/hsk6_{name}.m4a": root / "hsk6" / f"hsk6_{name}.m4a"
            for name in HSK30_HSK6_ASSETS
        })
    elif level_key in {"hsk3", "3"}:
        required.update({
            f"hsk3/{name}.m4a": path for name, path in {
                "hsk3_intro": root / "hsk3" / "hsk3_intro.m4a",
                "hsk3_part1": root / "hsk3" / "hsk3_part1.m4a",
                "hsk3_part2": root / "hsk3" / "hsk3_part2.m4a",
                "hsk3_part3": root / "hsk3" / "hsk3_part3.m4a",
                "hsk3_cue_q1_5": root / "hsk3" / "hsk3_cue_q1_5.m4a",
                "hsk3_cue_q6_10": root / "hsk3" / "hsk3_cue_q6_10.m4a",
                "hsk3_cue_q11_20": root / "hsk3" / "hsk3_cue_q11_20.m4a",
                "hsk3_cue_q21_28": root / "hsk3" / "hsk3_cue_q21_28.m4a",
                "hsk3_cue_q29_30": root / "hsk3" / "hsk3_cue_q29_30.m4a",
            }.items()
        })
    elif level_key in {"hsk5", "5"}:
        required.update({
            f"hsk5/{HSK30_HSK5_ASSET_DIRECTORY}/hsk5_{name}.m4a":
                root / "hsk5" / HSK30_HSK5_ASSET_DIRECTORY / f"hsk5_{name}.m4a"
            for name in HSK30_HSK5_ASSETS
        })
    elif level_key in {"hsk4", "4"}:
        required.update({
            f"hsk4/hsk4_{name}.m4a": root / "hsk4" / f"hsk4_{name}.m4a"
            for name in HSK30_HSK4_ASSETS
        })
    elif level_key in {"hsk2", "2"}:
        required.update({
            "hsk2/hsk2_intro.m4a": root / "hsk2" / "hsk2_intro.m4a",
            "hsk2/hsk2_part1.m4a": root / "hsk2" / "hsk2_part1.m4a",
            "hsk2/hsk2_part2.m4a": root / "hsk2" / "hsk2_part2.m4a",
            "hsk2/hsk2_part3.m4a": root / "hsk2" / "hsk2_part3.m4a",
        })
    else:
        required.update({
            "hsk1/hsk1_intro.m4a": root / "hsk1" / "hsk1_intro.m4a",
            "hsk1/hsk1_part1.m4a": root / "hsk1" / "hsk1_part1.m4a",
            "hsk1/hsk1_part2.m4a": root / "hsk1" / "hsk1_part2.m4a",
            "hsk1/hsk1_part3.m4a": root / "hsk1" / "hsk1_part3.m4a",
            "hsk1/hsk1_part4.m4a": root / "hsk1" / "hsk1_part4.m4a",
        })
    missing = [name for name, path in required.items() if not path.is_file()]
    present = [name for name in required if name not in missing]
    return {
        "root": str(root),
        "level": "HSK6" if level_key in {"hsk6", "6"} else ("HSK5" if level_key in {"hsk5", "5"} else ("HSK4" if level_key in {"hsk4", "4"} else ("HSK3" if level_key in {"hsk3", "3"} else ("HSK2" if level_key in {"hsk2", "2"} else "HSK1")))),
        "required": required,
        "missing": missing,
        "present": present,
        "ok": not missing,
    }


def phase1_output_filename(exam_code: str) -> str:
    hsk6_match = re.fullmatch(r"HSK6-30-(\d{2})", _normalize_exam_code(exam_code))
    if hsk6_match:
        return f"hsk6-30-{int(hsk6_match.group(1)):03d}.m4a"
    hsk5_match = re.fullmatch(r"HSK5_30_(\d{2})", _normalize_exam_code(exam_code))
    if hsk5_match:
        return f"hsk5-30-{int(hsk5_match.group(1)):03d}.m4a"
    normalized = _normalize_exam_code(exam_code)
    hsk1_exam_id = _normalize_hsk1_exam_id(normalized)
    hsk1_match = re.fullmatch(r"HSK1(\d{3})", hsk1_exam_id)
    if hsk1_match:
        return f"hsk1-30-{hsk1_match.group(1)}.m4a"
    hsk2_match = re.fullmatch(r"HSK2(\d{3})", normalized)
    if hsk2_match:
        return f"hsk2-30-{hsk2_match.group(1)}.m4a"
    hsk3_match = re.fullmatch(r"HSK3-30-(\d{2})", normalized)
    if hsk3_match:
        return f"hsk3-30-{int(hsk3_match.group(1)):03d}.m4a"
    hsk4_match = re.fullmatch(r"HSK4-30-(\d{2})", normalized)
    if hsk4_match:
        return f"hsk4-30-{int(hsk4_match.group(1)):03d}.m4a"
    if not hsk1_match and not hsk2_match and not hsk3_match and not hsk4_match:
        raise ValueError(f"Không hỗ trợ exam code: {exam_code}")


def parse_hsk1_phase1(
    excel_path: str | Path,
    sheet_name: str = "Sheet1",
    exam_code: str = "HSK1001",
    asset_root: str | Path | None = None,
    lang_state: Optional[Dict[str, str]] = None,
) -> Dict[str, object]:
    rows = read_xlsx_rows(excel_path, sheet_name=sheet_name)
    normalized_exam = _normalize_hsk1_exam_id(exam_code)
    question_rows: List[XlsxRow] = []
    fixed_rows: List[int] = []
    invalid_rows: List[str] = []
    detected_exam = None
    pending_fixed_rows: List[int] = []
    in_exam_block = False

    for row in rows:
        source_exam_value = _clean_text(row.cells.get("A", ""))
        exam_value = _normalize_hsk1_exam_id(source_exam_value)
        part_heading = _parse_part_label(row.cells.get("H", ""))

        if row.row_number == 1 or source_exam_value.casefold() == "mã đề":
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

        if not re.fullmatch(r"HSK\d{4}(?:-30)?", source_exam_value):
            invalid_rows.append(f"Dòng {row.row_number}: mã đề không hợp lệ '{source_exam_value}'")
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


def _parse_hsk2_segments(text: str, *, require_question: bool) -> List[Dict[str, str]]:
    source = _clean_text(text)
    matches = list(re.finditer(r"(男|女|问)\s*[：:]", source))
    if not matches or matches[0].start() != 0:
        raise ValueError("Phụ đề phải bắt đầu bằng prefix 男：/女：/问：")

    segments: List[Dict[str, str]] = []
    for index, match in enumerate(matches):
        token = match.group(1)
        end = matches[index + 1].start() if index + 1 < len(matches) else len(source)
        spoken = _clean_text(source[match.end():end])
        if not spoken:
            raise ValueError(f"Segment {token}： bị rỗng")
        segments.append({
            "speaker_label": {"男": "NAM", "女": "NỮ", "问": "CÂU HỎI"}[token],
            "speaker_token": token,
            "source_text": f"{token}：{spoken}",
            "text": spoken,
        })

    tokens = [segment["speaker_token"] for segment in segments]
    if require_question:
        if len(segments) != 3 or tokens[2] != "问" or set(tokens[:2]) != {"男", "女"}:
            raise ValueError("Part 3 phải có đúng 2 segment 男/女 và một segment 问")
    elif len(segments) != 2 or set(tokens) != {"男", "女"}:
        raise ValueError("Part 2 phải có đúng 2 segment 男/女")
    return segments


def validate_hsk2_workbook(excel_path: str | Path, sheet_name: str = "Sheet1") -> Dict[str, object]:
    rows = read_xlsx_rows(excel_path, sheet_name=sheet_name)
    if not rows:
        raise ValueError("Workbook HSK2 không có dữ liệu")
    header = rows[0].cells
    expected_headers = {"A": "Mã đề", "B": "Phần", "E": "Số câu", "F": "Phụ đề"}
    missing_headers = [name for column, name in expected_headers.items() if header.get(column) != name]
    if missing_headers:
        raise ValueError("Workbook HSK2 thiếu cột bắt buộc: " + ", ".join(missing_headers))

    exams: Dict[str, List[XlsxRow]] = {}
    invalid_exam_rows: List[str] = []
    for row in rows[1:]:
        exam = _clean_text(row.cells.get("A", ""))
        if not exam:
            continue
        if not re.fullmatch(r"HSK2\d{3}", exam):
            invalid_exam_rows.append(f"Dòng {row.row_number}: mã đề không hợp lệ '{exam}'")
            continue
        exams.setdefault(exam, []).append(row)

    expected_exams = [f"HSK2{index:03d}" for index in range(1, 17)]
    missing_exams = [exam for exam in expected_exams if exam not in exams]
    unexpected_exams = [exam for exam in exams if exam not in expected_exams]
    exam_summaries: Dict[str, Dict[str, object]] = {}
    for exam in sorted(exams):
        exam_rows = exams[exam]
        examples = [row for row in exam_rows if _clean_text(row.cells.get("E", "")) == "示例"]
        real_rows = [row for row in exam_rows if _clean_text(row.cells.get("E", "")) != "示例"]
        part_counts = Counter(_parse_part_label(row.cells.get("B", "")) for row in real_rows)
        exam_summaries[exam] = {
            "row_count": len(exam_rows),
            "example_count": len(examples),
            "real_question_count": len(real_rows),
            "part_counts": {part: int(part_counts.get(part, 0)) for part in (1, 2, 3)},
        }

    return {
        "workbook": str(Path(excel_path)),
        "sheet": sheet_name,
        "exam_count": len(exams),
        "exam_codes": sorted(exams),
        "missing_exams": missing_exams,
        "unexpected_exams": unexpected_exams,
        "invalid_exam_rows": invalid_exam_rows,
        "exams": exam_summaries,
        "status": "PASS" if not (missing_exams or unexpected_exams or invalid_exam_rows) and all(
            summary["row_count"] == 28
            and summary["example_count"] == 3
            and summary["real_question_count"] == 25
            and summary["part_counts"] == {1: 5, 2: 10, 3: 10}
            for summary in exam_summaries.values()
        ) else "FAIL",
    }


def parse_hsk2_phase1(
    excel_path: str | Path,
    sheet_name: str = "Sheet1",
    exam_code: str = "HSK2001",
    asset_root: str | Path | None = None,
    lang_state: Optional[Dict[str, str]] = None,
) -> Dict[str, object]:
    structure = validate_hsk2_workbook(excel_path, sheet_name=sheet_name)
    normalized_exam = _normalize_exam_code(exam_code)
    summary = structure.get("exams", {}).get(normalized_exam)
    if not summary:
        raise ValueError(f"Không tìm thấy dữ liệu cho {normalized_exam}")
    if summary["row_count"] != 28 or summary["example_count"] != 3 or summary["real_question_count"] != 25:
        raise ValueError(f"{normalized_exam} phải có 28 dòng, gồm 3 示例 và 25 câu thật")
    if summary["part_counts"] != {1: 5, 2: 10, 3: 10}:
        raise ValueError(f"{normalized_exam} sai số câu theo Part: {summary['part_counts']}")

    rows = read_xlsx_rows(excel_path, sheet_name=sheet_name)
    selected_rows = [row for row in rows if _clean_text(row.cells.get("A", "")) == normalized_exam]
    preview_items: List[Dict[str, object]] = []
    fixed_rows: List[int] = []
    invalid_rows: List[str] = []
    part_counts = {1: 0, 2: 0, 3: 0}
    real_index = 0
    lang_state = dict(lang_state or {})
    hsk_speed_profile = _clean_text(lang_state.get("hsk_speed_profile", "HSK2")) or "HSK2"
    speech_rate = _parse_speech_rate(lang_state.get("speech_rate", 0.70), default=0.70)

    for row in selected_rows:
        source_number = _clean_text(row.cells.get("E", ""))
        if source_number == "示例":
            fixed_rows.append(row.row_number)
            continue
        real_index += 1
        part = _parse_part_label(row.cells.get("B", ""))
        subtitle = _clean_text(row.cells.get("F", ""))
        if part not in (1, 2, 3):
            invalid_rows.append(f"Dòng {row.row_number}: Part HSK2 không hợp lệ")
            continue
        if not source_number or not subtitle:
            invalid_rows.append(f"Dòng {row.row_number}: thiếu Số câu hoặc Phụ đề")
            continue

        try:
            if part == 1:
                first_speaker = "NỮ" if real_index % 2 == 1 else "NAM"
                second_speaker = "NAM" if first_speaker == "NỮ" else "NỮ"
                repeat1 = [{"speaker_label": first_speaker, "text": subtitle, "source_text": subtitle}]
                repeat2 = [{"speaker_label": second_speaker, "text": subtitle, "source_text": subtitle}]
            elif part == 2:
                parsed_segments = _parse_hsk2_segments(subtitle, require_question=False)
                repeat1 = [{key: segment[key] for key in ("speaker_label", "text", "source_text")} for segment in parsed_segments]
                repeat2 = [{key: segment[key] for key in ("speaker_label", "text", "source_text")} for segment in parsed_segments]
            else:
                parsed_segments = _parse_hsk2_segments(subtitle, require_question=True)
                dialogue = [{key: segment[key] for key in ("speaker_label", "text", "source_text")} for segment in parsed_segments[:2]]
                question_text = parsed_segments[2]["text"]
                question_source = parsed_segments[2]["source_text"]
                first_speaker = dialogue[0]["speaker_label"]
                second_speaker = dialogue[1]["speaker_label"]
                repeat1 = dialogue + [{"speaker_label": first_speaker, "text": question_text, "source_text": question_source}]
                repeat2 = dialogue + [{"speaker_label": second_speaker, "text": question_text, "source_text": question_source}]
        except ValueError as exc:
            invalid_rows.append(f"Dòng {row.row_number}: {exc}")
            continue

        part_counts[part] += 1
        spoken_number = _spoken_question_number(source_number)
        item = {
            "question_number": real_index,
            "part": part,
            "part_label": PART_LABELS[part],
            "source_row": row.row_number,
            "source_exam": normalized_exam,
            "source_number": source_number,
            "question_number_spoken": spoken_number,
            "number_speaker_label": "NỮ",
            "text_h": subtitle,
            "text_j": "",
            "spoken_h": subtitle,
            "spoken_j": "",
            "repeat1": repeat1,
            "repeat2": repeat2,
        }
        item["preview"] = _build_item_preview(item)
        preview_items.append(item)

    asset_status = validate_hsk30_assets(asset_root=asset_root, level="hsk2")
    report_lang_state = {
        "engine": lang_state.get("engine", ""),
        "speed": lang_state.get("speed", ""),
        "voice": lang_state.get("voice", ""),
        "zh_male_voice": lang_state.get("zh_male_voice", ""),
        "zh_female_voice": lang_state.get("zh_female_voice", ""),
        "zh_narrator_voice": lang_state.get("zh_narrator_voice", ""),
        "hsk_speed_profile": hsk_speed_profile,
        "speech_rate": speech_rate,
        "speech_rate_label": _format_hsk_speed_label(hsk_speed_profile, speech_rate),
    }
    if invalid_rows or len(preview_items) != 25 or part_counts != {1: 5, 2: 10, 3: 10}:
        status = "REVIEW_WITH_WARNINGS"
    else:
        status = "READY_FOR_REVIEW"
    return {
        "level": "HSK2",
        "workbook": str(Path(excel_path)),
        "sheet": sheet_name,
        "detected_exam": normalized_exam,
        "question_count": len(preview_items),
        "part_counts": part_counts,
        "fixed_instruction_rows_ignored": fixed_rows,
        "missing_invalid_rows": invalid_rows,
        "asset_status": asset_status,
        "lang_state": report_lang_state,
        "output_filename": phase1_output_filename(normalized_exam),
        "reference_duration_ms": HSK2_AUDIO_PROFILE["reference_duration_ms"],
        "tts_requests_made": 0,
        "preview_items": preview_items,
        "status": status,
        "structure": structure,
        "question_voice_rule": "Part 3: 问 follows speaker 1 in repeat1 and speaker 2 in repeat2; dialogue roles stay unchanged.",
    }


def format_hsk2_preview_report(report: Dict[str, object]) -> str:
    lines = [
        f"{report.get('detected_exam', 'HSK2001')} parse summary",
        "Level: HSK2",
        f"Workbook: {report.get('workbook', '')}",
        f"Sheet: {report.get('sheet', '')}",
        f"Questions: {report.get('question_count', 0)}",
        f"Part 1: {report.get('part_counts', {}).get(1, 0)}",
        f"Part 2: {report.get('part_counts', {}).get(2, 0)}",
        f"Part 3: {report.get('part_counts', {}).get(3, 0)}",
        f"Fixed/example rows ignored: {len(report.get('fixed_instruction_rows_ignored', []))}",
        f"Missing/invalid rows: {len(report.get('missing_invalid_rows', []))}",
        f"Asset status: {'OK' if report.get('asset_status', {}).get('ok') else 'MISSING'}",
        f"HSK speed profile: {report.get('lang_state', {}).get('hsk_speed_profile', 'HSK2')}",
        f"Speech rate: {report.get('lang_state', {}).get('speech_rate_label', '')}",
        f"Question voice rule: {report.get('question_voice_rule', '')}",
        "TTS requests in dry run: 0",
        "",
    ]
    for item in report.get("preview_items", []):
        lines.extend([
            f"Q{item['question_number']:02d} — {PART_LABELS[item['part']]}",
            f"Source row: {item['source_row']}",
            f"Source number: {item['source_number']}",
            f"Number spoken: NỮ [{item['question_number_spoken']}]",
        ])
        if item["part"] == 1:
            lines.append(f"repeat1: {_render_segment(item['repeat1'][0]['speaker_label'], item['repeat1'][0]['text'])}")
            lines.append(f"repeat2: {_render_segment(item['repeat2'][0]['speaker_label'], item['repeat2'][0]['text'])}")
        else:
            lines.append("repeat1:")
            lines.extend(f"  {_render_segment(segment['speaker_label'], segment['text'])}" for segment in item["repeat1"])
            lines.append("repeat2:")
            lines.extend(f"  {_render_segment(segment['speaker_label'], segment['text'])}" for segment in item["repeat2"])
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


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


def _aws_credentials_from_lang_state(lang_state: Dict[str, object]) -> Dict[str, str]:
    return {
        "access_key_id": _clean_text(lang_state.get("aws_access_key_id", "")),
        "secret_access_key": _clean_text(lang_state.get("aws_secret_access_key", "")),
        "region": _clean_text(lang_state.get("aws_region", "")),
    }


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


def _export_audio_atomically(audio: AudioSegment, out_path: Path, export_kwargs: Dict[str, object]) -> int:
    """Export and probe a temporary file before replacing the published output."""
    out_path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(
        prefix=f".{out_path.stem}-",
        suffix=".tmp.m4a",
        dir=str(out_path.parent),
    )
    os.close(fd)
    temp_path = Path(temp_name)
    try:
        export_handle = audio.export(str(temp_path), **export_kwargs)
        try:
            export_handle.close()
        except Exception:
            pass
        duration_ms = _probe_audio_duration_ms(temp_path)
        os.replace(temp_path, out_path)
        return duration_ms
    except Exception:
        try:
            temp_path.unlink(missing_ok=True)
        except Exception:
            pass
        raise


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


def _tts_cache_key(
    engine: str,
    voice_name: str,
    speed: str,
    language: str,
    mode: str,
    spoken_text: str,
    source_text: str,
    speaker_label: str,
    audio_postprocess_version: str = "",
) -> str:
    payload = {
        "engine": _clean_text(engine).lower(),
        "voice": _clean_text(voice_name),
        "speed": _clean_text(speed),
        "language": _clean_text(language).lower(),
        "mode": _clean_text(mode).lower(),
        "spoken_text": _normalize_tts_text(spoken_text),
        "source_text": _normalize_tts_text(source_text),
        "speaker": _clean_text(speaker_label),
    }
    if _clean_text(audio_postprocess_version):
        payload["audio_postprocess_version"] = _clean_text(audio_postprocess_version)
    payload = json.dumps(
        payload,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _trim_tts_edge_silence(audio: AudioSegment, audio_metrics: Optional[Dict[str, int]] = None) -> AudioSegment:
    """Trim only the outer silence while preserving internal linguistic pauses."""
    if len(audio) <= HSK3_EDGE_MIN_SILENCE_MS * 2:
        return audio
    mono = audio.set_channels(1)
    nonsilent = detect_nonsilent(
        mono,
        min_silence_len=HSK3_EDGE_MIN_SILENCE_MS,
        silence_thresh=HSK3_EDGE_SILENCE_THRESHOLD_DB,
    )
    if not nonsilent:
        return audio
    speech_start = nonsilent[0][0]
    speech_end = nonsilent[-1][1]
    start = max(0, speech_start - HSK3_EDGE_SAFETY_PAD_MS)
    end = min(len(audio), speech_end + HSK3_EDGE_SAFETY_PAD_MS)
    if end <= start:
        return audio
    if audio_metrics is not None:
        audio_metrics.update(
            raw_duration_ms=len(audio), leading_silence_ms=speech_start,
            trailing_silence_ms=len(audio) - speech_end,
            active_speech_ms=speech_end - speech_start,
            edge_silence_trimmed_ms=start + len(audio) - end,
            retained_edge_silence_ms=speech_start - start + end - speech_end,
        )
    return audio[start:end]


def _prepare_tts_audio(audio: AudioSegment, audio_postprocess_version: str, audio_metrics: Optional[Dict[str, int]] = None) -> AudioSegment:
    if audio_postprocess_version == HSK3_TTS_POSTPROCESS_VERSION:
        return _trim_tts_edge_silence(audio, audio_metrics)
    return audio


def _export_tts_cache_audio(audio: AudioSegment, cache_path: Path, audio_postprocess_version: str, audio_metrics: Optional[Dict[str, int]] = None) -> AudioSegment:
    prepared = _prepare_tts_audio(audio, audio_postprocess_version, audio_metrics)
    if audio_metrics is None:
        export_handle = prepared.export(str(cache_path), format="mp3")
        try:
            export_handle.close()
        except Exception:
            pass
        return prepared
    # HSK5 metrics gate: never publish a raw/partially written normalized cache entry.
    if len(prepared) <= 0:
        raise ValueError(f"Empty TTS speech segment: {cache_path.name}")
    fd, temporary = tempfile.mkstemp(prefix=f".{cache_path.stem}.", suffix=".mp3", dir=cache_path.parent)
    os.close(fd)
    temporary_path = Path(temporary)
    try:
        export_handle = prepared.export(str(temporary_path), format="mp3")
        export_handle.close()
        if temporary_path.stat().st_size == 0:
            raise ValueError(f"Empty TTS cache export: {cache_path.name}")
        os.replace(temporary_path, cache_path)
        _write_hsk5_json(cache_path.with_suffix(".edge_metrics.json"), audio_metrics)
    finally:
        temporary_path.unlink(missing_ok=True)
    return prepared


def _decode_and_cache_tts_audio(temp_mp3: Path, cache_path: Path, audio_postprocess_version: str,
                                audio_metrics: Optional[Dict[str, int]]) -> AudioSegment:
    if audio_metrics is None:
        shutil.move(str(temp_mp3), str(cache_path))
        raw = load_audio_segment_from_mp3(str(cache_path))
        return _export_tts_cache_audio(raw, cache_path, audio_postprocess_version)
    try:
        raw = load_audio_segment_from_mp3(str(temp_mp3))
        return _export_tts_cache_audio(raw, cache_path, audio_postprocess_version, audio_metrics)
    finally:
        temp_mp3.unlink(missing_ok=True)


def _load_cached_tts_audio(cache_path: Path) -> AudioSegment:
    return load_audio_segment_from_mp3(str(cache_path)).set_frame_rate(44100).set_channels(2)


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


def _hsk6_google_synthesize_with_retry(client, synthesis_input, voice, audio_config):
    from google.api_core.exceptions import InvalidArgument

    for attempt in range(3):
        try:
            return client.synthesize_speech(input=synthesis_input, voice=voice, audio_config=audio_config)
        except InvalidArgument as exc:
            if "Failed to synthesize with voice" not in str(exc) or attempt == 2:
                raise
            time.sleep(attempt + 1)


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
    aws_credentials: Optional[Dict[str, str]] = None,
    audio_postprocess_version: str = "",
    audio_metrics: Optional[Dict[str, int]] = None,
    retry_google_voice_failure: bool = False,
) -> Tuple[AudioSegment, bool]:
    normalized_text = _normalize_tts_text(text)
    if not normalized_text:
        return _silent_segment(0), True

    prepared = prepare_spoken_text(normalized_text, "zh-CN", engine, voice_name)
    spoken_text = prepared.spoken_text
    cache_dir.mkdir(parents=True, exist_ok=True)
    cache_key = _tts_cache_key(
        engine, voice_name, speed, "zh-CN", prepared.mode, spoken_text, normalized_text,
        speaker_label, audio_postprocess_version=audio_postprocess_version,
    )
    cache_path = cache_dir / f"{cache_key}.mp3"

    if cache_path.is_file() and cache_path.stat().st_size > 0:
        if audio_metrics is not None:
            metadata_path = cache_path.with_suffix(".edge_metrics.json")
            if metadata_path.is_file():
                audio_metrics.update(json.loads(metadata_path.read_text(encoding="utf-8")))
            else:
                # Recover old interrupted HSK5 writes locally, without another TTS call.
                raw = load_audio_segment_from_mp3(str(cache_path))
                segment = _export_tts_cache_audio(raw, cache_path, audio_postprocess_version, audio_metrics)
                return segment.set_frame_rate(44100).set_channels(2), True
        return _load_cached_tts_audio(cache_path), True

    # Migrate an existing raw HSK3 clip locally. It is normalized into the
    # versioned key and never returned directly, so stale edge padding cannot
    # leak back into the builder and no live TTS request is needed.
    if audio_postprocess_version == HSK3_TTS_POSTPROCESS_VERSION:
        legacy_key = _tts_cache_key(
            engine, voice_name, speed, "zh-CN", prepared.mode, spoken_text,
            normalized_text, speaker_label,
        )
        legacy_path = cache_dir / f"{legacy_key}.mp3"
        if legacy_path.is_file() and legacy_path.stat().st_size > 0:
            legacy_audio = load_audio_segment_from_mp3(str(legacy_path))
            prepared_segment = _export_tts_cache_audio(legacy_audio, cache_path, audio_postprocess_version, audio_metrics)
            return prepared_segment.set_frame_rate(44100).set_channels(2), True

    if tts_provider is not None:
        segment = tts_provider(
            normalized_text,
            engine=engine,
            speed=speed,
            voice_name=voice_name,
            speaker_label=speaker_label,
            spoken_text=spoken_text,
            pronunciation_mode=prepared.mode,
        )
        if not isinstance(segment, AudioSegment):
            raise TypeError("tts_provider phải trả về AudioSegment")
        prepared_segment = _export_tts_cache_audio(segment, cache_path, audio_postprocess_version, audio_metrics)
        return prepared_segment.set_frame_rate(44100).set_channels(2), False

    engine_clean = _clean_text(engine).lower()
    speech_rate = _parse_speech_rate(speed, default=1.0)
    if engine_clean == "gtts":
        if gTTS is None:
            raise RuntimeError("gTTS không khả dụng")
        temp_mp3 = cache_path.with_suffix(".tmp.mp3")
        gTTS(text=spoken_text, lang="zh", slow=speech_rate < 0.95).save(str(temp_mp3))
        prepared_segment = _decode_and_cache_tts_audio(temp_mp3, cache_path, audio_postprocess_version, audio_metrics)
        return prepared_segment.set_frame_rate(44100).set_channels(2), False

    if engine_clean == "polly":
        if boto3 is None:
            raise RuntimeError("boto3 không khả dụng")
        if texttospeech is None:
            pass
        temp_mp3 = cache_path.with_suffix(".tmp.mp3")
        voice_id = "Zhiyu"
        if _clean_text(speaker_label) == "NAM":
            voice_id = "Zhiyu"
        credentials = aws_credentials or {}
        polly_client = boto3.Session(
            aws_access_key_id=credentials.get("access_key_id") or os.environ.get("AWS_ACCESS_KEY_ID"),
            aws_secret_access_key=credentials.get("secret_access_key") or os.environ.get("AWS_SECRET_ACCESS_KEY"),
            region_name=credentials.get("region") or os.environ.get("AWS_REGION") or os.environ.get("AWS_DEFAULT_REGION") or "ap-southeast-1",
        ).client("polly")
        text_type = "text"
        tts_text = spoken_text
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
        prepared_segment = _decode_and_cache_tts_audio(temp_mp3, cache_path, audio_postprocess_version, audio_metrics)
        return prepared_segment.set_frame_rate(44100).set_channels(2), False

    if texttospeech is None:
        raise RuntimeError("google-cloud-texttospeech not installed")

    client_options = None
    api_key = _clean_text(google_api_key) or _clean_text(os.environ.get("GOOGLE_TTS_API_KEY", ""))
    if api_key:
        from google.api_core.client_options import ClientOptions
        client_options = ClientOptions(api_key=api_key)

    client = texttospeech.TextToSpeechClient(client_options=client_options) if client_options else texttospeech.TextToSpeechClient()
    synthesis_input = texttospeech.SynthesisInput(text=spoken_text)
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
    selected = texttospeech.VoiceSelectionParams(**voice_kwargs)
    config = texttospeech.AudioConfig(**audio_kwargs)
    if retry_google_voice_failure:
        response = _hsk6_google_synthesize_with_retry(client, synthesis_input, selected, config)
    else:
        response = client.synthesize_speech(input=synthesis_input, voice=selected, audio_config=config)
    temp_mp3 = cache_path.with_suffix(".tmp.mp3")
    with open(temp_mp3, "wb") as fp:
        fp.write(response.audio_content)
    prepared_segment = _decode_and_cache_tts_audio(temp_mp3, cache_path, audio_postprocess_version, audio_metrics)
    return prepared_segment.set_frame_rate(44100).set_channels(2), False


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
    audio_postprocess_version: str = "",
) -> AudioSegment:
    pieces: List[AudioSegment] = []
    aws_credentials = _aws_credentials_from_lang_state(lang_state)
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
            aws_credentials=aws_credentials,
            audio_postprocess_version=audio_postprocess_version,
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
            aws_credentials=_aws_credentials_from_lang_state(lang_state),
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
            aws_credentials=_aws_credentials_from_lang_state(lang_state),
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
    outtro = _load_audio_segment(HSK30_COMMON_OUTTRO)
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

    fixed_assets_ms = len(music) + len(outtro) + len(intro) + sum(len(audio) for audio in part_assets.values())
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
        append_track(outtro)
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
        return _export_audio_atomically(audio_segment, out_path, export_kwargs)

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
        "outtro_ms": len(outtro),
        "part_breakdown": part_breakdown_rendered,
        "reference_duration_ms": reference_duration_ms,
    }

    result = {
        "exam": _normalize_exam_code(report.get("detected_exam", "HSK1001")),
        "output_file": str(out_path),
        "music_ms": len(music),
        "fixed_assets_ms": fixed_assets_ms - len(music),
        "generated_speech_ms": generated_speech_ms,
        "internal_pauses_ms": internal_pauses_ms,
        "answer_silence_ms": adjusted_answer_total,
        "part_transitions_ms": adjusted_transition_total,
        "final_silence_ms": transition_silence_ms["final_silence"],
        "outtro_ms": len(outtro),
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


def _build_hsk2_question_slot(
    item: Dict[str, object],
    *,
    engine: str,
    speed: str,
    lang_state: Dict[str, object],
    cache_dir: Path,
    google_api_key: str,
    tts_provider: Optional[Callable[..., AudioSegment]],
    counts: Dict[str, int],
) -> Tuple[AudioSegment, int]:
    profile = HSK2_AUDIO_PROFILE
    number_audio = _silent_segment(0)
    number_text = _clean_text(item.get("question_number_spoken", ""))
    internal_pause_ms = 0
    if number_text:
        number_audio, reused = _synthesize_tts_segment(
            number_text,
            engine=engine,
            speed=speed,
            voice_name=_narrator_voice_name(lang_state),
            speaker_label="NARRATOR",
            cache_dir=cache_dir,
            google_api_key=google_api_key,
            tts_provider=tts_provider,
            aws_credentials=_aws_credentials_from_lang_state(lang_state),
        )
        if reused:
            counts["reused_segments"] += 1
        else:
            counts["unique_tts_requests"] += 1
            counts["generated_segments"] += 1
        number_audio += _internal_pause_segment(profile["number_to_content_ms"])
        internal_pause_ms += profile["number_to_content_ms"]
        counts["internal_pauses_ms"] += profile["number_to_content_ms"]

    def make_repeat(segments: List[Dict[str, str]]) -> AudioSegment:
        return _dialogue_audio(
            segments,
            engine=engine,
            speed=speed,
            lang_state=lang_state,
            cache_dir=cache_dir,
            google_api_key=google_api_key,
            tts_provider=tts_provider,
            counts=counts,
            gap_ms=profile["question_gap_ms"] if int(item["part"]) == 3 else profile["speaker_gap_ms"],
        )

    repeat1 = make_repeat(item["repeat1"])
    repeat2 = make_repeat(item["repeat2"])
    slot = number_audio + repeat1 + _internal_pause_segment(profile["repeat_gap_ms"]) + repeat2
    internal_pause_ms += profile["repeat_gap_ms"]
    counts["internal_pauses_ms"] += profile["repeat_gap_ms"]
    return slot.set_frame_rate(44100).set_channels(2), internal_pause_ms


def format_hsk2_build_report(report: Dict[str, object]) -> str:
    lines = [
        f"Exam: {report.get('exam', 'HSK2001')}",
        "Level: HSK2",
        f"Output: {report.get('output_file', '')}",
        f"File size: {report.get('file_size_bytes', 0)} bytes",
        "",
        f"Intro music: {report.get('music_ms', 0)} ms",
        f"Fixed intro/part assets: {report.get('fixed_assets_ms', 0)} ms",
        f"Generated speech: {report.get('generated_speech_ms', 0)} ms",
        f"Internal pauses: {report.get('internal_pauses_ms', 0)} ms",
        f"Answer pauses: {report.get('answer_silence_ms', 0)} ms",
        f"Part transitions: {report.get('part_transitions_ms', 0)} ms",
        f"Final silence: {report.get('final_silence_ms', 0)} ms",
        f"Outro: {report.get('outtro_ms', 0)} ms",
        "",
        f"Unique TTS requests: {report.get('unique_tts_requests', 0)}",
        f"Cache reused: {report.get('reused_segments', 0)}",
        f"Total generated segments: {report.get('generated_segments', 0)}",
        f"HSK2 speed profile: {report.get('hsk_speed_profile', 'HSK2')}",
        f"Speech rate: {report.get('speech_rate_label', '')}",
        "",
        f"Reference duration: {report.get('reference_duration_ms', HSK2_AUDIO_PROFILE['reference_duration_ms'])} ms (17:00)",
        f"Actual audio duration: {report.get('final_duration_ms', 0)} ms",
        f"Difference: {report.get('difference_ms', 0)} ms",
        f"Status: {report.get('status', 'INFO')}",
        "",
    ]
    for part in (1, 2, 3):
        lines.append(f"Part{part} duration: {report.get('part_breakdown', {}).get(part, 0)} ms")
    return "\n".join(lines).rstrip() + "\n"


def build_hsk2_phase1b(
    report: Dict[str, object],
    output_file: str | Path,
    *,
    lang_state: Optional[Dict[str, object]] = None,
    tts_provider: Optional[Callable[..., AudioSegment]] = None,
    progress_callback: Optional[Callable[[str], None]] = None,
) -> Dict[str, object]:
    if not report or report.get("level") != "HSK2" or int(report.get("question_count", 0)) != 25:
        raise ValueError("Preview HSK2 không hợp lệ hoặc chưa đủ 25 câu.")
    if report.get("part_counts") != {1: 5, 2: 10, 3: 10}:
        raise ValueError("Preview HSK2 phải có Part 1=5, Part 2=10, Part 3=10.")
    if report.get("missing_invalid_rows"):
        raise ValueError("Preview HSK2 có dòng lỗi: " + "; ".join(report["missing_invalid_rows"]))

    asset_status = validate_hsk30_assets(level="hsk2")
    if not asset_status.get("ok"):
        raise FileNotFoundError("Thiếu asset HSK2: " + ", ".join(asset_status.get("missing", [])))

    lang_state = dict(lang_state or report.get("lang_state", {}) or {})
    engine = _clean_text(lang_state.get("engine", "")) or "Google Cloud TTS"
    hsk_speed_profile = _clean_text(lang_state.get("hsk_speed_profile", "HSK2")) or "HSK2"
    speech_rate = _parse_speech_rate(lang_state.get("speech_rate", 0.70), default=0.70)
    speed = _format_speech_rate(speech_rate)
    google_api_key = _clean_text(lang_state.get("google_api_key", ""))
    if not _clean_text(lang_state.get("zh_male_voice", "")) and not _clean_text(lang_state.get("zh_female_voice", "")):
        raise ValueError("Thiếu voice zh nam/nữ từ Lang.")

    out_path = Path(output_file)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    if progress_callback:
        progress_callback("Đang tải asset HSK2 cố định...")

    music = _load_audio_segment(HSK30_COMMON_ASSET)
    intro = _load_audio_segment(HSK30_HSK2_ASSETS["intro"])
    outtro = _load_audio_segment(HSK30_COMMON_OUTTRO)
    part_assets = {part: _load_audio_segment(HSK30_HSK2_ASSETS[f"part{part}"]) for part in (1, 2, 3)}
    cache_dir = _make_tts_cache_dir()
    counts = {
        "unique_tts_requests": 0,
        "reused_segments": 0,
        "generated_segments": 0,
        "internal_pauses_ms": 0,
    }

    question_plan: List[Dict[str, object]] = []
    generated_speech_ms = 0
    for item in report.get("preview_items", []):
        slot_audio, internal_pause_ms = _build_hsk2_question_slot(
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
        question_plan.append({
            "question_number": int(item["question_number"]),
            "part": int(item["part"]),
            "slot_audio": slot_audio,
            "speech_ms": question_ms,
            "internal_pause_ms": internal_pause_ms,
        })

    profile = HSK2_AUDIO_PROFILE
    answer_silences_ms = [int(profile["answer_pause_by_part"][entry["part"]]) for entry in question_plan]
    transitions = {
        "music_to_intro": int(profile["music_to_intro_ms"]),
        "intro_to_part1": int(profile["intro_to_part1_ms"]),
        "part1_to_q1": int(profile["part_intro_ms"]),
        "part2_to_q6": int(profile["part_intro_ms"]),
        "part3_to_q16": int(profile["part_intro_ms"]),
        "part1_to_part2": int(profile["part_gap_ms"]),
        "part2_to_part3": int(profile["part_gap_ms"]),
        "final_silence": int(profile["final_silence_ms"]),
    }
    part_to_items = {part: [entry for entry in question_plan if entry["part"] == part] for part in (1, 2, 3)}

    def compose_final_audio() -> Tuple[AudioSegment, Dict[int, int]]:
        final_audio = _silent_segment(0)
        local_breakdown = {1: 0, 2: 0, 3: 0}
        answer_index = 0
        final_audio += music
        final_audio += _silent_segment(transitions["music_to_intro"])
        final_audio += intro
        final_audio += _silent_segment(transitions["intro_to_part1"])
        for part in (1, 2, 3):
            final_audio += part_assets[part]
            final_audio += _silent_segment(transitions[{1: "part1_to_q1", 2: "part2_to_q6", 3: "part3_to_q16"}[part]])
            part_start = len(final_audio)
            for entry in part_to_items[part]:
                final_audio += entry["slot_audio"]
                final_audio += _silent_segment(answer_silences_ms[answer_index])
                answer_index += 1
            local_breakdown[part] = len(final_audio) - part_start
            if part < 3:
                final_audio += _silent_segment(transitions[{1: "part1_to_part2", 2: "part2_to_part3"}[part]])
        final_audio += _silent_segment(transitions["final_silence"])
        final_audio += outtro
        return final_audio, local_breakdown

    final_audio, part_breakdown = compose_final_audio()
    normalized_audio = final_audio.set_channels(FINAL_CHANNELS).set_frame_rate(FINAL_SAMPLE_RATE)
    peak = normalized_audio.max_dBFS
    if peak and peak != float("-inf") and peak > -4.0:
        normalized_audio = normalized_audio.apply_gain(-4.0 - peak)
    final_duration_ms = _export_audio_atomically(
        normalized_audio,
        out_path,
        {
            "format": "ipod",
            "codec": "aac",
            "bitrate": FINAL_BITRATE,
            "parameters": ["-ac", str(FINAL_CHANNELS), "-ar", str(FINAL_SAMPLE_RATE)],
        },
    )
    reference_duration_ms = int(profile["reference_duration_ms"])
    fixed_assets_ms = len(intro) + sum(len(audio) for audio in part_assets.values())
    part_transition_total = sum(value for key, value in transitions.items() if key != "final_silence")
    return {
        "exam": _normalize_exam_code(report.get("detected_exam", "HSK2001")),
        "level": "HSK2",
        "output_file": str(out_path),
        "file_size_bytes": out_path.stat().st_size,
        "music_ms": len(music),
        "fixed_assets_ms": fixed_assets_ms,
        "generated_speech_ms": generated_speech_ms,
        "internal_pauses_ms": counts["internal_pauses_ms"],
        "answer_silence_ms": sum(answer_silences_ms),
        "part_transitions_ms": part_transition_total,
        "final_silence_ms": transitions["final_silence"],
        "outtro_ms": len(outtro),
        "part_breakdown": part_breakdown,
        "unique_tts_requests": counts["unique_tts_requests"],
        "reused_segments": counts["reused_segments"],
        "generated_segments": counts["generated_segments"],
        "hsk_speed_profile": hsk_speed_profile,
        "speech_rate": speech_rate,
        "speech_rate_label": _format_hsk_speed_label(hsk_speed_profile, speech_rate),
        "reference_duration_ms": reference_duration_ms,
        "actual_duration_ms": final_duration_ms,
        "difference_ms": final_duration_ms - reference_duration_ms,
        "final_duration_ms": final_duration_ms,
        "status": "INFO",
        "audio_path": str(out_path),
        "transition_silences": transitions,
        "answer_silences": answer_silences_ms,
        "question_voice_rule": report.get("question_voice_rule", ""),
    }


# HSK3 keeps the shared TTS/cache/export helpers but has its own workbook
# grammar and acoustic timing profile.
def _parse_hsk3_segments(text: str, *, require_question: bool) -> List[Dict[str, str]]:
    source = _clean_text(text)
    matches = list(re.finditer(r"(男|女|问)\s*[：:]", source))
    if not matches or matches[0].start() != 0:
        raise ValueError("Nội dung phải bắt đầu bằng 男：/女：/问：")
    segments: List[Dict[str, str]] = []
    for index, match in enumerate(matches):
        token = match.group(1)
        end = matches[index + 1].start() if index + 1 < len(matches) else len(source)
        spoken = _clean_text(source[match.end():end])
        if not spoken:
            raise ValueError(f"Segment {token}： bị rỗng")
        segments.append({
            "speaker_token": token,
            "speaker_label": {"男": "NAM", "女": "NỮ", "问": "NỮ"}[token],
            "source_text": f"{token}：{spoken}",
            "text": _strip_speaker_prefix(spoken),
        })
    if require_question:
        if len(segments) < 3 or segments[-1]["speaker_token"] != "问":
            raise ValueError("Part 2 phải có ít nhất 2 lượt thoại và một 问 ở cuối")
        if any(segment["speaker_token"] == "问" for segment in segments[:-1]):
            raise ValueError("Part 2 chỉ cho phép 问 ở segment cuối")
        if any(segment["speaker_token"] not in {"男", "女"} for segment in segments[:-1]):
            raise ValueError("Part 2 có prefix thoại không hợp lệ")
    elif len(segments) != 2 or {segment["speaker_token"] for segment in segments} != {"男", "女"}:
        raise ValueError("Part 1 phải có đúng một segment 男 và một segment 女")
    return segments


def _split_hsk3_question(text: str) -> Tuple[str, str]:
    source = _clean_text(text)
    matches = list(re.finditer(r"问\s*[：:]", source))
    if len(matches) != 1:
        raise ValueError("Part 3 phải có đúng một 问： ở cuối")
    passage = _clean_text(source[:matches[0].start()])
    question = _strip_speaker_prefix(source[matches[0].end():])
    if not question:
        raise ValueError("Câu hỏi Part 3 bị rỗng")
    return passage, question


def _validate_hsk3_shared_pair(q29_text: str, q30_text: str) -> Tuple[str, str, str]:
    passage, question29 = _split_hsk3_question(q29_text)
    q30_passage, question30 = _split_hsk3_question(q30_text)
    if not passage:
        raise ValueError("Q29 phải có shared passage")
    if q30_passage and q30_passage != passage:
        similarity = SequenceMatcher(None, passage, q30_passage).ratio()
        if similarity < 0.90:
            raise ValueError("Q30 chỉ được lặp lại shared passage tương ứng của Q29")
    return passage, question29, question30


def hsk3_shared_block_timeline() -> List[str]:
    q29_first = [
        "shared_passage", "passage_to_number_gap", "number29",
        "number_to_question_gap", "question29",
    ]
    q30_pass = [
        "number30", "number_to_question_gap", "question30",
    ]
    q29_second = [*q29_first, "answer_pause"]
    q30_second = [*q30_pass, "answer_pause"]
    return [
        "cue_q29_30", "shared_cue_to_passage_gap",
        *q29_first, "repeat_gap", *q29_second,
        *q30_pass, "repeat_gap", *q30_second,
        "outro",
    ]


def hsk3_repeat_audit(preview_items: List[Dict[str, object]]) -> Dict[str, object]:
    """Return the semantic occurrence counts expected from the HSK3 builder."""
    items = {int(item["question_number"]): item for item in preview_items}
    part1_segments = sum(len(items[number]["repeat1"]) for number in range(1, 11)) * 2
    part2_segments = sum(len(items[number]["repeat1"]) for number in range(11, 21)) * 2
    part3_segments = sum(len(items[number]["repeat1"]) for number in range(21, 29)) * 2
    shared_segments = 3 * 2  # passage, Q29 question, Q30 question per pass
    return {
        "part1_questions": {"questions": 10, "repeats_per_question": 2, "semantic_segment_occurrences": part1_segments},
        "part2_questions": {"questions": 10, "repeats_per_question": 2, "semantic_segment_occurrences": part2_segments},
        "part3_questions_21_28": {"questions": 8, "repeats_per_question": 2, "semantic_segment_occurrences": part3_segments},
        "q29_q30_shared_block": {
            "passages": 2,
            "q29_questions": 2,
            "q30_questions": 2,
            "semantic_segment_occurrences": shared_segments,
            "order": ["q29_pass_1", "q29_pass_2", "q30_pass_1", "q30_pass_2"],
        },
        "question_number_occurrences": 28 + 2 + 2,
    }


def _hsk3_number_value(text: str) -> Optional[int]:
    clean = _clean_text(text)
    units = {"一": 1, "二": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7, "八": 8, "九": 9}
    if clean in units:
        return units[clean]
    if clean == "十":
        return 10
    if clean.startswith("十") and clean[1:] in units:
        return 10 + units[clean[1:]]
    if len(clean) == 2 and clean[0] in units and clean[1] == "十":
        return units[clean[0]] * 10
    if len(clean) == 3 and clean[0] in units and clean[1] == "十" and clean[2] in units:
        return units[clean[0]] * 10 + units[clean[2]]
    return None


def _resolve_hsk3_sheet_name(excel_path: str | Path, requested: str) -> str:
    path = Path(excel_path)
    with zipfile.ZipFile(path) as zf:
        sheet_names = list(_sheet_target_map(zf))
    if requested in sheet_names:
        return requested
    if len(sheet_names) == 1:
        return sheet_names[0]
    raise ValueError(f"Không tìm thấy sheet '{requested}' trong workbook HSK3")


def _normalize_hsk3_header(value: object) -> str:
    text = unicodedata.normalize("NFKD", _clean_text(value))
    text = "".join(char for char in text if not unicodedata.combining(char))
    text = text.replace("đ", "d").replace("Đ", "D")
    return re.sub(r"[^a-z0-9\u4e00-\u9fff]+", "", text.lower())


def _hsk3_required_columns(header: Dict[str, str]) -> Dict[str, str]:
    aliases = {
        "exam": {"made", "exam", "examcode"},
        "question_number": {"cau", "socau", "question", "questionnumber"},
        "content": {"noidungphannghe", "phude", "subtitle", "listeningcontent"},
    }
    normalized_columns = {
        _normalize_hsk3_header(value): column for column, value in header.items() if _clean_text(value)
    }
    missing = [name for name, accepted in aliases.items() if not any(key in normalized_columns for key in accepted)]
    if missing:
        labels = {"exam": "Mã đề", "question_number": "Câu", "content": "Nội dung phần nghe"}
        raise ValueError("Workbook HSK3 thiếu cột bắt buộc: " + ", ".join(labels[name] for name in missing))
    return {
        name: next(normalized_columns[key] for key in accepted if key in normalized_columns)
        for name, accepted in aliases.items()
    }


def _is_hsk3_example_row(question_value: object) -> bool:
    return _normalize_hsk3_header(question_value) in {"vidu", "mau", "example", "shili", "li"}


def _load_hsk3_exam_rows(excel_path: str | Path, sheet_name: str) -> Dict[str, object]:
    rows = read_xlsx_rows(excel_path, sheet_name=sheet_name)
    if not rows:
        raise ValueError("Workbook HSK3 không có dữ liệu")

    columns = _hsk3_required_columns(rows[0].cells)
    exams: Dict[str, List[XlsxRow]] = {}
    current_exam = ""
    invalid_rows: List[str] = []
    ignored_example_rows = 0
    for row in rows[1:]:
        explicit_exam = _normalize_exam_code(row.cells.get(columns["exam"], ""))
        if explicit_exam:
            current_exam = explicit_exam

        question_text = _clean_text(row.cells.get(columns["question_number"], ""))
        content_text = _clean_text(row.cells.get(columns["content"], ""))
        if _is_hsk3_example_row(question_text) or (not question_text and not content_text):
            ignored_example_rows += 1
            continue
        if not current_exam:
            invalid_rows.append(f"Dòng {row.row_number}: thiếu mã đề trước khi forward-fill")
            continue
        if not re.fullmatch(r"HSK3-30-\d{2}", current_exam):
            invalid_rows.append(f"Dòng {row.row_number}: mã đề không hợp lệ '{current_exam}'")
            continue

        exams.setdefault(current_exam, []).append(
            XlsxRow(
                row.row_number,
                {
                    **row.cells,
                    "A": current_exam,
                    "_hsk3_question_number": question_text,
                    "_hsk3_content": content_text,
                },
            )
        )

    return {
        "columns": columns,
        "exams": exams,
        "invalid_rows": invalid_rows,
        "ignored_example_rows": ignored_example_rows,
    }


def validate_hsk3_workbook(excel_path: str | Path, sheet_name: str = "Sheet1") -> Dict[str, object]:
    sheet_name = _resolve_hsk3_sheet_name(excel_path, sheet_name)
    loaded = _load_hsk3_exam_rows(excel_path, sheet_name)
    exams = loaded["exams"]
    invalid_rows = list(loaded["invalid_rows"])

    expected_exams = [f"HSK3-30-{index:02d}" for index in range(1, 17)]
    summaries: Dict[str, Dict[str, object]] = {}
    for exam in sorted(exams):
        exam_rows = exams[exam]
        numbers = [_hsk3_number_value(row.cells.get("_hsk3_question_number", "")) for row in exam_rows]
        summaries[exam] = {
            "row_count": len(exam_rows),
            "numbers": numbers,
            "part_counts": {1: sum(1 for number in numbers if number and number <= 10),
                             2: sum(1 for number in numbers if number and 11 <= number <= 20),
                             3: sum(1 for number in numbers if number and 21 <= number <= 30)},
        }
    structural_errors = list(invalid_rows)
    semantic_errors: List[str] = []
    part1_patterns: Counter[str] = Counter()
    part2_patterns: Counter[str] = Counter()
    part3_parsed = 0
    for exam in expected_exams:
        summary = summaries.get(exam)
        if summary is None:
            structural_errors.append(f"Thiếu {exam}")
            continue
        if summary["row_count"] != 30 or summary["numbers"] != list(range(1, 31)):
            structural_errors.append(f"{exam}: cần 30 câu số 一 đến 三十")
            continue
        for row in exams[exam]:
            number = _hsk3_number_value(row.cells.get("_hsk3_question_number", ""))
            subtitle = _clean_text(row.cells.get("_hsk3_content", ""))
            try:
                if number is None or not subtitle:
                    raise ValueError("thiếu số câu hoặc nội dung")
                if number <= 10:
                    segments = _parse_hsk3_segments(subtitle, require_question=False)
                    part1_patterns["→".join(segment["speaker_token"] for segment in segments)] += 1
                elif number <= 20:
                    segments = _parse_hsk3_segments(subtitle, require_question=True)
                    part2_patterns["→".join(segment["speaker_token"] for segment in segments)] += 1
                else:
                    passage, _question = _split_hsk3_question(subtitle)
                    if number < 30 and not passage:
                        raise ValueError(f"Q{number} phải có narration")
                    part3_parsed += 1
            except ValueError as exc:
                semantic_errors.append(f"{exam} dòng {row.row_number}: {exc}")
        question_texts = {
            _hsk3_number_value(row.cells.get("_hsk3_question_number", "")): row.cells.get("_hsk3_content", "")
            for row in exams[exam]
        }
        try:
            _validate_hsk3_shared_pair(question_texts[29], question_texts[30])
        except (KeyError, ValueError) as exc:
            semantic_errors.append(f"{exam}: {exc}")
    structural_errors.extend(semantic_errors)
    return {
        "workbook": str(Path(excel_path)),
        "sheet": sheet_name,
        "source_columns": loaded["columns"],
        "exam_count": len(exams),
        "exam_codes": sorted(exams),
        "ignored_example_rows": loaded["ignored_example_rows"],
        "missing_exams": [exam for exam in expected_exams if exam not in exams],
        "invalid_rows": structural_errors,
        "semantic_errors": semantic_errors,
        "part1_patterns": dict(part1_patterns),
        "part2_patterns": dict(part2_patterns),
        "part3_parsed": part3_parsed,
        "exams": summaries,
        "status": "PASS" if not structural_errors and sorted(exams) == expected_exams else "FAIL",
    }


def parse_hsk3_phase1(
    excel_path: str | Path,
    sheet_name: str = "Sheet1",
    exam_code: str = "HSK3-30-01",
    asset_root: str | Path | None = None,
    lang_state: Optional[Dict[str, object]] = None,
) -> Dict[str, object]:
    sheet_name = _resolve_hsk3_sheet_name(excel_path, sheet_name)
    structure = validate_hsk3_workbook(excel_path, sheet_name=sheet_name)
    if structure.get("status") != "PASS":
        raise ValueError("Workbook HSK3 không hợp lệ: " + "; ".join(structure.get("invalid_rows", [])))
    normalized_exam = _normalize_exam_code(exam_code)
    if normalized_exam not in structure["exams"]:
        raise ValueError(f"Không tìm thấy dữ liệu cho {normalized_exam}")
    loaded = _load_hsk3_exam_rows(excel_path, sheet_name)
    selected = loaded["exams"].get(normalized_exam, [])

    lang_state = dict(lang_state or {})
    rate = _parse_speech_rate(lang_state.get("speech_rate", 0.75), default=0.75)
    profile = _clean_text(lang_state.get("hsk_speed_profile", "HSK3")) or "HSK3"
    items: List[Dict[str, object]] = []
    invalid: List[str] = []
    part_counts = {1: 0, 2: 0, 3: 0}
    for row in selected:
        number_text = _clean_text(row.cells.get("_hsk3_question_number", ""))
        number = _hsk3_number_value(number_text)
        subtitle = _clean_text(row.cells.get("_hsk3_content", ""))
        if number is None or not subtitle:
            invalid.append(f"Dòng {row.row_number}: thiếu Câu hoặc Nội dung phần nghe")
            continue
        part = 1 if number <= 10 else (2 if number <= 20 else 3)
        try:
            if part == 1:
                segments = _parse_hsk3_segments(subtitle, require_question=False)
                repeat1 = segments
                repeat2 = [dict(segment) for segment in segments]
            elif part == 2:
                segments = _parse_hsk3_segments(subtitle, require_question=True)
                repeat1 = segments
                repeat2 = [dict(segment) for segment in segments]
            else:
                passage, question = _split_hsk3_question(subtitle)
                if number == 29 and not passage:
                    raise ValueError("Q29 phải có shared passage")
                narration_label = "NỮ" if number % 2 else "NAM"
                repeat1 = [] if number == 30 or not passage else [{"speaker_label": narration_label, "speaker_token": "", "source_text": passage, "text": passage}]
                repeat1.append({"speaker_label": "NỮ", "speaker_token": "问", "source_text": f"问：{question}", "text": question})
                repeat2 = [dict(segment) for segment in repeat1]
        except ValueError as exc:
            invalid.append(f"Dòng {row.row_number}: {exc}")
            continue
        part_counts[part] += 1
        item = {
            "question_number": number,
            "part": part,
            "source_row": row.row_number,
            "source_exam": normalized_exam,
            "source_number": number_text,
            "question_number_spoken": _spoken_question_number(number_text),
            "number_speaker_label": "NỮ",
            "source_text": subtitle,
            "repeat1": repeat1,
            "repeat2": repeat2,
        }
        item["preview"] = _build_item_preview(item)
        items.append(item)

    item_map = {int(item["question_number"]): item for item in items}
    if 29 in item_map and 30 in item_map:
        try:
            _validate_hsk3_shared_pair(item_map[29]["source_text"], item_map[30]["source_text"])
        except ValueError as exc:
            invalid.append(str(exc))

    report_lang = {
        "engine": lang_state.get("engine", ""), "speed": lang_state.get("speed", ""), "voice": lang_state.get("voice", ""),
        "zh_male_voice": lang_state.get("zh_male_voice", ""), "zh_female_voice": lang_state.get("zh_female_voice", ""),
        "zh_narrator_voice": lang_state.get("zh_narrator_voice", ""), "hsk_speed_profile": profile,
        "speech_rate": rate, "speech_rate_label": _format_hsk_speed_label(profile, rate),
    }
    return {
        "level": "HSK3", "workbook": str(Path(excel_path)), "sheet": sheet_name, "detected_exam": normalized_exam,
        "question_count": len(items), "part_counts": part_counts, "missing_invalid_rows": invalid,
        "ignored_example_rows": structure.get("ignored_example_rows", 0),
        "asset_status": validate_hsk30_assets(asset_root=asset_root, level="hsk3"), "lang_state": report_lang,
        "output_filename": phase1_output_filename(normalized_exam), "reference_duration_ms": HSK3_AUDIO_PROFILE["reference_duration_ms"],
        "sample_reference_duration_ms": HSK3_AUDIO_PROFILE["sample_reference_duration_ms"], "tts_requests_made": 0,
        "preview_items": sorted(items, key=lambda item: int(item["question_number"])),
        "shared_q29_q30_ok": any(int(item["question_number"]) == 29 for item in items) and any(int(item["question_number"]) == 30 for item in items),
        "part1_pattern_stats": structure.get("part1_patterns", {}),
        "part2_pattern_stats": structure.get("part2_patterns", {}),
        "part3_parse_count": structure.get("part3_parsed", 0),
        "shared_block_timeline": hsk3_shared_block_timeline(),
        "structure": structure,
        "status": "READY_FOR_REVIEW" if not invalid and len(items) == 30 and part_counts == {1: 10, 2: 10, 3: 10} else "REVIEW_WITH_WARNINGS",
    }


def format_hsk3_preview_report(report: Dict[str, object]) -> str:
    lines = [
        f"{report.get('detected_exam', 'HSK3-30-01')} parse summary", "Level: HSK3",
        f"Workbook: {report.get('workbook', '')}", f"Questions: {report.get('question_count', 0)}",
        f"Part 1: {report.get('part_counts', {}).get(1, 0)}", f"Part 2: {report.get('part_counts', {}).get(2, 0)}",
        f"Part 3: {report.get('part_counts', {}).get(3, 0)}", f"Missing/invalid rows: {len(report.get('missing_invalid_rows', []))}",
        f"Asset status: {'OK' if report.get('asset_status', {}).get('ok') else 'MISSING'}",
        f"HSK speed profile: {report.get('lang_state', {}).get('hsk_speed_profile', 'HSK3')}",
        f"Speech rate: {report.get('lang_state', {}).get('speech_rate_label', '')}",
        "Shared Q29–30 block: OK" if report.get("shared_q29_q30_ok") else "Shared Q29–30 block: ERROR",
        f"Part 1 patterns: {report.get('part1_pattern_stats', {})}",
        f"Part 2 patterns: {report.get('part2_pattern_stats', {})}",
        f"Part 3 parsed across workbook: {report.get('part3_parse_count', 0)}",
        f"Ignored example rows: {report.get('ignored_example_rows', 0)}",
        "TTS requests in dry run: 0", "",
    ]
    for item in report.get("preview_items", []):
        lines.extend([f"Q{item['question_number']:02d} — Part {item['part']}", f"Source row: {item['source_row']}", f"Number spoken: NỮ [{item['question_number_spoken']}]"])
        lines.append("repeat1:")
        lines.extend(f"  {_render_segment(segment['speaker_label'], segment['text'])}" for segment in item["repeat1"])
        lines.append("repeat2:")
        lines.extend(f"  {_render_segment(segment['speaker_label'], segment['text'])}" for segment in item["repeat2"])
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def _hsk3_number_audio(item: Dict[str, object], *, engine: str, speed: str, lang_state: Dict[str, object], cache_dir: Path, google_api_key: str, tts_provider: Optional[Callable[..., AudioSegment]], counts: Dict[str, int]) -> AudioSegment:
    audio, reused = _synthesize_tts_segment(
        str(item["question_number_spoken"]), engine=engine, speed=speed,
        voice_name=_narrator_voice_name(lang_state), speaker_label="NARRATOR",
        cache_dir=cache_dir, google_api_key=google_api_key, tts_provider=tts_provider,
        aws_credentials=_aws_credentials_from_lang_state(lang_state),
        audio_postprocess_version=HSK3_TTS_POSTPROCESS_VERSION,
    )
    if reused:
        counts["reused_segments"] += 1
    else:
        counts["unique_tts_requests"] += 1
        counts["generated_segments"] += 1
    return audio


def _hsk3_segment_gaps(part: int, segments: List[Dict[str, str]]) -> List[int]:
    if len(segments) < 2:
        return []
    if part == 1:
        return [int(HSK3_AUDIO_PROFILE["speaker_gap_ms"])] * (len(segments) - 1)
    if part == 2:
        return [
            int(HSK3_AUDIO_PROFILE["question_gap_ms"] if index == len(segments) - 2 else HSK3_AUDIO_PROFILE["speaker_gap_ms"])
            for index in range(len(segments) - 1)
        ]
    return [int(HSK3_AUDIO_PROFILE["question_gap_ms"])] * (len(segments) - 1)


def _hsk3_sequence_audio(segments: List[Dict[str, str]], *, part: int, engine: str, speed: str, lang_state: Dict[str, object], cache_dir: Path, google_api_key: str, tts_provider: Optional[Callable[..., AudioSegment]], counts: Dict[str, int]) -> Tuple[AudioSegment, int, int]:
    gaps = _hsk3_segment_gaps(part, segments)
    audio = _silent_segment(0)
    spoken_ms = 0
    for index, segment in enumerate(segments):
        segment_audio = _dialogue_audio(
            [segment], engine=engine, speed=speed, lang_state=lang_state, cache_dir=cache_dir,
            google_api_key=google_api_key, tts_provider=tts_provider, counts=counts, gap_ms=0,
            audio_postprocess_version=HSK3_TTS_POSTPROCESS_VERSION,
        )
        audio += segment_audio
        spoken_ms += len(segment_audio)
        if index < len(gaps):
            audio += _internal_pause_segment(gaps[index])
            counts["internal_pauses_ms"] += gaps[index]
            if part == 1 or (part == 2 and index < len(segments) - 2):
                counts["speaker_gap_ms"] += gaps[index]
            else:
                counts["question_gap_ms"] += gaps[index]
    return audio, sum(gaps), spoken_ms


def _hsk3_question_slot(item: Dict[str, object], *, engine: str, speed: str, lang_state: Dict[str, object], cache_dir: Path, google_api_key: str, tts_provider: Optional[Callable[..., AudioSegment]], counts: Dict[str, int]) -> Tuple[AudioSegment, int, int]:
    profile = HSK3_AUDIO_PROFILE
    number_audio = _hsk3_number_audio(item, engine=engine, speed=speed, lang_state=lang_state, cache_dir=cache_dir, google_api_key=google_api_key, tts_provider=tts_provider, counts=counts)
    part = int(item["part"])
    repeat1, repeat1_gap_ms, repeat1_spoken_ms = _hsk3_sequence_audio(item["repeat1"], part=part, engine=engine, speed=speed, lang_state=lang_state, cache_dir=cache_dir, google_api_key=google_api_key, tts_provider=tts_provider, counts=counts)
    repeat2, repeat2_gap_ms, repeat2_spoken_ms = _hsk3_sequence_audio(item["repeat2"], part=part, engine=engine, speed=speed, lang_state=lang_state, cache_dir=cache_dir, google_api_key=google_api_key, tts_provider=tts_provider, counts=counts)
    dialogue_pause_ms = repeat1_gap_ms + repeat2_gap_ms
    pauses = int(profile["number_to_content_ms"]) + int(profile["repeat_gap_ms"]) + dialogue_pause_ms
    slot = number_audio + _internal_pause_segment(profile["number_to_content_ms"]) + repeat1 + _internal_pause_segment(profile["repeat_gap_ms"]) + repeat2
    counts["internal_pauses_ms"] += int(profile["number_to_content_ms"]) + int(profile["repeat_gap_ms"])
    counts["number_to_content_ms"] += int(profile["number_to_content_ms"])
    counts["repeat_gap_ms"] += int(profile["repeat_gap_ms"])
    spoken_ms = len(number_audio) + repeat1_spoken_ms + repeat2_spoken_ms
    return slot.set_frame_rate(44100).set_channels(2), pauses, spoken_ms


def build_hsk3_phase1b(
    report: Dict[str, object],
    output_file: str | Path,
    *,
    lang_state: Optional[Dict[str, object]] = None,
    tts_provider: Optional[Callable[..., AudioSegment]] = None,
    progress_callback: Optional[Callable[[str], None]] = None,
) -> Dict[str, object]:
    if not report or report.get("level") != "HSK3" or int(report.get("question_count", 0)) != 30:
        raise ValueError("Preview HSK3 không hợp lệ hoặc chưa đủ 30 câu.")
    if report.get("part_counts") != {1: 10, 2: 10, 3: 10}:
        raise ValueError("Preview HSK3 phải có Part 1=10, Part 2=10, Part 3=10.")
    if report.get("missing_invalid_rows"):
        raise ValueError("Preview HSK3 có dòng lỗi: " + "; ".join(report["missing_invalid_rows"]))
    if not report.get("shared_q29_q30_ok"):
        raise ValueError("Preview HSK3 thiếu shared Q29–Q30 block.")
    asset_status = validate_hsk30_assets(level="hsk3")
    if not asset_status.get("ok"):
        raise FileNotFoundError("Thiếu asset HSK3: " + ", ".join(asset_status.get("missing", [])))

    lang_state = dict(lang_state or report.get("lang_state", {}) or {})
    engine = _clean_text(lang_state.get("engine", "")) or "Google Cloud TTS"
    speed_rate = _parse_speech_rate(lang_state.get("speech_rate", 0.75), default=0.75)
    speed = _format_speech_rate(speed_rate)
    profile_name = _clean_text(lang_state.get("hsk_speed_profile", "HSK3")) or "HSK3"
    google_api_key = _clean_text(lang_state.get("google_api_key", ""))
    if not _clean_text(lang_state.get("zh_male_voice", "")) and not _clean_text(lang_state.get("zh_female_voice", "")):
        raise ValueError("Thiếu voice zh nam/nữ từ Lang.")

    out_path = Path(output_file)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    if progress_callback:
        progress_callback("Đang tải asset HSK3 cố định...")
    music = _load_audio_segment(HSK30_COMMON_ASSET)
    fixed = {name: _load_audio_segment(path) for name, path in HSK30_HSK3_ASSETS.items()}
    outtro = _load_audio_segment(HSK30_COMMON_OUTTRO)
    cache_dir = _make_tts_cache_dir()
    counts = {
        "unique_tts_requests": 0, "reused_segments": 0, "generated_segments": 0,
        "internal_pauses_ms": 0, "number_to_content_ms": 0, "speaker_gap_ms": 0,
        "question_gap_ms": 0, "repeat_gap_ms": 0, "shared_block_gap_ms": 0,
    }
    profile = HSK3_AUDIO_PROFILE
    items = {int(item["question_number"]): item for item in report["preview_items"]}
    question_audio: Dict[int, AudioSegment] = {}
    question_internal: Dict[int, int] = {}
    speech_ms = 0
    for number in range(1, 29):
        slot, internal_ms, spoken_ms = _hsk3_question_slot(items[number], engine=engine, speed=speed, lang_state=lang_state, cache_dir=cache_dir, google_api_key=google_api_key, tts_provider=tts_provider, counts=counts)
        question_audio[number] = slot
        question_internal[number] = internal_ms
        speech_ms += spoken_ms

    # Q29/Q30 are repeated as one shared passage block. The passage is cached
    # after pass 1 and loaded again for pass 2.
    q29, q30 = items[29], items[30]
    passage = q29["repeat1"][0]
    passage_audio = _dialogue_audio(
        [passage], engine=engine, speed=speed, lang_state=lang_state, cache_dir=cache_dir,
        google_api_key=google_api_key, tts_provider=tts_provider, counts=counts, gap_ms=0,
        audio_postprocess_version=HSK3_TTS_POSTPROCESS_VERSION,
    )
    q29_question = q29["repeat1"][1]
    q30_question = q30["repeat1"][0]
    def synth_question(segment: Dict[str, str]) -> AudioSegment:
        audio = _dialogue_audio(
            [segment], engine=engine, speed=speed, lang_state=lang_state, cache_dir=cache_dir,
            google_api_key=google_api_key, tts_provider=tts_provider, counts=counts, gap_ms=0,
            audio_postprocess_version=HSK3_TTS_POSTPROCESS_VERSION,
        )
        return audio
    q29_number = _hsk3_number_audio(q29, engine=engine, speed=speed, lang_state=lang_state, cache_dir=cache_dir, google_api_key=google_api_key, tts_provider=tts_provider, counts=counts)
    q30_number = _hsk3_number_audio(q30, engine=engine, speed=speed, lang_state=lang_state, cache_dir=cache_dir, google_api_key=google_api_key, tts_provider=tts_provider, counts=counts)
    q29_question_audio = synth_question(q29_question)
    q30_question_audio = synth_question(q30_question)
    passage_to_number = _internal_pause_segment(profile["passage_to_number_ms"])
    number_to_question = _internal_pause_segment(profile["number_to_question_ms"])
    q29_answer = _internal_pause_segment(profile["answer_pause_by_part"][3])
    q30_answer = _internal_pause_segment(profile["answer_pause_by_part"][3])
    q29_block_pass = passage_audio + passage_to_number + q29_number + number_to_question + q29_question_audio
    q29_block_repeat = passage_audio + passage_to_number + q29_number + number_to_question + q29_question_audio + q29_answer
    q30_block_pass = q30_number + number_to_question + q30_question_audio
    q30_block_repeat = q30_number + number_to_question + q30_question_audio + q30_answer
    q29_block = q29_block_pass + _internal_pause_segment(profile["repeat_gap_ms"]) + q29_block_repeat
    q29_block += q30_block_pass + _internal_pause_segment(profile["repeat_gap_ms"]) + q30_block_repeat
    counts["internal_pauses_ms"] += 2 * (profile["passage_to_number_ms"] + 2 * profile["number_to_question_ms"])
    counts["internal_pauses_ms"] += 2 * profile["repeat_gap_ms"]
    counts["repeat_gap_ms"] += 2 * profile["repeat_gap_ms"]
    counts["shared_block_gap_ms"] += 2 * (profile["passage_to_number_ms"] + 2 * profile["number_to_question_ms"])
    speech_ms += 2 * (len(passage_audio) + len(q29_number) + len(q30_number) + len(q29_question_audio) + len(q30_question_audio))

    transitions = {
        "music_to_intro": profile["music_to_intro_ms"], "intro_to_part1": profile["intro_to_part1_ms"],
        "part1_to_part2": profile["part_gap_ms"], "part2_to_part3": profile["part_gap_ms"],
        "cue_to_number": profile["cue_to_number_ms"], "shared_cue_to_passage": profile["shared_cue_to_passage_ms"],
        "final_silence": profile["final_silence_ms"],
    }
    answer_pauses = [profile["answer_pause_by_part"][1]] * 10 + [profile["answer_pause_by_part"][2]] * 10 + [profile["answer_pause_by_part"][3]] * 10
    part_audio = {1: [], 2: [], 3: []}
    for number in range(1, 29):
        part_audio[1 if number <= 10 else (2 if number <= 20 else 3)].append(question_audio[number])

    final_audio = music + _silent_segment(transitions["music_to_intro"]) + fixed["intro"] + _silent_segment(transitions["intro_to_part1"])
    part_breakdown: Dict[int, int] = {}
    final_audio += fixed["part1"] + fixed["cue_q1_5"] + _silent_segment(transitions["cue_to_number"])
    part1_start = len(final_audio)
    for number in range(1, 6): final_audio += question_audio[number] + _internal_pause_segment(answer_pauses[number - 1])
    final_audio += fixed["cue_q6_10"] + _silent_segment(transitions["cue_to_number"])
    for number in range(6, 11): final_audio += question_audio[number] + _internal_pause_segment(answer_pauses[number - 1])
    part_breakdown[1] = len(final_audio) - part1_start
    final_audio += _silent_segment(transitions["part1_to_part2"]) + fixed["part2"] + fixed["cue_q11_20"] + _silent_segment(transitions["cue_to_number"])
    part2_start = len(final_audio)
    for number in range(11, 21): final_audio += question_audio[number] + _internal_pause_segment(answer_pauses[number - 1])
    part_breakdown[2] = len(final_audio) - part2_start
    final_audio += _silent_segment(transitions["part2_to_part3"]) + fixed["part3"] + fixed["cue_q21_28"] + _silent_segment(transitions["cue_to_number"])
    part3_start = len(final_audio)
    for number in range(21, 29): final_audio += question_audio[number] + _internal_pause_segment(answer_pauses[number - 1])
    final_audio += fixed["cue_q29_30"] + _silent_segment(transitions["shared_cue_to_passage"]) + q29_block
    part_breakdown[3] = len(final_audio) - part3_start
    final_audio += _silent_segment(transitions["final_silence"]) + outtro
    normalized_audio = final_audio.set_channels(FINAL_CHANNELS).set_frame_rate(FINAL_SAMPLE_RATE)
    peak = normalized_audio.max_dBFS
    if peak and peak != float("-inf") and peak > -4.0:
        normalized_audio = normalized_audio.apply_gain(-4.0 - peak)
    final_duration_ms = _export_audio_atomically(
        normalized_audio,
        out_path,
        {
            "format": "ipod",
            "codec": "aac",
            "bitrate": FINAL_BITRATE,
            "parameters": ["-ac", str(FINAL_CHANNELS), "-ar", str(FINAL_SAMPLE_RATE)],
        },
    )
    sample_difference_ms = final_duration_ms - HSK3_AUDIO_PROFILE["sample_reference_duration_ms"]
    duration_qa = "WARN" if abs(sample_difference_ms) > 3 * 60 * 1000 else "INFO"
    fixed_assets_ms = len(fixed["intro"]) + len(fixed["part1"]) + len(fixed["part2"]) + len(fixed["part3"])
    fixed_cue_ms = sum(len(fixed[key]) for key in ("cue_q1_5", "cue_q6_10", "cue_q11_20", "cue_q21_28", "cue_q29_30"))
    return {
        "exam": _normalize_exam_code(report["detected_exam"]), "level": "HSK3", "output_file": str(out_path), "file_size_bytes": out_path.stat().st_size,
        "music_ms": len(music), "fixed_assets_ms": fixed_assets_ms, "fixed_cue_ms": fixed_cue_ms, "generated_speech_ms": speech_ms,
        "internal_pauses_ms": counts["internal_pauses_ms"], "answer_pause_ms": sum(answer_pauses[:28]) + 2 * profile["answer_pause_by_part"][3],
        "number_to_content_ms": counts["number_to_content_ms"], "speaker_gap_ms": counts["speaker_gap_ms"],
        "question_gap_ms": counts["question_gap_ms"], "repeat_gap_ms": counts["repeat_gap_ms"],
        "shared_block_gap_ms": counts["shared_block_gap_ms"],
        "part_transitions_ms": transitions["music_to_intro"] + transitions["intro_to_part1"] + transitions["part1_to_part2"] + transitions["part2_to_part3"] + 4 * transitions["cue_to_number"] + transitions["shared_cue_to_passage"],
        "final_silence_ms": transitions["final_silence"], "outtro_ms": len(outtro), "part_breakdown": part_breakdown,
        "unique_tts_requests": counts["unique_tts_requests"], "reused_segments": counts["reused_segments"], "generated_segments": counts["generated_segments"],
        "hsk_speed_profile": profile_name, "speech_rate": speed_rate, "speech_rate_label": _format_hsk_speed_label(profile_name, speed_rate),
        "reference_duration_ms": HSK3_AUDIO_PROFILE["reference_duration_ms"], "sample_reference_duration_ms": HSK3_AUDIO_PROFILE["sample_reference_duration_ms"],
        "actual_duration_ms": final_duration_ms, "final_duration_ms": final_duration_ms, "difference_ms": final_duration_ms - HSK3_AUDIO_PROFILE["reference_duration_ms"],
        "sample_difference_ms": sample_difference_ms, "duration_qa": duration_qa,
        "status": "INFO", "audio_path": str(out_path), "q29_q30_shared_passage_reused": True, "transition_silences": transitions,
        "shared_block_timeline": hsk3_shared_block_timeline(),
        "repeat_audit": hsk3_repeat_audit(report["preview_items"]),
        "tts_postprocess_version": HSK3_TTS_POSTPROCESS_VERSION,
        "edge_trim_threshold_db": HSK3_EDGE_SILENCE_THRESHOLD_DB,
        "edge_trim_safety_pad_ms": HSK3_EDGE_SAFETY_PAD_MS,
    }


def _hsk3_speed_comparison_groups(report: Dict[str, object]) -> List[Tuple[str, List[Dict[str, str]]]]:
    items = {int(item["question_number"]): item for item in report["preview_items"]}
    return [
        ("Q1", list(items[1]["repeat1"])),
        ("Q11", list(items[11]["repeat1"])),
        ("Q21", list(items[21]["repeat1"])),
        ("Q29 passage", [items[29]["repeat1"][0]]),
    ]


def _hsk3_speech_occurrences(report: Dict[str, object]) -> List[Tuple[str, str, int]]:
    """Return semantic TTS uses in the full exam, excluding all pauses."""
    items = {int(item["question_number"]): item for item in report["preview_items"]}
    occurrences: List[Tuple[str, str, int]] = []
    for number in range(1, 29):
        item = items[number]
        occurrences.append((str(item["question_number_spoken"]), "NARRATOR", 1))
        for segment in item["repeat1"]:
            occurrences.append((segment["text"], segment["speaker_label"], 2))
    q29, q30 = items[29], items[30]
    occurrences.extend([
        (q29["repeat1"][0]["text"], q29["repeat1"][0]["speaker_label"], 2),
        (str(q29["question_number_spoken"]), "NARRATOR", 2),
        (q29["repeat1"][1]["text"], q29["repeat1"][1]["speaker_label"], 2),
        (str(q30["question_number_spoken"]), "NARRATOR", 2),
        (q30["repeat1"][0]["text"], q30["repeat1"][0]["speaker_label"], 2),
    ])
    return occurrences


def build_hsk3_speed_comparison(
    report: Dict[str, object],
    output_dir: str | Path,
    *,
    lang_state: Optional[Dict[str, object]] = None,
    speeds: Tuple[float, ...] = (0.90, 0.95, 1.00),
    separator_ms: int = HSK3_SPEED_COMPARISON_SEPARATOR_MS,
    base_output_file: str | Path | None = None,
) -> Dict[str, object]:
    """Build short HSK3 speech-rate samples without fixed assets or answer pauses."""
    if not report or report.get("level") != "HSK3" or int(report.get("question_count", 0)) != 30:
        raise ValueError("Preview HSK3 không hợp lệ cho speed comparison.")
    lang_state = dict(lang_state or report.get("lang_state", {}) or {})
    engine = _clean_text(lang_state.get("engine", "")) or "Google Cloud TTS"
    google_api_key = _clean_text(lang_state.get("google_api_key", ""))
    cache_dir = _make_tts_cache_dir()
    groups = _hsk3_speed_comparison_groups(report)
    output_root = Path(output_dir)
    output_root.mkdir(parents=True, exist_ok=True)
    results: Dict[str, object] = {}
    total_unique = 0
    total_reused = 0

    def add_segment(text: str, speaker_label: str, speed: str, counts: Dict[str, int]) -> AudioSegment:
        voice_name = _speaker_voice_name(lang_state, speaker_label)
        audio, reused = _synthesize_tts_segment(
            text, engine=engine, speed=speed, voice_name=voice_name, speaker_label=speaker_label,
            cache_dir=cache_dir, google_api_key=google_api_key, tts_provider=None,
            aws_credentials=_aws_credentials_from_lang_state(lang_state),
            audio_postprocess_version=HSK3_TTS_POSTPROCESS_VERSION,
        )
        if reused:
            counts["reused_segments"] += 1
        else:
            counts["unique_tts_requests"] += 1
        return audio

    for rate in speeds:
        speed = _format_speech_rate(rate)
        counts = {"unique_tts_requests": 0, "reused_segments": 0}
        pieces: List[AudioSegment] = []
        group_durations: Dict[str, int] = {}
        total_speech_ms = 0
        for group_index, (group_name, segments) in enumerate(groups):
            group_audio = _silent_segment(0)
            group_speech_ms = 0
            for segment in segments:
                audio = add_segment(segment["text"], segment["speaker_label"], speed, counts)
                group_audio += audio
                group_speech_ms += len(audio)
            pieces.append(group_audio)
            group_durations[group_name] = group_speech_ms
            total_speech_ms += group_speech_ms
            if group_index < len(groups) - 1:
                pieces.append(_silent_segment(separator_ms))
        comparison_audio = sum(pieces, _silent_segment(0)).set_channels(FINAL_CHANNELS).set_frame_rate(FINAL_SAMPLE_RATE)
        filename = f"hsk3-speed-{int(round(rate * 100)):03d}.m4a"
        output_path = output_root / filename
        export_handle = comparison_audio.export(
            str(output_path), format="ipod", codec="aac", bitrate=FINAL_BITRATE,
            parameters=["-ac", str(FINAL_CHANNELS), "-ar", str(FINAL_SAMPLE_RATE)],
        )
        try:
            export_handle.close()
        except Exception:
            pass
        actual_duration_ms = _probe_audio_duration_ms(output_path)
        results[speed] = {
            "speed": rate,
            "speed_label": speed,
            "output_file": str(output_path),
            "group_speech_ms": group_durations,
            "representative_speech_ms": total_speech_ms,
            "separator_ms": separator_ms,
            "actual_duration_ms": actual_duration_ms,
            "unique_tts_requests": counts["unique_tts_requests"],
            "reused_segments": counts["reused_segments"],
        }
        total_unique += counts["unique_tts_requests"]
        total_reused += counts["reused_segments"]

    base_output = Path(base_output_file or (BASE_DIR / "testdata" / "hsk30" / "hsk3" / "hsk3-30-001.m4a"))
    base_speed = _format_speech_rate(lang_state.get("speech_rate", 0.75))
    base_speech_ms = 0
    for text, speaker_label, uses in _hsk3_speech_occurrences(report):
        voice_name = _speaker_voice_name(lang_state, speaker_label)
        audio, _ = _synthesize_tts_segment(
            text, engine=engine, speed=base_speed, voice_name=voice_name, speaker_label=speaker_label,
            cache_dir=cache_dir, google_api_key=google_api_key, tts_provider=lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("Base HSK3 cache miss")),
            aws_credentials=_aws_credentials_from_lang_state(lang_state),
            audio_postprocess_version=HSK3_TTS_POSTPROCESS_VERSION,
        )
        base_speech_ms += len(audio) * uses
    base_full_duration_ms = _probe_audio_duration_ms(base_output)
    fixed_and_timing_ms = base_full_duration_ms - base_speech_ms
    base_representative_speech_ms = sum(
        group_ms for group_ms in results.get(base_speed, {}).get("group_speech_ms", {}).values()
    ) if base_speed in results else 0
    base_group_durations = dict(results.get(base_speed, {}).get("group_speech_ms", {})) if base_speed in results else {}
    if not base_representative_speech_ms:
        # The current 0.75x comparison is not exported; load the same four
        # semantic groups from its versioned cache for ratio estimation.
        for group_name, segments in groups:
            group_ms = 0
            for segment in segments:
                voice_name = _speaker_voice_name(lang_state, segment["speaker_label"])
                audio, _ = _synthesize_tts_segment(
                    segment["text"], engine=engine, speed=base_speed, voice_name=voice_name,
                    speaker_label=segment["speaker_label"], cache_dir=cache_dir,
                    google_api_key=google_api_key,
                    tts_provider=lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("Base comparison cache miss")),
                    aws_credentials=_aws_credentials_from_lang_state(lang_state),
                    audio_postprocess_version=HSK3_TTS_POSTPROCESS_VERSION,
                )
                group_ms += len(audio)
            base_group_durations[group_name] = group_ms
            base_representative_speech_ms += group_ms
    estimates: Dict[str, object] = {}
    for speed, comparison in results.items():
        ratio = comparison["representative_speech_ms"] / base_representative_speech_ms
        estimated_speech_ms = round(base_speech_ms * ratio)
        estimates[speed] = {
            "estimated_speech_ms": estimated_speech_ms,
            "estimated_full_duration_ms": fixed_and_timing_ms + estimated_speech_ms,
            "ratio_vs_base": ratio,
        }
    return {
        "outputs": results,
        "base_speed": base_speed,
        "base_speech_ms": base_speech_ms,
        "base_full_duration_ms": base_full_duration_ms,
        "fixed_and_timing_ms": fixed_and_timing_ms,
        "base_representative_speech_ms": base_representative_speech_ms,
        "base_group_speech_ms": base_group_durations,
        "estimates": estimates,
        "unique_tts_requests": total_unique,
        "reused_segments": total_reused,
        "edge_trim_version": HSK3_TTS_POSTPROCESS_VERSION,
        "timing_unchanged": True,
    }


def format_hsk3_build_report(report: Dict[str, object]) -> str:
    lines = [f"Exam: {report.get('exam', 'HSK3-30-01')}", f"Output: {report.get('output_file', '')}", "",
             f"Music: {report.get('music_ms', 0)} ms", f"Fixed intro/part assets: {report.get('fixed_assets_ms', 0)} ms",
             f"Fixed cues: {report.get('fixed_cue_ms', 0)} ms", f"Generated speech: {report.get('generated_speech_ms', 0)} ms",
             f"Internal pauses total: {report.get('internal_pauses_ms', 0)} ms", f"Number to content: {report.get('number_to_content_ms', 0)} ms",
             f"Speaker gaps: {report.get('speaker_gap_ms', 0)} ms", f"Question gaps: {report.get('question_gap_ms', 0)} ms",
             f"Repeat gaps: {report.get('repeat_gap_ms', 0)} ms", f"Shared-block gaps: {report.get('shared_block_gap_ms', 0)} ms",
             f"Answer pauses: {report.get('answer_pause_ms', 0)} ms",
             f"Part transitions: {report.get('part_transitions_ms', 0)} ms", f"Final silence: {report.get('final_silence_ms', 0)} ms", f"Outro: {report.get('outtro_ms', 0)} ms", "",
             f"Unique TTS requests: {report.get('unique_tts_requests', 0)}", f"Cache reused: {report.get('reused_segments', 0)}",
             f"HSK3 speed: {report.get('speech_rate_label', '')}", "Voice mapping: dialogue by 男/女, questions female, numbers female", f"Q29–30 shared passage reused: {'YES' if report.get('q29_q30_shared_passage_reused') else 'NO'}", ""]
    repeat_audit = report.get("repeat_audit", {})
    lines.extend([
        f"Repeat audit: Part1={repeat_audit.get('part1_questions', {}).get('repeats_per_question', 0)}x, "
        f"Part2={repeat_audit.get('part2_questions', {}).get('repeats_per_question', 0)}x, "
        f"Part3 Q21-28={repeat_audit.get('part3_questions_21_28', {}).get('repeats_per_question', 0)}x, "
        "Q29/Q30 shared block=2 passes",
        f"TTS postprocess: {report.get('tts_postprocess_version', '')} ({report.get('edge_trim_safety_pad_ms', 0)} ms safety pad)",
        "",
    ])
    for part in (1, 2, 3): lines.append(f"Part{part} duration: {report.get('part_breakdown', {}).get(part, 0)} ms")
    lines += ["", "Reference official: 1380000 ms (~23:00)", f"Sample reference: {report.get('sample_reference_duration_ms', 0)} ms", f"Actual duration: {report.get('actual_duration_ms', 0)} ms", f"Difference from official reference: {report.get('difference_ms', 0)} ms", f"Difference from sample: {report.get('sample_difference_ms', 0)} ms", f"Duration QA: {report.get('duration_qa', 'INFO')}", "Status: INFO"]
    return "\n".join(lines) + "\n"


# HSK4 uses one playback per question and derives shared passages from the
# workbook's Nhóm câu column. It deliberately does not reuse HSK3 repeat logic.
HSK4_QUESTION_MARKER_RE = re.compile(r"(?:\d{1,2}\s*[.．、]\s*)?(?:问|問)\s*[：:]")
HSK4_SPEAKER_RE = re.compile(r"(男|女)\s*[：:]")
HSK4_GROUP_RE = re.compile(r"(\d{1,2})\s*[–—-]\s*(\d{1,2})")
HSK4_NARRATION_GROUP_VOICES = {
    "20–21": "NỮ",
    "22–23": "NAM",
    "24–25": "NỮ",
    "26–27": "NAM",
    "28–29": "NỮ",
    "30–32": "NAM",
}


def _hsk4_normalize_group(value: object) -> str:
    clean = _clean_text(value).replace("—", "–").replace("-", "–")
    match = HSK4_GROUP_RE.fullmatch(clean)
    if not match:
        return clean
    return f"{int(match.group(1))}–{int(match.group(2))}"


def _hsk4_group_range(value: object) -> Optional[List[int]]:
    group = _hsk4_normalize_group(value)
    match = HSK4_GROUP_RE.fullmatch(group)
    if not match:
        return None
    start, end = int(match.group(1)), int(match.group(2))
    if end < start:
        return None
    return list(range(start, end + 1))


def _hsk4_strip_question_marker(text: str) -> str:
    clean = _normalize_tts_text(text)
    return HSK4_QUESTION_MARKER_RE.sub("", clean, count=1).strip()


def _hsk4_parse_stimulus(stimulus: str, *, narration_label: str = "NỮ") -> List[Dict[str, str]]:
    source = _normalize_tts_text(stimulus)
    if not source:
        return []
    matches = list(HSK4_SPEAKER_RE.finditer(source))
    if not matches:
        return [{
            "speaker_label": narration_label,
            "speaker_token": "",
            "source_text": source,
            "text": _strip_speaker_prefix(source),
        }]
    if matches[0].start() != 0:
        raise ValueError("HSK4 stimulus có nội dung trước prefix 男：/女：")
    segments: List[Dict[str, str]] = []
    for index, match in enumerate(matches):
        end = matches[index + 1].start() if index + 1 < len(matches) else len(source)
        spoken = _normalize_tts_text(source[match.end():end])
        if not spoken:
            raise ValueError(f"Segment {match.group(1)}： bị rỗng")
        token = match.group(1)
        segments.append({
            "speaker_label": {"男": "NAM", "女": "NỮ"}[token],
            "speaker_token": token,
            "source_text": f"{token}：{spoken}",
            "text": spoken,
        })
    return segments


def _hsk4_parse_content(text: str, *, narration_label: str = "NỮ") -> Dict[str, object]:
    source = _normalize_tts_text(text)
    matches = list(HSK4_QUESTION_MARKER_RE.finditer(source))
    if len(matches) != 1:
        raise ValueError("HSK4 mỗi dòng phải có đúng một 问：")
    marker = matches[0]
    stimulus_text = source[:marker.start()].strip()
    question_text = _hsk4_strip_question_marker(source[marker.start():])
    if not question_text:
        raise ValueError("Câu hỏi HSK4 bị rỗng")
    return {
        "stimulus_text": stimulus_text,
        "stimulus_segments": _hsk4_parse_stimulus(stimulus_text, narration_label=narration_label),
        "question": {
            "speaker_label": "NỮ",
            "speaker_token": "问",
            "source_text": source[marker.start():],
            "text": question_text,
        },
    }


def _hsk4_required_columns(header: Dict[str, str]) -> Dict[str, str]:
    aliases = {
        "exam": {"made", "exam", "examcode"},
        "part": {"phan", "part"},
        "group": {"nhomcau", "group", "questiongroup"},
        "question_number": {"cau", "socau", "question", "questionnumber"},
        "content": {"noidungphannghe", "phude", "subtitle", "listeningcontent"},
    }
    normalized_columns = {
        _normalize_hsk3_header(value): column for column, value in header.items() if _clean_text(value)
    }
    missing = [name for name, accepted in aliases.items() if not any(key in normalized_columns for key in accepted)]
    if missing:
        labels = {
            "exam": "Mã đề", "part": "Phần", "group": "Nhóm câu",
            "question_number": "Câu", "content": "Nội dung phần nghe",
        }
        raise ValueError("Workbook HSK4 thiếu cột bắt buộc: " + ", ".join(labels[name] for name in missing))
    return {
        name: next(normalized_columns[key] for key in accepted if key in normalized_columns)
        for name, accepted in aliases.items()
    }


def _load_hsk4_exam_rows(excel_path: str | Path, sheet_name: str) -> Dict[str, object]:
    rows = read_xlsx_rows(excel_path, sheet_name=sheet_name)
    if not rows:
        raise ValueError("Workbook HSK4 không có dữ liệu")
    columns = _hsk4_required_columns(rows[0].cells)
    exams: Dict[str, List[XlsxRow]] = {}
    invalid_rows: List[str] = []
    current_exam = ""
    for row in rows[1:]:
        explicit_exam = _normalize_exam_code(row.cells.get(columns["exam"], ""))
        if explicit_exam:
            current_exam = explicit_exam
        number_text = _clean_text(row.cells.get(columns["question_number"], ""))
        content_text = _clean_text(row.cells.get(columns["content"], ""))
        if not number_text and not content_text:
            continue
        if not current_exam:
            invalid_rows.append(f"Dòng {row.row_number}: thiếu mã đề trước khi forward-fill")
            continue
        if not re.fullmatch(r"HSK4-30-\d{2}", current_exam):
            invalid_rows.append(f"Dòng {row.row_number}: mã đề không hợp lệ '{current_exam}'")
            continue
        exams.setdefault(current_exam, []).append(
            XlsxRow(
                row.row_number,
                {
                    **row.cells,
                    columns["exam"]: current_exam,
                    "_hsk4_question_number": number_text,
                    "_hsk4_content": content_text,
                    "_hsk4_part": _parse_part_label(row.cells.get(columns["part"], "")),
                    "_hsk4_group": _hsk4_normalize_group(row.cells.get(columns["group"], "")),
                },
            )
        )
    return {"columns": columns, "exams": exams, "invalid_rows": invalid_rows}


def validate_hsk4_workbook(excel_path: str | Path, sheet_name: str = "Sheet1") -> Dict[str, object]:
    sheet_name = _resolve_hsk3_sheet_name(excel_path, sheet_name)
    loaded = _load_hsk4_exam_rows(excel_path, sheet_name)
    exams = loaded["exams"]
    expected_exams = [f"HSK4-30-{index:02d}" for index in range(1, 18)]
    errors = list(loaded["invalid_rows"])
    summaries: Dict[str, Dict[str, object]] = {}
    part1_patterns: Counter[str] = Counter()
    part2_patterns: Counter[str] = Counter()
    shared_group_counts: Counter[str] = Counter()
    embedded_marker_count = 0

    for exam in expected_exams:
        exam_rows = exams.get(exam, [])
        numbers = [_hsk3_number_value(row.cells.get("_hsk4_question_number", "")) for row in exam_rows]
        part_counts = {
            1: sum(1 for row in exam_rows if row.cells.get("_hsk4_part") == 1),
            2: sum(1 for row in exam_rows if row.cells.get("_hsk4_part") == 2),
        }
        summaries[exam] = {
            "row_count": len(exam_rows),
            "numbers": numbers,
            "part_counts": part_counts,
            "shared_groups": {},
            "independent_count": 0,
        }
        if not exam_rows:
            errors.append(f"Thiếu {exam}")
            continue
        if len(exam_rows) != 32 or numbers != list(range(1, 33)):
            errors.append(f"{exam}: cần đúng 32 câu số 一 đến 三十二")
            continue
        if part_counts != {1: 14, 2: 18}:
            errors.append(f"{exam}: cần Part 1=14, Part 2=18, nhận được {part_counts}")

        grouped: Dict[str, List[Tuple[XlsxRow, Dict[str, object]]]] = {}
        for row in exam_rows:
            number = _hsk3_number_value(row.cells.get("_hsk4_question_number", ""))
            part = row.cells.get("_hsk4_part")
            group = row.cells.get("_hsk4_group", "")
            content = row.cells.get("_hsk4_content", "")
            if number is None or not content:
                errors.append(f"{exam} dòng {row.row_number}: thiếu Câu hoặc Nội dung phần nghe")
                continue
            if (number <= 14 and part != 1) or (number >= 15 and part != 2):
                errors.append(f"{exam} Q{number}: Phần không khớp số câu")
            try:
                parsed = _hsk4_parse_content(content, narration_label=HSK4_NARRATION_GROUP_VOICES.get(group, "NỮ"))
            except ValueError as exc:
                errors.append(f"{exam} Q{number}: {exc}")
                continue
            if re.search(r"\d{1,2}\s*[.．、]\s*(?:问|問)\s*[：:]", content):
                embedded_marker_count += 1
            stimulus = parsed["stimulus_segments"]
            if number <= 14:
                if len(stimulus) < 2:
                    errors.append(f"{exam} Q{number}: Part 1 cần ít nhất 2 lượt thoại")
                part1_patterns["→".join(segment["speaker_token"] for segment in stimulus)] += 1
            elif group == "Độc lập":
                if not stimulus:
                    errors.append(f"{exam} Q{number}: câu độc lập thiếu stimulus")
                part2_patterns["→".join(segment["speaker_token"] for segment in stimulus) or "NARRATION"] += 1
                summaries[exam]["independent_count"] += 1
            else:
                range_numbers = _hsk4_group_range(group)
                if not range_numbers:
                    errors.append(f"{exam} Q{number}: Nhóm câu không phải Độc lập hoặc khoảng hợp lệ")
                grouped.setdefault(group, []).append((row, parsed))

        for group, entries in grouped.items():
            group_numbers = [_hsk3_number_value(row.cells.get("_hsk4_question_number", "")) for row, _ in entries]
            expected_group = _hsk4_group_range(group)
            summaries[exam]["shared_groups"][group] = {
                "numbers": group_numbers,
                "size": len(entries),
            }
            shared_group_counts[group] += 1
            if expected_group != group_numbers:
                errors.append(f"{exam} nhóm {group}: cần các câu {expected_group}, nhận được {group_numbers}")
            for index, (row, parsed) in enumerate(entries):
                stimulus = parsed["stimulus_segments"]
                if index == 0 and not stimulus:
                    errors.append(f"{exam} nhóm {group}: dòng đầu phải sở hữu shared stimulus")
                if index > 0 and stimulus:
                    errors.append(f"{exam} Q{group_numbers[index]}: không được có stimulus mới trong shared group")

    unexpected_exams = sorted(exam for exam in exams if exam not in expected_exams)
    errors.extend(f"Mã đề ngoài danh sách: {exam}" for exam in unexpected_exams)
    return {
        "workbook": str(Path(excel_path)),
        "sheet": sheet_name,
        "source_columns": loaded["columns"],
        "exam_count": len(exams),
        "exam_codes": sorted(exams),
        "missing_exams": [exam for exam in expected_exams if exam not in exams],
        "unexpected_exams": unexpected_exams,
        "invalid_rows": errors,
        "semantic_errors": errors,
        "part1_patterns": dict(part1_patterns),
        "part2_patterns": dict(part2_patterns),
        "shared_group_counts": dict(shared_group_counts),
        "embedded_marker_count": embedded_marker_count,
        "exams": summaries,
        "status": "PASS" if not errors and sorted(exams) == expected_exams else "FAIL",
    }


def parse_hsk4_phase1(
    excel_path: str | Path,
    sheet_name: str = "Sheet1",
    exam_code: str = "HSK4-30-01",
    asset_root: str | Path | None = None,
    lang_state: Optional[Dict[str, object]] = None,
) -> Dict[str, object]:
    sheet_name = _resolve_hsk3_sheet_name(excel_path, sheet_name)
    structure = validate_hsk4_workbook(excel_path, sheet_name=sheet_name)
    if structure.get("status") != "PASS":
        raise ValueError("Workbook HSK4 không hợp lệ: " + "; ".join(structure.get("invalid_rows", [])))
    normalized_exam = _normalize_exam_code(exam_code)
    if normalized_exam not in structure["exams"]:
        raise ValueError(f"Không tìm thấy dữ liệu cho {normalized_exam}")
    loaded = _load_hsk4_exam_rows(excel_path, sheet_name)
    selected = loaded["exams"][normalized_exam]
    lang_state = dict(lang_state or {})
    rate = _parse_speech_rate(lang_state.get("speech_rate", 0.95), default=0.95)
    profile = _clean_text(lang_state.get("hsk_speed_profile", "HSK4")) or "HSK4"
    items: List[Dict[str, object]] = []
    part_counts = {1: 0, 2: 0}
    invalid: List[str] = []
    for row in selected:
        number_text = _clean_text(row.cells.get("_hsk4_question_number", ""))
        number = _hsk3_number_value(number_text)
        part = row.cells.get("_hsk4_part")
        group = row.cells.get("_hsk4_group", "")
        content = _clean_text(row.cells.get("_hsk4_content", ""))
        try:
            if number is None or part not in (1, 2) or not content:
                raise ValueError("thiếu số câu, Part hoặc nội dung")
            parsed = _hsk4_parse_content(content, narration_label=HSK4_NARRATION_GROUP_VOICES.get(group, "NỮ"))
        except ValueError as exc:
            invalid.append(f"Dòng {row.row_number}: {exc}")
            continue
        question = parsed["question"]
        stimulus = parsed["stimulus_segments"]
        item = {
            "question_number": number,
            "part": part,
            "part_label": PART_LABELS[part],
            "group": group,
            "is_shared": group != "Độc lập",
            "source_row": row.row_number,
            "source_exam": normalized_exam,
            "source_number": number_text,
            "question_number_spoken": _spoken_question_number(number_text),
            "number_speaker_label": "NỮ",
            "source_text": content,
            "stimulus_segments": stimulus,
            "question_segment": question,
            "question_source": question["source_text"],
            "question_spoken": question["text"],
            "repeat1": [*stimulus, question],
            "repeat2": [],
        }
        item["preview"] = _build_item_preview(item)
        items.append(item)
        part_counts[part] += 1

    report_lang = {
        "engine": lang_state.get("engine", ""), "speed": lang_state.get("speed", ""), "voice": lang_state.get("voice", ""),
        "zh_male_voice": lang_state.get("zh_male_voice", ""), "zh_female_voice": lang_state.get("zh_female_voice", ""),
        "zh_narrator_voice": lang_state.get("zh_narrator_voice", ""), "hsk_speed_profile": profile,
        "speech_rate": rate, "speech_rate_label": _format_hsk_speed_label(profile, rate),
    }
    shared_groups = structure["exams"][normalized_exam]["shared_groups"]
    return {
        "level": "HSK4", "workbook": str(Path(excel_path)), "sheet": sheet_name, "detected_exam": normalized_exam,
        "question_count": len(items), "part_counts": part_counts, "missing_invalid_rows": invalid,
        "asset_status": validate_hsk30_assets(asset_root=asset_root, level="hsk4"), "lang_state": report_lang,
        "output_filename": phase1_output_filename(normalized_exam), "reference_duration_ms": HSK4_AUDIO_PROFILE["reference_duration_ms"],
        "sample_reference_duration_ms": HSK4_AUDIO_PROFILE["sample_reference_duration_ms"], "tts_requests_made": 0,
        "preview_items": sorted(items, key=lambda item: int(item["question_number"])),
        "shared_groups": shared_groups, "independent_count": structure["exams"][normalized_exam]["independent_count"],
        "shared_group_count": len(shared_groups), "embedded_marker_count": structure["embedded_marker_count"],
        "part1_pattern_stats": structure["part1_patterns"], "part2_pattern_stats": structure["part2_patterns"],
        "repetitions": 1, "status": "READY_FOR_REVIEW" if not invalid and len(items) == 32 and part_counts == {1: 14, 2: 18} else "REVIEW_WITH_WARNINGS",
        "structure": structure,
    }


def format_hsk4_preview_report(report: Dict[str, object]) -> str:
    lines = [
        f"{report.get('detected_exam', 'HSK4-30-01')} parse summary", "Level: HSK4",
        f"Workbook: {report.get('workbook', '')}", f"Sheet: {report.get('sheet', '')}",
        f"Questions: {report.get('question_count', 0)}", f"Part 1: {report.get('part_counts', {}).get(1, 0)}",
        f"Part 2: {report.get('part_counts', {}).get(2, 0)}", "Repetitions: 1 (no repeat2)",
        f"Independent items: {report.get('independent_count', 0)}", f"Shared groups: {report.get('shared_group_count', 0)}",
        f"Embedded Arabic markers stripped: {report.get('embedded_marker_count', 0)}",
        f"Missing/invalid rows: {len(report.get('missing_invalid_rows', []))}",
        f"Asset status: {'OK' if report.get('asset_status', {}).get('ok') else 'MISSING'}",
        f"HSK speed profile: {report.get('lang_state', {}).get('hsk_speed_profile', 'HSK4')}",
        f"Speech rate: {report.get('lang_state', {}).get('speech_rate_label', '')}",
        f"Part 1 patterns: {report.get('part1_pattern_stats', {})}",
        f"Part 2 patterns: {report.get('part2_pattern_stats', {})}",
        "TTS requests in dry run: 0", "",
    ]
    for item in report.get("preview_items", []):
        lines.extend([
            f"Q{item['question_number']:02d} — {item['part_label']}",
            f"Source row: {item['source_row']}",
            f"Source number: {item['source_number']}",
            f"Number spoken: NỮ [{item['question_number_spoken']}]",
            f"Group: {item['group']}",
        ])
        if item["is_shared"]:
            if item["stimulus_segments"]:
                lines.append("Shared stimulus owner: this row (plays once for group)")
                lines.extend(f"  {_render_segment(segment['speaker_label'], segment['text'])}" for segment in item["stimulus_segments"])
            else:
                lines.append("Shared stimulus: reuse from first row of group")
        else:
            lines.append("Stimulus:")
            lines.extend(f"  {_render_segment(segment['speaker_label'], segment['text'])}" for segment in item["stimulus_segments"])
        lines.append(f"Source question: {item['question_source']}")
        lines.append(f"Spoken question: {item['question_spoken']}")
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def _hsk4_count_tts(counts: Dict[str, int], reused: bool, audio: AudioSegment, audio_metrics: Optional[Dict[str, int]] = None) -> None:
    if reused:
        counts["reused_segments"] += 1
    else:
        counts["unique_tts_requests"] += 1
        counts["generated_segments"] += 1
    counts["generated_speech_ms"] += len(audio)
    if "speech_metrics" in counts:
        counts["speech_metrics"].append(audio_metrics or {})


def _hsk4_tts_segment(
    segment: Dict[str, str], *, engine: str, speed: str, lang_state: Dict[str, object], cache_dir: Path,
    google_api_key: str, tts_provider: Optional[Callable[..., AudioSegment]], counts: Dict[str, int],
) -> AudioSegment:
    metrics = {} if "speech_metrics" in counts else None
    audio, reused = _synthesize_tts_segment(
        segment["text"], engine=engine, speed=speed,
        voice_name=_speaker_voice_name(lang_state, segment["speaker_label"]),
        speaker_label=segment["speaker_label"], cache_dir=cache_dir, google_api_key=google_api_key,
        tts_provider=tts_provider, aws_credentials=_aws_credentials_from_lang_state(lang_state),
        audio_postprocess_version=HSK3_TTS_POSTPROCESS_VERSION,
        audio_metrics=metrics,
        retry_google_voice_failure=lang_state.get("hsk_speed_profile") == "HSK6",
    )
    _hsk4_count_tts(counts, reused, audio, metrics)
    return audio


def _hsk4_number_audio(
    item: Dict[str, object], *, engine: str, speed: str, lang_state: Dict[str, object], cache_dir: Path,
    google_api_key: str, tts_provider: Optional[Callable[..., AudioSegment]], counts: Dict[str, int],
) -> AudioSegment:
    number_text = _clean_text(item["question_number_spoken"])
    metrics = {} if "speech_metrics" in counts else None
    audio, reused = _synthesize_tts_segment(
        number_text, engine=engine, speed=speed, voice_name=_narrator_voice_name(lang_state),
        speaker_label="NARRATOR", cache_dir=cache_dir, google_api_key=google_api_key,
        tts_provider=tts_provider, aws_credentials=_aws_credentials_from_lang_state(lang_state),
        audio_postprocess_version=HSK3_TTS_POSTPROCESS_VERSION,
        audio_metrics=metrics,
        retry_google_voice_failure=lang_state.get("hsk_speed_profile") == "HSK6",
    )
    _hsk4_count_tts(counts, reused, audio, metrics)
    return audio


def _hsk4_segments_audio(
    segments: List[Dict[str, str]], *, gap_ms: int, gap_key: str, engine: str, speed: str,
    lang_state: Dict[str, object], cache_dir: Path, google_api_key: str,
    tts_provider: Optional[Callable[..., AudioSegment]], counts: Dict[str, int],
) -> AudioSegment:
    pieces: List[AudioSegment] = []
    for index, segment in enumerate(segments):
        pieces.append(_hsk4_tts_segment(
            segment, engine=engine, speed=speed, lang_state=lang_state, cache_dir=cache_dir,
            google_api_key=google_api_key, tts_provider=tts_provider, counts=counts,
        ))
        if index < len(segments) - 1:
            pieces.append(_internal_pause_segment(gap_ms))
            counts["internal_pauses_ms"] += gap_ms
            counts[gap_key] += gap_ms
    return sum(pieces, _silent_segment(0)) if pieces else _silent_segment(0)


def _hsk4_render_independent(
    item: Dict[str, object], *, engine: str, speed: str, lang_state: Dict[str, object], cache_dir: Path,
    google_api_key: str, tts_provider: Optional[Callable[..., AudioSegment]], counts: Dict[str, int],
    profile: Optional[Dict[str, object]] = None,
) -> Tuple[AudioSegment, List[str]]:
    profile = profile or HSK4_AUDIO_PROFILE
    answer_ms = _single_playback_pause(profile, "answer_pause", item["part"])
    number = _hsk4_number_audio(item, engine=engine, speed=speed, lang_state=lang_state, cache_dir=cache_dir, google_api_key=google_api_key, tts_provider=tts_provider, counts=counts)
    stimulus = _hsk4_segments_audio(
        item["stimulus_segments"], gap_ms=profile["speaker_gap_ms"], gap_key="speaker_gap_ms",
        engine=engine, speed=speed, lang_state=lang_state, cache_dir=cache_dir, google_api_key=google_api_key,
        tts_provider=tts_provider, counts=counts,
    )
    question = _hsk4_tts_segment(
        item["question_segment"], engine=engine, speed=speed, lang_state=lang_state, cache_dir=cache_dir,
        google_api_key=google_api_key, tts_provider=tts_provider, counts=counts,
    )
    counts["internal_pauses_ms"] += profile["number_to_content_ms"] + profile["content_to_question_ms"]
    counts["number_to_content_ms"] += profile["number_to_content_ms"]
    counts["content_to_question_ms"] += profile["content_to_question_ms"]
    counts["answer_pause_ms"] += answer_ms
    audio = (
        number + _internal_pause_segment(profile["number_to_content_ms"]) + stimulus
        + _internal_pause_segment(profile["content_to_question_ms"]) + question
        + _internal_pause_segment(answer_ms)
    )
    return audio, [f"number{item['question_number']}", f"stimulus{item['question_number']}", f"question{item['question_number']}", "answer_pause"]


def _hsk4_render_shared_group(
    items: List[Dict[str, object]], *, engine: str, speed: str, lang_state: Dict[str, object], cache_dir: Path,
    google_api_key: str, tts_provider: Optional[Callable[..., AudioSegment]], counts: Dict[str, int],
    profile: Optional[Dict[str, object]] = None,
) -> Tuple[AudioSegment, List[str]]:
    profile = profile or HSK4_AUDIO_PROFILE
    owner = items[0]
    passage = _hsk4_segments_audio(
        owner["stimulus_segments"], gap_ms=profile["speaker_gap_ms"], gap_key="speaker_gap_ms",
        engine=engine, speed=speed, lang_state=lang_state, cache_dir=cache_dir, google_api_key=google_api_key,
        tts_provider=tts_provider, counts=counts,
    )
    if "group_passage_ms" in counts:
        counts["group_passage_ms"][owner["group"]] = len(passage)
    counts["internal_pauses_ms"] += profile["shared_passage_to_number_ms"]
    counts["shared_passage_to_number_ms"] += profile["shared_passage_to_number_ms"]
    audio = passage + _internal_pause_segment(profile["shared_passage_to_number_ms"])
    timeline = [f"shared_passage_{owner['group']}", "shared_passage_to_number_gap"]
    for item in items:
        number_gap = _single_playback_pause(profile, "number_to_question", item["part"])
        answer_ms = _single_playback_pause(profile, "answer_pause", item["part"])
        number = _hsk4_number_audio(item, engine=engine, speed=speed, lang_state=lang_state, cache_dir=cache_dir, google_api_key=google_api_key, tts_provider=tts_provider, counts=counts)
        question = _hsk4_tts_segment(
            item["question_segment"], engine=engine, speed=speed, lang_state=lang_state, cache_dir=cache_dir,
            google_api_key=google_api_key, tts_provider=tts_provider, counts=counts,
        )
        counts["internal_pauses_ms"] += number_gap
        counts["number_to_question_ms"] += number_gap
        counts["answer_pause_ms"] += answer_ms
        audio += number + _internal_pause_segment(number_gap) + question + _internal_pause_segment(answer_ms)
        timeline.extend([f"number{item['question_number']}", "number_to_question_gap", f"question{item['question_number']}", "answer_pause"])
    return audio, timeline


def _single_playback_pause(profile: Dict[str, object], name: str, part: int) -> int:
    if f"{name}_by_part" in profile:
        return profile[f"{name}_by_part"][part]
    return profile[f"{name}_ms"]


def build_hsk4_phase1b(
    report: Dict[str, object], output_file: str | Path, *, lang_state: Optional[Dict[str, object]] = None,
    tts_provider: Optional[Callable[..., AudioSegment]] = None,
    progress_callback: Optional[Callable[[str], None]] = None,
) -> Dict[str, object]:
    return _build_single_playback_exam(
        report, output_file, profile=HSK4_AUDIO_PROFILE, assets=HSK30_HSK4_ASSETS,
        lang_state=lang_state, tts_provider=tts_provider, progress_callback=progress_callback,
    )


def build_hsk5_phase1b(
    report: Dict[str, object], output_file: str | Path, *, lang_state: Optional[Dict[str, object]] = None,
    tts_provider: Optional[Callable[..., AudioSegment]] = None,
    progress_callback: Optional[Callable[[str], None]] = None,
) -> Dict[str, object]:
    return _build_single_playback_exam(
        report, output_file, profile=HSK5_AUDIO_PROFILE, assets=HSK30_HSK5_ASSETS,
        lang_state=lang_state, tts_provider=tts_provider, progress_callback=progress_callback,
    )


def _build_single_playback_exam(
    report: Dict[str, object], output_file: str | Path, *, lang_state: Optional[Dict[str, object]] = None,
    tts_provider: Optional[Callable[..., AudioSegment]] = None,
    progress_callback: Optional[Callable[[str], None]] = None,
    profile: Dict[str, object], assets: Dict[str, Path],
) -> Dict[str, object]:
    level = profile["level"]
    part_counts = profile["part_counts"]
    question_count = sum(part_counts.values())
    if not report or report.get("level") != level or int(report.get("question_count", 0)) != question_count:
        raise ValueError(f"Preview {level} không hợp lệ hoặc chưa đủ {question_count} câu.")
    if report.get("part_counts") != part_counts:
        raise ValueError(f"Preview {level} cần số câu mỗi Part: {part_counts}.")
    if report.get("missing_invalid_rows"):
        raise ValueError(f"Preview {level} có dòng lỗi: " + "; ".join(report["missing_invalid_rows"]))
    asset_status = validate_hsk30_assets(level=level)
    if not asset_status.get("ok"):
        raise FileNotFoundError(f"Thiếu asset {level}: " + ", ".join(asset_status.get("missing", [])))

    lang_state = dict(lang_state or report.get("lang_state", {}) or {})
    engine = _clean_text(lang_state.get("engine", "")) or "Google Cloud TTS"
    default_rate = profile.get("default_rate", 0.95)
    speed_rate = _parse_speech_rate(lang_state.get("speech_rate", default_rate), default=default_rate)
    speed = _format_speech_rate(speed_rate)
    profile_name = _clean_text(lang_state.get("hsk_speed_profile", level)) or level
    google_api_key = _clean_text(lang_state.get("google_api_key", ""))
    if not _clean_text(lang_state.get("zh_female_voice", "")):
        raise ValueError("Thiếu voice zh nữ từ Lang.")

    out_path = Path(output_file)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    if progress_callback:
        progress_callback(f"Đang tải asset {level} cố định...")
    music = _load_audio_segment(HSK30_COMMON_ASSET)
    intro = _load_audio_segment(assets["intro"])
    part_assets = {part: _load_audio_segment(assets[f"part{part}"]) for part in part_counts}
    cue_assets = {number: _load_audio_segment(assets[key]) for number, key in profile.get("cues_before_question", {}).items()}
    outtro = _load_audio_segment(HSK30_COMMON_OUTTRO)
    cache_dir = _make_tts_cache_dir()
    counts = {
        "unique_tts_requests": 0, "reused_segments": 0, "generated_segments": 0,
        "generated_speech_ms": 0, "internal_pauses_ms": 0, "number_to_content_ms": 0,
        "speaker_gap_ms": 0, "content_to_question_ms": 0, "shared_passage_to_number_ms": 0,
        "number_to_question_ms": 0, "answer_pause_ms": 0,
    }
    if level in {"HSK5", "HSK6"}:
        counts["speech_metrics"] = []
    if level in {"HSK4", "HSK5", "HSK6"}:
        counts["group_passage_ms"] = {}
    items = {int(item["question_number"]): item for item in report["preview_items"]}
    groups: Dict[str, List[Dict[str, object]]] = {}
    for item in report["preview_items"]:
        if item["is_shared"]:
            groups.setdefault(item["group"], []).append(item)
    for group_items in groups.values():
        group_items.sort(key=lambda item: int(item["question_number"]))

    part_breakdown: Dict[int, int] = {}
    part_start_gaps = {part: _single_playback_pause(profile, "part_start_gap", part) for part in part_counts}
    fixed_asset_boundaries = []
    part_asset_boundaries = []
    pending_fixed_asset = None
    timeline: List[str] = ["music", "music_to_intro_gap", f"{level.lower()}_intro", "intro_to_part_gap", f"{level.lower()}_part1"]
    if level != "HSK6" or part_start_gaps[1]:
        timeline.append("part1_start_gap")
    final_audio = music + _silent_segment(profile["music_to_intro_ms"]) + intro + _silent_segment(profile["intro_to_part_ms"])
    part_asset_boundaries.append({"part": 1, "asset_start_ms": len(final_audio),
                                  "asset_end_ms": len(final_audio) + len(part_assets[1]),
                                  "manual_gap_ms": part_start_gaps[1]})
    final_audio += part_assets[1] + _silent_segment(part_start_gaps[1])
    processed_groups: set[str] = set()
    first_number = 1
    for part, part_count in part_counts.items():
        numbers = range(first_number, first_number + part_count)
        first_number += part_count
        if part > 1:
            asset_start = len(final_audio)
            final_audio += part_assets[part]
            asset_end = len(final_audio)
            timeline.append(f"{level.lower()}_part{part}")
            part_asset_boundaries.append({"part": part, "asset_start_ms": asset_start,
                                          "asset_end_ms": asset_end, "manual_gap_ms": part_start_gaps[part]})
            if part_start_gaps[part]:
                final_audio += _silent_segment(part_start_gaps[part])
                timeline.append(f"part{part}_start_gap")
            if level == "HSK5":
                pending_fixed_asset = {"asset": "hsk5_part2", "path": str(assets["part2"]),
                                       "asset_start_ms": asset_start, "asset_end_ms": asset_end,
                                       "manual_gap_ms": part_start_gaps[2]}
        part_start = len(final_audio)
        for number in numbers:
            item = items[number]
            if progress_callback:
                progress_callback(f"Đang tạo Q{number:02d}...")
            cue_matches_group = level != "HSK4" or (
                item["is_shared"] and profile["cues_before_question"].get(number) ==
                "cue_q" + item["group"].replace("–", "_")
            )
            if number in cue_assets and cue_matches_group:
                asset_start = len(final_audio)
                final_audio += cue_assets[number]
                asset_end = len(final_audio)
                cue_gap = profile.get("cue_to_stimulus_ms", 0)
                cue_token = f"{level.lower()}_{profile['cues_before_question'][number]}"
                timeline.append(cue_token)
                if cue_gap:
                    final_audio += _silent_segment(cue_gap)
                    timeline.append("cue_to_stimulus_gap")
                if level in {"HSK4", "HSK5", "HSK6"}:
                    pending_fixed_asset = {"asset": cue_token, "path": str(assets[profile['cues_before_question'][number]]),
                                           "asset_start_ms": asset_start, "asset_end_ms": asset_end,
                                           "manual_gap_ms": cue_gap}
            kwargs = dict(engine=engine, speed=speed, lang_state=lang_state, cache_dir=cache_dir,
                          google_api_key=google_api_key, tts_provider=tts_provider, counts=counts, profile=profile)
            if item["is_shared"]:
                group = item["group"]
                if group in processed_groups:
                    continue
                audio, item_timeline = _hsk4_render_shared_group(groups[group], **kwargs)
                if level in {"HSK4", "HSK5", "HSK6"} and pending_fixed_asset is not None:
                    passage_start = len(final_audio)
                    fixed_asset_boundaries.append({**pending_fixed_asset, "group": group,
                                                   "passage_start_ms": passage_start,
                                                   "passage_end_ms": passage_start + counts["group_passage_ms"][group]})
                    pending_fixed_asset = None
                processed_groups.add(group)
            else:
                renderer = _hsk6_render_independent if level == "HSK6" else _hsk4_render_independent
                audio, item_timeline = renderer(item, **kwargs)
            final_audio += audio
            timeline.extend(item_timeline)
        part_breakdown[part] = len(final_audio) - part_start
    final_audio += _silent_segment(profile["final_silence_ms"]) + outtro
    timeline.extend(["final_silence", "outro"])

    normalized_audio = final_audio.set_channels(FINAL_CHANNELS).set_frame_rate(FINAL_SAMPLE_RATE)
    peak = normalized_audio.max_dBFS
    if peak and peak != float("-inf") and peak > -4.0:
        normalized_audio = normalized_audio.apply_gain(-4.0 - peak)
    final_duration_ms = _export_audio_atomically(
        normalized_audio, out_path,
        {"format": "ipod", "codec": "aac", "bitrate": FINAL_BITRATE, "parameters": ["-ac", str(FINAL_CHANNELS), "-ar", str(FINAL_SAMPLE_RATE)]},
    )
    sample_difference_ms = final_duration_ms - profile["sample_reference_duration_ms"]
    fixed_assets_ms = len(intro) + sum(len(audio) for audio in part_assets.values())
    part_transition_ms = profile["music_to_intro_ms"] + profile["intro_to_part_ms"] + sum(part_start_gaps.values()) + len(cue_assets) * profile.get("cue_to_stimulus_ms", 0)
    return {
        "exam": _normalize_exam_code(report["detected_exam"]), "level": level, "output_file": str(out_path), "file_size_bytes": out_path.stat().st_size,
        "music_ms": len(music), "fixed_assets_ms": fixed_assets_ms, "generated_speech_ms": counts["generated_speech_ms"],
        "fixed_cues_ms": sum(len(audio) for audio in cue_assets.values()),
        "fixed_assets_version": profile.get("fixed_assets_version", ""),
        "fixed_asset_boundaries": fixed_asset_boundaries, "part_start_gaps_ms": part_start_gaps,
        "part_asset_boundaries": part_asset_boundaries,
        "edge_trim_version": HSK3_TTS_POSTPROCESS_VERSION, "edge_trim_threshold_db": HSK3_EDGE_SILENCE_THRESHOLD_DB, "edge_trim_safety_pad_ms": HSK3_EDGE_SAFETY_PAD_MS,
        "internal_pauses_ms": counts["internal_pauses_ms"], "number_to_content_ms": counts["number_to_content_ms"], "speaker_gap_ms": counts["speaker_gap_ms"],
        "content_to_question_ms": counts["content_to_question_ms"], "shared_passage_to_number_ms": counts["shared_passage_to_number_ms"],
        "number_to_question_ms": counts["number_to_question_ms"], "answer_pause_ms": counts["answer_pause_ms"], "repeat_gap_ms": 0,
        "part_transitions_ms": part_transition_ms, "final_silence_ms": profile["final_silence_ms"], "outtro_ms": len(outtro),
        "part_breakdown": part_breakdown, "independent_items": report["independent_count"], "shared_groups": report["shared_group_count"],
        "shared_passage_count": report["shared_group_count"], "repetitions": 1, "unique_tts_requests": counts["unique_tts_requests"],
        "reused_segments": counts["reused_segments"], "generated_segments": counts["generated_segments"], "hsk_speed_profile": profile_name,
        "speech_rate": speed_rate, "speech_rate_label": _format_hsk_speed_label(profile_name, speed_rate), "reference_duration_ms": profile["reference_duration_ms"],
        "sample_reference_duration_ms": profile["sample_reference_duration_ms"], "actual_duration_ms": final_duration_ms,
        "meaningful_sample_duration_ms": profile.get("meaningful_sample_duration_ms"),
        "final_duration_ms": final_duration_ms, "difference_ms": final_duration_ms - profile["reference_duration_ms"],
        "sample_difference_ms": sample_difference_ms, "status": "INFO", "duration_qa": "INFO", "audio_path": str(out_path),
        "timeline": timeline, "one_playback": True, "group_model": "Nhóm câu", "no_repeat2": True,
        "speech_metrics": counts.get("speech_metrics", []),
        "active_speech_ms": sum(metric.get("active_speech_ms", 0) for metric in counts.get("speech_metrics", [])),
        "edge_silence_trimmed_ms": sum(metric.get("edge_silence_trimmed_ms", 0) for metric in counts.get("speech_metrics", [])),
        "metrics_available_segments": sum(bool(metric) for metric in counts.get("speech_metrics", [])),
    }


def format_hsk4_build_report(report: Dict[str, object]) -> str:
    lines = [
        f"Exam: {report.get('exam', 'HSK4-30-01')}", f"Output: {report.get('output_file', '')}",
        f"File size: {report.get('file_size_bytes', 0)} bytes", f"HSK4 speed: {report.get('speech_rate_label', '')}", "",
        f"Music: {report.get('music_ms', 0)} ms", f"Fixed intro/part assets: {report.get('fixed_assets_ms', 0)} ms",
        f"Fixed group cues: {report.get('fixed_cues_ms', 0)} ms",
        f"Generated speech: {report.get('generated_speech_ms', 0)} ms", f"Edge trim: {report.get('edge_trim_version', '')} / {report.get('edge_trim_safety_pad_ms', 0)} ms",
        f"Internal pauses: {report.get('internal_pauses_ms', 0)} ms", f"Number to content: {report.get('number_to_content_ms', 0)} ms",
        f"Speaker gaps: {report.get('speaker_gap_ms', 0)} ms", f"Content to question: {report.get('content_to_question_ms', 0)} ms",
        f"Shared passage to number: {report.get('shared_passage_to_number_ms', 0)} ms", f"Number to question: {report.get('number_to_question_ms', 0)} ms",
        f"Answer pauses: {report.get('answer_pause_ms', 0)} ms", f"Part transitions: {report.get('part_transitions_ms', 0)} ms",
        f"Final silence: {report.get('final_silence_ms', 0)} ms", f"Outro: {report.get('outtro_ms', 0)} ms", "",
        f"Part1 duration: {report.get('part_breakdown', {}).get(1, 0)} ms", f"Part2 duration: {report.get('part_breakdown', {}).get(2, 0)} ms", "",
        f"Independent items: {report.get('independent_items', 0)}", f"Shared groups: {report.get('shared_groups', 0)}", f"Shared passage count: {report.get('shared_passage_count', 0)}",
        f"Repetitions: {report.get('repetitions', 0)}", f"Unique TTS requests: {report.get('unique_tts_requests', 0)}", f"Cache reused: {report.get('reused_segments', 0)}", "",
        f"Reference official: {report.get('reference_duration_ms', 0)} ms (~20:00)", f"Full sample reference: {report.get('sample_reference_duration_ms', 0)} ms (~21:09)",
        f"Actual duration: {report.get('actual_duration_ms', 0)} ms", f"Difference from official: {report.get('difference_ms', 0)} ms", f"Difference from sample: {report.get('sample_difference_ms', 0)} ms",
        "Status: INFO",
    ]
    for boundary in report.get("fixed_asset_boundaries", []):
        lines.append(f"{boundary['group']}: {boundary['asset']}; asset {boundary['asset_start_ms']}..{boundary['asset_end_ms']} ms; "
                     f"passage starts {boundary['passage_start_ms']} ms; added gap {boundary['manual_gap_ms']} ms")
    return "\n".join(lines) + "\n"


def _hsk5_parse_content(text: str, *, narration_label: str = "NỮ") -> Dict[str, object]:
    source = _clean_text(text)
    markers = list(HSK4_QUESTION_MARKER_RE.finditer(source))
    if not markers:
        raise ValueError("HSK5 thiếu final 问：")
    # Narrative phrases such as 导演问： belong to the stimulus, not metadata.
    marker = markers[-1]
    stimulus = source[:marker.start()].strip()
    question = _normalize_tts_text(source[marker.end():])
    if not question:
        raise ValueError("HSK5 câu hỏi cuối bị rỗng")
    return {
        "stimulus_text": stimulus,
        "stimulus_segments": _hsk4_parse_stimulus(stimulus, narration_label=narration_label),
        "question": {"speaker_label": "NỮ", "speaker_token": "问",
                     "source_text": source[marker.start():], "text": question},
    }


def _load_hsk5_exam_rows(excel_path: str | Path, sheet_name: str) -> Dict[str, object]:
    rows = read_xlsx_rows(excel_path, sheet_name)
    if not rows:
        raise ValueError("Workbook HSK5 không có dữ liệu")
    columns = _hsk4_required_columns(rows[0].cells)
    headers = {_normalize_hsk3_header(value): key for key, value in rows[0].cells.items()}
    for name, header in (("skill", "kynang"), ("item_type", "dangbai")):
        if header not in headers:
            raise ValueError(f"Workbook HSK5 thiếu cột {name}")
        columns[name] = headers[header]
    exams: Dict[str, List[Dict[str, object]]] = {}
    errors: List[str] = []
    for row in rows[1:]:
        values = {key: _clean_text(row.cells.get(column, "")) for key, column in columns.items()}
        if not all(values[key] for key in ("exam", "part", "group", "question_number", "content")):
            errors.append(f"Dòng {row.row_number}: thiếu Mã đề/Phần/Nhóm câu/Câu/Nội dung")
            continue
        exam = _normalize_exam_code(values["exam"])
        if not re.fullmatch(r"HSK5_30_\d{2}", exam):
            errors.append(f"Dòng {row.row_number}: mã đề HSK5 không hợp lệ")
            continue
        values.update(source_row=row.row_number, exam=exam,
                      part=_parse_part_label(values["part"]),
                      group=_hsk4_normalize_group(values["group"]),
                      number=_hsk3_number_value(values["question_number"]))
        exams.setdefault(exam, []).append(values)
    return {"exams": exams, "errors": errors, "columns": columns}


def _hsk5_semantic_item(row: Dict[str, object]) -> Dict[str, object]:
    group, part = row["group"], row["part"]
    narration = (HSK5_AUDIO_PROFILE["part1_narration_fallback"] if part == 1
                 else HSK5_AUDIO_PROFILE["narration_group_voices"].get(group, "NỮ"))
    parsed = _hsk5_parse_content(row["content"], narration_label=narration)
    item = {
        "question_number": row["number"], "source_number": row["question_number"],
        "question_number_spoken": _spoken_question_number(row["question_number"]),
        "number_speaker_label": "NỮ", "source_row": row["source_row"],
        "source_exam": row["exam"], "source_text": row["content"],
        "part": part, "part_label": PART_LABELS.get(part, ""),
        "group": group, "is_shared": group != "Độc lập",
        "stimulus_source": parsed["stimulus_text"],
        "stimulus_segments": parsed["stimulus_segments"],
        "question_segment": parsed["question"],
        "question_source": parsed["question"]["source_text"],
        "question_spoken": parsed["question"]["text"],
        "repeat1": [*parsed["stimulus_segments"], parsed["question"]], "repeat2": [],
    }
    item["preview"] = _build_item_preview(item)
    return item


def validate_hsk5_workbook(excel_path: str | Path, sheet_name: str = "Sheet1") -> Dict[str, object]:
    sheet_name = _resolve_hsk3_sheet_name(excel_path, sheet_name)
    loaded = _load_hsk5_exam_rows(excel_path, sheet_name)
    expected = [f"HSK5_30_{index:02d}" for index in range(1, 18)]
    errors = list(loaded["errors"])
    summaries, semantic_items = {}, {}
    independent_turns: Counter[int] = Counter()
    narration_exams: List[str] = []
    for exam in expected:
        rows = loaded["exams"].get(exam, [])
        parts = {part: sum(row["part"] == part for row in rows) for part in (1, 2)}
        if len(rows) != 35 or [row["number"] for row in rows] != list(range(1, 36)):
            errors.append(f"{exam}: cần đúng 35 câu số 一 đến 三十五 theo thứ tự")
        if parts != {1: 19, 2: 16}:
            errors.append(f"{exam}: cần Part1=19, Part2=16")
        items, groups = [], {}
        for row in rows:
            number, part, group = row["number"], row["part"], row["group"]
            try:
                if number is None or part not in (1, 2):
                    raise ValueError("số câu hoặc Phần không hợp lệ")
                if part != (1 if number <= 19 else 2):
                    raise ValueError("Phần không khớp số câu")
                item = _hsk5_semantic_item(row)
                stimulus = item["stimulus_segments"]
                if number <= 10:
                    if group != "Độc lập" or len(stimulus) < 2 or any(not seg["speaker_token"] for seg in stimulus):
                        raise ValueError("Q1–10 cần Độc lập và ít nhất 2 lượt thoại")
                    independent_turns[len(stimulus)] += 1
                else:
                    if group not in HSK5_AUDIO_PROFILE["expected_shared_groups"]:
                        raise ValueError("Nhóm câu không thuộc layout HSK5")
                    if number not in _hsk4_group_range(group):
                        raise ValueError("số câu nằm ngoài Nhóm câu")
                    groups.setdefault(group, []).append(item)
                items.append(item)
            except ValueError as exc:
                errors.append(f"{exam} Q{number} dòng {row['source_row']}: {exc}")
        if tuple(groups) != HSK5_AUDIO_PROFILE["expected_shared_groups"]:
            errors.append(f"{exam}: thiếu/sai thứ tự nhóm câu")
        for group, entries in groups.items():
            if [item["question_number"] for item in entries] != _hsk4_group_range(group):
                errors.append(f"{exam} nhóm {group}: thiếu/trùng/sai số câu")
            if not entries[0]["stimulus_segments"]:
                errors.append(f"{exam} nhóm {group}: dòng đầu thiếu shared stimulus")
            if any(item["stimulus_segments"] for item in entries[1:]):
                errors.append(f"{exam} nhóm {group}: dòng sau chứa stimulus mới")
            if group == "17–19" and entries[0]["stimulus_segments"] and not entries[0]["stimulus_segments"][0]["speaker_token"]:
                narration_exams.append(exam)
        semantic_items[exam] = items
        summaries[exam] = {"row_count": len(rows), "part_counts": parts,
                           "independent_count": sum(not item["is_shared"] for item in items),
                           "shared_groups": {group: [item["question_number"] for item in entries] for group, entries in groups.items()}}
    unexpected = sorted(set(loaded["exams"]) - set(expected))
    errors.extend(f"Mã đề ngoài danh sách: {exam}" for exam in unexpected)
    return {"status": "PASS" if not errors else "FAIL", "exam_count": len(loaded["exams"]),
            "row_count": sum(len(rows) for rows in loaded["exams"].values()),
            "exams": summaries, "exam_codes": sorted(loaded["exams"]),
            "invalid_rows": errors, "semantic_errors": errors, "sheet": sheet_name,
            "independent_turn_counts": dict(independent_turns),
            "part1_narration_exams": narration_exams, "semantic_items": semantic_items}


def parse_hsk5_phase1(
    excel_path: str | Path, sheet_name: str = "Sheet1", exam_code: str = "HSK5_30_01",
    asset_root: str | Path | None = None, lang_state: Optional[Dict[str, object]] = None,
) -> Dict[str, object]:
    structure = validate_hsk5_workbook(excel_path, sheet_name)
    if structure["status"] != "PASS":
        raise ValueError("Workbook HSK5 không hợp lệ: " + "; ".join(structure["invalid_rows"]))
    exam = _normalize_exam_code(exam_code)
    if exam not in structure["semantic_items"]:
        raise ValueError(f"Không tìm thấy {exam}")
    state = dict(lang_state or {})
    state["speech_rate"] = _parse_speech_rate(state.get("speech_rate", 1.0))
    state["hsk_speed_profile"] = "HSK5"
    state["speech_rate_label"] = _format_hsk_speed_label("HSK5", state["speech_rate"])
    # Preview never needs credentials; the live builder receives them separately.
    for key in ("google_api_key", "aws_access_key_id", "aws_secret_access_key"):
        state.pop(key, None)
    items = structure["semantic_items"][exam]
    summary = structure["exams"][exam]
    return {
        "level": "HSK5", "workbook": str(Path(excel_path)), "sheet": structure["sheet"],
        "detected_exam": exam, "question_count": len(items), "part_counts": summary["part_counts"],
        "missing_invalid_rows": [], "asset_status": validate_hsk30_assets(asset_root, level="hsk5"),
        "lang_state": state, "preview_items": items, "shared_groups": summary["shared_groups"],
        "independent_count": summary["independent_count"], "shared_group_count": len(summary["shared_groups"]),
        "shared_group_count_by_part": {part: sum(entries[0] <= 19 if part == 1 else entries[0] >= 20 for entries in summary["shared_groups"].values()) for part in (1, 2)},
        "output_filename": phase1_output_filename(exam), "tts_requests_made": 0,
        "embedded_marker_count": 0, "repetitions": 1, "structure": structure,
        "status": "READY_FOR_REVIEW",
    }


def format_hsk5_preview_report(report: Dict[str, object]) -> str:
    lines = [f"{report['detected_exam']} parse summary", "Level: HSK5",
             f"Workbook: {report['workbook']}", f"Sheet: {report['sheet']}",
             f"Questions: {report['question_count']}", f"Part counts: {report['part_counts']}",
             f"Independent Part1: {report['independent_count']}",
             f"Shared groups by part: {report['shared_group_count_by_part']}",
             "Question delimiter: LAST 问：", "Repetitions: 1 (no repeat2)",
             f"Assets: {'OK' if report['asset_status']['ok'] else 'MISSING'}",
             f"HSK speed profile: HSK5 / {report['lang_state']['speech_rate_label']}",
             "Dry-run TTS requests: 0", ""]
    for item in report["preview_items"]:
        lines.extend([f"Q{item['question_number']:02d} / {item['part_label']} / {item['group']}",
                      f"Number spoken: NỮ [{item['question_number_spoken']}]",
                      *[_render_segment(seg["speaker_label"], seg["text"]) for seg in item["stimulus_segments"]],
                      f"Question: NỮ [{item['question_spoken']}]", ""])
    return "\n".join(lines)


def format_hsk5_build_report(report: Dict[str, object]) -> str:
    lines = [f"Exam: {report['exam']}", f"Output: {report['output_file']}",
             f"File size: {report['file_size_bytes']} bytes", f"Speed: {report['speech_rate_label']}",
             "Voices: from Lang; number/question female, stimulus follows semantic mapping",
             f"Part1 duration: {report['part_breakdown'][1]} ms",
             f"Part2 duration: {report['part_breakdown'][2]} ms"]
    lines.append(f"Fixed assets version: {report.get('fixed_assets_version', '')}")
    for label, key in (("Music", "music_ms"), ("Fixed intro/part assets", "fixed_assets_ms"),
                       ("Fixed cue", "fixed_cues_ms"), ("Generated speech including safety pads", "generated_speech_ms"),
                       ("Active speech span (internal linguistic pauses retained)", "active_speech_ms"),
                       ("Edge silence trimmed", "edge_silence_trimmed_ms"),
                       ("Speaker gaps", "speaker_gap_ms"), ("Content to question gaps", "content_to_question_ms"),
                       ("Number to content gaps", "number_to_content_ms"),
                       ("Shared stimulus to number gaps", "shared_passage_to_number_ms"),
                       ("Number to question gaps", "number_to_question_ms"),
                       ("Answer pauses", "answer_pause_ms"), ("Part transitions", "part_transitions_ms"),
                       ("Outro", "outtro_ms")):
        lines.append(f"{label}: {report[key]} ms")
    lines.extend([
        f"Edge metrics available: {report['metrics_available_segments']} / {len(report['speech_metrics'])} segments",
        f"Independent questions: {report['independent_items']}", f"Shared groups: {report['shared_groups']}",
        "Repetitions: 1 / Repeat gap: 0", f"TTS requests: {report['unique_tts_requests']}",
        f"Cache reused: {report['reused_segments']}", "Output: AAC-LC / 48000 Hz / mono / 32 kbps",
        "Reference: ~25:00", "Full sample reference: 26:31.98",
        "Meaningful sample reference: ~26:11.7 (no trailing-silence padding)",
        f"Actual duration: {report['actual_duration_ms']} ms",
        f"Difference vs meaningful sample: {report['actual_duration_ms'] - report['meaningful_sample_duration_ms']} ms",
        "Status: INFO", "", "Timeline:", " → ".join(report["timeline"]),
    ])
    lines.extend(["", "Fixed asset -> shared passage audit (natural trailing silence belongs to asset):"])
    for boundary in report.get("fixed_asset_boundaries", []):
        lines.append(f"Q{_hsk4_group_range(boundary['group'])[0]}: answer_pause -> {boundary['asset']} -> shared_passage_{boundary['group']}; "
                     f"asset {boundary['asset_start_ms']}..{boundary['asset_end_ms']} ms; "
                     f"passage starts {boundary['passage_start_ms']} ms; added gap {boundary['manual_gap_ms']} ms")
    if report.get("asset_durations_ms"):
        lines.extend(["", "Fixed asset mapping / decoded durations:"])
        for key, duration in report["asset_durations_ms"].items():
            lines.append(f"{key}: {duration} ms; {report.get('asset_mapping', {}).get(key, '')}")
    return "\n".join(lines) + "\n"


def audit_hsk5_timeline(report: Dict[str, object]) -> Dict[str, bool]:
    timeline = report["timeline"]
    checks = {
        "numbers_once": all(timeline.count(f"number{n}") == 1 for n in range(1, 36)),
        "questions_once": all(timeline.count(f"question{n}") == 1 for n in range(1, 36)),
        "answer_pauses": timeline.count("answer_pause") == 35,
        "no_repeat_gap": report["repeat_gap_ms"] == 0 and "repeat_gap" not in timeline,
        "v2_assets": report.get("fixed_assets_version") == HSK5_AUDIO_PROFILE["fixed_assets_version"],
        "fixed_cues_once": all(timeline.count(f"hsk5_{key}") == 1 for key in HSK5_AUDIO_PROFILE["cues_before_question"].values()),
        "obsolete_cue_absent": "hsk5_cue_q11_19" not in timeline,
        "part2_once": timeline.count("hsk5_part2") == 1,
        "no_manual_asset_gap": "part2_start_gap" not in timeline and "cue_to_stimulus_gap" not in timeline,
        "outro_once": timeline.count("outro") == 1 and timeline[-1] == "outro",
    }
    for group in HSK5_AUDIO_PROFILE["expected_shared_groups"]:
        token = f"shared_passage_{group}"
        sequence = [token, "shared_passage_to_number_gap"]
        for number in _hsk4_group_range(group):
            sequence.extend([f"number{number}", "number_to_question_gap", f"question{number}", "answer_pause"])
        position = timeline.index(token) if token in timeline else -1
        checks[f"group_{group}"] = timeline.count(token) == 1 and timeline[position:position + len(sequence)] == sequence
        start_number = _hsk4_group_range(group)[0]
        fixed_token = "hsk5_part2" if start_number == 20 else f"hsk5_{HSK5_AUDIO_PROFILE['cues_before_question'][start_number]}"
        checks[f"fixed_boundary_{group}"] = position >= 2 and timeline[position - 2:position] == ["answer_pause", fixed_token]
    boundaries = report.get("fixed_asset_boundaries", [])
    checks["ten_fixed_boundaries"] = [entry["group"] for entry in boundaries] == list(HSK5_AUDIO_PROFILE["expected_shared_groups"])
    checks["zero_added_gap"] = len(boundaries) == 10 and all(
        entry["manual_gap_ms"] == 0 and entry["asset_end_ms"] == entry["passage_start_ms"]
        for entry in boundaries)
    if not all(checks.values()):
        raise ValueError(f"HSK5 timeline validation FAIL: {checks}")
    return checks


def dry_run_hsk5_batch(excel_path: str | Path, *, lang_state=None) -> List[Dict[str, object]]:
    previews = []
    for index in range(1, 18):
        preview = parse_hsk5_phase1(excel_path, exam_code=f"HSK5_30_{index:02d}", lang_state=lang_state)
        if not preview["asset_status"]["ok"]:
            raise FileNotFoundError(f"HSK5 assets missing: {preview['asset_status']['missing']}")
        previews.append(preview)
    return previews


def _hsk5_file_digest(path: str | Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


HSK5_COMPATIBLE_BATCH_CODE_HASHES = (
    # Pre-recovery release: same approved profile/assets/voices, decoder/cache fixes only.
    "abcac9e4c8db726043a14138ef446837dc3032cdb1f8ab43d98fa9cc1d421d0d",
    # HSK6 extension does not invalidate approved HSK5 resumable outputs.
    "0d629eba97acc1f0f26ef8602bba2195076e58712f2962e3a2dceb33f7acd496",
)


def _hsk5_batch_identity(source: str | Path, lang_state: Dict[str, object], *, code_digest=None) -> str:
    # Secrets never belong in reports. Hash source, processing code and assets for safe resume.
    state = {key: lang_state.get(key) for key in
             ("engine", "speech_rate", "zh_male_voice", "zh_female_voice", "zh_narrator_voice", "aws_region")}
    files = [Path(source), Path(__file__), BASE_DIR / "pipelines" / "pronunciation_overrides.py",
             HSK30_COMMON_ASSET, HSK30_COMMON_OUTTRO, *HSK30_HSK5_ASSETS.values()]
    digests = [_hsk5_file_digest(path) for path in files]
    if code_digest is not None:
        digests[1] = code_digest
    payload = {"files": digests, "state": state,
               "profile": HSK5_AUDIO_PROFILE, "processing": HSK3_TTS_POSTPROCESS_VERSION}
    return hashlib.sha256(json.dumps(payload, sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest()


def _write_hsk5_json(path: Path, data: object) -> None:
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(data, stream, ensure_ascii=False, indent=2)
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def build_hsk5_batch(excel_path: str | Path, output_dir: str | Path, *, lang_state=None,
                     tts_provider=None, progress_callback=None) -> List[Dict[str, object]]:
    if not HSK5_BATCH_APPROVED:
        raise ValueError("HSK5 fixed assets v2: batch locked pending approval of the new golden")
    state = dict(lang_state or {})
    state.setdefault("speech_rate", 1.0)
    state["hsk_speed_profile"] = "HSK5"
    previews = dry_run_hsk5_batch(excel_path, lang_state=state)
    identity = _hsk5_batch_identity(excel_path, state)
    compatible_identities = {identity, *(_hsk5_batch_identity(excel_path, state, code_digest=digest)
                                        for digest in HSK5_COMPATIBLE_BATCH_CODE_HASHES)}
    source_digest = _hsk5_file_digest(excel_path)
    destination = Path(output_dir)
    destination.mkdir(parents=True, exist_ok=True)
    results = []
    for index, preview in enumerate(previews, start=1):
        exam = preview["detected_exam"]
        output = destination / preview["output_filename"]
        sidecar = output.with_suffix(".report.json")
        result = {"exam": exam, "level": "HSK5", "output_file": str(output),
                  "question_count": 35, "part_counts": {1: 19, 2: 16}, "shared_groups": 10,
                  "unique_tts_requests": 0, "reused_segments": 0,
                  "validation": "PASS", "status": "FAIL"}
        if progress_callback:
            progress_callback(f"[{index}/17] {exam}: starting")
        try:
            if _hsk5_file_digest(excel_path) != source_digest:
                raise ValueError("Excel changed during batch; revalidate before building")
            if output.exists():
                previous = json.loads(sidecar.read_text(encoding="utf-8")) if sidecar.is_file() else {}
                if (previous.get("batch_identity") not in compatible_identities or not output.is_file()
                        or previous.get("audio_sha256") != _hsk5_file_digest(output)):
                    raise ValueError("Existing output is unverified or source/settings changed; choose a new output folder. Not overwritten.")
                audit_hsk5_timeline(previous)
                if abs(_probe_audio_duration_ms(output) - previous["actual_duration_ms"]) > 50:
                    raise ValueError("Existing output duration does not match its report")
                result.update(previous, status="SKIP_VERIFIED", unique_tts_requests=0, reused_segments=0)
            else:
                # A failed build may already have called TTS; do not report an invented zero.
                result.update(unique_tts_requests=None, reused_segments=None)
                built = build_hsk5_phase1b(preview, output, lang_state=state, tts_provider=tts_provider,
                                          progress_callback=(lambda message: progress_callback(f"{exam}: {message}")) if progress_callback else None)
                result.update(built)
                result["timeline_checks"] = audit_hsk5_timeline(built)
                result["voice_mapping"] = {key: state.get(key, "") for key in
                                           ("zh_male_voice", "zh_female_voice", "zh_narrator_voice")}
                result["question_voice_audit"] = [
                    {"question": item["question_number"], "group": item["group"],
                     "stimulus_speakers": [segment["speaker_label"] for segment in item["stimulus_segments"]],
                     "question_speaker": item["question_segment"]["speaker_label"],
                     "stimulus_source": item["stimulus_source"], "question_spoken": item["question_spoken"]}
                    for item in preview["preview_items"]]
                result.update(batch_identity=identity, audio_sha256=_hsk5_file_digest(output),
                              source_path=str(Path(excel_path).resolve()), validation="PASS")
                _write_hsk5_json(sidecar, result)
        except Exception as exc:
            result.update(status="FAIL", validation="FAIL", error=str(exc))
        results.append(result)
        _write_hsk5_json(destination / "hsk5-batch.report.json", results)
        if progress_callback:
            progress_callback(f"[{index}/17] {exam}: {result['status']} / TTS {result['unique_tts_requests']} / cache {result['reused_segments']}")
    return results


def format_hsk5_batch_report(results: List[Dict[str, object]]) -> str:
    lines = ["| Exam | Duration | File size | Questions | Part1 | Part2 | Shared groups | TTS new | Cache reuse | Validation | Status |",
             "| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | --- | --- |"]
    for result in results:
        seconds = result.get("actual_duration_ms", 0) / 1000
        duration = f"{int(seconds // 60):02d}:{seconds % 60:05.2f}" if seconds else "-"
        size = f"{result.get('file_size_bytes', 0) / 1_000_000:.2f} MB"
        new_tts = result["unique_tts_requests"] if result["unique_tts_requests"] is not None else "unknown"
        reused = result["reused_segments"] if result["reused_segments"] is not None else "unknown"
        lines.append(f"| {result['exam']} | {duration} | {size} | {result['question_count']} | 19 | 16 | {result['shared_groups']} | {new_tts} | {reused} | {result['validation']} | {result['status']} |")
    for result in results:
        if result.get("error"):
            lines.append(f"\n{result['exam']}: {result['error']}")
    return "\n".join(lines)


def _hsk6_split_content(text: str, *, owns_passage: bool) -> Tuple[str, str]:
    """Split the exam question, never an interviewer's internal question."""
    source = _clean_text(text)
    if not source.endswith("？"):
        raise ValueError("final question phải kết thúc bằng ？")
    if owns_passage:
        passage, delimiter, question = source.rpartition("。")
        question = question.strip()
        if not delimiter or not passage.strip() or not question.removesuffix("？").strip():
            raise ValueError("không tách chắc chắn passage + final question tại dấu 。 cuối")
        if "？" in question[:-1] or "?" in question:
            raise ValueError("final question chứa nhiều câu hỏi; cần kiểm tra nguồn")
        return passage.strip() + delimiter, question
    if "。" in source or "？" in source[:-1] or not source.removesuffix("？").strip():
        raise ValueError("later row phải chỉ chứa một question, không có stimulus mới")
    return "", source


def _hsk6_segment(text: str, label: str) -> Dict[str, str]:
    # Prefix-looking natural words are content, not HSK6 speaker metadata.
    return {"speaker_label": label, "speaker_token": "", "source_text": text,
            "text": _normalize_tts_text(text)}


def _hsk6_required_columns(header: Dict[str, str]) -> Dict[str, str]:
    compatible = dict(header)
    for column, value in header.items():
        if _normalize_hsk3_header(value) == "noidungnghe":
            compatible[column] = "NỘI DUNG PHẦN NGHE"
    return _hsk4_required_columns(compatible)


HSK6_INTERVIEW_TURN_RE = re.compile(r"(主持人|嘉宾)\s*[：:]\s*(.+)")


def _hsk6_interview_segments(passage: str, group: str) -> List[Dict[str, str]]:
    source = _clean_text(passage)
    role_voices = HSK6_AUDIO_PROFILE["interview_role_voices_by_group"].get(group)
    if not role_voices or set(role_voices) != {"主持人", "嘉宾"} or set(role_voices.values()) != {"NAM", "NỮ"}:
        raise ValueError(f"interview group {group} chưa có voice mapping hợp lệ")
    lines = [line.strip() for line in source.splitlines() if line.strip()]
    segments = []
    for index, line in enumerate(lines):
        match = HSK6_INTERVIEW_TURN_RE.fullmatch(line)
        if not match:
            raise ValueError(f"interview turn {index + 1} thiếu nhãn 主持人：/嘉宾： hoặc có narration không gắn nhãn")
        role, text = match.group(1), match.group(2).strip()
        if not text or text.rstrip('”」』"')[-1] not in "。？！.!?":
            raise ValueError(f"speaker turn {index + 1} bị rỗng hoặc thiếu dấu kết thúc")
        segment = _hsk6_segment(text, role_voices[role])
        segment.update(speaker_token=role, source_text=text)
        segments.append(segment)
    if len(segments) != 10 or [segment["speaker_token"] for segment in segments] != ["主持人", "嘉宾"] * 5:
        raise ValueError("interview cần đúng 10 lượt luân phiên 主持人 → 嘉宾")
    return segments


def validate_hsk6_workbook(excel_path: str | Path, sheet_name: str = "Sheet1") -> Dict[str, object]:
    sheet_name = _resolve_hsk3_sheet_name(excel_path, sheet_name)
    rows = read_xlsx_rows(excel_path, sheet_name)
    if not rows:
        raise ValueError("Workbook HSK6 không có dữ liệu")
    columns = _hsk6_required_columns(rows[0].cells)
    headers = {_normalize_hsk3_header(value): column for column, value in rows[0].cells.items()}
    for name, header in (("skill", "kynang"), ("item_type", "dangbai")):
        if header not in headers:
            raise ValueError(f"Workbook HSK6 thiếu cột {header}")
        columns[name] = headers[header]
    expected_exams = [f"HSK6-30-{index:02d}" for index in range(1, 19)]
    exams: Dict[str, List[Dict[str, object]]] = {}
    errors = []
    first_rows = 0
    no_space_rows = []
    interview_patterns = Counter()
    interview_speakers = Counter()
    interview_roles = Counter()
    type_counts = Counter()
    interview_types = {_normalize_hsk3_header(label) for label in
                       ("Phỏng vấn – Nghe hội thoại, trả lời câu hỏi",
                        "Phỏng vấn – Nghe phỏng vấn, chọn đáp án đúng")}
    types = {
        1: {_normalize_hsk3_header(label) for label in
            ("Đoạn văn ngắn – Chọn phát biểu đúng", "Nghe đoạn văn ngắn – Chọn phát biểu đúng")},
        2: {_normalize_hsk3_header("Nghe đoạn văn dài – Trả lời câu hỏi")},
        3: {_normalize_hsk3_header("Nghe bài nói dài – Trả lời câu hỏi"), *interview_types},
    }
    for row in rows[1:]:
        values = {key: _clean_text(row.cells.get(column, "")) for key, column in columns.items()}
        exam = _normalize_exam_code(values["exam"])
        number = _hsk3_number_value(values["question_number"])
        part = _parse_part_label(values["part"])
        group = _hsk4_normalize_group(values["group"])
        location = f"{exam or 'HSK6'} source row {row.row_number} Q{number}"
        if not all(values.values()):
            errors.append(f"{location}: thiếu cột bắt buộc")
            continue
        if exam not in expected_exams or number not in range(1, 41):
            errors.append(f"{location}: mã đề hoặc số câu không hợp lệ")
            continue
        expected_part = 1 if number <= 8 else (2 if number <= 28 else 3)
        expected_group = "1–8" if part == 1 else next(
            (value for value in HSK6_AUDIO_PROFILE["expected_shared_groups"]
             if number in _hsk4_group_range(value)), "")
        if part != expected_part or group != expected_group:
            errors.append(f"{location}: Phần/Nhóm câu không khớp layout HSK6")
            continue
        item_type = _normalize_hsk3_header(values["item_type"])
        is_interview = part == 3 and item_type in interview_types
        if item_type not in types[part]:
            errors.append(f"{location}: Dạng bài HSK6 không được hỗ trợ '{values['item_type']}'")
            continue
        owns_passage = part > 1 and number == _hsk4_group_range(group)[0]
        try:
            if part == 1:
                passage, question = values["content"], ""
            else:
                passage, question = _hsk6_split_content(values["content"], owns_passage=owns_passage)
            label = ("NAM" if number % 2 else "NỮ") if part == 1 else HSK6_AUDIO_PROFILE["narration_group_voices"][group]
            stimulus = (_hsk6_interview_segments(passage, group) if is_interview else [_hsk6_segment(passage, label)]) if passage else []
        except ValueError as exc:
            errors.append(f"{location}: {exc}")
            continue
        if owns_passage:
            first_rows += 1
            after_period = values["content"].rsplit("。", 1)[1]
            if not after_period[:1].isspace():
                no_space_rows.append(row.row_number)
            if is_interview:
                interview_patterns[len(stimulus)] += 1
                interview_speakers.update(segment["speaker_label"] for segment in stimulus)
                interview_roles.update(segment["speaker_token"] for segment in stimulus)
        type_counts[values["item_type"]] += 1
        question_segment = _hsk6_segment(question, "NỮ") if question else None
        exams.setdefault(exam, []).append({
            "question_number": number, "part": part, "part_label": PART_LABELS[part],
            "group": group, "is_shared": part != 1, "source_row": row.row_number,
            "item_type": values["item_type"], "is_interview": is_interview,
            "source_exam": exam, "source_number": values["question_number"],
            "question_number_spoken": _spoken_question_number(values["question_number"]),
            "number_speaker_label": "NỮ", "source_text": values["content"],
            "stimulus_source": passage, "stimulus_segments": stimulus,
            "question_segment": question_segment, "question_source": question,
            "question_spoken": question_segment["text"] if question_segment else "",
            "repeat1": stimulus + ([question_segment] if question_segment else []), "repeat2": [],
        })
    summaries = {}
    for exam in expected_exams:
        items = exams.get(exam, [])
        numbers = [item["question_number"] for item in items]
        counts = dict(Counter(item["part"] for item in items))
        groups = {group: [item["question_number"] for item in items if item["is_shared"] and item["group"] == group]
                  for group in HSK6_AUDIO_PROFILE["expected_shared_groups"]}
        if numbers != list(range(1, 41)) or counts != HSK6_AUDIO_PROFILE["part_counts"]:
            errors.append(f"{exam}: cần 40 câu duy nhất đúng thứ tự; Part1/2/3 = 8/20/12")
        for group, group_numbers in groups.items():
            if group_numbers != _hsk4_group_range(group):
                errors.append(f"{exam}: nhóm {group} sai size hoặc thiếu câu")
        summaries[exam] = {"row_count": len(items), "part_counts": counts, "independent_count": 8,
                           "shared_groups": groups, "shared_group_count": 9}
    if len(rows) != 721 or [row.row_number for row in rows] != list(range(1, 722)):
        errors.append("HSK6 cần đúng 720 dòng dữ liệu liên tục sau header")
    return {"workbook": str(Path(excel_path)), "sheet": sheet_name, "source_columns": columns,
            "exam_count": len(exams), "row_count": len(rows) - 1, "exam_codes": list(exams),
            "exams": summaries, "parsed_items": exams, "first_group_rows_split": first_rows,
            "interview_groups": sum(interview_patterns.values()), "interview_turn_counts": dict(interview_patterns),
            "interview_speaker_counts": dict(interview_speakers), "interview_role_counts": dict(interview_roles),
            "later_question_rows": sum(len(items) - 8 - 9 for items in exams.values()),
            "item_type_counts": dict(type_counts),
            "no_space_first_group_rows": no_space_rows, "invalid_rows": errors,
            "semantic_errors": errors, "status": "PASS" if not errors else "FAIL"}


def hsk6_proposed_timeline(items: List[Dict[str, object]]) -> List[str]:
    timeline = ["music", "music_to_intro_gap", "hsk6_intro", "intro_to_part_gap", "hsk6_part1"]
    for item in items:
        number = item["question_number"]
        if number in (9, 29):
            timeline.append(f"hsk6_part{item['part']}")
        if item["is_shared"]:
            if item["stimulus_segments"]:
                timeline.extend([f"hsk6_{HSK6_AUDIO_PROFILE['cues_before_question'][number]}",
                                 f"shared_passage_{item['group']}", "shared_passage_to_number_gap"])
            timeline.extend([f"number{number}", "number_to_question_gap", f"question{number}", "answer_pause"])
        else:
            timeline.extend([f"number{number}", "number_to_content_gap", f"stimulus{number}", "answer_pause"])
    return timeline + ["final_silence", "outro"]


def parse_hsk6_phase1(excel_path: str | Path, sheet_name: str = "Sheet1", exam_code: str = "HSK6-30-01",
                      asset_root: str | Path | None = None, lang_state: Optional[Dict[str, object]] = None) -> Dict[str, object]:
    structure = validate_hsk6_workbook(excel_path, sheet_name)
    if structure["status"] != "PASS":
        raise ValueError("Workbook HSK6 không hợp lệ: " + "; ".join(structure["invalid_rows"]))
    exam = _normalize_exam_code(exam_code)
    if exam not in structure["parsed_items"]:
        raise ValueError(f"Không tìm thấy {exam}")
    items = structure["parsed_items"][exam]
    state = {key: value for key, value in (lang_state or {}).items()
             if key in ("engine", "speed", "voice", "zh_male_voice", "zh_female_voice", "zh_narrator_voice")}
    rate = _parse_speech_rate((lang_state or {}).get("speech_rate", 1.00))
    state.update(speech_rate=rate, hsk_speed_profile="HSK6", speech_rate_label=_format_hsk_speed_label("HSK6", rate))
    summary = structure["exams"][exam]
    return {"level": "HSK6", "workbook": str(Path(excel_path)), "sheet": structure["sheet"],
            "detected_exam": exam, "question_count": len(items), "part_counts": summary["part_counts"],
            "independent_count": 8, "shared_group_count": 9, "shared_groups": summary["shared_groups"],
            "asset_status": validate_hsk30_assets(asset_root, "hsk6"), "missing_invalid_rows": [],
            "lang_state": state, "preview_items": items, "output_filename": phase1_output_filename(exam),
            "proposed_timeline": hsk6_proposed_timeline(items), "structure": structure,
            "status": "READY_FOR_REVIEW", "repetitions": 1, "tts_requests_made": 0}


def format_hsk6_preview_report(report: Dict[str, object]) -> str:
    structure = report["structure"]
    lines = [f"{report['detected_exam']} parse summary", f"Workbook: {report['workbook']}",
             f"Questions: {report['question_count']} / Part counts: {report['part_counts']}",
             "Independent Part1: 8 (Nhóm câu 1–8 is NOT shared)", "Shared groups: 9 (6 Part2 / 3 Part3)",
             f"Workbook: {structure['exam_count']} exams / {structure['row_count']} rows",
             f"First group rows split: {structure['first_group_rows_split']}/162",
             f"Interview groups: {structure['interview_groups']} / Turns: {structure['interview_turn_counts']} / Speakers: {structure['interview_speaker_counts']}",
             f"No-space first rows: {structure['no_space_first_group_rows']}",
             f"Validation errors: {structure['invalid_rows']}",
             f"Assets: {'OK' if report['asset_status']['ok'] else report['asset_status']['missing']}",
             f"Speed: {report['lang_state']['speech_rate_label']}",
             "Number/question narrator: female; playback: 1; dry-run TTS requests: 0",
             "Batch HSK6 enabled after manual golden approval.", ""]
    for item in report["preview_items"]:
        lines.append(f"Q{item['question_number']} / Part{item['part']} / {item['group']} / source row {item['source_row']}")
        lines.extend(_render_segment(segment["speaker_label"], segment["text"]) for segment in item["stimulus_segments"])
        if item["question_segment"]:
            lines.append("Question: " + _render_segment("NỮ", item["question_spoken"]))
    lines += ["", "Proposed timeline:", " -> ".join(report["proposed_timeline"])]
    return "\n".join(lines) + "\n"


def _hsk6_render_independent(item: Dict[str, object], *, profile: Dict[str, object], **kwargs) -> Tuple[AudioSegment, List[str]]:
    number = _hsk4_number_audio(item, **kwargs)
    narration = _hsk4_tts_segment(item["stimulus_segments"][0], **kwargs)
    gap = profile["number_to_content_ms"]
    answer = profile["answer_pause_by_part"][1]
    kwargs["counts"]["internal_pauses_ms"] += gap
    kwargs["counts"]["number_to_content_ms"] += gap
    kwargs["counts"]["answer_pause_ms"] += answer
    audio = number + _internal_pause_segment(gap) + narration + _internal_pause_segment(answer)
    return audio, [f"number{item['question_number']}", "number_to_content_gap", f"stimulus{item['question_number']}", "answer_pause"]


def build_hsk6_phase1b(report: Dict[str, object], output_file: str | Path, *, lang_state=None,
                      tts_provider=None, progress_callback=None) -> Dict[str, object]:
    state = dict(lang_state or report.get("lang_state", {}))
    if not state.get("zh_male_voice") or not state.get("zh_female_voice"):
        raise ValueError("HSK6 cần cấu hình voice zh nam và nữ từ Lang")
    state["zh_narrator_voice"] = state["zh_female_voice"]
    result = _build_single_playback_exam(report, output_file, lang_state=state, tts_provider=tts_provider,
                                        progress_callback=progress_callback, profile=HSK6_AUDIO_PROFILE, assets=HSK30_HSK6_ASSETS)
    result.update(question_count=40, part_counts=HSK6_AUDIO_PROFILE["part_counts"],
                  question_count_by_part={1: 0, 2: 20, 3: 12},
                  source_path=report["workbook"], voice_mapping={key: state[key] for key in
                    ("zh_male_voice", "zh_female_voice", "zh_narrator_voice")})
    interviews = [item for item in report["preview_items"] if item.get("is_interview") and item["stimulus_segments"]]
    result.update(interview_groups=len(interviews),
                  interview_speaker_counts=dict(Counter(segment["speaker_label"] for item in interviews for segment in item["stimulus_segments"])),
                  interview_role_counts=dict(Counter(segment["speaker_token"] for item in interviews for segment in item["stimulus_segments"])),
                  interview_turn_counts={item["group"]: len(item["stimulus_segments"]) for item in interviews},
                  expected_speech_segments=sum(1 + len(item["stimulus_segments"]) + bool(item["question_segment"]) for item in report["preview_items"]))
    result["timeline_checks"] = audit_hsk6_timeline(result, report["proposed_timeline"])
    return result


def audit_hsk6_timeline(report: Dict[str, object], proposed=None) -> Dict[str, bool]:
    timeline = report["timeline"]
    checks = {
        "numbers_once": all(timeline.count(f"number{n}") == 1 for n in range(1, 41)),
        "independent_part1": all(timeline.count(f"stimulus{n}") == 1 and f"question{n}" not in timeline for n in range(1, 9)),
        "questions_once": all(timeline.count(f"question{n}") == 1 for n in range(9, 41)),
        "answer_pauses_40": timeline.count("answer_pause") == 40,
        "no_repeat": report["repeat_gap_ms"] == 0 and "repeat_gap" not in timeline,
        "outro_once": timeline.count("outro") == 1 and timeline[-1] == "outro",
        "parts_once": all(timeline.count(f"hsk6_part{part}") == 1 for part in (1, 2, 3)),
        "no_part_start_gap": {int(key): value for key, value in report["part_start_gaps_ms"].items()} == {1: 0, 2: 0, 3: 0},
        "no_manual_cue_gap": "cue_to_stimulus_gap" not in timeline,
        "proposed_order": proposed is None or timeline == proposed,
    }
    boundaries = report["fixed_asset_boundaries"]
    checks["nine_direct_boundaries"] = [entry["group"] for entry in boundaries] == list(HSK6_AUDIO_PROFILE["expected_shared_groups"]) and all(
        entry["manual_gap_ms"] == 0 and entry["asset_end_ms"] == entry["passage_start_ms"] for entry in boundaries)
    for group in HSK6_AUDIO_PROFILE["expected_shared_groups"]:
        numbers = _hsk4_group_range(group)
        cue = "hsk6_" + HSK6_AUDIO_PROFILE["cues_before_question"][numbers[0]]
        passage = "shared_passage_" + group
        expected = [cue, passage, "shared_passage_to_number_gap"]
        for number in numbers:
            expected += [f"number{number}", "number_to_question_gap", f"question{number}", "answer_pause"]
        position = timeline.index(cue) if cue in timeline else -1
        checks[f"group_{group}"] = timeline.count(cue) == 1 and timeline.count(passage) == 1 and timeline[position:position + len(expected)] == expected
    if not all(checks.values()):
        raise ValueError(f"HSK6 timeline FAIL: {checks}")
    return checks


def format_hsk6_build_report(report: Dict[str, object]) -> str:
    lines = [f"Exam: {report['exam']}", f"Output: {report['output_file']}",
             f"Duration: {report['actual_duration_ms'] / 1000:.3f} s", f"File size: {report['file_size_bytes']} bytes",
             f"Speed: {report['speech_rate_label']}", "Format: AAC-LC / 48 kHz / mono / 32 kbps",
             "40 items / 8 independent narrations / 32 questions / 9 shared groups; one playback only",
             f"Voice mapping: {report['voice_mapping']}",
             f"Interviews: {report['interview_groups']} / Turns per group: {report['interview_turn_counts']} / Male-Female turns: {report['interview_speaker_counts']}",
             f"Active speech span (internal linguistic pauses retained): {report['active_speech_ms']} ms",
             f"TTS segment total: {report['generated_speech_ms']} ms",
             f"Edge silence trimmed: {report['edge_silence_trimmed_ms']} ms",
             f"Answer pauses: {report['answer_pause_ms']} ms / 40 pauses",
             f"Fixed intro/parts: {report['fixed_assets_ms']} ms / Cues: {report['fixed_cues_ms']} ms",
             f"TTS new: {report['unique_tts_requests']} / Cache reuse: {report['reused_segments']}",
             f"Timeline checks: {report['timeline_checks']}", "Duration status: INFO; no auto-adjustment", ""]
    for part, duration in report["part_breakdown"].items():
        lines.append(f"Part{part}: {duration} ms")
    for entry in report["fixed_asset_boundaries"]:
        lines.append(f"{entry['group']}: {entry['asset']} {entry['asset_start_ms']}..{entry['asset_end_ms']} ms -> "
                     f"passage {entry['passage_start_ms']} ms; manual gap {entry['manual_gap_ms']} ms")
    return "\n".join(lines) + "\n"


def dry_run_hsk6_batch(excel_path: str | Path, *, lang_state=None) -> List[Dict[str, object]]:
    previews = []
    for index in range(1, 19):
        preview = parse_hsk6_phase1(excel_path, exam_code=f"HSK6-30-{index:02d}", lang_state=lang_state)
        if not preview["asset_status"]["ok"]:
            raise FileNotFoundError(f"HSK6 assets missing: {preview['asset_status']['missing']}")
        previews.append(preview)
    return previews


def _hsk6_batch_identity(source: str | Path, lang_state: Dict[str, object]) -> str:
    state = {key: lang_state.get(key) for key in
             ("engine", "speech_rate", "zh_male_voice", "zh_female_voice", "zh_narrator_voice", "aws_region")}
    files = [Path(source), Path(__file__), BASE_DIR / "pipelines" / "pronunciation_overrides.py",
             HSK30_COMMON_ASSET, HSK30_COMMON_OUTTRO, *HSK30_HSK6_ASSETS.values()]
    payload = {"files": [_hsk5_file_digest(path) for path in files], "state": state,
               "profile": HSK6_AUDIO_PROFILE, "processing": HSK3_TTS_POSTPROCESS_VERSION}
    return hashlib.sha256(json.dumps(payload, sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest()


def build_hsk6_batch(excel_path: str | Path, output_dir: str | Path, *, lang_state=None,
                     tts_provider=None, progress_callback=None) -> List[Dict[str, object]]:
    if not HSK6_BATCH_APPROVED:
        raise ValueError("HSK6 batch locked pending approval of the golden")
    state = dict(lang_state or {})
    state.setdefault("speech_rate", 1.0)
    state["hsk_speed_profile"] = "HSK6"
    previews = dry_run_hsk6_batch(excel_path, lang_state=state)
    identity = _hsk6_batch_identity(excel_path, state)
    source_digest = _hsk5_file_digest(excel_path)
    destination = Path(output_dir)
    destination.mkdir(parents=True, exist_ok=True)
    results = []
    for index, preview in enumerate(previews, start=1):
        exam = preview["detected_exam"]
        output = destination / preview["output_filename"]
        sidecar = output.with_suffix(".report.json")
        result = {"exam": exam, "level": "HSK6", "output_file": str(output),
                  "question_count": 40, "part_counts": {1: 8, 2: 20, 3: 12}, "shared_groups": 9,
                  "unique_tts_requests": 0, "reused_segments": 0,
                  "validation": "PASS", "status": "FAIL"}
        if progress_callback:
            progress_callback(f"[{index}/18] {exam}: starting")
        try:
            if _hsk5_file_digest(excel_path) != source_digest:
                raise ValueError("Excel changed during batch; revalidate before building")
            if output.exists():
                previous = json.loads(sidecar.read_text(encoding="utf-8")) if sidecar.is_file() else {}
                if (previous.get("batch_identity") != identity or not output.is_file()
                        or previous.get("audio_sha256") != _hsk5_file_digest(output)):
                    raise ValueError("Existing output is unverified or source/settings changed; choose a new output folder. Not overwritten.")
                audit_hsk6_timeline(previous)
                if abs(_probe_audio_duration_ms(output) - previous["actual_duration_ms"]) > 50:
                    raise ValueError("Existing output duration does not match its report")
                result.update(previous, status="SKIP_VERIFIED", unique_tts_requests=0, reused_segments=0)
            else:
                result.update(unique_tts_requests=None, reused_segments=None)
                built = build_hsk6_phase1b(
                    preview, output, lang_state=state, tts_provider=tts_provider,
                    progress_callback=(lambda message: progress_callback(f"{exam}: {message}")) if progress_callback else None,
                )
                result.update(built)
                result["timeline_checks"] = audit_hsk6_timeline(built)
                result.update(batch_identity=identity, audio_sha256=_hsk5_file_digest(output),
                              source_path=str(Path(excel_path).resolve()), validation="PASS")
                _write_hsk5_json(sidecar, result)
        except Exception as exc:
            result.update(status="FAIL", validation="FAIL", error=str(exc))
        results.append(result)
        _write_hsk5_json(destination / "hsk6-batch.report.json", results)
        if progress_callback:
            progress_callback(f"[{index}/18] {exam}: {result['status']} / TTS {result['unique_tts_requests']} / cache {result['reused_segments']}")
    return results


def format_hsk6_batch_report(results: List[Dict[str, object]]) -> str:
    lines = ["| Exam | Duration | File size | Questions | Part1 | Part2 | Part3 | Shared groups | TTS new | Cache reuse | Validation | Status |",
             "| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | --- | --- |"]
    for result in results:
        seconds = result.get("actual_duration_ms", 0) / 1000
        duration = f"{int(seconds // 60):02d}:{seconds % 60:05.2f}" if seconds else "-"
        size = f"{result.get('file_size_bytes', 0) / 1_000_000:.2f} MB"
        new_tts = result["unique_tts_requests"] if result["unique_tts_requests"] is not None else "unknown"
        reused = result["reused_segments"] if result["reused_segments"] is not None else "unknown"
        lines.append(f"| {result['exam']} | {duration} | {size} | {result['question_count']} | 8 | 20 | 12 | "
                     f"{result['shared_groups']} | {new_tts} | {reused} | {result['validation']} | {result['status']} |")
    for result in results:
        if result.get("error"):
            lines.append(f"\n{result['exam']}: {result['error']}")
    return "\n".join(lines)
