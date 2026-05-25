"""Provider-backed refill for employee official channels."""
from __future__ import annotations

import json
from datetime import date, datetime
from typing import Any, Callable

from app.core.logging import get_logger
from app.db.connection import get_conn
from app.services.cache import cache_clear
from app.services.vkpi.channel_post_metrics import record_channel_post_metrics
from app.domains.media import prewarm_official_media_cache
from app.services.vkpi.schema_channels import ensure_vkpi_channels_schema
from app.domains.projects.workflow import staff_id as resolve_staff_id


ProgressCallback = Callable[[int, str], None]
CancelCheck = Callable[[], bool]
logger = get_logger(__name__)


class CancelledException(Exception):
    """Raised when an async task requests cancellation at a sync stage boundary."""


def _progress(progress_callback: ProgressCallback | None, pct: int, text: str) -> None:
    if progress_callback is None:
        return
    progress_callback(max(0, min(100, int(pct))), text)


def _check_cancel(cancel_check: CancelCheck | None) -> None:
    if cancel_check is not None and cancel_check():
        raise CancelledException("official channel sync cancelled")


def _utcnow() -> str:
    return datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")


def _today() -> str:
    return date.today().isoformat()


def _json(value: Any) -> str:
    return json.dumps(value or {}, ensure_ascii=False, default=str)


def _int(value: Any, default: int = 0) -> int:
    try:
        return int(value or default)
    except (TypeError, ValueError):
        return default


def _float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value or default)
    except (TypeError, ValueError):
        return default


def _bounded_rate(value: Any) -> float:
    return max(0.0, min(99.9999, _float(value)))


def _text(*values: Any) -> str:
    for value in values:
        text = str(value or "").strip()
        if text:
            return text
    return ""


def _clear_channel_read_cache() -> None:
    try:
        cache_clear(prefix="vkpi:channels:")
    except Exception as exc:
        logger.warning("vkpi channel refill cache clear failed: %s", exc)


def _profile_url(*values: Any) -> str:
    for value in values:
        text = str(value or "").strip()
        if text and "/status/" not in text and "/posts/" not in text:
            return text
    return ""


def _audit_staff_id(channel: dict[str, Any], staff: dict[str, Any] | None) -> int | None:
    return _int(channel.get("staff_id")) or resolve_staff_id(staff) or None


def _items(value: Any) -> list[dict[str, Any]]:
    return [item for item in value if isinstance(item, dict)] if isinstance(value, list) else []


def _nested(row: dict[str, Any], *keys: str) -> Any:
    value: Any = row
    for key in keys:
        if not isinstance(value, dict):
            return None
        value = value.get(key)
    return value


def _sum_posts(posts: list[dict[str, Any]], *keys: str) -> int:
    total = 0
    for post in posts:
        for key in keys:
            value = _nested(post, *key.split(".")) if "." in key else post.get(key)
            if _int(value):
                total += _int(value)
                break
    return total


def _post_items(items: list[dict[str, Any]], *, platform: str = "") -> list[dict[str, Any]]:
    posts: list[dict[str, Any]] = []
    platform = str(platform or "").lower()
    for item in items:
        if item.get("error"):
            continue
        data_type = str(item.get("dataType") or "").lower()
        if platform == "reddit" and data_type in {"community", "subreddit", "comment"}:
            continue
        if data_type == "subreddit" or item.get("type") == "subreddit_profile":
            continue
        if platform == "facebook" and not _text(item.get("url"), item.get("postUrl"), item.get("topLevelUrl")):
            continue
        posts.append(item)
    return posts


def _facebook_profile(items: list[dict[str, Any]]) -> dict[str, Any]:
    for item in items:
        if item.get("pageId") or item.get("pageName") or item.get("likes") or item.get("followers"):
            return item
    return {}


def _reddit_profile(items: list[dict[str, Any]]) -> dict[str, Any]:
    for item in items:
        if item.get("type") == "subreddit_profile" or item.get("dataType") == "subreddit" or item.get("subscribers") or item.get("numberOfMembers"):
            return item
    return {}


