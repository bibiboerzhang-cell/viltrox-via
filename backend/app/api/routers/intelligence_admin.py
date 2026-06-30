"""
api/routers/intelligence_admin.py — Intelligence admin endpoints (batch 3)

These are the NEW endpoints; many existing endpoints in intelligence.py are
reused by the UI directly (bh/summary, bh/products, viltrox/overview, etc).

Wire into main:
    from app.api.routers import intelligence_admin
    app.include_router(intelligence_admin.router)
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from typing import Literal

from app.core.security import require_admin_async as require_admin
from app.services.intelligence import market as market_svc
from app.services.intelligence import brand as brand_svc
from app.services.intelligence import analytics as analytics_svc

router = APIRouter(prefix="/api", tags=["intelligence-admin"])


# =========================================================================
# Market
# =========================================================================

@router.get("/intelligence/market/heatmap")
def market_heatmap(admin=Depends(require_admin)):
    """Category heatmap — Viltrox vs competitors across focal ranges."""
    return market_svc.build_category_heatmap()


@router.get("/intelligence/market/observations")
def market_observations(
    kind: str | None = None,
    impact: str | None = None,
    from_date: str | None = None,
    to_date: str | None = None,
    limit: int = 50,
    admin=Depends(require_admin),
):
    return market_svc.list_observations(
        kind=kind, impact=impact, from_date=from_date, to_date=to_date, limit=limit
    )


@router.get("/intelligence/market/trends")
def market_trends(
    kind: str | None = None,
    history: bool = False,
    limit: int = 100,
    admin=Depends(require_admin),
):
    """N7 市场趋势 / 观察:只读合成 market_brain + competitor_radar + bet_ledger 真数据。

    返回结构化观察 {topic, kind, source, evidence_refs, confidence, suggested_action}。
    默认实时合成(并加性落库累积历史);best-effort,无真数据诚实返回空。
    history=true 时改读历史快照表(累积视图,只读),不触发新合成。
    纯只读 / 加性,绝不触 viltrox_fit_score。可选 kind 过滤(热点 / 竞品 / 机会 / 风险)。
    """
    from datetime import datetime, timezone

    from app.domains.market import market_observation

    if history:
        result = market_observation.list_observations_history(kind=kind, limit=limit)
        result["generated_at"] = datetime.now(timezone.utc).isoformat()
        return result

    result = market_observation.generate_observations(staff=admin if isinstance(admin, dict) else None)
    observations = result.get("observations") or []
    if kind:
        observations = [o for o in observations if isinstance(o, dict) and o.get("kind") == kind]
        result = {
            **result,
            "observations": observations,
            "count": len(observations),
        }
    result["generated_at"] = datetime.now(timezone.utc).isoformat()
    return result


@router.get("/intelligence/market/benchmarks")
def market_benchmarks(admin=Depends(require_admin)):
    return market_svc.list_benchmarks()


@router.get("/intelligence/market/gaps")
def market_gaps(admin=Depends(require_admin)):
    """AI-generated gap insights — reads from ai_insights cache."""
    return market_svc.list_gap_insights()


@router.post("/intelligence/market/gaps/generate")
async def generate_market_gaps(admin=Depends(require_admin)):
    """Force Claude-driven gap generation."""
    return await market_svc.regenerate_gap_insights()


# =========================================================================
# Brand
# =========================================================================

@router.get("/intelligence/brand/matrix")
def brand_matrix(admin=Depends(require_admin)):
    return brand_svc.get_matrix()


@router.get("/intelligence/brand/posts")
def brand_posts(
    account_handle: str | None = None,
    top_only: bool = False,
    limit: int = 50,
    admin=Depends(require_admin),
):
    return brand_svc.list_posts(
        account_handle=account_handle, top_only=top_only, limit=limit
    )


@router.get("/intelligence/brand/insights")
def brand_insights(admin=Depends(require_admin)):
    return brand_svc.list_insights()


@router.post("/intelligence/brand/insights/generate")
async def generate_brand_insights(admin=Depends(require_admin)):
    return await brand_svc.regenerate_insights()


@router.get("/intelligence/brand/voice")
def brand_voice(admin=Depends(require_admin)):
    return brand_svc.get_voice_guidelines()


@router.put("/intelligence/brand/voice")
def update_brand_voice(body: dict, admin=Depends(require_admin)):
    return brand_svc.update_voice_guidelines(body)


# =========================================================================
# Analytics
# =========================================================================

@router.get("/admin/analytics/trend")
def analytics_trend(
    metric: Literal["submissions", "gmv", "views", "score", "active_creators"] = "submissions",
    window_days: int = 30,
    admin=Depends(require_admin),
):
    return analytics_svc.trend_series(metric=metric, window_days=window_days)


@router.get("/admin/analytics/correlations")
def analytics_correlations(admin=Depends(require_admin)):
    return analytics_svc.get_correlations()


@router.get("/admin/analytics/pipeline")
def analytics_pipeline(window: Literal["7d", "30d"] = "7d", admin=Depends(require_admin)):
    return analytics_svc.pipeline_funnel(window=window)


@router.get("/admin/analytics/rejection-reasons")
def analytics_rejection_reasons(admin=Depends(require_admin)):
    return analytics_svc.rejection_reasons()


@router.get("/admin/analytics/series-performance")
def analytics_series(admin=Depends(require_admin)):
    return analytics_svc.product_series_performance()


@router.get("/admin/analytics/cohorts")
def analytics_cohorts(
    cohort_type: Literal["signup_month"] = "signup_month",
    admin=Depends(require_admin),
):
    return analytics_svc.cohort_retention(cohort_type=cohort_type)


@router.get("/admin/analytics/creator-rankings")
def analytics_creator_rankings(
    metric: Literal["combined", "submissions", "gmv", "score"] = "combined",
    limit: int = 50,
    admin=Depends(require_admin),
):
    return analytics_svc.creator_rankings(metric=metric, limit=limit)
