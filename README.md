# text-to-audio-viettrungnhat

[![Python](https://img.shields.io/badge/Python-3.12-blue.svg)](https://www.python.org/)
[![Platform](https://img.shields.io/badge/Platform-macOS%20%7C%20Windows-lightgrey.svg)](https://www.python.org/)
[![License](https://img.shields.io/badge/License-Private-red.svg)]()

Phần mềm này là để bổ trợ cho các ứng dụng mobile, web... Thuộc sở hữu của Bảo Ngọc EduSoft Studio - 0986. 183 806.

Ứng dụng text-to-speech đa ngôn ngữ dùng Python/Tkinter, tối ưu cho chạy cục bộ trên macOS và Windows.

## Tính năng chính

- Đọc text thành MP3/M4A với nhiều ngôn ngữ.
- Hỗ trợ gTTS, Google Cloud TTS, OpenAI và Gemini.
- Có khu vực quản lý API key, Discord webhook và AWS.
- Giao diện Tkinter có sẵn launcher trên macOS để mở nhanh.
- Hỗ trợ xuất âm thanh, lưu cấu hình, và chạy đúng code mới nhất trong repo.
ĐỌc luân phiên, sole nam nữ đa ngôn ngữ, xuất audio chuẩn m4a nhẹ nhất vẫn rõ lời

## Mockup giao diện

### Luồng chính

![Luồng text-to-audio](docs/assets/text-to-audio-flow.svg)

### Chọn giọng và tốc độ

![Cài đặt giọng đọc](docs/assets/voice-settings.svg)

### API và tích hợp dịch vụ

![Cài đặt API](docs/assets/api-settings.svg)

## Mở app trên macOS

Click [TextToMp3Launcher.app](TextToMp3Launcher.app) để mở app.

Launcher này luôn trỏ về [app.pyw](app.pyw) trong thư mục dự án, nên mỗi lần bạn sửa code xong chỉ cần bấm lại icon là nó chạy bản mới nhất.

## Chạy bằng Terminal

```bash
cd /path/to/text-to-audio-viettrungnhat
./.venv312/bin/python app.pyw
```

## Cài trên máy khác

Xem hướng dẫn đầy đủ tại [docs/run-on-another-machine.md](docs/run-on-another-machine.md).
