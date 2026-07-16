from __future__ import annotations

import asyncio
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pytest

from app.services.scheduler import fleet_guard


class _PermitFireLease:
    def __init__(self) -> None:
        self.acquired = False
        self.released = False

    def try_acquire(self) -> bool:
        self.acquired = True
        return True

    def healthy(self) -> bool:
        return self.acquired and not self.released

    def release(self) -> None:
        self.released = True
        self.acquired = False


class _SharedAdvisoryLock:
    def __init__(self) -> None:
        self.owner: object | None = None
        self.statements: list[str] = []


class _FakeCursor:
    def __init__(self, connection: "_FakeConnection") -> None:
        self.connection = connection
        self._row: tuple[Any, ...] | None = None

    def __enter__(self) -> "_FakeCursor":
        return self

    def __exit__(self, *_args: object) -> None:
        return None

    def execute(self, statement: str, _params: tuple[Any, ...] | None = None) -> None:
        self.connection.shared.statements.append(statement)
        if "pg_try_advisory_lock" in statement:
            acquired = self.connection.shared.owner is None
            if acquired:
                self.connection.shared.owner = self.connection
            self._row = (acquired,)
        elif "pg_advisory_unlock" in statement:
            unlocked = self.connection.shared.owner is self.connection
            if unlocked:
                self.connection.shared.owner = None
            self._row = (unlocked,)
        else:
            self._row = (1,)

    def fetchone(self) -> tuple[Any, ...] | None:
        return self._row


class _FakeConnection:
    def __init__(self, shared: _SharedAdvisoryLock) -> None:
        self.shared = shared
        self.closed = False

    def cursor(self) -> _FakeCursor:
        return _FakeCursor(self)

    def close(self) -> None:
        if self.shared.owner is self:
            self.shared.owner = None
        self.closed = True


def _connect_factory(shared: _SharedAdvisoryLock):
    def connect(*_args: Any, **_kwargs: Any) -> _FakeConnection:
        return _FakeConnection(shared)

    return connect


def test_postgres_advisory_lease_allows_one_owner_then_takeover() -> None:
    shared = _SharedAdvisoryLock()
    connect = _connect_factory(shared)
    first = fleet_guard.SchedulerLeaderLease(
        identity="first", dsn="postgresql://test", connect_fn=connect, postgres_enabled=True
    )
    second = fleet_guard.SchedulerLeaderLease(
        identity="second", dsn="postgresql://test", connect_fn=connect, postgres_enabled=True
    )

    assert first.try_acquire() is True
    assert second.try_acquire() is False
    assert first.healthy() is True
    first.release()
    assert second.try_acquire() is True
    second.release()

    assert any("pg_try_advisory_lock" in sql for sql in shared.statements)
    assert any("pg_advisory_unlock" in sql for sql in shared.statements)


def test_controller_promotes_standby_after_leader_release() -> None:
    async def scenario() -> None:
        shared = _SharedAdvisoryLock()
        connect = _connect_factory(shared)
        events: list[str] = []

        def lease(identity: str):
            return lambda: fleet_guard.SchedulerLeaderLease(
                identity=identity,
                dsn="postgresql://test",
                connect_fn=connect,
                postgres_enabled=True,
            )

        first = fleet_guard.SchedulerFleetController(
            identity="first",
            lease_factory=lease("first"),
            on_promote=lambda: events.append("first-promote"),
            on_demote=lambda: events.append("first-demote"),
        )
        second = fleet_guard.SchedulerFleetController(
            identity="second",
            lease_factory=lease("second"),
            on_promote=lambda: events.append("second-promote"),
            on_demote=lambda: events.append("second-demote"),
        )

        assert await first.tick() is True
        assert await second.tick() is False
        await first.shutdown()
        assert await second.tick() is True
        await second.shutdown()

        assert events == [
            "first-promote",
            "first-demote",
            "second-promote",
            "second-demote",
        ]

    asyncio.run(scenario())


def test_unhealthy_leader_releases_lease_when_demote_fails() -> None:
    async def scenario() -> None:
        events: list[str] = []

        class Lease:
            acquired = False
            backend = "fake"
            healthy_now = True

            def try_acquire(self) -> bool:
                self.acquired = True
                return True

            def healthy(self) -> bool:
                return self.acquired and self.healthy_now

            def release(self) -> None:
                events.append("release")
                self.acquired = False

        lease = Lease()

        def demote() -> None:
            events.append("demote")
            raise RuntimeError("planned demote failure")

        controller = fleet_guard.SchedulerFleetController(
            identity="leader",
            lease_factory=lambda: lease,
            on_promote=lambda: events.append("promote"),
            on_demote=demote,
        )
        assert await controller.tick() is True
        lease.healthy_now = False
        with pytest.raises(RuntimeError, match="planned demote failure"):
            await controller.tick()

        assert controller.is_leader is False
        assert events == ["promote", "demote", "release"]

    asyncio.run(scenario())


