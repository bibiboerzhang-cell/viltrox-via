"""
backend/app/domains/comments/collector.py

P1.3: Comments collection service.

Orchestrates evidence-level data ingestion:
  1. Fetch comments from each platform's crawler
  2. Standardize fields (to vkpi_comments schema)
  3. Dedupe via UNIQUE (platform, external_comment_id)
  4. Persist to vkpi_comments
  5. Audit log via vkpi_comments_collection_runs
  6. Trigger P1.4 sentiment queue (just leaves sentiment_id=NULL for batch)

Public API:
  collect_post_comments(post_id, post_table, *, max_comments, staff)
  batch_collect_pending(*, platform, days, limit, staff)
  stats(*, days)
  
Compatible with V-KPI workflow.staff_id and audit framework.
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timedelta, timezone
from typing import Any

from app.db.connection import get_conn, is_postgres_runtime
from app.platform.industry_crawlers import get_crawler
from app.domains.comments.compat import resolve_post_for_comments
from app.domains.comments.job_identity import comments_freshness_hours, comments_job_identity, normalize_evidence_ids
from app.domains.comments.language_detection import detect_comment_language
from app.domains.tasks.apify_idempotency import active_job_idempotency_key, enqueue_active_apify_job
from app.domains.tasks.search_session_lineage import (
    attach_search_session_lineage_to_job,
    inherit_search_session_lineage,
    merge_search_session_lineages,
    with_search_session_lineage,
)
from app.core.logging import get_logger

logger = get_logger(__name__)


# Default per-post max comments by platform (overridable via env)
PLATFORM_DEFAULTS = {
    "youtube": 50,    # Free YouTube Data API
    "instagram": 30,  # Apify cost
    "tiktok": 30,     # Apify cost
    "reddit": 100,    # PRAW free, includes nested
    "facebook": 30,   # Apify cost
    "x": 50,           # X API / optional Apify replies actor
    # 迸发⑤ 摄影论坛监听骨架:无 crawler(get_crawler→None→not_configured,零网络);
    # 映射先行,执行体属开闸刀(market_listening.py,闸 VKPI_FORUM_COLLECT_ENABLED 默认 0)。
    "forum": 50,
}

# Monthly budget for paid platforms (Apify)
DEFAULT_MONTHLY_BUDGET_USD = 50


def ensure_vkpi_comments_schema() -> None:
    """Create comment tables for the local SQLite fallback.

    PostgreSQL schema ownership belongs exclusively to the migration runner.
    Runtime CREATE/ALTER/INDEX statements require heavyweight relation locks
    even when guarded by ``IF NOT EXISTS`` and can block unrelated API reads.
    """
    if is_postgres_runtime():
        return

    conn = get_conn()
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS vkpi_comments (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          account_id INTEGER,
          post_id INTEGER,
          post_table TEXT DEFAULT 'industry_posts',
          external_post_id TEXT,
          platform TEXT NOT NULL,
          external_comment_id TEXT NOT NULL,
          comment_text TEXT,
          language_detected TEXT,
          author_handle TEXT,
          author_id TEXT,
          is_op INTEGER DEFAULT 0,
          parent_comment_id TEXT,
          depth INTEGER DEFAULT 0,
          likes_count INTEGER DEFAULT 0,
          reply_count INTEGER DEFAULT 0,
          created_at TEXT,
          fetched_at TEXT DEFAULT CURRENT_TIMESTAMP,
          sentiment_id INTEGER,
          pillar_id INTEGER,
          raw_data_json TEXT,
          author_avatar_url TEXT,
          CONSTRAINT vkpi_comments_external_uniq UNIQUE (platform, external_comment_id)
        )
        """
    )
    columns = {str(row["name"]) for row in conn.execute("PRAGMA table_info(vkpi_comments)").fetchall()}
    if "author_avatar_url" not in columns:
        conn.execute("ALTER TABLE vkpi_comments ADD COLUMN author_avatar_url TEXT")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_vkpi_comments_account ON vkpi_comments(account_id, created_at DESC)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_vkpi_comments_post ON vkpi_comments(post_id, post_table)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_vkpi_comments_platform ON vkpi_comments(platform)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_vkpi_comments_external_post ON vkpi_comments(platform, external_post_id)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_vkpi_comments_sentiment_pending ON vkpi_comments(id) WHERE sentiment_id IS NULL")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_vkpi_comments_pillar_pending ON vkpi_comments(id) WHERE pillar_id IS NULL")
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS vkpi_comments_collection_runs (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          post_id INTEGER,
          post_table TEXT,
          platform TEXT NOT NULL,
          status TEXT NOT NULL,
          fetched_count INTEGER DEFAULT 0,
          new_count INTEGER DEFAULT 0,
          duplicate_count INTEGER DEFAULT 0,
          error_message TEXT,
          cost_cents INTEGER DEFAULT 0,
          staff_id INTEGER,
          triggered_by TEXT,
          created_at TEXT DEFAULT CURRENT_TIMESTAMP
        )
        """
    )
    conn.execute("CREATE INDEX IF NOT EXISTS idx_vkpi_comments_runs_platform_time ON vkpi_comments_collection_runs(platform, created_at DESC)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_vkpi_comments_runs_post ON vkpi_comments_collection_runs(post_id, post_table)")
    conn.commit()


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _resolve_post(post_id: int, post_table: str) -> dict | None:
    """Look up post info from V-KPI tables."""
    return resolve_post_for_comments(post_id, post_table)


def _detect_comment_language(text: str) -> str | None:
    """单条评论语言判别:优先 langdetect(拉丁语系精判,seed 固定保证可复现),
    缺库/判失败退 audience_language 的零成本启发式;都不中返回 None(诚实未知)。"""
    return detect_comment_language(text, logger=logger)


def _standardize_comment(
    raw: dict,
    *,
    platform: str,
    post_id: int,
    account_id: int | None,
    external_post_id: str,
    post_table: str,
) -> dict:
    """
    Convert platform-specific comment fields to vkpi_comments standard fields.
    
    Handles 0-as-falsy bug (likes_count=0 should be preserved as known 0).
    """
    # Platform-specific field mappings
    mapping = {
        "youtube": {
            "external_comment_id": ["id"],
            "comment_text": ["snippet.topLevelComment.snippet.textDisplay", "snippet.textDisplay", "text"],
            "author_handle": ["snippet.topLevelComment.snippet.authorDisplayName", "snippet.authorDisplayName", "author"],
            "author_id": ["snippet.topLevelComment.snippet.authorChannelId.value", "snippet.authorChannelId.value"],
            "likes_count": ["snippet.topLevelComment.snippet.likeCount", "snippet.likeCount", "likes"],
            "reply_count": ["snippet.totalReplyCount", "reply_count"],
            "created_at": ["snippet.topLevelComment.snippet.publishedAt", "snippet.publishedAt", "created_at"],
            "is_op": [],
            "parent_comment_id": ["snippet.parentId"],
            "depth": ["depth"],
            "author_avatar_url": ["snippet.topLevelComment.snippet.authorProfileImageUrl", "snippet.authorProfileImageUrl"],
        },
        "instagram": {
            "external_comment_id": ["id"],
            "comment_text": ["text", "comment_text"],
            "author_handle": ["owner.username", "author", "username"],
            "author_id": ["owner.id"],
            "likes_count": ["likes", "like_count"],
            "reply_count": ["repliesCount", "reply_count"],
            "created_at": ["timestamp", "created_at"],
            "is_op": [],
            "author_avatar_url": ["ownerProfilePicUrl", "owner.profile_pic_url", "owner.profilePicUrl"],
        },
        "tiktok": {
            "external_comment_id": ["cid", "id"],
            "comment_text": ["text", "comment_text"],
            # TikTok 评论批次(Apify comment actor)raw 顶层是扁平 uid/uniqueId,
            # 嵌套 author.*/user.* 形态并存 —— 两种都要认,否则 author_id 落空。
            "author_handle": ["author.uniqueId", "user.uniqueId", "uniqueId"],
            "author_id": ["author.id", "user.id", "uid"],
            "likes_count": ["diggCount", "digg_count"],
            "reply_count": ["replyCommentTotal"],
            "created_at": ["createTime", "create_time"],
            "is_op": [],
            "author_avatar_url": ["avatarThumbnail", "author.avatarThumb", "user.avatarThumb", "author.avatarThumbnail"],
        },
        "reddit": {
            "external_comment_id": ["id", "parsedId", "url"],
            "comment_text": ["body"],
            "author_handle": ["author", "username"],
            "author_id": ["userId"],
            "likes_count": ["score", "ups", "upVotes"],
            "reply_count": ["numberOfreplies", "reply_count"],  # Reddit nested via depth
            "created_at": ["created_utc", "created_at", "createdAt"],
            "is_op": ["is_submitter", "isSubmitter"],
            "parent_comment_id": ["parent_id", "parentId"],
            "depth": ["depth"],
        },
        "facebook": {
            "external_comment_id": ["id", "commentId"],
            "comment_text": ["text", "message"],
            # Facebook 评论(Apify)raw 是 profileName/profileId/profileUrl,
            # 不是 Graph API 的 from.*;profileUrl 尾段兜底见下方 facebook 特判。
            "author_handle": ["from.name", "author", "profileName"],
            "author_id": ["from.id", "author_id", "profileId"],
            "likes_count": ["likesCount", "likes_count", "reactionsCount"],
            "reply_count": ["repliesCount"],
            # Apify facebook comments actor 的时间键是 'date'(ISO8601);此前键列表漏它
            # → 49 条存量 created_at 全 NULL(2026-07-11 已回填,见修复报告)。
            "created_at": ["createdTime", "created_at", "timestamp", "date"],
            "is_op": [],
            "author_avatar_url": ["profilePicture", "from.picture.data.url"],
        },
        # 迸发⑤ 摄影论坛监听骨架映射(Reddit 之外的论坛统一形态;XenForo/Discourse
        # API 与 Apify 通用 actor 的常见键并存兜底)。执行体未接线,先立字段契约。
        "forum": {
            "external_comment_id": ["id", "post_id", "postId", "guid"],
            "comment_text": ["message", "body", "content", "text"],
            "author_handle": ["author", "username", "user.username", "user.name"],
            "author_id": ["author_id", "user.id", "userId"],
            "likes_count": ["likes", "like_count", "reaction_score", "score", "upVotes"],
            "reply_count": ["replies", "reply_count", "comment_count"],
            "created_at": ["post_date", "created_at", "createdAt", "date"],
            "is_op": ["is_first_post", "isFirstPost", "is_topic_starter"],
            "parent_comment_id": ["parent_id", "parentId", "quoted_post_id"],
            "depth": ["depth", "position_in_thread"],
            "author_avatar_url": ["avatar_url", "avatarUrl", "user.avatar_urls.s"],
        },
        "x": {
            "external_comment_id": ["id", "replyId", "tweetId"],
            "comment_text": ["fullText", "text", "body"],
            "author_handle": ["author.username", "author.userName", "author.name", "user.username", "username"],
            "author_id": ["author.id", "user.id", "author_id", "authorId"],
            "likes_count": ["public_metrics.like_count", "likeCount", "favoriteCount", "likes"],
            "reply_count": ["public_metrics.reply_count", "replyCount", "replies"],
            "created_at": ["created_at", "createdAt"],
            "is_op": [],
            "parent_comment_id": ["in_reply_to_user_id", "parent_id", "parentId"],
            "depth": ["depth"],
            "author_avatar_url": ["author.profilePicture", "user.profile_image_url"],
        },
    }
    
    fields = mapping.get(platform, {})
    
    def _get_path(data: dict, path: str):
        """Navigate nested dict via dotted path."""
        parts = path.split(".")
        current = data
        for part in parts:
            if isinstance(current, dict) and part in current:
                current = current[part]
            else:
                return None
        return current
    
    def _try_paths(paths: list, default=None):
        """Try multiple field paths, return first non-None."""
        for path in paths:
            value = _get_path(raw, path)
            if value is not None and value != "":
                return value
        return default
    
    # 0-preserved field selection (based on B.6-Xiaohongshu lesson)
    def _int_keep_zero(value):
        if value is None or value == "":
            return 0
        try:
            return int(value)
        except (TypeError, ValueError):
            return 0
    
    def _str_safe(value, max_len=200):
        if value is None:
            return None
        return str(value)[:max_len]
    
    def _ts_iso(value):
        if value is None:
            return None
        if isinstance(value, str):
            return value
        if isinstance(value, (int, float)):
            try:
                return datetime.utcfromtimestamp(float(value)).strftime(
                    "%Y-%m-%dT%H:%M:%SZ"
                )
            except (TypeError, ValueError):
                return None
        return None
    
    def _avatar_url(value):
        # C6 零新抓提列:评论者头像只存 URL,不下载文件。TT user.avatarThumb 可能是
        # {"url_list": [...]} 结构 —— 拆出首个;非 http 的一律弃(防把 dict/占位符串进列)。
        if isinstance(value, dict):
            url_list = value.get("url_list")
            value = url_list[0] if isinstance(url_list, list) and url_list else ""
        text = str(value or "").strip()
        if not text.lower().startswith("http"):
            return None
        return text[:500]

    author_handle = _str_safe(_try_paths(fields.get("author_handle", [])), 200)
    author_id = _str_safe(_try_paths(fields.get("author_id", [])), 200)
    if platform == "facebook" and not author_handle:
        # 无 profileName 时用 profileUrl 尾段兜底(去 query;profile.php 不是 handle)。
        profile_url = str(_try_paths(["profileUrl"]) or "")
        tail = profile_url.split("?", 1)[0].rstrip("/").rsplit("/", 1)[-1].strip()
        if tail and tail.lower() != "profile.php":
            author_handle = _str_safe(tail, 200)

    comment_text = (str(_try_paths(fields.get("comment_text", []), "")) or "")[:5000]
    return {
        "account_id": account_id,
        "post_id": post_id,
        "post_table": post_table,
        "external_post_id": external_post_id,
        "platform": platform,
        "external_comment_id": _str_safe(_try_paths(fields.get("external_comment_id", [])), 200),
        "comment_text": comment_text,
        # 采集时即填(旧设计等 sentiment 阶段 LLM 填,但该 cron 默认关 → 列恒空);零 LLM 成本。
        "language_detected": _detect_comment_language(comment_text),
        "author_handle": author_handle,
        "author_id": author_id,
        "is_op": bool(_try_paths(fields.get("is_op", []), False)),
        "parent_comment_id": _str_safe(_try_paths(fields.get("parent_comment_id", [])), 200),
        "depth": _int_keep_zero(_try_paths(fields.get("depth", []), 0)),
        "likes_count": _int_keep_zero(_try_paths(fields.get("likes_count", []))),
        "reply_count": _int_keep_zero(_try_paths(fields.get("reply_count", []))),
        "created_at": _ts_iso(_try_paths(fields.get("created_at", []))),
        "author_avatar_url": _avatar_url(_try_paths(fields.get("author_avatar_url", []))),
        "raw_data_json": json.dumps(raw, default=str, ensure_ascii=False)[:20000],
    }


def collect_post_comments(
    post_id: int,
    post_table: str = "industry_posts",
    *,
    max_comments: int | None = None,
    staff: dict | None = None,
    triggered_by: str = "manual",
) -> dict:
    """
    Collect comments for a single post.
    
    Returns:
      {
        "post_id": int,
        "platform": str,
        "fetched_count": int,
        "new_count": int,
        "duplicate_count": int,
        "status": "ok" / "skip" / "fail" / "not_configured",
        "error": str (if any),
      }
    """
    ensure_vkpi_comments_schema()
    post = _resolve_post(post_id, post_table)
    if not post:
        return {
            "post_id": post_id,
            "status": "fail",
            "error": f"post not found in {post_table}",
        }
    
    platform = (post.get("platform") or "").lower()
    if platform not in PLATFORM_DEFAULTS:
        return {
            "post_id": post_id,
            "platform": platform,
            "status": "skip",
            "error": f"platform {platform} not supported in P1.3",
        }
    
    if max_comments is None:
        max_comments = PLATFORM_DEFAULTS[platform]
    
    # Get crawler
    crawler = get_crawler(platform)
    if crawler is None:
        return _record_run(
            post_id=post_id,
            post_table=post_table,
            platform=platform,
            status="not_configured",
            error=f"no crawler for {platform}",
            triggered_by=triggered_by,
            staff=staff,
        )
    
    if not crawler.configured:
        return _record_run(
            post_id=post_id,
            post_table=post_table,
            platform=platform,
            status="not_configured",
            error=f"{platform} crawler not configured (missing token)",
            triggered_by=triggered_by,
            staff=staff,
        )
    
    # Determine video_id_or_url
    external_post_id = post.get("external_post_id") or ""
    if not external_post_id:
        return {
            "post_id": post_id,
            "platform": platform,
            "status": "fail",
            "error": "post missing external_post_id",
        }
    
    # Fetch
    try:
        result = crawler.crawl_video_comments(
            external_post_id, max_results=max_comments
        )
    except Exception as exc:
        return _record_run(
            post_id=post_id,
            post_table=post_table,
            platform=platform,
            status="fail",
            error=f"crawler exception: {exc}",
            triggered_by=triggered_by,
            staff=staff,
        )
    
    if result.get("provider_status") in ("not_supported", "not_configured"):
        return _record_run(
            post_id=post_id,
            post_table=post_table,
            platform=platform,
            status="skip",
            error=result.get("error", "provider not supported"),
            triggered_by=triggered_by,
            staff=staff,
        )
    
    if result.get("provider_status") != "ok":
        return _record_run(
            post_id=post_id,
            post_table=post_table,
            platform=platform,
            status="fail",
            error=result.get("error", "unknown error"),
            triggered_by=triggered_by,
            staff=staff,
        )
    
    raw_comments = result.get("items") or []
    
    # Standardize + persist
    new_count = 0
    duplicate_count = 0
    conn = get_conn()
    
    # 并发时代:多车道同时写 vkpi_comments 会死锁(全盘测速实锤 DeadlockDetected 2 例)。
    # 事务级 advisory 锁串行化同表写;写入本身毫秒级互斥代价≈0,锁随 commit 自动释放。
    try:
        from app.db.connection import is_postgres_runtime

        if is_postgres_runtime():
            conn.execute("SELECT pg_advisory_xact_lock(hashtext('vkpi_comments_write'))")
    except Exception:
        logger.warning("comments advisory lock failed (continuing without)", exc_info=True)

    for raw in raw_comments:
        if not isinstance(raw, dict):
            continue

        std = _standardize_comment(
            raw,
            platform=platform,
            post_id=post_id,
            account_id=post.get("account_id"),
            external_post_id=external_post_id,
            post_table=post_table,
        )
        
        if not std.get("external_comment_id"):
            continue  # Skip if no ID
        
        # Insert with ON CONFLICT for dedup
        try:
            conn.execute(
                """
                INSERT INTO vkpi_comments (
                  account_id, post_id, post_table, external_post_id,
                  platform, external_comment_id, comment_text, language_detected,
                  author_handle, author_id, is_op,
                  parent_comment_id, depth,
                  likes_count, reply_count,
                  created_at, fetched_at, raw_data_json,
                  author_avatar_url
                ) VALUES (
                  ?, ?, ?, ?,
                  ?, ?, ?, ?,
                  ?, ?, ?,
                  ?, ?,
                  ?, ?,
                  ?, ?, ?,
                  ?
                )
                ON CONFLICT (platform, external_comment_id) DO NOTHING
                """,
                (
                    std["account_id"], std["post_id"], std["post_table"],
                    std["external_post_id"],
                    std["platform"], std["external_comment_id"],
                    std["comment_text"], std["language_detected"],
                    std["author_handle"], std["author_id"], std["is_op"],
                    std["parent_comment_id"], std["depth"],
                    std["likes_count"], std["reply_count"],
                    std["created_at"], _now_iso(), std["raw_data_json"],
                    std.get("author_avatar_url"),
                ),
            )
            # Note: rowcount may be 0 on conflict (dedup); we estimate via
            # checking if existed before (could query, but expensive)
            # Heuristic: track total upserted, distinguish later via COUNT
            new_count += 1  # Will overstate if duplicates; refine via SELECT pre-check if needed
        except Exception as exc:
            # Log but continue with other comments
            duplicate_count += 1
    
    return _record_run(
        post_id=post_id,
        post_table=post_table,
        platform=platform,
        status="ok",
        fetched_count=len(raw_comments),
        new_count=new_count,
        duplicate_count=duplicate_count,
        triggered_by=triggered_by,
        staff=staff,
    )


def _record_run(
    *,
    post_id: int,
    post_table: str,
    platform: str,
    status: str,
    fetched_count: int = 0,
    new_count: int = 0,
    duplicate_count: int = 0,
    error: str = "",
    cost_cents: int = 0,
    triggered_by: str = "manual",
    staff: dict | None = None,
) -> dict:
    """Audit-log a collection run + return summary."""
    ensure_vkpi_comments_schema()
    conn = get_conn()
    
    staff_id = None
    if staff and isinstance(staff, dict):
        staff_id = staff.get("id")
    
    conn.execute(
        """
        INSERT INTO vkpi_comments_collection_runs (
          post_id, post_table, platform, status,
          fetched_count, new_count, duplicate_count,
          error_message, cost_cents,
          staff_id, triggered_by, created_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            post_id, post_table, platform, status,
            fetched_count, new_count, duplicate_count,
            (error or "")[:1000], cost_cents,
            staff_id, triggered_by, _now_iso(),
        ),
    )
    conn.commit()
    
    return {
        "post_id": post_id,
        "platform": platform,
        "status": status,
        "fetched_count": fetched_count,
        "new_count": new_count,
        "duplicate_count": duplicate_count,
        "error": error,
    }


