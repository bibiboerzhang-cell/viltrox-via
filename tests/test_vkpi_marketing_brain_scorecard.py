from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from app.domains.intelligence import marketing_brain_scorecard as scorecard
from app.domains.intelligence.marketing_brain_scorecard import _action_contract_from_rows
from app.domains.intelligence.raw_market_source import latest_raw_market_source_observation


NOW = datetime(2026, 7, 13, 12, 30, tzinfo=timezone.utc)


def _raw_market_artifact(*, generated_at: datetime = NOW) -> dict:
    source_statuses = [
        {
            "source_key": f"source-{index}",
            "provider": "rss",
            "source_type": "rss_feed",
            "url": f"https://feeds.example.com/{index}",
            "allowlisted": True,
            "status": "fetched",
        }
        for index in range(9)
    ]
    items = [
        {
            "source_uid": f"external:{index}",
            "source_key": f"source-{index % 9}",
            "provider": "rss",
            "source_type": "rss_feed",
            "source_url": f"https://news.example.com/items/{index}",
        }
        for index in range(36)
    ]
    return {
        "mode": "market_external_signal_smoke_v0",
        "generated_at": generated_at.isoformat().replace("+00:00", "Z"),
        "provider_calls": True,
        "external_http_calls": True,
        "llm_calls": False,
        "gemini_calls": False,
        "write_db": False,
        "sync_triggered": False,
        "task_enqueued": False,
        "passed": True,
        "checks": {
            "no_db_write": True,
            "no_sync_triggered": True,
            "no_llm_call": True,
            "allowlisted_sources_only": True,
            "live_fetch_returned_items": True,
        },
        "summary": {
            "sources_requested": 9,
            "sources_fetched": 9,
            "items_loaded": 36,
        },
        "source_statuses": source_statuses,
        "items": items,
        "errors": [],
    }


def _write_raw_market_artifact(
    root: Path,
    payload: dict,
    *,
    name: str = "20260713T123000Z-market-external-signal-smoke-v0.json",
) -> Path:
    path = root / name
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def test_action_contract_scores_complete_recommendation_rows():
    result = _action_contract_from_rows([
        {
            "expected_gain": "提升市场推荐质量",
            "risk_level": "low",
            "evidence_refs_json": [{"type": "market_signal", "id": "1"}],
            "verification_plan_json": ["执行后检查推荐状态"],
            "affected_tables_json": ["vkpi_kol_pool"],
            "writes_business_data": True,
            "uses_llm": False,
            "requires_approval": True,
        },
        {
            "expected_gain": "生成营销策略草案",
            "risk_level": "medium",
            "evidence_refs_json": [{"type": "competitor_brand", "id": "sony"}],
            "verification_plan_json": ["人工审阅后再执行"],
            "affected_tables_json": [],
            "writes_business_data": False,
            "uses_llm": True,
            "requires_approval": True,
        },
    ])

    assert result["score"] == 1.0
    assert result["checks"]["has_decision_fields"] is True
    assert result["checks"]["has_evidence_refs"] is True
    assert result["checks"]["has_verification_plan"] is True
    assert result["checks"]["write_or_llm_requires_approval"] is True
    assert result["checks"]["write_actions_have_affected_tables"] is True


def test_action_contract_penalizes_unexplainable_or_ungated_rows():
    result = _action_contract_from_rows([
        {
            "expected_gain": "",
            "risk_level": "",
            "evidence_refs_json": [],
            "verification_plan_json": [],
            "affected_tables_json": [],
            "writes_business_data": True,
            "uses_llm": True,
            "requires_approval": False,
        }
    ])

    assert result["score"] < 0.25
    assert result["checks"]["has_decision_fields"] is False
    assert result["checks"]["has_evidence_refs"] is False
    assert result["checks"]["has_verification_plan"] is False
    assert result["checks"]["write_or_llm_requires_approval"] is False
    assert result["checks"]["write_actions_have_affected_tables"] is False


def test_raw_market_source_accepts_fresh_validated_read_only_artifact(tmp_path):
    path = _write_raw_market_artifact(tmp_path, _raw_market_artifact())

    result = latest_raw_market_source_observation(tmp_path, now=NOW)

    assert result["status"] == "validated"
    assert result["validated"] is True
    assert result["observed"] is True
    assert result["evidence_score"] == 1.0
    assert result["artifact_path"] == str(path.resolve())
    assert result["sources_fetched"] == 9
    assert result["items_loaded"] == 36
    assert result["source_url_coverage"] == 1.0
    assert result["item_provenance_coverage"] == 1.0
    assert result["policy"]["counts_as_promoted_competitor_signal"] is False
    assert result["policy"]["counts_as_outcome"] is False


