from __future__ import annotations

import json
import threading
from contextlib import contextmanager, nullcontext
from typing import Any

import pytest

from app.workers import apify_jobs_worker as worker
from app.workers import apify_jobs_worker_runtime as runtime


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

    def transaction(self):
        return nullcontext()


def test_gemini_start_rate_defers_without_sleep_or_write_before_shared_interval(monkeypatch) -> None:
    conn = _Connection(
        [
            {"locked": True},
            {"now_epoch": 102.0, "value_json": '{"last_started_at_epoch":100.0}'},
        ]
    )
    sleeps: list[float] = []
    monkeypatch.setattr(worker, "GEMINI_MIN_INTERVAL_SECONDS", 4.0)
    monkeypatch.setattr(worker.time, "sleep", lambda seconds: sleeps.append(seconds))
    with pytest.raises(worker.SharedProviderRateDeferred, match="gemini_shared_rate_wait") as caught:
        worker._respect_gemini_qps(conn)  # type: ignore[arg-type]

    assert 2.05 <= caught.value.retry_delay_seconds <= 2.5
    assert sleeps == []
    assert not any("INSERT" in sql for sql, _ in conn.cursor_value.calls)


def test_gemini_start_rate_does_not_sleep_after_idle_period(monkeypatch) -> None:
    conn = _Connection(
        [
            {"locked": True},
            {"now_epoch": 200.0, "value_json": '{"last_started_at_epoch":100.0}'},
        ]
    )
    sleeps: list[float] = []
    monkeypatch.setattr(worker, "GEMINI_MIN_INTERVAL_SECONDS", 4.0)
    monkeypatch.setattr(worker.time, "sleep", lambda seconds: sleeps.append(seconds))

    worker._respect_gemini_qps(conn)  # type: ignore[arg-type]

    assert sleeps == []
    inserts = [params for sql, params in conn.cursor_value.calls if "INSERT" in sql]
    assert len(inserts) == 1
    assert json.loads(inserts[0][1]) == {"last_started_at_epoch": 200.0}


@pytest.mark.parametrize("failure_stage", ["cursor", "read", "write", "commit"])
def test_shared_state_faults_never_grant_process_local_allowance(monkeypatch, failure_stage) -> None:
    conn = _Connection([{"locked": True}, {"now_epoch": 200.0, "value_json": None}])
    original_execute = conn.cursor_value.execute

    def execute(sql, params=()):
        if (failure_stage == "read" and "clock_timestamp" in sql) or (failure_stage == "write" and "INSERT" in sql):
            raise RuntimeError("shared database unavailable")
        original_execute(sql, params)

    @contextmanager
    def transaction():
        yield
        if failure_stage == "commit":
            raise RuntimeError("commit outcome unknown")

    conn.cursor_value.execute = execute
    conn.transaction = transaction
    if failure_stage == "cursor":
        def broken_cursor(**_kwargs):
            raise RuntimeError("connection unavailable")
        conn.cursor = broken_cursor
    monkeypatch.setattr(worker, "GEMINI_MIN_INTERVAL_SECONDS", 2.0)
    monkeypatch.setattr(worker.time, "sleep", lambda _seconds: pytest.fail("must not hold a worker slot asleep"))

    with pytest.raises(worker.SharedProviderRateDeferred, match="gemini_shared_rate_unavailable") as caught:
        worker._respect_gemini_qps(conn)
    assert 30.0 <= caught.value.retry_delay_seconds <= 35.0


@pytest.mark.parametrize("raw_state", ['{broken', '{}', '[]', '{"last_started_at_epoch":"nan"}', '{"last_started_at_epoch":-1}'])
def test_corrupt_shared_state_fails_closed(monkeypatch, raw_state) -> None:
    conn = _Connection([{"locked": True}, {"now_epoch": 200.0, "value_json": raw_state}])
    monkeypatch.setattr(worker, "GEMINI_MIN_INTERVAL_SECONDS", 2.0)
    with pytest.raises(worker.SharedProviderRateDeferred, match="gemini_shared_rate_unavailable"):
        worker._respect_gemini_qps(conn)
    assert not any("INSERT" in sql for sql, _ in conn.cursor_value.calls)


@pytest.mark.parametrize("interval", [float("nan"), float("inf")])
def test_nonfinite_interval_cannot_disable_fleet_throttling(monkeypatch, interval) -> None:
    monkeypatch.setattr(worker, "GEMINI_MIN_INTERVAL_SECONDS", interval)
    with pytest.raises(worker.SharedProviderRateDeferred, match="gemini_shared_rate_invalid_interval"):
        worker._respect_gemini_qps(object())


