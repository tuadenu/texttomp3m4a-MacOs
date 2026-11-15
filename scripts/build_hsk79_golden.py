"""Validate all 17 HSK79 exams; live mode builds only HSK79-01."""
import argparse
import json
import re
import shutil
import statistics
import struct
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from build_hsk5_golden import load_lang_state
from pipelines import hsk79_audio as audio


def audit_final_audio(result):
    ffmpeg = shutil.which("ffmpeg") or str(ROOT / "ffmpeg_bin/bin/ffmpeg")
    measured = subprocess.run([
        ffmpeg, "-hide_banner", "-i", result["output_file"], "-af", "silencedetect=noise=-45dB:d=0.035",
        "-f", "null", "-"], capture_output=True, text=True, check=True)
    intervals = []
    start = None
    for line in measured.stderr.splitlines():
        found_start = re.search(r"silence_start: ([0-9.]+)", line)
        found_end = re.search(r"silence_end: ([0-9.]+)", line)
        if found_start:
            start = float(found_start[1]) * 1000
        if found_end and start is not None:
            intervals.append((start, float(found_end[1]) * 1000))
            start = None
    rows = []
    for event in result["events"]:
        if event["type"] not in {"work_pause", "answer_pause", "number_to_question_gap", "passage_to_number_gap", "speaker_gap"}:
            continue
        midpoint = (event["start_ms"] + event["end_ms"]) / 2
        interval = next((value for value in intervals if value[0] <= midpoint <= value[1]), None)
        if interval is None:
            raise ValueError(f"Final audio has no detected silence at {event}")
        rows.append({"type": event["type"], "question": event.get("question"), "group": event.get("group"),
                     "configured_ms": event["end_ms"] - event["start_ms"],
                     "actual_ms": round(interval[1] - interval[0], 3),
                     "silence_start_ms": interval[0], "silence_end_ms": interval[1]})
    summary = {}
    for kind in sorted({row["type"] for row in rows}):
        selected = [row for row in rows if row["type"] == kind]
        summary[kind] = {"count": len(selected), "configured_median_ms": statistics.median(row["configured_ms"] for row in selected),
                         "actual_median_ms": statistics.median(row["actual_ms"] for row in selected),
                         "actual_min_ms": min(row["actual_ms"] for row in selected),
                         "actual_max_ms": max(row["actual_ms"] for row in selected)}
    decoder = subprocess.Popen([ffmpeg, "-v", "error", "-xerror", "-i", result["output_file"],
                                "-f", "s16le", "-ac", "1", "-ar", "48000", "-"],
                               stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    peak = saturated = samples = 0
    while True:
        chunk = decoder.stdout.read(262144)
        if not chunk:
            break
        for (value,) in struct.iter_unpack("<h", chunk):
            peak = max(peak, abs(value))
            saturated += value in {-32768, 32767}
            samples += 1
    stderr = decoder.stderr.read().decode("utf-8", errors="replace")
    if decoder.wait() != 0:
        raise RuntimeError(f"Golden decode failed: {stderr}")
    return {"threshold_db": -45, "gap_summary": summary, "gap_measurements": rows,
            "decode": "PASS", "peak_pcm16": peak, "saturated_samples": saturated, "sample_count": samples}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, default=audio.HSK30_HSK79_SOURCE)
    parser.add_argument("--live", action="store_true")
    args = parser.parse_args()
    source = args.source.resolve()
    structure = audio.validate_hsk79_workbook(source)
    if structure["status"] != "PASS":
        raise ValueError("HSK79 dry-run FAIL: " + "; ".join(structure["invalid_rows"]))
    assets = audio.validate_hsk79_assets()
    if not assets["ok"]:
        raise FileNotFoundError(f"HSK79 assets FAIL: {assets}")
    for exam, summary in structure["exams"].items():
        assert summary["questions"] == 40 and summary["part_counts"] == {1: 10, 2: 12, 3: 18}
        assert len(summary["groups"]) == 7
        print(f"{exam}: PASS / 40 / 10-12-18 / 7 groups / TTS=0", flush=True)
    assert structure["stats"]["group_starts"] == len(structure["exams"]) * len(audio.HSK79_GROUPS)
    dry = {key: value for key, value in structure.items() if key != "parsed_items"}
    dry.update(source_path=str(source), source_sha256=audio._digest(source), asset_status=assets, tts_requests=0)
    audio.shared._write_hsk5_json(source.parent / "hsk79-dry-run.report.json", dry)
    print(json.dumps({"status": "PASS", "stats": structure["stats"], "speaker_labels": structure["speaker_labels"],
                      "assets": "11/11", "tts_requests": 0}, ensure_ascii=False), flush=True)
    if not args.live:
        return
    state = load_lang_state()
    state.update(speech_rate=1.00, hsk_speed_profile="HSK79")
    if (state["engine"] != "Google Cloud TTS" or state["zh_male_voice"] != "cmn-CN-Standard-C"
            or state["zh_female_voice"] != "cmn-CN-Standard-A"):
        raise ValueError("HSK79 golden requires the configured Google Standard-A/Standard-C production voices")
    preview = audio.parse_hsk79_phase1(source, lang_state=state)
    result = audio.build_hsk79_golden(preview, source.parent / "hsk79-001.m4a", lang_state=state,
                                      progress_callback=lambda text: print(text, flush=True))
    ffprobe = shutil.which("ffprobe") or str(ROOT / "ffmpeg_bin/bin/ffprobe")
    probe = json.loads(subprocess.check_output([
        ffprobe, "-v", "error", "-show_entries", "stream=codec_name,profile,sample_rate,channels,bit_rate",
        "-show_entries", "format=duration,size", "-of", "json", result["output_file"]], text=True))
    stream = probe["streams"][0]
    assert (stream["codec_name"], stream["profile"], stream["sample_rate"], stream["channels"]) == ("aac", "LC", "48000", 1)
    assert 0 < int(stream["bit_rate"]) <= 36_000
    assert abs(float(probe["format"]["duration"]) * 1000 - result["actual_duration_ms"]) < 50
    result["format_probe"] = probe
    print("HSK79-01: checking final waveform and audible gaps", flush=True)
    result["final_audio_audit"] = audit_final_audio(result)
    audio.shared._write_hsk5_json(Path(result["output_file"]).with_suffix(".report.json"), result)
    print(audio.format_hsk79_build_report(result), flush=True)
    print(json.dumps(result["final_audio_audit"]["gap_summary"], ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()
