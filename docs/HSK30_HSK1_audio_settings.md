# HSK 3.0 Audio - HSK1 Chốt Cấu Hình

Ghi chú này chốt các thông số hiện tại đang áp dụng cho toàn bộ HSK1 3.0 Audio.

## Phạm Vi

- Áp dụng cho toàn bộ phase HSK1 3.0.
- Tốc độ đọc có thể đổi trong popup `Tinh chỉnh HSK 3.0 Audio`.
- Mức đang dùng cho test hiện tại: `0.70x`.

## Logic Nội Dung

- Cột `Số câu` trong Excel được đọc thành câu số ở đầu mỗi câu hỏi.
- Câu số chỉ đọc 1 lần, trước `repeat1`.
- `Part 4` vẫn strip prefix `问：` trước khi đưa vào TTS.
- Speaker pattern giữ nguyên theo logic HSK1 đã duyệt.

## Tốc Độ HSK

- HSK1 profile dùng các mức:
  - `0.60x`
  - `0.70x`
  - `0.88x`
  - `0.90x`
  - `0.92x`
- Tốc độ HSK là cấu hình riêng, tách khỏi tốc độ Lang chung.
- TTS cache key có chứa `speech_rate`, nên đổi tốc độ sẽ tạo cache mới.

## Nhịp Nghe

- `question number pause`: `300 ms`
- `repeat gap`: `900 ms`
- `speaker gap` trong 1 câu: `1150 ms`
- `Part 4 question gap`: `1050 ms`
- `answer silence` không còn ép để nhồi file về 12 phút.
- Builder hiện dùng nhịp chuẩn làm gốc, còn 12 phút chỉ là mốc tham chiếu QA.

## Output Audio

- Format xuất:
  - `AAC-LC`
  - `48,000 Hz`
  - `Mono`
  - khoảng `32 kbps`
  - `.m4a`

## Report

- Report build hiện dùng:
  - `Reference duration`
  - `Actual audio duration`
  - `Difference`
  - `Status: INFO`
- `Status` chỉ dùng để báo lỗi thật, không còn phụ thuộc việc file có sát 12 phút hay không.

## Ghi Chú Vận Hành

- Nếu muốn đổi tốc độ đọc, vào popup `Tinh chỉnh HSK 3.0 Audio` rồi chọn mức khác và bấm `Lưu`.
- Sau khi lưu, preview sẽ reload theo tốc độ mới.
- Khi xuất file, builder sẽ lấy đúng `speech_rate` đang lưu trong state HSK.

