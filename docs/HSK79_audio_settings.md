# HSK 7-9 Golden Settings

HSK79 uses `pipelines/hsk79_audio.py`. The approved HSK1-6 implementation in
`pipelines/hsk30_audio.py` is unchanged. Only existing TTS, pronunciation,
edge normalization, cache and atomic export helpers are reused.

## Sources

- Workbook: `testdata/hsk30/hsk79/file-xuat-audio-hsk79.xlsx`.
- Reference audio: `testdata/hsk30/hsk79/HSK(七-九级)样卷听力.mp3`.
- Reference PDF: `testdata/hsk30/hsk79/HSK_7-9_mau.pdf`, listening pages 1-6.
- Fixed assets: `assets/hsk30_audio/hsk79/`, including manifest and read-me.

All 17 exams have 40 questions, parts 10/12/18 and seven shared passages.
Groups are validated from the workbook's `Nhóm câu` column. The final blank-line
block contains the first statement/question; earlier blank lines stay inside
the passage. Embedded numeric prefixes must match column `Câu` and are removed.
Missing prefixes are allowed. Unclear blocks fail with exam/question/source row.

Part1 statements Q1-10 are metadata only, following the user's playback rule.
They and their individual numbers never enter TTS. Only two passages and two
work pauses play. Part2/3 play five passages and 30 questions/numbers once.
There are no repetitions or common HSK1-6 intro/outro assets.

## Explicit Voice Casting

`config/hsk79_audio.json` configures narrator and role voices separately by group.
This is provisional sample-style casting for golden listening approval.
The acoustic reference uses pitch measurements, not a verified role transcript.

| Group | Narration or role | Production voice |
| --- | --- | --- |
| 1-5 | Narration | Male Standard-C |
| 6-10 | Narration | Female Standard-A |
| 11-16 | Host/reporter | Female Standard-A |
| 11-16 | Expert/researcher/director/manager/responsible person/scholar | Male Standard-C |
| 11-16 | Separate narration paragraphs | Male Standard-C |
| 17-22 | 女 / explicitly configured 甲, 甲方 | Female Standard-A |
| 17-22 | 男 / explicitly configured 乙, 乙方 | Male Standard-C |
| 23-28 | Narration | Male Standard-C |
| 29-34 | Narration | Female Standard-A |
| 35-40 | Narration | Male Standard-C |
| 11-40 | Number and question | Female Standard-A |

Generic parties are configured casting choices, not genders inferred from names.
Reporter and host are explicitly aliased for the interview in HSK79-09.
The separate unlabelled paragraph in HSK79-12 uses its group's narration voice.
Natural colons such as `调查发现：` remain narration. Unmapped or ambiguous
short speaker labels fail validation; no voice is silently selected.

The golden requires Google Cloud TTS Standard-C/Standard-A configured in Lang.
Speed is HSK79-specific, initially 1.00x, with UI options 0.95/1.00/1.05.

## Rhythm

| Boundary | Inserted pause | Intended audible interval |
| --- | ---: | ---: |
| Part1 work block 1 | 74539 ms | Approximately 74.54 s |
| Part1 work block 2 | 74388 ms | Approximately 74.39 s |
| Answer after each Q11-40 | 13883 ms | Approximately 14043 ms between TTS speech edges |
| Passage to first number | 1940 ms | Approximately 2100 ms |
| Number to question | 907 ms | Approximately 1067 ms |
| Speaker change | 440 ms | Approximately 600 ms |
| Inline missing-word slot | 540 ms | Approximately 700 ms |

TTS edge_trim_v1 retains 80 ms on each side at -45 dB. Audible targets depend
on actual retained edges, and transitions to fixed assets use their supplied
natural boundaries. Same-speaker paragraphs and chunks receive no extra gap.
All seven group assets join the passage directly. Part1 post-passage assets
include their own leading quiet, so no gap is added before them.

81 terminal writing lines, including `答：` fields, are removed from spoken
questions. Four inline slots in HSK79-06 Q16/Q19/Q34/Q37 remain short pauses
with both surrounding texts preserved; answers are never filled in.
The inline-slot duration is a design baseline requiring later listening review.

## Golden Workflow

Run `.venv/bin/python scripts/build_hsk79_golden.py` for parser-only validation.
Run with `--live` to build only `HSK79-01` as `hsk79-001.m4a`. Batch is locked
in both the UI and golden builder. Await user approval before a batch extension.

Output is M4A AAC-LC, 48 kHz mono, nominal 32 kbps. AAC average bitrate can
decrease on long silence. Fixed assets are never trimmed or recut. The source's
20-second silent tail is not appended. Duration is informational, never a target.
Generated speech receives 3 dB assembly headroom to prevent AAC decoding
overshoot measured in the first golden encode. This is HSK79-only, after cache
retrieval; edge_trim_v1/cache entries and the fixed assets keep their original levels.

The sidecar records every fixed asset, semantic TTS chunk, voice, source row,
pause, group and timestamp. The CLI additionally decodes final audio, checks
PCM saturation and measures audible gaps at -45 dB. Original assets/workbook
hashes are verified unchanged. Cache text/voice/speed/engine/representation and
edge processing identities reuse the existing system without deleting entries.
