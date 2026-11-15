"""HSK 7-9 listening: seven passages, two work blocks and thirty questions."""
import copy
import hashlib
import json
import re
from collections import Counter
from pathlib import Path

from pydub import AudioSegment

from . import hsk30_audio as shared


HSK30_HSK79_SOURCE = shared.BASE_DIR / "testdata/hsk30/hsk79/xuat audio hsk79 02112016.xlsx"
HSK79_ASSET_ROOT = shared.HSK30_AUDIO_ROOT / "hsk79"
HSK79_VOICE_CONFIG = shared.BASE_DIR / "config/hsk79_audio.json"
HSK79_BATCH_APPROVED = True
HSK79_GROUPS = ("1–5", "6–10", "11–16", "17–22", "23–28", "29–34", "35–40")
HSK79_ASSETS = {
    name: HSK79_ASSET_ROOT / f"hsk79_{name}.m4a"
    for name in ("intro", "part1_q1_5", "part1_after_q1_5", "cue_q6_10",
                 "part1_after_q6_10", "part2_q11_16", "cue_q17_22",
                 "part3_q23_28", "cue_q29_34", "cue_q35_40", "outro")
}
HSK79_PART_LABELS = {
    "第一部分": 1,
    "第二部分": 2,
    "第三部分": 3,
}
HSK79_PROFILE = {
    "level": "HSK79", "default_rate": 1.00, "part_counts": {1: 10, 2: 12, 3: 18},
    "repetitions": 1, "work_pause_ms": {"1–5": 74539, "6–10": 74388},
    "answer_pause_ms": 14043 - 2 * shared.HSK3_EDGE_SAFETY_PAD_MS,
    "passage_to_number_ms": 2100 - 2 * shared.HSK3_EDGE_SAFETY_PAD_MS,
    "number_to_question_ms": 1067 - 2 * shared.HSK3_EDGE_SAFETY_PAD_MS,
    "speaker_gap_ms": 600 - 2 * shared.HSK3_EDGE_SAFETY_PAD_MS,
    "placeholder_gap_ms": 700 - 2 * shared.HSK3_EDGE_SAFETY_PAD_MS,
    "cue_to_passage_ms": 0, "passage_to_after_cue_ms": 0,
    "max_chunk_bytes": 4000, "sample_duration_ms": 1897587, "tts_gain_db": -3.0,
    "group_assets": {"1–5": "part1_q1_5", "6–10": "cue_q6_10", "11–16": "part2_q11_16",
                     "17–22": "cue_q17_22", "23–28": "part3_q23_28", "29–34": "cue_q29_34", "35–40": "cue_q35_40"},
    "after_assets": {"1–5": "part1_after_q1_5", "6–10": "part1_after_q6_10"},
}
NUMBER_PREFIX = re.compile(r"^\s*(\d{1,2})\s*[.．、]\s*")
ROLE_LINE = re.compile(r"^\s*([\u3400-\u9fff]{1,6})\s*[：:]\s*(.*)$")
BLANK = re.compile(r"_{2,}")
NARRATIVE_COLON = re.compile(r"(?:发现|指出|表示|提到|补充|解释|强调|认为|说|答道|问道|追问|询问|如下|结果|结论|显示|提醒|感叹|介绍|原因|情况|例如|包括|事项|问题|建议|说明|注意)$|(?:学生|导演|记者|有人|他|她)问$|(?:有人问我|计划提醒公众)$")


def _digest(path):
    return shared._hsk5_file_digest(path)


def hsk79_output_filename(exam):
    match = re.fullmatch(r"HSK79-(\d{2})", str(exam).strip().upper())
    if not match or not 1 <= int(match[1]) <= 17:
        raise ValueError(f"Invalid HSK79 exam: {exam}")
    return f"hsk79-{int(match[1]):03d}.m4a"


def parse_hsk79_part(value):
    label = re.sub(r"\s+", "", str(value or "")).casefold()
    if label in HSK79_PART_LABELS:
        return HSK79_PART_LABELS[label]
    match = re.fullmatch(r"phần([123])", label, re.IGNORECASE)
    return int(match[1]) if match else None


def load_hsk79_voice_profile():
    data = json.loads(HSK79_VOICE_CONFIG.read_text(encoding="utf-8"))
    for group in HSK79_GROUPS:
        entry = data["groups"].get(group, {})
        if entry.get("narration") not in {"NAM", "NỮ"}:
            raise ValueError(f"HSK79 voice mapping missing narration for {group}")
        if any(value not in {"NAM", "NỮ"} for value in entry.get("roles", {}).values()):
            raise ValueError(f"HSK79 invalid role voice mapping for {group}")
    for key in ("number", "question"):
        if data.get(key) not in {"NAM", "NỮ"}:
            raise ValueError(f"HSK79 missing {key} voice mapping")
    return data


