"""
services/intelligence/account_scan_service.py — 账号扫描服务

把矩阵扫描的重逻辑从 router 剥离出来，便于:
1. Web 直接同步调用
2. Worker 后台异步调用
3. 后续拆成独立 sidecar
"""
from __future__ import annotations

import asyncio
import os
import sys
import time
from typing import Any, Awaitable, Callable, Dict, List

from app.core.logging import get_logger
from app.platform.apify_budget import (
    ApifyBudgetBlocked,
    ApifyExecutionClaimBlocked,
    ApifyProviderReplayBlocked,
    call_apify_actor,
)
from app.services.intelligence.account_scan_helpers import *  # noqa: F403
from app.services.intelligence.account_scan_outcome import ActorRunError, read_actor_dataset

logger = get_logger(__name__)

def _client():
    module = sys.modules.get("app.services.scraping.apify")
    return getattr(module, "_client", None) if module else None


async def _run_actor(actor_id: str, payload: Dict[str, Any], timeout: int = 600) -> List[Dict[str, Any]]:
    client = _client()
    if not client:
        logger.warning("scanner.client_missing")
        raise ActorRunError("actor_not_configured")

    def go() -> List[Dict[str, Any]]:
        logger.info("scanner.actor_started", extra={"actor_id": actor_id})
        run = call_apify_actor(
            client,
            actor_id,
            operation="account_scan",
            source="intelligence.account_scan_service",
            run_input=payload,
            timeout_secs=timeout,
        )
        # Only complete terminal datasets can release/settle the reservation.
        # Partial reads retain the existing run and its budget for reconciliation.
        items = read_actor_dataset(client, run)
        # C5 成本记账收口:矩阵扫描全部 actor run 走此共用 runner,统一记账
        # (幂等 by run_id;失败绝不影响扫描)。
        try:
            from app.domains.costs.budget_guard import record_apify_run

            record_apify_run(
                run,
                actor_id=actor_id,
                platform="",
                operation="account_scan",
                source="intelligence.account_scan_service",
                dataset_item_count=len(items),
            )
        except Exception:
            logger.warning("scanner.cost_record_failed", extra={"actor_id": actor_id}, exc_info=True)
        logger.info("scanner.actor_complete", extra={"actor_id": actor_id, "item_count": len(items)})
        return items

    try:
        return await asyncio.to_thread(go)
    except (ApifyBudgetBlocked, ApifyExecutionClaimBlocked, ApifyProviderReplayBlocked):
        raise
    except ActorRunError:
        raise
    except Exception as exc:
        logger.warning("scanner.actor_failed", extra={"actor_id": actor_id, "error_type": type(exc).__name__})
        raise ActorRunError("actor_provider_failed", provider_outcome_unknown=True) from exc


def provider_ready() -> bool:
    return _client() is not None


# ── 兼容面:平台内容搜索(全网新发现 provider 层)已拆到 account_search_discovery.py
# (K2 扩量刀 + 千行卫兵)。原名 re-export,既有 import 点/monkeypatch 点全部不变;
# 拆出模块经 _scan_service() 懒 import 回取本模块的 _run_actor/provider_ready,无循环。
from app.services.intelligence.account_search_discovery import (  # noqa: E402,F401
    _instagram_collapse_owner_posts,
    _instagram_hashtags,
    _instagram_owner_profiles,
    _short_search_queries,
    _youtube_channel_statistics,
    _youtube_data_api_normalize,
    _youtube_data_api_search,
    _youtube_search_query_variants,
    search_platform_content,
)