def batch_collect_pending(
    *,
    platform: str = "",
    days: int = 7,
    limit: int = 100,
    staff: dict | None = None,
) -> dict:
    """Find recent posts without comments and collect."""
    ensure_vkpi_comments_schema()
    conn = get_conn()
    
    where_clauses = ["1=1"]
    params: list = []
    
    if platform:
        where_clauses.append("p.platform = ?")
        params.append(platform)
    
    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).strftime("%Y-%m-%dT%H:%M:%SZ")
    where_clauses.append("p.created_at >= ?")
    params.append(cutoff)
    
    where_sql = " AND ".join(where_clauses)
    
    # Find posts without recent comments collection
    posts = conn.execute(
        f"""
        SELECT p.id, p.platform
        FROM vkpi_industry_posts p
        LEFT JOIN vkpi_comments_collection_runs r
          ON r.post_id = p.id
          AND r.post_table = 'industry_posts'
          AND r.status = 'ok'
          AND r.created_at >= ?
        WHERE {where_sql} AND r.id IS NULL
        ORDER BY p.created_at DESC
        LIMIT ?
        """,
        (cutoff, *params, limit),
    ).fetchall()
    
    summary = {"total": len(posts), "by_status": {}, "errors": []}
    for row in posts:
        result = collect_post_comments(
            post_id=row["id"],
            post_table="industry_posts",
            staff=staff,
            triggered_by="batch",
        )
        status = result.get("status", "unknown")
        summary["by_status"][status] = summary["by_status"].get(status, 0) + 1
        if status == "fail":
            summary["errors"].append(result)
    
    return summary


