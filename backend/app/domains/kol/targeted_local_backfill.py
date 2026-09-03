"""定向 QueryCell 层的回填区合并:精准命中之后才轮到回填,缺口只按精准计。

每个 cell 的召回层(``profile_recall`` smart-local)已经把回填的人单列在 ``backfill_items``
里、每人都带 ``backfill_tier`` 标记;本模块只做三件事——按 cell 截取、跨 cell 去重后补到
剩余名额、把多 cell 的回填账合一。零 I/O、零 LLM,不改排序公式,不写 fit_score。
"""
from __future__ import annotations

from typing import Any

from app.domains.kol import profile_recall_backfill_ladder as _ladder
from app.domains.kol.targeted_local_support import (
    _annotate,
    _dedupe,
    _identity_keys,
    _rank_key,
)


def _aggregate_backfill(
    contracts: list[dict[str, Any]],
    *,
    items: list[dict[str, Any]],
    target: int,
) -> dict[str, Any]:
    """多 cell 的回填账合一:最终选中的回填人按级计数;缺口原因按 cell 加总。"""

    blocks = [
        contract.get("backfill")
        for contract in contracts
        if isinstance(contract.get("backfill"), dict)
    ]
    filled_by_tier = {tier: 0 for tier in _ladder.TIER_ORDER}
    for item in items:
        tier = str(item.get("backfill_tier") or "")
        if tier in filled_by_tier:
            filled_by_tier[tier] += 1
    return {
        "schema": _ladder.BACKFILL_LADDER_SCHEMA,
        "status": "filled" if any(filled_by_tier.values()) else "nothing_to_backfill",
        "target": int(target),
        "filled_total": sum(filled_by_tier.values()),
        "filled_by_tier": filled_by_tier,
        "cell_filled_by_tier": _ladder.merge_tier_counts(*blocks),
        "gaps": _ladder.merge_gaps(*blocks),
        "cells_with_backfill": sum(1 for block in blocks if int(block.get("filled_total") or 0) > 0),
        "policy": "favorites_soft_excluded;verticals_relaxable;explicit_filters_never_relaxed",
    }


def _backfill_rows(
    result: dict[str, Any],
    cell: dict[str, Any],
    candidate_cap: int,
) -> list[dict[str, Any]]:
    """本 cell 召回层回填区(每人都带 backfill_tier 标记),按同一排序键截到 cap。"""

    rows = [
        dict(item)
        for item in (result.get("backfill_items") or [])
        if _ladder.is_backfill_item(item)
    ]
    rows = _annotate(rows, cell)
    rows.sort(key=_rank_key, reverse=True)
    return rows[:candidate_cap]


def _backfill_take(
    backfill_candidates: list[dict[str, Any]],
    *,
    taken_identity_keys: set[str],
    capacity: int,
) -> tuple[list[dict[str, Any]], int]:
    """精准命中之后才轮到回填区:去重、剔除已选身份、按同一排序键截到剩余名额。"""

    available = [
        item
        for item in sorted(_dedupe(backfill_candidates), key=_rank_key, reverse=True)
        if _identity_keys(item).isdisjoint(taken_identity_keys)
    ]
    chosen = available[: max(0, int(capacity))]
    for item in chosen:
        item["counts_toward_target"] = False
        proof = item.get("qualification_evidence")
        if isinstance(proof, dict):
            proof["counts_toward_target"] = False
    return chosen, len(available)


def _favorite_excluded_total(execution: Any) -> int:
    """各 cell 召回诊断里「被同事收藏而隐藏」的人数加总(``execution`` 为 _CellExecution)。"""

    total = 0
    for result in execution.results:
        diagnostics = result.get("diagnostics") if isinstance(result.get("diagnostics"), dict) else {}
        try:
            total += int(diagnostics.get("favorite_excluded_count") or 0)
        except (TypeError, ValueError):
            continue
    return total


__all__ = [
    "_aggregate_backfill",
    "_backfill_rows",
    "_backfill_take",
    "_favorite_excluded_total",
]
