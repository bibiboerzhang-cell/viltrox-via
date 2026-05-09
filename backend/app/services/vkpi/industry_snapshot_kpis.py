"""KPI normalization helpers for V-KPI industry snapshots."""
from __future__ import annotations

import json
import re
from collections import Counter, defaultdict
from datetime import datetime, timedelta
from typing import Any


SNAPSHOT_FIELDS = [
    "followers",
    "followers_growth_24h",
    "followers_growth_30d",
    "followers_growth_pct_30d",
    "posts",
    "posts_30d",
    "avg_posts_per_day",
    "views",
    "views_30d",
    "likes",
    "comments",
    "shares",
    "saves",
    "engagement_total_30d",
    "engagement_rate",
    "avg_engagement_rate_by_followers",
    "avg_engagement_per_day",
    "avg_eng_rate_by_views",
    "avg_eng_rate_by_impressions",
    "avg_eng_rate_by_reach",
    "avg_views",
    "reach_total_30d",
    "impressions_total_30d",
    "reels_views_30d",
    "top_post_views",
    "day_with_most_posts",
    "hour_with_most_posts",
    "day_with_highest_engagement",
    "hour_with_highest_engagement",
    "avg_hashtags_per_post",
    "avg_video_duration_seconds",
    "estimated_organic_value_cents",
    "vkpi_attributed_gmv_cents",
    "vkpi_attributed_orders",
    "vkpi_linked_kol_count",
    "vkpi_project_count",
    "youtube_kpi_status",
    "youtube_kpi_source_ref",
    "youtube_kpi_updated_at",
    "youtube_kpi_json",
    "raw_platform_data",
]


def _utcnow() -> str:
    return datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")


def _today() -> str:
    return datetime.utcnow().date().isoformat()


def _json(value: Any) -> str:
    return json.dumps(value or {}, ensure_ascii=False, default=str)


def _int(value: Any) -> int | None:
    try:
        if value is None or value == "":
            return None
        return int(float(value))
    except (TypeError, ValueError):
        return None


def _first_present(*values: Any) -> Any:
    for value in values:
        if value is not None and value != "":
            return value
    return None


def _sum_known(values: list[int | None]) -> int | None:
    known = [int(value) for value in values if value is not None]
    return sum(known) if known else None


def _avg_known(values: list[int | float | None]) -> float | None:
    known = [float(value) for value in values if value is not None]
    return (sum(known) / len(known)) if known else None


def _ratio(numerator: int | float | None, denominator: int | float | None) -> float | None:
    if numerator is None or denominator in {None, 0}:
        return None
    return float(numerator) / float(denominator)


def _parse_datetime(value: Any) -> datetime | None:
    if not value:
        return None
    text = str(value).strip()
    for candidate in (text, text.replace("Z", "+00:00")):
        try:
            return datetime.fromisoformat(candidate).replace(tzinfo=None)
        except Exception:
            continue
    return None


def _parse_duration_seconds(value: Any) -> int | None:
    text = str(value or "").strip()
    if not text:
        return None
    match = re.fullmatch(r"P(?:\d+D)?T(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?", text)
    if not match:
        return None
    hours, minutes, seconds = (int(group or 0) for group in match.groups())
    return hours * 3600 + minutes * 60 + seconds


def _video_items(raw_data: dict[str, Any]) -> list[dict[str, Any]]:
    videos = raw_data.get("videos") or raw_data.get("items") or []
    if isinstance(videos, dict):
        videos = videos.get("items") or []
    return [item for item in videos if isinstance(item, dict)]


def _profile_item(raw_data: dict[str, Any]) -> dict[str, Any]:
    profile = raw_data.get("profile") or raw_data.get("account") or raw_data
    if isinstance(profile, dict) and isinstance(profile.get("items"), list) and profile["items"]:
        return profile["items"][0] if isinstance(profile["items"][0], dict) else {}
    return profile if isinstance(profile, dict) else {}


def _stats(item: dict[str, Any]) -> dict[str, Any]:
    stats: dict[str, Any] = {}
    for key in ("statistics", "public_metrics", "metrics", "stats", "authorMeta"):
        value = item.get(key)
        if isinstance(value, dict):
            stats.update(value)
    for key, value in item.items():
        if not isinstance(value, (dict, list)):
            stats.setdefault(key, value)
    return stats or item


