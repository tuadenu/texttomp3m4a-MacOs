import copy
import os
import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from pydub import AudioSegment
from pydub.generators import Sine

from pipelines.hsk30_audio import (
    HSK30_HSK5_SOURCE, HSK5_AUDIO_PROFILE, HSK3_TTS_POSTPROCESS_VERSION,
    _hsk5_parse_content, _load_hsk5_exam_rows, _normalize_tts_text,
    _synthesize_tts_segment, build_hsk5_phase1b, parse_hsk5_phase1,
    phase1_output_filename, validate_hsk5_workbook, validate_hsk30_assets,
    format_hsk5_preview_report, format_hsk5_build_report,
    audit_hsk5_timeline, build_hsk5_batch, dry_run_hsk5_batch,
    _decode_and_cache_tts_audio, _hsk5_batch_identity, HSK5_COMPATIBLE_BATCH_CODE_HASHES,
    _tts_cache_key,
    HSK30_HSK5_ASSETS, HSK30_HSK5_ASSET_DIRECTORY, HSK5_BATCH_APPROVED,
)
from pipelines.pronunciation_overrides import prepare_spoken_text

HSK30_HSK5_SOURCE = Path(os.environ.get("HSK5_TEST_SOURCE", HSK30_HSK5_SOURCE))