def validate_hsk79_assets(asset_root=None):
    root = Path(asset_root) / "hsk79" if asset_root else HSK79_ASSET_ROOT
    files = {key: root / path.name for key, path in HSK79_ASSETS.items()}
    missing = [path.name for path in files.values() if not path.is_file()]
    mismatched = []
    manifest_path = root / "MANIFEST.json"
    if not manifest_path.is_file():
        missing.append("MANIFEST.json")
    else:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        expected = {entry["name"]: entry["sha256"] for entry in manifest["assets"]}
        for path in files.values():
            if path.is_file() and expected.get(path.name) != _digest(path):
                mismatched.append(path.name)
    return {"ok": not missing and not mismatched, "level": "HSK79", "root": str(root),
            "required": {key: str(path) for key, path in files.items()},
            "missing": missing, "hash_mismatches": mismatched}


def _strip_number(text, number):
    match = NUMBER_PREFIX.match(text)
    if match:
        if int(match[1]) != number:
            raise ValueError(f"Embedded number {match[1]} differs from column Câu {number}")
        return text[match.end():].strip(), True
    return text.strip(), False


def normalize_hsk79_question_layout(text):
    lines = [line.strip() for line in str(text).replace("\r", "").split("\n") if line.strip()]
    if len(lines) > 1:
        if not lines[0].endswith(("？", "?")):
            raise ValueError("Ambiguous multiline exam question: first line must end with a question mark")
        if any(not BLANK.search(line) for line in lines[1:]):
            raise ValueError("Ambiguous multiline exam question: continuation lines must contain answer blanks")
    return " ".join(lines)


def split_hsk79_owner(text, number, part):
    source = str(text).strip()
    separators = list(re.finditer(r"\n[ \t\r]*\n", source))
    if not separators:
        raise ValueError("Missing final passage/question block separator")
    boundary = separators[-1]
    passage, raw_question = source[:boundary.start()].strip(), source[boundary.end():].strip()
    if not passage or not raw_question:
        raise ValueError("Ambiguous final passage/question block")
    question, marked = _strip_number(raw_question, number)
    question = normalize_hsk79_question_layout(question)
    if not question or ROLE_LINE.match(question) or BLANK.search(passage):
        raise ValueError("Invalid first question or placeholder inside passage")
    if part != 1 and not question.endswith(("？", "?")) and not BLANK.search(question):
        raise ValueError("Final exam question must end with a question mark")
    return passage, question, marked


def hsk79_question_parts(text):
    """Writing lines are metadata; inline blanks become timed, unfilled slots."""
    question = normalize_hsk79_question_layout(text)
    writing_field = re.search(r"\s*(?:答\s*[：:]\s*)?_{2,}\s*$", question)
    removed_writing_field = False
    if writing_field:
        prefix = question[:writing_field.start()].rstrip()
        prefix = re.sub(r"[（(]简答[）)]\s*$", "", prefix).rstrip()
        if prefix.endswith(("？", "?")):
            question = prefix
            removed_writing_field = True
    if re.search(r"(?m)^\s*答\s*[：:]", question):
        raise ValueError("Unrecognized answer field after question")
    parts = []
    position = 0
    for match in BLANK.finditer(question):
        if question[position:match.start()].strip():
            parts.append({"type": "speech", "text": question[position:match.start()].strip()})
        parts.append({"type": "blank"})
        position = match.end()
    if question[position:].strip():
        parts.append({"type": "speech", "text": question[position:].strip()})
    if not parts or not any(part["type"] == "speech" for part in parts):
        raise ValueError("Empty question speech")
    return question, parts, removed_writing_field


def parse_hsk79_speakers(passage, group, voice_profile):
    config = voice_profile["groups"][group]
    roles = {"男": "NAM", "女": "NỮ", **config.get("roles", {})}
    segments = []
    buffer = []
    role = None

    def flush():
        content = "\n".join(buffer).strip()
        if content:
            if role is None and segments and segments[-1]["speaker_token"] == "NARRATION":
                segments[-1]["source_text"] += "\n\n" + content
                segments[-1]["text"] = segments[-1]["source_text"]
            else:
                segments.append({"source_text": content, "text": content,
                                 "speaker_token": role or "NARRATION",
                                 "speaker_label": roles[role] if role else config["narration"]})
        buffer.clear()

    for line in passage.splitlines():
        match = ROLE_LINE.match(line)
        if match and (match[1] in roles or not NARRATIVE_COLON.search(match[1])):
            token, text = match.groups()
            if token not in roles:
                raise ValueError(f"Unmapped speaker label '{token}' in group {group}")
            flush()
            role = token
            if not text.strip():
                raise ValueError(f"Empty speaker turn '{token}'")
            buffer.append(text.strip())
        elif not line.strip():
            flush()
            role = None
        else:
            buffer.append(line.strip())
    flush()
    if not segments:
        raise ValueError("Empty shared passage")
    return segments


