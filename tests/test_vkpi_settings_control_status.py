from __future__ import annotations

from typing import Any

import importlib

platform_crawl_settings = importlib.import_module("app.domains.settings.platform_crawl")


class FakeRefreshPlanner:
    def __init__(self, result: dict[str, Any] | None = None) -> None:
        self.result = result or {}
        self.calls: list[dict[str, Any]] = []

    def plan(self, **kwargs: Any) -> dict[str, Any]:
        self.calls.append(kwargs)
        return dict(self.result)


class FakeConn:
    def execute(self, sql: str, params: tuple[Any, ...] = ()):
        if "COUNT(*) AS n FROM vkpi_kol_pool" in sql:
            return FakeCursor([{"n": 1023}])
        if "FROM vkpi_kol_refresh_tier" in sql:
            return FakeCursor([
                {"tier": "hot", "n": 92, "never_refreshed": 0, "searched_rows": 2, "search_count_30d": 3},
                {"tier": "cold", "n": 931, "never_refreshed": 931, "searched_rows": 0, "search_count_30d": 0},
            ])
        if "FROM job_execution_ledger" in sql:
            assert params[0] == platform_crawl_settings.ON_DEMAND_TASK_TYPE
            return FakeCursor([
                {"status": "queued", "n": 1},
                {"status": "done", "n": 4},
            ])
        raise AssertionError(f"unexpected SQL: {sql}")


class FakeCursor:
    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self.rows = rows

    def fetchone(self):
        return self.rows[0] if self.rows else None

    def fetchall(self):
        return self.rows


def test_kol_refresh_status_reports_tier_and_task_gate(monkeypatch) -> None:
    monkeypatch.delenv("VKPI_KOL_ON_DEMAND_REFRESH_ENABLED", raising=False)
    monkeypatch.setattr(platform_crawl_settings, "_table_exists", lambda table: True)
    monkeypatch.setattr(platform_crawl_settings, "get_conn", lambda: FakeConn())
    planner = FakeRefreshPlanner(
        {
            "mode": "plan_only",
            "execution_enabled": False,
            "reason": "apify_batch_execution_not_enabled",
            "selector_ready": True,
            "stale_before": "2026-05-22T00:00:00Z",
            "max_posts": 1,
            "max_concurrent_runs": 2,
            "source_total": 92,
            "total_targets": 7,
            "batch_count": 2,
            "platforms": {"youtube": 5, "instagram": 2},
        }
    )

    status = platform_crawl_settings._kol_refresh_status(refresh_planner=planner)

    assert status["mode"] == "searchable_records_only"
    assert status["provider_gate_enabled"] is False
    assert status["kol_pool_total"] == 1023
    assert status["hot_count"] == 92
    assert status["cold_count"] == 931
    assert status["cold_never_refreshed"] == 931
    assert status["search_count_30d"] == 3
    assert status["active_on_demand_tasks"] == 1
    assert status["status_counts"] == {"queued": 1, "done": 4}
    assert status["apify_batch_plan"]["mode"] == "plan_only"
    assert status["apify_batch_plan"]["execution_enabled"] is False
    assert status["apify_batch_plan"]["source_total"] == 92
    assert status["apify_batch_plan"]["target_count"] == 7
    assert status["apify_batch_plan"]["batch_count"] == 2
    assert status["apify_batch_plan"]["max_concurrent_runs"] == 2
    assert status["apify_batch_plan"]["platforms"] == {"youtube": 5, "instagram": 2}
    assert planner.calls == [
        {
            "limit": 200,
            "stale_days": 1,
            "tiers": {"hot"},
            "max_posts": 1,
            "max_concurrent": 2,
        }
    ]


def test_kol_refresh_status_exposes_enabled_mode(monkeypatch) -> None:
    monkeypatch.setenv("VKPI_KOL_ON_DEMAND_REFRESH_ENABLED", "1")
    monkeypatch.setattr(platform_crawl_settings, "_table_exists", lambda table: False)

    planner = FakeRefreshPlanner()
    status = platform_crawl_settings._kol_refresh_status(refresh_planner=planner)

    assert status["mode"] == "stale_while_revalidate_enabled"
    assert status["provider_gate_enabled"] is True
    assert status["provider_calls_default"] is True
    assert planner.calls == []


def test_kol_refresh_status_keeps_planner_failure_in_status(monkeypatch) -> None:
    class FailingPlanner:
        def plan(self, **kwargs: Any) -> dict[str, Any]:
            raise RuntimeError("planner unavailable")

    monkeypatch.setattr(platform_crawl_settings, "_table_exists", lambda table: True)
    monkeypatch.setattr(platform_crawl_settings, "get_conn", lambda: FakeConn())

    status = platform_crawl_settings._kol_refresh_status(refresh_planner=FailingPlanner())

    assert status["error"] == "RuntimeError: planner unavailable"
    assert status["apify_batch_plan"]["mode"] == "plan_only"
    assert status["apify_batch_plan"]["execution_enabled"] is False