def test_state_outage_recovery_uses_shared_allowance_without_process_clock(monkeypatch) -> None:
    conn = _Connection([{"locked": True}, {"now_epoch": 203.0, "value_json": '{"last_started_at_epoch":200.0}'}])
    original_cursor = conn.cursor

    def unavailable_cursor(**_kwargs):
        raise RuntimeError("temporary connection failure")

    conn.cursor = unavailable_cursor
    monkeypatch.setattr(worker, "GEMINI_MIN_INTERVAL_SECONDS", 2.0)
    with pytest.raises(worker.SharedProviderRateDeferred, match="gemini_shared_rate_unavailable"):
        worker._respect_gemini_qps(conn)
    conn.cursor = original_cursor
    worker._respect_gemini_qps(conn)
    inserts = [params for sql, params in conn.cursor_value.calls if "INSERT" in sql]
    assert len(inserts) == 1
    assert json.loads(inserts[0][1]) == {"last_started_at_epoch": 203.0}


def test_concurrent_workers_admit_only_one_shared_start_and_recover(monkeypatch) -> None:
    lock = threading.Lock()
    entered = threading.Event()
    continue_first = threading.Event()
    shared = {"epoch": 200.0, "state": None}
    outcomes: list[str] = []

    class SharedConnection:
        owns_lock = False

        @contextmanager
        def transaction(self):
            try:
                yield
            finally:
                if self.owns_lock:
                    self.owns_lock = False
                    lock.release()

        def cursor(self, **_kwargs):
            return self

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def execute(self, sql, params=()):
            if "pg_try_advisory_xact_lock" in sql:
                self.owns_lock = lock.acquire(blocking=False)
                self.row = {"locked": self.owns_lock}
            elif "clock_timestamp" in sql:
                self.row = {"now_epoch": shared["epoch"], "value_json": shared["state"]}
            elif "INSERT" in sql:
                entered.set()
                assert continue_first.wait(timeout=2)
                shared["state"] = json.loads(params[1])

        def fetchone(self):
            return self.row

    def first_worker():
        worker._respect_gemini_qps(SharedConnection())
        outcomes.append("admitted")

    monkeypatch.setattr(worker, "GEMINI_MIN_INTERVAL_SECONDS", 2.0)
    thread = threading.Thread(target=first_worker)
    thread.start()
    try:
        assert entered.wait(timeout=2)
        with pytest.raises(worker.SharedProviderRateDeferred, match="gemini_shared_rate_busy"):
            worker._respect_gemini_qps(SharedConnection())
    finally:
        continue_first.set()
        thread.join(timeout=2)
    assert outcomes == ["admitted"]
    with pytest.raises(worker.SharedProviderRateDeferred, match="gemini_shared_rate_wait"):
        worker._respect_gemini_qps(SharedConnection())
    shared["epoch"] = 203.0
    worker._respect_gemini_qps(SharedConnection())
    assert shared["state"] == {"last_started_at_epoch": 203.0}


def _install_execution_fakes(monkeypatch, *, persistence_failure=False):
    events: list[tuple] = []
    monkeypatch.setattr(worker, "release_validation_active", lambda: False)
    monkeypatch.setattr(worker, "db_connection_sync_scope", nullcontext)
    monkeypatch.setattr(worker, "_running_job_heartbeat", lambda *_args: nullcontext())
    monkeypatch.setattr(worker, "apify_execution_context", lambda *_args: nullcontext())
    monkeypatch.setattr(worker, "acquire_provider_execution_claim", lambda *_args, **_kwargs: 7)
    monkeypatch.setattr(worker, "finalize_provider_execution_claim", lambda *args: events.append(("finalized", *args)) or True)

    def requeue(_conn, job_id, reason, **kwargs):
        if persistence_failure:
            raise RuntimeError("requeue persistence unavailable")
        events.append(("queued", job_id, reason, kwargs))

    monkeypatch.setattr(worker, "_requeue_job", requeue)
    monkeypatch.setattr(worker, "_sync_search_session_job", lambda *_args, **_kwargs: pytest.fail("deferred job is not terminal"))
    return events


