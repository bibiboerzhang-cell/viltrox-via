"""Deterministic fallback rules for smart-query planning."""
from __future__ import annotations

import re
from typing import Any

from app.core.coerce import _text
from app.domains.kol.search_intent_text import affirmative_search_text
from app.domains.kol.smart_query_planner_fallback_rules import build_fallback_keywords


def as_float_or_none(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def as_list(value: Any) -> list[str]:
    if isinstance(value, list):
        return [_text(item).lower() for item in value if _text(item)]
    if isinstance(value, str):
        return [_text(part).lower() for part in re.split(r"[,/，、\s]+", value) if _text(part)]
    return []


def fallback_platforms(lowered: str, supported_platforms: tuple[str, ...]) -> list[str]:
    platforms = [
        platform
        for platform in supported_platforms
        if platform in lowered or (platform == "youtube" and "yt" in lowered)
    ]
    return platforms or ["youtube", "instagram", "tiktok"]


def vague_people_request(value: Any) -> bool:
    """Return true only for quality adjectives plus an otherwise generic role."""

    text = affirmative_search_text(value).lower()
    compact = re.sub(r"[\s,，。！？!?、;；]+", "", text)
    if re.fullmatch(
        r"(?:请|帮我|给我|想要|找|寻找|推荐|一些|几个|几位|一批|靠谱|优质|合适|好的|不错的|的|达人|创作者|博主|kol|kols|人|一下|吧)+",
        compact,
    ):
        return True
    words = re.findall(r"[a-z]+", text)
    allowed = {
        "please", "help", "me", "find", "recommend", "some", "a", "few",
        "good", "great", "reliable", "quality", "suitable", "creator", "creators",
        "influencer", "influencers", "kol", "kols", "people",
    }
    return bool(words) and set(words) <= allowed


def fallback_keywords(lowered: str) -> list[str]:
    return build_fallback_keywords(lowered)


def avoid_types_for_product(product: dict[str, Any] | None) -> list[str]:
    if not product:
        return []
    blob = " ".join(
        str(product.get(key) or "")
        for key in ("category_main", "category_detail", "series", "model_name", "marketing_name")
    ).lower()
    if "cine" in blob or "anamorphic" in blob:
        return ["generic gear reviewer", "still-photography-only photographer", "phone vlogger", "camera store unboxing channel"]
    if "monitor" in blob:
        return ["still-photography-only photographer", "phone vlogger"]
    if "flash" in blob or "lighting" in blob:
        return ["pure landscape shooter", "automotive-only videographer"]
    return []


def product_search_terms(product: dict[str, Any] | None) -> list[str]:
    if not product:
        return []
    blob = " ".join(
        str(product.get(key) or "")
        for key in ("category_main", "category_detail", "series", "model_name", "marketing_name")
    ).lower()
    if "cine" in blob or "anamorphic" in blob:
        return ["cinematographer", "director of photography", "filmmaker", "anamorphic filmmaker", "commercial film", "music video"]
    if "monitor" in blob:
        return ["filmmaker", "videographer", "cinematographer", "field monitor", "content creator", "camera operator"]
    if "flash" in blob or "lighting" in blob:
        return ["wedding photographer", "portrait photographer", "studio lighting", "off-camera flash", "lighting educator"]
    if "lens" in blob:
        return ["photographer", "videographer", "portrait photographer", "filmmaker"]
    return []
