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

from fastapi import APIRouter, Body, Depends, HTTPException, Query, Request

from app.api.dependencies.perms import require_tab
from app.domains.kol import competitor_detector as kol_competitor_detector
from app.domains.kol import intelligence_card as kol_intelligence_card
from app.services.vkpi import (
    ai_brief,
    evidence_summary,
    eleven_dimensions,
    gemini_single_kol_preflight,
    kol_pool,
    refresh_tier,
    task_enqueue,
)
from app.services.vkpi.audit_decorator import audit_action
from app.services.vkpi.firewall_decorator import firewall_check


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
    staff=Depends(require_tab("vkpi", "read")),
) -> dict:
    """返回 KOL Pool 项的规则版 11 维画像；只读、不调 provider、不写库。"""
    try:
        return eleven_dimensions.compose_dimensions_11(int(kol_pool_id))
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


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
