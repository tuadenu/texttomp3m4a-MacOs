# HSK4 Fixed Cues Golden

Content patch only. Golden manually approved by the user; batch export may
proceed with the patched assembly. No batch run as part of approval.

- Full regression: 121 tests PASS, including HSK1-5.
- Final file: 17:08.91; 4324463 bytes; AAC-LC, 48000 Hz, mono, 32116 bps.
- Approved speech profile retained: 0.95x; edge_trim_v1, -45 dB, 80 ms.
- 32 questions (14/18), 32 answer pauses, six cues once, six shared passages once.
- Q15-19 receive no added group cues. Each cue-to-passage builder gap is 0 ms.
- New TTS requests: 0; cached segments reused: 112.
- All nine HSK4 asset SHA256 values are unchanged before/after the patch/build.

```text
Exam: HSK4-30-01
Output: /Users/vungocnguyenanh/Projects/texttomp3m4a-hsk-publish/testdata/hsk30/hsk4/hsk4-30-001.m4a
File size: 4324463 bytes
HSK4 speed: HSK4 chuẩn — 0.95x

Music: 29000 ms
Fixed intro/part assets: 55157 ms
Fixed group cues: 33315 ms
Generated speech: 278681 ms
Edge trim: edge_trim_v1 / 80 ms
Internal pauses: 100730 ms
Number to content: 22800 ms
Speaker gaps: 14030 ms
Content to question: 38760 ms
Shared passage to number: 13440 ms
Number to question: 11700 ms
Answer pauses: 517760 ms
Part transitions: 8230 ms
Final silence: 0 ms
Outro: 6044 ms

Part1 duration: 394623 ms
Part2 duration: 535859 ms

Independent items: 5
Shared groups: 6
Shared passage count: 6
Repetitions: 1
Unique TTS requests: 0
Cache reused: 112

Reference official: 1200000 ms (~20:00)
Full sample reference: 1269000 ms (~21:09)
Actual duration: 1028912 ms
Difference from official: -171088 ms
Difference from sample: -240088 ms
Status: INFO
20–21: hsk4_cue_q20_21; asset 635398..640328 ms; passage starts 640328 ms; added gap 0 ms
22–23: hsk4_cue_q22_23; asset 697409..703209 ms; passage starts 703209 ms; added gap 0 ms
24–25: hsk4_cue_q24_25; asset 758967..764332 ms; passage starts 764332 ms; added gap 0 ms
26–27: hsk4_cue_q26_27; asset 818774..824788 ms; passage starts 824788 ms; added gap 0 ms
28–29: hsk4_cue_q28_29; asset 877709..882939 ms; passage starts 882939 ms; added gap 0 ms
30–32: hsk4_cue_q30_32; asset 935704..941679 ms; passage starts 941679 ms; added gap 0 ms
```

Timeline checks: {"questions_once": true, "answer_pauses_32": true, "six_cues_once": true, "passage_once_per_group": true, "no_manual_cue_gap": true, "q15_19_no_extra_cues": true, "outro_once": true}

Asset hashes unchanged (including original intro/part1/part2).
