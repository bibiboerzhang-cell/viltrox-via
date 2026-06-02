"""
services/scraping/apify.py — Apify scraping integration
========================================================
Replaces Playwright/yt-dlp metadata fetch with Apify Actors for:
- Instagram (Reels, Posts)
- TikTok
- YouTube (metadata only — Gemini handles video analysis directly)
"""
from __future__ import annotations

import os
import asyncio
from typing import Any, Dict

from app.core.logging import get_logger

try:
    from apify_client import ApifyClient
    APIFY_AVAILABLE = True
except ImportError:
    APIFY_AVAILABLE = False
    ApifyClient = None

APIFY_TOKEN = os.getenv("APIFY_TOKEN", "")
logger = get_logger(__name__)

if APIFY_AVAILABLE and APIFY_TOKEN:
    _client = ApifyClient(APIFY_TOKEN)
    logger.info("apify.client_initialized")
else:
    _client = None
    if not APIFY_TOKEN:
        logger.warning("apify.disabled_missing_token")
    if not APIFY_AVAILABLE:
        logger.warning("apify.disabled_missing_client")


def _empty_result(error: str = "") -> Dict[str, Any]:
    return {
        "scraped_ok": False,
        "title": "",
        "caption": "",
        "scraped_text": "",
        "og_image": "",
        "metrics": {"views": 0, "likes": 0, "comments": 0, "shares": 0, "favorites": 0},
        "metrics_available": {"views": False, "likes": False, "comments": False,
                              "shares": False, "favorites": False},
        "visible_comments": [],
        "published_at": None,
        "video_url": "",
        "error": error,
        "scraper": "apify",
    }


def _apify_available() -> bool:
    return _client is not None


def _int(value: Any) -> int:
    try:
        return int(float(str(value or "0").replace(",", "")))
    except (TypeError, ValueError):
        return 0


def _first_nested_int(item: dict[str, Any], keys: tuple[str, ...]) -> int:
    wanted = {k.lower() for k in keys}
    stack: list[Any] = [item]
    while stack:
        current = stack.pop()
        if isinstance(current, dict):
            for key, value in current.items():
                if str(key).lower() in wanted:
                    parsed = _int(value)
                    if parsed:
                        return parsed
                if isinstance(value, (dict, list)):
                    stack.append(value)
        elif isinstance(current, list):
            stack.extend(current[:20])
    return 0


def _first_path(source: dict[str, Any], *paths: str) -> Any:
    for path in paths:
        current: Any = source
        found = True
        for part in path.split("."):
            if isinstance(current, dict) and part in current:
                current = current[part]
            else:
                found = False
                break
        if found and current not in (None, ""):
            return current
    return ""


def _first_video_url(item: dict[str, Any]) -> str:
    direct = _first_path(
        item,
        "videoUrl",
        "downloadAddr",
        "downloadUrl",
        "videoUrlNoWaterMark",
        "video.playAddr",
        "video.downloadAddr",
        "media.videoUrl",
    )
    if direct:
        return str(direct).strip()
    medias = item.get("medias") if isinstance(item.get("medias"), list) else []
    for media in medias:
        if not isinstance(media, dict):
            continue
        url = media.get("url") or media.get("videoUrl") or media.get("downloadUrl")
        if url:
            return str(url).strip()
    return ""


def _tiktok_actor_id() -> str:
    return (os.getenv("APIFY_TIKTOK_ACTOR_ID", "").strip() or "clockworks/tiktok-scraper")


def _douyin_actor_id(kind: str) -> str:
    specific = os.getenv(f"APIFY_DOUYIN_{kind.upper()}_ACTOR_ID", "").strip()
    return specific or os.getenv("APIFY_DOUYIN_ACTOR_ID", "").strip()


def _actor_slug(actor_id: str) -> str:
    return str(actor_id or "").strip().lower()


