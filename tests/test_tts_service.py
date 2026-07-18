import unittest
from unittest.mock import patch

from pydub import AudioSegment

from services.tts_service import generate_audio_core


class TtsServiceTests(unittest.TestCase):
    def test_reverse_dialogue_pair_alternates_female_then_male(self):
        calls = []

        def fake_tts(text, **kwargs):
            calls.append(kwargs["voice"])

        with patch(
            "services.tts_service.AudioSegment.from_mp3",
            return_value=AudioSegment.silent(duration=10),
        ):
            _, count = generate_audio_core(
                [("one", "en"), ("two", "en"), ("three", "en")],
                giong="Hội thoại 1 câu nữ - 1 câu nam",
                toc_do="Bình thường",
                engine="Google Cloud TTS",
                clean_text_func=lambda value: value,
                tts_func=fake_tts,
            )

        self.assertEqual(["Nữ", "Nam", "Nữ"], calls)
        self.assertEqual(3, count)


if __name__ == "__main__":
    unittest.main()
