"""Comment access helpers for employee channel content."""
from __future__ import annotations

from pathlib import Path
from typing import Any

from app.db.connection import get_conn
from app.domains import channels
from app.domains.channels.identity import canonical_post_identity


COMMENT_COLLECT_PLATFORMS = {"youtube", "instagram", "tiktok", "facebook", "reddit", "x"}
MAX_CHANNEL_COMMENT_CAP = 300


def _reddit_comment_payload(item: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": 0,
        "external_comment_id": channels._text(item.get("id"), item.get("parsedId"), item.get("url")),
        "external_post_id": channels._text(item.get("postId")),
        "text": channels._text(item.get("body"), item.get("html")),
        "author": channels._text(item.get("username"), "匿名"),
        "author_id": channels._text(item.get("userId")),
        "likes": channels._int(item.get("upVotes"), channels._int(item.get("score"))),
        "reply_count": channels._int(item.get("numberOfreplies"), channels._int(item.get("reply_count"))),
        "depth": 1 if channels._text(item.get("parentId")).startswith("t1_") else 0,
        "parent_comment_id": channels._text(item.get("parentId")),
        "is_op": channels._bool(item.get("isSubmitter"), channels._bool(item.get("is_submitter"))),
        "created_at": channels._text(item.get("createdAt"), item.get("created_utc")),
        "fetched_at": channels._text(item.get("scrapedAt")),
        "sentiment": "",
    }


def _package_reddit_comments(package_dir: str, external_ids: set[str]) -> list[dict[str, Any]]:
    normalized = {channels._text(value) for value in external_ids if channels._text(value)}
    normalized |= {channels._reddit_external_id(value) for value in list(normalized)}
    comments: list[dict[str, Any]] = []
    seen: set[str] = set()
    package_dirs = [Path(package_dir)] if package_dir else []
    if package_dirs and package_dirs[0].parent.exists():
        siblings = sorted(
            [item for item in package_dirs[0].parent.iterdir() if item.is_dir() and item not in package_dirs],
            key=lambda item: item.name,
            reverse=True,
        )
        package_dirs.extend(siblings[:12])
    for directory in package_dirs:
        raw = channels._raw_package(str(directory))
        if not raw:
            continue
        for item in channels._raw_package_items(raw):
            if str(item.get("dataType") or "").lower() != "comment":
                continue
            keys = {
                channels._text(item.get("postId")),
                channels._reddit_external_id(channels._text(item.get("postId"))),
                channels._reddit_external_id(channels._text(item.get("url"))),
            }
            comment_id = channels._text(item.get("id"), item.get("parsedId"), item.get("url"))
            if comment_id in seen:
                continue
            if normalized & {key for key in keys if key}:
                seen.add(comment_id)
                comments.append(_reddit_comment_payload(item))
    comments.sort(key=lambda item: item.get("created_at") or "", reverse=True)
    return comments


def _instagram_inline_comment(comment: dict[str, Any]) -> dict[str, Any]:
    owner = comment.get("owner") if isinstance(comment.get("owner"), dict) else {}
    return {
        "id": channels._text(comment.get("id")),
        "text": channels._text(comment.get("text"), comment.get("comment_text")),
        "owner": {
            "username": channels._text(owner.get("username"), comment.get("ownerUsername"), comment.get("username"), comment.get("author")),
            "id": channels._text(owner.get("id"), comment.get("ownerId"), comment.get("author_id")),
        },
        "likes": channels._int(comment.get("likes"), channels._int(comment.get("likesCount"), channels._int(comment.get("like_count")))),
        "repliesCount": channels._int(comment.get("repliesCount"), channels._int(comment.get("reply_count"))),
        "timestamp": channels._text(comment.get("timestamp"), comment.get("created_at"), comment.get("createdAt")),
        "raw_inline": comment,
    }


