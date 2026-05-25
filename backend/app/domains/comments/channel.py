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
        from app.services.vkpi.industry_crawlers.reddit_crawler import RedditCrawler

        return RedditCrawler(), "Reddit 评论抓取需要 PRAW 配置。"
    if platform == "instagram":
        from app.services.vkpi.industry_crawlers.instagram_crawler import InstagramCrawler

        return InstagramCrawler(), "Instagram 评论抓取需要 APIFY_TOKEN 配置。"
    if platform == "facebook":
        from app.services.vkpi.industry_crawlers.facebook_crawler import FacebookCrawler

        return FacebookCrawler(), "Facebook 评论抓取需要 APIFY_TOKEN 配置。"
    if platform == "youtube":
        from app.services.vkpi.industry_crawlers.youtube_crawler import YouTubeCrawler

        return YouTubeCrawler(), "YouTube 评论抓取需要 YOUTUBE_API_KEY 配置。"
    if platform == "tiktok":
        from app.services.vkpi.industry_crawlers.tiktok_crawler import TikTokCrawler

        return TikTokCrawler(run_timeout_seconds=300), "TikTok 评论抓取需要 APIFY_TOKEN 配置。"
    if platform == "x":
        from app.services.vkpi.industry_crawlers.x_crawler import XCrawler

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
        standardized = comments_collector._standardize_comment(
            raw,
            platform=platform,
            post_id=0,
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
              created_at, fetched_at, raw_data_json
            ) VALUES (
              ?, ?, ?,
              ?, ?, ?, ?, ?,
              ?, ?, ?, ?, ?,
              ?, ?, ?, ?, ?
            )
            ON CONFLICT (platform, external_comment_id) DO UPDATE SET
              account_id = EXCLUDED.account_id,
              post_id = EXCLUDED.post_id,
              post_table = EXCLUDED.post_table,
              external_post_id = EXCLUDED.external_post_id,
              fetched_at = EXCLUDED.fetched_at,
              raw_data_json = EXCLUDED.raw_data_json
            """,
            (
                int(channel_id),
                0,
                "vkpi_employee_channels",
                external_post_id,
                platform,
                comment_id,
                channels._text(standardized.get("comment_text")),
                channels._text(standardized.get("language_detected"), raw.get("language")),
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
            ),
        )
    conn.commit()
    after = conn.execute(
        "SELECT COUNT(*) AS n FROM vkpi_comments WHERE platform=? AND external_post_id=?",
        (platform, external_post_id),
    ).fetchone()
    return max(0, channels._int(after["n"] if after else 0) - channels._int(before["n"] if before else 0))


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