async def scan_instagram_account(handle: str, max_posts: int = 1000) -> Dict[str, Any]:
    normalized = handle.lstrip("@").split("/")[-1].strip()
    started_at = time.time()
    url = f"https://www.instagram.com/{normalized}/"
    logger.info("scanner.instagram_scan_started", extra={"url": url, "max_posts": max_posts})
    raw_posts = await _run_actor(
        "apify/instagram-scraper",
        {
            "directUrls": [url],
            "resultsType": "posts",
            "resultsLimit": max_posts,
        },
    )
    profile_error = None
    try:
        raw_profile = await _run_actor(
            "apify/instagram-scraper",
            {"directUrls": [url], "resultsType": "details", "resultsLimit": 1},
            timeout=240,
        )
    except ActorRunError as exc:
        profile_error = exc
        raw_profile = exc.partial_items
    posts = [post for post in raw_posts if (post.get("ownerUsername", "").lower() == normalized.lower())]
    profile = _profile_from_items("instagram", normalized, raw_profile + (posts or raw_posts))
    profile["profile_url"] = profile.get("profile_url") or url
    result = _build_scan_result(
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
                "raw_comments": _raw_comments(post),
            }
            for post in posts
        ],
        time.time() - started_at,
        profile=profile,
    )
    if profile_error:
        result.update(status="partial" if posts else "failed", metadata=profile_error.as_result("instagram")["metadata"])
    return result


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
    profile = _profile_from_items("tiktok", normalized, videos)
    profile["profile_url"] = profile.get("profile_url") or f"https://www.tiktok.com/@{normalized}"
    return _build_scan_result(
        "tiktok",
        normalized,
        [
            {
                "title": (video.get("text") or "")[:300],
                "url": video.get("webVideoUrl", ""),
                "thumbnail": (
                    (video.get("videoMeta") or {}).get("coverUrl")
                    if isinstance(video.get("videoMeta"), dict)
                    else ""
                ) or video.get("cover") or video.get("thumbnail") or "",
                "views": video.get("playCount") or video.get("stats", {}).get("playCount", 0),
                "likes": video.get("diggCount") or video.get("stats", {}).get("diggCount", 0),
                "comments": video.get("commentCount") or video.get("stats", {}).get("commentCount", 0),
                "shares": video.get("shareCount") or video.get("stats", {}).get("shareCount", 0),
                "published": video.get("createTimeISO", ""),
                "type": "video",
                "channel": normalized,
                "raw_comments": _raw_comments(video),
            }
            for video in videos
        ],
        time.time() - started_at,
        profile=profile,
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
                "searchQueries": [f"viltrox {normalized}"],
                "maxResults": max_posts,
                "maxResultsShorts": 0,
                "maxResultStreams": 0,
            },
        )
    profile = _profile_from_items("youtube", normalized, videos)
    profile["profile_url"] = profile.get("profile_url") or f"https://www.youtube.com/@{normalized}"
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
                "shares": video.get("shares", 0),
                "published": video.get("date") or video.get("uploadDate") or video.get("published", ""),
                "type": "video",
                "channel": video.get("channelName") or normalized,
                "raw_comments": _raw_comments(video),
            }
            for video in videos
        ],
        time.time() - started_at,
        profile=profile,
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
    profile = _profile_from_items("facebook", normalized, posts)
    profile["profile_url"] = profile.get("profile_url") or url
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
                "raw_comments": _raw_comments(post),
            }
            for post in posts
        ],
        time.time() - started_at,
        profile=profile,
    )


def _generic_profile_actor_payload(url: str, handle: str, limit: int) -> Dict[str, Any]:
    return {
        "startUrls": [{"url": url}],
        "urls": [url],
        "profiles": [handle.lstrip("@")],
        "handles": [handle.lstrip("@")],
        "maxItems": limit,
        "maxResults": limit,
        "resultsLimit": limit,
        "limit": limit,
        "shouldDownloadVideos": False,
        "shouldDownloadImages": False,
        "proxyConfiguration": {"useApifyProxy": True},
    }


async def scan_reddit_account(handle: str, max_posts: int = 100) -> Dict[str, Any]:
    normalized = handle.strip().lstrip("@")
    started_at = time.time()
    actor_id = os.getenv("APIFY_REDDIT_ACCOUNT_ACTOR_ID", "").strip()
    profile_url = normalized if normalized.startswith("http") else f"https://www.reddit.com/user/{normalized}/"
    if not actor_id:
        result = _build_scan_result("reddit", normalized, [], time.time() - started_at, profile={"profile_url": profile_url, "sync_status": "not_configured"})
        result.update({"status": "actor_not_configured", "error": "APIFY_REDDIT_ACCOUNT_ACTOR_ID is not configured"})
        return result
    raw_items = await _run_actor(actor_id, _generic_profile_actor_payload(profile_url, normalized, max_posts), timeout=360)
    profile = _profile_from_items("reddit", normalized, raw_items)
    profile["profile_url"] = profile.get("profile_url") or profile_url
    posts = [
        {
            "title": _source_key(item, "title", "text", "body")[:300],
            "url": _source_key(item, "url", "permalink"),
            "thumbnail": _source_key(item, "thumbnail", "thumbnailUrl"),
            "views": _normalize_int(item.get("viewCount") or item.get("views")),
            "likes": _normalize_int(item.get("ups") or item.get("upvotes") or item.get("score")),
            "comments": _normalize_int(item.get("numComments") or item.get("commentsCount")),
            "shares": _normalize_int(item.get("shares")),
            "published": _published_value(item),
            "type": "post",
            "channel": normalized,
            "raw_comments": _raw_comments(item),
        }
        for item in raw_items[:max_posts]
    ]
    result = _build_scan_result("reddit", normalized, posts, time.time() - started_at, profile=profile)
    result.update({"status": "done" if posts else "empty"})
    return result


