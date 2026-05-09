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
from app.core.config import (
    VIA_OFFICIAL_INSTAGRAM_HANDLE,
    VIA_OFFICIAL_TIKTOK_HANDLE,
    VIA_OFFICIAL_YOUTUBE_HANDLE,
)

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

        run = await asyncio.to_thread(
            lambda: _client.actor("clockworks/free-tiktok-scraper").call(run_input=run_input)
        )

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
            "video_url": item.get("videoUrl", "") or item.get("downloadAddr", "") or "",
            "owner_username": author.get("name", "") or "",
            "owner_full_name": author.get("nickName", "") or "",
            "duration": video.get("duration", 0),
            "hashtags": [h.get("name", "") for h in (item.get("hashtags") or [])],
            "music_name": (item.get("musicMeta") or {}).get("musicName", ""),
            "error": None,
            "scraper": "apify_tiktok",
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


# ============================================================
# 评论抓取 — 用于验证系统
# 抓 Viltrox 官号最新视频/帖子的评论, 找含验证码的
# ============================================================

import asyncio
from typing import List, Dict, Any


async def fetch_viltrox_youtube_comments(
    max_videos: int = 30,
    max_comments_per_video: int = 50,
    channel_handle: str = "",
) -> List[Dict[str, Any]]:
    """
    抓 Viltrox Official YouTube 频道最新视频的评论.
    
    流程:
      1. 拿最新 N 个视频列表 (channel scraper)
      2. 一次性把所有视频 URL 传给 comments scraper
      3. 返回所有评论 (扁平 list)
    
    Args:
        max_videos: 最多抓多少个视频 (默认 30)
        max_comments_per_video: 每视频最多多少条评论 (默认 50)
    
    Returns:
        list of comment dicts, 每个含:
          - cid (评论唯一 ID)
          - comment (评论文本)
          - author (评论者 username, 格式 @xxx)
          - publishedTimeText (例 "2 minutes ago")
          - voteCount, replyCount
          - videoId, pageUrl, title (视频信息)
    """
    if not _apify_available():
        logger.warning("apify.viltrox.youtube.disabled")
        return []
    channel_handle = (channel_handle or VIA_OFFICIAL_YOUTUBE_HANDLE or "viltroxofficial").lstrip("@").strip()
    
    logger.info(
        "apify.viltrox.youtube.start | handle=%s | videos=%s | per_video=%s",
        channel_handle,
        max_videos,
        max_comments_per_video,
    )
    
    # Step 1: 拿最新视频列表
    try:
        def _fetch_videos():
            run = _client.actor("streamers/youtube-channel-scraper").call(run_input={
                "startUrls": [{"url": f"https://www.youtube.com/@{channel_handle}/videos"}],
                "maxResults": max_videos,
            })
            return list(_client.dataset(run["defaultDatasetId"]).iterate_items())
        
        videos = await asyncio.to_thread(_fetch_videos)
        logger.info("apify.viltrox.youtube.videos_loaded | count=%s", len(videos))
        
        if not videos:
            return []
        
        video_urls = [{"url": v.get("url")} for v in videos if v.get("url")]
        if not video_urls:
            return []
    
    except Exception as e:
        logger.warning("apify.viltrox.youtube.video_list_failed | handle=%s | error=%s", channel_handle, e)
        return []
    
    # Step 2: 一次性抓所有评论
    try:
        def _fetch_comments():
            run = _client.actor("streamers/youtube-comments-scraper").call(run_input={
                "startUrls": video_urls,
                "maxComments": max_comments_per_video,
            })
            return list(_client.dataset(run["defaultDatasetId"]).iterate_items())
        
        comments = await asyncio.to_thread(_fetch_comments)
        logger.info(
            "apify.viltrox.youtube.comments_loaded | comments=%s | videos=%s",
            len(comments),
            len(videos),
        )
        return comments
    
    except Exception as e:
        logger.warning("apify.viltrox.youtube.comments_failed | handle=%s | error=%s", channel_handle, e)
        return []


async def fetch_viltrox_instagram_comments(
    max_posts: int = 10,
    max_comments_per_post: int = 50,
    account_handle: str = "",
) -> List[Dict[str, Any]]:
    """
    抓 Viltrox Global Instagram 最新 post 的评论.
    
    Returns: 评论 list (字段名按 Instagram actor 输出)
    """
    if not _apify_available():
        return []
    account_handle = (account_handle or VIA_OFFICIAL_INSTAGRAM_HANDLE or "viltrox.official").lstrip("@").strip().strip("/")
    
    logger.info("apify.viltrox.instagram.start | handle=%s | posts=%s", account_handle, max_posts)
    
    try:
        def _fetch_posts():
            # 先拿 viltrox_global 主页最新 posts
            run = _client.actor("apify/instagram-scraper").call(run_input={
                "directUrls": [f"https://www.instagram.com/{account_handle}/"],
                "resultsType": "posts",
                "resultsLimit": max_posts,
            })
            return list(_client.dataset(run["defaultDatasetId"]).iterate_items())
        
        posts = await asyncio.to_thread(_fetch_posts)
        logger.info("apify.viltrox.instagram.posts_loaded | count=%s", len(posts))
        
        if not posts:
            return []
        
        # 拿所有 post URL
        post_urls = [p.get("url") for p in posts if p.get("url")]
        if not post_urls:
            return []
        
        # 抓评论
        def _fetch_comments():
            run = _client.actor("apify/instagram-comment-scraper").call(run_input={
                "directUrls": post_urls,
                "resultsLimit": max_comments_per_post,
            })
            return list(_client.dataset(run["defaultDatasetId"]).iterate_items())
        
        comments = await asyncio.to_thread(_fetch_comments)
        logger.info("apify.viltrox.instagram.comments_loaded | count=%s", len(comments))
        return comments
    
    except Exception as e:
        logger.warning("apify.viltrox.instagram.failed | handle=%s | error=%s", account_handle, e)
        return []


