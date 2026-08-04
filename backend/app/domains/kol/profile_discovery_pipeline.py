"""Queued smart-search profile advance pipeline."""
from __future__ import annotations

from typing import Any

from app.domains.kol import profile_recall, search_sessions
from app.domains.kol.discovery_filters import _annotate_new_priority, _int, _text
from app.domains.kol.profile_discovery_candidates import filter_recall_result_platforms
from app.domains.kol.profile_discovery_provider import discover_new_creators
from app.domains.kol.profile_discovery_session import (
    _profile_advance_pipeline_status,
    advance_search_session_items,
)
from app.domains.kol.search_progress_contract import completion_contract


async def execute_smart_search_profile_advance_pipeline(
    *,
    session_id: int,
    payload: dict[str, Any],
) -> dict[str, Any]:
    """Execute a queued text recall/new-discovery/profile-advance pipeline."""

    query = _text(payload.get("query_text") or payload.get("input") or payload.get("query"))
    if not query:
        raise ValueError("smart profile advance payload missing query_text")
    operator_query = query
    # P0-1:LLM planner 改在 worker 跑(请求侧已去同步 LLM,见 vkpi_kol_pool smart-search 端点)。
    # payload 未带 plan 时,worker 侧补 planner:拿英文 search_query(治中文 query 捞中文圈)+ persona。
    # 失效则退原 query(管线既有 rule_v0 英文兜底)。本管线本就同步阻塞跑 recall,planner 同步调用一致。
    if not payload.get("product_focus") and not _text(payload.get("target_persona")) and not payload.get("_worker_planned"):
        try:
            from app.domains.kol import smart_query_planner as _sqp
            _plan = _sqp.plan_text_query(query, body=payload, staff=None)
            if _text(_plan.get("status")) == "needs_clarification":
                clarification_contract = completion_contract(
                    base_count=0,
                    total=0,
                    terminal_count=0,
                    ready_count=0,
                )
                search_sessions.update_session_result_summary(
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
                            **clarification_contract,
                        },
                        **clarification_contract,
                        "llm_query_plan": _plan,
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
                    "llm_query_plan": _plan,
                    "recall": {"method": "product_catalog_guard", "returned_count": 0, "diagnostics": {}},
                    "new_discovery": None,
                    "advance": {"status": "not_started", "selected": 0, "counts": {}},
                    "provider_calls_performed": False,
                    "write_db": True,
                    "writes": ["vkpi_kol_search_sessions"],
                    "viltrox_fit_score_changed_ids": [],
                    "viltrox_fit_score_untouched": True,
                }
            _eff = _text(_plan.get("search_query"))
            if _eff:
                query = _eff
            payload["product_focus"] = _plan.get("product_focus")
            payload["target_persona"] = _text(_plan.get("target_persona"))
            for _k in ("creator_quota", "reviewer_quota", "new_discovery_limit"):
                if payload.get(_k) is None and _plan.get(_k) is not None:
                    payload[_k] = _plan.get(_k)
            payload["_worker_planned"] = True
            payload["query_plan_source"] = "llm_plan"
        except Exception:
            # 诚实标注:planner 抛错 → 退 rule_v0 英文兜底(行为不变),仅记录走了哪条路。
            payload["query_plan_source"] = "rule_v0_fallback"
    recall_filters = dict(payload.get("filters") or {}) if isinstance(payload.get("filters"), dict) else {}
    explicit_platforms = (
        payload.get("platforms")
        or payload.get("platform")
        or payload.get("new_discovery_platforms")
        or payload.get("discovery_platforms")
    )
    if not explicit_platforms:
        explicit_platforms = profile_recall.explicit_platforms_from_query(operator_query)
    if explicit_platforms and not recall_filters.get("platforms"):
        recall_filters["platforms"] = explicit_platforms
    recall_result = profile_recall.recall_kol_profiles(
        query_text=query,
        product_sku=_text(payload.get("product_sku")),
        candidate_limit=max(1, min(_int(payload.get("candidate_limit"), 100), 500)),
        limit=max(
            1,
            min(
                _int(payload.get("result_limit") or payload.get("candidate_count") or payload.get("limit"), 30),
                50,
            ),
        ),
        creator_quota=max(0, min(_int(payload.get("creator_quota"), 15), 50)),
        reviewer_quota=max(0, min(_int(payload.get("reviewer_quota"), 15), 50)),
        ratio_policy=_text(payload.get("ratio_policy") or "soft"),
        mixed_policy=_text(payload.get("mixed_policy") or "dominant"),
        dedupe=bool(payload.get("dedupe", True)),
        vector_weight=float(payload.get("vector_weight") if payload.get("vector_weight") is not None else 0.7),
        type_weight=float(payload.get("type_weight") if payload.get("type_weight") is not None else 0.3),
        type_boost_enabled=bool(payload.get("type_boost_enabled", True)),
        exclude_chinese=bool(payload.get("exclude_chinese", True)),
        product_focus=payload.get("product_focus"),
        target_persona=_text(payload.get("target_persona")),
        filters=recall_filters,
        search_strategy=_text(payload.get("search_strategy") or "balanced"),
        bucket_policy=payload.get("bucket_policy") if isinstance(payload.get("bucket_policy"), dict) else None,
        allow_backfill=bool(payload.get("allow_backfill", True)),
        operator_query_text=operator_query,
    )
    recall_result = filter_recall_result_platforms(
        recall_result,
        recall_filters.get("platforms"),
    )
    recall_items = recall_result.get("items") if isinstance(recall_result.get("items"), list) else []
    recall_buckets = recall_result.get("buckets") if isinstance(recall_result.get("buckets"), dict) else {}
    recall_count = len(recall_items) or sum(
        len(items) for items in recall_buckets.values() if isinstance(items, list)
    )
    advance_limit = max(1, min(_int(payload.get("advance_limit") or payload.get("profile_advance_limit"), 15), 15))
    recall_total = min(recall_count, advance_limit)
    recall_contract = completion_contract(
        base_count=recall_count,
        total=recall_total,
        terminal_count=0,
        ready_count=0,
        active_tasks=recall_total,
        requested_tasks_terminal=False,
    )
    recall_session = search_sessions.attach_recall_result(
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
                **recall_contract,
            },
        },
    )
    base_count = recall_count
    new_discovery: dict[str, Any] | None = None
    if bool(payload.get("include_new_discovery", True)):
        # persona 检索词原料:payload 有 product_focus/target_persona(来自 llm_query_plan);
        # verticals/ideal_creator_types/avoid_types 不在 payload → 用 product_sku 实时兜底取(只读 KB,零 LLM)。
        _persona_kb: dict[str, Any] = {}
        _sku = _text(payload.get("product_sku"))
        if _sku:
            try:
                from app.domains.costs import product_persona as _product_persona_kb
                _persona_kb = _product_persona_kb.get_product_persona(_sku) or {}
            except Exception:
                _persona_kb = {}
        new_discovery = await discover_new_creators(
            query_text=query,
            platforms=payload.get("new_discovery_platforms") or payload.get("discovery_platforms"),
            platform_hint=_text(payload.get("platform")),
            market=_text(payload.get("market") or payload.get("country")),
            limit=max(1, min(_int(payload.get("new_discovery_limit"), 15), 50)),
            per_platform_limit=max(1, min(_int(payload.get("new_discovery_per_platform_limit"), 15), 50)),
            search_query_en=query,  # pipeline 入参 query 已是 effective_query(planner 英文 search_query;失效退 rule_v0 英文兜底)
            product_focus=payload.get("product_focus"),
            ideal_creator_types=_persona_kb.get("ideal_creator_types_json"),
            verticals=_persona_kb.get("verticals_json"),
            avoid_types=_persona_kb.get("avoid_types_json"),
            target_persona=_text(payload.get("target_persona")),
        )
        # 收口路①-4:新人优先展示信号(新发现/低合作/成长期加权,饱和大号降位)。纯展示透出,
        # 绝不写 viltrox_fit_score / 不改 rule_v0;注解后再 attach(库内召回的 display_rank_score 已在 recall 侧产出)。
        new_discovery = _annotate_new_priority(new_discovery)
        discovery_count = len(new_discovery.get("existing_matches") or []) + len(new_discovery.get("new_creators") or [])
        if discovery_count <= 0:
            discovery_count = len(new_discovery.get("items") or [])
        base_count += discovery_count
        discovery_total = min(base_count, advance_limit)
        discovery_contract = completion_contract(
            base_count=base_count,
            total=discovery_total,
            terminal_count=0,
            ready_count=0,
            active_tasks=discovery_total,
            requested_tasks_terminal=False,
        )
        search_sessions.attach_new_discovery_result(
            int(session_id),
            {
                **new_discovery,
                "_session_pipeline_running": True,
                "_session_progress": {
                    "base": base_count,
                    "total": discovery_total,
                    "profile_ready": 0,
                    "profile_failed": 0,
                    "complete_ready": 0,
                    "complete_partial": 0,
                    **discovery_contract,
                },
            },
        )

    advance_result = advance_search_session_items(
        session_id=int(session_id),
        body={
            **payload,
            "execute": True,
            "limit": advance_limit,
            "_pipeline_running": True,
            "max_posts": max(1, min(_int(payload.get("max_posts"), 12), 12)),
            "mode": _text(payload.get("advance_mode") or payload.get("mode") or "account_deep"),
            "item_types": payload.get("item_types") or ["new_creator", "existing_kol", "recall_candidate"],
            "include_completed": bool(payload.get("include_completed")),
        },
    )
    changed_ids = [
        _int(value)
        for value in (advance_result.get("viltrox_fit_score_changed_ids") or [])
        if _int(value) > 0
    ]
    # 收口路①-2:搜索拿到候选(库内召回 + 发现 + advance 补全)后,对**头部 N 个有视频证据的
    # 库内候选**异步入队内容契合深析(「思考中」段)。控量(top N + 有证据 + cache 复用 + 去重)。
    # 纯编排入队 + exposure_potential 展示计算,零烧 LLM、零写 fit。入队失败不阻断 pipeline。
    content_fit: dict[str, Any] | None = None
    if bool(payload.get("include_content_fit", True)):
        try:
            from app.domains.kol import content_fit_enqueue

            content_fit = content_fit_enqueue.enqueue_content_fit_for_session(
                session_id=int(session_id),
                product_sku=_text(payload.get("product_sku")),
                top_n=max(1, min(_int(payload.get("content_fit_top_n"), content_fit_enqueue.DEFAULT_TOP_N), content_fit_enqueue.MAX_TOP_N)),
                triggered_by_user_id=_int(payload.get("triggered_by_user_id")) or None,
            )
        except Exception as exc:
            content_fit = {"status": "error", "reason": "content_fit_enqueue_failed"}
    # Lane D(用户裁令「搜索时顺带懒抓」):对搜索召回的、**缺视频**的库内候选,顺带抓少数 account_deep,
    # 成本摊到未来、按需、自动优先真被搜到的人(不一次性全量烧 $660)。入队失败不阻断 pipeline。
    video_backfill: dict[str, Any] | None = None
    if bool(payload.get("include_lazy_video_backfill", True)):
        try:
            from app.domains.kol import video_backfill_enqueue

            video_backfill = video_backfill_enqueue.enqueue_lazy_video_backfill_for_session(
                session_id=int(session_id),
                top_n=max(1, min(_int(payload.get("lazy_video_backfill_top_n"), video_backfill_enqueue.DEFAULT_TOP_N), video_backfill_enqueue.MAX_TOP_N)),
                staff=None,
            )
        except Exception as exc:
            video_backfill = {"status": "error", "reason": "video_backfill_enqueue_failed"}
    pipeline_status = _profile_advance_pipeline_status(recall_result, new_discovery, advance_result)
    final_status = "partial" if changed_ids else pipeline_status
    profile_ready = 0
    profile_failed = 0
    for item in advance_result.get("items") or []:
        result = item.get("result") if isinstance(item, dict) and isinstance(item.get("result"), dict) else {}
        profile_status = _text(result.get("profile_status") or item.get("status")).lower()
        if profile_status in {"ready", "already_analyzed"}:
            profile_ready += 1
        elif "failed" in profile_status or profile_status == "error":
            profile_failed += 1
    profile_failed = max(
        profile_failed,
        _int((advance_result.get("counts") or {}).get("failed"))
        + _int((advance_result.get("counts") or {}).get("errors")),
    )
    selected_count = int(advance_result.get("selected") or 0)
    profile_completed = len(advance_result.get("items") or [])
    final_contract = completion_contract(
        base_count=base_count,
        total=selected_count,
        terminal_count=profile_completed,
        ready_count=profile_ready,
        profile_failed=profile_failed,
        active_tasks=max(0, selected_count - profile_completed),
        # Downstream video/comments/audience jobs are registered and rebuilt
        # after this profile loop.  A 15/15 profile batch must not masquerade
        # as terminal or as the strict full-analysis state in this gap.
        requested_tasks_terminal=False,
    )
    search_sessions.update_session_result_summary(
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
                "complete_ready": _int((advance_result.get("counts") or {}).get("ready")),
                "complete_partial": _int((advance_result.get("counts") or {}).get("partial")),
                **final_contract,
            },
            **final_contract,
            "smart_search_profile_advance_job": {
                "status": pipeline_status,
                "query_text": query,
                "recall_returned": len(recall_result.get("items") or []),
                "new_discovery_status": (new_discovery or {}).get("status") if new_discovery else "not_requested",
                "advance_status": advance_result.get("status"),
                "advance_counts": advance_result.get("counts"),
                # 内容契合入队状态(「思考中」桶进度):入队数 / 跳过原因,纯展示透出。
                "content_fit_status": (content_fit or {}).get("status") if content_fit else "not_requested",
                "content_fit_enqueued": (content_fit or {}).get("enqueued_count") if content_fit else 0,
                "content_fit_ai_analysis": (content_fit or {}).get("ai_analysis") if content_fit else {
                    "state": "not_requested",
                    "reason": "not_requested",
                    "provider_calls_allowed": False,
                },
                "viltrox_fit_score_changed_ids": changed_ids,
                "viltrox_fit_score_untouched": not changed_ids,
                # 诚实信号:本次走 LLM planner('llm_plan')还是 rule_v0 英文兜底('rule_v0_fallback');
                # 未尝试规划(已带 product_focus/persona)则为 None。前端据此如实告知用户。
                "query_plan_source": payload.get("query_plan_source"),
            }
        },
    )
    return {
        "status": pipeline_status,
        "session_id": int(session_id),
        "query": query,
        "query_plan_source": payload.get("query_plan_source"),
        "content_fit": content_fit,
        "recall": {
            "method": recall_result.get("method"),
            "returned_count": len(recall_result.get("items") or []),
            "diagnostics": recall_result.get("diagnostics"),
            "search_session": recall_session,
        },
        "new_discovery": new_discovery,
        "advance": advance_result,
        "provider_calls_performed": True,
        "write_db": True,
        "writes": ["vkpi_kol_search_sessions", "vkpi_kol_search_session_items", "vkpi_kol_pool", "vkpi_kol_url_deep_crawl_runs"],
        "viltrox_fit_score_changed_ids": changed_ids,
        "viltrox_fit_score_untouched": not changed_ids,
    }
