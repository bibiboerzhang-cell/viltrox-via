"""
backend/app/api/routers/vkpi_comments.py

P1.3 API endpoints for comments collection.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query

from app.api.dependencies.perms import require_permission
from app.domains.comments.compat import admin_router_prefix
from app.domains.comments import collector as comments_collector


router = APIRouter(prefix=admin_router_prefix("comments"), tags=["vkpi-comments"])


@router.post("/collect-post/{post_id}")
def api_collect_post_comments(
    post_id: int,
    post_table: str = Query("industry_posts"),
    max_comments: int | None = Query(None),
    staff: dict = Depends(require_permission("vkpi.comments.collect")),
) -> dict[str, Any]:
    """Collect comments for a single post (manual trigger)."""
    return comments_collector.collect_post_comments(
        post_id=post_id,
        post_table=post_table,
        max_comments=max_comments,
        staff=staff,
        triggered_by="manual",
    )


@router.post("/batch-collect")
def api_batch_collect(
    platform: str = Query(""),
    days: int = Query(7, ge=1, le=30),
    limit: int = Query(100, ge=1, le=500),
    staff: dict = Depends(require_permission("vkpi.comments.batch_collect")),
) -> dict[str, Any]:
    """Batch collect comments for recent posts without coverage."""
    return comments_collector.batch_collect_pending(
        platform=platform,
        days=days,
        limit=limit,
        staff=staff,
    )


@router.get("/stats")
def api_stats(
    days: int = Query(30, ge=1, le=180),
    staff: dict = Depends(require_permission("vkpi.comments.read")),
) -> dict[str, Any]:
    """Comments collection statistics."""
    return comments_collector.stats(days=days)


@router.get("/by-post/{post_id}")
def api_comments_by_post(
    post_id: int,
    post_table: str = Query("industry_posts"),
    limit: int = Query(100, ge=1, le=500),
    staff: dict = Depends(require_permission("vkpi.comments.read")),
) -> dict[str, Any]:
    """List comments for a specific post."""
    from app.db.connection import get_conn
    
    conn = get_conn()
    rows = conn.execute(
        """
        SELECT id, external_comment_id, comment_text, author_handle,
               likes_count, reply_count, created_at, depth, parent_comment_id,
               sentiment_id, pillar_id
        FROM vkpi_comments
        WHERE post_id = ? AND post_table = ?
        ORDER BY created_at DESC
        LIMIT ?
        """,
        (post_id, post_table, limit),
    ).fetchall()
    
    return {
        "post_id": post_id,
        "post_table": post_table,
        "count": len(rows),
        "comments": [dict(r) for r in rows],
    }
