"""
services/intelligence/account_scan_service.py — 账号扫描服务

把矩阵扫描的重逻辑从 router 剥离出来，便于:
1. Web 直接同步调用
2. Worker 后台异步调用
3. 后续拆成独立 sidecar
"""
from __future__ import annotations

import asyncio
import sys
import time
from typing import Any, Awaitable, Callable, Dict, List

from app.core.logging import get_logger

logger = get_logger(__name__)


def _client():
    module = sys.modules.get("app.services.scraping.apify")
    return getattr(module, "_client", None) if module else None


async def _run_actor(actor_id: str, payload: Dict[str, Any], timeout: int = 600) -> List[Dict[str, Any]]:
    client = _client()
    if not client:
        logger.warning("scanner.client_missing")
        return []

    def go() -> List[Dict[str, Any]]:
        logger.info("scanner.actor_started", extra={"actor_id": actor_id})
        run = client.actor(actor_id).call(run_input=payload, timeout_secs=timeout)
        items = list(client.dataset(run["defaultDatasetId"]).iterate_items())
        logger.info("scanner.actor_complete", extra={"actor_id": actor_id, "item_count": len(items)})
        return items

    try:
        return await asyncio.to_thread(go)
    except Exception as exc:
        logger.warning("scanner.actor_failed", extra={"actor_id": actor_id, "error": str(exc)})
        return []


def _build_scan_result(platform: str, handle: str, posts: List[Dict[str, Any]], duration_sec: float) -> Dict[str, Any]:
    return {
        "platform": platform,
        "handle": handle,
        "posts": posts,
        "stats": {
            "total_posts": len(posts),
            "total_views": sum(item.get("views", 0) for item in posts),
            "total_likes": sum(item.get("likes", 0) for item in posts),
            "total_comments": sum(item.get("comments", 0) for item in posts),
        },
        "duration_sec": round(duration_sec, 1),
    }


async def scan_instagram_account(handle: str, max_posts: int = 1000) -> Dict[str, Any]:
    normalized = handle.lstrip("@").split("/")[-1].strip()
    started_at = time.time()
    url = f"https://www.instagram.com/{normalized}/"
    logger.info("scanner.instagram_scan_started", extra={"url": url, "max_posts": max_posts})
    posts = await _run_actor(
        "apify/instagram-scraper",
        {
            "directUrls": [url],
            "resultsType": "posts",
            "resultsLimit": max_posts,
        },
    )
    posts = [post for post in posts if (post.get("ownerUsername", "").lower() == normalized.lower())]
    return _build_scan_result(
        "instagram",
        normalized,
        [
            {
                "title": (post.get("caption") or "")[:300],
                "url": post.get("url", ""),
                "thumbnail": post.get("displayUrl", ""),
                "views": post.get("videoViewCount") or post.get("videoPlayCount") or 0,
                "likes": post.get("likesCount", 0),
                "comments": post.get("commentsCount", 0),
                "published": post.get("timestamp", ""),
                "type": "video" if post.get("isVideo") else "image",
                "channel": normalized,
            }
            for post in posts
        ],
        time.time() - started_at,
    )


async def scan_tiktok_account(handle: str, max_posts: int = 1000) -> Dict[str, Any]:
    normalized = handle.lstrip("@").strip()
    started_at = time.time()
    logger.info("scanner.tiktok_scan_started", extra={"handle": normalized, "max_posts": max_posts})
    videos = await _run_actor(
        "clockworks/free-tiktok-scraper",
        {
            "profiles": [normalized],
            "resultsPerPage": max_posts,
            "shouldDownloadVideos": False,
            "shouldDownloadCovers": False,
        },
    )
    return _build_scan_result(
        "tiktok",
        normalized,
        [
            {
                "title": (video.get("text") or "")[:300],
                "url": video.get("webVideoUrl", ""),
                "views": video.get("playCount") or video.get("stats", {}).get("playCount", 0),
                "likes": video.get("diggCount") or video.get("stats", {}).get("diggCount", 0),
                "comments": video.get("commentCount") or video.get("stats", {}).get("commentCount", 0),
                "shares": video.get("shareCount") or video.get("stats", {}).get("shareCount", 0),
                "published": video.get("createTimeISO", ""),
                "type": "video",
                "channel": normalized,
            }
            for video in videos
        ],
        time.time() - started_at,
    )


