#!/usr/bin/env python3
"""Crawl one official channel and package de-duplicated post data.

Output lives under tmp/ by default, so large raw provider payloads do not enter
git. The script is intentionally single-account to keep provider spend and
failure scope controlled.
"""
from __future__ import annotations

import argparse
import asyncio
import csv
import hashlib
import json
import re
import sys
import urllib.parse
from datetime import datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))


def _utc_stamp() -> str:
    return datetime.utcnow().strftime("%Y%m%dT%H%M%SZ")


def _text(*values: Any) -> str:
    for value in values:
        text = str(value or "").strip()
        if text:
            return text
    return ""


def _int(value: Any) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def _nested(row: dict[str, Any], *keys: str) -> Any:
    value: Any = row
    for key in keys:
        if not isinstance(value, dict):
            return None
        value = value.get(key)
    return value


def _media_urls(value: Any, *, depth: int = 0) -> list[str]:
    if depth > 7:
        return []
    if isinstance(value, str):
        text = _text(value)
        if text.startswith(("http://", "https://")):
            host = urllib.parse.urlparse(text).hostname or ""
            if any(part in host for part in ("fbcdn", "cdninstagram", "redd.it", "redditmedia", "ytimg", "apifyusercontent", "twimg")):
                return [text]
        return []
    if isinstance(value, list):
        urls: list[str] = []
        for item in value[:40]:
            urls.extend(_media_urls(item, depth=depth + 1))
        return list(dict.fromkeys(urls))
    if isinstance(value, dict):
        urls: list[str] = []
        for key in ("displayUrl", "imageUrl", "thumbnailUrl", "thumbnail", "uri", "picture", "photo_image", "thumbnailImage", "media_url", "media_url_https", "preview_image_url", "url"):
            urls.extend(_media_urls(value.get(key), depth=depth + 1))
        return list(dict.fromkeys(urls))
    return []


def _video_url(value: Any, *, depth: int = 0) -> str:
    if depth > 7:
        return ""
    if isinstance(value, str):
        text = _text(value)
        if not text.startswith(("http://", "https://")):
            return ""
        parsed = urllib.parse.urlparse(text)
        host = parsed.hostname or ""
        return text if ".mp4" in parsed.path.lower() or "video-" in host or host.endswith("v.redd.it") or host.endswith("video.twimg.com") else ""
    if isinstance(value, list):
        return next((_video_url(item, depth=depth + 1) for item in value if _video_url(item, depth=depth + 1)), "")
    if isinstance(value, dict):
        for key in ("videoUrl", "browser_native_hd_url", "browser_native_sd_url", "playable_url", "fallback_url", "url"):
            found = _video_url(value.get(key), depth=depth + 1)
            if found:
                return found
        for item in value.values():
            found = _video_url(item, depth=depth + 1)
            if found:
                return found
    return ""


def _items(value: Any) -> list[dict[str, Any]]:
    return [item for item in value if isinstance(item, dict)] if isinstance(value, list) else []


def _slug(value: str) -> str:
    text = re.sub(r"[^a-zA-Z0-9_.-]+", "-", str(value or "").strip().lower()).strip("-")
    return text or "channel"


def _canonical_url(value: str) -> str:
    raw = str(value or "").strip()
    if not raw:
        return ""
    parsed = urllib.parse.urlparse(raw)
    if not parsed.netloc:
        return raw.rstrip("/")
    host = parsed.netloc.lower().removeprefix("www.")
    path = re.sub(r"/+", "/", parsed.path).rstrip("/")
    query = urllib.parse.parse_qs(parsed.query)
    keep_query = ""
    for key in ("v", "story_fbid", "id"):
        if query.get(key):
            keep_query = urllib.parse.urlencode({key: query[key][0]})
            break
    return urllib.parse.urlunparse((parsed.scheme or "https", host, path, "", keep_query, ""))


