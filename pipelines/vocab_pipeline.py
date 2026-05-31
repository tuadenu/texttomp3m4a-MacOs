import argparse
import json
import os
import re
import shutil
import subprocess
import tempfile
import uuid
import time
import hashlib
import random
import threading

import pandas as pd
from gtts import gTTS
from gtts.tts import gTTSError
import boto3
from pydub import AudioSegment
from pypinyin import Style, lazy_pinyin
try:
    from google.cloud import texttospeech
except Exception:
    texttospeech = None

try:
    from config.settings import AWS_REGION as CONFIG_AWS_REGION
except Exception:
    CONFIG_AWS_REGION = "ap-southeast-1"


PAUSE_MS = 500
TARGET_SAMPLE_RATE = 22050
TARGET_CHANNELS = 1
TARGET_BITRATE = "32k"

# gTTS local cache and rate limiting
_TTS_CACHE_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".tts_cache")
os.makedirs(_TTS_CACHE_DIR, exist_ok=True)
_gtts_lock = threading.Lock()
_last_gtts_time = 0.0


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


def _normalize_lang_code(lang):
    lang_clean = (lang or "vi").lower().strip()
    if lang_clean.startswith("vi"):
        return "vi"
    if lang_clean.startswith(("zh", "zh-cn", "zh_tw", "zh-hk")):
        return "zh"
    if lang_clean.startswith("ja"):
        return "ja"
    if lang_clean.startswith("en"):
        return "en"
    return "vi"


def _polly_voice_id(lang_code):
    if lang_code == "ja":
        return "Mizuki"
    if lang_code == "zh":
        return "Zhiyu"
    if lang_code == "en":
        return "Joanna"
    return None


def _get_polly_region():
    return (
        os.environ.get("AWS_REGION")
        or os.environ.get("AWS_DEFAULT_REGION")
        or CONFIG_AWS_REGION
        or "ap-southeast-1"
    )


def _get_google_profile(lang):
    lang_code = _normalize_lang_code(lang)
    raw = os.environ.get("GOOGLE_TTS_PROFILES_JSON", "")
    if not raw:
        return {"gender": "Mặc định", "voice_name": ""}

    try:
        data = json.loads(raw)
        profile = data.get(lang_code, {}) or {}
        return {
            "gender": profile.get("gender", "Mặc định") or "Mặc định",
            "voice_name": profile.get("voice_name", "") or "",
        }
    except Exception:
        return {"gender": "Mặc định", "voice_name": ""}


def _tts_segment_gtts(text, lang, temp_dir):
    temp_mp3 = os.path.join(temp_dir, f"tts_{uuid.uuid4().hex}.mp3")
    max_retries = int(os.environ.get("TTS_MAX_RETRIES", "5"))
    base_delay = float(os.environ.get("TTS_BASE_DELAY_SECONDS", "5"))
    min_delay = float(os.environ.get("TTS_MIN_DELAY_SECONDS", "1.5"))
    jitter_max = float(os.environ.get("TTS_DELAY_JITTER_SECONDS", "0.3"))

    # cache key based on text+lang
    key = hashlib.sha1(f"{lang}|{text}".encode("utf-8")).hexdigest()
    cache_path = os.path.join(_TTS_CACHE_DIR, f"{key}.mp3")
    if os.path.exists(cache_path):
        try:
            return AudioSegment.from_mp3(cache_path), cache_path
        except Exception:
            # fall through to regenerate if cache corrupted
            pass

    for attempt in range(1, max_retries + 1):
        try:
            # rate limit: ensure a minimum delay between gTTS calls
            with _gtts_lock:
                global _last_gtts_time
                elapsed = time.time() - _last_gtts_time
                if elapsed < min_delay:
                    to_sleep = (min_delay - elapsed) + (random.random() * jitter_max)
                    time.sleep(to_sleep)
                _last_gtts_time = time.time()

            gTTS(text=text, lang=lang).save(temp_mp3)
            # copy to cache for reuse
            try:
                shutil.copyfile(temp_mp3, cache_path)
            except Exception:
                pass
            return AudioSegment.from_mp3(temp_mp3), temp_mp3
        except gTTSError as exc:
            message = str(exc)
            rate_limited = "429" in message or "Too Many Requests" in message
            if not rate_limited or attempt >= max_retries:
                _log(f"[Pipeline] gTTS failed for lang={lang}: {message}")
                break

            wait_seconds = base_delay * (2 ** (attempt - 1))
            _log(
                f"[Pipeline] gTTS rate-limited for lang={lang}, retry {attempt}/{max_retries} in {wait_seconds:.1f}s"
            )
            time.sleep(wait_seconds)
        except Exception as exc:
            _log(f"[Pipeline] gTTS failed for lang={lang}: {exc}")
            break

    try:
        if os.path.exists(temp_mp3):
            os.remove(temp_mp3)
    except OSError:
        pass

    _log(f"[Pipeline] Fallback to silence for lang={lang}")
    return AudioSegment.silent(duration=PAUSE_MS), None