def _douyin_video_payload(actor_id: str, url: str) -> Dict[str, Any]:
    actor = _actor_slug(actor_id)
    if actor == "apple_yang/douyin-video-audio-downloader":
        return {"videoUrls": [url]}
    return {
        "url": url,
        "urls": [url],
        "videoUrls": [url],
        "startUrls": [{"url": url}],
        "maxItems": 1,
        "maxResults": 1,
        "limit": 1,
        "shouldDownloadVideos": False,
        "shouldDownloadCovers": False,
        "proxyConfiguration": {"useApifyProxy": True},
    }


def _douyin_comments_payload(actor_id: str, url: str, max_comments: int = 20) -> Dict[str, Any]:
    actor = _actor_slug(actor_id)
    if actor == "natanielsantos/douyin-comments-scraper":
        return {"postUrls": [url], "maxComments": max(1, min(int(max_comments), 100))}
    return {
        "url": url,
        "urls": [url],
        "postUrls": [url],
        "videoUrls": [url],
        "maxComments": max(1, min(int(max_comments), 100)),
        "maxItems": max(1, min(int(max_comments), 100)),
    }


def _douyin_metrics_payload(actor_id: str, url: str) -> Dict[str, Any]:
    actor = _actor_slug(actor_id)
    if actor == "openclawai/tiktok-douyin-bilibili-scraper":
        return {
            "mode": "video_detail",
            "platform": "douyin",
            "url": url,
            "urls": [url],
            "maxItems": 1,
            "downloadVideos": False,
            "includeComments": False,
            "proxyConfiguration": {"useApifyProxy": True, "apifyProxyGroups": ["RESIDENTIAL"]},
        }
    return {
        "url": url,
        "urls": [url],
        "videoUrls": [url],
        "postUrls": [url],
        "startUrls": [{"url": url}],
        "maxItems": 1,
        "maxResults": 1,
        "limit": 1,
        "proxyConfiguration": {"useApifyProxy": True},
    }


def _normalize_douyin_comments(items: list[dict]) -> list[dict]:
    comments = []
    for item in items[:100]:
        text = str(item.get("text") or item.get("comment") or item.get("message") or "").strip()
        if not text:
            continue
        comments.append(
            {
                "text": text[:1000],
                "author": str(item.get("nickname") or item.get("author") or item.get("username") or ""),
                "likes": _int(item.get("diggCount") or item.get("likeCount") or item.get("likes")),
                "published": item.get("createTimeISO") or item.get("createTime") or "",
            }
        )
    return comments


async def _fetch_douyin_comments(url: str, max_comments: int = 20) -> list[dict]:
    actor_id = _douyin_actor_id("comments")
    if not actor_id or not _apify_available():
        return []
    try:
        run_input = _douyin_comments_payload(actor_id, url, max_comments)
        run = await asyncio.to_thread(lambda: _client.actor(actor_id).call(run_input=run_input))
        items = list(_client.dataset(run["defaultDatasetId"]).iterate_items())
        return _normalize_douyin_comments(items)
    except Exception as e:
        logger.warning("apify.scrape_douyin.comments_failed | url=%s | actor=%s | error=%s", url, actor_id, e)
        return []


async def _fetch_douyin_metrics(url: str) -> dict[str, int]:
    actor_id = _douyin_actor_id("metrics") or _douyin_actor_id("detail") or _douyin_actor_id("analytics")
    if not actor_id or not _apify_available():
        return {}
    try:
        run_input = _douyin_metrics_payload(actor_id, url)
        run = await asyncio.to_thread(lambda: _client.actor(actor_id).call(run_input=run_input))
        items = list(_client.dataset(run["defaultDatasetId"]).iterate_items())
        if not items:
            return {}
        item = items[0]
        if isinstance(item.get("result"), dict):
            item = item["result"]
        return {
            "views": _first_nested_int(item, ("playCount", "play_count", "viewCount", "view_count", "views", "play", "plays")),
            "likes": _first_nested_int(item, ("diggCount", "likeCount", "like_count", "likes", "digg_count")),
            "comments": _first_nested_int(item, ("commentCount", "comment_count", "comments")),
            "shares": _first_nested_int(item, ("shareCount", "share_count", "shares")),
            "favorites": _first_nested_int(item, ("collectCount", "collect_count", "favorites", "favoriteCount")),
        }
    except Exception as e:
        logger.warning("apify.scrape_douyin.metrics_failed | url=%s | actor=%s | error=%s", url, actor_id, e)
        return {}


