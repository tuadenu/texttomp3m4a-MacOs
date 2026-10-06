# HSK5 3.0 Audio

Status: fixed assets v2 golden manually approved. HSK5 batch is enabled.
Approved golden: HSK5_30_01, 23:50.19 at 1.00x. HSK1-4 behavior is unchanged.

Current source: `testdata/hsk30/hsk5/xuat_audio_hsk5-1.xlsx`.
The original workbook remains available; import the intended source explicitly.
17 exams, `HSK5_30_01` through `HSK5_30_17`; 35 questions per exam,
Part1 = 19, Part2 = 16. Output: `hsk5-30-001.m4a` through `hsk5-30-017.m4a`.

The shared-group renderer is common with HSK4, using separate level profiles.
HSK5 parses the final `问：` as exam metadata, preserving internal narrative
questions. Dynamic `男：` / `女：` order comes from Excel. Shared stimulus plays
once per group; each number and exam question plays once. Number comes from
`Câu`; number and final question use female voices from Lang.

Part1 unprefixed narration fallback is female. Part2 unprefixed mapping:
20–21 male, 22–23 male, 24–26 female, 27–29 female, 30–32 male, 33–35 female.
Explicit dialogue labels override this fallback.

Initial speed is 1.00x; app offers 0.95x, 1.00x and 1.05x independently.
Current golden uses Google voices cmn-CN-Standard-A (female) and
cmn-CN-Standard-C (male). Credentials/voices/engine remain from Lang.

Generated speech uses existing `edge_trim_v1`, -45 dB threshold and 80 ms safety
pads. No internal pauses or fixed assets are trimmed. Cache identity and
pronunciation processing remain shared; optional edge metrics record raw and
trimmed durations without changing audio processing.

Timing in milliseconds:

| Boundary | Value |
| --- | ---: |
| Music to intro | 1020 |
| Intro to part1 | 0 |
| Part1 start | 3150 |
| Part2 asset to shared stimulus 20-21 | 0 |
| Cue to stimulus | 0 |
| Independent number to content | 1150 |
| Speaker handoff | 650 |
| Independent content to question | 2020 |
| Shared stimulus to number | 2250 |
| Shared number to question, Part1 | 740 |
| Shared number to question, Part2 | 760 |
| Answer pause, Part1 | 14900 |
| Answer pause, Part2 | 15860 |

All HSK5 fixed assets resolve directly from
`assets/hsk30_audio/hsk5/hsk5_30_fixed_assets_v2/`, with no v1 fallback.
The obsolete `hsk5_cue_q11_19.m4a` is retired.

| Before shared group | Fixed asset |
| --- | --- |
| 11-12 | hsk5_cue_q11_12.m4a |
| 13-14 | hsk5_cue_q13_14.m4a |
| 15-16 | hsk5_cue_q15_16.m4a |
| 17-19 | hsk5_cue_q17_19.m4a |
| 20-21 | hsk5_part2.m4a (includes group cue) |
| 22-23 | hsk5_cue_q22_23.m4a |
| 24-26 | hsk5_cue_q24_26.m4a |
| 27-29 | hsk5_cue_q27_29.m4a |
| 30-32 | hsk5_cue_q30_32.m4a |
| 33-35 | hsk5_cue_q33_35.m4a |

Each cue and the v2 Part2 asset contains natural trailing silence: append the
shared stimulus directly, with zero additional builder gap. Answer pauses
before the fixed assets are preserved. Intro and Part1 use their v2 files;
Part1 start gap, common music and common outro are unchanged. No assets are
trimmed or rewritten. No repeat gap or trailing padding is added.
Output remains AAC-LC, 48000 Hz, mono, 32 kbps in M4A.

Duration references are informational: official ~25:00, sample 26:31.98,
meaningful sample ~26:11.7. Duration does not drive pause or speed adjustment.

`scripts/build_hsk5_golden.py` runs a zero-TTS dry-run by default. `--live`
builds only HSK5_30_01, writes a detailed report and audits the actual builder
timeline. `scripts/build_hsk5_batch.py --source <xlsx>` dry-runs all 17 exams
without TTS. Add `--live` to build the approved v2 batch at 1.00x.

Import the revised workbook explicitly in the app before batch. The final
verification uses `testdata/hsk30/hsk5/xuat_audio_hsk5-1.xlsx`; the original
repo workbook is not edited or silently replaced. Batch writes a report after
each exam, continues after individual failures, and verifies exact once-only
playback/group ownership. Existing audio is skipped only with matching source,
settings/processing identity, audio hash and probed duration. Unverified or stale
outputs are not overwritten: choose a new folder such as `Audio_v2` when
rebuilding with v2 fixed assets or revised data. Restart the app after updating
code, import `xuat_audio_hsk5-1.xlsx`, select HSK5 at 1.00x, approve its preview,
then use the existing batch button. No batch was run as part of approval.