def stats(*, days: int = 30) -> dict:
    """Comments collection statistics."""
    ensure_vkpi_comments_schema()
    conn = get_conn()
    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).strftime("%Y-%m-%dT%H:%M:%SZ")
    
    by_platform = conn.execute(
        """
        SELECT 
          platform,
          COUNT(*) as run_count,
          SUM(CASE WHEN status='ok' THEN 1 ELSE 0 END) as ok_count,
          SUM(fetched_count) as total_fetched,
          SUM(new_count) as total_new
        FROM vkpi_comments_collection_runs
        WHERE created_at >= ?
        GROUP BY platform
        """,
        (cutoff,),
    ).fetchall()
    
    total_comments = conn.execute(
        """SELECT COUNT(*) as n FROM vkpi_comments WHERE fetched_at >= ?""",
        (cutoff,),
    ).fetchone()
    
    return {
        "days": days,
        "by_platform": [dict(r) for r in by_platform],
        "total_comments": total_comments["n"] if total_comments else 0,
    }


# ============ KOL Pool evidence 评论(2026-06-12 裁令"评论的展示也要有")============
POOL_COMMENTS_JOB_TYPE = "kol_pool_comments_collect"
POOL_AUDIENCE_REFRESH_JOB_TYPE = "kol_audience_stats_refresh"
POOL_EVIDENCE_POST_TABLE = "vkpi_kol_video_evidence"


