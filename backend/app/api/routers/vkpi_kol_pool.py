"""backend/app/api/routers/vkpi_kol_pool.py

R59: 独立 KOL Pool 路由 + 防火墙 + 审计装饰器集成示范.

这个文件是 R59 装饰器实战示范:
  - import 操作 → 防火墙 (require_budget) + 审计
  - link 操作 → 审计 (无防火墙,因为是内部数据修改)
  - list 操作 → 无装饰器 (read-only)

新增 endpoint:
  POST /api/admin/vkpi/kol-pool/import     # 一键导入 (防火墙 + 审计)
  GET  /api/admin/vkpi/kol-pool             # 列表
  GET  /api/admin/vkpi/kol-pool/{id}        # 详情
  POST /api/admin/vkpi/kol-pool/{id}/link   # 链接到 kols 主表 (审计)

注: 现有 vkpi_product_analysis.py 也有 import_items / list_pool 的暴露,
    本文件不替换那些,而是提供独立"KOL Pool 管理"入口,语义更清晰.
"""
from __future__ import annotations

import os
from urllib.parse import urlparse

from fastapi import APIRouter, Body, Depends, HTTPException, Query, Request
from fastapi.concurrency import run_in_threadpool

from app.api.dependencies.perms import require_tab
from app.domains.kol import competitor_detector as kol_competitor_detector
from app.domains.kol import account_dossier as kol_account_dossier
from app.domains.kol import account_dossier_extract as kol_account_dossier_extract
from app.domains.kol import eleven_dimensions
from app.domains.kol import intelligence_card as kol_intelligence_card
from app.domains.kol import history_match as kol_history_match
from app.domains.kol import llm_deep_analysis as kol_llm_deep_analysis
from app.domains.kol import pool as kol_pool
from app.domains.kol import profile_discovery as kol_profile_discovery
import app.domains.kol.profile_recall as kol_profile_recall
import app.domains.kol.search_sessions as kol_search_sessions
import app.domains.kol.smart_query_planner as kol_smart_query_planner
import app.domains.kol.url_deep_crawl as kol_url_deep_crawl
import app.domains.kol.video_analysis_enqueue as kol_video_analysis_enqueue
from app.domains.intelligence import gemini_single_kol_preflight
import app.domains.intelligence.ai_brief as ai_brief
import app.domains.evidence.summary as evidence_summary
from app.domains.projects import workflow as project_workflow
import app.domains.sync.refresh_tier as refresh_tier
import app.domains.tasks.enqueue as task_enqueue
import app.domains.tasks.queue_view as task_queue_view
from app.domains.audit.decorator import audit_action
from app.domains.access.firewall import firewall_check


router = APIRouter(prefix="/api/admin/vkpi", tags=["vkpi-kol-pool"])


# ─── Read endpoints (无装饰器) ──────────────────────


def _on_demand_refresh_enabled() -> bool:
    """Runtime provider gate for P1.X.C stale-while-revalidate.

    Search/detail endpoints may expose freshness state and record search
    interest by default, but they must not enqueue provider work unless an
    operator explicitly enables this gate in the runtime environment.
    """
    for name in ("VKPI_KOL_ON_DEMAND_REFRESH_ENABLED", "VKPI_ENABLE_KOL_ON_DEMAND_REFRESH"):
        value = os.getenv(name, "").strip().lower()
        if value in {"1", "true", "yes", "on"}:
            return True
    return False


def _int_or_none(value) -> int | None:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return None
    return parsed if parsed > 0 else None


_KNOWN_URL_DOMAINS = (
    "youtube.com", "youtu.be", "tiktok.com", "instagram.com", "facebook.com",
    "twitter.com", "x.com", "bilibili.com", "b23.tv", "douyin.com", "twitch.tv", "reddit.com",
)


def _looks_like_url(value: str) -> bool:
    """诊断 P1-2/3 分流CJK收口:真闸门。此前 urlparse netloc 含点即判 url,致含域名词的
    中文问句(如「找像youtube.com的博主」无空格、「推荐 example.com 的人」带空格)被静默
    吞进 URL 分支、语义意图丢失。现加三重校验:无空白 + host 纯 ASCII DNS-label(CJK/
    punycode-折叠的中文主机一律拒)+ 命中已知平台域或合法 TLD;否则回退 recall。"""
    text = str(value or "").strip()
    if not text:
        return False
    # 真 URL 不含空白——含空白的输入是问句,回退 recall
    if any(ch.isspace() for ch in text):
        return False
    candidate = text if "://" in text else f"https://{text}"
    parsed = urlparse(candidate)
    host = (parsed.hostname or "").lower()
    if not host or "." not in host:
        return False
    # host 必须是合法 DNS 名(纯 ASCII 字母数字/连字符/点)——CJK 主机拒判
    if not all(ch.isascii() and (ch.isalnum() or ch in ".-") for ch in host):
        return False
    if any(host == d or host.endswith("." + d) for d in _KNOWN_URL_DOMAINS):
        return True
    tld = host.rsplit(".", 1)[-1]
    return tld.isalpha() and len(tld) >= 2


def _smart_query_type(*, branch: str, result: dict | None = None) -> str:
    if branch == "url":
        url_type = str((result or {}).get("url_type") or "").strip()
        if url_type == "video":
            return "url_video"
        if url_type == "profile":
            return "url_profile"
        return "unknown"
    if branch == "recall":
        return "text_recall"
    return "unknown"


def _attach_smart_url_session(
    *,
    body: dict,
    result: dict,
    query_text: str,
    staff: dict,
) -> dict:
    session = kol_search_sessions.ensure_session_for_result(
        session_id=_int_or_none(body.get("session_id")),
        create=bool(body.get("create_session", True)),
        query_text=query_text,
        query_type=_smart_query_type(branch="url", result=result),
        source=str(body.get("source") or "kol_smart_search"),
        input_payload={key: value for key, value in body.items() if key != "api_token"},
        staff=staff,
    )
    if session:
        result["search_session"] = kol_search_sessions.attach_url_result(int(session["id"]), result)
    return result


def _attach_smart_recall_session(
    *,
    body: dict,
    result: dict,
    query_text: str,
    staff: dict,
) -> dict:
    session = kol_search_sessions.ensure_session_for_result(
        session_id=_int_or_none(body.get("session_id")),
        create=bool(body.get("create_session", True)),
        query_text=query_text,
        query_type="text_recall",
        source=str(body.get("source") or "kol_smart_search"),
        input_payload={key: value for key, value in body.items() if key != "api_token"},
        staff=staff,
    )
    if session:
        result["search_session"] = kol_search_sessions.attach_recall_result(int(session["id"]), result)
    return result


