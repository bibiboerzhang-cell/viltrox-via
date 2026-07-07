"""闭环波 L3 · GTM 三窗对答案 + 周报路由。

- POST /api/admin/vkpi/gtm/windows/refresh?dry_run=false
  → 对 vkpi_gtm_outcomes 未裁决行按账龄回填三窗(7d 执行/14d 内容/28d 商业);
    dry_run=true 只算不写。实现在 app.domains.market_brain.gtm_windows
    (每日 job 同函数,收口挂 scheduler 由主循环定)。
- GET  /api/admin/vkpi/gtm/weekly-answers?days=7
  → 周对答案报告:分组胜率(decision 口径,样本<5 标 insufficient)+
    对了什么/错了什么/下次怎么改 三段 + headline。实现在
    app.domains.market_brain.weekly_answers(纯读)。

红线:POST 只写 window_7d/14d/28d 三列(素材回填),绝无自动裁决路径——decision/lesson
只归 L2 人工裁决端点;GET 纯读。表未建(L2 迁移 217 并行)诚实空态,聚合异常不 500;
零触 viltrox_fit_score、不碰 rule_v0。
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, Query

from app.api.dependencies.perms import require_tab
from app.core.logging import get_logger

logger = get_logger(__name__)

router = APIRouter(prefix="/api/admin/vkpi/gtm", tags=["vkpi-gtm-windows"])


@router.post("/windows/refresh")
def post_gtm_windows_refresh(
    dry_run: bool = Query(default=False),
    limit: int = Query(default=500, ge=1, le=2000),
    staff=Depends(require_tab("vkpi", "write")),
) -> dict:
    """三窗自动回填(job 同款函数):窗到期才填,只写 window_* 列,绝不触 decision。"""
    del staff
    from app.domains.market_brain import gtm_windows

    try:
        return gtm_windows.refresh_gtm_windows(dry_run=bool(dry_run), limit=int(limit))
    except Exception as exc:  # noqa: BLE001 — 回填失败不炸接口,诚实回原因
        logger.warning("gtm_windows_refresh failed: %s", exc)
        return {"status": "error", "dry_run": bool(dry_run), "reason": str(exc)[:300]}


@router.get("/weekly-answers")
def get_gtm_weekly_answers(
    days: int = Query(default=7, ge=0, le=90),
    staff=Depends(require_tab("vkpi", "read")),
) -> dict:
    """周对答案报告(纯读):对了什么/错了什么/下次怎么改 + 分组胜率;days=0 全量。"""
    del staff
    from app.domains.market_brain import weekly_answers

    try:
        return weekly_answers.weekly_report(days=int(days))
    except Exception as exc:  # noqa: BLE001
        logger.warning("gtm_weekly_answers failed: %s", exc)
        return {"status": "error", "reason": str(exc)[:300]}
