"""Shared runtime wiring for targeted KOL search.

The HTTP preview and queued worker must derive follower gates, QueryCells and
the prospective/existing-evidence boundary identically.  Keeping that wiring
here prevents either entrypoint from silently drifting back to legacy recall.
"""
from __future__ import annotations

from collections.abc import Callable
from copy import deepcopy
from typing import Any

from app.domains.kol import (
    operator_search_spec,
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
    spec = operator_search_spec.build_operator_search_spec(
        plan=plan, body=body, recall_filters=recall_filters, market=market, platforms=platforms,
    )
    filters = deepcopy(spec["filters"])
    followers_min = filters.get("followers_min", filters.get("follower_min"))
    followers_max = filters.get("followers_max", filters.get("follower_max"))

    objective = str(plan.get("objective") or "prospective_growth").strip()
    literal_filters = operator_search_spec.normalize_operator_body(body)["filters"]
    has_bounds = followers_min is not None or followers_max is not None
    has_literal_bounds = any(literal_filters.get(key) is not None for key in ("followers_min", "followers_max"))
    follower_source = (
        "not_requested" if not has_bounds else "operator_filter" if has_literal_bounds
        else str(follower.get("source") or "operator_filter").strip()
    )
    follower.update(followers_min=followers_min, followers_max=followers_max, source=follower_source)
    # 松绑口径是产品默认;``strict_gates=true`` 一键退回 2026-08 的严口径,
    # ``hide_team_favorites=true`` 只把「同事已关注」的人重新藏起来,不连带收紧另外两道闸。
    gate_mode = search_relaxation.resolve_mode(body)
    policy = profile_recall_qualification.smart_local_policy(
        market=market,
        platforms=platforms,
        languages=spec["policy_inputs"]["languages"],
        profile_types=spec["policy_inputs"]["profile_types"],
        geo_constraints=spec["policy_inputs"]["geo_constraints"],
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
    raw_cells = brief["query_cells"] if "query_cells" in brief else plan.get("query_cells", [])
    if raw_cells is not None:
        if not isinstance(raw_cells, list) or any(
            not isinstance(raw, dict)
            or (raw.get("round", 1) == 1 and not targeted_query_execution.normalize_first_round_cells([raw])[0])
            for raw in raw_cells
        ):
            raise ValueError("invalid_query_cells")
    else:
        raw_cells = []
    cells, omitted = targeted_query_execution.normalize_first_round_cells(raw_cells)
    if raw_cells and not cells:
        raise ValueError("invalid_query_cells")
    semantics = brief.get("query_plan_semantics") or plan.get("query_plan_semantics")
    if isinstance(semantics, dict) and semantics.get("status") == "invalid":
        raise ValueError("invalid_query_plan_semantics")
    return {
        "operator_search_spec": spec,
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
