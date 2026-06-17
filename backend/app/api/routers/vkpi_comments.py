"""
backend/app/api/routers/vkpi_comments.py

P1.3 API endpoints for comments collection.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query

from app.api.dependencies.perms import require_permission
from app.domains.comments.compat import admin_router_prefix
from app.domains.comments import channel as comments_channel
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


@router.post("/batch-collect-channel")
def api_batch_collect_channel(
    channel_id: int | None = Query(None, description="单个官号;留空=遍历全部 18 官号"),
    posts_per_channel: int = Query(10, ge=1, le=50, description="每个官号扫描近 N 条帖子(小批)"),
    limit_per_post: int = Query(100, ge=1, le=300, description="每帖最多采集评论数"),
    dry_run: bool = Query(True, description="默认 True 只验成本(数 candidate/declared/cached/gap),不真抓不入队"),
    staff: dict = Depends(require_permission("vkpi.comments.batch_collect")),
) -> dict[str, Any]:
    """批量采集官号评论入口(默认 dry_run 验成本)。

    dry_run=True(默认):只返回 candidate_posts / declared / cached / gap 核算,不烧配额。
    dry_run=False:对每个官号入 apify_jobs 一条任务(泳道可见),worker 逐帖复用现成采集机器。
    X 官号缺 token 由 worker 标 not_configured 跳过非失败。
    """
    return comments_channel.batch_collect_channel_comments(
        channel_id=channel_id,
        posts_per_channel=posts_per_channel,
        limit_per_post=limit_per_post,
        dry_run=dry_run,
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
