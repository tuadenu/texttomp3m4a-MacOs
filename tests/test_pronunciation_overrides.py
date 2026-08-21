from pathlib import Path
import unittest

from pipelines.pronunciation_overrides import prepare_spoken_text


class PronunciationOverrideTests(unittest.TestCase):
    def test_two_word_fallback_terms_override_only_spoken_text(self):
        for text, spoken in [("点儿", "点"), ("小孩儿", "小孩"), ("面条儿", "面条")]:
            for engine in ["Google Cloud TTS", "gTTS"]:
                payload = prepare_spoken_text(text, "zh-CN", engine)
                self.assertTrue(payload.matched)
                self.assertEqual("text_fallback", payload.mode)
                self.assertEqual(text, payload.source_text)
                self.assertEqual(spoken, payload.spoken_text)
                self.assertEqual(text, payload.matched_key)

            polly_payload = prepare_spoken_text(text, "zh-CN", "Polly")
            self.assertFalse(polly_payload.matched)
            self.assertEqual("raw", polly_payload.mode)
            self.assertEqual(text, polly_payload.spoken_text)
            self.assertEqual(None, polly_payload.matched_key)

    def test_two_word_fallback_inside_sentence(self):
        payload = prepare_spoken_text("...点儿, 小孩儿, 面条儿...", "zh-CN", "Google Cloud TTS")
        self.assertTrue(payload.matched)
        self.assertEqual("...点儿, 小孩儿, 面条儿...", payload.source_text)
        self.assertEqual("...点, 小孩, 面条...", payload.spoken_text)
        self.assertEqual("text_fallback", payload.mode)
        self.assertEqual("点儿", payload.matched_key)

    def test_raw_only_terms_stay_raw(self):
        for text in ["哪儿", "这儿", "玩儿", "儿子", "女儿", "儿童", "儿科"]:
            payload = prepare_spoken_text(text, "zh-CN", "Google Cloud TTS")
            self.assertFalse(payload.matched)
            self.assertEqual("raw", payload.mode)
            self.assertEqual(text, payload.spoken_text)

    def test_yidianr_matches_point_rule(self):
        for engine in ["Google Cloud TTS", "gTTS"]:
            payload = prepare_spoken_text("一点儿", "zh-CN", engine)
            self.assertTrue(payload.matched)
            self.assertEqual("text_fallback", payload.mode)
            self.assertEqual("一点", payload.spoken_text)

        polly_payload = prepare_spoken_text("一点儿", "zh-CN", "Polly")
        self.assertFalse(polly_payload.matched)
        self.assertEqual("raw", polly_payload.mode)
        self.assertEqual("一点儿", polly_payload.spoken_text)

    def test_non_chinese_stays_raw(self):
        payload = prepare_spoken_text("hello", "vi", "Google Cloud TTS")
        self.assertFalse(payload.matched)
        self.assertEqual("raw", payload.mode)
        self.assertEqual("hello", payload.spoken_text)

    def test_engine_behavior(self):
        google = prepare_spoken_text("小孩儿", "zh-CN", "Google Cloud TTS")
        gtts = prepare_spoken_text("面条儿", "zh-CN", "gTTS")
        polly = prepare_spoken_text("小孩儿", "zh-CN", "Polly")
        self.assertTrue(google.matched)
        self.assertEqual("小孩", google.spoken_text)
        self.assertEqual("text_fallback", google.mode)
        self.assertTrue(gtts.matched)
        self.assertEqual("面条", gtts.spoken_text)
        self.assertEqual("text_fallback", gtts.mode)
        self.assertFalse(polly.matched)
        self.assertEqual("raw", polly.mode)
        self.assertEqual("小孩儿", polly.spoken_text)

    def test_raw_terms_in_sentence_stay_raw(self):
        for text in ["...哪儿...", "...这儿...", "...玩儿...", "...儿子...", "...女儿...", "...儿童...", "...儿科..."]:
            payload = prepare_spoken_text(text, "zh-CN", "Google Cloud TTS")
            self.assertFalse(payload.matched)
            self.assertEqual(text, payload.spoken_text)

    def test_cache_variant_identity_differs(self):
        raw_payload = prepare_spoken_text("一点儿", "zh-CN", "Google Cloud TTS")
        override_payload = prepare_spoken_text("小孩儿", "zh-CN", "Google Cloud TTS")
        self.assertNotEqual((raw_payload.mode, raw_payload.spoken_text), (override_payload.mode, override_payload.spoken_text))

    def test_call_sites_use_shared_layer(self):
        project = Path(__file__).resolve().parents[1]
        self.assertIn("prepare_spoken_text", (project / "services" / "tts_service.py").read_text(encoding="utf-8"))
        self.assertIn("prepare_spoken_text", (project / "app.pyw").read_text(encoding="utf-8"))
        self.assertIn("prepare_spoken_text", (project / "pipelines" / "vocab_pipeline.py").read_text(encoding="utf-8"))
        self.assertIn("prepare_spoken_text", (project / "pipelines" / "hsk30_audio.py").read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()