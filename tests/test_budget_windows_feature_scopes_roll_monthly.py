"""功能 scope(非 cron:/provider:/single_call*)必须按月滚,否则 cap 成终身额度(2026-08-23 prod audience_stats 事故)。"""
from __future__ import annotations

from datetime import datetime, timezone

from app.domains.costs.budget_windows import budget_window_kind, project_budget_window


def test_feature_scope_gets_monthly_anchor_and_rolls_next_month():
    now = datetime(2026, 8, 23, 2, 0, tzinfo=timezone.utc)
    row = {"scope": "audience_stats", "cap_usd": 10, "current_spend": 9.97, "reset_at": ""}
    projected, roll, zero = project_budget_window(row, now=now)
    assert roll is True and zero is False  # 首锚只记锚点,历史花费保留
    assert projected["reset_at"] == "2026-09-01T00:00:00Z"
    later = datetime(2026, 9, 1, 0, 0, 1, tzinfo=timezone.utc)
    projected2, roll2, zero2 = project_budget_window(projected, now=later)
    assert roll2 is True and zero2 is True and projected2["current_spend"] == 0
    assert projected2["reset_at"] == "2026-10-01T00:00:00Z"


def test_single_call_scopes_never_roll():
    now = datetime(2026, 8, 23, tzinfo=timezone.utc)
    for scope in ("single_call", "single_call_contract", "single_call_project_retrospective"):
        row = {"scope": scope, "cap_usd": 2, "current_spend": 0, "reset_at": ""}
        assert project_budget_window(row, now=now) == (row, False, False)


def test_cron_and_provider_semantics_unchanged():
    now = datetime(2026, 8, 23, 2, 0, tzinfo=timezone.utc)
    _, roll, zero = project_budget_window({"scope": "cron:x", "current_spend": 1, "reset_at": ""}, now=now)
    assert roll and zero
    _, roll, zero = project_budget_window({"scope": "provider:gemini", "current_spend": 1, "reset_at": ""}, now=now)
    assert roll and not zero


def test_budget_window_kind_is_the_shared_scope_contract():
    assert budget_window_kind("") == "none"
    assert budget_window_kind("single_call") == "per_call"
    assert budget_window_kind("single_call_contract") == "per_call"
    assert budget_window_kind("cron:vkpi_weekly_summary") == "daily"
    assert budget_window_kind("DASHBOARD:REPORT_ANALYSIS") == "daily"
    assert budget_window_kind("metric_tracking") == "monthly"
    assert budget_window_kind("agent_skill") == "monthly"
