# HSK 2.0 / 3.0 Vocab ZIP Builder

Tài liệu vận hành cho màn **HSK 2.0 / 3.0 Vocab ZIP Builder**.

Tool tạo ZIP từ vựng HSK 2.0 và HSK 3.0, chia thành BASE/PLUS, upload ZIP
immutable, tạo catalog immutable và cập nhật signed pointer. Luồng HSK 2.0
và HSK 3.0 dùng chung builder nhưng tách namespace theo `version`.

## Cách kích hoạt cấp độ mới trong app

Upload ZIP lên Supabase chưa làm cấp độ xuất hiện trong app. Để kích hoạt
cấp độ vừa upload, phải thực hiện tiếp bước **3. Publish Catalog + Signed
Pointer**.

Nút này nằm trong màn **HSK 2.0 / 3.0 Vocab ZIP Builder**, ngay phía dưới nút
**2. Upload + Verify Packs**. Nếu cửa sổ thấp thì kéo xuống hoặc tăng chiều
cao cửa sổ.

Khi trạng thái đã đúng, app sẽ hiển thị:

- `REMOTE PACKS VERIFIED`
- `SIGNING KEY READY`
- `POINTER ACTIVE`

Chỉ khi cả ba trạng thái này đều đúng mới nên bấm publish. Confirmation phrase
cho bước publish là:

```text
PUBLISH VOCAB CATALOG
```

Hiện tại nút Publish trong tool sẽ quét tất cả deploy receipt đủ điều kiện
trong `output/` hiện hành, nên một lần publish có thể kích hoạt một level hoặc
nhiều level đã stage sẵn. Nó không chỉ giới hạn ở level đang chọn.

## 1. Tổng quan quy trình

1. Chọn Excel.
2. Chọn vocab version.
3. Chọn sheet.
4. Chọn level.
5. Chọn pack version.
6. Bấm **Build + Validate Local**.
7. Nghe thử audio local.
8. Bấm **Upload + Verify Packs**.
9. Lặp lại cho các level/version cần chuẩn bị.
10. Bấm **Publish Catalog + Signed Pointer** một lần.
11. Sau khi signed pointer đã được cấu hình trong Flutter, app tự nhận catalog mới.

Upload ZIP chưa làm dữ liệu xuất hiện trong app. Dữ liệu chỉ được kích hoạt khi
catalog được publish và `current.json` cập nhật, GET-verify thành công. Có thể
stage nhiều level/version rồi publish catalog một lần.

## 2. Version và level hỗ trợ

Version canonical:

- HSK 2.0 -> `2.0`
- HSK 3.0 -> `3.0`

Sheet mapping canonical:

- `hsk1_20` -> HSK 2.0 / `hsk1`
- `hsk2_20` -> HSK 2.0 / `hsk2`
- `hsk3_20` -> HSK 2.0 / `hsk3`
- `hsk4_20` -> HSK 2.0 / `hsk4`
- `hsk5_20` -> HSK 2.0 / `hsk5`
- `hsk6_20` -> HSK 2.0 / `hsk6`
- `hsk1_30` -> HSK 3.0 / `hsk1`
- `hsk2_30` -> HSK 3.0 / `hsk2`
- `hsk3_30` -> HSK 3.0 / `hsk3`
- `hsk4_30` -> HSK 3.0 / `hsk4`
- `hsk5_30` -> HSK 3.0 / `hsk5`
- `hsk6_30` -> HSK 3.0 / `hsk6`
- `hsk7_9_30` -> HSK 3.0 / `hsk7_9`

Đổi sheet sẽ tự đồng bộ version và level, nhưng build vẫn hard-fail nếu tuple
không khớp.

## 3. Các level hỗ trợ

| Hiển thị | Canonical code |
| --- | --- |
| HSK1 | `hsk1` |
| HSK2 | `hsk2` |
| HSK3 | `hsk3` |
| HSK4 | `hsk4` |
| HSK5 | `hsk5` |
| HSK6 | `hsk6` |
| HSK 7–9 | `hsk7_9` |