def test_controller_runs_blocking_lease_io_off_event_loop_thread() -> None:
    async def scenario() -> None:
        from threading import get_ident

        event_loop_thread = get_ident()
        lease_threads: list[int] = []

        class Lease:
            acquired = False
            backend = "fake"

            def try_acquire(self) -> bool:
                lease_threads.append(get_ident())
                self.acquired = True
                return True

            def healthy(self) -> bool:
                lease_threads.append(get_ident())
                return self.acquired

            def release(self) -> None:
                lease_threads.append(get_ident())
                self.acquired = False

        lease = Lease()
        controller = fleet_guard.SchedulerFleetController(
            identity="leader",
            lease_factory=lambda: lease,
            on_promote=lambda: None,
            on_demote=lambda: None,
        )
        assert await controller.tick() is True
        assert await controller.tick() is True
        await controller.shutdown()

        assert len(lease_threads) == 3
        assert event_loop_thread not in lease_threads

    asyncio.run(scenario())


def test_scheduled_fire_wrapper_skips_duplicate_without_calling_job(monkeypatch) -> None:
    claims = iter(
        [
            fleet_guard.ScheduledFireClaim(True, 1, "job-a", "2026-07-14T20:00:00Z", True),
            fleet_guard.ScheduledFireClaim(False, None, "job-a", "2026-07-14T20:00:00Z", True),
        ]
    )
    finishes: list[tuple[int | None, str]] = []
    calls: list[str] = []

    @contextmanager
    def scope():
        yield

    monkeypatch.setattr(fleet_guard, "claim_scheduled_fire", lambda *_a, **_k: next(claims))
    monkeypatch.setattr(
        fleet_guard,
        "finish_scheduled_fire",
        lambda claim, *, status, error="": finishes.append((claim.claim_id, status)),
    )
    monkeypatch.setattr(fleet_guard, "db_connection_sync_scope", scope)

    guarded = fleet_guard.guard_scheduled_callable(
        "job-a", lambda: calls.append("called") or "ok", owner_id="leader-a"
    )
    assert guarded() == "ok"
    duplicate = guarded()

    assert duplicate["status"] == "duplicate_scheduled_fire_skipped"
    assert calls == ["called"]
    assert finishes == [(1, "completed")]


def test_scheduled_fire_wrapper_marks_failure_and_releases_scope(monkeypatch) -> None:
    claim = fleet_guard.ScheduledFireClaim(
        True, 7, "job-b", "2026-07-14T20:01:00Z", True
    )
    events: list[str] = []

    @contextmanager
    def scope():
        events.append("scope-enter")
        try:
            yield
        finally:
            events.append("scope-exit")

    monkeypatch.setattr(fleet_guard, "claim_scheduled_fire", lambda *_a, **_k: claim)
    monkeypatch.setattr(
        fleet_guard,
        "finish_scheduled_fire",
        lambda _claim, *, status, error="": events.append(f"finish-{status}-{bool(error)}"),
    )
    monkeypatch.setattr(fleet_guard, "db_connection_sync_scope", scope)

    def fail() -> None:
        events.append("job")
        raise RuntimeError("planned failure")

    guarded = fleet_guard.guard_scheduled_callable("job-b", fail, owner_id="leader-b")
    with pytest.raises(RuntimeError, match="planned failure"):
        guarded()

    assert events == ["scope-enter", "job", "scope-exit", "finish-failed-True"]


def test_fire_finalize_failure_does_not_mask_job_failure(monkeypatch) -> None:
    claim = fleet_guard.ScheduledFireClaim(
        True, 8, "job-d", "2026-07-14T20:03:00Z", True
    )

    @contextmanager
    def scope():
        yield

    monkeypatch.setattr(fleet_guard, "claim_scheduled_fire", lambda *_a, **_k: claim)
    monkeypatch.setattr(fleet_guard, "db_connection_sync_scope", scope)

    def finalize_failure(*_args: Any, **_kwargs: Any) -> None:
        raise OSError("ledger unavailable")

    def job_failure() -> None:
        raise RuntimeError("original job failure")

    monkeypatch.setattr(fleet_guard, "finish_scheduled_fire", finalize_failure)
    guarded = fleet_guard.guard_scheduled_callable(
        "job-d", job_failure, owner_id="leader-d"
    )

    with pytest.raises(RuntimeError, match="original job failure"):
        guarded()


