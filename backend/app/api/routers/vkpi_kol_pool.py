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
import uuid
from urllib.parse import urlparse

from app.core.logging import get_logger
from fastapi import APIRouter, Body, Depends, HTTPException, Query, Request, Response
from fastapi.concurrency import run_in_threadpool

from app.api.dependencies.manager_guard import require_manager_staff, require_manager_tab
from app.api.dependencies.perms import require_tab
from app.api.routers.vkpi_kol_contact_projection import PRIVATE_CONTACT_HEADERS
from app.api.routers.vkpi_kol_paid_scope import assert_paid_target_writable, build_paid_target_fence
from app.core.release_validation import release_validation_active
from app.domains.kol import competitor_detector as kol_competitor_detector
from app.domains.kol import account_dossier as kol_account_dossier
from app.domains.kol import account_dossier_extract as kol_account_dossier_extract
from app.domains.kol import eleven_dimensions
from app.domains.kol import intelligence_card as kol_intelligence_card
from app.domains.kol import history_match as kol_history_match
from app.domains.kol import llm_deep_analysis as kol_llm_deep_analysis
from app.domains.kol import pool as kol_pool
from app.domains.kol.pool_common import CONTACT_VISIBILITY_MASKED
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
from app.domains.projects import cost_estimate as project_cost_estimate
from app.domains.projects import outreach as project_outreach
import app.domains.sync.refresh_tier as refresh_tier
import app.domains.tasks.enqueue as task_enqueue
from app.domains.audit.decorator import audit_action
from app.domains.access.firewall import firewall_check


logger = get_logger(__name__)

def _kol_operation_error(operation: str, exc: Exception) -> HTTPException:
    """Map KOL writes to stable client errors without exposing internals."""
    correlation_id = uuid.uuid4().hex
    class_name = type(exc).__name__.lower()
    message = str(exc).lower()

    explicit_status = getattr(exc, "status_code", None)
    explicit_code = str(getattr(exc, "code", "") or "")
    if explicit_code and isinstance(explicit_status, int):
        return HTTPException(status_code=explicit_status, detail=explicit_code)
    if isinstance(exc, LookupError):
        status_code, code, retryable = 404, f"{operation}_not_found", False
        public_message = "请求的 KOL 或视频证据不存在。"
    elif isinstance(exc, ValueError):
        status_code, code, retryable = 422, f"{operation}_invalid_request", False
        public_message = "请求参数无效，请检查后重试。"
    elif (
        "integrity" in class_name
        or "uniqueviolation" in class_name
        or any(token in message for token in ("already linked", "conflict", "duplicate", "changed_ids", "rolled back"))
    ):
        status_code, code, retryable = 409, f"{operation}_conflict", False
        public_message = "当前数据状态与该操作冲突，请刷新后核对。"
    elif isinstance(exc, RuntimeError) or any(
        token in class_name for token in ("operationalerror", "interfaceerror", "databaseerror", "timeouterror")
    ):
        status_code, code, retryable = 503, f"{operation}_queue_unavailable", True
        public_message = "任务队列暂时不可用，操作未完成，请稍后重试。"
    else:
        status_code, code, retryable = 500, f"{operation}_internal_error", False
        public_message = "操作未完成，请联系管理员并提供错误编号。"

    if status_code >= 500:
        logger.exception("kol operation failed operation=%s correlation_id=%s", operation, correlation_id)
    else:
        logger.info(
            "kol operation rejected operation=%s status=%s correlation_id=%s exception=%s",
            operation,
            status_code,
            correlation_id,
            type(exc).__name__,
        )
    return HTTPException(
        status_code=status_code,
        detail={
            "code": code,
            "message": public_message,
            "retryable": retryable,
            "correlation_id": correlation_id,
        },
    )


