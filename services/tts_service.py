import os
import tempfile
import uuid

from pydub import AudioSegment


def process_text_lines(danh_sach, selected_lang_list=None):
    """Chuẩn hóa input text/lang cho pipeline xuất audio."""
    if selected_lang_list is None:
        return [(dong, lang) for dong, lang in danh_sach]
    return [(dong, selected_lang_list[idx]) for idx, (dong, _) in enumerate(danh_sach)]


def generate_audio_core(
    danh_sach_doc,
    giong,
    toc_do,
    engine,
    start_count=0,
    initial_silence_ms=500,
    between_silence_ms=300,
    progress_callback=None,
    clean_text_func=None,
    tts_func=None,
):
    """Tạo AudioSegment từ danh sách (text, lang), dùng chung cho popup MP3/M4A."""
    if clean_text_func is None or tts_func is None:
        raise ValueError("generate_audio_core cần clean_text_func và tts_func.")

    full_audio = AudioSegment.silent(duration=initial_silence_ms)
    count = start_count
    tong_dong = len(danh_sach_doc)
    temp_dir = tempfile.gettempdir()

    for i, (dong, lang) in enumerate(danh_sach_doc):
        dong_sach = clean_text_func(dong)

        dialogue_pairs = {
            "Hội thoại 1 câu nam - 1 câu nữ": ("Nam", "Nữ"),
            "Hội thoại 1 câu nữ - 1 câu nam": ("Nữ", "Nam"),
        }
        if giong in dialogue_pairs:
            first_voice, second_voice = dialogue_pairs[giong]
            voice = first_voice if count % 2 == 0 else second_voice
            count += 1
        else:
            voice = giong

        temp_mp3 = os.path.join(temp_dir, f"temp_{uuid.uuid4().hex}.mp3")
        try:
            if not dong_sach.strip():
                full_audio += AudioSegment.silent(duration=between_silence_ms)
            else:
                tts_func(dong_sach, lang=lang, voice=voice, toc_do=toc_do, engine=engine, file_out=temp_mp3)
                try:
                    segment = AudioSegment.from_mp3(temp_mp3)
                except Exception as e2:
                    print(f"⚠ Không nạp được mp3 tạm: {e2} → chèn im lặng {between_silence_ms}ms")
                    segment = AudioSegment.silent(duration=between_silence_ms)
                full_audio += segment + AudioSegment.silent(duration=between_silence_ms)
        except Exception as e:
            msg = str(e)
            if "No text to speak" in msg or "No text to send to TTS API" in msg:
                print(f"⚠ Dòng rỗng/không hợp lệ → chèn im lặng {between_silence_ms}ms")
                full_audio += AudioSegment.silent(duration=between_silence_ms)
            else:
                raise
        finally:
            try:
                if os.path.exists(temp_mp3):
                    os.remove(temp_mp3)
            except:
                pass

        if progress_callback:
            progress_callback(i + 1, tong_dong)

    return full_audio, count


def export_audio_batch(full_audio, file_path, output_kind="mp3", m4a_exporter=None, export_progress_callback=None):
    """Xuất AudioSegment ra file; M4A vẫn dùng fallback ffmpeg hiện có của popup."""
    if output_kind == "m4a":
        if not m4a_exporter:
            raise ValueError("Thiếu m4a_exporter cho xuất M4A.")
        # Pass progress callback to m4a_exporter nếu có
        if export_progress_callback and hasattr(m4a_exporter, '__code__') and m4a_exporter.__code__.co_argcount > 2:
            m4a_exporter(full_audio, file_path, export_progress_callback)
        else:
            m4a_exporter(full_audio, file_path)
    else:
        full_audio.export(file_path, format="mp3", bitrate="192k")
