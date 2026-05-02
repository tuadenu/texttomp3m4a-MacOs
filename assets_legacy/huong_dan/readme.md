# Huong dan dung secrets

File `secrets.enc` la file luu tat ca secret da duoc ma hoa bang mat khau chung.

## Mat khau giai ma 20112012

- App se hoi mat khau khi khoi dong.
- Neu muon chay tu dong, co the set bien moi truong `TEXTTOMP3_VAULT_PASSWORD`.
- Goi y mat khau: "Sinh nhật Bảo Khiêm-Linh Dương"

## App se lam gi

Khi chay, app se:

1. Doc `secrets.enc`.
2. Giai ma va lay ra:
   - `config_default`
   - `client_secret.json`
3. Tu dong tao file local trong `AppData` neu chua co:
   - `AppData/config.json`
   - `AppData/client_secret.json`

## Luu y

- Khong sua truc tiep `secrets.enc` bang editor text.
- Neu can cap nhat secret, sua local truoc, sau do ma hoa lai file vault.
- Repo nay chi nen giu file ma hoa, khong giu ban plaintext cua key/secret.
- Neu may dich chua co thu vien `cryptography`, can cai them truoc khi chay app.
