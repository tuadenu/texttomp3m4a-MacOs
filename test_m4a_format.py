#!/usr/bin/env python3
"""Test M4A export format and file size."""

import sys
import os
import json
import subprocess
import tempfile
from pathlib import Path

# Add project to path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from services.tts_service import export_audio_batch
from pydub import AudioSegment


def get_ffprobe_info(file_path):
    """Get media info using ffprobe."""
    try:
        cmd = [
            "ffprobe", "-v", "error",
            "-show_format", "-show_streams",
            "-of", "json",
            file_path
        ]
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
        if result.returncode == 0:
            return json.loads(result.stdout)
        return None
    except Exception as e:
        print(f"⚠️  ffprobe error: {e}")
        return None


def format_bytes(bytes_val):
    """Format bytes to human-readable."""
    for unit in ['B', 'KB', 'MB']:
        if bytes_val < 1024:
            return f"{bytes_val:.1f} {unit}"
        bytes_val /= 1024
    return f"{bytes_val:.1f} GB"


def main():
    print("=" * 60)
    print("Testing M4A Export Format")
    print("=" * 60)
    
    # Create test audio: ~30 seconds of 1kHz tone
    print("\n1️⃣  Creating test audio (30 seconds)...")
    test_audio = AudioSegment.silent(duration=30000)  # 30s silence
    
    # Create temp directory for outputs
    temp_dir = tempfile.mkdtemp()
    m4a_path = os.path.join(temp_dir, "test.m4a")
    mp3_path = os.path.join(temp_dir, "test.mp3")
    
    print(f"   Temp dir: {temp_dir}")
    
    # Export M4A with current config
    print("\n2️⃣  Exporting M4A (48k bitrate, 22050 Hz, mono)...")
    try:
        # Get ffmpeg path and M4A settings
        import subprocess
        
        # Try to find ffmpeg
        ffmpeg_path = None
        for candidate in ["ffmpeg", "/usr/bin/ffmpeg", "/usr/local/bin/ffmpeg", 
                         "/opt/homebrew/bin/ffmpeg", "ffmpeg_bin/bin/ffmpeg"]:
            try:
                result = subprocess.run([candidate, "-version"], 
                                      capture_output=True, timeout=2)
                if result.returncode == 0:
                    ffmpeg_path = candidate
                    break
            except:
                pass
        
        if not ffmpeg_path:
            # Try to read FFMPEG_PATH from app.pyw
            print("   Trying to locate FFMPEG_PATH from app.pyw...")
            # This is a workaround - just hardcode the path expectation
            ffmpeg_path = "ffmpeg_bin/bin/ffmpeg"
            if not os.path.exists(ffmpeg_path):
                ffmpeg_path = "ffmpeg"
        
        print(f"   Using ffmpeg: {ffmpeg_path}")
        
        # M4A settings
        M4A_VOICE_BITRATE = "48k"
        M4A_VOICE_SAMPLE_RATE = "22050"
        
        temp_wav = os.path.join(temp_dir, "temp.wav")
        test_audio.set_channels(1).set_frame_rate(int(M4A_VOICE_SAMPLE_RATE)).export(temp_wav, format="wav")
        
        cmd = [
            ffmpeg_path, "-y",
            "-i", temp_wav,
            "-c:a", "aac",
            "-b:a", M4A_VOICE_BITRATE,
            "-ac", "1",
            "-ar", M4A_VOICE_SAMPLE_RATE,
            "-movflags", "+faststart",
            m4a_path,
        ]
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
        if result.returncode != 0:
            print(f"   ❌ FFmpeg error: {result.stderr}")
            return
        print(f"   ✓ M4A exported: {m4a_path}")
        os.remove(temp_wav)
    except Exception as e:
        print(f"   ❌ Error: {e}")
        import traceback
        traceback.print_exc()
        return
    
    # Export MP3 for comparison
    print("\n3️⃣  Exporting MP3 (192k bitrate, for comparison)...")
    try:
        test_audio.export(mp3_path, format="mp3", bitrate="192k")
        print(f"   ✓ MP3 exported: {mp3_path}")
    except Exception as e:
        print(f"   ❌ Error: {e}")
        return
    
    # Get file sizes
    print("\n4️⃣  File Sizes:")
    m4a_size = os.path.getsize(m4a_path)
    mp3_size = os.path.getsize(mp3_path)
    print(f"   • M4A:  {format_bytes(m4a_size)}")
    print(f"   • MP3:  {format_bytes(mp3_size)}")
    print(f"   • Ratio: {m4a_size / mp3_size:.2%} (M4A vs MP3)")
    
    # Get codec info with ffprobe
    print("\n5️⃣  Codec Details (via ffprobe):")
    
    m4a_info = get_ffprobe_info(m4a_path)
    if m4a_info:
        streams = m4a_info.get("streams", [])
        if streams:
            audio = streams[0]
            codec = audio.get("codec_name", "?")
            channels = audio.get("channels", "?")
            sample_rate = audio.get("sample_rate", "?")
            bit_rate = audio.get("bit_rate", "?")
            
            print(f"   M4A:")
            print(f"      Codec: {codec}")
            print(f"      Channels: {channels} (mono=1)")
            print(f"      Sample Rate: {sample_rate} Hz")
            print(f"      Bit Rate: {bit_rate} bps" if bit_rate != "?" else f"      Bit Rate: ~{M4A_VOICE_BITRATE} (configured)")
            
            if channels == 1:
                print(f"      ✓ Mono confirmed")
            else:
                print(f"      ❌ Not mono! Channels: {channels}")
    else:
        print("   ⚠️  ffprobe not available, skipping detailed codec check")
    
    mp3_info = get_ffprobe_info(mp3_path)
    if mp3_info:
        streams = mp3_info.get("streams", [])
        if streams:
            audio = streams[0]
            codec = audio.get("codec_name", "?")
            channels = audio.get("channels", "?")
            print(f"\n   MP3:")
            print(f"      Codec: {codec}")
            print(f"      Channels: {channels}")
    
    # Estimate per-minute size
    print("\n6️⃣  Size Estimate per 1 Minute of Audio:")
    m4a_per_min = (m4a_size / 30) * 60
    mp3_per_min = (mp3_size / 30) * 60
    print(f"   M4A:  ~{format_bytes(m4a_per_min)} per minute")
    print(f"   MP3:  ~{format_bytes(mp3_per_min)} per minute")
    
    # Estimate 1000 lines (typical document)
    print("\n7️⃣  Size Estimate for 1000 Lines (~30 min audio):")
    lines_m4a = (m4a_per_min * 30) / 1024  # KB
    lines_mp3 = (mp3_per_min * 30) / 1024  # KB
    print(f"   M4A:  ~{lines_m4a:.1f} KB")
    print(f"   MP3:  ~{lines_mp3:.1f} KB")
    
    print("\n" + "=" * 60)
    print("✅ Test Complete!")
    print("=" * 60)
    
    # Cleanup
    try:
        import shutil
        shutil.rmtree(temp_dir)
        print(f"\n🗑️  Cleaned up temp dir: {temp_dir}")
    except:
        pass


if __name__ == "__main__":
    main()
