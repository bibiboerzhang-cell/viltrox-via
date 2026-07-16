from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from types import SimpleNamespace
from typing import Any

import pytest

from app.services.scheduler import jobs
from app.services.system import runtime


@pytest.fixture(autouse=True)
def _enable_degraded_run_now_for_existing_contracts(monkeypatch) -> None:
    monkeypatch.setenv("VKPI_SCHEDULER_RUN_NOW_ENABLED", "1")


class _FakeScheduler:
    def __init__(self, *, running: bool = True, registered: dict[str, object] | None = None):
        self.running = running
        self._registered = registered or {}
        self.modified: list[tuple[str, dict[str, object]]] = []

    def get_job(self, job_id: str):
        return self._registered.get(job_id)

    def modify_job(self, job_id: str, **changes: object):
        self.modified.append((job_id, changes))
        return self._registered[job_id]


class _FakeCursor:
    def __init__(self, *, row: dict[str, Any] | None = None, rowcount: int = 1):
        self._row = row
        self.rowcount = rowcount

    def fetchone(self):
        return self._row


class _EnqueueConnection:
    def __init__(self):
        self.executed: list[tuple[str, tuple[Any, ...]]] = []
        self.commits = 0
        self.rollbacks = 0

    def execute(self, sql: str, params: tuple[Any, ...] = ()):
        self.executed.append((sql, params))
        return _FakeCursor(
            row={
                "id": 71,
                "task_key": params[0],
                "status": "queued",
                "created_at": datetime(2026, 7, 16, 12, 0, tzinfo=timezone.utc),
            }
        )

    def commit(self) -> None:
        self.commits += 1

    def rollback(self) -> None:
        self.rollbacks += 1


class _DispatchConnection:
    def __init__(self, rows: list[dict[str, Any]]):
        self.rows = list(rows)
        self.final_updates: list[tuple[Any, ...]] = []
        self.commits = 0
        self.rollbacks = 0

    def execute(self, sql: str, params: tuple[Any, ...] = ()):
        normalized = " ".join(sql.split())
        if normalized.startswith("SELECT id, task_key"):
            return _FakeCursor(row=self.rows.pop(0) if self.rows else None)
        if "SET claimed_at = NOW()" in normalized:
            return _FakeCursor(rowcount=1)
        if "SET status = ?" in normalized:
            self.final_updates.append(params)
            return _FakeCursor(rowcount=1)
        raise AssertionError(f"unexpected SQL: {normalized}")

    def commit(self) -> None:
        self.commits += 1

    def rollback(self) -> None:
        self.rollbacks += 1


def _run(job_id: str) -> dict:
    return asyncio.run(runtime.run_job_now(job_id))


def test_cross_process_run_now_is_disabled_by_default(monkeypatch) -> None:
    monkeypatch.delenv("VKPI_SCHEDULER_RUN_NOW_ENABLED", raising=False)
    monkeypatch.setattr(jobs, "_scheduler", None)

    result = _run("vkpi_ai_today_hot")

    assert result == {
        "job_id": "vkpi_ai_today_hot",
        "status": "disabled",
        "queued": False,
        "triggered": False,
        "reason": "run_now_ack_lease_not_ready",
        "error": "cross-process scheduler run-now is disabled",
    }


def test_run_job_now_reports_scheduler_not_started(monkeypatch) -> None:
    monkeypatch.setattr(jobs, "_scheduler", None)

    result = jobs.trigger_job_now("vkpi_ai_today_hot")

    assert result == {
        "job_id": "vkpi_ai_today_hot",
        "status": "not_started",
        "triggered": False,
        "error": "scheduler not started in this process",
    }


def test_runtime_run_job_now_queues_when_scheduler_is_in_another_process(monkeypatch) -> None:
    monkeypatch.setattr(jobs, "_scheduler", None)
    queued: list[str] = []

    def enqueue(job_id: str, **_kwargs: Any) -> dict[str, Any]:
        queued.append(job_id)
        return {
            "job_id": job_id,
            "request_id": 71,
            "status": "queued",
            "queued": True,
            "triggered": False,
        }

    monkeypatch.setattr(jobs, "enqueue_job_run_request", enqueue)

    result = _run("vkpi_ai_today_hot")

    assert result["status"] == "queued"
    assert result["request_id"] == 71
    assert queued == ["vkpi_ai_today_hot"]


def test_run_job_now_reports_missing_registered_job(monkeypatch) -> None:
    scheduler = _FakeScheduler(running=True)
    monkeypatch.setattr(jobs, "_scheduler", scheduler)

    result = _run("missing-job")

    assert result == {
        "job_id": "missing-job",
        "status": "not_found",
        "triggered": False,
        "error": "job not found",
    }
    assert scheduler.modified == []


