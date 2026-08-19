from __future__ import annotations

import argparse
import json
import multiprocessing as mp
import os
import sys
import time
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

try:
    from google.cloud import texttospeech
except Exception:  # pragma: no cover - optional runtime dependency
    texttospeech = None

from pipelines.pronunciation_overrides import prepare_spoken_text

APP_CONFIG_PATH = PROJECT_ROOT / "AppData" / "config.json"
REQUEST_TIMEOUT_SECONDS = 30

WORDS = {
    "xiaohaier": "小孩儿",
    "miantiaor": "面条儿",
}

GOOGLE_VOICES = {
    "A": "cmn-CN-Standard-A",
    "C": "cmn-CN-Standard-C",
}

PHONEME_CANDIDATES = {
    "小孩儿": [
        ("ph1", "xiao3 hai2r"),
        ("ph2", "xiao3 hai2 er"),
    ],
    "面条儿": [
        ("ph1", "mian4 tiao2r"),
        ("ph2", "mian4 tiao2 er"),
    ],
}


def _print(message: str) -> None:
    print(message, flush=True)


def _load_app_config() -> dict:
    if not APP_CONFIG_PATH.is_file():
        return {}
    try:
        return json.loads(APP_CONFIG_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _short_word_key(word: str) -> str:
    return WORDS.get(word, word)


def _clean_label(text: str) -> str:
    return "".join(ch if ch.isalnum() else "_" for ch in text.lower()).strip("_")


def _ensure_ffmpeg() -> str | None:
    import shutil

    system = shutil.which("ffmpeg")
    if system:
        return system
    bundled = PROJECT_ROOT / "ffmpeg_bin" / "bin" / "ffmpeg"
    return str(bundled) if bundled.is_file() else None


def _duration_ms(path: Path) -> int:
    from pydub import AudioSegment

    try:
        return len(AudioSegment.from_file(str(path)))
    except Exception:
        ffmpeg = _ensure_ffmpeg()
        if not ffmpeg:
            return -1
        import subprocess

        result = subprocess.run([ffmpeg, "-i", str(path), "-f", "null", "-"], capture_output=True, text=True)
        for line in (result.stderr or "").splitlines():
            if "Duration:" in line:
                part = line.split("Duration:", 1)[1].strip().split(",", 1)[0].strip()
                hh, mm, ss = part.split(":")
                seconds = float(ss)
                return int((int(hh) * 3600 + int(mm) * 60 + seconds) * 1000)
        return -1


def _write_summary(payload: dict, out_dir: Path) -> None:
    (out_dir / "summary.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _worker(queue, ssml: str, voice_name: str, out_path: str) -> None:
    try:
        config = _load_app_config()
        api_key = (config.get("GOOGLE_TTS_API_KEY") or os.environ.get("GOOGLE_TTS_API_KEY") or "").strip()
        if texttospeech is None:
            raise RuntimeError("google-cloud-texttospeech not installed")
        if api_key:
            from google.api_core.client_options import ClientOptions

            client = texttospeech.TextToSpeechClient(client_options=ClientOptions(api_key=api_key))
        else:
            client = texttospeech.TextToSpeechClient()

        synthesis_input = texttospeech.SynthesisInput(ssml=ssml)
        voice = texttospeech.VoiceSelectionParams(language_code="cmn-CN", name=voice_name)
        audio_config = texttospeech.AudioConfig(audio_encoding=texttospeech.AudioEncoding.MP3)
        response = client.synthesize_speech(input=synthesis_input, voice=voice, audio_config=audio_config)
        Path(out_path).write_bytes(response.audio_content)
        queue.put({"ok": True})
    except Exception as exc:
        queue.put({"ok": False, "error": repr(exc)})


def _run_google_request(ssml: str, voice_name: str, out_path: Path, timeout_seconds: int) -> None:
    context = mp.get_context("spawn")
    queue = context.Queue()
    process = context.Process(target=_worker, args=(queue, ssml, voice_name, str(out_path)))
    process.daemon = True
    process.start()
    process.join(timeout_seconds)
    if process.is_alive():
        process.terminate()
        process.join(5)
        raise TimeoutError(f"timeout>{timeout_seconds}s")
    payload = queue.get() if not queue.empty() else {"ok": False, "error": "worker returned no payload"}
    if not payload.get("ok"):
        raise RuntimeError(payload.get("error") or "worker failed")


def _export_case(out_dir: Path, word_key: str, label: str, ssml: str, voice_label: str, voice_name: str) -> dict:
    safe = _short_word_key(word_key)
    suffix = _clean_label(label)
    file_path = out_dir / f"google_{voice_label}_{safe}_{suffix}.m4a"
    started = time.monotonic()
    _print(f"START {voice_label} {word_key} {label}")
    _run_google_request(ssml, voice_name, file_path, REQUEST_TIMEOUT_SECONDS)
    duration_ms = _duration_ms(file_path)
    wall_ms = int((time.monotonic() - started) * 1000)
    _print(f"DONE {voice_label} {word_key} {label} duration={duration_ms}ms wall={wall_ms}ms")
    return {
        "word": word_key,
        "voice": voice_name,
        "label": label,
        "ssml": ssml,
        "status": "ok",
        "duration_ms": duration_ms,
        "wall_ms": wall_ms,
        "file": str(file_path),
    }


def build_candidates(out_dir: Path) -> list[dict]:
    tasks: list[tuple[str, str, str, str, str]] = []
    for word_key, word in WORDS.items():
        for voice_label, voice_name in GOOGLE_VOICES.items():
            raw_ssml = f"<speak>{word}</speak>"
            tasks.append((word_key, "raw", raw_ssml, voice_label, voice_name))
            for candidate_label, ph_value in PHONEME_CANDIDATES[word]:
                ssml = f'<speak><phoneme alphabet="pinyin" ph="{ph_value}">{word}</phoneme></speak>'
                tasks.append((word_key, candidate_label, ssml, voice_label, voice_name))

    config = _load_app_config()
    google_mode = "api_key" if (config.get("GOOGLE_TTS_API_KEY") or os.environ.get("GOOGLE_TTS_API_KEY")) else "adc"
    _print(f"Python executable: {sys.executable}")
    _print(f"Output dir: {out_dir}")
    _print(f"Candidate count: {len(tasks)}")
    _print(f"Google credential mode: {google_mode}")

    rows: list[dict] = []
    success = 0
    failed = 0
    total = len(tasks)
    for index, (word_key, label, ssml, voice_label, voice_name) in enumerate(tasks, start=1):
        prefix = f"[{index}/{total}] google / {voice_label} / {word_key} / {label}"
        _print(f"{prefix} START")
        try:
            row = _export_case(out_dir, word_key, label, ssml, voice_label, voice_name)
            rows.append(row)
            success += 1
            _print(f"{prefix} DONE duration={row['duration_ms']}ms file={row['file']}")
        except Exception as exc:
            failed += 1
            error = str(exc)
            rows.append({
                "word": word_key,
                "voice": voice_name,
                "label": label,
                "ssml": ssml,
                "status": "error",
                "error": error,
                "duration_ms": -1,
                "wall_ms": -1,
                "file": "",
            })
            _print(f"{prefix} ERROR: {error}")

    payload = {
        "success": success,
        "failed": failed,
        "output_dir": str(out_dir),
        "items": rows,
    }
    _write_summary(payload, out_dir)
    _print(f"COMPLETE success={success} failed={failed} output={out_dir}")
    return rows


def main() -> None:
    parser = argparse.ArgumentParser(description="Export Google Cloud phoneme candidates for Mandarin erhua words.")
    parser.add_argument("--out-dir", default="pron_test_phoneme", help="Output directory for candidate audio")
    args = parser.parse_args()

    out_dir = Path(args.out_dir).resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    build_candidates(out_dir)


if __name__ == "__main__":
    main()