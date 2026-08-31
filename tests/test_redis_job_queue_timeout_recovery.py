"""Redis execution-timeout transaction and PostgreSQL race regressions."""
from __future__ import annotations

import sqlite3
import threading
import time
import uuid

import pytest

from app.db.connection import PostgresCompatConnection
from app.services.jobs import queue as queue_mod
from tests.redis_job_queue_test_support import ledger_conn, queue, seed  # noqa: F401


# ---------------------------------------------------------------- execution timeout


def test_timeout_sweep_only_terminalizes_started_executions(queue, ledger_conn):
    old = "2000-01-01T00:00:00Z"
    recent = queue_mod._utcnow()
    seed(ledger_conn, "queued-old", status="queued", timeout_seconds=1)
    seed(ledger_conn, "retrying-old", status="retrying", started_at=old, timeout_seconds=1)
    seed(ledger_conn, "processing-startless", status="processing", timeout_seconds=1)
    seed(ledger_conn, "processing-expired", status="processing", started_at=old, timeout_seconds=1)
    seed(ledger_conn, "running-expired", status="running", started_at=old, timeout_seconds=1)
    seed(ledger_conn, "processing-recent", status="processing", started_at=recent, timeout_seconds=3600)
    for task_id in (
        "queued-old",
        "retrying-old",
        "processing-startless",
        "processing-expired",
        "running-expired",
        "processing-recent",
    ):
        ledger_conn.execute(
            "INSERT INTO vkpi_async_task_items (task_id, status, error, updated_at) VALUES (?, 'pending', '', ?)",
            (task_id, old),
        )
    ledger_conn.commit()

    assert queue._mark_timed_out_jobs() == 2

    statuses = {
        row["task_id"]: row["status"]
        for row in ledger_conn.execute("SELECT task_id, status FROM job_execution_ledger").fetchall()
    }
    assert statuses == {
        "queued-old": "queued",
        "retrying-old": "retrying",
        "processing-startless": "processing",
        "processing-expired": "timeout",
        "running-expired": "timeout",
        "processing-recent": "processing",
    }
    item_statuses = {
        row["task_id"]: row["status"]
        for row in ledger_conn.execute("SELECT task_id, status FROM vkpi_async_task_items").fetchall()
    }
    assert item_statuses["processing-expired"] == "failed"
    assert item_statuses["running-expired"] == "failed"
    assert item_statuses["queued-old"] == "pending"
    assert item_statuses["retrying-old"] == "pending"
    summary = queue._ledger_queue_summary()
    assert summary["waiting"] == 2
    assert summary["oldest_waiting_age_seconds"] > 0


def test_retry_wait_is_not_charged_to_next_execution_timeout(queue, ledger_conn):
    old = "2000-01-01T00:00:00Z"
    seed(
        ledger_conn,
        "retry-reclaimed",
        status="retrying",
        started_at=old,
        timeout_seconds=60,
    )

    snapshot = queue._update_job_ledger("retry-reclaimed", "processing")

    assert snapshot and snapshot["status"] == "processing"
    row = ledger_conn.execute(
        "SELECT started_at FROM job_execution_ledger WHERE task_id='retry-reclaimed'"
    ).fetchone()
    assert row["started_at"] != old
    assert queue._mark_timed_out_jobs() == 0


def test_timeout_sweep_cas_does_not_overwrite_concurrent_terminal(
    queue,
    ledger_conn,
    monkeypatch,
):
    old = "2000-01-01T00:00:00Z"
    seed(
        ledger_conn,
        "race-finished",
        status="processing",
        started_at=old,
        timeout_seconds=1,
    )
    ledger_conn.execute(
        "INSERT INTO vkpi_async_task_items (task_id, status, error, updated_at) VALUES (?, 'pending', '', ?)",
        ("race-finished", old),
    )
    ledger_conn.commit()

    class FinishBeforeTimeoutCas:
        def __init__(self, inner):
            self.inner = inner
            self.injected = False

        def execute(self, sql, params=()):
            compact = " ".join(str(sql).split())
            if not self.injected and "AND started_at=?" in compact:
                self.inner.execute(
                    "UPDATE job_execution_ledger SET status='done', finished_at=? WHERE task_id=?",
                    (queue_mod._utcnow(), "race-finished"),
                )
                self.inner.commit()
                self.injected = True
            return self.inner.execute(sql, params)

        def commit(self):
            return self.inner.commit()

        def rollback(self):
            return self.inner.rollback()

    racing_conn = FinishBeforeTimeoutCas(ledger_conn)
    monkeypatch.setattr(queue_mod, "get_conn", lambda: racing_conn)

    assert queue._mark_timed_out_jobs() == 0
    row = ledger_conn.execute(
        "SELECT status FROM job_execution_ledger WHERE task_id='race-finished'"
    ).fetchone()
    assert row["status"] == "done"
    item = ledger_conn.execute(
        "SELECT status FROM vkpi_async_task_items WHERE task_id='race-finished'"
    ).fetchone()
    assert item["status"] == "pending"


