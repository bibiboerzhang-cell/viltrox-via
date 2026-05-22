"""Per-post metric deltas for official channel refreshes."""
from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

from app.db.connection import get_conn, is_postgres_runtime

DELTA_METHOD = "post_metric_delta_v1"
_SCHEMA_READY = False


def ensure_channel_post_metrics_schema() -> None:
    global _SCHEMA_READY
    if _SCHEMA_READY:
        return
    conn = get_conn()
    id_type = "BIGSERIAL PRIMARY KEY" if is_postgres_runtime() else "INTEGER PRIMARY KEY AUTOINCREMENT"
    channel_id_type = "BIGINT" if is_postgres_runtime() else "INTEGER"
    snapshot_date_type = "DATE" if is_postgres_runtime() else "TEXT"
    metric_type = "BIGINT" if is_postgres_runtime() else "INTEGER"
    timestamp_type = "TIMESTAMPTZ" if is_postgres_runtime() else "TEXT"
    conn.execute(
        f"""
        CREATE TABLE IF NOT EXISTS vkpi_channel_post_metrics (
            id {id_type},
            channel_id {channel_id_type} NOT NULL,
            snapshot_date {snapshot_date_type} NOT NULL,
            platform TEXT NOT NULL DEFAULT '',
            post_uid TEXT NOT NULL,
            post_url TEXT DEFAULT '',
            title TEXT DEFAULT '',
            posted_at {timestamp_type},
            views {metric_type} NOT NULL DEFAULT 0,
            likes {metric_type} NOT NULL DEFAULT 0,
            comments {metric_type} NOT NULL DEFAULT 0,
            shares {metric_type} NOT NULL DEFAULT 0,
            views_delta {metric_type} NOT NULL DEFAULT 0,
            likes_delta {metric_type} NOT NULL DEFAULT 0,
            comments_delta {metric_type} NOT NULL DEFAULT 0,
            shares_delta {metric_type} NOT NULL DEFAULT 0,
            delta_method TEXT NOT NULL DEFAULT 'post_metric_delta_v1',
            raw_post_json TEXT NOT NULL DEFAULT '{{}}',
            first_seen_at {timestamp_type} NOT NULL,
            captured_at {timestamp_type} NOT NULL,
            UNIQUE(channel_id, post_uid, snapshot_date)
        )
        """
    )
    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_vkpi_channel_post_metrics_latest
            ON vkpi_channel_post_metrics(channel_id, post_uid, snapshot_date DESC, captured_at DESC)
        """
    )
    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_vkpi_channel_post_metrics_channel_date
            ON vkpi_channel_post_metrics(channel_id, snapshot_date DESC)
        """
    )
    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_vkpi_channel_post_metrics_platform_date
            ON vkpi_channel_post_metrics(platform, snapshot_date DESC)
        """
    )
    conn.commit()
    _SCHEMA_READY = True


def _items(value: Any) -> list[dict[str, Any]]:
    return [item for item in value if isinstance(item, dict)] if isinstance(value, list) else []


def _text(*values: Any) -> str:
    for value in values:
        text = str(value or "").strip()
        if text:
            return text
    return ""


def _int(value: Any, default: int = 0) -> int:
    try:
        return int(float(str(value).replace(",", "")))
    except (TypeError, ValueError):
        return int(default or 0)


def _json(value: Any) -> str:
    return json.dumps(value or {}, ensure_ascii=False, default=str)


def _nested(row: dict[str, Any], *keys: str) -> Any:
    value: Any = row
    for key in keys:
        if not isinstance(value, dict):
            return None
        value = value.get(key)
    return value


def _metric(item: dict[str, Any], *keys: str) -> int:
    for key in keys:
        value = _nested(item, *key.split(".")) if "." in key else item.get(key)
        if value is not None and value != "":
            return _int(value)
    return 0


def _parse_datetime(value: Any) -> datetime | None:
    text = _text(value)
    if not text:
        return None
    if text.isdigit():
        try:
            return datetime.fromtimestamp(float(text), tz=timezone.utc)
        except (OverflowError, ValueError):
            return None
    normalized = text.replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _iso_or_none(value: Any) -> str | None:
    parsed = _parse_datetime(value)
    if not parsed:
        return None
    return parsed.isoformat(timespec="seconds").replace("+00:00", "Z")


def _post_uid(platform: str, item: dict[str, Any], *values: Any) -> str:
    for value in values:
        text = _text(value)
        if text:
            return text
    url = _text(item.get("url"), item.get("postUrl"), item.get("webVideoUrl"), item.get("twitterUrl"), item.get("permalink"))
    if url:
        return url
    return ""


def _post(platform: str, item: dict[str, Any], *, post_uid: str, title: str, url: str, posted_at: Any, views: int, likes: int, comments: int, shares: int) -> dict[str, Any] | None:
    uid = _text(post_uid)
    if not uid:
        return None
    return {
        "platform": platform,
        "post_uid": uid,
        "post_url": _text(url),
        "title": _text(title),
        "posted_at": _iso_or_none(posted_at),
        "views": max(0, int(views or 0)),
        "likes": max(0, int(likes or 0)),
        "comments": max(0, int(comments or 0)),
        "shares": max(0, int(shares or 0)),
        "raw_post": item,
    }


def normalize_channel_posts(platform: str, raw_payload: dict[str, Any]) -> list[dict[str, Any]]:
    """Return canonical metric rows from a channel snapshot raw payload."""
    platform = _text(platform).lower()
    sample = raw_payload.get("raw_sample") if isinstance(raw_payload.get("raw_sample"), dict) else {}
    posts: list[dict[str, Any] | None] = []
    if platform == "youtube":
        for item in _items(sample.get("videos")):
            snippet = item.get("snippet") if isinstance(item.get("snippet"), dict) else {}
            stats = item.get("statistics") if isinstance(item.get("statistics"), dict) else {}
            item_id = item.get("id")
            video_id = _text(item_id.get("videoId") if isinstance(item_id, dict) else item_id)
            posts.append(
                _post(
                    platform,
                    item,
                    post_uid=video_id,
                    title=_text(snippet.get("title")),
                    url=f"https://www.youtube.com/watch?v={video_id}" if video_id else "",
                    posted_at=snippet.get("publishedAt"),
                    views=_metric(stats, "viewCount"),
                    likes=_metric(stats, "likeCount"),
                    comments=_metric(stats, "commentCount"),
                    shares=0,
                )
            )
    elif platform == "instagram":
        source_posts = _items(sample.get("posts"))
        if not source_posts:
            for profile in _items(sample.get("items")):
                source_posts.extend(_items(profile.get("latestPosts")))
                if _text(profile.get("shortCode"), profile.get("id"), profile.get("url")):
                    source_posts.append(profile)
        for item in source_posts:
            uid = _post_uid(platform, item, item.get("shortCode"), item.get("id"), item.get("source_id"))
            posts.append(
                _post(
                    platform,
                    item,
                    post_uid=uid,
                    title=_text(item.get("caption"), item.get("title"), item.get("alt")),
                    url=_text(item.get("url"), item.get("shortCodeUrl")),
                    posted_at=_text(item.get("timestamp"), item.get("createdAt"), item.get("posted_at")),
                    views=_metric(item, "videoViewCount", "viewCount", "videoPlayCount", "views"),
                    likes=_metric(item, "likesCount", "likes"),
                    comments=_metric(item, "commentsCount", "comments"),
                    shares=_metric(item, "shareCount", "shares"),
                )
            )
    elif platform == "tiktok":
        for item in _items(sample.get("items")):
            uid = _post_uid(platform, item, item.get("id"), item.get("webVideoUrl"))
            posts.append(
                _post(
                    platform,
                    item,
                    post_uid=uid,
                    title=_text(item.get("text")),
                    url=_text(item.get("webVideoUrl"), item.get("url")),
                    posted_at=_text(item.get("createTimeISO"), item.get("createTime")),
                    views=_metric(item, "playCount", "views"),
                    likes=_metric(item, "diggCount", "likes"),
                    comments=_metric(item, "commentCount", "comments"),
                    shares=_metric(item, "shareCount", "shares"),
                )
            )
    elif platform == "facebook":
        for item in _items(sample.get("items")):
            url = _text(item.get("url"), item.get("postUrl"), item.get("topLevelUrl"))
            if not url:
                continue
            uid = _post_uid(platform, item, item.get("postId"), item.get("id"), url)
            posts.append(
                _post(
                    platform,
                    item,
                    post_uid=uid,
                    title=_text(item.get("text"), item.get("message")),
                    url=url,
                    posted_at=_text(item.get("time"), item.get("timestamp"), item.get("createdAt")),
                    views=_metric(item, "views", "videoViews", "videoViewCount", "viewsCount"),
                    likes=_metric(item, "likes", "reactions", "reactionLikeCount", "topReactionsCount"),
                    comments=_metric(item, "comments", "commentsCount"),
                    shares=_metric(item, "shares", "sharesCount"),
                )
            )
    elif platform == "reddit":
        for item in _items(sample.get("items")):
            data_type = _text(item.get("dataType")).lower()
            if data_type in {"community", "subreddit", "comment"} or item.get("numberOfMembers"):
                continue
            uid = _post_uid(platform, item, item.get("parsedId"), item.get("id"), item.get("name"), item.get("permalink"))
            posts.append(
                _post(
                    platform,
                    item,
                    post_uid=uid,
                    title=_text(item.get("title"), item.get("body")),
                    url=_text(item.get("url"), item.get("permalink")),
                    posted_at=item.get("createdAt"),
                    views=0,
                    likes=_metric(item, "score", "upVotes", "ups"),
                    comments=_metric(item, "num_comments", "numberOfComments", "comments"),
                    shares=0,
                )
            )
    elif platform == "x":
        for item in _items(sample.get("items")):
            url = _text(item.get("twitterUrl"), item.get("url"))
            if not url and not _text(item.get("id")):
                continue
            uid = _post_uid(platform, item, item.get("id"), url)
            posts.append(
                _post(
                    platform,
                    item,
                    post_uid=uid,
                    title=_text(item.get("fullText"), item.get("text")),
                    url=url,
                    posted_at=item.get("createdAt"),
                    views=_metric(item, "viewCount", "views", "impressionCount", "public_metrics.impression_count"),
                    likes=_metric(item, "likeCount", "likes", "favoriteCount", "public_metrics.like_count"),
                    comments=_metric(item, "replyCount", "replies", "public_metrics.reply_count"),
                    shares=_metric(item, "retweetCount", "retweets", "public_metrics.retweet_count"),
                )
            )
    deduped: dict[str, dict[str, Any]] = {}
    for item in posts:
        if not item:
            continue
        key = item["post_uid"]
        if key not in deduped:
            deduped[key] = item
    return list(deduped.values())


def record_channel_post_metrics(
    *,
    channel_id: int,
    platform: str,
    snapshot_date: str,
    captured_at: str,
    raw_payload: dict[str, Any],
    previous_captured_at: str = "",
) -> dict[str, Any]:
    """Write post metrics and return safe positive deltas for the snapshot."""
    ensure_channel_post_metrics_schema()
    posts = normalize_channel_posts(platform, raw_payload)
    summary = {
        "method": DELTA_METHOD,
        "sample_count": len(posts),
        "matched_posts": 0,
        "new_posts": 0,
        "first_seen_existing_posts": 0,
        "views_delta": 0,
        "likes_delta": 0,
        "comments_delta": 0,
        "shares_delta": 0,
    }
    if not posts:
        return summary

    conn = get_conn()
    previous_cutoff = _parse_datetime(previous_captured_at)
    for post in posts:
        previous = conn.execute(
            """
            SELECT views, likes, comments, shares, first_seen_at
            FROM vkpi_channel_post_metrics
            WHERE channel_id=? AND post_uid=? AND snapshot_date < ?
            ORDER BY snapshot_date DESC, captured_at DESC, id DESC
            LIMIT 1
            """,
            (int(channel_id), post["post_uid"], snapshot_date),
        ).fetchone()
        if previous:
            prev = dict(previous)
            summary["matched_posts"] += 1
            views_delta = max(0, post["views"] - _int(prev.get("views")))
            likes_delta = max(0, post["likes"] - _int(prev.get("likes")))
            comments_delta = max(0, post["comments"] - _int(prev.get("comments")))
            shares_delta = max(0, post["shares"] - _int(prev.get("shares")))
            first_seen_at = _text(prev.get("first_seen_at"), captured_at)
        else:
            posted_at = _parse_datetime(post.get("posted_at"))
            is_new_since_previous_snapshot = bool(previous_cutoff and posted_at and posted_at > previous_cutoff)
            if is_new_since_previous_snapshot:
                summary["new_posts"] += 1
                views_delta = post["views"]
                likes_delta = post["likes"]
                comments_delta = post["comments"]
                shares_delta = post["shares"]
            else:
                summary["first_seen_existing_posts"] += 1
                views_delta = likes_delta = comments_delta = shares_delta = 0
            first_seen_at = captured_at

        summary["views_delta"] += int(views_delta)
        summary["likes_delta"] += int(likes_delta)
        summary["comments_delta"] += int(comments_delta)
        summary["shares_delta"] += int(shares_delta)
        conn.execute(
            """
            INSERT INTO vkpi_channel_post_metrics
                (channel_id, snapshot_date, platform, post_uid, post_url, title, posted_at,
                 views, likes, comments, shares, views_delta, likes_delta, comments_delta,
                 shares_delta, delta_method, raw_post_json, first_seen_at, captured_at)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            ON CONFLICT(channel_id, post_uid, snapshot_date) DO UPDATE SET
                platform=excluded.platform,
                post_url=excluded.post_url,
                title=excluded.title,
                posted_at=excluded.posted_at,
                views=excluded.views,
                likes=excluded.likes,
                comments=excluded.comments,
                shares=excluded.shares,
                views_delta=excluded.views_delta,
                likes_delta=excluded.likes_delta,
                comments_delta=excluded.comments_delta,
                shares_delta=excluded.shares_delta,
                delta_method=excluded.delta_method,
                raw_post_json=excluded.raw_post_json,
                captured_at=excluded.captured_at
            """,
            (
                int(channel_id),
                snapshot_date,
                _text(platform).lower(),
                post["post_uid"],
                post["post_url"],
                post["title"][:500],
                post["posted_at"],
                post["views"],
                post["likes"],
                post["comments"],
                post["shares"],
                views_delta,
                likes_delta,
                comments_delta,
                shares_delta,
                DELTA_METHOD,
                _json(post.get("raw_post")),
                first_seen_at,
                captured_at,
            ),
        )
    return summary
