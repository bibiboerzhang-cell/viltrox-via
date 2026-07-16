"""Lifecycle helpers for Apify clients and their hidden HTTPX transports.

``apify-client==1.10.0`` exposes neither ``close`` nor ``aclose`` on its
public clients.  Each client nevertheless owns *both* an ``httpx.Client`` and
an ``httpx.AsyncClient`` below ``client.http_client``.  Short-lived clients
must close both transports on every return/exception path; process-level
clients must do the same during interpreter shutdown.

The helpers are deliberately provider-agnostic and best-effort: cleanup must
never turn a completed provider operation into a failed business operation.
"""
from __future__ import annotations

import asyncio
import atexit
import inspect
import logging
import threading
import weakref
from contextlib import asynccontextmanager, contextmanager
from typing import Any, AsyncIterator, Iterator, TypeVar


logger = logging.getLogger(__name__)

_ClientT = TypeVar("_ClientT")
_STATE_LOCK = threading.RLock()
_CLOSED_CLIENTS: weakref.WeakSet[Any] = weakref.WeakSet()
_REGISTERED_CLIENTS: weakref.WeakSet[Any] = weakref.WeakSet()
_CLOSED_ATTR = "_vkpi_apify_lifecycle_closed"
_REGISTERED_ATTR = "_vkpi_apify_shutdown_registered"
_PENDING_TASK_ATTR = "_vkpi_apify_lifecycle_pending_task"


def _claim_once(client: Any, registry: weakref.WeakSet[Any], attr: str) -> bool:
    """Atomically claim a lifecycle action for a client.

    Apify clients are mutable and weak-referenceable, so both markers work for
    the supported SDK.  The attribute fallback also keeps simple test doubles
    and future wrapper implementations idempotent.
    """
    if client is None:
        return False
    with _STATE_LOCK:
        attr_available = True
        try:
            if bool(getattr(client, attr, False)):
                return False
        except Exception:
            attr_available = False
        try:
            if client in registry:
                return False
        except (TypeError, ValueError):
            pass
        if attr_available:
            try:
                setattr(client, attr, True)
            except Exception:
                attr_available = False
        try:
            registry.add(client)
        except (TypeError, ValueError):
            pass
        return True


def _is_closed(client: Any) -> bool:
    if client is None:
        return True
    with _STATE_LOCK:
        try:
            if bool(getattr(client, _CLOSED_ATTR, False)):
                return True
        except Exception:
            logger.debug("apify closed marker read failed | type=%s", type(client).__name__, exc_info=True)
        try:
            return client in _CLOSED_CLIENTS
        except (TypeError, ValueError):
            return False


def _mark_closed(client: Any) -> None:
    """Publish completion only after every cleanup target succeeded."""
    with _STATE_LOCK:
        try:
            setattr(client, _CLOSED_ATTR, True)
        except Exception:
            attr_unavailable = True
        else:
            attr_unavailable = False
        try:
            _CLOSED_CLIENTS.add(client)
        except (TypeError, ValueError):
            if attr_unavailable:
                logger.debug("apify client cannot retain a closed marker | type=%s", type(client).__name__)


def _active_pending_task(client: Any) -> asyncio.Task[bool] | None:
    """Return a live close task, clearing stale/cancelled-loop markers."""
    with _STATE_LOCK:
        try:
            task = getattr(client, _PENDING_TASK_ATTR, None)
        except Exception:
            return None
        if not isinstance(task, asyncio.Task):
            return None
        try:
            active = not task.done() and not task.get_loop().is_closed()
        except Exception:
            active = False
        if active:
            return task
        try:
            delattr(client, _PENDING_TASK_ATTR)
        except Exception:
            marker_unavailable = True
        else:
            marker_unavailable = False
        if marker_unavailable:
            logger.debug("apify pending-close marker could not be cleared | type=%s", type(client).__name__)
        return None


def _set_pending_task(client: Any, task: asyncio.Task[bool]) -> None:
    with _STATE_LOCK:
        try:
            setattr(client, _PENDING_TASK_ATTR, task)
        except Exception:
            logger.debug("apify client cannot retain pending-close task | type=%s", type(client).__name__)


def _clear_pending_task(client: Any, task: asyncio.Task[bool]) -> None:
    with _STATE_LOCK:
        try:
            if getattr(client, _PENDING_TASK_ATTR, None) is task:
                delattr(client, _PENDING_TASK_ATTR)
        except Exception:
            logger.debug("apify pending-close task cleanup failed | type=%s", type(client).__name__)


def _cleanup_targets(client: Any) -> list[Any]:
    """Return public and private cleanup targets without duplicate objects."""
    http_client = getattr(client, "http_client", None)
    candidates = (
        client,
        http_client,
        getattr(http_client, "httpx_client", None),
        getattr(http_client, "httpx_async_client", None),
    )
    targets: list[Any] = []
    seen: set[int] = set()
    for candidate in candidates:
        if candidate is None or id(candidate) in seen:
            continue
        seen.add(id(candidate))
        targets.append(candidate)
    return targets


