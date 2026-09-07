"""Real row-lock races on synthetic search sessions; no workers or providers."""
from __future__ import annotations

import json
import threading
import time
from types import SimpleNamespace
from uuid import uuid4

import pytest

from app.db.connection import PostgresCompatConnection
from app.domains.kol import search_session_job_analysis as analysis
from app.domains.kol import search_sessions as sessions
from app.domains.kol import profile_discovery_pipeline_stages as stages
from app.domains.kol.search_progress_contract import completion_contract
from app.domains.kol.search_sessions_lanes import write_execution_failure


pytestmark = pytest.mark.pg
SID = 81


def attempt(name="attempt-one", job_id=17):
    return {"search_execution_id": name, "search_execution_job_id": job_id,
            "smart_search_profile_advance_job": {"job_id": job_id, "status": "running", "execution_id": name},
            "search_lanes": {"online": {"status": "running", "returned_count": None, "execution_id": name}}}


@pytest.fixture
def lifecycle(pg_dsn):
    import psycopg
    from psycopg import sql
    from psycopg.rows import dict_row

    schema = "vkpi_search_lifecycle_test_" + uuid4().hex
    options = "-c statement_timeout=12000 -c lock_timeout=5000"
    admin = psycopg.connect(pg_dsn, autocommit=True, connect_timeout=5, options=options)
    connections = []

    def connect(*, raw=False):
        conn = psycopg.connect(pg_dsn, connect_timeout=5, options=options,
                               **({"row_factory": dict_row} if raw else {}))
        conn.execute(sql.SQL("SET search_path TO {}, pg_catalog").format(sql.Identifier(schema)))
        conn.commit()
        wrapped = conn if raw else PostgresCompatConnection(conn, pool=None)
        connections.append(wrapped)
        return wrapped

    try:
        admin.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(schema)))
        setup = connect()
        setup.execute("""
            CREATE TABLE vkpi_kol_search_sessions (
                id BIGINT PRIMARY KEY, status TEXT, result_summary_json JSONB,
                updated_at TIMESTAMPTZ DEFAULT NOW());
            CREATE TABLE vkpi_kol_search_session_items (
                id BIGINT PRIMARY KEY, session_id BIGINT REFERENCES vkpi_kol_search_sessions(id),
                item_type TEXT, status TEXT, stage TEXT, rank INTEGER, score NUMERIC,
                kol_pool_id BIGINT, evidence_id BIGINT, job_id BIGINT, source_url TEXT,
                payload_json JSONB DEFAULT '{}', updated_at TIMESTAMPTZ DEFAULT NOW());
        """)
        setup.execute("INSERT INTO vkpi_kol_search_sessions VALUES (?,'running',?::jsonb,NOW())",
                      (SID, json.dumps(attempt())))
        setup.commit()
        yield setup, connect
    finally:
        for conn in connections:
            conn.close()
        try:
            admin.execute(sql.SQL("DROP SCHEMA IF EXISTS {} CASCADE").format(sql.Identifier(schema)))
        finally:
            admin.close()


def insert_item(conn, item_id):
    conn.execute("INSERT INTO vkpi_kol_search_session_items "
                 "(id,session_id,item_type,status,stage,rank) VALUES (?,?,'url_profile','ready','profile',?)",
                 (item_id, SID, item_id))


def read_session(conn, *, commit=True):
    row = dict(conn.execute("SELECT status,result_summary_json FROM vkpi_kol_search_sessions WHERE id=?", (SID,)).fetchone())
    if commit:
        conn.commit()
    summary = row["result_summary_json"]
    return row["status"], summary if isinstance(summary, dict) else json.loads(summary)


def wait_for_real_lock(conn, pid, finished):
    deadline = time.monotonic() + 3
    while time.monotonic() < deadline:
        row = conn.execute("SELECT wait_event_type FROM pg_stat_activity WHERE pid=?", (pid,)).fetchone()
        conn.commit()  # Refresh PostgreSQL's per-transaction statistics snapshot.
        if row and row["wait_event_type"] == "Lock":
            return
        assert not finished.is_set(), "contender completed without waiting for the real row lock"
        time.sleep(.01)
    pytest.fail("contender never reached a real PostgreSQL Lock wait")


