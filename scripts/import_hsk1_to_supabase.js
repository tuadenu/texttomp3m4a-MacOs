const fs = require('node:fs/promises');
const path = require('node:path');
require('dotenv').config();

const { createClient } = require('@supabase/supabase-js');

const SUPABASE_URL = process.env.SUPABASE_URL;
const SUPABASE_SERVICE_ROLE_KEY = process.env.SUPABASE_SERVICE_ROLE_KEY;

const BUCKET = process.env.SUPABASE_BUCKET || 'audio';
const STORAGE_FOLDER = process.env.SUPABASE_STORAGE_FOLDER || 'hsk1';
const TABLE_NAME = process.env.SUPABASE_TABLE || 'vocab';

const JSON_PATH = process.env.JSON_PATH || 'output/hsk1_20/output_vocab.json';
const AUDIO_DIR = process.env.AUDIO_DIR || 'output/hsk1_20/audio';
const LEVEL = process.env.LEVEL || 'hsk1';
const DECK = process.env.DECK || path.basename(path.dirname(JSON_PATH));
const SHEET_NAME = DECK;
const INSERT_BATCH_SIZE = Number(process.env.INSERT_BATCH_SIZE || 100);
const OVERWRITE_EXISTING_UPLOAD = String(process.env.OVERWRITE_EXISTING_UPLOAD || 'false').toLowerCase() === 'true';
const SKIP_EXISTING_UPLOAD = String(process.env.SKIP_EXISTING_UPLOAD || 'true').toLowerCase() !== 'false';
const CREATE_BUCKET_IF_MISSING =
  String(process.env.CREATE_BUCKET_IF_MISSING || 'true').toLowerCase() !== 'false';
const ALLOW_INSERT_WHEN_UPLOAD_FAILED =
  String(process.env.ALLOW_INSERT_WHEN_UPLOAD_FAILED || 'false').toLowerCase() === 'true';

const FIELD_MAP = {
  word: 'word',
  meaning: 'meaning',
  audio: 'audio',
  example: 'example',
  example_vi: 'example_vi',
  audio_url: 'audio_url',
  level: 'level',
  deck: 'deck',
  version: 'version',
  index: 'index'
};

function getVersionFromSheet(sheetName) {
  if (sheetName.includes('_20')) return '2.0';
  if (sheetName.includes('_30')) return '3.0';
  return null;
}

function getContentTypeByExt(filename) {
  const ext = path.extname(filename).toLowerCase();
  if (ext === '.m4a') return 'audio/mp4';
  if (ext === '.mp3') return 'audio/mpeg';
  if (ext === '.wav') return 'audio/wav';
  if (ext === '.ogg') return 'audio/ogg';
  return 'application/octet-stream';
}

function chunkArray(arr, size) {
  const out = [];
  for (let i = 0; i < arr.length; i += size) {
    out.push(arr.slice(i, i + size));
  }
  return out;
}

async function getAllExistingFileNames(supabase) {
  const existing = new Set();
  let offset = 0;
  const limit = 1000;

  while (true) {
    const { data, error } = await supabase.storage
      .from(BUCKET)
      .list(STORAGE_FOLDER, {
        limit,
        offset,
        sortBy: { column: 'name', order: 'asc' }
      });

    if (error) {
      throw new Error(`Không thể list file trong storage: ${error.message}`);
    }

    if (!data || data.length === 0) {
      break;
    }

    for (const item of data) {
      if (item && item.name) {
        existing.add(item.name);
      }
    }

    if (data.length < limit) {
      break;
    }

    offset += limit;
  }

  return existing;
}

async function getExistingAudioUrlsForDeck(supabase) {
  const existing = new Set();
  let offset = 0;
  const limit = 1000;

  while (true) {
    const { data, error } = await supabase
      .from(TABLE_NAME)
      .select('audio_url')
      .eq('deck', DECK)
      .range(offset, offset + limit - 1);

    if (error) {
      throw new Error(`Không thể đọc audio_url đã có trong DB: ${error.message}`);
    }

    if (!data || data.length === 0) {
      break;
    }

    for (const item of data) {
      if (item && item.audio_url) {
        existing.add(String(item.audio_url));
      }
    }

    if (data.length < limit) {
      break;
    }

    offset += limit;
  }

  return existing;
}