async def fetch_viltrox_tiktok_comments(
    max_videos: int = 10,
    max_comments_per_video: int = 50,
    profile_handle: str = "",
) -> List[Dict[str, Any]]:
    """
    抓 Viltrox Global TikTok 最新视频的评论.
    
    注意: clockworks/free-tiktok-scraper 可能不直接支持 channel mode,
    需要先抓 profile 拿视频列表, 再抓评论.
    """
    if not _apify_available():
        return []
    profile_handle = (profile_handle or VIA_OFFICIAL_TIKTOK_HANDLE or "viltrox.global").lstrip("@").strip()
    
    logger.info("apify.viltrox.tiktok.start | handle=%s | videos=%s", profile_handle, max_videos)
    
    try:
        # Step 1: 用 profile URL 拿最新视频
        def _fetch_videos():
            run = _client.actor("clockworks/free-tiktok-scraper").call(run_input={
                "profiles": [profile_handle],
                "resultsPerPage": max_videos,
                "shouldDownloadVideos": False,
                "shouldDownloadCovers": False,
            })
            return list(_client.dataset(run["defaultDatasetId"]).iterate_items())
        
        videos = await asyncio.to_thread(_fetch_videos)
        logger.info("apify.viltrox.tiktok.videos_loaded | count=%s", len(videos))
        
        if not videos:
            return []
        
        # TikTok comments 需要单独 actor
        # apify/tiktok-comments-scraper 或类似
        # 这里先返回视频中已包含的评论 (clockworks scraper 有时会包)
        all_comments = []
        for v in videos:
            video_url = v.get("webVideoUrl", "")
            video_text = v.get("text", "")
            video_id = v.get("id", "")
            
            # clockworks 不返回评论文本, 只有 commentsDatasetUrl
            # 真实场景下需要再调 comments scraper
            # TODO: 接 apify/tiktok-comments-scraper 真正抓评论
            
        logger.info("apify.viltrox.tiktok.comments_todo | handle=%s", profile_handle)
        return all_comments
    
    except Exception as e:
        logger.warning("apify.viltrox.tiktok.failed | handle=%s | error=%s", profile_handle, e)
        return []


async def fetch_viltrox_comments(platform: str, **kwargs) -> List[Dict[str, Any]]:
    """
    统一入口 — 按平台抓 Viltrox 官号评论.
    
    Args:
        platform: youtube / instagram / tiktok
        **kwargs: 传给具体函数 (max_videos, max_comments_per_video)
    
    Returns: 标准化评论 list
    """
    platform = platform.lower()
    
    if platform == "youtube":
        return await fetch_viltrox_youtube_comments(**kwargs)
    elif platform == "instagram":
        return await fetch_viltrox_instagram_comments(**kwargs)
    elif platform == "tiktok":
        return await fetch_viltrox_tiktok_comments(**kwargs)
    else:
        logger.warning("apify.viltrox.unsupported_platform | platform=%s", platform)
        return []


def normalize_comment(comment: Dict[str, Any], platform: str) -> Dict[str, Any]:
    """
    把不同平台的评论数据标准化成统一格式.
    
    返回的标准格式:
      {
        "platform":          str,
        "comment_id":        str,    # 评论唯一 ID
        "text":              str,    # 评论文本
        "author":            str,    # username (格式 @xxx)
        "published_text":    str,    # "2 minutes ago" / ISO time
        "video_url":         str,    # 评论所在视频 URL
        "video_title":       str,    # 视频标题
        "raw":               dict,   # 原始数据 (debug 用)
      }
    """
    if platform == "youtube":
        return {
            "platform":       "youtube",
            "comment_id":     comment.get("cid", ""),
            "text":           comment.get("comment", "") or "",
            "author":         comment.get("author", "") or "",
            "published_text": comment.get("publishedTimeText", "") or "",
            "video_url":      comment.get("pageUrl", "") or "",
            "video_title":    comment.get("title", "") or "",
            "raw":            comment,
        }
    
    elif platform == "instagram":
        return {
            "platform":       "instagram",
            "comment_id":     comment.get("id", "") or comment.get("commentId", ""),
            "text":           comment.get("text", "") or "",
            "author":         comment.get("ownerUsername", "") or "",
            "published_text": comment.get("timestamp", "") or "",
            "video_url":      comment.get("postUrl", "") or "",
            "video_title":    "",
            "raw":            comment,
        }
    
    elif platform == "tiktok":
        return {
            "platform":       "tiktok",
            "comment_id":     comment.get("cid", "") or comment.get("id", ""),
            "text":           comment.get("text", "") or "",
            "author":         comment.get("uniqueId", "") or "",
            "published_text": comment.get("createTimeISO", "") or "",
            "video_url":      comment.get("videoWebUrl", "") or "",
            "video_title":    "",
            "raw":            comment,
        }
    
    return {
        "platform":       platform,
        "comment_id":     "",
        "text":           "",
        "author":         "",
        "published_text": "",
        "video_url":      "",
        "video_title":    "",
        "raw":            comment,
    }