HSK 7–9 chỉ dùng canonical code `hsk7_9`; không tạo riêng `hsk7`, `hsk8`,
hoặc `hsk9`.

## 4. Yêu cầu file Excel

Các cột bắt buộc:

- `index`
- `word`
- `meaning_vi`
- `example_zh`
- `example_vi`

`index` phải bắt đầu từ 1, liên tục, không trùng và không có gap. `word`,
`meaning_vi`, `example_zh`, `example_vi` không được rỗng. BASE/PLUS split lấy
từ policy hiện có của catalog/manifest; builder không tự suy đoán lại số lượng.

Excel, CSV kiểm tra, JSON trung gian và thư mục unpacked không được upload lên
Supabase.

## 5. Chất lượng audio và TTS

Màn hình dùng chung các lựa chọn engine, giọng, tốc độ và bitrate M4A. Mỗi từ
có đúng một M4A; audio được đóng trong ZIP, không upload audio rời.

Nên nghe thử các từ đầu, giữa và cuối của BASE/PLUS. Nếu audio đã tồn tại và
bytes không đổi, builder có thể reuse canonical audio URI. Nếu audio thay đổi,
alias phải có content SHA mới; không map alias cũ sang bytes mới.

Nếu audio lỗi, sửa nguồn/TTS, tăng pack version nếu artifact đã publish, build
lại và không upload pack lỗi.

## 6. Pack version

Pack version là version của artifact ZIP. Lần đầu thường dùng `v1`. Khi sửa
hoặc rebuild dữ liệu đã publish, tăng lên `v2`, `v3`… và dùng object path mới:

```text
vocab/2.0/hsk2/base/v1/vocab_hsk2_20_base_v1.zip
vocab/2.0/hsk2/base/v2/vocab_hsk2_20_base_v2.zip
```

Không overwrite ZIP cũ. Catalog revision mới thay descriptor bằng descriptor
pack version mới; catalog revision cũ vẫn giữ nguyên để rollback. Không tăng
pack version chỉ vì bấm build lại khi artifact và SHA hoàn toàn giống nhau.

## 6. Bước 1 — Build + Validate Local

Điều kiện trước khi bấm:

- Excel, sheet, level và pack version đã chọn.
- Output directory hợp lệ.
- Cấu hình TTS/audio đã kiểm tra.

Nút **1. Build + Validate Local** đọc Excel strict, tạo hoặc reuse M4A, chia
BASE/PLUS, tạo `vocab.json`/`manifest.json`, đóng ZIP deterministic, mở lại ZIP
và verify.

Kết quả PASS hiển thị tổng dòng, BASE/PLUS count, đường dẫn ZIP, bytes,
SHA-256, compatibility hash, resource validation và deterministic/reopen
verification. Chỉ trạng thái **LOCAL PASS** mới cho phép stage.

## 7. Nghe thử audio local

Nút **Phát thử audio local** dùng audio nguồn local. Hãy thử từ đầu BASE, cuối
BASE và một số từ đầu/giữa/cuối PLUS. Kiểm tra tiếng Trung rõ, không cắt âm,
không nhầm từ và không có silent placeholder.

## 8. Bước 2 — Upload + Verify Packs

Nút **2. Upload + Verify Packs** yêu cầu Local PASS, profile Supabase hợp lệ,
receipt hợp lệ và confirmation đúng:

```text
STAGE HSK1 2.0
STAGE HSK2 2.0
STAGE HSK3 2.0
STAGE HSK4 2.0
STAGE HSK5 2.0
STAGE HSK6 2.0
STAGE HSK1 3.0
STAGE HSK2 3.0
STAGE HSK3 3.0
STAGE HSK4 3.0
STAGE HSK5 3.0
STAGE HSK6 3.0
STAGE HSK7_9 3.0
```

Tool upload BASE create-only rồi GET-verify, sau đó làm tương tự với PLUS và
ghi `deploy_receipt.json`. Bước này chưa publish catalog nên app chưa thấy
level mới.

Idempotency:

