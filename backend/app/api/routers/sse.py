"""
sse.py — event-driven realtime task streaming + proxy config
"""
from __future__ import annotations

import json
import os

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

try:
    from sse_starlette.sse import EventSourceResponse

    SSE_AVAILABLE = True
except ImportError:
    SSE_AVAILABLE = False
    EventSourceResponse = None

YTDLP_PROXY: str = os.getenv("YTDLP_PROXY", "")
router = APIRouter()

_TERMINAL = {"done", "failed", "partial_done", "prefilter_rejected"}
_STATUS_LABEL = {
    "queued": "排队中",
    "processing": "Via 处理中",
    "partial_done": "部分完成",
    "retrying": "重试中",
    "done": "处理完成",
    "failed": "处理失败",
    "prefilter_rejected": "内容不相关，已跳过",
}


@router.get("/api/audit/stream/{task_id}")
async def stream_audit_status(task_id: str, request: Request):
    if not SSE_AVAILABLE:
        return JSONResponse(status_code=503, content={"error": "SSE not available. Install sse-starlette."})

    job_queue = getattr(request.app.state, "job_queue", None)
    if job_queue is None or not hasattr(job_queue, "subscribe_task_events"):
        return JSONResponse(status_code=503, content={"error": "No realtime queue backend available"})

    snapshot = await job_queue.get_status(task_id)
    if not snapshot:
        return JSONResponse(status_code=404, content={"error": "task not found"})

    async def event_generator():
        initial_status = str(snapshot.get("status") or "")
        yield {
            "event": "status_update",
            "data": json.dumps(
                {
                    "task_id": task_id,
                    "status": initial_status,
                    "label": _STATUS_LABEL.get(initial_status, initial_status),
                    "retry_count": int(snapshot.get("retry_count", 0) or 0),
                    "stage": snapshot.get("stage") or "",
                },
                ensure_ascii=False,
            ),
        }
        if initial_status in _TERMINAL:
            yield {
                "event": "final_result",
                "data": json.dumps(snapshot, ensure_ascii=False, default=str),
            }
            return

        async for event in job_queue.subscribe_task_events(task_id):
            if await request.is_disconnected():
                break
            event_type = str(event.get("event_type") or "message")
            if event_type == "heartbeat":
                yield {"event": "heartbeat", "data": json.dumps(event, ensure_ascii=False)}
                continue
            status = str(event.get("status") or "")
            payload = {
                **event,
                "label": _STATUS_LABEL.get(status, status),
            }
            yield {
                "event": "status_update" if event_type not in {"result_ready", "failed"} else event_type,
                "data": json.dumps(payload, ensure_ascii=False, default=str),
            }
            if status in _TERMINAL or event_type in {"result_ready", "failed"}:
                final_snapshot = await job_queue.get_status(task_id)
                yield {
                    "event": "final_result",
                    "data": json.dumps(final_snapshot or payload, ensure_ascii=False, default=str),
                }
                break

    return EventSourceResponse(event_generator())


@router.get("/api/audit/task/{task_id}/status")
async def get_task_status(task_id: str, request: Request):
    job_queue = getattr(request.app.state, "job_queue", None)
    if job_queue is None:
        return JSONResponse(status_code=503, content={"error": "No job backend available"})
    snapshot = await job_queue.get_status(task_id)
    if not snapshot:
        return JSONResponse(status_code=404, content={"error": "task not found"})
    status = str(snapshot.get("status") or "")
    return {
        **snapshot,
        "label": _STATUS_LABEL.get(status, status),
        "done": status in _TERMINAL,
    }
