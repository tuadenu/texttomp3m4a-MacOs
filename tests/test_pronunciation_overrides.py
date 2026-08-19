from pathlib import Path
import unittest

from pipelines.pronunciation_overrides import prepare_spoken_text


class PronunciationOverrideTests(unittest.TestCase):
    def test_erhua_terms_stay_raw_for_all_engines(self):
        for text in ["小孩儿", "面条儿"]:
            for engine in ["Google Cloud TTS", "gTTS", "Polly"]:
                payload = prepare_spoken_text(text, "zh-CN", engine)
                self.assertFalse(payload.matched)
                self.assertEqual("raw", payload.mode)
                self.assertEqual(text, payload.spoken_text)
                self.assertEqual(None, payload.matched_key)

    def test_raw_only_terms_stay_raw(self):
        for text in ["哪儿", "这儿", "玩儿", "儿子", "女儿", "儿童", "儿科"]:
            payload = prepare_spoken_text(text, "zh-CN", "Google Cloud TTS")
            self.assertFalse(payload.matched)
            self.assertEqual("raw", payload.mode)
            self.assertEqual(text, payload.spoken_text)

    def test_yidianr_stays_raw(self):
        for engine in ["Google Cloud TTS", "gTTS", "Polly"]:
            payload = prepare_spoken_text("一点儿", "zh-CN", engine)
            self.assertFalse(payload.matched)
            self.assertEqual("raw", payload.mode)
            self.assertEqual("一点儿", payload.spoken_text)

    def test_non_chinese_stays_raw(self):
        payload = prepare_spoken_text("hello", "vi", "Google Cloud TTS")
        self.assertFalse(payload.matched)
        self.assertEqual("raw", payload.mode)
        self.assertEqual("hello", payload.spoken_text)

    def test_engine_behavior(self):
        for engine in ["Google Cloud TTS", "gTTS", "Polly"]:
            google = prepare_spoken_text("小孩儿", "zh-CN", engine)
            self.assertFalse(google.matched)
            self.assertEqual("raw", google.mode)
            self.assertEqual("小孩儿", google.spoken_text)

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