def _tts_segment_polly(text, lang, temp_dir):
    temp_mp3 = os.path.join(temp_dir, f"tts_{uuid.uuid4().hex}.mp3")
    lang_code = _normalize_lang_code(lang)
    voice_id = _polly_voice_id(lang_code)
    if not voice_id:
        raise ValueError(f"Polly does not support lang={lang_code}")

    polly_client = boto3.Session(
        aws_access_key_id=os.environ.get("AWS_ACCESS_KEY_ID"),
        aws_secret_access_key=os.environ.get("AWS_SECRET_ACCESS_KEY"),
        region_name=_get_polly_region(),
    ).client("polly")

    response = polly_client.synthesize_speech(
        VoiceId=voice_id,
        OutputFormat="mp3",
        Text=text,
        TextType="text",
    )

    with open(temp_mp3, "wb") as f:
        f.write(response["AudioStream"].read())

    return AudioSegment.from_mp3(temp_mp3), temp_mp3


def _tts_segment_google(text, lang, temp_dir):
    """Use Google Cloud Text-to-Speech via ADC or service account."""
    if texttospeech is None:
        raise RuntimeError("google-cloud-texttospeech not installed")

    temp_mp3 = os.path.join(temp_dir, f"gcloud_tts_{uuid.uuid4().hex}.mp3")
    max_retries = int(os.environ.get("GOOGLE_TTS_MAX_RETRIES", "3"))
    base_delay = float(os.environ.get("GOOGLE_TTS_BASE_DELAY", "1"))

    # prepare client and params
    client = texttospeech.TextToSpeechClient()
    synthesis_input = texttospeech.SynthesisInput(text=text)
    # choose language/voice per requested lang
    lang_map = {
        "vi": "vi-VN",
        "zh": "cmn-CN",
        "ja": "ja-JP",
        "en": "en-US",
    }
    lang_code = lang_map.get(lang, "vi-VN")

    profile = _get_google_profile(lang)
    voice_name = (profile.get("voice_name") or "").strip()
    gender_label = (profile.get("gender") or "Mặc định").strip()

    voice_kwargs = {"language_code": lang_code}
    if voice_name:
        voice_kwargs["name"] = voice_name
    else:
        try:
            gender_map = {
                "Nam": texttospeech.SsmlVoiceGender.MALE,
                "Nữ": texttospeech.SsmlVoiceGender.FEMALE,
                "Trung tính": texttospeech.SsmlVoiceGender.NEUTRAL,
            }
            gender_enum = gender_map.get(gender_label, texttospeech.SsmlVoiceGender.SSML_VOICE_GENDER_UNSPECIFIED)
            if gender_enum != texttospeech.SsmlVoiceGender.SSML_VOICE_GENDER_UNSPECIFIED:
                voice_kwargs["ssml_gender"] = gender_enum
        except Exception:
            pass

    voice = texttospeech.VoiceSelectionParams(**voice_kwargs)
    audio_config = texttospeech.AudioConfig(audio_encoding=texttospeech.AudioEncoding.MP3)

    last_exc = None
    for attempt in range(1, max_retries + 1):
        try:
            response = client.synthesize_speech(input=synthesis_input, voice=voice, audio_config=audio_config)
            with open(temp_mp3, "wb") as f:
                f.write(response.audio_content)
            return AudioSegment.from_mp3(temp_mp3), temp_mp3
        except Exception as exc:
            last_exc = exc
            _log(f"[Pipeline] Google Cloud TTS attempt {attempt}/{max_retries} failed for lang={lang}: {exc}")
            if attempt < max_retries:
                time.sleep(base_delay * (2 ** (attempt - 1)))

    # all retries exhausted
    raise last_exc


