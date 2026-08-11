"""Marketing Brain scorecard.

This read-only scorecard turns the "90+ AI Marketing Brain" target into a
measurable contract. It does not judge UI polish; it checks whether the backend
has the ingredients a recommendation AI needs: evidence, durable workflows,
action contracts, learning feedback, market intelligence, and eval governance.
"""
from __future__ import annotations

import json
from typing import Any

from app.db.connection import get_conn, table_exists
from app.domains.intelligence.marketing_brain_activity_evidence import (
    activity_evidence_contracts as _activity_evidence_contracts,
    server_bound_event_sql as _server_bound_event_sql,
)
from app.domains.intelligence.raw_market_source import latest_raw_market_source_observation
from app.domains.market_brain.data_readiness import (
    DataRequirement,
    build_learning_readiness,
    evaluate_requirements,
    outcome_evidence_sql,
    verified_prediction_binding_sql,
    verified_prediction_event_sql,
)

SCORECARD_VERSION = "marketing_brain_scorecard_v3_observed_evidence"


def _loads(value: Any, fallback: Any) -> Any:
    if value in (None, ""):
        return fallback
    if isinstance(value, (dict, list)):
        return value
    try:
        return json.loads(value)
    except Exception:
        return fallback


def _count(table: str, where: str = "", params: tuple[Any, ...] = ()) -> int | None:
    if not table_exists(table):
        return None
    sql = f"SELECT COUNT(*) AS n FROM {table}"
    if where:
        sql += f" WHERE {where}"
    try:
        row = get_conn().execute(sql, params).fetchone()
        return int(dict(row or {}).get("n") or 0)
    except Exception:
        return None


def _distinct_count(table: str, field: str, where: str = "", params: tuple[Any, ...] = ()) -> int | None:
    if not table_exists(table):
        return None
    sql = f"SELECT COUNT(DISTINCT {field}) AS n FROM {table}"
    if where:
        sql += f" WHERE {where}"
    try:
        row = get_conn().execute(sql, params).fetchone()
        return int(dict(row or {}).get("n") or 0)
    except Exception:
        return None


def _latest_value(
    table: str,
    expression: str,
    where: str = "",
    params: tuple[Any, ...] = (),
) -> Any:
    if not table_exists(table):
        return None
    sql = f"SELECT MAX({expression}) AS value FROM {table}"
    if where:
        sql += f" WHERE {where}"
    try:
        row = get_conn().execute(sql, params).fetchone()
        return dict(row or {}).get("value")
    except Exception:
        return None


def _clamp(value: float) -> float:
    return max(0.0, min(1.0, float(value)))


def _coverage(numerator: int, denominator: int) -> float:
    if denominator <= 0:
        return 0.0
    return _clamp(numerator / denominator)


_WINDOW_DAYS = 7  # 行为验收窗口:近 7 天"真在运营"


def _recent_count(table: str, ts_col: str = "created_at", *, days: int = _WINDOW_DAYS,
                  where: str = "", params: tuple[Any, ...] = ()) -> int:
    """近 N 天行数(行为验收核心:不是'曾经存在'而是'最近真在跑')。列缺失/出错 → 0。"""
    if not table_exists(table):
        return 0
    clause = f"{ts_col} >= NOW() - INTERVAL '{int(days)} days'"
    if where:
        clause = f"({where}) AND {clause}"
    n = _count(table, clause, params)
    return int(n or 0)


def _recent_distinct_count(
    table: str,
    field: str,
    ts_col: str = "created_at",
    *,
    days: int = _WINDOW_DAYS,
    where: str = "",
    params: tuple[Any, ...] = (),
) -> int:
    """Count recent stable evidence units rather than repeat invocations."""
    if not table_exists(table):
        return 0
    clause = f"{ts_col} >= NOW() - INTERVAL '{int(days)} days'"
    if where:
        clause = f"({where}) AND {clause}"
    n = _distinct_count(table, field, clause, params)
    return int(n or 0)


def _ramp(value: float, target: float) -> float:
    """线性爬坡评分:value 达到 target 给满分,之下按比例(行为门槛,非二元存在性)。"""
    if target <= 0:
        return 0.0
    return _clamp(float(value) / float(target))


def _json_list(value: Any) -> list[Any]:
    parsed = _loads(value, [])
    return parsed if isinstance(parsed, list) else []


def _has_text(value: Any) -> bool:
    return bool(str(value or "").strip())