def test_actual_llm_dispatch_defers_and_releases_slots_without_provider_or_attempt(monkeypatch) -> None:
    real_requeue = worker._requeue_job
    events = _install_execution_fakes(monkeypatch)
    monkeypatch.setattr(worker, "_requeue_job", real_requeue)
    monkeypatch.setattr(worker, "_sync_search_session_job", lambda _conn, job_id, **kwargs: events.append(("synced", job_id, kwargs)))
    monkeypatch.setattr(runtime, "_cache_reuse_state", lambda *_args, **_kwargs: {})
    monkeypatch.setattr(runtime, "_finish_cache_hit", lambda *_args, **_kwargs: False)
    monkeypatch.setattr(runtime, "_preflight_state", lambda *_args: {"allowed": True, "reason": "ok", "estimated_cost": 0.01})
    monkeypatch.setattr(runtime, "_authorized_execution_payload", lambda *_args, **_kwargs: {"authorized": True})
    monkeypatch.setattr(worker, "_acquire_llm_slot", lambda *_args: "0")
    monkeypatch.setattr(worker, "_llm_budget_preflight", lambda *_args, **_kwargs: {})
    monkeypatch.setattr(worker, "_log_budget_preflight_record_only", lambda **_kwargs: None)
    monkeypatch.setattr(worker, "_advisory_unlock", lambda _conn, scope, key: events.append(("released", scope, key)))
    monkeypatch.setattr(worker, "_process_gemini_video", lambda *_args: pytest.fail("provider must not start"))
    monkeypatch.setattr(worker, "GEMINI_MIN_INTERVAL_SECONDS", 2.0)
    conn = _Connection([{"locked": True}, {"now_epoch": 200.0, "value_json": '{broken'}])
    job = {"id": 21, "lease_owner": "worker-test", "attempts": 4, "job_type": "video"}

    def process(_conn, _job):
        runtime._run_locked_llm(
            _conn, job=_job, payload={}, target_type="video", target_id="7",
            derive_method="gemini", target_lock="video:7:gemini",
            context={"cache_derive_method": "gemini", "execution_class": "production"},
            deps=runtime._runtime_dependencies(dict(vars(worker))),
        )

    monkeypatch.setattr(worker, "_process_claimed_job", process)
    assert worker._execute_claimed_job(conn, job) == "queued"
    assert job["attempts"] == 4
    assert events[:3] == [
        ("released", "vkpi_analysis_worker_llm_slot", "0"),
        ("released", "vkpi_analysis_worker_target", "video:7:gemini"),
        ("finalized", "apify-job:21", 7, "blocked"),
    ]
    assert events[3] == ("synced", 21, {"raw_status": "queued", "reason": "gemini_shared_rate_unavailable"})
    updates = [(sql, params) for sql, params in conn.cursor_value.calls if "UPDATE apify_jobs" in sql]
    assert len(updates) == 1
    assert "SET status='queued'" in updates[0][0]
    assert "attempts" not in updates[0][0]
    assert 30.0 <= updates[0][1][1] <= 35.0


def test_defer_persistence_failure_preserves_no_provider_attempt_classification(monkeypatch) -> None:
    _install_execution_fakes(monkeypatch, persistence_failure=True)
    deferred = worker.SharedProviderRateDeferred("gemini_shared_rate_unavailable")

    def defer(*_args):
        raise deferred

    monkeypatch.setattr(worker, "_process_claimed_job", defer)
    with pytest.raises(worker.SharedProviderRateDeferred) as caught:
        worker._execute_claimed_job(object(), {"id": 22, "lease_owner": "worker-test"})
    assert caught.value is deferred
    assert str(caught.value.__cause__) == "requeue persistence unavailable"
    events = _install_execution_fakes(monkeypatch)
    worker._fail_job(object(), 22, caught.value)
    assert events == [("queued", 22, "gemini_shared_rate_unavailable", {"retry_delay_seconds": 30.0})]


def test_cleanup_failure_cannot_reclassify_shared_rate_deferral_as_provider_failure(monkeypatch) -> None:
    events = _install_execution_fakes(monkeypatch)

    def cleanup_fails(*_args):
        try:
            raise worker.SharedProviderRateDeferred("gemini_shared_rate_unavailable")
        finally:
            raise RuntimeError("advisory unlock connection lost")

    monkeypatch.setattr(worker, "_process_claimed_job", cleanup_fails)
    assert worker._execute_claimed_job(object(), {"id": 23, "lease_owner": "worker-test"}) == "queued"
    assert events[0] == ("finalized", "apify-job:23", 7, "blocked")
    assert events[1][0:3] == ("queued", 23, "gemini_shared_rate_unavailable")


def test_outer_failure_handler_preserves_wrapped_deferral_and_handles_exception_cycles(monkeypatch) -> None:
    events = _install_execution_fakes(monkeypatch)
    wrapped = RuntimeError("DB scope cleanup failed")
    deferred = worker.SharedProviderRateDeferred("gemini_shared_rate_unavailable")
    wrapped.__context__ = deferred
    worker._fail_job(object(), 24, wrapped)
    assert events == [("queued", 24, "gemini_shared_rate_unavailable", {"retry_delay_seconds": 30.0})]
    wrapped.__context__ = wrapped
    assert worker.shared_provider_rate_deferral(wrapped) is None