async def scrape_youtube(url: str) -> Dict[str, Any]:
    """Fetch YouTube metadata via Apify (no video download)."""
    if not _apify_available():
        return _empty_result("apify not available")

    logger.info("apify.scrape_youtube.start | url=%s", url)

    try:
        run_input = {
            "startUrls": [{"url": url}],
            "maxResults": 1,
            "maxResultsShorts": 0,
            "maxResultStreams": 0,
        }

        run = await asyncio.to_thread(
            lambda: _client.actor("streamers/youtube-scraper").call(run_input=run_input)
        )

        items = list(_client.dataset(run["defaultDatasetId"]).iterate_items())
        if not items:
            return _empty_result("apify returned no items")

        item = items[0]

        video = item.get("video") if isinstance(item.get("video"), dict) else {}
        return {
            "scraped_ok": True,
            "title": item.get("title", ""),
            "caption": item.get("text", "") or "",
            "scraped_text": item.get("text", "") or "",
            "og_image": item.get("thumbnailUrl", "") or "",
            "metrics": {
                "views": int(item.get("viewCount") or 0),
                "likes": int(item.get("likes") or 0),
                "comments": int(item.get("commentsCount") or 0),
                "shares": 0,
                "favorites": 0,
            },
            "metrics_available": {
                "views": "viewCount" in item,
                "likes": "likes" in item,
                "comments": "commentsCount" in item,
                "shares": False,
                "favorites": False,
            },
            "visible_comments": [],
            "published_at": item.get("date") or None,
            "video_url": "",
            "channel_name": item.get("channelName", ""),
            "channel_url": item.get("channelUrl", ""),
            "subscriber_count": int(item.get("numberOfSubscribers") or 0),
            "duration": item.get("duration", ""),
            "hashtags": item.get("hashtags", []),
            "subtitles": item.get("subtitles", []),
            "error": None,
            "scraper": "apify_youtube",
        }
    except Exception as e:
        logger.warning("apify.scrape_youtube.failed | url=%s | error=%s", url, e)
        return _empty_result(f"apify YouTube error: {e}")


async def scrape_instagram(url: str) -> Dict[str, Any]:
    """Fetch Instagram Reel/Post via Apify."""
    if not _apify_available():
        return _empty_result("apify not available")

    logger.info("apify.scrape_instagram.start | url=%s", url)

    try:
        run_input = {
            "directUrls": [url],
            "resultsLimit": 1,
            "addParentData": False,
        }

        run = await asyncio.to_thread(
            lambda: _client.actor("apify/instagram-scraper").call(run_input=run_input)
        )

        items = list(_client.dataset(run["defaultDatasetId"]).iterate_items())
        if not items:
            return _empty_result("apify returned no items")

        item = items[0]

        likes = int(item.get("likesCount") or 0)
        comments = int(item.get("commentsCount") or 0)
        views = int(item.get("videoPlayCount") or item.get("videoViewCount") or 0)

        return {
            "scraped_ok": True,
            "title": (item.get("caption", "") or "")[:200],
            "caption": item.get("caption", "") or "",
            "scraped_text": item.get("caption", "") or "",
            "og_image": item.get("displayUrl", "") or "",
            "metrics": {
                "views": views,
                "likes": likes,
                "comments": comments,
                "shares": 0,
                "favorites": 0,
            },
            "metrics_available": {
                "views": views > 0,
                "likes": likes > 0,
                "comments": comments > 0,
                "shares": False,
                "favorites": False,
            },
            "visible_comments": [],
            "published_at": item.get("timestamp") or None,
            "video_url": item.get("videoUrl", "") or "",
            "owner_username": item.get("ownerUsername", "") or "",
            "owner_full_name": item.get("ownerFullName", "") or "",
            "duration": item.get("videoDuration", 0),
            "hashtags": item.get("hashtags", []) or [],
            "error": None,
            "scraper": "apify_instagram",
        }
    except Exception as e:
        logger.warning("apify.scrape_instagram.failed | url=%s | error=%s", url, e)
        return _empty_result(f"apify Instagram error: {e}")