def test_fire_finalize_failure_does_not_turn_success_into_job_failure(monkeypatch) -> None:
    claim = fleet_guard.ScheduledFireClaim(
        True, 9, "job-e", "2026-07-14T20:04:00Z", True
    )

    @contextmanager
    def scope():
        yield

    monkeypatch.setattr(fleet_guard, "claim_scheduled_fire", lambda *_a, **_k: claim)
    monkeypatch.setattr(fleet_guard, "db_connection_sync_scope", scope)
    monkeypatch.setattr(
        fleet_guard,
        "finish_scheduled_fire",
        lambda *_a, **_k: (_ for _ in ()).throw(OSError("ledger unavailable")),
    )
    guarded = fleet_guard.guard_scheduled_callable(
        "job-e",
        lambda: "business-work-completed",
        owner_id="leader-e",
    )

    assert guarded() == "business-work-completed"


def test_fire_claim_uses_exact_planned_utc_time_and_unique_migration(monkeypatch) -> None:
    captured: list[tuple[str, tuple[Any, ...]]] = []

    class CursorResult:
        def fetchone(self):
            return {"id": 99}

    class Connection:
        def execute(self, statement: str, params: tuple[Any, ...]):
            captured.append((statement, params))
            return CursorResult()

        def commit(self) -> None:
            return None

    @contextmanager
    def scope():
        yield

    monkeypatch.setattr(fleet_guard, "is_postgres_runtime", lambda: True)
    monkeypatch.setattr(fleet_guard, "db_connection_sync_scope", scope)
    monkeypatch.setattr(fleet_guard, "get_conn", lambda: Connection())
    execution_lease = _PermitFireLease()
    monkeypatch.setattr(
        fleet_guard,
        "_build_scheduled_fire_execution_lease",
        lambda _key: execution_lease,
    )

    claim = fleet_guard.claim_scheduled_fire(
        "job-c",
        "leader-c",
        fire_at=datetime(2026, 7, 14, 20, 2, 59, 999999, tzinfo=timezone.utc),
    )
    assert claim.scheduled_fire_at == "2026-07-14T20:02:59.999999Z"
    assert claim.claim_id == 99
    assert claim.lease_token
    assert claim.execution_lease is execution_lease
    assert "ON CONFLICT (task_key, scheduled_fire_at) DO NOTHING" in captured[0][0]

    migration = (
        Path(__file__).resolve().parents[1]
        / "migrations"
        / "249_vkpi_scheduler_fleet_guard.sql"
    ).read_text(encoding="utf-8")
    assert "UNIQUE (task_key, scheduled_fire_at)" in migration
    assert "CHECK (status IN ('running', 'completed', 'failed'))" in migration
    assert "The migration runner owns the surrounding transaction" in migration
    assert "BEGIN;" not in migration
    assert "COMMIT;" not in migration

    recovery_migration = (
        Path(__file__).resolve().parents[1]
        / "migrations"
        / "251_vkpi_scheduler_fire_recovery.sql"
    ).read_text(encoding="utf-8")
    assert "fire_lock_key" in recovery_migration
    assert "lease_token" in recovery_migration
    assert "heartbeat_at" in recovery_migration
    assert "lease_expires_at" in recovery_migration
    assert "vkpi_scheduler_fire_recoveries" in recovery_migration
    assert "marked_failed_outcome_unknown" in recovery_migration
    assert "Do not add BEGIN/COMMIT here" in recovery_migration
    assert "BEGIN;" not in recovery_migration
    assert "COMMIT;" not in recovery_migration


def test_fire_claim_lock_contention_fails_closed_before_insert(monkeypatch) -> None:
    class DeniedLease(_PermitFireLease):
        def try_acquire(self) -> bool:
            return False

    monkeypatch.setattr(fleet_guard, "is_postgres_runtime", lambda: True)
    monkeypatch.setattr(
        fleet_guard,
        "_build_scheduled_fire_execution_lease",
        lambda _key: DeniedLease(),
    )
    monkeypatch.setattr(
        fleet_guard,
        "get_conn",
        lambda: (_ for _ in ()).throw(AssertionError("DB insert must not run")),
    )

    claim = fleet_guard.claim_scheduled_fire(
        "lock-contended-job",
        "leader-a",
        fire_at=datetime(2026, 7, 14, 20, 5, tzinfo=timezone.utc),
    )
    assert claim.claimed is False
    assert claim.claim_id is None
    assert claim.persisted is True