def _quality_summary(items: list[dict[str, Any]], platform: str) -> dict[str, Any]:
    posts = _post_items(items, platform=platform)
    urls = sum(1 for item in posts if _text(item.get("url"), item.get("postUrl"), item.get("facebookUrl"), item.get("permalink")))
    media = sum(1 for item in posts if _text(item.get("media"), item.get("image"), item.get("picture"), item.get("thumbnailUrl")))
    engagement = sum(1 for item in posts if _int(item.get("likes"), _int(item.get("reactions"), _int(item.get("score"), _int(item.get("upVotes"))))) or _int(item.get("comments"), _int(item.get("numberOfComments"), _int(item.get("num_comments")))))
    return {
        "platform": platform,
        "post_count": len(posts),
        "posts_with_url": urls,
        "posts_with_media": media,
        "posts_with_engagement": engagement,
        "quality": "good" if posts and urls and engagement else "partial" if posts else "empty",
    }


def _instagram_profile_has_data(profile: dict[str, Any]) -> bool:
    if profile.get("error"):
        return False
    posts = _items(profile.get("latestPosts"))
    return bool(
        posts
        or _text(profile.get("profilePicUrlHD"), profile.get("profilePicUrl"), profile.get("profilePictureUrl"), profile.get("fullName"))
        or _int(profile.get("followersCount"))
        or _int(profile.get("postsCount"))
    )


def _tiktok_items_have_data(items: list[dict[str, Any]]) -> bool:
    for item in items:
        if item.get("error"):
            continue
        author = item.get("authorMeta") if isinstance(item.get("authorMeta"), dict) else {}
        if _text(author.get("avatar"), author.get("avatarMedium"), author.get("avatarThumb"), author.get("nickName")):
            return True
        if _int(author.get("fans")) or _int(item.get("playCount")) or _int(item.get("diggCount")):
            return True
    return False


def _mark(channel: dict[str, Any], *, status: str, message: str, staff: dict[str, Any] | None) -> dict[str, Any]:
    now = _utcnow()
    conn = get_conn()
    conn.execute(
        "UPDATE vkpi_employee_channels SET last_sync_at=?, last_sync_status=?, last_sync_error=?, sync_failure_count=sync_failure_count+1, updated_at=? WHERE id=?",
        (now, status, message, now, int(channel.get("id") or 0)),
    )
    conn.execute(
        "INSERT INTO vkpi_channel_audit (channel_id, staff_id, action, detail, occurred_at) VALUES (?,?,?,?,?)",
        (int(channel.get("id") or 0), _audit_staff_id(channel, staff), "sync_skipped", message, now),
    )
    conn.commit()
    _clear_channel_read_cache()
    return {"channel_id": int(channel.get("id") or 0), "sync_status": status, "message": message, "metrics": None}


def _mark_with_progress(
    channel: dict[str, Any],
    *,
    status: str,
    message: str,
    staff: dict[str, Any] | None,
    progress_callback: ProgressCallback | None,
    cancel_check: CancelCheck | None,
) -> dict[str, Any]:
    _check_cancel(cancel_check)
    _progress(progress_callback, 60, "写入同步状态")
    result = _mark(channel, status=status, message=message, staff=staff)
    _progress(progress_callback, 80, "同步状态已写入")
    return result


