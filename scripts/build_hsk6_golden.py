"""Dry-run all HSK6 exams, then optionally build ONLY HSK6-30-01."""
import argparse
import hashlib
import json
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from build_hsk5_golden import load_lang_state
from pipelines.hsk30_audio import (
    HSK30_HSK6_SOURCE, HSK30_HSK6_ASSETS, HSK30_COMMON_ASSET, HSK30_COMMON_OUTTRO,
    HSK6_AUDIO_PROFILE, HSK6_BATCH_APPROVED, _probe_audio_duration_ms,
    parse_hsk6_phase1, build_hsk6_phase1b, format_hsk6_build_report,
)


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--live", action="store_true")
    parser.add_argument("--source", type=Path, default=HSK30_HSK6_SOURCE,
                        help="HSK6 workbook to validate and build from")
    args = parser.parse_args()
    state = load_lang_state()
    state.update(speech_rate=1.00, hsk_speed_profile="HSK6")
    assert state["engine"] == "Google Cloud TTS"
    assert state["zh_male_voice"] == "cmn-CN-Standard-C"
    assert state["zh_female_voice"] == "cmn-CN-Standard-A"
    assert HSK6_BATCH_APPROVED
    source = args.source.expanduser().resolve()
    if not source.is_file():
        parser.error(f"HSK6 source workbook not found: {source}")
    source_hash = digest(source)
    previews = []
    for index in range(1, 19):
        preview = parse_hsk6_phase1(source, exam_code=f"HSK6-30-{index:02d}", lang_state=state)
        assert preview["status"] == "READY_FOR_REVIEW" and preview["asset_status"]["ok"]
        assert preview["question_count"] == 40 and preview["part_counts"] == {1: 8, 2: 20, 3: 12}
        assert preview["independent_count"] == 8 and preview["shared_group_count"] == 9
        assert preview["structure"]["interview_groups"] == 54
        assert preview["structure"]["first_group_rows_split"] == 162
        assert preview["structure"]["later_question_rows"] == 414
        assert preview["structure"]["interview_turn_counts"] == {10: 54}
        assert preview["structure"]["interview_speaker_counts"] == {"NAM": 270, "NỮ": 270}
        previews.append(preview)
        print(f"Dry-run {index}/18: {preview['detected_exam']} PASS / 40 / 8-20-12 / 9 groups / TTS=0", flush=True)
    assets = {"music": HSK30_COMMON_ASSET, **HSK30_HSK6_ASSETS, "outro": HSK30_COMMON_OUTTRO}
    hashes = {key: digest(path) for key, path in assets.items()}
    manifest = json.loads((HSK30_HSK6_ASSETS["intro"].parent / "MANIFEST.json").read_text())
    for item in manifest["assets"]:
        assert digest(HSK30_HSK6_ASSETS["intro"].parent / item["file"]) == item["sha256"]
    durations = {key: _probe_audio_duration_ms(path) for key, path in assets.items()}
    dry_run = {
        "source_path": str(source), "source_sha256": source_hash, "status": "PASS", "tts_requests": 0,
        "exam_count": 18, "row_count": 720, "first_group_rows_split": 162,
        "later_question_rows": previews[0]["structure"]["later_question_rows"],
        "interview_groups": previews[0]["structure"]["interview_groups"],
        "interview_turn_counts": previews[0]["structure"]["interview_turn_counts"],
        "interview_speaker_counts": previews[0]["structure"]["interview_speaker_counts"],
        "interview_role_counts": previews[0]["structure"]["interview_role_counts"],
        "no_space_first_group_rows": previews[0]["structure"]["no_space_first_group_rows"],
        "asset_durations_ms": durations, "asset_sha256": hashes, "profile": HSK6_AUDIO_PROFILE,
        "narration_voice_mapping": HSK6_AUDIO_PROFILE["narration_group_voices"],
        "interview_role_voice_mapping": HSK6_AUDIO_PROFILE["interview_role_voices_by_group"],
        "exams": [{"exam": preview["detected_exam"], "questions": 40, "part_counts": preview["part_counts"],
                   "independent": 8, "shared_groups": 9, "errors": [], "assets_ok": True,
                   "proposed_timeline": preview["proposed_timeline"]} for preview in previews],
    }
    (source.parent / "hsk6-dry-run.report.json").write_text(json.dumps(dry_run, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({key: value for key, value in dry_run.items() if key != "exams"}, ensure_ascii=False), flush=True)
    if not args.live:
        return
    assert digest(source) == source_hash, "Source changed during dry-run"
    output = source.parent / "hsk6-30-001.m4a"
    result = build_hsk6_phase1b(previews[0], output, lang_state=state,
                              progress_callback=lambda message: print(message, flush=True))
    assert digest(source) == source_hash, "Source changed during build"
    assert hashes == {key: digest(path) for key, path in assets.items()}, "Fixed assets changed"
    ffprobe = shutil.which("ffprobe") or str(ROOT / "ffmpeg_bin" / "bin" / "ffprobe")
    probe = json.loads(subprocess.check_output([
        ffprobe, "-v", "error", "-show_entries", "stream=codec_name,profile,sample_rate,channels,bit_rate",
        "-show_entries", "format=duration,size", "-of", "json", str(output)], text=True))
    stream = probe["streams"][0]
    assert (stream["codec_name"], stream["profile"], stream["sample_rate"], stream["channels"]) == ("aac", "LC", "48000", 1)
    assert 30_000 <= int(stream["bit_rate"]) <= 34_000
    assert abs(float(probe["format"]["duration"]) * 1000 - result["actual_duration_ms"]) < 50
    assert result["metrics_available_segments"] == result["expected_speech_segments"]
    result.update(source_sha256=source_hash, asset_sha256=hashes, asset_durations_ms=durations,
                  asset_mapping={key: str(path) for key, path in assets.items()}, format_probe=probe,
                  source_columns=previews[0]["structure"]["source_columns"],
                  voice_audit=[{"question": item["question_number"], "source_row": item["source_row"],
                                "group": item["group"], "number_voice": state["zh_female_voice"],
                                "stimulus_speakers": [segment["speaker_label"] for segment in item["stimulus_segments"]],
                                "stimulus_turns": [{"speaker": segment["speaker_token"],
                                                    "voice": state["zh_male_voice"] if segment["speaker_label"] == "NAM" else state["zh_female_voice"],
                                                    "text": segment["source_text"]} for segment in item["stimulus_segments"]],
                                "question_source": item["question_source"],
                                "question_spoken": item["question_spoken"],
                                "question_voice": state["zh_female_voice"] if item["question_segment"] else None}
                               for item in previews[0]["preview_items"]])
    boundaries = {entry["group"]: entry for entry in result["fixed_asset_boundaries"]}
    result["interview_timeline"] = [
        {"group": item["group"], "question_number": item["question_number"],
         "source_row": item["source_row"],
         "cue_start_ms": boundaries[item["group"]]["asset_start_ms"],
         "cue_end_ms": boundaries[item["group"]]["asset_end_ms"],
         "passage_start_ms": boundaries[item["group"]]["passage_start_ms"],
         "passage_end_ms": boundaries[item["group"]]["passage_end_ms"],
         "added_gap_ms": boundaries[item["group"]]["manual_gap_ms"],
         "turns": [{"role": segment["speaker_token"], "speaker": segment["speaker_label"]}
                   for segment in item["stimulus_segments"]],
         "question_spoken": item["question_spoken"]}
        for item in previews[0]["preview_items"] if item["is_interview"] and item["stimulus_segments"]
    ]
    output.with_suffix(".report.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(format_hsk6_build_report(result), flush=True)


if __name__ == "__main__":
    main()
