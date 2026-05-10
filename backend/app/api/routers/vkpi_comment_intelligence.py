"""P2.1 comment intelligence API."""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query

from app.api.dependencies.perms import require_permission
from app.services.vkpi import comment_intelligence
from app.services.vkpi.p1_compat import admin_router_prefix


router = APIRouter(
    prefix=admin_router_prefix("comment-intelligence"),
    tags=["vkpi-comment-intelligence"],
)


@router.post("/process-post/{post_id}")
def api_process_post(
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
    """Run comments -> sentiment -> pillar for one post."""
    return comment_intelligence.process_post(
        post_id,
        post_table=post_table,
        max_comments=max_comments,
        collect_comments=collect_comments,
        analyze_sentiment=analyze_sentiment,
        classify_pillar=classify_pillar,
        force_reprocess=force_reprocess,
        comment_limit=comment_limit,
        staff=staff,
        triggered_by="api",
    )


@router.post("/process-recent")
def api_process_recent(
    platform: str = Query(""),
    days: int = Query(7, ge=1, le=90),
    limit: int = Query(25, ge=1, le=250),
    collect_comments: bool = Query(False),
    analyze_sentiment: bool = Query(True),
    classify_pillar: bool = Query(True),
    force_reprocess: bool = Query(False),
    staff: dict = Depends(require_permission("vkpi.comment_intelligence.run")),
) -> dict[str, Any]:
    """Run the pipeline for recent industry posts."""
    return comment_intelligence.process_recent_posts(
        platform=platform,
        days=days,
        limit=limit,
        collect_comments=collect_comments,
        analyze_sentiment=analyze_sentiment,
        classify_pillar=classify_pillar,
        force_reprocess=force_reprocess,
        staff=staff,
    )


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
def api_retry_run(
    run_id: int,
    staff: dict = Depends(require_permission("vkpi.comment_intelligence.run")),
) -> dict[str, Any]:
    """Retry a previous pipeline run using its stored parameters."""
    return comment_intelligence.retry_run(run_id, staff=staff)
