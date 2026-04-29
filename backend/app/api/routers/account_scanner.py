"""
account_scanner.py — 多平台矩阵账号扫描
"""
from __future__ import annotations

from fastapi import APIRouter, Request

from app.api.dependencies.admin import get_admin
from app.services.intelligence.account_scan_service import scan_account, scan_matrix
from app.services.security.rate_limiter import rate_limit

router = APIRouter(prefix="/api/admin/intel", tags=["account-scanner"])


@router.post("/scan-account")
@rate_limit("admin_mutation", max_requests=60, window_sec=300)
async def api_scan(request: Request, body: dict):
    get_admin(request)
    platform = (body.get("platform") or "").lower()
    handle = (body.get("handle") or "").strip()
    max_posts = int(body.get("max_posts", 1000) or 1000)
    queue = getattr(request.app.state, "job_queue", None)

    if body.get("sync") or queue is None:
        result = await scan_account(platform, handle, max_posts)
        result["status"] = "done"
        return result

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
    }


@router.post("/scan-matrix")
@rate_limit("admin_mutation", max_requests=30, window_sec=300)
async def api_matrix(request: Request, body: dict):
    get_admin(request)
    accounts = body.get("accounts", [])
    max_posts_per_account = int(body.get("max_posts_per_account", 1000) or 1000)
    queue = getattr(request.app.state, "job_queue", None)

    if body.get("sync") or queue is None:
        result = await scan_matrix(accounts, max_posts_per_account)
        result["status"] = "done"
        return result

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
    }
