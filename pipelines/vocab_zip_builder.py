#!/usr/bin/env python3
"""Local-only deterministic HSK 2.0/3.0 vocabulary ZIP builder.

This module intentionally has no Supabase client and performs no network I/O.
It produces the same catalog contract for both vocabulary standards without
touching the legacy row-import workflow.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import re
import shutil
import tempfile
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterable

import pandas as pd

# Reuse the proven word-audio generation and pinyin naming code.  Do not alter
# the legacy module; this new workflow simply consumes its stable helpers.
# Support both ``python -m pipelines.vocab_zip_builder`` (tests) and the
# direct script invocation used by the Tkinter application.
try:
    from pipelines.vocab_pipeline import _build_word_audio, _export_m4a, _to_pinyin_slug
except ModuleNotFoundError:
    from vocab_pipeline import _build_word_audio, _export_m4a, _to_pinyin_slug


SUPPORTED_VERSIONS = ("2.0", "3.0")
SUPPORTED_LEVELS = ("hsk1", "hsk2", "hsk3", "hsk4", "hsk5", "hsk6", "hsk7_9")
SHEET_SELECTIONS = {
    **{f"hsk{number}_20": ("2.0", f"hsk{number}") for number in range(1, 7)},
    **{f"hsk{number}_30": ("3.0", f"hsk{number}") for number in range(1, 7)},
    "hsk7_9_30": ("3.0", "hsk7_9"),
    "hsk7-9_30": ("3.0", "hsk7_9"),
}
FIXED_CREATED_AT = "2000-01-01T00:00:00Z"
FIXED_ZIP_DATE = (2000, 1, 1, 0, 0, 0)
PACK_VERSION = 1


class BuildValidationError(ValueError):
    """A strict source or artifact validation failure."""


@dataclass(frozen=True)
class SourceVocab:
    index: int
    word: str
    meaning: str
    example: str
    example_meaning: str

    @property
    def stable_id(self) -> str:
        return str(self.index)


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _json_bytes(value: object) -> bytes:
    # The separators, key insertion order, UTF-8 encoding and final newline are
    # part of the artifact format and must remain stable across builds.
    return (json.dumps(value, ensure_ascii=False, separators=(",", ":"), allow_nan=False) + "\n").encode("utf-8")


def _write_json(path: Path, value: object) -> bytes:
    payload = _json_bytes(value)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(payload)
    return payload


def _clean_cell(value: object) -> str:
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return ""
    return str(value).strip()


def _canonical_column_name(value: object) -> str:
    return re.sub(r"\s+", " ", str(value).strip().lower())


def _resolve_columns(frame: pd.DataFrame) -> dict[str, object]:
    columns = {_canonical_column_name(column): column for column in frame.columns}
    aliases = {
        "index": ("index", "stt", "số thứ tự"),
        "word": ("word", "中文", "từ vựng"),
        "meaning": ("meaning_vi", "nghĩa tiếng việt", "nghĩa"),
        "example": ("example_zh", "ví dụ (中文)", "ví dụ"),
        "example_meaning": ("example_vi", "nghĩa ví dụ"),
    }
    resolved: dict[str, object] = {}
    missing: list[str] = []
    for field, choices in aliases.items():
        match = next((columns[choice] for choice in choices if choice in columns), None)
        if match is None:
            missing.append(choices[0])
        else:
            resolved[field] = match
    if missing:
        raise BuildValidationError("Thiếu cột bắt buộc: " + ", ".join(missing))
    return resolved


def load_excel_strict(excel_path: str | Path, sheet_name: str) -> list[SourceVocab]:
    """Load exactly the requested source schema without dropping or renumbering rows."""
    frame = pd.read_excel(excel_path, sheet_name=sheet_name)
    columns = _resolve_columns(frame)
    items: list[SourceVocab] = []
    errors: list[str] = []
    seen_indexes: set[int] = set()

    for excel_row, (_, row) in enumerate(frame.iterrows(), start=2):
        raw_index = row[columns["index"]]
        if raw_index is None or (isinstance(raw_index, float) and pd.isna(raw_index)) or _clean_cell(raw_index) == "":
            errors.append(f"Dòng Excel {excel_row}: thiếu index")
            continue
        try:
            parsed = int(raw_index)
            if isinstance(raw_index, float) and raw_index != parsed:
                raise ValueError
            if str(raw_index).strip().isdigit() is False and not (isinstance(raw_index, float) and raw_index == parsed):
                raise ValueError
        except (TypeError, ValueError):
            errors.append(f"Dòng Excel {excel_row}: index phải là số nguyên")
            continue
        if parsed in seen_indexes:
            errors.append(f"Dòng Excel {excel_row}: trùng index {parsed}")
        seen_indexes.add(parsed)

        item = SourceVocab(
            index=parsed,
            word=_clean_cell(row[columns["word"]]),
            meaning=_clean_cell(row[columns["meaning"]]),
            example=_clean_cell(row[columns["example"]]),
            example_meaning=_clean_cell(row[columns["example_meaning"]]),
        )
        for name, value in (("word", item.word), ("meaning", item.meaning), ("example", item.example), ("example_meaning", item.example_meaning)):
            if not value:
                errors.append(f"Dòng Excel {excel_row}, index {parsed}: thiếu {name}")
        items.append(item)

    actual_indexes = sorted(item.index for item in items)
    expected_indexes = list(range(1, len(items) + 1))
    if actual_indexes != expected_indexes:
        errors.append("index phải bắt đầu từ 1, liên tục và không có gap")
    if errors:
        raise BuildValidationError("; ".join(errors))
    return sorted(items, key=lambda item: item.index)


def audio_filename(sheet_name: str, item: SourceVocab) -> str:
    return f"{sheet_name}_{item.index:03d}_{_to_pinyin_slug(item.word)}.m4a"


def canonical_audio_uri(version: str, level: str, item: SourceVocab, content_hash: str | None = None) -> str:
    base = f"vocab://{version}/{level}/{item.stable_id}/audio"
    return f"{base}/{content_hash}" if content_hash else base


def _ordered_ids_hash(items: Iterable[SourceVocab]) -> str:
    # Flutter/HSK2 contract: compact JSON UTF-8, with no trailing newline.
    ordered = [item.stable_id for item in items]
    payload = json.dumps(ordered, ensure_ascii=False, separators=(",", ":"), sort_keys=False).encode("utf-8")
    return _sha256_bytes(payload)


def _validate_level(level: str) -> None:
    if level not in SUPPORTED_LEVELS:
        raise BuildValidationError("Level không hỗ trợ: " + level)


def _normalize_sheet_name(sheet_name: str) -> str:
    return str(sheet_name or "").strip().lower().replace("-", "_")


def _validate_version(version: str) -> str:
    value = str(version or "").strip()
    if value not in SUPPORTED_VERSIONS:
        raise BuildValidationError("Vocab version không hỗ trợ: " + value)
    return value


def resolve_sheet_selection(sheet_name: str, version: str | None = None, level: str | None = None) -> tuple[str, str]:
    """Return the canonical version/level and reject every mismatched tuple."""
    try:
        expected_version, expected_level = SHEET_SELECTIONS[_normalize_sheet_name(sheet_name)]
    except KeyError as exc:
        raise BuildValidationError("Sheet vocab không hỗ trợ: " + str(sheet_name)) from exc
    if version is not None and _validate_version(version) != expected_version:
        raise BuildValidationError(f"Sheet {sheet_name} thuộc HSK {expected_version}, không phải HSK {version}.")
    if level is not None and str(level).strip().lower() != expected_level:
        raise BuildValidationError(f"Sheet {sheet_name} thuộc level {expected_level}, không phải {level}.")
    return expected_version, expected_level


def audio_cache_key(item: SourceVocab, *, engine: str, speed: str, voice: str, profile: str, bitrate: str, audio_mode: str) -> str:
    """Content-address cache identity: a changed voice/profile/text cannot reuse M4A."""
    value = "\0".join((engine, voice, speed, profile, bitrate, audio_mode, item.word, item.meaning))
    return _sha256_bytes(value.encode("utf-8"))


def _source_audio_path(audio_root: Path, sheet_name: str, item: SourceVocab, *, engine: str, speed: str, voice: str, profile: str, bitrate: str, audio_mode: str) -> Path:
    return audio_root / f"{audio_cache_key(item, engine=engine, speed=speed, voice=voice, profile=profile, bitrate=bitrate, audio_mode=audio_mode)}.m4a"


def ensure_audio(
    items: list[SourceVocab],
    audio_root: Path,
    sheet_name: str,
    engine: str,
    speed: str,
    voice: str,
    bitrate: str,
    generate_missing: bool,
    progress: Callable[[str], None] | None = None,
    audio_mode: str = "zh_vi",
    profile: str = "",
    force_regenerate: bool = False,
) -> tuple[int, int]:
    """Reuse non-empty M4A files or generate them through the existing TTS core."""
    audio_root.mkdir(parents=True, exist_ok=True)
    reused = generated = 0
    for position, item in enumerate(items, start=1):
        target = _source_audio_path(audio_root, sheet_name, item, engine=engine, speed=speed, voice=voice, profile=profile, bitrate=bitrate, audio_mode=audio_mode)
        if not force_regenerate and target.is_file() and target.stat().st_size > 0:
            reused += 1
            if progress:
                progress(f"Audio {position}/{len(items)}: dùng lại {target.name}")
            continue
        if not generate_missing:
            raise BuildValidationError(f"Thiếu audio: {target}")
        if progress:
            progress(f"Audio {position}/{len(items)}: tạo {target.name}")
        # Write atomically so cancelling the subprocess cannot leave a
        # partially encoded, non-empty M4A that a later build would reuse.
        partial_target = target.with_name(target.name + ".part")
        try:
            if partial_target.exists():
                partial_target.unlink()
            audio = _build_word_audio(item.word, item.meaning, engine, speed, voice, audio_mode)
            _export_m4a(audio, str(partial_target), bitrate)
            if not partial_target.is_file() or partial_target.stat().st_size == 0:
                raise BuildValidationError(f"TTS tạo audio rỗng: {target}")
            os.replace(partial_target, target)
        except BuildValidationError:
            raise
        except Exception as exc:
            raise BuildValidationError(
                f"TTS thất bại tại index {item.index} ({item.word!r}); không tạo audio im lặng: {exc}"
            ) from exc
        finally:
            try:
                if partial_target.exists():
                    partial_target.unlink()
            except OSError:
                pass
        generated += 1
    return reused, generated


def _vocab_item(version: str, level: str, item: SourceVocab, audio_url: str | None = None) -> dict[str, object]:
    return {
        "id": item.stable_id,
        "level": level,
        "version": version,
        "index": item.index,
        "word": item.word,
        "meaning": item.meaning,
        "audio_url": audio_url or canonical_audio_uri(version, level, item),
        "example": item.example,
        "example_meaning": item.example_meaning,
    }


def _manifest(version: str, level: str, segment: str, items: list[SourceVocab], resources: list[dict[str, object]], base_hash: str | None, base_count: int, pack_version: int = PACK_VERSION) -> dict[str, object]:
    pack_id = f"vocab:{version}:{level}:{segment}:v{pack_version}"
    manifest: dict[str, object] = {
        "schemaVersion": 2,
        "packId": pack_id,
        "packVersion": pack_version,
        "level": level,
        "standardVersion": version,
        "datasetVersion": version,
        "zipSha256": "",
        "resources": resources,
        "createdAt": FIXED_CREATED_AT,
        "orderedVocabIds": [item.stable_id for item in items],
        "orderedVocabIdsSha256": _ordered_ids_hash(items),
        "packType": "vocab",
        "segment": segment,
        "accessTier": "base" if segment == "base" else "vip",
        "vocabCount": len(items),
        "resourceCount": len(resources),
    }
    if segment == "base":
        manifest["previewWordCount"] = base_count
    else:
        assert base_hash is not None
        manifest.update(
            {
                "requiresPackId": f"vocab:{version}:{level}:base:v{pack_version}",
                "compatibleBaseVersion": pack_version,
                "remainingVocabCount": len(items),
                "baseOrderedVocabIdsSha256": base_hash,
            }
        )
    return manifest


def _load_previous_audio_state(segment_root: Path, current_pack_version: int) -> tuple[dict[str, str], dict[str, str]]:
    """Return the newest prior audio URI/hash map for the same level segment."""
    candidates: list[tuple[int, Path]] = []
    if segment_root.is_dir():
        for path in segment_root.glob("v*/unpacked"):
            match = re.fullmatch(r"v(\d+)", path.parent.name)
            if not match:
                continue
            version = int(match.group(1))
            if version < 1 or version > current_pack_version:
                continue
            candidates.append((version, path))
    candidates.sort(key=lambda item: item[0], reverse=True)
    for _, unpacked in candidates:
        try:
            previous_vocab = json.loads((unpacked / "vocab.json").read_text(encoding="utf-8"))
            previous_manifest = json.loads((unpacked / "manifest.json").read_text(encoding="utf-8"))
        except (OSError, ValueError, json.JSONDecodeError):
            continue
        previous_audio_urls: dict[str, str] = {}
        previous_resource_hashes: dict[str, str] = {}
        if isinstance(previous_vocab, list):
            previous_audio_urls = {
                str(item.get("id")): str(item.get("audio_url"))
                for item in previous_vocab
                if isinstance(item, dict) and item.get("id") and item.get("audio_url")
            }
        if isinstance(previous_manifest, dict) and isinstance(previous_manifest.get("resources"), list):
            for resource in previous_manifest["resources"]:
                if isinstance(resource, dict) and resource.get("type") == "vocab_audio":
                    previous_resource_hashes[str(resource.get("canonicalSource"))] = str(resource.get("sha256"))
        return previous_audio_urls, previous_resource_hashes
    return {}, {}


def _write_deterministic_zip(unpacked: Path, zip_path: Path) -> None:
    names = sorted(path.relative_to(unpacked).as_posix() for path in unpacked.rglob("*") if path.is_file())
    zip_path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9, strict_timestamps=True) as archive:
        for name in names:
            info = zipfile.ZipInfo(filename=name, date_time=FIXED_ZIP_DATE)
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o100644 << 16
            info.flag_bits |= 0x800
            archive.writestr(info, (unpacked / name).read_bytes(), compress_type=zipfile.ZIP_DEFLATED, compresslevel=9)


def _build_one_pack(
    version: str,
    level: str,
    segment: str,
    items: list[SourceVocab],
    source_audio_root: Path,
    sheet_name: str,
    output_level_root: Path,
    base_hash: str | None,
    base_count: int,
    engine: str,
    speed: str,
    voice: str,
    profile: str,
    bitrate: str,
    audio_mode: str,
    pack_version: int = PACK_VERSION,
) -> dict[str, object]:
    if not isinstance(pack_version, int) or pack_version < 1:
        raise BuildValidationError("pack_version phải là integer dương.")
    version_root = output_level_root / segment / f"v{pack_version}"
    unpacked = version_root / "unpacked"
    previous_audio_urls, previous_resource_hashes = _load_previous_audio_state(output_level_root / segment, pack_version)
    if unpacked.exists():
        shutil.rmtree(unpacked)
    (unpacked / "audio").mkdir(parents=True)

    audio_resources: list[dict[str, object]] = []
    audio_urls: dict[str, str] = {}
    for item in items:
        source = _source_audio_path(source_audio_root, sheet_name, item, engine=engine, speed=speed, voice=voice, profile=profile, bitrate=bitrate, audio_mode=audio_mode)
        if not source.is_file() or source.stat().st_size == 0:
            raise BuildValidationError(f"Audio thiếu hoặc 0 byte: {source}")
        digest = sha256_file(source)
        relative_path = f"audio/{digest}.m4a"
        destination = unpacked / relative_path
        if not destination.exists():
            shutil.copyfile(source, destination)
        stable_id = item.stable_id
        base_uri = canonical_audio_uri(version, level, item)
        old_uri = previous_audio_urls.get(stable_id)
        if old_uri:
            audio_uri = old_uri if previous_resource_hashes.get(old_uri) == digest else canonical_audio_uri(version, level, item, digest)
        else:
            audio_uri = base_uri
        audio_urls[stable_id] = audio_uri
        audio_resources.append(
            {
                "type": "vocab_audio",
                "path": relative_path,
                "sha256": digest,
                "bytes": source.stat().st_size,
                "canonicalSource": audio_uri,
            }
        )

    vocab = [_vocab_item(version, level, item, audio_urls[item.stable_id]) for item in items]
    vocab_bytes = _write_json(unpacked / "vocab.json", vocab)
    resources = [
        {
            "type": "vocab_json",
            "path": "vocab.json",
            "sha256": _sha256_bytes(vocab_bytes),
            "bytes": len(vocab_bytes),
            "canonicalSource": f"vocab:{version}:{level}:{segment}:v{pack_version}",
        },
        *sorted(audio_resources, key=lambda entry: (str(entry["canonicalSource"]), str(entry["path"]))),
    ]
    manifest = _manifest(version, level, segment, items, resources, base_hash, base_count, pack_version)
    _write_json(unpacked / "manifest.json", manifest)

    filename = f"vocab_{level}_{version.replace('.', '')}_{segment}_v{pack_version}.zip"
    zip_path = version_root / filename
    _write_deterministic_zip(unpacked, zip_path)
    digest = sha256_file(zip_path)
    (version_root / f"{filename}.sha256").write_text(f"{digest}  {filename}\n", encoding="utf-8")

    # Build again from precisely the same unpacked content to make deterministic
    # output a build gate, not merely a unit-test promise.
    with tempfile.TemporaryDirectory(prefix="hsk30-determinism-") as temp_dir:
        duplicate = Path(temp_dir) / filename
        _write_deterministic_zip(unpacked, duplicate)
        if sha256_file(duplicate) != digest:
            raise BuildValidationError(f"ZIP không deterministic: {filename}")

    return {
        "segment": segment,
        "unpacked": str(unpacked),
        "zip": str(zip_path),
        "sha256": digest,
        "bytes": zip_path.stat().st_size,
        "manifest": manifest,
        "vocab": vocab,
    }


def _read_zip_json(archive: zipfile.ZipFile, name: str) -> object:
    try:
        return json.loads(archive.read(name).decode("utf-8"))
    except KeyError as exc:
        raise BuildValidationError(f"ZIP thiếu {name}") from exc


def verify_pack(zip_path: str | Path, expected_level: str | None = None, expected_segment: str | None = None, expected_pack_version: int | None = None, expected_version: str | None = None) -> dict[str, object]:
    """Verify the ZIP itself against its manifest, without touching any remote service."""
    path = Path(zip_path)
    errors: list[str] = []
    try:
        with zipfile.ZipFile(path, "r") as archive:
            names = archive.namelist()
            if names != sorted(names):
                errors.append("ZIP entries không được sort theo path")
            if len(names) != len(set(names)):
                errors.append("ZIP có entry trùng")
            for info in archive.infolist():
                if info.date_time != FIXED_ZIP_DATE:
                    errors.append(f"ZIP timestamp không cố định: {info.filename}")
                    break
            manifest = _read_zip_json(archive, "manifest.json")
            vocab = _read_zip_json(archive, "vocab.json")
            errors.extend(validate_pack_data(manifest, vocab, lambda item_path: archive.read(item_path), names, expected_level, expected_segment, expected_pack_version, expected_version))
    except (OSError, zipfile.BadZipFile, UnicodeDecodeError, json.JSONDecodeError) as exc:
        errors.append(f"Không mở/đọc được ZIP: {exc}")
    if errors:
        raise BuildValidationError("; ".join(errors))
    return {"zip": str(path), "bytes": path.stat().st_size, "sha256": sha256_file(path), "status": "PASS"}


def verify_pack_pair(base_zip: str | Path, plus_zip: str | Path, expected_level: str, expected_pack_version: int | None = None, expected_version: str | None = None) -> dict[str, object]:
    """Verify the BASE/PLUS relationship after both individual ZIP checks pass."""
    base_result = verify_pack(base_zip, expected_level, "base", expected_pack_version, expected_version)
    plus_result = verify_pack(plus_zip, expected_level, "plus", expected_pack_version, expected_version)
    with zipfile.ZipFile(base_zip, "r") as archive:
        base_manifest = _read_zip_json(archive, "manifest.json")
        base_vocab = _read_zip_json(archive, "vocab.json")
    with zipfile.ZipFile(plus_zip, "r") as archive:
        plus_manifest = _read_zip_json(archive, "manifest.json")
        plus_vocab = _read_zip_json(archive, "vocab.json")
    assert isinstance(base_manifest, dict) and isinstance(plus_manifest, dict)
    assert isinstance(base_vocab, list) and isinstance(plus_vocab, list)
    errors: list[str] = []
    base_indexes = [item.get("index") for item in base_vocab if isinstance(item, dict)]
    plus_indexes = [item.get("index") for item in plus_vocab if isinstance(item, dict)]
    base_count = int(base_manifest.get("previewWordCount", 0) or 0)
    if base_count < 1 or base_indexes != list(range(1, base_count + 1)):
        errors.append("BASE index không khớp previewWordCount")
    expected_plus = list(range(base_count + 1, base_count + 1 + len(plus_indexes)))
    if plus_indexes != expected_plus:
        errors.append("PLUS phải bắt đầu từ index 51 và liên tục")
    if set(base_indexes).intersection(plus_indexes):
        errors.append("BASE/PLUS overlap index")
    if base_indexes + plus_indexes != list(range(1, len(base_indexes) + len(plus_indexes) + 1)):
        errors.append("BASE + PLUS không bằng toàn bộ Excel")
    if plus_manifest.get("requiresPackId") != base_manifest.get("packId"):
        errors.append("PLUS requiresPackId không trỏ tới BASE")
    if plus_manifest.get("baseOrderedVocabIdsSha256") != base_manifest.get("orderedVocabIdsSha256"):
        errors.append("PLUS baseOrderedVocabIdsSha256 không khớp BASE")
    if errors:
        raise BuildValidationError("; ".join(errors))
    return {"status": "PASS", "base": base_result, "plus": plus_result}


def verify_pack_parts(base_zip: str | Path, plus1_zip: str | Path, plus2_zip: str | Path, expected_level: str, expected_pack_version: int | None = None, expected_version: str | None = None) -> dict[str, object]:
    """Verify BASE + PLUS1 + PLUS2 contiguity for HSK7-9."""
    paths = (("base", base_zip), ("plus1", plus1_zip), ("plus2", plus2_zip))
    results = {segment: verify_pack(path, expected_level, segment, expected_pack_version, expected_version) for segment, path in paths}
    manifests = {}
    vocabs = {}
    for segment, path in paths:
        with zipfile.ZipFile(path, "r") as archive:
            manifest = _read_zip_json(archive, "manifest.json")
            vocab = _read_zip_json(archive, "vocab.json")
        if not isinstance(manifest, dict) or not isinstance(vocab, list):
            raise BuildValidationError(f"{segment} manifest/vocab không hợp lệ")
        manifests[segment] = manifest
        vocabs[segment] = [item for item in vocab if isinstance(item, dict)]
    all_items = vocabs["base"] + vocabs["plus1"] + vocabs["plus2"]
    indexes = [item.get("index") for item in all_items]
    ids = [item.get("id") for item in all_items]
    errors = []
    if indexes != list(range(1, len(indexes) + 1)):
        errors.append("BASE + PLUS1 + PLUS2 index không liên tục")
    if len(ids) != len(set(ids)):
        errors.append("BASE/PLUS1/PLUS2 trùng stable ID")
    base_hash = manifests["base"].get("orderedVocabIdsSha256")
    for segment in ("plus1", "plus2"):
        if manifests[segment].get("requiresPackId") != manifests["base"].get("packId"):
            errors.append(f"{segment} requiresPackId không trỏ tới BASE")
        if manifests[segment].get("baseOrderedVocabIdsSha256") != base_hash:
            errors.append(f"{segment} baseOrderedVocabIdsSha256 không khớp BASE")
    if errors:
        raise BuildValidationError("; ".join(errors))
    return {"status": "PASS", **results}


def validate_pack_data(
    manifest: object,
    vocab: object,
    read_resource: Callable[[str], bytes],
    names: Iterable[str],
    expected_level: str | None = None,
    expected_segment: str | None = None,
    expected_pack_version: int | None = None,
    expected_version: str | None = None,
) -> list[str]:
    errors: list[str] = []
    if not isinstance(manifest, dict) or not isinstance(vocab, list):
        return ["manifest hoặc vocab.json sai kiểu dữ liệu"]
    level = manifest.get("level")
    segment = manifest.get("segment")
    if expected_level and level != expected_level:
        errors.append("level trong manifest không khớp")
    if expected_segment and segment != expected_segment:
        errors.append("segment trong manifest không khớp")
    version = str(manifest.get("standardVersion", ""))
    if manifest.get("schemaVersion") != 2 or version not in SUPPORTED_VERSIONS or manifest.get("datasetVersion") != version:
        errors.append("schema/version manifest không hợp lệ")
    if expected_version is not None and version != expected_version:
        errors.append("version trong manifest không khớp")
    if manifest.get("createdAt") != FIXED_CREATED_AT or manifest.get("zipSha256") != "":
        errors.append("createdAt/zipSha256 không đúng contract")
    if manifest.get("packType") != "vocab" or (expected_pack_version is not None and manifest.get("packVersion") != expected_pack_version):
        errors.append("packType/packVersion không đúng")
    pack_version = int(manifest.get("packVersion", 0) or 0)
    expected_pack = f"vocab:{version}:{level}:{segment}:v{pack_version}"
    if manifest.get("packId") != expected_pack:
        errors.append("packId không đúng")
    if segment == "base":
        if manifest.get("accessTier") != "base" or manifest.get("vocabCount") != manifest.get("previewWordCount") or not isinstance(manifest.get("previewWordCount"), int) or manifest.get("previewWordCount", 0) < 1:
            errors.append("BASE metadata không đúng")
    elif segment in {"plus", "plus1", "plus2"}:
        base_id = f"vocab:{version}:{level}:base:v{pack_version}"
        if manifest.get("accessTier") != "vip" or manifest.get("requiresPackId") != base_id or manifest.get("compatibleBaseVersion") != pack_version:
            errors.append("PLUS metadata không tương thích BASE")
        if manifest.get("remainingVocabCount") != len(vocab):
            errors.append("PLUS remainingVocabCount không đúng")
    else:
        errors.append("segment không hợp lệ")

    required_fields = ("id", "level", "version", "index", "word", "meaning", "audio_url", "example", "example_meaning")
    ids: list[str] = []
    audio_urls: list[str] = []
    for entry in vocab:
        if not isinstance(entry, dict):
            errors.append("vocab item không phải object")
            continue
        for field in required_fields:
            if field not in entry or entry[field] is None or str(entry[field]).strip() == "":
                errors.append(f"vocab item thiếu {field}")
        item_id = str(entry.get("id", ""))
        index = entry.get("index")
        audio_url = str(entry.get("audio_url", ""))
        if entry.get("level") != level or entry.get("version") != version or item_id != str(index):
            errors.append(f"vocab item {item_id}: level/version/id không đúng")
        if not isinstance(index, int):
            errors.append(f"vocab item {item_id}: index không phải integer")
        expected_uri = f"vocab://{version}/{level}/{item_id}/audio"
        if audio_url != expected_uri and not re.fullmatch(re.escape(expected_uri) + r"/[0-9a-f]{64}", audio_url):
            errors.append(f"vocab item {item_id}: audio_url không đúng contract")
        ids.append(item_id)
        audio_urls.append(audio_url)
    if len(audio_urls) != len(set(audio_urls)):
        errors.append("audio_url không unique")
    if manifest.get("orderedVocabIds") != ids:
        errors.append("orderedVocabIds không khớp vocab.json")
    expected_ids_hash = _sha256_bytes(
        json.dumps(ids, ensure_ascii=False, separators=(",", ":"), sort_keys=False).encode("utf-8")
    )
    if manifest.get("orderedVocabIdsSha256") != expected_ids_hash:
        errors.append("orderedVocabIdsSha256 không đúng")
    if manifest.get("vocabCount") != len(vocab):
        errors.append("vocabCount không đúng")

    resources = manifest.get("resources")
    if not isinstance(resources, list) or manifest.get("resourceCount") != len(resources):
        return errors + ["resources/resourceCount không hợp lệ"]
    vocab_resources = [resource for resource in resources if isinstance(resource, dict) and resource.get("type") == "vocab_json"]
    if len(vocab_resources) != 1 or vocab_resources[0].get("path") != "vocab.json" or vocab_resources[0].get("canonicalSource") != expected_pack:
        errors.append("resource vocab.json không đúng contract")
    name_set = set(names)
    audio_by_canonical: dict[str, list[dict[str, object]]] = {}
    referenced_paths: set[str] = set()
    for resource in resources:
        if not isinstance(resource, dict):
            errors.append("resource không phải object")
            continue
        needed = ("type", "path", "sha256", "bytes", "canonicalSource")
        if any(key not in resource for key in needed):
            errors.append("resource thiếu field bắt buộc")
            continue
        resource_path = str(resource["path"])
        referenced_paths.add(resource_path)
        if resource_path not in name_set:
            errors.append(f"resource không tồn tại: {resource_path}")
            continue
        try:
            data = read_resource(resource_path)
        except Exception:
            errors.append(f"không đọc được resource: {resource_path}")
            continue
        if len(data) != resource["bytes"] or _sha256_bytes(data) != resource["sha256"]:
            errors.append(f"SHA/bytes resource sai: {resource_path}")
        if resource["type"] == "vocab_audio":
            if len(data) == 0:
                errors.append(f"audio resource 0 byte: {resource_path}")
            if not resource_path.startswith("audio/") or not re.fullmatch(r"audio/[0-9a-f]{64}\.m4a", resource_path):
                errors.append(f"audio path không đúng: {resource_path}")
            if resource["sha256"] != Path(resource_path).stem:
                errors.append(f"audio path không khớp SHA: {resource_path}")
            audio_by_canonical.setdefault(str(resource["canonicalSource"]), []).append(resource)
        elif resource["type"] != "vocab_json":
            errors.append(f"resource type không hỗ trợ: {resource['type']}")
    for audio_url in audio_urls:
        if len(audio_by_canonical.get(audio_url, [])) != 1:
            errors.append(f"audio_url phải map đúng một resource: {audio_url}")
    actual_audio_paths = {name for name in name_set if name.startswith("audio/")}
    declared_audio_paths = {str(resource["path"]) for resource in resources if isinstance(resource, dict) and resource.get("type") == "vocab_audio"}
    if actual_audio_paths != declared_audio_paths:
        errors.append("có audio thừa hoặc audio không được tham chiếu")
    return errors


def _write_local_csv(path: Path, items: list[SourceVocab]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as output:
        writer = csv.DictWriter(output, fieldnames=["index", "word", "meaning_vi", "example_zh", "example_vi"])
        writer.writeheader()
        for item in items:
            writer.writerow({"index": item.index, "word": item.word, "meaning_vi": item.meaning, "example_zh": item.example, "example_vi": item.example_meaning})


def split_policy(version: str, level: str) -> dict[str, int | str]:
    """Read the checked-in combined catalog before falling back to tested HSK3 policy."""
    version = _validate_version(version)
    _validate_level(level)
    catalog_path = Path(__file__).resolve().parents[1] / "input" / "vocab_pack_catalog_all_enabled_rollout.json"
    try:
        catalog = json.loads(catalog_path.read_text(encoding="utf-8"))
        entries = catalog.get("packs", [])
        selected = [entry for entry in entries if isinstance(entry, dict) and entry.get("version") == version and entry.get("level") == level]
        base = next((entry for entry in selected if entry.get("segment") == "base"), None)
        plus = next((entry for entry in selected if entry.get("segment") == "plus"), None)
        if isinstance(base, dict) and isinstance(plus, dict):
            base_count, plus_count = int(base["vocabCount"]), int(plus["vocabCount"])
            if base_count > 0 and plus_count >= 0:
                return {"source": "combined catalog", "base": base_count, "plus": plus_count, "total": base_count + plus_count}
    except (OSError, ValueError, TypeError, KeyError, json.JSONDecodeError):
        pass
    # HSK 3.0's existing tested builder policy is 50 preview/base words.
    return {"source": "tested builder default", "base": 50, "plus": -1, "total": -1}


def _split_plus1_plus2(items: list[SourceVocab], audio_root: Path, sheet_name: str, *, engine: str, speed: str, voice: str, profile: str, bitrate: str, audio_mode: str) -> tuple[list[SourceVocab], list[SourceVocab]]:
    """Split remaining vocab by cumulative cached audio size, preserving order."""
    if len(items) < 2:
        raise BuildValidationError("HSK7–9 cần ít nhất hai item trong PLUS để chia PLUS1/PLUS2")
    sizes = []
    for item in items:
        path = _source_audio_path(audio_root, sheet_name, item, engine=engine, speed=speed, voice=voice, profile=profile, bitrate=bitrate, audio_mode=audio_mode)
        sizes.append(path.stat().st_size if path.is_file() else 0)
    total = sum(sizes)
    cumulative = 0
    split_at = 1
    best_delta = None
    # Evaluate every legal boundary.  Do not keep updating after the target;
    # that was the bug that produced N-1/1 instead of a balanced split.
    for index, size in enumerate(sizes[:-1], start=1):
        cumulative += size
        delta = abs(cumulative - (total - cumulative))
        if best_delta is None or delta < best_delta:
            best_delta = delta
            split_at = index
    return items[:split_at], items[split_at:]


def build_vocab_pack(
    excel_path: str | Path,
    sheet_name: str,
    level: str,
    output_directory: str | Path,
    version: str = "3.0",
    engine: str = "gTTS",
    generate_missing: bool = True,
    progress: Callable[[str], None] | None = None,
    speed: str = "Bình thường",
    voice: str = "Mặc định",
    bitrate: str = "32k",
    audio_mode: str = "zh_vi",
    languages: Iterable[str] = ("vi", "zh"),
    config_confirmed: bool = True,
    pack_version: int = PACK_VERSION,
    tts_profile: str = "",
    force_regenerate_audio: bool = False,
) -> dict[str, object]:
    """Run the complete local-only Build + Validate workflow."""
    version, level = resolve_sheet_selection(sheet_name, version, level)
    if not isinstance(pack_version, int) or pack_version < 1:
        raise BuildValidationError("pack_version phải là integer dương.")
    normalized_languages = {str(language).strip().lower() for language in languages if str(language).strip()}
    if not config_confirmed:
        raise BuildValidationError(f"Chưa xác nhận dùng cấu hình TTS hiện tại cho vocab HSK {version}.")
    if not {"vi", "zh"}.issubset(normalized_languages):
        raise BuildValidationError(f"Vocab HSK {version} cần chọn cả Tiếng Việt và Tiếng Trung trong cấu hình TTS.")
    if bitrate not in {"26k", "32k"}:
        raise BuildValidationError("M4A bitrate chỉ hỗ trợ 26k hoặc 32k.")
    if audio_mode not in {"zh_only", "zh_vi"}:
        raise BuildValidationError("Audio mode chỉ hỗ trợ zh_only hoặc zh_vi.")
    level_root = Path(output_directory).expanduser().resolve() / "vocab" / version / level
    report_root = level_root / "builds" / f"v{pack_version}"
    report_root.mkdir(parents=True, exist_ok=True)
    report_path = report_root / "build_report.json"
    validation_path = report_root / "validation_report.json"
    try:
        if progress:
            progress("Đọc Excel strict")
        items = load_excel_strict(excel_path, sheet_name)
        policy = split_policy(version, level)
        base_count = int(policy["base"])
        expected_total = int(policy["total"])
        if expected_total >= 0 and len(items) != expected_total:
            raise BuildValidationError(f"Tổng rows {len(items)} không khớp policy catalog {expected_total} cho HSK {version} {level}.")
        if len(items) < base_count:
            raise BuildValidationError("Excel phải có tối thiểu 50 item để tạo BASE")
        base_items = [item for item in items if item.index <= base_count]
        plus_items = [item for item in items if item.index >= base_count + 1]
        if len(base_items) != base_count:
            raise BuildValidationError(f"BASE phải gồm chính xác index 1–{base_count}")
        audio_root = level_root / "audio_cache"
        if progress:
            progress("Tạo/tái sử dụng M4A")
        reused, generated = ensure_audio(
            items, audio_root, sheet_name, engine, speed, voice, bitrate,
            generate_missing, progress, audio_mode=audio_mode, profile=tts_profile,
            force_regenerate=force_regenerate_audio,
        )
        _write_local_csv(report_root / "source_check.csv", items)
        if progress:
            progress("Đóng BASE deterministic")
        base = _build_one_pack(version, level, "base", base_items, audio_root, sheet_name, level_root, None, base_count, engine, speed, voice, tts_profile, bitrate, audio_mode, pack_version)
        is_split_hsk79 = version == "3.0" and level == "hsk7_9"
        if is_split_hsk79:
            plus1_items, plus2_items = _split_plus1_plus2(
                plus_items, audio_root, sheet_name, engine=engine, speed=speed, voice=voice,
                profile=tts_profile, bitrate=bitrate, audio_mode=audio_mode,
            )
            if progress:
                progress("Đóng PLUS1 deterministic")
            plus1 = _build_one_pack(version, level, "plus1", plus1_items, audio_root, sheet_name, level_root, base["manifest"]["orderedVocabIdsSha256"], base_count, engine, speed, voice, tts_profile, bitrate, audio_mode, pack_version)
            if progress:
                progress("Đóng PLUS2 deterministic")
            plus2 = _build_one_pack(version, level, "plus2", plus2_items, audio_root, sheet_name, level_root, base["manifest"]["orderedVocabIdsSha256"], base_count, engine, speed, voice, tts_profile, bitrate, audio_mode, pack_version)
            for part_name, part in (("PLUS1", plus1), ("PLUS2", plus2)):
                if int(part["bytes"]) >= 50_000_000:
                    raise BuildValidationError(f"{part_name} ZIP phải dưới 50,000,000 bytes; hiện tại {part['bytes']} bytes")
            if progress:
                progress("Mở lại ZIP và verify")
            pair_verify = verify_pack_parts(base["zip"], plus1["zip"], plus2["zip"], level, pack_version, version)
            plus = None
        else:
            if progress:
                progress("Đóng PLUS deterministic")
            plus = _build_one_pack(version, level, "plus", plus_items, audio_root, sheet_name, level_root, base["manifest"]["orderedVocabIdsSha256"], base_count, engine, speed, voice, tts_profile, bitrate, audio_mode, pack_version)
            if progress:
                progress("Mở lại ZIP và verify")
            pair_verify = verify_pack_pair(base["zip"], plus["zip"], level, pack_version, version)
        result: dict[str, object] = {
            "status": "PASS",
            "workflow": "Generic HSK vocab local build only; deploy/publish disabled",
            "version": version,
            "level": level,
            "packVersion": pack_version,
            "sheet": sheet_name,
            "sourceExcel": str(Path(excel_path).resolve()),
            "ttsConfig": {
                "engine": engine,
                "speed": speed,
                "voice": voice,
                "profile": tts_profile,
                "audioMode": audio_mode,
                "languages": sorted(normalized_languages),
                "m4a": {"codec": "AAC-LC", "channels": 1, "sampleRate": 22050, "bitrate": bitrate},
            },
            "totalRows": len(items),
            "splitPolicy": policy,
            "audioReused": reused,
            "audioGenerated": generated,
            "base": base,
            **({"plus1": plus1, "plus2": plus2} if is_split_hsk79 else {"plus": plus}),
            "deployEnabled": False,
            "sourceAudioRoot": str(audio_root),
            "objectPaths": {
                "base": f"vocab/{version}/{level}/base/v{pack_version}/vocab_{level}_{version.replace('.', '')}_base_v{pack_version}.zip",
                **({
                    "plus1": f"vocab/{version}/{level}/plus1/v{pack_version}/vocab_{level}_{version.replace('.', '')}_plus1_v{pack_version}.zip",
                    "plus2": f"vocab/{version}/{level}/plus2/v{pack_version}/vocab_{level}_{version.replace('.', '')}_plus2_v{pack_version}.zip",
                } if is_split_hsk79 else {
                    "plus": f"vocab/{version}/{level}/plus/v{pack_version}/vocab_{level}_{version.replace('.', '')}_plus_v{pack_version}.zip",
                }),
            },
        }
        _write_json(report_path, result)
        _write_json(validation_path, pair_verify)
        return result
    except Exception as exc:
        failure = {"status": "FAIL", "version": version, "level": level, "sheet": sheet_name, "error": str(exc), "deployEnabled": False}
        _write_json(report_path, failure)
        _write_json(validation_path, failure)
        if isinstance(exc, BuildValidationError):
            raise
        raise BuildValidationError(str(exc)) from exc


def build_hsk30(*args, **kwargs) -> dict[str, object]:
    """Compatibility entry point retained for existing callers."""
    kwargs["version"] = "3.0"
    return build_vocab_pack(*args, **kwargs)


def deployment_allowed(result: object) -> bool:
    """The UI may only enable future deploy after an unchanged local PASS artifact."""
    if not isinstance(result, dict) or result.get("status") != "PASS":
        return False
    segments = ("base", "plus1", "plus2") if result.get("level") == "hsk7_9" and result.get("version") == "3.0" else ("base", "plus")
    for segment in segments:
        pack = result.get(segment)
        if not isinstance(pack, dict):
            return False
        path = Path(str(pack.get("zip", "")))
        if not path.is_file() or sha256_file(path) != pack.get("sha256"):
            return False
    return False  # First pilot has no deploy implementation by design.


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build HSK 2.0/3.0 vocab ZIP packs locally; no upload is implemented.")
    parser.add_argument("excel_file")
    parser.add_argument("--sheet", required=True)
    parser.add_argument("--version", required=True, choices=SUPPORTED_VERSIONS)
    parser.add_argument("--level", required=True, choices=SUPPORTED_LEVELS)
    parser.add_argument("--pack-version", type=int, default=PACK_VERSION, help="Immutable pack version (default: 1)")
    parser.add_argument("--output", required=True, help="Parent directory for output/vocab/<version>")
    parser.add_argument("--engine", default=os.environ.get("TTS_ENGINE", "gTTS"))
    parser.add_argument("--speed", default=os.environ.get("TTS_SPEED", "Bình thường"))
    parser.add_argument("--voice", default=os.environ.get("TTS_VOICE", "Mặc định"))
    parser.add_argument("--bitrate", default=os.environ.get("M4A_BITRATE", "32k"), choices=("26k", "32k"))
    parser.add_argument("--audio-mode", default=os.environ.get("TTS_AUDIO_MODE", "zh_vi"), choices=("zh_only", "zh_vi"))
    parser.add_argument("--languages", default=os.environ.get("TTS_LANGUAGES", "vi,zh"))
    parser.add_argument(
        "--config-confirmed",
        default=os.environ.get("TTS_CONFIG_CONFIRMED", "true"),
        choices=("true", "false"),
        help="The UI confirmation snapshot; false blocks the build.",
    )
    parser.add_argument("--reuse-only", action="store_true", help="Fail instead of creating a missing audio file")
    parser.add_argument("--tts-profile", default=os.environ.get("TTS_PROFILE", ""))
    parser.add_argument("--force-regenerate-audio", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = _parse_args()
    try:
        result = build_vocab_pack(
            args.excel_file,
            args.sheet,
            args.level,
            args.output,
            version=args.version,
            engine=args.engine,
            generate_missing=not args.reuse_only,
            progress=print,
            speed=args.speed,
            voice=args.voice,
            bitrate=args.bitrate,
            audio_mode=args.audio_mode,
            languages=args.languages.split(","),
            config_confirmed=args.config_confirmed == "true",
            pack_version=args.pack_version,
            tts_profile=args.tts_profile,
            force_regenerate_audio=args.force_regenerate_audio,
        )
        if "plus1" in result:
            print(f"STATUS: PASS\nBASE_SHA256: {result['base']['sha256']}\nPLUS1_SHA256: {result['plus1']['sha256']}\nPLUS2_SHA256: {result['plus2']['sha256']}")
        else:
            print(f"STATUS: PASS\nBASE_SHA256: {result['base']['sha256']}\nPLUS_SHA256: {result['plus']['sha256']}")
        return 0
    except BuildValidationError as exc:
        print(f"STATUS: FAIL\n{exc}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
