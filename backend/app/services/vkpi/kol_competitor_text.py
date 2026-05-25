"""Compatibility shim for KOL competitor text helpers.

The implementation lives in the KOL domain.
"""
from app.domains.kol.competitor_text import (
    _extract_posts,
    _first_text,
    _loads,
    _post_date,
    _post_text_blob,
    _post_title,
    _post_url,
    _row_profile_post,
    _text,
    detect_competitor_mentions,
    load_competitor_brands,
)

__all__ = [
    "_extract_posts",
    "_first_text",
    "_loads",
    "_post_date",
    "_post_text_blob",
    "_post_title",
    "_post_url",
    "_row_profile_post",
    "_text",
    "detect_competitor_mentions",
    "load_competitor_brands",
]