def _url_identity(platform: str, url: str) -> str:
    canonical = _canonical_url(url)
    if not canonical:
        return ""
    parsed = urllib.parse.urlparse(canonical)
    parts = [part for part in parsed.path.split("/") if part]
    if platform == "instagram":
        for marker in ("p", "reel", "tv"):
            if marker in parts:
                idx = parts.index(marker)
                if idx + 1 < len(parts):
                    return f"ig:{parts[idx + 1]}"
    if platform == "tiktok" and "video" in parts:
        idx = parts.index("video")
        if idx + 1 < len(parts):
            return f"tiktok:{parts[idx + 1]}"
    if platform == "youtube":
        video = urllib.parse.parse_qs(parsed.query).get("v", [""])[0]
        if video:
            return f"youtube:{video}"
        if parsed.netloc.endswith("youtu.be") and parts:
            return f"youtube:{parts[0]}"
        if "shorts" in parts:
            idx = parts.index("shorts")
            if idx + 1 < len(parts):
                return f"youtube:{parts[idx + 1]}"
    if platform == "x" and "status" in parts:
        idx = parts.index("status")
        if idx + 1 < len(parts):
            return f"x:{parts[idx + 1]}"
    if platform == "reddit" and "comments" in parts:
        idx = parts.index("comments")
        if idx + 1 < len(parts):
            return f"reddit:{parts[idx + 1]}"
    if platform == "facebook":
        query = urllib.parse.parse_qs(parsed.query)
        if query.get("story_fbid"):
            return f"facebook:{query['story_fbid'][0]}"
    return canonical


def _dedupe_key(platform: str, handle: str, post: dict[str, Any]) -> str:
    identity = _url_identity(platform, _text(post.get("url")))
    if not identity:
        identity = _text(post.get("source_id"), post.get("id"), post.get("short_code"))
    if not identity:
        digest_source = "|".join([_text(post.get("title")), _text(post.get("posted_at")), str(_int(post.get("views")))])
        identity = hashlib.sha1(digest_source.encode("utf-8")).hexdigest()[:16]
    return f"{platform}:{handle.lower()}:{identity}"


def _metric_sum(posts: list[dict[str, Any]], field: str) -> int:
    return sum(_int(post.get(field)) for post in posts)


def _post_instagram(item: dict[str, Any]) -> dict[str, Any]:
    url = _text(item.get("url"), item.get("shortCodeUrl"))
    short_code = _text(item.get("shortCode"), item.get("code"))
    if not url and short_code:
        url = f"https://www.instagram.com/p/{short_code}/"
    image_urls = _media_urls(item.get("childPosts")) or _media_urls(item.get("displayUrl")) or _media_urls(item.get("imageUrl"))
    video_url = _text(item.get("videoUrl"), _video_url(item))
    return {
        "source_id": _text(item.get("id"), short_code, url),
        "short_code": short_code,
        "title": _text(item.get("caption"), item.get("alt"), "Instagram 内容"),
        "url": url,
        "media_url": _text(item.get("displayUrl"), item.get("imageUrl"), video_url, image_urls[0] if image_urls else ""),
        "video_url": video_url,
        "image_urls": image_urls,
        "media_type": _text(item.get("type"), item.get("productType")),
        "posted_at": _text(item.get("timestamp"), item.get("createdAt")),
        "views": _int(_text(item.get("videoViewCount"), item.get("videoPlayCount"), item.get("viewCount"), item.get("views"))),
        "likes": _int(_text(item.get("likesCount"), item.get("likes"))),
        "comments": _int(_text(item.get("commentsCount"), item.get("comments"))),
        "shares": _int(_text(item.get("shareCount"), item.get("shares"))),
    }


def _post_tiktok(item: dict[str, Any]) -> dict[str, Any]:
    video_meta = item.get("videoMeta") if isinstance(item.get("videoMeta"), dict) else {}
    return {
        "source_id": _text(item.get("id"), item.get("webVideoUrl")),
        "short_code": "",
        "title": _text(item.get("text"), "TikTok 内容"),
        "url": _text(item.get("webVideoUrl"), item.get("url")),
        "media_url": _text(video_meta.get("coverUrl"), video_meta.get("originalCoverUrl"), item.get("coverUrl")),
        "media_type": "video",
        "posted_at": _text(item.get("createTimeISO"), item.get("createTime")),
        "views": _int(item.get("playCount")),
        "likes": _int(item.get("diggCount")),
        "comments": _int(item.get("commentCount")),
        "shares": _int(item.get("shareCount")),
    }