def chunk_hsk79_speech(text, max_bytes=None):
    max_bytes = max_bytes or HSK79_PROFILE["max_chunk_bytes"]
    if not text.strip():
        raise ValueError("Empty TTS chunk")
    if len(text.encode("utf-8")) <= max_bytes:
        return [text]
    units = re.split(r"(?<=[。！？!?；;])", text)
    refined = []
    for unit in units:
        if len(unit.encode("utf-8")) > max_bytes:
            refined.extend(re.split(r"(?<=[，,、])", unit))
        else:
            refined.append(unit)
    chunks = []
    current = ""
    for unit in refined:
        if len(unit.encode("utf-8")) > max_bytes:
            raise ValueError("HSK79 sentence/clause exceeds safe TTS request size")
        if len((current + unit).encode("utf-8")) > max_bytes:
            chunks.append(current)
            current = ""
        current += unit
    if current:
        chunks.append(current)
    assert "".join(chunks) == text
    return chunks


def validate_hsk79_workbook(excel_path, sheet_name="Sheet1", voice_profile=None):
    voice_profile = copy.deepcopy(voice_profile or load_hsk79_voice_profile())
    rows = shared.read_xlsx_rows(excel_path, sheet_name)
    expected_headers = {"Mã đề": "exam", "Phần": "part", "Nhóm câu": "group", "Câu": "number"}
    headers = {shared._normalize_hsk3_header(value): key for key, value in rows[0].cells.items()}
    columns = {}
    for name, key in expected_headers.items():
        normalized = shared._normalize_hsk3_header(name)
        if normalized not in headers:
            raise ValueError(f"HSK79 missing column: {name}")
        columns[key] = headers[normalized]
    content_aliases = (
        "NỘI DUNG PHẦN NGHE VÀ CÂU HỎI/CÂU DẪN",
        "Nội dung phần nghe",
        "Nội dung nghe",
    )
    content_column = next((headers[shared._normalize_hsk3_header(name)]
                           for name in content_aliases
                           if shared._normalize_hsk3_header(name) in headers), None)
    if content_column is None:
        raise ValueError("HSK79 missing column: Nội dung phần nghe (or legacy listening-content header)")
    columns["content"] = content_column
    exams = {}
    errors = []
    stats = Counter()
    speaker_stats = Counter()
    group_passages = {}
    passage_copy_variants = []
    for row in rows[1:]:
        values = {key: str(row.cells.get(column, "")).strip() for key, column in columns.items()}
        exam = values["exam"]
        location = f"{exam} Câu {values['number']} source row {row.row_number}"
        try:
            if not all(values[key] for key in ("exam", "part", "group", "number")):
                raise ValueError("Missing required metadata cell")
            hsk79_output_filename(exam)
            number = shared._hsk3_number_value(values["number"])
            part = parse_hsk79_part(values["part"])
            if part is None or not 1 <= number <= 40:
                raise ValueError(f"Invalid part label or Chinese question number: {values['part']!r}")
            expected_part = 1 if number <= 10 else 2 if number <= 22 else 3
            group = re.sub(r"\s+", "", values["group"]).replace("-", "–").replace("—", "–")
            if group not in HSK79_GROUPS or number not in shared._hsk4_group_range(group) or part != expected_part:
                raise ValueError("Part/group does not match HSK79 question layout")
            owner = number == shared._hsk4_group_range(group)[0]
            content = values["content"]
            if part == 1 and owner:
                if not content:
                    raise ValueError("Missing independent Part1 passage")
                if re.search(r"\n[ \t\r]*\n", content):
                    passage, question, marked = split_hsk79_owner(content, number, part)
                else:
                    passage, question, marked = content, "", False
                group_passages[(exam, group)] = {"text": passage, "source_row": row.row_number}
                segments = parse_hsk79_speakers(passage, group, voice_profile)
                for segment in segments:
                    chunk_hsk79_speech(segment["text"])
                    speaker_stats[segment["speaker_token"]] += 1
                stats["group_starts"] += 1
                stats["internal_blank_line_owners"] += bool(re.search(r"\n[ \t\r]*\n", passage))
            elif part == 1:
                passage, segments = "", []
                owner_passage = group_passages.get((exam, group))
                if content and owner_passage and content.strip() == owner_passage["text"]:
                    question, marked = "", False
                    stats["repeated_part1_passages"] += 1
                else:
                    question, marked = _strip_number(content, number) if content else ("", False)
            elif owner:
                if not content:
                    raise ValueError("Missing shared passage and first exam question")
                passage, question, marked = split_hsk79_owner(content, number, part)
                group_passages[(exam, group)] = {"text": passage, "source_row": row.row_number}
                segments = parse_hsk79_speakers(passage, group, voice_profile)
                for segment in segments:
                    chunk_hsk79_speech(segment["text"])
                    speaker_stats[segment["speaker_token"]] += 1
                stats["group_starts"] += 1
                stats["internal_blank_line_owners"] += bool(re.search(r"\n[ \t\r]*\n", passage))
            else:
                passage, segments = "", []
                if not content:
                    raise ValueError("Missing exam question content")
                if re.search(r"\n[ \t\r]*\n", content):
                    copied_passage, question, marked = split_hsk79_owner(content, number, part)
                    owner_passage = group_passages.get((exam, group))
                    if owner_passage is None:
                        raise ValueError("Repeated passage appears before its shared-group owner")
                    stats["repeated_passage_rows"] += 1
                    if copied_passage != owner_passage["text"]:
                        stats["repeated_passage_variants"] += 1
                        passage_copy_variants.append({
                            "exam": exam,
                            "group": group,
                            "question_number": number,
                            "source_row": row.row_number,
                            "owner_source_row": owner_passage["source_row"],
                            "owner_passage_chars": len(owner_passage["text"]),
                            "copy_passage_chars": len(copied_passage),
                        })
                else:
                    question, marked = _strip_number(content, number)
            cleaned, question_parts, writing_field = hsk79_question_parts(question) if question else ("", [], False)
            if part == 1:
                if "\n" in cleaned or BLANK.search(cleaned):
                    raise ValueError("Invalid Part1 statement metadata")
            elif not BLANK.search(cleaned) and not cleaned.endswith(("？", "?")):
                raise ValueError("Missing final exam question")
            if "\n" in cleaned:
                raise ValueError("Unseparated extra content in question row")
            if any(ROLE_LINE.match(line) for line in cleaned.splitlines()):
                raise ValueError("Later row introduces stimulus/speaker content")
            stats["missing_number_prefix"] += bool(question) and not marked
            stats["writing_fields_removed"] += writing_field
            stats["inline_blanks"] += sum(item["type"] == "blank" for item in question_parts)
            exams.setdefault(exam, []).append({
                "question_number": number, "part": part, "group": group, "source_row": row.row_number,
                "source_number": values["number"], "question_number_spoken": shared._spoken_question_number(values["number"]),
                "source_text": values["content"], "stimulus_source": passage, "stimulus_segments": segments,
                "question_source": question, "question_spoken": cleaned, "question_parts": question_parts,
                "spoken": part != 1, "owns_passage": owner,
            })
        except (ValueError, KeyError, TypeError) as exc:
            errors.append(f"{location}: {exc}")
    expected_exams = [f"HSK79-{index:02d}" for index in range(1, 18)]
    summaries = {}
    if list(exams) != expected_exams or len(rows) != 681:
        errors.append("HSK79 requires 17 ordered exams and 680 data rows")
    for exam in expected_exams:
        items = exams.get(exam, [])
        counts = dict(Counter(item["part"] for item in items))
        if [item["question_number"] for item in items] != list(range(1, 41)) or counts != HSK79_PROFILE["part_counts"]:
            errors.append(f"{exam}: requires unique Q1–40 and part counts 10/12/18")
        groups = {group: [item["question_number"] for item in items if item["group"] == group] for group in HSK79_GROUPS}
        if any(numbers != shared._hsk4_group_range(group) for group, numbers in groups.items()):
            errors.append(f"{exam}: invalid group size/order")
        summaries[exam] = {"questions": len(items), "part_counts": counts, "groups": groups}
    return {"status": "FAIL" if errors else "PASS", "invalid_rows": errors, "exam_count": len(exams),
            "row_count": len(rows) - 1, "exams": summaries, "parsed_items": exams,
            "stats": dict(stats), "speaker_labels": dict(speaker_stats), "voice_profile": voice_profile,
            "source_columns": columns, "passage_copy_variants": passage_copy_variants}


