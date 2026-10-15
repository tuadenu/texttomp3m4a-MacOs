# HSK6 Golden QA: 02 xuất mp3 hsk6 30.xlsx

Rebuilt HSK6-30-01 on 2026-10-15 and approved by manual listening.
Source Excel and supplied audio assets remain unchanged. Resumable batch is enabled.

## Source And Dry Run

- Final source: `testdata/hsk30/hsk6/02 xuất mp3 hsk6 30.xlsx` (NFD filename on disk).
- SHA256: `1baaa40d673503daa58466d6147a212a462f6d57b41fb31d886c39798ccb9642`.
- 18/18 exams PASS, 720 rows, 40 items/exam, parts 8/20/12.
- Part1: 8 independent narrations/exam, including Q1-8 of exam 01.
- Both Part1 type labels (with/without initial Nghe) are accepted.
- New Nội dung nghe and legacy NỘI DUNG PHẦN NGHE headers both pass.
- 162/162 shared owner rows split at the LAST Chinese full stop.
- 44 no-space boundaries pass, including source rows 10, 13, 16, 19, 23, 27.
- 54 interviews have exactly 10 alternating `主持人：`/`嘉宾：` turns each.
- All 540 turns preserved in source order: 270 male / 270 female.
- 414 later group rows are question-only; shared stimulus never repeats.
- The six corrected questions in exams 15 and 17 match the latest workbook and enter the TTS/cache path unchanged.
- Parser-only dry-run made zero live TTS requests.

Final exam questions are separated BEFORE tokenizing the interview.
Interviewer's internal question marks stay inside the passage. No split on
问 or whitespace. Source text/punctuation/order are retained; existing TTS
canonical punctuation normalization remains unchanged. Missing role lines,
unlabelled paragraphs and unclear voice mapping fail with exam/question/source row.

Approved mapping against the supplied full HSK6 sample: group 29-32 starts
with a male host followed by a female guest; group 33-36 starts with a female
host followed by a male guest; group 37-40 starts with a male host followed by
a female guest. This mapping is explicit in the HSK6 profile and regression tests.

## Golden Output

| Field | Result |
| --- | --- |
| Exam | HSK6-30-01 |
| Output | testdata/hsk30/hsk6/hsk6-30-001.m4a |
| Duration | 30:19.384 |
| File size | 7,644,429 bytes / 7.64 MB |
| Speed | 1.00x |
| FFprobe format | AAC-LC / 48 kHz / mono / approx. 32 kbps |
| Female / male | cmn-CN-Standard-A / cmn-CN-Standard-C |
| Number / exam question | Female Standard-A |
| Part1 | 8 independent narrations |
| Part2 | 6 shared narration groups |
| Part3 | 3 shared interviews, 10 turns/group |
| Interview turns in golden | 15 male + 15 female |
| Semantic speech segments | 116 |
| TTS new / cache reuse, successful run | 10 / 106 |
| Answer pauses | 40, configured total 554.260 s |
| Shared passage / cue / question | Once each |
| Repeat gaps | None |
| Common outro | Once |

Only golden 001 was live-built. No other exam audio was regenerated.
The preceding build with the reversed Q29-32 mapping was 30:16.393. Correcting
only those ten role voices changed the duration by +2.991 seconds.
No speed/timing adjustment or target-duration padding was performed.

## Exact Cue Positions

Cue end equals passage start. All nine cues appear exactly once, with ZERO
extra manual gap. Natural silence remains inside each supplied asset.