class Hsk30AudioHsk5Tests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.structure = validate_hsk5_workbook(HSK30_HSK5_SOURCE)

    def state(self):
        return {"engine": "Google Cloud TTS", "speech_rate": 1.0,
                "hsk_speed_profile": "HSK5", "zh_female_voice": "cmn-CN-Standard-A",
                "zh_male_voice": "cmn-CN-Standard-C", "zh_narrator_voice": "cmn-CN-Standard-A"}

    def test_all_exams_and_layout(self):
        structure = self.structure
        self.assertEqual("PASS", structure["status"])
        self.assertEqual(17, structure["exam_count"])
        self.assertEqual(595, structure["row_count"])
        self.assertEqual({2: 19, 3: 41, 4: 106, 5: 4}, structure["independent_turn_counts"])
        for exam, summary in structure["exams"].items():
            with self.subTest(exam=exam):
                self.assertEqual(35, summary["row_count"])
                self.assertEqual({1: 19, 2: 16}, summary["part_counts"])
                self.assertEqual(10, summary["independent_count"])
                self.assertEqual(HSK5_AUDIO_PROFILE["expected_shared_groups"], tuple(summary["shared_groups"]))

    def test_independent_dialogue_preserves_all_turns(self):
        for turns in (2, 3, 4, 5):
            content = "".join(f"{'男' if index % 2 else '女'}：这是第{index}句话。" for index in range(turns)) + "问：他们讨论什么？"
            parsed = _hsk5_parse_content(content)
            self.assertEqual(turns, len(parsed["stimulus_segments"]))
            self.assertEqual(["女" if index % 2 == 0 else "男" for index in range(turns)],
                             [segment["speaker_token"] for segment in parsed["stimulus_segments"]])
            self.assertEqual("NỮ", parsed["question"]["speaker_label"])

    def test_final_question_preserves_internal_narrative_question(self):
        items = self.structure["semantic_items"]["HSK5_30_10"]
        for number, phrase, question in (
            (24, "导演问：“是不是要把故事全部重写？”", "老师认为故事哪里需要调整？"),
            (27, "一位学生问：“那专业人士的建议是不是应该完全照着做？”", "老师首先介绍了什么？"),
        ):
            item = items[number - 1]
            # Independent fixtures remain protective even if Excel removes internal 问：.
            for stimulus in (phrase, phrase.replace("：“", "：").replace("”", "")):
                parsed = _hsk5_parse_content(stimulus + "老师继续说明。问：" + question)
                self.assertIn(stimulus, parsed["stimulus_text"])
                self.assertIn(_normalize_tts_text(stimulus), parsed["stimulus_segments"][0]["text"])
                self.assertEqual(_normalize_tts_text(question), parsed["question"]["text"])
            self.assertEqual(_normalize_tts_text(question), item["question_spoken"])
            self.assertEqual("NỮ", item["question_segment"]["speaker_label"])

    def test_all_exam_dry_runs_never_call_tts(self):
        with patch("pipelines.hsk30_audio._synthesize_tts_segment", side_effect=AssertionError("No TTS in dry-run")):
            previews = dry_run_hsk5_batch(HSK30_HSK5_SOURCE, lang_state=self.state())
        self.assertEqual(17, len(previews))
        for preview in previews:
            self.assertEqual(35, preview["question_count"])
            self.assertEqual({1: 19, 2: 16}, preview["part_counts"])
            self.assertEqual(10, preview["shared_group_count"])
            self.assertEqual(0, preview["tts_requests_made"])

    def test_batch_continues_after_failure_and_safe_resume(self):
        timeline = []
        for number in range(1, 11):
            timeline += [f"number{number}", f"question{number}", "answer_pause"]
        for group in HSK5_AUDIO_PROFILE["expected_shared_groups"]:
            first = int(group.split("–")[0])
            fixed_token = "hsk5_part2" if first == 20 else f"hsk5_{HSK5_AUDIO_PROFILE['cues_before_question'][first]}"
            timeline.append(fixed_token)
            timeline += [f"shared_passage_{group}", "shared_passage_to_number_gap"]
            start, end = map(int, group.split("–"))
            for number in range(start, end + 1):
                timeline += [f"number{number}", "number_to_question_gap", f"question{number}", "answer_pause"]
        timeline += ["outro"]
        def builder(preview, output, **kwargs):
            if preview["detected_exam"] == "HSK5_30_01":
                raise RuntimeError("Test failure")
            output.write_bytes(b"audio")
            return {"timeline": timeline, "repeat_gap_ms": 0, "status": "INFO",
                    "fixed_assets_version": HSK5_AUDIO_PROFILE["fixed_assets_version"],
                    "fixed_asset_boundaries": [{"group": group, "manual_gap_ms": 0,
                                                "asset_end_ms": 40, "passage_start_ms": 40}
                                               for group in HSK5_AUDIO_PROFILE["expected_shared_groups"]],
                    "actual_duration_ms": 1000, "unique_tts_requests": 1, "reused_segments": 0}
        with TemporaryDirectory() as tmpdir, \
             patch("pipelines.hsk30_audio.build_hsk5_phase1b", side_effect=builder) as build, \
             patch("pipelines.hsk30_audio._probe_audio_duration_ms", return_value=1000):
            results = build_hsk5_batch(HSK30_HSK5_SOURCE, tmpdir, lang_state=self.state())
            self.assertEqual("FAIL", results[0]["status"])
            self.assertEqual(16, sum(result["status"] == "INFO" for result in results))
            self.assertEqual("HSK5_30_17", results[-1]["exam"])
            legacy_identity = _hsk5_batch_identity(HSK30_HSK5_SOURCE, self.state(),
                                                  code_digest=HSK5_COMPATIBLE_BATCH_CODE_HASHES[0])
            for result in results[1:]:
                sidecar = Path(result["output_file"]).with_suffix(".report.json")
                saved = json.loads(sidecar.read_text())
                saved["batch_identity"] = legacy_identity
                sidecar.write_text(json.dumps(saved))
            build.reset_mock()
            resumed = build_hsk5_batch(HSK30_HSK5_SOURCE, tmpdir, lang_state=self.state())
            self.assertEqual(1, build.call_count)
            self.assertEqual(16, sum(result["status"] == "SKIP_VERIFIED" for result in resumed))
            protected = Path(tmpdir) / "hsk5-30-002.m4a"
            before = protected.read_bytes()
            changed = self.state() | {"speech_rate": 0.95}
            rejected = build_hsk5_batch(HSK30_HSK5_SOURCE, tmpdir, lang_state=changed)
            self.assertEqual("FAIL", rejected[1]["status"])
            self.assertIn("Not overwritten", rejected[1]["error"])
            self.assertEqual(before, protected.read_bytes())
        broken = {"timeline": timeline + ["repeat_gap"], "repeat_gap_ms": 2170}
        with self.assertRaises(ValueError):
            audit_hsk5_timeline(broken)

    def test_failed_decode_never_publishes_raw_hsk5_cache(self):
        with TemporaryDirectory() as tmpdir:
            temporary = Path(tmpdir) / "raw.tmp.mp3"
            canonical = Path(tmpdir) / "normalized.mp3"
            temporary.write_bytes(b"raw")
            with patch("pipelines.hsk30_audio.load_audio_segment_from_mp3", side_effect=RuntimeError("decoder terminated")):
                with self.assertRaisesRegex(RuntimeError, "decoder terminated"):
                    _decode_and_cache_tts_audio(temporary, canonical, HSK3_TTS_POSTPROCESS_VERSION, {})
            self.assertFalse(canonical.exists())
            self.assertFalse(canonical.with_suffix(".edge_metrics.json").exists())
            self.assertFalse(temporary.exists())

    def test_old_interrupted_raw_cache_is_normalized_without_tts(self):
        text, voice = "测试。", "cmn-CN-Standard-A"
        prepared = prepare_spoken_text(_normalize_tts_text(text), "zh-CN", "Google Cloud TTS", voice)
        key = _tts_cache_key("Google Cloud TTS", voice, "1.00x", "zh-CN", prepared.mode,
                             prepared.spoken_text, prepared.source_text, "NỮ",
                             audio_postprocess_version=HSK3_TTS_POSTPROCESS_VERSION)
        raw = AudioSegment.silent(400) + Sine(440).to_audio_segment(duration=200) + AudioSegment.silent(600)
        with TemporaryDirectory() as tmpdir:
            canonical = Path(tmpdir) / f"{key}.mp3"
            raw.export(canonical, format="mp3").close()
            metrics = {}
            audio, reused = _synthesize_tts_segment(text, engine="Google Cloud TTS", speed="1.00x",
                                                   voice_name=voice, speaker_label="NỮ", cache_dir=Path(tmpdir),
                                                   audio_postprocess_version=HSK3_TTS_POSTPROCESS_VERSION,
                                                   audio_metrics=metrics,
                                                   tts_provider=lambda *args, **kwargs: self.fail("No new TTS required"))
            self.assertTrue(reused)
            self.assertAlmostEqual(360, len(audio), delta=15)
            self.assertGreater(metrics["edge_silence_trimmed_ms"], 700)
            self.assertTrue(canonical.with_suffix(".edge_metrics.json").exists())
            self.assertFalse(list(Path(tmpdir).glob(".*.mp3")))

    def test_part1_narration_fallback_and_shared_ownership(self):
        self.assertEqual(["HSK5_30_03", "HSK5_30_04", "HSK5_30_05", "HSK5_30_14", "HSK5_30_15"],
                         self.structure["part1_narration_exams"])
        for exam in self.structure["part1_narration_exams"]:
            items = self.structure["semantic_items"][exam][16:19]
            self.assertEqual(1, len(items[0]["stimulus_segments"]))
            self.assertEqual("NỮ", items[0]["stimulus_segments"][0]["speaker_label"])
            self.assertFalse(items[1]["stimulus_segments"])
            self.assertFalse(items[2]["stimulus_segments"])
            self.assertTrue(all(item["question_spoken"] for item in items))

    def test_part2_mapping_and_explicit_dialogue_override(self):
        narration_count, dialogue_count = 0, 0
        for items in self.structure["semantic_items"].values():
            for item in items[19:]:
                segments = item["stimulus_segments"]
                if not segments:
                    continue
                if not segments[0]["speaker_token"]:
                    narration_count += 1
                    self.assertEqual(HSK5_AUDIO_PROFILE["narration_group_voices"][item["group"]], segments[0]["speaker_label"])
                else:
                    dialogue_count += 1
                    for segment in segments:
                        self.assertEqual({"男": "NAM", "女": "NỮ"}[segment["speaker_token"]], segment["speaker_label"])
        self.assertGreater(narration_count, 0)
        self.assertGreater(dialogue_count, 0)

    def test_missing_final_question_fails_and_numbers_remain_in_stimulus(self):
        for text in ("男：你好。女：你好。", "男：你好。问：",
                     "女：男：你好。问：为什么？", "额外内容女：你好。问：为什么？"):
            with self.assertRaises(ValueError):
                _hsk5_parse_content(text)
        for marker in ("20．", "20.", "20、"):
            parsed = _hsk5_parse_content("2020年，他问：“怎么办？”" + marker + "问：是什么时候？")
            self.assertIn("2020年", parsed["stimulus_text"])
            self.assertIn("他问：", parsed["stimulus_text"])
            self.assertEqual("是什么时候?", parsed["question"]["text"])

    def test_invalid_shared_groups_fail_explicitly(self):
        original = _load_hsk5_exam_rows(HSK30_HSK5_SOURCE, "Sheet1")
        for index, field, value in ((11, "content", "女：新的内容。问：为什么？"),
                                    (10, "content", "问：为什么？"),
                                    (11, "group", "13–14"), (0, "number", 2)):
            loaded = copy.deepcopy(original)
            loaded["exams"]["HSK5_30_01"][index][field] = value
            with patch("pipelines.hsk30_audio._load_hsk5_exam_rows", return_value=loaded):
                self.assertEqual("FAIL", validate_hsk5_workbook(HSK30_HSK5_SOURCE)["status"])

    def test_assets_output_names_and_preview(self):
        self.assertTrue(validate_hsk30_assets(level="hsk5")["ok"])
        self.assertEqual("hsk5-30-001.m4a", phase1_output_filename("HSK5_30_01"))
        self.assertEqual("hsk5-30-017.m4a", phase1_output_filename("HSK5_30_17"))
        report = parse_hsk5_phase1(HSK30_HSK5_SOURCE, lang_state=self.state())
        self.assertEqual({1: 4, 2: 6}, report["shared_group_count_by_part"])
        self.assertEqual("一。", report["preview_items"][0]["question_number_spoken"])
        self.assertEqual(0, report["tts_requests_made"])
        self.assertIn("LAST 问：", format_hsk5_preview_report(report))

    def test_v2_assets_required_and_batch_approved(self):
        self.assertTrue(HSK5_BATCH_APPROVED)
        self.assertEqual(12, len(HSK30_HSK5_ASSETS))
        self.assertNotIn("cue_q11_19", HSK30_HSK5_ASSETS)
        self.assertTrue(all(path.parent.name == HSK30_HSK5_ASSET_DIRECTORY for path in HSK30_HSK5_ASSETS.values()))
        with TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            for folder, name in (("common", "hsk_1_6_intro_music"), ("common", "hsk_outtro"),
                                 ("hsk5", "hsk5_intro"), ("hsk5", "hsk5_part1"),
                                 ("hsk5", "hsk5_part2"), ("hsk5", "hsk5_cue_q11_19")):
                destination = root / folder / f"{name}.m4a"
                destination.parent.mkdir(exist_ok=True)
                destination.write_bytes(b"v1")
            status = validate_hsk30_assets(root, level="hsk5")
            self.assertFalse(status["ok"])
            self.assertEqual(12, len(status["missing"]))
            with patch("pipelines.hsk30_audio.HSK5_BATCH_APPROVED", False), \
                 patch("pipelines.hsk30_audio._synthesize_tts_segment", side_effect=AssertionError("No batch TTS")):
                with self.assertRaisesRegex(ValueError, "batch locked"):
                    build_hsk5_batch(HSK30_HSK5_SOURCE, tmpdir, lang_state=self.state())

    def test_one_playback_exact_group_order_pauses_and_outro(self):
        report = parse_hsk5_phase1(HSK30_HSK5_SOURCE, lang_state=self.state())
        calls = []
        tone = Sine(440).to_audio_segment(duration=100)
        def provider(text, **kwargs):
            calls.append((text, kwargs))
            return AudioSegment.silent(200) + tone + AudioSegment.silent(300)
        def export(audio, path, kwargs):
            self.assertEqual(48000, audio.frame_rate)
            self.assertEqual(1, audio.channels)
            self.assertEqual("32k", kwargs["bitrate"])
            path.write_bytes(b"test")
            return len(audio)
        with TemporaryDirectory() as tmpdir:
            with patch("pipelines.hsk30_audio._make_tts_cache_dir", return_value=Path(tmpdir) / "cache"), \
                 patch("pipelines.hsk30_audio._load_audio_segment", return_value=AudioSegment.silent(40)), \
                 patch("pipelines.hsk30_audio._export_audio_atomically", side_effect=export):
                result = build_hsk5_phase1b(report, Path(tmpdir) / "test.m4a", tts_provider=provider)
        timeline = result["timeline"]
        self.assertTrue(all(audit_hsk5_timeline(result).values()))
        self.assertEqual(35, timeline.count("answer_pause"))
        self.assertEqual(1, timeline.count("outro"))
        self.assertNotIn("hsk5_cue_q11_19", timeline)
        for key in HSK5_AUDIO_PROFILE["cues_before_question"].values():
            self.assertEqual(1, timeline.count(f"hsk5_{key}"))
        self.assertNotIn("part2_start_gap", timeline)
        self.assertEqual({1: 3150, 2: 0}, result["part_start_gaps_ms"])
        self.assertEqual(4170, result["part_transitions_ms"])
        self.assertEqual(0, result["repeat_gap_ms"])
        self.assertEqual(19 * 14900 + 16 * 15860, result["answer_pause_ms"])
        self.assertEqual(9 * 740 + 16 * 760, result["number_to_question_ms"])
        self.assertEqual(10 * 2250, result["shared_passage_to_number_ms"])
        self.assertEqual(9 * 40, result["fixed_cues_ms"])
        self.assertEqual(10, len(result["fixed_asset_boundaries"]))
        for boundary in result["fixed_asset_boundaries"]:
            self.assertEqual(0, boundary["manual_gap_ms"])
            self.assertEqual(boundary["asset_end_ms"], boundary["passage_start_ms"])
            self.assertAlmostEqual(40, boundary["asset_end_ms"] - boundary["asset_start_ms"], delta=1)
        accounted = sum(result[key] for key in ("music_ms", "fixed_assets_ms", "fixed_cues_ms", "generated_speech_ms",
                                                "internal_pauses_ms", "answer_pause_ms", "part_transitions_ms", "outtro_ms"))
        self.assertAlmostEqual(accounted, result["actual_duration_ms"], delta=5)
        for number in range(1, 36):
            self.assertEqual(1, timeline.count(f"number{number}"))
            self.assertEqual(1, timeline.count(f"question{number}"))
        for group in HSK5_AUDIO_PROFILE["expected_shared_groups"]:
            start = timeline.index(f"shared_passage_{group}")
            expected = [f"shared_passage_{group}", "shared_passage_to_number_gap"]
            for number in report["shared_groups"][group]:
                expected += [f"number{number}", "number_to_question_gap", f"question{number}", "answer_pause"]
            self.assertEqual(expected, timeline[start:start + len(expected)])
        self.assertEqual(timeline.index("answer_pause", timeline.index("question10")) + 1, timeline.index("hsk5_cue_q11_12"))
        for boundary in result["fixed_asset_boundaries"]:
            position = timeline.index(f"shared_passage_{boundary['group']}")
            self.assertEqual(["answer_pause", boundary["asset"]], timeline[position - 2:position])
        for token in ("hsk5_cue_q13_14", "hsk5_part2"):
            position = timeline.index(token)
            for altered in (timeline[:position] + timeline[position + 1:],
                            timeline[:position] + [token] + timeline[position:],
                            timeline[:position + 1] + ["cue_to_stimulus_gap"] + timeline[position + 1:]):
                with self.assertRaises(ValueError):
                    audit_hsk5_timeline(result | {"timeline": altered})
        self.assertTrue(all(call[0] in {_normalize_tts_text(seg["text"]) for item in report["preview_items"]
                                       for seg in [{"text": item["question_number_spoken"]}, *item["stimulus_segments"], item["question_segment"]]}
                            for call in calls))
        self.assertTrue(all(call[1]["speed"] == "1.00x" for call in calls))
        self.assertTrue(all(call[1]["voice_name"] == ("cmn-CN-Standard-C" if call[1]["speaker_label"] == "NAM" else "cmn-CN-Standard-A") for call in calls))
        self.assertGreater(result["edge_silence_trimmed_ms"], 0)
        self.assertEqual("INFO", result["status"])
        self.assertIn("Status: INFO", format_hsk5_build_report(result))

    def test_edge_normalization_cache_reuse_and_internal_pause(self):
        tone = Sine(440).to_audio_segment(duration=180)
        raw = AudioSegment.silent(300) + tone + AudioSegment.silent(500) + tone + AudioSegment.silent(400)
        calls = []
        def provider(text, **kwargs):
            calls.append(text)
            return raw
        with TemporaryDirectory() as tmpdir:
            kwargs = dict(engine="Google Cloud TTS", speed="1.00x", voice_name="cmn-CN-Standard-A",
                          speaker_label="NỮ", cache_dir=Path(tmpdir), tts_provider=provider,
                          audio_postprocess_version=HSK3_TTS_POSTPROCESS_VERSION)
            first_metrics, cached_metrics = {}, {}
            first, reused = _synthesize_tts_segment("测试。", audio_metrics=first_metrics, **kwargs)
            second, cached = _synthesize_tts_segment("测试。", audio_metrics=cached_metrics, **kwargs)
            self.assertFalse(reused)
            self.assertTrue(cached)
            self.assertEqual(1, len(calls))
            self.assertEqual(first_metrics, cached_metrics)
            self.assertAlmostEqual(80 + 180 + 500 + 180 + 80, len(first), delta=10)
            self.assertAlmostEqual(len(first), len(second), delta=5)
            self.assertLess(first[80:240].dBFS, 0)
            self.assertLess(first[300:650].dBFS, -45)
            self.assertAlmostEqual(540, first_metrics["edge_silence_trimmed_ms"], delta=10)


if __name__ == "__main__":
    unittest.main()