def _post_youtube(item: dict[str, Any]) -> dict[str, Any]:
    snippet = item.get("snippet") if isinstance(item.get("snippet"), dict) else {}
    stats = item.get("statistics") if isinstance(item.get("statistics"), dict) else {}
    thumbnails = snippet.get("thumbnails") if isinstance(snippet.get("thumbnails"), dict) else {}
    thumb = next((thumbnails[key] for key in ("maxres", "standard", "high", "medium", "default") if isinstance(thumbnails.get(key), dict)), {})
    video_id = _text(item.get("id"))
    if isinstance(item.get("id"), dict):
        video_id = _text(item["id"].get("videoId"))
    return {
        "source_id": video_id,
        "short_code": video_id,
        "title": _text(snippet.get("title"), "YouTube 内容"),
        "url": f"https://www.youtube.com/watch?v={video_id}" if video_id else "",
        "media_url": _text(thumb.get("url")),
        "media_type": "video",
        "posted_at": _text(snippet.get("publishedAt")),
        "views": _int(stats.get("viewCount")),
        "likes": _int(stats.get("likeCount")),
        "comments": _int(stats.get("commentCount")),
        "shares": 0,
    }


def _post_facebook(item: dict[str, Any]) -> dict[str, Any] | None:
    url = _text(item.get("url"), item.get("postUrl"), item.get("topLevelUrl"), item.get("facebookUrl"))
    if item.get("error") or not url:
        return None
    image_urls = _media_urls(item.get("media")) or _media_urls(item.get("image")) or _media_urls(item.get("picture"))
    video_url = _text(item.get("videoUrl"), _video_url(item.get("media")), _video_url(item))
    reaction_like = _int(item.get("reactionLikeCount"))
    reaction_love = _int(item.get("reactionLoveCount"))
    reaction_care = _int(item.get("reactionCareCount"))
    reaction_haha = _int(item.get("reactionHahaCount"))
    reaction_wow = _int(item.get("reactionWowCount"))
    reaction_sad = _int(item.get("reactionSadCount"))
    reaction_angry = _int(item.get("reactionAngryCount"))
    reaction_split_total = reaction_like + reaction_love + reaction_care + reaction_haha + reaction_wow + reaction_sad + reaction_angry
    reaction_total = max(_int(_text(item.get("topReactionsCount"), item.get("reactions"))), reaction_split_total)
    return {
        "source_id": _text(item.get("postId"), item.get("id"), url),
        "short_code": "",
        "title": _text(item.get("text"), item.get("message"), "Facebook 内容"),
        "url": url,
        "media_url": _text(item.get("thumbnailUrl"), item.get("picture"), item.get("image"), image_urls[0] if image_urls else ""),
        "video_url": video_url,
        "image_urls": image_urls[:12],
        "media_type": _text(item.get("type"), "post"),
        "posted_at": _text(item.get("time"), item.get("timestamp"), item.get("createdAt")),
        "views": _int(_text(item.get("views"), item.get("videoViews"), item.get("videoViewCount"), item.get("viewsCount"))),
        "likes": _int(_text(item.get("likes"), item.get("reactions"), item.get("topReactionsCount"))),
        "comments": _int(_text(item.get("comments"), item.get("commentsCount"))),
        "shares": _int(_text(item.get("shares"), item.get("sharesCount"))),
        "reaction_total": reaction_total,
        "reaction_like": reaction_like,
        "reaction_love": reaction_love,
        "reaction_care": reaction_care,
        "reaction_haha": reaction_haha,
        "reaction_wow": reaction_wow,
        "reaction_sad": reaction_sad,
        "reaction_angry": reaction_angry,
    }


