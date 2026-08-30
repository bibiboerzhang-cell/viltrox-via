"""Characterization tests for build_marketing_brain_scorecard(CC 54 降刀锁行为).

三套场景把 440 行聚合函数的行为逐字锁死:零数据全形状、富数据逐维打分、
外呼预测护栏 SQL 与 90+ 未验证降级。全部 DB 探针 monkeypatch 在 scorecard
模块名上(与既有测试同款接线),降复杂度刀改完必须原样绿。
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from app.domains.intelligence import marketing_brain_scorecard as scorecard

FRESH_AT = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()

INSUFFICIENT_READINESS = {
    "version": "market_brain_data_readiness_v1",
    "status": "insufficient",
    "ready": False,
    "claimable": False,
    "claim_level": "descriptive_only",
    "checks": {},
    "blockers": ["finalized_outcomes:sample<5"],
    "facts": {},
    "policy": {"effectiveness_claims_require_ready": True},
}

DIMENSION_CONTRACT = [
    ("evidence_graph", "证据图谱 / Trace", 18),
    ("durable_workflow", "Durable Workflow", 18),
    ("recommendation_contract", "推荐决策合约", 22),
    ("learning_loop", "学习回写", 18),
    ("market_intelligence", "市场/竞品智能", 14),
    ("eval_governance", "Evals 治理", 10),
]

EXPECTED_TARGETS = {
    "evidence_graph": "近7天>=80条带trace/provenance的事件,所有推荐可追溯。",
    "durable_workflow": "近7天>=20条真自动 run(搜索/建档/深析/履约/复盘都走 workflow),非手动 demo。",
    "recommendation_contract": "合约字段齐 + 执行后有真 result_checklist(before/after),>=10 条真验收。",
    "learning_loop": "真反馈>=20 + 真业务outcome>=20 + 有实际值的预测评估>=10 + 有证据人工finalized>=10。",
    "market_intelligence": "近7天原始外部信号采集可验证,并有>=20条未过期 promoted signal / mention。",
    "eval_governance": "近7天有评测套件运行,近30天有>=10条带真实 actual 的 prediction eval。",
}

EXPECTED_NEXT_STEPS = {
    "evidence_graph": "把 market/KOL/project/action 关键判断统一 emit 到 event_ledger,并带 trace_id/provenance。",
    "durable_workflow": "把搜索/建档/深析/履约观察/复盘/action执行都接成 workflow,挂调度自动起。",
    "recommendation_contract": "跑真 approve->execute 让 result_checklist 规模落地;拒绝无证据推荐。",
    "learning_loop": "先积累真实 shortlist/reject、履约/订单结果、人工裁决与 prediction eval;三腿未齐只展示观察值。",
    "market_intelligence": "保持 external_smoke 只读采集,经审核后再提升为 competitor signal / mention;原始工件不冒充入库信号。",
    "eval_governance": "把 scorecard 纳入 evals,并让真实结果持续回填 prediction_evals;历史通过不算近期证据。",
}


def _wire_zero(monkeypatch):
    monkeypatch.setattr(scorecard, "_count", lambda *_a, **_k: 0)
    monkeypatch.setattr(scorecard, "_distinct_count", lambda *_a, **_k: 0)
    monkeypatch.setattr(scorecard, "_latest_value", lambda *_a, **_k: None)
    monkeypatch.setattr(scorecard, "table_exists", lambda _name: False)
    monkeypatch.setattr(
        scorecard, "_action_contract_snapshot", lambda: {"available": False, "score": 0.0},
    )
    monkeypatch.setattr(
        scorecard, "_market_card_contract_probe", lambda: {"passed": False, "card_count": 0},
    )
    monkeypatch.setattr(
        scorecard, "build_learning_readiness", lambda: dict(INSUFFICIENT_READINESS),
    )
    monkeypatch.setattr(
        scorecard,
        "latest_raw_market_source_observation",
        lambda _ops_dir: {"evidence_score": 0.0, "validated": False},
    )


def _rich_count(table, where="", params=()):
    if table == "vkpi_event_ledger":
        recent = "INTERVAL" in where
        if "provenance_json" in where:
            return 30 if recent else 60
        if "trace_id" in where:
            return 40 if recent else 80
        return 50 if recent else 100
    if table == "vkpi_action_inbox":
        return 12 if "EXISTS" in where else 30
    return {
        "vkpi_memory_feedback": 7,
        "vkpi_recommendation_feedback": 9,
        "vkpi_recommendation_outcomes": 11,
        "vkpi_competitor_signals": 18 if "expired" in where else 25,
        "vkpi_market_mentions": 15 if "INTERVAL" in where else 33,
        "vkpi_eval_runs": 5,
        "vkpi_eval_results": 50,
        "vkpi_workflow_runs": 60,
        "vkpi_workflow_steps": 200,
        "vkpi_workflow_checkpoints": 80,
    }.get(table, 0)


def _rich_distinct(table, field, where="", params=()):
    if table == "vkpi_event_ledger":
        server_bound = "staff_attestation" in where
        traced = "trace_id IS NOT NULL" in where
        if server_bound and traced:
            return 64  # 全合约 verified 单元
        if server_bound:
            return 40  # server-bound provenance 单元
        if traced:
            return 45  # traced 单元
        return 50  # 基础业务单元
    if table == "vkpi_workflow_runs":
        return 15 if "INTERVAL" in where else 30
    if table == "vkpi_eval_runs":
        return 2 if "INTERVAL" in where else 4
    if table == "vkpi_prediction_evals":
        return 8
    return 0


def _wire_rich(monkeypatch, *, readiness_claimable=True):
    monkeypatch.setattr(scorecard, "_count", _rich_count)
    monkeypatch.setattr(scorecard, "_distinct_count", _rich_distinct)
    monkeypatch.setattr(scorecard, "_latest_value", lambda *_a, **_k: FRESH_AT)
    monkeypatch.setattr(scorecard, "table_exists", lambda _name: True)
    monkeypatch.setattr(
        scorecard,
        "_action_contract_snapshot",
        lambda: {"available": True, "sample_size": 40, "score": 0.9, "checks": {}, "coverage": {}},
    )
    monkeypatch.setattr(
        scorecard, "_market_card_contract_probe", lambda: {"passed": True, "card_count": 4},
    )
    monkeypatch.setattr(
        scorecard,
        "build_learning_readiness",
        lambda: {
            "version": "market_brain_data_readiness_v1",
            "status": "ready" if readiness_claimable else "insufficient",
            "ready": readiness_claimable,
            "claimable": readiness_claimable,
            "claim_level": "validated" if readiness_claimable else "descriptive_only",
            "checks": {},
            "blockers": [],
            "facts": {
                "real_human_feedback": 15,
                "evidence_backed_finalized_outcomes": 10,
                "prediction_evals_with_actual": 12,
                "outreach_prediction_coverage": {"claimable": False},
            },
            "policy": {"effectiveness_claims_require_ready": True},
        },
    )
    monkeypatch.setattr(
        scorecard,
        "latest_raw_market_source_observation",
        lambda _ops_dir: {"evidence_score": 1.0, "validated": True},
    )


def test_zero_data_scorecard_full_shape(monkeypatch, tmp_path):
    _wire_zero(monkeypatch)

    result = scorecard.build_marketing_brain_scorecard(ops_dir=str(tmp_path))

    assert result["status"] == "ok"
    assert result["version"] == "marketing_brain_scorecard_v3_observed_evidence"
    assert result["basis"] == "observed_evidence"
    assert result["score"] == 0.0
    assert result["observed_evidence_score"] == 0.0
    assert result["capability_score"] == 0.0
    assert result["scores"] == {"capability": 0.0, "observed_evidence": 0.0, "decision_score": 0.0}
    assert result["target_score"] == 90
    assert result["grade"] == "module_collection"
    assert result["capability_grade"] == "module_collection"
    assert result["claim_status"] == "descriptive_only"

    dims = result["dimensions"]
    assert [(d["key"], d["label"], d["weight"]) for d in dims] == DIMENSION_CONTRACT
    for dim in dims:
        assert dim["score"] == 0.0
        assert dim["weighted_score"] == 0.0
        assert dim["capability_score"] == 0.0
        assert dim["capability_weighted_score"] == 0.0
        assert dim["observed_evidence_score"] == 0.0
        assert dim["observed_evidence_weighted_score"] == 0.0
        assert dim["target"] == EXPECTED_TARGETS[dim["key"]]
        assert dim["next_step"] == EXPECTED_NEXT_STEPS[dim["key"]]

    assert [w["key"] for w in result["weakest_dimensions"]] == [
        "recommendation_contract",
        "evidence_graph",
        "durable_workflow",
    ]
    assert result["recommended_sequence"] == [
        EXPECTED_NEXT_STEPS["recommendation_contract"],
        EXPECTED_NEXT_STEPS["evidence_graph"],
        EXPECTED_NEXT_STEPS["durable_workflow"],
    ]

    readiness = result["data_readiness"]
    assert readiness["ready"] is False
    assert readiness["claimable"] is False
    assert readiness["claim_level"] == "descriptive_only"
    assert readiness["status"] == "insufficient"
    assert readiness["blockers"] == [
        "learning:finalized_outcomes:sample<5",
        "source:competitor_signals:sample<5",
        "source:market_mentions:sample<5",
    ]
    assert readiness["policy"]["effectiveness_claims_require_ready"] is True
    assert readiness["policy"]["raw_market_source_is_observation_only"] is True
    assert readiness["policy"]["raw_market_source_does_not_clear_db_or_outcome_blockers"] is True

    assert result["policy"] == {
        "no_viltrox_fit_score_write": True,
        "human_approval_required_for_write_or_llm": True,
        "evidence_required_before_recommendation": True,
        "outcome_feedback_required_for_learning_claims": True,
        "decision_score_is_observed_evidence": True,
        "capability_score_is_not_business_evidence": True,
        "unready_data_blocks_effectiveness_claims": True,
        "raw_artifacts_do_not_count_as_promoted_signals_or_outcomes": True,
    }
    assert result["note"] == (
        "scorecard(v3): capability 只回答系统会不会做,observed_evidence 才回答近期真实数据是否证明有效。"
        "原始外部信号工件只计 raw-market-source 观察腿,不计 promoted signal 或 outcome;"
        "DataReadiness 未通过时只允许描述观察值。"
    )


def test_rich_data_scorecard_scores_each_dimension(monkeypatch, tmp_path):
    _wire_rich(monkeypatch)

    result = scorecard.build_marketing_brain_scorecard(ops_dir=str(tmp_path))

    assert result["score"] == 83.5
    assert result["capability_score"] == 97.8
    assert result["grade"] == "near_90"
    assert result["capability_grade"] == "90+ ready"
    assert result["claim_status"] == "validated"
    assert result["data_readiness"]["ready"] is True
    assert result["data_readiness"]["claim_level"] == "validated"
    assert result["data_readiness"]["blockers"] == []
    assert result["data_readiness"]["source_freshness"]["claimable"] is True

    dims = {d["key"]: d for d in result["dimensions"]}
    assert dims["evidence_graph"]["observed_evidence_score"] == 0.825
    assert dims["evidence_graph"]["observed_evidence_weighted_score"] == 14.8
    assert dims["evidence_graph"]["capability_score"] == 1.0
    assert dims["evidence_graph"]["facts"] == {
        "event_count": 100,
        "recent_7d": 50,
        "recent_distinct_business_units_7d": 50,
        "recent_distinct_traced_units_7d": 45,
        "recent_distinct_server_bound_units_7d": 40,
        "recent_verified_units_7d": 64,
        "trace_coverage": 0.8,
        "provenance_coverage": 0.6,
        "recent_trace_coverage": 0.8,
        "recent_provenance_coverage": 0.6,
        "distinct_trace_coverage": 0.9,
        "distinct_server_bound_coverage": 0.8,
    }

    assert dims["durable_workflow"]["observed_evidence_score"] == 0.825
    assert dims["durable_workflow"]["facts"] == {
        "runs": 60,
        "recent_7d": 15,
        "steps": 200,
        "checkpoints": 80,
        "completed_runs": 30,
        "distinct_business_units": 30,
        "server_bound_distinct_units": 30,
        "recent_completed_7d": 15,
        "historical_completion_coverage": 1.0,
    }

    assert dims["recommendation_contract"]["observed_evidence_score"] == 1.0
    assert dims["recommendation_contract"]["capability_score"] == 0.9
    assert dims["recommendation_contract"]["facts"]["executed_total"] == 30
    assert dims["recommendation_contract"]["facts"]["executed_verified"] == 12

    assert dims["learning_loop"]["observed_evidence_score"] == 0.68
    learning_facts = dims["learning_loop"]["facts"]
    assert learning_facts["memory_feedback"] == 7
    assert learning_facts["recommendation_feedback"] == 9
    assert learning_facts["real_feedback_nondemo"] == 15
    assert learning_facts["recommendation_outcomes"] == 11
    assert learning_facts["real_outcomes_with_label"] == 10
    assert learning_facts["evidence_backed_finalized_gtm_outcomes"] == 10
    assert learning_facts["prediction_evals_with_actual"] == 12
    assert learning_facts["recent_prediction_evals_30d"] == 8
    assert learning_facts["outreach_prediction_coverage"] == {"claimable": False}

    assert dims["market_intelligence"]["observed_evidence_score"] == 0.885
    assert dims["market_intelligence"]["capability_score"] == 1.0
    assert dims["market_intelligence"]["facts"]["observed_evidence_legs"] == {
        "promoted_competitor_signals": 0.9,
        "market_mentions": 0.75,
        "raw_external_market_source": 1.0,
    }

    assert dims["eval_governance"]["observed_evidence_score"] == 0.733
    assert dims["eval_governance"]["facts"] == {
        "eval_runs": 5,
        "eval_results": 50,
        "recent_runs_7d": 2,
        "fully_passed_runs": 4,
        "fully_passed_distinct_server_bound_suites": 4,
        "prediction_evals_with_actual": 12,
        "recent_prediction_evals_30d": 8,
    }

    assert [w["key"] for w in result["weakest_dimensions"]] == [
        "learning_loop",
        "eval_governance",
        "evidence_graph",
    ]


@pytest.mark.parametrize("outreach_claimable", [True, False])
def test_recent_prediction_evals_outreach_guard(monkeypatch, tmp_path, outreach_claimable):
    prediction_wheres: list[str] = []

    def spy_distinct(table, field, where="", params=()):
        if table == "vkpi_prediction_evals":
            prediction_wheres.append(where)
        return _rich_distinct(table, field, where, params)

    _wire_rich(monkeypatch)
    monkeypatch.setattr(scorecard, "_distinct_count", spy_distinct)
    monkeypatch.setattr(
        scorecard,
        "build_learning_readiness",
        lambda: {
            "claimable": False,
            "blockers": [],
            "facts": {
                "real_human_feedback": 0,
                "evidence_backed_finalized_outcomes": 0,
                "prediction_evals_with_actual": 0,
                "outreach_prediction_coverage": {"claimable": outreach_claimable},
            },
        },
    )

    scorecard.build_marketing_brain_scorecard(ops_dir=str(tmp_path))

    assert len(prediction_wheres) == 1
    where = prediction_wheres[0]
    assert ("kol_outreach_reply_probability" in where) is (not outreach_claimable)
    assert "actual_value IS NOT NULL AND outcome_id IS NOT NULL" in where
    assert "LOWER(error_abs::text) NOT IN ('nan', 'infinity', '-infinity')" in where
    assert "o.decided_at >= NOW() - INTERVAL '30 days'" in where


def test_high_observed_score_without_readiness_downgrades_grade(monkeypatch, tmp_path):
    def maxed_count(table, where="", params=()):
        if table == "vkpi_competitor_signals":
            return 20
        if table == "vkpi_market_mentions":
            return 20
        return _rich_count(table, where, params)

    def maxed_distinct(table, field, where="", params=()):
        if table == "vkpi_event_ledger":
            return 80 if "staff_attestation" in where and "trace_id IS NOT NULL" in where else 50
        if table == "vkpi_workflow_runs":
            return 20
        if table == "vkpi_eval_runs":
            return 3
        if table == "vkpi_prediction_evals":
            return 10
        return 0

    _wire_rich(monkeypatch, readiness_claimable=False)
    monkeypatch.setattr(scorecard, "_count", maxed_count)
    monkeypatch.setattr(scorecard, "_distinct_count", maxed_distinct)
    monkeypatch.setattr(
        scorecard,
        "build_learning_readiness",
        lambda: {
            "claimable": False,
            "status": "insufficient",
            "blockers": ["real_feedback:sample<20"],
            "facts": {
                "real_human_feedback": 20,
                "evidence_backed_finalized_outcomes": 20,
                "prediction_evals_with_actual": 10,
                "outreach_prediction_coverage": {"claimable": True},
            },
        },
    )

    result = scorecard.build_marketing_brain_scorecard(ops_dir=str(tmp_path))

    assert result["score"] == 100.0
    assert result["grade"] == "90+ observed_but_unvalidated"
    assert result["claim_status"] == "descriptive_only"
    assert result["data_readiness"]["claim_level"] == "descriptive_only"
    assert result["data_readiness"]["status"] == "insufficient"
    assert result["data_readiness"]["blockers"] == ["learning:real_feedback:sample<20"]
