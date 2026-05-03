# HSK Import Playbook (Supabase)

Tai lieu nay dung lai cho cac sheet/deck khac nhu `hsk2_20`, `hsk3_30`, ...

## 1) Migration deck (chi can chay 1 lan)

Da co file migration mau:

- `sql/20260502_add_deck_to_vocab.sql`

No se:

- add column `deck` cho `public.vocab`
- backfill du lieu cu: `level='hsk1' -> deck='hsk1_20'`

## 1.1) Migration version (chi can chay 1 lan)

Da co file migration mau:

- `sql/20260502_add_version_to_vocab.sql`

No se:

- add column `version` cho `public.vocab`
- backfill du lieu cu: `deck like '%_20' -> version='2.0'`

## 2) Import deck moi

Script su dung:

- `scripts/import_hsk1_to_supabase.js`

Logic quan trong da co san:

- `deck` duoc gan tu thu muc cha cua `JSON_PATH`
- co the override bang bien moi truong `DECK`
- `version` duoc suy ra tu `sheetName`:
	- co `_20` -> `2.0`
	- co `_30` -> `3.0`

## 3) Command mau cho deck moi

### Cach A: Truyen bien ngay tren command

```bash
export PATH="$PWD/.tools/node-v20.20.2-darwin-x64/bin:$PATH"
LEVEL=hsk3 \
JSON_PATH=output/hsk3_30/output_vocab.json \
AUDIO_DIR=output/hsk3_30/audio \
SUPABASE_STORAGE_FOLDER=hsk3 \
DECK=hsk3_30 \
node scripts/import_hsk1_to_supabase.js
```

### Cach B: Sua `.env` roi chay

Cap nhat cac bien:

- `LEVEL`
- `JSON_PATH`
- `AUDIO_DIR`
- `SUPABASE_STORAGE_FOLDER`
- `DECK` (khuyen nghi set ro rang)

Sau do chay:

```bash
export PATH="$PWD/.tools/node-v20.20.2-darwin-x64/bin:$PATH"
node scripts/import_hsk1_to_supabase.js
```

## 4) SQL check nhanh sau import

```sql
select level, deck, version, count(*)
from public.vocab
group by level, deck, version
order by level, deck, version;
```

## 5) Luu y

- `SKIP_EXISTING_UPLOAD=true` se bo qua audio da co tren Storage.
- Neu import lai cung `level + index` ma bi duplicate, can xoa du lieu cu hoac doi dataset/index.
- Khong can doi ten script hien tai.
