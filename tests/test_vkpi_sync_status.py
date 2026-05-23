"""Sync overview should expose daily sync guard state honestly."""
from __future__ import annotations

import json

from app.services.vkpi import sync_status


class _Rows:
    def __init__(self, rows: list[dict[str, object]]) -> None:
        self._rows = rows

    def fetchall(self) -> list[dict[str, object]]:
        return self._rows


class _Conn:
    def __init__(self, rows: list[dict[str, object]]) -> None:
        self.rows = rows
        self.queries: list[str] = []

    def execute(self, sql: str, *_args: object, **_kwargs: object) -> _Rows:
        self.queries.append(sql)
        return _Rows(self.rows)


def test_daily_sync_status_exposes_blocking_run_and_failure_rate(monkeypatch) -> None:
    row = {
        "run_id": "daily_incremental_sync_20260522_summary",
        "job_name": "daily_incremental_sync",
        "stage": "daily_summary",
        "started_at": "2026-05-22T10:00:00Z",
        "finished_at": "2026-05-22T10:30:00Z",
        "status": "failed",
        "total_targets": 10,
        "last_success_index": 10,
        "interrupted_at_index": None,
        "interrupted_kol_pool_id": None,
        "reason": "failure_rate_threshold_exceeded",
        "error_type": "other",
        "error_class": "",
        "error_message": "",
        "summary_json": json.dumps({
            "official": {"requested": 0, "failed": 0},
            "kol_pool_light": {"requested": 10, "errors": 2},
        }),
    }
    blocking = {
        "run_id": row["run_id"],
        "status": "failed",
        "reason": "failure_rate_threshold_exceeded",
        "ack_required": True,
    }

    conn = _Conn([row])
    monkeypatch.setattr(sync_status, "get_conn", lambda: conn)
    monkeypatch.setattr(sync_status.daily_sync, "_blocking_sync_run", lambda _scope: blocking)
    monkeypatch.setattr(sync_status.daily_sync, "_latest_sync_ack", lambda _scope: None)

    result = sync_status._daily_sync_status()

    assert result["guard_allowed"] is False
    assert result["ack_required"] is True
    assert result["blocking_run"] == blocking
    assert result["latest_summary"]["status"] == "failed"
    assert result["latest_summary"]["health"]["failure_rate"] == 0.2
    assert result["latest_summary"]["health"]["blocked_next_run"] is True
    assert "ORDER BY started_at DESC NULLS LAST, created_at DESC NULLS LAST" in conn.queries[0]


def test_summary_health_marks_daily_sync_blocker_as_critical(monkeypatch) -> None:
    monkeypatch.setattr(sync_status, "_industry_status", lambda: {"last_24h_failed": 0})
    monkeypatch.setattr(sync_status, "_platform_settings_status", lambda: {"_global_budgets": []})
    monkeypatch.setattr(sync_status, "_daily_sync_status", lambda: {
        "ack_required": True,
        "blocking_run": {"run_id": "blocked-run"},
    })

    result = sync_status._summary_health()

    assert result["overall_health"] == "down"
    assert result["issues"][0]["category"] == "daily_sync"
    assert "blocked-run" in result["issues"][0]["message"]


def test_shopify_status_uses_current_reconciliation_schema(monkeypatch) -> None:
    row = {
        "id": 1,
        "started_at": "2026-05-23T10:00:00Z",
        "completed_at": "2026-05-23T10:01:00Z",
        "status": "success",
        "orders_received": 3,
        "orders_matched": 2,
        "orders_unmatched": 1,
        "orders_failed": 0,
        "error_message": "",
    }
    conn = _Conn([row])
    monkeypatch.setattr(sync_status, "get_conn", lambda: conn)

    result = sync_status._shopify_status()

    assert result["last_run_status"] == "success"
    assert result["recent_runs"][0]["orders_received"] == 3
    assert "orders_received" in conn.queries[0]
    assert "total_orders" not in conn.queries[0]
