"""Small, ordered keyword rules used by the deterministic smart-query plan."""
from __future__ import annotations

import re

from app.domains.kol.search_intent_text import affirmative_search_text


def _contains_any(text: str, terms: tuple[str, ...]) -> bool:
    return any(term in text for term in terms)


def _gear_is_unrestricted(text: str) -> bool:
    return _contains_any(
        text,
        (
            "不限器材", "不限制器材", "不限设备", "不要求器材",
            "no gear requirement", "without gear requirements", "regardless of gear",
        ),
    )


def _non_lens_mm_context(text: str) -> bool:
    return bool(re.search(
        r"(?:\d{1,3}\s*mm\s*(?:film\b|胶片|底片|(?:full[- ]?frame\s+)?equivalent\b|全画幅\s*等效|等效)"
        r"|(?:equivalent(?:\s+to)?|等效(?:于)?)\s*\d{1,3}\s*mm)",
        text,
        flags=re.IGNORECASE,
    ))


def _append_medium_terms(
    text: str,
    keywords: list[str],
    *,
    non_lens_mm: bool,
) -> None:
    film_photo = _contains_any(
        text,
        (
            "35mm film", "film photography", "film photographer",
            "analog photography", "analog photographer",
            "analogue photography", "analogue photographer", "胶片", "底片",
        ),
    )
    if film_photo:
        keywords.extend(["film photographer", "analog photography"])
    natural_light = _contains_any(
        text,
        ("natural light", "natural-light", "natural lighting", "available light", "自然光", "不打灯"),
    )
    lighting = not natural_light and _contains_any(
        text,
        ("flash", "strobe", "lighting", "studio light", "video light", "闪光", "灯", "补光"),
    )
    if lighting:
        keywords.extend(["lighting", "flash", "strobe", "studio lighting"])
    if _contains_any(text, ("300w", "300 w")) or (lighting and "300" in text):
        keywords.extend(["300W", "portable lighting"])
    if _contains_any(text, ("人像", "portrait")):
        keywords.extend(["portrait", "portrait photographer"])


def _append_people_terms(text: str, keywords: list[str], *, non_lens_mm: bool) -> None:
    if (
        not non_lens_mm
        and _contains_any(text, ("摄影师", "photographer"))
        and not any("photographer" in keyword for keyword in keywords)
    ):
        keywords.append("professional photographer")
    if _contains_any(text, ("摄像师", "videographer")):
        keywords.append("professional videographer")
    if _contains_any(text, ("摄影指导", "cinematographer", "director of photography")):
        keywords.append("cinematographer")
    if _contains_any(text, ("摄影机操作员", "相机操作员", "camera operator")):
        keywords.append("camera operator")
    if _contains_any(text, ("导演", "filmmaker", "film director")):
        keywords.append("filmmaker")
    if _contains_any(text, ("博主", "达人", "content creator", "influencer", "blogger")):
        keywords.append("content creator")


def _append_gear_terms(
    text: str,
    keywords: list[str],
    *,
    gear_unrestricted: bool,
    non_lens_mm: bool,
) -> None:
    if not gear_unrestricted and _contains_any(text, ("测评", "评测", "review", "gear")):
        keywords.extend(["gear reviewer", "camera gear review"])
    if _contains_any(
        text,
        ("monitor", "监视器", "550pro", "550 pro", "550por", "外接屏", "screen", "屏"),
    ):
        keywords.extend([
            "camera monitor", "field monitor", "videographer", "filmmaker", "cinematographer",
            "content creator", "automotive videographer", "food videographer", "wedding filmmaker", "commercial video",
        ])
    lens_unrestricted = gear_unrestricted or _contains_any(
        text,
        ("不限镜头", "不限制镜头", "无需指定镜头", "any lens", "regardless of lens"),
    )
    names_lens = "镜头" in text or re.search(
        r"(?<![a-z0-9])(?:lens|lab)(?![a-z0-9])", text
    )
    names_focal_length = re.search(
        r"(?<![a-z0-9])\d{1,3}\s*mm(?![a-z0-9])", text
    ) and not non_lens_mm
    if not lens_unrestricted and (names_lens or names_focal_length):
        keywords.extend(["lens review", "videographer", "photographer", "camera gear"])


def _append_scene_terms(text: str, keywords: list[str]) -> None:
    if _contains_any(text, ("电影感", "cinematic", "cinematography")):
        keywords.extend(["cinematic", "cinematography"])
    if _contains_any(text, ("旅行", "travel")):
        keywords.append("travel")
    if _contains_any(text, ("风光", "landscape")):
        keywords.append("landscape")
    if _contains_any(text, ("微距", "macro")):
        keywords.append("macro")
    if _contains_any(text, ("产品摄影", "product photography")):
        keywords.append("product photography")
    if _contains_any(text, ("赛车", "机车", "摩托")):
        keywords.extend(["automotive videographer", "motorsport", "racing"])
    if _contains_any(text, ("厨师", "餐饮", "美食", "烹饪")):
        keywords.extend(["food creator", "culinary", "chef", "food videographer"])
    if "婚礼" in text:
        keywords.append("wedding filmmaker")
    if "健身" in text:
        keywords.append("fitness creator")
    if "宠物" in text:
        keywords.append("pet creator")
    if "旅拍" in text:
        keywords.append("travel videographer")


def build_fallback_keywords(value: str) -> list[str]:
    """Return fallback terms in the historical priority order."""

    text = affirmative_search_text(value).lower()
    keywords: list[str] = []
    gear_unrestricted = _gear_is_unrestricted(text)
    non_lens_mm = _non_lens_mm_context(text)
    _append_medium_terms(text, keywords, non_lens_mm=non_lens_mm)
    _append_people_terms(text, keywords, non_lens_mm=non_lens_mm)
    _append_gear_terms(
        text,
        keywords,
        gear_unrestricted=gear_unrestricted,
        non_lens_mm=non_lens_mm,
    )
    _append_scene_terms(text, keywords)
    return keywords
