"""Small, behavior-preserving stages for the queued profile-discovery pipeline.

The public pipeline deliberately keeps its patchable provider/filter/advance
seams.  This module owns deterministic planning, recall setup, optional queue
orchestration, and final receipt projection so those concerns can be tested in
isolation without making provider calls.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable


@dataclass(frozen=True)
class StageDependencies:
    profile_discovery_evidence: Any
    profile_recall_qualification: Any
    search_sessions: Any
    targeted_search_runtime: Any
    smart_query_planner: Any
    explicit_platforms_from_query: Callable[[str], list[str]]
    resolve_market_constraint: Callable[[str, Any], str]
    query_evidence_terms: Callable[[Any], list[str]]
    completion_contract: Callable[..., dict[str, Any]]
    int_value: Callable[..., int]
    text: Callable[[Any], str]


@dataclass(frozen=True)
class PlanningState:
    query: str
    operator_query: str
    operator_anchor: dict[str, Any]
    operator_platforms: list[str]
    operator_market: str
    early_result: dict[str, Any] | None = None


@dataclass(frozen=True)
class RecallSetup:
    context: dict[str, Any]
    recall_kwargs: dict[str, Any]
    recall_filters: dict[str, Any]
    resolved_platforms: Any
    follower_filter: dict[str, Any]
    followers_min: int | None
    followers_max: int | None
    follower_source: str
    query_cells: list[dict[str, Any]]
    query_cells_omitted: bool


@dataclass(frozen=True)
class RecallState:
    result: dict[str, Any]
    session: Any
    base_count: int
    advance_limit: int
    smart_local_30: bool


_UNTRUSTED_PLAN_KEYS = (
    "product_focus",
    "target_persona",
    "resolved_product",
    "llm_query_plan",
    "query_plan_source",
    "search_brief",
    "query_cells",
    "follower_filter",
)


def _guard_rich_plan(
    *,
    guard_plan: dict[str, Any],
    rich_plan: dict[str, Any],
    source: str,
    planner: Any,
    deps: StageDependencies,
) -> tuple[dict[str, Any], str]:
    if deps.text(guard_plan.get("status")) == "needs_clarification":
        return guard_plan, source

    guard_query = deps.text(guard_plan.get("search_query"))
    guard_terms = set(deps.query_evidence_terms(guard_query))
    rich_terms = set(deps.query_evidence_terms(rich_plan.get("search_query")))
    if guard_terms and not guard_terms.issubset(rich_terms):
        return (
            {
                **rich_plan,
                "search_query": guard_query,
                "evidence_anchor_source": "provider_free_guard",
            },
            f"{source}_with_guard_anchors",
        )
    if not rich_terms:
        return planner._require_evidence_anchor(guard_plan), source
    return rich_plan, source


def _resolve_worker_plan(
    query: str,
    payload: dict[str, Any],
    deps: StageDependencies,
) -> tuple[dict[str, Any], str]:
    planner = deps.smart_query_planner
    guard_plan = planner.plan_text_query_provider_free(query, body=payload)
    try:
        rich_plan = planner.plan_text_query(query, body=payload, staff=None)
        source = "llm_plan"
    except Exception:
        rich_plan = guard_plan
        source = "provider_free_guard_fallback"
    return _guard_rich_plan(
        guard_plan=guard_plan,
        rich_plan=rich_plan,
        source=source,
        planner=planner,
        deps=deps,
    )


def _clarification_result(
    *,
    session_id: int,
    query: str,
    plan: dict[str, Any],
    deps: StageDependencies,
) -> dict[str, Any]:
    contract = deps.completion_contract(
        base_count=0,
        total=0,
        terminal_count=0,
        ready_count=0,
    )
    deps.search_sessions.update_session_result_summary(
        int(session_id),
        status="partial",
        summary_patch={
            "phase": "partial",
            "progress": {
                "base": 0,
                "total": 0,
                "profile_ready": 0,
                "profile_failed": 0,
                "complete_ready": 0,
                "complete_partial": 0,
                **contract,
            },
            **contract,
            "llm_query_plan": plan,
            "smart_search_profile_advance_job": {
                "status": "needs_clarification",
                "query_text": query,
                "advance_status": "not_started",
                "viltrox_fit_score_untouched": True,
            },
        },
    )
    return {
        "status": "needs_clarification",
        "session_id": int(session_id),
        "query": query,
        "query_plan_source": "product_catalog_guard",
        "llm_query_plan": plan,
        "recall": {
            "method": "product_catalog_guard",
            "returned_count": 0,
            "diagnostics": {},
        },
        "new_discovery": None,
        "advance": {"status": "not_started", "selected": 0, "counts": {}},
        "provider_calls_performed": False,
        "write_db": True,
        "writes": ["vkpi_kol_search_sessions"],
        "viltrox_fit_score_changed_ids": [],
        "viltrox_fit_score_untouched": True,
    }


def _apply_plan(
    payload: dict[str, Any],
    plan: dict[str, Any],
    source: str,
    deps: StageDependencies,
) -> str:
    payload["product_focus"] = plan.get("product_focus")
    payload["target_persona"] = deps.text(plan.get("target_persona"))
    payload["resolved_product"] = plan.get("resolved_product")
    payload["llm_query_plan"] = plan
    payload["objective"] = deps.text(plan.get("objective") or "prospective_growth")
    payload["search_brief"] = (
        dict(plan.get("search_brief")) if isinstance(plan.get("search_brief"), dict) else {}
    )
    payload["query_cells"] = list(
        payload["search_brief"].get("query_cells") or plan.get("query_cells") or []
    )
    payload["follower_filter"] = (
        dict(plan.get("follower_filter"))
        if isinstance(plan.get("follower_filter"), dict)
        else {}
    )
    if not payload.get("product_sku") and isinstance(plan.get("resolved_product"), dict):
        payload["product_sku"] = deps.text(plan["resolved_product"].get("sku"))
    for key in ("creator_quota", "reviewer_quota", "new_discovery_limit"):
        if payload.get(key) is None and plan.get(key) is not None:
            payload[key] = plan.get(key)
    payload["_worker_planned"] = True
    payload["query_plan_source"] = source
    return deps.text(plan.get("search_query"))


def prepare_plan(
    *,
    session_id: int,
    payload: dict[str, Any],
    deps: StageDependencies,
) -> PlanningState:
    query = deps.text(payload.get("query_text") or payload.get("input") or payload.get("query"))
    if not query:
        raise ValueError("smart profile advance payload missing query_text")
    operator_query = query
    operator_anchor = deps.profile_discovery_evidence.operator_anchor_inputs(payload)
    operator_platforms = deps.explicit_platforms_from_query(operator_query)
    operator_market = deps.resolve_market_constraint(
        operator_query,
        payload.get("market") or payload.get("country"),
    )
    if operator_platforms:
        payload["platforms"] = operator_platforms
        payload["new_discovery_platforms"] = operator_platforms
    if operator_market:
        payload["market"] = operator_market

    if payload.get("_worker_planned") is not True:
        for key in _UNTRUSTED_PLAN_KEYS:
            payload.pop(key, None)
        plan, source = _resolve_worker_plan(query, payload, deps)
        if deps.text(plan.get("status")) == "needs_clarification":
            return PlanningState(
                query=query,
                operator_query=operator_query,
                operator_anchor=operator_anchor,
                operator_platforms=operator_platforms,
                operator_market=operator_market,
                early_result=_clarification_result(
                    session_id=session_id,
                    query=query,
                    plan=plan,
                    deps=deps,
                ),
            )
        effective_query = _apply_plan(payload, plan, source, deps)
        if effective_query:
            query = effective_query
        if not operator_platforms and not any(
            payload.get(key)
            for key in (
                "platforms",
                "platform",
                "discovery_platforms",
                "new_discovery_platforms",
            )
        ):
            payload["platforms"] = []

    return PlanningState(
        query=query,
        operator_query=operator_query,
        operator_anchor=operator_anchor,
        operator_platforms=operator_platforms,
        operator_market=operator_market,
    )


def prepare_recall(
    payload: dict[str, Any],
    planning: PlanningState,
    deps: StageDependencies,
) -> RecallSetup:
    recall_filters = (
        dict(payload.get("filters") or {}) if isinstance(payload.get("filters"), dict) else {}
    )
    resolved_platforms = (
        planning.operator_platforms
        or payload.get("platforms")
        or payload.get("new_discovery_platforms")
        or payload.get("discovery_platforms")
        or payload.get("platform")
    )
    if resolved_platforms and not recall_filters.get("platforms"):
        recall_filters["platforms"] = resolved_platforms
    context = deps.targeted_search_runtime.prepare_local_search(
        plan=payload,
        body=payload,
        recall_filters=recall_filters,
        market=planning.operator_market,
        platforms=resolved_platforms,
    )
    recall_filters = context["recall_filters"]
    recall_kwargs = {
        "query_text": planning.query,
        "product_sku": deps.text(payload.get("product_sku")),
        "candidate_limit": deps.profile_recall_qualification.SMART_LOCAL_CANDIDATE_LIMIT,
        "limit": deps.profile_recall_qualification.SMART_LOCAL_TARGET,
        "creator_quota": max(0, min(deps.int_value(payload.get("creator_quota"), 15), 50)),
        "reviewer_quota": max(0, min(deps.int_value(payload.get("reviewer_quota"), 15), 50)),
        "ratio_policy": deps.text(payload.get("ratio_policy") or "soft"),
        "mixed_policy": deps.text(payload.get("mixed_policy") or "dominant"),
        "dedupe": True,
        "vector_weight": float(
            payload.get("vector_weight")
            if payload.get("vector_weight") is not None
            else deps.profile_recall_qualification.SMART_LOCAL_VECTOR_WEIGHT
        ),
        "type_weight": float(
            payload.get("type_weight")
            if payload.get("type_weight") is not None
            else deps.profile_recall_qualification.SMART_LOCAL_TYPE_WEIGHT
        ),
        "type_boost_enabled": bool(payload.get("type_boost_enabled", True)),
        "exclude_chinese": bool(payload.get("exclude_chinese", True)),
        "product_focus": payload.get("product_focus"),
        "target_persona": deps.text(payload.get("target_persona")),
        "filters": recall_filters,
        "search_strategy": deps.text(payload.get("search_strategy") or "balanced"),
        "bucket_policy": (
            payload.get("bucket_policy")
            if isinstance(payload.get("bucket_policy"), dict)
            else None
        ),
        "allow_backfill": False,
        "operator_query_text": planning.operator_query,
        "required_product_evidence_terms": (
            payload.get("resolved_product")
            if deps.text(payload.get("objective")) == "existing_evidence"
            else None
        ),
        "local_qualification_policy": context["local_qualification_policy"],
    }
    follower_filter = context["follower_filter"]
    return RecallSetup(
        context=context,
        recall_kwargs=recall_kwargs,
        recall_filters=recall_filters,
        resolved_platforms=resolved_platforms,
        follower_filter=follower_filter,
        followers_min=context["followers_min"],
        followers_max=context["followers_max"],
        follower_source=context["follower_source"],
        query_cells=context["query_cells"],
        query_cells_omitted=context["query_cells_omitted"],
    )


def attach_recall(
    *,
    session_id: int,
    payload: dict[str, Any],
    recall_result: dict[str, Any],
    deps: StageDependencies,
) -> RecallState:
    if isinstance(payload.get("llm_query_plan"), dict):
        recall_result["llm_query_plan"] = payload["llm_query_plan"]
    items = recall_result.get("items") if isinstance(recall_result.get("items"), list) else []
    buckets = (
        recall_result.get("buckets") if isinstance(recall_result.get("buckets"), dict) else {}
    )
    recall_count = len(items) or sum(
        len(bucket_items)
        for bucket_items in buckets.values()
        if isinstance(bucket_items, list)
    )
    smart_local_30 = payload.get("_smart_local_30_contract") is True
    advance_cap = deps.profile_recall_qualification.SMART_LOCAL_TARGET if smart_local_30 else 15
    advance_default = (
        deps.profile_recall_qualification.SMART_LOCAL_TARGET if smart_local_30 else 15
    )
    advance_limit = max(
        1,
        min(
            deps.int_value(
                payload.get("advance_limit") or payload.get("profile_advance_limit"),
                advance_default,
            ),
            advance_cap,
        ),
    )
    recall_total = min(recall_count, advance_limit)
    contract = deps.completion_contract(
        base_count=recall_count,
        total=recall_total,
        terminal_count=0,
        ready_count=0,
        active_tasks=recall_total,
        requested_tasks_terminal=False,
    )
    recall_session = deps.search_sessions.attach_recall_result(
        int(session_id),
        {
            **recall_result,
            "_session_pipeline_running": True,
            "_session_progress": {
                "base": recall_count,
                "total": recall_total,
                "profile_ready": 0,
                "profile_failed": 0,
                "complete_ready": 0,
                "complete_partial": 0,
                **contract,
            },
        },
    )
    return RecallState(
        result=recall_result,
        session=recall_session,
        base_count=recall_count,
        advance_limit=advance_limit,
        smart_local_30=smart_local_30,
    )


def changed_fit_ids(
    advance_result: dict[str, Any],
    deps: StageDependencies,
) -> list[int]:
    return [
        deps.int_value(value)
        for value in (advance_result.get("viltrox_fit_score_changed_ids") or [])
        if deps.int_value(value) > 0
    ]


def enqueue_content_fit(
    *,
    session_id: int,
    payload: dict[str, Any],
    provider_actor: dict[str, Any] | None,
    queue_module: Any,
    deps: StageDependencies,
) -> dict[str, Any] | None:
    if not bool(payload.get("include_content_fit", True)):
        return None
    try:
        return queue_module.enqueue_content_fit_for_session(
            session_id=int(session_id),
            product_sku=deps.text(payload.get("product_sku")),
            top_n=max(
                1,
                min(
                    deps.int_value(
                        payload.get("content_fit_top_n"),
                        queue_module.DEFAULT_TOP_N,
                    ),
                    queue_module.MAX_TOP_N,
                ),
            ),
            triggered_by_user_id=deps.int_value(payload.get("triggered_by_user_id")) or None,
            provider_actor=provider_actor,
        )
    except Exception:
        return {"status": "error", "reason": "content_fit_enqueue_failed"}


def enqueue_video_backfill(
    *,
    session_id: int,
    payload: dict[str, Any],
    queue_module: Any,
    deps: StageDependencies,
) -> dict[str, Any] | None:
    if not bool(payload.get("include_lazy_video_backfill", True)):
        return None
    try:
        return queue_module.enqueue_lazy_video_backfill_for_session(
            session_id=int(session_id),
            top_n=max(
                1,
                min(
                    deps.int_value(
                        payload.get("lazy_video_backfill_top_n"),
                        queue_module.DEFAULT_TOP_N,
                    ),
                    queue_module.MAX_TOP_N,
                ),
            ),
            staff=None,
        )
    except Exception:
        return {"status": "error", "reason": "video_backfill_enqueue_failed"}


def _profile_counts(
    advance_result: dict[str, Any],
    deps: StageDependencies,
) -> tuple[int, int, int, int]:
    profile_ready = 0
    profile_failed = 0
    for item in advance_result.get("items") or []:
        result = (
            item.get("result")
            if isinstance(item, dict) and isinstance(item.get("result"), dict)
            else {}
        )
        profile_status = deps.text(result.get("profile_status") or item.get("status")).lower()
        if profile_status in {"ready", "already_analyzed"}:
            profile_ready += 1
        elif "failed" in profile_status or profile_status == "error":
            profile_failed += 1
    counts = advance_result.get("counts") or {}
    profile_failed = max(
        profile_failed,
        deps.int_value(counts.get("failed")) + deps.int_value(counts.get("errors")),
    )
    selected_count = int(advance_result.get("selected") or 0)
    profile_completed = len(advance_result.get("items") or [])
    return profile_ready, profile_failed, selected_count, profile_completed


def finalize_pipeline(
    *,
    session_id: int,
    query: str,
    payload: dict[str, Any],
    recall: RecallState,
    new_discovery: dict[str, Any] | None,
    base_count: int,
    advance_result: dict[str, Any],
    changed_ids: list[int],
    content_fit: dict[str, Any] | None,
    field_topup: dict[str, Any] | None,
    pipeline_status_resolver: Callable[[dict[str, Any], dict[str, Any] | None, dict[str, Any]], str],
    deps: StageDependencies,
) -> dict[str, Any]:
    pipeline_status = pipeline_status_resolver(recall.result, new_discovery, advance_result)
    final_status = "partial" if changed_ids else pipeline_status
    profile_ready, profile_failed, selected_count, profile_completed = _profile_counts(
        advance_result,
        deps,
    )
    counts = advance_result.get("counts") or {}
    final_contract = deps.completion_contract(
        base_count=base_count,
        total=selected_count,
        terminal_count=profile_completed,
        ready_count=profile_ready,
        profile_failed=profile_failed,
        active_tasks=max(0, selected_count - profile_completed),
        requested_tasks_terminal=False,
    )
    deps.search_sessions.update_session_result_summary(
        int(session_id),
        status=final_status,
        summary_patch={
            "phase": "complete" if final_status == "ready" else "partial",
            "progress": {
                "base": base_count,
                "total": selected_count,
                "profile_ready": profile_ready,
                "profile_failed": profile_failed,
                "profile_completed": profile_completed,
                "profile_succeeded": max(0, profile_completed - profile_failed),
                "profile_remaining": max(0, selected_count - profile_completed),
                "complete_ready": deps.int_value(counts.get("ready")),
                "complete_partial": deps.int_value(counts.get("partial")),
                **final_contract,
            },
            **final_contract,
            **({"field_topup": field_topup} if field_topup else {}),
            "smart_search_profile_advance_job": {
                "status": pipeline_status,
                "query_text": query,
                "recall_returned": len(recall.result.get("items") or []),
                "new_discovery_status": (
                    (new_discovery or {}).get("status") if new_discovery else "not_requested"
                ),
                "advance_status": advance_result.get("status"),
                "advance_counts": advance_result.get("counts"),
                "content_fit_status": (
                    (content_fit or {}).get("status") if content_fit else "not_requested"
                ),
                "content_fit_enqueued": (
                    (content_fit or {}).get("enqueued_count") if content_fit else 0
                ),
                "content_fit_ai_analysis": (
                    (content_fit or {}).get("ai_analysis")
                    if content_fit
                    else {
                        "state": "not_requested",
                        "reason": "not_requested",
                        "provider_calls_allowed": False,
                    }
                ),
                "viltrox_fit_score_changed_ids": changed_ids,
                "viltrox_fit_score_untouched": not changed_ids,
                "query_plan_source": payload.get("query_plan_source"),
            },
        },
    )
    return {
        "status": pipeline_status,
        "session_id": int(session_id),
        "query": query,
        "query_plan_source": payload.get("query_plan_source"),
        "content_fit": content_fit,
        "field_topup": field_topup,
        "recall": {
            "method": recall.result.get("method"),
            "returned_count": len(recall.result.get("items") or []),
            "diagnostics": recall.result.get("diagnostics"),
            "local_qualification": recall.result.get("local_qualification"),
            "search_session": recall.session,
        },
        "new_discovery": new_discovery,
        "advance": advance_result,
        "provider_calls_performed": True,
        "write_db": True,
        "writes": [
            "vkpi_kol_search_sessions",
            "vkpi_kol_search_session_items",
            "vkpi_kol_pool",
            "vkpi_kol_url_deep_crawl_runs",
        ],
        "viltrox_fit_score_changed_ids": changed_ids,
        "viltrox_fit_score_untouched": not changed_ids,
    }