def _action_contract_from_rows(rows: list[dict[str, Any]]) -> dict[str, Any]:
    total = len(rows)
    if not total:
        return {
            "sample_size": 0,
            "score": 0.0,
            "checks": {
                "has_decision_fields": False,
                "has_evidence_refs": False,
                "has_verification_plan": False,
                "write_or_llm_requires_approval": False,
                "write_actions_have_affected_tables": False,
            },
            "coverage": {},
        }

    decision_ok = 0
    evidence_ok = 0
    verification_ok = 0
    approval_ok = 0
    affected_ok = 0
    write_rows = 0
    gated_rows = 0
    for row in rows:
        expected_gain = _has_text(row.get("expected_gain"))
        risk_level = str(row.get("risk_level") or "").strip().lower()
        if expected_gain and risk_level in {"low", "medium", "high"}:
            decision_ok += 1
        if _json_list(row.get("evidence_refs_json")):
            evidence_ok += 1
        if _json_list(row.get("verification_plan_json")):
            verification_ok += 1

        writes = bool(row.get("writes_business_data") in (True, 1, "1", "t", "true"))
        uses_llm = bool(row.get("uses_llm") in (True, 1, "1", "t", "true"))
        requires_approval = bool(row.get("requires_approval") in (True, 1, "1", "t", "true"))
        if writes:
            write_rows += 1
            if _json_list(row.get("affected_tables_json")):
                affected_ok += 1
        if writes or uses_llm:
            gated_rows += 1
            if requires_approval:
                approval_ok += 1

    decision_cov = _coverage(decision_ok, total)
    evidence_cov = _coverage(evidence_ok, total)
    verification_cov = _coverage(verification_ok, total)
    approval_cov = 1.0 if gated_rows == 0 else _coverage(approval_ok, gated_rows)
    affected_cov = 1.0 if write_rows == 0 else _coverage(affected_ok, write_rows)
    score = (
        decision_cov * 0.22
        + evidence_cov * 0.22
        + verification_cov * 0.22
        + approval_cov * 0.18
        + affected_cov * 0.16
    )
    return {
        "sample_size": total,
        "score": round(score, 3),
        "checks": {
            "has_decision_fields": decision_cov >= 0.9,
            "has_evidence_refs": evidence_cov >= 0.8,
            "has_verification_plan": verification_cov >= 0.9,
            "write_or_llm_requires_approval": approval_cov >= 1.0,
            "write_actions_have_affected_tables": affected_cov >= 0.9,
        },
        "coverage": {
            "decision_fields": round(decision_cov, 3),
            "evidence_refs": round(evidence_cov, 3),
            "verification_plan": round(verification_cov, 3),
            "approval_gate": round(approval_cov, 3),
            "affected_tables": round(affected_cov, 3),
        },
    }


def _action_contract_snapshot(limit: int = 80) -> dict[str, Any]:
    if not table_exists("vkpi_action_inbox"):
        return {"available": False, "score": 0.0, "reason": "vkpi_action_inbox_missing"}
    rows = get_conn().execute(
        """
        SELECT expected_gain, risk_level, evidence_refs_json, verification_plan_json,
               affected_tables_json, writes_business_data, uses_llm, requires_approval
        FROM vkpi_action_inbox
        WHERE status IN ('suggested', 'approved', 'executed', 'done')
        ORDER BY updated_at DESC, id DESC
        LIMIT ?
        """,
        (max(1, min(int(limit or 80), 200)),),
    ).fetchall()
    result = _action_contract_from_rows([dict(row) for row in rows])
    return {"available": True, **result}


def _market_card_contract_probe() -> dict[str, Any]:
    from app.domains.market import intelligence_cards

    probe = intelligence_cards.build_market_intelligence_cards({
        "passed": True,
        "generated_at": "probe",
        "summary": {
            "run_id": "scorecard",
            "signals_loaded": 3,
            "launch_candidates": 1,
            "comment_opportunities": 1,
            "high_priority": 1,
        },
        "distributions": {"review_status": {"pending": 3}},
        "hot_brands": [{"brand": "sony", "count": 2, "score": 72}],
    })
    cards = probe.get("cards") if isinstance(probe.get("cards"), list) else []
    cards_have_actions = all(bool(card.get("actions")) for card in cards if isinstance(card, dict))
    return {
        "passed": bool(probe.get("passed")) and bool(cards) and cards_have_actions,
        "card_count": len(cards),
        "cards_have_evidence": bool((probe.get("checks") or {}).get("cards_have_evidence")),
        "cards_have_actions": cards_have_actions,
    }


