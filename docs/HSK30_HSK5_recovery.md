# HSK5 Exam 16 Recovery

Historical v1 batch recovery. These outputs are preserved, not replaced by
the approved v2 golden. To export all 17 exams with v2 assets, choose a new
output folder; see `HSK30_HSK5_audio_settings.md`.

## Observed Failure

The first app batch exported exams 01-15 and 17 successfully. Exam 16 stopped
while decoding the shared Q24-26 stimulus. The fallback error mentioned missing
ffprobe and empty FFmpeg output. The old error did not record FFmpeg's exit code,
so the reason for that particular decoder failure cannot be established.

The cached Q24 response was a valid 43.392-second MP3, decoded successfully on
retry. A confirmed cache bug had published the raw response under the normalized
cache key before decoding/edge trimming, leaving it without edge metadata.

## Fix

- Retry a terminated/empty FFmpeg decoder once and include exit code and segment
  path in persistent errors. A successful FFmpeg decode does not need ffprobe.
- HSK5 generated clips are decoded in a temporary file. Only normalized audio is
  atomically published into the reusable cache; failed decoding does not publish
  a raw clip as normalized audio.
- Recover old interrupted HSK5 cache entries without another TTS request.
- Preserve HSK1-4 processing paths and all approved HSK5 timing/speed settings.
- Accept the specifically audited pre-recovery batch code identity only when
  the source/settings/assets/pronunciation identity, audio hash, probed duration
  and exact builder timeline also match. This enables safe resume without
  rewriting previously completed outputs.

The Q24 repair removed 674 ms of edge silence while retaining 80 ms at each edge.
No fixed assets, internal pauses, speed or answer pauses were changed.

## Verification and Output

- 119 full-repo regression tests PASS.
- 18 focused TTS/HSK5 tests PASS.
- Revised Excel: all 17 exams dry-run PASS, no TTS in dry-run.
- Existing exams 01-15 and 17: SKIP_VERIFIED; audio files and sidecars unchanged.
- Only HSK5_30_16 was built: 22 new TTS requests, 133 cached segments reused.
- Output: `testdata/hsk30/hsk5/Audio/hsk5-30-016.m4a`.
- Duration: 1424538 ms (23:44.54); size: 5993570 bytes (5.99 MB).
- Format verified: AAC-LC, 48000 Hz, mono, approximately 32.15 kbps.
- Speech rate: 1.00x; edge_trim_v1, -45 dB, 80 ms safety padding.
- All 17 audio hashes and exact once-only playback/group timelines PASS.
- Spot checks: exam 03 narration fallback, exam 10 internal question markers,
  exam 17, two-question and three-question group ownership/order PASS against
  saved production reports and source. No subjective listening claim is made.

The output folder now contains all 17 files. The complete resume table is in
`testdata/hsk30/hsk5/Audio/hsk5-batch.report.md` and the JSON report alongside it.
No batch rerun is required. Restart the app before future exports to load the fix;
an already-open report window still shows its original failed-batch snapshot.