def _sanitize_batch_enqueue_result(result: dict) -> dict:
    items = result.get("items")
    if not isinstance(items, list):
        return result
    sanitized: list[dict] = []
    for raw_item in items:
        item = dict(raw_item) if isinstance(raw_item, dict) else {"status": "error"}
        if item.get("status") == "error":
            item.pop("reason", None)
            item.update({"code": "video_analysis_item_failed", "retryable": True})
        elif item.get("status") == "not_found":
            item.pop("reason", None)
            item.update({"code": "video_evidence_not_found", "retryable": False})
        sanitized.append(item)
    return {**result, "items": sanitized}


def _record_pool_feedback_signal(
    kol_pool_id: int,
    action: str,
    *,
    staff: dict | None = None,
    note: str = "",
) -> None:
    """L7: bridge a real board action (favorite/promote/unfavorite) into
    recommendation feedback so the learning corpus grows from real operator
    behavior. Best-effort — never breaks the primary board action, and never
    touches viltrox_fit_score."""
    try:
        from app.domains.recommendations import actions as rec_actions

        rec_actions.record_pool_action_feedback(
            int(kol_pool_id),
            action,
            staff=staff,
            note=note,
        )
    except Exception:
        logger.debug("kol_pool.feedback_bridge_failed", exc_info=True)


router = APIRouter(prefix="/api/admin/vkpi", tags=["vkpi-kol-pool"])

# task-queue 端点已抽到 vkpi_task_queue.py(无 prefix);include 后继承本 router 的 /api/admin/vkpi,路径不变。
from app.api.routers.vkpi_task_queue import router as _task_queue_router  # noqa: E402

router.include_router(_task_queue_router)


# ─── Read endpoints (无装饰器) ──────────────────────


# ── 内聚 helper 簇行为不变迁出 → vkpi_kol_pool_helpers.py;此处 re-export 兜住所有调用点 ──
from app.api.routers.vkpi_kol_pool_helpers import (  # noqa: E402
    _attach_smart_recall_session,
    _attach_smart_url_session,
    _int_or_none,
    _KNOWN_URL_DOMAINS,
    _looks_like_url,
    _maybe_enqueue_refresh,
    _on_demand_refresh_enabled,
    _smart_query_type,
)

# ── 搜索 / 会话 / 召回 / URL 深爬端点簇行为不变迁出 → vkpi_kol_pool_search.py;无 prefix 子 router include ──
from app.api.routers.vkpi_kol_pool_search import router as _kol_pool_search_router  # noqa: E402

router.include_router(_kol_pool_search_router)


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
    """列出 KOL Pool；GET 始终纯读，refresh_if_stale 仅为旧客户端兼容参数。"""
    del request, refresh_if_stale, staff
    return kol_pool.list_pool(
        limit=limit,
        offset=offset,
        platform=platform,
        query=query,
        country=country,
        data_status=data_status,
        sort_by=sort_by,
        enrichable=enrichable,
        # Bulk surfaces never disclose plaintext contacts. Authorized users use
        # the single-KOL audited contact boundary instead.
        contact_visibility=CONTACT_VISIBILITY_MASKED,
    )


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
    return kol_pool.workspace(
        limit=limit,
        offset=offset,
        platform=platform,
        query=query,
        country=country,
        data_status=data_status,
        sort_by=sort_by,
        enrichable=enrichable,
        contact_visibility=CONTACT_VISIBILITY_MASKED,
    )


@router.get("/kol-pool/{kol_pool_id}/recommendation-card")
def kol_recommendation_card(
    kol_pool_id: int,
    staff=Depends(require_tab("vkpi", "read")),
) -> dict:
    """KOL 推荐卡:数据完整度档(A-D)+ 为什么推荐 + 展示信号(只读;档位非 fit)。"""
    from app.domains.kol import recommendation_card

    return recommendation_card.get_recommendation_card(int(kol_pool_id), staff=staff)