- remote absent → upload;
- cùng SHA → reuse PASS;
- khác SHA → hard fail;
- không overwrite và không delete.

Không upload Excel, JSON rời, audio rời, unpacked directory hoặc private key.

## 9. Deploy receipt

Receipt nằm tại:

```text
output/vocab/<version>/<level>/deploy_receipt.json
```

Receipt ghi level, data version, pack version, descriptor BASE/PLUS, SHA/bytes,
compatibility hash, remote verification, build receipt identity và
`catalogPublished` state. Chỉ receipt có cả BASE và PLUS remote verified mới
được dùng để publish. Không chỉnh receipt thủ công.

## 10. Signing key

Trạng thái:

- `SIGNING KEY NOT INITIALIZED`
- `SIGNING KEY READY`

Nút **Initialize Production Signing Key** yêu cầu:

```text
INITIALIZE VOCAB SIGNING KEY
```

Key dùng Ed25519, private seed raw 32 byte và public key raw 32 byte base64.
Private seed nằm ngoài repo tại:

```text
~/Library/Application Support/HSKVocabZipTool/keys/vocab-ed25519-v1.seed
```

Permission phải là `0600`. Public key B64 không phải secret và được dùng để
cấu hình Flutter một lần. Private seed tuyệt đối không được commit, dán vào
chat, log, upload, để trong `output/`, hoặc đưa vào Flutter.

Mất private seed thì không thể ký pointer mới cho app đang tin public key đó.
Không tự tạo key mới nếu production đã tin public key cũ; cần quy trình key
rotation riêng.

## 11. Initialize VOCAB POINTER

Nút **Initialize VOCAB POINTER** yêu cầu:

```text
INITIALIZE VOCAB POINTER
```

Chỉ dùng khi signing key READY và `current.json` chưa tồn tại. Pointer lần đầu
trỏ catalog combined v1 sau khi verify bytes/SHA/parser, tạo archive immutable
và GET-verify:

```text
catalogs/vocab/pointers/v1/vocab_catalog_pointer_v1.json
catalogs/vocab/current.json
```

`current.json` là pointer mutable duy nhất. Không bấm initialize lại khi
pointer đã ACTIVE.

## 12. Bước 3 — Publish Catalog + Signed Pointer

Nút **3. Publish Catalog + Signed Pointer** yêu cầu:

```text
PUBLISH VOCAB CATALOG
```

Thứ tự bắt buộc:

1. Verify receipt.
2. Đọc và verify current pointer.
3. Tải và verify catalog hiện hành.
4. Tạo catalog revision mới.
5. Upload catalog immutable create-only.
6. GET-verify catalog.
7. Tạo pointer revision mới.
8. Ký Ed25519 và verify chữ ký local.
9. Upload pointer archive immutable.
10. GET-verify pointer archive.
11. Update `catalogs/vocab/current.json` cuối cùng.
12. GET-verify `current.json`.
13. Báo `PUBLISHED`.

Nếu catalog upload PASS nhưng current update thất bại, catalog là inactive
artifact; không xóa và retry an toàn. ZIP, catalog revision và pointer archive
immutable; chỉ `current.json` được mutable/upsert.

## 13. Catalog revision

`catalogRevision` là revision của catalog immutable. `pointerRevision` là
revision của quyết định kích hoạt; hai số có thể khác nhau. Rollback vẫn tăng
pointer revision, ví dụ `pointerRevision 8 → catalogRevision 4`.

Catalog object:

```text
catalogs/vocab/combined/v<N>/vocab_pack_catalog_20_30_v<N>.json
```

Catalog replace theo khóa `(version, level, segment)`, nên HSK 2.0 và HSK 3.0
không duplicate nhau. Nếu chỉ thay pack version của một level đã có, entry count
giữ nguyên.

## 14. Thêm level mới và sửa level cũ

Level mới: Build → nghe thử → Upload + Verify → Publish catalog.

Level đã publish: sửa Excel/audio → tăng pack version → build ZIP mới → upload
object path mới → publish catalog revision mới → pointer trỏ catalog mới.

