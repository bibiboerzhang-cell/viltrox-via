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
