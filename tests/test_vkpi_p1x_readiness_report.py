from __future__ import annotations

import asyncio

from scripts import vkpi_p1x_readiness_report


def _selector_report(*, passed: bool = True) -> dict:
    return {
        "passed": passed,
        "provider_calls": False,
        "sync_triggered": False,
        "checks": {"timer_official_only": True},
        "stored": {
            "tiers": {
                "hot": {"count": 92},
                "cold": {"count": 931},
            }
        },
        "selector": {"qualified": {"source_total": 92}},
    }


def _on_demand_report(*, passed: bool = True) -> dict:
    return {
        "passed": passed,
        "provider_calls": False,
        "sync_triggered": False,
        "task_enqueued": False,
        "checks": {"timer_official_only": True},
        "policy": {"provider_gate_enabled": False},
        "tasks": {"active_count": 0},
        "tier": {"searched_rows_total": 0, "search_count_30d_total": 0},
    }


def _batch_report(*, provider_allowed: bool = False) -> dict:
    return {
        "mode": "plan_with_blocked_executor",
        "provider_calls_allowed": provider_allowed,
        "provider_gate": {"reason": "provider_calls_not_requested"},
        "plan": {
            "mode": "plan_only",
            "execution_enabled": False,
            "selector_ready": True,
            "source_total": 92,
            "total_targets": 0,
            "batch_count": 0,
            "max_concurrent_runs": 2,
            "platforms": {},
        },
        "execution": {"executed": False},
        "operator_summary": {
            "readiness": "blocked_provider_calls",
            "provider_gate_reason": "provider_calls_not_requested",
            "provider_calls_allowed": provider_allowed,
            "execution_preflight_status": "no_targets_to_execute",
            "selector_ready": True,
            "source_total": 92,
            "target_count": 0,
            "batch_count": 0,
            "safe_window_count": 0,
            "platforms": {},
        },
    }


def test_p1x_readiness_report_passes_without_side_effects(monkeypatch) -> None:
    async def fake_batch(_args):
        return _batch_report()

    monkeypatch.setattr(vkpi_p1x_readiness_report.vkpi_refresh_tier_acceptance, "build_report", lambda **_kwargs: _selector_report())
    monkeypatch.setattr(vkpi_p1x_readiness_report.vkpi_on_demand_refresh_acceptance, "build_report", lambda **_kwargs: _on_demand_report())
    monkeypatch.setattr(vkpi_p1x_readiness_report.vkpi_apify_batch_refresh, "run_from_args", fake_batch)

    report = asyncio.run(
        vkpi_p1x_readiness_report.build_report(
            timer_command="scripts/cron_daily_sync.py --official-max-posts 50 --skip-kol",
            daily_service_active="inactive",
            daily_timer_active="active",
            qualified_timer_enabled="not-found",
        )
    )

    assert report["passed"] is True
    assert report["provider_calls"] is False
    assert report["sync_triggered"] is False
    assert report["task_enqueued"] is False
    assert report["checks"]["provider_calls_blocked"] is True
    assert report["checks"]["qualified_timer_not_enabled"] is True
    markdown = vkpi_p1x_readiness_report.render_markdown(report)
    assert "V-KPI P1.X Readiness Report" in markdown
    assert "targets=0" in markdown


def test_p1x_readiness_report_fails_when_provider_or_timer_gate_is_open(monkeypatch) -> None:
    async def fake_batch(_args):
        return _batch_report(provider_allowed=True)

    monkeypatch.setattr(vkpi_p1x_readiness_report.vkpi_refresh_tier_acceptance, "build_report", lambda **_kwargs: _selector_report())
    monkeypatch.setattr(vkpi_p1x_readiness_report.vkpi_on_demand_refresh_acceptance, "build_report", lambda **_kwargs: _on_demand_report())
    monkeypatch.setattr(vkpi_p1x_readiness_report.vkpi_apify_batch_refresh, "run_from_args", fake_batch)

    report = asyncio.run(
        vkpi_p1x_readiness_report.build_report(
            daily_service_active="active",
            daily_timer_active="active",
            qualified_timer_enabled="enabled",
        )
    )

    assert report["passed"] is False
    assert report["checks"]["provider_calls_blocked"] is False
    assert report["checks"]["daily_service_not_active"] is False
    assert report["checks"]["qualified_timer_not_enabled"] is False