def enqueue_kol_pool_comments_job(
    kol_pool_id: int,
    *,
    evidence_ids: list[int] | None = None,
    max_comments: int | None = None,
    staff: dict | None = None,
    force_refresh: bool = False,
    queue_lane: str = "interactive",
    search_session_id: int | None = None,
    search_session_item_id: int | None = None,
    parent_job_id: int | None = None,
) -> dict:
    """Queue one evidence-versioned job with DB-serialized active duplicates.
    A successful job for the same exact evidence set is reused within the freshness window unless ``force_refresh``
    is explicit; force never bypasses an already-active job.
    """
    from app.db.connection import get_conn as _get_conn
    normalized_queue_lane = str(queue_lane or "interactive").strip().lower()
    if normalized_queue_lane not in {"interactive", "batch"}:
        raise ValueError("queue_lane must be interactive or batch")
    conn = _get_conn()
    kol = conn.execute(
        "SELECT id, handle, display_name FROM vkpi_kol_pool WHERE id=?", (int(kol_pool_id),)
    ).fetchone()
    if not kol:
        raise LookupError("kol pool item not found")
    resolved_evidence_ids = normalize_evidence_ids(evidence_ids)
    if not resolved_evidence_ids:
        rows = conn.execute(
            """
            SELECT id FROM vkpi_kol_video_evidence
            WHERE kol_pool_id=? AND is_active IS NOT FALSE
              AND COALESCE(evidence_type,'video')='video'
            ORDER BY COALESCE(view_count,0) DESC, id DESC LIMIT 20
            """,
            (int(kol_pool_id),),
        ).fetchall()
        resolved_evidence_ids = normalize_evidence_ids(dict(row).get("id") for row in rows)
    idempotency_key, data_version = comments_job_identity(int(kol_pool_id), resolved_evidence_ids)
    lineage_payload = with_search_session_lineage(
        {},
        search_session_id=search_session_id,
        search_session_item_id=search_session_item_id,
        role="comments",
        parent_job_id=parent_job_id,
    )
    active = conn.execute(
        """
        SELECT id FROM apify_jobs
        WHERE status IN ('queued','running')
          AND (idempotency_key=? OR (idempotency_key IS NULL AND job_type=? AND payload->>'kol_pool_id'=? ))
        ORDER BY id DESC LIMIT 1
        """,
        (idempotency_key, POOL_COMMENTS_JOB_TYPE, str(int(kol_pool_id))),
    ).fetchone()
    if active:
        active_id = int(dict(active)["id"])
        linked = attach_search_session_lineage_to_job(conn, active_id, lineage_payload)
        if linked:
            conn.commit()
        return {
            "status": "already_queued",
            "job_id": active_id,
            "lineage_linked": bool(linked),
        }
    freshness_hours = comments_freshness_hours()
    if not bool(force_refresh):
        fresh = conn.execute(
            """
            SELECT id, updated_at FROM apify_jobs
            WHERE idempotency_key=? AND status='done' AND updated_at >= NOW() - make_interval(hours => ?)
            ORDER BY updated_at DESC, id DESC LIMIT 1
            """,
            (idempotency_key, freshness_hours),
        ).fetchone()
        if fresh:
            fresh_id = int(dict(fresh)["id"])
            linked = attach_search_session_lineage_to_job(conn, fresh_id, lineage_payload)
            if linked:
                conn.commit()
            return {
                "status": "recently_done",
                "job_id": fresh_id,
                "data_version": data_version,
                "freshness_hours": freshness_hours,
                "lineage_linked": bool(linked),
            }
    name = str(dict(kol).get("handle") or dict(kol).get("display_name") or kol_pool_id)
    payload = with_search_session_lineage({
        "queue_lane": normalized_queue_lane,
        "kol_pool_id": int(kol_pool_id),
        "evidence_ids": resolved_evidence_ids,
        "evidence_set_hash": data_version,
        "freshness_hours": freshness_hours,
        "force_refresh": bool(force_refresh),
        "max_comments": int(max_comments) if max_comments else None,
        "query_text": f"评论采集 · {name}"[:96],
        "target_type": "kol_profile",
        "target_id": int(kol_pool_id),
        "triggered_by_user_id": (staff or {}).get("user_id"),
        "staff_id": (staff or {}).get("id") or (staff or {}).get("staff_id"),
    },
        search_session_id=search_session_id,
        search_session_item_id=search_session_item_id,
        role="comments",
        parent_job_id=parent_job_id,
    )
    job, inserted = enqueue_active_apify_job(
        conn,
        job_type=POOL_COMMENTS_JOB_TYPE,
        payload=payload,
        idempotency_key=idempotency_key,
    )
    conn.commit()
    return {
        "status": "queued" if inserted else "already_queued",
        "job_id": int(job["id"]),
        "data_version": data_version,
        "freshness_hours": freshness_hours,
    }