def _post_reddit(item: dict[str, Any]) -> dict[str, Any] | None:
    data_type = str(item.get("dataType") or "").lower()
    if data_type in {"community", "subreddit", "comment"} or item.get("numberOfMembers"):
        return None
    url = _text(item.get("url"), item.get("permalink"))
    if not url:
        return None
    image_urls = _media_urls(item.get("images"))
    video_url = _text(item.get("videoUrl"), _video_url(item))
    return {
        "source_id": _text(item.get("id"), item.get("name"), url),
        "short_code": "",
        "title": _text(item.get("title"), item.get("body"), "Reddit 内容"),
        "url": url,
        "media_url": _text(item.get("thumbnailUrl"), image_urls[0] if image_urls else ""),
        "video_url": video_url,
        "image_urls": image_urls[:12],
        "media_type": _text(item.get("type"), "post"),
        "posted_at": _text(item.get("createdAt")),
        "views": _int(item.get("views")),
        "likes": _int(_text(item.get("upVotes"), item.get("score"), item.get("ups"))),
        "comments": _int(_text(item.get("numberOfComments"), item.get("num_comments"), item.get("comments"))),
        "shares": 0,
    }


def _post_x(item: dict[str, Any]) -> dict[str, Any] | None:
    url = _text(item.get("twitterUrl"), item.get("url"))
    source_id = _text(item.get("id"), url)
    if not source_id:
        return None
    image_urls = (
        _media_urls(item.get("extendedEntities"))
        or _media_urls(item.get("entities"))
        or _media_urls(item.get("media"))
        or _media_urls(item.get("photos"))
    )
    video_url = (
        _video_url(item.get("extendedEntities"))
        or _video_url(item.get("entities"))
        or _video_url(item.get("media"))
        or _video_url(item.get("video"))
    )
    return {
        "source_id": source_id,
        "short_code": "",
        "title": _text(item.get("fullText"), item.get("text"), "X 内容"),
        "url": url,
        "media_url": _text(image_urls[0] if image_urls else "", video_url),
        "video_url": video_url,
        "image_urls": image_urls[:12],
        "media_type": "video" if video_url else ("image" if image_urls else "post"),
        "posted_at": _text(item.get("createdAt")),
        "views": _int(_text(item.get("viewCount"), item.get("views"), _nested(item, "public_metrics", "impression_count"))),
        "likes": _int(_text(item.get("likeCount"), item.get("likes"), _nested(item, "public_metrics", "like_count"))),
        "comments": _int(_text(item.get("replyCount"), _nested(item, "public_metrics", "reply_count"))),
        "shares": _int(_text(item.get("retweetCount"), _nested(item, "public_metrics", "retweet_count"))),
    }


def _normalize_posts(platform: str, raw_posts: list[dict[str, Any]], handle: str) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    normalized: list[dict[str, Any]] = []
    seen: set[str] = set()
    duplicate_keys: list[str] = []
    for index, item in enumerate(raw_posts):
        post: dict[str, Any] | None
        if platform == "instagram":
            post = _post_instagram(item)
        elif platform == "tiktok":
            post = _post_tiktok(item)
        elif platform == "youtube":
            post = _post_youtube(item)
        elif platform == "facebook":
            post = _post_facebook(item)
        elif platform == "reddit":
            post = _post_reddit(item)
        elif platform == "x":
            post = _post_x(item)
        else:
            post = None
        if not post:
            continue
        key = _dedupe_key(platform, handle, post)
        if key in seen:
            duplicate_keys.append(key)
            continue
        seen.add(key)
        post.update({"row_index": index, "platform": platform, "account_handle": handle, "dedupe_key": key})
        normalized.append(post)
    return normalized, {"raw_count": len(raw_posts), "unique_count": len(normalized), "duplicate_count": len(duplicate_keys), "duplicate_keys": duplicate_keys[:200]}


def _previous_keys(root: Path, slug: str) -> set[str]:
    keys: set[str] = set()
    for path in (root / slug).glob("*/dedupe_keys.json"):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            continue
        if isinstance(payload, list):
            keys.update(str(item) for item in payload if item)
    return keys


def _write_json(path: Path, payload: Any) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=str), encoding="utf-8")


def _write_csv(path: Path, posts: list[dict[str, Any]]) -> None:
    fields = [
        "dedupe_key",
        "platform",
        "account_handle",
        "source_id",
        "short_code",
        "title",
        "url",
        "media_url",
        "video_url",
        "image_urls",
        "media_type",
        "posted_at",
        "views",
        "likes",
        "comments",
        "shares",
        "reaction_total",
        "reaction_like",
        "reaction_love",
        "reaction_care",
        "reaction_haha",
        "reaction_wow",
        "reaction_sad",
        "reaction_angry",
    ]
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for post in posts:
            writer.writerow({field: json.dumps(post.get(field), ensure_ascii=False) if isinstance(post.get(field), (list, dict)) else post.get(field, "") for field in fields})


