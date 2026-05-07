import argparse
import json
import os
import re
import shutil
import subprocess
import tempfile
import uuid

import pandas as pd
from gtts import gTTS
from pydub import AudioSegment
from pypinyin import Style, lazy_pinyin


PAUSE_MS = 500
TARGET_SAMPLE_RATE = 22050
TARGET_CHANNELS = 1
TARGET_BITRATE = "32k"


def _log(message):
    print(message, flush=True)


def _resolve_node_executable():
    system_node = shutil.which("node")
    if system_node:
        return system_node

    project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    local_node = os.path.join(project_root, ".tools", "node", "bin", "node")
    if os.path.exists(local_node):
        return local_node

    return None


def _clean_cell(value):
    if value is None:
        return ""
    if isinstance(value, float) and pd.isna(value):
        return ""
    return str(value).strip()


def _normalize_columns(df):
    mapping = {}
    for col in df.columns:
        normalized = str(col).strip().lower()
        mapping[normalized] = col

    required_aliases = {
        "word": ["中文", "từ vựng"],
        "meaning": ["nghĩa tiếng việt", "nghĩa"],
        "example": ["ví dụ (中文)", "ví dụ"],
        "example_vi": ["nghĩa ví dụ"],
    }

    resolved = {}
    missing = []
    for key, aliases in required_aliases.items():
        found = None
        for alias in aliases:
            normalized_alias = alias.strip().lower()
            if normalized_alias in mapping:
                found = mapping[normalized_alias]
                break

        if found is None:
            missing.append(aliases[0])
        else:
            resolved[key] = found

    if missing:
        raise ValueError(
            "Missing required columns in sheet: " + ", ".join(missing)
        )

    return resolved


def _to_pinyin_slug(text):
    base = "".join(lazy_pinyin(text, style=Style.NORMAL, strict=False))
    base = base.lower().replace(" ", "")
    base = re.sub(r"[^a-z0-9]", "", base)
    return base or "na"


def _tts_segment(text, lang, temp_dir):
    temp_mp3 = os.path.join(temp_dir, f"tts_{uuid.uuid4().hex}.mp3")
    gTTS(text=text, lang=lang).save(temp_mp3)
    return AudioSegment.from_mp3(temp_mp3), temp_mp3


def _build_word_audio(word, meaning):
    temp_dir = tempfile.gettempdir()
    created_files = []
    try:
        zh_seg, zh_path = _tts_segment(word, "zh-CN", temp_dir)
        created_files.append(zh_path)
        vi_seg, vi_path = _tts_segment(meaning, "vi", temp_dir)
        created_files.append(vi_path)

        full = zh_seg + AudioSegment.silent(duration=PAUSE_MS) + vi_seg
        return full
    finally:
        for file_path in created_files:
            try:
                if os.path.exists(file_path):
                    os.remove(file_path)
            except OSError:
                pass


def _export_m4a(audio, file_path):
    normalized = audio.set_channels(TARGET_CHANNELS).set_frame_rate(TARGET_SAMPLE_RATE)
    normalized.export(
        file_path,
        format="ipod",
        codec="aac",
        bitrate=TARGET_BITRATE,
        parameters=["-ac", str(TARGET_CHANNELS), "-ar", str(TARGET_SAMPLE_RATE)],
    )


