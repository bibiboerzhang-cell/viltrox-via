"""Bounded operator filter normalization for strict local KOL recall.

The request/router layer may accept friendlier labels, but the qualification
layer owns a deliberately small taxonomy.  Unknown explicit values are kept in
``invalid`` so callers cannot silently widen an invalid filter to "all".
"""
from __future__ import annotations

import re
from typing import Any


MAX_OPERATOR_LANGUAGES = 8
SUPPORTED_OPERATOR_LANGUAGES = frozenset({
    "ar",
    "de",
    "en",
    "es",
    "fr",
    "id",
    "it",
    "ja",
    "ko",
    "ms",
    "nl",
    "pl",
    "pt",
    "ru",
    "sv",
    "th",
    "tr",
    "vi",
    "zh",
})
SUPPORTED_OPERATOR_PROFILE_TYPES = frozenset({"creator", "reviewer", "mixed"})

_LANGUAGE_ALIASES = {
    "arabic": "ar",
    "阿拉伯语": "ar",
    "german": "de",
    "deutsch": "de",
    "德语": "de",
    "english": "en",
    "英语": "en",
    "spanish": "es",
    "espanol": "es",
    "español": "es",
    "西班牙语": "es",
    "french": "fr",
    "francais": "fr",
    "français": "fr",
    "法语": "fr",
    "indonesian": "id",
    "bahasa indonesia": "id",
    "印尼语": "id",
    "italian": "it",
    "意大利语": "it",
    "japanese": "ja",
    "日语": "ja",
    "korean": "ko",
    "韩语": "ko",
    "malay": "ms",
    "bahasa melayu": "ms",
    "dutch": "nl",
    "荷兰语": "nl",
    "polish": "pl",
    "波兰语": "pl",
    "portuguese": "pt",
    "portugues": "pt",
    "português": "pt",
    "葡萄牙语": "pt",
    "russian": "ru",
    "俄语": "ru",
    "swedish": "sv",
    "瑞典语": "sv",
    "thai": "th",
    "泰语": "th",
    "turkish": "tr",
    "土耳其语": "tr",
    "vietnamese": "vi",
    "越南语": "vi",
    "chinese": "zh",
    "mandarin": "zh",
    "中文": "zh",
    "汉语": "zh",
    "普通话": "zh",
}

_PROFILE_TYPE_ALIASES = {
    "creator": "creator",
    "content creator": "creator",
    "photographer": "creator",
    "videographer": "creator",
    "创作者": "creator",
    "内容创作者": "creator",
    "reviewer": "reviewer",
    "review": "reviewer",
    "gear reviewer": "reviewer",
    "评测": "reviewer",
    "评测者": "reviewer",
    "mixed": "mixed",
    "hybrid": "mixed",
    "creator reviewer": "mixed",
    "混合": "mixed",
    "综合": "mixed",
}

_SEPARATOR_RE = re.compile(r"[,;/|\n\r\t、]+")


def _operator_tokens(value: Any, *, maximum: int) -> list[str]:
    raw_values = value if isinstance(value, (list, tuple, set)) else [value]
    tokens: list[str] = []
    for raw in raw_values:
        if raw is None or isinstance(raw, bool):
            continue
        for part in _SEPARATOR_RE.split(str(raw)):
            token = re.sub(r"\s+", " ", part.strip().lower())[:80]
            if token and token not in tokens:
                tokens.append(token)
            if len(tokens) >= maximum:
                return tokens
    return tokens


def _language_code(token: str) -> str:
    alias = _LANGUAGE_ALIASES.get(token)
    if alias:
        return alias
    code = token.replace("_", "-").split("-", 1)[0]
    return code if code in SUPPORTED_OPERATOR_LANGUAGES else ""


def parse_operator_languages(value: Any) -> dict[str, Any]:
    """Return normalized and invalid explicit language tokens."""
    tokens = _operator_tokens(value, maximum=MAX_OPERATOR_LANGUAGES + 1)
    normalized: list[str] = []
    invalid: list[str] = []
    for token in tokens[:MAX_OPERATOR_LANGUAGES]:
        code = _language_code(token)
        target = normalized if code else invalid
        resolved = code or token
        if resolved not in target:
            target.append(resolved)
    if len(tokens) > MAX_OPERATOR_LANGUAGES:
        invalid.append("too_many_values")
    return {
        "requested": bool(tokens),
        "values": normalized,
        "invalid": invalid,
        "maximum": MAX_OPERATOR_LANGUAGES,
    }


def normalize_operator_languages(value: Any) -> list[str]:
    return list(parse_operator_languages(value)["values"])


def parse_operator_profile_types(value: Any) -> dict[str, Any]:
    """Return normalized and invalid explicit KOL profile-type tokens."""
    tokens = _operator_tokens(value, maximum=len(SUPPORTED_OPERATOR_PROFILE_TYPES) + 1)
    normalized: list[str] = []
    invalid: list[str] = []
    for token in tokens[: len(SUPPORTED_OPERATOR_PROFILE_TYPES)]:
        resolved = _PROFILE_TYPE_ALIASES.get(token, "")
        target = normalized if resolved else invalid
        value_out = resolved or token
        if value_out not in target:
            target.append(value_out)
    if len(tokens) > len(SUPPORTED_OPERATOR_PROFILE_TYPES):
        invalid.append("too_many_values")
    return {
        "requested": bool(tokens),
        "values": normalized,
        "invalid": invalid,
        "maximum": len(SUPPORTED_OPERATOR_PROFILE_TYPES),
    }


def normalize_operator_profile_types(value: Any) -> list[str]:
    return list(parse_operator_profile_types(value)["values"])


def operator_filter_spec(*, languages: Any = None, profile_types: Any = None) -> dict[str, Any]:
    """Build the serializable, fail-closed filter fragment used by policy."""
    return {
        "languages": parse_operator_languages(languages),
        "profile_types": parse_operator_profile_types(profile_types),
    }
