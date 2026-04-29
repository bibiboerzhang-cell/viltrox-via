"""
api/routers/platform_ingest.py — third-party webhook ingest surface
"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request, Response

from app.services.ingestion.pipeline import build_platform_job_type
from app.services.jobs.backpressure import enforce_queue_backpressure
from app.services.security.rate_limiter import rate_limit
from app.services.ingestion.webhooks import (
    SUPPORTED_WEBHOOK_PLATFORMS,
    build_webhook_ingest_payloads,
    parse_request_body,
    verify_meta_challenge,
    verify_webhook_request,
)


router = APIRouter(prefix="/api/platform-ingest", tags=["platform-ingest"])


@router.get("/meta/webhook")
def verify_meta_webhook(request: Request):
    try:
        challenge = verify_meta_challenge(request.query_params)
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return Response(content=challenge, media_type="text/plain")


@router.post("/{platform}/webhook")
@rate_limit("platform_webhook", max_requests=120, window_sec=60)
async def ingest_platform_webhook(platform: str, request: Request):
    source = (platform or "").strip().lower()
    if source not in SUPPORTED_WEBHOOK_PLATFORMS:
        raise HTTPException(status_code=404, detail="Unsupported platform webhook")

    raw_body = await request.body()
    try:
        auth_mode = verify_webhook_request(
            source,
            request.headers,
            raw_body,
            getattr(getattr(request, "client", None), "host", "") or "",
        )
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc

    queue = getattr(request.app.state, "job_queue", None)
    if queue is None:
        raise HTTPException(status_code=503, detail="job queue unavailable")

    content_type = request.headers.get("content-type", "")
    parsed = parse_request_body(raw_body, content_type)
    payloads = build_webhook_ingest_payloads(
        platform=source,
        headers=request.headers,
        query=request.query_params,
        body=parsed,
    )
    if not payloads:
        return {"status": "ignored", "platform": source, "auth_mode": auth_mode, "event_count": 0}

    job_type = build_platform_job_type(source)
    queue_pressure = await enforce_queue_backpressure(queue, job_type=job_type)
    job_ids: list[str] = []
    for payload in payloads:
        task_id = await queue.enqueue(
            job_type,
            payload,
            submission_id=None,
        )
        job_ids.append(task_id)
    return {
        "status": "queued",
        "job_id": job_ids[0],
        "job_ids": job_ids,
        "event_count": len(job_ids),
        "platform": source,
        "auth_mode": auth_mode,
        "queue": queue_pressure,
    }
