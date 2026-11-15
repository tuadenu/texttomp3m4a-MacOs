# HSK79-01 Golden QA

Only `HSK79-01` was built. Await manual listening approval. HSK79 batch remains
locked. The workbook, 11 fixed assets and all HSK1-6 audio settings are unchanged.

## Implementation

- `pipelines/hsk79_audio.py`: separate parser, role handling, semantic chunks,
  fixed-asset resolver, golden builder, timeline validation and reports.
- `config/hsk79_audio.json`: explicit group narration/role casting.
- `app.pyw`: HSK 7-9 level, preview, HSK79 speed settings, golden export and batch lock.
- `scripts/build_hsk79_golden.py`: dry-run all exams, build only exam 01, then
  inspect final codec, waveform peaks and audible pauses.
- `tests/test_hsk79_audio.py`: 14 parser/assembly/cache/UI regression tests.
- `.gitignore`: generated HSK79 audio, sidecars and Excel lock files.

The existing HSK1-6 module `pipelines/hsk30_audio.py` has no changes.

## Validation

| Check | Result |
| --- | --- |
| Exams / data rows | 17 / 680 PASS |
| Questions/exam | 40, ordered and unique |
| Parts | 10 / 12 / 18 |
| Shared groups/exam | 7 |
| Group starts | 119/119 PASS |
| Owners with internal blank lines | 35, full passage preserved |
| Missing embedded numeric prefixes | 78, both exams 10 and 12 remain complete |
| Writing-only answer fields | 81 removed from speech, including `答：` fields |
| Inline missing-word questions | HSK79-06 Q16/Q19/Q34/Q37 preserve both sides with a pause |
| Fixed assets | 11/11, manifest hashes match |
| Source edits / recuts / fixed-asset trims | None |
| Dry-run live TTS requests | 0 |

All 119 passages preserve every non-whitespace character except explicit
speaker metadata. The final blank-line block, not the first blank or first
question mark, separates the first statement/question from the passage.
Unknown or ambiguous speaker labels fail with exam, question and Excel row.
Natural narration colons remain text. Same-speaker narration paragraphs remain
one semantic segment; no additional paragraph pause is inserted.

Source workbook SHA256:
`3ed0d0fc7ae5b34728cbc3eb9d90b5246ed98e3be9370aeda1f6920f56de7424`.

## Golden Output

| Field | Result |
| --- | --- |
| File | `testdata/hsk30/hsk79/hsk79-001.m4a` |
| Duration | 34:37.545 |
| Size | 8,751,455 bytes / 8.75 MB |
| Format | AAC-LC, 48000 Hz, mono |
| Bitrate | 32195 bit/s measured, 32 kbps configured |
| Speed | 1.00x |
| Generated semantic speech | 104 segments, including 30 numbers and 30 questions |
| TTS in final assembly | 0 new / 104 cache reuse |
| Initial unique live synthesis | 104 segments |
| Active speech span, internal pauses retained | 1304.686 s |
| Generated speech with retained edges | 1321.326 s |
| TTS edge silence removed | 39.234 s |
| Fixed assets | 138.053 s |
| Two work pauses | 148.927 s configured total |
| Thirty answer pauses | 416.490 s configured total |
| Decode | PASS |
| Final PCM peak | 30418 / approximately -0.65 dBFS |
| Saturated PCM samples | 0 |
| Timeline/encoded duration difference | -7 ms |

The initial encode exposed AAC overshoot in generated speech, peaking at
approximately +1.86 dBFS on floating-point decode. HSK79 now applies 3 dB
headroom only to generated speech after retrieving normalized cache audio.
The final encode reuses all 104 cached segments. Canonical edge_trim_v1 entries,
TTS text, speed, timings and fixed assets keep their original configuration.

Reference MP3 duration is 31:37.539, including approximately 20.26 seconds of
trailing quiet. Golden duration is informational; no speed/pause tuning or
padding was used to match the reference duration.

## Voice Mapping In Golden

The mapping is provisional sample-style casting based on acoustic pitch
measurements and explicit corpus roles. It still needs listening approval.

