"""backend/app/api/routers/vkpi_kol_pool_search.py

行为不变抽取:vkpi_kol_pool.py 的「智能搜索 / 搜索会话 / 召回 / URL 深爬」端点簇。
本模块自带一个无 prefix 的 APIRouter;主 router(prefix=/api/admin/vkpi)include 它,
路径逐字不变。函数体逐字搬运,与原文件行为一致。

红线:零触 viltrox_fit_score;此簇只做编排 / 会话 / 召回。
"""
from __future__ import annotations

from fastapi import APIRouter, Body, Depends, HTTPException, Query
from fastapi.concurrency import run_in_threadpool

from app.api.dependencies.perms import require_tab
import app.domains.kol.profile_recall as kol_profile_recall
import app.domains.kol.search_sessions as kol_search_sessions
import app.domains.kol.smart_query_planner as kol_smart_query_planner
import app.domains.kol.url_deep_crawl as kol_url_deep_crawl
from app.domains.kol import profile_discovery as kol_profile_discovery
from app.domains.projects import workflow as project_workflow
from app.domains.projects import cost_estimate as project_cost_estimate
from app.domains.projects import outreach as project_outreach

from app.api.routers.vkpi_kol_pool_helpers import (
    _attach_smart_recall_session,
    _attach_smart_url_session,
    _int_or_none,
    _looks_like_url,
    _smart_query_type,
)

router = APIRouter(tags=["vkpi-kol-pool"])