async def _maybe_enqueue_refresh(
    request: Request,
    kol_pool_id: int,
    *,
    staff: dict,
    enabled: bool,
    force: bool = False,
    reason: str = "stale_while_revalidate",
) -> dict:
    search_marker = refresh_tier.record_kol_search(int(kol_pool_id))
    freshness = refresh_tier.freshness_for_kol(int(kol_pool_id))
    provider_calls_enabled = _on_demand_refresh_enabled()
    if not enabled:
        return {
            "triggered": False,
            "reason": "not_requested",
            "freshness": freshness,
            "search_marker": search_marker,
            "provider_calls_enabled": provider_calls_enabled,
        }
    if not force and not freshness.get("needs_refresh"):
        return {
            "triggered": False,
            "reason": "fresh",
            "freshness": freshness,
            "search_marker": search_marker,
            "provider_calls_enabled": provider_calls_enabled,
        }
    if not provider_calls_enabled:
        return {
            "triggered": False,
            "reason": "on_demand_refresh_disabled",
            "message": "stale-while-revalidate is reporting freshness only; provider enqueue is disabled by runtime policy",
            "freshness": freshness,
            "search_marker": search_marker,
            "provider_calls_enabled": False,
        }
    queue = getattr(request.app.state, "job_queue", None)
    if queue is None:
        return {
            "triggered": False,
            "reason": "job_queue_unavailable",
            "freshness": freshness,
            "search_marker": search_marker,
            "provider_calls_enabled": True,
        }
    try:
        queued = await task_enqueue.enqueue_kol_pool_on_demand_refresh(
            queue,
            int(kol_pool_id),
            reason=reason,
            max_posts=1,
            staff=staff,
        )
    except ValueError as exc:
        return {
            "triggered": False,
            "reason": "not_enqueueable",
            "message": str(exc),
            "freshness": freshness,
            "search_marker": search_marker,
            "provider_calls_enabled": True,
        }
    return {
        "triggered": True,
        "reason": reason,
        "freshness": freshness,
        "search_marker": search_marker,
        "provider_calls_enabled": True,
        **queued,
    }


@router.get("/kol-pool")
async def list_pool(
    request: Request,
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    platform: str = Query(default=""),
    query: str = Query(default=""),
    country: str = Query(default=""),
    data_status: str = Query(default=""),
    sort_by: str = Query(default="fit"),
    enrichable: bool | None = Query(default=None),
    refresh_if_stale: bool = Query(default=False),
    staff=Depends(require_tab("vkpi", "read")),
) -> dict:
    """列出 KOL Pool"""
    result = kol_pool.list_pool(
        limit=limit,
        offset=offset,
        platform=platform,
        query=query,
        country=country,
        data_status=data_status,
        sort_by=sort_by,
        enrichable=enrichable,
    )
    refresh_state = None
    items = result.get("items") if isinstance(result, dict) else []
    if refresh_if_stale and query and isinstance(items, list) and items:
        first_id = int(items[0].get("id") or 0)
        if first_id:
            refresh_state = await _maybe_enqueue_refresh(
                request,
                first_id,
                staff=staff,
                enabled=True,
                reason="search_stale_while_revalidate",
            )
    if refresh_state:
        result["refresh"] = refresh_state
    return result


@router.get("/kol-pool/summary")
def get_pool_summary(
    staff=Depends(require_tab("vkpi", "read")),
) -> dict:
    """KOL Pool 资产池口径统计；不等于 Daily Top100 新候选。"""
    return kol_pool.summary()


