"""Validate all HSK6 exams, then optionally run the approved resumable batch."""
import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from build_hsk5_golden import load_lang_state
from pipelines.hsk30_audio import (
    HSK30_HSK6_SOURCE,
    build_hsk6_batch,
    dry_run_hsk6_batch,
    format_hsk6_batch_report,
)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, default=HSK30_HSK6_SOURCE)
    parser.add_argument("--output-dir", type=Path, default=HSK30_HSK6_SOURCE.parent / "audio-hsk6-30")
    parser.add_argument("--live", action="store_true")
    args = parser.parse_args()
    state = load_lang_state()
    state.update(speech_rate=1.00, hsk_speed_profile="HSK6")
    if (state["engine"] != "Google Cloud TTS"
            or state["zh_female_voice"] != "cmn-CN-Standard-A"
            or state["zh_male_voice"] != "cmn-CN-Standard-C"):
        raise ValueError("Lang settings differ from the approved HSK6 production voices/engine")
    previews = dry_run_hsk6_batch(args.source, lang_state=state)
    print(json.dumps({"source": str(args.source.resolve()), "dry_run": "PASS", "tts_requests": 0,
                      "exams": [{"exam": p["detected_exam"], "questions": p["question_count"],
                                 "parts": p["part_counts"], "independent": p["independent_count"],
                                 "groups": p["shared_group_count"], "validation": "PASS"} for p in previews]},
                     ensure_ascii=False), flush=True)
    if not args.live:
        return
    results = build_hsk6_batch(args.source, args.output_dir, lang_state=state,
                               progress_callback=lambda message: print(message, flush=True))
    table = format_hsk6_batch_report(results)
    report = ("# HSK6 Approved Batch\n\nSource: `" + str(args.source.resolve())
              + "`\n\nSpeed: 1.00x. Timing/edge normalization unchanged.\n\n" + table + "\n")
    failures = [result for result in results if result["status"] == "FAIL"]
    for result in failures:
        report += f"\n{result['exam']}: {result['error']}\n"
    args.output_dir.mkdir(parents=True, exist_ok=True)
    (args.output_dir / "hsk6-batch.report.md").write_text(report, encoding="utf-8")
    print(table, flush=True)
    if failures:
        sys.exit(1)


if __name__ == "__main__":
    main()
