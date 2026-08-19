from __future__ import annotations

import argparse
import json
import os
import multiprocessing as mp
import subprocess
import tempfile
import sys
import time
from queue import Empty as QueueEmpty
from pathlib import Path
from typing import Iterable

try:
    from gtts import gTTS
except Exception:  # pragma: no cover - optional runtime dependency
    gTTS = None

try:
    from google.cloud import texttospeech
except Exception:  # pragma: no cover - optional runtime dependency
    texttospeech = None

try:
    import boto3
except Exception:  # pragma: no cover - optional runtime dependency
    boto3 = None

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from pipelines.pronunciation_overrides import prepare_spoken_text

APP_CONFIG_PATH = PROJECT_ROOT / "AppData" / "config.json"


def _load_app_config() -> dict:
    if not APP_CONFIG_PATH.is_file():
        return {}
    try:
        return json.loads(APP_CONFIG_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {}


WORDS = {
    "xiaohaier": "小孩儿",
    "miantiaor": "面条儿",
    "yidianr": "一点儿",
}

REQUEST_TIMEOUT_SECONDS = 25

GOOGLE_VOICES = {
    "A": "cmn-CN-Standard-A",
    "C": "cmn-CN-Standard-C",
}

CANDIDATES = {
    "小孩儿": [
        ("c1", "小孩子"),
        ("c2", "小孩"),
    ],
    "面条儿": [
        ("c1", "面条"),
    ],
    "一点儿": [
        ("c1", "一点"),
        ("c2", "一点点"),
    ],
}


def _clean_label(text: str) -> str:
    return "".join(ch if ch.isalnum() else "_" for ch in text.lower()).strip("_")


def _short_word_key(word: str) -> str:
    mapping = {
        "小孩儿": "xiaohaier",
        "面条儿": "miantiaor",
        "一点儿": "yidianr",
    }
    return mapping.get(word, _clean_label(word))


def _ensure_ffmpeg() -> str | None:
    system = subprocess.run(["which", "ffmpeg"], capture_output=True, text=True)
    if system.returncode == 0:
        return system.stdout.strip()
    bundled = Path(__file__).resolve().parents[1] / "ffmpeg_bin" / "bin" / "ffmpeg"
    return str(bundled) if bundled.is_file() else None


def _duration_ms(path: Path) -> int:
    try:
        from pydub import AudioSegment

        return len(AudioSegment.from_file(str(path)))
    except Exception:
        ffmpeg = _ensure_ffmpeg()
        if not ffmpeg:
            return -1
        result = subprocess.run([ffmpeg, "-i", str(path), "-f", "null", "-"], capture_output=True, text=True)
        stderr = result.stderr or ""
        for line in stderr.splitlines():
            if "Duration:" in line:
                part = line.split("Duration:", 1)[1].strip().split(",", 1)[0].strip()
                hh, mm, ss = part.split(":")
                seconds = float(ss)
                total = int((int(hh) * 3600 + int(mm) * 60 + seconds) * 1000)
                return total
        return -1


def _save_google(text: str, voice_name: str, out_path: Path, ssml: bool = False) -> None:
    if texttospeech is None:
        raise RuntimeError("google-cloud-texttospeech not installed")

    config = _load_app_config()
    api_key = (config.get("GOOGLE_TTS_API_KEY") or os.environ.get("GOOGLE_TTS_API_KEY") or "").strip()
    if api_key:
        from google.api_core.client_options import ClientOptions

        client = texttospeech.TextToSpeechClient(client_options=ClientOptions(api_key=api_key))
    else:
        client = texttospeech.TextToSpeechClient()
    if ssml:
        synthesis_input = texttospeech.SynthesisInput(ssml=text)
    else:
        synthesis_input = texttospeech.SynthesisInput(text=text)
    voice = texttospeech.VoiceSelectionParams(language_code="cmn-CN", name=voice_name)
    config = texttospeech.AudioConfig(audio_encoding=texttospeech.AudioEncoding.MP3)
    response = client.synthesize_speech(input=synthesis_input, voice=voice, audio_config=config)
    out_path.write_bytes(response.audio_content)


def _save_gtts(text: str, out_path: Path) -> None:
    if gTTS is None:
        raise RuntimeError("gTTS not installed")
    gTTS(text=text, lang="zh", slow=False).save(str(out_path))


def _save_polly(text: str, out_path: Path) -> None:
    if boto3 is None:
        raise RuntimeError("boto3 not installed")
    config = _load_app_config()
    session = boto3.Session(
        aws_access_key_id=(config.get("AWS_ACCESS_KEY_ID") or os.environ.get("AWS_ACCESS_KEY_ID") or "").strip() or None,
        aws_secret_access_key=(config.get("AWS_SECRET_ACCESS_KEY") or os.environ.get("AWS_SECRET_ACCESS_KEY") or "").strip() or None,
        region_name=(config.get("AWS_REGION") or os.environ.get("AWS_REGION") or os.environ.get("AWS_DEFAULT_REGION") or "ap-southeast-1").strip(),
    )
    client = session.client("polly")
    response = client.synthesize_speech(VoiceId="Zhiyu", OutputFormat="mp3", Text=text, TextType="text")
    out_path.write_bytes(response["AudioStream"].read())


def _export_m4a(mp3_path: Path, out_path: Path) -> None:
    ffmpeg = _ensure_ffmpeg()
    if not ffmpeg:
        raise RuntimeError("ffmpeg not available")
    subprocess.run(
        [ffmpeg, "-y", "-i", str(mp3_path), "-acodec", "aac", "-b:a", "32k", "-ac", "1", "-ar", "48000", "-f", "ipod", str(out_path)],
        check=True,
        capture_output=True,
        text=True,
    )


def _write_summary(payload: dict, out_dir: Path) -> None:
    summary_path = out_dir / "summary.json"
    summary_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _print(message: str) -> None:
    print(message, flush=True)


def _worker(queue, func, args, kwargs):
    try:
        queue.put({"ok": True, "result": func(*args, **kwargs)})
    except Exception as exc:
        queue.put({"ok": False, "error": repr(exc)})


def _run_with_timeout(func, timeout_seconds: int, *args, **kwargs):
    context = mp.get_context("spawn")
    queue = context.Queue()
    process = context.Process(target=_worker, args=(queue, func, args, kwargs))
    process.daemon = True
    process.start()
    process.join(timeout_seconds)
    if process.is_alive():
        process.terminate()
        process.join(5)
        raise TimeoutError(f"timeout>{timeout_seconds}s")
    try:
        payload = queue.get_nowait()
    except QueueEmpty:
        raise RuntimeError("worker returned no payload")
    if payload.get("ok"):
        return payload.get("result")
    raise RuntimeError(payload.get("error") or "worker failed")


def _export_case(out_dir: Path, word_key: str, label: str, text: str, engine: str, voice: str | None = None, ssml: bool = False) -> dict:
    safe = _short_word_key(word_key)
    suffix = _clean_label(label)
    prepared = prepare_spoken_text(text, "zh-CN", engine, voice)
    spoken = prepared.spoken_text

    if engine == "gtts":
        mp3_path = out_dir / f"gtts_{safe}_{suffix}.mp3"
        _save_gtts(spoken, mp3_path)
        return {
            "word": text,
            "engine": "gTTS",
            "voice": None,
            "label": label,
            "mode": prepared.mode,
            "source_text": prepared.source_text,
            "spoken_text": spoken,
            "file": str(mp3_path),
            "duration_ms": _duration_ms(mp3_path),
        }

    if engine == "google":
        voice_label = "A" if (voice or "").endswith("A") else ("C" if (voice or "").endswith("C") else "A")
        mp3_path = out_dir / f"google_{voice_label}_{safe}_{suffix}.mp3"
        m4a_path = out_dir / f"google_{voice_label}_{safe}_{suffix}.m4a"
        _save_google(spoken if not ssml else text, voice or "cmn-CN-Standard-A", mp3_path, ssml=ssml)
        _export_m4a(mp3_path, m4a_path)
        return {
            "word": text,
            "engine": "Google Cloud TTS",
            "voice": voice,
            "label": label,
            "mode": "ssml" if ssml else prepared.mode,
            "source_text": prepared.source_text,
            "spoken_text": spoken if not ssml else text,
            "file": str(m4a_path),
            "duration_ms": _duration_ms(m4a_path),
        }

    if engine == "polly":
        mp3_path = out_dir / f"polly_{safe}_{suffix}.mp3"
        _save_polly(text, mp3_path)
        return {
            "word": text,
            "engine": "Polly",
            "voice": "Zhiyu",
            "label": label,
            "mode": "raw",
            "source_text": text,
            "spoken_text": text,
            "file": str(mp3_path),
            "duration_ms": _duration_ms(mp3_path),
        }

    raise ValueError(f"Unsupported engine: {engine}")


def build_candidates(out_dir: Path) -> list[dict]:
    tasks: list[dict] = []
    for word_key, word in WORDS.items():
        candidates = CANDIDATES[word]
        for voice_label, voice_name in GOOGLE_VOICES.items():
            tasks.append({"word_key": word_key, "word": word, "engine": "google", "voice_label": voice_label, "voice_name": voice_name, "label": f"raw_{voice_label}", "text": word, "ssml": False})
            for candidate_label, candidate_text in candidates:
                tasks.append({"word_key": word_key, "word": word, "engine": "google", "voice_label": voice_label, "voice_name": voice_name, "label": f"{candidate_label}_{voice_label}", "text": candidate_text, "ssml": False})
            if word == "小孩儿":
                tasks.append({"word_key": word_key, "word": word, "engine": "google", "voice_label": voice_label, "voice_name": voice_name, "label": f"ssml_{voice_label}", "text": "<speak><prosody rate='100%'>小孩儿</prosody></speak>", "ssml": True})

        tasks.append({"word_key": word_key, "word": word, "engine": "gtts", "voice_label": None, "voice_name": None, "label": "raw", "text": word, "ssml": False})
        for candidate_label, candidate_text in candidates:
            tasks.append({"word_key": word_key, "word": word, "engine": "gtts", "voice_label": None, "voice_name": None, "label": f"{candidate_label}", "text": candidate_text, "ssml": False})

        tasks.append({"word_key": word_key, "word": word, "engine": "polly", "voice_label": None, "voice_name": None, "label": "reference", "text": word, "ssml": False})

    _print(f"Python executable: {sys.executable}")
    _print(f"Output dir: {out_dir}")
    _print(f"Candidate count: {len(tasks)}")
    config = _load_app_config()
    google_mode = "api_key" if (config.get("GOOGLE_TTS_API_KEY") or os.environ.get("GOOGLE_TTS_API_KEY")) else "adc"
    _print(f"Google credential mode: {google_mode}")
    if config.get("AWS_REGION") or os.environ.get("AWS_REGION") or os.environ.get("AWS_DEFAULT_REGION"):
        _print(f"AWS region: {(config.get('AWS_REGION') or os.environ.get('AWS_REGION') or os.environ.get('AWS_DEFAULT_REGION'))}")

    rows: list[dict] = []
    success = 0
    failed = 0
    total = len(tasks)
    for index, task in enumerate(tasks, start=1):
        word = task["word"]
        engine = task["engine"]
        voice = task["voice_name"] or ""
        label = task["label"]
        candidate_name = label.replace("_", " ")
        prefix = f"[{index}/{total}] {engine} / {voice or 'plain'} / {word} / {candidate_name}"
        _print(f"{prefix} START")
        started = time.monotonic()
        try:
            row = _run_with_timeout(
                _export_case,
                REQUEST_TIMEOUT_SECONDS,
                out_dir,
                task["word_key"],
                label,
                task["text"],
                engine,
                voice=voice or None,
                ssml=task["ssml"],
            )
            row["status"] = "ok"
            row["error"] = ""
            row["duration_wall_ms"] = int((time.monotonic() - started) * 1000)
            rows.append(row)
            success += 1
            _print(f"{prefix} DONE duration={row['duration_wall_ms']}ms file={row['file']}")
        except TimeoutError:
            failed += 1
            error = f"timeout>{REQUEST_TIMEOUT_SECONDS}s"
            rows.append({
                "word": word,
                "engine": engine,
                "voice": voice or None,
                "label": label,
                "mode": "error",
                "source_text": task["text"],
                "spoken_text": task["text"],
                "file": "",
                "duration_ms": -1,
                "status": "error",
                "error": error,
                "duration_wall_ms": int((time.monotonic() - started) * 1000),
            })
            _print(f"{prefix} ERROR: {error}")
        except Exception as exc:
            failed += 1
            error = str(exc)
            rows.append({
                "word": word,
                "engine": engine,
                "voice": voice or None,
                "label": label,
                "mode": "error",
                "source_text": task["text"],
                "spoken_text": task["text"],
                "file": "",
                "duration_ms": -1,
                "status": "error",
                "error": error,
                "duration_wall_ms": int((time.monotonic() - started) * 1000),
            })
            _print(f"{prefix} ERROR: {error}")

    summary = {
        "success": success,
        "failed": failed,
        "output_dir": str(out_dir),
        "items": rows,
    }
    _write_summary(summary, out_dir)
    _print(f"COMPLETE success={success} failed={failed} output={out_dir}")
    return rows


def main() -> None:
    parser = argparse.ArgumentParser(description="Export pronunciation candidates for zh words.")
    parser.add_argument("--out-dir", default="pron_test", help="Output directory for candidate audio")
    args = parser.parse_args()

    out_dir = Path(args.out_dir).resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    rows = build_candidates(out_dir)
    print(json.dumps({"output_dir": str(out_dir), "items": rows}, ensure_ascii=False, indent=2), flush=True)


if __name__ == "__main__":
    main()