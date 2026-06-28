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

SCORECARD_VERSION = "marketing_brain_scorecard_v2_behavioral"


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


def _dimension(key: str, label: str, weight: int, score: float, *, facts: dict[str, Any],
               target: str, next_step: str) -> dict[str, Any]:
    return {
        "key": key,
        "label": label,
        "weight": weight,
        "score": round(_clamp(score), 3),
        "weighted_score": round(weight * _clamp(score), 1),
        "facts": facts,
        "target": target,
        "next_step": next_step,
    }


def build_marketing_brain_scorecard(staff: dict[str, Any] | None = None) -> dict[str, Any]:
    """Return a read-only 90+ scorecard for the AI Marketing Brain target."""
    del staff
    # ── 1. evidence_graph:近期事件流 + trace/provenance 覆盖率(行为:近7天真产证据,非曾经有)──
    event_count = _count("vkpi_event_ledger") or 0
    recent_events = _recent_count("vkpi_event_ledger", "occurred_at")
    traced_events = _count("vkpi_event_ledger", "trace_id IS NOT NULL AND trace_id <> ''") or 0
    provenance_events = _count("vkpi_event_ledger", "provenance_json IS NOT NULL AND provenance_json <> '{}'::jsonb") or 0
    trace_cov = _coverage(traced_events, event_count)
    prov_cov = _coverage(provenance_events, event_count)
    evidence_score = (
        (0.15 if table_exists("vkpi_event_ledger") else 0.0)
        + 0.50 * _ramp(recent_events, 80)
        + 0.20 * trace_cov
        + 0.15 * prov_cov
    )

    # ── 2. durable_workflow:近期 run + 完成率(行为:近7天真自动跑,非曾经跑过3条)──
    workflow_runs = _count("vkpi_workflow_runs") or 0
    workflow_steps = _count("vkpi_workflow_steps") or 0
    workflow_checkpoints = _count("vkpi_workflow_checkpoints") or 0
    completed_runs = _count("vkpi_workflow_runs", "status = 'completed'") or 0
    recent_runs = _recent_count("vkpi_workflow_runs", "created_at")
    completed_cov = _coverage(completed_runs, workflow_runs)
    workflow_score = (
        (0.15 if table_exists("vkpi_workflow_runs") and table_exists("vkpi_workflow_steps") else 0.0)
        + 0.55 * _ramp(recent_runs, 20)
        + 0.15 * completed_cov
        + (0.15 if workflow_checkpoints > 0 else 0.0)
    )

    # ── 3. recommendation_contract:合约字段覆盖 + 执行后真验收(行为:执行有result_checklist)──
    action_contract = _action_contract_snapshot()
    contract_cov = float(action_contract.get("score") or 0.0)
    executed_total = _count("vkpi_action_inbox", "status IN ('executed', 'done')") or 0
    executed_verified = _count(
        "vkpi_action_inbox",
        "status IN ('executed', 'done') AND result_checklist_json IS NOT NULL "
        "AND result_checklist_json::text NOT IN ('', '{}', 'null')",
    ) or 0
    exec_cov = _ramp(executed_verified, 10)
    action_score = 0.6 * contract_cov + 0.4 * exec_cov

    # ── 4. learning_loop:真反馈(排除demo)+ 真业务outcome(行为:非1条人造)──
    feedback_rows = _count("vkpi_memory_feedback") or 0
    recommendation_feedback = _count("vkpi_recommendation_feedback") or 0
    real_feedback = _count("vkpi_recommendation_feedback", "COALESCE(note, '') NOT ILIKE ?", ("%demo%",)) or 0
    recommendation_outcomes = _count("vkpi_recommendation_outcomes") or 0
    real_outcomes = _count(
        "vkpi_recommendation_outcomes",
        "COALESCE(content_published, FALSE) = TRUE OR COALESCE(order_attributed, FALSE) = TRUE OR computed_roi IS NOT NULL",
    ) or 0
    learning_score = (
        (0.10 if table_exists("vkpi_recommendation_feedback") else 0.0)
        + 0.45 * _ramp(real_feedback, 20)
        + 0.45 * _ramp(real_outcomes, 20)
    )

    # ── 5. market_intelligence:新鲜信号(未过期)+ 近期mention(行为:活体看世界,非曾经有信号)──
    competitor_signals = _count("vkpi_competitor_signals") or 0
    fresh_signals = _count(
        "vkpi_competitor_signals",
        "COALESCE(review_status, '') <> 'expired' AND (expires_at IS NULL OR expires_at >= NOW())",
    ) or 0
    market_mentions = _count("vkpi_market_mentions") or 0
    recent_mentions = _recent_count("vkpi_market_mentions", "created_at")
    market_contract = _market_card_contract_probe()
    market_score = (
        (0.30 if market_contract.get("passed") else 0.0)
        + 0.45 * _ramp(fresh_signals, 20)
        + 0.25 * _ramp(recent_mentions, 20)
    )

    # ── 6. eval_governance:近期评测跑 + 通过(评测本就在跑,可保留较高)──
    eval_runs = _count("vkpi_eval_runs") or 0
    eval_results = _count("vkpi_eval_results") or 0
    recent_evals = _recent_count("vkpi_eval_runs", "finished_at")
    latest_passed = _count("vkpi_eval_runs", "status = 'done' AND total = passed") or 0
    eval_score = (
        (0.20 if table_exists("vkpi_eval_runs") and table_exists("vkpi_eval_results") else 0.0)
        + 0.40 * _ramp(recent_evals, 3)
        + (0.40 if latest_passed > 0 else 0.0)
    )

    dimensions = [
        _dimension(
            "evidence_graph", "证据图谱 / Trace", 18, evidence_score,
            facts={"event_count": event_count, "recent_7d": recent_events, "trace_coverage": round(trace_cov, 3),
                   "provenance_coverage": round(prov_cov, 3)},
            target="近7天>=80条带trace/provenance的事件,所有推荐可追溯。",
            next_step="把 market/KOL/project/action 关键判断统一 emit 到 event_ledger,并带 trace_id/provenance。",
        ),
        _dimension(
            "durable_workflow", "Durable Workflow", 18, workflow_score,
            facts={"runs": workflow_runs, "recent_7d": recent_runs, "steps": workflow_steps,
                   "checkpoints": workflow_checkpoints, "completed_runs": completed_runs},
            target="近7天>=20条真自动 run(搜索/建档/深析/履约/复盘都走 workflow),非手动 demo。",
            next_step="把搜索/建档/深析/履约观察/复盘/action执行都接成 workflow,挂调度自动起。",
        ),
        _dimension(
            "recommendation_contract", "推荐决策合约", 22, round(_clamp(action_score), 3),
            facts={**action_contract, "executed_total": executed_total, "executed_verified": executed_verified},
            target="合约字段齐 + 执行后有真 result_checklist(before/after),>=10 条真验收。",
            next_step="跑真 approve->execute 让 result_checklist 规模落地;拒绝无证据推荐。",
        ),
        _dimension(
            "learning_loop", "学习回写", 18, learning_score,
            facts={
                "memory_feedback": feedback_rows,
                "recommendation_feedback": recommendation_feedback,
                "real_feedback_nondemo": real_feedback,
                "recommendation_outcomes": recommendation_outcomes,
                "real_outcomes_with_label": real_outcomes,
            },
            target="近真反馈>=20(排除demo)+真业务outcome>=20(published/order/roi非空)。",
            next_step="接员工 shortlist/reject 批量写 recommendation_feedback;履约 claim/publish 回写 outcome label。",
        ),
        _dimension(
            "market_intelligence", "市场/竞品智能", 14, market_score,
            facts={
                "competitor_signals": competitor_signals,
                "fresh_signals_nonexpired": fresh_signals,
                "market_mentions": market_mentions,
                "recent_mentions_7d": recent_mentions,
                "card_contract": market_contract,
            },
            target="近7天有>=20条未过期新鲜信号(活体看世界),非停在旧快照。",
            next_step="signal fetch(competitor_radar/external_smoke)挂调度真跑,补新鲜 mention->signal。",
        ),
        _dimension(
            "eval_governance", "Evals 治理", 10, eval_score,
            facts={"eval_runs": eval_runs, "eval_results": eval_results, "recent_runs_7d": recent_evals,
                   "fully_passed_runs": latest_passed},
            target="近7天有评测跑且全通过,退化可被发现。",
            next_step="把 scorecard 纳入 evals;低于行为门槛禁止宣称 90+。",
        ),
    ]

    score = round(sum(float(item["weighted_score"]) for item in dimensions), 1)
    weakest = sorted(dimensions, key=lambda item: (float(item["score"]), -int(item["weight"])))[:3]
    if score >= 90:
        grade = "90+ ready"
    elif score >= 80:
        grade = "near_90"
    elif score >= 65:
        grade = "internal_ai_platform"
    elif score >= 45:
        grade = "capability_stack"
    else:
        grade = "module_collection"
    return {
        "status": "ok",
        "version": SCORECARD_VERSION,
        "basis": "behavioral",
        "score": score,
        "target_score": 90,
        "grade": grade,
        "dimensions": dimensions,
        "weakest_dimensions": [{"key": item["key"], "score": item["score"], "next_step": item["next_step"]} for item in weakest],
        "recommended_sequence": [item["next_step"] for item in weakest],
        "policy": {
            "no_viltrox_fit_score_write": True,
            "human_approval_required_for_write_or_llm": True,
            "evidence_required_before_recommendation": True,
            "outcome_feedback_required_for_learning_claims": True,
            "score_is_behavioral_not_structural": True,
        },
        "note": "行为验收 scorecard(v2):分数由'近7天真活跃/真outcome/新鲜信号/排除demo'驱动,非'有表+几行数据'。"
                "结构齐全但样本未规模化时分数会诚实偏低,随真数据流过自动爬升。",
    }


__all__ = [
    "SCORECARD_VERSION",
    "build_marketing_brain_scorecard",
    "_action_contract_from_rows",
]
