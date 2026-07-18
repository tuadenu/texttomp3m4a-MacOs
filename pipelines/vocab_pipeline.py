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
import io

# macOS may fork a child process when pydub invokes ffmpeg.  Google Cloud
# TTS uses gRPC background pollers; enabling fork support prevents the child
# from inheriting an inconsistent gRPC poll set (which otherwise aborts with
# ``wakeup_fd_->ConsumeWakeup``).
os.environ.setdefault("GRPC_ENABLE_FORK_SUPPORT", "1")
os.environ.setdefault("GRPC_POLL_STRATEGY", "poll")

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
SUPPORTED_M4A_BITRATES = {"26k", "32k"}
SUPPORTED_VOCAB_AUDIO_MODES = {"zh_only", "zh_vi"}

# gTTS local cache and rate limiting
_TTS_CACHE_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".tts_cache")
os.makedirs(_TTS_CACHE_DIR, exist_ok=True)
_gtts_lock = threading.Lock()
_last_gtts_time = 0.0
_google_client_lock = threading.Lock()
_google_client = None
_google_client_identity = None


class TTSGenerationError(RuntimeError):
    """A requested TTS engine could not create audible source audio."""


def _is_slow_speed(speed: str | None) -> bool:
    return (speed or "").strip().lower() == "chậm"


def _resolve_m4a_bitrate(bitrate: str | None = None) -> str:
    resolved = (bitrate or os.environ.get("M4A_BITRATE") or TARGET_BITRATE).strip().lower()
    if resolved not in SUPPORTED_M4A_BITRATES:
        raise ValueError(
            f"M4A bitrate không hỗ trợ: {resolved}. Chỉ hỗ trợ: "
            + ", ".join(sorted(SUPPORTED_M4A_BITRATES))
        )
    return resolved


def _resolve_vocab_audio_mode(audio_mode: str | None = None) -> str:
    resolved = (audio_mode or os.environ.get("TTS_AUDIO_MODE") or "zh_vi").strip().lower()
    if resolved in {"zh_only", "zh-only", "chinese_only"}:
        return "zh_only"
    return "zh_vi"


def _vocab_tts_runtime_config() -> tuple[str, str, str, set[str], str]:
    """Read the UI snapshot passed to the vocab subprocess without secrets."""
    if str(os.environ.get("TTS_CONFIG_CONFIRMED", "true")).strip().lower() not in {"1", "true", "yes"}:
        raise ValueError("Chưa xác nhận dùng cấu hình TTS hiện tại cho vocab HSK.")
    speed = (os.environ.get("TTS_SPEED") or "Bình thường").strip()
    voice = (os.environ.get("TTS_VOICE") or "Mặc định").strip()
    bitrate = _resolve_m4a_bitrate()
    raw_languages = os.environ.get("TTS_LANGUAGES", "").strip()
    languages = {value.strip().lower() for value in raw_languages.split(",") if value.strip()}
    if raw_languages and not {"vi", "zh"}.issubset(languages):
        raise ValueError("Vocab HSK cần chọn cả Tiếng Việt và Tiếng Trung trong cấu hình TTS.")
    return speed, voice, bitrate, languages, _resolve_vocab_audio_mode()


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


def _get_google_profile(lang, requested_voice="Mặc định"):
    lang_code = _normalize_lang_code(lang)
    raw = os.environ.get("GOOGLE_TTS_PROFILES_JSON", "")
    if not raw:
        return {"gender": "Mặc định", "voice_name": ""}

    try:
        data = json.loads(raw)
        profile = data.get(lang_code, {}) or {}
        requested_label = str(requested_voice or "Mặc định").strip()
        slots = profile.get("slots", {})
        if isinstance(slots, dict) and requested_label in {"Nam", "Nữ", "Trung tính"}:
            slot = slots.get(requested_label, {}) or {}
            return {
                "gender": slot.get("gender", requested_label) or requested_label,
                "voice_name": slot.get("voice_name", "") or "",
            }
        if isinstance(slots, dict) and requested_label == "Mặc định":
            default_slot = slots.get("Mặc định", {}) or {}
            if default_slot.get("voice_name"):
                return {
                    "gender": default_slot.get("gender", "Mặc định") or "Mặc định",
                    "voice_name": default_slot.get("voice_name", "") or "",
                }
        return {
            "gender": profile.get("gender", "Mặc định") or "Mặc định",
            "voice_name": profile.get("voice_name", "") or "",
        }
    except Exception:
        return {"gender": "Mặc định", "voice_name": ""}