@router.get("/kol-pool/unified-search")
def kol_unified_search(
    q: str = Query(..., min_length=1, max_length=256),
    external: bool = False,
    limit: int = Query(default=20, ge=1, le=100),
    staff=Depends(require_tab("vkpi", "read")),
) -> dict:
    """段2 · 统一搜索响应模型:source/status/cost_gate/provider_status/candidate_ids/history_match 一个形。"""
    from app.domains.kol import unified_search

    return unified_search.unified_search(q, include_external=bool(external), limit=int(limit), staff=staff)


@router.get("/kol-pool/discovery/providers")
def kol_pool_discovery_providers(staff=Depends(require_tab("vkpi", "read"))) -> dict:
    """联邦发现:源注册表(自有 internal_pool 就绪;商业源 modash/hypeauditor/蝉妈妈 待 key+适配器)。"""
    from app.domains.discovery import federation

    return {"providers": federation.list_providers()}


@router.get("/kol-pool/discovery/federated-search")
def kol_pool_federated_search(
    q: str = Query(..., min_length=1, max_length=256),
    limit: int = Query(default=20, ge=1, le=100),
    staff=Depends(require_tab("vkpi", "read")),
) -> dict:
    """联邦发现即时预览。

    读路径只查自有/已物化数据；付费外部源返回
    ``background_refresh_required``，由 refresh 写路径持久化排队。
    """
    from app.domains.discovery import federation

    result = federation.federated_search(q, limit=int(limit), staff=staff)
    result["execution_mode"] = "preview_only"
    return result


@router.post("/kol-pool/discovery/federated-search/refresh")
async def kol_pool_federated_search_refresh(
    request: Request,
    q: str = Query(..., min_length=1, max_length=256),
    limit: int = Query(default=20, ge=1, le=100),
    staff=Depends(require_manager_tab("vkpi", "write")),
) -> dict:
    """持久化刷新联邦外部源；结果由通用任务进度/结果端点读取。"""
    query = str(q or "").strip()
    if not query:
        raise HTTPException(status_code=400, detail="q required")
    queue = getattr(request.app.state, "job_queue", None)
    if queue is None:
        raise HTTPException(status_code=503, detail="durable job queue unavailable")
    if str(getattr(queue, "backend_name", "")) == "inprocess":
        raise HTTPException(
            status_code=503,
            detail="durable_queue_required:inprocess_queue_has_no_provider_execution_fence",
        )
    safe_limit = max(1, min(int(limit or 20), 100))
    try:
        task_id = await queue.enqueue(
            "discovery_federated_search",
            {"query": query, "limit": safe_limit, "staff": dict(staff or {})},
            lock_key=f"discovery_federated_search:{query.casefold()}:{safe_limit}",
            timeout_seconds=1200,
        )
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    if not str(task_id or "").strip():
        raise HTTPException(status_code=503, detail="durable job queue returned no job id")
    return {
        "status": "queued",
        "job_id": task_id,
        "progressive": True,
        "initial_stage": "queued",
    }


@router.post("/kol-pool/onboarding-sweep")
async def kol_onboarding_sweep(
    request: Request,
    q: str = Query(..., min_length=1, max_length=256),
    staff=Depends(require_manager_tab("vkpi", "write")),
) -> dict:
    """阶段②·KOL 建档 Durable Workflow:联邦发现+落库→富集→记忆(可恢复,串起 Apify 线)。"""
    from app.domains.kol import onboarding_workflow

    query = str(q or "").strip()
    if not query:
        raise HTTPException(status_code=400, detail="q required")
    try:
        result = await onboarding_workflow.enqueue_kol_onboarding(
            getattr(request.app.state, "job_queue", None),
            query,
            staff=staff,
        )
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    return {
        **result,
        "progressive": True,
        "initial_stage": "queued",
    }


