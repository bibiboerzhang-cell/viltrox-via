"""Deterministic fallback rules for smart-query planning."""
from __future__ import annotations

import re
from typing import Any

from app.core.coerce import _text


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


def fallback_keywords(lowered: str) -> list[str]:
    keywords: list[str] = []
    film_photo_intent = any(
        term in lowered
        for term in (
            "35mm film", "film photography", "film photographer",
            "analog photography", "analog photographer",
            "analogue photography", "analogue photographer", "胶片", "底片",
        )
    )
    non_lens_mm_context = bool(re.search(
        r"(?:\d{1,3}\s*mm\s*(?:film\b|胶片|底片|(?:full[- ]?frame\s+)?equivalent\b|全画幅\s*等效|等效)"
        r"|(?:equivalent(?:\s+to)?|等效(?:于)?)\s*\d{1,3}\s*mm)",
        lowered,
        flags=re.IGNORECASE,
    ))
    if film_photo_intent:
        keywords.extend(["film photographer", "analog photography"])
    is_lighting = any(
        term in lowered
        for term in ("flash", "strobe", "lighting", "light", "闪光", "灯", "补光")
    )
    if is_lighting:
        keywords.extend(["lighting", "flash", "strobe", "studio lighting"])
    if any(term in lowered for term in ("300w", "300 w")) or (is_lighting and "300" in lowered):
        keywords.extend(["300W", "portable lighting"])
    if any(term in lowered for term in ("人像", "portrait")):
        keywords.extend(["portrait", "portrait photographer"])
    if any(term in lowered for term in ("测评", "评测", "review", "gear")):
        keywords.extend(["gear reviewer", "camera gear review"])
    if any(
        term in lowered
        for term in ("monitor", "监视器", "550pro", "550 pro", "550por", "外接屏", "screen", "屏")
    ):
        keywords.extend([
            "camera monitor", "field monitor", "videographer", "filmmaker", "cinematographer",
            "content creator", "automotive videographer", "food videographer", "wedding filmmaker", "commercial video",
        ])
    if any(term in lowered for term in ("镜头", "lens", "lab")) or (
        "mm" in lowered and not non_lens_mm_context
    ):
        keywords.extend(["lens review", "videographer", "photographer", "camera gear"])
    if any(term in lowered for term in ("电影感", "cinematic", "cinematography")):
        keywords.extend(["cinematic", "cinematography"])
    if any(term in lowered for term in ("旅行", "travel")):
        keywords.append("travel")
    if any(term in lowered for term in ("风光", "landscape")):
        keywords.append("landscape")
    if any(term in lowered for term in ("微距", "macro")):
        keywords.append("macro")
    if any(term in lowered for term in ("产品摄影", "product photography")):
        keywords.append("product photography")
    if any(term in lowered for term in ("赛车", "机车", "摩托")):
        keywords.extend(["automotive videographer", "motorsport", "racing"])
    if any(term in lowered for term in ("厨师", "餐饮", "美食", "烹饪")):
        keywords.extend(["food creator", "culinary", "chef", "food videographer"])
    if "婚礼" in lowered:
        keywords.append("wedding filmmaker")
    if "健身" in lowered:
        keywords.append("fitness creator")
    if "宠物" in lowered:
        keywords.append("pet creator")
    if "旅拍" in lowered:
        keywords.append("travel videographer")
    return keywords


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
