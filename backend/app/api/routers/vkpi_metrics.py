"""R13 · ROI/曝光/转化 metrics 只读端点。

GET /api/admin/vkpi/metrics/project/{project_id}  — 单项目聚合(scope 收口)。
GET /api/admin/vkpi/metrics/portfolio             — 可见项目组合级聚合。
红线:只读聚合;无 revenue 时诚实 awaiting_m5(非假 0);绝不触 viltrox_fit_score。
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Response

from app.api.dependencies.perms import require_tab
from app.domains.access import scope
from app.domains.metrics import aggregation

router = APIRouter(prefix="/api/admin/vkpi/metrics", tags=["vkpi-metrics"])


def _require_admin(staff: Any) -> None:
    """A2 防越权:公司级大盘/报告仅管理层可见(员工直接打端点也 403,纵深防御)。"""
    if not scope.can_view_all(staff):
        raise HTTPException(status_code=403, detail="仅管理层可见")


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


@router.get("/report-types")
def report_types(staff=Depends(require_tab("vkpi", "read"))) -> dict[str, Any]:
    """G2 · 可生成的 report 类型清单。"""
    from app.domains.dashboard import report_generator

    return {"types": report_generator.list_report_types()}


@router.get("/report")
def generate_report(
    report_type: str = Query(default="industry_overview"),
    window_days: int = Query(default=30, ge=1, le=365),
    staff=Depends(require_tab("vkpi", "read")),
) -> dict[str, Any]:
    """G2 · 数据导出:本地数据 → 结构化 report(前端可渲染/导出 PDF/Excel)。"""
    _require_admin(staff)
    from app.domains.dashboard import report_generator

    return report_generator.generate_report(report_type, staff=staff, window_days=int(window_days))


@router.get("/opportunities")
def opportunities(
    window_days: int = Query(default=30, ge=1, le=365),
    limit: int = Query(default=10, ge=1, le=20),
    staff=Depends(require_tab("vkpi", "read")),
) -> dict[str, Any]:
    """③ 预测 · 机会窗:竞品热区 + 渠道动能 → 该关注什么(仅管理层;无真销量诚实标 awaiting)。"""
    _require_admin(staff)
    from app.domains.market import prediction

    return prediction.predict_opportunities(window_days=int(window_days), limit=int(limit), staff=staff)


@router.get("/movers")
def temporal_movers(
    window_days: int = Query(default=30, ge=1, le=365),
    metric: str = Query(default="followers"),
    limit: int = Query(default=10, ge=1, le=50),
    staff=Depends(require_tab("vkpi", "read")),
) -> dict[str, Any]:
    """B4 时间索引:窗口内哪些账号在变好/变差(risers/fallers,仅管理层)。"""
    _require_admin(staff)
    from app.domains.market import temporal_movers as tm

    return tm.compute_movers(window_days=int(window_days), metric=str(metric), limit=int(limit))


@router.get("/report/export")
def export_report(
    report_type: str = Query(default="industry_overview"),
    format: str = Query(default="pdf"),
    window_days: int = Query(default=30, ge=1, le=365),
    staff=Depends(require_tab("vkpi", "read")),
) -> Response:
    """J2 · Report Builder:report → PDF/Excel 下载(仅管理层)。"""
    _require_admin(staff)
    from app.domains.dashboard import report_export

    out = report_export.build_report_file(report_type, fmt=str(format), window_days=int(window_days), staff=staff)
    if not out:
        raise HTTPException(status_code=400, detail="report render failed or unknown report_type")
    return Response(
        content=out["bytes"],
        media_type=out["content_type"],
        headers={"Content-Disposition": f'attachment; filename="{out["filename"]}"'},
    )


@router.get("/industry-board")
def industry_board(
    window_days: int = Query(default=30, ge=1, le=365),
    staff=Depends(require_tab("vkpi", "read")),
) -> dict[str, Any]:
    """G1 · 行业大盘:商业(组合ROI/GMV/订单)+ KOL(高价值/池规模)+ 市场(竞品/产品)。

    市场预估在真订单数据接入前诚实标 awaiting_data。只读;零触 viltrox_fit_score。
    """
    _require_admin(staff)
    from app.domains.market import industry_board as ib

    return ib.get_industry_board(staff, window_days=int(window_days))


@router.get("/high-value-kols")
def high_value_kols(
    limit: int = Query(default=10, ge=1, le=50),
    staff=Depends(require_tab("vkpi", "read")),
) -> dict[str, Any]:
    """高价值红人榜(按合作项目数 + ROI + 下次推荐权重排序;只读,喂个人工作台)。"""
    _require_admin(staff)
    from app.domains.kol import roi_aggregate

    return roi_aggregate.list_high_value_kols(limit=int(limit), staff=staff)


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
