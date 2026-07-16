from __future__ import annotations

from typing import Any

from app.workers import apify_jobs_worker as worker


class _Cursor:
    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self.rows = list(rows)
        self.calls: list[tuple[str, tuple[Any, ...]]] = []

    def __enter__(self) -> "_Cursor":
        return self

    def __exit__(self, *_args: object) -> None:
        return None

    def execute(self, sql: str, params: tuple[Any, ...] = ()) -> None:
        self.calls.append((str(sql), tuple(params)))

    def fetchone(self) -> dict[str, Any]:
        return self.rows.pop(0)


class _Connection:
    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self.cursor_value = _Cursor(rows)

    def cursor(self, **_kwargs: Any) -> _Cursor:
        return self.cursor_value


def test_gemini_start_rate_uses_shared_postgres_clock_and_state(monkeypatch) -> None:
    conn = _Connection(
        [
            {"locked": True},
            {"now_epoch": 102.0, "value_json": '{"last_started_at_epoch":100.0}'},
            {"now_epoch": 104.0},
        ]
    )
    sleeps: list[float] = []
    unlocks: list[tuple[str, str]] = []
    monkeypatch.setattr(worker, "GEMINI_MIN_INTERVAL_SECONDS", 4.0)
    monkeypatch.setattr(worker.time, "sleep", lambda seconds: sleeps.append(seconds))
    monkeypatch.setattr(
        worker,
        "_advisory_unlock",
        lambda _conn, scope, key: unlocks.append((scope, key)),
    )

    worker._respect_gemini_qps(conn)  # type: ignore[arg-type]

    assert sleeps == [2.0]
    assert any("persistent_cache" in sql and "INSERT" in sql for sql, _ in conn.cursor_value.calls)
    assert unlocks == [(worker._GEMINI_QPS_SCOPE, worker._GEMINI_QPS_KEY)]


def test_gemini_start_rate_does_not_sleep_after_idle_period(monkeypatch) -> None:
    conn = _Connection(
        [
            {"locked": True},
            {"now_epoch": 200.0, "value_json": '{"last_started_at_epoch":100.0}'},
            {"now_epoch": 200.0},
        ]
    )
    sleeps: list[float] = []
    monkeypatch.setattr(worker, "GEMINI_MIN_INTERVAL_SECONDS", 4.0)
    monkeypatch.setattr(worker.time, "sleep", lambda seconds: sleeps.append(seconds))
    monkeypatch.setattr(worker, "_advisory_unlock", lambda *_args: None)

    worker._respect_gemini_qps(conn)  # type: ignore[arg-type]

    assert sleeps == []