async function ensureBucketExists(supabase) {
  const { data, error } = await supabase.storage.getBucket(BUCKET);

  if (!error && data) {
    return;
  }

  const lowerMessage = String(error?.message || '').toLowerCase();
  const notFound = lowerMessage.includes('not found') || lowerMessage.includes('does not exist');

  if (!notFound) {
    throw new Error(`Không thể kiểm tra bucket ${BUCKET}: ${error?.message || 'Unknown error'}`);
  }

  if (!CREATE_BUCKET_IF_MISSING) {
    throw new Error(
      `Bucket ${BUCKET} chưa tồn tại. Hãy tạo bucket này trong Supabase Storage hoặc bật CREATE_BUCKET_IF_MISSING=true.`
    );
  }

  console.log(`[Preflight] Bucket ${BUCKET} chưa tồn tại, đang tạo mới...`);

  const { error: createError } = await supabase.storage.createBucket(BUCKET, {
    public: true,
    fileSizeLimit: null,
    allowedMimeTypes: null
  });

  if (createError) {
    throw new Error(
      `Không thể tạo bucket ${BUCKET}: ${createError.message}. Kiểm tra lại quyền của SUPABASE_SERVICE_ROLE_KEY.`
    );
  }

  console.log(`[Preflight] Đã tạo bucket ${BUCKET} (public).`);
}

async function ensureTableAccessible(supabase) {
  const { error } = await supabase.from(TABLE_NAME).select('*', { head: true, count: 'exact' }).limit(1);

  if (!error) {
    return;
  }

  const lowerMessage = String(error.message || '').toLowerCase();
  const tableMissing =
    lowerMessage.includes('could not find the table') ||
    lowerMessage.includes('relation') ||
    lowerMessage.includes('does not exist');

  if (tableMissing) {
    throw new Error(
      `Table public.${TABLE_NAME} chưa tồn tại trên Supabase. Hãy tạo table trước khi chạy script.`
    );
  }

  throw new Error(`Không thể truy cập table ${TABLE_NAME}: ${error.message}`);
}

function mapRecordToTableColumns(record) {
  const mapped = {};

  for (const [sourceKey, targetKey] of Object.entries(FIELD_MAP)) {
    if (record[sourceKey] !== undefined) {
      mapped[targetKey] = record[sourceKey];
    }
  }

  for (const [key, value] of Object.entries(record)) {
    if (!(key in FIELD_MAP)) {
      mapped[key] = value;
    }
  }

  return mapped;
}

