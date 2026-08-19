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


PRODUCTION_PRONUNCIATION_OVERRIDE_ACTIVE = False

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

ZH_PRONUNCIATION_OVERRIDES = {}

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

    matched = source_text in ZH_PRONUNCIATION_OVERRIDES
    if not matched:
        return SpokenTextPayload(source_text=source_text, spoken_text=source_text, mode="raw", matched=False, matched_key=None)

    override = ZH_PRONUNCIATION_OVERRIDES[source_text]
    mode = "text_fallback" if engine_clean == "gtts" else override.get("mode", "raw")
    spoken_text = source_text
    if PRODUCTION_PRONUNCIATION_OVERRIDE_ACTIVE:
        spoken_text = _clean(override.get("spoken", source_text)) or source_text

    return SpokenTextPayload(
        source_text=source_text,
        spoken_text=spoken_text,
        mode=mode,
        matched=True,
        matched_key=source_text,
    )