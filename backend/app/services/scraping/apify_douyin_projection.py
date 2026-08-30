"""Pure normalization for the legacy Douyin Apify adapter."""
from __future__ import annotations

from typing import Any, Callable


def project_douyin_result(
    item: dict[str, Any],
    *,
    views: int,
    likes: int,
    comments: int,
    shares: int,
    favorites: int,
    visible_comments: list[Any],
    video_url: str,
    first_nested_int: Callable[[dict[str, Any], tuple[str, ...]], int],
) -> dict[str, Any]:
    author = item.get("author") if isinstance(item.get("author"), dict) else {}
    video = item.get("video") if isinstance(item.get("video"), dict) else {}
    title = str(
        item.get("desc")
        or item.get("description")
        or item.get("text")
        or item.get("title")
        or ""
    )
    owner_username = str(
        item.get("unique_id")
        or item.get("authorUniqueId")
        or author.get("uniqueId")
        or author.get("secUid")
        or author.get("uid")
        or ""
    )
    owner_full_name = str(
        item.get("nickname")
        or item.get("nickName")
        or item.get("authorName")
        or item.get("authorNickname")
        or author.get("nickname")
        or author.get("nickName")
        or author.get("name")
        or ""
    )
    owner_url = (
        f"https://www.douyin.com/user/{owner_username}" if owner_username else ""
    )
    return {
        "scraped_ok": True,
        "title": title[:200],
        "caption": title,
        "scraped_text": title,
        "og_image": str(
            item.get("thumbnail")
            or item.get("cover")
            or item.get("coverUrl")
            or item.get("dynamicCover")
            or ""
        ),
        "metrics": {
            "views": views,
            "likes": likes,
            "comments": comments,
            "shares": shares,
            "favorites": favorites,
        },
        "metrics_available": {
            "views": views > 0,
            "likes": likes > 0,
            "comments": comments > 0,
            "shares": shares > 0,
            "favorites": favorites > 0,
        },
        "visible_comments": visible_comments,
        "published_at": item.get("createTime") or item.get("create_time") or None,
        "video_url": video_url,
        "owner_username": owner_username,
        "owner_full_name": owner_full_name,
        "owner": owner_full_name,
        "author": owner_full_name,
        "channel_name": owner_full_name,
        "channel_url": owner_url,
        "owner_url": owner_url,
        "avatar_url": str(
            item.get("avatarUri")
            or item.get("avatarUrl")
            or author.get("avatarThumb")
            or ""
        ),
        "follower_count": first_nested_int(
            item,
            ("followerCount", "follower_count", "followers", "fansCount"),
        ),
        "total_favorited": first_nested_int(
            item,
            ("totalFavorited", "total_favorited"),
        ),
        "duration": item.get("duration") or video.get("duration") or 0,
        "hashtags": item.get("hashtags")
        if isinstance(item.get("hashtags"), list)
        else [],
        "error": None,
        "scraper": "apify_douyin",
        "metrics_source": {
            "views": "apify_douyin" if views > 0 else "unavailable"
        },
    }


__all__ = ["project_douyin_result"]
