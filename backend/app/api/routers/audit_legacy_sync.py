"""Compatibility boundary for the retired synchronous audit endpoint."""
from __future__ import annotations

from typing import Any, Awaitable, Callable

from fastapi import HTTPException


async def run_audit_sync(
    request,
    req,
    current_user: dict[str, Any] | None,
    audit_async_func: Callable[..., Awaitable[dict[str, Any]]],
) -> dict[str, Any]:
    """Queue through the durable audit path or fail closed when it is absent.

    The historical inline implementation followed an unconditional 503 raise,
    so it was unreachable. Keeping only the two observable branches makes the
    provider and database boundary explicit: this compatibility endpoint never
    performs paid provider work or persistence in the web process itself.
    """
    if getattr(request.app.state, "job_queue", None) is not None:
        response = await audit_async_func(request, req, current_user)
        response["deprecated_sync"] = True
        response["message"] = (
            "Synchronous audit is deprecated; request was queued instead."
        )
        return response

    raise HTTPException(status_code=503, detail="durable job queue unavailable")


__all__ = ["run_audit_sync"]
