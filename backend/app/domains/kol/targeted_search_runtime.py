"""Shared runtime wiring for targeted KOL search.

The HTTP preview and queued worker must derive follower gates, QueryCells and
the prospective/existing-evidence boundary identically.  Keeping that wiring
here prevents either entrypoint from silently drifting back to legacy recall.
"""
from __future__ import annotations

from collections.abc import Callable
from typing import Any

from app.domains.kol import (
    profile_recall_qualification,
    search_relaxation,
    targeted_local_recall,
    targeted_query_execution,
)


Recall = Callable[..., dict[str, Any]]


def prepare_local_search(
    *,
    plan: dict[str, Any],
    body: dict[str, Any],
    recall_filters: dict[str, Any],
    market: Any,
    platforms: Any,
) -> dict[str, Any]:
    """Return one server-owned local-search context for preview or worker."""

    follower = dict(plan.get("follower_filter")) if isinstance(plan.get("follower_filter"), dict) else {}
    filters = dict(recall_filters)
    followers_min = follower.get("followers_min")
    followers_max = follower.get("followers_max")
    if followers_min is None:
        followers_min = filters.get("followers_min", filters.get("follower_min"))
    if followers_max is None:
        followers_max = filters.get("followers_max", filters.get("follower_max"))
    if followers_min is not None:
        filters["followers_min"] = followers_min
    if followers_max is not None:
        filters["followers_max"] = followers_max

    objective = str(plan.get("objective") or "prospective_growth").strip()
    follower_source = str(follower.get("source") or (
        "operator_filter" if followers_min is not None or followers_max is not None else "not_requested"
    )).strip()
    # 松绑口径是产品默认;``strict_gates=true`` 一键退回 2026-08 的严口径,
    # ``hide_team_favorites=true`` 只把「同事已关注」的人重新藏起来,不连带收紧另外两道闸。
    gate_mode = search_relaxation.resolve_mode(body)
    policy = profile_recall_qualification.smart_local_policy(
        market=market,
        platforms=platforms,
        languages=body.get("languages") or body.get("content_languages"),
        profile_types=body.get("profile_types") or body.get("kol_types"),
        gate_mode=gate_mode,
        hide_team_favorites=search_relaxation.resolve_hide_team_favorites(
            body, mode=gate_mode
        ),
    )
    if objective == "prospective_growth" or followers_min is not None or followers_max is not None:
        policy["followers_filter"] = profile_recall_qualification.follower_filter_policy(
            followers_min=followers_min,
            followers_max=followers_max,
            source=follower_source,
            unknown_policy=(
                profile_recall_qualification.FOLLOWERS_UNKNOWN_REJECT
                if followers_min is not None or followers_max is not None
                else profile_recall_qualification.FOLLOWERS_UNKNOWN_PENDING
            ),
        )

    brief = dict(plan.get("search_brief")) if isinstance(plan.get("search_brief"), dict) else {}
    raw_cells = brief.get("query_cells") or plan.get("query_cells") or []
    cells, omitted = targeted_query_execution.normalize_first_round_cells(raw_cells)
    return {
        "objective": objective,
        "resolved_product": dict(plan.get("resolved_product")) if isinstance(plan.get("resolved_product"), dict) else {},
        "recall_filters": filters,
        "follower_filter": follower,
        "followers_min": followers_min,
        "followers_max": followers_max,
        "follower_source": follower_source,
        "local_qualification_policy": policy,
        "search_brief": brief,
        "query_cells": cells,
        "query_cells_omitted": omitted,
    }


def execute_local_search(
    *,
    context: dict[str, Any],
    recall_kwargs: dict[str, Any],
    recall: Recall,
    target: int = profile_recall_qualification.SMART_LOCAL_TARGET,
) -> dict[str, Any]:
    """Execute local-pool recall without embedding, LLM or discovery I/O."""

    local_kwargs = {**recall_kwargs, "provider_free": True}
    cells = context.get("query_cells") if isinstance(context.get("query_cells"), list) else []
    if not cells:
        return recall(**local_kwargs)
    return targeted_local_recall.execute_first_round_local_cells(
        query_cells=cells,
        search_brief=context.get("search_brief"),
        base_kwargs=local_kwargs,
        recall=recall,
        target=target,
    )


__all__ = ["execute_local_search", "prepare_local_search"]
