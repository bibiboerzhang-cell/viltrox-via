from __future__ import annotations

from contextlib import contextmanager
import threading

import pytest

from app.domains.dashboard import parallel_reads


def test_non_postgres_dashboard_reads_remain_sequential(monkeypatch) -> None:
    monkeypatch.setattr(parallel_reads, "is_postgres_runtime", lambda: False)
    caller_thread = threading.get_ident()
    seen: list[tuple[str, int]] = []

    result = parallel_reads.run_dashboard_read_tasks(
        {
            "first": lambda: seen.append(("first", threading.get_ident())) or 1,
            "second": lambda: seen.append(("second", threading.get_ident())) or 2,
        }
    )

    assert result == {"first": 1, "second": 2}
    assert seen == [("first", caller_thread), ("second", caller_thread)]


def test_single_connection_pool_falls_back_to_caller_thread(monkeypatch) -> None:
    monkeypatch.setattr(parallel_reads, "is_postgres_runtime", lambda: True)
    monkeypatch.setattr(parallel_reads, "_PARALLELISM_AVAILABLE", False)
    caller_thread = threading.get_ident()

    result = parallel_reads.run_dashboard_read_tasks(
        {
            "first": lambda: threading.get_ident(),
            "second": lambda: threading.get_ident(),
        }
    )

    assert result == {"first": caller_thread, "second": caller_thread}


def test_postgres_dashboard_reads_overlap_with_guarded_scopes(monkeypatch) -> None:
    monkeypatch.setattr(parallel_reads, "is_postgres_runtime", lambda: True)
    monkeypatch.setattr(parallel_reads, "_PARALLEL_DB_SLOT_COUNT", 4)
    monkeypatch.setattr(
        parallel_reads,
        "_PARALLEL_DB_SLOTS",
        threading.BoundedSemaphore(4),
    )
    entered: list[int] = []
    exited: list[int] = []
    guards: list[bool] = []
    gate = threading.Barrier(4, timeout=2)

    @contextmanager
    def fake_scope(*, release_validation_guard: bool):
        guards.append(release_validation_guard)
        thread_id = threading.get_ident()
        entered.append(thread_id)
        try:
            yield None
        finally:
            exited.append(thread_id)

    monkeypatch.setattr(parallel_reads, "db_connection_sync_scope", fake_scope)

    def task(value: int) -> int:
        gate.wait()
        return value

    result = parallel_reads.run_dashboard_read_tasks(
        {
            f"task_{index}": lambda index=index: task(index)
            for index in range(4)
        }
    )

    assert result == {f"task_{index}": index for index in range(4)}
    assert len(set(entered)) == 4
    assert sorted(exited) == sorted(entered)
    assert guards == [True, True, True, True]


def test_dashboard_parallel_read_exception_remains_visible(monkeypatch) -> None:
    monkeypatch.setattr(parallel_reads, "is_postgres_runtime", lambda: True)

    @contextmanager
    def fake_scope(*, release_validation_guard: bool):
        assert release_validation_guard is True
        yield None

    monkeypatch.setattr(parallel_reads, "db_connection_sync_scope", fake_scope)

    with pytest.raises(RuntimeError, match="source unavailable"):
        parallel_reads.run_dashboard_read_tasks(
            {
                "ok": lambda: 1,
                "broken": lambda: (_ for _ in ()).throw(
                    RuntimeError("source unavailable")
                ),
            }
        )
