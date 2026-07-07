"""G2 · gifted→posted 履约漏斗路由(件④)。

- GET  /api/admin/vkpi/gifted/funnel
  → 全池 送样→发布 漏斗:已送样 n / 已发布 m / 观察中 w / 超期未发 k(>N 天,默认 21)
  + 超期红名单(带天数与建议动作「催更」)。实现在 app.domains.projects.gifted_funnel(全只读)。
- POST /api/admin/vkpi/gifted/funnel/catch-up?dry_run=true
  → 把超期条目写成 Action Inbox「催更」建议;dry_run=true(默认)只预览绝不落库;
  dry_run=false 幂等落 vkpi_action_inbox(系统自身台账,仍需人工审批)。

红线:GET 纯读;POST 只写 Action Inbox 台账,绝不改派单/项目/费用状态,绝不真发外联;
零触 viltrox_fit_score、不碰 rule_v0。诚实态:空段回 0 + reason,聚合异常不 500。
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, Query

from app.api.dependencies.perms import require_tab
from app.core.logging import get_logger

logger = get_logger(__name__)

router = APIRouter(prefix="/api/admin/vkpi/gifted", tags=["vkpi-gifted-funnel"])


@router.get("/funnel")
def get_gifted_funnel(
    overdue_days: int = Query(default=21, ge=1, le=365),
    staff=Depends(require_tab("vkpi", "read")),
) -> dict:
    """送样→发布漏斗(全只读,不写库);空段诚实 0。"""
    del staff
    from app.domains.projects import gifted_funnel

    try:
        return gifted_funnel.funnel(overdue_days=int(overdue_days))
    except Exception as exc:  # noqa: BLE001 — 对账板失败不炸接口,诚实回原因
        logger.warning("gifted_funnel failed: %s", exc)
        return {"status": "error", "reason": str(exc)[:300]}


@router.post("/funnel/catch-up")
def post_gifted_catch_up(
    dry_run: bool = Query(default=True),
    overdue_days: int = Query(default=21, ge=1, le=365),
    limit: int = Query(default=50, ge=1, le=200),
    staff=Depends(require_tab("vkpi", "write")),
) -> dict:
    """超期条目 → Action Inbox「催更」建议;dry_run=true 只预览(默认),false 才幂等落台账。"""
    del staff
    from app.domains.projects import gifted_funnel

    try:
        return gifted_funnel.catch_up_actions(
            dry_run=bool(dry_run), overdue_days=int(overdue_days), limit=int(limit)
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("gifted_catch_up failed: %s", exc)
        return {"ok": False, "dry_run": bool(dry_run), "reason": str(exc)[:300]}
