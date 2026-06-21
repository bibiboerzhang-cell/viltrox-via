"""R13 · ROI/曝光/转化 metrics 只读端点。

GET /api/admin/vkpi/metrics/project/{project_id}  — 单项目聚合(scope 收口)。
GET /api/admin/vkpi/metrics/portfolio             — 可见项目组合级聚合。
红线:只读聚合;无 revenue 时诚实 awaiting_m5(非假 0);绝不触 viltrox_fit_score。
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Query

from app.api.dependencies.perms import require_tab
from app.domains.metrics import aggregation

router = APIRouter(prefix="/api/admin/vkpi/metrics", tags=["vkpi-metrics"])


@router.get("/project/{project_id}")
def project_metrics(
    project_id: int,
    window_days: int = Query(default=30, ge=1, le=365),
    staff=Depends(require_tab("vkpi", "read")),
) -> dict[str, Any]:
    return aggregation.aggregate_project_metrics(project_id, window_days=window_days, staff=staff)


@router.get("/portfolio")
def portfolio_metrics(
    window_days: int = Query(default=30, ge=1, le=365),
    staff=Depends(require_tab("vkpi", "read")),
) -> dict[str, Any]:
    return aggregation.aggregate_portfolio_metrics(window_days=window_days, staff=staff)


@router.get("/kol/{kol_pool_id}")
def kol_roi_metrics(
    kol_pool_id: int,
    staff=Depends(require_tab("vkpi", "read")),
) -> dict[str, Any]:
    """R16 · 单 KOL 的 ROI 汇总 + 下次推荐权重(只读展示信号,绝不并入 viltrox_fit_score)。"""
    from app.domains.kol import roi_aggregate

    return {
        "kol_pool_id": int(kol_pool_id),
        "roi_summary": roi_aggregate.get_kol_roi_summary(kol_pool_id, staff=staff),
        "next_recommendation_weight": roi_aggregate.compute_next_recommendation_weight(kol_pool_id),
    }
