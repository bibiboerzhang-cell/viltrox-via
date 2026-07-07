"""Text-only competitor mention detection helpers for cached KOL data."""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any


CONFIG_PATH = Path(__file__).with_name("competitor_brands.json")
DEFAULT_BRANDS: dict[str, dict[str, Any]] = {
    "sigma": {"keywords": ["sigma", "sigmaphoto", "@sigmaphoto"], "priority": "tier1"},
    "tamron": {"keywords": ["tamron", "@tamronusa", "tamronlens"], "priority": "tier1"},
    "samyang": {"keywords": ["samyang", "samyanglens", "@samyangoptics", "rokinon"], "priority": "tier2"},
    "yongnuo": {"keywords": ["yongnuo", "@yongnuo", "yn lens", "yn "], "priority": "tier3"},
    "meike": {"keywords": ["meike", "@meike_global"], "priority": "tier3"},
    "godox": {"keywords": ["godox", "@godox_photo"], "priority": "tier2"},
}

SPONSORED_PATTERNS = (
    "sponsored by",
    "paid partnership",
    "partnered with",
    "in partnership with",
    "#ad",
    " ad ",
)
GIFTED_PATTERNS = (
    "gifted",
    "sent me",
    "sent over",
    "thanks to",
    "provided by",
    "loaned",
)
EVALUATED_PATTERNS = (
    "review",
    "hands-on",
    "hands on",
    "test",
    "comparison",
    "compare",
    "versus",
    " vs ",
    "评测",
    "测试",
    "对比",
)
POSITIVE_PATTERNS = ("best", "love", "amazing", "excellent", "great", "10/10", "sharp", "favorite")
NEGATIVE_PATTERNS = ("disappointed", "poor", "issue", "issues", "bad", "worst", "broken", "terrible")


from app.core.coerce import _loads, _text


def load_competitor_brands() -> dict[str, dict[str, Any]]:
    try:
        parsed = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    except Exception:
        return DEFAULT_BRANDS
    if not isinstance(parsed, dict):
        return DEFAULT_BRANDS
    result: dict[str, dict[str, Any]] = {}
    for brand, config in parsed.items():
        if not isinstance(config, dict):
            continue
        keywords = config.get("keywords")
        if not isinstance(keywords, list) or not keywords:
            continue
        result[str(brand).strip().lower()] = {
            "keywords": [str(item).strip().lower() for item in keywords if str(item).strip()],
            "priority": str(config.get("priority") or "tier3"),
            # 产品线扩容:category(lens/monitor/flash)+ brand_type(oem 原厂=镜头直接对手须避雷 /
            # third_party 副厂=对副厂开放生态可挖角)。仅元数据,不参与 viltrox_fit_score。
            "category": str(config.get("category") or "lens"),
            "brand_type": str(config.get("brand_type") or "third_party"),
        }
    return result or DEFAULT_BRANDS


def _keyword_match(text: str, keyword: str) -> bool:
    lowered = text.lower()
    key = keyword.lower().strip()
    if not key:
        return False
    if key.startswith("@") or " " in key:
        return key in lowered
    return re.search(rf"(?<![a-z0-9]){re.escape(key)}(?![a-z0-9])", lowered) is not None


def _first_text(*values: Any) -> str:
    for value in values:
        text = _text(value)
        if text:
            return text
    return ""


def _post_title(post: dict[str, Any]) -> str:
    snippet = post.get("snippet") if isinstance(post.get("snippet"), dict) else {}
    return _first_text(post.get("title"), snippet.get("title"), post.get("caption"), post.get("text"), post.get("description"))


def _post_date(post: dict[str, Any]) -> str:
    snippet = post.get("snippet") if isinstance(post.get("snippet"), dict) else {}
    return _first_text(post.get("publishedAt"), post.get("published_at"), post.get("createdAt"), post.get("timestamp"), snippet.get("publishedAt"))


def _post_url(post: dict[str, Any], platform: str) -> str:
    snippet = post.get("snippet") if isinstance(post.get("snippet"), dict) else {}
    resource = snippet.get("resourceId") if isinstance(snippet.get("resourceId"), dict) else {}
    video_id = _first_text(post.get("id"), post.get("videoId"), resource.get("videoId"))
    if _text(post.get("url")):
        return _text(post.get("url"))
    if platform == "youtube" and video_id:
        if _text(post.get("kind")) == "youtube#channel":
            return f"https://www.youtube.com/channel/{video_id}"
        return f"https://www.youtube.com/watch?v={video_id}"
    return ""