def _reddit_inline_comment(comment: dict[str, Any]) -> dict[str, Any]:
    parent_id = channels._text(comment.get("parentId"), comment.get("parent_id"))
    return {
        "id": channels._text(comment.get("id"), comment.get("parsedId"), comment.get("url")),
        "body": channels._text(comment.get("body"), comment.get("html")),
        "author": channels._text(comment.get("username"), comment.get("author"), "匿名"),
        "score": channels._int(comment.get("upVotes"), channels._int(comment.get("score"))),
        "created_at": channels._text(comment.get("createdAt"), comment.get("created_utc")),
        "parent_id": parent_id,
        "depth": 1 if parent_id.startswith("t1_") else 0,
        "is_submitter": channels._bool(comment.get("isSubmitter"), channels._bool(comment.get("is_submitter"))),
        "raw_inline": comment,
    }


def _inline_comment_groups_from_row(row: dict[str, Any]) -> dict[str, list[dict[str, Any]]]:
    """Extract comment bodies already present in the latest official-channel snapshot."""

    platform = str(row.get("platform") or "").lower()
    sample = channels._raw_sample(row)
    groups: dict[str, list[dict[str, Any]]] = {}

    def add(external_post_id: str, raw_comment: dict[str, Any]) -> None:
        key = channels._text(external_post_id)
        if not key or not raw_comment:
            return
        groups.setdefault(key, []).append(raw_comment)

    if platform == "instagram":
        posts = channels._items(sample.get("posts"))
        if not posts:
            for item in channels._items(sample.get("items")):
                posts.extend(channels._items(item.get("latestPosts")))
        for post in posts:
            external_post_id = channels._text(post.get("url"), post.get("shortCode"), post.get("id"))
            for comment in channels._items(post.get("latestComments")):
                add(external_post_id, _instagram_inline_comment(comment))
    elif platform == "reddit":
        for item in channels._items(sample.get("items")):
            if str(item.get("dataType") or "").lower() != "comment":
                continue
            external_post_id = channels._reddit_external_id(channels._text(item.get("postId"), item.get("url")))
            add(external_post_id, _reddit_inline_comment(item))
    return groups


def cache_inline_channel_comments(
    channel_id: int,
    *,
    staff: dict[str, Any] | None = None,
    execute: bool = False,
    limit_per_post: int = 300,
) -> dict[str, Any]:
    """Persist comment bodies already embedded in the latest channel snapshot."""

    row = channels._latest_channel_row(channel_id, staff=staff)
    platform = str(row.get("platform") or "").lower()
    groups = _inline_comment_groups_from_row(row)
    limit = max(1, min(300, int(limit_per_post or 300)))
    saved_posts = 0
    new_count = 0
    comments_seen = 0
    sample: list[dict[str, Any]] = []
    for external_post_id, comments in groups.items():
        clipped = comments[:limit]
        comments_seen += len(clipped)
        if len(sample) < 8:
            sample.append({"external_post_id": external_post_id, "comments": len(clipped)})
        if not execute:
            continue
        saved_posts += 1
        new_count += _save_channel_comments(
            channel_id=int(channel_id),
            platform=platform,
            external_post_id=external_post_id,
            comments=clipped,
        )
    return {
        "channel_id": int(channel_id),
        "platform": platform,
        "handle": channels._text(row.get("account_handle")),
        "execute": bool(execute),
        "posts_with_inline_comments": len(groups),
        "comments_seen": comments_seen,
        "saved_posts": saved_posts,
        "new_count": new_count,
        "sample": sample,
    }


def _comment_payload(row: Any) -> dict[str, Any]:
    data = dict(row)
    raw = channels._parse_json(data.get("raw_data_json"))
    return {
        "id": channels._int(data.get("id")),
        "external_comment_id": channels._text(data.get("external_comment_id")),
        "external_post_id": channels._text(data.get("external_post_id")),
        "text": channels._text(data.get("comment_text")),
        "author": channels._text(data.get("author_handle"), "匿名"),
        "author_id": channels._text(data.get("author_id")),
        "likes": channels._int(data.get("likes_count")),
        "reply_count": channels._int(data.get("reply_count")),
        "depth": channels._int(data.get("depth")),
        "parent_comment_id": channels._text(data.get("parent_comment_id")),
        "is_op": channels._bool(data.get("is_op")),
        "created_at": channels._text(data.get("created_at")),
        "fetched_at": channels._text(data.get("fetched_at")),
        "sentiment": channels._text(raw.get("sentiment")),
    }