def test_run_job_now_only_moves_next_run_time(monkeypatch) -> None:
    direct_calls: list[str] = []

    def provider_job() -> None:
        direct_calls.append("called")

    registered = SimpleNamespace(id="vkpi_ai_today_hot", func=provider_job)
    scheduler = _FakeScheduler(
        running=True,
        registered={"vkpi_ai_today_hot": registered},
    )
    monkeypatch.setattr(jobs, "_scheduler", scheduler)

    result = _run("  vkpi_ai_today_hot  ")

    assert result["job_id"] == "vkpi_ai_today_hot"
    assert result["status"] == "triggered"
    assert result["triggered"] is True
    assert result["scheduled_for"].endswith("Z")
    assert direct_calls == []
    assert len(scheduler.modified) == 1
    modified_job_id, changes = scheduler.modified[0]
    assert modified_job_id == "vkpi_ai_today_hot"
    next_run_time = changes["next_run_time"]
    assert next_run_time.tzinfo is not None
    assert result["scheduled_for"] == next_run_time.isoformat().replace("+00:00", "Z")


def test_enqueue_run_request_reports_unavailable_without_postgres(monkeypatch) -> None:
    monkeypatch.setattr(jobs, "is_postgres_runtime", lambda: False)

    result = jobs.enqueue_job_run_request("vkpi_ai_today_hot")

    assert result == {
        "job_id": "vkpi_ai_today_hot",
        "status": "unavailable",
        "queued": False,
        "triggered": False,
        "reason": "postgres_required",
        "error": "scheduler run request storage unavailable",
    }


def test_enqueue_run_request_reports_unavailable_before_migration_269(monkeypatch) -> None:
    conn = _EnqueueConnection()
    monkeypatch.setattr(jobs, "is_postgres_runtime", lambda: True)
    monkeypatch.setattr(jobs, "table_exists", lambda _name: False)
    monkeypatch.setattr(jobs, "get_conn", lambda: conn)

    result = jobs.enqueue_job_run_request("vkpi_ai_today_hot")

    assert result["status"] == "unavailable"
    assert result["reason"] == "migration_269_not_applied"
    assert result["queued"] is False
    assert conn.rollbacks == 1
    assert conn.executed == []


def test_enqueue_run_request_persists_one_bounded_queue_row(monkeypatch) -> None:
    conn = _EnqueueConnection()
    monkeypatch.setattr(jobs, "is_postgres_runtime", lambda: True)
    monkeypatch.setattr(jobs, "table_exists", lambda _name: True)
    monkeypatch.setattr(jobs, "get_conn", lambda: conn)

    result = jobs.enqueue_job_run_request("  vkpi_ai_today_hot  ")

    assert result == {
        "job_id": "vkpi_ai_today_hot",
        "request_id": 71,
        "status": "queued",
        "queued": True,
        "triggered": False,
        "queued_at": "2026-07-16T12:00:00Z",
    }
    assert conn.commits == 1
    sql, params = conn.executed[0]
    assert "ON CONFLICT (task_key) WHERE status = 'queued'" in sql
    assert params == ("vkpi_ai_today_hot", None)


def test_dispatch_run_request_claims_with_skip_locked_and_only_moves_schedule(monkeypatch) -> None:
    direct_calls: list[str] = []

    def provider_job() -> None:
        direct_calls.append("called")

    scheduler = _FakeScheduler(
        running=True,
        registered={
            "vkpi_ai_today_hot": SimpleNamespace(
                id="vkpi_ai_today_hot",
                func=provider_job,
            )
        },
    )
    conn = _DispatchConnection(
        [{"id": 71, "task_key": "vkpi_ai_today_hot"}],
    )
    monkeypatch.setattr(jobs, "_scheduler", scheduler)
    monkeypatch.setattr(jobs, "is_postgres_runtime", lambda: True)
    monkeypatch.setattr(jobs, "table_exists", lambda _name: True)
    monkeypatch.setattr(jobs, "get_conn", lambda: conn)

    result = jobs.dispatch_queued_run_requests(limit=10)

    assert result == {"status": "ok", "claimed": 1, "dispatched": 1, "errors": 0}
    assert direct_calls == []
    assert len(scheduler.modified) == 1
    assert conn.final_updates == [("dispatched", "dispatched", "", 71)]
    assert conn.commits == 2  # terminal dispatch plus empty-queue read transaction


def test_dispatch_run_request_rolls_back_when_leadership_changes(monkeypatch) -> None:
    scheduler = _FakeScheduler(running=True, registered={})
    conn = _DispatchConnection(
        [{"id": 72, "task_key": "vkpi_ai_today_hot"}],
    )
    monkeypatch.setattr(jobs, "_scheduler", scheduler)
    monkeypatch.setattr(jobs, "is_postgres_runtime", lambda: True)
    monkeypatch.setattr(jobs, "table_exists", lambda _name: True)
    monkeypatch.setattr(jobs, "get_conn", lambda: conn)
    monkeypatch.setattr(
        jobs,
        "trigger_job_now",
        lambda job_id: {
            "job_id": job_id,
            "status": "not_started",
            "triggered": False,
            "error": "scheduler not started in this process",
        },
    )

    result = jobs.dispatch_queued_run_requests(limit=1)

    assert result == {
        "status": "not_started",
        "claimed": 0,
        "dispatched": 0,
        "errors": 0,
    }
    assert conn.rollbacks == 1
    assert conn.final_updates == []