def parse_hsk79_phase1(excel_path=HSK30_HSK79_SOURCE, sheet_name="Sheet1", exam_code="HSK79-01",
                       asset_root=None, lang_state=None):
    structure = validate_hsk79_workbook(excel_path, sheet_name)
    if structure["status"] != "PASS":
        raise ValueError("HSK79 validation FAIL: " + "; ".join(structure["invalid_rows"]))
    if exam_code not in structure["parsed_items"]:
        raise ValueError(f"Missing HSK79 exam: {exam_code}")
    rate = shared._parse_speech_rate((lang_state or {}).get("speech_rate", 1.00))
    items = structure["parsed_items"][exam_code]
    return {"level": "HSK79", "workbook": str(Path(excel_path).resolve()), "sheet": sheet_name,
            "detected_exam": exam_code, "question_count": len(items), "part_counts": {1: 10, 2: 12, 3: 18},
            "shared_group_count": 7, "independent_count": 0, "preview_items": items,
            "lang_state": {"speech_rate": rate, "hsk_speed_profile": "HSK79", "speech_rate_label": f"HSK79 {rate:.2f}x"},
            "structure": structure, "asset_status": validate_hsk79_assets(asset_root), "tts_requests_made": 0,
            "status": "READY_FOR_REVIEW", "output_filename": hsk79_output_filename(exam_code)}