| Group | Actual golden voices | Turns |
| --- | --- | ---: |
| 1-5 | Male Standard-C narration | 1 |
| 6-10 | Female Standard-A narration | 1 |
| 11-16 | Host Standard-A / expert Standard-C | 20: 10 female, 10 male |
| 17-22 | 女 Standard-A / 男 Standard-C, source order retained | 19: 10 female, 9 male |
| 23-28 | Male Standard-C narration | 1 |
| 29-34 | Female Standard-A narration | 1 |
| 35-40 | Male Standard-C narration | 1 |
| Numbers/questions Q11-40 | Female Standard-A | 30 numbers, 30 questions |

Role names do not determine gender. Generic role/party casting is explicit
in the config. A reporter/host alias in exam 09 and an unlabelled narration
tail after the interview in exam 12 were validated without synthesizing them.

## Fixed Asset Timeline

Each asset plays exactly once. The seven group guidance assets join their
passages directly with zero additional manual gap. Both Part1 after-assets
also follow their passages directly, preserving their supplied leading quiet.

| Asset | Start | End / next boundary |
| --- | --- | --- |
| hsk79_intro | 00:00.000 | 00:30.294 |
| hsk79_part1_q1_5 | 00:30.294 | Passage 1-5 at 01:20.375 |
| hsk79_part1_after_q1_5 | 03:47.085 | Work pause starts 03:51.145 |
| hsk79_cue_q6_10 | 05:05.684 | Passage 6-10 at 05:11.023 |
| hsk79_part1_after_q6_10 | 07:07.171 | Work pause starts 07:11.094 |
| hsk79_part2_q11_16 | 08:25.482 | Passage 11-16 at 08:37.183 |
| hsk79_cue_q17_22 | 13:59.354 | Passage 17-22 at 14:05.254 |
| hsk79_part3_q23_28 | 19:11.073 | Passage 23-28 at 19:23.338 |
| hsk79_cue_q29_34 | 24:27.201 | Passage 29-34 at 24:33.563 |
| hsk79_cue_q35_40 | 29:42.840 | Passage 35-40 at 29:49.021 |
| hsk79_outro | 34:35.605 | 34:37.552 assembly boundary |

Part1 Q1-10 numbers/statements are never synthesized. Part1 has two passage
playbacks and two work pauses. Part2/3 have five passage playbacks, 30 individual
question/number playbacks and 30 answer pauses. No repeat or common music/outro.

## Actual Audible Gaps

Measurements are taken from the final M4A at -45 dB, not inferred from profile
durations. Values include retained speech edges and natural asset boundaries.

| Boundary | Configured inserted silence | Actual median | Reference/target |
| --- | ---: | ---: | ---: |
| Answer pause, 30 | 13.883 s | 14.023 s | approximately 14.043 s |
| Number to question, 30 | 0.907 s | 1.049 s | approximately 1.067 s |
| Passage to first number, 5 | 1.940 s | 2.086 s | approximately 2.100 s |
| Speaker handoff, 36 | 0.440 s | 0.583 s | approximately 0.600 s |
| Part1 work pause 1 | 74.539 s | 74.539 s | approximately 74.54 s |
| Part1 work pause 2 | 74.388 s | 74.386 s | approximately 74.39 s |

The first work pause runs 03:51.145-05:05.684, the second
07:11.094-08:25.482. No manual cue-to-passage or passage-to-after-cue silence.
Source cues contain approximately 3.1 seconds of trailing quiet themselves.

## Regression And Listening Review

- Full repository suite: 152/152 PASS at the integration checkpoint, including
  the 139 prior tests. The final HSK79 suite passes 14/14 after the final changes.
- Python compilation and `git diff --check`: PASS.
- Original HSK1-6 pipeline source unchanged; no existing level audio regenerated.
- Asset/workbook hashes and exact timeline validation: PASS.
- Final complete decode, codec check, audible-gap measurement and PCM saturation
  check: PASS. The per-event audit is in the adjacent JSON sidecar.

Please listen to the host/guest casting, number/question narrator and speaker
cadence. The two after-cues are supplied proposed cuts and need an ear check
at 03:47.085 and 07:07.171. The first source asset begins with a short above-threshold
sound before its leading quiet; it was preserved byte-for-byte as instructed.
No authoritative role transcript was available in the reference PDF, so voice
casting has not been certified by transcription or manual listening.

Inline-slot behavior is parser/test-verified on exam 06 but is not present in
golden exam 01, so its 700 ms target remains a later listening check.

STOP after golden. No batch, upload or deployment was performed.