async function main() {
  if (!SUPABASE_URL || !SUPABASE_SERVICE_ROLE_KEY) {
    throw new Error('Thiếu SUPABASE_URL hoặc SUPABASE_SERVICE_ROLE_KEY trong .env');
  }

  const supabase = createClient(SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY, {
    auth: { persistSession: false }
  });

  const absoluteJsonPath = path.resolve(JSON_PATH);
  const absoluteAudioDir = path.resolve(AUDIO_DIR);

  console.log('=== Bắt đầu import vocab HSK1 lên Supabase ===');
  console.log(`JSON: ${absoluteJsonPath}`);
  console.log(`Audio dir: ${absoluteAudioDir}`);
  console.log(`Storage: ${BUCKET}/${STORAGE_FOLDER}`);
  console.log(`Table: ${TABLE_NAME}`);
  console.log(`Deck: ${DECK}`);
  console.log(`Version: ${getVersionFromSheet(SHEET_NAME)}`);
  console.log(`Mode: ${OVERWRITE_EXISTING_UPLOAD ? 'overwrite' : 'add_missing'}`);

  await ensureBucketExists(supabase);
  await ensureTableAccessible(supabase);

  const jsonRaw = await fs.readFile(absoluteJsonPath, 'utf8');
  const jsonData = JSON.parse(jsonRaw);

  if (!Array.isArray(jsonData)) {
    throw new Error('JSON phải là một mảng object.');
  }

  const dirEntries = await fs.readdir(absoluteAudioDir, { withFileTypes: true });
  const audioFiles = dirEntries
    .filter((entry) => entry.isFile())
    .map((entry) => entry.name)
    .sort();

  if (audioFiles.length === 0) {
    throw new Error('Không tìm thấy file audio nào trong thư mục audio.');
  }

  const shouldSkipExistingUpload = !OVERWRITE_EXISTING_UPLOAD && SKIP_EXISTING_UPLOAD;
  const existingFiles = shouldSkipExistingUpload ? await getAllExistingFileNames(supabase) : new Set();
  let uploadedCount = 0;
  let skippedCount = 0;
  let failedUploads = 0;

  console.log(`\n[Upload] Tổng file local: ${audioFiles.length}`);
  if (shouldSkipExistingUpload) {
    console.log(`[Upload] File đã tồn tại trên storage: ${existingFiles.size}`);
  }

  for (let i = 0; i < audioFiles.length; i += 1) {
    const filename = audioFiles[i];
    const storagePath = `${STORAGE_FOLDER}/${filename}`;

    if (shouldSkipExistingUpload && existingFiles.has(filename)) {
      skippedCount += 1;
      console.log(`[Upload ${i + 1}/${audioFiles.length}] Skip tồn tại: ${filename}`);
      continue;
    }

    const localPath = path.join(absoluteAudioDir, filename);

    try {
      const fileBuffer = await fs.readFile(localPath);
      const { error } = await supabase.storage.from(BUCKET).upload(storagePath, fileBuffer, {
        contentType: getContentTypeByExt(filename),
        upsert: OVERWRITE_EXISTING_UPLOAD
      });

      if (error) {
        failedUploads += 1;
        console.error(`[Upload ${i + 1}/${audioFiles.length}] Lỗi ${filename}: ${error.message}`);
      } else {
        uploadedCount += 1;
        console.log(`[Upload ${i + 1}/${audioFiles.length}] OK: ${filename}`);
      }
    } catch (err) {
      failedUploads += 1;
      console.error(`[Upload ${i + 1}/${audioFiles.length}] Exception ${filename}: ${err.message}`);
    }
  }

  console.log(`\n[Upload] Hoàn tất: uploaded=${uploadedCount}, skipped=${skippedCount}, failed=${failedUploads}`);

  if (failedUploads > 0 && !ALLOW_INSERT_WHEN_UPLOAD_FAILED) {
    throw new Error(
      `Có ${failedUploads} file upload thất bại. Dừng trước khi insert để tránh dữ liệu audio_url không hợp lệ.`
    );
  }

  const transformed = jsonData.map((item, idx) => {
    const originalAudio = String(item.audio || '');
    const audioFileName = path.basename(originalAudio);
    const storagePath = `${STORAGE_FOLDER}/${audioFileName}`;
    const publicUrl = supabase.storage.from(BUCKET).getPublicUrl(storagePath).data.publicUrl;

    const withAddedFields = {
      ...item,
      audio_url: publicUrl,
      level: LEVEL,
      deck: DECK,
      version: getVersionFromSheet(SHEET_NAME),
      index: idx + 1
    };

    return mapRecordToTableColumns(withAddedFields);
  });

  if (transformed.length > 0) {
    const sample = transformed[0];
    const sampleUrl = sample.audio_url || '(không có audio_url)';
    console.log(`\n[Bonus] Public URL mẫu: ${sampleUrl}`);
  }

  const existingAudioUrls = await getExistingAudioUrlsForDeck(supabase);
  const recordsToInsert = [];
  let skippedExistingDb = 0;
  const seenAudioUrls = new Set(existingAudioUrls);

  for (const record of transformed) {
    const audioUrl = String(record.audio_url || '').trim();
    if (audioUrl && seenAudioUrls.has(audioUrl)) {
      skippedExistingDb += 1;
      console.log(`[Insert] Skip trùng DB: ${record.word || audioUrl}`);
      continue;
    }

    if (audioUrl) {
      seenAudioUrls.add(audioUrl);
    }

    recordsToInsert.push(record);
  }

  const batches = chunkArray(recordsToInsert, INSERT_BATCH_SIZE);
  let insertedTotal = 0;

  console.log(`\n[Insert] Tổng record: ${transformed.length}`);
  console.log(`[Insert] Đã có sẵn trong DB: ${existingAudioUrls.size}`);
  console.log(`[Insert] Skip trùng DB: ${skippedExistingDb}`);
  console.log(`[Insert] Record mới cần insert: ${recordsToInsert.length}`);
  console.log(`[Insert] Batch size: ${INSERT_BATCH_SIZE}`);
  console.log(`[Insert] Số batch: ${batches.length}`);

  if (recordsToInsert.length === 0) {
    console.log('\n[Insert] Không có record mới nào để insert.');
    if (!OVERWRITE_EXISTING_UPLOAD) {
      console.log('[Insert] STATUS: NO_NEW_RECORDS');
    }
    console.log('\n=== Hoàn thành toàn bộ quy trình ===');
    return;
  }

  for (let i = 0; i < batches.length; i += 1) {
    const batch = batches[i];
    const from = insertedTotal + 1;
    const to = insertedTotal + batch.length;

    const { error } = await supabase.from(TABLE_NAME).insert(batch);

    if (error) {
      throw new Error(
        `[Insert batch ${i + 1}/${batches.length}] Lỗi khi insert record ${from}-${to}: ${error.message}`
      );
    }

    insertedTotal += batch.length;
    console.log(`[Insert batch ${i + 1}/${batches.length}] OK: ${from}-${to}`);
  }

  console.log(`\n[Insert] Hoàn tất: inserted=${insertedTotal}`);
  console.log('\n=== Hoàn thành toàn bộ quy trình ===');
}

main().catch((err) => {
  console.error('\n=== SCRIPT FAILED ===');
  console.error(err.message);
  process.exit(1);
});
