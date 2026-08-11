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
from app.domains.market_brain.data_readiness import (
    outcome_evidence_sql,
    real_recommendation_feedback_sql,
    verified_prediction_binding_sql,
    verified_prediction_event_sql,
)

logger = get_logger(__name__)

_REAL_LEARNING_TARGETS = {
    "finalized_outcomes": 100,
    "human_feedback": 20,
    "prediction_actual_evals": 50,
    "human_reviewed_skill_runs": 100,
    "reviewed_skill_types": 4,
    "executed_agent_tool_runs": 20,
    "executed_tool_types": 3,
    "verified_action_tool_cases": 20,
}
_NONPRODUCTION_SKILL_MARKERS = (
    "%test%", "%demo%", "%synthetic%", "%fixture%", "%smoke%", "%dry_run%",
)


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
        "reviewed_skill_types": 2,
        "executed_agent_tool_runs": 1,
        "executed_tool_types": 1,
        "verified_action_tool_cases": 1,
    }
    if all(int(truth.get(key) or 0) >= target for key, target in warming_targets.items()):
        return "warming"
    return "cold"


def _verified_learning_evidence() -> dict[str, int]:
    """Count only human/actual/finalized evidence and explicitly reject test markers."""
    outreach_claimable = False
    if table_exists("vkpi_prediction_runs"):
        try:
            from app.domains.market_brain import outreach_truth_bridge

            outreach_claimable = bool(
                outreach_truth_bridge.outreach_prediction_coverage(get_conn()).get("claimable")
            )
        except Exception:
            logger.debug("learning_signals.outreach_coverage_failed", exc_info=True)
    finalized_evidence_sql = outcome_evidence_sql("o")
    finalized = _scalar(
        "vkpi_gtm_outcomes",
        f"""
        SELECT COUNT(DISTINCT o.action_inbox_id) AS n
        FROM vkpi_gtm_outcomes o
        WHERE o.decision <> 'open'
          AND o.decided_at IS NOT NULL
          AND o.decided_by IS NOT NULL
          AND o.action_inbox_id IS NOT NULL
          AND ({finalized_evidence_sql})
        """,
    )
    real_feedback_sql, real_feedback_params = real_recommendation_feedback_sql()
    feedback = _scalar(
        "vkpi_recommendation_feedback",
        f"""
        SELECT COUNT(DISTINCT recommendation_id) AS n
        FROM vkpi_recommendation_feedback
        WHERE {real_feedback_sql}
        """,
        real_feedback_params,
    )
    binding_sql = verified_prediction_binding_sql("e")
    verification_event_sql = verified_prediction_event_sql("e")
    evidence_sql = outcome_evidence_sql("o")
    actual_evals = _scalar(
        "vkpi_prediction_evals",
        f"""
        SELECT COUNT(DISTINCT e.outcome_id) AS n
        FROM vkpi_prediction_evals e
        JOIN vkpi_gtm_outcomes o ON o.id = e.outcome_id
        LEFT JOIN vkpi_prediction_runs pr
          ON pr.organization_id=e.organization_id AND pr.run_id=e.run_id
        WHERE e.actual_value IS NOT NULL
          AND LOWER(e.actual_value::text) NOT IN ('nan', 'infinity', '-infinity')
          AND {binding_sql}
          AND {verification_event_sql}
          AND e.error_abs IS NOT NULL
          AND LOWER(e.error_abs::text) NOT IN ('nan', 'infinity', '-infinity')
          AND o.decision <> 'open'
          AND o.decided_at IS NOT NULL
          AND o.decided_by IS NOT NULL
          AND ({evidence_sql})
          AND (
            COALESCE(pr.task_type, '') <> 'kol_outreach_reply_probability'
            OR {'TRUE' if outreach_claimable else 'FALSE'}
          )
        """,
    )
    reviewed_skills = _scalar(
        "vkpi_skill_runs",
        """
        SELECT COUNT(DISTINCT (
            sr.skill_name,
            ev.provenance_json->>'server_bound_input_sha256',
            ev.provenance_json->>'server_bound_output_sha256'
        )) AS n
        FROM vkpi_skill_runs sr
        JOIN vkpi_event_ledger ev
          ON ev.event_type = CASE
                WHEN sr.accepted = TRUE THEN 'skill_run_accepted'
                ELSE 'skill_run_rejected'
             END
         AND ev.entity_type = 'skill_run'
         AND ev.entity_id = CAST(sr.id AS TEXT)
         AND ev.actor_type = 'staff'
         AND ev.organization_id = 1
         AND ev.actor_id <> ''
         AND ev.source = 'skill_studio.human_review'
         AND ev.trace_id <> ''
         AND ev.provenance_json IS NOT NULL
         AND CAST(ev.provenance_json AS TEXT) NOT IN ('', '{}', 'null')
         AND COALESCE(ev.provenance_json->>'evidence_verification', '')
             = 'staff_attestation_bound_to_skill_run'
         AND COALESCE(ev.provenance_json->>'review_eligibility', '')
             = 'usable_production_output'
         AND COALESCE(ev.provenance_json->>'server_bound_input_sha256', '')
             ~ '^[0-9a-f]{64}$'
         AND COALESCE(ev.provenance_json->>'server_bound_output_sha256', '')
             ~ '^[0-9a-f]{64}$'
        WHERE sr.human_score IS NOT NULL
          AND sr.accepted IS NOT NULL
          AND LOWER(COALESCE(sr.skill_name,'')) NOT LIKE ?
          AND LOWER(COALESCE(sr.skill_name,'')) NOT LIKE ?
          AND LOWER(COALESCE(sr.business_result,'')) NOT LIKE ?
          AND LOWER(COALESCE(sr.business_result,'')) NOT LIKE ?
          AND LOWER(COALESCE(sr.business_result,'')) NOT LIKE ?
          AND LOWER(COALESCE(sr.business_result,'')) NOT LIKE ?
          AND LOWER(COALESCE(sr.business_result,'')) NOT LIKE ?
          AND LOWER(COALESCE(sr.business_result,'')) NOT LIKE ?
        """,
        ("test%", "%smoke%", *_NONPRODUCTION_SKILL_MARKERS),
    )
    tool_runs = _scalar(
        "vkpi_agent_tool_run",
        """
        SELECT COUNT(DISTINCT tr.id) AS n
        FROM vkpi_agent_tool_run tr
        JOIN vkpi_agent_orchestration_plan plan ON plan.id=tr.plan_id
        JOIN vkpi_action_inbox action
          ON action.id=CAST(tr.inputs_json->>'action_id' AS BIGINT)
         AND action.category='orchestrated_step'
         AND action.dedupe_key=(
              'plan:' || CAST(tr.plan_id AS TEXT) || ':step:' || CAST(tr.step_index AS TEXT)
         )
        JOIN vkpi_event_ledger ev
          ON ev.event_type = 'agent_tool_run_accepted'
         AND ev.entity_type = 'agent_tool_run'
         AND ev.entity_id = CAST(tr.id AS TEXT)
         AND ev.actor_type = 'staff'
         AND ev.organization_id = 1
         AND ev.actor_id <> ''
         AND ev.source = 'action_inbox.human_verification'
         AND ev.trace_id <> ''
         AND ev.provenance_json IS NOT NULL
         AND CAST(ev.provenance_json AS TEXT) NOT IN ('', '{}', 'null')
         AND COALESCE(ev.provenance_json->>'evidence_verification', '')
             = 'staff_attestation_bound_to_execution_ledger'
         AND COALESCE(ev.provenance_json->>'execution_effect', '')
             IN ('state_changed', 'external_confirmed')
        WHERE tr.status='executed' AND tr.executed_at IS NOT NULL
          AND tr.plan_id IS NOT NULL
          AND COALESCE(plan.plan_json->tr.step_index->>'tool_id','')=tr.tool_id
          AND COALESCE(tr.inputs_json->'step_inputs','{}'::jsonb)
              = COALESCE(plan.plan_json->tr.step_index->'inputs','{}'::jsonb)
          AND COALESCE(tr.inputs_json->>'contract_sha256','')
              = COALESCE(action.payload_json->>'contract_sha256','')
          AND COALESCE(tr.inputs_json->>'entity_type','')=action.entity_type
          AND COALESCE(tr.inputs_json->>'entity_id','')=action.entity_id
          AND COALESCE(tr.inputs_json->'affected_tables','[]'::jsonb)
              = COALESCE(plan.plan_json->tr.step_index->'affected_tables','[]'::jsonb)
          AND COALESCE(action.affected_tables_json,'[]'::jsonb)
              = COALESCE(plan.plan_json->tr.step_index->'affected_tables','[]'::jsonb)
          AND COALESCE(tr.inputs_json->>'execution_effect', '')
              = COALESCE(ev.provenance_json->>'execution_effect', '')
          AND COALESCE(tr.inputs_json->>'execution_ledger_id', '')
              = COALESCE(ev.provenance_json->>'execution_ledger_id', '')
        """,
    )
    skill_types = _scalar(
        "vkpi_skill_runs",
        """
        SELECT COUNT(DISTINCT sr.skill_name) AS n
        FROM vkpi_skill_runs sr
        JOIN vkpi_event_ledger ev
          ON ev.entity_type='skill_run' AND ev.entity_id=CAST(sr.id AS TEXT)
         AND ev.event_type = CASE
               WHEN sr.accepted = TRUE THEN 'skill_run_accepted'
               ELSE 'skill_run_rejected'
             END
         AND ev.actor_type='staff' AND ev.actor_id <> ''
         AND ev.organization_id=1
         AND ev.source='skill_studio.human_review' AND ev.trace_id <> ''
         AND ev.provenance_json IS NOT NULL
         AND CAST(ev.provenance_json AS TEXT) NOT IN ('', '{}', 'null')
         AND COALESCE(ev.provenance_json->>'evidence_verification', '')
             = 'staff_attestation_bound_to_skill_run'
         AND COALESCE(ev.provenance_json->>'review_eligibility', '')
             = 'usable_production_output'
         AND COALESCE(ev.provenance_json->>'server_bound_input_sha256', '')
             ~ '^[0-9a-f]{64}$'
         AND COALESCE(ev.provenance_json->>'server_bound_output_sha256', '')
             ~ '^[0-9a-f]{64}$'
        WHERE sr.accepted IS NOT NULL AND sr.human_score IS NOT NULL
          AND LOWER(COALESCE(sr.skill_name,'')) NOT LIKE ?
          AND LOWER(COALESCE(sr.skill_name,'')) NOT LIKE ?
          AND LOWER(COALESCE(sr.business_result,'')) NOT LIKE ?
          AND LOWER(COALESCE(sr.business_result,'')) NOT LIKE ?
          AND LOWER(COALESCE(sr.business_result,'')) NOT LIKE ?
          AND LOWER(COALESCE(sr.business_result,'')) NOT LIKE ?
          AND LOWER(COALESCE(sr.business_result,'')) NOT LIKE ?
          AND LOWER(COALESCE(sr.business_result,'')) NOT LIKE ?
        """,
        ("test%", "%smoke%", *_NONPRODUCTION_SKILL_MARKERS),
    )
    tool_types = _scalar(
        "vkpi_agent_tool_run",
        """
        SELECT COUNT(DISTINCT tr.tool_id) AS n
        FROM vkpi_agent_tool_run tr
        JOIN vkpi_agent_orchestration_plan plan ON plan.id=tr.plan_id
        JOIN vkpi_action_inbox action
          ON action.id=CAST(tr.inputs_json->>'action_id' AS BIGINT)
         AND action.category='orchestrated_step'
         AND action.dedupe_key=(
              'plan:' || CAST(tr.plan_id AS TEXT) || ':step:' || CAST(tr.step_index AS TEXT)
         )
        JOIN vkpi_event_ledger ev
          ON ev.entity_type='agent_tool_run' AND ev.entity_id=CAST(tr.id AS TEXT)
         AND ev.event_type='agent_tool_run_accepted'
         AND ev.actor_type='staff' AND ev.actor_id <> ''
         AND ev.organization_id=1
         AND ev.source='action_inbox.human_verification' AND ev.trace_id <> ''
         AND ev.provenance_json IS NOT NULL
         AND CAST(ev.provenance_json AS TEXT) NOT IN ('', '{}', 'null')
         AND COALESCE(ev.provenance_json->>'evidence_verification', '')
             = 'staff_attestation_bound_to_execution_ledger'
         AND COALESCE(ev.provenance_json->>'execution_effect', '')
             IN ('state_changed', 'external_confirmed')
        WHERE tr.status='executed' AND tr.executed_at IS NOT NULL
          AND tr.plan_id IS NOT NULL
          AND COALESCE(plan.plan_json->tr.step_index->>'tool_id','')=tr.tool_id
          AND COALESCE(tr.inputs_json->'step_inputs','{}'::jsonb)
              = COALESCE(plan.plan_json->tr.step_index->'inputs','{}'::jsonb)
          AND COALESCE(tr.inputs_json->>'contract_sha256','')
              = COALESCE(action.payload_json->>'contract_sha256','')
          AND COALESCE(tr.inputs_json->>'entity_type','')=action.entity_type
          AND COALESCE(tr.inputs_json->>'entity_id','')=action.entity_id
          AND COALESCE(tr.inputs_json->'affected_tables','[]'::jsonb)
              = COALESCE(plan.plan_json->tr.step_index->'affected_tables','[]'::jsonb)
          AND COALESCE(action.affected_tables_json,'[]'::jsonb)
              = COALESCE(plan.plan_json->tr.step_index->'affected_tables','[]'::jsonb)
          AND COALESCE(tr.inputs_json->>'execution_effect', '')
              = COALESCE(ev.provenance_json->>'execution_effect', '')
          AND COALESCE(tr.inputs_json->>'execution_ledger_id', '')
              = COALESCE(ev.provenance_json->>'execution_ledger_id', '')
        """,
    )
    verified_cases = _scalar(
        "vkpi_event_ledger",
        """
        SELECT COUNT(DISTINCT action_ev.entity_id) AS n
        FROM vkpi_event_ledger action_ev
        JOIN vkpi_event_ledger tool_ev
         ON tool_ev.trace_id=action_ev.trace_id
         AND tool_ev.event_type='agent_tool_run_accepted'
         AND tool_ev.source='action_inbox.human_verification'
         AND tool_ev.actor_type='staff' AND tool_ev.actor_id <> ''
         AND tool_ev.provenance_json IS NOT NULL
         AND CAST(tool_ev.provenance_json AS TEXT) NOT IN ('', '{}', 'null')
         AND COALESCE(tool_ev.provenance_json->>'evidence_verification', '')
             = 'staff_attestation_bound_to_execution_ledger'
         AND COALESCE(tool_ev.provenance_json->>'execution_effect', '')
             IN ('state_changed', 'external_confirmed')
        JOIN vkpi_agent_tool_run tr
          ON CAST(tr.id AS TEXT)=tool_ev.entity_id
         AND tr.status='executed'
         AND COALESCE(tr.inputs_json->>'action_id','')=action_ev.entity_id
         AND COALESCE(tr.inputs_json->>'execution_ledger_id','')
             = COALESCE(tool_ev.provenance_json->>'execution_ledger_id','')
         AND COALESCE(tr.inputs_json->>'execution_effect','')
             = COALESCE(tool_ev.provenance_json->>'execution_effect','')
        JOIN vkpi_agent_orchestration_plan plan ON plan.id=tr.plan_id
        JOIN vkpi_action_inbox action
          ON action.id=CAST(tr.inputs_json->>'action_id' AS BIGINT)
         AND action.category='orchestrated_step'
         AND action.dedupe_key=(
              'plan:' || CAST(tr.plan_id AS TEXT) || ':step:' || CAST(tr.step_index AS TEXT)
         )
        WHERE action_ev.event_type='action_result_accepted'
          AND action_ev.entity_type='action'
          AND action_ev.actor_type='staff'
          AND action_ev.organization_id=1
          AND tool_ev.organization_id=1
          AND action_ev.actor_id <> ''
          AND action_ev.source='action_inbox.human_verification'
          AND action_ev.trace_id <> ''
          AND action_ev.provenance_json IS NOT NULL
          AND CAST(action_ev.provenance_json AS TEXT) NOT IN ('', '{}', 'null')
          AND COALESCE(action_ev.provenance_json->>'evidence_verification', '')
              = 'staff_attestation_bound_to_execution_ledger'
          AND COALESCE(action_ev.provenance_json->>'execution_ledger_id','')
              = COALESCE(tool_ev.provenance_json->>'execution_ledger_id','')
          AND COALESCE(action_ev.provenance_json->>'execution_effect','')
              = COALESCE(tool_ev.provenance_json->>'execution_effect','')
          AND tr.plan_id IS NOT NULL
          AND COALESCE(plan.plan_json->tr.step_index->>'tool_id','')=tr.tool_id
          AND COALESCE(tr.inputs_json->'step_inputs','{}'::jsonb)
              = COALESCE(plan.plan_json->tr.step_index->'inputs','{}'::jsonb)
          AND COALESCE(tr.inputs_json->>'contract_sha256','')
              = COALESCE(action.payload_json->>'contract_sha256','')
          AND COALESCE(tr.inputs_json->>'entity_type','')=action.entity_type
          AND COALESCE(tr.inputs_json->>'entity_id','')=action.entity_id
          AND COALESCE(tr.inputs_json->'affected_tables','[]'::jsonb)
              = COALESCE(plan.plan_json->tr.step_index->'affected_tables','[]'::jsonb)
          AND COALESCE(action.affected_tables_json,'[]'::jsonb)
              = COALESCE(plan.plan_json->tr.step_index->'affected_tables','[]'::jsonb)
        """,
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
        "reviewed_skill_types": skill_types,
        "executed_agent_tool_runs": tool_runs,
        "executed_tool_types": tool_types,
        "verified_action_tool_cases": verified_cases,
        "linked_agent_outcomes": linked_outcomes,
        "outreach_prediction_claimable": int(outreach_claimable),
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
