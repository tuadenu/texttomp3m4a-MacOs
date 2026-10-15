# HSK6 3.0 Golden Phase

HSK6 only; approved HSK1-5 settings and speech/cache processing remain unchanged.
The user approved HSK6-30-01 by listening; resumable HSK6 batch export is enabled.

## Source And Parser

Use the final `02 xuất mp3 hsk6 30.xlsx` in
`testdata/hsk30/hsk6/`, without editing or renaming it.
Required columns: exam, skill, part, item type, question group, number, listening content.
Headers are resolved by name. Both `Nội dung nghe` and the legacy
`NỘI DUNG PHẦN NGHE` are accepted only in the HSK6 column adapter.
Part1 accepts both the label with and without the initial word `Nghe`.

18 exams, 720 rows, 40 items each, parts 8/20/12.
Part1 group label 1-8 means eight independent narrations, NOT one shared passage.
Part1 speaks number + complete narration, with no separate exam question.

Part2/3 have nine shared groups. The owner row splits at its LAST Chinese full stop.
The full stop remains in the passage; the remaining final question must end with
a Chinese question mark. Whitespace at the boundary is optional. Later rows must
contain only a question. Ambiguous/malformed content fails with source row.
No split on `问` or on an interviewer's internal question mark is used.

Part3's final `Phỏng vấn – Nghe phỏng vấn, chọn đáp án đúng` type parses ten
`主持人：`/`嘉宾：` lines after separating the final exam question. The roles must
alternate five times. Source turn punctuation and order are preserved; role
labels are not sent to TTS. Unlabelled paragraphs, missing turns, unclear voice
mapping and empty/incomplete turns fail with exam/question/source row.

The final workbook has 54 interviews with 10 turns each, 540 turns total
(270 male / 270 female). It has 162 shared owners and 414 later question rows.
44 owner rows have no whitespace at the passage/question boundary.

## Voices And Speed

Baseline 1.00x; popup also exposes 1.03x and 1.05x for later manual comparison.
Voices come from Lang: female Standard-A, male Standard-C for the golden.
Number/question use the selected female voice, independently of narrator override.
Part1 alternates male/female starting male. Part2 shared narration follows:

| Group | Voice |
| --- | --- |
| 9-11 | Female |
| 12-14 | Male |
| 15-17 | Female |
| 18-21 | Male |
| 22-25 | Female |
| 26-28 | Male |

The approved role mapping follows the supplied full HSK6 sample. Group 29-32
uses a male host and female guest; group 33-36 uses a female host and male
guest; group 37-40 uses a male host and female guest.

| Interview group | 主持人 | 嘉宾 |
| --- | --- | --- |
| 29-32 | Male Standard-C | Female Standard-A |
| 33-36 | Female Standard-A | Male Standard-C |
| 37-40 | Male Standard-C | Female Standard-A |

The old Part3 narration mapping remains only as a legacy non-interview fallback.

## Timing

Generated speech reuses edge_trim_v1, -45 dB, 80 ms safety padding each edge.
Internal linguistic pauses are preserved. No fixed asset or final file is trimmed.
No repeat2, no repeat gap, no final silence padding.
No additional speaker gap was introduced: the existing HSK6 configured value
remains 0 ms. Each generated turn retains its normal 80 ms safety edges.

| Boundary | Configured ms | Approximate audible target ms |
| --- | ---: | ---: |
| Part1 answer | 14009 | 14169 |
| Part2 answer | 13791 | 13951 |
| Part3 answer | 13864 | 14024 |
| Number to narration | 1190 | 1350 |
| Shared passage to number | 2290 | 2450 |
| Number to question | 490 | 650 |

Targets include both retained TTS safety edges, not a second manual padding step.
At transitions to fixed assets the next edge belongs to the asset, so audible
silence is subject to the supplied natural asset boundaries. No duration tuning.

13 HSK6 fixed assets are validated before export. Part assets and all nine cues
already include natural trailing silence and are followed directly, with ZERO
configured part-start or cue-to-passage gap. Common music and outro play once.
Manifest intro starts after common music, so there is no duplicate music/announcement.
Common music is 29s; initial music-to-intro transition is 1229ms, based on the
manifest's source intro timestamp 30.229s. Intro-to-Part1 is direct.

## Output And QA

M4A AAC-LC, 48 kHz mono, 32 kbps. Output names hsk6-30-001 through 018.
Only golden 001 is authorized for live build in this phase.

`scripts/build_hsk6_golden.py` dry-runs all 18 exams without TTS.
`--live` additionally builds only 001 and checks actual assembly timeline,
the dynamically derived semantic speech count, 40 answer pauses, nine direct cue boundaries,
one passage per group, one question per item, one outro, format and asset hashes.
Generated dry-run/build JSON reports are kept beside the source Excel.
All regressions must pass before live build. Manual listening remains required.
For final-source golden 001, 30 interview turns give 116 semantic segments total.
Cache keys already include normalized text, voice, engine, speed, language/mode
and edge_trim_v1. Changed text/voice gets a different key; no cache deletion.
HSK6 retries Google's transient `Failed to synthesize with voice` response up to
three attempts; other TTS errors still fail immediately. HSK1-5 use one attempt.
