"""Offline real-finalizer / write-fence checks; never open a provider or DB."""
from __future__ import annotations

import asyncio
from contextlib import nullcontext
from copy import deepcopy
import json
from types import SimpleNamespace

import pytest

from app.domains.kol import search_execution_fence as fence
from app.domains.kol import search_sessions, search_sessions_items
from app.domains.kol.profile_discovery_pipeline_stages import RecallState, finalize_pipeline
from app.domains.kol.search_progress_contract import completion_contract
from app.domains.kol.search_sessions_lanes import write_execution_failure, write_lane_summary
from app.workers import apify_jobs_worker_handlers as handlers


def summary(execution_id="attempt-one", job_id=17):
    return {"search_execution_id": execution_id, "search_execution_job_id": job_id,
            "smart_search_profile_advance_job": {"status": "running", "job_id": job_id},
            "search_lanes": {"online": {"status": "running", "returned_count": None, "execution_id": execution_id}},
            "diagnostic_marker": "preserve"}


class Connection:
    def __init__(self, initial):
        self.summary = deepcopy(initial)
        self.status = "running"
        self.calls = []
        self.commits = 0

    def execute(self, sql, args):
        self.calls.append((sql, args))
        if sql.lstrip().startswith("UPDATE"):
            if "status=?" in sql:
                self.status, raw = args[:2]
            else:
                raw = args[0]
                if "status='failed'" in sql:
                    self.status = "failed"
            self.summary = json.loads(raw)
        return self

    def fetchone(self):
        return {"id": 81, "status": self.status, "result_summary_json": deepcopy(self.summary)}

    def fetchall(self):
        return []

    def commit(self):
        self.commits += 1


def finish():
    return finalize_pipeline(
        session_id=81, query="portrait creators",
        payload={"_search_execution_id": "attempt-one", "job_id": 17},
        recall=RecallState(result={"items": [{"id": 1}]}, session=None, base_count=1, advance_limit=1, smart_local_30=False),
        new_discovery=None, base_count=1, advance_result={"status": "ready", "items": [], "counts": {}, "selected": 1},
        changed_ids=[], content_fit=None, field_topup=None, pipeline_status_resolver=lambda *_args: "ready",
        deps=SimpleNamespace(search_sessions=search_sessions, completion_contract=completion_contract,
                             int_value=lambda value, *_args: int(value or 0), text=lambda value: str(value or "")),
    )


@pytest.mark.parametrize("state", ["current", "new_attempt", "new_job", "failed"])
def test_actual_finalizer_cannot_finish_new_attempt_or_erase_failure(monkeypatch, state):
    conn = Connection(summary("attempt-two" if state == "new_attempt" else "attempt-one", 18 if state == "new_job" else 17))
    if state == "failed":
        write_execution_failure(conn, 81, execution_id="attempt-one", job_id=17,
                                reason="search_pipeline_failed", error="synthetic")
    before, status_before = deepcopy(conn.summary), conn.status
    conn.calls.clear()
    monkeypatch.setattr(search_sessions, "get_conn", lambda: conn)
    result = finish()
    if state == "current":
        assert result["status"] == "ready" and result["write_db"] is True
        assert conn.summary["smart_search_profile_advance_job"]["execution_id"] == "attempt-one"
        assert conn.summary["smart_search_profile_advance_job"]["job_id"] == 17
        assert len([sql for sql, _ in conn.calls if sql.lstrip().startswith("UPDATE")]) == 1
    else:
        assert result["status"] == "superseded" and result["write_db"] is False
        assert conn.summary == before and conn.status == status_before
        assert not any(sql.lstrip().startswith("UPDATE") for sql, _ in conn.calls)


@pytest.mark.parametrize("incoming_id, incoming_job", [("attempt-one", 17), ("attempt-two", 17), ("attempt-two", 18)])
def test_lane_owner_checked_under_lock_and_identity_retained(incoming_id, incoming_job):
    conn = Connection(summary("attempt-two", 18))
    before = deepcopy(conn.summary)
    applied = write_lane_summary(conn, 81, lane="online", status="ready", returned_count=3,
                                 expected_execution_id=incoming_id, expected_job_id=incoming_job)
    if (incoming_id, incoming_job) == ("attempt-two", 18):
        assert applied is True
        assert conn.summary["search_lanes"]["online"] == {
            "status": "ready", "returned_count": 3, "execution_id": "attempt-two",
        }
    else:
        assert applied is False and conn.summary == before
        assert len(conn.calls) == 1
    assert "FOR NO KEY UPDATE" in conn.calls[0][0]