def format_hsk79_preview_report(report):
    structure = report["structure"]
    lines = [f"{report['detected_exam']} / HSK79", f"Workbook: {report['workbook']}",
             "40 questions / Parts 10-12-18 / 7 shared groups",
             "Part1: 2 passages, 2 work pauses; Q1–10 statements/numbers NOT spoken",
             "Part2/3: 30 spoken questions/numbers, 30 answer pauses; passages play once",
             f"Validation: {structure['status']} / {structure['exam_count']} exams / {structure['row_count']} rows",
             f"Parser statistics: {structure['stats']}", f"Assets: {report['asset_status']}",
             f"Voice mapping: {structure['voice_profile']}",
             "Batch enabled for 17 validated exams. Dry-run TTS requests: 0", ""]
    for item in report["preview_items"]:
        lines.append(f"Q{item['question_number']} / group {item['group']} / source row {item['source_row']}")
        for segment in item["stimulus_segments"]:
            lines.append(f"  {segment['speaker_token']} / {segment['speaker_label']}: {segment['source_text']}")
        lines.append(("Question: " if item["spoken"] else "Statement metadata (not spoken): ") + item["question_spoken"])
    return "\n".join(lines) + "\n"


def audit_hsk79_timeline(report):
    tokens = report["timeline"]
    expected = ["hsk79_intro"]
    for group in HSK79_GROUPS:
        expected += ["hsk79_" + HSK79_PROFILE["group_assets"][group], "passage_" + group]
        if group in HSK79_PROFILE["after_assets"]:
            expected += ["hsk79_" + HSK79_PROFILE["after_assets"][group], "work_pause_" + group]
        else:
            expected.append("passage_to_number_gap")
            for number in shared._hsk4_group_range(group):
                expected += [f"number{number}", "number_to_question_gap", f"question{number}", "answer_pause"]
    expected.append("hsk79_outro")
    checks = {
        "exact_order": tokens == expected,
        "eleven_assets_once": all(tokens.count("hsk79_" + key) == 1 for key in HSK79_ASSETS),
        "seven_passages_once": all(tokens.count("passage_" + group) == 1 for group in HSK79_GROUPS),
        "no_part1_questions_or_numbers": all(f"question{n}" not in tokens and f"number{n}" not in tokens for n in range(1, 11)),
        "thirty_questions_once": all(tokens.count(f"question{n}") == 1 and tokens.count(f"number{n}") == 1 for n in range(11, 41)),
        "thirty_answer_pauses": tokens.count("answer_pause") == 30,
        "two_work_pauses": sum(t.startswith("work_pause_") for t in tokens) == 2,
        "no_common_music_outro": "music" not in tokens and "outro" not in tokens,
        "direct_cues": [entry["group"] for entry in report["fixed_asset_boundaries"]] == list(HSK79_GROUPS) and all(
            entry["asset_end_ms"] == entry["passage_start_ms"] for entry in report["fixed_asset_boundaries"]),
        "no_repeats": not any("repeat" in token for token in tokens),
    }
    if not all(checks.values()):
        raise ValueError(f"HSK79 timeline FAIL: {checks}")
    return checks