def start_call(call, results, errors, finished):
    def run():
        try:
            results.append(call())
        except BaseException as exc:
            errors.append(exc)
        finally:
            finished.set()
    thread = threading.Thread(target=run, daemon=True)
    thread.start()
    return thread


class PausedSummaryRead:
    def __init__(self, conn, entered, release):
        self.conn, self.entered, self.release = conn, entered, release

    def execute(self, statement, params=()):
        result = self.conn.execute(statement, params)
        normalized = " ".join(statement.split())
        # Pause the real summary read even when its projection gains columns;
        # the test below must still observe a genuine PostgreSQL Lock wait.
        if (normalized.startswith("SELECT result_summary_json")
                and " FROM vkpi_kol_search_sessions WHERE id=" in normalized):
            self.entered.set()
            assert self.release.wait(5)
        return result

    def __getattr__(self, name):
        return getattr(self.conn, name)


def test_summary_and_terminal_lane_serialize_with_item_foreign_keys(lifecycle, monkeypatch):
    setup, connect = lifecycle
    first, second = connect(), connect()
    inserted = threading.Barrier(2)
    entered, release, done_first, done_second = (threading.Event() for _ in range(4))
    paused = PausedSummaryRead(first, entered, release)
    by_thread = {}
    monkeypatch.setattr(sessions, "get_conn", lambda: by_thread[threading.current_thread().ident])
    results, errors = [], []

    def summarize():
        by_thread[threading.current_thread().ident] = paused
        insert_item(first, 1)
        inserted.wait(5)
        return sessions.update_session_result_summary(SID, status="running", summary_patch={"synthetic_patch": True})

    def terminate():
        by_thread[threading.current_thread().ident] = second
        insert_item(second, 2)
        inserted.wait(5)
        assert entered.wait(5)
        return sessions.interrupt_search_lanes(SID, execution_id="attempt-one", reason="search_pipeline_cancelled")

    workers = [start_call(summarize, results, errors, done_first), start_call(terminate, results, errors, done_second)]
    try:
        assert entered.wait(3), "summary lock was blocked by the other item's FK KEY SHARE"
        wait_for_real_lock(setup, second._raw.info.backend_pid, done_second)
    finally:
        release.set()
        for worker in workers:
            worker.join(13)
    assert not any(worker.is_alive() for worker in workers) and errors == []
    assert True in results
    _, summary = read_session(setup)
    assert summary["synthetic_patch"] is True
    assert summary["search_lanes"]["online"]["status"] == "failed"
    assert summary["search_lanes"]["online"]["reason"] == "search_pipeline_cancelled"
    assert setup.execute("SELECT COUNT(*) AS n FROM vkpi_kol_search_session_items").fetchone()["n"] == 2


def test_ready_child_rebuild_waits_and_preserves_failed_execution(lifecycle):
    setup, connect = lifecycle
    held, worker = connect(), connect(raw=True)
    insert_item(setup, 1)
    setup.commit()
    assert write_execution_failure(held, SID, execution_id="attempt-one", job_id=17,
                                   reason="search_pipeline_failed", error="synthetic failure") is True
    results, errors, done = [], [], threading.Event()

    def rebuild():
        with worker.transaction():
            with worker.cursor() as cur:
                analysis.rebuild_search_session_summary(cur, session_id=SID, session_status="ready")

    thread = start_call(rebuild, results, errors, done)
    try:
        wait_for_real_lock(setup, worker.info.backend_pid, done)
    finally:
        held.commit()
        thread.join(13)
    assert not thread.is_alive() and errors == []
    status, summary = read_session(setup)
    assert status == summary["phase"] == "failed"
    assert summary["counts"]["ready"] == 1
    assert summary["smart_search_profile_advance_job"]["status"] == "failed"
    assert summary["search_lanes"]["online"]["status"] == "failed"


