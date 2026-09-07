"""Fault-path receipts using in-memory DB seams; no provider or business DB."""
from __future__ import annotations

import asyncio
from copy import deepcopy
import json

import pytest

from app.domains.kol import search_sessions
from app.domains.kol import search_sessions_items, search_session_job_analysis
from app.domains.kol.search_sessions_lanes import (
    interrupted_lane_summary,
    preserve_execution_failure,
    retain_execution_observation,
    write_diagnostics_patch,
    write_execution_failure,
    write_interrupted_lanes,
)
from app.workers import apify_jobs_worker_handlers as handlers


class MemoryConnection:
    def __init__(self, summary=None):
        self.summary = deepcopy(summary or {})
        self.status = "running"
        self.calls = []
        self.commits = 0

    def execute(self, sql, args):
        self.calls.append((sql, args))
        if sql.startswith("UPDATE"):
            self.summary = json.loads(args[0])
            if "status='failed'" in sql:
                self.status = "failed"
        return self

    def fetchone(self):
        return {"result_summary_json": json.dumps(self.summary)}

    def commit(self):
        self.commits += 1


def running_summary():
    return {
        "phase": "base", "search_execution_id": "attempt-one", "search_execution_job_id": 17,
        "smart_search_profile_advance_job": {"job_id": 17, "status": "running", "query_text": "portraits"},
        "search_lanes": {
            "local": {"status": "ready", "returned_count": 1},
            "online": {"status": "running", "returned_count": None, "execution_id": "attempt-one"},
        },
        "local_qualification": {"snapshot_id": "local-proof"},
        "diagnostic_marker": "preserve",
    }


@pytest.mark.parametrize("terminal_status", ["ready", "empty", "failed", "timeout", "blocked", "shortfall"])
def test_interruption_preserves_terminal_evidence_and_input(terminal_status):
    source = running_summary()
    source["search_lanes"]["local"].update(status=terminal_status, execution_id="attempt-one")
    before = deepcopy(source)
    result = interrupted_lane_summary(source, execution_id="attempt-one", reason="search_pipeline_cancelled")
    assert result["search_lanes"]["local"] == before["search_lanes"]["local"]
    assert result["search_lanes"]["online"] == {
        "status": "failed", "returned_count": None, "execution_id": "attempt-one", "reason": "search_pipeline_cancelled",
    }
    assert result["local_qualification"] == {"snapshot_id": "local-proof"}
    assert source == before


@pytest.mark.parametrize("current_id", [None, "attempt-two"])
def test_lane_interruption_cannot_terminate_unknown_or_new_attempt(current_id):
    source = running_summary()
    source["search_lanes"]["online"]["execution_id"] = current_id
    conn = MemoryConnection(source)
    assert write_interrupted_lanes(conn, 81, execution_id="attempt-one", reason="search_pipeline_cancelled") is False
    assert conn.summary == source
    assert len(conn.calls) == 1 and "FOR NO KEY UPDATE" in conn.calls[0][0]


@pytest.mark.parametrize("changed", [
    {"search_execution_id": "attempt-two"},
    {"search_execution_job_id": 18},
    {"smart_search_profile_advance_job": {"job_id": 18, "status": "running"}},
    {"smart_search_profile_advance_job": {"job_id": 17, "status": "ready"}},
    {"smart_search_profile_advance_job": {"job_id": 17, "status": "partial"}},
    {"smart_search_profile_advance_job": {"job_id": 17, "status": "needs_clarification"}},
])
def test_execution_failure_is_noop_after_new_attempt_or_observed_terminal(changed):
    source = {**running_summary(), **changed}
    conn = MemoryConnection(source)
    assert write_execution_failure(conn, 81, execution_id="attempt-one", job_id=17,
                                   reason="search_pipeline_failed", error="synthetic") is False
    assert conn.summary == source and conn.status == "running"
    assert len(conn.calls) == 1 and "FOR NO KEY UPDATE" in conn.calls[0][0]


