"""Prepared keyword inputs for the Category Tracks hot path."""
from __future__ import annotations

from collections.abc import Callable
from typing import Any


def prepare_keyword_groups(
    vocab: dict[str, dict[str, Any]],
    viltrox_terms: list[str],
) -> tuple[dict[str, tuple[str, ...]], tuple[str, ...], Callable[[str, str], bool]]:
    """Normalize reusable terms once and return a boundary-aware matcher."""
    try:
        from app.domains.kol.competitor_text import _keyword_match_prepared

        match = _keyword_match_prepared
    except Exception:
        match = lambda lowered, key: key in lowered
    brand_keywords = {
        brand: tuple(
            normalized
            for keyword in (meta.get("keywords") or [])
            if (normalized := str(keyword or "").lower().strip())
        )
        for brand, meta in vocab.items()
    }
    own_terms = tuple(
        normalized
        for term in viltrox_terms
        if (normalized := str(term or "").lower().strip())
    )
    return brand_keywords, own_terms, match