def _channel(channel_id: int) -> dict[str, Any]:
    from app.db.connection import get_conn
    from app.services.vkpi.schema_channels import ensure_vkpi_channels_schema

    ensure_vkpi_channels_schema()
    row = get_conn().execute(
        "SELECT * FROM vkpi_employee_channels WHERE id=? AND deleted_at IS NULL",
        (int(channel_id),),
    ).fetchone()
    if not row:
        raise SystemExit(f"channel not found: {channel_id}")
    return dict(row)


def _current_snapshot(channel: dict[str, Any]) -> dict[str, Any]:
    from app.db.connection import get_conn

    row = get_conn().execute(
        """
        SELECT followers, posts_count, total_views
        FROM vkpi_channel_metrics
        WHERE channel_id=?
        ORDER BY snapshot_date DESC, captured_at DESC, id DESC
        LIMIT 1
        """,
        (int(channel.get("id") or 0),),
    ).fetchone()
    return {
        "display_name": _text(channel.get("account_display_name"), channel.get("account_handle")),
        "account_url": _text(channel.get("account_url")),
        "avatar_url": _text(channel.get("avatar_url")),
        "followers": _int(row["followers"]) if row else 0,
        "posts_count": _int(row["posts_count"]) if row else 0,
        "account_total_views": _int(row["total_views"]) if row else 0,
    }


def _crawl(channel: dict[str, Any], max_posts: int, timeout: int) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    platform = str(channel.get("platform") or "").lower()
    target = _text(channel.get("account_url"), channel.get("account_handle"))
    if platform == "instagram":
        from app.platform.industry_crawlers.instagram_crawler import InstagramCrawler

        crawler = InstagramCrawler(run_timeout_seconds=timeout)
        profile = crawler.crawl_channel_profile(target, max_posts=12)
        posts = crawler.crawl_channel_videos(target, max_results=max_posts)
        profile_item = (_items(profile.get("items")) or [{}])[0]
        raw_posts = _items(posts.get("items")) or _items(profile_item.get("latestPosts"))
        return {"profile": profile, "posts": posts}, raw_posts
    if platform == "tiktok":
        from app.platform.industry_crawlers.tiktok_crawler import TikTokCrawler

        result = TikTokCrawler(run_timeout_seconds=timeout).crawl_channel_profile(target, max_posts=max_posts)
        return {"profile": result}, _items(result.get("items"))
    if platform == "youtube":
        from app.platform.industry_crawlers.youtube_crawler import YouTubeCrawler

        crawler = YouTubeCrawler()
        profile = crawler.crawl_channel_profile(target)
        item = (_items(profile.get("items")) or [{}])[0]
        videos = crawler.crawl_channel_videos(_text(item.get("id")), max_results=max_posts)
        return {"profile": profile, "videos": videos}, _items(videos.get("items"))
    if platform == "facebook":
        from app.platform.industry_crawlers.facebook_crawler import FacebookCrawler

        result = FacebookCrawler().crawl_channel_profile(target, max_posts=max_posts)
        return {"profile": result}, _items(result.get("items"))
    if platform == "reddit":
        from app.platform.industry_crawlers.reddit_crawler import RedditCrawler

        result = RedditCrawler().crawl_channel_profile(target, max_posts=max_posts)
        return {"profile": result}, _items(result.get("items"))
    if platform == "x":
        from app.platform.industry_crawlers.x_crawler import XCrawler

        result = XCrawler(run_timeout_seconds=timeout).crawl_channel_profile(target, max_posts=max_posts)
        return {"profile": result}, _items(result.get("items"))
    raise SystemExit(f"unsupported platform: {platform}")