def _google_tts_client():
    """Create a Cloud TTS client using the dedicated API key when configured."""
    global _google_client, _google_client_identity
    api_key = os.environ.get("GOOGLE_TTS_API_KEY", "").strip()
    identity = api_key or "adc"
    with _google_client_lock:
        if _google_client is not None and _google_client_identity == identity:
            return _google_client
        if _google_client is not None:
            try:
                _google_client.close()
            except Exception:
                pass
        if api_key:
            from google.api_core.client_options import ClientOptions
            _google_client = texttospeech.TextToSpeechClient(
                client_options=ClientOptions(api_key=api_key)
            )
        else:
            _google_client = texttospeech.TextToSpeechClient()
        _google_client_identity = identity
        return _google_client


def _reset_google_tts_client() -> None:
    """Close and forget a broken shared client before a retry."""
    global _google_client, _google_client_identity
    with _google_client_lock:
        client = _google_client
        _google_client = None
        _google_client_identity = None
        if client is not None:
            try:
                client.close()
            except Exception:
                pass


def _audio_segment_from_mp3_spawn(path: str) -> AudioSegment:
    """Decode an MP3 without forking a gRPC-threaded parent on macOS.

    pydub's default Popen settings select ``fork_exec`` on some Python/macOS
    combinations.  Google Cloud TTS leaves gRPC poller threads behind, and a
    forked ffmpeg child can then abort in grpc_event_engine.  ``close_fds=False``
    allows CPython to use posix_spawn; ffmpeg writes WAV to stdout, which is
    parsed in-process without another child.
    """
    command = [AudioSegment.converter, "-y", "-i", path, "-vn", "-f", "wav", "-"]
    process = subprocess.Popen(
        command,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        close_fds=False,
    )
    wav_data, stderr = process.communicate()
    if process.returncode != 0 or not wav_data:
        detail = stderr.decode("utf-8", errors="replace").strip()
        raise RuntimeError(f"ffmpeg decode failed ({process.returncode}): {detail}")
    return AudioSegment.from_wav(io.BytesIO(wav_data))


def _tts_segment_gtts(text, lang, temp_dir, speed="Bình thường"):
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
            return _audio_segment_from_mp3_spawn(cache_path), cache_path
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

            gTTS(text=text, lang=lang, slow=_is_slow_speed(speed)).save(temp_mp3)
            # copy to cache for reuse
            try:
                shutil.copyfile(temp_mp3, cache_path)
            except Exception:
                pass
            return _audio_segment_from_mp3_spawn(temp_mp3), temp_mp3
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

    raise TTSGenerationError(f"gTTS không tạo được audio cho lang={lang}: {text!r}")


def _tts_segment_polly(text, lang, temp_dir, speed="Bình thường", voice="Mặc định"):
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

    text_type = "text"
    tts_text = text
    if _is_slow_speed(speed):
        tts_text = f"<speak><prosody rate='80%'>{text}</prosody></speak>"
        text_type = "ssml"
    response = polly_client.synthesize_speech(
        VoiceId=voice_id,
        OutputFormat="mp3",
        Text=tts_text,
        TextType=text_type,
    )

    with open(temp_mp3, "wb") as f:
        f.write(response["AudioStream"].read())

    return AudioSegment.from_mp3(temp_mp3), temp_mp3


def _tts_segment_google(text, lang, temp_dir, speed="Bình thường", voice="Mặc định"):
    """Use Google Cloud Text-to-Speech via ADC or service account."""
    if texttospeech is None:
        raise RuntimeError("google-cloud-texttospeech not installed")

    temp_mp3 = os.path.join(temp_dir, f"gcloud_tts_{uuid.uuid4().hex}.mp3")
    max_retries = int(os.environ.get("GOOGLE_TTS_MAX_RETRIES", "3"))
    base_delay = float(os.environ.get("GOOGLE_TTS_BASE_DELAY", "1"))

    # prepare client and params
    client = _google_tts_client()
    synthesis_input = texttospeech.SynthesisInput(text=text)
    # choose language/voice per requested lang
    lang_map = {
        "vi": "vi-VN",
        "zh": "cmn-CN",
        "ja": "ja-JP",
        "en": "en-US",
    }
    lang_code = lang_map.get(lang, "vi-VN")

    requested_voice = (voice or "Mặc định").strip()
    profile = _get_google_profile(lang, requested_voice)
    voice_name = (profile.get("voice_name") or "").strip()
    gender_label = (profile.get("gender") or "Mặc định").strip()
    if not voice_name and requested_voice in {"Nam", "Nữ", "Trung tính"}:
        gender_label = requested_voice

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
    audio_kwargs = {"audio_encoding": texttospeech.AudioEncoding.MP3}
    if _is_slow_speed(speed):
        audio_kwargs["speaking_rate"] = 0.8
    audio_config = texttospeech.AudioConfig(**audio_kwargs)

    last_exc = None
    for attempt in range(1, max_retries + 1):
        try:
            response = client.synthesize_speech(input=synthesis_input, voice=voice, audio_config=audio_config)
            with open(temp_mp3, "wb") as f:
                f.write(response.audio_content)
            return _audio_segment_from_mp3_spawn(temp_mp3), temp_mp3
        except Exception as exc:
            last_exc = exc
            _log(f"[Pipeline] Google Cloud TTS attempt {attempt}/{max_retries} failed for lang={lang}: {exc}")
            _reset_google_tts_client()
            if attempt < max_retries:
                time.sleep(base_delay * (2 ** (attempt - 1)))
    # all retries exhausted
    raise last_exc