def _write_snapshot(channel: dict[str, Any], metrics: dict[str, Any], raw_payload: dict[str, Any], *, staff: dict[str, Any] | None) -> dict[str, Any]:
    now = _utcnow()
    snapshot_date = _today()
    raw_payload = dict(raw_payload or {})
    raw_payload["media_cache"] = prewarm_official_media_cache(metrics, raw_payload)
    conn = get_conn()
    channel_id = int(channel.get("id") or 0)
    previous = conn.execute(
        """
        SELECT followers, posts_count, total_views, total_likes, total_comments, total_shares, captured_at
        FROM vkpi_channel_metrics
        WHERE channel_id=? AND snapshot_date < ?
        ORDER BY snapshot_date DESC, captured_at DESC, id DESC
        LIMIT 1
        """,
        (channel_id, snapshot_date),
    ).fetchone()
    previous_row = dict(previous) if previous else {}
    followers = _int(metrics.get("followers"))
    following = _int(metrics.get("following"))
    posts_count = _int(metrics.get("posts_count"))
    total_views = _int(metrics.get("total_views"))
    total_likes = _int(metrics.get("total_likes"))
    total_comments = _int(metrics.get("total_comments"))
    total_shares = _int(metrics.get("total_shares"))
    post_delta = record_channel_post_metrics(
        channel_id=channel_id,
        platform=_text(channel.get("platform")),
        snapshot_date=snapshot_date,
        captured_at=now,
        raw_payload=raw_payload,
        previous_captured_at=_text(previous_row.get("captured_at")),
    )
    raw_payload["post_level_delta"] = post_delta
    if not raw_payload.get("allow_cumulative_regression"):
        cumulative_floor: dict[str, dict[str, int]] = {}
        previous_followers = _int(previous_row.get("followers"))
        if previous_followers > 0 and followers <= 0:
            cumulative_floor["followers"] = {"provider_value": followers, "kept_value": previous_followers}
            followers = previous_followers
        for key, current in (
            ("posts_count", posts_count),
            ("total_views", total_views),
            ("total_likes", total_likes),
            ("total_comments", total_comments),
            ("total_shares", total_shares),
        ):
            previous_value = _int(previous_row.get(key))
            if previous_value > current:
                cumulative_floor[key] = {"provider_value": current, "kept_value": previous_value}
                if key == "posts_count":
                    posts_count = previous_value
                elif key == "total_views":
                    total_views = previous_value
                elif key == "total_likes":
                    total_likes = previous_value
                elif key == "total_comments":
                    total_comments = previous_value
                elif key == "total_shares":
                    total_shares = previous_value
        if cumulative_floor:
            raw_payload["cumulative_floor"] = {
                "reason": "provider returned a narrower sample or missing cumulative value than the previous official-account baseline",
                "previous_snapshot_date_before": snapshot_date,
                "fields": cumulative_floor,
            }
    followers_delta = followers - _int(previous_row.get("followers"), followers)
    posts_delta = posts_count - _int(previous_row.get("posts_count"), posts_count)
    views_delta = total_views - _int(previous_row.get("total_views"), total_views)
    likes_delta = total_likes - _int(previous_row.get("total_likes"), total_likes)
    posts_delta = max(posts_delta, _int(post_delta.get("new_posts")))
    views_delta = max(views_delta, _int(post_delta.get("views_delta")))
    likes_delta = max(likes_delta, _int(post_delta.get("likes_delta")))
    engagement_rate = _float(metrics.get("engagement_rate"))
    if not engagement_rate and total_views:
        engagement_rate = ((total_likes + total_comments + total_shares) / total_views) * 100.0
    engagement_rate = _bounded_rate(engagement_rate)
    conn.execute(
        """
        INSERT INTO vkpi_channel_metrics
            (channel_id, snapshot_date, followers, following, posts_count, total_views,
             total_likes, total_comments, total_shares, followers_delta, posts_delta, views_delta_24h,
             likes_delta_24h, engagement_rate, raw_payload_json, captured_at)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        ON CONFLICT(channel_id, snapshot_date) DO UPDATE SET
            followers=excluded.followers,
            following=excluded.following,
            posts_count=excluded.posts_count,
            total_views=excluded.total_views,
            total_likes=excluded.total_likes,
            total_comments=excluded.total_comments,
            total_shares=excluded.total_shares,
            followers_delta=excluded.followers_delta,
            posts_delta=excluded.posts_delta,
            views_delta_24h=excluded.views_delta_24h,
            likes_delta_24h=excluded.likes_delta_24h,
            engagement_rate=excluded.engagement_rate,
            raw_payload_json=excluded.raw_payload_json,
            captured_at=excluded.captured_at
        """,
        (
            channel_id,
            snapshot_date,
            followers,
            following,
            posts_count,
            total_views,
            total_likes,
            total_comments,
            total_shares,
            followers_delta,
            posts_delta,
            views_delta,
            likes_delta,
            engagement_rate,
            _json(raw_payload),
            now,
        ),
    )
    conn.execute(
        """
        UPDATE vkpi_employee_channels
        SET account_display_name=COALESCE(NULLIF(?, ''), account_display_name),
            account_url=COALESCE(NULLIF(?, ''), account_url),
            avatar_url=COALESCE(NULLIF(?, ''), avatar_url),
            last_sync_at=?,
            last_sync_status='synced',
            last_sync_error='',
            updated_at=?
        WHERE id=?
        """,
        (
            _text(metrics.get("display_name")),
            _text(metrics.get("account_url")),
            _text(metrics.get("avatar_url")),
            now,
            now,
            int(channel.get("id") or 0),
        ),
    )
    conn.execute(
        "INSERT INTO vkpi_channel_audit (channel_id, staff_id, action, detail, occurred_at) VALUES (?,?,?,?,?)",
        (int(channel.get("id") or 0), _audit_staff_id(channel, staff), "sync_provider", str(raw_payload.get("provider") or ""), now),
    )
    conn.commit()
    _clear_channel_read_cache()
    return {"channel_id": int(channel.get("id") or 0), "sync_status": "synced", "message": "平台数据已同步。", "metrics": metrics}


