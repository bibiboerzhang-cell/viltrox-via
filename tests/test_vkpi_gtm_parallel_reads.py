from __future__ import annotations

from contextlib import contextmanager
import threading

from app.domains.market_brain import parallel_reads


def test_non_postgres_reads_remain_sequential(monkeypatch) -> None:
    monkeypatch.setattr(parallel_reads, "is_postgres_runtime", lambda: False)
    caller_thread = threading.get_ident()
    seen: list[tuple[str, int]] = []

    result = parallel_reads.run_read_tasks(
        {
            "first": lambda: seen.append(("first", threading.get_ident())) or 1,
            "second": lambda: seen.append(("second", threading.get_ident())) or 2,
        }
    )

    assert result == {"first": 1, "second": 2}
    assert seen == [("first", caller_thread), ("second", caller_thread)]


def test_postgres_reads_overlap_and_each_gets_a_scoped_lease(monkeypatch) -> None:
    monkeypatch.setattr(parallel_reads, "is_postgres_runtime", lambda: True)
    monkeypatch.setattr(parallel_reads, "_PARALLEL_DB_SLOT_COUNT", 4)
    monkeypatch.setattr(parallel_reads, "_PARALLEL_DB_SLOTS", threading.BoundedSemaphore(4))
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

    result = parallel_reads.run_read_tasks(
        {f"task_{index}": lambda index=index: task(index) for index in range(4)}
    )

    assert result == {f"task_{index}": index for index in range(4)}
    assert len(set(entered)) == 4
    assert sorted(exited) == sorted(entered)
    assert guards == [True, True, True, True]


def test_parallel_read_exception_remains_caller_visible(monkeypatch) -> None:
    monkeypatch.setattr(parallel_reads, "is_postgres_runtime", lambda: True)

    @contextmanager
    def fake_scope(*, release_validation_guard: bool):
        assert release_validation_guard is True
        yield None

    monkeypatch.setattr(parallel_reads, "db_connection_sync_scope", fake_scope)

    def broken() -> int:
        raise RuntimeError("source unavailable")

    try:
        parallel_reads.run_read_tasks({"ok": lambda: 1, "broken": broken})
    except RuntimeError as exc:
        assert str(exc) == "source unavailable"
    else:  # pragma: no cover - regression assertion
        raise AssertionError("parallel read exception was swallowed")