Không cần sửa app nếu schema, runtime contract và tập level vẫn tương thích.

## 15. Khi nào phải sửa app

Chỉ cần build app lại khi đổi pointer/catalog/manifest schema, đổi public key,
thêm loại level/resource mới, đổi BASE/VIP hoặc cache/runtime contract, hoặc
tăng `minAppBuild` vượt build đang phát hành.

Không cần sửa app chỉ vì thêm từ, sửa nghĩa/ví dụ/audio, thêm level HSK 2.0
hoặc HSK 3.0, tăng pack version hoặc publish catalog revision.

## 16. minAppBuild

`minAppBuild` nằm trong signed pointer. App có build thấp hơn sẽ từ chối catalog
mới và dùng LKG/bootstrap. Mặc định nên là `1`; không tự tăng theo
`catalogRevision`.

## 17. Pointer và Last-Known-Good trên app

App tải `current.json`, verify schema/Ed25519/replay, tải catalog immutable,
verify bytes/SHA, parse, activate và lưu last-known-good. Fallback là signed
pointer network → last-known-good → bootstrap catalog → UI unavailable. Pointer
lỗi không được làm mất catalog tốt đang dùng.

## 18. Public key và Flutter config

Cấu hình Flutter một lần:

```text
VOCAB_CATALOG_POINTER_URL
VOCAB_CATALOG_POINTER_PUBLIC_KEY_B64
VOCAB_CATALOG_POINTER_KEY_ID
```

Pointer URL canonical:

```text
https://bcyuovvlkfcvymjintpi.supabase.co/storage/v1/object/public/vocab-pack-staging/catalogs/vocab/current.json
```

Chỉ public key đưa vào app. Copy public key từ trạng thái `SIGNING KEY READY`;
không ghi private seed vào app hoặc tài liệu.

## 19. Trạng thái trên UI

- `SIGNING KEY NOT INITIALIZED`: chưa có key.
- `SIGNING KEY READY`: key tồn tại và có thể ký.
- `POINTER NOT INITIALIZED`: GET-verify xác nhận current.json thật sự chưa tồn tại.
- `POINTER STATUS UNKNOWN`: chưa thể đọc/verify do lỗi mạng hoặc dữ liệu; không được
  suy luận rằng pointer chưa từng được khởi tạo.
- `POINTER ACTIVE`: current đã verify.
- `LOCAL PASS`: ZIP local hợp lệ.
- `REMOTE PACKS VERIFIED`: BASE/PLUS đã verify.
- `CATALOG NOT PUBLISHED`: đã stage nhưng chưa activate.
- `CATALOG PUBLISHED`: current đã trỏ catalog mới.
- `CURRENT POINTER REVISION`: revision chống replay.
- `CURRENT CATALOG REVISION`: catalog immutable đang active.
- `Version`: hiển thị rõ HSK 2.0 hoặc HSK 3.0 cùng sheet, level và pack version.

## 20. Xử lý lỗi thường gặp

### Compatibility hash chưa PASS

Kiểm tra BASE/PLUS split theo policy đúng version, stable IDs và build lại,
không upload.

### Upload button disabled

Kiểm tra Local PASS, receipt, profile và xem build có đang chạy không.

### Remote object conflict

Object đã tồn tại khác SHA. Không overwrite; tăng pack version hoặc kiểm tra
nguồn dữ liệu.

### Pointer signature fail

Kiểm tra private key, keyId và serialization payload. Không cập nhật
`current.json`.

### current.json update fail

Catalog mới chưa active; giữ pointer cũ, retry sau, không xóa catalog mới.

### App không thấy level mới

Có thể mới chỉ upload pack, chưa publish catalog, pointer chưa update, app chưa
cấu hình public key, `minAppBuild` quá cao hoặc entry disabled.

### Audio cũ sau khi đổi audio

Kiểm tra `canonicalSource`; audio mới phải có content SHA mới, tăng pack version
và publish catalog revision mới.

### Mất private signing seed

Dừng publish, không tạo key mới tùy tiện. App production vẫn tin public key cũ;
cần quy trình key rotation có cập nhật app.