def build_hsk79_exam(report, output_file, *, lang_state=None, tts_provider=None, progress_callback=None):
    exam = report["detected_exam"]
    hsk79_output_filename(exam)
    state = dict(lang_state or {})
    rate = shared._parse_speech_rate(state.get("speech_rate", 1.0))
    if (state.get("engine") != "Google Cloud TTS"
            or state.get("zh_male_voice") != "cmn-CN-Standard-C"
            or state.get("zh_female_voice") != "cmn-CN-Standard-A"):
        raise ValueError("HSK79 golden needs Google Cloud TTS with male Standard-C and female Standard-A selected in Lang")
    source_digest = _digest(Path(report["workbook"]))
    fresh = parse_hsk79_phase1(report["workbook"], sheet_name=report["sheet"], exam_code=exam, lang_state=state)
    if _digest(Path(report["workbook"])) != source_digest:
        raise ValueError("HSK79 workbook changed during validation")
    status = validate_hsk79_assets()
    if not status["ok"]:
        raise FileNotFoundError(f"HSK79 fixed assets invalid: {status['missing']} {status['hash_mismatches']}")
    voices = fresh["structure"]["voice_profile"]
    asset_digests = {key: _digest(path) for key, path in HSK79_ASSETS.items()}
    cache_dir = shared._make_tts_cache_dir()
    counts = {"unique_tts_requests": 0, "reused_segments": 0, "generated_segments": 0,
              "generated_speech_ms": 0, "speech_metrics": []}
    timeline, events, pieces, boundaries, passages = [], [], [], [], []
    cursor = 0
    profile = copy.deepcopy(HSK79_PROFILE)

    def append(audio, kind, **metadata):
        nonlocal cursor
        clip = audio.set_frame_rate(48000).set_channels(1).set_sample_width(2)
        start = cursor
        cursor += len(clip)
        pieces.append(clip)
        events.append({"type": kind, "start_ms": start, "end_ms": cursor, **metadata})

    def pause(duration, kind, **metadata):
        append(shared._internal_pause_segment(duration), kind, **metadata)

    def fixed(key):
        timeline.append("hsk79_" + key)
        append(shared._load_audio_segment(HSK79_ASSETS[key]), "fixed_asset", asset=key)

    def speak(text, speaker, kind, **metadata):
        for index, chunk in enumerate(chunk_hsk79_speech(text)):
            metrics = {}
            voice = shared._speaker_voice_name(state, speaker)
            clip, reused = shared._synthesize_tts_segment(
                chunk, engine=state.get("engine", "Google Cloud TTS"), speed=f"{rate:.2f}x",
                voice_name=voice, speaker_label=speaker, cache_dir=cache_dir,
                google_api_key=state.get("google_api_key", ""),
                aws_credentials=shared._aws_credentials_from_lang_state(state), tts_provider=tts_provider,
                audio_postprocess_version=shared.HSK3_TTS_POSTPROCESS_VERSION, audio_metrics=metrics,
                retry_google_voice_failure=True, tts_timeout_s=90,
                close_google_client_before_audio_decode=True,
            )
            shared._hsk4_count_tts(counts, reused, clip, metrics)
            # Leave headroom for AAC overshoot without changing canonical cached speech.
            clip = clip.apply_gain(profile["tts_gain_db"])
            append(clip, kind, speaker=speaker, voice=voice, text=chunk, chunk=index, cache_reused=reused, **metadata)

    fixed("intro")
    items = fresh["preview_items"]
    for group in HSK79_GROUPS:
        if progress_callback:
            progress_callback(f"{exam}: group {group}")
        group_items = [item for item in items if item["group"] == group]
        owner = group_items[0]
        cue = profile["group_assets"][group]
        cue_start = cursor
        fixed(cue)
        cue_end = cursor
        passage_start = cursor
        timeline.append("passage_" + group)
        for turn, segment in enumerate(owner["stimulus_segments"]):
            if (turn and profile["speaker_gap_ms"]
                    and owner["stimulus_segments"][turn - 1]["speaker_token"] != segment["speaker_token"]):
                pause(profile["speaker_gap_ms"], "speaker_gap", group=group)
            speak(segment["text"], segment["speaker_label"], "passage_speech", group=group,
                  role=segment["speaker_token"], turn=turn, source_row=owner["source_row"])
        passages.append({"group": group, "start_ms": passage_start, "end_ms": cursor,
                         "turns": len(owner["stimulus_segments"]),
                         "speakers": dict(Counter(s["speaker_label"] for s in owner["stimulus_segments"]))})
        boundaries.append({"group": group, "asset": cue, "asset_start_ms": cue_start,
                           "asset_end_ms": cue_end, "passage_start_ms": passage_start, "manual_gap_ms": 0})
        if owner["part"] == 1:
            fixed(profile["after_assets"][group])
            timeline.append("work_pause_" + group)
            pause(profile["work_pause_ms"][group], "work_pause", group=group)
            continue
        timeline.append("passage_to_number_gap")
        pause(profile["passage_to_number_ms"], "passage_to_number_gap", group=group)
        for item in group_items:
            number = item["question_number"]
            timeline += [f"number{number}", "number_to_question_gap", f"question{number}", "answer_pause"]
            speak(item["question_number_spoken"], voices["number"], "number", question=number, group=group)
            pause(profile["number_to_question_ms"], "number_to_question_gap", question=number)
            for part in item["question_parts"]:
                if part["type"] == "blank":
                    pause(profile["placeholder_gap_ms"], "placeholder_gap", question=number)
                else:
                    speak(part["text"], voices["question"], "question_speech", question=number, group=group)
            pause(profile["answer_pause_ms"], "answer_pause", question=number, group=group)
    fixed("outro")
    assert _digest(Path(report["workbook"])) == source_digest, "HSK79 workbook changed during build"
    assert asset_digests == {key: _digest(path) for key, path in HSK79_ASSETS.items()}, "HSK79 assets changed during build"
    result = {"exam": exam, "level": "HSK79", "output_file": str(Path(output_file).resolve()),
              "source_path": report["workbook"], "source_sha256": source_digest, "asset_sha256": asset_digests,
              "question_count": 40, "part_counts": {1: 10, 2: 12, 3: 18}, "shared_groups": 7,
              "timeline": timeline, "events": events, "passages": passages, "fixed_asset_boundaries": boundaries,
              "profile": profile, "voice_profile": voices, "speech_rate": rate,
              "tts_engine": state.get("engine", "Google Cloud TTS"),
              "tts_voices": {"male": state.get("zh_male_voice"), "female": state.get("zh_female_voice")},
              "edge_trim_version": shared.HSK3_TTS_POSTPROCESS_VERSION,
              "edge_trim_threshold_db": shared.HSK3_EDGE_SILENCE_THRESHOLD_DB,
              "edge_trim_safety_pad_ms": shared.HSK3_EDGE_SAFETY_PAD_MS,
              "work_pauses_ms": sum(profile["work_pause_ms"].values()),
              "answer_pauses_ms": 30 * profile["answer_pause_ms"], **counts}
    result["timeline_checks"] = audit_hsk79_timeline(result)
    # Canonical mono clips avoid the memory cost of repeated full-exam stereo concatenation.
    final = AudioSegment(data=b"".join(clip.raw_data for clip in pieces), sample_width=2, frame_rate=48000, channels=1)
    if progress_callback:
        progress_callback(f"{exam}: encoding M4A")
    path = Path(output_file)
    actual = shared._export_audio_atomically(final, path, {"format": "ipod", "codec": "aac", "bitrate": "32k",
                                                          "parameters": ["-ac", "1", "-ar", "48000", "-profile:a", "aac_low"]})
    result.update(actual_duration_ms=actual, file_size_bytes=path.stat().st_size,
                  audio_sha256=_digest(path), status="INFO",
                  active_speech_ms=sum(m.get("active_speech_ms", 0) for m in counts["speech_metrics"]),
                  edge_silence_trimmed_ms=sum(m.get("edge_silence_trimmed_ms", 0) for m in counts["speech_metrics"]))
    shared._write_hsk5_json(path.with_suffix(".report.json"), result)
    return result