@router.get("/kol-pool/suspect-inflation")
def get_suspect_inflation_review(
    limit: int = Query(default=200, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    staff=Depends(require_tab("vkpi", "read")),
) -> dict:
    """P0-3 疑似刷量/假粉复核清单;独立角标列,绝不参与 viltrox_fit_score。"""
    del staff
    return kol_pool.suspect_inflation_review_list(limit=limit, offset=offset)


@router.get("/kol-pool/workspace")
def get_pool_workspace(
    limit: int = Query(default=1200, ge=1, le=2000),
    offset: int = Query(default=0, ge=0),
    platform: str = Query(default=""),
    query: str = Query(default=""),
    country: str = Query(default=""),
    data_status: str = Query(default=""),
    sort_by: str = Query(default="fit"),
    enrichable: bool | None = Query(default=None),
    staff=Depends(require_tab("vkpi", "read")),
) -> dict:
    """One read-only KOL Pool workspace bundle for 100-user-friendly page boot."""
    del staff
    return kol_pool.workspace(
        limit=limit,
        offset=offset,
        platform=platform,
        query=query,
        country=country,
        data_status=data_status,
        sort_by=sort_by,
        enrichable=enrichable,
    )


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
                    platforms=discovery_platforms or llm_query_plan.get("platforms"),
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
                    platforms=discovery_platforms or llm_query_plan.get("platforms"),
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


@router.get("/kol-pool/available")
def list_available_for_project(
    project_id: int = Query(..., ge=1),
    query: str = Query(default=""),
    limit: int = Query(default=200, ge=1, le=500),
    scope: str = Query(default="favorites"),
    staff=Depends(require_tab("vkpi", "read")),
) -> dict:
    """KOL Pool candidates not yet assigned to the project.

    scope=favorites(默认)只返本人收藏子集;scope=all 显式逃生门返全池(诊断 P0-2 裁决)。
    """
    try:
        return project_workflow.list_available_project_kols(
            project_id,
            query=query,
            limit=limit,
            scope_mode=scope,
            staff=staff,
        )
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except Exception as exc:
        if exc.__class__.__name__ == "ScopeDenied":
            raise HTTPException(status_code=403, detail=str(exc) or "scope denied") from exc
        raise


@router.get("/kol-pool/competitors/dashboard")
def get_pool_competitor_dashboard(
    brand: str = Query(default=""),
    limit: int = Query(default=1200, ge=1, le=1200),
    source_type: str = Query(default="legacy_excel_p2d"),
    source: str = Query(default="auto", pattern="^(auto|computed)$"),
    staff=Depends(require_tab("vkpi", "read")),
) -> dict:
    """按 1012 历史池已有资料返回竞品风险概览；默认优先读已落库关系。"""
    return kol_competitor_detector.batch_evaluate_kol_pool(
        brand=brand,
        limit=limit,
        source_type=source_type,
        prefer_persisted=source == "auto",
    )


@router.get("/kol-pool/competitors/poach-targets")
def get_competitor_poach_targets(
    category: str = Query(default="", pattern="^(|lens|monitor|flash)$"),
    limit: int = Query(default=200, ge=1, le=1000),
    staff=Depends(require_tab("vkpi", "read")),
) -> dict:
    """可挖角名单:对副厂开放(无原厂深度绑定)的 KOL;纯读已落库竞品关系。"""
    del staff
    return kol_competitor_detector.list_poach_targets(category=category, limit=limit)


@router.get("/kol-pool/competitors/avoid-brands")
def get_competitor_avoid_brands(
    limit: int = Query(default=500, ge=1, le=2000),
    staff=Depends(require_tab("vkpi", "read")),
) -> dict:
    """近期合作友商/原厂避雷名单:risk_tier 在 caution/avoid 的 KOL×品牌关系;纯读。"""
    del staff
    return kol_competitor_detector.list_avoid_brands(limit=limit)


@router.post("/kol-pool/batch-enrich")
@audit_action(
    action_type="kol_pool_batch_enrich",
    target_type="kol_pool",
    detail_extractor=lambda result, kwargs: f"batch enriched {result.get('enriched', 0)} attempted {result.get('attempted', 0)}",
    metadata_extractor=lambda result, kwargs: {
        "attempted": result.get("attempted", 0) if isinstance(result, dict) else 0,
        "enriched": result.get("enriched", 0) if isinstance(result, dict) else 0,
        "complete": result.get("complete", 0) if isinstance(result, dict) else 0,
        "partial": len(result.get("partial", [])) if isinstance(result, dict) else 0,
        "errors": len(result.get("errors", [])) if isinstance(result, dict) else 0,
        "capped": result.get("capped", False) if isinstance(result, dict) else False,
    },
)
def batch_enrich_pool_items(
    body: dict = Body(default_factory=dict),
    staff=Depends(require_tab("vkpi", "write")),
) -> dict:
    """小批量真实补齐候选池数据；服务端强制最多 5 条。"""
    ids = body.get("ids") or []
    if ids and not isinstance(ids, list):
        raise HTTPException(status_code=400, detail="ids must be a list")
    return kol_pool.batch_enrich_items(
        ids=[int(value) for value in ids if str(value).strip().isdigit()] if ids else None,
        platform=str(body.get("platform") or ""),
        query=str(body.get("query") or ""),
        data_status=str(body.get("data_status") or "missing"),
        limit=max(1, min(int(body.get("limit") or 3), 5)),
        max_posts=max(1, min(int(body.get("max_posts") or 6), 24)),
        staff=staff,
    )


@router.post("/kol-pool/profile-deep-crawl/enqueue")
@audit_action(
    action_type="kol_profile_deep_crawl_enqueue",
    target_type="kol_pool",
    target_id_extractor=lambda result, kwargs: str((result or {}).get("job_id") or ""),
    detail_extractor=lambda result, kwargs: f"enqueue deep crawl status={(result or {}).get('status')}",
)
def enqueue_kol_profile_deep_crawl(
    body: dict = Body(default_factory=dict),
    staff=Depends(require_tab("vkpi", "write")),
) -> dict:
    """队列铁律:账号深爬入 apify_jobs(泳道可见),替代同步内爬。"""
    from app.domains.kol import url_deep_crawl as kol_url_deep_crawl

    try:
        return kol_url_deep_crawl.enqueue_profile_deep_crawl_job(
            str(body.get("url") or ""),
            kol_pool_id=body.get("kol_pool_id"),
            max_posts=int(body.get("max_posts") or 3),
            staff=staff,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/kol-pool/comments-collect/enqueue")
@audit_action(
    action_type="kol_pool_comments_collect_enqueue",
    target_type="kol_pool",
    target_id_extractor=lambda result, kwargs: str((result or {}).get("job_id") or ""),
    detail_extractor=lambda result, kwargs: f"enqueue comments collect status={(result or {}).get('status')}",
)
def enqueue_kol_pool_comments_collect(
    body: dict = Body(default_factory=dict),
    staff=Depends(require_tab("vkpi", "write")),
) -> dict:
    """评论采集入 apify_jobs(2026-06-12 裁令"评论的展示也要有";泳道「评论采集」可见)。"""
    from app.domains.comments import collector as comments_collector

    try:
        return comments_collector.enqueue_kol_pool_comments_job(
            int(body.get("kol_pool_id") or 0),
            evidence_ids=body.get("evidence_ids") or None,
            max_comments=body.get("max_comments"),
            staff=staff,
        )
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except (TypeError, ValueError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/kol-pool/{kol_pool_id}/video-comments")
def get_kol_pool_video_comments(
    kol_pool_id: int,
    evidence_id: int = Query(..., ge=1),
    limit: int = Query(default=100, ge=1, le=500),
    staff=Depends(require_tab("vkpi", "read")),
) -> dict:
    """读该 evidence 的已采评论(vkpi_comments,post_table=evidence;字段对齐 mapCommentRows)。"""
    del staff
    from app.domains.comments import collector as comments_collector

    try:
        return comments_collector.list_pool_video_comments(
            int(kol_pool_id), evidence_id=int(evidence_id), limit=int(limit)
        )
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post("/kol-pool/outreach-draft/enqueue")
@audit_action(
    action_type="kol_outreach_draft_enqueue",
    target_type="kol_pool",
    target_id_extractor=lambda result, kwargs: str((result or {}).get("job_id") or ""),
    detail_extractor=lambda result, kwargs: f"enqueue outreach draft status={(result or {}).get('status')}",
)
def enqueue_kol_outreach_draft(
    body: dict = Body(default_factory=dict),
    staff=Depends(require_tab("vkpi", "write")),
) -> dict:
    """联系草稿入队(2026-06-12 裁令:点联系给优化后的聊天方式;泳道「联系草稿」可见)。"""
    from app.domains.kol import outreach_draft as kol_outreach_draft

    try:
        return kol_outreach_draft.enqueue_outreach_draft_job(
            int(body.get("kol_pool_id") or 0),
            project_id=body.get("project_id"),
            staff=staff,
        )
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except (TypeError, ValueError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/kol-pool/outreach-optimize")
def optimize_kol_outreach(
    body: dict = Body(default_factory=dict),
    staff=Depends(require_tab("vkpi", "write")),
) -> dict:
    """AI 优化外联文案(同步小调用):给定 KOL 名 + 主推产品 + 当前主题/正文,LLM 把这封给海外创作者的
    合作邀约润色成更自然、更口语、更高回复率的英文。**只产文案、不发送**;失败诚实回退原文。
    返回 {ok, subject, body, model}。走 llm_gateway(预算闸 + 代理)。零触 viltrox_fit_score。"""
    import json as _json
    import re as _re

    from app.platform import llm_gateway

    subject = str(body.get("subject") or "").strip()
    draft = str(body.get("body") or "").strip()
    product = str(body.get("product") or "").strip()
    kol_name = str(body.get("kol_name") or "").strip()
    if not draft and not subject:
        return {"ok": False, "reason": "empty_draft", "subject": subject, "body": draft}

    prompt = (
        "你是 Viltrox(唯卓仕,海外相机镜头品牌)的 KOL 外联文案专家。把下面这封给【海外/英文圈创作者】的\n"
        "合作邀约润色成更自然、更口语、更高回复率的**英文**(别中式生硬英文)。要求:真诚、简短、具体\n"
        "(点出一个具体的合作点),不套路营销腔、不夸大、不编造数据。\n"
        f"对象 KOL:{kol_name or '(未提供)'};主推产品:{product or 'Viltrox 镜头'}。\n"
        f"当前主题:{subject or '(空)'}\n当前正文:\n{draft or '(空)'}\n\n"
        '只输出 JSON(不要多余文字):{"subject": "优化后主题", "body": "优化后正文(英文,保留换行)"}'
    )
    resp = llm_gateway.invoke(
        prompt,
        purpose="vkpi_kol_outreach_optimize",
        max_output_tokens=1200,
        cost_tag="vkpi_kol_outreach_optimize",
        staff=staff or {},
        metadata={"kol_pool_id": int(body.get("kol_pool_id") or 0)},
    )
    text = str(resp.get("text") or "").strip()
    out_subject, out_body = subject, draft  # 兜底原文
    try:
        cleaned = _re.sub(r"^```json\s*|```$", "", text, flags=_re.MULTILINE).strip()
        s, e = cleaned.find("{"), cleaned.rfind("}")
        if s != -1 and e != -1 and e > s:
            parsed = _json.loads(cleaned[s : e + 1])
            if isinstance(parsed, dict):
                out_subject = str(parsed.get("subject") or subject).strip()
                out_body = str(parsed.get("body") or draft).strip()
    except Exception:
        pass
    return {"ok": bool(text), "subject": out_subject, "body": out_body, "model": resp.get("model")}


@router.post("/kol-pool/{kol_pool_id}/contacts")
def add_kol_manual_contact(
    kol_pool_id: int,
    body: dict = Body(default_factory=dict),
    staff=Depends(require_tab("vkpi", "write")),
) -> dict:
    """人工保存 KOL 联系方式(ContactModal「保存联系方式」)。合规留痕:source='manual'、
    consent='manual_entry'、is_public_declared=FALSE、记操作人;写 vkpi_kol_pool_contacts 审计表 +
    other_contacts_json 展示快照(并集去重)。纯人工录入零外调。零触 viltrox_fit_score。"""
    from app.domains.kol import business_contact_extract

    try:
        return business_contact_extract.add_manual_contact(
            int(kol_pool_id),
            email=str(body.get("email") or ""),
            platform=str(body.get("platform") or ""),
            handle=str(body.get("handle") or ""),
            staff=staff or {},
        )
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=500, detail=f"save contact error: {exc}") from exc


@router.get("/kol-pool/{kol_pool_id}/cooperation")
def get_kol_cooperation(
    kol_pool_id: int,
    staff=Depends(require_tab("vkpi", "read")),
) -> dict:
    """读 KOL 当前合作状态 + 时间线(平台为基准)。"""
    del staff
    from app.domains.kol import cooperation

    return cooperation.get_cooperation(int(kol_pool_id))


@router.post("/kol-pool/{kol_pool_id}/cooperation")
def record_kol_cooperation(
    kol_pool_id: int,
    body: dict = Body(default_factory=dict),
    staff=Depends(require_tab("vkpi", "write")),
) -> dict:
    """KOL 合作动作(续约/加大投入/退出合作/评估/备注)→ 平台为基准的状态时间线。
    action ∈ renew|scale_up|exit|evaluate|note。零触 viltrox_fit_score。"""
    from app.domains.kol import cooperation

    try:
        return cooperation.record_action(
            int(kol_pool_id),
            str(body.get("action") or ""),
            note=str(body.get("note") or ""),
            staff=staff or {},
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.get("/kol-pool/{kol_pool_id}/outreach-draft")
def get_kol_outreach_draft(
    kol_pool_id: int,
    staff=Depends(require_tab("vkpi", "read")),
) -> dict:
    """读最新联系草稿(cache,kol_outreach_draft_v1);无则 state=missing。"""
    del staff
    from app.domains.kol import outreach_draft as kol_outreach_draft

    return kol_outreach_draft.get_outreach_draft(int(kol_pool_id))


@router.get("/kol-pool/favorites")
def list_kol_pool_favorites(
    limit: int = Query(default=2000, ge=1, le=5000),
    staff=Depends(require_tab("vkpi", "read")),
) -> dict:
    """本人收藏清单(staff 隔离),供 Pool 星标/My KOL 收藏集渲染。"""
    from app.domains.kol import pool_favorites

    try:
        return pool_favorites.list_favorites(staff=staff, limit=limit)
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc


@router.get("/kol-pool/needs-analysis")
def list_kol_pool_needs_analysis(
    limit: int = Query(default=50, ge=1, le=200),
    staff=Depends(require_tab("vkpi", "read")),
) -> dict:
    """库内有视频证据但还没 ready 深析的 KOL(供「待分析」列表 + 批量入队)。注册在 /{kol_pool_id} 之前避免被吞。"""
    del staff
    try:
        return kol_video_analysis_enqueue.list_kols_needing_video_analysis(limit=limit)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=500, detail=f"needs-analysis error: {exc}") from exc


@router.get("/kol-pool/{kol_pool_id}")
async def get_item(
    request: Request,
    kol_pool_id: int,
    refresh_if_stale: bool = Query(default=True),
    staff=Depends(require_tab("vkpi", "read")),
) -> dict:
    """获取单个 KOL Pool 项"""
    try:
        result = kol_pool.get_item(int(kol_pool_id))
        refresh_state = await _maybe_enqueue_refresh(
            request,
            int(kol_pool_id),
            staff=staff,
            enabled=bool(refresh_if_stale),
            reason="detail_stale_while_revalidate",
        )
        result["freshness"] = refresh_state.get("freshness")
        result["refresh"] = refresh_state
        return result
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.get("/kol-pool/{kol_pool_id}/detail-bundle")
def get_item_detail_bundle(
    kol_pool_id: int,
    # P9:此前 default=3/max=10 把账号详情抽屉钉死在"前 4 条";底层 evidence 早已物化全量,
    # 抬到 default=24/max=200,让单账号详情默认展示该账号(基本)全部视频,前端可按需再加载。
    # 这是 READ-ONLY 物化展示口径(便宜),不触发新的 Gemini 深析(那是另一条限量+预算闸的链)。
    video_limit: int = Query(default=24, ge=1, le=200),
    llm_limit: int = Query(default=20, ge=1, le=50),
    staff=Depends(require_tab("vkpi", "read")),
) -> dict:
    """Read-only detail drawer bundle; does not refresh providers or touch V6 Fit."""
    del staff
    try:
        return kol_pool.detail_bundle(int(kol_pool_id), video_limit=video_limit, llm_limit=llm_limit)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.get("/kol-pool/{kol_pool_id}/account-dossier")
def get_pool_item_account_dossier(
    kol_pool_id: int,
    video_limit: int = Query(default=50, ge=1, le=200),
    event_limit: int = Query(default=80, ge=1, le=300),
    deep_limit: int = Query(default=20, ge=1, le=50),
    staff=Depends(require_tab("vkpi", "read")),
) -> dict:
    """Read-only KOL account dossier; aggregates local state without providers."""
    del staff
    try:
        return kol_account_dossier.get_kol_account_dossier(
            int(kol_pool_id),
            video_limit=video_limit,
            event_limit=event_limit,
            deep_limit=deep_limit,
        )
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post("/kol-pool/{kol_pool_id}/account-dossier-extract-job")
def enqueue_pool_item_account_dossier_extract(
    kol_pool_id: int,
    body: dict = Body(default_factory=dict),
    staff=Depends(require_tab("vkpi", "write")),
) -> dict:
    """Queue local account dossier materialization into independent profile_llm results."""
    try:
        return kol_account_dossier_extract.enqueue_account_dossier_extract_job(
            int(kol_pool_id),
            body=body or {},
            staff=staff,
        )
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@router.post("/kol-pool/{kol_pool_id}/refresh")
async def refresh_pool_item(
    request: Request,
    kol_pool_id: int,
    body: dict = Body(default_factory=dict),
    staff=Depends(require_tab("vkpi", "write")),
) -> dict:
    """Queue a stale-while-revalidate KOL Pool refresh; does not block on providers."""
    try:
        kol_pool.get_item(int(kol_pool_id))
        return await _maybe_enqueue_refresh(
            request,
            int(kol_pool_id),
            staff=staff,
            enabled=True,
            force=bool(body.get("force")),
            reason=str(body.get("reason") or "manual_on_demand_refresh"),
        )
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post("/kol-pool/{kol_pool_id}/enqueue-video-analysis")
def enqueue_pool_item_video_analysis(
    kol_pool_id: int,
    body: dict = Body(default_factory=dict),
    staff=Depends(require_tab("vkpi", "write")),
) -> dict:
    """Queue one final_v1 video analysis job; independent from V6 Fit."""
    evidence_id = body.get("evidence_id")
    try:
        evidence_id_int = int(evidence_id)
    except (TypeError, ValueError) as exc:
        raise HTTPException(status_code=400, detail="evidence_id required") from exc
    try:
        return kol_video_analysis_enqueue.enqueue_final_v1_video_analysis(
            kol_pool_id=int(kol_pool_id),
            evidence_id=evidence_id_int,
            staff=staff,
        )
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.post("/kol-pool/enqueue-video-analysis-batch")
def enqueue_pool_video_analysis_batch(
    body: dict = Body(default_factory=dict),
    staff=Depends(require_tab("vkpi", "write")),
) -> dict:
    """Queue multiple final_v1 video analysis jobs; independent from V6 Fit."""
    raw_items = body.get("items")
    if not isinstance(raw_items, list) or not raw_items:
        raise HTTPException(status_code=400, detail="items required")
    try:
        return kol_video_analysis_enqueue.enqueue_final_v1_video_analysis_batch(
            items=raw_items,
            staff=staff,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.post("/kol-pool/{kol_pool_id}/enqueue-all-videos")
def enqueue_pool_all_videos(
    kol_pool_id: int,
    staff=Depends(require_tab("vkpi", "write")),
) -> dict:
    """「全视频跑」:该 KOL 全部视频证据各入队一条 final_v1;独立于 V6 Fit。"""
    try:
        return kol_video_analysis_enqueue.enqueue_all_kol_videos(
            kol_pool_id=int(kol_pool_id),
            staff=staff,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.get("/kol-pool/{kol_pool_id}/main-candidates")
def get_main_candidates(
    kol_pool_id: int,
    limit: int = Query(default=5, ge=1, le=20),
    staff=Depends(require_tab("vkpi", "read")),
) -> dict:
    """查找 KOL Pool 项可能对应的 kols 主表记录。"""
    try:
        return kol_pool.main_candidates(int(kol_pool_id), limit=limit)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.get("/kol-pool/{kol_pool_id}/competitors")
def get_pool_item_competitors(
    kol_pool_id: int,
    source: str = Query(default="auto", pattern="^(auto|computed)$"),
    staff=Depends(require_tab("vkpi", "read")),
) -> dict:
    """返回单个 KOL Pool 项与 6 个竞品的关系；默认优先读已落库关系。"""
    try:
        return kol_competitor_detector.evaluate_kol_competitors(
            int(kol_pool_id),
            prefer_persisted=source == "auto",
        )
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.get("/kol-pool/{kol_pool_id}/dimensions11")
def get_pool_item_dimensions11(
    kol_pool_id: int,
    require_persisted: bool = Query(default=False),
    staff=Depends(require_tab("vkpi", "read")),
) -> dict:
    """返回 KOL Pool 项的规则版 11 维画像；只读、不调 provider、不写库。"""
    try:
        if require_persisted:
            payload = eleven_dimensions.load_persisted_dimensions_11(int(kol_pool_id))
            if payload:
                return payload
            return {
                "kol_pool_id": int(kol_pool_id),
                "status": "missing",
                "reason": "dimensions_11_json_missing",
                "persisted": False,
                "provider_calls": False,
                "llm_calls": False,
                "write_db": False,
            }
        return eleven_dimensions.compose_dimensions_11(int(kol_pool_id))
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.get("/kol-pool/{kol_pool_id}/llm-deep-analysis")
def get_pool_item_llm_deep_analysis(
    kol_pool_id: int,
    limit: int = Query(default=20, ge=1, le=50),
    staff=Depends(require_tab("vkpi", "read")),
) -> dict:
    """Return independent LLM deep-analysis results; never touches V6 Fit."""
    del staff
    return kol_llm_deep_analysis.get_kol_llm_deep_analysis(int(kol_pool_id), limit=limit)


CONTENT_FIT_JOB_TYPE = "kol_content_fit_analysis"
CONTENT_FIT_DERIVE_METHOD = "content_fit_v1"


def _enqueue_content_fit_on_demand(
    kol_pool_id: int,
    product_sku: str | None,
    *,
    force: bool,
    staff: dict | None,
) -> dict:
    """T4:把内容契合深析从请求路径挪到后台队列(避开 60s 超时)。

    纯 DB 路径,**不**烧 LLM:已有 ready content_fit_v1 cache 且非 force → 直接返回缓存;
    否则 INSERT 一个 kol_content_fit_analysis job(worker 端 _process_kol_content_fit_analysis
    调 content_fit_analysis.analyze_content_fit 跑实际 LLM)→ 立即返回 {status:'queued', job_id}。
    已有同 KOL queued/running job → 返回 already_queued(去重)。红线:零触 viltrox_fit_score。
    """
    from app.db.connection import get_conn
    from app.domains.kol import content_fit_analysis as kol_content_fit

    kid = int(kol_pool_id)
    conn = get_conn()

    # ① 复用已就绪缓存(非 force):零烧 LLM、零入队。
    if not force:
        cached = kol_content_fit.get_content_fit(kid)
        if cached and str(cached.get("state") or "") not in ("missing", ""):
            return cached

    # ② 去重:同 KOL 已有 queued/running content_fit job → 不重复入队。
    existing = conn.execute(
        """
        SELECT id, status FROM apify_jobs
        WHERE job_type=?
          AND status IN ('queued', 'running', 'retrying')
          AND payload->>'kol_pool_id'=?
        ORDER BY created_at DESC, id DESC
        LIMIT 1
        """,
        (CONTENT_FIT_JOB_TYPE, str(kid)),
    ).fetchone()
    if existing:
        row = dict(existing)
        return {
            "status": "already_queued" if str(row.get("status")) == "queued" else "already_running",
            "state": "queued",
            "kol_pool_id": kid,
            "job_id": int(row.get("id")) if row.get("id") is not None else None,
            "job_type": CONTENT_FIT_JOB_TYPE,
            "viltrox_fit_score_untouched": True,
        }

    # ③ 入队:实际 LLM 深析由 worker 跑(off the request path)。
    triggered_by_user_id = (staff or {}).get("user_id") if isinstance(staff, dict) else None
    payload = {
        "kol_pool_id": kid,
        "target_type": "kol",
        "target_id": str(kid),
        "product_sku": str(product_sku or "") or None,
        "derive_method": CONTENT_FIT_DERIVE_METHOD,
        "source": "kol_pool_detail_on_demand",
        "trigger": "content_fit_on_demand",
        "force": bool(force),
        "triggered_by_user_id": triggered_by_user_id,
        "viltrox_fit_score_untouched": True,
        "query_text": f"content fit - kol_pool #{kid}",
    }
    inserted = conn.execute(
        """
        INSERT INTO apify_jobs (job_type, payload, status, created_at, updated_at)
        VALUES (?, ?::jsonb, 'queued', NOW(), NOW())
        RETURNING id
        """,
        (CONTENT_FIT_JOB_TYPE, kol_search_sessions._json_dumps(payload)),
    ).fetchone()
    conn.commit()
    job_id = (dict(inserted).get("id") if inserted else None)
    return {
        "status": "queued",
        "state": "queued",
        "kol_pool_id": kid,
        "job_id": int(job_id) if job_id is not None else None,
        "job_type": CONTENT_FIT_JOB_TYPE,
        "force": bool(force),
        "write_db": True,
        "writes": ["apify_jobs"],
        "viltrox_fit_score_untouched": True,
    }


@router.get("/kol-pool/{kol_pool_id}/content-fit")
async def get_pool_item_content_fit(
    kol_pool_id: int,
    analyze: bool = Query(default=False),
    force: bool = Query(default=False),
    product_sku: str | None = Query(default=None),
    staff=Depends(require_tab("vkpi", "read")),
) -> dict:
    """地基B 内容契合深析(content_fit_v1):读该 KOL 视频画面/故事 + 评论的契合判断。

    默认只读已缓存结果(不烧 LLM)。analyze=true / force=true 不再在请求内同步烧 LLM
    (那是 60s 超时的根源):若已有 ready cache 直接返回;否则把深析**入队**(worker 端
    跑 LLM)并立即返回 {status:'queued', job_id}。红线:零触 viltrox_fit_score,不新跑
    Gemini 视频分析;无视频证据由 worker 落 status='insufficient_evidence'(诚实不杜撰)。
    """
    from app.domains.kol import content_fit_analysis as kol_content_fit

    staff_dict = staff if isinstance(staff, dict) else None
    try:
        if analyze or force:
            # 重活移出请求路径:仅入队 + 立即返回(DB 路径走 threadpool,不阻塞事件循环)。
            return await run_in_threadpool(
                _enqueue_content_fit_on_demand,
                int(kol_pool_id),
                product_sku,
                force=bool(force),
                staff=staff_dict,
            )
        return await run_in_threadpool(kol_content_fit.get_content_fit, int(kol_pool_id))
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.get("/task-queue")
def get_vkpi_task_queue(
    limit: int = Query(default=50, ge=1, le=100),
    recent_minutes: int = Query(default=10, ge=1, le=120),
    include_llm_calls: bool = Query(default=True),
    staff=Depends(require_tab("vkpi", "read")),
) -> dict:
    """Read-only sidebar task queue projection; no worker/provider side effects."""
    # 波2 R1:重型端点同样按 viewer 遮蔽(此前 del staff 绕过 compact 隐私)
    return task_queue_view.get_task_queue(
        limit=int(limit),
        recent_minutes=int(recent_minutes),
        include_llm_calls=bool(include_llm_calls),
        viewer=staff if isinstance(staff, dict) else None,
    )


@router.get("/task-queue/compact")
def get_vkpi_task_queue_compact(
    limit: int = Query(default=30, ge=1, le=50),
    recent_minutes: int = Query(default=5, ge=1, le=30),
    staff=Depends(require_tab("vkpi", "read")),
) -> dict:
    """Cached read-only sidebar task queue projection for 2.5s polling."""
    # viewer 用于队列隐私(非管理员只见他人任务的存在与位次,内容遮蔽)——缓存仍全员共享。
    return task_queue_view.get_task_queue_compact(
        limit=int(limit),
        recent_minutes=int(recent_minutes),
        viewer=staff if isinstance(staff, dict) else None,
    )


@router.get("/kol-pool/{kol_pool_id}/intelligence-card")
def get_pool_item_intelligence_card(
    kol_pool_id: int,
    include_product_fit: bool = Query(default=True),
    staff=Depends(require_tab("vkpi", "read")),
) -> dict:
    """Return one read-only P2 KOL decision card from existing evidence."""
    del staff
    try:
        return kol_intelligence_card.build_kol_pool_intelligence_card(
            int(kol_pool_id),
            include_product_fit=bool(include_product_fit),
        )
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.get("/kol-pool/{kol_pool_id}/videos")
def list_kol_pool_videos(
    kol_pool_id: int,
    limit: int = Query(default=50, ge=1, le=200),
    staff=Depends(require_tab("vkpi", "read")),
) -> dict:
    """C4-full:MY KOL 库内容层(Pool 收藏行)读该 KOL 全部 evidence 视频(只读)。"""
    from app.domains.kol.pool import _video_evidence_for_kol

    items = _video_evidence_for_kol(int(kol_pool_id), limit=limit)
    return {"items": items, "total": len(items), "kol_pool_id": int(kol_pool_id)}


@router.get("/kol-pool/{kol_pool_id}/evidence-summary")
def get_pool_item_evidence_summary(
    kol_pool_id: int,
    include_product_fit: bool = Query(default=True),
    ref_limit: int = Query(default=8, ge=1, le=25),
    include_llm_preflight: bool = Query(default=True),
    staff=Depends(require_tab("vkpi", "read")),
) -> dict:
    """Return traceable summaries derived only from existing IntelligenceCard evidence."""
    del staff
    try:
        return evidence_summary.build_kol_pool_evidence_summary(
            int(kol_pool_id),
            include_product_fit=bool(include_product_fit),
            ref_limit=int(ref_limit),
            include_llm_preflight=bool(include_llm_preflight),
        )
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.get("/kol-pool/{kol_pool_id}/ai-brief")
def get_pool_item_ai_brief(
    kol_pool_id: int,
    include_product_fit: bool = Query(default=True),
    ref_limit: int = Query(default=8, ge=1, le=25),
    max_items: int = Query(default=8, ge=1, le=12),
    staff=Depends(require_tab("vkpi", "read")),
) -> dict:
    """Return read-only AI Brief v0 from existing evidence refs only."""
    del staff
    try:
        return ai_brief.build_kol_pool_ai_brief(
            int(kol_pool_id),
            include_product_fit=bool(include_product_fit),
            ref_limit=int(ref_limit),
            max_items=int(max_items),
        )
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.get("/kol-pool/{kol_pool_id}/gemini-preflight")
def get_pool_item_gemini_preflight(
    kol_pool_id: int,
    candidate_limit: int = Query(default=24, ge=1, le=100),
    include_budget_preflight: bool = Query(default=True),
    staff=Depends(require_tab("vkpi", "read")),
) -> dict:
    """Return P4.55 Gemini readiness from cached evidence only; no provider call."""
    del staff
    try:
        return gemini_single_kol_preflight.build_kol_pool_gemini_preflight(
            int(kol_pool_id),
            candidate_limit=int(candidate_limit),
            include_budget_preflight=bool(include_budget_preflight),
        )
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.get("/kol-pool/{kol_pool_id}/gemini-go-no-go")
def get_pool_item_gemini_go_no_go(
    kol_pool_id: int,
    candidate_limit: int = Query(default=24, ge=1, le=100),
    staff=Depends(require_tab("vkpi", "read")),
) -> dict:
    """Return P4.56 Gemini go/no-go report; read-only and no provider call."""
    del staff
    try:
        return gemini_single_kol_preflight.build_kol_pool_gemini_go_no_go(
            int(kol_pool_id),
            candidate_limit=int(candidate_limit),
        )
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.get("/kol-pool-dimensions11/preview")
def get_pool_dimensions11_preview(
    limit: int = Query(default=20, ge=1, le=200),
    source_type: str = Query(default="legacy_excel_p2d"),
    staff=Depends(require_tab("vkpi", "read")),
) -> dict:
    """批量预览规则版 11 维画像；只读、不调 provider、不写库。"""
    del staff
    return eleven_dimensions.batch_preview_dimensions11(limit=limit, source_type=source_type)


_BIO_ZH_CACHE: dict[str, str] = {}


@router.post("/kol-pool/translate-bio")
def translate_bio(body: dict = Body(default_factory=dict), staff=Depends(require_tab("vkpi", "read"))) -> dict:
    """#25 发现卡英文 bio → 简体中文(gpt-4o-mini,预算闸由 llm_gateway 内置)。

    进程内按原文缓存:同一 bio 第二次命中不再烧 LLM。失败/空/预算挡 → 诚实返回空译文(前端回退原文)。
    """
    text = str((body or {}).get("text") or "").strip()
    if not text:
        return {"translated": "", "lang": "zh", "cached": False, "skipped": "empty"}
    if len(text) > 1200:
        text = text[:1200]
    if text in _BIO_ZH_CACHE:
        return {"translated": _BIO_ZH_CACHE[text], "lang": "zh", "cached": True}
    try:
        from app.platform import llm_gateway

        prompt = (
            "Translate the following social-media creator bio into natural Simplified Chinese. "
            "Keep @handles, brand names and URLs as-is. Return ONLY the translation, "
            "no quotes, no explanation:\n\n" + text
        )
        resp = llm_gateway.invoke(
            prompt=prompt,
            purpose="vkpi_bio_translate",
            preferred_provider="openai",
            max_output_tokens=400,
        )
        if str(resp.get("status") or "") == "success":
            out = str(resp.get("text") or "").strip().strip('"').strip("'").strip()
            if out:
                _BIO_ZH_CACHE[text] = out
                return {"translated": out, "lang": "zh", "cached": False}
        return {"translated": "", "lang": "zh", "cached": False, "skipped": str(resp.get("status") or "unavailable")}
    except Exception as exc:  # noqa: BLE001 — 翻译失败不阻断,前端回退原文
        return {"translated": "", "lang": "zh", "cached": False, "error": str(exc)}


@router.get("/kol-pool/resolve")
def resolve_kol_pool(
    handle: str = Query(default=""),
    platform: str = Query(default=""),
    staff=Depends(require_tab("vkpi", "read")),
) -> dict:
    """#17 按 handle(可选 platform)解析到 vkpi 主池记录。

    供 mover 预览弹窗(#5)/ KOLDetailModal 真指标(#22):用 handle 拿真 kol_pool_id +
    真 followers/avg_views/合作摘要。命中返回 history_match 全量 payload;未命中诚实
    返回 matched=False(前端据此走「先入库」或显空,不再编造假指标)。
    """
    h = (handle or "").strip()
    plat = (platform or "").strip()
    if not h:
        return {"matched": False, "reason": "handle required"}
    item = {"handle": h, "display_name": h, "platform": plat}
    payload = kol_history_match.find_history_match(item, platform=plat)
    if not payload:
        return {"matched": False, "handle": h, "platform": plat}
    return payload


@router.post("/kol-pool/{kol_pool_id}/promote")
@audit_action(
    action_type="kol_pool_promote_to_main",
    target_type="kol_pool",
    target_id_extractor=lambda result, kwargs: str(kwargs.get("kol_pool_id") or ""),
    detail_extractor=lambda result, kwargs: f"promote pool_id={kwargs.get('kol_pool_id')} mode={result.get('mode')} main_kol_id={result.get('main_kol_id')}",
    metadata_extractor=lambda result, kwargs: {
        "mode": result.get("mode") if isinstance(result, dict) else "",
        "main_kol_id": result.get("main_kol_id") if isinstance(result, dict) else None,
    },
)
def promote_to_main_kol(
    kol_pool_id: int,
    body: dict = Body(default_factory=dict),
    staff=Depends(require_tab("vkpi", "write")),
) -> dict:
    """自动匹配或创建 kols 主表记录并链接，替代前端手动输入 ID。"""
    try:
        return kol_pool.promote_to_main(
            int(kol_pool_id),
            staff=staff,
            mode=str(body.get("mode") or "match_or_create"),
        )
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.post("/kol-pool/{kol_pool_id}/enrich")
@audit_action(
    action_type="kol_pool_enrich",
    target_type="kol_pool",
    target_id_extractor=lambda result, kwargs: str(kwargs.get("kol_pool_id") or ""),
    detail_extractor=lambda result, kwargs: f"enriched pool_id={kwargs.get('kol_pool_id')} status={result.get('sync_status')}",
)
def enrich_pool_item(
    kol_pool_id: int,
    body: dict = Body(default_factory=dict),
    staff=Depends(require_tab("vkpi", "write")),
) -> dict:
    """用真实平台 crawler 补齐单条候选的头像/播放/互动/适配度。"""
    try:
        return kol_pool.enrich_item(
            int(kol_pool_id),
            max_posts=max(1, min(int(body.get("max_posts") or 12), 50)),
            staff=staff,
        )
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


# ─── Write endpoints (装饰器集成示范) ────────────────


@router.post("/kol-pool/import")
@firewall_check(
    platform="",  # 平台从 body 动态取,防火墙在 service 层做(此处先用 feature_flag)
    feature_flag="",  # 暂时不用 feature_flag,只做 audit
    require_budget=False,  # import 本身不调外部 API,不用 budget
    bypass_param="force",
)
@audit_action(
    action_type="kol_pool_import",
    target_type="kol_pool",
    detail_extractor=lambda result, kwargs: f"imported {result.get('imported', 0)} skipped {result.get('skipped', 0)}",
    metadata_extractor=lambda result, kwargs: {
        "source_type": kwargs.get("body", {}).get("source_type") if isinstance(kwargs.get("body"), dict) else "",
        "platform": kwargs.get("body", {}).get("platform") if isinstance(kwargs.get("body"), dict) else "",
        "imported_count": result.get("imported", 0) if isinstance(result, dict) else 0,
    },
)
def import_pool(
    body: dict = Body(default_factory=dict),
    staff=Depends(require_tab("vkpi", "write")),
) -> dict:
    """
    一键导入 KOL 数据 (CSV / Apify / 手动).
    
    Body:
      items:        list[dict] - KOL 数据列表
      source_type:  str        - "manual" | "apify" | "csv" 等
      source_ref:   str        - 来源标识 (run_id / file_name 等)
      platform:     str        - 默认平台 (item 没指定时用)
      force:        bool       - owner 可用,bypass 防火墙
    
    返回:
      {imported: int, skipped: int, items: [...]}
    """
    items = body.get("items") or []
    if not isinstance(items, list):
        raise HTTPException(status_code=400, detail="items must be a list")
    if not items:
        raise HTTPException(status_code=400, detail="items cannot be empty")
    
    return kol_pool.import_items(
        items,
        source_type=str(body.get("source_type") or "manual"),
        source_ref=str(body.get("source_ref") or ""),
        platform=str(body.get("platform") or ""),
        staff=staff,
    )


@router.post("/kol-pool/{kol_pool_id}/link")
@audit_action(
    action_type="kol_pool_link_to_main",
    target_type="kol_pool",
    detail_extractor=lambda result, kwargs: f"linked pool_id={kwargs.get('kol_pool_id')} to main_kol_id={kwargs.get('body', {}).get('main_kol_id')}",
)
def link_to_main_kol(
    kol_pool_id: int,
    body: dict = Body(default_factory=dict),
    staff=Depends(require_tab("vkpi", "write")),
) -> dict:
    """
    把 KOL Pool 项链接到 kols 主表(作为活跃合作 KOL).
    
    Body:
      main_kol_id: int - kols 表的 id
    """
    main_kol_id = body.get("main_kol_id")
    if not main_kol_id:
        raise HTTPException(status_code=400, detail="main_kol_id is required")
    
    from app.db.connection import get_conn
    from datetime import datetime, UTC
    
    conn = get_conn()
    
    # 验证 kol_pool 存在
    pool_row = conn.execute(
        "SELECT id FROM vkpi_kol_pool WHERE id=?",
        (int(kol_pool_id),),
    ).fetchone()
    if not pool_row:
        raise HTTPException(status_code=404, detail="kol_pool item not found")
    
    # 验证 kols 主表存在
    main_row = conn.execute(
        "SELECT id FROM kols WHERE id=?",
        (int(main_kol_id),),
    ).fetchone()
    if not main_row:
        raise HTTPException(status_code=404, detail="main kol not found")
    
    # 链接
    now = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    conn.execute(
        "UPDATE vkpi_kol_pool SET linked_main_kol_id=?, updated_at=? WHERE id=?",
        (int(main_kol_id), now, int(kol_pool_id)),
    )
    conn.commit()
    
    return {
        "kol_pool_id": int(kol_pool_id),
        "main_kol_id": int(main_kol_id),
        "linked": True,
    }


# ── C2 收藏三端点(四环漏斗第一段;依赖 migration 107,apply 前勿激活)──
@router.post("/kol-pool/{kol_pool_id}/favorite")
@audit_action(
    action_type="kol_pool_favorite",
    target_type="kol_pool",
    target_id_extractor=lambda result, kwargs: str(kwargs.get("kol_pool_id") or ""),
    detail_extractor=lambda result, kwargs: f"favorite pool_id={kwargs.get('kol_pool_id')} status={result.get('status')}",
)
def favorite_kol_pool_item(
    kol_pool_id: int,
    body: dict = Body(default_factory=dict),
    staff=Depends(require_tab("vkpi", "write")),
) -> dict:
    """收藏(My KOL 归宿)。幂等:重复收藏返回 already_favorited。"""
    from app.domains.kol import pool_favorites

    try:
        return pool_favorites.add_favorite(int(kol_pool_id), staff=staff, note=str(body.get("note") or ""))
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc


@router.delete("/kol-pool/{kol_pool_id}/favorite")
@audit_action(
    action_type="kol_pool_unfavorite",
    target_type="kol_pool",
    target_id_extractor=lambda result, kwargs: str(kwargs.get("kol_pool_id") or ""),
    detail_extractor=lambda result, kwargs: f"unfavorite pool_id={kwargs.get('kol_pool_id')} status={result.get('status')}",
)
def unfavorite_kol_pool_item(
    kol_pool_id: int,
    staff=Depends(require_tab("vkpi", "write")),
) -> dict:
    """取消收藏。幂等;在役软禁止(C8)落地时在 domain 层前置。"""
    from app.domains.kol import pool_favorites

    try:
        return pool_favorites.remove_favorite(int(kol_pool_id), staff=staff)
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc



