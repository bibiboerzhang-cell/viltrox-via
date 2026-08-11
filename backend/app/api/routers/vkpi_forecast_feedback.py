"""V-KPI 预测流水对答案路由(B3 学习闭环通电 · 刀1)。

- POST /api/admin/vkpi/learning/forecast-outcomes/refresh
  → 对满窗(缺省 30 天)仍 pending 的 vkpi_forecast_log 行回查该 KOL 窗口内
  实际播放中位数,填 actual_views/actual_at 并判 outcome(hit_in_band/below/above)。
  手动触发入口;每日 job 后续直调 learning.forecast_feedback.refresh_forecast_outcomes。
- GET /api/admin/vkpi/learning/forecast-log/summary
  → 预测流水总览:按 outcome 分布 + 已判行带内率 + 按 context/method 分桶 + 最近样本。

诚实态:表未建(迁移215未 apply)domain 层回 {status:"empty", reason};
内部异常不 500,回 {status:"error", reason}(学习闭环增益件非阻塞)。
红线:唯一写入是流水表 actual/outcome 明确列;零触 fit 评分列、不碰 rule_v0、零 LLM。
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query

from app.api.dependencies.legacy_scope import legacy_system_admin_scope_guard
from app.api.dependencies.manager_guard import require_manager_tab
from app.api.dependencies.perms import require_tab
from app.core.logging import get_logger

logger = get_logger(__name__)

router = APIRouter(prefix="/api/admin/vkpi", tags=["vkpi-forecast-feedback"])


def _require_legacy_forecast_scope(staff: dict[str, Any], *, surface: str) -> None:
    unavailable = legacy_system_admin_scope_guard(staff, surface=surface)
    if unavailable is not None:
        raise HTTPException(status_code=403, detail=unavailable)


@router.post("/learning/forecast-outcomes/refresh")
def refresh_forecast_outcomes(
    min_age_days: int = Query(default=30, ge=0, le=365, description="满多少天才对答案(冒烟/回补可传 0)"),
    limit: int = Query(default=200, ge=1, le=1000, description="单次最多处理的 pending 行数"),
    staff=Depends(require_manager_tab("vkpi", "write")),
) -> dict:
    """手动触发预测流水对答案(只写 vkpi_forecast_log 的 actual/outcome 列)。"""
    _require_legacy_forecast_scope(staff, surface="Forecast outcome refresh")
    from app.domains.learning import forecast_feedback

    return forecast_feedback.refresh_forecast_outcomes(min_age_days=min_age_days, limit=limit)


@router.get("/learning/forecast-log/summary")
def get_forecast_log_summary(
    recent_limit: int = Query(default=10, ge=0, le=50, description="附带最近流水行数"),
    staff=Depends(require_tab("vkpi", "read")),
) -> dict:
    """预测流水总览(带内率统计;全只读)。"""
    _require_legacy_forecast_scope(staff, surface="Forecast log summary")
    from app.domains.learning import forecast_feedback

    return forecast_feedback.forecast_log_summary(recent_limit=recent_limit)