def run_vocab_pipeline(file_path, sheet_name, skip_validate=False):
    overwrite_local_audio = str(os.environ.get("OVERWRITE_LOCAL_AUDIO", "false")).lower() == "true"
    _log(f"[Pipeline] Start: excel={file_path}")
    _log(f"[Pipeline] Sheet: {sheet_name}")
    df = pd.read_excel(file_path, sheet_name=sheet_name)
    col = _normalize_columns(df)

    total_rows = len(df)
    _log(f"[Pipeline] Rows loaded: {total_rows}")

    output_dir = os.path.join("output", sheet_name)
    os.makedirs(output_dir, exist_ok=True)

    audio_dir = os.path.join(output_dir, "audio")
    os.makedirs(audio_dir, exist_ok=True)

    _log(f"[Pipeline] Output dir: {output_dir}")
    _log(f"[Pipeline] Audio dir: {audio_dir}")
    _log(f"[Pipeline] Overwrite local audio: {overwrite_local_audio}")

    valid_rows = []
    skipped_rows = 0

    for _, row in df.iterrows():
        word = _clean_cell(row[col["word"]])
        meaning = _clean_cell(row[col["meaning"]])
        example = _clean_cell(row[col["example"]])
        example_vi = _clean_cell(row[col["example_vi"]])

        if not word or not meaning:
            skipped_rows += 1
            continue

        valid_rows.append(
            {
                "word": word,
                "meaning": meaning,
                "example": example,
                "example_vi": example_vi,
            }
        )

    total_valid = len(valid_rows)
    _log(f"[Pipeline] Valid rows: {total_valid}")
    _log(f"[Pipeline] Skipped empty rows: {skipped_rows}")

    output_items = []
    generated_count = 0
    skipped_existing_count = 0

    for row_index, item in enumerate(valid_rows, start=1):
        word = item["word"]
        meaning = item["meaning"]
        example = item["example"]
        example_vi = item["example_vi"]

        pinyin_slug = _to_pinyin_slug(word)
        file_name = f"{sheet_name}_{row_index:03d}_{pinyin_slug}.m4a"
        file_path_out = os.path.join(audio_dir, file_name)

        if os.path.isfile(file_path_out) and os.path.getsize(file_path_out) > 0 and not overwrite_local_audio:
            skipped_existing_count += 1
            _log(f"[Pipeline] Skip existing M4A {row_index}/{total_valid}: {file_name}")
        else:
            if overwrite_local_audio and os.path.isfile(file_path_out):
                _log(f"[Pipeline] Overwrite existing M4A {row_index}/{total_valid}: {file_name}")
            _log(f"[Pipeline] Generate {row_index}/{total_valid}: word={word} -> {file_name}")
            audio = _build_word_audio(word, meaning)
            _export_m4a(audio, file_path_out)
            generated_count += 1

        _log(f"[Pipeline] Progress: processed {row_index}/{total_valid} items")

        output_items.append(
            {
                "word": word,
                "meaning": meaning,
                "audio": os.path.join("audio", file_name).replace("\\", "/"),
                "example": example,
                "example_vi": example_vi,
            }
        )

    json_path = os.path.join(output_dir, "output_vocab.json")
    _log(f"[Pipeline] Writing JSON: {json_path}")
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(output_items, f, ensure_ascii=False, indent=2)

    _log(f"[Pipeline] JSON written: {len(output_items)} items")
    _log(f"[Pipeline] Generated new M4A: {generated_count}")
    _log(f"[Pipeline] Skipped existing M4A: {skipped_existing_count}")
    _log(f"[Pipeline] Skipped rows: {skipped_rows}")

    metadata_path = os.path.join(output_dir, "output_vocab_metadata.json")
    metadata = {
        "sheet_name": sheet_name,
        "excel_file": os.path.abspath(file_path),
        "expected_count": len(output_items),
        "generated_count": generated_count,
        "skipped_existing_count": skipped_existing_count,
        "skipped_empty_count": skipped_rows,
        "total_rows": total_rows,
        "overwrite_local_audio": overwrite_local_audio,
    }
    _log(f"[Pipeline] Writing metadata: {metadata_path}")
    with open(metadata_path, "w", encoding="utf-8") as f:
        json.dump(metadata, f, ensure_ascii=False, indent=2)

    if skip_validate:
        _log("[Pipeline] Validation skipped by flag")
        print("STATUS: SKIPPED")
        print("Validation skipped by --skip-validate")
    else:
        validator_script = os.path.join(
            os.path.dirname(__file__), "validate_vocab_output.js"
        )
        node_executable = _resolve_node_executable()
        if not node_executable:
            raise RuntimeError(
                "Node.js not found. Install Node.js or place it at .tools/node/bin/node."
            )

        _log("[Pipeline] Running validator...")
        validate_cmd = [
            node_executable,
            validator_script,
            "--excel",
            file_path,
            "--sheet",
            sheet_name,
            "--output",
            output_dir,
        ]
        validate_result = subprocess.run(validate_cmd, check=False)

        if validate_result.returncode != 0:
            _log("[Pipeline] Validator failed")
            raise RuntimeError("Validation failed. See STATUS: FAIL above.")

        _log("[Pipeline] Validator passed")

    _log("[Pipeline] Finished successfully")
    print(f"Done: {len(output_items)} items")
    print(f"Audio dir: {audio_dir}")
    print(f"JSON: {json_path}")


def parse_args():
    parser = argparse.ArgumentParser(
        description="Export HSK vocab sheet to M4A files + output_vocab.json"
    )
    parser.add_argument("excel_file", help="Path to Excel file")
    parser.add_argument("--sheet", required=True, help="Sheet name, e.g. hsk1_20")
    parser.add_argument(
        "--skip-validate",
        action="store_true",
        help="Skip post-export validation step",
    )
    return parser.parse_args()


if __name__ == "__main__":
    args = parse_args()
    run_vocab_pipeline(args.excel_file, args.sheet, skip_validate=args.skip_validate)
