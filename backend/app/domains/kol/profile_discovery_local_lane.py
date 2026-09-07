"""Bounded provider-free local reads for an independently running hybrid lane.

There is no pending work queue: at most two reads may be outstanding per worker
process, including timed-out reads. A timeout cannot kill a database thread, so
its slot stays occupied until the actual read finishes. Threads never attach
results or write session state; the awaiting orchestrator alone may do that.
"""
from __future__ import annotations

import asyncio
from concurrent.futures import ThreadPoolExecutor
from contextvars import copy_context
from threading import BoundedSemaphore
from typing import Any, Callable

from app.domains.kol.provider_job_access import ProviderJobAccessError

LOCAL_LANE_TIMEOUT_SECONDS = 8.0
_SLOTS = BoundedSemaphore(2)
_EXECUTOR = ThreadPoolExecutor(max_workers=2, thread_name_prefix="kol-local-lane")


def unavailable_result(status: str, reason: str) -> dict[str, Any]:
    return {"method": "hybrid_local", "status": status, "items": [],
            "buckets": {"creator": [], "reviewer": []},
            "diagnostics": {"local_recall_status": status, "returned_count": None,
                            "reason": reason, "inventory_recall_performed": status != "capacity_unavailable"}}


async def read_local_lane(read: Callable[[], dict[str, Any]]) -> dict[str, Any]:
    if not _SLOTS.acquire(blocking=False):
        return unavailable_result("capacity_unavailable", "local_read_capacity_exhausted")
    try:
        future = _EXECUTOR.submit(copy_context().run, read)
    except BaseException:
        _SLOTS.release()
        raise
    future.add_done_callback(lambda _future: _SLOTS.release())
    wrapped = asyncio.wrap_future(future)
    # A timed-out read may eventually fail. Consume that exception without any
    # late attachment, retry, new queue submission or unhandled-future warning.
    wrapped.add_done_callback(lambda done: None if done.cancelled() else done.exception())
    try:
        return await asyncio.wait_for(asyncio.shield(wrapped), timeout=LOCAL_LANE_TIMEOUT_SECONDS)
    except asyncio.TimeoutError:
        return unavailable_result("timeout", "local_read_deadline_exceeded")
    except (ValueError, PermissionError, ProviderJobAccessError):
        raise
    except Exception:
        return unavailable_result("failed", "local_read_failed")
