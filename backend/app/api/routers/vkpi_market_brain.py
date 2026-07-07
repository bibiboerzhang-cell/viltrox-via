"""V-KPI GTM 总脑路由(GTM-1,纯读)。

- GET /api/admin/vkpi/market-brain/gtm-plan/preview
  → 输入 SKU+国家+预算+目标+窗口,返回 11 段作战建议(public_plan)+ meta。
  实现在 app.domains.market_brain.gtm_plan_preview(纯读聚合,零写库零 LLM 零采集)。

诚实态:SKU 不存在 404;goal 非法 422;每段缺数据由 domain 层返回
{status:"empty"/"data_missing", reason};聚合内部异常不 500,回 {status:"error"}。
红线:显示层宪法——响应只出 public_plan,private_evidence 绝不返回
(?debug=1+owner 分支 v1 不实现);纯读展示,零触 viltrox_fit_score、不碰 rule_v0;
绝不复用带副作用的 GET(marketing-brain/daily 与 market/trends 一律不碰)。
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query

from app.api.dependencies.perms import require_tab
from app.core.logging import get_logger

logger = get_logger(__name__)

router = APIRouter(prefix="/api/admin/vkpi", tags=["vkpi-market-brain"])


@router.get("/market-brain/gtm-plan/preview")
def get_gtm_plan_preview(
    sku: str = Query(..., min_length=1, max_length=120, description="必填 SKU 码(vkpi_products 口径)"),
    country: str | None = Query(None, max_length=8, description="ISO 国家码;v1 只影响候选排序与 Dealer 占位说明"),
    budget_usd: float = Query(3000, gt=0, le=1_000_000, description="预算(USD),默认 3000"),
    goal: str = Query("exposure", description="exposure|conversion|content|channel"),
    window_days: int = Query(30, ge=7, le=90, description="预判窗口天数,默认 30"),
    staff=Depends(require_tab("vkpi", "read")),
) -> dict:
    """GTM Plan 纯读预览:11 段建议 + meta(零写库零 LLM 零采集,同输入同输出)。"""
    del staff
    from app.domains.market_brain import gtm_plan_preview

    goal_clean = (goal or "").strip().lower()
    if goal_clean not in gtm_plan_preview.GOALS:
        raise HTTPException(
            status_code=422,
            detail=f"goal must be one of {list(gtm_plan_preview.GOALS)}, got {goal!r}",
        )
    try:
        return gtm_plan_preview.build_preview(
            sku=sku,
            country=country,
            budget_usd=budget_usd,
            goal=goal_clean,
            window_days=window_days,
        )
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except Exception as exc:  # noqa: BLE001 — 聚合失败不 500 裸奔,诚实回原因
        logger.warning("gtm_plan_preview failed for sku=%s: %s", sku, exc)
        return {"status": "error", "reason": str(exc)[:300], "sku": str(sku)[:120]}