@router.post("/kol-search-sessions")
def create_kol_search_session(
    body: dict = Body(default_factory=dict),
    staff=Depends(require_tab("vkpi", "read")),
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
    del staff
    try:
        return kol_search_sessions.list_sessions(limit=limit, status=status)
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@router.get("/kol-search-history")
def list_kol_search_history(
    limit: int = Query(default=20, ge=1, le=50),
    status: str = Query(default=""),
    query_type: str = Query(default=""),
    item_limit: int = Query(default=5, ge=0, le=10),
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
        )
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@router.get("/kol-search-sessions/{session_id}")
def get_kol_search_session(
    session_id: int,
    staff=Depends(require_tab("vkpi", "read")),
) -> dict:
    """Return one KOL search session with its candidate items."""
    del staff
    try:
        return kol_search_sessions.get_session(int(session_id))
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
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@router.post("/kol-search-sessions/{session_id}/create-project-draft")
def create_project_draft_from_kol_search_session(
    session_id: int,
    body: dict = Body(default_factory=dict),
    staff=Depends(require_tab("vkpi", "write")),
) -> dict:
    """R2:从已批准的搜索会话一键建项目草案(discovery)+ 挂选中 KOL。

    选人缺省取会话 approved_kol_ids(R1);body 可带 product_positioning/target_persona/
    project_name/product_sku/product_name/platform/kol_pool_ids 覆盖。占用冲突降级为 warning。
    """
    try:
        return project_workflow.create_project_draft_from_session(
            int(session_id),
            body or {},
            staff=staff,
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
    try:
        session = kol_search_sessions.get_session(int(session_id))
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    raw_ids = body.get("kol_pool_ids") if isinstance(body, dict) else None
    if not isinstance(raw_ids, list) or not raw_ids:
        raw_ids = session.get("approved_kol_ids") or []
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
    """R4:为该会话候选(缺省 approved_kol_ids)生成合作话术 + SOW 草案。

    走 llm_gateway(预算闸 + 代理);仅草案——绝不外发、不承诺价格、零触 viltrox_fit_score。
    """
    try:
        session = kol_search_sessions.get_session(int(session_id))
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    raw_ids = body.get("kol_pool_ids") if isinstance(body, dict) else None
    if not isinstance(raw_ids, list) or not raw_ids:
        raw_ids = session.get("approved_kol_ids") or []
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
    return project_outreach.generate_outreach(
        raw_ids,
        brief=merged_brief,
        product={"product_name": body.get("product_name") or ""},
        staff=staff,
        preferred_provider=body.get("llm_provider"),
    )


@router.post("/kol-search-sessions/{session_id}/items/{item_id}/profile-crawl")
def execute_kol_search_session_item_profile_crawl(
    session_id: int,
    item_id: int,
    body: dict = Body(default_factory=dict),
    staff=Depends(require_tab("vkpi", "write")),
) -> dict:
    """Plan or execute safe profile crawl for a discovery session item."""
    del staff
    try:
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
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@router.post("/kol-search-sessions/{session_id}/advance")
def advance_kol_search_session_items(
    session_id: int,
    body: dict = Body(default_factory=dict),
    staff=Depends(require_tab("vkpi", "write")),
) -> dict:
    """Plan or execute ordered profile crawl for discovery items in one session."""
    del staff
    try:
        return kol_profile_discovery.advance_search_session_items(
            session_id=int(session_id),
            body=body or {},
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@router.post("/kol-search-sessions/{session_id}/advance-job")
def enqueue_kol_search_session_advance(
    session_id: int,
    body: dict = Body(default_factory=dict),
    staff=Depends(require_tab("vkpi", "write")),
) -> dict:
    """Queue ordered profile crawl for session items; worker executes it."""
    try:
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
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@router.post("/kol-search-sessions/{session_id}/advance-job/cancel")
def cancel_kol_search_session_advance(
    session_id: int,
    body: dict = Body(default_factory=dict),
    staff=Depends(require_tab("vkpi", "write")),
) -> dict:
    """Block queued session-advance jobs; running provider work is left alone."""
    try:
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
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@router.post("/kol-smart-search")
async def smart_kol_search(
    body: dict = Body(...),
    staff=Depends(require_tab("vkpi", "read")),
) -> dict:
    """Unified smart input endpoint.

    Auto-dispatches pasted URLs to the URL workflow and plain text to profile
    recall. It records orchestration state in search sessions only and never
    touches vkpi_kol_pool scoring fields.
    """
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
            crawl_body = {
                **body,
                "url": query_text,
                "create_session": False,
            }
            result = kol_url_deep_crawl.dry_run_url_deep_crawl(crawl_body)
            result = _attach_smart_url_session(body=body, result=result, query_text=query_text, staff=staff)
            return {
                "status": "ready",
                "mode": "url",
                "query_type": _smart_query_type(branch="url", result=result),
                "branch": "kol_url_deep_crawl",
                "result": result,
                "search_session": result.get("search_session"),
                "provider_calls": bool(result.get("provider_calls") or result.get("llm_calls_performed")),
                "viltrox_fit_score_untouched": result.get("viltrox_fit_score_untouched"),
            }

        recall_query = str(body.get("query_text") or query_text).strip()
        # 问题5 性能:plan_text_query(同步 LLM,冷启可达 15s)+ recall(同步 embedding+Qdrant)
        # 此前直接在事件循环线程跑,阻塞同进程其他请求。卸到线程池,首屏不再被冷启 LLM 卡死。
        llm_query_plan = await run_in_threadpool(kol_smart_query_planner.plan_text_query, recall_query, body=body, staff=staff)
        effective_query = str(llm_query_plan.get("search_query") or recall_query).strip()
        result = await run_in_threadpool(
            kol_profile_recall.recall_kol_profiles,
            query_text=effective_query,
            product_sku=str(body.get("product_sku") or ""),
            candidate_limit=int(body.get("candidate_limit") or 100),
            limit=int(body.get("limit") or 30),
            creator_quota=int(body.get("creator_quota") or llm_query_plan.get("creator_quota") or 15),
            reviewer_quota=int(body.get("reviewer_quota") or llm_query_plan.get("reviewer_quota") or 15),
            ratio_policy=str(body.get("ratio_policy") or "soft"),
            mixed_policy=str(body.get("mixed_policy") or "dominant"),
            dedupe=bool(body.get("dedupe", True)),
            vector_weight=float(body.get("vector_weight") if body.get("vector_weight") is not None else 0.85),
            type_weight=float(body.get("type_weight") if body.get("type_weight") is not None else 0.15),
            type_boost_enabled=bool(body.get("type_boost_enabled", True)),
            exclude_chinese=bool(body.get("exclude_chinese", True)),
            product_focus=llm_query_plan.get("product_focus"),
            target_persona=str(llm_query_plan.get("target_persona") or ""),
        )
        result["llm_query_plan"] = llm_query_plan
        result["original_query_text"] = recall_query
        result["effective_query_text"] = effective_query
        result = _attach_smart_recall_session(body=body, result=result, query_text=recall_query, staff=staff)
        discovery_payload: dict | None = None
        include_new_discovery = bool(body.get("include_new_discovery") or body.get("include_discovery"))
        execute_new_discovery = bool(body.get("execute_new_discovery"))
        if include_new_discovery:
            discovery_limit = int(body.get("new_discovery_limit") or body.get("discovery_limit") or 15)
            discovery_platforms = body.get("new_discovery_platforms") or body.get("discovery_platforms")
            platform_hint = str(body.get("platform") or "")
            if execute_new_discovery:
                discovery_payload = await kol_profile_discovery.discover_new_creators(
                    query_text=effective_query,
                    platforms=discovery_platforms,  # 2026-07-02:仅认用户显式选择;缺省交 _platforms 三平台兜底(不再被 LLM 规划锁两平台)
                    platform_hint=platform_hint,
                    market=str(body.get("market") or body.get("country") or llm_query_plan.get("market") or ""),
                    limit=discovery_limit,
                    per_platform_limit=int(body.get("new_discovery_per_platform_limit") or discovery_limit),
                )
                search_session = result.get("search_session") if isinstance(result.get("search_session"), dict) else {}
                session_id = _int_or_none(search_session.get("session_id") or search_session.get("id"))
                if session_id:
                    result["new_discovery_session"] = kol_search_sessions.attach_new_discovery_result(
                        int(session_id),
                        discovery_payload,
                    )
            else:
                discovery_payload = kol_profile_discovery.discovery_plan(
                    query_text=effective_query,
                    platforms=discovery_platforms,  # 2026-07-02:仅认用户显式选择;缺省交 _platforms 三平台兜底(不再被 LLM 规划锁两平台)
                    platform_hint=platform_hint,
                    limit=discovery_limit,
                )
        return {
            "status": "ready",
            "mode": "text",
            "query_type": "text_recall",
            "branch": "kol_recall",
            "result": result,
            "search_session": result.get("search_session"),
            "provider_calls": True,
            "provider_note": "text search uses LLM query planning plus OpenAI embedding recall; costs are recorded in ledgers",
            "llm_query_plan": llm_query_plan,
            "new_discovery": discovery_payload,
            "viltrox_fit_score_untouched": True,
            "new_discovery_status": (discovery_payload or {}).get("status") if discovery_payload else "not_requested",
        }
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


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
    queue_pipeline_raw = body.get("queue_pipeline", True)
    queue_pipeline = str(queue_pipeline_raw).strip().lower() not in {"0", "false", "no", "off", "sync"}

    try:
        if queue_pipeline:
            # P0-1 命门(100 人并发):请求侧不再同步跑 LLM planner(冷启~15s,会打爆 threadpool/provider)。
            # raw query 入队 → worker 的 execute_smart_search_profile_advance_pipeline 跑 planner+recall+advance。
            queued = kol_profile_discovery.enqueue_smart_search_profile_advance(
                query_text=query_text,
                body={**body, "original_query_text": query_text},
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

        # 非队列(显式 sync 模式,低频/调试):此路本就同步,保留请求内 planner+recall。
        llm_query_plan = await run_in_threadpool(kol_smart_query_planner.plan_text_query, query_text, body=body, staff=staff)
        effective_query = str(llm_query_plan.get("search_query") or query_text).strip()
        recall_result = kol_profile_recall.recall_kol_profiles(
            query_text=effective_query,
            product_sku=str(body.get("product_sku") or ""),
            candidate_limit=int(body.get("candidate_limit") or 100),
            limit=int(body.get("limit") or 30),
            creator_quota=int(body.get("creator_quota") or llm_query_plan.get("creator_quota") or 15),
            reviewer_quota=int(body.get("reviewer_quota") or llm_query_plan.get("reviewer_quota") or 15),
            ratio_policy=str(body.get("ratio_policy") or "soft"),
            mixed_policy=str(body.get("mixed_policy") or "dominant"),
            dedupe=bool(body.get("dedupe", True)),
            vector_weight=float(body.get("vector_weight") if body.get("vector_weight") is not None else 0.85),
            type_weight=float(body.get("type_weight") if body.get("type_weight") is not None else 0.15),
            type_boost_enabled=bool(body.get("type_boost_enabled", True)),
            exclude_chinese=bool(body.get("exclude_chinese", True)),
            product_focus=llm_query_plan.get("product_focus"),
            target_persona=str(llm_query_plan.get("target_persona") or ""),
        )
        recall_result["llm_query_plan"] = llm_query_plan
        recall_result["original_query_text"] = query_text
        recall_result["effective_query_text"] = effective_query
        recall_result = _attach_smart_recall_session(
            body={**body, "create_session": True, "source": body.get("source") or "kol_smart_search_profile_advance"},
            result=recall_result,
            query_text=query_text,
            staff=staff,
        )
        search_session = recall_result.get("search_session") if isinstance(recall_result.get("search_session"), dict) else {}
        session_id = _int_or_none(search_session.get("session_id") or search_session.get("id"))
        if not session_id:
            raise RuntimeError("smart search session was not created")

        include_new_discovery = bool(body.get("include_new_discovery", True))
        new_discovery: dict | None = None
        if include_new_discovery:
            discovery_limit = int(body.get("new_discovery_limit") or body.get("discovery_limit") or 15)
            new_discovery = await kol_profile_discovery.discover_new_creators(
                query_text=effective_query,
                platforms=body.get("new_discovery_platforms") or body.get("discovery_platforms") or llm_query_plan.get("platforms"),
                platform_hint=str(body.get("platform") or ""),
                market=str(body.get("market") or body.get("country") or llm_query_plan.get("market") or ""),
                limit=discovery_limit,
                per_platform_limit=int(body.get("new_discovery_per_platform_limit") or discovery_limit),
            )
            recall_result["new_discovery_session"] = kol_search_sessions.attach_new_discovery_result(
                int(session_id),
                new_discovery,
            )

        advance_job = kol_profile_discovery.enqueue_search_session_advance(
            session_id=int(session_id),
            body={
                "limit": int(body.get("advance_limit") or body.get("profile_advance_limit") or 15),
                "max_posts": int(body.get("max_posts") or 12),
                "mode": str(body.get("advance_mode") or body.get("mode") or "account_deep"),
                "representative_video_limit": body.get("representative_video_limit"),
                "item_types": body.get("item_types") or ["new_creator", "existing_kol", "recall_candidate"],
                "include_completed": bool(body.get("include_completed")),
            },
            staff=staff,
        )
        return {
            "status": "queued" if advance_job.get("status") in {"queued", "already_queued"} else advance_job.get("status"),
            "mode": "text",
            "query_type": "text_recall",
            "branch": "kol_recall_profile_advance",
            "query": query_text,
            "result": recall_result,
            "search_session": recall_result.get("search_session"),
            "new_discovery": new_discovery,
            "advance_job": advance_job,
            "provider_calls": True,
            "provider_note": "text recall/new discovery may call provider APIs; profile advancement is queued on apify_jobs",
            "write_db": True,
            "writes": ["vkpi_kol_search_sessions", "vkpi_kol_search_session_items", "apify_jobs"],
            "viltrox_fit_score_changed_ids": [],
            "viltrox_fit_score_untouched": True,
        }
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@router.get("/kol-recall")
def recall_kol_profiles(
    query_text: str = Query(default=""),
    product_sku: str = Query(default=""),
    candidate_limit: int = Query(default=50, ge=1, le=500),
    limit: int = Query(default=10, ge=1, le=50),
    creator_quota: int = Query(default=7, ge=0, le=50),
    reviewer_quota: int = Query(default=3, ge=0, le=50),
    ratio_policy: str = Query(default="soft"),
    mixed_policy: str = Query(default="dominant"),
    dedupe: bool = Query(default=True),
    vector_weight: float = Query(default=0.85, ge=0, le=1),
    type_weight: float = Query(default=0.15, ge=0, le=1),
    type_boost_enabled: bool = Query(default=True),
    exclude_chinese: bool = Query(default=True),
    session_id: int | None = Query(default=None, ge=1),
    create_session: bool = Query(default=False),
    staff=Depends(require_tab("vkpi", "read")),
) -> dict:
    """Vector recall endpoint for KOL Index; does not affect KOL Pool ranking."""
    try:
        result = kol_profile_recall.recall_kol_profiles(
            query_text=query_text,
            product_sku=product_sku,
            candidate_limit=candidate_limit,
            limit=limit,
            creator_quota=creator_quota,
            reviewer_quota=reviewer_quota,
            ratio_policy=ratio_policy,
            mixed_policy=mixed_policy,
            dedupe=dedupe,
            vector_weight=vector_weight,
            type_weight=type_weight,
            type_boost_enabled=type_boost_enabled,
            exclude_chinese=exclude_chinese,
        )
        session = kol_search_sessions.ensure_session_for_result(
            session_id=session_id,
            create=bool(create_session),
            query_text=query_text or product_sku,
            query_type="text_recall",
            source="kol_recall",
            input_payload={
                "query_text": query_text,
                "product_sku": product_sku,
                "candidate_limit": candidate_limit,
                "limit": limit,
                "creator_quota": creator_quota,
                "reviewer_quota": reviewer_quota,
            },
            staff=staff,
        )
        if session:
            result["search_session"] = kol_search_sessions.attach_recall_result(int(session["id"]), result)
        return result
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@router.post("/kol-url-deep-crawl")
def dry_run_kol_url_deep_crawl(
    body: dict = Body(...),
    staff=Depends(require_tab("vkpi", "read")),
) -> dict:
    """Classify a pasted URL and optionally execute the resolved URL workflow."""
    try:
        result = kol_url_deep_crawl.dry_run_url_deep_crawl(body)
        session_id = body.get("session_id")
        try:
            session_id_int = int(session_id) if session_id else None
        except (TypeError, ValueError):
            raise ValueError("session_id must be an integer") from None
        create_session = bool(body.get("create_session"))
        session = kol_search_sessions.ensure_session_for_result(
            session_id=session_id_int,
            create=create_session,
            query_text=str(body.get("url") or ""),
            query_type="url_video" if result.get("url_type") == "video" else "url_profile" if result.get("url_type") == "profile" else "unknown",
            source="kol_url_deep_crawl",
            input_payload={key: value for key, value in body.items() if key != "api_token"},
            staff=staff,
        )
        if session:
            result["search_session"] = kol_search_sessions.attach_url_result(int(session["id"]), result)
        return result
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
