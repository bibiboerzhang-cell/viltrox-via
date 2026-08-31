"""backend/app/api/routers/vkpi_kol_pool_intel_reports.py

行为不变抽取:vkpi_kol_pool_intel.py 的「只读情报报表」子域——11 维画像、情报卡、
evidence 视频清单、竞品露出、证据摘要、AI Brief、Gemini 预检/go-no-go、批量画像预览。
全部只读:零 provider 调用、零 LLM、零写库、零触 viltrox_fit_score。

本模块导出**两个** APIRouter,不是按主题拆的,而是为了让 app.main 的路由表顺序
逐条不变(见 tests/test_router_package_lazy_import_contract.py 的路由签名钉子):
父模块在原来定义 dimensions11 的位置 include ``dimensions11_item_router``,在原来
定义情报卡簇的位置 include ``router``。两个 router 都不带 tags,由父级 router 逐层
带入,保证 OpenAPI tag 列表与拆分前一致。
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query

from app.api.dependencies.perms import require_tab
from app.core.logging import get_logger
from app.domains.kol import eleven_dimensions
from app.domains.kol import intelligence_card as kol_intelligence_card
import app.domains.intelligence.ai_brief as ai_brief
import app.domains.evidence.summary as evidence_summary
from app.domains.intelligence import gemini_single_kol_preflight

dimensions11_item_router = APIRouter()
router = APIRouter()

logger = get_logger(__name__)


@dimensions11_item_router.get("/kol-pool/{kol_pool_id}/dimensions11")
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


@router.get("/kol-pool/{kol_pool_id}/competitor-exposure")
def get_pool_item_competitor_exposure(
    kol_pool_id: int,
    force: bool = Query(default=False),
    staff=Depends(require_tab("vkpi", "read")),
) -> dict:
    """#51 百家饭指数(竞品露出率):聚合该 KOL 已深析(final_v1)evidence 的品牌提及 →
    Viltrox vs 竞品露出比 + 专情指数(0-100,带样本量置信折扣)。纯读已有深析产物,
    零新分析/零 LLM;结果当日缓存(vkpi_analysis_cache),force=true 才重算。
    红线:零触 viltrox_fit_score、不动 rule_v0、不碰 KOL 归属判定。"""
    del staff
    from app.domains.kol import competitor_exposure

    try:
        return competitor_exposure.get_competitor_exposure(int(kol_pool_id), force=bool(force))
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except Exception as exc:  # noqa: BLE001 — 聚合失败不该 500 裸炸,诚实回原因供前端展示
        logger.warning("vkpi.competitor_exposure_read_failed | kol_pool_id=%s", kol_pool_id, exc_info=True)
        return {"status": "error", "reason": "competitor_exposure_read_failed", "kol_pool_id": int(kol_pool_id)}


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