@pytest.mark.parametrize("diagnostics_first", [False, True])
@pytest.mark.parametrize("job_display_status", ["queued", "already_queued", "running"])
def test_failure_and_diagnostics_share_lock_without_erasing_lane_evidence(diagnostics_first, job_display_status):
    source = running_summary()
    source["smart_search_profile_advance_job"]["status"] = job_display_status
    conn = MemoryConnection(source)
    diagnostics = lambda: write_diagnostics_patch(conn, 81, {"discovery_funnel": {"raw_count": 3}})
    failure = lambda: write_execution_failure(conn, 81, execution_id="attempt-one", job_id=17,
                                             reason="search_pipeline_failed", error="synthetic")
    for action in ((diagnostics, failure) if diagnostics_first else (failure, diagnostics)):
        action()
    assert conn.status == "failed" and conn.summary["phase"] == "failed"
    assert conn.summary["search_lanes"]["local"] == source["search_lanes"]["local"]
    assert conn.summary["search_lanes"]["online"]["returned_count"] is None
    assert conn.summary["discovery_funnel"] == {"raw_count": 3}
    assert conn.summary["local_qualification"] == source["local_qualification"]
    assert conn.summary["smart_search_profile_advance_job"]["query_text"] == "portraits"
    assert "provider_calls_performed" not in conn.summary
    assert "provider_calls_performed" not in conn.summary["smart_search_profile_advance_job"]
    assert all("FOR NO KEY UPDATE" in sql for sql, _ in conn.calls if sql.startswith("SELECT"))


class ForbiddenQueueConnection:
    def __getattr__(self, name):
        raise AssertionError(f"session failure handler must not mutate queue/provider state: {name}")


@pytest.mark.parametrize("failure_type", [RuntimeError, ValueError, TimeoutError, asyncio.CancelledError])
@pytest.mark.parametrize("storage_fails", [False, True])
def test_worker_failure_settles_session_and_rethrows_without_queue_mutation(monkeypatch, failure_type, storage_fails):
    conn = MemoryConnection()
    pipeline_calls = []
    failure = failure_type("synthetic failure")
    monkeypatch.setattr(handlers, "authorize_provider_job_before_execution", lambda *_args, **_kwargs: {"id": 7})
    monkeypatch.setattr(search_sessions, "get_conn", lambda: conn)

    def initial_summary(_sid, *, status, summary_patch, start_execution=False):
        assert start_execution is True
        conn.status = status
        conn.summary.update(deepcopy(summary_patch))

    async def execute(**kwargs):
        pipeline_calls.append(kwargs)
        execution_id = kwargs["payload"]["_search_execution_id"]
        assert execution_id == conn.summary["search_execution_id"]
        conn.summary["search_lanes"] = {
            "local": {"status": "ready", "returned_count": 2},
            "online": {"status": "running", "returned_count": None, "execution_id": execution_id},
        }
        conn.summary["local_qualification"] = {"snapshot_id": "preserve-proof"}
        if storage_fails:
            def unavailable(*_args, **_kwargs):
                raise OSError("synthetic storage unavailable")
            monkeypatch.setattr(conn, "execute", unavailable)
        raise failure

    monkeypatch.setattr(search_sessions, "update_session_result_summary", initial_summary)
    monkeypatch.setattr(handlers.kol_profile_discovery, "execute_smart_search_profile_advance_pipeline", execute)
    payload = {"search_session_id": 81, "query_text": "portraits", "search_mode": "hybrid", "include_new_discovery": True}
    before = deepcopy(payload)
    with pytest.raises(failure_type) as caught:
        handlers._process_smart_search_profile_advance(ForbiddenQueueConnection(), {"id": 17}, payload)
    assert caught.value is failure
    assert len(pipeline_calls) == 1 and payload == before
    assert conn.summary["search_lanes"]["local"] == {"status": "ready", "returned_count": 2}
    assert conn.summary["local_qualification"] == {"snapshot_id": "preserve-proof"}
    if storage_fails:
        # Storage did not confirm a terminal receipt: never assert a successful
        # write or suppress the original failure/cancellation to continue work.
        assert conn.status == "running" and conn.commits == 0
    else:
        assert conn.status == "failed" and conn.commits == 1
        assert conn.summary["search_lanes"]["online"]["status"] == "failed"
        assert conn.summary["search_lanes"]["online"]["returned_count"] is None
        expected = "search_pipeline_cancelled" if failure_type is asyncio.CancelledError else "search_pipeline_failed"
        assert conn.summary["smart_search_profile_advance_job"]["reason"] == expected