async def scrape_tiktok(url: str) -> Dict[str, Any]:
    """Fetch TikTok video via Apify."""
    if not _apify_available():
        return _empty_result("apify not available")

    logger.info("apify.scrape_tiktok.start | url=%s", url)

    try:
        run_input = {
            "postURLs": [url],
            "resultsPerPage": 1,
            "shouldDownloadVideos": False,
            "shouldDownloadCovers": False,
            "shouldDownloadSubtitles": False,
        }

        actor_id = _tiktok_actor_id()
        run = await asyncio.to_thread(lambda: _client.actor(actor_id).call(run_input=run_input))

        items = list(_client.dataset(run["defaultDatasetId"]).iterate_items())
        if not items:
            return _empty_result("apify returned no items")

        item = items[0]

        plays = int(item.get("playCount") or 0)
        likes = int(item.get("diggCount") or 0)
        comments = int(item.get("commentCount") or 0)
        shares = int(item.get("shareCount") or 0)

        author = item.get("authorMeta", {}) or {}
        video = item.get("videoMeta", {}) or {}

        covers = item.get("covers", []) or []
        cover_url = covers[0] if covers else ""

        return {
            "scraped_ok": True,
            "title": (item.get("text", "") or "")[:200],
            "caption": item.get("text", "") or "",
            "scraped_text": item.get("text", "") or "",
            "og_image": cover_url,
            "metrics": {
                "views": plays,
                "likes": likes,
                "comments": comments,
                "shares": shares,
                "favorites": 0,
            },
            "metrics_available": {
                "views": plays > 0,
                "likes": likes > 0,
                "comments": comments > 0,
                "shares": shares > 0,
                "favorites": False,
            },
            "visible_comments": [],
            "published_at": item.get("createTimeISO") or None,
            "video_url": _first_video_url(item),
            "owner_username": author.get("name", "") or "",
            "owner_full_name": author.get("nickName", "") or "",
            "duration": video.get("duration", 0),
            "hashtags": [h.get("name", "") for h in (item.get("hashtags") or [])],
            "music_name": (item.get("musicMeta") or {}).get("musicName", ""),
            "error": None,
            "scraper": "apify_tiktok",
            "actor_id": actor_id,
        }
    except Exception as e:
        logger.warning("apify.scrape_tiktok.failed | url=%s | error=%s", url, e)
        return _empty_result(f"apify TikTok error: {e}")


