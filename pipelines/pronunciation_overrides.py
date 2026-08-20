from __future__ import annotations

from dataclasses import dataclass
from typing import Optional


@dataclass(frozen=True)
class SpokenTextPayload:
    source_text: str
    spoken_text: str
    mode: str
    matched: bool
    matched_key: Optional[str] = None


PRODUCTION_PRONUNCIATION_OVERRIDE_ACTIVE = True

_CHINESE_LANGUAGE_ALIASES = {
    "zh",
    "zh-cn",
    "zh_cn",
    "zh-CN",
    "cmn-CN",
    "cmn-cn",
    "cmn",
    "zh-hans",
    "zh-hant",
}

ZH_PRONUNCIATION_OVERRIDES = {
    "一点儿": {"mode": "text_fallback", "spoken": "一点"},
    "小孩儿": {"mode": "text_fallback", "spoken": "小孩"},
    "面条儿": {"mode": "text_fallback", "spoken": "面条"},
}

_ZH_RAW_ONLY = {
    "哪儿",
    "这儿",
    "玩儿",
    "儿子",
    "女儿",
    "儿童",
    "儿科",
}


def _clean(value: object) -> str:
    return str(value or "").strip()


def _is_chinese_language(language: object) -> bool:
    normalized = _clean(language).lower()
    return normalized in {item.lower() for item in _CHINESE_LANGUAGE_ALIASES} or normalized.startswith("zh") or normalized.startswith("cmn")


def prepare_spoken_text(text, language, engine, voice=None) -> SpokenTextPayload:
    source_text = _clean(text)
    if not source_text:
        return SpokenTextPayload(source_text="", spoken_text="", mode="raw", matched=False, matched_key=None)

    if not _is_chinese_language(language):
        return SpokenTextPayload(source_text=source_text, spoken_text=source_text, mode="raw", matched=False, matched_key=None)

    engine_clean = _clean(engine).lower()
    if engine_clean == "polly":
        return SpokenTextPayload(source_text=source_text, spoken_text=source_text, mode="raw", matched=False, matched_key=None)

    if source_text in _ZH_RAW_ONLY:
        return SpokenTextPayload(source_text=source_text, spoken_text=source_text, mode="raw", matched=False, matched_key=None)

    matched_key = None
    spoken_text = source_text
    matched = False
    mode = "raw"
    first_match_index = None
    for key, override in ZH_PRONUNCIATION_OVERRIDES.items():
        index = source_text.find(key)
        if index < 0:
            continue
        matched = True
        if first_match_index is None or index < first_match_index:
            first_match_index = index
            matched_key = key
        mode = "text_fallback" if engine_clean == "gtts" else override.get("mode", "raw")
        if PRODUCTION_PRONUNCIATION_OVERRIDE_ACTIVE:
            spoken_text = spoken_text.replace(key, _clean(override.get("spoken", key)) or key)

    if not matched:
        return SpokenTextPayload(source_text=source_text, spoken_text=source_text, mode="raw", matched=False, matched_key=None)

    return SpokenTextPayload(
        source_text=source_text,
        spoken_text=spoken_text,
        mode=mode,
        matched=True,
        matched_key=matched_key,
    )