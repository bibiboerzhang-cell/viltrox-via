"""Release every acquired session lock; uncertain cleanup retires its owner."""
from __future__ import annotations

from typing import Any, Callable, Iterable

import psycopg


class WorkerLockCleanupFailed(RuntimeError):
    """One or more session unlocks failed; never reuse this connection."""


class WorkerConnectionRetired(psycopg.InterfaceError):
    """Closing failed, but the caller must still abandon this connection."""


def release_worker_locks(conn: Any, locks: Iterable[tuple[str, str]], unlock: Callable[..., Any]) -> None:
    errors: list[Exception] = []
    for scope, key in locks:
        try:
            unlock(conn, scope, key)
        except Exception as exc:
            errors.append(exc)
    if errors:
        raise WorkerLockCleanupFailed(f"worker session lock cleanup uncertain ({len(errors)} unlock errors)") from errors[0]


def worker_lock_cleanup_failed(exc: BaseException) -> bool:
    seen: set[int] = set()
    while id(exc) not in seen:
        seen.add(id(exc))
        if isinstance(exc, WorkerLockCleanupFailed):
            return True
        cause = exc.__cause__ or exc.__context__
        if cause is None:
            return False
        exc = cause
    return False


def retire_worker_connection(conn: Any) -> None:
    try:
        conn.close()
    except Exception as exc:
        raise WorkerConnectionRetired("worker connection must be abandoned after uncertain lock cleanup") from exc