def test_finish_uses_attempt_token_compare_and_swap(monkeypatch) -> None:
    statements: list[tuple[str, tuple[Any, ...]]] = []

    class CursorResult:
        def fetchone(self):
            return {"id": 42}

    class Connection:
        def execute(self, statement: str, params: tuple[Any, ...]):
            statements.append((statement, params))
            return CursorResult()

        def commit(self) -> None:
            return None

    @contextmanager
    def scope():
        yield

    monkeypatch.setattr(fleet_guard, "db_connection_sync_scope", scope)
    monkeypatch.setattr(fleet_guard, "get_conn", lambda: Connection())
    claim = fleet_guard.ScheduledFireClaim(
        True,
        42,
        "token-job",
        "2026-07-14T20:05:00Z",
        True,
        owner_id="leader-a",
        lease_token="attempt-token",
        attempt_no=1,
    )
    assert fleet_guard.finish_scheduled_fire(claim, status="completed") is True
    assert "lease_token=?" in statements[0][0]
    assert statements[0][1][-1] == "attempt-token"


def test_recovery_configuration_is_bounded_and_fail_closed(monkeypatch) -> None:
    monkeypatch.setattr(fleet_guard, "is_postgres_runtime", lambda: True)
    with pytest.raises(ValueError, match="batch_size must be between"):
        fleet_guard.recover_stale_scheduled_fires("leader", batch_size=0)
    with pytest.raises(ValueError, match="batch_size must be between"):
        fleet_guard.recover_stale_scheduled_fires("leader", batch_size=101)
    monkeypatch.setenv("VKPI_SCHEDULER_FIRE_LEASE_SECONDS", "59")
    with pytest.raises(RuntimeError, match="must be between 60 and 86400"):
        fleet_guard.scheduled_fire_lease_seconds()


def test_planned_fire_identity_survives_wall_clock_minute_boundary(monkeypatch) -> None:
    seen: set[tuple[str, datetime]] = set()
    next_id = 0
    calls: list[str] = []

    class CursorResult:
        def __init__(self, row: dict[str, int] | None) -> None:
            self.row = row

        def fetchone(self):
            return self.row

    class Connection:
        def execute(self, _statement: str, params: tuple[Any, ...]):
            nonlocal next_id
            identity = (str(params[0]), params[1])
            if identity in seen:
                return CursorResult(None)
            seen.add(identity)
            next_id += 1
            return CursorResult({"id": next_id})

        def commit(self) -> None:
            return None

    @contextmanager
    def scope():
        yield

    monkeypatch.setattr(fleet_guard, "is_postgres_runtime", lambda: True)
    monkeypatch.setattr(fleet_guard, "db_connection_sync_scope", scope)
    monkeypatch.setattr(fleet_guard, "get_conn", lambda: Connection())
    monkeypatch.setattr(
        fleet_guard,
        "_build_scheduled_fire_execution_lease",
        lambda _key: _PermitFireLease(),
    )
    monkeypatch.setattr(fleet_guard, "finish_scheduled_fire", lambda *_a, **_k: None)

    guarded = fleet_guard.guard_scheduled_callable(
        "planned-job",
        lambda: calls.append("called") or "ok",
        owner_id="leader",
    )
    first_fire = datetime(2026, 7, 14, 20, 0, 59, 900000, tzinfo=timezone.utc)
    distinct_fire_same_minute = datetime(
        2026, 7, 14, 20, 0, 59, 950000, tzinfo=timezone.utc
    )

    # The same planned fire keeps one ledger identity even if actual execution
    # happens on opposite sides of a wall-clock minute boundary.
    with fleet_guard.scheduled_fire_context(first_fire):
        assert guarded() == "ok"
    with fleet_guard.scheduled_fire_context(first_fire):
        assert guarded()["status"] == "duplicate_scheduled_fire_skipped"

    # Exact planned time avoids falsely collapsing two legitimate fires that
    # happen to share one wall-clock minute.
    with fleet_guard.scheduled_fire_context(distinct_fire_same_minute):
        assert guarded() == "ok"

    assert calls == ["called", "called"]
    assert len(seen) == 2