async def _await_cleanups(
    awaitables: list[tuple[Any, Any, str]],
    *,
    initial_success: bool,
) -> bool:
    success = initial_success
    for awaitable, target, method_name in awaitables:
        try:
            await awaitable
        except asyncio.CancelledError:
            raise
        except Exception:
            success = False
            _log_cleanup_failure(target, method_name)
    return success


def _log_cleanup_failure(target: Any, method_name: str) -> None:
    logger.warning(
        "apify client cleanup failed | target=%s method=%s",
        type(target).__name__,
        method_name,
        exc_info=True,
    )


def _discard_unawaited(awaitables: list[tuple[Any, Any, str]]) -> None:
    for awaitable, _target, _method_name in awaitables:
        close = getattr(awaitable, "close", None)
        if callable(close):
            close()


def _finish_awaitables_from_sync(
    client: Any,
    awaitables: list[tuple[Any, Any, str]],
    *,
    initial_success: bool,
) -> None:
    """Finish async cleanup without marking completion before it succeeds."""
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        try:
            success = asyncio.run(
                _await_cleanups(awaitables, initial_success=initial_success)
            )
        except Exception:
            logger.warning("apify async cleanup runner failed", exc_info=True)
            return
        if success:
            _mark_closed(client)
        return

    # A synchronous provider adapter can occasionally be invoked from an
    # already-running event loop.  Blocking that loop would deadlock, so queue
    # one aggregate close task. Completion is published only from its callback;
    # cancellation/failure clears the pending marker and remains retryable.
    try:
        task = loop.create_task(
            _await_cleanups(awaitables, initial_success=initial_success)
        )
    except Exception:
        _discard_unawaited(awaitables)
        return
    _set_pending_task(client, task)

    def _consume_result(done: asyncio.Task[bool]) -> None:
        _clear_pending_task(client, done)
        try:
            success = done.result()
        except asyncio.CancelledError:
            return
        except Exception:
            logger.warning("apify scheduled cleanup failed", exc_info=True)
            return
        if success:
            _mark_closed(client)

    task.add_done_callback(_consume_result)


def close_apify_client(client: Any) -> None:
    """Close an Apify client and both hidden transports at most once.

    All cleanup exceptions are contained so a provider result is never
    rewritten as a failure by lifecycle housekeeping.
    """
    if _is_closed(client) or _active_pending_task(client) is not None:
        return
    success = True
    awaitables: list[tuple[Any, Any, str]] = []
    for target in _cleanup_targets(client):
        method_name = "close" if callable(getattr(target, "close", None)) else "aclose"
        method = getattr(target, method_name, None)
        if not callable(method):
            continue
        try:
            result = method()
            if inspect.isawaitable(result):
                awaitables.append((result, target, method_name))
        except Exception:
            success = False
            _log_cleanup_failure(target, method_name)
    if awaitables:
        _finish_awaitables_from_sync(
            client,
            awaitables,
            initial_success=success,
        )
    elif success:
        _mark_closed(client)


async def close_apify_client_async(client: Any) -> None:
    """Async counterpart that awaits async transports before returning."""
    if _is_closed(client):
        return
    pending = _active_pending_task(client)
    if pending is not None:
        try:
            if pending.get_loop() is asyncio.get_running_loop():
                await asyncio.shield(pending)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.warning("apify pending cleanup await failed", exc_info=True)
            return
        return
    success = True
    for target in _cleanup_targets(client):
        method_name = "aclose" if callable(getattr(target, "aclose", None)) else "close"
        method = getattr(target, method_name, None)
        if not callable(method):
            continue
        try:
            result = method()
            if inspect.isawaitable(result):
                await result
        except Exception:
            success = False
            _log_cleanup_failure(target, method_name)
    if success:
        _mark_closed(client)


@contextmanager
def managed_apify_client(client: _ClientT) -> Iterator[_ClientT]:
    """Own a short-lived synchronous client across every exit path."""
    try:
        yield client
    finally:
        close_apify_client(client)


@asynccontextmanager
async def managed_apify_client_async(client: _ClientT) -> AsyncIterator[_ClientT]:
    """Own a short-lived asynchronous client across every exit path."""
    try:
        yield client
    finally:
        await close_apify_client_async(client)


def register_apify_client_shutdown(client: _ClientT) -> _ClientT:
    """Register one best-effort cleanup for a process-level shared client."""
    if _claim_once(client, _REGISTERED_CLIENTS, _REGISTERED_ATTR):
        atexit.register(close_apify_client, client)
    return client


__all__ = [
    "close_apify_client",
    "close_apify_client_async",
    "managed_apify_client",
    "managed_apify_client_async",
    "register_apify_client_shutdown",
]
