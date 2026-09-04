"""Pure QueryCell construction for targeted KOL search."""
from __future__ import annotations

import math
from collections.abc import Iterable
from typing import Any

from app.domains.kol.targeted_search_terms import (
    build_locked_term_groups,
    required_role_terms_for,
)


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
    """Search the requested people directly; capability is verified afterward."""

    return _text(segment_term)


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
    product_evidence_required: bool = True,
    product_evidence_basis: str = "resolved_product",
    scene_terms: Iterable[Any] = (),
    role_terms: Iterable[Any] = (),
    role_only: bool = False,
) -> dict[str, Any]:
    prospective = objective == PROSPECTIVE_GROWTH
    required_scene_terms = [] if role_only else (_dedupe(scene_terms) or [key])
    required_role_terms = _dedupe(role_terms) or required_role_terms_for(primary)
    required_evidence_groups = ["market_activation"]
    if required_scene_terms:
        required_evidence_groups.insert(0, "segment_use_case")
    if required_role_terms:
        required_evidence_groups.insert(0, "people_role")
    if product_evidence_required:
        required_evidence_groups.insert(0, "product_use_fit")
    return {
        "query_cell_id": f"segment_{index}_{key}",
        "objective": objective,
        "segment": key,
        "segment_label": label,
        "segment_source": source,
        "segment_locked": locked,
        "required_scene_terms": required_scene_terms,
        "scene_match_mode": "all" if len(required_scene_terms) > 1 else "any",
        "required_role_terms": required_role_terms,
        "role_match_mode": "all" if len(required_role_terms) > 1 else "any",
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
        "required_evidence_groups": required_evidence_groups,
        "product_evidence_required": product_evidence_required,
        "product_evidence_basis": product_evidence_basis if product_evidence_required else "none",
        "brand_or_model_required": objective == EXISTING_EVIDENCE,
        "brand_or_model_ranking_weight": 0 if prospective else None,
        "discovery_intent": (
            "operator_people_intent"
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
            capability=capability if product_evidence_required else "",
            segment="" if role_only else key,
            segment_label="" if role_only else label,
            scene_terms=required_scene_terms,
            role_terms=required_role_terms,
        ),
    }


__all__ = [
    "build_query_cell",
    "first_round_raw_limit",
    "prospective_primary_query",
]