def test_worker_failure_from_old_execution_cannot_override_replacement(monkeypatch):
    conn = MemoryConnection()
    monkeypatch.setattr(handlers, "authorize_provider_job_before_execution", lambda *_args, **_kwargs: {"id": 7})
    monkeypatch.setattr(search_sessions, "get_conn", lambda: conn)
    monkeypatch.setattr(search_sessions, "update_session_result_summary",
                        lambda _sid, **kwargs: conn.summary.update(deepcopy(kwargs["summary_patch"])))

    async def execute(**kwargs):
        # A replacement of the same job gets a different observation identity.
        conn.summary["search_execution_id"] = "replacement-execution"
        conn.summary["search_lanes"] = {
            "online": {"status": "running", "returned_count": None, "execution_id": "replacement-execution"},
        }
        raise asyncio.CancelledError()

    monkeypatch.setattr(handlers.kol_profile_discovery, "execute_smart_search_profile_advance_pipeline", execute)
    with pytest.raises(asyncio.CancelledError):
        handlers._process_smart_search_profile_advance(ForbiddenQueueConnection(), {"id": 17}, {"search_session_id": 81})
    assert conn.status == "running"
    assert conn.summary["search_lanes"]["online"]["status"] == "running"
    assert conn.summary["search_execution_id"] == "replacement-execution"


@pytest.mark.parametrize("proposed_status", ["failed", "ready", "running", "partial"])
def test_general_summary_writer_uses_lane_row_lock_and_preserves_terminal_evidence(monkeypatch, proposed_status):
    class OrchestrationConnection(MemoryConnection):
        def execute(self, sql, args):
            self.calls.append((sql, args))
            if sql.lstrip().startswith("UPDATE"):
                self.status = args[0]
                self.summary = json.loads(args[1])
            return self

        def fetchall(self):
            return []

        def fetchone(self):
            return {"id": 81, "status": self.status, "result_summary_json": json.dumps(self.summary)}

    failure_conn = MemoryConnection(running_summary())
    write_execution_failure(failure_conn, 81, execution_id="attempt-one", job_id=17,
                            reason="search_pipeline_cancelled", error="CancelledError")
    source = deepcopy(failure_conn.summary)
    conn = OrchestrationConnection(source)
    monkeypatch.setattr(search_sessions, "get_conn", lambda: conn)
    monkeypatch.setattr(search_sessions, "project_session_result_summary", lambda summary, _items, **_kwargs: summary)
    result = search_sessions.update_session_result_summary(81, status=proposed_status, summary_patch={"phase": "complete"})
    assert "FOR NO KEY UPDATE" in conn.calls[0][0]
    assert result["status"] == "failed" and result["result_summary"]["phase"] == "failed"
    assert result["result_summary"]["search_lanes"] == source["search_lanes"]
    assert conn.summary["local_qualification"] == source["local_qualification"]
    assert conn.commits == 1
    # A genuine new execution explicitly supersedes the prior owner; unlike a
    # late progress patch it may start running without reusing old failure truth.
    newer = search_sessions.update_session_result_summary(81, status="running", summary_patch={
        "phase": "base", "search_execution_id": "attempt-two", "search_execution_job_id": 18,
        "smart_search_profile_advance_job": {"job_id": 18, "status": "running"},
    }, start_execution=True)
    assert newer["status"] == "running" and newer["result_summary"]["phase"] == "base"
    assert newer["result_summary"]["search_execution_id"] == "attempt-two"