## 21. Quy trình khuyến nghị

```text
HSK1 2.0: Build → nghe thử → Upload + Verify
HSK2 2.0: Build → nghe thử → Upload + Verify
HSK3 2.0: Build → nghe thử → Upload + Verify
HSK4 2.0: Build → nghe thử → Upload + Verify
HSK5 2.0: Build → nghe thử → Upload + Verify
HSK6 2.0: Build → nghe thử → Upload + Verify
HSK1 3.0: Build → nghe thử → Upload + Verify
HSK2 3.0: Build → nghe thử → Upload + Verify
HSK3 3.0: Build → nghe thử → Upload + Verify
HSK4 3.0: Build → nghe thử → Upload + Verify
HSK5 3.0: Build → nghe thử → Upload + Verify
HSK6 3.0: Build → nghe thử → Upload + Verify
HSK7–9 3.0: Build → nghe thử → Upload + Verify

Sau khi tất cả remote verified: Publish Catalog + Signed Pointer một lần.
```

## 22. Checklist trước khi publish

- [ ] Đúng Excel, sheet, level và pack version.
- [ ] BASE/PLUS đúng policy của version đang chọn.
- [ ] Compatibility hash PASS.
- [ ] Đã nghe thử audio.
- [ ] ZIP local verify PASS.
- [ ] BASE/PLUS remote verify PASS.
- [ ] Receipt PASS.
- [ ] Signing key READY.
- [ ] Pointer ACTIVE.
- [ ] `minAppBuild` đúng.
- [ ] Confirmation phrase đúng.
- [ ] Không duplicate/conflict.
- [ ] Không có private key trong log/output.

## 23. Checklist sau khi publish

- [ ] Catalog upload và GET verify PASS.
- [ ] Pointer archive upload và GET verify PASS.
- [ ] `current.json` update/GET verify PASS.
- [ ] Signature PASS.
- [ ] Pointer/catalog revision đúng.
- [ ] Entry count đúng.
- [ ] UI báo `CATALOG PUBLISHED`.
- [ ] App online/restart nhận catalog.
- [ ] App offline dùng LKG.
- [ ] Pack/audio phát đúng.

## 24. Các file không được commit

Không dùng `git add .`. Không commit:

- `pipelines/.tts_cache/`
- `output/`
- private signing seed;
- credential/profile chứa secret;
- file tạm và artifact local ngoài source control.

## 25. Refresh Pointer Status và Publish theo level

Khi mở màn HSK 2.0 hoặc HSK 3.0, tool thực hiện GET-only để đọc và verify
`catalogs/vocab/current.json`, chữ ký Ed25519 và catalog mà pointer đang trỏ tới.

- Bấm `Refresh Pointer Status` để kiểm tra lại production pointer.
- Chỉ khi `current.json` thật sự không tồn tại mới hiển thị
  `POINTER NOT INITIALIZED` và cho phép `Initialize VOCAB POINTER`.
- Nếu lỗi mạng hoặc lỗi verify tạm thời, tool hiển thị `POINTER STATUS UNKNOWN`,
  không tự kết luận pointer chưa được khởi tạo.
- Khi pointer hợp lệ, UI hiển thị `POINTER ACTIVE`, pointer revision, catalog
  revision và số entry.
- Không initialize lại pointer revision 1 khi pointer hiện hành đã tồn tại.

Nút `3. Publish Catalog + Signed Pointer` luôn nằm ở hàng vận hành riêng để dễ
nhìn thấy. Nút chỉ mở khi signing key READY, pointer ACTIVE và receipt BASE/PLUS
của level đang chọn đã `REMOTE PACKS VERIFIED`. Publish dùng receipt của level
đang chọn; ví dụ HSK2 sẽ tạo catalog từ 14 entry hiện hành thành 16 entry.

Confirmation bắt buộc:

```text
PUBLISH VOCAB CATALOG
```

`current.json` chỉ được cập nhật sau khi catalog immutable và pointer archive đã
upload/GET-verify thành công.