| Group | Asset | Cue start | Passage start | Manual gap |
| --- | --- | --- | --- | ---: |
| 9-11 | hsk6_cue_q9_11.m4a | 06:31.689 | 06:36.083 | 0 ms |
| 12-14 | hsk6_cue_q12_14.m4a | 08:50.641 | 08:55.729 | 0 ms |
| 15-17 | hsk6_cue_q15_17.m4a | 11:03.209 | 11:07.617 | 0 ms |
| 18-21 | hsk6_cue_q18_21.m4a | 13:16.706 | 13:21.859 | 0 ms |
| 22-25 | hsk6_cue_q22_25.m4a | 15:49.455 | 15:54.436 | 0 ms |
| 26-28 | hsk6_cue_q26_28.m4a | 18:32.018 | 18:37.404 | 0 ms |
| 29-32 | hsk6_cue_q29_32.m4a | 20:56.009 | 21:01.419 | 0 ms |
| 33-36 | hsk6_cue_q33_36.m4a | 24:00.301 | 24:05.205 | 0 ms |
| 37-40 | hsk6_cue_q37_40.m4a | 27:05.600 | 27:10.689 | 0 ms |

## Interview Timeline

| First question | Passage | Turns | 主持人 / 嘉宾 | First exam question |
| --- | --- | ---: | --- | --- |
| Q29 | 21:01.419-22:45.789 | 10 | Male / Female | 受访者最初为什么开始学习古籍修复？ |
| Q33 | 24:05.205-25:51.691 | 10 | Female / Male | 什么经历让受访者开始关注海底考古？ |
| Q37 | 27:10.689-28:58.486 | 10 | Male / Female | 受访者为什么开始从事无障碍电影解说？ |

Each passage follows its cue directly and precedes the first question number;
the remaining three questions in each group play without another passage.

## Unchanged Profile And Cache

All 13 fixed HSK6 assets match manifest hashes before/after build.
Common intro music and outro hashes are unchanged. No recut, fixed-asset trim,
or separate re-encode. edge_trim_v1, -45 dB, 80 ms safety pad unchanged.
All pauses and speed 1.00x unchanged. Part-start and cue-to-passage gap stay 0 ms.
The existing speaker gap remains 0 ms; no new manual turn pause.

Cache identity distinguishes text, voice, engine, speed, language/mode and
processing version. No cache deletion/version change. Old whole-passage clips
cannot substitute for a new turn with different text/voice. Identical unchanged
segments intentionally reuse the cache.
Google Standard voice errors observed during synthesis were retried only for
HSK6. The successful run reused clips cached by the earlier interrupted attempts.

| Duration category | Seconds |
| --- | ---: |
| Active speech span including internal linguistic pauses | 1049.696 |
| TTS speech including safety edges | 1068.263 |
| Removed TTS edge silence | 44.912 |
| Fixed intro/parts | 69.970 |
| Fixed cues | 44.813 |
| Answer pauses | 554.260 |
| Part1 items | 308.250 |
| Part2 items/groups/cues | 847.560 |
| Part3 items/groups/cues | 557.331 |

Part durations exclude their part-introduction assets. Duration status: INFO.

## Verification And App

- 138 tests PASS: 17 HSK6 tests plus 121 existing regressions.
- Tests cover 10-turn role parsing, internal interview questions, no-space split,
  all source turns/text/voices, six corrected questions, missing assets and invalid narration.
- Cache tests distinguish changed text/voice and reuse normalized clips.
- Assembly asserts 116 segments, passage once/group, question once/item,
  40 answer pauses, nine direct cue boundaries and one outro.
- Transient Google voice failure retry is covered without retrying malformed input.
- Saved JSON timeline audit supports both integer and string Part keys.
- HSK1-5 parsers, profiles, shared renderers, header aliases and TTS/cache/edge
  processing match their prior code. Existing regressions all PASS.
- Python compilation, app routing and batch approval checked.
- Manual listening approval completed.

Restart the app to load the updated source/parser. HSK6 single export is
available; HSK6 batch is enabled after explicit golden approval.
Per-turn voice/text audit, all speech metrics, exact timeline and source/asset
hashes: `testdata/hsk30/hsk6/hsk6-30-001.report.json`.

The approved golden is the baseline for the 18-exam batch.