def _tts_segment(text, lang, temp_dir, engine_mode, speed="Bình thường", voice="Mặc định"):
    lang_code = _normalize_lang_code(lang)
    engine_clean = (engine_mode or "gTTS").strip().lower()
    if engine_clean == "gtts":
        # gTTS is an explicit user choice. Do not probe Google Cloud first.
        return _tts_segment_gtts(text, lang_code, temp_dir, speed)

    # If Polly is explicitly selected, keep fallback local to Polly -> gTTS.
    # Do not probe Google Cloud here: Polly has no Vietnamese voice in this
    # pipeline, and a Polly credential/service failure should not depend on a
    # separate Google Cloud project being enabled.
    if engine_clean == "polly":
        if lang_code not in {"en", "ja", "zh"}:
            _log(f"[Pipeline] Polly does not support lang={lang_code}; using gTTS")
            return _tts_segment_gtts(text, lang_code, temp_dir, speed)
        try:
            return _tts_segment_polly(text, lang_code, temp_dir, speed, voice)
        except Exception as exc:
            _log(f"[Pipeline] Polly failed for lang={lang_code}: {exc}; falling back to gTTS")
            return _tts_segment_gtts(text, lang_code, temp_dir, speed)

    # Google Cloud (and legacy/unrecognised values): prefer Google Cloud then gTTS.
    try:
        return _tts_segment_google(text, lang_code, temp_dir, speed, voice)
    except Exception as exc:
        _log(f"[Pipeline] Google Cloud TTS failed for lang={lang_code}: {exc}; falling back to gTTS")

    try:
        return _tts_segment_gtts(text, lang_code, temp_dir, speed)
    except Exception as exc:
        _log(f"[Pipeline] gTTS failed for lang={lang_code}: {exc}")
        if lang_code in {"en", "ja", "zh"}:
            _log(f"[Pipeline] Fallback to Polly for lang={lang_code}")
            try:
                return _tts_segment_polly(text, lang_code, temp_dir, speed, voice)
            except Exception as polly_exc:
                _log(f"[Pipeline] Polly fallback failed for lang={lang_code}: {polly_exc}")
        raise TTSGenerationError(f"Không tạo được audio bằng engine {engine_mode} cho lang={lang_code}") from exc


def _build_word_audio(word, meaning, engine_mode, speed="Bình thường", voice="Mặc định", audio_mode="zh_vi"):
    temp_dir = tempfile.gettempdir()
    created_files = []
    try:
        dialogue_pairs = {
            "Hội thoại 1 câu nam - 1 câu nữ": ("Nam", "Nữ"),
            "Hội thoại 1 câu nữ - 1 câu nam": ("Nữ", "Nam"),
        }
        zh_voice, vi_voice = dialogue_pairs.get(voice, (voice, voice))
        zh_seg, zh_path = _tts_segment(word, "zh-CN", temp_dir, engine_mode, speed, zh_voice)
        if zh_path:
            created_files.append(zh_path)
        if _resolve_vocab_audio_mode(audio_mode) == "zh_only":
            return zh_seg
        vi_seg, vi_path = _tts_segment(meaning, "vi", temp_dir, engine_mode, speed, vi_voice)
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


