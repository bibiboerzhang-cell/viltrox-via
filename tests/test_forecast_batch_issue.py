"""预测批量发射日任务:MY KOL x 活跃 SKU,按 (kol, sku, day) 幂等,零 LLM。hermetic 假连接按表名路由。"""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone

from app.services.scheduler import jobs_forecast_batch as jfb


class _Cursor:
    def __init__(self, rows):
        self._rows = rows

    def fetchall(self):
        return self._rows

    def fetchone(self):
        return self._rows[0] if self._rows else None


class _Conn:
    def __init__(self, *, launches, favorites, members, issued):
        self.launches, self.favorites, self.members, self.issued = launches, favorites, members, issued

    def execute(self, sql: str, params: tuple = ()):
        if "FROM vkpi_product_launches" in sql:
            return _Cursor(self.launches)
        if "FROM vkpi_kol_pool_favorites" in sql:
            return _Cursor([{"kol_pool_id": i} for i in self.favorites])
        if "FROM vkpi_kol_pool_members" in sql:
            return _Cursor([{"kol_pool_id": i} for i in self.members])
        if "FROM vkpi_forecast_log" in sql:
            return _Cursor([{"kol_pool_id": k, "sku": s} for k, s in self.issued])
        return _Cursor([])

    def commit(self):
        return None


def _wire(monkeypatch, conn: _Conn, *, ready: set[int] | None = None):
    import app.db.connection as connection

    monkeypatch.setattr(connection, "table_exists", lambda name: True)
    monkeypatch.setattr(connection, "get_conn", lambda: conn)
    calls: list[tuple[int, str, str, bool]] = []

    def _fake_forecast(kol_pool_id, sku=None, *, conn=None, context="drawer", dry_run=False):
        calls.append((int(kol_pool_id), str(sku), context, bool(dry_run)))
        if int(kol_pool_id) == 99:
            raise LookupError("missing")
        if ready is not None and int(kol_pool_id) not in ready:
            return {"status": "insufficient"}
        return {"status": "ready", "expected_views_p50": 100}

    from app.domains.kol import performance_forecast

    monkeypatch.setattr(performance_forecast, "forecast_for_kol", _fake_forecast)
    return calls


NOW = datetime(2026, 8, 23, 12, 0, tzinfo=timezone.utc)


def test_active_launch_filters_archived_expired_and_dedupes_sku() -> None:
    launches = [
        {"id": 1, "product_sku": "AF-85", "status": "active", "launch_window_end": None},
        {"id": 2, "product_sku": "AF-85", "status": "draft", "launch_window_end": None},  # 同 SKU 去重
        {"id": 3, "product_sku": "AF-16", "status": "archived", "launch_window_end": None},
        {"id": 4, "product_sku": "AF-27", "status": "active", "launch_window_end": NOW - timedelta(days=60)},  # 过期
        {"id": 5, "product_sku": "AF-56", "status": "active", "launch_window_end": NOW - timedelta(days=3)},
        {"id": 6, "product_sku": "", "status": "active", "launch_window_end": None},
    ]
    conn = _Conn(launches=launches, favorites=[], members=[], issued=[])
    import app.db.connection as connection

    prev = connection.table_exists
    connection.table_exists = lambda name: True
    try:
        out = jfb.active_launch_skus(conn, now=NOW)
    finally:
        connection.table_exists = prev
    assert [x["sku"] for x in out] == ["AF-85", "AF-56"]


def test_batch_issues_pairs_idempotently_and_counts(monkeypatch) -> None:
    conn = _Conn(
        launches=[{"id": 1, "product_sku": "AF-85", "status": "active", "launch_window_end": None},
                  {"id": 2, "product_sku": "AF-16", "status": "active", "launch_window_end": None}],
        favorites=[11, 12], members=[12, 13, 99],
        issued=[(11, "AF-85")],  # 今天人工已点开过 → 跳过
    )
    calls = _wire(monkeypatch, conn, ready={11, 12})
    out = jfb.run_forecast_batch()
    assert out["status"] == "ok" and out["kols"] == 4 and out["skus"] == 2 and out["pairs"] == 8
    assert out["skipped_issued_today"] == 1
    assert out["issued"] == 3  # 11xAF-16, 12xAF-85, 12xAF-16
    assert out["not_ready"] == 2  # 13 两个 SKU 样本不足
    assert out["failed"] == 2  # 99 LookupError
    assert all(c[2] == "batch" and c[3] is False for c in calls)
    assert (11, "AF-85") not in {(c[0], c[1]) for c in calls}
    assert jfb.BATCH_CONTEXT == "batch"
    from app.domains.kol import performance_forecast

    assert "batch" in performance_forecast.LOG_CONTEXTS


def test_batch_truncates_at_budget_and_dry_run_passthrough(monkeypatch) -> None:
    conn = _Conn(
        launches=[{"id": 1, "product_sku": "AF-85", "status": "active", "launch_window_end": None}],
        favorites=[1, 2, 3, 4], members=[], issued=[],
    )
    calls = _wire(monkeypatch, conn)
    out = jfb.run_forecast_batch(max_pairs=2, dry_run=True)
    assert out["issued"] == 2 and out["truncated"] is True and out["pairs"] == 4
    assert len(calls) == 2 and all(c[3] is True for c in calls)


def test_batch_empty_when_no_launch_or_no_kol(monkeypatch) -> None:
    conn = _Conn(launches=[], favorites=[1], members=[], issued=[])
    _wire(monkeypatch, conn)
    out = jfb.run_forecast_batch()
    assert out["status"] == "empty" and out["issued"] == 0


def test_job_gate_default_off(monkeypatch) -> None:
    monkeypatch.setattr(jfb, "_scheduler_task_enabled", lambda key, default=False: False)
    assert asyncio.run(jfb.job_vkpi_forecast_batch_issue()) is None


def test_job_records_run_when_enabled(monkeypatch) -> None:
    recorded: list[tuple[str, bool]] = []
    monkeypatch.setattr(jfb, "_scheduler_task_enabled", lambda key, default=False: True)
    monkeypatch.setattr(jfb, "_record_scheduler_run", lambda key, *, ok, error="": recorded.append((key, ok)))
    monkeypatch.setattr(jfb, "run_forecast_batch", lambda: {"status": "ok", "issued": 1})
    out = asyncio.run(jfb.job_vkpi_forecast_batch_issue())
    assert out == {"status": "ok", "issued": 1} and recorded == [(jfb.TASK_KEY, True)]
