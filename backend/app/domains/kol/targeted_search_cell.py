"""Pure QueryCell construction for targeted KOL search."""
from __future__ import annotations

import math
from collections.abc import Iterable
from typing import Any

from app.domains.kol.targeted_search_terms import build_locked_term_groups


PROSPECTIVE_GROWTH = "prospective_growth"
EXISTING_EVIDENCE = "existing_evidence"
DEFAULT_RAW_LIMIT = 12
MIN_RAW_LIMIT = 10
MAX_RAW_LIMIT = 15
DEFAULT_TARGET_COUNT = 30


def _text(value: Any) -> str:
    return " ".join(str(value or "").split()).strip()


def _dedupe(values: Iterable[Any]) -> list[str]:
    output: list[str] = []
    seen: set[str] = set()
    for value in values:
        item = _text(value)
        key = item.casefold()
        if item and key not in seen:
            seen.add(key)
            output.append(item)
    return output


def first_round_raw_limit(body: Any, *, cell_count: int) -> int:
    payload = body if isinstance(body, dict) else {}
    explicit = "first_round_raw_limit" in payload or "raw_limit" in payload
    if explicit:
        raw_value = payload.get("first_round_raw_limit", payload.get("raw_limit"))
        try:
            value = int(raw_value)
        except (TypeError, ValueError):
            value = DEFAULT_RAW_LIMIT
    else:
        value = math.ceil(DEFAULT_TARGET_COUNT / max(1, int(cell_count or 1)))
    return max(MIN_RAW_LIMIT, min(MAX_RAW_LIMIT, value))


def prospective_primary_query(segment_term: Any) -> str:
    """Aim exact first-round recall at educators and gear decision makers."""

    return _text(f"{_text(segment_term)} tutorial camera gear")


def build_query_cell(
    *,
    index: int,
    key: str,
    label: str,
    source: str,
    locked: bool,
    primary: str,
    fallbacks: Iterable[Any],
    objective: str,
    platforms: list[str],
    raw_limit: int,
    follower_filter: dict[str, Any],
    capability: str,
) -> dict[str, Any]:
    prospective = objective == PROSPECTIVE_GROWTH
    return {
        "query_cell_id": f"segment_{index}_{key}",
        "objective": objective,
        "segment": key,
        "segment_label": label,
        "segment_source": source,
        "segment_locked": locked,
        "primary_query": _text(primary),
        "fallback_queries": [
            value
            for value in _dedupe(fallbacks)
            if value.casefold() != _text(primary).casefold()
        ],
        "platforms": platforms,
        "round": 1,
        "raw_limit": raw_limit,
        "independent_raw_quota": True,
        "required_evidence_groups": [
            "product_use_fit",
            "segment_use_case",
            "market_activation",
        ],
        "brand_or_model_required": objective == EXISTING_EVIDENCE,
        "brand_or_model_ranking_weight": 0 if prospective else None,
        "discovery_intent": (
            "segment_creator_education_gear"
            if prospective
            else "existing_product_evidence"
        ),
        "capability_in_primary_query": not prospective,
        "capability_verification_policy": (
            "post_retrieval_locked_evidence"
            if prospective
            else "anchored_query_and_locked_evidence"
        ),
        "follower_filter": dict(follower_filter),
        "locked_term_groups": build_locked_term_groups(
            capability=capability,
            segment=key,
            segment_label=label,
        ),
    }


__all__ = [
    "build_query_cell",
    "first_round_raw_limit",
    "prospective_primary_query",
]
