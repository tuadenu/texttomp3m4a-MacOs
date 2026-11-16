"""PCM-only adapters for the currently selected HSKK TTS engine."""

from __future__ import annotations

import io
from xml.sax.saxutils import escape

from pydub import AudioSegment

from pipelines.pronunciation_overrides import prepare_spoken_text


def synthesize_hskk3_pcm(
    text,
    *,
    engine,
    voice,
    speed,
    sample_rate_hz=48000,
    sample_format="LINEAR16",
    google_api_key="",
    aws_access_key_id="",
    aws_secret_access_key="",
    aws_region="us-east-1",
):
    """Return linear PCM from the selected engine; never fallback to another engine."""
    if sample_format != "LINEAR16":
        raise ValueError("HSKK3 hiện chỉ nhận TTS PCM LINEAR16.")
    spoken = prepare_spoken_text(
        str(text or "").strip(),
        "zh",
        engine,
        voice,
    ).spoken_text
    if not spoken.strip():
        return AudioSegment.silent(duration=0, frame_rate=sample_rate_hz).set_channels(1).set_sample_width(2)

    engine_name = str(engine or "").strip().casefold()
    if engine_name in {"google cloud tts", "google cloud", "google"}:
        from google.cloud import texttospeech

        if google_api_key:
            from google.api_core.client_options import ClientOptions
            client = texttospeech.TextToSpeechClient(client_options=ClientOptions(api_key=google_api_key))
        else:
            client = texttospeech.TextToSpeechClient()
        voice_name = str(voice or "").strip()
        voice_kwargs = {"language_code": "cmn-CN"}
        if voice_name and voice_name not in {"Mặc định", "Nam", "Nữ", "Trung tính"}:
            voice_kwargs["name"] = voice_name
        else:
            gender_map = {
                "Nam": texttospeech.SsmlVoiceGender.MALE,
                "Nữ": texttospeech.SsmlVoiceGender.FEMALE,
                "Trung tính": texttospeech.SsmlVoiceGender.NEUTRAL,
            }
            if voice_name in gender_map:
                voice_kwargs["ssml_gender"] = gender_map[voice_name]
        response = client.synthesize_speech(
            input=texttospeech.SynthesisInput(text=spoken),
            voice=texttospeech.VoiceSelectionParams(**voice_kwargs),
            audio_config=texttospeech.AudioConfig(
                audio_encoding=texttospeech.AudioEncoding.LINEAR16,
                sample_rate_hertz=int(sample_rate_hz),
                speaking_rate=float(speed),
            ),
        )
        return AudioSegment.from_file(io.BytesIO(response.audio_content), format="wav")

    if engine_name == "polly":
        voice_id = str(voice or "").strip()
        if voice_id != "Zhiyu":
            raise ValueError("Polly zh-CN hiện dùng Zhiyu theo mapping Mandarin hiện tại của app.")
        import boto3

        polly = boto3.Session(
            aws_access_key_id=aws_access_key_id or None,
            aws_secret_access_key=aws_secret_access_key or None,
            region_name=aws_region or None,
        ).client("polly")
        rate = max(50, min(200, round(float(speed) * 100)))
        ssml = f"<speak><prosody rate='{rate}%'>{escape(spoken)}</prosody></speak>"
        response = polly.synthesize_speech(
            VoiceId=voice_id,
            OutputFormat="pcm",
            Text=ssml,
            TextType="ssml",
            SampleRate="16000",
        )
        return AudioSegment(
            data=response["AudioStream"].read(),
            sample_width=2,
            frame_rate=16000,
            channels=1,
        )

    if engine_name == "gtts":
        raise RuntimeError(
            "gTTS không trả PCM. Không tự chuyển engine cho HSKK3; "
            "hãy chọn Polly hoặc Google Cloud TTS trong app nếu muốn tiếp tục xuất golden."
        )
    raise ValueError(f"Engine hiện tại chưa có adapter PCM HSKK3: {engine}")