def _comment_contract(
    *,
    declared: int,
    cached: int,
    cap: int,
    collect_supported: bool = True,
    missing_post_id: bool = False,
) -> dict[str, Any]:
    """Make declared platform counts distinct from cached comment bodies."""

    declared_i = max(0, channels._int(declared))
    cached_i = max(0, channels._int(cached))
    cap_i = max(1, min(MAX_CHANNEL_COMMENT_CAP, channels._int(cap, 50)))
    if missing_post_id:
        status = "missing_post_id"
    elif not collect_supported:
        status = "not_supported"
    elif declared_i <= 0 and cached_i <= 0:
        status = "none_declared"
    elif declared_i <= 0 and cached_i > 0:
        status = "cached_without_declared"
    elif cached_i >= declared_i:
        status = "complete"
    elif cached_i >= cap_i:
        status = "capped"
    elif cached_i > 0:
        status = "partial"
    else:
        status = "not_cached"
    return {
        "declared": declared_i,
        "cached": cached_i,
        "cap": cap_i,
        "status": status,
    }


def _comment_external_post_id(platform: str, post_id: str, url: str, post: dict[str, Any] | None) -> str:
    post = post or {}
    identity = canonical_post_identity(platform, {**post, "id": channels._text(post.get("id"), post_id), "url": channels._text(post.get("url"), url)})
    provider_id = channels._text(identity.get("provider_post_id"))
    if provider_id:
        return provider_id
    canonical_url = channels._text(identity.get("canonical_url"))
    if canonical_url:
        return canonical_url
    if platform == "reddit":
        return channels._reddit_external_id(channels._text(post.get("url"), url, post.get("source_id"), post_id))
    if platform in {"facebook", "youtube", "tiktok", "x"}:
        return channels._text(post.get("source_id"), post.get("id"), post_id, post.get("url"), url)
    if platform == "instagram":
        return channels._text(post.get("url"), url, post.get("source_id"), post.get("short_code"), post.get("id"), post_id)
    return channels._text(post.get("source_id"), post.get("url"), url, post_id)


def _comment_crawler(platform: str):
    if platform == "reddit":
        from app.platform.industry_crawlers.reddit_crawler import RedditCrawler

        return RedditCrawler(), "Reddit 评论抓取需要 PRAW 配置。"
    if platform == "instagram":
        from app.platform.industry_crawlers.instagram_crawler import InstagramCrawler

        return InstagramCrawler(), "Instagram 评论抓取需要 APIFY_TOKEN 配置。"
    if platform == "facebook":
        from app.platform.industry_crawlers.facebook_crawler import FacebookCrawler

        return FacebookCrawler(), "Facebook 评论抓取需要 APIFY_TOKEN 配置。"
    if platform == "youtube":
        from app.platform.industry_crawlers.youtube_crawler import YouTubeCrawler

        return YouTubeCrawler(), "YouTube 评论抓取需要 YOUTUBE_API_KEY 配置。"
    if platform == "tiktok":
        from app.platform.industry_crawlers.tiktok_crawler import TikTokCrawler

        return TikTokCrawler(run_timeout_seconds=300), "TikTok 评论抓取需要 APIFY_TOKEN 配置。"
    if platform == "x":
        from app.platform.industry_crawlers.x_crawler import XCrawler

        return XCrawler(run_timeout_seconds=300), "X 评论抓取需要 X_BEARER_TOKEN，或配置 APIFY_X_COMMENTS_ACTOR_ID。"
    return None, "当前平台评论采集未接入频道层。"


