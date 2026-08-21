from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta, timezone

from app.domains.metrics import aggregation


class _Result:
    def __init__(self, *, one=None, many=None):
        self._one = one
        self._many = many or []

    def fetchone(self):
        return self._one

    def fetchall(self):
        return self._many


class _WindowConn:
    def __init__(self):
        self.executions: list[tuple[str, tuple]] = []

    def execute(self, sql, params=()):
        compact = " ".join(str(sql).split())
        params = tuple(params)
        self.executions.append((compact, params))
        if "SELECT p.id FROM vkpi_projects" in compact:
            return _Result(one={"id": params[0]})
        if "FROM vkpi_cost_ledger" in compact:
            return _Result(one={"c": 1000})
        if "FROM vkpi_sales_attributions" in compact:
            return _Result(
                many=[{"currency": "USD", "rev": 3000, "com": 300, "n": 1}]
            )
        if "FROM vkpi_project_content_posts" in compact:
            return _Result(one={"n": 2})
        raise AssertionError(compact)


def test_project_window_is_utc_half_open_and_applied_to_every_metric(monkeypatch):
    conn = _WindowConn()
    local_now = datetime(2026, 8, 21, 8, 30, tzinfo=timezone(timedelta(hours=8)))
    expected_end = datetime(2026, 8, 21, 0, 30, tzinfo=timezone.utc)
    expected_start = datetime(2026, 8, 14, 0, 30, tzinfo=timezone.utc)
    monkeypatch.setattr(aggregation, "get_conn", lambda: conn)
    monkeypatch.setattr(aggregation, "table_exists", lambda _name: True)
    monkeypatch.setattr(aggregation, "_utcnow", lambda: local_now)
    monkeypatch.setattr(aggregation, "is_postgres_runtime", lambda: False)
    monkeypatch.setattr(aggregation.scope, "project_filter", lambda *_args, **_kwargs: ("", []))

    result = aggregation.aggregate_project_metrics(
        42,
        window_days=7,
        staff={"id": 7, "role": "employee"},
    )

    assert result["status"] == "ready"
    assert result["data_window_days"] == 7
    assert result["window_start"] == expected_start.isoformat()
    assert result["window_end"] == expected_end.isoformat()
    assert result["window_timezone"] == "UTC"
    assert result["window_boundary"] == "[start,end)"

    metric_queries = [item for item in conn.executions if "SELECT p.id FROM vkpi_projects" not in item[0]]
    assert len(metric_queries) == 3
    expected_columns = ("incurred_at", "occurred_at", "published_at")
    for (sql, params), column in zip(metric_queries, expected_columns):
        assert f"datetime({column}) >= datetime(?)" in sql
        assert f"datetime({column}) < datetime(?)" in sql
        assert params == (
            42,
            expected_start.strftime("%Y-%m-%dT%H:%M:%S.%fZ"),
            expected_end.strftime("%Y-%m-%dT%H:%M:%S.%fZ"),
        )


def test_window_bounds_clamp_and_treat_naive_now_as_utc():
    naive_now = datetime(2026, 1, 31, 12, 0)

    days, start, end = aggregation._window_bounds(999, now=naive_now)

    assert days == 365
    assert end == datetime(2026, 1, 31, 12, 0, tzinfo=timezone.utc)
    assert start == end - timedelta(days=365)

    low_days, low_start, low_end = aggregation._window_bounds(0, now=naive_now)
    assert low_days == 1
    assert low_start == low_end - timedelta(days=1)


def test_postgres_window_keeps_timestamptz_half_open_comparison(monkeypatch):
    start = datetime(2026, 8, 21, 0, 0, tzinfo=timezone.utc)
    end = datetime(2026, 8, 22, 0, 0, tzinfo=timezone.utc)
    params: list[object] = [42]
    monkeypatch.setattr(aggregation, "is_postgres_runtime", lambda: True)

    clause = aggregation._time_clause("occurred_at", params, start, end)

    assert clause == " AND occurred_at >= ? AND occurred_at < ?"
    assert params == [42, start, end]


def test_financial_aggregates_fail_closed_without_staff(monkeypatch):
    def unexpected_connection():
        raise AssertionError("missing staff must not reach the database")

    monkeypatch.setattr(aggregation, "get_conn", unexpected_connection)

    assert aggregation.aggregate_project_metrics(42, staff=None) == {
        "status": "not_found",
        "scope": "project",
    }
    assert aggregation.aggregate_portfolio_metrics(staff=None) == {
        "status": "unavailable",
        "scope": "portfolio",
        "reason": "staff_scope_unavailable",
    }


def test_sqlite_text_window_normalizes_z_offsets_and_excludes_right_boundary(monkeypatch):
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute(
        "CREATE TABLE vkpi_project_content_posts "
        "(status TEXT NOT NULL, project_id INTEGER, published_at TEXT)"
    )
    conn.executemany(
        "INSERT INTO vkpi_project_content_posts(status, project_id, published_at) VALUES (?, ?, ?)",
        [
            ("matched", 1, "2026-08-21T00:00:00Z"),
            ("matched", 1, "2026-08-21T00:30:00Z"),
            ("matched", 1, "2026-08-21T02:30:00+02:00"),
            ("matched", 1, "2026-08-21T01:00:00Z"),
            ("matched", 1, "2026-08-21T02:00:00+01:00"),
            ("matched", 1, "2026-08-20T23:59:59Z"),
        ],
    )
    monkeypatch.setattr(aggregation, "get_conn", lambda: conn)
    monkeypatch.setattr(aggregation, "table_exists", lambda name: name == "vkpi_project_content_posts")
    monkeypatch.setattr(aggregation, "is_postgres_runtime", lambda: False)
    start = datetime(2026, 8, 21, 0, 0, tzinfo=timezone.utc)
    end = datetime(2026, 8, 21, 1, 0, tzinfo=timezone.utc)

    count = aggregation._count_exposure("AND project_id = ?", [1], window_start=start, window_end=end)

    assert count == 3