def _post_text_blob(post: dict[str, Any]) -> str:
    snippet = post.get("snippet") if isinstance(post.get("snippet"), dict) else {}
    branding = post.get("brandingSettings") if isinstance(post.get("brandingSettings"), dict) else {}
    branding_channel = branding.get("channel") if isinstance(branding.get("channel"), dict) else {}
    values: list[str] = [
        _post_title(post),
        _text(post.get("description")),
        _text(snippet.get("description")),
        _text(post.get("caption")),
        _text(post.get("text")),
        _text(post.get("biography")),
        _text(post.get("bio")),
        _text(post.get("primary_topic")),
        _text(post.get("secondary_topics_json")),
        _text(post.get("brand_collaborations_json")),
        _text(post.get("potential_concerns_json")),
        _text(branding_channel.get("keywords")),
        _text(branding_channel.get("description")),
    ]
    hashtags = post.get("hashtags")
    if isinstance(hashtags, list):
        values.extend(_text(item) for item in hashtags)
    tags = snippet.get("tags")
    if isinstance(tags, list):
        values.extend(_text(item) for item in tags)
    return " ".join(item for item in values if item)


def _row_profile_post(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": f"kol_pool:{row.get('id')}:profile",
        "title": _first_text(row.get("display_name"), row.get("handle")),
        "description": " ".join(
            item
            for item in (
                _text(row.get("bio")),
                _text(row.get("primary_topic")),
                _text(row.get("secondary_topics_json")),
                _text(row.get("brand_collaborations_json")),
                _text(row.get("potential_concerns_json")),
                _text(row.get("recommended_product_lines_json")),
            )
            if item
        ),
        "published_at": _first_text(row.get("last_seen_at"), row.get("updated_at"), row.get("created_at")),
        "source": "vkpi_kol_pool.profile_fields",
    }


def _depth_for_text(text: str, brand: str) -> str:
    lowered = f" {text.lower()} "
    if any(pattern in lowered for pattern in SPONSORED_PATTERNS) and brand in lowered:
        return "sponsored"
    if any(pattern in lowered for pattern in GIFTED_PATTERNS) and brand in lowered:
        return "gifted"
    if any(pattern in lowered for pattern in EVALUATED_PATTERNS):
        return "evaluated"
    return "mentioned"


def _sentiment_for_text(text: str) -> str:
    lowered = text.lower()
    positive = any(pattern in lowered for pattern in POSITIVE_PATTERNS)
    negative = any(pattern in lowered for pattern in NEGATIVE_PATTERNS)
    if positive and not negative:
        return "positive"
    if negative and not positive:
        return "negative"
    return "neutral"


def _extract_posts(raw_platform_data: dict[str, Any]) -> list[dict[str, Any]]:
    posts: list[dict[str, Any]] = []
    videos = raw_platform_data.get("videos")
    if isinstance(videos, list):
        posts.extend(item for item in videos if isinstance(item, dict))
    profile = raw_platform_data.get("profile")
    if isinstance(profile, dict):
        for key in ("items", "posts", "latestPosts", "videos"):
            items = profile.get(key)
            if isinstance(items, list):
                posts.extend(item for item in items if isinstance(item, dict))
        raw = profile.get("raw")
        if isinstance(raw, dict):
            for key in ("items", "posts", "latestPosts"):
                items = raw.get(key)
                if isinstance(items, list):
                    posts.extend(item for item in items if isinstance(item, dict))
    deduped: list[dict[str, Any]] = []
    seen: set[str] = set()
    for post in posts:
        key = _first_text(post.get("id"), post.get("url"), post.get("shortCode"), _post_title(post))
        if key and key in seen:
            continue
        if key:
            seen.add(key)
        deduped.append(post)
    return deduped


def detect_competitor_mentions(post: dict[str, Any], *, platform: str = "") -> list[dict[str, Any]]:
    text_blob = _post_text_blob(post)
    if not text_blob:
        return []
    brands = load_competitor_brands()
    mentions: list[dict[str, Any]] = []
    for brand, config in brands.items():
        matched = [keyword for keyword in config["keywords"] if _keyword_match(text_blob, keyword)]
        if not matched:
            continue
        mentions.append({
            "brand": brand,
            "depth": _depth_for_text(text_blob, brand),
            "sentiment": _sentiment_for_text(text_blob),
            "matched_keywords": matched[:6],
            "post_uid": _first_text(post.get("id"), post.get("shortCode"), post.get("url"), _post_title(post)),
            "title": _post_title(post),
            "url": _post_url(post, platform),
            "published_at": _post_date(post),
            "evidence": text_blob[:600],
        })
    return mentions
