"""学习闭环状态 —— 让"系统在学什么"可见(只读,建立在 S3 的 agent_actions 之上)。

汇总三股学习信号:
- agent_actions:有价值动作沉淀(收藏/拒绝/加项目/复盘…按类计数)
- memory_feedback:反馈信号(action_executed / 关注收藏…按类计数)
- recommendation_outcomes:推荐漏斗(认领/达成/发布)
喂未来的学习仪表盘。红线:全程只读;零触 viltrox_fit_score / rule_v0。
"""
from __future__ import annotations

from typing import Any

from app.core.logging import get_logger
from app.db.connection import get_conn, table_exists

logger = get_logger(__name__)


def _count_by(table: str, col: str) -> dict[str, int]:
    if not table_exists(table):
        return {}
    try:
        rows = get_conn().execute(f"SELECT {col} AS k, COUNT(*) AS n FROM {table} GROUP BY {col}").fetchall()
        return {str(dict(r).get("k") or "?"): int(dict(r).get("n") or 0) for r in rows}
    except Exception:
        logger.debug("learning_signals.count_failed", extra={"table": table}, exc_info=True)
        return {}


def get_learning_status(staff: dict[str, Any] | None = None) -> dict[str, Any]:
    """学习闭环状态摘要(只读)。表缺则该块为空(诚实)。"""
    del staff
    actions = _count_by("vkpi_agent_actions", "action_kind")
    feedback = _count_by("vkpi_memory_feedback", "feedback_type")

    funnel: dict[str, Any] = {}
    if table_exists("vkpi_recommendation_outcomes"):
        try:
            row = get_conn().execute(
                "SELECT COUNT(*) AS total, "
                "COALESCE(SUM(CASE WHEN was_claimed THEN 1 ELSE 0 END),0) AS claimed, "
                "COALESCE(SUM(CASE WHEN agreement_reached THEN 1 ELSE 0 END),0) AS agreed, "
                "COALESCE(SUM(CASE WHEN content_published THEN 1 ELSE 0 END),0) AS published "
                "FROM vkpi_recommendation_outcomes"
            ).fetchone()
            funnel = {k: int(dict(row).get(k) or 0) for k in ("total", "claimed", "agreed", "published")} if row else {}
        except Exception:
            logger.debug("learning_signals.funnel_failed", exc_info=True)

    actions_total = sum(actions.values())
    feedback_total = sum(feedback.values())
    # 学习成熟度(粗):有沉淀动作 + 有反馈 + 有漏斗 → 越成熟。纯展示信号。
    maturity = "cold"
    if actions_total + feedback_total >= 50 and funnel.get("published", 0) > 0:
        maturity = "warming"
    if actions_total + feedback_total >= 200 and funnel.get("published", 0) >= 10:
        maturity = "learning"

    return {
        "status": "ok",
        "agent_actions": {"total": actions_total, "by_kind": actions},
        "memory_feedback": {"total": feedback_total, "by_type": feedback},
        "recommendation_funnel": funnel,
        "maturity": maturity,
        "note": "学习闭环只读摘要;maturity 为展示信号,绝不并入 viltrox_fit_score;权重回写留②后续。",
    }