@pytest.mark.parametrize("mode", ["fresh_network", "hybrid"])
@pytest.mark.parametrize("candidate_count", [0, 1])
def test_worker_batch_replacement_keeps_execution_identity_until_failure(monkeypatch, mode, candidate_count):
    class BatchConnection(MemoryConnection):
        items = [{"id": 1, "session_id": 81, "item_type": "recall_candidate", "status": "ready", "payload_json": {}}][:candidate_count]

        def execute(self, sql, args):
            if sql.lstrip().startswith("UPDATE") and "status=?" in sql:
                self.calls.append((sql, args))
                self.status = args[0]
                self.summary = json.loads(args[1])
                return self
            return super().execute(sql.strip(), args)

        def fetchall(self):
            return deepcopy(self.items)

    conn = BatchConnection()
    monkeypatch.setattr(handlers, "authorize_provider_job_before_execution", lambda *_args, **_kwargs: {"id": 7})
    monkeypatch.setattr(search_sessions, "get_conn", lambda: conn)
    monkeypatch.setattr(search_sessions_items, "session_origin_breakdown", lambda *_args: {})
    monkeypatch.setattr(search_sessions_items, "session_completion_breakdown", lambda *_args: {})
    monkeypatch.setattr(search_sessions, "update_session_result_summary",
                        lambda _sid, **kwargs: conn.summary.update(deepcopy(kwargs["summary_patch"])))

    async def execute(**kwargs):
        execution_id = kwargs["payload"]["_search_execution_id"]
        # Use real record_items + actual legacy summary writer, mocking only
        # individual item persistence: no live DB/provider/qualification calls.
        batch_summary = {"kind": "kol_recall", "phase": "base", "items_written": candidate_count}
        original = deepcopy(batch_summary)
        search_sessions_items.record_items(
            81, conn.items, status="running", summary=batch_summary,
            get_conn_fn=lambda: conn, upsert_item_fn=lambda _conn, _sid, item: item,
        )
        assert batch_summary == original
        assert conn.summary["search_execution_id"] == execution_id
        assert conn.summary["search_execution_job_id"] == 17
        assert conn.summary["smart_search_profile_advance_job"]["status"] == "running"
        raise RuntimeError("advance failed after assembled results")

    monkeypatch.setattr(handlers.kol_profile_discovery, "execute_smart_search_profile_advance_pipeline", execute)
    with pytest.raises(RuntimeError, match="advance failed"):
        handlers._process_smart_search_profile_advance(ForbiddenQueueConnection(), {"id": 17},
            {"search_session_id": 81, "query_text": "portraits", "search_mode": mode})
    assert conn.status == "failed"
    assert preserve_execution_failure(conn.summary, "ready") == "failed"
    assert len(conn.items) == candidate_count
    # A later item-derived ready update must retain the terminal failure owner.
    search_sessions_items._update_session(conn, 81, status="ready", summary={"kind": "kol_recall"})
    assert conn.status == "failed" and conn.summary["phase"] == "failed"
    assert conn.summary["result_projection"]["terminal"] is True
    assert conn.summary["result_projection"]["returned_count"] == candidate_count


def test_late_child_rebuild_preserves_failure_and_new_ready_evidence():
    conn = MemoryConnection(running_summary())
    write_execution_failure(conn, 81, execution_id="attempt-one", job_id=17,
                            reason="search_pipeline_cancelled", error="CancelledError")
    failure_summary = deepcopy(conn.summary)

    class Cursor:
        calls = []
        updated = None

        def execute(self, sql, args):
            self.calls.append((sql, args))
            if sql.lstrip().startswith("UPDATE"):
                self.updated = args

        def fetchone(self):
            return {"result_summary_json": deepcopy(failure_summary)}

        def fetchall(self):
            return [{"id": 1, "item_type": "recall_candidate", "status": "ready", "payload_json": {}}]

    cursor = Cursor()
    search_session_job_analysis.rebuild_search_session_summary(cursor, session_id=81, session_status="ready")
    status, raw_summary, sid = cursor.updated
    stored = json.loads(raw_summary)
    assert status == "failed" and sid == 81 and stored["phase"] == "failed"
    assert stored["counts"]["ready"] == 1 and stored["items_written"] == 1
    assert stored["search_lanes"] == failure_summary["search_lanes"]
    assert stored["smart_search_profile_advance_job"] == failure_summary["smart_search_profile_advance_job"]
    assert "FOR NO KEY UPDATE" in cursor.calls[0][0]


def test_batch_lifecycle_retention_prefers_locked_new_attempt_not_stale_caller():
    current = running_summary()
    stale = {"search_execution_id": "old-attempt", "search_execution_job_id": 4,
             "smart_search_profile_advance_job": {"status": "running", "job_id": 4},
             "search_lanes": {"online": {"status": "running", "execution_id": "old-attempt"}},
             "kind": "kol_recall", "items_written": 1}
    before = deepcopy(stale)
    retained = retain_execution_observation(current, stale)
    for key in ("search_execution_id", "search_execution_job_id", "smart_search_profile_advance_job", "search_lanes"):
        assert retained[key] == current[key]
    assert retained["kind"] == "kol_recall" and retained["items_written"] == 1
    assert stale == before