def test_raw_market_source_rejects_stale_artifact(tmp_path):
    payload = _raw_market_artifact(generated_at=NOW - timedelta(days=8))
    _write_raw_market_artifact(tmp_path, payload)

    result = latest_raw_market_source_observation(tmp_path, now=NOW)

    assert result["status"] == "stale"
    assert result["validated"] is False
    assert result["evidence_score"] == 0.0
    assert result["blockers"] == ["generated_at:stale>7d"]


def test_raw_market_source_rejects_malformed_artifact(tmp_path):
    path = tmp_path / "20260713T123000Z-market-external-signal-smoke-v0.json"
    path.write_text("{not-json", encoding="utf-8")

    result = latest_raw_market_source_observation(tmp_path, now=NOW)

    assert result["status"] == "rejected"
    assert result["validated"] is False
    assert result["blockers"] == ["artifact:malformed_json"]


@pytest.mark.parametrize("field", ["llm_calls", "gemini_calls", "write_db"])
def test_raw_market_source_rejects_side_effect_flags(tmp_path, field):
    payload = _raw_market_artifact()
    payload[field] = True
    _write_raw_market_artifact(tmp_path, payload)

    result = latest_raw_market_source_observation(tmp_path, now=NOW)

    assert result["validated"] is False
    assert result["evidence_score"] == 0.0
    assert f"side_effect:{field}" in result["blockers"]


def test_raw_market_source_rejects_demo_artifact(tmp_path):
    payload = _raw_market_artifact()
    payload["is_demo"] = True
    _write_raw_market_artifact(tmp_path, payload)

    result = latest_raw_market_source_observation(tmp_path, now=NOW)

    assert result["validated"] is False
    assert "contract:demo_or_synthetic" in result["blockers"]


def test_raw_market_source_requires_passed_contract_and_full_provenance(tmp_path):
    payload = _raw_market_artifact()
    payload["passed"] = False
    payload["source_statuses"][0]["url"] = ""
    payload["items"][0]["source_uid"] = ""
    _write_raw_market_artifact(tmp_path, payload)

    result = latest_raw_market_source_observation(tmp_path, now=NOW)

    assert result["validated"] is False
    assert "contract:passed" in result["blockers"]
    assert "coverage:source_url<1" in result["blockers"]
    assert "coverage:item_provenance<1" in result["blockers"]


def test_raw_market_source_reports_missing_artifacts(tmp_path):
    result = latest_raw_market_source_observation(tmp_path, now=NOW)

    assert result["status"] == "missing"
    assert result["validated"] is False
    assert result["evidence_score"] == 0.0
    assert result["blockers"] == ["artifact:missing"]