@pytest.mark.parametrize("mutation", ["record_items", "profile_item", "diagnostics"])
def test_bound_pipeline_context_stops_old_leaf_writes_and_resets(monkeypatch, mutation):
    conn = Connection(summary("attempt-two", 17))
    before = deepcopy(conn.summary)

    def bomb(*_args, **_kwargs):
        raise AssertionError("superseded execution must not persist candidate rows")

    @fence.bind_pipeline_execution
    async def execute(*, session_id, payload, provider_actor=None):
        assert fence.resolve_fence(session_id).execution_id == "attempt-one"
        observed = await asyncio.to_thread(fence.resolve_fence, session_id)
        assert observed.execution_id == "attempt-one"
        if mutation == "record_items":
            result = search_sessions_items.record_items(session_id, [{"id": 1}], get_conn_fn=lambda: conn, upsert_item_fn=bomb)
        elif mutation == "profile_item":
            result = search_sessions_items.update_item_profile_execution(session_id, 1, profile_result={}, get_conn_fn=lambda: conn)
        else:
            result = search_sessions.update_session_result_summary(session_id, status="ready", summary_patch={"new_discovery": {"status": "ready"}})
        fence.require_applied(result)
        bomb()

    monkeypatch.setattr(search_sessions, "get_conn", lambda: conn)
    result = asyncio.run(execute(session_id=81, payload={"_search_execution_id": "attempt-one", "job_id": 17}))
    assert result["status"] == "superseded" and result["write_db"] is False
    assert conn.summary == before
    assert not any(sql.lstrip().startswith(("INSERT", "UPDATE", "DELETE")) for sql, _ in conn.calls)
    assert fence.resolve_fence(81) is None


@pytest.mark.parametrize("exit_type", [RuntimeError, asyncio.CancelledError])
def test_execution_context_resets_on_exception_or_cancel(exit_type):
    @fence.bind_pipeline_execution
    async def execute(*, session_id, payload, provider_actor=None):
        assert not fence.permits_write(summary(), 82, fence.resolve_fence(82))
        raise exit_type("synthetic")
    with pytest.raises(exit_type):
        asyncio.run(execute(session_id=81, payload={"_search_execution_id": "attempt-one", "job_id": 17}))
    assert fence.resolve_fence(81) is None


class QueueConnection:
    def __init__(self, owner, attempts):
        self.owner, self.attempts = owner, attempts
        self.status = "running"
        self.payload = {"new_attempt": "preserve"}
        self.calls, self.updated = [], False

    def transaction(self):
        return nullcontext()

    def cursor(self):
        return nullcontext(self)

    def execute(self, sql, args):
        self.calls.append((sql, args))
        assert "lease_owner=%s AND attempts=%s AND status='running' RETURNING id" in sql
        self.updated = args[-2:] == (self.owner, self.attempts)
        if self.updated:
            self.status, self.payload = args[0], json.loads(args[2])

    def fetchone(self):
        return {"id": 17} if self.updated else None


@pytest.mark.parametrize("result_status,new_owner,new_attempts", [
    ("superseded", "new-owner", 2), ("ready", "new-owner", 2),
    ("ready", "old-owner", 2), ("ready", "old-owner", 1),
])
def test_worker_queue_terminal_write_requires_current_claim(monkeypatch, result_status, new_owner, new_attempts):
    conn = QueueConnection(new_owner, new_attempts)
    before = deepcopy(conn.payload)
    monkeypatch.setattr(handlers, "authorize_provider_job_before_execution", lambda *_args, **_kwargs: {"id": 7})
    monkeypatch.setattr(search_sessions, "update_session_result_summary", lambda *_args, **_kwargs: {})

    async def execute(**_kwargs):
        return {"status": result_status, "write_db": result_status != "superseded"}

    monkeypatch.setattr(handlers.kol_profile_discovery, "execute_smart_search_profile_advance_pipeline", execute)
    job = {"id": 17, "lease_owner": "old-owner", "attempts": 1}
    if result_status == "superseded" or (new_owner, new_attempts) != ("old-owner", 1):
        with pytest.raises(fence.SearchExecutionSuperseded):
            handlers._process_smart_search_profile_advance(conn, job, {"search_session_id": 81})
        assert conn.payload == before and conn.status == "running"
        if result_status == "superseded":
            assert not conn.calls
    else:
        handlers._process_smart_search_profile_advance(conn, job, {"search_session_id": 81})
        assert conn.status == "done" and len(conn.calls) == 1