def test_late_handler_cas_cannot_overwrite_timeout_terminal(
    queue,
    ledger_conn,
    monkeypatch,
):
    old = "2000-01-01T00:00:00Z"
    seed(
        ledger_conn,
        "race-timeout",
        status="processing",
        started_at=old,
        timeout_seconds=1,
    )

    class TimeoutBeforeHandlerCas:
        def __init__(self, inner):
            self.inner = inner
            self.injected = False

        def execute(self, sql, params=()):
            compact = " ".join(str(sql).split())
            if (
                not self.injected
                and compact.startswith("UPDATE job_execution_ledger SET status=?")
                and "AND status=?" in compact
            ):
                self.inner.execute(
                    "UPDATE job_execution_ledger SET status='timeout', finished_at=? WHERE task_id=?",
                    (queue_mod._utcnow(), "race-timeout"),
                )
                self.inner.commit()
                self.injected = True
            return self.inner.execute(sql, params)

        def commit(self):
            return self.inner.commit()

        def rollback(self):
            return self.inner.rollback()

    racing_conn = TimeoutBeforeHandlerCas(ledger_conn)
    monkeypatch.setattr(queue_mod, "get_conn", lambda: racing_conn)

    snapshot = queue._update_job_ledger("race-timeout", "done")

    assert snapshot and snapshot["status"] == "timeout"
    assert snapshot["_stale_status_ignored"] is True
    row = ledger_conn.execute(
        "SELECT status FROM job_execution_ledger WHERE task_id='race-timeout'"
    ).fetchone()
    assert row["status"] == "timeout"


def test_timeout_transaction_rolls_back_ledger_when_item_update_fails(
    queue,
    ledger_conn,
    monkeypatch,
):
    old = "2000-01-01T00:00:00Z"
    seed(
        ledger_conn,
        "item-write-fails",
        status="processing",
        started_at=old,
        timeout_seconds=1,
    )
    ledger_conn.execute(
        "INSERT INTO vkpi_async_task_items (task_id, status, error, updated_at) VALUES (?, 'pending', '', ?)",
        ("item-write-fails", old),
    )
    ledger_conn.commit()

    class FailItemUpdate:
        def __init__(self, inner):
            self.inner = inner
            self.rollbacks = 0

        def execute(self, sql, params=()):
            compact = " ".join(str(sql).split())
            if compact.startswith("UPDATE vkpi_async_task_items"):
                raise sqlite3.OperationalError("injected async item write failure")
            return self.inner.execute(sql, params)

        def commit(self):
            return self.inner.commit()

        def rollback(self):
            self.rollbacks += 1
            return self.inner.rollback()

    failing_conn = FailItemUpdate(ledger_conn)
    monkeypatch.setattr(queue_mod, "get_conn", lambda: failing_conn)

    assert queue._mark_timed_out_jobs() == 0
    assert failing_conn.rollbacks == 1
    ledger = ledger_conn.execute(
        "SELECT status, finished_at FROM job_execution_ledger WHERE task_id='item-write-fails'"
    ).fetchone()
    assert ledger["status"] == "processing"
    assert ledger["finished_at"] is None
    item = ledger_conn.execute(
        "SELECT status, error FROM vkpi_async_task_items WHERE task_id='item-write-fails'"
    ).fetchone()
    assert item["status"] == "pending"
    assert item["error"] == ""


@pytest.mark.parametrize("affected_rows", [0, 1])
def test_postgres_compat_update_cursor_preserves_rowcount(affected_rows):
    class RawCursor:
        description = None

        def __init__(self):
            self.rowcount = affected_rows

        def execute(self, sql, params):
            self.executed = (sql, params)

    class RawConnection:
        def __init__(self):
            self.raw_cursor = RawCursor()

        def cursor(self):
            return self.raw_cursor

    raw = RawConnection()
    conn = PostgresCompatConnection(raw)

    cursor = conn.execute(
        "UPDATE job_execution_ledger SET status=? WHERE task_id=?",
        ("timeout", "task-1"),
    )

    assert cursor.rowcount == affected_rows
    assert raw.raw_cursor.executed[1] == ["timeout", "task-1"]