def test_scheduler_executor_propagates_planned_fire_to_sync_and_async_jobs(
    monkeypatch,
) -> None:
    from app.services.scheduler import jobs

    planned = datetime(2026, 7, 14, 22, 4, 5, 123456, tzinfo=timezone.utc)
    observed: list[datetime | None] = []

    @contextmanager
    def scope():
        yield

    def claim(task_key: str, _owner_id: str):
        observed.append(fleet_guard._scheduled_fire_at.get())
        return fleet_guard.ScheduledFireClaim(
            True,
            len(observed),
            task_key,
            planned.isoformat().replace("+00:00", "Z"),
            True,
        )

    monkeypatch.setattr(fleet_guard, "claim_scheduled_fire", claim)
    monkeypatch.setattr(fleet_guard, "finish_scheduled_fire", lambda *_a, **_k: None)
    monkeypatch.setattr(fleet_guard, "db_connection_sync_scope", scope)

    class Job:
        id = "executor-context-test"
        args: tuple[Any, ...] = ()
        kwargs: dict[str, Any] = {}
        misfire_grace_time = None

        def __init__(self, func) -> None:
            self.func = func

        def __str__(self) -> str:
            return self.id

    sync_job = Job(
        fleet_guard.guard_scheduled_callable(
            "sync-planned",
            lambda: "sync-ok",
            owner_id="leader",
        )
    )
    sync_events = jobs._run_job_with_planned_fire(
        sync_job,
        "default",
        [planned],
        "test.scheduler.executor",
    )

    async def async_job() -> str:
        return "async-ok"

    async_guarded_job = Job(
        fleet_guard.guard_scheduled_callable(
            "async-planned",
            async_job,
            owner_id="leader",
        )
    )

    async def run_async_job():
        return await jobs._run_coroutine_job_with_planned_fire(
            async_guarded_job,
            "default",
            [planned],
            "test.scheduler.executor",
        )

    async_events = asyncio.run(run_async_job())

    assert [event.retval for event in sync_events] == ["sync-ok"]
    assert [event.retval for event in async_events] == ["async-ok"]
    assert observed == [planned, planned]


def test_scheduler_lifecycle_starts_only_local_leader_and_wraps_every_job(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.services.scheduler import jobs

    monkeypatch.delenv("VKPI_WORKFLOW_AUTO_RECOVERY_ENABLED", raising=False)

    async def scenario() -> None:
        await jobs.stop_scheduler()
        await jobs.start_scheduler()
        scheduler = None
        try:
            status = jobs.get_scheduler_status()
            assert status["running"] is True
            assert status["fleet"]["is_leader"] is True
            assert status["fleet"]["backend"] == "process_local"
            assert jobs._scheduler is not None
            scheduler = jobs._scheduler
            scheduled = scheduler.get_jobs()
            assert scheduled
            assert any(job.id == "scheduler_fire_stale_recovery" for job in scheduled)
            assert not any(job.id == "vkpi_workflow_recovery" for job in scheduled)
            assert not any(job.id == "vkpi_fulfillment_sweep" for job in scheduled)
            assert not any(job.id == "vkpi_agent_cycle" for job in scheduled)
            assert any(
                isinstance(executor, jobs.FleetSafeAsyncIOExecutor)
                for executor in scheduler._executors.values()
            )
            assert all(
                getattr(job.func, "__vkpi_scheduled_fire_guard__", False)
                for job in scheduled
            )
        finally:
            await jobs.stop_scheduler()
        assert jobs.get_scheduler_status()["running"] is False
        assert scheduler is not None
        assert scheduler.running is False

    asyncio.run(scenario())


def test_workflow_auto_recovery_requires_explicit_valid_opt_in(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.services.scheduler.jobs_workflow_recovery import (
        workflow_auto_recovery_enabled,
    )

    monkeypatch.delenv("VKPI_WORKFLOW_AUTO_RECOVERY_ENABLED", raising=False)
    assert workflow_auto_recovery_enabled() is False

    monkeypatch.setenv("VKPI_WORKFLOW_AUTO_RECOVERY_ENABLED", "true")
    assert workflow_auto_recovery_enabled() is True

    monkeypatch.setenv("VKPI_WORKFLOW_AUTO_RECOVERY_ENABLED", "sometimes")
    with pytest.raises(RuntimeError, match="VKPI_WORKFLOW_AUTO_RECOVERY_ENABLED"):
        workflow_auto_recovery_enabled()


def test_workflow_scheduled_execution_requires_explicit_valid_opt_in(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.services.scheduler.jobs_workflow_recovery import (
        workflow_scheduled_execution_enabled,
    )

    monkeypatch.delenv("VKPI_WORKFLOW_SCHEDULED_EXECUTION_ENABLED", raising=False)
    assert workflow_scheduled_execution_enabled() is False

    monkeypatch.setenv("VKPI_WORKFLOW_SCHEDULED_EXECUTION_ENABLED", "1")
    assert workflow_scheduled_execution_enabled() is True

    monkeypatch.setenv("VKPI_WORKFLOW_SCHEDULED_EXECUTION_ENABLED", "maybe")
    with pytest.raises(RuntimeError, match="VKPI_WORKFLOW_SCHEDULED_EXECUTION_ENABLED"):
        workflow_scheduled_execution_enabled()
