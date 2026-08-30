"""KOL smart search, search-session, recall, and URL-crawl routes."""
from __future__ import annotations

from fastapi import Body, Depends, HTTPException, Query
from fastapi.concurrency import run_in_threadpool
from app.core.logging import get_logger
from app.api.dependencies.perms import require_tab
from app.domains.audit.decorator import audit_action
import app.domains.kol.profile_recall as kol_profile_recall
import app.domains.kol.profile_recall_qualification as kol_profile_recall_qualification
import app.domains.kol.profile_recall_response as kol_profile_recall_response
import app.domains.kol.search_auto_relax as kol_search_auto_relax
import app.domains.kol.search_sessions as kol_search_sessions
import app.domains.kol.search_sessions_online as kol_search_sessions_online
import app.domains.kol.smart_query_planner as kol_smart_query_planner
import app.domains.kol.url_deep_crawl as kol_url_deep_crawl
from app.domains.kol import profile_discovery as kol_profile_discovery
from app.domains.kol import targeted_search_runtime as kol_targeted_search_runtime
from app.domains.projects import workflow as project_workflow
from app.domains.projects import cost_estimate as project_cost_estimate
from app.domains.projects import outreach as project_outreach
from app.services.projects.creator_lifecycle_adapters import (
    DEFAULT_RECOMMENDATION_FEEDBACK_SINK,
    DEFAULT_SEARCH_SESSION_DRAFT_PORT,
)

from app.api.routers.vkpi_kol_pool_helpers import (
    _attach_smart_recall_session,
    _int_or_none,
    _looks_like_url,
    _smart_query_type,
)
from app.api.routers.vkpi_kol_pool_search_responses import (
    _body_bool,
    _pending_enrichment_state,
    _service_unavailable,
    _text_response_status,
    _url_response_status,
)
from app.api.routers.vkpi_kol_pool_smart_search_helpers import (
    smart_local_recall_kwargs,
    smart_url_search_response,
)
from app.api.routers.vkpi_kol_pool_search_scope import (
    _approved_session_kol_ids, _owned_search_session_or_http,
    _prepare_video_resolver_session_item, _reused_video_session_lineage,
)
from app.api.routers.vkpi_kol_pool_url_crawl_orchestration import run_url_deep_crawl
from app.api.routers.vkpi_kol_pool_recall_route import (
    recall_kol_profiles,
    router as kol_recall_router,
)
from app.api.routers.vkpi_kol_pool_search_team_status_route import router

logger = get_logger(__name__)

def _run_url_deep_crawl(
    body: dict,
    *,
    staff: dict,
    default_defer_profile: bool,
    default_create_session: bool,
    default_source: str,
) -> dict:
    del default_defer_profile  # provider-capable HTTP paths are always durable
    return run_url_deep_crawl(
        body,
        staff=staff,
        default_create_session=default_create_session,
        default_source=default_source,
        url_deep_crawl=kol_url_deep_crawl,
        search_sessions=kol_search_sessions,
        body_bool=_body_bool,
        int_or_none=_int_or_none,
        reused_video_session_lineage=_reused_video_session_lineage,
        prepare_video_resolver_session_item=_prepare_video_resolver_session_item,
        pending_enrichment_state=_pending_enrichment_state,
        url_response_status=_url_response_status,
    )


