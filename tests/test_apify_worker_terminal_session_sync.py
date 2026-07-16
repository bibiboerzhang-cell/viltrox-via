from __future__ import annotations

from contextlib import contextmanager
from typing import Any

from app.workers import apify_jobs_worker as worker
from app.workers import apify_jobs_worker_maintenance as maintenance
from app.workers import apify_jobs_worker_session as worker_session


class _StatusCursor:
    def __init__(self, row: dict[str, Any]) -> None:
        self.row = row

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def execute(self, _sql, _params=()):
        return None

    def fetchone(self):
        return self.row


class _StatusConn:
    def __init__(self, row: dict[str, Any]) -> None:
        self.row = row

    def cursor(self, **_kwargs):
        return _StatusCursor(self.row)


def _install_execution_stubs(monkeypatch) -> list[tuple[Any, ...]]:
    synced: list[tuple[Any, ...]] = []

    @contextmanager
    def scope():
        yield

    @contextmanager
    def heartbeat(*_args):
        yield

    monkeypatch.setattr(worker, "db_connection_sync_scope", scope)
    monkeypatch.setattr(worker, "_running_job_heartbeat", heartbeat)
    monkeypatch.setattr(worker, "_process_claimed_job", lambda _conn, _job: None)
    monkeypatch.setattr(worker, "acquire_provider_execution_claim", lambda *_args, **_kwargs: 17)
    monkeypatch.setattr(worker, "finalize_provider_execution_claim", lambda *_args, **_kwargs: True)
    monkeypatch.setattr(
        worker,
        "_sync_search_session_job",
        lambda *args, **kwargs: synced.append((*args, kwargs)) or True,
    )
    return synced


def test_execute_claimed_job_reduces_persisted_done_status(monkeypatch) -> None:
    synced = _install_execution_stubs(monkeypatch)

    worker._execute_claimed_job(
        _StatusConn({"status": "done", "last_error": ""}),
        {"id": 18502, "job_type": "kol_audience_stats_refresh", "lease_owner": "worker-a"},
    )

    assert len(synced) == 1
    assert synced[0][1] == 18502
    assert synced[0][-1] == {"raw_status": "done", "reason": ""}


def test_execute_claimed_job_reduces_blocked_status_with_reason(monkeypatch) -> None:
    synced = _install_execution_stubs(monkeypatch)

    worker._execute_claimed_job(
        _StatusConn(
            {"status": "blocked", "last_error": "readiness_not_production_ready"}
        ),
        {"id": 18472, "job_type": "video", "lease_owner": "worker-a"},
    )

    assert len(synced) == 1
    assert synced[0][-1] == {
        "raw_status": "blocked",
        "reason": "readiness_not_production_ready",
    }


def test_execute_claimed_job_does_not_terminalize_requeued_job(monkeypatch) -> None:
    synced = _install_execution_stubs(monkeypatch)

    worker._execute_claimed_job(
        _StatusConn({"status": "queued", "last_error": "resource slot busy"}),
        {"id": 18600, "job_type": "kol_pool_comments_collect", "lease_owner": "worker-a"},
    )

    assert synced == []


def test_session_sync_module_has_real_lineage_parser() -> None:
    """Guard the split worker module against a runtime-only missing import."""

    assert worker_session.search_session_lineages(
        {
            "search_session_id": 1089,
            "search_session_item_id": 2333,
            "search_session_role": "comments",
        }
    ) == [
        {
            "search_session_id": 1089,
            "search_session_item_id": 2333,
            "role": "comments",
        }
    ]


class _RepairCursor:
    def __init__(self, rows: list[dict[str, Any]], calls: list[tuple[str, tuple[Any, ...]]]) -> None:
        self.rows = rows
        self.calls = calls

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def execute(self, sql, params=()):
        self.calls.append((" ".join(str(sql).split()), tuple(params)))

    def fetchall(self):
        return self.rows


class _RepairConn:
    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self.rows = rows
        self.calls: list[tuple[str, tuple[Any, ...]]] = []

    def cursor(self, **_kwargs):
        return _RepairCursor(self.rows, self.calls)


def test_startup_repair_replays_only_selected_terminal_jobs(monkeypatch) -> None:
    conn = _RepairConn(
        [
            {"id": 18468, "status": "done", "last_error": None},
            {"id": 18472, "status": "blocked", "last_error": "model gate"},
        ]
    )
    replayed: list[tuple[int, str, str]] = []
    monkeypatch.setattr(
        maintenance,
        "_sync_search_session_job",
        lambda _conn, job_id, *, raw_status, reason: replayed.append(
            (job_id, raw_status, reason)
        )
        or True,
    )

    result = maintenance._reconcile_terminal_search_session_jobs(conn, limit=1000)

    assert [row["id"] for row in result] == [18468, 18472]
    assert replayed == [(18468, "done", ""), (18472, "blocked", "model gate")]
    assert "session.status='running'" in conn.calls[0][0]
    assert "job.status IN ('done', 'blocked', 'failed', 'triage')" in conn.calls[0][0]
    assert conn.calls[0][1] == (1000,)