def _dimension(
    key: str,
    label: str,
    weight: int,
    capability_score: float,
    observed_evidence_score: float,
    *,
    facts: dict[str, Any],
    target: str,
    next_step: str,
) -> dict[str, Any]:
    observed = _clamp(observed_evidence_score)
    capability = _clamp(capability_score)
    return {
        "key": key,
        "label": label,
        "weight": weight,
        # Compatibility: score remains the decision-facing score, now explicitly
        # based on observed evidence rather than installed capability.
        "score": round(observed, 3),
        "weighted_score": round(weight * observed, 1),
        "capability_score": round(capability, 3),
        "capability_weighted_score": round(weight * capability, 1),
        "observed_evidence_score": round(observed, 3),
        "observed_evidence_weighted_score": round(weight * observed, 1),
        "facts": facts,
        "target": target,
        "next_step": next_step,
    }


def build_marketing_brain_scorecard(
    staff: dict[str, Any] | None = None,
    *,
    ops_dir: str = "runtime/ops",
) -> dict[str, Any]:
    """Return a read-only 90+ scorecard for the AI Marketing Brain target."""
    del staff
    activity_contracts = _activity_evidence_contracts()
    # 1. Evidence graph: installed ledger capability vs recent traced evidence.
    event_scope = "organization_id = 1"
    event_count = _count("vkpi_event_ledger", event_scope) or 0
    recent_events = _recent_count(
        "vkpi_event_ledger", "occurred_at", where=event_scope,
    )
    traced_events = _count(
        "vkpi_event_ledger",
        f"{event_scope} AND trace_id IS NOT NULL AND trace_id <> ''",
    ) or 0
    provenance_events = _count(
        "vkpi_event_ledger",
        f"{event_scope} AND provenance_json IS NOT NULL "
        "AND provenance_json <> '{}'::jsonb",
    ) or 0
    recent_traced = _recent_count(
        "vkpi_event_ledger", "occurred_at",
        where=f"{event_scope} AND trace_id IS NOT NULL AND trace_id <> ''",
    )
    recent_provenance = _recent_count(
        "vkpi_event_ledger",
        "occurred_at",
        where=f"{event_scope} AND provenance_json IS NOT NULL "
        "AND provenance_json <> '{}'::jsonb",
    )
    trace_cov = _coverage(traced_events, event_count)
    prov_cov = _coverage(provenance_events, event_count)
    recent_trace_cov = _coverage(recent_traced, recent_events)
    recent_prov_cov = _coverage(recent_provenance, recent_events)
    event_contract = activity_contracts["event"]
    event_base_contract = activity_contracts["event_base"]
    recent_event_units = _recent_distinct_count(
        event_base_contract.table,
        event_base_contract.unit_sql,
        event_base_contract.timestamp_column,
        where=event_base_contract.where_sql,
    )
    recent_traced_event_units = _recent_distinct_count(
        event_base_contract.table,
        event_base_contract.unit_sql,
        event_base_contract.timestamp_column,
        where=(
            f"({event_base_contract.where_sql}) AND "
            "trace_id IS NOT NULL AND trace_id <> ''"
        ),
    )
    recent_provenance_event_units = _recent_distinct_count(
        event_base_contract.table,
        event_base_contract.unit_sql,
        event_base_contract.timestamp_column,
        where=(
            f"({event_base_contract.where_sql}) "
            "AND provenance_json IS NOT NULL AND provenance_json <> '{}'::jsonb "
            f"AND {_server_bound_event_sql()}"
        ),
    )
    recent_verified_event_units = _recent_distinct_count(
        event_contract.table,
        event_contract.unit_sql,
        event_contract.timestamp_column,
        where=event_contract.where_sql,
    )
    distinct_trace_cov = _coverage(recent_traced_event_units, recent_event_units)
    distinct_prov_cov = _coverage(recent_provenance_event_units, recent_event_units)
    evidence_capability = 1.0 if table_exists("vkpi_event_ledger") else 0.0
    evidence_score = (
        0.50 * _ramp(recent_verified_event_units, 80)
        + 0.25 * distinct_trace_cov
        + 0.25 * distinct_prov_cov
    )

    # 2. Durable workflow: tables/checkpoint contract vs recent completed runs.
    workflow_runs = _count("vkpi_workflow_runs") or 0
    workflow_steps = _count("vkpi_workflow_steps") or 0
    workflow_checkpoints = _count("vkpi_workflow_checkpoints") or 0
    workflow_contract = activity_contracts["workflow"]
    observed_workflow_units = _distinct_count(
        workflow_contract.table,
        workflow_contract.unit_sql,
        workflow_contract.where_sql,
    ) or 0
    # The observed contract already requires a completed, fence-bound run.
    completed_runs = observed_workflow_units
    recent_runs = _recent_distinct_count(
        workflow_contract.table,
        workflow_contract.unit_sql,
        workflow_contract.timestamp_column,
        where=workflow_contract.where_sql,
    )
    recent_completed_runs = recent_runs
    completed_cov = _coverage(completed_runs, observed_workflow_units)
    recent_completed_cov = _coverage(recent_completed_runs, recent_runs)
    workflow_capability = sum(
        1.0
        for table in ("vkpi_workflow_runs", "vkpi_workflow_steps", "vkpi_workflow_checkpoints")
        if table_exists(table)
    ) / 3.0
    workflow_score = 0.70 * _ramp(recent_runs, 20) + 0.30 * recent_completed_cov

    # 3. Recommendation contract: contract shape is capability; verified executions are evidence.
    action_contract = _action_contract_snapshot()
    contract_cov = float(action_contract.get("score") or 0.0)
    executed_total = _count("vkpi_action_inbox", "status IN ('executed', 'done')") or 0
    executed_verified = _count(
        "vkpi_action_inbox",
        "status IN ('executed', 'done') AND result_checklist_json IS NOT NULL "
        "AND result_checklist_json::text NOT IN ('', '{}', 'null') "
        "AND EXISTS ("
        "SELECT 1 FROM vkpi_event_ledger ev "
        "WHERE ev.event_type = 'action_result_accepted' "
        "AND ev.entity_type = 'action' "
        "AND ev.entity_id = CAST(vkpi_action_inbox.id AS TEXT) "
        "AND ev.organization_id = 1 "
        "AND ev.actor_type = 'staff' AND ev.actor_id <> '' "
        "AND ev.source = 'action_inbox.human_verification' "
        "AND ev.trace_id <> '' "
        "AND ev.provenance_json IS NOT NULL "
        "AND ev.provenance_json <> '{}'::jsonb "
        "AND COALESCE(ev.provenance_json->>'evidence_verification','') "
        "= 'staff_attestation_bound_to_execution_ledger' "
        "AND COALESCE(ev.provenance_json->>'execution_effect','') "
        "IN ('state_changed','external_confirmed'))",
    ) or 0
    exec_cov = _ramp(executed_verified, 10)
    action_capability = contract_cov
    action_score = exec_cov

    # 4. Learning loop: no effectiveness claim unless all three independent legs pass.
    feedback_rows = _count("vkpi_memory_feedback") or 0
    recommendation_feedback = _count("vkpi_recommendation_feedback") or 0
    recommendation_outcomes = _count("vkpi_recommendation_outcomes") or 0
    learning_readiness = build_learning_readiness()
    learning_facts = learning_readiness.get("facts") or {}
    real_feedback = int(learning_facts.get("real_human_feedback") or 0)
    finalized_outcomes = int(learning_facts.get("evidence_backed_finalized_outcomes") or 0)
    # Raw recommendation flags/cost fields are descriptive operational state,
    # not a verified business outcome.  Only evidence-backed, human-finalized
    # GTM outcomes may contribute to this observed leg.
    real_outcomes = finalized_outcomes
    prediction_evals = int(learning_facts.get("prediction_evals_with_actual") or 0)
    outreach_coverage = learning_facts.get("outreach_prediction_coverage") or {}
    outreach_claimable = bool(outreach_coverage.get("claimable"))
    outreach_recent_guard = (
        "" if outreach_claimable else
        "AND NOT EXISTS (SELECT 1 FROM vkpi_prediction_runs pr "
        "WHERE pr.organization_id=vkpi_prediction_evals.organization_id "
        "AND pr.run_id=vkpi_prediction_evals.run_id "
        "AND pr.task_type='kol_outreach_reply_probability') "
    )
    binding_sql = verified_prediction_binding_sql("vkpi_prediction_evals")
    verification_event_sql = verified_prediction_event_sql("vkpi_prediction_evals")
    outcome_sql = outcome_evidence_sql("o")
    # One finalized business outcome is one learning unit even when several
    # forecast runs are evaluated against it.  Counting eval rows here would
    # let duplicate runs inflate both the learning and eval-governance legs.
    recent_prediction_evals = _distinct_count(
        "vkpi_prediction_evals",
        "outcome_id",
        "actual_value IS NOT NULL AND outcome_id IS NOT NULL "
        f"AND {binding_sql} "
        f"AND {verification_event_sql} "
        f"{outreach_recent_guard}"
        "AND error_abs IS NOT NULL "
        "AND LOWER(error_abs::text) NOT IN ('nan', 'infinity', '-infinity') "
        "AND EXISTS (SELECT 1 FROM vkpi_gtm_outcomes o "
        "WHERE o.id=vkpi_prediction_evals.outcome_id AND o.decision <> 'open' "
        "AND o.decided_at IS NOT NULL AND o.decided_by IS NOT NULL "
        f"AND ({outcome_sql}) "
        "AND o.decided_at >= NOW() - INTERVAL '30 days')",
    ) or 0
    learning_capability = sum(
        1.0
        for table in (
            "vkpi_recommendation_feedback",
            "vkpi_recommendation_outcomes",
            "vkpi_gtm_outcomes",
            "vkpi_prediction_evals",
        )
        if table_exists(table)
    ) / 4.0
    learning_score = (
        0.30 * _ramp(real_feedback, 20)
        + 0.35 * _ramp(finalized_outcomes, 20)
        + 0.35 * _ramp(recent_prediction_evals, 10)
    )

    # 5. Market intelligence: raw observations stay separate from promoted DB evidence.
    competitor_signals = _count("vkpi_competitor_signals") or 0
    fresh_signal_where = (
        "COALESCE(review_status, '') <> 'expired' "
        "AND (expires_at IS NULL OR expires_at >= NOW())"
    )
    fresh_signals = _count(
        "vkpi_competitor_signals",
        fresh_signal_where,
    ) or 0
    market_mentions = _count("vkpi_market_mentions") or 0
    recent_mentions = _recent_count("vkpi_market_mentions", "created_at")
    latest_fresh_signal = _latest_value(
        "vkpi_competitor_signals", "created_at", fresh_signal_where
    )
    latest_market_mention = _latest_value("vkpi_market_mentions", "created_at")
    source_freshness = evaluate_requirements(
        [
            DataRequirement(
                key="competitor_signals",
                label="non-expired competitor signals",
                observed=fresh_signals,
                minimum=5,
                freshest_at=latest_fresh_signal,
                max_age_days=14,
            ),
            DataRequirement(
                key="market_mentions",
                label="market mentions",
                observed=recent_mentions,
                minimum=5,
                freshest_at=latest_market_mention,
                max_age_days=14,
            ),
        ]
    ).to_dict()
    raw_market_source = latest_raw_market_source_observation(ops_dir)
    promoted_signal_score = _ramp(fresh_signals, 20)
    market_mention_score = _ramp(recent_mentions, 20)
    raw_market_source_score = float(raw_market_source.get("evidence_score") or 0.0)
    market_contract = _market_card_contract_probe()
    market_capability = (
        0.60 * (1.0 if market_contract.get("passed") else 0.0)
        + 0.20 * (1.0 if table_exists("vkpi_competitor_signals") else 0.0)
        + 0.20 * (1.0 if table_exists("vkpi_market_mentions") else 0.0)
    )
    # Raw source evidence is bounded to its own leg and cannot replace reviewed DB rows.
    market_score = (
        0.40 * promoted_signal_score
        + 0.30 * market_mention_score
        + 0.30 * raw_market_source_score
    )

    # 6. Eval governance: installed suites vs recent suites and real prediction evaluations.
    eval_runs = _count("vkpi_eval_runs") or 0
    eval_results = _count("vkpi_eval_results") or 0
    eval_contract = activity_contracts["eval"]
    recent_evals = _recent_distinct_count(
        eval_contract.table,
        eval_contract.unit_sql,
        eval_contract.timestamp_column,
        where=eval_contract.where_sql,
    )
    latest_passed = _distinct_count(
        eval_contract.table,
        eval_contract.unit_sql,
        eval_contract.where_sql,
    ) or 0
    eval_table_capability = sum(
        1.0
        for table in (
            "vkpi_eval_runs",
            "vkpi_eval_results",
            "vkpi_prediction_runs",
            "vkpi_prediction_evals",
        )
        if table_exists(table)
    ) / 4.0
    # Passing data is observed evidence, never installed capability.
    eval_capability = eval_table_capability
    eval_score = (
        0.50 * _ramp(recent_evals, 3)
        + 0.50 * _ramp(recent_prediction_evals, 10)
    )

    dimensions = [
        _dimension(
            "evidence_graph", "证据图谱 / Trace", 18, evidence_capability, evidence_score,
            facts={
                "event_count": event_count,
                "recent_7d": recent_events,
                "recent_distinct_business_units_7d": recent_event_units,
                "recent_distinct_traced_units_7d": recent_traced_event_units,
                "recent_distinct_server_bound_units_7d": recent_provenance_event_units,
                "recent_verified_units_7d": recent_verified_event_units,
                # Raw row coverages remain descriptive diagnostics only.  The
                # score uses the distinct coverages below so duplicate emits
                # cannot lift it.
                "trace_coverage": round(trace_cov, 3),
                "provenance_coverage": round(prov_cov, 3),
                "recent_trace_coverage": round(recent_trace_cov, 3),
                "recent_provenance_coverage": round(recent_prov_cov, 3),
                "distinct_trace_coverage": round(distinct_trace_cov, 3),
                "distinct_server_bound_coverage": round(distinct_prov_cov, 3),
            },
            target="近7天>=80条带trace/provenance的事件,所有推荐可追溯。",
            next_step="把 market/KOL/project/action 关键判断统一 emit 到 event_ledger,并带 trace_id/provenance。",
        ),
        _dimension(
            "durable_workflow", "Durable Workflow", 18, workflow_capability, workflow_score,
            facts={"runs": workflow_runs, "recent_7d": recent_runs, "steps": workflow_steps,
                   "checkpoints": workflow_checkpoints, "completed_runs": completed_runs,
                   "distinct_business_units": observed_workflow_units,
                   "server_bound_distinct_units": observed_workflow_units,
                   "recent_completed_7d": recent_completed_runs,
                   "historical_completion_coverage": round(completed_cov, 3)},
            target="近7天>=20条真自动 run(搜索/建档/深析/履约/复盘都走 workflow),非手动 demo。",
            next_step="把搜索/建档/深析/履约观察/复盘/action执行都接成 workflow,挂调度自动起。",
        ),
        _dimension(
            "recommendation_contract", "推荐决策合约", 22, action_capability, action_score,
            facts={**action_contract, "executed_total": executed_total, "executed_verified": executed_verified},
            target="合约字段齐 + 执行后有真 result_checklist(before/after),>=10 条真验收。",
            next_step="跑真 approve->execute 让 result_checklist 规模落地;拒绝无证据推荐。",
        ),
        _dimension(
            "learning_loop", "学习回写", 18, learning_capability, learning_score,
            facts={
                "memory_feedback": feedback_rows,
                "recommendation_feedback": recommendation_feedback,
                "real_feedback_nondemo": real_feedback,
                "recommendation_outcomes": recommendation_outcomes,
                "real_outcomes_with_label": real_outcomes,
                "evidence_backed_finalized_gtm_outcomes": finalized_outcomes,
                "prediction_evals_with_actual": prediction_evals,
                "recent_prediction_evals_30d": recent_prediction_evals,
                "outreach_prediction_coverage": outreach_coverage,
                "data_readiness": learning_readiness,
            },
            target="真反馈>=20 + 真业务outcome>=20 + 有实际值的预测评估>=10 + 有证据人工finalized>=10。",
            next_step="先积累真实 shortlist/reject、履约/订单结果、人工裁决与 prediction eval;三腿未齐只展示观察值。",
        ),
        _dimension(
            "market_intelligence", "市场/竞品智能", 14, market_capability, market_score,
            facts={
                "competitor_signals": competitor_signals,
                "fresh_signals_nonexpired": fresh_signals,
                "market_mentions": market_mentions,
                "recent_mentions_7d": recent_mentions,
                "latest_fresh_signal": latest_fresh_signal,
                "latest_market_mention": latest_market_mention,
                "source_freshness": source_freshness,
                "raw_market_source": raw_market_source,
                "observed_evidence_legs": {
                    "promoted_competitor_signals": round(promoted_signal_score, 3),
                    "market_mentions": round(market_mention_score, 3),
                    "raw_external_market_source": round(raw_market_source_score, 3),
                },
                "card_contract": market_contract,
            },
            target="近7天原始外部信号采集可验证,并有>=20条未过期 promoted signal / mention。",
            next_step="保持 external_smoke 只读采集,经审核后再提升为 competitor signal / mention;原始工件不冒充入库信号。",
        ),
        _dimension(
            "eval_governance", "Evals 治理", 10, eval_capability, eval_score,
            facts={"eval_runs": eval_runs, "eval_results": eval_results, "recent_runs_7d": recent_evals,
                   "fully_passed_runs": latest_passed,
                   "fully_passed_distinct_server_bound_suites": latest_passed,
                   "prediction_evals_with_actual": prediction_evals,
                   "recent_prediction_evals_30d": recent_prediction_evals},
            target="近7天有评测套件运行,近30天有>=10条带真实 actual 的 prediction eval。",
            next_step="把 scorecard 纳入 evals,并让真实结果持续回填 prediction_evals;历史通过不算近期证据。",
        ),
    ]

    score = round(sum(float(item["observed_evidence_weighted_score"]) for item in dimensions), 1)
    capability_score = round(sum(float(item["capability_weighted_score"]) for item in dimensions), 1)
    weakest = sorted(dimensions, key=lambda item: (float(item["score"]), -int(item["weight"])))[:3]

    def grade_for(value: float) -> str:
        if value >= 90:
            return "90+ ready"
        if value >= 80:
            return "near_90"
        if value >= 65:
            return "internal_ai_platform"
        if value >= 45:
            return "capability_stack"
        return "module_collection"

    overall_claimable = bool(learning_readiness.get("claimable")) and bool(source_freshness.get("claimable"))
    data_readiness = {
        **learning_readiness,
        "ready": overall_claimable,
        "claimable": overall_claimable,
        "claim_level": "validated" if overall_claimable else "descriptive_only",
        "status": "ready" if overall_claimable else str(learning_readiness.get("status") or "insufficient"),
        "source_freshness": source_freshness,
        "raw_market_source": raw_market_source,
        "blockers": [
            *[f"learning:{item}" for item in learning_readiness.get("blockers") or []],
            *[f"source:{item}" for item in source_freshness.get("blockers") or []],
        ],
        "policy": {
            **(learning_readiness.get("policy") or {}),
            "raw_market_source_is_observation_only": True,
            "raw_market_source_does_not_clear_db_or_outcome_blockers": True,
        },
    }
    grade = grade_for(score)
    if grade == "90+ ready" and not overall_claimable:
        grade = "90+ observed_but_unvalidated"
    return {
        "status": "ok",
        "version": SCORECARD_VERSION,
        "basis": "observed_evidence",
        "score": score,
        "observed_evidence_score": score,
        "capability_score": capability_score,
        "scores": {
            "capability": capability_score,
            "observed_evidence": score,
            "decision_score": score,
        },
        "target_score": 90,
        "grade": grade,
        "capability_grade": grade_for(capability_score),
        "claim_status": "validated" if overall_claimable else "descriptive_only",
        "data_readiness": data_readiness,
        "dimensions": dimensions,
        "weakest_dimensions": [{"key": item["key"], "score": item["score"], "next_step": item["next_step"]} for item in weakest],
        "recommended_sequence": [item["next_step"] for item in weakest],
        "policy": {
            "no_viltrox_fit_score_write": True,
            "human_approval_required_for_write_or_llm": True,
            "evidence_required_before_recommendation": True,
            "outcome_feedback_required_for_learning_claims": True,
            "decision_score_is_observed_evidence": True,
            "capability_score_is_not_business_evidence": True,
            "unready_data_blocks_effectiveness_claims": True,
            "raw_artifacts_do_not_count_as_promoted_signals_or_outcomes": True,
        },
        "note": "scorecard(v3): capability 只回答系统会不会做,observed_evidence 才回答近期真实数据是否证明有效。"
                "原始外部信号工件只计 raw-market-source 观察腿,不计 promoted signal 或 outcome;"
                "DataReadiness 未通过时只允许描述观察值。",
    }


__all__ = [
    "SCORECARD_VERSION",
    "build_marketing_brain_scorecard",
    "_action_contract_from_rows",
]
