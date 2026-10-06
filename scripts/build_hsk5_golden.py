"""Dry-run HSK5 and optionally build only its first exam with current Lang settings."""
import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from pipelines.hsk30_audio import (
    HSK30_HSK5_SOURCE, HSK30_HSK5_ASSETS, HSK30_COMMON_ASSET, HSK30_COMMON_OUTTRO,
    _probe_audio_duration_ms, parse_hsk5_phase1, build_hsk5_phase1b,
    format_hsk5_build_report,
    audit_hsk5_timeline, _hsk5_file_digest,
)


def audit_timeline(report):
    return audit_hsk5_timeline(report)


def load_lang_state():
    config = json.loads((ROOT / "AppData" / "config.json").read_text(encoding="utf-8"))
    slots = config.get("GOOGLE_TTS_PROFILES", {}).get("zh", {}).get("slots", {})
    return {
        "engine": config.get("TTS_ENGINE_SELECTED", "Google Cloud TTS"),
        "speech_rate": 1.00, "hsk_speed_profile": "HSK5",
        "zh_male_voice": slots.get("Nam", {}).get("voice_name", ""),
        "zh_female_voice": slots.get("Nữ", {}).get("voice_name", ""),
        "zh_narrator_voice": slots.get("Nữ", {}).get("voice_name", ""),
        "google_api_key": config.get("GOOGLE_TTS_API_KEY", ""),
        "aws_access_key_id": config.get("AWS_ACCESS_KEY_ID", ""),
        "aws_secret_access_key": config.get("AWS_SECRET_ACCESS_KEY", ""),
        "aws_region": config.get("AWS_REGION", ""),
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--live", action="store_true", help="Build ONLY HSK5_30_01 after dry-run passes")
    parser.add_argument("--source", type=Path, default=HSK30_HSK5_SOURCE)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    state = load_lang_state()
    preview = parse_hsk5_phase1(args.source, lang_state=state)
    assert preview["status"] == "READY_FOR_REVIEW" and preview["asset_status"]["ok"]
    assert preview["question_count"] == 35 and preview["part_counts"] == {1: 19, 2: 16}
    assert preview["independent_count"] == 10 and preview["shared_group_count_by_part"] == {1: 4, 2: 6}
    assets = {"music": HSK30_COMMON_ASSET, **HSK30_HSK5_ASSETS, "outro": HSK30_COMMON_OUTTRO}
    asset_durations = {key: _probe_audio_duration_ms(path) for key, path in assets.items()}
    asset_hashes = {key: _hsk5_file_digest(path) for key, path in assets.items()}
    print(json.dumps({"dry_run": "PASS", "exams": preview["structure"]["exam_count"],
                      "rows": preview["structure"]["row_count"], "questions": 35,
                      "part_counts": preview["part_counts"], "shared_groups_by_part": preview["shared_group_count_by_part"],
                      "independent_turns": preview["structure"]["independent_turn_counts"],
                      "asset_durations_ms": asset_durations, "dry_run_tts_requests": 0,
                      "engine": state["engine"], "female": state["zh_female_voice"], "male": state["zh_male_voice"]}, ensure_ascii=False), flush=True)
    if not args.live:
        return
    output = args.output or args.source.parent / "hsk5-30-001.m4a"
    result = build_hsk5_phase1b(preview, output, lang_state=state,
                              progress_callback=lambda message: print(message, flush=True))
    result["timeline_checks"] = audit_timeline(result)
    result["asset_durations_ms"] = asset_durations
    result["asset_mapping"] = {key: str(path) for key, path in assets.items()}
    result["asset_sha256"] = asset_hashes
    assert asset_hashes == {key: _hsk5_file_digest(path) for key, path in assets.items()}, "Fixed assets changed"
    result["voice_mapping"] = {key: state[key] for key in ("zh_male_voice", "zh_female_voice", "zh_narrator_voice")}
    result["question_voice_audit"] = [
        {"question": item["question_number"], "group": item["group"],
         "stimulus_speakers": [segment["speaker_label"] for segment in item["stimulus_segments"]],
         "question_speaker": item["question_segment"]["speaker_label"],
         "number_spoken": item["question_number_spoken"]}
        for item in preview["preview_items"]
    ]
    output.with_suffix(".report.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    (ROOT / "docs" / "HSK30_HSK5_golden_report.md").write_text(
        "# HSK5 Golden QA\n\n```text\n" + format_hsk5_build_report(result) + "```\n\n"
        + "Timeline checks: " + json.dumps(result["timeline_checks"], ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    print(format_hsk5_build_report(result), flush=True)


if __name__ == "__main__":
    main()