def test_scorecard_counts_raw_artifact_as_distinct_market_leg_without_clearing_blockers(
    monkeypatch,
    tmp_path,
):
    _write_raw_market_artifact(tmp_path, _raw_market_artifact())
    monkeypatch.setattr(scorecard, "_count", lambda *_args, **_kwargs: 0)
    monkeypatch.setattr(scorecard, "_recent_count", lambda *_args, **_kwargs: 0)
    monkeypatch.setattr(scorecard, "_latest_value", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(scorecard, "table_exists", lambda _name: False)
    monkeypatch.setattr(
        scorecard,
        "_action_contract_snapshot",
        lambda: {"available": False, "score": 0.0},
    )
    monkeypatch.setattr(
        scorecard,
        "_market_card_contract_probe",
        lambda: {"passed": False, "card_count": 0},
    )
    monkeypatch.setattr(
        scorecard,
        "build_learning_readiness",
        lambda: {
            "version": "market_brain_data_readiness_v1",
            "status": "insufficient",
            "ready": False,
            "claimable": False,
            "claim_level": "descriptive_only",
            "checks": {},
            "blockers": ["finalized_outcomes:sample<5"],
            "facts": {},
            "policy": {"effectiveness_claims_require_ready": True},
        },
    )
    monkeypatch.setattr(
        scorecard,
        "latest_raw_market_source_observation",
        lambda ops_dir: latest_raw_market_source_observation(ops_dir, now=NOW),
    )

    result = scorecard.build_marketing_brain_scorecard(ops_dir=str(tmp_path))
    market = next(item for item in result["dimensions"] if item["key"] == "market_intelligence")

    assert result["score"] == 4.2
    assert result["claim_status"] == "descriptive_only"
    assert market["observed_evidence_score"] == 0.3
    assert market["observed_evidence_weighted_score"] == 4.2
    assert market["facts"]["fresh_signals_nonexpired"] == 0
    assert market["facts"]["recent_mentions_7d"] == 0
    assert market["facts"]["raw_market_source"]["validated"] is True
    assert "source:competitor_signals:sample<5" in result["data_readiness"]["blockers"]
    assert "source:market_mentions:sample<5" in result["data_readiness"]["blockers"]


def test_scorecard_scopes_every_event_evidence_count_to_legacy_org1(
    monkeypatch,
    tmp_path,
):
    count_calls: list[tuple[str, str]] = []
    recent_calls: list[tuple[str, str]] = []

    def fake_count(table, where="", *_args, **_kwargs):
        if table == "vkpi_event_ledger":
            count_calls.append((table, where))
        return 0

    def fake_recent(table, _ts_col="created_at", *, where="", **_kwargs):
        if table == "vkpi_event_ledger":
            recent_calls.append((table, where))
        return 0

    monkeypatch.setattr(scorecard, "_count", fake_count)
    monkeypatch.setattr(scorecard, "_recent_count", fake_recent)
    monkeypatch.setattr(scorecard, "_latest_value", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(scorecard, "table_exists", lambda _name: False)
    monkeypatch.setattr(
        scorecard, "_action_contract_snapshot", lambda: {"available": False, "score": 0.0},
    )
    monkeypatch.setattr(
        scorecard, "_market_card_contract_probe", lambda: {"passed": False, "card_count": 0},
    )
    monkeypatch.setattr(
        scorecard,
        "build_learning_readiness",
        lambda: {"facts": {}, "blockers": [], "claimable": False},
    )
    monkeypatch.setattr(
        scorecard,
        "latest_raw_market_source_observation",
        lambda _ops_dir: {"evidence_score": 0.0, "validated": False},
    )

    scorecard.build_marketing_brain_scorecard(ops_dir=str(tmp_path))

    assert len(count_calls) == 3
    assert len(recent_calls) == 3
    assert all("organization_id = 1" in where for _, where in count_calls + recent_calls)


def test_scorecard_uses_distinct_verified_units_for_activity_legs(
    monkeypatch,
    tmp_path,
):
    distinct_calls: list[tuple[str, str, str]] = []

    monkeypatch.setattr(scorecard, "_count", lambda *_args, **_kwargs: 0)
    monkeypatch.setattr(scorecard, "_recent_count", lambda *_args, **_kwargs: 0)
    monkeypatch.setattr(
        scorecard,
        "_distinct_count",
        lambda table, field, where="", *_args, **_kwargs: (
            distinct_calls.append((table, field, where)) or 0
        ),
    )
    monkeypatch.setattr(scorecard, "_latest_value", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(scorecard, "table_exists", lambda _name: True)
    monkeypatch.setattr(
        scorecard, "_action_contract_snapshot", lambda: {"available": False, "score": 0.0},
    )
    monkeypatch.setattr(
        scorecard, "_market_card_contract_probe", lambda: {"passed": False, "card_count": 0},
    )
    monkeypatch.setattr(
        scorecard,
        "build_learning_readiness",
        lambda: {"facts": {}, "blockers": [], "claimable": False},
    )
    monkeypatch.setattr(
        scorecard,
        "latest_raw_market_source_observation",
        lambda _ops_dir: {"evidence_score": 0.0, "validated": False},
    )

    scorecard.build_marketing_brain_scorecard(ops_dir=str(tmp_path))

    event_calls = [call for call in distinct_calls if call[0] == "vkpi_event_ledger"]
    workflow_calls = [call for call in distinct_calls if call[0] == "vkpi_workflow_runs"]
    eval_calls = [call for call in distinct_calls if call[0] == "vkpi_eval_runs"]
    prediction_calls = [
        call for call in distinct_calls if call[0] == "vkpi_prediction_evals"
    ]
    assert any(
        "ROW(LOWER(BTRIM(event_type))" in field for _, field, _ in event_calls
    )
    assert any(
        "ROW(LOWER(BTRIM(workflow_name))" in field for _, field, _ in workflow_calls
    )
    assert all("entity_id IS NOT NULL" in where for _, _, where in workflow_calls)
    assert any(
        field == "LOWER(BTRIM(suite))"
        and "total = passed" in where
        and "eval_suite_completed" in where
        for _, field, where in eval_calls
    )
    assert any(
        field == "outcome_id"
        and "prediction_actual_verified" in where
        and "decided_at >= NOW() - INTERVAL '30 days'" in where
        for _, field, where in prediction_calls
    )


def test_activity_evidence_sql_is_org1_nonsynthetic_and_server_bound():
    contracts = scorecard._activity_evidence_contracts()

    event = contracts["event"]
    assert event.table == "vkpi_event_ledger"
    assert "organization_id = 1" in event.where_sql
    assert "server_bound_input_sha256" in event.where_sql
    assert "staff_attestation_bound_to_execution_ledger" in event.where_sql
    assert "server_resolved_outcome_contract" in event.where_sql
    assert "server_produced_observation_window" in event.where_sql
    assert "dry_run" in event.where_sql
    assert "is_test" in event.where_sql
    assert "ROW(" in event.unit_sql

    workflow = contracts["workflow"]
    assert "organization_id = 1" in workflow.where_sql
    assert "status = 'completed'" in workflow.where_sql
    assert "ws.fence_token = vkpi_workflow_runs.fence_token" in workflow.where_sql
    assert "workflow_ev.event_type = 'workflow_completed'" in workflow.where_sql
    assert "workflow_ev.trace_id = vkpi_workflow_runs.trace_id" in workflow.where_sql
    assert "workflow_ev.actor_id = ''" in workflow.where_sql
    assert "workflow_ev.confidence IS NULL" in workflow.where_sql
    assert "workflow_ev.payload_json = jsonb_build_object(" in workflow.where_sql
    assert "workflow_ev.provenance_json = jsonb_build_object(" in workflow.where_sql
    assert "server_bound_fenced_workflow_completion" in workflow.where_sql
    assert "SELECT COUNT(*) FROM vkpi_event_ledger workflow_ev" in workflow.where_sql
    assert "dry_run" in workflow.where_sql
    assert "ROW(" in workflow.unit_sql

    eval_contract = contracts["eval"]
    assert eval_contract.unit_sql == "LOWER(BTRIM(suite))"
    assert "eval_result.run_id = vkpi_eval_runs.id" in eval_contract.where_sql
    assert "COUNT(DISTINCT eval_result.case_name)" in eval_contract.where_sql
    assert "eval_ev.organization_id = 1" in eval_contract.where_sql
    assert "server_bound_eval_suite" in eval_contract.where_sql
    assert "dry_run" in eval_contract.where_sql
    assert all(
        "%" not in contract.unit_sql and "%" not in contract.where_sql
        for contract in contracts.values()
    )


def test_activity_scores_ignore_duplicate_raw_rows_and_keep_capability_separate(
    monkeypatch,
    tmp_path,
):
    raw_multiplier = {"value": 1}

    def fake_count(table, *_args, **_kwargs):
        if table in {"vkpi_event_ledger", "vkpi_workflow_runs", "vkpi_eval_runs"}:
            return 100 * raw_multiplier["value"]
        return 0

    def fake_distinct(table, _field, where="", *_args, **_kwargs):
        if table == "vkpi_event_ledger":
            return 2 if "server_bound" in where or "evidence_verification" in where else 4
        if table == "vkpi_workflow_runs":
            return 1
        if table == "vkpi_eval_runs":
            return 1
        return 0

    monkeypatch.setattr(scorecard, "_count", fake_count)
    monkeypatch.setattr(scorecard, "_distinct_count", fake_distinct)
    monkeypatch.setattr(scorecard, "_latest_value", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(scorecard, "table_exists", lambda _name: True)
    monkeypatch.setattr(
        scorecard, "_action_contract_snapshot", lambda: {"available": False, "score": 0.0},
    )
    monkeypatch.setattr(
        scorecard, "_market_card_contract_probe", lambda: {"passed": False, "card_count": 0},
    )
    monkeypatch.setattr(
        scorecard,
        "build_learning_readiness",
        lambda: {"facts": {}, "blockers": [], "claimable": False},
    )
    monkeypatch.setattr(
        scorecard,
        "latest_raw_market_source_observation",
        lambda _ops_dir: {"evidence_score": 0.0, "validated": False},
    )

    first = scorecard.build_marketing_brain_scorecard(ops_dir=str(tmp_path))
    raw_multiplier["value"] = 50
    repeated = scorecard.build_marketing_brain_scorecard(ops_dir=str(tmp_path))

    first_dims = {item["key"]: item for item in first["dimensions"]}
    repeated_dims = {item["key"]: item for item in repeated["dimensions"]}
    for key in ("evidence_graph", "durable_workflow", "eval_governance"):
        assert repeated_dims[key]["observed_evidence_score"] == first_dims[key][
            "observed_evidence_score"
        ]
        assert repeated_dims[key]["capability_score"] == first_dims[key][
            "capability_score"
        ]

    assert first_dims["eval_governance"]["capability_score"] == 1.0
    assert first_dims["eval_governance"]["observed_evidence_score"] < 1.0
