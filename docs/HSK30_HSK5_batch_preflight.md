# HSK5 Batch Preflight

Historical v1 preflight. The current approved release uses fixed assets v2
and the repo source `testdata/hsk30/hsk5/xuat_audio_hsk5-1.xlsx`.
See `HSK30_HSK5_audio_settings.md` for the current batch workflow.

Revised source verified:
`/Users/vungocnguyenanh/Documents/Data_hsk/hsk5-30/xuat_audio_hsk5-1.xlsx`.
This file is outside the repo. The original repo workbook was not replaced.
Only HSK5_30_10 Q24 and Q27 differ from the original workbook.

## Results

- 17/17 exams: parser/dry-run PASS, 595 rows total.
- Every exam: 35 questions, Part1 19, Part2 16, 10 independent items.
- Shared groups: 11-12, 13-14, 15-16, 17-19, 20-21, 22-23,
  24-26, 27-29, 30-32, 33-35.
- Each shared group's first row owns its stimulus; later rows are question-only.
- Final question required; empty/malformed speaker segments rejected.
- Parser selects the LAST question marker. Independent regression fixtures retain
  internal director/student question markers, both with and without quote marks.
- Dynamic dialogues with 2/3/4/5 turns PASS.
- Builder timeline: shared passage once, each number/question once,
  35 answer pauses, no repeat gap, cue once, outro once.
- Full regression suite: 115 tests PASS, using the revised source for HSK5 tests.
- HSK5-specific suite: 12 tests PASS on both original and revised workbooks.
- Compile and whitespace checks PASS.
- Live TTS requests in this verification: 0. No live batch was started.

## Spot Checks

- HSK5_30_03 Q17-19: Q17 owns one female narration segment; Q18/Q19 have no stimulus.
- HSK5_30_10 Q24/Q27: internal question markers remain in stimulus; final exam
  questions are respectively about story adjustments and what the teacher introduced first.
- HSK5_30_17: all 35 questions and all 10 shared groups PASS.
- Exact timeline assertions cover both two-question and three-question groups.

## Unchanged Golden Profile

Speed 1.00x; Google female cmn-CN-Standard-A, male cmn-CN-Standard-C;
edge_trim_v1, threshold -45 dB, safety pad 80 ms;
Part1 answer pause 14900 ms, Part2 15860 ms.
Fixed assets, pronunciation rules and HSK1-4 profiles unchanged.
AAC-LC, 48000 Hz, mono, 32 kbps. No forced duration or trailing-silence padding.

## App Batch

Restart the app, select HSK5 and import the revised source above.
Verify speed 1.00x and approve the preview. Choose a new/empty output folder,
then run Batch HSK5 to produce hsk5-30-001.m4a through hsk5-30-017.m4a.

Batch validates the entire workbook before TTS, logs individual failures and
continues other exams. It saves hsk5-batch.report.json after every exam and
shows the requested per-exam statistics table. Resume skips only outputs with
matching source/settings/processing identity, audio hash and probed duration.
Unverified or stale existing files are never silently overwritten.

Final-file listening spot checks remain for after the user's live batch.
