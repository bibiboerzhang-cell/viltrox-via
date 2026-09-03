"""定向本地召回的纯工具:文本/数值归一、cell 上下文、排序键、身份键与去重合并。

从 ``targeted_local_recall`` 拆出(千行卫兵 / 800 软棘轮),零业务决策、零 I/O;
``targeted_local_recall`` 与 ``targeted_local_backfill`` 共用,依赖方向单向。
"""
from __future__ import annotations

from typing import Any

from app.domains.kol.identity import canonical_creator_aliases


def _text(value: Any) -> str:
    return " ".join(str(value or "").split()).strip()


def _number(value: Any) -> float:
    try:
        return float(value or 0.0)
    except (TypeError, ValueError):
        return 0.0


def _cell_context(cell: dict[str, Any]) -> dict[str, Any]:
    context = {
        "query_cell_id": cell.get("query_cell_id"),
        "objective": cell.get("objective"),
        "segment": cell.get("segment"),
        "segment_label": cell.get("segment_label"),
        "primary_query": cell.get("primary_query"),
        "required_evidence_groups": list(cell.get("required_evidence_groups") or []),
        "brand_or_model_required": cell.get("brand_or_model_required") is True,
        "brand_or_model_ranking_weight": cell.get("brand_or_model_ranking_weight"),
    }
    if isinstance(cell.get("follower_filter"), dict):
        context["follower_filter"] = dict(cell["follower_filter"])
    if isinstance(cell.get("locked_term_groups"), dict):
        context["locked_term_groups"] = dict(cell["locked_term_groups"])
    return context


def _annotate(items: list[dict[str, Any]], cell: dict[str, Any]) -> list[dict[str, Any]]:
    context = _cell_context(cell)
    return [
        {
            **item,
            "query_cell_id": cell["query_cell_id"],
            "query_cell_segment": cell.get("segment"),
            "query_cell_query": cell["primary_query"],
            "matched_query_cells": [context],
        }
        for item in items
    ]


def _rank_key(item: dict[str, Any]) -> tuple[float, float, float]:
    growth = item.get("growth_candidate_score")
    if growth is not None:
        return (
            _number(growth),
            _number(item.get("evidence_confidence")),
            _number(item.get("display_rank_score")),
        )
    return (
        _number(item.get("display_rank_score")),
        _number(item.get("recall_rank_score")),
        _number(item.get("retrieval_score")),
    )


def _merge_matches(existing: dict[str, Any], incoming: dict[str, Any]) -> dict[str, Any]:
    winner, other = (
        (existing, incoming)
        if _rank_key(existing) >= _rank_key(incoming)
        else (incoming, existing)
    )
    merged = dict(winner)
    for key, value in other.items():
        if merged.get(key) in (None, "", [], {}):
            merged[key] = value
    contexts: list[dict[str, Any]] = []
    seen: set[str] = set()
    for source in (existing, incoming):
        for raw in source.get("matched_query_cells") or []:
            if not isinstance(raw, dict):
                continue
            cell_id = _text(raw.get("query_cell_id"))
            if cell_id and cell_id not in seen:
                seen.add(cell_id)
                contexts.append(dict(raw))
    merged["matched_query_cells"] = contexts
    return merged


def _identity_keys(item: dict[str, Any]) -> set[str]:
    aliases = canonical_creator_aliases(item)
    if aliases:
        return {f"alias:{alias}" for alias in aliases}
    return {
        "fallback:"
        f"{_text(item.get('platform')).lower()}:"
        f"{_text(item.get('handle')).lstrip('@').lower()}:"
        f"{item.get('kol_pool_id') or ''}"
    }


def _dedupe(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    output: list[dict[str, Any]] = []
    aliases: list[set[str]] = []
    fallback: dict[str, int] = {}
    for item in items:
        item_aliases = canonical_creator_aliases(item)
        index = next(
            (position for position, known in enumerate(aliases) if item_aliases and item_aliases.intersection(known)),
            None,
        )
        if index is None and not item_aliases:
            key = (
                f"{_text(item.get('platform')).lower()}:"
                f"{_text(item.get('handle')).lstrip('@').lower()}:"
                f"{item.get('kol_pool_id') or ''}"
            )
            index = fallback.get(key)
        if index is None:
            index = len(output)
            output.append(item)
            aliases.append(set(item_aliases))
            if not item_aliases:
                fallback[key] = index
            continue
        output[index] = _merge_matches(output[index], item)
        aliases[index].update(item_aliases)
    return output


__all__ = [
    "_annotate",
    "_cell_context",
    "_dedupe",
    "_identity_keys",
    "_merge_matches",
    "_number",
    "_rank_key",
    "_text",
]
