"""Bounded parallel execution for independent dashboard read aggregates.

The dashboard summary is assembled from several unrelated PostgreSQL views.
On a cold full-summary cache miss those reads used to run serially on the
request connection, so latency was the sum of every source.  Each worker here
opens its own release-guarded scope; no request connection or transaction is
shared across threads.  SQLite remains sequential for deterministic local and
hermetic test behaviour.
"""
from __future__ import annotations

from collections.abc import Callable, Mapping
from concurrent.futures import ThreadPoolExecutor
import threading
from typing import TypeVar

from app.core.config import POSTGRES_POOL_MAX_SIZE
from app.db.connection import db_connection_sync_scope, is_postgres_runtime


T = TypeVar("T")

MAX_PARALLEL_DASHBOARD_READ_WORKERS = 4
_PARALLELISM_AVAILABLE = int(POSTGRES_POOL_MAX_SIZE) >= 2
_PARALLEL_DB_SLOT_COUNT = max(
    1,
    min(
        MAX_PARALLEL_DASHBOARD_READ_WORKERS,
        max(1, int(POSTGRES_POOL_MAX_SIZE) - 1),
    ),
)
_PARALLEL_DB_SLOTS = threading.BoundedSemaphore(_PARALLEL_DB_SLOT_COUNT)


def _run_scoped(fn: Callable[[], T]) -> T:
    with _PARALLEL_DB_SLOTS:
        with db_connection_sync_scope(release_validation_guard=True):
            return fn()


def run_dashboard_read_tasks(
    tasks: Mapping[str, Callable[[], T]],
    *,
    max_workers: int = MAX_PARALLEL_DASHBOARD_READ_WORKERS,
) -> dict[str, T]:
    """Run independent reads concurrently without sharing a DB connection."""

    ordered = list(tasks.items())
    if len(ordered) < 2 or not is_postgres_runtime() or not _PARALLELISM_AVAILABLE:
        return {name: fn() for name, fn in ordered}

    workers = max(1, min(int(max_workers), len(ordered), _PARALLEL_DB_SLOT_COUNT))
    with ThreadPoolExecutor(
        max_workers=workers,
        thread_name_prefix="vkpi-dashboard-read",
    ) as pool:
        futures = [(name, pool.submit(_run_scoped, fn)) for name, fn in ordered]
        return {name: future.result() for name, future in futures}


__all__ = [
    "MAX_PARALLEL_DASHBOARD_READ_WORKERS",
    "run_dashboard_read_tasks",
]
