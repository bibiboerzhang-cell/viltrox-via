"""backend/app/api/routers/vkpi_kol_pool_discovery.py

行为不变迁出:发现子域端点簇(推荐卡 / 统一搜索 / 联邦发现源注册表+即时预览+
持久化刷新 / 建档 Workflow / 发现落池 / 外部富集证据读)。
原 vkpi_kol_pool.py 通过 router.include_router(_kol_pool_discovery_router) 兜住;
本子 router 无 prefix,include 后继承父 router 的 /api/admin/vkpi,路径逐字不变。

铁律:本文件端点的先后顺序 = 拆分前父文件里的注册顺序,逐条照抄,绝不重排
(路由表顺序 = 对外行为;test_router_package_lazy_import_contract 钉了全表 sha)。
include 点也钉死在父文件 workspace 之后 / enrich-via-apify 之前的原位。
/kol-pool/unified-search 是单段静态 GET,必须先于 /kol-pool/{kol_pool_id} 注册,
否则被当 int 解析 → 永久 422。

红线:零触 viltrox_fit_score。
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, Request

from app.api.dependencies.manager_guard import require_manager_tab
from app.api.dependencies.perms import require_tab


router = APIRouter(tags=["vkpi-kol-pool"])


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
