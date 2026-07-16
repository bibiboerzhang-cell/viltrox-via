"""
account_scanner.py — 多平台矩阵账号扫描
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request

from app.api.dependencies.perms import require_tab
from app.services.security.rate_limiter import rate_limit

router = APIRouter(prefix="/api/admin/intel", tags=["account-scanner"])


@router.post("/scan-account")
@rate_limit("admin_mutation", max_requests=60, window_sec=300)
async def api_scan(request: Request, body: dict, staff=Depends(require_tab("intelligence", "write"))):
    platform = (body.get("platform") or "").lower()
    handle = (body.get("handle") or "").strip()
    max_posts = int(body.get("max_posts", 1000) or 1000)
    queue = getattr(request.app.state, "job_queue", None)

    if queue is None:
        raise HTTPException(503, "durable job queue unavailable")

    task_id = await queue.enqueue(
        "intel_scan_account",
        {
            "platform": platform,
            "handle": handle,
            "max_posts": max_posts,
        },
    )
    return {
        "status": "queued",
        "job_id": task_id,
        "platform": platform,
        "handle": handle,
        "message": "Account scan queued",
        "progressive": True,
        "initial_stage": "queued",
    }


@router.post("/scan-matrix")
@rate_limit("admin_mutation", max_requests=30, window_sec=300)
async def api_matrix(request: Request, body: dict, staff=Depends(require_tab("intelligence", "write"))):
    accounts = body.get("accounts", [])
    max_posts_per_account = int(body.get("max_posts_per_account", 1000) or 1000)
    queue = getattr(request.app.state, "job_queue", None)

    if queue is None:
        raise HTTPException(503, "durable job queue unavailable")

    task_id = await queue.enqueue(
        "intel_scan_matrix",
        {
            "accounts": accounts,
            "max_posts_per_account": max_posts_per_account,
        },
    )
    return {
        "status": "queued",
        "job_id": task_id,
        "total": len(accounts),
        "message": "Matrix scan queued",
        "progressive": True,
        "initial_stage": "queued",
    }
