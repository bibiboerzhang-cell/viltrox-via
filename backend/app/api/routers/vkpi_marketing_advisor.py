"""Private Marketing Advisor conversations and owner-confirmed personal memory."""
from __future__ import annotations

import json
from typing import Any, Callable

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from starlette.responses import StreamingResponse

from app.api.dependencies.advisor_scope import (
    require_advisor_read_scope,
    require_advisor_write_scope,
)
from app.domains.advisor import repository, service
from app.domains.advisor.scope import AdvisorScope


router = APIRouter(
    prefix="/api/admin/vkpi/marketing-advisor",
    tags=["vkpi-marketing-advisor"],
)


class ThreadCreateBody(BaseModel):
    title: str = Field(default="", max_length=240)
    context_refs: list[dict[str, Any]] = Field(default_factory=list, max_length=12)


class ThreadUpdateBody(BaseModel):
    title: str | None = Field(default=None, max_length=240)
    status: str | None = Field(default=None, max_length=20)
    context_refs: list[dict[str, Any]] | None = Field(default=None, max_length=12)


class MessageCreateBody(BaseModel):
    content: str = Field(..., min_length=1, max_length=20_000)
    context_refs: list[dict[str, Any]] = Field(default_factory=list, max_length=12)
    requested_actions: list[dict[str, Any]] = Field(default_factory=list, max_length=12)
    client_request_id: str = Field(default="", max_length=120)
    allow_external_ai: bool = False


class MemorySettingsBody(BaseModel):
    state: str = Field(..., max_length=20)
    retention_days: int | None = Field(default=None, ge=1, le=3650)


class MemoryCandidateBody(BaseModel):
    memory_kind: str = Field(..., max_length=40)
    memory_key: str = Field(..., min_length=1, max_length=160)
    summary: str = Field(default="", max_length=2000)
    value: dict[str, Any] = Field(default_factory=dict)
    provenance: dict[str, Any] = Field(default_factory=dict)
    sensitivity: str = Field(default="normal", max_length=20)
    source_message_uid: str | None = Field(default=None, max_length=80)


class MemoryFactUpdateBody(BaseModel):
    summary: str | None = Field(default=None, max_length=2000)
    value: dict[str, Any] | None = None
    status: str | None = Field(default=None, max_length=20)


def _fields_set(model: BaseModel) -> set[str]:
    value = getattr(model, "model_fields_set", None)
    if isinstance(value, set):
        return value
    return set(getattr(model, "__fields_set__", set()) or set())


def _run(fn: Callable[..., Any], *args: Any, **kwargs: Any) -> Any:
    try:
        return fn(*args, **kwargs)
    except repository.AdvisorValidationError as exc:
        raise HTTPException(status_code=422, detail={"code": exc.code, "reason": str(exc)}) from exc
    except repository.AdvisorNotFound as exc:
        raise HTTPException(status_code=404, detail={"code": exc.code, "reason": str(exc)}) from exc
    except repository.AdvisorConflict as exc:
        raise HTTPException(status_code=409, detail={"code": exc.code, "reason": str(exc)}) from exc
    except repository.AdvisorSchemaUnavailable as exc:
        raise HTTPException(status_code=503, detail={"code": exc.code, "reason": str(exc)}) from exc
    except repository.AdvisorRepositoryError as exc:
        raise HTTPException(status_code=400, detail={"code": exc.code, "reason": str(exc)}) from exc


@router.get("/readiness")
def advisor_readiness(
    scope: AdvisorScope = Depends(require_advisor_read_scope),
) -> dict[str, Any]:
    del scope
    return service.readiness()


@router.post("/threads")
def create_thread(
    body: ThreadCreateBody,
    scope: AdvisorScope = Depends(require_advisor_write_scope),
) -> dict[str, Any]:
    return {"status": "ok", "thread": _run(
        repository.create_thread,
        scope,
        title=body.title,
        context_refs=body.context_refs,
    )}


@router.get("/threads")
def list_threads(
    limit: int = Query(default=50, ge=1, le=200),
    scope: AdvisorScope = Depends(require_advisor_read_scope),
) -> dict[str, Any]:
    items = _run(repository.list_threads, scope, limit=limit)
    return {"status": "ok", "threads": items, "count": len(items)}


@router.get("/threads/{thread_uid}")
def get_thread(
    thread_uid: str,
    scope: AdvisorScope = Depends(require_advisor_read_scope),
) -> dict[str, Any]:
    return {"status": "ok", "thread": _run(repository.get_thread, scope, thread_uid)}


@router.patch("/threads/{thread_uid}")
def update_thread(
    thread_uid: str,
    body: ThreadUpdateBody,
    scope: AdvisorScope = Depends(require_advisor_write_scope),
) -> dict[str, Any]:
    present = _fields_set(body)
    return {"status": "ok", "thread": _run(
        repository.update_thread,
        scope,
        thread_uid,
        title=body.title,
        status=body.status,
        context_refs=body.context_refs,
        context_refs_present="context_refs" in present,
    )}


@router.delete("/threads/{thread_uid}")
def delete_thread(
    thread_uid: str,
    scope: AdvisorScope = Depends(require_advisor_write_scope),
) -> dict[str, Any]:
    return {"status": "ok", "thread": _run(repository.delete_thread, scope, thread_uid)}


@router.get("/threads/{thread_uid}/messages")
def list_messages(
    thread_uid: str,
    limit: int = Query(default=100, ge=1, le=500),
    scope: AdvisorScope = Depends(require_advisor_read_scope),
) -> dict[str, Any]:
    items = _run(repository.list_messages, scope, thread_uid, limit=limit)
    return {"status": "ok", "messages": items, "count": len(items)}