async def scan_x_account(handle: str, max_posts: int = 100) -> Dict[str, Any]:
    normalized = handle.strip().lstrip("@")
    started_at = time.time()
    actor_id = os.getenv("APIFY_X_ACCOUNT_ACTOR_ID", os.getenv("APIFY_TWITTER_ACCOUNT_ACTOR_ID", "")).strip()
    profile_url = normalized if normalized.startswith("http") else f"https://x.com/{normalized}"
    if not actor_id:
        result = _build_scan_result("x", normalized, [], time.time() - started_at, profile={"profile_url": profile_url, "sync_status": "not_configured"})
        result.update({"status": "actor_not_configured", "error": "APIFY_X_ACCOUNT_ACTOR_ID or APIFY_TWITTER_ACCOUNT_ACTOR_ID is not configured"})
        return result
    raw_items = await _run_actor(actor_id, _generic_profile_actor_payload(profile_url, normalized, max_posts), timeout=360)
    profile = _profile_from_items("x", normalized, raw_items)
    profile["profile_url"] = profile.get("profile_url") or profile_url
    posts = [
        {
            "title": _source_key(item, "text", "fullText", "title")[:300],
            "url": _source_key(item, "url", "tweetUrl"),
            "thumbnail": _source_key(item, "thumbnail", "thumbnailUrl"),
            "views": _normalize_int(item.get("viewCount") or item.get("views")),
            "likes": _normalize_int(item.get("likeCount") or item.get("likes")),
            "comments": _normalize_int(item.get("replyCount") or item.get("comments")),
            "shares": _normalize_int(item.get("retweetCount") or item.get("shares")),
            "published": _published_value(item),
            "type": "post",
            "channel": normalized,
            "raw_comments": _raw_comments(item),
        }
        for item in raw_items[:max_posts]
    ]
    result = _build_scan_result("x", normalized, posts, time.time() - started_at, profile=profile)
    result.update({"status": "done" if posts else "empty"})
    return result


async def scan_douyin_account(handle: str, max_posts: int = 100) -> Dict[str, Any]:
    normalized = handle.strip()
    started_at = time.time()
    actor_id = _douyin_actor_id("account")
    if not actor_id:
        result = _build_scan_result("douyin", normalized, [], time.time() - started_at)
        result.update({"status": "actor_not_configured", "error": "APIFY_DOUYIN_ACCOUNT_ACTOR_ID or APIFY_DOUYIN_ACTOR_ID is not configured"})
        return result
    safe_limit = max(1, min(int(max_posts or 100), 500))
    logger.info("scanner.douyin_scan_started", extra={"handle": normalized, "max_posts": safe_limit, "actor_id": actor_id})
    raw_items = await _run_actor(actor_id, _douyin_account_payload(normalized, safe_limit), timeout=360)
    posts = [_normalize_douyin_item(item, normalized) for item in raw_items[:safe_limit]]
    profile = _profile_from_items("douyin", normalized, raw_items)
    profile["profile_url"] = profile.get("profile_url") or _douyin_profile_url(normalized)
    result = _build_scan_result("douyin", normalized, posts, time.time() - started_at, profile=profile)
    follower_count = 0
    for item in raw_items:
        author = item.get("author") if isinstance(item.get("author"), dict) else {}
        follower_count = _normalize_int(
            item.get("followerCount")
            or item.get("fansCount")
            or author.get("followerCount")
            or author.get("follower_count")
            or author.get("fans")
        )
        if follower_count:
            break
    result.update({"follower_count": follower_count, "status": "done" if posts else "empty"})
    return result


SCANNERS: Dict[str, Callable[[str, int], Awaitable[Dict[str, Any]]]] = {
    "instagram": scan_instagram_account,
    "tiktok": scan_tiktok_account,
    "douyin": scan_douyin_account,
    "youtube": scan_youtube_account,
    "facebook": scan_facebook_account,
    "reddit": scan_reddit_account,
    "x": scan_x_account,
    "twitter": scan_x_account,
}


async def scan_account(platform: str, handle: str, max_posts: int = 1000) -> Dict[str, Any]:
    normalized_platform = (platform or "").lower()
    normalized_handle = (handle or "").strip()
    scanner = SCANNERS.get(normalized_platform)
    if not scanner:
        return {**_build_scan_result(normalized_platform, normalized_handle, [], 0), "status": "not_configured"}
    try:
        if normalized_platform in {"x", "twitter", "reddit"}:
            from app.services.intelligence.account_search_secondary import scan_secondary_profile

            return await scan_secondary_profile(normalized_platform, normalized_handle, max_posts=max_posts)
        return await scanner(normalized_handle, max_posts)
    except ActorRunError as exc:
        # Dataset rows are raw, not normalized posts. Keep them separate so a
        # partial download is not reported as a successfully scanned account.
        return {**_build_scan_result(normalized_platform, normalized_handle, [], 0),
                **exc.as_result(normalized_platform), "handle": normalized_handle}


async def scan_matrix(accounts: List[Dict[str, Any]], max_posts_per_account: int = 1000) -> Dict[str, Any]:
    results: List[Dict[str, Any]] = []
    for index, account in enumerate(accounts):
        platform = (account.get("platform") or "").lower()
        handle = account.get("handle", "")
        name = account.get("name", "")
        logger.info(
            "scanner.matrix_account_started",
            extra={"index": index + 1, "total": len(accounts), "platform": platform, "account_name": name},
        )
        try:
            result = await scan_account(platform, handle, max_posts_per_account)
            result["account_name"] = name
            results.append(result)
        except (ApifyBudgetBlocked, ApifyExecutionClaimBlocked, ApifyProviderReplayBlocked):
            raise
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