def _write_snapshot_with_progress(
    channel: dict[str, Any],
    metrics: dict[str, Any],
    raw_payload: dict[str, Any],
    *,
    staff: dict[str, Any] | None,
    progress_callback: ProgressCallback | None,
    cancel_check: CancelCheck | None,
) -> dict[str, Any]:
    _check_cancel(cancel_check)
    _progress(progress_callback, 60, "写入账号快照")
    result = _write_snapshot(channel, metrics, raw_payload, staff=staff)
    _progress(progress_callback, 80, "账号快照已写入")
    return result


def _sync_youtube(
    channel: dict[str, Any],
    *,
    staff: dict[str, Any] | None,
    max_posts: int,
    progress_callback: ProgressCallback | None = None,
    cancel_check: CancelCheck | None = None,
) -> dict[str, Any]:
    from app.services.vkpi.industry_crawlers.youtube_crawler import YouTubeCrawler

    _check_cancel(cancel_check)
    _progress(progress_callback, 10, "YouTube crawler 启动")
    crawler = YouTubeCrawler()
    if not crawler.configured:
        return _mark_with_progress(channel, status="not_configured", message="YouTube API key 未配置，未执行外部抓取。", staff=staff, progress_callback=progress_callback, cancel_check=cancel_check)
    profile = crawler.crawl_channel_profile(_text(channel.get("account_url"), channel.get("account_handle")))
    item = _items(profile.get("items"))[0] if _items(profile.get("items")) else {}
    if not item:
        _progress(progress_callback, 40, "YouTube crawler 完成")
        return _mark_with_progress(channel, status=str(profile.get("sync_status") or "no_results"), message=str(profile.get("error") or profile.get("message") or "YouTube 未返回账号资料。"), staff=staff, progress_callback=progress_callback, cancel_check=cancel_check)
    stats = item.get("statistics") if isinstance(item.get("statistics"), dict) else {}
    snippet = item.get("snippet") if isinstance(item.get("snippet"), dict) else {}
    thumbs = snippet.get("thumbnails") if isinstance(snippet.get("thumbnails"), dict) else {}
    avatar = _text(_nested(thumbs, "high", "url"), _nested(thumbs, "medium", "url"), _nested(thumbs, "default", "url"))
    videos = crawler.crawl_channel_videos(_text(item.get("id")), max_results=max_posts)
    _check_cancel(cancel_check)
    _progress(progress_callback, 40, "YouTube crawler 完成")
    video_items = _items(videos.get("items"))
    metrics = {
        "display_name": _text(snippet.get("title")),
        "account_url": f"https://www.youtube.com/channel/{item.get('id')}" if item.get("id") else "",
        "avatar_url": avatar,
        "followers": _int(stats.get("subscriberCount")),
        "posts_count": _int(stats.get("videoCount"), len(video_items)),
        "total_views": _int(stats.get("viewCount")),
        "total_likes": _sum_posts(video_items, "statistics.likeCount"),
        "total_comments": _sum_posts(video_items, "statistics.commentCount"),
        "total_shares": 0,
    }
    return _write_snapshot_with_progress(channel, metrics, {"provider": "youtube_api", "raw_sample": {"profile": profile, "videos": video_items}}, staff=staff, progress_callback=progress_callback, cancel_check=cancel_check)


