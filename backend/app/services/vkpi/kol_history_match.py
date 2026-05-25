"""Compatibility bridge for KOL history matching use cases."""
from __future__ import annotations

from app.domains.kol.history_match import (
    _avatar_from_raw,
    _recent_post_summary,
    annotate_platform_items,
    find_history_match,
    normalize_history_handle,
    search_pool_for_natural,
)

__all__ = [
    "_avatar_from_raw",
    "_recent_post_summary",
    "annotate_platform_items",
    "find_history_match",
    "normalize_history_handle",
    "search_pool_for_natural",
]