def _export_m4a(audio, file_path, bitrate=None):
    normalized = audio.set_channels(TARGET_CHANNELS).set_frame_rate(TARGET_SAMPLE_RATE)
    resolved_bitrate = _resolve_m4a_bitrate(bitrate)
    # Write WAV directly (no child process), then invoke ffmpeg with
    # close_fds=False so CPython/macOS can use posix_spawn instead of
    # fork_exec.  The latter can crash when Google gRPC poller threads exist.
    wav_path = None
    try:
        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as wav_file:
            wav_path = wav_file.name
        normalized.export(wav_path, format="wav")
        command = [
            AudioSegment.converter,
            "-y",
            "-f", "wav",
            "-i", wav_path,
            "-acodec", "aac",
            "-b:a", resolved_bitrate,
            "-ac", str(TARGET_CHANNELS),
            "-ar", str(TARGET_SAMPLE_RATE),
            "-f", "ipod",
            str(file_path),
        ]
        process = subprocess.Popen(
            command,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            close_fds=False,
        )
        _, stderr = process.communicate()
        if process.returncode != 0:
            detail = stderr.decode("utf-8", errors="replace").strip()
            raise RuntimeError(
                f"Encoding failed. ffmpeg/avlib returned error code: {process.returncode}\n"
                f"Command:{command}\n\nOutput from ffmpeg/avlib:\n\n{detail}"
            )
    finally:
        if wav_path:
            try:
                os.unlink(wav_path)
            except OSError:
                pass


def _google_profiles_digest():
    raw = os.environ.get("GOOGLE_TTS_PROFILES_JSON", "")
    try:
        canonical = json.dumps(json.loads(raw), ensure_ascii=False, sort_keys=True)
    except Exception:
        canonical = raw
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def run_vocab_pipeline(file_path, sheet_name, skip_validate=False):
    overwrite_local_audio = str(os.environ.get("OVERWRITE_LOCAL_AUDIO", "false")).lower() == "true"
    engine_mode = os.environ.get("TTS_ENGINE", "gTTS")
    speed, voice, bitrate, languages, audio_mode = _vocab_tts_runtime_config()
    tts_config = {
        "engine": engine_mode,
        "speed": speed,
        "voice": voice,
        "bitrate": bitrate,
        "audio_mode": audio_mode,
        "google_profiles_sha256": _google_profiles_digest(),
    }
    _log(f"[Pipeline] Start: excel={file_path}")
    _log(f"[Pipeline] Sheet: {sheet_name}")
    _log(f"[Pipeline] TTS engine: {engine_mode}")
    _log(f"[Pipeline] TTS speed: {speed} | voice: {voice} | audio mode: {audio_mode} | M4A: AAC-LC mono 22050Hz {bitrate}")
    if languages:
        _log(f"[Pipeline] Selected languages: {','.join(sorted(languages))}")
    df = pd.read_excel(file_path, sheet_name=sheet_name)
    col = _normalize_columns(df)

    total_rows = len(df)
    _log(f"[Pipeline] Rows loaded: {total_rows}")

    output_dir = os.path.join("output", sheet_name)
    os.makedirs(output_dir, exist_ok=True)

    audio_dir = os.path.join(output_dir, "audio")
    os.makedirs(audio_dir, exist_ok=True)

    metadata_path = os.path.join(output_dir, "output_vocab_metadata.json")
    existing_metadata = {}
    try:
        with open(metadata_path, "r", encoding="utf-8") as f:
            existing_metadata = json.load(f)
    except (OSError, ValueError, TypeError):
        pass
    reuse_existing_audio = (
        not overwrite_local_audio
        and existing_metadata.get("ttsConfig") == tts_config
    )

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

        if os.path.isfile(file_path_out) and os.path.getsize(file_path_out) > 0 and reuse_existing_audio:
            skipped_existing_count += 1
            _log(f"[Pipeline] Skip existing M4A {row_index}/{total_valid}: {file_name}")
        else:
            if overwrite_local_audio and os.path.isfile(file_path_out):
                _log(f"[Pipeline] Overwrite existing M4A {row_index}/{total_valid}: {file_name}")
            _log(f"[Pipeline] Generate {row_index}/{total_valid}: word={word} -> {file_name}")
            try:
                audio = _build_word_audio(word, meaning, engine_mode, speed, voice, audio_mode)
                _export_m4a(audio, file_path_out, bitrate)
            except Exception as exc:
                raise TTSGenerationError(
                    f"TTS thất bại tại vocab {row_index} ({word!r}); không tạo audio im lặng: {exc}"
                ) from exc
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

    metadata = {
        "sheet_name": sheet_name,
        "excel_file": os.path.abspath(file_path),
        "expected_count": len(output_items),
        "generated_count": generated_count,
        "skipped_existing_count": skipped_existing_count,
        "skipped_empty_count": skipped_rows,
        "total_rows": total_rows,
        "overwrite_local_audio": overwrite_local_audio,
        "ttsConfig": tts_config,
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
    try:
        run_vocab_pipeline(args.excel_file, args.sheet, skip_validate=args.skip_validate)
    except Exception as exc:
        print(f"STATUS: FAIL\n{exc}")
        raise SystemExit(1)