def _sync_instagram(
    channel: dict[str, Any],
    *,
    staff: dict[str, Any] | None,
    max_posts: int,
    progress_callback: ProgressCallback | None = None,
    cancel_check: CancelCheck | None = None,
) -> dict[str, Any]:
    from app.services.vkpi.industry_crawlers.instagram_crawler import InstagramCrawler

    _check_cancel(cancel_check)
    _progress(progress_callback, 10, "Instagram crawler 启动")
    crawler = InstagramCrawler(run_timeout_seconds=300)
    if not crawler.configured:
        return _mark_with_progress(channel, status="not_configured", message="APIFY_TOKEN 未配置，Instagram 抓取未执行。", staff=staff, progress_callback=progress_callback, cancel_check=cancel_check)
    result = crawler.crawl_channel_profile(_text(channel.get("account_url"), channel.get("account_handle")), max_posts=max_posts)
    items = _items(result.get("items"))
    if str(result.get("sync_status") or "") != "synced" or not items:
        _progress(progress_callback, 40, "Instagram crawler 完成")
        return _mark_with_progress(channel, status=str(result.get("sync_status") or "no_results"), message=str(result.get("error") or result.get("message") or "Instagram 未返回账号资料。"), staff=staff, progress_callback=progress_callback, cancel_check=cancel_check)
    profile = items[0]
    profile_posts = _items(profile.get("latestPosts"))
    posts = profile_posts or items
    posts_result: dict[str, Any] = {}
    if max_posts > len(profile_posts):
        posts_result = crawler.crawl_channel_videos(_text(profile.get("url"), channel.get("account_url"), channel.get("account_handle")), max_results=max_posts)
        post_items = _items(posts_result.get("items"))
        if str(posts_result.get("sync_status") or "") in {"ok", "synced"} and post_items:
            posts = post_items
    _check_cancel(cancel_check)
    _progress(progress_callback, 40, "Instagram crawler 完成")
    if not _instagram_profile_has_data(profile):
        return _mark_with_progress(channel, status="no_results", message="Instagram 返回空资料或账号不存在，未写入同步快照。", staff=staff, progress_callback=progress_callback, cancel_check=cancel_check)
    metrics = {
        "display_name": _text(profile.get("fullName"), profile.get("username"), channel.get("account_display_name")),
        "account_url": _text(profile.get("url"), channel.get("account_url")),
        "avatar_url": _text(profile.get("profilePicUrlHD"), profile.get("profilePicUrl"), profile.get("profilePictureUrl")),
        "followers": _int(profile.get("followersCount"), _int(profile.get("followers"))),
        "following": _int(profile.get("followsCount"), _int(profile.get("following"))),
        "posts_count": _int(profile.get("postsCount"), len(posts)),
        "total_views": _sum_posts(posts, "videoViewCount", "viewCount", "videoPlayCount", "views"),
        "total_likes": _sum_posts(posts, "likesCount", "likes"),
        "total_comments": _sum_posts(posts, "commentsCount", "comments"),
        "total_shares": _sum_posts(posts, "shareCount", "shares"),
    }
    return _write_snapshot_with_progress(
        channel,
        metrics,
        {
            "provider": "apify_instagram",
            "raw_sample": {"items": items, "posts": posts[: max(1, min(1000, int(max_posts or 12)))]},
            "quality": _quality_summary(posts, "instagram"),
        },
        staff=staff,
        progress_callback=progress_callback,
        cancel_check=cancel_check,
    )


