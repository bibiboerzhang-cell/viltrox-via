"""V-KPI 夜班经理晨报路由(件 B)。

- GET /api/admin/vkpi/morning-brief?window_hours=16
  → 「昨晚我做了 X 件」:窗口内(默认昨天 16:00 → 今晨 8:00 本地锚点)系统完成的
  抓取/深析/评论/新入池/告警/预算/异常,全部纯 SQL 聚合已有数据,零 LLM、零写库。
  实现在 app.domains.agents.night_shift(按需计算,本轮不建 cron;
  将来每日 job 直接挂 night_shift.morning_brief)。

诚实态:每段缺数据由 domain 层返回 {status:"empty", reason};聚合内部异常不 500,
回 {status:"error", reason}(前端安静降级,增益卡非阻塞)。
红线:纯读展示,零外部动作;零触 viltrox_fit_score、不碰 rule_v0。
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, Query

from app.api.dependencies.perms import require_tab
from app.core.logging import get_logger

logger = get_logger(__name__)

router = APIRouter(prefix="/api/admin/vkpi", tags=["vkpi-morning-brief"])


@router.get("/morning-brief")
def get_morning_brief(
    window_hours: int = Query(default=16, ge=1, le=168, description="回看窗口小时数(锚点=最近一次本地 08:00)"),
    staff=Depends(require_tab("vkpi", "read")),
) -> dict:
    """夜班经理晨报:headline + sections(每段真实计数与例证,零数据段诚实 0)。"""
    del staff
    from app.domains.agents import night_shift

    try:
        return night_shift.morning_brief(window_hours=int(window_hours))
    except Exception as exc:  # noqa: BLE001 — 增益卡失败不炸接口,诚实回原因
        logger.warning("morning_brief failed window_hours=%s: %s", window_hours, exc)
        return {"status": "error", "reason": str(exc)[:300], "window_hours": int(window_hours)}