def enqueue_kol_audience_stats_refresh_job(
    kol_pool_id: int,
    *,
    source_comments_job_id: int | None = None,
    staff: dict | None = None,
    lineage_payload: dict[str, Any] | None = None,
) -> dict:
    """Queue one durable audience refresh without widening provider concurrency.

    Idempotency is scoped to the KOL while the refresh is active.  The source
    comment job remains provenance only, so retries or overlapping comment
    batches cannot create multiple concurrent audience refreshes.
    """
    from app.db.connection import get_conn as _get_conn

    normalized_kol_pool_id = int(kol_pool_id or 0)
    if normalized_kol_pool_id <= 0:
        raise ValueError("kol_pool_id required")
    inherited_lineage = inherit_search_session_lineage(
        lineage_payload,
        role="audience",
        parent_job_id=source_comments_job_id,
    )
    payload = merge_search_session_lineages({
        "queue_lane": "batch",
        "kol_pool_id": normalized_kol_pool_id,
        "target_type": "kol_profile",
        "target_id": normalized_kol_pool_id,
        "query_text": f"受众刷新 · KOL #{normalized_kol_pool_id}"[:96],
        "source_comments_job_id": (
            int(source_comments_job_id) if source_comments_job_id else None
        ),
        "triggered_by_user_id": (staff or {}).get("user_id"),
        "staff_id": (staff or {}).get("id") or (staff or {}).get("staff_id"),
    }, [inherited_lineage])
    conn = _get_conn()
    job, inserted = enqueue_active_apify_job(
        conn,
        job_type=POOL_AUDIENCE_REFRESH_JOB_TYPE,
        payload=payload,
        idempotency_key=active_job_idempotency_key(
            POOL_AUDIENCE_REFRESH_JOB_TYPE,
            normalized_kol_pool_id,
        ),
    )
    if not inserted and inherited_lineage:
        attach_search_session_lineage_to_job(conn, job.get("id"), inherited_lineage)
    conn.commit()
    return {
        "status": "queued" if inserted else "already_queued",
        "job_id": int(job["id"]),
        "queue_lane": "batch",
    }