def _sync_tiktok(
    channel: dict[str, Any],
    *,
    staff: dict[str, Any] | None,
    max_posts: int,
    progress_callback: ProgressCallback | None = None,
    cancel_check: CancelCheck | None = None,
) -> dict[str, Any]:
    from app.services.vkpi.industry_crawlers.tiktok_crawler import TikTokCrawler

    _check_cancel(cancel_check)
    _progress(progress_callback, 10, "TikTok crawler 启动")
    crawler = TikTokCrawler(run_timeout_seconds=240)
    if not crawler.configured:
        return _mark_with_progress(channel, status="not_configured", message="APIFY_TOKEN 未配置，TikTok 抓取未执行。", staff=staff, progress_callback=progress_callback, cancel_check=cancel_check)
    result = crawler.crawl_channel_profile(_text(channel.get("account_url"), channel.get("account_handle")), max_posts=max_posts)
    items = _items(result.get("items"))
    if str(result.get("sync_status") or "") != "synced" or not items:
        _progress(progress_callback, 40, "TikTok crawler 完成")
        return _mark_with_progress(channel, status=str(result.get("sync_status") or "no_results"), message=str(result.get("error") or result.get("message") or "TikTok 未返回账号资料。"), staff=staff, progress_callback=progress_callback, cancel_check=cancel_check)
    _check_cancel(cancel_check)
    _progress(progress_callback, 40, "TikTok crawler 完成")
    if not _tiktok_items_have_data(items):
        return _mark_with_progress(channel, status="no_results", message="TikTok 返回空资料或账号不存在，未写入同步快照。", staff=staff, progress_callback=progress_callback, cancel_check=cancel_check)
    author = items[0].get("authorMeta") if isinstance(items[0].get("authorMeta"), dict) else {}
    metrics = {
        "display_name": _text(author.get("nickName"), author.get("name"), channel.get("account_display_name")),
        "account_url": _text(author.get("profileUrl"), channel.get("account_url")),
        "avatar_url": _text(author.get("avatar"), author.get("avatarMedium"), author.get("avatarThumb")),
        "followers": _int(author.get("fans"), _int(author.get("followers"))),
        "following": _int(author.get("following")),
        "posts_count": len(items),
        "total_views": _sum_posts(items, "playCount", "views"),
        "total_likes": _sum_posts(items, "diggCount", "likes"),
        "total_comments": _sum_posts(items, "commentCount", "comments"),
        "total_shares": _sum_posts(items, "shareCount", "shares"),
    }
    return _write_snapshot_with_progress(channel, metrics, {"provider": "apify_tiktok", "raw_sample": {"items": items}}, staff=staff, progress_callback=progress_callback, cancel_check=cancel_check)


def _sync_facebook(
    channel: dict[str, Any],
    *,
    staff: dict[str, Any] | None,
    max_posts: int,
    progress_callback: ProgressCallback | None = None,
    cancel_check: CancelCheck | None = None,
) -> dict[str, Any]:
    from app.services.vkpi.industry_crawlers.facebook_crawler import FacebookCrawler

    _check_cancel(cancel_check)
    _progress(progress_callback, 10, "Facebook crawler 启动")
    crawler = FacebookCrawler()
    if not crawler.configured:
        return _mark_with_progress(channel, status="not_configured", message="APIFY_TOKEN / Meta Graph 未配置，Facebook 抓取未执行。", staff=staff, progress_callback=progress_callback, cancel_check=cancel_check)
    result = crawler.crawl_channel_profile(_text(channel.get("account_url"), channel.get("account_handle")), max_posts=max_posts)
    items = _items(result.get("items"))
    if str(result.get("sync_status") or "") not in {"ok", "synced"} or not items:
        _progress(progress_callback, 40, "Facebook crawler 完成")
        return _mark_with_progress(channel, status=str(result.get("sync_status") or "no_results"), message=str(result.get("error") or result.get("message") or "Facebook 未返回账号资料。"), staff=staff, progress_callback=progress_callback, cancel_check=cancel_check)
    _check_cancel(cancel_check)
    _progress(progress_callback, 40, "Facebook crawler 完成")
    profile = _facebook_profile(items)
    posts = _post_items(items, platform="facebook")
    metrics = {
        "display_name": _text(channel.get("account_display_name"), profile.get("title"), profile.get("name"), profile.get("pageName")),
        "account_url": _text(profile.get("url"), profile.get("facebookUrl"), channel.get("account_url")),
        "avatar_url": _text(profile.get("profilePictureUrl"), profile.get("profilePic"), profile.get("profilePicture"), profile.get("image"), profile.get("picture"), profile.get("profilePhoto")),
        "followers": _int(profile.get("followers"), _int(profile.get("followersCount"), _int(profile.get("likes")))),
        "posts_count": len(posts) or _int(profile.get("postsCount")),
        "total_views": _sum_posts(posts, "views", "videoViews", "videoViewCount", "viewsCount"),
        "total_likes": _sum_posts(posts, "likes", "reactions"),
        "total_comments": _sum_posts(posts, "comments", "commentsCount"),
        "total_shares": _sum_posts(posts, "shares", "sharesCount"),
    }
    return _write_snapshot_with_progress(
        channel,
        metrics,
        {"provider": "apify_facebook", "raw_sample": {"items": items}, "quality": _quality_summary(items, "facebook")},
        staff=staff,
        progress_callback=progress_callback,
        cancel_check=cancel_check,
    )


