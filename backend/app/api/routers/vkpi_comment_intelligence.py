"""P2.1 comment intelligence API."""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request

from app.api.dependencies.perms import require_permission
from app.domains.comments.compat import admin_router_prefix
from app.domains.comments import intelligence as comment_intelligence


router = APIRouter(
    prefix=admin_router_prefix("comment-intelligence"),
    tags=["vkpi-comment-intelligence"],
)


@router.get("/overview")
def api_overview(
    days: int = Query(7, ge=1, le=180),
    recent_limit: int = Query(8, ge=1, le=50),
    staff: dict = Depends(require_permission("vkpi.comment_intelligence.read")),
) -> dict[str, Any]:
    """Dashboard-ready status and coverage summary."""
    return comment_intelligence.overview(days=days, recent_limit=recent_limit)


@router.post("/process-post/{post_id}")
async def api_process_post(
    request: Request,
    post_id: int,
    post_table: str = Query("industry_posts"),
    max_comments: int | None = Query(None, ge=1, le=500),
    collect_comments: bool = Query(True),
    analyze_sentiment: bool = Query(True),
    classify_pillar: bool = Query(True),
    force_reprocess: bool = Query(False),
    comment_limit: int = Query(100, ge=1, le=1000),
    staff: dict = Depends(require_permission("vkpi.comment_intelligence.run")),
) -> dict[str, Any]:
    """Queue comments -> sentiment -> pillar for one post."""
    queue = getattr(request.app.state, "job_queue", None)
    if queue is None:
        raise HTTPException(status_code=503, detail="durable job queue unavailable")
    try:
        task_id = await queue.enqueue(
            "comment_intelligence_post",
            {
                "post_id": int(post_id),
                "post_table": post_table,
                "max_comments": max_comments,
                "collect_comments": collect_comments,
                "analyze_sentiment": analyze_sentiment,
                "classify_pillar": classify_pillar,
                "force_reprocess": force_reprocess,
                "comment_limit": comment_limit,
                "staff": dict(staff or {}),
                "triggered_by": "api",
            },
            lock_key=f"comment_intelligence_post:{post_table}:{int(post_id)}",
            timeout_seconds=3600,
        )
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    return {"status": "queued", "job_id": task_id, "progressive": True, "initial_stage": "queued"}


@router.post("/process-recent")
async def api_process_recent(
    request: Request,
    platform: str = Query(""),
    days: int = Query(7, ge=1, le=90),
    limit: int = Query(25, ge=1, le=250),
    collect_comments: bool = Query(False),
    analyze_sentiment: bool = Query(True),
    classify_pillar: bool = Query(True),
    force_reprocess: bool = Query(False),
    staff: dict = Depends(require_permission("vkpi.comment_intelligence.run")),
) -> dict[str, Any]:
    """Queue the pipeline for recent industry posts."""
    queue = getattr(request.app.state, "job_queue", None)
    if queue is None:
        raise HTTPException(status_code=503, detail="durable job queue unavailable")
    try:
        task_id = await queue.enqueue(
            "comment_intelligence_recent",
            {
                "platform": platform,
                "days": days,
                "limit": limit,
                "collect_comments": collect_comments,
                "analyze_sentiment": analyze_sentiment,
                "classify_pillar": classify_pillar,
                "force_reprocess": force_reprocess,
                "staff": dict(staff or {}),
            },
            lock_key=f"comment_intelligence_recent:{platform or 'all'}:{days}",
            timeout_seconds=7200,
        )
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    return {"status": "queued", "job_id": task_id, "progressive": True, "initial_stage": "queued"}


@router.get("/runs")
def api_list_runs(
    post_id: int | None = Query(None),
    status: str = Query(""),
    limit: int = Query(100, ge=1, le=500),
    staff: dict = Depends(require_permission("vkpi.comment_intelligence.read")),
) -> dict[str, Any]:
    """List recent pipeline runs."""
    return comment_intelligence.list_runs(post_id=post_id, status=status, limit=limit)


@router.get("/runs/{run_id}")
def api_get_run(
    run_id: int,
    staff: dict = Depends(require_permission("vkpi.comment_intelligence.read")),
) -> dict[str, Any]:
    """Get one pipeline run with stored params and step results."""
    run = comment_intelligence.get_run(run_id)
    if not run:
        raise HTTPException(status_code=404, detail="pipeline run not found")
    return run


@router.post("/runs/{run_id}/retry")
async def api_retry_run(
    request: Request,
    run_id: int,
    staff: dict = Depends(require_permission("vkpi.comment_intelligence.run")),
) -> dict[str, Any]:
    """Queue a retry using the previous run's stored parameters."""
    original = comment_intelligence.get_run(run_id)
    if not original:
        raise HTTPException(status_code=404, detail="pipeline run not found")
    params = original.get("params") or {}
    queue = getattr(request.app.state, "job_queue", None)
    if queue is None:
        raise HTTPException(status_code=503, detail="durable job queue unavailable")
    try:
        task_id = await queue.enqueue(
            "comment_intelligence_post",
            {
                "post_id": int(original["post_id"]),
                "post_table": str(original.get("post_table") or "industry_posts"),
                "max_comments": params.get("max_comments"),
                "collect_comments": bool(params.get("collect_comments", True)),
                "analyze_sentiment": bool(params.get("analyze_sentiment", True)),
                "classify_pillar": bool(params.get("classify_pillar", True)),
                "force_reprocess": True,
                "comment_limit": int(params.get("comment_limit") or 100),
                "staff": dict(staff or {}),
                "triggered_by": "retry",
                "retry_of_run_id": int(run_id),
            },
            lock_key=f"comment_intelligence_retry:{int(run_id)}",
            timeout_seconds=3600,
        )
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    return {"status": "queued", "job_id": task_id, "progressive": True, "initial_stage": "queued"}
