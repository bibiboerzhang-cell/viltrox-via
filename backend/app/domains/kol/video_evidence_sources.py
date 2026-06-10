"""Shared source enums for KOL video evidence materialization."""
from __future__ import annotations

EVIDENCE_SOURCE_MAX_LEN = 40

SOURCE_MANUAL_URL = "manual_url"
SOURCE_URL_METADATA = "url_metadata"
SOURCE_VIDEO_METADATA = "video_metadata"
SOURCE_YOUTUBE_API = "youtube_api"
SOURCE_APIFY = "apify"

PROFILE_CRAWL_SOURCES = {
    "youtube": "youtube_profile_crawl",
    "instagram": "instagram_profile_crawl",
    "tiktok": "tiktok_profile_crawl",
}


def profile_crawl_source(platform: str) -> str:
    key = str(platform or "").strip().lower()
    return PROFILE_CRAWL_SOURCES.get(key) or (f"{key}_profile_crawl" if key else SOURCE_URL_METADATA)


def validate_source_value(field: str, value: str, *, max_len: int = EVIDENCE_SOURCE_MAX_LEN) -> str:
    text = str(value or "").strip()
    if len(text) > max_len:
        raise ValueError(f"{field} exceeds varchar({max_len}): {text!r} ({len(text)} chars)")
    return text