def test_postgres_timeout_cas_casts_normalized_started_at(
    queue,
    monkeypatch,
):
    executed_sql: list[str] = []

    class Result:
        def __init__(self, *, rows=None, rowcount=0):
            self.rows = list(rows or [])
            self.rowcount = rowcount

        def fetchall(self):
            return self.rows

    class PgContractConnection:
        def execute(self, sql, params=()):
            compact = " ".join(str(sql).split())
            executed_sql.append(compact)
            if compact.startswith("SELECT task_id, started_at, timeout_seconds"):
                return Result(
                    rows=[
                        {
                            "task_id": "pg-timeout",
                            "started_at": "2000-01-01T00:00:00+00:00",
                            "timeout_seconds": 1,
                        }
                    ]
                )
            return Result(rowcount=0)

        def rollback(self):
            return None

    monkeypatch.setattr(queue_mod, "get_conn", lambda: PgContractConnection())
    monkeypatch.setattr(queue_mod, "is_postgres_runtime", lambda: True)

    assert queue._mark_timed_out_jobs() == 0
    timeout_updates = [
        sql for sql in executed_sql if sql.startswith("UPDATE job_execution_ledger")
    ]
    assert len(timeout_updates) == 1
    assert "started_at=CAST(? AS timestamptz)" in timeout_updates[0]


@pytest.mark.pg
def test_postgres_timeout_cas_executes_against_timestamptz(
    queue,
    pg_compat,
    monkeypatch,
):
    pg_compat.execute(
        """
        CREATE TEMP TABLE job_execution_ledger (
            id BIGSERIAL PRIMARY KEY,
            task_id TEXT NOT NULL UNIQUE,
            job_type TEXT NOT NULL,
            status TEXT NOT NULL,
            started_at TIMESTAMPTZ,
            timeout_seconds INTEGER,
            updated_at TIMESTAMPTZ,
            finished_at TIMESTAMPTZ,
            error_message TEXT,
            stage TEXT
        )
        """
    )
    pg_compat.execute(
        """
        CREATE TEMP TABLE vkpi_async_task_items (
            task_id TEXT,
            status TEXT,
            error TEXT,
            updated_at TIMESTAMPTZ
        )
        """
    )
    pg_compat.execute(
        """
        INSERT INTO job_execution_ledger (
            task_id, job_type, status, started_at, timeout_seconds, updated_at
        ) VALUES (?, ?, 'processing', CAST(? AS timestamptz), 1, NOW())
        """,
        ("pg-real-timeout", "vkpi_test", "2000-01-01T00:00:00+00:00"),
    )
    pg_compat.execute(
        """
        INSERT INTO vkpi_async_task_items (task_id, status, error, updated_at)
        VALUES (?, 'pending', '', NOW())
        """,
        ("pg-real-timeout",),
    )
    pg_compat.commit()
    monkeypatch.setattr(queue_mod, "get_conn", lambda: pg_compat)
    monkeypatch.setattr(queue_mod, "is_postgres_runtime", lambda: True)

    candidates = pg_compat.execute(
        """
        SELECT task_id, started_at, timeout_seconds
        FROM job_execution_ledger
        WHERE status IN ('processing', 'running')
          AND started_at IS NOT NULL
          AND job_type LIKE ?
        """,
        ("vkpi_%",),
    ).fetchall()
    assert len(candidates) == 1
    assert isinstance(candidates[0]["started_at"], str)

    timed_out = queue._mark_timed_out_jobs()
    ledger = pg_compat.execute(
        "SELECT status, error_message FROM job_execution_ledger WHERE task_id=?",
        ("pg-real-timeout",),
    ).fetchone()
    item = pg_compat.execute(
        "SELECT status FROM vkpi_async_task_items WHERE task_id=?",
        ("pg-real-timeout",),
    ).fetchone()
    assert timed_out == 1, dict(ledger)
    assert ledger["status"] == "timeout"
    assert "job execution exceeded" in ledger["error_message"]
    assert item["status"] == "failed"


