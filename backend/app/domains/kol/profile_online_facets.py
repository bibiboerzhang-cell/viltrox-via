"""Bounded, provider-public evidence adapters for strict online filters."""
from __future__ import annotations

import re
from typing import Any

from app.domains.kol.profile_recall_search_spec import (
    normalize_operator_languages,
    normalize_operator_profile_types,
)


_LANGUAGE_SOURCES = frozenset({
    "platform_content_metadata",
    "platform_profile",
    "provider_public_content_language_v1",
})
_PROFILE_TYPE_SOURCES = frozenset({
    "platform_profile",
    "provider_declared",
    "provider_public_content_profile_type_v1",
})
_REVIEWER_TERMS = re.compile(
    r"\b(?:review|reviews|reviewer|tested|testing|comparison|compare|unboxing|gear test)\b",
    re.IGNORECASE,
)
_CREATOR_TERMS = re.compile(
    r"\b(?:filmmaker|photographer|cinematographer|videographer|vlogger|content creator)\b",
    re.IGNORECASE,
)


def _text(value: Any) -> str:
    return str(value or "").strip()


def _public_profile_text(raw: dict[str, Any]) -> tuple[str, list[str]]:
    values: list[str] = []
    fields: list[str] = []
    for field in ("bio", "description", "sample_title", "title"):
        value = _text(raw.get(field))
        if value:
            values.append(value[:600])
            fields.append(field)
    latest = raw.get("latest_real_video")
    if isinstance(latest, dict) and _text(latest.get("title")):
        values.append(_text(latest.get("title"))[:500])
        fields.append("latest_real_video.title")
    return " ".join(values)[:1800], fields[:5]


def _detect_language(text: str) -> tuple[str, float]:
    letters = sum(char.isalpha() for char in text)
    if letters < 32 or len(text.split()) < 6:
        return "", 0.0
    try:
        from langdetect import DetectorFactory, detect_langs  # type: ignore

        DetectorFactory.seed = 0
        probabilities = detect_langs(text)
    except Exception:
        return "", 0.0
    if not probabilities:
        return "", 0.0
    top = probabilities[0]
    language = str(getattr(top, "lang", "") or "").lower().split("-", 1)[0]
    confidence = float(getattr(top, "prob", 0.0) or 0.0)
    normalized = normalize_operator_languages(language)
    if confidence < 0.90 or len(normalized) != 1:
        return "", confidence
    return normalized[0], confidence


def adapt_language(raw: dict[str, Any]) -> dict[str, Any]:
    """Use declared public metadata or deterministic bounded text detection."""
    declared = normalize_operator_languages(raw.get("language") or raw.get("content_language"))
    declared_source = _text(raw.get("language_source")).lower()
    if len(declared) == 1 and declared_source in _LANGUAGE_SOURCES:
        return {
            "value": declared[0],
            "source": declared_source,
            "confidence": 1.0,
            "evidence_fields": ["language"],
            "version": 1,
        }
    text, fields = _public_profile_text(raw)
    detected, confidence = _detect_language(text)
    if not detected:
        return {"value": "", "source": "unknown", "confidence": confidence, "evidence_fields": fields, "version": 1}
    return {
        "value": detected,
        "source": "provider_public_content_language_v1",
        "confidence": round(confidence, 4),
        "evidence_fields": fields,
        "version": 1,
    }


def adapt_profile_type(raw: dict[str, Any]) -> dict[str, Any]:
    """Classify only strong public-content rules; weak evidence stays unknown."""
    declared = normalize_operator_profile_types(raw.get("profile_type"))
    declared_source = _text(raw.get("profile_type_source")).lower()
    if len(declared) == 1 and declared_source in _PROFILE_TYPE_SOURCES:
        return {
            "value": declared[0],
            "source": declared_source,
            "evidence_fields": ["profile_type"],
            "version": 1,
        }
    text, fields = _public_profile_text(raw)
    reviewer = bool(_REVIEWER_TERMS.search(text))
    creator = bool(_CREATOR_TERMS.search(text))
    value = "mixed" if reviewer and creator else "reviewer" if reviewer else "creator" if creator else ""
    return {
        "value": value,
        "source": "provider_public_content_profile_type_v1" if value else "unknown",
        "evidence_fields": fields if value else [],
        "version": 1,
    }
