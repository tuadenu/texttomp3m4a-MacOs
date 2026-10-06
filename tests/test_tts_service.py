import unittest
import io
from types import SimpleNamespace
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from pydub import AudioSegment

from services.tts_service import generate_audio_core, load_audio_segment_from_mp3


class TtsServiceTests(unittest.TestCase):
    def test_transient_decoder_failure_retries_without_ffprobe(self):
        wav = io.BytesIO()
        AudioSegment.silent(80).export(wav, format="wav")
        responses = [SimpleNamespace(returncode=-9, stdout=b"", stderr=b""),
                     SimpleNamespace(returncode=0, stdout=wav.getvalue(), stderr=b"")]
        with patch("services.tts_service.subprocess.run", side_effect=responses) as run, \
             patch("services.tts_service.AudioSegment.from_mp3", side_effect=AssertionError("No ffprobe fallback")):
            audio = load_audio_segment_from_mp3("sample.mp3", ffmpeg_path="/ffmpeg")
        self.assertGreater(len(audio), 0)
        self.assertEqual(2, run.call_count)

    def test_decoder_error_reports_exit_code_and_segment_path(self):
        response = SimpleNamespace(returncode=-9, stdout=b"", stderr=b"")
        with patch("services.tts_service.subprocess.run", return_value=response) as run, \
             patch("services.tts_service.AudioSegment.from_mp3", side_effect=FileNotFoundError("ffprobe")):
            with self.assertRaisesRegex(RuntimeError, "exit=-9, file=broken.mp3"):
                load_audio_segment_from_mp3("broken.mp3", ffmpeg_path="/ffmpeg")
        self.assertEqual(2, run.call_count)

    def test_mp3_loader_does_not_require_ffprobe_in_path(self):
        with TemporaryDirectory() as temp_dir:
            mp3_path = Path(temp_dir) / "sample.mp3"
            export_handle = AudioSegment.silent(duration=80).export(mp3_path, format="mp3")
            export_handle.close()
            ffmpeg_path = Path(__file__).parents[1] / "ffmpeg_bin" / "bin" / "ffmpeg"
            with patch(
                "services.tts_service.AudioSegment.from_mp3",
                side_effect=FileNotFoundError("ffprobe"),
            ):
                decoded = load_audio_segment_from_mp3(mp3_path, ffmpeg_path=str(ffmpeg_path))

        self.assertGreater(len(decoded), 0)

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