def _tts_segment(text, lang, temp_dir, engine_mode):
    lang_code = _normalize_lang_code(lang)
    engine_clean = (engine_mode or "gTTS").strip().lower()
    # If engine explicitly set to Polly, try Polly first, then Google, then gTTS
    if engine_clean == "polly":
        if lang_code in {"en", "ja", "zh"}:
            try:
                return _tts_segment_polly(text, lang_code, temp_dir)
            except Exception as exc:
                _log(f"[Pipeline] Polly failed for lang={lang_code}: {exc}")
                _log(f"[Pipeline] Trying Google Cloud TTS for lang={lang_code}")
                try:
                    return _tts_segment_google(text, lang_code, temp_dir)
                except Exception as gexc:
                    _log(f"[Pipeline] Google Cloud TTS also failed: {gexc}; falling back to gTTS")
                    return _tts_segment_gtts(text, lang_code, temp_dir)

        _log(f"[Pipeline] Polly does not support lang={lang_code}; trying Google Cloud then gTTS")
        try:
            return _tts_segment_google(text, lang_code, temp_dir)
        except Exception as gexc:
            _log(f"[Pipeline] Google Cloud TTS failed for lang={lang_code}: {gexc}; falling back to gTTS")
            return _tts_segment_gtts(text, lang_code, temp_dir)

    # Default (engine is gTTS or unspecified): prefer Google Cloud, then gTTS, then Polly
    try:
        return _tts_segment_google(text, lang_code, temp_dir)
    except Exception as exc:
        _log(f"[Pipeline] Google Cloud TTS failed for lang={lang_code}: {exc}; falling back to gTTS")

    try:
        return _tts_segment_gtts(text, lang_code, temp_dir)
    except Exception as exc:
        _log(f"[Pipeline] gTTS failed for lang={lang_code}: {exc}")
        if lang_code in {"en", "ja", "zh"}:
            _log(f"[Pipeline] Fallback to Polly for lang={lang_code}")
            try:
                return _tts_segment_polly(text, lang_code, temp_dir)
            except Exception as polly_exc:
                _log(f"[Pipeline] Polly fallback failed for lang={lang_code}: {polly_exc}")
        _log(f"[Pipeline] Fallback to silence for lang={lang_code}")
        return AudioSegment.silent(duration=PAUSE_MS), None


def _build_word_audio(word, meaning, engine_mode):
    temp_dir = tempfile.gettempdir()
    created_files = []
    try:
        zh_seg, zh_path = _tts_segment(word, "zh-CN", temp_dir, engine_mode)
        if zh_path:
            created_files.append(zh_path)
        vi_seg, vi_path = _tts_segment(meaning, "vi", temp_dir, engine_mode)
        if vi_path:
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
    engine_mode = os.environ.get("TTS_ENGINE", "gTTS")
    _log(f"[Pipeline] Start: excel={file_path}")
    _log(f"[Pipeline] Sheet: {sheet_name}")
    _log(f"[Pipeline] TTS engine: {engine_mode}")
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
            audio = _build_word_audio(word, meaning, engine_mode)
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