def channel_post_comments(
    channel_id: int,
    *,
    post_id: str,
    url: str = "",
    limit: int = 50,
    staff: dict[str, Any] | None = None,
) -> dict[str, Any]:
    row = channels._latest_channel_row(channel_id, staff=staff)
    platform = str(row.get("platform") or "").lower()
    safe_limit = max(1, min(MAX_CHANNEL_COMMENT_CAP, int(limit or 50)))
    _, _, package_dir = channels._all_posts_for_channel(row)
    post = channels._match_post(row, post_id, url) or {}
    declared_count = channels._int(post.get("comments"))
    collect_supported = platform in COMMENT_COLLECT_PLATFORMS
    external_ids = {
        channels._text(post_id),
        channels._text(url),
        channels._text(post.get("id")),
        channels._text(post.get("source_id")),
        channels._text(post.get("url")),
    }
    if platform == "reddit":
        external_ids |= {channels._reddit_external_id(value) for value in list(external_ids)}
    external_ids = {value for value in external_ids if value}
    if not external_ids:
        contract = _comment_contract(
            declared=0,
            cached=0,
            cap=safe_limit,
            collect_supported=collect_supported,
            missing_post_id=True,
        )
        return {
            "channel_id": int(channel_id),
            "post_id": post_id,
            "platform": platform,
            "comments": [],
            "comment_count": 0,
            "declared_count": contract["declared"],
            "cached_count": contract["cached"],
            "comment_cap": contract["cap"],
            "coverage_status": contract["status"],
            "comment_contract": contract,
            "status": "missing_post_id",
        }

    from app.domains.comments import collector as comments_collector

    comments_collector.ensure_vkpi_comments_schema()
    placeholders = ",".join("?" for _ in external_ids)
    rows = get_conn().execute(
        f"""
        SELECT *
        FROM vkpi_comments
        WHERE platform=? AND external_post_id IN ({placeholders})
        ORDER BY COALESCE(created_at, fetched_at) DESC, id DESC
        LIMIT ?
        """,
        (platform, *sorted(external_ids), safe_limit),
    ).fetchall()
    comments = [_comment_payload(item) for item in rows]
    seen = {channels._text(item.get("external_comment_id")) for item in comments}
    if platform == "reddit":
        for item in _package_reddit_comments(package_dir, external_ids):
            key = channels._text(item.get("external_comment_id"))
            if key and key not in seen:
                seen.add(key)
                comments.append(item)
        comments.sort(key=lambda item: item.get("created_at") or item.get("fetched_at") or "", reverse=True)
        comments = comments[:safe_limit]
    contract = _comment_contract(
        declared=declared_count,
        cached=len(comments),
        cap=safe_limit,
        collect_supported=collect_supported,
    )
    return {
        "channel_id": int(channel_id),
        "post_id": post_id,
        "platform": platform,
        "post": post,
        "comments": comments,
        "comment_count": len(comments),
        "declared_count": contract["declared"],
        "cached_count": contract["cached"],
        "comment_cap": contract["cap"],
        "coverage_status": contract["status"],
        "comment_contract": contract,
        "status": "ok" if comments else "not_collected",
        "collect_supported": collect_supported,
        "source": "db_or_package" if comments else "db",
    }