@router.post("/kol-search-sessions")
def create_kol_search_session(
    body: dict = Body(default_factory=dict),
    staff=Depends(require_tab("vkpi", "write")),
) -> dict:
    """Create a unified KOL search session; orchestration state only."""
    try:
        return kol_search_sessions.create_session(
            query_text=str(body.get("query_text") or body.get("query") or ""),
            query_type=str(body.get("query_type") or "unknown"),
            source=str(body.get("source") or "smart_kol_input"),
            input_payload=body.get("input_payload") if isinstance(body.get("input_payload"), dict) else body,
            status=str(body.get("status") or "planned"),
            staff=staff,
        )
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@router.get("/kol-search-sessions")
def list_kol_search_sessions(
    limit: int = Query(default=20, ge=1, le=100),
    status: str = Query(default=""),
    staff=Depends(require_tab("vkpi", "read")),
) -> dict:
    """List recent unified KOL search sessions."""
    try:
        return kol_search_sessions.list_sessions(
            limit=limit,
            status=status,
            staff=staff,
            scope_to_staff=True,
        )
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@router.get("/kol-search-history")
def list_kol_search_history(
    limit: int = Query(default=20, ge=1, le=50),
    status: str = Query(default=""),
    query_type: str = Query(default=""),
    item_limit: int = Query(default=5, ge=0, le=10),
    archived: bool = Query(default=False),
    staff=Depends(require_tab("vkpi", "read")),
) -> dict:
    """Return compact smart-search history with item previews and status counts.

    每个人的记录不能串:按当前登录员工 created_by 作用域过滤(service 内做)。
    """
    try:
        return kol_search_sessions.list_history(
            limit=limit,
            status=status,
            query_type=query_type,
            item_limit=item_limit,
            staff=staff,
            archived=archived,
        )
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@router.delete("/kol-search-history")
@audit_action(action_type="kol_search_history_clear", target_type="kol_search_history")
def archive_kol_search_history(
    staff=Depends(require_tab("vkpi", "write")),
) -> dict:
    """Soft-archive the current staff member's terminal search history."""
    try:
        return kol_search_sessions.archive_history_sessions(staff=staff)
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@router.delete("/kol-search-history/{session_id}")
@audit_action(action_type="kol_search_history_archive", target_type="kol_search_session")
def archive_kol_search_history_session(
    session_id: int,
    staff=Depends(require_tab("vkpi", "write")),
) -> dict:
    """Soft-archive one terminal search session owned by the current staff member."""
    try:
        return kol_search_sessions.archive_history_session(int(session_id), staff=staff)
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@router.post("/kol-search-history/{session_id}/restore")
@audit_action(action_type="kol_search_history_restore", target_type="kol_search_session")
def restore_kol_search_history_session(
    session_id: int,
    staff=Depends(require_tab("vkpi", "write")),
) -> dict:
    """Restore one archived search session owned by the current staff member."""
    try:
        return kol_search_sessions.restore_history_session(int(session_id), staff=staff)
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@router.get("/kol-search-sessions/{session_id}")
def get_kol_search_session(
    session_id: int,
    staff=Depends(require_tab("vkpi", "read")),
) -> dict:
    """Return one KOL search session with its candidate items."""
    try:
        return kol_search_sessions.get_session(int(session_id), staff=staff, scope_to_staff=True)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@router.post("/kol-search-sessions/{session_id}/approve")
