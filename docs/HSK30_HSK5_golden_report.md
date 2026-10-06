# HSK5 Golden QA

Fixed assets v2 golden manually approved by the user. HSK5 batch is enabled;
no batch run as part of approval.

- Source: `testdata/hsk30/hsk5/xuat_audio_hsk5-1.xlsx`.
- Full regression: 120 tests PASS, including HSK1-5.
- FFprobe duration: 1430.193563 seconds (23:50.19).
- File: 6015346 bytes; AAC-LC, 48000 Hz, mono, 32142 bps.
- Speed: 1.00x; edge_trim_v1, -45 dB, 80 ms safety pads unchanged.
- TTS new: 0; cache reused: 114.
- All 10 fixed-asset/group boundaries: correct asset once, added gap 0 ms.
- Shared stimulus once per group; all 35 numbers/questions once; outro once.
- SHA256 unchanged for all 12 v2 assets and all 17 existing batch outputs.
- Obsolete hsk5_cue_q11_19.m4a removed; no v1 fallback.

```text
Exam: HSK5_30_01
Output: testdata/hsk30/hsk5/hsk5-30-001.m4a
File size: 6015346 bytes
Speed: HSK5 chuẩn — 1.00x
Voices: from Lang; number/question female, stimulus follows semantic mapping
Part1 duration: 713313 ms
Part2 duration: 622730 ms
Fixed assets version: hsk5_fixed_assets_v2
Music: 29000 ms
Fixed intro/part assets: 54937 ms
Fixed cue: 49132 ms
Generated speech including safety pads: 661436 ms
Active speech span (internal linguistic pauses retained): 643190 ms
Edge silence trimmed: 41458 ms
Speaker gaps: 15600 ms
Content to question gaps: 20200 ms
Number to content gaps: 11500 ms
Shared stimulus to number gaps: 22500 ms
Number to question gaps: 18820 ms
Answer pauses: 536860 ms
Part transitions: 4170 ms
Outro: 6044 ms
Edge metrics available: 114 / 114 segments
Independent questions: 10
Shared groups: 10
Repetitions: 1 / Repeat gap: 0
TTS requests: 0
Cache reused: 114
Output: AAC-LC / 48000 Hz / mono / 32 kbps
Reference: ~25:00
Full sample reference: 26:31.98
Meaningful sample reference: ~26:11.7 (no trailing-silence padding)
Actual duration: 1430194 ms
Difference vs meaningful sample: -141506 ms
Status: INFO

Timeline:
music → music_to_intro_gap → hsk5_intro → intro_to_part_gap → hsk5_part1 → part1_start_gap → number1 → stimulus1 → question1 → answer_pause → number2 → stimulus2 → question2 → answer_pause → number3 → stimulus3 → question3 → answer_pause → number4 → stimulus4 → question4 → answer_pause → number5 → stimulus5 → question5 → answer_pause → number6 → stimulus6 → question6 → answer_pause → number7 → stimulus7 → question7 → answer_pause → number8 → stimulus8 → question8 → answer_pause → number9 → stimulus9 → question9 → answer_pause → number10 → stimulus10 → question10 → answer_pause → hsk5_cue_q11_12 → shared_passage_11–12 → shared_passage_to_number_gap → number11 → number_to_question_gap → question11 → answer_pause → number12 → number_to_question_gap → question12 → answer_pause → hsk5_cue_q13_14 → shared_passage_13–14 → shared_passage_to_number_gap → number13 → number_to_question_gap → question13 → answer_pause → number14 → number_to_question_gap → question14 → answer_pause → hsk5_cue_q15_16 → shared_passage_15–16 → shared_passage_to_number_gap → number15 → number_to_question_gap → question15 → answer_pause → number16 → number_to_question_gap → question16 → answer_pause → hsk5_cue_q17_19 → shared_passage_17–19 → shared_passage_to_number_gap → number17 → number_to_question_gap → question17 → answer_pause → number18 → number_to_question_gap → question18 → answer_pause → number19 → number_to_question_gap → question19 → answer_pause → hsk5_part2 → shared_passage_20–21 → shared_passage_to_number_gap → number20 → number_to_question_gap → question20 → answer_pause → number21 → number_to_question_gap → question21 → answer_pause → hsk5_cue_q22_23 → shared_passage_22–23 → shared_passage_to_number_gap → number22 → number_to_question_gap → question22 → answer_pause → number23 → number_to_question_gap → question23 → answer_pause → hsk5_cue_q24_26 → shared_passage_24–26 → shared_passage_to_number_gap → number24 → number_to_question_gap → question24 → answer_pause → number25 → number_to_question_gap → question25 → answer_pause → number26 → number_to_question_gap → question26 → answer_pause → hsk5_cue_q27_29 → shared_passage_27–29 → shared_passage_to_number_gap → number27 → number_to_question_gap → question27 → answer_pause → number28 → number_to_question_gap → question28 → answer_pause → number29 → number_to_question_gap → question29 → answer_pause → hsk5_cue_q30_32 → shared_passage_30–32 → shared_passage_to_number_gap → number30 → number_to_question_gap → question30 → answer_pause → number31 → number_to_question_gap → question31 → answer_pause → number32 → number_to_question_gap → question32 → answer_pause → hsk5_cue_q33_35 → shared_passage_33–35 → shared_passage_to_number_gap → number33 → number_to_question_gap → question33 → answer_pause → number34 → number_to_question_gap → question34 → answer_pause → number35 → number_to_question_gap → question35 → answer_pause → final_silence → outro

Fixed asset -> shared passage audit (natural trailing silence belongs to asset):
Q11: answer_pause -> hsk5_cue_q11_12 -> shared_passage_11–12; asset 441908..447624 ms; passage starts 447624 ms; added gap 0 ms
Q13: answer_pause -> hsk5_cue_q13_14 -> shared_passage_13–14; asset 531120..536633 ms; passage starts 536633 ms; added gap 0 ms
Q15: answer_pause -> hsk5_cue_q15_16 -> shared_passage_15–16; asset 605222..610664 ms; passage starts 610664 ms; added gap 0 ms
Q17: answer_pause -> hsk5_cue_q17_19 -> shared_passage_17–19; asset 667785..673403 ms; passage starts 673403 ms; added gap 0 ms
Q20: answer_pause -> hsk5_part2 -> shared_passage_20–21; asset 785054..801420 ms; passage starts 801420 ms; added gap 0 ms
Q22: answer_pause -> hsk5_cue_q22_23 -> shared_passage_22–23; asset 885160..890620 ms; passage starts 890620 ms; added gap 0 ms
Q24: answer_pause -> hsk5_cue_q24_26 -> shared_passage_24–26; asset 962178..967780 ms; passage starts 967780 ms; added gap 0 ms
Q27: answer_pause -> hsk5_cue_q27_29 -> shared_passage_27–29; asset 1065170..1070309 ms; passage starts 1070309 ms; added gap 0 ms
Q30: answer_pause -> hsk5_cue_q30_32 -> shared_passage_30–32; asset 1187622..1193013 ms; passage starts 1193013 ms; added gap 0 ms
Q33: answer_pause -> hsk5_cue_q33_35 -> shared_passage_33–35; asset 1305087..1310336 ms; passage starts 1310336 ms; added gap 0 ms

Fixed asset mapping / decoded durations:
music: 29000 ms; /Users/vungocnguyenanh/Projects/texttomp3m4a-hsk-publish/assets/hsk30_audio/common/hsk_1_6_intro_music.m4a
intro: 5348 ms; /Users/vungocnguyenanh/Projects/texttomp3m4a-hsk-publish/assets/hsk30_audio/hsk5/hsk5_30_fixed_assets_v2/hsk5_intro.m4a
part1: 33223 ms; /Users/vungocnguyenanh/Projects/texttomp3m4a-hsk-publish/assets/hsk30_audio/hsk5/hsk5_30_fixed_assets_v2/hsk5_part1.m4a
cue_q11_12: 5716 ms; /Users/vungocnguyenanh/Projects/texttomp3m4a-hsk-publish/assets/hsk30_audio/hsk5/hsk5_30_fixed_assets_v2/hsk5_cue_q11_12.m4a
cue_q13_14: 5513 ms; /Users/vungocnguyenanh/Projects/texttomp3m4a-hsk-publish/assets/hsk30_audio/hsk5/hsk5_30_fixed_assets_v2/hsk5_cue_q13_14.m4a
cue_q15_16: 5442 ms; /Users/vungocnguyenanh/Projects/texttomp3m4a-hsk-publish/assets/hsk30_audio/hsk5/hsk5_30_fixed_assets_v2/hsk5_cue_q15_16.m4a
cue_q17_19: 5618 ms; /Users/vungocnguyenanh/Projects/texttomp3m4a-hsk-publish/assets/hsk30_audio/hsk5/hsk5_30_fixed_assets_v2/hsk5_cue_q17_19.m4a
part2: 16366 ms; /Users/vungocnguyenanh/Projects/texttomp3m4a-hsk-publish/assets/hsk30_audio/hsk5/hsk5_30_fixed_assets_v2/hsk5_part2.m4a
cue_q22_23: 5460 ms; /Users/vungocnguyenanh/Projects/texttomp3m4a-hsk-publish/assets/hsk30_audio/hsk5/hsk5_30_fixed_assets_v2/hsk5_cue_q22_23.m4a
cue_q24_26: 5603 ms; /Users/vungocnguyenanh/Projects/texttomp3m4a-hsk-publish/assets/hsk30_audio/hsk5/hsk5_30_fixed_assets_v2/hsk5_cue_q24_26.m4a
cue_q27_29: 5139 ms; /Users/vungocnguyenanh/Projects/texttomp3m4a-hsk-publish/assets/hsk30_audio/hsk5/hsk5_30_fixed_assets_v2/hsk5_cue_q27_29.m4a
cue_q30_32: 5392 ms; /Users/vungocnguyenanh/Projects/texttomp3m4a-hsk-publish/assets/hsk30_audio/hsk5/hsk5_30_fixed_assets_v2/hsk5_cue_q30_32.m4a
cue_q33_35: 5249 ms; /Users/vungocnguyenanh/Projects/texttomp3m4a-hsk-publish/assets/hsk30_audio/hsk5/hsk5_30_fixed_assets_v2/hsk5_cue_q33_35.m4a
outro: 6044 ms; /Users/vungocnguyenanh/Projects/texttomp3m4a-hsk-publish/assets/hsk30_audio/common/hsk_outtro.m4a
```

Timeline checks: {"numbers_once": true, "questions_once": true, "answer_pauses": true, "no_repeat_gap": true, "v2_assets": true, "fixed_cues_once": true, "obsolete_cue_absent": true, "part2_once": true, "no_manual_asset_gap": true, "outro_once": true, "group_11–12": true, "fixed_boundary_11–12": true, "group_13–14": true, "fixed_boundary_13–14": true, "group_15–16": true, "fixed_boundary_15–16": true, "group_17–19": true, "fixed_boundary_17–19": true, "group_20–21": true, "fixed_boundary_20–21": true, "group_22–23": true, "fixed_boundary_22–23": true, "group_24–26": true, "fixed_boundary_24–26": true, "group_27–29": true, "fixed_boundary_27–29": true, "group_30–32": true, "fixed_boundary_30–32": true, "group_33–35": true, "fixed_boundary_33–35": true, "ten_fixed_boundaries": true, "zero_added_gap": true}