def _sync_reddit(
    channel: dict[str, Any],
    *,
    staff: dict[str, Any] | None,
    max_posts: int,
    progress_callback: ProgressCallback | None = None,
    cancel_check: CancelCheck | None = None,
) -> dict[str, Any]:
    from app.services.vkpi.industry_crawlers.reddit_crawler import RedditCrawler

    _check_cancel(cancel_check)
    _progress(progress_callback, 10, "Reddit crawler 启动")
    crawler = RedditCrawler()
    if not crawler.configured:
        return _mark_with_progress(channel, status="not_configured", message="Reddit PRAW / APIFY_TOKEN 未配置，Reddit 抓取未执行。", staff=staff, progress_callback=progress_callback, cancel_check=cancel_check)
    result = crawler.crawl_channel_profile(_text(channel.get("account_url"), channel.get("account_handle")), max_posts=max_posts)
    items = _items(result.get("items"))
    if str(result.get("sync_status") or "") not in {"ok", "synced"} or not items:
        _progress(progress_callback, 40, "Reddit crawler 完成")
        return _mark_with_progress(channel, status=str(result.get("sync_status") or "no_results"), message=str(result.get("error") or result.get("message") or "Reddit 未返回帖子。"), staff=staff, progress_callback=progress_callback, cancel_check=cancel_check)
    _check_cancel(cancel_check)
    _progress(progress_callback, 40, "Reddit crawler 完成")
    profile = _reddit_profile(items)
    posts = _post_items(items, platform="reddit")
    subreddit_name = _text(profile.get("display_name"), profile.get("subreddit"), channel.get("account_handle"))
    metrics = {
        "display_name": _text(profile.get("title"), profile.get("display_name"), channel.get("account_display_name")),
        "account_url": _text(profile.get("url"), channel.get("account_url")),
        "avatar_url": _text(profile.get("icon_img"), profile.get("communityIcon"), profile.get("banner_img")),
        "followers": _int(profile.get("subscribers"), _int(profile.get("numberOfMembers"), _int(channel.get("latest_followers")))),
        "posts_count": len(posts),
        "total_views": 0,
        "total_likes": _sum_posts(posts, "score", "upVotes", "ups"),
        "total_comments": _sum_posts(posts, "num_comments", "numberOfComments", "comments"),
        "total_shares": 0,
    }
    if subreddit_name and not metrics["account_url"]:
        metrics["account_url"] = f"https://www.reddit.com/r/{subreddit_name}/"
    return _write_snapshot_with_progress(
        channel,
        metrics,
        {"provider": result.get("provider") or "reddit", "raw_sample": {"items": items}, "quality": _quality_summary(items, "reddit")},
        staff=staff,
        progress_callback=progress_callback,
        cancel_check=cancel_check,
    )