def approve_kol_search_session(
    session_id: int,
    body: dict = Body(default_factory=dict),
    staff=Depends(require_tab("vkpi", "write")),
) -> dict:
    """R1:人审锁定该会话要推进合作的候选 KOL(写 approved_kol_ids;R2 据此建项目草案)。

    body: {"kol_pool_ids": [<int>, ...]}。只接受本会话候选项里的真实 kol_pool_id(交集校验)。
    """
    raw_ids = body.get("kol_pool_ids") if isinstance(body, dict) else None
    if not isinstance(raw_ids, list):
        raise HTTPException(status_code=400, detail="kol_pool_ids must be a list")
    try:
        return kol_search_sessions.approve_session(
            int(session_id),
            kol_pool_ids=raw_ids,
            staff=staff,
        )
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@router.post("/kol-search-sessions/{session_id}/create-project-draft")
def create_project_draft_from_kol_search_session(
    session_id: int,
    body: dict = Body(default_factory=dict),
    staff=Depends(require_tab("vkpi", "write")),
) -> dict:
    """R2:从已批准的搜索会话一键建项目草案(discovery)+ 挂选中 KOL。

    选人只取会话 approved_kol_ids(R1);body 只提供项目/产品 brief。占用冲突降级为 warning。
    """
    try:
        kol_search_sessions.require_session_owner(int(session_id), staff=staff)
        return project_workflow.create_project_draft_from_session(
            int(session_id),
            body or {},
            staff=staff,
            search_session_port=DEFAULT_SEARCH_SESSION_DRAFT_PORT,
            feedback_sink=DEFAULT_RECOMMENDATION_FEEDBACK_SINK,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@router.post("/kol-search-sessions/{session_id}/cost-estimate")
def estimate_kol_search_session_cost(
    session_id: int,
    body: dict = Body(default_factory=dict),
    staff=Depends(require_tab("vkpi", "read")),
) -> dict:
    """R3:估算该会话候选(缺省取 approved_kol_ids;body.kol_pool_ids 覆盖)的合作预算 + 风险。

    只读估算:费率档 × 平台 → 预算区间;风险只读展示信号,零触 viltrox_fit_score。
    """
    session = _owned_search_session_or_http(int(session_id), staff)
    raw_ids = _approved_session_kol_ids(
        session,
        body.get("kol_pool_ids") if isinstance(body, dict) else None,
    )
    posts = body.get("posts_per_creator") if isinstance(body, dict) else None
    try:
        ppc = int(posts) if posts is not None else 1
    except (TypeError, ValueError):
        ppc = 1
    return project_cost_estimate.estimate_cost_for_kols(raw_ids, staff=staff, posts_per_creator=ppc)


@router.post("/kol-search-sessions/{session_id}/generate-outreach")
def generate_kol_search_session_outreach(
    session_id: int,
    body: dict = Body(default_factory=dict),
    staff=Depends(require_tab("vkpi", "write")),
) -> dict:
    """R4:只为该会话服务端 approved_kol_ids 生成合作话术 + SOW 草案。

    走 llm_gateway(预算闸 + 代理);仅草案——绝不外发、不承诺价格、零触 viltrox_fit_score。
    """
    session = _owned_search_session_or_http(int(session_id), staff)
    # The server-owned approval snapshot is the only outreach audience.  A
    # request body must not silently narrow or replace the reviewed set.
    raw_ids = _approved_session_kol_ids(session, None)
    # brief:body 优先 → 会话内可得 plan → query_text 兜底(与 R2 草案同口径)。
    brief_in = body.get("brief") if isinstance(body.get("brief"), dict) else {}
    input_payload = session.get("input_payload") if isinstance(session.get("input_payload"), dict) else {}
    result_summary = session.get("result_summary") if isinstance(session.get("result_summary"), dict) else {}
    plan: dict = {}
    for src in (result_summary.get("llm_query_plan"), input_payload.get("llm_query_plan"), input_payload):
        if isinstance(src, dict) and (src.get("product_positioning") or src.get("target_persona")):
            plan = src
            break
    query_text = session.get("query_text") or ""
    merged_brief = {
        "query_text": query_text,
        "product_positioning": brief_in.get("product_positioning") or body.get("product_positioning") or plan.get("product_positioning") or "",
        "target_persona": brief_in.get("target_persona") or body.get("target_persona") or plan.get("target_persona") or query_text,
        "search_session_id": int(session_id),
    }
    try:
        return project_outreach.generate_outreach(
            raw_ids,
            brief=merged_brief,
            product={"product_name": body.get("product_name") or ""},
            staff=staff,
            preferred_provider=body.get("llm_provider"),
        )
    except Exception as exc:  # noqa: BLE001 - provider/domain failures must not become 500
        logger.warning("kol search outreach generation failed session_id=%s", session_id, exc_info=True)
        raise _service_unavailable("outreach_generation_unavailable", "generate_outreach") from exc


@router.post("/kol-search-sessions/{session_id}/items/{item_id}/profile-crawl")
def execute_kol_search_session_item_profile_crawl(
    session_id: int,
    item_id: int,
    body: dict = Body(default_factory=dict),
    staff=Depends(require_tab("vkpi", "write")),
) -> dict:
    """Plan or execute safe profile crawl for a discovery session item."""
    try:
        kol_search_sessions.require_session_owner(int(session_id), staff=staff)
        _owned_search_session_or_http(int(session_id), staff)
        if _body_bool(body, "execute"):
            queued = kol_profile_discovery.enqueue_search_session_advance(
                session_id=int(session_id),
                body={
                    **(body or {}),
                    "item_ids": [int(item_id)],
                    "limit": 1,
                    "mode": str(body.get("mode") or "account_deep"),
                },
                staff=staff,
            )
            is_pending = queued.get("status") in {"queued", "already_queued"}
            return {
                "status": queued.get("status"),
                "execute": True,
                "deferred_to_queue": is_pending,
                "session_id": int(session_id),
                "item_id": int(item_id),
                "advance_job": queued,
                "enrichment": _pending_enrichment_state() if is_pending else None,
                "provider_calls_performed": False,
                "viltrox_fit_score_untouched": True,
            }
        return kol_profile_discovery.execute_profile_crawl_for_session_item(
            session_id=int(session_id),
            item_id=int(item_id),
            body=body or {},
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise _service_unavailable("profile_crawl_unavailable", "profile_crawl") from exc


@router.post("/kol-search-sessions/{session_id}/advance")
def advance_kol_search_session_items(
    session_id: int,
    body: dict = Body(default_factory=dict),
    staff=Depends(require_tab("vkpi", "write")),
) -> dict:
    """Plan or execute ordered profile crawl for discovery items in one session."""
    try:
        kol_search_sessions.require_session_owner(int(session_id), staff=staff)
        _owned_search_session_or_http(int(session_id), staff)
        if _body_bool(body, "execute"):
            queued = kol_profile_discovery.enqueue_search_session_advance(
                session_id=int(session_id),
                body=body or {},
                staff=staff,
            )
            is_pending = queued.get("status") in {"queued", "already_queued"}
            return {
                **queued,
                "execute": True,
                "deferred_to_queue": is_pending,
                "enrichment": _pending_enrichment_state() if is_pending else None,
            }
        return kol_profile_discovery.advance_search_session_items(
            session_id=int(session_id),
            body=body or {},
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise _service_unavailable("profile_advance_unavailable", "profile_advance") from exc


@router.post("/kol-search-sessions/{session_id}/advance-job")
def enqueue_kol_search_session_advance(
    session_id: int,
    body: dict = Body(default_factory=dict),
    staff=Depends(require_tab("vkpi", "write")),
) -> dict:
    """Queue ordered profile crawl for session items; worker executes it."""
    try:
        kol_search_sessions.require_session_owner(int(session_id), staff=staff)
        _owned_search_session_or_http(int(session_id), staff)
        return kol_profile_discovery.enqueue_search_session_advance(
            session_id=int(session_id),
            body=body or {},
            staff=staff,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise _service_unavailable("profile_advance_queue_unavailable", "profile_advance_queue") from exc


@router.post("/kol-search-sessions/{session_id}/advance-job/cancel")
def cancel_kol_search_session_advance(
    session_id: int,
    body: dict = Body(default_factory=dict),
    staff=Depends(require_tab("vkpi", "write")),
) -> dict:
    """Block queued session-advance jobs; running provider work is left alone."""
    try:
        kol_search_sessions.require_session_owner(int(session_id), staff=staff)
        _owned_search_session_or_http(int(session_id), staff)
        return kol_profile_discovery.cancel_search_session_advance(
            session_id=int(session_id),
            body=body or {},
            staff=staff,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise _service_unavailable("profile_advance_cancel_unavailable", "profile_advance_cancel") from exc


async def _smart_search_session_body(body: dict, recall_query: str, staff: dict) -> dict:
    """Persist the visible session before any local recall work."""
    initial_session: dict | None = None
    if _body_bool(body, "create_session", default=True):
        initial_session = await run_in_threadpool(
            kol_search_sessions.ensure_session_for_result,
            session_id=_int_or_none(body.get("session_id")),
            create=True,
            query_text=recall_query,
            query_type="text_recall",
            source=str(body.get("source") or "kol_smart_search"),
            input_payload={key: value for key, value in body.items() if key != "api_token"},
            staff=staff,
        )
    initial_session_id = _int_or_none((initial_session or {}).get("id"))
    return {
        **body,
        **(
            {"session_id": initial_session_id, "create_session": False}
            if initial_session_id
            else {}
        ),
    }


def _smart_clarification_response(*, body: dict, plan: dict, recall_query: str, staff: dict) -> dict:
    empty_result = {
        "method": "product_catalog_guard",
        "items": [],
        "buckets": {"creator": [], "reviewer": []},
        "diagnostics": {"candidate_count": 0, "returned_count": 0},
        "llm_query_plan": plan,
        "original_query_text": recall_query,
        "effective_query_text": "",
    }
    empty_result = _attach_smart_recall_session(
        body=body,
        result=empty_result,
        query_text=recall_query,
        staff=staff,
    )
    return {
        "status": "needs_clarification",
        "mode": "text",
        "query_type": "text_recall",
        "branch": "product_catalog_guard",
        "result": empty_result,
        "search_session": empty_result.get("search_session"),
        "provider_calls": False,
        "provider_note": "explicit product did not match the catalog; no LLM or discovery provider was called",
        "llm_query_plan": plan,
        "new_discovery": None,
        "viltrox_fit_score_untouched": True,
        "new_discovery_status": "not_requested",
    }


async def _smart_local_recall(
    *, body: dict, plan: dict, recall_query: str, session_body: dict,
    explicit_market: str | None, explicit_platforms: object, staff: dict,
) -> tuple[dict, str]:
    effective_query = str(plan.get("search_query") or recall_query).strip()
    explicit_query_platforms = kol_profile_recall.explicit_platforms_from_query(recall_query)
    # Auto-relax remains fail-safe off until estimate and silent-auto-filter
    # symmetry are independently repaired and reviewed.
    auto_body = dict(body)
    auto_body.setdefault("auto_relax", False)
    auto_body.setdefault(kol_search_auto_relax.BODY_AUTO_FILTERS_KEY, False)
    recall_filters, auto_relax_ledger = await run_in_threadpool(
        kol_search_auto_relax.run_auto_relax,
        auto_body,
        plan,
        query_platforms=explicit_query_platforms,
        target=_int_or_none(body.get("result_limit")) or kol_search_auto_relax.DEFAULT_TARGET,
    )
    targeted_context = kol_targeted_search_runtime.prepare_local_search(
        plan=plan,
        body=body,
        recall_filters=recall_filters,
        market=explicit_market,
        platforms=explicit_platforms,
    )
    recall_filters = targeted_context["recall_filters"]
    resolved_product = targeted_context["resolved_product"]
    recall_kwargs = smart_local_recall_kwargs(
        body=body,
        plan=plan,
        context=targeted_context,
        recall_filters=recall_filters,
        effective_query=effective_query,
        recall_query=recall_query,
        resolved_product=resolved_product,
    )
    result = await run_in_threadpool(
        kol_targeted_search_runtime.execute_local_search,
        context=targeted_context,
        recall_kwargs=recall_kwargs,
        recall=kol_profile_recall.recall_kol_profiles,
    )
    result = kol_profile_discovery.filter_recall_result_platforms(
        result,
        recall_filters.get("platforms"),
    )
    result = kol_profile_discovery.filter_recall_result_market(result, explicit_market)
    result.setdefault("query", {})["explicit_operator_platforms"] = explicit_query_platforms
    result = kol_profile_recall_qualification.project_smart_local_result(result)
    result["llm_query_plan"] = plan
    result["original_query_text"] = recall_query
    result["effective_query_text"] = effective_query
    result = _attach_smart_recall_session(
        body=session_body,
        result=result,
        query_text=recall_query,
        staff=staff,
    )
    if str(body.get("response_projection") or "").strip() == "smart_local_compact_v1":
        result = kol_profile_recall_response.compact_smart_local_api_result(result)
    result["auto_relax"] = auto_relax_ledger
    return result, effective_query


def _smart_discovery_payload(
    *, body: dict, recall_query: str, effective_query: str, explicit_platforms: object, staff: dict,
) -> dict | None:
    discovery_payload: dict | None = None
    include_new_discovery = bool(
        body.get("include_new_discovery") or body.get("include_discovery")
    )
    execute_new_discovery = bool(body.get("execute_new_discovery"))
    if include_new_discovery:
        online_spec = body.get("online_qualification_spec")
        strict_online_30 = bool(
            isinstance(online_spec, dict)
            and str(online_spec.get("version") or "") == "online_net_new_30_v1"
            and str(online_spec.get("target_count") or "") == "30"
        )
        discovery_limit = int(body.get("new_discovery_limit") or body.get("discovery_limit") or 15)
        discovery_platforms = (
            body.get("new_discovery_platforms")
            or body.get("discovery_platforms")
            or body.get("platforms")
            or explicit_platforms
        )
        platform_hint = str(body.get("platform") or "")
        if execute_new_discovery:
            queued = kol_profile_discovery.enqueue_smart_search_profile_advance(
                query_text=recall_query,
                body={
                    **body,
                    "original_query_text": recall_query,
                    "include_new_discovery": True,
                    "new_discovery_limit": discovery_limit,
                    "new_discovery_platforms": discovery_platforms,
                    "platform": platform_hint,
                },
                staff=staff,
            )
            discovery_payload = {
                "status": queued.get("status") or "queued",
                "deferred_to_queue": True,
                "job_id": queued.get("job_id") or (queued.get("job") or {}).get("id"),
                "progressive": True,
                "provider_calls_performed": False,
                **(
                    {
                        "online_qualification": kol_search_sessions_online.queued_online_qualification(
                            queued.get("status") or "queued"
                        )
                    }
                    if strict_online_30
                    else {}
                ),
            }
        else:
            discovery_payload = kol_profile_discovery.discovery_plan(
                query_text=effective_query,
                platforms=discovery_platforms,
                platform_hint=platform_hint,
                limit=discovery_limit,
            )
    return discovery_payload


async def _smart_text_search(body: dict, query_text: str, staff: dict) -> dict:
    recall_query = str(body.get("query_text") or query_text).strip()
    explicit_market = kol_profile_discovery.resolve_market_constraint(
        recall_query,
        body.get("market") or body.get("country"),
    )
    query_platforms = kol_profile_discovery.explicit_platforms_from_query(recall_query)
    explicit_platforms = (
        query_platforms
        or body.get("platforms")
        or body.get("new_discovery_platforms")
        or body.get("discovery_platforms")
        or body.get("platform")
    )
    session_body = await _smart_search_session_body(body, recall_query, staff)
    plan = await run_in_threadpool(
        kol_smart_query_planner.plan_text_query_provider_free,
        recall_query,
        body=body,
    )
    if str(plan.get("status") or "") == "needs_clarification":
        return _smart_clarification_response(
            body=session_body,
            plan=plan,
            recall_query=recall_query,
            staff=staff,
        )
    result, effective_query = await _smart_local_recall(
        body=body,
        plan=plan,
        recall_query=recall_query,
        session_body=session_body,
        explicit_market=explicit_market,
        explicit_platforms=explicit_platforms,
        staff=staff,
    )
    discovery_payload = _smart_discovery_payload(
        body=body,
        recall_query=recall_query,
        effective_query=effective_query,
        explicit_platforms=explicit_platforms,
        staff=staff,
    )
    return {
        "status": _text_response_status(result, discovery_payload),
        "mode": "text",
        "query_type": "text_recall",
        "branch": "kol_recall",
        "result": result,
        "search_session": result.get("search_session"),
        "provider_calls": False,
        "provider_note": "initial recall is provider-free; full LLM planning and vector/provider stages run in the queued worker",
        "llm_query_plan": plan,
        "new_discovery": discovery_payload,
        "viltrox_fit_score_untouched": True,
        "new_discovery_status": (discovery_payload or {}).get("status") if discovery_payload else "not_requested",
    }


@router.post("/kol-smart-search")
async def smart_kol_search(body: dict = Body(...), staff=Depends(require_tab("vkpi", "write"))) -> dict:
    """Dispatch a URL or provider-free first-round text search."""
    query_text = str(body.get("input") or body.get("query") or body.get("query_text") or body.get("url") or "").strip()
    if not query_text:
        raise HTTPException(status_code=400, detail="input is required")
    mode = str(body.get("mode") or "auto").strip().lower()
    if mode not in {"auto", "url", "recall", "text"}:
        raise HTTPException(status_code=400, detail="mode must be auto, url, recall, or text")
    branch = "url" if mode == "url" or (mode == "auto" and _looks_like_url(query_text)) else "recall"
    if mode == "text":
        branch = "recall"
    try:
        if branch == "url":
            return smart_url_search_response(
                body, query_text, staff,
                run_url_deep_crawl=_run_url_deep_crawl,
                url_response_status=_url_response_status,
                smart_query_type=_smart_query_type,
            )
        return await _smart_text_search(body, query_text, staff)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise _service_unavailable("kol_search_unavailable", "kol_smart_search") from exc
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception(
            "kol_smart_search failed branch=%s input=%s",
            branch,
            query_text[:160],
        )
        raise HTTPException(
            status_code=503,
            detail="KOL 搜索服务暂时不可用，当前任务未被标记为完成；请稍后重试。",
        ) from exc


@router.post("/kol-smart-search/profile-advance-job")
async def smart_kol_search_profile_advance_job(
    body: dict = Body(...),
    staff=Depends(require_tab("vkpi", "write")),
) -> dict:
    """Run text recall/new discovery and queue ordered profile advancement.

    This is the write-mode companion to /kol-smart-search. It keeps plain
    recall read-oriented, while giving the unified input one backend call for
    "find candidates, persist the session, then advance profiles in the queue".
    """

    query_text = str(body.get("input") or body.get("query") or body.get("query_text") or "").strip()
    if not query_text:
        raise HTTPException(status_code=400, detail="input is required")
    if _looks_like_url(query_text):
        raise HTTPException(status_code=400, detail="profile-advance-job accepts text needs only; use kol-url-deep-crawl for URLs")
    # Legacy ``queue_pipeline=false|sync`` is intentionally ignored.  A normal
    # web request must never execute planner/discovery providers inline.
    queue_pipeline = True

    try:
        if queue_pipeline:
            normalized_market = kol_profile_discovery.resolve_market_constraint(
                query_text,
                body.get("market") or body.get("country"),
            )
            # P0-1 命门(100 人并发):请求侧不再同步跑 LLM planner(冷启~15s,会打爆 threadpool/provider)。
            # raw query 入队 → worker 的 execute_smart_search_profile_advance_pipeline 跑 planner+recall+advance。
            queued = kol_profile_discovery.enqueue_smart_search_profile_advance(
                query_text=query_text,
                body={
                    **body,
                    **({"market": normalized_market} if normalized_market else {}),
                    "original_query_text": query_text,
                },
                staff=staff,
            )
            return {
                "status": queued.get("status"),
                "mode": "text",
                "query_type": "text_recall",
                "branch": "kol_recall_profile_advance_pipeline",
                "query": query_text,
                "original_query": query_text,
                "llm_query_plan": None,
                "search_session": queued.get("search_session"),
                "advance_job": queued,
                "provider_calls": False,
                "provider_note": "planner deferred to worker; request makes no synchronous LLM call",
                "write_db": True,
                "writes": queued.get("writes") or ["vkpi_kol_search_sessions", "apify_jobs"],
                "viltrox_fit_score_changed_ids": [],
                "viltrox_fit_score_untouched": True,
            }

    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise _service_unavailable("profile_advance_unavailable", "profile_advance") from exc
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("kol_smart_search_profile_advance failed input=%s", query_text[:160])
        raise HTTPException(
            status_code=503,
            detail="KOL 深析队列暂时不可用，当前任务未被标记为完成；请稍后重试。",
        ) from exc


@router.post("/kol-url-deep-crawl")
def dry_run_kol_url_deep_crawl(
    body: dict = Body(...),
    staff=Depends(require_tab("vkpi", "write")),
) -> dict:
    """Classify a pasted URL and optionally execute the resolved URL workflow."""
    try:
        return _run_url_deep_crawl(
            body,
            staff=staff,
            default_defer_profile=True,
            default_create_session=False,
            default_source="kol_url_deep_crawl",
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise _service_unavailable("url_deep_crawl_unavailable", "url_deep_crawl") from exc
    except Exception as exc:
        logger.exception(
            "kol_url_deep_crawl request failed url=%s execute=%s deferred=%s",
            str(body.get("url") or "")[:160],
            bool(body.get("execute")),
            bool(body.get("defer_to_queue")),
        )
        raise HTTPException(
            status_code=503,
            detail="账号抓取服务暂时不可用，任务未被标记为完成；请稍后重试。",
        ) from exc


router.include_router(kol_recall_router)
