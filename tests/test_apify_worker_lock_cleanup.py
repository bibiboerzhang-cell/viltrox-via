"""Uncertain unlocks must not leak locks into the next worker job."""
from __future__ import annotations

from contextlib import nullcontext
from types import SimpleNamespace

import pytest

from app.workers import apify_jobs_worker as worker
from app.workers import apify_jobs_worker_runtime as runtime
from app.workers.apify_jobs_worker_locks import WorkerConnectionRetired
from tests.test_apify_worker_fleet_rate import _install_execution_fakes


@pytest.mark.parametrize("persistence_failure", [False, True])
def test_first_unlock_failure_still_unlocks_target_then_requeues_and_retires(monkeypatch, persistence_failure):
    events = _install_execution_fakes(monkeypatch, persistence_failure=persistence_failure)

    class Connection:
        closed = False

        def close(self):
            events.append(("closed",))
            self.closed = True

    conn = Connection()
    monkeypatch.setattr(runtime, "_cache_reuse_state", lambda *_a, **_kw: {})
    monkeypatch.setattr(runtime, "_finish_cache_hit", lambda *_a, **_kw: False)
    monkeypatch.setattr(runtime, "_preflight_state", lambda *_a: {"allowed": True, "reason": "ok", "estimated_cost": 0.01})
    monkeypatch.setattr(runtime, "_authorized_execution_payload", lambda *_a, **_kw: {})
    monkeypatch.setattr(worker, "_acquire_llm_slot", lambda *_a: "0")
    monkeypatch.setattr(worker, "_llm_budget_preflight", lambda *_a, **_kw: {})
    monkeypatch.setattr(worker, "_log_budget_preflight_record_only", lambda **_kw: None)
    monkeypatch.setattr(worker, "_process_gemini_video", lambda *_a: pytest.fail("provider forbidden"))

    def defer(_conn):
        raise worker.SharedProviderRateDeferred("gemini_shared_rate_unavailable")

    def unlock(_conn, scope, key):
        assert not conn.closed  # Session remains usable until requeue finishes.
        events.append(("unlock", scope, key))
        if scope == "vkpi_analysis_worker_llm_slot":
            raise RuntimeError("unlock timed out while session remains connected")

    def process(_conn, job):
        runtime._run_locked_llm(
            _conn, job=job, payload={}, target_type="video", target_id="7",
            derive_method="gemini", target_lock="video:7:gemini",
            context={"cache_derive_method": "gemini", "execution_class": "production"},
            deps=runtime._runtime_dependencies(dict(vars(worker))),
        )

    monkeypatch.setattr(worker, "_respect_gemini_qps", defer)
    monkeypatch.setattr(worker, "_advisory_unlock", unlock)
    monkeypatch.setattr(worker, "_process_claimed_job", process)
    job = {"id": 25, "lease_owner": "worker-test", "attempts": 4, "job_type": "video"}
    if persistence_failure:
        with pytest.raises(worker.SharedProviderRateDeferred):
            worker._execute_claimed_job(conn, job)
    else:
        assert worker._execute_claimed_job(conn, job) == "queued"
        assert events[-2][0:3] == ("queued", 25, "gemini_shared_rate_unavailable")
    assert events[:2] == [
        ("unlock", "vkpi_analysis_worker_llm_slot", "0"),
        ("unlock", "vkpi_analysis_worker_target", "video:7:gemini"),
    ]
    assert events[2] == ("finalized", "apify-job:25", 7, "blocked")
    assert events[-1] == ("closed",)
    assert conn.closed is True and job["attempts"] == 4


@pytest.mark.parametrize("persistence_failure", [False, True])
def test_close_failure_forces_connection_abandonment_without_attempt_mutation(monkeypatch, persistence_failure):
    from app.workers.apify_jobs_worker_locks import WorkerLockCleanupFailed

    events = _install_execution_fakes(monkeypatch, persistence_failure=persistence_failure)

    class Connection:
        def close(self):
            raise RuntimeError("close failed")

    def process(*_args):
        try:
            raise worker.SharedProviderRateDeferred("gemini_shared_rate_unavailable")
        finally:
            raise WorkerLockCleanupFailed("unlock uncertain")

    monkeypatch.setattr(worker, "_process_claimed_job", process)
    with pytest.raises(WorkerConnectionRetired) as caught:
        worker._execute_claimed_job(Connection(), {"id": 26, "lease_owner": "worker-test"})
    assert worker.shared_provider_rate_deferral(caught.value) is not None
    if not persistence_failure:
        assert events[-1][0] == "queued"
    before = list(events)
    worker._fail_job(Connection(), 26, caught.value)
    assert events == before  # Pooled executor cannot write via the retired session.


def test_inline_worker_reconnects_after_connection_retirement(monkeypatch):
    from app.workers import apify_jobs_worker_video_pool as pool_module

    events = []

    class Stop:
        stopped = False

        def is_set(self):
            return self.stopped

        def wait(self, delay):
            events.append(("reconnect_wait", delay))
            return False

    stop = Stop()

    def connect(*_args, **_kwargs):
        events.append(("connect",))
        if events.count(("connect",)) == 2:
            stop.stopped = True
        return nullcontext(object())

    def execute(*_args):
        raise WorkerConnectionRetired("injected close failure")

    monkeypatch.setattr(worker, "DB_RUNTIME_URL", "fixture-only-no-network")
    monkeypatch.setattr(worker, "_stop_event", stop)
    monkeypatch.setattr(worker.psycopg, "connect", connect)
    monkeypatch.setattr(worker.signal, "signal", lambda *_a: None)
    monkeypatch.setattr(worker, "release_validation_active", lambda: False)
    for name in ("_reclaim_stale_running_jobs", "_adopt_recent_provider_pressure_failures", "_reconcile_terminal_search_session_jobs", "_upsert_worker_heartbeat", "close_db_runtime_sync"):
        monkeypatch.setattr(worker, name, lambda *_a: None)
    monkeypatch.setattr(worker, "_claim_job", lambda _conn: {"id": 28})
    monkeypatch.setattr(worker, "_execute_claimed_job", execute)
    monkeypatch.setattr(worker, "_fail_job", lambda *_a: pytest.fail("retired connection cannot mutate attempts"))
    monkeypatch.setattr(pool_module.VideoJobPool, "bind_worker", lambda **_kw: SimpleNamespace(submit=lambda _job: False, drain=lambda **_kw: None))
    worker.run_worker()
    assert events == [("connect",), ("reconnect_wait", worker.WORKER_DB_RECONNECT_SECONDS), ("connect",)]
