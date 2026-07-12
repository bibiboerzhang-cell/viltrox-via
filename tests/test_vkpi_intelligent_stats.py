"""Intelligent 问答 · /stats 综合车道留痕统计单测(零真 DB)。

照 test_baseline_forecast_dualwrite 模式:monkeypatch app.db.connection 的
table_exists / get_conn 注入假连接。诚实态宪法:缺表 empty / 异常 error /
有留痕 ready 全字段(total + last_at + by_day UTC 日界),绝不 500。
"""
from __future__ import annotations

from datetime import date, datetime, timezone

from app.api.routers import vkpi_intelligent


# ── 假连接:按 SQL 关键字分流(头行 COUNT+MAX vs 按日 GROUP BY)────────────


class _FakeCursor:
    def __init__(self, rows: list[dict]):
        self._rows = rows

    def fetchall(self) -> list[dict]:
        return self._rows

    def fetchone(self) -> dict | None:
        return self._rows[0] if self._rows else None


class _FakeConn:
    def __init__(self, head: dict, days: list[dict]):
        self._head = head
        self._days = days
        self.params_seen: list[tuple] = []

    def execute(self, sql: str, params: tuple = ()) -> _FakeCursor:
        self.params_seen.append(params)
        if "GROUP BY" in sql:
            return _FakeCursor(self._days)
        return _FakeCursor([self._head])


def _patch_db(monkeypatch, *, exists: bool, conn=None) -> None:
    import app.db.connection as connection

    monkeypatch.setattr(connection, "table_exists", lambda name: exists)
    monkeypatch.setattr(connection, "get_conn", lambda: conn)


def test_stats_table_missing_is_honest_empty(monkeypatch):
    _patch_db(monkeypatch, exists=False)
    out = vkpi_intelligent._synth_call_stats()
    assert out["status"] == "empty"
    assert "vkpi_llm_calls" in out["reason"]


def test_stats_ready_with_rows(monkeypatch):
    last = datetime(2026, 7, 8, 13, 7, 57, tzinfo=timezone.utc)
    conn = _FakeConn(
        head={"n": 5, "last_at": last},
        days=[{"day": date(2026, 7, 7), "n": 3}, {"day": date(2026, 7, 8), "n": 2}],
    )
    _patch_db(monkeypatch, exists=True, conn=conn)
    out = vkpi_intelligent._synth_call_stats()
    assert out["status"] == "ready"
    assert out["total"] == 5
    assert out["last_at"] and "2026-07-08" in out["last_at"]
    assert out["by_day"] == [
        {"date": "2026-07-07", "count": 3},
        {"date": "2026-07-08", "count": 2},
    ]
    # 两条查询都用 purpose 占位参数(cost scope),零字面拼接
    assert all(p == (vkpi_intelligent._SYNTH_BUDGET_SCOPE,) for p in conn.params_seen)


def test_stats_zero_rows_is_real_zero(monkeypatch):
    conn = _FakeConn(head={"n": 0, "last_at": None}, days=[])
    _patch_db(monkeypatch, exists=True, conn=conn)
    out = vkpi_intelligent._synth_call_stats()
    assert out["status"] == "ready"
    assert out["total"] == 0
    assert out["last_at"] is None
    assert out["by_day"] == []


def test_stats_db_error_is_honest_error(monkeypatch):
    class _Boom:
        def execute(self, sql: str, params: tuple = ()):
            raise RuntimeError("db down")

    _patch_db(monkeypatch, exists=True, conn=_Boom())
    out = vkpi_intelligent._synth_call_stats()
    assert out["status"] == "error"
    assert "db down" in out["reason"]


def test_stats_endpoint_never_raises(monkeypatch):
    _patch_db(monkeypatch, exists=False)
    out = vkpi_intelligent.intelligent_stats(staff={"id": 1})
    assert out["status"] in {"empty", "error", "ready"}
