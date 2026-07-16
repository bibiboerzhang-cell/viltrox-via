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

_REAL_LEARNING_TARGETS = {
    "finalized_outcomes": 100,
    "human_feedback": 20,
    "prediction_actual_evals": 50,
    "human_reviewed_skill_runs": 100,
    "executed_agent_tool_runs": 20,
}


def _count_by(table: str, col: str) -> dict[str, int]:
    if not table_exists(table):
        return {}
    try:
        rows = get_conn().execute(f"SELECT {col} AS k, COUNT(*) AS n FROM {table} GROUP BY {col}").fetchall()
        return {str(dict(r).get("k") or "?"): int(dict(r).get("n") or 0) for r in rows}
    except Exception:
        logger.debug("learning_signals.count_failed", extra={"table": table}, exc_info=True)
        return {}


def _scalar(table: str, sql: str, params: tuple[Any, ...] = ()) -> int:
    if not table_exists(table):
        return 0
    try:
        row = get_conn().execute(sql, params).fetchone()
        return int(dict(row).get("n") or 0) if row else 0
    except Exception:
        logger.debug("learning_signals.scalar_failed", extra={"table": table}, exc_info=True)
        return 0


def _maturity_from_truth(truth: dict[str, int]) -> str:
    """Evidence-gated maturity; raw/test/demo rows never raise the level."""
    if all(int(truth.get(key) or 0) >= target for key, target in _REAL_LEARNING_TARGETS.items()):
        return "learning"
    warming_targets = {
        "finalized_outcomes": 5,
        "human_feedback": 5,
        "prediction_actual_evals": 5,
        "human_reviewed_skill_runs": 5,
        "executed_agent_tool_runs": 1,
    }
    if all(int(truth.get(key) or 0) >= target for key, target in warming_targets.items()):
        return "warming"
    return "cold"


def _verified_learning_evidence() -> dict[str, int]:
    """Count only human/actual/finalized evidence and explicitly reject test markers."""
    finalized = _scalar(
        "vkpi_recommendation_outcomes",
        "SELECT COUNT(*) AS n FROM vkpi_recommendation_outcomes WHERE outcome_finalized_at IS NOT NULL",
    )
    feedback = _scalar(
        "vkpi_recommendation_feedback",
        """
        SELECT COUNT(*) AS n
        FROM vkpi_recommendation_feedback
        WHERE created_by_staff_id IS NOT NULL
          AND LOWER(COALESCE(CAST(metadata_json AS TEXT),'')) NOT LIKE ?
          AND LOWER(COALESCE(CAST(metadata_json AS TEXT),'')) NOT LIKE ?
          AND LOWER(COALESCE(CAST(metadata_json AS TEXT),'')) NOT LIKE ?
          AND LOWER(COALESCE(CAST(metadata_json AS TEXT),'')) NOT LIKE ?
        """,
        ("%test%", "%demo%", "%smoke%", "%dry_run%"),
    )
    actual_evals = _scalar(
        "vkpi_prediction_evals",
        "SELECT COUNT(*) AS n FROM vkpi_prediction_evals WHERE actual_value IS NOT NULL",
    )
    reviewed_skills = _scalar(
        "vkpi_skill_runs",
        """
        SELECT COUNT(*) AS n
        FROM vkpi_skill_runs
        WHERE (human_score IS NOT NULL OR accepted IS NOT NULL)
          AND LOWER(COALESCE(skill_name,'')) NOT LIKE ?
          AND LOWER(COALESCE(skill_name,'')) NOT LIKE ?
          AND LOWER(COALESCE(business_result,'')) NOT IN (?,?,?,?,?)
        """,
        ("test%", "%smoke%", "pytest", "test", "demo", "dry_run", "smoke"),
    )
    tool_runs = _scalar(
        "vkpi_agent_tool_run",
        "SELECT COUNT(*) AS n FROM vkpi_agent_tool_run WHERE status='executed' AND executed_at IS NOT NULL",
    )
    linked_outcomes = _scalar(
        "vkpi_agent_outcome_evaluations",
        "SELECT COUNT(*) AS n FROM vkpi_agent_outcome_evaluations WHERE agent_action_id IS NOT NULL AND success IS NOT NULL",
    )
    return {
        "finalized_outcomes": finalized,
        "human_feedback": feedback,
        "prediction_actual_evals": actual_evals,
        "human_reviewed_skill_runs": reviewed_skills,
        "executed_agent_tool_runs": tool_runs,
        "linked_agent_outcomes": linked_outcomes,
    }


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
    truth = _verified_learning_evidence()
    maturity = _maturity_from_truth(truth)

    raw_skill_runs = _scalar("vkpi_skill_runs", "SELECT COUNT(*) AS n FROM vkpi_skill_runs")
    raw_agent_evals = _scalar(
        "vkpi_agent_outcome_evaluations",
        "SELECT COUNT(*) AS n FROM vkpi_agent_outcome_evaluations",
    )

    return {
        "status": "ok",
        "agent_actions": {"total": actions_total, "by_kind": actions},
        "memory_feedback": {"total": feedback_total, "by_type": feedback},
        "recommendation_funnel": funnel,
        "verified_evidence": truth,
        "targets_4_5": dict(_REAL_LEARNING_TARGETS),
        "excluded_raw_activity": {
            "skill_runs_total": raw_skill_runs,
            "skill_runs_without_qualifying_human_review": max(0, raw_skill_runs - truth["human_reviewed_skill_runs"]),
            "agent_outcome_evaluations_total": raw_agent_evals,
            "agent_outcomes_without_linked_action_result": max(0, raw_agent_evals - truth["linked_agent_outcomes"]),
        },
        "maturity": maturity,
        "claim_status": "descriptive_only",
        "note": "成熟度只看 finalized outcome、非演示人工反馈、actual eval、人工复核 Skill 与真实 tool run；原始/test/demo/ack 行仅作运营流水，绝不并入学习成熟度或 viltrox_fit_score。",
    }