async def scan_youtube_account(handle: str, max_posts: int = 1000) -> Dict[str, Any]:
    normalized = handle.lstrip("@").strip()
    started_at = time.time()
    channel_url = f"https://www.youtube.com/@{normalized}/videos"
    logger.info("scanner.youtube_scan_started", extra={"channel_url": channel_url, "max_posts": max_posts})
    videos = await _run_actor(
        "streamers/youtube-scraper",
        {
            "startUrls": [{"url": channel_url}],
            "maxResults": max_posts,
            "maxResultsShorts": 0,
        },
    )
    if not videos:
        logger.info("scanner.youtube_fallback_search", extra={"handle": normalized, "max_posts": max_posts})
        videos = await _run_actor(
            "streamers/youtube-scraper",
            {
                "searchKeywords": f"viltrox {normalized}",
                "maxResults": max_posts,
            },
        )
    return _build_scan_result(
        "youtube",
        normalized,
        [
            {
                "title": (video.get("title") or "")[:300],
                "url": video.get("url", ""),
                "thumbnail": video.get("thumbnailUrl") or video.get("thumbnail", ""),
                "views": video.get("viewCount") or video.get("views", 0),
                "likes": video.get("likes", 0),
                "comments": video.get("commentsCount") or video.get("comments", 0),
                "published": video.get("date") or video.get("uploadDate") or video.get("published", ""),
                "type": "video",
                "channel": video.get("channelName") or normalized,
            }
            for video in videos
        ],
        time.time() - started_at,
    )


async def scan_facebook_account(handle: str, max_posts: int = 1000) -> Dict[str, Any]:
    normalized = handle.strip()
    started_at = time.time()
    if "profile.php" in normalized or "facebook.com" in normalized:
        url = normalized if normalized.startswith("http") else f"https://www.facebook.com/{normalized}"
    else:
        url = f"https://www.facebook.com/{normalized}/"
    logger.info("scanner.facebook_scan_started", extra={"url": url, "max_posts": max_posts})
    posts = await _run_actor(
        "apify/facebook-posts-scraper",
        {
            "startUrls": [{"url": url}],
            "resultsLimit": max_posts,
        },
    )
    return _build_scan_result(
        "facebook",
        normalized,
        [
            {
                "title": (post.get("text") or post.get("message") or "")[:300],
                "url": post.get("url") or post.get("postUrl", ""),
                "views": post.get("viewsCount", 0),
                "likes": post.get("likesCount") or post.get("reactionsCount", 0),
                "comments": post.get("commentsCount", 0),
                "shares": post.get("sharesCount", 0),
                "published": post.get("time") or post.get("timestamp", ""),
                "type": "post",
                "channel": normalized,
            }
            for post in posts
        ],
        time.time() - started_at,
    )


SCANNERS: Dict[str, Callable[[str, int], Awaitable[Dict[str, Any]]]] = {
    "instagram": scan_instagram_account,
    "tiktok": scan_tiktok_account,
    "youtube": scan_youtube_account,
    "facebook": scan_facebook_account,
}


async def scan_account(platform: str, handle: str, max_posts: int = 1000) -> Dict[str, Any]:
    normalized_platform = (platform or "").lower()
    normalized_handle = (handle or "").strip()
    scanner = SCANNERS.get(normalized_platform)
    if not scanner:
        return _build_scan_result(normalized_platform, normalized_handle, [], 0)
    return await scanner(normalized_handle, max_posts)


async def scan_matrix(accounts: List[Dict[str, Any]], max_posts_per_account: int = 1000) -> Dict[str, Any]:
    results: List[Dict[str, Any]] = []
    for index, account in enumerate(accounts):
        platform = (account.get("platform") or "").lower()
        handle = account.get("handle", "")
        name = account.get("name", "")
        logger.info(
            "scanner.matrix_account_started",
            extra={"index": index + 1, "total": len(accounts), "platform": platform, "name": name},
        )
        try:
            result = await scan_account(platform, handle, max_posts_per_account)
            result["account_name"] = name
            results.append(result)
        except Exception as exc:
            results.append(
                {
                    "platform": platform,
                    "handle": handle,
                    "account_name": name,
                    "posts": [],
                    "stats": {"total_posts": 0},
                    "error": str(exc),
                }
            )

    aggregate = {
        "total_posts": sum(len(result.get("posts", [])) for result in results),
        "total_views": sum(result.get("stats", {}).get("total_views", 0) for result in results),
        "total_likes": sum(result.get("stats", {}).get("total_likes", 0) for result in results),
        "total_comments": sum(result.get("stats", {}).get("total_comments", 0) for result in results),
    }
    return {
        "scanned": len(results),
        "total": len(accounts),
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "aggregate": aggregate,
        "results": results,
    }