def test_old_attempt_failure_waits_and_cannot_change_new_attempt(lifecycle, monkeypatch):
    setup, connect = lifecycle
    held, contender = connect(), connect()
    newer = attempt("attempt-two", 18)
    held.execute("UPDATE vkpi_kol_search_sessions SET result_summary_json=?::jsonb WHERE id=?", (json.dumps(newer), SID))
    monkeypatch.setattr(sessions, "get_conn", lambda: contender)
    results, errors, done = [], [], threading.Event()
    thread = start_call(lambda: sessions.fail_search_execution(
        SID, execution_id="attempt-one", job_id=17, reason="search_pipeline_failed", error="old failure"),
        results, errors, done)
    try:
        wait_for_real_lock(setup, contender._raw.info.backend_pid, done)
    finally:
        held.commit()
        thread.join(13)
    assert not thread.is_alive() and errors == [] and results == [False]
    assert read_session(setup) == ("running", newer)
    assert sessions.interrupt_search_lanes(SID, execution_id="attempt-one", reason="search_pipeline_cancelled") is False
    assert read_session(setup) == ("running", newer)


def finalize_old_attempt():
    """Use the actual pipeline-to-writer bridge, not test-injected fence kwargs."""
    return stages.finalize_pipeline(
        session_id=SID, query="synthetic lifecycle completion",
        payload={"_search_execution_id": "attempt-one", "job_id": 17,
                 "search_mode": "hybrid", "include_new_discovery": True},
        recall=stages.RecallState(result={"status": "ready", "items": []}, session=None,
                                 base_count=0, advance_limit=1, smart_local_30=False),
        new_discovery={"status": "ready", "items": [], "provider_calls_performed": False},
        base_count=0, advance_result={"status": "ready", "selected": 0, "items": [], "counts": {}},
        changed_ids=[], content_fit=None, field_topup=None,
        pipeline_status_resolver=lambda *_args: "ready",
        deps=SimpleNamespace(search_sessions=sessions, completion_contract=completion_contract,
                             int_value=lambda value, default=0: int(value or default),
                             text=lambda value: str(value or "")),
    )


def test_old_normal_finalization_waits_and_cannot_finish_new_attempt(lifecycle, monkeypatch):
    setup, connect = lifecycle
    held, contender = connect(), connect()
    newer = attempt("attempt-two", 18)
    held.execute("UPDATE vkpi_kol_search_sessions SET result_summary_json=?::jsonb WHERE id=?", (json.dumps(newer), SID))
    monkeypatch.setattr(sessions, "get_conn", lambda: contender)
    results, errors, done = [], [], threading.Event()
    thread = start_call(finalize_old_attempt, results, errors, done)
    try:
        wait_for_real_lock(setup, contender._raw.info.backend_pid, done)
    finally:
        held.commit()
        thread.join(13)
    assert not thread.is_alive() and errors == [] and len(results) == 1
    assert results[0]["status"] == "superseded" and results[0]["write_db"] is False
    assert read_session(setup) == ("running", newer)


def test_late_normal_finalization_preserves_current_failure(lifecycle, monkeypatch):
    setup, connect = lifecycle
    held, contender = connect(), connect()
    insert_item(setup, 1)
    setup.commit()
    assert write_execution_failure(held, SID, execution_id="attempt-one", job_id=17,
                                   reason="search_pipeline_failed", error="synthetic failure") is True
    expected = read_session(held, commit=False)
    monkeypatch.setattr(sessions, "get_conn", lambda: contender)
    results, errors, done = [], [], threading.Event()
    thread = start_call(finalize_old_attempt, results, errors, done)
    try:
        wait_for_real_lock(setup, contender._raw.info.backend_pid, done)
    finally:
        held.commit()
        thread.join(13)
    assert not thread.is_alive() and errors == [] and len(results) == 1
    assert results[0]["status"] == "superseded" and results[0]["write_db"] is False
    assert read_session(setup) == expected
    assert expected[0] == expected[1]["phase"] == "failed"
    assert setup.execute("SELECT COUNT(*) AS n FROM vkpi_kol_search_session_items WHERE status='ready'").fetchone()["n"] == 1
