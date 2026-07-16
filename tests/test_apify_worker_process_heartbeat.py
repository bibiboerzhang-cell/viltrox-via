from __future__ import annotations

import sys
from pathlib import Path
from typing import Any


BACKEND_ROOT = Path(__file__).resolve().parents[1] / "backend"
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.workers import apify_jobs_worker as worker  # noqa: E402


class _OneTickStopSignal:
    def __init__(self) -> None:
        self.calls = 0

    def wait(self, _seconds: float) -> bool:
        self.calls += 1
        return self.calls > 1


class _FakeCursor:
    def __init__(self) -> None:
        self.executions: list[tuple[str, tuple[Any, ...]]] = []
        self.rowcount = 1

    def __enter__(self) -> "_FakeCursor":
        return self

    def __exit__(self, *_args: object) -> None:
        return None

    def execute(self, statement: str, params: tuple[Any, ...]) -> None:
        self.executions.append((statement, params))


class _FakeConnection:
    def __init__(self) -> None:
        self.cursor_instance = _FakeCursor()

    def __enter__(self) -> "_FakeConnection":
        return self

    def __exit__(self, *_args: object) -> None:
        return None

    def cursor(self) -> _FakeCursor:
        return self.cursor_instance


def test_running_job_timer_renews_job_lease_and_process_heartbeat(monkeypatch) -> None:
    connection = _FakeConnection()
    heartbeat_connections: list[object] = []

    monkeypatch.setattr(worker.psycopg, "connect", lambda *_args, **_kwargs: connection)
    monkeypatch.setattr(
        worker,
        "_upsert_worker_heartbeat",
        lambda conn: heartbeat_connections.append(conn),
    )

    worker._heartbeat_running_job(
        42,
        "worker-a:123",
        "apify-job:42",
        9,
        _OneTickStopSignal(),
    )

    assert len(connection.cursor_instance.executions) == 2
    statement, params = connection.cursor_instance.executions[0]
    assert "lease_expires_at" in statement
    assert "status='running'" in statement
    assert "lease_owner=%s" in statement
    assert params == (worker.STALE_RECLAIM_SECONDS, 42, "worker-a:123")
    fence_statement, fence_params = connection.cursor_instance.executions[1]
    assert "vkpi_provider_execution_claims" in fence_statement
    assert fence_params == (
        worker.STALE_RECLAIM_SECONDS,
        "apify-job:42",
        9,
        "worker-a:123",
    )
    assert heartbeat_connections == [connection]