def build_hsk79_golden(report, output_file, *, lang_state=None, tts_provider=None, progress_callback=None):
    if report["detected_exam"] != "HSK79-01":
        raise ValueError("HSK79 golden-only phase: only HSK79-01 is approved for build")
    return build_hsk79_exam(report, output_file, lang_state=lang_state, tts_provider=tts_provider,
                            progress_callback=progress_callback)


def build_hsk79_batch(excel_path, output_dir, *, lang_state=None, tts_provider=None, progress_callback=None):
    if not HSK79_BATCH_APPROVED:
        raise ValueError("HSK79 batch locked pending golden approval")
    state = dict(lang_state or {})
    state.setdefault("speech_rate", 1.0)
    state["hsk_speed_profile"] = "HSK79"
    if (state.get("engine") != "Google Cloud TTS"
            or state.get("zh_male_voice") != "cmn-CN-Standard-C"
            or state.get("zh_female_voice") != "cmn-CN-Standard-A"):
        raise ValueError("HSK79 batch needs Google Cloud TTS with male Standard-C and female Standard-A selected in Lang")

    source = Path(excel_path).resolve()
    source_digest = _digest(source)
    structure = validate_hsk79_workbook(source)
    if structure["status"] != "PASS":
        raise ValueError("HSK79 batch validation FAIL: " + "; ".join(structure["invalid_rows"]))
    assets = validate_hsk79_assets()
    if not assets["ok"]:
        raise FileNotFoundError(f"HSK79 fixed assets invalid: {assets['missing']} {assets['hash_mismatches']}")

    destination = Path(output_dir)
    destination.mkdir(parents=True, exist_ok=True)
    results = []
    exam_codes = list(structure["exams"])
    for index, exam in enumerate(exam_codes, start=1):
        output = destination / hsk79_output_filename(exam)
        sidecar = output.with_suffix(".report.json")
        result = {"exam": exam, "level": "HSK79", "output_file": str(output.resolve()),
                  "question_count": 40, "part_counts": {1: 10, 2: 12, 3: 18}, "shared_groups": 7,
                  "validation": "PASS", "unique_tts_requests": 0, "reused_segments": 0, "status": "FAIL"}
        if progress_callback:
            progress_callback(f"[{index}/{len(exam_codes)}] {exam}: starting")
        try:
            if _digest(source) != source_digest:
                raise ValueError("Excel changed during batch; stopped using this source snapshot")
            if output.is_file() and sidecar.is_file():
                previous = json.loads(sidecar.read_text(encoding="utf-8"))
                previous_audio_hash = _digest(output)
                expected_assets = {key: _digest(path) for key, path in HSK79_ASSETS.items()}
                if (previous.get("source_sha256") == source_digest
                        and previous.get("audio_sha256") == previous_audio_hash
                        and previous.get("asset_sha256") == expected_assets
                        and previous.get("speech_rate") == shared._parse_speech_rate(state["speech_rate"])
                        and previous.get("tts_engine", "Google Cloud TTS") == state.get("engine")
                        and previous.get("tts_voices", {
                            "male": state.get("zh_male_voice"), "female": state.get("zh_female_voice")}
                        ) == {
                            "male": state.get("zh_male_voice"), "female": state.get("zh_female_voice")}
                        and bool(previous.get("timeline_checks"))
                        and all(previous.get("timeline_checks", {}).values())
                        and abs(shared._probe_audio_duration_ms(output) - previous.get("actual_duration_ms", 0)) <= 50):
                    result.update(previous, output_file=str(output.resolve()), status="SKIP_VERIFIED",
                                  unique_tts_requests=0, reused_segments=0)
                    result.setdefault("tts_engine", state.get("engine"))
                    result.setdefault("tts_voices", {
                        "male": state.get("zh_male_voice"), "female": state.get("zh_female_voice")})
                    shared._write_hsk5_json(sidecar, result)
                    results.append(result)
                    shared._write_hsk5_json(destination / "hsk79-batch.report.json", results)
                    if progress_callback:
                        progress_callback(f"[{index}/{len(exam_codes)}] {exam}: SKIP_VERIFIED / source and audio hash match")
                    continue
            preview = parse_hsk79_phase1(source, exam_code=exam, lang_state=state)
            result.update(unique_tts_requests=None, reused_segments=None)
            result.update(build_hsk79_exam(
                preview, output, lang_state=state, tts_provider=tts_provider,
                progress_callback=progress_callback,
            ))
            result.update(validation="PASS", source_path=str(source), source_sha256=source_digest,
                          audio_sha256=_digest(output))
            result["status"] = "INFO"
            shared._write_hsk5_json(output.with_suffix(".report.json"), result)
        except Exception as exc:
            result.update(status="FAIL", validation="FAIL", error=str(exc))
        results.append(result)
        shared._write_hsk5_json(destination / "hsk79-batch.report.json", results)
        if progress_callback:
            progress_callback(f"[{index}/{len(exam_codes)}] {exam}: {result['status']} / TTS {result['unique_tts_requests']} / cache {result['reused_segments']}")
    return results


