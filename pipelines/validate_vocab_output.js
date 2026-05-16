#!/usr/bin/env node
const fs = require("fs");
const path = require("path");

function toPosix(input) {
  return input.replace(/\\/g, "/");
}

function readMetadata(outputRoot) {
  const metadataPath = path.join(outputRoot, "output_vocab_metadata.json");
  if (!fs.existsSync(metadataPath)) {
    return { metadataPath, metadata: null };
  }

  try {
    const raw = fs.readFileSync(metadataPath, "utf8");
    return { metadataPath, metadata: JSON.parse(raw) };
  } catch (err) {
    return { metadataPath, metadata: { __error: err.message } };
  }
}

function validateVocabOutput({ excelFilePath, sheetName, outputRoot }) {
  const errors = [];

  const jsonPath = path.join(outputRoot, "output_vocab.json");
  const audioDir = path.join(outputRoot, "audio");

  if (!fs.existsSync(jsonPath)) {
    errors.push(`Missing JSON file: ${jsonPath}`);
    return { ok: false, total: 0, errors, jsonPath, audioDir };
  }

  let items;
  try {
    const raw = fs.readFileSync(jsonPath, "utf8");
    items = JSON.parse(raw);
  } catch (err) {
    errors.push(`Invalid JSON parse: ${err.message}`);
    return { ok: false, total: 0, errors, jsonPath, audioDir };
  }

  if (!Array.isArray(items)) {
    errors.push("Malformed JSON: root must be an array of objects.");
    return { ok: false, total: 0, errors, jsonPath, audioDir };
  }

  const total = items.length;
  const { metadataPath, metadata } = readMetadata(outputRoot);

  if (!metadata) {
    errors.push(`Missing metadata file: ${metadataPath}`);
  } else if (metadata.__error) {
    errors.push(`Invalid metadata JSON: ${metadata.__error}`);
  }

  const expectedCount = Number(metadata?.expected_count);
  if (Number.isFinite(expectedCount)) {
    if (total !== expectedCount) {
      errors.push(`Invalid item count: got ${total}, expected ${expectedCount}.`);
    }
  } else if (metadata) {
    errors.push(`Missing expected_count in metadata: ${metadataPath}`);
  }

  const wordSeen = new Map();
  const requiredFields = ["word", "meaning", "example", "audio"];
  const expectedAudioPrefix = `${sheetName}_`;
  const filenameRegex = new RegExp(`^${sheetName}_(\\d{3,})_.+\\.m4a$`);
  const foundIndexes = [];

  for (let i = 0; i < items.length; i += 1) {
    const entry = items[i];
    const at = i + 1;

    if (!entry || typeof entry !== "object" || Array.isArray(entry)) {
      errors.push(`Malformed entry at index ${at}: must be an object.`);
      continue;
    }

    for (const field of requiredFields) {
      const value = entry[field];
      if (value == null || String(value).trim() === "") {
        errors.push(`Missing/empty field '${field}' at index ${at}.`);
      }
    }

    const word = String(entry.word || "").trim();
    if (word) {
      if (wordSeen.has(word)) {
        errors.push(`Duplicate word '${word}' at indexes ${wordSeen.get(word)} and ${at}.`);
      } else {
        wordSeen.set(word, at);
      }
    }

    const audioRel = toPosix(String(entry.audio || "").trim());
    if (!audioRel) {
      continue;
    }

    const audioFileName = path.basename(audioRel);
    if (!audioFileName.startsWith(expectedAudioPrefix)) {
      errors.push(
        `Invalid audio filename prefix at index ${at}: ${audioFileName} (expected prefix ${expectedAudioPrefix}).`
      );
    }

    const match = audioFileName.match(filenameRegex);
    if (!match) {
      errors.push(
        `Invalid audio filename format at index ${at}: ${audioFileName} (expected ${sheetName}_XXX_*.m4a).`
      );
    } else {
      foundIndexes.push(Number.parseInt(match[1], 10));
    }

    const absoluteAudio = path.resolve(outputRoot, audioRel);
    if (!fs.existsSync(absoluteAudio)) {
      errors.push(`Missing audio file at index ${at}: ${audioRel}`);
    }
  }

  if (foundIndexes.length) {
    const sorted = [...foundIndexes].sort((a, b) => a - b);
    const expectedMax = total;

    for (let i = 1; i <= expectedMax; i += 1) {
      if (sorted[i - 1] !== i) {
        errors.push(`Audio index sequence is not continuous at ${String(i).padStart(3, "0")}.`);
        break;
      }
    }
  }

  if (!fs.existsSync(audioDir)) {
    errors.push(`Missing audio directory: ${audioDir}`);
  }

  return {
    ok: errors.length === 0,
    total,
    errors,
    jsonPath,
    audioDir,
    expectedCount,
  };
}

function printReport(result) {
  if (result.ok) {
    console.log("STATUS: PASS");
    console.log(`TOTAL: ${result.total}`);
    return;
  }

  console.log("STATUS: FAIL");
  for (const err of result.errors) {
    console.log(`- ${err}`);
  }
}

function parseArgv(argv) {
  const args = {
    excelFilePath: null,
    sheetName: null,
    outputRoot: null,
  };

  for (let i = 2; i < argv.length; i += 1) {
    const token = argv[i];
    if (token === "--excel") {
      args.excelFilePath = argv[i + 1];
      i += 1;
    } else if (token === "--sheet") {
      args.sheetName = argv[i + 1];
      i += 1;
    } else if (token === "--output") {
      args.outputRoot = argv[i + 1];
      i += 1;
    }
  }

  if (!args.excelFilePath || !args.sheetName) {
    throw new Error("Usage: node pipelines/validate_vocab_output.js --excel <excel_file> --sheet <sheet_name> [--output <output_dir>]");
  }

  if (!args.outputRoot) {
    args.outputRoot = path.join(process.cwd(), "output", args.sheetName);
  }

  return args;
}

if (require.main === module) {
  try {
    const args = parseArgv(process.argv);
    const result = validateVocabOutput(args);
    printReport(result);
    process.exit(result.ok ? 0 : 1);
  } catch (err) {
    console.log("STATUS: FAIL");
    console.log(`- ${err.message}`);
    process.exit(1);
  }
}

module.exports = {
  validateVocabOutput,
};