@router.post("/threads/{thread_uid}/messages")
def create_message(
    thread_uid: str,
    body: MessageCreateBody,
    scope: AdvisorScope = Depends(require_advisor_write_scope),
) -> dict[str, Any]:
    return _run(
        service.create_message_turn,
        scope,
        thread_uid,
        content=body.content,
        context_refs=body.context_refs,
        requested_actions=body.requested_actions,
        client_request_id=body.client_request_id,
        allow_external_ai=body.allow_external_ai,
    )


def _sse(event: str, payload: dict[str, Any]) -> str:
    return (
        f"event: {event}\n"
        f"data: {json.dumps(payload, ensure_ascii=False, separators=(',', ':'), default=str)}\n\n"
    )


@router.post("/threads/{thread_uid}/messages/stream")
def create_message_stream(
    thread_uid: str,
    body: MessageCreateBody,
    scope: AdvisorScope = Depends(require_advisor_write_scope),
) -> StreamingResponse:
    """Staged SSE contract: immediate acceptance, then one persisted final turn.

    Provider transports remain buffered and are honestly advertised as such;
    this endpoint does not pretend to emit provider tokens. The durable request
    claim makes reconnect/retry safe before any paid call is attempted.
    """

    def events():
        yield _sse(
            "accepted",
            {
                "status": "accepted",
                "transport": "staged_sse_v1",
                "provider_streaming": False,
                "durable_idempotency": bool(body.client_request_id),
            },
        )
        try:
            result = service.create_message_turn(
                scope,
                thread_uid,
                content=body.content,
                context_refs=body.context_refs,
                requested_actions=body.requested_actions,
                client_request_id=body.client_request_id,
                allow_external_ai=body.allow_external_ai,
            )
        except repository.AdvisorRepositoryError as exc:
            yield _sse(
                "error",
                {
                    "status": "error",
                    "code": exc.code,
                    "retryable": False,
                    "provider_streaming": False,
                },
            )
            return
        yield _sse("final", result)

    return StreamingResponse(
        events(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-store",
            "X-Accel-Buffering": "no",
        },
    )


@router.get("/memory")
def get_memory(
    limit: int = Query(default=100, ge=1, le=500),
    scope: AdvisorScope = Depends(require_advisor_read_scope),
) -> dict[str, Any]:
    return {"status": "ok", **_run(repository.get_memory, scope, limit=limit)}


@router.patch("/memory/settings")
def update_memory_settings(
    body: MemorySettingsBody,
    scope: AdvisorScope = Depends(require_advisor_write_scope),
) -> dict[str, Any]:
    settings = _run(
        repository.update_memory_settings,
        scope,
        state=body.state,
        retention_days=body.retention_days,
    )
    return {"status": "ok", "settings": settings}


@router.post("/memory/candidates")
def create_memory_candidate(
    body: MemoryCandidateBody,
    scope: AdvisorScope = Depends(require_advisor_write_scope),
) -> dict[str, Any]:
    candidate = _run(
        repository.create_memory_candidate,
        scope,
        memory_kind=body.memory_kind,
        memory_key=body.memory_key,
        summary=body.summary,
        value=body.value,
        provenance=body.provenance,
        sensitivity=body.sensitivity,
        source_message_uid=body.source_message_uid,
    )
    return {
        "status": "pending_confirmation",
        "candidate": candidate,
        "memory_active": False,
    }


@router.post("/memory/candidates/{candidate_uid}/confirm")
def confirm_memory_candidate(
    candidate_uid: str,
    scope: AdvisorScope = Depends(require_advisor_write_scope),
) -> dict[str, Any]:
    fact = _run(repository.confirm_memory_candidate, scope, candidate_uid)
    return {"status": "ok", "fact": fact, "memory_active": fact.get("status") == "active"}


@router.post("/memory/candidates/{candidate_uid}/reject")
def reject_memory_candidate(
    candidate_uid: str,
    scope: AdvisorScope = Depends(require_advisor_write_scope),
) -> dict[str, Any]:
    candidate = _run(repository.reject_memory_candidate, scope, candidate_uid)
    return {"status": "ok", "candidate": candidate, "memory_active": False}


@router.delete("/memory/candidates/{candidate_uid}")
def delete_memory_candidate(
    candidate_uid: str,
    scope: AdvisorScope = Depends(require_advisor_write_scope),
) -> dict[str, Any]:
    candidate = _run(repository.delete_memory_candidate, scope, candidate_uid)
    return {"status": "ok", "candidate": candidate}


@router.patch("/memory/facts/{fact_uid}")
def update_memory_fact(
    fact_uid: str,
    body: MemoryFactUpdateBody,
    scope: AdvisorScope = Depends(require_advisor_write_scope),
) -> dict[str, Any]:
    present = _fields_set(body)
    fact = _run(
        repository.update_memory_fact,
        scope,
        fact_uid,
        summary=body.summary,
        value=body.value,
        value_present="value" in present,
        status=body.status,
    )
    return {"status": "ok", "fact": fact}


@router.delete("/memory/facts/{fact_uid}")
def delete_memory_fact(
    fact_uid: str,
    scope: AdvisorScope = Depends(require_advisor_write_scope),
) -> dict[str, Any]:
    fact = _run(repository.delete_memory_fact, scope, fact_uid)
    return {"status": "ok", "fact": fact}


@router.get("/draft-actions")
def list_action_drafts(
    thread_uid: str | None = Query(default=None),
    limit: int = Query(default=100, ge=1, le=500),
    scope: AdvisorScope = Depends(require_advisor_read_scope),
) -> dict[str, Any]:
    drafts = _run(repository.list_action_drafts, scope, thread_uid=thread_uid, limit=limit)
    return {
        "status": "ok",
        "draft_actions": drafts,
        "count": len(drafts),
        "executable": False,
    }
