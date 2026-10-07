"""Validate HSK4 cues and optionally rebuild only HSK4-30-01 at approved settings."""
import argparse
import hashlib
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from pipelines.hsk30_audio import (
    HSK30_HSK4_SOURCE, HSK30_HSK4_ASSETS, HSK4_AUDIO_PROFILE,
    HSK30_COMMON_ASSET, HSK30_COMMON_OUTTRO, _probe_audio_duration_ms,
    parse_hsk4_phase1, build_hsk4_phase1b, format_hsk4_build_report,
)
from build_hsk5_golden import load_lang_state


def asset_hashes(assets):
    return {key: hashlib.sha256(path.read_bytes()).hexdigest() for key, path in assets.items()}


def audit_timeline(result):
    timeline = result["timeline"]
    assert all(timeline.count(f"number{n}") == 1 and timeline.count(f"question{n}") == 1 for n in range(1, 33))
    assert timeline.count("answer_pause") == 32
    assert result["repeat_gap_ms"] == 0 and "repeat_gap" not in timeline
    assert timeline.count("outro") == 1 and timeline[-1] == "outro"
    block = timeline[timeline.index("hsk4_part2"):timeline.index("hsk4_cue_q20_21")]
    assert not any("cue_" in token for token in block)
    assert [token for token in block if token.startswith("number")] == [f"number{n}" for n in range(15, 20)]
    boundaries = result["fixed_asset_boundaries"]
    assert len(boundaries) == 6
    for boundary, (number, key) in zip(boundaries, HSK4_AUDIO_PROFILE["cues_before_question"].items()):
        cue = f"hsk4_{key}"
        group = key.removeprefix("cue_q").replace("_", "–")
        passage = f"shared_passage_{group}"
        assert boundary["asset"] == cue and boundary["group"] == group
        assert timeline.count(cue) == 1 and timeline.count(passage) == 1
        position = timeline.index(passage)
        assert timeline[position - 3:position] == [f"question{number - 1}", "answer_pause", cue]
        sequence = [passage, "shared_passage_to_number_gap"]
        first, last = map(int, group.split("–"))
        for question in range(first, last + 1):
            sequence += [f"number{question}", "number_to_question_gap", f"question{question}", "answer_pause"]
        assert timeline[position:position + len(sequence)] == sequence
        assert boundary["manual_gap_ms"] == 0
        assert boundary["asset_end_ms"] == boundary["passage_start_ms"]
    assert "cue_to_stimulus_gap" not in timeline
    return {"questions_once": True, "answer_pauses_32": True, "six_cues_once": True,
            "passage_once_per_group": True, "no_manual_cue_gap": True,
            "q15_19_no_extra_cues": True, "outro_once": True}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--live", action="store_true")
    args = parser.parse_args()
    state = load_lang_state()
    state.update(speech_rate=0.95, hsk_speed_profile="HSK4")
    assert state["engine"] == "Google Cloud TTS"
    assert state["zh_female_voice"] == "cmn-CN-Standard-A"
    assert state["zh_male_voice"] == "cmn-CN-Standard-C"
    preview = parse_hsk4_phase1(HSK30_HSK4_SOURCE, exam_code="HSK4-30-01", lang_state=state)
    assert preview["status"] == "READY_FOR_REVIEW" and preview["asset_status"]["ok"]
    assert preview["question_count"] == 32 and preview["part_counts"] == {1: 14, 2: 18}
    assert preview["shared_group_count"] == 6
    assets = {"music": HSK30_COMMON_ASSET, **HSK30_HSK4_ASSETS, "outro": HSK30_COMMON_OUTTRO}
    hashes = asset_hashes(assets)
    durations = {key: _probe_audio_duration_ms(path) for key, path in assets.items()}
    print(json.dumps({"dry_run": "PASS", "exam": preview["detected_exam"], "questions": 32,
                      "parts": preview["part_counts"], "shared_groups": 6,
                      "asset_durations_ms": durations, "tts_requests": 0}), flush=True)
    if not args.live:
        return
    output = HSK30_HSK4_SOURCE.parent / "hsk4-30-001.m4a"
    result = build_hsk4_phase1b(preview, output, lang_state=state,
                              progress_callback=lambda message: print(message, flush=True))
    result["timeline_checks"] = audit_timeline(result)
    assert hashes == asset_hashes(assets), "Fixed assets modified"
    result.update(asset_sha256=hashes, asset_durations_ms=durations,
                  asset_mapping={key: str(path) for key, path in assets.items()},
                  voice_mapping={key: state[key] for key in ("zh_male_voice", "zh_female_voice", "zh_narrator_voice")})
    output.with_suffix(".report.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    text_report = format_hsk4_build_report(result)
    (ROOT / "docs" / "HSK30_HSK4_golden_cues_report.md").write_text(
        "# HSK4 Fixed Cues Golden\n\nContent patch only. Pending manual listening; no batch run.\n\n```text\n"
        + text_report + "```\n\nTimeline checks: " + json.dumps(result["timeline_checks"])
        + "\n\nAsset hashes unchanged (including original intro/part1/part2).\n",
        encoding="utf-8")
    print(text_report, flush=True)


if __name__ == "__main__":
    main()