def _profile_summary(channel: dict[str, Any], raw: dict[str, Any], posts: list[dict[str, Any]]) -> dict[str, Any]:
    platform = str(channel.get("platform") or "").lower()
    current = _current_snapshot(channel)
    profile_items = _items((raw.get("profile") or {}).get("items"))
    profile = profile_items[0] if profile_items else {}
    if platform == "youtube" and profile:
        stats = profile.get("statistics") if isinstance(profile.get("statistics"), dict) else {}
        snippet = profile.get("snippet") if isinstance(profile.get("snippet"), dict) else {}
        return {"display_name": _text(snippet.get("title"), channel.get("account_display_name")), "followers": _int(stats.get("subscriberCount")), "posts_count": _int(stats.get("videoCount")), "account_total_views": _int(stats.get("viewCount"))}
    if platform == "instagram" and profile:
        return {"display_name": _text(profile.get("fullName"), profile.get("username"), channel.get("account_display_name")), "followers": _int(profile.get("followersCount")), "posts_count": _int(profile.get("postsCount")), "account_total_views": 0}
    if platform == "tiktok" and posts:
        author = posts[0].get("authorMeta") if isinstance(posts[0].get("authorMeta"), dict) else {}
        return {"display_name": _text(author.get("nickName"), channel.get("account_display_name")), "followers": _int(author.get("fans")), "posts_count": len(posts), "account_total_views": 0}
    if platform == "facebook" and profile:
        return {
            "display_name": _text(profile.get("title"), profile.get("pageName"), current.get("display_name")),
            "account_url": _text(profile.get("pageUrl"), profile.get("facebookUrl"), current.get("account_url")),
            "avatar_url": _text(profile.get("profilePictureUrl"), profile.get("profilePhoto"), current.get("avatar_url")),
            "followers": _int(profile.get("followers")) or _int(current.get("followers")),
            "posts_count": len(posts) or _int(current.get("posts_count")),
            "account_total_views": _int(current.get("account_total_views")),
        }
    if platform == "reddit" and profile:
        followers = _int(profile.get("subscribers")) or _int(profile.get("numberOfMembers")) or _int(current.get("followers"))
        return {
            "display_name": _text(profile.get("title"), profile.get("display_name"), current.get("display_name")),
            "account_url": _text(profile.get("url"), current.get("account_url")),
            "avatar_url": _text(profile.get("iconUrl"), current.get("avatar_url")),
            "followers": followers,
            "posts_count": len(posts) or _int(current.get("posts_count")),
            "account_total_views": 0,
        }
    if platform == "x" and profile:
        author = profile.get("author") if isinstance(profile.get("author"), dict) else {}
        public_metrics = profile.get("public_metrics") if isinstance(profile.get("public_metrics"), dict) else {}
        handle = _text(profile.get("username"), author.get("userName"), author.get("username"), channel.get("account_handle"))
        account_url = _text(current.get("account_url"), f"https://x.com/{handle.lstrip('@')}" if handle else "")
        return {
            "display_name": _text(profile.get("name"), author.get("name"), current.get("display_name")),
            "account_url": account_url,
            "avatar_url": _text(profile.get("profile_image_url"), profile.get("profilePicture"), author.get("profilePicture"), current.get("avatar_url")),
            "followers": _int(public_metrics.get("followers_count")) or _int(author.get("followers")) or _int(profile.get("followers")) or _int(current.get("followers")),
            "posts_count": len(posts) or _int(public_metrics.get("tweet_count")) or _int(current.get("posts_count")),
            "account_total_views": _int(current.get("account_total_views")),
        }
    return {
        "display_name": current.get("display_name"),
        "account_url": current.get("account_url"),
        "avatar_url": current.get("avatar_url"),
        "followers": _int(current.get("followers")),
        "posts_count": len(posts) or _int(current.get("posts_count")),
        "account_total_views": _int(current.get("account_total_views")),
    }


