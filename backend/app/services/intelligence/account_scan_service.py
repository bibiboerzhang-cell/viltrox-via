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
import re
import sys
import time
from datetime import datetime
from typing import Any, Awaitable, Callable, Dict, List

from app.core.logging import get_logger
from app.platform.apify_budget import (
    ApifyBudgetBlocked,
    ApifyExecutionClaimBlocked,
    ApifyProviderReplayBlocked,
    call_apify_actor,
)
from app.services.intelligence.account_scan_helpers import *  # noqa: F403

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
        run = call_apify_actor(
            client,
            actor_id,
            operation="account_scan",
            source="intelligence.account_scan_service",
            run_input=payload,
            timeout_secs=timeout,
        )
        items = list(client.dataset(run["defaultDatasetId"]).iterate_items())
        logger.info("scanner.actor_complete", extra={"actor_id": actor_id, "item_count": len(items)})
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
        return items

    try:
        return await asyncio.to_thread(go)
    except (ApifyBudgetBlocked, ApifyExecutionClaimBlocked, ApifyProviderReplayBlocked):
        raise
    except Exception as exc:
        logger.warning("scanner.actor_failed", extra={"actor_id": actor_id, "error": str(exc)})
        return []


def provider_ready() -> bool:
    return _client() is not None


# Instagram hashtag 召回口径:与 YouTube/TikTok 一致 —— 把多词 query 当作完整的
# 召回意图,而不是只取首词当单 hashtag(旧逻辑系统性少召回/错召回)。
# IG hashtag actor 不能整串去空格拼成一个超长无效 hashtag(搜不到 → 恒空),
# 故拆成「多个 hashtag」:每个有意义词单独成 tag,保留组合关键词语义。
_INSTAGRAM_HASHTAG_STOPWORDS = frozenset({
    "the", "and", "for", "with", "from", "into", "your", "you", "are",
    "this", "that", "best", "top", "new", "all", "any", "how", "why",
    "what", "who", "kol", "kols", "influencer", "influencers", "creator",
    "creators", "channel", "channels", "video", "videos", "review",
    "reviews",
})


def _instagram_hashtags(query: str, *, max_tags: int = 5) -> List[str]:
    """Split a (possibly multi-word) query into multiple Instagram hashtags.

    Aligns IG recall with YouTube/TikTok, which pass the full query. Each
    meaningful token becomes its own hashtag (alnum/underscore only, <=80
    chars), stopwords and tiny tokens are dropped, duplicates collapsed, and
    the count is capped at ``max_tags`` to preserve actor throttle/budget.

    Pure function (no Apify call) so it can be unit-tested directly.
    """
    seen: set[str] = set()
    tags: List[str] = []
    fallback: List[str] = []  # short/stopword tokens kept only if nothing else
    for word in (query or "").lower().split():
        token = "".join(ch for ch in word if ch.isalnum() or ch == "_")[:80]
        if not token or token in seen:
            continue
        seen.add(token)
        if len(token) > 2 and token not in _INSTAGRAM_HASHTAG_STOPWORDS:
            tags.append(token)
            if len(tags) >= max_tags:
                break
        elif len(token) > 1:
            fallback.append(token)
    if not tags:
        # No "meaningful" token survived (e.g. very short brand like "
        # dji" already >2, but pure-stopword/short queries) → keep what we
        # have so IG still gets a real hashtag instead of returning empty.
        tags = fallback[:max_tags]
    return tags


def _short_search_queries(query: str, *, max_queries: int = 4) -> List[str]:
    """Turn a planner's comma-separated persona list into short search intents.

    TikTok's keyword actor performs poorly when it receives the whole planner
    sentence as one exact query. The returned list is bounded and deterministic;
    callers divide the original result budget across these variants.
    """
    chunks = [" ".join(part.split()) for part in re.split(r"[,;|，；]+", query or "") if part.strip()]
    if len(chunks) <= 1:
        words = (query or "").split()
        chunks = [" ".join(words[index:index + 5]) for index in range(0, len(words), 5)] or [query]
    out: List[str] = []
    seen: set[str] = set()
    for chunk in chunks:
        value = " ".join(chunk.split()[:8]).strip()[:100]
        key = value.lower()
        if not value or key in seen:
            continue
        seen.add(key)
        out.append(value)
        if len(out) >= max_queries:
            break
    return out or [" ".join((query or "").split())[:100]]


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
    raw_profile = await _run_actor(
        "apify/instagram-scraper",
        {
            "directUrls": [url],
            "resultsType": "details",
            "resultsLimit": 1,
        },
        timeout=240,
    )
    posts = [post for post in raw_posts if (post.get("ownerUsername", "").lower() == normalized.lower())]
    profile = _profile_from_items("instagram", normalized, raw_profile + (posts or raw_posts))
    profile["profile_url"] = profile.get("profile_url") or url
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
                "raw_comments": _raw_comments(post),
            }
            for post in posts
        ],
        time.time() - started_at,
        profile=profile,
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


