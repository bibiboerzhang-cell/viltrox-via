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


def _mask_author_handle(value: Any) -> str | None:
    """显示层宪法(全评论读口统一):作者个人字段不出明文,脱敏成首字符+***。"""
    text = str(value or "").strip()
    if not text:
        return None
    return text[0] + "***"


@router.get("/by-post/{post_id}")
def api_comments_by_post(
    post_id: int,
    post_table: str = Query("industry_posts"),
    limit: int = Query(100, ge=1, le=500),
    staff: dict = Depends(require_permission("vkpi.comments.read")),
) -> dict[str, Any]:
    """List comments for a specific post.

    显示层宪法(与 voice_feed 同口径,全评论读口统一):author_handle 只出脱敏形态
    (首字符+***),author_id / raw_data_json 绝不入 SELECT。排序对齐 voice_feed:
    created_at 为空的行(历史 facebook 缺 date 映射等)按 fetched_at 兜底,不再沉底乱序。
    纯增量字段:language_detected + sentiment 关联(s.sentiment/emotion/brand_attitude),
    不破现有返回形状。
    """
    from app.db.connection import get_conn

    conn = get_conn()
    rows = conn.execute(
        """
        SELECT c.id, c.external_comment_id, c.comment_text, c.author_handle,
               c.likes_count, c.reply_count, c.created_at, c.fetched_at,
               c.depth, c.parent_comment_id,
               c.sentiment_id, c.pillar_id, c.language_detected,
               s.sentiment, s.emotion, s.brand_attitude
        FROM vkpi_comments c
        LEFT JOIN vkpi_sentiment_results s ON s.id = c.sentiment_id
        WHERE c.post_id = ? AND c.post_table = ?
        ORDER BY COALESCE(c.created_at, c.fetched_at) DESC
        LIMIT ?
        """,
        (post_id, post_table, limit),
    ).fetchall()

    comments = []
    for r in rows:
        item = dict(r)
        item["author_handle"] = _mask_author_handle(item.get("author_handle"))
        comments.append(item)

    return {
        "post_id": post_id,
        "post_table": post_table,
        "count": len(comments),
        "comments": comments,
    }
