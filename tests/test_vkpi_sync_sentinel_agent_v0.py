from __future__ import annotations

from app.services.vkpi import sync_sentinel_agent_v0


def _overview(*, ack_required: bool = False, failure_rate: float = 0.0) -> dict:
    blocking = {"run_id": "blocked-run", "reason": "failure_rate_threshold_exceeded"} if ack_required else None
    return {
        "daily_sync": {
            "guard_allowed": not ack_required,
            "ack_required": ack_required,
            "failure_rate_threshold": 0.1,
            "blocking_run": blocking,
            "latest_summary": {
                "run_id": "latest-run",
                "status": "failed" if failure_rate else "completed",
                "reason": "failure_rate_threshold_exceeded" if failure_rate else "",
                "health": {"failure_rate": failure_rate},
            },
        },
        "summary": {"overall_health": "down" if ack_required else "healthy", "issues": []},
    }


def test_sync_sentinel_reports_sync_guard_and_budget_blocks(monkeypatch) -> None:
    monkeypatch.setattr(sync_sentinel_agent_v0.sync_status, "get_overview", lambda: _overview(ack_required=True, failure_rate=0.2))
    monkeypatch.setattr(
        sync_sentinel_agent_v0,
        "_budget_caps",
        lambda: {
            "configured": True,
            "budgets": [
                {
                    "scope": "provider:openai",
                    "cap_usd": 1.0,
                    "current_spend": 1.0,
                    "usage_ratio": 1.0,
                    "warning": True,
                    "hard_stopped": True,
                }
            ],
            "summary": {"scopes": 1, "warnings": 1, "hard_stopped": 1},
        },
    )
    monkeypatch.setattr(
        sync_sentinel_agent_v0,
        "_open_alerts",
        lambda _limit: {
            "configured": True,
            "alerts": [{"id": 1, "rule_key": "recommendation.review_gap", "severity": "danger", "title": "Review gap"}],
            "summary": {"open_total": 1, "critical_open": 1, "warning_open": 0},
        },
    )
    monkeypatch.setattr(
        sync_sentinel_agent_v0,
        "_latest_p6_79",
        lambda _ops_dir: {
            "loaded": True,
            "artifact_name": "p6-79.json",
            "summary": {"official_accuracy_pending": True, "business_confirmed": False},
        },
    )

    report = sync_sentinel_agent_v0.build_sync_sentinel_agent_v0()

    assert report["passed"] is True
    assert report["summary"]["sentinel_status"] == "blocked"
    assert report["summary"]["sync_ack_required"] is True
    assert report["summary"]["budget_hard_stop_scopes"] == 1
    assert report["summary"]["critical_count"] >= 3
    assert report["provider_calls"] is False
    assert report["write_db"] is False
    assert report["sync_triggered"] is False
    assert report["policy"]["no_manual_ack"] is True


def test_sync_sentinel_can_be_healthy_with_clean_sources(monkeypatch) -> None:
    monkeypatch.setattr(sync_sentinel_agent_v0.sync_status, "get_overview", lambda: _overview())
    monkeypatch.setattr(
        sync_sentinel_agent_v0,
        "_budget_caps",
        lambda: {"configured": True, "budgets": [], "summary": {"scopes": 0, "warnings": 0, "hard_stopped": 0}},
    )
    monkeypatch.setattr(
        sync_sentinel_agent_v0,
        "_open_alerts",
        lambda _limit: {"configured": True, "alerts": [], "summary": {"open_total": 0, "critical_open": 0, "warning_open": 0}},
    )
    monkeypatch.setattr(
        sync_sentinel_agent_v0,
        "_latest_p6_79",
        lambda _ops_dir: {"loaded": True, "artifact_name": "p6-79.json", "summary": {"official_accuracy_pending": False, "business_confirmed": True}},
    )

    report = sync_sentinel_agent_v0.build_sync_sentinel_agent_v0()

    assert report["passed"] is True
    assert report["summary"]["sentinel_status"] == "healthy"
    assert report["summary"]["signal_count"] == 0


def test_sync_sentinel_requires_p6_79_artifact(monkeypatch) -> None:
    monkeypatch.setattr(sync_sentinel_agent_v0.sync_status, "get_overview", lambda: _overview())
    monkeypatch.setattr(
        sync_sentinel_agent_v0,
        "_budget_caps",
        lambda: {"configured": True, "budgets": [], "summary": {"scopes": 0, "warnings": 0, "hard_stopped": 0}},
    )
    monkeypatch.setattr(
        sync_sentinel_agent_v0,
        "_open_alerts",
        lambda _limit: {"configured": True, "alerts": [], "summary": {"open_total": 0, "critical_open": 0, "warning_open": 0}},
    )
    monkeypatch.setattr(sync_sentinel_agent_v0, "_latest_p6_79", lambda _ops_dir: {"loaded": False, "summary": {}})

    report = sync_sentinel_agent_v0.build_sync_sentinel_agent_v0()

    assert report["passed"] is False
    assert report["checks"]["p6_79_loaded"] is False
    assert report["summary"]["sentinel_status"] == "degraded"