def _youtube_data_api_normalize(items: List[Dict[str, Any]], query: str, market: str, actor_id: str, safe_limit: int) -> List[Dict[str, Any]]:
    """Map YouTube Data API search.list (type=channel) snippets to discovery candidates.

    Output shape matches the Apify-path item exactly so downstream annotate / region /
    garbage filters behave identically regardless of which provider fed the candidate.
    """
    normalized: List[Dict[str, Any]] = []
    for raw in items[:safe_limit]:
        snippet = raw.get("snippet") if isinstance(raw.get("snippet"), dict) else {}
        channel_id = str(((raw.get("id") or {}).get("channelId")) or "").strip()
        channel_name = str(snippet.get("channelTitle") or snippet.get("title") or "").strip()
        thumbs = snippet.get("thumbnails") if isinstance(snippet.get("thumbnails"), dict) else {}
        avatar_url = str(
            (((thumbs.get("high") or thumbs.get("medium") or thumbs.get("default")) or {}).get("url")) or ""
        ).strip()
        channel_url = f"https://www.youtube.com/channel/{channel_id}" if channel_id else ""
        # search.list snippet 无 @handle;用 channel_id 当 handle 保证非空(避开 _is_discovery_garbage 丢弃),
        # Apify 深度爬阶段再富化真 @handle。
        handle = channel_id or channel_name
        clean_channel_name = _known_text(channel_name, handle) or "Unknown creator"
        normalized.append(
            {
                "platform": "youtube",
                "channel_name": clean_channel_name,
                "handle": _known_text(handle, channel_name),
                "avatar_url": avatar_url,
                "thumbnail_url": avatar_url,
                "channel_url": channel_url,
                "source_url": channel_url,
                "sample_title": str(snippet.get("description") or "")[:300],
                "views": 0,
                "likes": 0,
                "comments": 0,
                "avg_views": 0,
                "published": str(snippet.get("publishedAt") or "").strip(),
                "market": (market or "").strip().upper(),
                "search_query": (query or "").strip(),
                "provider_actor": actor_id,
                "channel_id": channel_id,
                "fast_path": True,
            }
        )
    return normalized


async def _youtube_data_api_search(search_query: str, *, market: str = "", safe_limit: int = 25, relevance_language: str = "en") -> Dict[str, Any] | None:
    """YouTube Data API fast path (search.list type=channel, ~1s). None => fall back to Apify.

    None is returned when: no API key, quota exhausted, or any API error — the caller then
    runs the existing Apify youtube-scraper branch unchanged. Quota: search.list = 100
    units/call (daily default 10000). One call per discovery.
    """
    from app.platform.industry_crawlers.youtube_crawler import YouTubeCrawler

    crawler = YouTubeCrawler()
    if not crawler.api_key:
        return None

    def _channel_search(q: str) -> Dict[str, Any] | None:
        payload = crawler._request(
            "search",
            {
                "part": "snippet",
                "type": "channel",
                "q": q,
                "maxResults": max(1, min(25, int(safe_limit or 25))),
                "relevanceLanguage": (relevance_language or "en").strip().lower() or "en",
                "safeSearch": "none",
            },
        )
        if crawler._should_use_apify_fallback(payload) or str(payload.get("provider_status") or "") == "error":
            return None
        return payload

    def go() -> Dict[str, Any] | None:
        payload = _channel_search(search_query)
        if payload is None:
            return None
        # 长 query(产品名 + 多 persona 词,如 planner 监视器输出 10+ 词)在 type=channel 上
        # 常 0 命中 → 用前 5 个词(persona 关键词)重试,使长 query 也走 ~0.6s 快路径而非降级 Apify。
        if not (payload.get("items") or []):
            short_q = " ".join(str(search_query or "").split()[:5]).strip()
            if short_q and short_q.lower() != str(search_query or "").strip().lower():
                retry = _channel_search(short_q)
                if retry is not None and (retry.get("items") or []):
                    return retry
        return payload

    try:
        payload = await asyncio.to_thread(go)
    except Exception as exc:  # pragma: no cover - network only
        logger.warning("scanner.youtube_data_api_failed", extra={"error": str(exc)})
        return None
    if payload is None:
        return None
    raw_items = [item for item in (payload.get("items") or []) if isinstance(item, dict)]
    items = _youtube_data_api_normalize(raw_items, search_query, market, "youtube-data-api/search.list", int(safe_limit or 25))
    return {
        "status": "done",
        "platform": "youtube",
        "query": (search_query or "").strip(),
        "market": (market or "").strip().upper(),
        "items": items,
        "metadata": {
            "actor_id": "youtube-data-api/search.list",
            "provider": "youtube_data_api",
            "fast_path": True,
            "requested": int(safe_limit or 25),
            "returned": len(items),
            "quota_units": 100,
        },
    }