def _save_channel_comments(
    *,
    channel_id: int,
    platform: str,
    external_post_id: str,
    comments: list[dict[str, Any]],
) -> int:
    from app.domains.comments import collector as comments_collector

    comments_collector.ensure_vkpi_comments_schema()
    conn = get_conn()
    before = conn.execute(
        "SELECT COUNT(*) AS n FROM vkpi_comments WHERE platform=? AND external_post_id=?",
        (platform, external_post_id),
    ).fetchone()
    for raw in comments:
        if not isinstance(raw, dict):
            continue
        # 2026-07-11 owned 回链:post_id 写 channel_id(此前写死 0 → voice_feed 的
        # owned JOIN(ec.id = c.post_id)永不命中,identity_ref/post_url 全空)。
        # 该表评论挂频道行,post_id 语义 = vkpi_employee_channels.id。
        standardized = comments_collector._standardize_comment(
            raw,
            platform=platform,
            post_id=int(channel_id),
            account_id=int(channel_id),
            external_post_id=external_post_id,
            post_table="vkpi_employee_channels",
        )
        comment_id = channels._text(
            standardized.get("external_comment_id"),
            raw.get("id"),
            raw.get("commentId"),
            raw.get("name"),
            raw.get("cid"),
        )
        if not comment_id:
            continue
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
              ?, ?, ?,
              ?, ?, ?, ?, ?,
              ?, ?, ?, ?, ?,
              ?, ?, ?, ?, ?,
              ?
            )
            ON CONFLICT (platform, external_comment_id) DO UPDATE SET
              account_id = EXCLUDED.account_id,
              post_id = EXCLUDED.post_id,
              post_table = EXCLUDED.post_table,
              external_post_id = EXCLUDED.external_post_id,
              fetched_at = EXCLUDED.fetched_at,
              raw_data_json = EXCLUDED.raw_data_json,
              author_avatar_url = COALESCE(EXCLUDED.author_avatar_url, vkpi_comments.author_avatar_url)
            """,
            (
                int(channel_id),
                int(channel_id),
                "vkpi_employee_channels",
                external_post_id,
                platform,
                comment_id,
                channels._text(standardized.get("comment_text")),
                # 空值口径统一 NULL(此前 _text 把 None 压成 ''——语言回填/统计都要多认一种空)。
                channels._text(standardized.get("language_detected"), raw.get("language")) or None,
                channels._text(standardized.get("author_handle")),
                channels._text(standardized.get("author_id")),
                channels._bool(standardized.get("is_op")),
                channels._text(standardized.get("parent_comment_id")),
                channels._int(standardized.get("depth")),
                channels._int(standardized.get("likes_count")),
                channels._int(standardized.get("reply_count")),
                channels._text(standardized.get("created_at")) or None,
                channels._utcnow(),
                channels._json(raw),
                standardized.get("author_avatar_url"),
            ),
        )
    conn.commit()
    after = conn.execute(
        "SELECT COUNT(*) AS n FROM vkpi_comments WHERE platform=? AND external_post_id=?",
        (platform, external_post_id),
    ).fetchone()
    return max(0, channels._int(after["n"] if after else 0) - channels._int(before["n"] if before else 0))


BATCH_OFFICIAL_COMMENTS_JOB_TYPE = "official_channel_comments_collect"
# 默认每个官号扫描多少条近期帖子(小批,避免擅自烧配额)
DEFAULT_BATCH_POSTS_PER_CHANNEL = 10
MAX_BATCH_POSTS_PER_CHANNEL = 50


def _official_comment_channels(
    *,
    channel_id: int | None = None,
    staff: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """枚举要批量采评论的官号(单个 channel_id,或全部 18 官号)。

    复用 channels._latest_official_channel_rows——它已经按 is_official 过滤,
    18 官号是公司级 baseline(staff scope 不限制可见性)。
    """

    rows = channels._latest_official_channel_rows(staff=staff)
    if channel_id is not None:
        target = int(channel_id)
        rows = [row for row in rows if channels._int(row.get("id")) == target]
    return rows


def _channel_candidate_posts(
    row: dict[str, Any],
    *,
    posts_per_channel: int,
) -> list[dict[str, Any]]:
    """对单个官号:近 N 条帖子的 candidate 列表(纯读,不抓)。

    declared = 平台声明的评论数;cached = vkpi_comments 已落库数;
    gap = max(0, declared - cached)。只用于 dry_run 成本核算。
    """

    from app.domains.comments import collector as comments_collector

    comments_collector.ensure_vkpi_comments_schema()
    channel_id = channels._int(row.get("id"))
    platform = str(row.get("platform") or "").lower()
    posts, _source, _package_dir = channels._all_posts_for_channel(row)
    limit = max(1, min(MAX_BATCH_POSTS_PER_CHANNEL, int(posts_per_channel or DEFAULT_BATCH_POSTS_PER_CHANNEL)))
    posts = posts[:limit]
    conn = get_conn()
    candidates: list[dict[str, Any]] = []
    for post in posts:
        external_post_id = _comment_external_post_id(platform, channels._text(post.get("id")), channels._text(post.get("url")), post)
        if not external_post_id:
            candidates.append(
                {
                    "post_id": channels._text(post.get("id"), post.get("url")),
                    "external_post_id": "",
                    "url": channels._text(post.get("url")),
                    "declared": channels._int(post.get("comments")),
                    "cached": 0,
                    "gap": 0,
                    "status": "missing_post_id",
                }
            )
            continue
        external_ids = {external_post_id, channels._text(post.get("id")), channels._text(post.get("url"))}
        if platform == "reddit":
            external_ids |= {channels._reddit_external_id(value) for value in list(external_ids)}
        external_ids = {value for value in external_ids if value}
        cached = 0
        if external_ids:
            placeholders = ",".join("?" for _ in external_ids)
            cached_row = conn.execute(
                f"SELECT COUNT(*) AS n FROM vkpi_comments WHERE platform=? AND external_post_id IN ({placeholders})",
                (platform, *sorted(external_ids)),
            ).fetchone()
            cached = channels._int(cached_row["n"] if cached_row else 0)
        declared = channels._int(post.get("comments"))
        gap = max(0, declared - cached)
        candidates.append(
            {
                "post_id": channels._text(post.get("id"), post.get("url")),
                "external_post_id": external_post_id,
                "url": channels._text(post.get("url")),
                "declared": declared,
                "cached": cached,
                "gap": gap,
                "status": "cached" if cached > 0 and gap == 0 else ("gap" if gap > 0 else "none_declared"),
            }
        )
    return candidates


def batch_collect_channel_comments(
    *,
    channel_id: int | None = None,
    posts_per_channel: int = DEFAULT_BATCH_POSTS_PER_CHANNEL,
    limit_per_post: int = 100,
    dry_run: bool = True,
    staff: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """批量采集官号评论入口。

    - channel_id 为空 → 遍历全部官号;否则只处理该官号。
    - posts_per_channel:每个官号扫描近 N 条帖子(小批,默认 10,封顶 50)。
    - dry_run=True(默认):只数 candidate_posts/declared/cached/gap,**不真抓、不入队**,
      用于验成本,绝不擅自烧配额。
    - dry_run=False:对每个官号入 apify_jobs 一条 job(泳道可见),worker 逐帖复用
      collect_channel_post_comments。X 官号缺 token 由 worker 标 not_configured 跳过非失败。
    """

    rows = _official_comment_channels(channel_id=channel_id, staff=staff)
    safe_posts = max(1, min(MAX_BATCH_POSTS_PER_CHANNEL, int(posts_per_channel or DEFAULT_BATCH_POSTS_PER_CHANNEL)))
    safe_limit_per_post = max(1, min(MAX_CHANNEL_COMMENT_CAP, int(limit_per_post or 100)))

    channel_summaries: list[dict[str, Any]] = []
    total_candidate_posts = 0
    total_gap = 0
    total_declared = 0
    total_cached = 0
    enqueued: list[dict[str, Any]] = []

    for row in rows:
        cid = channels._int(row.get("id"))
        platform = str(row.get("platform") or "").lower()
        handle = channels._text(row.get("account_handle"), row.get("account_display_name"))
        collect_supported = platform in COMMENT_COLLECT_PLATFORMS
        candidates = _channel_candidate_posts(row, posts_per_channel=safe_posts)
        candidate_posts = len(candidates)
        gap = sum(channels._int(item.get("gap")) for item in candidates)
        declared = sum(channels._int(item.get("declared")) for item in candidates)
        cached = sum(channels._int(item.get("cached")) for item in candidates)
        total_candidate_posts += candidate_posts
        total_gap += gap
        total_declared += declared
        total_cached += cached

        summary = {
            "channel_id": cid,
            "platform": platform,
            "handle": handle,
            "collect_supported": collect_supported,
            "candidate_posts": candidate_posts,
            "declared": declared,
            "cached": cached,
            "gap": gap,
            "candidates": candidates[:20],
        }

        if not dry_run:
            if not collect_supported:
                summary["enqueue_status"] = "not_supported"
            else:
                job = _enqueue_official_channel_comments_job(
                    channel_id=cid,
                    posts_per_channel=safe_posts,
                    limit_per_post=safe_limit_per_post,
                    staff=staff,
                )
                summary["enqueue_status"] = job.get("status")
                summary["job_id"] = job.get("job_id")
                enqueued.append({"channel_id": cid, **job})
        channel_summaries.append(summary)

    return {
        "dry_run": bool(dry_run),
        "scope": "single" if channel_id is not None else "all_official",
        "channels": len(rows),
        "posts_per_channel": safe_posts,
        "limit_per_post": safe_limit_per_post,
        "total_candidate_posts": total_candidate_posts,
        "total_declared": total_declared,
        "total_cached": total_cached,
        "total_gap": total_gap,
        "enqueued": enqueued,
        "results": channel_summaries,
    }


def _enqueue_official_channel_comments_job(
    *,
    channel_id: int,
    posts_per_channel: int,
    limit_per_post: int,
    staff: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """对单个官号入 apify_jobs(泳道可见;幂等:同 channel 已有活跃任务则复用)。"""

    import json as _json

    conn = get_conn()
    active = conn.execute(
        """
        SELECT id FROM apify_jobs
        WHERE job_type=? AND status IN ('queued','running')
          AND payload->>'channel_id'=? LIMIT 1
        """,
        (BATCH_OFFICIAL_COMMENTS_JOB_TYPE, str(int(channel_id))),
    ).fetchone()
    if active:
        return {"status": "already_queued", "job_id": channels._int(dict(active)["id"])}
    payload = {
        "channel_id": int(channel_id),
        "posts_per_channel": int(posts_per_channel),
        "limit_per_post": int(limit_per_post),
        "query_text": f"官号评论批量采集 · channel {int(channel_id)}"[:96],
        "target_type": "official_channel",
        "target_id": int(channel_id),
        "triggered_by_user_id": (staff or {}).get("user_id"),
        "staff_id": (staff or {}).get("id") or (staff or {}).get("staff_id"),
    }
    job = conn.execute(
        """
        INSERT INTO apify_jobs (job_type, payload, status, created_at, updated_at)
        VALUES (?, ?::jsonb, 'queued', NOW(), NOW())
        RETURNING id
        """,
        (BATCH_OFFICIAL_COMMENTS_JOB_TYPE, _json.dumps(payload, ensure_ascii=False)),
    ).fetchone()
    conn.commit()
    return {"status": "queued", "job_id": channels._int(dict(job)["id"]) if job else None}


def run_official_channel_comments_for_job(payload: dict[str, Any], *, staff: dict[str, Any] | None = None) -> dict[str, Any]:
    """worker 入口:对该官号近 N 条帖子逐条采评论(复用 collect_channel_post_comments)。

    X 官号缺 token → collect_channel_post_comments 返回 status=not_configured,
    本入口标 skipped 不计 fail。
    """

    channel_id = channels._int(payload.get("channel_id"))
    if not channel_id:
        raise ValueError("channel_id required")
    posts_per_channel = max(1, min(MAX_BATCH_POSTS_PER_CHANNEL, channels._int(payload.get("posts_per_channel"), DEFAULT_BATCH_POSTS_PER_CHANNEL)))
    limit_per_post = max(1, min(MAX_CHANNEL_COMMENT_CAP, channels._int(payload.get("limit_per_post"), 100)))

    row = channels._latest_channel_row(channel_id, staff=staff)
    platform = str(row.get("platform") or "").lower()
    posts, _source, _package_dir = channels._all_posts_for_channel(row)
    posts = posts[:posts_per_channel]

    results: list[dict[str, Any]] = []
    new_total = 0
    ok_count = 0
    skipped_count = 0
    for post in posts:
        post_id = channels._text(post.get("id"), post.get("url"))
        url = channels._text(post.get("url"))
        result = collect_channel_post_comments(
            channel_id,
            post_id=post_id,
            url=url,
            limit=limit_per_post,
            staff=staff,
        )
        status = str(result.get("status") or "")
        new_count = channels._int(result.get("new_count"))
        new_total += new_count
        if status == "not_configured":
            skipped_count += 1
        elif status in ("ok", "complete", "capped", "partial", "cached_without_declared"):
            ok_count += 1
        results.append(
            {
                "post_id": post_id,
                "status": status,
                "new": new_count,
                "fetched": channels._int(result.get("fetched_count")),
                # 逐帖错误文本随行带出(worker 据此判「全败是否可重试类」;截断防payload膨胀)。
                "error": channels._text(result.get("message"), result.get("error"))[:120],
            }
        )
    # 诚实终态(对齐 run_kol_pool_comments_for_job):有帖可采却一条都没成、也没有
    # 因缺 token 被合法跳过 → 不再报 ready 假成功;worker 侧按逐帖错误决定 requeue/blocked。
    all_failed = bool(results) and not ok_count and not skipped_count
    return {
        "status": "blocked:all_posts_failed" if all_failed else "ready",
        "channel_id": channel_id,
        "platform": platform,
        "posts": len(results),
        "ok": ok_count,
        "skipped": skipped_count,
        "new_comments": new_total,
        "results": results,
    }


def collect_channel_post_comments(
    channel_id: int,
    *,
    post_id: str,
    url: str = "",
    limit: int = 100,
    staff: dict[str, Any] | None = None,
) -> dict[str, Any]:
    row = channels._latest_channel_row(channel_id, staff=staff)
    platform = str(row.get("platform") or "").lower()
    safe_limit = max(1, min(MAX_CHANNEL_COMMENT_CAP, int(limit or 100)))
    if platform not in COMMENT_COLLECT_PLATFORMS:
        existing = channel_post_comments(channel_id, post_id=post_id, url=url, limit=limit, staff=staff)
        return {**existing, "status": existing["status"] if existing.get("comments") else "not_supported", "message": "当前平台评论采集未接入频道层。"}
    post = channels._match_post(row, post_id, url)
    external_post_id = _comment_external_post_id(platform, post_id, url, post)
    if not external_post_id:
        contract = _comment_contract(
            declared=channels._int((post or {}).get("comments")),
            cached=0,
            cap=safe_limit,
            collect_supported=True,
            missing_post_id=True,
        )
        return {
            "channel_id": int(channel_id),
            "platform": platform,
            "post_id": post_id,
            "comments": [],
            "comment_count": 0,
            "declared_count": contract["declared"],
            "cached_count": contract["cached"],
            "comment_cap": contract["cap"],
            "coverage_status": contract["status"],
            "comment_contract": contract,
            "status": "missing_post_id",
        }

    crawler, config_message = _comment_crawler(platform)
    if crawler is None:
        existing = channel_post_comments(channel_id, post_id=post_id, url=url, limit=limit, staff=staff)
        return {**existing, "status": existing["status"] if existing.get("comments") else "not_supported", "message": config_message}
    if not crawler.configured:
        existing = channel_post_comments(channel_id, post_id=external_post_id, url=url, limit=limit, staff=staff)
        return {**existing, "status": "not_configured", "message": config_message}
    crawl_ref = url or channels._text(post.get("url")) or external_post_id
    result = crawler.crawl_video_comments(crawl_ref, max_results=safe_limit)
    raw_comments = [item for item in result.get("items") or [] if isinstance(item, dict)]
    new_count = _save_channel_comments(channel_id=int(channel_id), platform=platform, external_post_id=external_post_id, comments=raw_comments)
    payload = channel_post_comments(channel_id, post_id=external_post_id, url=url, limit=limit, staff=staff)
    return {
        **payload,
        "status": result.get("provider_status") or payload.get("status"),
        "fetched_count": len(raw_comments),
        "new_count": new_count,
        "message": channels._text(result.get("error"), result.get("message")),
    }
