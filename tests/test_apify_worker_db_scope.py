from __future__ import annotations

from contextlib import contextmanager

import pytest

from app.db import connection as db_connection
from app.workers import apify_jobs_worker as worker


def test_claimed_job_uses_explicit_db_scope_on_success(monkeypatch) -> None:
    events: list[str] = []

    @contextmanager
    def scope():
        events.append("scope-enter")
        try:
            yield
        finally:
            events.append("scope-exit")

    @contextmanager
    def heartbeat(_job_id: int, _owner: str, _task_id: str, _fence: int):
        events.append("heartbeat-enter")
        try:
            yield
        finally:
            events.append("heartbeat-exit")

    monkeypatch.setattr(worker, "db_connection_sync_scope", scope)
    monkeypatch.setattr(worker, "_running_job_heartbeat", heartbeat)
    monkeypatch.setattr(worker, "_process_claimed_job", lambda _conn, _job: events.append("process"))
    monkeypatch.setattr(worker, "acquire_provider_execution_claim", lambda *_args, **_kwargs: 7)
    monkeypatch.setattr(worker, "finalize_provider_execution_claim", lambda *_args, **_kwargs: True)

    class Cursor:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def execute(self, *_args, **_kwargs):
            return None

        def fetchone(self):
            return {"status": "done"}

    class Conn:
        def cursor(self, **_kwargs):
            return Cursor()

    worker._execute_claimed_job(Conn(), {"id": 42, "lease_owner": "worker-a"})

    assert events == [
        "scope-enter",
        "heartbeat-enter",
        "process",
        "heartbeat-exit",
        "scope-exit",
    ]


def test_claimed_job_releases_db_scope_on_exception(monkeypatch) -> None:
    events: list[str] = []

    @contextmanager
    def scope():
        events.append("scope-enter")
        try:
            yield
        finally:
            events.append("scope-exit")

    @contextmanager
    def heartbeat(_job_id: int, _owner: str, _task_id: str, _fence: int):
        events.append("heartbeat-enter")
        try:
            yield
        finally:
            events.append("heartbeat-exit")

    def fail(_conn, _job) -> None:
        events.append("process")
        raise RuntimeError("boom")

    monkeypatch.setattr(worker, "db_connection_sync_scope", scope)
    monkeypatch.setattr(worker, "_running_job_heartbeat", heartbeat)
    monkeypatch.setattr(worker, "_process_claimed_job", fail)
    monkeypatch.setattr(worker, "acquire_provider_execution_claim", lambda *_args, **_kwargs: 8)
    monkeypatch.setattr(worker, "finalize_provider_execution_claim", lambda *_args, **_kwargs: True)

    with pytest.raises(RuntimeError, match="boom"):
        worker._execute_claimed_job(object(), {"id": 43, "lease_owner": "worker-b"})

    assert events == [
        "scope-enter",
        "heartbeat-enter",
        "process",
        "heartbeat-exit",
        "scope-exit",
    ]


def test_postgres_scope_closes_and_restores_context_on_exception(monkeypatch) -> None:
    class FakeConnection:
        closed = False

        def close(self) -> None:
            self.closed = True

    fake = FakeConnection()
    before = db_connection._scoped_conn.get()
    monkeypatch.setattr(db_connection, "is_postgres_runtime", lambda: True)
    def build(*, release_validation_guard: bool) -> FakeConnection:
        assert release_validation_guard is False
        return fake

    monkeypatch.setattr(db_connection, "_build_postgres_conn", build)

    with pytest.raises(ValueError, match="scope failure"):
        with db_connection.db_connection_sync_scope():
            assert db_connection.get_conn() is fake
            raise ValueError("scope failure")

    assert fake.closed is True
    assert db_connection._scoped_conn.get() is before


def test_postgres_scope_closes_and_restores_context_on_success(monkeypatch) -> None:
    class FakeConnection:
        closed = False

        def close(self) -> None:
            self.closed = True

    fake = FakeConnection()
    before = db_connection._scoped_conn.get()
    monkeypatch.setattr(db_connection, "is_postgres_runtime", lambda: True)
    def build(*, release_validation_guard: bool) -> FakeConnection:
        assert release_validation_guard is False
        return fake

    monkeypatch.setattr(db_connection, "_build_postgres_conn", build)

    with db_connection.db_connection_sync_scope():
        assert db_connection.get_conn() is fake

    assert fake.closed is True
    assert db_connection._scoped_conn.get() is before


def test_postgres_reusing_scope_does_not_lease_twice_inside_request_scope(monkeypatch) -> None:
    class FakeConnection:
        closed = False

        def close(self) -> None:
            self.closed = True

    fake = FakeConnection()
    builds: list[FakeConnection] = []
    monkeypatch.setattr(db_connection, "is_postgres_runtime", lambda: True)

    def build(*, release_validation_guard: bool) -> FakeConnection:
        assert release_validation_guard is False
        builds.append(fake)
        return fake

    monkeypatch.setattr(db_connection, "_build_postgres_conn", build)

    with db_connection.db_connection_sync_scope():
        assert db_connection.get_conn() is fake
        with db_connection.db_connection_sync_reusing_scope():
            assert db_connection.get_conn() is fake
        assert fake.closed is False

    assert fake.closed is True
    assert builds == [fake]


def test_postgres_reusing_scope_still_bounds_standalone_call(monkeypatch) -> None:
    class FakeConnection:
        closed = False

        def close(self) -> None:
            self.closed = True

    fake = FakeConnection()
    before = db_connection._scoped_conn.get()
    monkeypatch.setattr(db_connection, "is_postgres_runtime", lambda: True)
    def build(*, release_validation_guard: bool) -> FakeConnection:
        assert release_validation_guard is False
        return fake

    monkeypatch.setattr(db_connection, "_build_postgres_conn", build)

    with db_connection.db_connection_sync_reusing_scope():
        assert db_connection.get_conn() is fake

    assert fake.closed is True
    assert db_connection._scoped_conn.get() is before