async def search_platform_content(
    platform: str,
    query: str,
    *,
    market: str = "",
    max_results: int = 25,
    relevance_language: str = "en",
) -> Dict[str, Any]:
    """Search public platform content and normalize it into KOL candidates.

    This returns real provider results only. If the Apify provider is not
    configured or a platform search actor is unavailable, the status says so
    explicitly instead of fabricating rows.
    """
    normalized_platform = (platform or "youtube").strip().lower()
    normalized_query = (query or "").strip()
    safe_limit = max(1, min(int(max_results or 25), 100))
    if not normalized_query:
        return {"status": "invalid_query", "items": [], "message": "query is required"}
    if not provider_ready():
        return {"status": "provider_unavailable", "items": [], "message": "APIFY_TOKEN is not configured"}

    search_query = _market_query(normalized_query, market)
    provider_queries = [search_query]

    # YouTube fast path: YouTube Data API search.list (~1s) before the slow Apify actor
    # (10-60s cold start). 命中即返回归一化候选;无 key / 配额耗尽 / 错误 → None,落回下方
    # 原 Apify youtube-scraper 分支(逻辑零改动)。
    if normalized_platform == "youtube":
        fast = await _youtube_data_api_search(search_query, market=market, safe_limit=safe_limit, relevance_language=relevance_language)
        if fast is not None and fast.get("items"):
            return fast

    actor_id = ""
    payload: Dict[str, Any] = {}
    timeout = 240
    if normalized_platform == "youtube":
        actor_id = "streamers/youtube-scraper"
        payload = {
            "searchQueries": [search_query],
            "maxResults": safe_limit,
            "maxResultsShorts": 0,
            "maxResultStreams": 0,
        }
    elif normalized_platform == "tiktok":
        actor_id = "clockworks/free-tiktok-scraper"
        provider_queries = _short_search_queries(search_query)
        payload = {
            "searchQueries": provider_queries,
            "resultsPerPage": max(3, (safe_limit + len(provider_queries) - 1) // len(provider_queries)),
            "shouldDownloadVideos": False,
            "shouldDownloadCovers": False,
        }
    elif normalized_platform == "instagram":
        actor_id = "apify/instagram-hashtag-scraper"
        # 多词 query 拆成「多个 hashtag」(与 YouTube/TikTok 召回口径对齐),
        # 而非旧逻辑只取首词当单 hashtag → 系统性少召回/错召回。
        # 整串去空格会拼成超长无效 hashtag(IG actor 搜不到 → 恒空),故分词。
        hashtags = _instagram_hashtags(search_query)
        if not hashtags:
            return {"status": "invalid_query", "items": [], "message": "instagram hashtag query is empty after normalization"}
        # resultsLimit 是「总条数」上限,保留既有节流/预算口径不变:多 hashtag
        # 不放大总抓取量,仍由 safe_limit 封顶(actor 在多个 hashtag 间分摊)。
        payload = {
            "hashtags": hashtags,
            "resultsLimit": safe_limit,
            "resultsType": "posts",
        }
        timeout = 300
    elif normalized_platform == "facebook":
        # Facebook 发现(用户口径:FB 流量一般般,做够用的就行,别过度设计)。
        # actor 选择说明:仓库既有 apify/facebook-posts-scraper 只吃 startUrls(页面 URL)+
        # resultsLimit(见本文件 scan_facebook_account 与 facebook_crawler.py),不支持关键词
        # 搜索,做不了「检索词 → 发现新创作者」。故此分支换官方 apify/facebook-search-scraper:
        # 关键词搜公开帖文,帖文项自带发帖主页名/URL,可映射成统一候选结构。
        # env 可换 actor(与 douyin 分支同模式);resultsLimit 沿用仓库 FB 调用的封顶口径。
        actor_id = (os.getenv("APIFY_FACEBOOK_SEARCH_ACTOR_ID") or "apify/facebook-search-scraper").strip()
        payload = {
            "searchQueries": [search_query],
            "searchType": "posts",
            "resultsLimit": safe_limit,
        }
        timeout = 300
    elif normalized_platform == "douyin":
        actor_id = _douyin_actor_id("search")
        if not actor_id:
            return {
                "status": "actor_not_configured",
                "platform": "douyin",
                "items": [],
                "message": "APIFY_DOUYIN_SEARCH_ACTOR_ID or APIFY_DOUYIN_ACTOR_ID is not configured",
            }
        payload = _douyin_search_payload(search_query, safe_limit)
        timeout = 360
    else:
        return {
            "status": "unsupported_platform",
            "items": [],
            "message": f"{normalized_platform} platform search is not configured",
        }

    started_at = datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")
    raw_items = await _run_actor(actor_id, payload, timeout=timeout)
    items: List[Dict[str, Any]] = []
    for item in raw_items[:safe_limit]:
        handle = ""
        avatar_url = ""
        thumbnail_url = ""
        followers = 0  # 仅 facebook 分支可能填上;>0 才随 item 透出,其余平台输出不变
        if normalized_platform == "youtube":
            channel_name = _source_key(item, "channelName", "channelTitle", "author")
            channel_url = _source_key(item, "channelUrl", "channelURL")
            handle = _source_key(item, "channelHandle", "channelUsername", "handle", "author")
            avatar_url = _clean_url(_source_key(item, "channelAvatar", "channelThumbnail", "channelImage", "avatarUrl", "authorThumbnail"))
            thumbnail_url = _clean_url(_source_key(item, "thumbnailUrl", "thumbnail", "image", "cover"))
            source_url = _source_key(item, "url", "link")
            title = _source_key(item, "title", "text")
            views = _normalize_int(item.get("viewCount") or item.get("views"))
            likes = _normalize_int(item.get("likes"))
            comments = _normalize_int(item.get("commentsCount") or item.get("comments"))
        elif normalized_platform == "tiktok":
            author = item.get("authorMeta") if isinstance(item.get("authorMeta"), dict) else {}
            channel_name = _source_key(author, "nickName", "name") or _source_key(item, "authorName", "author")
            handle = _source_key(author, "name") or _source_key(item, "author")
            channel_url = f"https://www.tiktok.com/@{handle}" if handle else ""
            avatar_url = _clean_url(_source_key(author, "avatar", "avatarThumb", "avatarMedium", "avatarLarger", "profilePicture"))
            thumbnail_url = _clean_url(_source_key(item, "videoMeta.coverUrl", "cover", "coverUrl", "thumbnail"))
            source_url = _source_key(item, "webVideoUrl", "url")
            title = _source_key(item, "text", "desc", "title")
            stats = item.get("stats") if isinstance(item.get("stats"), dict) else {}
            views = _normalize_int(item.get("playCount") or stats.get("playCount"))
            likes = _normalize_int(item.get("diggCount") or stats.get("diggCount"))
            comments = _normalize_int(item.get("commentCount") or stats.get("commentCount"))
        elif normalized_platform == "facebook":
            # facebook-search-scraper 帖文项映射:发帖主页名→channel_name/display_name,
            # 主页 URL→channel_url,帖文文本→sample_title。字段名多版本防御,
            # 数字口径与本文件 scan_facebook_account 对齐(text/message、likesCount/reactionsCount)。
            channel_name = _source_key(item, "pageName", "user.name", "authorName", "pageTitle", "name")
            channel_url = _clean_url(_source_key(item, "pageUrl", "user.profileUrl", "facebookUrl", "topLevelUrl"))
            handle = _source_key(item, "pageHandle", "username", "user.username", "pageUsername")
            if not handle and "facebook.com/" in channel_url:
                # 主页 URL 的 vanity slug 当 handle(facebook.com/<slug>);系统路径不算 handle。
                _slug = channel_url.split("facebook.com/", 1)[1].strip("/").split("/", 1)[0].split("?", 1)[0]
                if _slug and _slug.lower() not in {"profile.php", "people", "pages", "groups", "watch", "reel", "reels", "search"}:
                    handle = _slug
            if not channel_url and handle:
                channel_url = f"https://www.facebook.com/{handle}"
            avatar_url = _clean_url(_source_key(item, "user.profilePic", "pageProfilePic", "profilePic", "avatar"))
            thumbnail_url = _clean_url(_source_key(item, "thumbnail", "imageUrl", "image"))
            source_url = _source_key(item, "url", "postUrl", "topLevelUrl")
            title = _source_key(item, "text", "message", "title")
            views = _normalize_int(item.get("viewsCount") or item.get("views"))
            likes = _normalize_int(item.get("likesCount") or item.get("reactionsCount") or item.get("likes"))
            comments = _normalize_int(item.get("commentsCount") or item.get("comments"))
            # 粉丝数:posts 搜索项通常不带;字段有就带上,没有保持 0(不杜撰)。
            followers = _normalize_int(_source_key(item, "followers", "followersCount", "pageFollowers", "user.followers") or 0)
        elif normalized_platform == "douyin":
            post = _normalize_douyin_item(item)
            channel_name = str(post.get("channel") or "Unknown creator")
            handle = str(post.get("handle") or channel_name)
            channel_url = str(post.get("channel_url") or "")
            avatar_url = str(post.get("avatar_url") or "")
            thumbnail_url = str(post.get("thumbnail") or "")
            source_url = str(post.get("url") or "")
            title = str(post.get("title") or "")
            views = _normalize_int(post.get("views"))
            likes = _normalize_int(post.get("likes"))
            comments = _normalize_int(post.get("comments"))
        else:
            channel_name = _source_key(item, "ownerUsername", "username", "ownerFullName")
            handle = _source_key(item, "ownerUsername", "username")
            channel_url = f"https://www.instagram.com/{channel_name}/" if channel_name else _source_key(item, "ownerProfileUrl")
            avatar_url = _clean_url(_source_key(item, "ownerProfilePicUrl", "profilePicUrl", "profilePictureUrl", "displayProfilePicUrl", "avatarUrl"))
            thumbnail_url = _clean_url(_source_key(item, "displayUrl", "imageUrl", "thumbnailUrl", "thumbnail", "image"))
            source_url = _source_key(item, "url", "shortCode")
            title = _source_key(item, "caption", "title", "text")
            views = _normalize_int(item.get("videoViewCount") or item.get("videoPlayCount"))
            likes = _normalize_int(item.get("likesCount"))
            comments = _normalize_int(item.get("commentsCount"))

        # 修 query-as-handle bug:去掉 normalized_query 兜底——无真 handle/name 时不再把整句查询
        # 当成创作者(此前造出 youtube.com/@整句 的假号混入发现结果)。
        clean_channel_name = _known_text(channel_name, handle) or "Unknown creator"
        items.append(
            {
                "platform": normalized_platform,
                "channel_name": clean_channel_name,
                "handle": _known_text(handle, channel_name),
                "avatar_url": avatar_url or thumbnail_url,
                "thumbnail_url": thumbnail_url,
                "channel_url": channel_url,
                "source_url": source_url,
                "sample_title": title[:300],
                "views": views,
                "likes": likes,
                "comments": comments,
                "avg_views": views,
                "published": _published_value(item),
                "market": (market or "").strip().upper(),
                "search_query": normalized_query,
                "provider_actor": actor_id,
                # followers 仅在真有值时透出(目前只有 facebook 分支可能填),其余平台 item 结构不变。
                **({"followers": followers} if followers > 0 else {}),
            }
        )

    return {
        "status": "done",
        "platform": normalized_platform,
        "query": normalized_query,
        "market": (market or "").strip().upper(),
        "items": items,
        "metadata": {
            "actor_id": actor_id,
            "requested": safe_limit,
            "returned": len(items),
            "provider_queries": provider_queries,
            "searched_at": started_at,
        },
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