def format_hsk79_build_report(report):
    lines = [f"Exam: {report['exam']}", f"Output: {report['output_file']}",
             f"Duration: {report['actual_duration_ms'] / 1000:.3f} s / {report['file_size_bytes']} bytes",
             f"Speed: {report['speech_rate']:.2f}x / AAC-LC 48 kHz mono 32 kbps",
             "7 passages once / Part1 statements NOT spoken / 30 questions once",
             f"Work pauses: {report['profile']['work_pause_ms']}",
             f"30 answer pauses: {report['profile']['answer_pause_ms']} ms configured each",
             f"TTS new: {report['unique_tts_requests']} / Cache reuse: {report['reused_segments']}",
             f"Active speech: {report['active_speech_ms']} ms / Edge trimmed: {report['edge_silence_trimmed_ms']} ms",
             f"Timeline: {report['timeline_checks']}", "Duration status: INFO; no auto-adjustment", ""]
    for boundary in report["fixed_asset_boundaries"]:
        lines.append(f"{boundary['group']}: {boundary['asset']} @ {boundary['asset_start_ms']} ms -> passage {boundary['passage_start_ms']} ms; manual gap 0 ms")
    return "\n".join(lines) + "\n"


def format_hsk79_batch_report(results):
    lines = ["| Exam | Duration | File size | Questions | Part1 | Part2 | Part3 | Shared groups | TTS new | Cache reuse | Validation | Status |",
             "| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | --- | --- |"]
    for result in results:
        seconds = result.get("actual_duration_ms", 0) / 1000
        duration = f"{int(seconds // 60):02d}:{seconds % 60:05.2f}" if seconds else "-"
        size = f"{result.get('file_size_bytes', 0) / 1_000_000:.2f} MB"
        new_tts = result.get("unique_tts_requests")
        reused = result.get("reused_segments")
        lines.append(f"| {result['exam']} | {duration} | {size} | {result.get('question_count', 40)} | 10 | 12 | 18 | 7 | "
                     f"{new_tts if new_tts is not None else 'unknown'} | {reused if reused is not None else 'unknown'} | "
                     f"{result.get('validation', 'FAIL')} | {result.get('status', 'FAIL')} |")
    for result in results:
        if result.get("error"):
            lines.append(f"\n{result['exam']}: {result['error']}")
    return "\n".join(lines)