def _snippet(item: dict[str, Any]) -> dict[str, Any]:
    snippet = item.get("snippet") if isinstance(item.get("snippet"), dict) else {}
    return snippet or item


def _field_dict(default: Any = None) -> dict[str, Any]:
    return {field: default for field in SNAPSHOT_FIELDS}


def calculate_kpis(raw_data: dict[str, Any]) -> dict[str, Any]:
    """Calculate the full industry snapshot shape from real raw data.

    Unknown fields stay ``None``. This is intentional: the UI should display
    待同步/未提供 instead of treating missing provider data as zero.
    """

    raw = raw_data or {}
    profile = _profile_item(raw)
    profile_stats = _stats(profile)
    videos = _video_items(raw)
    now = datetime.utcnow()
    cutoff = now - timedelta(days=30)

    followers = _int(
        _first_present(
            profile_stats.get("subscriberCount"),
            profile_stats.get("followers"),
            profile_stats.get("followersCount"),
            profile_stats.get("followerCount"),
            profile_stats.get("followers_count"),
            profile_stats.get("follower"),
            profile_stats.get("fans"),
            profile_stats.get("fansCount"),
            profile_stats.get("fan_count"),
        )
    )
    posts = _int(
        _first_present(
            profile_stats.get("videoCount"),
            profile_stats.get("posts"),
            profile_stats.get("postsCount"),
            profile_stats.get("posts_count"),
            profile_stats.get("mediaCount"),
            profile_stats.get("tweet_count"),
            profile_stats.get("noteCount"),
            profile_stats.get("notesCount"),
            profile_stats.get("notes"),
            profile_stats.get("archiveCount"),
            profile_stats.get("video"),
            profile_stats.get("videos"),
        )
    )
    views = _int(
        _first_present(
            profile_stats.get("viewCount"),
            profile_stats.get("views"),
            profile_stats.get("view"),
            profile_stats.get("totalViews"),
            profile_stats.get("view_count"),
            profile_stats.get("play"),
            profile_stats.get("playCount"),
            profile_stats.get("impression_count"),
        )
    )

    video_views: list[int | None] = []
    video_likes: list[int | None] = []
    video_comments: list[int | None] = []
    video_shares: list[int | None] = []
    video_saves: list[int | None] = []
    video_durations: list[int | None] = []
    hashtag_counts: list[int] = []
    recent_posts = 0
    day_post_counts: Counter[str] = Counter()
    hour_post_counts: Counter[str] = Counter()
    day_engagement: defaultdict[str, int] = defaultdict(int)
    hour_engagement: defaultdict[str, int] = defaultdict(int)

    for video in videos:
        video_stats = _stats(video)
        video_snippet = _snippet(video)
        view_count = _int(
            _first_present(
                video_stats.get("viewCount"),
                video_stats.get("views"),
                video_stats.get("view"),
                video_stats.get("view_count"),
                video_stats.get("play"),
                video_stats.get("playCount"),
                video_stats.get("videoViewCount"),
                video_stats.get("videoPlayCount"),
                video_stats.get("impression_count"),
            )
        )
        like_count = _int(
            _first_present(
                video_stats.get("likeCount"),
                video_stats.get("likes"),
                video_stats.get("like_count"),
                video_stats.get("likesCount"),
                video_stats.get("likedCount"),
                video_stats.get("diggCount"),
            )
        )
        comment_count = _int(
            _first_present(
                video_stats.get("commentCount"),
                video_stats.get("comments"),
                video_stats.get("commentsCount"),
                video_stats.get("reply_count"),
                video_stats.get("comment_count"),
            )
        )
        share_count = _int(
            _first_present(
                video_stats.get("shareCount"),
                video_stats.get("shares"),
                video_stats.get("sharesCount"),
                video_stats.get("share_count"),
                video_stats.get("retweet_count"),
                video_stats.get("repostCount"),
            )
        )
        save_count = _int(
            _first_present(
                video_stats.get("saveCount"),
                video_stats.get("saves"),
                video_stats.get("savedCount"),
                video_stats.get("collectCount"),
                video_stats.get("bookmark_count"),
            )
        )
        video_views.append(view_count)
        video_likes.append(like_count)
        video_comments.append(comment_count)
        video_shares.append(share_count)
        video_saves.append(save_count)
        duration = (video.get("contentDetails") or {}).get("duration") if isinstance(video.get("contentDetails"), dict) else video.get("duration")
        video_durations.append(_parse_duration_seconds(duration))

        published_at = _parse_datetime(video_snippet.get("publishedAt") or video.get("published_at") or video.get("publishedAt") or video.get("timestamp"))
        title = str(video_snippet.get("title") or video.get("title") or "")
        caption = str(video_snippet.get("description") or video.get("caption") or "")
        hashtag_counts.append(len(re.findall(r"#[\\w\\-\\u4e00-\\u9fff]+", f"{title} {caption}")))
        engagement = sum(value or 0 for value in (like_count, comment_count, share_count, save_count))
        if published_at:
            day_key = published_at.strftime("%A")
            hour_key = published_at.strftime("%H:00")
            day_post_counts[day_key] += 1
            hour_post_counts[hour_key] += 1
            day_engagement[day_key] += engagement
            hour_engagement[hour_key] += engagement
            if published_at >= cutoff:
                recent_posts += 1

    views_30d = _sum_known(video_views) if videos else None
    likes = _sum_known(video_likes)
    if likes is None:
        likes = _int(_first_present(profile_stats.get("totalLikes"), profile_stats.get("likedCount"), profile_stats.get("likes")))
    comments = _sum_known(video_comments)
    shares = _sum_known(video_shares)
    saves = _sum_known(video_saves)
    engagement_total = _sum_known([likes, comments, shares, saves])
    avg_views = _avg_known(video_views)
    top_post_views = max([value for value in video_views if value is not None], default=None)

    result = _field_dict()
    result.update(
        {
            "followers": followers,
            "posts": posts,
            "posts_30d": recent_posts if videos else None,
            "avg_posts_per_day": (recent_posts / 30) if videos else None,
            "views": views,
            "views_30d": views_30d,
            "likes": likes,
            "comments": comments,
            "shares": shares,
            "saves": saves,
            "engagement_total_30d": engagement_total,
            "engagement_rate": _ratio(engagement_total, followers),
            "avg_engagement_rate_by_followers": _ratio(engagement_total, followers),
            "avg_engagement_per_day": (engagement_total / 30) if engagement_total is not None else None,
            "avg_eng_rate_by_views": _ratio(engagement_total, views_30d),
            "avg_views": int(avg_views) if avg_views is not None else None,
            "top_post_views": top_post_views,
            "day_with_most_posts": day_post_counts.most_common(1)[0][0] if day_post_counts else None,
            "hour_with_most_posts": hour_post_counts.most_common(1)[0][0] if hour_post_counts else None,
            "day_with_highest_engagement": max(day_engagement.items(), key=lambda item: item[1])[0] if day_engagement else None,
            "hour_with_highest_engagement": max(hour_engagement.items(), key=lambda item: item[1])[0] if hour_engagement else None,
            "avg_hashtags_per_post": _avg_known(hashtag_counts),
            "avg_video_duration_seconds": _avg_known(video_durations),
            "youtube_kpi_status": str(raw.get("youtube_kpi_status") or ("synced" if profile or videos else "not_configured")),
            "youtube_kpi_source_ref": str(raw.get("youtube_kpi_source_ref") or raw.get("source_ref") or ""),
            "youtube_kpi_updated_at": raw.get("youtube_kpi_updated_at") or _utcnow(),
            "youtube_kpi_json": raw.get("youtube_kpi_json") or {"reserved": True, "source": raw.get("source") or "collector"},
            "raw_platform_data": raw,
        }
    )
    for passthrough in (
        "followers_growth_24h",
        "followers_growth_30d",
        "followers_growth_pct_30d",
        "avg_eng_rate_by_impressions",
        "avg_eng_rate_by_reach",
        "reach_total_30d",
        "impressions_total_30d",
        "reels_views_30d",
        "estimated_organic_value_cents",
        "vkpi_attributed_gmv_cents",
        "vkpi_attributed_orders",
        "vkpi_linked_kol_count",
        "vkpi_project_count",
    ):
        if passthrough in raw:
            result[passthrough] = raw.get(passthrough)
    return result

