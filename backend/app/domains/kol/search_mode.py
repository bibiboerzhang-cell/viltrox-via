"""Explicit search-source selection, separate from ranking/search strategy."""
from __future__ import annotations

from typing import Any

SEARCH_MODES = frozenset({"hybrid", "fresh_network", "saved"})


def normalize_search_mode(value: Any = None) -> str:
    """Default legacy requests to hybrid; never guess an unknown paid mode."""
    if value is None:
        return "hybrid"
    if not isinstance(value, str) or value.strip().lower() not in SEARCH_MODES:
        raise ValueError("search_mode must be hybrid, fresh_network, or saved")
    return value.strip().lower()