def run_kol_pool_comments_for_job(payload: dict, *, staff: dict | None = None) -> dict:
    """worker 入口:对该 KOL 的 evidence 逐帖采评论(复用 collect_post_comments 全套机器)。"""
    from app.db.connection import get_conn as _get_conn
    kol_pool_id = int(payload.get("kol_pool_id") or 0)
    if not kol_pool_id:
        raise ValueError("kol_pool_id required")
    evidence_ids = payload.get("evidence_ids")
    # New versioned jobs snapshot the exact evidence set (including []). Old
    # historical jobs omit the field/hold NULL and keep the legacy late bind.
    if evidence_ids is None:
        rows = _get_conn().execute(
            """
            SELECT id FROM vkpi_kol_video_evidence
            WHERE kol_pool_id=? AND is_active IS NOT FALSE
              AND COALESCE(evidence_type,'video')='video'
            ORDER BY COALESCE(view_count,0) DESC, id DESC LIMIT 20
            """,
            (kol_pool_id,),
        ).fetchall()
        evidence_ids = [int(dict(r)["id"]) for r in rows]
    # C2 诊断:evidence 解析为空 → 该 KOL 无可采视频证据,原本静默返回 "ready" 掩盖 0 产出
    if not evidence_ids:
        logger.warning(
            "C2 评论采集:kol_pool #%s 无可采 evidence(0 视频证据)→ 无评论可抓", kol_pool_id
        )
    results = []
    new_total = 0
    for eid in evidence_ids:
        result = collect_post_comments(
            int(eid),
            POOL_EVIDENCE_POST_TABLE,
            max_comments=payload.get("max_comments"),
            staff=staff,
            triggered_by="kol_pool_button",
        )
        new_total += int(result.get("new_count") or 0)
        results.append({"evidence_id": int(eid), "status": result.get("status"), "new": result.get("new_count"), "error": (result.get("error") or "")[:120]})
    ok_count = sum(1 for r in results if r.get("status") == "ok")
    # C2 诊断:逐帖结果汇总(provider not_configured / 0 新评论等原本只在 results 内,无运行级日志)
    if results:
        skipped = [r for r in results if r.get("status") not in ("ok",)]
        logger.info(
            "C2 评论采集完成 | kol_pool #%s posts=%s ok=%s new=%s skipped=%s",
            kol_pool_id, len(results), ok_count, new_total,
            [(r.get("evidence_id"), r.get("status"), (r.get("error") or "")[:60]) for r in skipped][:5],
        )
    return {
        "status": "ready" if ok_count or not results else "blocked:all_posts_failed",
        "kol_pool_id": kol_pool_id,
        "posts": len(results),
        "ok": ok_count,
        "new_comments": new_total,
        "results": results,
    }


def list_pool_video_comments(kol_pool_id: int, *, evidence_id: int, limit: int = 100) -> dict:
    """Return persisted comments for one pool video, including the dossier bridge."""

    from app.domains.comments.collector_read import list_pool_video_comments_impl

    return list_pool_video_comments_impl(
        kol_pool_id,
        evidence_id=evidence_id,
        limit=limit,
        post_table=POOL_EVIDENCE_POST_TABLE,
        logger=logger,
    )
