"""
api/routers/via.py — Via session/persona/event APIs
"""
from __future__ import annotations

import asyncio
import json

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse

from app.core.security import get_current_user
from app.services.security.rate_limiter import rate_limit
from app.services.via import build_via_event_bus
from app.services.via.stock_watch import build_via_stock_watch
from app.services.via.session_service import (
    bootstrap_via_session,
    patch_via_persona_for_session,
    publish_via_session_event,
    record_via_reward_trace_for_session,
    refresh_via_memory_refs,
    reply_in_via_session,
)

try:
    from sse_starlette.sse import EventSourceResponse
    SSE_AVAILABLE = True
except Exception:
    EventSourceResponse = None
    SSE_AVAILABLE = False


router = APIRouter(prefix="/api/via", tags=["via"])


def _event_bus(request: Request):
    bus = getattr(request.app.state, "via_event_bus", None)
    if bus is None:
        bus = build_via_event_bus()
        request.app.state.via_event_bus = bus
    return bus


@router.get("/stock-watch")
async def via_stock_watch(limit: int = 4):
    return build_via_stock_watch(limit=limit)


@router.post("/sessions")
@rate_limit("via_chat", max_requests=30, window_sec=60)
async def create_via_session(request: Request):
    body = await request.json() if request.headers.get("content-type", "").startswith("application/json") else {}
    bus = _event_bus(request)
    user = get_current_user(request)
    bundle = await bootstrap_via_session(
        user=user,
        signed_device_id=str(body.get("signed_device_id") or "").strip(),
        client_fingerprint=str(body.get("client_fingerprint") or request.headers.get("User-Agent", "")).strip(),
        current_surface=str(body.get("surface") or "upload").strip() or "upload",
        persona_patch=body.get("persona") if isinstance(body.get("persona"), dict) else {},
        request_ip=getattr(getattr(request, "client", None), "host", "") or "",
        event_bus=bus,
    )
    return bundle


@router.get("/sessions/{session_key}")
async def get_via_session_bundle(request: Request, session_key: str):
    from app.db.repositories.via import get_via_session_bundle as load_bundle
    bundle = await asyncio.to_thread(load_bundle, session_key, 24)
    if not bundle:
        raise HTTPException(status_code=404, detail="Via session not found")
    return bundle


@router.patch("/sessions/{session_key}/persona")
@rate_limit("via_chat", max_requests=30, window_sec=60)
async def patch_via_persona(request: Request, session_key: str):
    body = await request.json()
    bundle = await patch_via_persona_for_session(
        session_key=session_key,
        patch=body or {},
        event_bus=_event_bus(request),
    )
    if not bundle:
        raise HTTPException(status_code=404, detail="Via session not found")
    return bundle


@router.post("/sessions/{session_key}/memory/refresh")
@rate_limit("via_chat", max_requests=30, window_sec=60)
async def refresh_via_memory(request: Request, session_key: str):
    bundle = await refresh_via_memory_refs(session_key, event_bus=_event_bus(request))
    if not bundle:
        raise HTTPException(status_code=404, detail="Via session not found")
    return bundle


@router.post("/sessions/{session_key}/events")
@rate_limit("via_chat", max_requests=30, window_sec=60)
async def post_via_event(request: Request, session_key: str):
    body = await request.json()
    result = await publish_via_session_event(
        session_key=session_key,
        event_bus=_event_bus(request),
        event_type=str(body.get("event_type") or "message").strip() or "message",
        title=str(body.get("title") or "Via update").strip() or "Via update",
        text=str(body.get("text") or "").strip() or "Via posted a new event.",
        payload=body.get("payload") if isinstance(body.get("payload"), dict) else {},
        current_surface=str(body.get("surface") or "").strip(),
    )
    if not result:
        raise HTTPException(status_code=404, detail="Via session not found")
    return {"ok": True, **result}


@router.post("/sessions/{session_key}/respond")
@rate_limit("via_chat", max_requests=30, window_sec=60)
async def respond_via_session(request: Request, session_key: str):
    body = await request.json()
    user_text = str(body.get("text") or body.get("message") or "").strip()
    if not user_text:
        raise HTTPException(status_code=400, detail="Via message cannot be empty")
    result = await reply_in_via_session(
        session_key=session_key,
        user_text=user_text,
        current_surface=str(body.get("surface") or "").strip(),
        event_bus=_event_bus(request),
    )
    if not result:
        raise HTTPException(status_code=404, detail="Via session not found")
    return {"ok": True, **result}


@router.post("/sessions/{session_key}/reward-traces")
@rate_limit("via_chat", max_requests=30, window_sec=60)
async def post_via_reward_trace(request: Request, session_key: str):
    body = await request.json()
    result = await record_via_reward_trace_for_session(
        session_key=session_key,
        event_type=str(body.get("event_type") or "").strip(),
        payload=body.get("payload") if isinstance(body.get("payload"), dict) else {},
        decision_id=str(body.get("decision_id") or "").strip(),
        current_surface=str(body.get("surface") or "").strip(),
        source=str(body.get("source") or "").strip(),
        origin=str(body.get("origin") or "").strip(),
        product_key=str(body.get("product_key") or "").strip(),
        event_value=body.get("event_value"),
        idempotency_key=str(body.get("idempotency_key") or "").strip(),
    )
    if not result:
        raise HTTPException(status_code=404, detail="Via session not found")
    if result.get("error") == "invalid_event_type":
        raise HTTPException(status_code=400, detail="Invalid reward trace event type")
    return {"ok": True, **result}


@router.get("/sessions/{session_key}/stream")
async def stream_via_events(request: Request, session_key: str, after_id: str = "0-0"):
    if not SSE_AVAILABLE:
        return JSONResponse(status_code=503, content={"error": "SSE not available"})
    bus = _event_bus(request)

    async def event_generator():
        async for event in bus.iter_events(session_key, after_id=after_id):
            if await request.is_disconnected():
                break
            yield {
                "event": event.get("event_type", "message"),
                "id": event.get("id", ""),
                "data": json.dumps(
                    {
                        "id": event.get("id", ""),
                        "event_type": event.get("event_type", "message"),
                        "created_at": event.get("created_at", ""),
                        "payload": event.get("payload", {}),
                    },
                    ensure_ascii=False,
                ),
            }

    return EventSourceResponse(event_generator())