@pytest.mark.pg
def test_postgres_stream_marker_wins_after_row_lock_epq(
    pg_dsn,
    monkeypatch,
):
    """A producer's old statement snapshot cannot erase a committed stream."""

    import psycopg

    schema = f"queue_stream_race_{uuid.uuid4().hex}"
    expected_stream_id = "91-0"
    worker_raw = None
    producer_raw = None
    producer_thread = None
    admin = psycopg.connect(pg_dsn, autocommit=True, connect_timeout=5)
    result: dict[str, object] = {}
    started = threading.Event()
    try:
        with admin.cursor() as cursor:
            cursor.execute(f'CREATE SCHEMA "{schema}"')
            cursor.execute(
                f"""
                CREATE TABLE "{schema}".job_execution_ledger (
                    task_id TEXT PRIMARY KEY,
                    status TEXT NOT NULL,
                    stream_id TEXT,
                    updated_at TIMESTAMPTZ,
                    finished_at TIMESTAMPTZ,
                    error_message TEXT,
                    stage TEXT,
                    payload_json TEXT DEFAULT '{{}}',
                    extra_json TEXT DEFAULT '{{}}'
                )
                """
            )
            cursor.execute(
                f"""
                CREATE TABLE "{schema}".vkpi_provider_execution_claims (
                    task_id TEXT PRIMARY KEY,
                    state TEXT NOT NULL,
                    lease_expires_at TIMESTAMPTZ NOT NULL
                )
                """
            )
            cursor.execute(
                f"""
                INSERT INTO "{schema}".job_execution_ledger
                    (task_id, status, updated_at)
                VALUES ('pg-stream-race', 'queued', NOW())
                """
            )

        worker_raw = psycopg.connect(pg_dsn, connect_timeout=5)
        producer_raw = psycopg.connect(pg_dsn, connect_timeout=5)
        worker_raw.execute(f'SET search_path TO "{schema}"')
        producer_raw.execute(f'SET search_path TO "{schema}"')
        producer_raw.execute("SET lock_timeout TO '5s'")
        worker_raw.commit()
        producer_raw.commit()

        # Hold the ledger row while the producer starts from the older
        # stream_id=NULL statement snapshot.
        worker_raw.execute(
            """
            UPDATE job_execution_ledger
            SET status='processing', stream_id=%s, updated_at=NOW()
            WHERE task_id='pg-stream-race'
            """,
            (expected_stream_id,),
        )

        producer_conn = PostgresCompatConnection(producer_raw, pool=None)
        queue_instance = queue_mod.RedisJobQueue.__new__(queue_mod.RedisJobQueue)
        monkeypatch.setattr(queue_mod, "get_conn", lambda: producer_conn)

        def attempt_containment() -> None:
            started.set()
            try:
                result["snapshot"] = queue_instance._fail_unbound_stream_job(
                    "pg-stream-race",
                    expected_stream_id=expected_stream_id,
                    error_message="producer observed bind failure",
                )
            except BaseException as exc:  # surface thread failures in pytest
                result["error"] = exc

        producer_thread = threading.Thread(target=attempt_containment, daemon=True)
        producer_thread.start()
        assert started.wait(timeout=2)

        blocked = False
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline:
            with admin.cursor() as cursor:
                cursor.execute(
                    "SELECT wait_event_type FROM pg_stat_activity WHERE pid=%s",
                    (producer_raw.info.backend_pid,),
                )
                activity = cursor.fetchone()
            if activity and activity[0] == "Lock":
                blocked = True
                break
            time.sleep(0.02)

        worker_raw.commit()
        producer_thread.join(timeout=5)

        assert blocked, "producer UPDATE never blocked on the worker row lock"
        assert not producer_thread.is_alive()
        assert "error" not in result, repr(result.get("error"))
        snapshot = result.get("snapshot")
        assert isinstance(snapshot, dict)
        assert snapshot["status"] == "processing"
        assert snapshot["stream_id"] == expected_stream_id
        assert snapshot["_stream_bind_failed_applied"] is False
        assert snapshot["_durable_stream_won"] is True
        with admin.cursor() as cursor:
            cursor.execute(
                f"""
                SELECT status, stream_id
                FROM "{schema}".job_execution_ledger
                WHERE task_id='pg-stream-race'
                """
            )
            persisted = cursor.fetchone()
        assert persisted == ("processing", expected_stream_id)
    finally:
        if worker_raw is not None:
            worker_raw.rollback()
        if producer_raw is not None:
            producer_raw.rollback()
        if producer_thread is not None and producer_thread.is_alive():
            producer_thread.join(timeout=1)
        if worker_raw is not None:
            worker_raw.close()
        if producer_raw is not None:
            producer_raw.close()
        with admin.cursor() as cursor:
            cursor.execute(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE')
        admin.close()
