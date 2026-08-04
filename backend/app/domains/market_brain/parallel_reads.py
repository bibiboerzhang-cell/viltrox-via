"""Bounded parallel execution for independent, read-only GTM aggregates.

The GTM summary and preview compose several PostgreSQL-backed views which do
not depend on one another.  Running them serially makes one cold cache miss pay
the sum of every query.  This helper overlaps those reads while preserving the
request connection invariant: every worker gets its own bounded DB scope and
returns the lease before the request completes.

SQLite stays sequential.  Besides avoiding extra local connections, that keeps
the hermetic test/runtime contract deterministic.  A process-wide slot gate
also prevents several authorization-scoped cold misses from consuming the
whole PostgreSQL pool at once.
"""
from __future__ import annotations

from collections.abc import Callable, Mapping
from concurrent.futures import ThreadPoolExecutor
import threading
from typing import TypeVar

from app.core.config import POSTGRES_POOL_MAX_SIZE
from app.db.connection import db_connection_sync_scope, is_postgres_runtime


T = TypeVar("T")

MAX_PARALLEL_READ_WORKERS = 4
_PARALLEL_DB_SLOT_COUNT = max(
    1,
    min(MAX_PARALLEL_READ_WORKERS, max(1, int(POSTGRES_POOL_MAX_SIZE) - 1)),
)
_PARALLEL_DB_SLOTS = threading.BoundedSemaphore(_PARALLEL_DB_SLOT_COUNT)


def _run_scoped(fn: Callable[[], T]) -> T:
    with _PARALLEL_DB_SLOTS:
        with db_connection_sync_scope(release_validation_guard=True):
            return fn()


def run_read_tasks(
    tasks: Mapping[str, Callable[[], T]],
    *,
    max_workers: int = MAX_PARALLEL_READ_WORKERS,
) -> dict[str, T]:
    """Run independent reads concurrently on PostgreSQL, sequentially elsewhere.

    Results retain input order.  Exceptions are deliberately re-raised when a
    future is collected so each caller's existing section-level fail-soft
    policy remains authoritative.
    """

    ordered = list(tasks.items())
    if len(ordered) < 2 or not is_postgres_runtime():
        return {name: fn() for name, fn in ordered}

    workers = max(1, min(int(max_workers), len(ordered), _PARALLEL_DB_SLOT_COUNT))
    with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="vkpi-gtm-read") as pool:
        futures = [(name, pool.submit(_run_scoped, fn)) for name, fn in ordered]
        return {name: future.result() for name, future in futures}


__all__ = ["MAX_PARALLEL_READ_WORKERS", "run_read_tasks"]