@router.post("/kol-pool/discovery/enroll")
async def kol_pool_discovery_enroll(
    request: Request,
    q: str = Query(..., min_length=1, max_length=256),
    limit: int = Query(default=20, ge=1, le=100),
    staff=Depends(require_manager_tab("vkpi", "write")),
) -> dict:
    """链1 KOL 自增长:排入有预算与执行闸的发现→落池 Workflow。"""
    from app.domains.kol import onboarding_workflow

    try:
        return await onboarding_workflow.enqueue_kol_onboarding(
            getattr(request.app.state, "job_queue", None),
            q,
            limit=int(limit),
            staff=staff,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@router.get("/kol-pool/{kol_pool_id}/enrichment")
def kol_pool_enrichment(kol_pool_id: int, staff=Depends(require_tab("vkpi", "read"))) -> dict:
    """KOL 外部富集证据(受众/刷粉/画像/历史;独立展示,绝不并入 viltrox_fit_score)。"""
    from app.domains.discovery import enrichment

    return enrichment.get_enrichment(int(kol_pool_id))


@router.post("/kol-pool/{kol_pool_id}/enrich-via-apify")
async def kol_pool_enrich_via_apify(
    request: Request,
    kol_pool_id: int,
    staff=Depends(require_tab("vkpi", "write")),
) -> dict:
    """把 Apify 用透:抓该 KOL 公开数据 → 存富集证据(env 门控防意外计费)。"""
    if release_validation_active():
        raise HTTPException(status_code=503, detail="release_validation_fenced")
    fence_key, target_fence = build_paid_target_fence(
        int(kol_pool_id), staff, action="kol_apify_enrich"
    )
    queue = getattr(request.app.state, "job_queue", None)
    if queue is None:
        raise HTTPException(status_code=503, detail="durable job queue unavailable")
    task_id = await queue.enqueue(
        "kol_apify_enrich",
        {"kol_pool_id": int(kol_pool_id), "force": True, fence_key: target_fence},
        lock_key=f"kol_apify_enrich:{int(kol_pool_id)}",
        timeout_seconds=1200,
    )
    return {"status": "queued", "job_id": task_id, "progressive": True, "initial_stage": "queued"}


@router.get("/kol-pool/auto-poll/status")
def kol_pool_auto_poll_status(staff=Depends(require_tab("vkpi", "read"))) -> dict:
    """D3 · 关注 KOL 自动轮询状态:应轮询候选数 + 队列可用性(只读,全容错)。"""
    from app.domains.kol import auto_poll

    return auto_poll.auto_poll_status()


@router.get("/kol-pool/{kol_pool_id}/twin")
def kol_twin(
    kol_pool_id: int,
    staff=Depends(require_tab("vkpi", "read")),
) -> dict:
    """C3 · KOL 数字孪生:合作判断档案(身份+为什么记住+数据等级+历史表现+学习信号+合作建议)。"""
    from app.domains.kol import twin

    return twin.get_kol_twin(int(kol_pool_id), staff=staff)


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


# ── 竞品只读 + 批量富集/深爬/评论采集/联系草稿/外联优化 端点簇行为不变迁出 → vkpi_kol_pool_jobs.py;无 prefix 子 router include ──
from app.api.routers.vkpi_kol_pool_jobs import router as _kol_pool_jobs_router  # noqa: E402

router.include_router(_kol_pool_jobs_router)


# ── 联系方式 / 合作时间线 / 联系草稿读端点簇行为不变迁出 → vkpi_kol_pool_intel.py(同子 router,已 include)──


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
        raise _kol_operation_error("needs_analysis", exc) from exc


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

    注册在 /{kol_pool_id} 动态路由之前:FastAPI 按声明顺序匹配,静态 /resolve 若排在
    /{kol_pool_id} 之后会被当 int 解析 → 永久 422(与 needs-analysis 同款吞路由陷阱)。
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


@router.get("/kol-pool/{kol_pool_id}")
async def get_item(
    request: Request,
    response: Response,
    kol_pool_id: int,
    refresh_if_stale: bool = Query(default=True),
    staff=Depends(require_tab("vkpi", "read")),
) -> dict:
    """获取单个 KOL Pool 项；GET 不写搜索标记、不排队，刷新仅走显式 POST。"""
    del request, refresh_if_stale, staff
    response.headers.update(PRIVATE_CONTACT_HEADERS)
    try:
        result = kol_pool.get_item(
            int(kol_pool_id),
            contact_visibility=CONTACT_VISIBILITY_MASKED,
        )
        result["contact_projection_reason"] = "summary_only"
        return result
    except LookupError as exc:
        raise HTTPException(status_code=404, detail="kol pool item not found", headers=PRIVATE_CONTACT_HEADERS) from exc


@router.get("/kol-pool/{kol_pool_id}/detail-bundle")
def get_item_detail_bundle(
    request: Request,
    response: Response,
    kol_pool_id: int,
    # P9:此前 default=3/max=10 把账号详情抽屉钉死在"前 4 条";底层 evidence 早已物化全量,
    # 抬到 default=24/max=200,让单账号详情默认展示该账号(基本)全部视频,前端可按需再加载。
    # 这是 READ-ONLY 物化展示口径(便宜),不触发新的 Gemini 深析(那是另一条限量+预算闸的链)。
    video_limit: int = Query(default=24, ge=1, le=200),
    llm_limit: int = Query(default=20, ge=1, le=50),
    staff=Depends(require_tab("vkpi", "read")),
) -> dict:
    """Read-only detail drawer bundle; does not refresh providers or touch V6 Fit."""
    response.headers.update(PRIVATE_CONTACT_HEADERS)
    try:
        result = kol_pool.detail_bundle(
            int(kol_pool_id),
            video_limit=video_limit,
            llm_limit=llm_limit,
            contact_visibility=CONTACT_VISIBILITY_MASKED,
        )
        result["contact_projection_reason"] = "summary_only"
        return result
    except LookupError as exc:
        raise HTTPException(status_code=404, detail="kol pool item not found", headers=PRIVATE_CONTACT_HEADERS) from exc


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
        assert_paid_target_writable(int(kol_pool_id), staff)
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
    if body.get("local_evaluation") is True:
        raise HTTPException(status_code=403, detail="local_evaluation_http_forbidden")
    evidence_id = body.get("evidence_id")
    try:
        evidence_id_int = int(evidence_id)
    except (TypeError, ValueError) as exc:
        raise _kol_operation_error("video_analysis_enqueue", ValueError("invalid evidence_id")) from exc
    try:
        return kol_video_analysis_enqueue.enqueue_final_v1_video_analysis(
            kol_pool_id=int(kol_pool_id),
            evidence_id=evidence_id_int,
            staff=staff,
            local_evaluation=False,
            enforce_target_write=True,
        )
    except Exception as exc:
        raise _kol_operation_error("video_analysis_enqueue", exc) from exc


@router.post("/kol-pool/enqueue-video-analysis-batch")
def enqueue_pool_video_analysis_batch(
    body: dict = Body(default_factory=dict),
    staff=Depends(require_tab("vkpi", "write")),
) -> dict:
    """Queue multiple final_v1 video analysis jobs; independent from V6 Fit."""
    raw_items = body.get("items")
    if not isinstance(raw_items, list) or not raw_items:
        raise _kol_operation_error("video_analysis_batch_enqueue", ValueError("invalid items"))
    try:
        from app.db.connection import get_conn
        from app.domains.kol.my_kol_paid_action_access import assert_target_writable

        conn = get_conn()
        pool_ids = {
            int(item.get("kol_pool_id") or 0)
            for item in raw_items
            if isinstance(item, dict) and str(item.get("kol_pool_id") or "").isdigit()
        }
        for pool_id in sorted(pool_ids):
            assert_target_writable(conn, kol_pool_id=pool_id, staff=staff)
        result = kol_video_analysis_enqueue.enqueue_final_v1_video_analysis_batch(
            items=raw_items,
            staff=staff,
            enforce_target_write=True,
        )
        return _sanitize_batch_enqueue_result(result)
    except Exception as exc:
        raise _kol_operation_error("video_analysis_batch_enqueue", exc) from exc


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
            enforce_target_write=True,
        )
    except Exception as exc:
        raise _kol_operation_error("video_analysis_all_enqueue", exc) from exc


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