async def scrape_douyin(url: str) -> Dict[str, Any]:
    """Best-effort Douyin scrape via a configured Apify actor.

    Douyin actors are not standardized like the TikTok/YouTube actors. This
    function only runs when APIFY_DOUYIN_VIDEO_ACTOR_ID or APIFY_DOUYIN_ACTOR_ID
    is configured, and normalizes whatever public fields the actor returns.
    """
    if not _apify_available():
        return _empty_result("apify not available")
    actor_id = _douyin_actor_id("video")
    if not actor_id:
        return _empty_result("APIFY_DOUYIN_VIDEO_ACTOR_ID or APIFY_DOUYIN_ACTOR_ID is not configured")

    logger.info("apify.scrape_douyin.start | url=%s | actor=%s", url, actor_id)
    try:
        run_input = _douyin_video_payload(actor_id, url)
        run = await asyncio.to_thread(lambda: _client.actor(actor_id).call(run_input=run_input))
        items = list(_client.dataset(run["defaultDatasetId"]).iterate_items())
        if not items:
            return _empty_result("apify returned no items")
        item = items[0]
        if isinstance(item.get("result"), dict):
            item = item["result"]
        author = item.get("author") if isinstance(item.get("author"), dict) else {}
        stats = item.get("statistics") if isinstance(item.get("statistics"), dict) else {}
        if isinstance(item.get("stats"), dict):
            stats = {**stats, **item["stats"]}
        title = str(item.get("desc") or item.get("description") or item.get("text") or item.get("title") or "")
        views = _first_nested_int(item, ("playCount", "play_count", "viewCount", "view_count", "views", "play", "plays"))
        likes = _first_nested_int(item, ("diggCount", "likeCount", "like_count", "likes", "digg_count"))
        comments = _first_nested_int(item, ("commentCount", "comment_count", "comments"))
        shares = _first_nested_int(item, ("shareCount", "share_count", "shares"))
        favorites = _first_nested_int(item, ("collectCount", "collect_count", "favorites", "favoriteCount"))
        if not views:
            detail_metrics = await _fetch_douyin_metrics(url)
            views = detail_metrics.get("views") or views
            likes = detail_metrics.get("likes") or likes
            comments = detail_metrics.get("comments") or comments
            shares = detail_metrics.get("shares") or shares
            favorites = detail_metrics.get("favorites") or favorites
        video = item.get("video") if isinstance(item.get("video"), dict) else {}
        visible_comments = item.get("comments") if isinstance(item.get("comments"), list) else []
        if not visible_comments:
            visible_comments = await _fetch_douyin_comments(url, max_comments=20)
        video_url = str(item.get("videoUrl") or item.get("downloadUrl") or item.get("downloadAddr") or "")
        if not video_url:
            medias = item.get("medias") if isinstance(item.get("medias"), list) else []
            for media in medias:
                if isinstance(media, dict) and str(media.get("type") or "").lower() == "video" and media.get("url"):
                    video_url = str(media.get("url") or "")
                    break
        owner_username = str(item.get("unique_id") or item.get("authorUniqueId") or author.get("uniqueId") or author.get("secUid") or author.get("uid") or "")
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
        owner_url = f"https://www.douyin.com/user/{owner_username}" if owner_username else ""
        return {
            "scraped_ok": True,
            "title": title[:200],
            "caption": title,
            "scraped_text": title,
            "og_image": str(item.get("thumbnail") or item.get("cover") or item.get("coverUrl") or item.get("dynamicCover") or ""),
            "metrics": {"views": views, "likes": likes, "comments": comments, "shares": shares, "favorites": favorites},
            "metrics_available": {"views": views > 0, "likes": likes > 0, "comments": comments > 0, "shares": shares > 0, "favorites": favorites > 0},
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
            "avatar_url": str(item.get("avatarUri") or item.get("avatarUrl") or author.get("avatarThumb") or ""),
            "follower_count": _first_nested_int(item, ("followerCount", "follower_count", "followers", "fansCount")),
            "total_favorited": _first_nested_int(item, ("totalFavorited", "total_favorited")),
            "duration": item.get("duration") or video.get("duration") or 0,
            "hashtags": item.get("hashtags") if isinstance(item.get("hashtags"), list) else [],
            "error": None,
            "scraper": "apify_douyin",
            "metrics_source": {"views": "apify_douyin" if views > 0 else "unavailable"},
        }
    except Exception as e:
        logger.warning("apify.scrape_douyin.failed | url=%s | error=%s", url, e)
        return _empty_result(f"apify Douyin error: {e}")


async def scrape_with_apify(url: str, platform: str) -> Dict[str, Any]:
    """Unified Apify scrape entry. Routes to platform-specific actor."""
    if not _apify_available():
        return _empty_result("apify not available (check APIFY_TOKEN)")

    p = platform.lower()
    if p == "youtube":
        return await scrape_youtube(url)
    elif p == "instagram":
        return await scrape_instagram(url)
    elif p == "tiktok":
        return await scrape_tiktok(url)
    elif p == "douyin":
        return await scrape_douyin(url)
    else:
        return _empty_result(f"apify: platform {platform} not yet supported")
