"""Real PostgreSQL admission checks, opt-in and unique-schema isolated."""
from __future__ import annotations

import json
import uuid

import psycopg
from psycopg import sql
import pytest

from app.workers import apify_jobs_worker as worker
from app.workers.apify_jobs_worker_locks import release_worker_locks
from tests.test_apify_worker_fleet_rate import _install_execution_fakes


@pytest.mark.pg
def test_shared_rate_pg_mutex_commit_release_and_failure_recovery(pg_dsn, monkeypatch) -> None:
    # pg_dsn independently rejects non-disposable databases. Neither connection
    # can resolve public/business tables: search_path contains only this schema.
    schema = f"rate_test_{uuid.uuid4().hex}"
    scope = f"rate-test-{uuid.uuid4().hex}"
    monkeypatch.setattr(worker, "_GEMINI_QPS_SCOPE", scope)
    monkeypatch.setattr(worker, "GEMINI_MIN_INTERVAL_SECONDS", 60.0)
    monkeypatch.setattr(worker, "_process_gemini_video", lambda *_args: pytest.fail("provider forbidden"))
    with psycopg.connect(pg_dsn, autocommit=True, connect_timeout=5) as first:
        first.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(schema)))
        try:
            with psycopg.connect(pg_dsn, autocommit=True, connect_timeout=5) as second:
                for conn in (first, second):
                    conn.execute(sql.SQL("SET search_path TO {}, pg_catalog").format(sql.Identifier(schema)))
                    conn.execute("SET statement_timeout TO '2s'")
                    conn.execute("SET lock_timeout TO '1s'")
                first.execute(
                    "CREATE TABLE persistent_cache (cache_key TEXT PRIMARY KEY, value_json TEXT NOT NULL, "
                    "expires_at TIMESTAMPTZ NOT NULL, created_at TIMESTAMPTZ NOT NULL)"
                )
                # Cross-connection contention returns a deferral without waiting
                # for the first session's lock or touching the shared timestamp.
                with first.transaction():
                    first.execute(
                        "SELECT pg_advisory_xact_lock(hashtext(%s), hashtext(%s))",
                        (scope, worker._GEMINI_QPS_KEY),
                    )
                    with pytest.raises(worker.SharedProviderRateDeferred, match="gemini_shared_rate_busy"):
                        worker._respect_gemini_qps(second)
                    assert first.execute("SELECT COUNT(*) FROM persistent_cache").fetchone()[0] == 0

                worker._respect_gemini_qps(first)
                state = second.execute("SELECT value_json FROM persistent_cache").fetchone()[0]
                assert json.loads(state)["last_started_at_epoch"] > 0
                with pytest.raises(worker.SharedProviderRateDeferred, match="gemini_shared_rate_wait"):
                    worker._respect_gemini_qps(second)
                assert second.execute("SELECT value_json FROM persistent_cache").fetchone()[0] == state

                # A state parsing failure aborts the admission transaction and
                # releases its xact lock. The next worker can acquire it again.
                first.execute("UPDATE persistent_cache SET value_json='invalid-json'")
                with pytest.raises(worker.SharedProviderRateDeferred, match="gemini_shared_rate_unavailable"):
                    worker._respect_gemini_qps(second)
                with first.transaction():
                    locked = first.execute(
                        "SELECT pg_try_advisory_xact_lock(hashtext(%s), hashtext(%s))",
                        (scope, worker._GEMINI_QPS_KEY),
                    ).fetchone()[0]
                    assert locked is True

                # Restored state resumes safe admission; no process restart or
                # provider call is needed and only the isolated cache row moves.
                first.execute(
                    "UPDATE persistent_cache SET value_json=%s",
                    (json.dumps({"last_started_at_epoch": 0.0}),),
                )
                worker._respect_gemini_qps(second)
                assert first.execute("SELECT COUNT(*) FROM persistent_cache").fetchone()[0] == 1
                recovered = first.execute("SELECT value_json FROM persistent_cache").fetchone()[0]
                assert json.loads(recovered)["last_started_at_epoch"] >= json.loads(state)["last_started_at_epoch"]
        finally:
            # Exact UUID-named scratch schema only; never a shared/default schema.
            first.execute(sql.SQL("DROP SCHEMA {} CASCADE").format(sql.Identifier(schema)))


@pytest.mark.pg
def test_unlock_failure_requeues_before_retiring_and_releases_all_pg_session_locks(pg_dsn, monkeypatch):
    schema = f"cleanup_test_{uuid.uuid4().hex}"
    locks = [("vkpi-test-llm-slot", schema), ("vkpi-test-target", schema)]
    real_requeue, real_unlock = worker._requeue_job, worker._advisory_unlock
    events = _install_execution_fakes(monkeypatch)
    monkeypatch.setattr(worker, "_requeue_job", real_requeue)
    monkeypatch.setattr(worker, "_sync_search_session_job", lambda *_a, **_kw: None)
    with psycopg.connect(pg_dsn, autocommit=True, connect_timeout=5) as first, psycopg.connect(pg_dsn, autocommit=True, connect_timeout=5) as second:
        second.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(schema)))
        try:
            for conn in (first, second):
                conn.execute(sql.SQL("SET search_path TO {}, pg_catalog").format(sql.Identifier(schema)))
                conn.execute("SET statement_timeout TO '2s'")
            first.execute(
                "CREATE TABLE apify_jobs (id BIGINT PRIMARY KEY, status TEXT, attempts INTEGER, "
                "last_error TEXT, last_error_category TEXT, next_retry_at TIMESTAMPTZ, updated_at TIMESTAMPTZ)"
            )
            first.execute("INSERT INTO apify_jobs (id,status,attempts) VALUES (27,'running',4)")
            for scope, key in locks:
                assert worker._advisory_lock(first, scope, key)

            def unlock(conn, scope, key):
                events.append(("unlock", scope, key))
                if scope == locks[0][0]:
                    raise RuntimeError("injected unlock timeout; session and lock remain live")
                real_unlock(conn, scope, key)

            def process(conn, _job):
                try:
                    raise worker.SharedProviderRateDeferred("gemini_shared_rate_unavailable")
                finally:
                    release_worker_locks(conn, locks, unlock)

            monkeypatch.setattr(worker, "_process_claimed_job", process)
            assert worker._execute_claimed_job(first, {"id": 27, "lease_owner": "worker-test"}) == "queued"
            assert first.closed is True
            assert events[:2] == [("unlock", *locks[0]), ("unlock", *locks[1])]
            assert events[2] == ("finalized", "apify-job:27", 7, "blocked")
            row = second.execute("SELECT status, attempts, next_retry_at IS NOT NULL FROM apify_jobs WHERE id=27").fetchone()
            assert row == ("queued", 4, True)
            for scope, key in locks:
                assert worker._advisory_lock(second, scope, key), "closed owner must release even the failed-unlock lock"
                real_unlock(second, scope, key)
        finally:
            first.close()
            second.execute(sql.SQL("DROP SCHEMA {} CASCADE").format(sql.Identifier(schema)))
