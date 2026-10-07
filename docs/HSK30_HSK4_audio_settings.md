# HSK4 3.0 Audio

Status: approved by manual listening; batch export enabled for HSK4-30-01
through HSK4-30-17. Output names: hsk4-30-001.m4a through hsk4-30-017.m4a.

Content patch: six missing Part2 shared-group cues added. Golden HSK4-30-01
manually approved at 17:08.91, speed 0.95x. Batch may now use the patched
assembly. No batch was run during this patch or approval.

## Source and Structure

- Default source: `testdata/hsk30/hsk4/xuat audio hsk 4 đúng (1).xlsx`.
- Required columns: `Mã đề`, `Phần`, `Nhóm câu`, `Câu`, `Nội dung phần nghe`.
- Each exam: 32 questions, Part 1 = 14 and Part 2 = 18.
- Each question plays once; no repeat gap.
- Part 2 groups come from `Nhóm câu`. Shared stimulus plays once before
  the first question number; later group questions contain no stimulus.
- Number and question use the female narrator. Dialogue follows the explicit
  male/female labels in Excel. Embedded Arabic question markers are stripped.

## Approved Profile

- Default speech rate: 0.95x, adjustable in the app to 0.90x or 1.00x.
- Google voices used for golden: female `cmn-CN-Standard-A`,
  male `cmn-CN-Standard-C`. Engine, credentials and voices come from Lang.
- Generated speech only: `edge_trim_v1`, threshold -45 dB, safety pad 80 ms.
- Music to intro: 1020 ms; intro to part: 930 ms; part start: 3140 ms.
- Number to content: 1200 ms; speaker handoff: 610 ms.
- Content to question: 2040 ms; shared stimulus to number: 2240 ms.
- Shared-group number to question: 900 ms; answer pause: 16180 ms.
- Common music, HSK4 intro/part1/part2 assets and common outro are retained.
- Fixed cues before groups 20-21, 22-23, 24-25, 26-27, 28-29, 30-32:
  `hsk4_cue_q20_21.m4a`, `hsk4_cue_q22_23.m4a`, `hsk4_cue_q24_25.m4a`,
  `hsk4_cue_q26_27.m4a`, `hsk4_cue_q28_29.m4a`, `hsk4_cue_q30_32.m4a`.
- Cues already include trailing silence; append the shared passage directly
  with zero extra cue gap. Q15-19 receive no additional group cues.
- Validate all nine HSK4 assets at preview/export; missing cues are errors.
- No extra answer-card silence; duration references are informational only.
- Output: M4A, AAC-LC, 48000 Hz, mono, 32 kbps.

## Export

Select HSK4, import the saved workbook, review the preview and export a single
exam or batch. Batch skips existing nonempty output files. For changed Excel,
voices or speed, use single export to rebuild a file, or select a new batch
output folder. Cache includes speech settings and processing version.
Use a new batch folder after the fixed-cue patch: old nonempty outputs may
otherwise be skipped. Speech cache keys are unchanged and are reused.
Restart the app to load the patch, select HSK4 at 0.95x, import the intended
workbook and approve its preview. Export the batch into a new/empty folder,
for example `testdata/hsk30/hsk4/Audio_v2`.

Exports are written to a temporary file and probed before replacing the final
output. Generated audio and local TTS cache are excluded from this commit.