# ── 只读情报/画像/契合/证据端点簇行为不变迁出 → vkpi_kol_pool_intel.py;无 prefix 子 router include。
#    私有名 _enqueue_content_fit_on_demand / _BIO_ZH_CACHE 在下方 re-export 兜住可能的外部引用。──
from app.api.routers.vkpi_kol_pool_intel import (  # noqa: E402
    router as _kol_pool_intel_router,
    _enqueue_content_fit_on_demand,
    _BIO_ZH_CACHE,
)

router.include_router(_kol_pool_intel_router)
# ── 账号级视频深析进度(只读,O→F 契约:failure_category/failure_reason_human/eta_seconds)→ vkpi_kol_pool_progress.py ──
from app.api.routers.vkpi_kol_pool_progress import router as _kol_pool_progress_router  # noqa: E402

router.include_router(_kol_pool_progress_router)


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
    staff=Depends(require_manager_tab("vkpi", "write")),
) -> dict:
    """自动匹配或创建 kols 主表记录并链接，替代前端手动输入 ID。"""
    require_manager_staff(staff if isinstance(staff, dict) else {})
    mode = str(body.get("mode") or "match_or_create")
    if mode not in {"match_or_create", "match_only"}:
        raise _kol_operation_error("kol_promote", ValueError("invalid promote mode"))
    try:
        result = kol_pool.promote_to_main(
            int(kol_pool_id),
            staff=staff,
            mode=mode,
        )
    except Exception as exc:
        raise _kol_operation_error("kol_promote", exc) from exc
    _record_pool_feedback_signal(int(kol_pool_id), "promote", staff=staff, note=str(body.get("note") or ""))
    return result