def _sync_x(
    channel: dict[str, Any],
    *,
    staff: dict[str, Any] | None,
    max_posts: int,
    progress_callback: ProgressCallback | None = None,
    cancel_check: CancelCheck | None = None,
) -> dict[str, Any]:
    from app.services.vkpi.industry_crawlers.x_crawler import XCrawler

    _check_cancel(cancel_check)
    _progress(progress_callback, 10, "X crawler 启动")
    crawler = XCrawler(run_timeout_seconds=240)
    if not crawler.configured:
        return _mark_with_progress(channel, status="not_configured", message="X API / APIFY_TOKEN 未配置，X 抓取未执行。", staff=staff, progress_callback=progress_callback, cancel_check=cancel_check)
    result = crawler.crawl_channel_profile(_text(channel.get("account_url"), channel.get("account_handle")), max_posts=max_posts)
    items = _items(result.get("items"))
    if str(result.get("sync_status") or "") not in {"ok", "synced"} or not items:
        _progress(progress_callback, 40, "X crawler 完成")
        return _mark_with_progress(channel, status=str(result.get("sync_status") or "no_results"), message=str(result.get("error") or result.get("message") or "X 未返回账号资料。"), staff=staff, progress_callback=progress_callback, cancel_check=cancel_check)
    _check_cancel(cancel_check)
    _progress(progress_callback, 40, "X crawler 完成")
    profile = items[0]
    public_metrics = profile.get("public_metrics") if isinstance(profile.get("public_metrics"), dict) else {}
    author = profile.get("author") if isinstance(profile.get("author"), dict) else {}
    user = profile.get("user") if isinstance(profile.get("user"), dict) else {}
    posts = [item for item in items if _text(item.get("url"), item.get("tweetUrl"), item.get("twitterUrl"), item.get("id"))]
    handle = _text(profile.get("username"), author.get("userName"), author.get("username"), user.get("username"), channel.get("account_handle"))
    account_url = _profile_url(profile.get("url"), profile.get("twitterUrl"), profile.get("profileUrl"), channel.get("account_url"))
    if not account_url and handle:
        account_url = f"https://x.com/{handle.lstrip('@')}"
    metrics = {
        "display_name": _text(profile.get("name"), author.get("name"), user.get("name"), channel.get("account_display_name")),
        "account_url": account_url,
        "avatar_url": _text(profile.get("profile_image_url"), profile.get("profilePicture"), author.get("profilePicture"), user.get("profile_image_url")),
        "followers": _int(public_metrics.get("followers_count"), _int(author.get("followers"), _int(profile.get("followers"), _int(profile.get("followersCount"))))),
        "following": _int(public_metrics.get("following_count"), _int(profile.get("following"), _int(profile.get("followingCount")))),
        "posts_count": len(posts) if len(items) > 1 else _int(public_metrics.get("tweet_count"), len(posts)),
        "total_views": _sum_posts(posts, "viewCount", "views", "impressionCount", "public_metrics.impression_count"),
        "total_likes": _sum_posts(posts, "likeCount", "likes", "favoriteCount", "public_metrics.like_count"),
        "total_comments": _sum_posts(posts, "replyCount", "replies", "public_metrics.reply_count"),
        "total_shares": _sum_posts(posts, "retweetCount", "retweets", "public_metrics.retweet_count"),
    }
    return _write_snapshot_with_progress(channel, metrics, {"provider": result.get("mode") or "x", "raw_sample": {"items": items}}, staff=staff, progress_callback=progress_callback, cancel_check=cancel_check)


def sync_channel_snapshot(
    channel: dict[str, Any],
    *,
    staff: dict[str, Any] | None = None,
    max_posts: int = 12,
    progress_callback: ProgressCallback | None = None,
    cancel_check: CancelCheck | None = None,
) -> dict[str, Any]:
    ensure_vkpi_channels_schema()
    platform = str(channel.get("platform") or "").lower()
    _check_cancel(cancel_check)
    _progress(progress_callback, 0, "开始同步官方账号")
    if platform == "youtube":
        return _sync_youtube(channel, staff=staff, max_posts=max_posts, progress_callback=progress_callback, cancel_check=cancel_check)
    if platform == "instagram":
        return _sync_instagram(channel, staff=staff, max_posts=max_posts, progress_callback=progress_callback, cancel_check=cancel_check)
    if platform == "tiktok":
        return _sync_tiktok(channel, staff=staff, max_posts=max_posts, progress_callback=progress_callback, cancel_check=cancel_check)
    if platform == "facebook":
        return _sync_facebook(channel, staff=staff, max_posts=max_posts, progress_callback=progress_callback, cancel_check=cancel_check)
    if platform == "reddit":
        return _sync_reddit(channel, staff=staff, max_posts=max_posts, progress_callback=progress_callback, cancel_check=cancel_check)
    if platform == "x":
        return _sync_x(channel, staff=staff, max_posts=max_posts, progress_callback=progress_callback, cancel_check=cancel_check)
    return _mark_with_progress(channel, status="not_supported", message=f"{platform or 'unknown'} 尚未接入自动补抓。", staff=staff, progress_callback=progress_callback, cancel_check=cancel_check)
