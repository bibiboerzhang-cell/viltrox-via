"""Shared, IO-free evidence construction for targeted QueryCells.

Both local-pool and online candidates must receive the same server-owned
capability-use and scene-alias treatment.  Client-supplied aliases are never
trusted: the QueryCell is rebuilt from the static targeted-search registry
before any controlled evidence is emitted.
"""
from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from app.domains.kol.profile_recall_match_evidence import (
    build_controlled_alias_evidence,
    build_match_evidence,
)
from app.domains.kol.targeted_search_contract import rebuild_locked_term_groups_for_cell


def _merge_evidence(*values: Any, limit: int = 12) -> list[dict[str, str]]:
    output: list[dict[str, str]] = []
    seen: set[tuple[str, str, str, str, str]] = set()
    for value in values:
        for raw in value if isinstance(value, list) else []:
            if not isinstance(raw, dict):
                continue
            item = {str(key): str(nested) for key, nested in raw.items() if nested not in (None, "")}
            key = (
                item.get("field", ""),
                item.get("term", ""),
                item.get("source", ""),
                item.get("canonical_term", ""),
                item.get("observed_term", ""),
            )
            if not key[0] or not key[1] or key in seen:
                continue
            seen.add(key)
            output.append(item)
            if len(output) >= max(1, int(limit)):
                return output
    return output


def build_query_cell_match_evidence(
    row: dict[str, Any],
    evidence: dict[str, Any],
    query_text: Any,
    *,
    query_cell: dict[str, Any] | None = None,
    required_product_terms: Iterable[str] = (),
    min_intent_terms: int = 2,
    fallback_query_text: Any = "",
) -> list[dict[str, str]]:
    """Build identical descriptive prospective-suitability proof for both lanes."""

    direct = build_match_evidence(
        row,
        evidence,
        query_text,
        required_product_terms=required_product_terms,
        min_intent_terms=min_intent_terms,
    )
    fallback = str(fallback_query_text or "").strip()
    if not direct and fallback and fallback != str(query_text or "").strip():
        direct = build_match_evidence(
            row,
            evidence,
            fallback,
            required_product_terms=required_product_terms,
            min_intent_terms=min_intent_terms,
        )
    locked = rebuild_locked_term_groups_for_cell(query_cell or {})
    controlled = build_controlled_alias_evidence(row, evidence, locked)
    # Controlled coordinates come first so the bounded projection cannot hide
    # the product/scene proof behind a long list of ordinary lexical matches.
    return _merge_evidence(controlled, direct)


__all__ = ["build_query_cell_match_evidence"]