@router.post("/kol-pool/{kol_pool_id}/enrich")
@audit_action(
    action_type="kol_pool_enrich",
    target_type="kol_pool",
    target_id_extractor=lambda result, kwargs: str(kwargs.get("kol_pool_id") or ""),
    detail_extractor=lambda result, kwargs: f"enrich queued pool_id={kwargs.get('kol_pool_id')} status={result.get('status')}",
)
async def enrich_pool_item(
    request: Request,
    kol_pool_id: int,
    body: dict = Body(default_factory=dict),
    staff=Depends(require_tab("vkpi", "write")),
) -> dict:
    """持久队列补齐单条候选的真实平台资料。"""
    if release_validation_active():
        raise HTTPException(status_code=503, detail="release_validation_fenced")
    from app.domains.kol.my_kol_paid_action_access import MyKolPaidActionError

    try:
        queue = getattr(request.app.state, "job_queue", None)
        return await task_enqueue.enqueue_kol_pool_on_demand_refresh(
            queue,
            int(kol_pool_id),
            reason="manual_api_enrich",
            max_posts=max(1, min(int(body.get("max_posts") or 3), 3)),
            staff=staff,
            enforce_target_write=True,
        )
    except MyKolPaidActionError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.code) from exc
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


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
    """收藏(My KOL 归宿)。幂等:重复收藏返回 already_favorited。C5:收藏即登记指标追踪(best-effort,受月闸)。"""
    from app.domains.kol import favorite_side_effects, pool_favorites

    try:
        result = pool_favorites.add_favorite(int(kol_pool_id), staff=staff, note=str(body.get("note") or ""))
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    _record_pool_feedback_signal(int(kol_pool_id), "favorite", staff=staff, note=str(body.get("note") or ""))
    return {**result, **favorite_side_effects.enroll_tracking_after_favorite(int(kol_pool_id), staff=staff)}


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
        result = pool_favorites.remove_favorite(int(kol_pool_id), staff=staff)
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    if result.get("status") == "unfavorited":
        _record_pool_feedback_signal(int(kol_pool_id), "unfavorite", staff=staff)
    return result
