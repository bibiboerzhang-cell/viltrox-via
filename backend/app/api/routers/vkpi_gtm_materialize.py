"""V-KPI GTM Plan bet materialize 路由(GTM-Loop L1)。

- POST /api/admin/vkpi/market-brain/gtm-plan/materialize
  body: {sku, country?, budget_usd?, goal?, window_days?, dry_run}
  实现在 app.domains.market_brain.materialize(调 build_preview 拿 bet 条目,
  dry_run=False 时经 producers.make_suggestion + inbox.persist_suggestions 落
  vkpi_action_inbox,幂等:同 plan 同 action 不重插)。

人审红线:落库的每条 bet requires_approval=True;裁决(verdict)只走人工 POST
(L2 端点),本路由与其 domain 层无任何自动裁决路径。dry_run 默认 True——
真落库必须显式 dry_run=false,写权限(vkpi:write)才可调。

诚实态:SKU 不存在 404;goal/budget 非法 422;内部异常不 500 裸奔,回
{status:"error", reason}。红线:零触 viltrox_fit_score、不碰 rule_v0。
"""
from __future__ import annotations

from fastapi import APIRouter, Body, Depends, HTTPException

from app.api.dependencies.manager_guard import require_manager_staff
from app.api.dependencies.perms import require_tab
from app.core.logging import get_logger

logger = get_logger(__name__)

router = APIRouter(prefix="/api/admin/vkpi", tags=["vkpi-gtm-materialize"])


@router.post("/market-brain/gtm-plan/materialize")
def post_gtm_plan_materialize(
    sku: str = Body(..., min_length=1, max_length=120, description="必填 SKU 码(vkpi_products 口径)"),
    country: str | None = Body(None, max_length=8, description="ISO 国家码(与 preview 同口径)"),
    budget_usd: float = Body(3000, gt=0, le=1_000_000, description="预算(USD),默认 3000"),
    goal: str = Body("exposure", description="exposure|conversion|content|channel"),
    window_days: int = Body(30, ge=7, le=90, description="预判窗口天数,默认 30"),
    dry_run: bool = Body(True, description="默认 True 只出 bet 预览零落库;显式 false 才真落库"),
    staff=Depends(require_tab("vkpi", "write")),
) -> dict:
    """GTM Plan → bet 落库(dry_run 双态;每条 requires_approval=True 走人审)。

    条件管理层闸:dry_run=True 员工可模拟(零落库);dry_run=False 真落库
    仅 owner/管理岗 → 否则 403。
    """
    from app.domains.market_brain import gtm_plan_preview, materialize

    if not dry_run:
        require_manager_staff(staff)
    goal_clean = (goal or "").strip().lower()
    if goal_clean not in gtm_plan_preview.GOALS:
        raise HTTPException(
            status_code=422,
            detail=f"goal must be one of {list(gtm_plan_preview.GOALS)}, got {goal!r}",
        )
    actor_staff_id = None
    try:
        actor_staff_id = int((staff or {}).get("id") or 0) or None
    except (TypeError, ValueError):
        actor_staff_id = None
    try:
        return materialize.materialize_plan(
            sku=sku,
            country=country,
            budget_usd=budget_usd,
            goal=goal_clean,
            dry_run=bool(dry_run),
            window_days=window_days,
            actor_staff_id=actor_staff_id,
        )
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except Exception as exc:  # noqa: BLE001 — 聚合/落库失败不 500 裸奔,诚实回原因
        logger.warning("gtm_plan_materialize failed for sku=%s: %s", sku, exc)
        return {"status": "error", "reason": str(exc)[:300], "sku": str(sku)[:120], "dry_run": bool(dry_run)}