def _write_snapshot(channel: dict[str, Any], summary: dict[str, Any], posts: list[dict[str, Any]], package_dir: Path) -> None:
    from app.services.vkpi.channel_refill import _write_snapshot as write_snapshot

    profile = summary.get("profile") if isinstance(summary.get("profile"), dict) else {}
    total_views = max(_int(profile.get("account_total_views")), _int(summary.get("totals", {}).get("views")))
    metrics = {
        "display_name": profile.get("display_name") or channel.get("account_display_name"),
        "account_url": profile.get("account_url") or channel.get("account_url"),
        "avatar_url": profile.get("avatar_url") or channel.get("avatar_url"),
        "followers": profile.get("followers") or 0,
        "posts_count": profile.get("posts_count") or len(posts),
        "total_views": total_views,
        "total_likes": summary.get("totals", {}).get("likes") or 0,
        "total_comments": summary.get("totals", {}).get("comments") or 0,
        "total_shares": summary.get("totals", {}).get("shares") or 0,
    }
    write_snapshot(
        channel,
        metrics,
        {
            "provider": f"deep_dive_{channel.get('platform')}",
            "package_dir": str(package_dir),
            "raw_sample": {"posts": posts[:1000]},
            "quality": summary.get("quality", {}),
        },
        staff={"id": 1, "role": "admin"},
    )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--channel-id", type=int, required=True)
    parser.add_argument("--max-posts", type=int, default=500)
    parser.add_argument("--timeout", type=int, default=900)
    parser.add_argument("--out-dir", default=str(ROOT / "tmp" / "vkpi_channel_packages"))
    parser.add_argument("--write-snapshot", action="store_true")
    args = parser.parse_args()

    from app.db.connection import close_db_runtime

    try:
        channel = _channel(args.channel_id)
        platform = str(channel.get("platform") or "").lower()
        handle = str(channel.get("account_handle") or "").strip().lstrip("@")
        package_root = Path(args.out_dir)
        account_slug = _slug(f"{platform}_{handle}")
        package_dir = package_root / account_slug / _utc_stamp()
        package_dir.mkdir(parents=True, exist_ok=True)

        previous = _previous_keys(package_root, account_slug)
        raw, raw_posts = _crawl(channel, max(1, int(args.max_posts or 1)), max(30, int(args.timeout or 900)))
        posts, dedupe = _normalize_posts(platform, raw_posts, handle)
        overlap = [post["dedupe_key"] for post in posts if post["dedupe_key"] in previous]
        profile = _profile_summary(channel, raw, raw_posts)
        if platform == "reddit":
            profile["posts_count"] = len(posts)
            profile["raw_items_count"] = len(raw_posts)
            profile["raw_comments_count"] = sum(1 for item in raw_posts if str(item.get("dataType") or "").lower() == "comment")
        totals = {
            "views": _metric_sum(posts, "views"),
            "likes": _metric_sum(posts, "likes"),
            "comments": _metric_sum(posts, "comments"),
            "shares": _metric_sum(posts, "shares"),
        }
        summary = {
            "channel_id": int(channel.get("id") or 0),
            "platform": platform,
            "account_handle": handle,
            "account_display_name": _text(channel.get("account_display_name"), handle),
            "account_url": _text(channel.get("account_url")),
            "captured_at": datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ"),
            "requested_max_posts": int(args.max_posts or 0),
            "profile": profile,
            "totals": totals,
            "quality": {
                "raw_posts": dedupe["raw_count"],
                "unique_posts": dedupe["unique_count"],
                "duplicates_removed": dedupe["duplicate_count"],
                "previous_package_overlap": len(overlap),
                "posts_with_url": sum(1 for post in posts if post.get("url")),
                "posts_with_views": sum(1 for post in posts if _int(post.get("views"))),
                "posts_with_engagement": sum(1 for post in posts if _int(post.get("likes")) or _int(post.get("comments")) or _int(post.get("shares"))),
            },
            "files": {
                "raw": str(package_dir / "raw.json"),
                "posts_csv": str(package_dir / "posts.csv"),
                "summary": str(package_dir / "summary.json"),
                "dedupe_report": str(package_dir / "dedupe_report.json"),
            },
        }

        _write_json(package_dir / "raw.json", raw)
        _write_json(package_dir / "summary.json", summary)
        _write_json(package_dir / "dedupe_report.json", {**dedupe, "previous_package_overlap": overlap[:200]})
        _write_json(package_dir / "dedupe_keys.json", [post["dedupe_key"] for post in posts])
        _write_csv(package_dir / "posts.csv", posts)
        if args.write_snapshot:
            _write_snapshot(channel, summary, posts, package_dir)
            summary["snapshot_written"] = True
            _write_json(package_dir / "summary.json", summary)
        print(json.dumps(summary, ensure_ascii=False, indent=2, default=str))
    finally:
        asyncio.run(close_db_runtime())


if __name__ == "__main__":
    main()
