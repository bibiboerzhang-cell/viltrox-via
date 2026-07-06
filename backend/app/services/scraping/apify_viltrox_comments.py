"""Viltrox official-account comment scraping helpers backed by Apify."""
from __future__ import annotations

import asyncio
from typing import Any, Dict, List

from app.core.config import (
    VIA_OFFICIAL_INSTAGRAM_HANDLE,
    VIA_OFFICIAL_TIKTOK_HANDLE,
    VIA_OFFICIAL_YOUTUBE_HANDLE,
)
from app.core.logging import get_logger
from app.services.scraping.apify import _apify_available, _client

logger = get_logger(__name__)

# 官号评论 actor 默认超时长达 7 天——统一栅栏,env 可调。
import os as _os
_APIFY_CALL_TIMEOUT_SECS = max(60, int(_os.environ.get("APIFY_CALL_TIMEOUT_SECS", "600")))


def _record_cost(run: Any, actor_id: str, platform: str, operation: str, item_count: int) -> None:
    """C5 成本记账收口:官号评论抓取也统一记账(幂等 by run_id;失败绝不影响抓取)。"""
    try:
        from app.domains.costs.budget_guard import record_apify_run

        record_apify_run(
            run,
            actor_id=actor_id,
            platform=platform,
            operation=operation,
            source="services.scraping.apify_viltrox_comments",
            dataset_item_count=item_count,
        )
    except Exception:
        logger.warning("apify.viltrox.cost_record_failed | actor=%s | op=%s", actor_id, operation, exc_info=True)


async def fetch_viltrox_youtube_comments(
    max_videos: int = 30,
    max_comments_per_video: int = 50,
    channel_handle: str = "",
) -> List[Dict[str, Any]]:
    """Fetch recent comments from the configured Viltrox YouTube channel."""
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

    try:
        def _fetch_videos():
            run = _client.actor("streamers/youtube-channel-scraper").call(timeout_secs=_APIFY_CALL_TIMEOUT_SECS, run_input={
                "startUrls": [{"url": f"https://www.youtube.com/@{channel_handle}/videos"}],
                "maxResults": max_videos,
            })
            items = list(_client.dataset(run["defaultDatasetId"]).iterate_items())
            _record_cost(run, "streamers/youtube-channel-scraper", "youtube", "viltrox_channel_videos", len(items))
            return items

        videos = await asyncio.to_thread(_fetch_videos)
        logger.info("apify.viltrox.youtube.videos_loaded | count=%s", len(videos))
        if not videos:
            return []

        video_urls = [{"url": video.get("url")} for video in videos if video.get("url")]
        if not video_urls:
            return []
    except Exception as exc:
        logger.warning("apify.viltrox.youtube.video_list_failed | handle=%s | error=%s", channel_handle, exc)
        return []

    try:
        def _fetch_comments():
            run = _client.actor("streamers/youtube-comments-scraper").call(timeout_secs=_APIFY_CALL_TIMEOUT_SECS, run_input={
                "startUrls": video_urls,
                "maxComments": max_comments_per_video,
            })
            items = list(_client.dataset(run["defaultDatasetId"]).iterate_items())
            _record_cost(run, "streamers/youtube-comments-scraper", "youtube", "viltrox_video_comments", len(items))
            return items

        comments = await asyncio.to_thread(_fetch_comments)
        logger.info("apify.viltrox.youtube.comments_loaded | comments=%s | videos=%s", len(comments), len(videos))
        return comments
    except Exception as exc:
        logger.warning("apify.viltrox.youtube.comments_failed | handle=%s | error=%s", channel_handle, exc)
        return []


async def fetch_viltrox_instagram_comments(
    max_posts: int = 10,
    max_comments_per_post: int = 50,
    account_handle: str = "",
) -> List[Dict[str, Any]]:
    """Fetch recent comments from the configured Viltrox Instagram account."""
    if not _apify_available():
        return []
    account_handle = (account_handle or VIA_OFFICIAL_INSTAGRAM_HANDLE or "viltrox.official").lstrip("@").strip().strip("/")
    logger.info("apify.viltrox.instagram.start | handle=%s | posts=%s", account_handle, max_posts)

    try:
        def _fetch_posts():
            run = _client.actor("apify/instagram-scraper").call(timeout_secs=_APIFY_CALL_TIMEOUT_SECS, run_input={
                "directUrls": [f"https://www.instagram.com/{account_handle}/"],
                "resultsType": "posts",
                "resultsLimit": max_posts,
            })
            items = list(_client.dataset(run["defaultDatasetId"]).iterate_items())
            _record_cost(run, "apify/instagram-scraper", "instagram", "viltrox_posts", len(items))
            return items

        posts = await asyncio.to_thread(_fetch_posts)
        logger.info("apify.viltrox.instagram.posts_loaded | count=%s", len(posts))
        if not posts:
            return []

        post_urls = [post.get("url") for post in posts if post.get("url")]
        if not post_urls:
            return []

        def _fetch_comments():
            run = _client.actor("apify/instagram-comment-scraper").call(timeout_secs=_APIFY_CALL_TIMEOUT_SECS, run_input={
                "directUrls": post_urls,
                "resultsLimit": max_comments_per_post,
            })
            items = list(_client.dataset(run["defaultDatasetId"]).iterate_items())
            _record_cost(run, "apify/instagram-comment-scraper", "instagram", "viltrox_post_comments", len(items))
            return items

        comments = await asyncio.to_thread(_fetch_comments)
        logger.info("apify.viltrox.instagram.comments_loaded | count=%s", len(comments))
        return comments
    except Exception as exc:
        logger.warning("apify.viltrox.instagram.failed | handle=%s | error=%s", account_handle, exc)
        return []


async def fetch_viltrox_tiktok_comments(
    max_videos: int = 10,
    max_comments_per_video: int = 50,
    profile_handle: str = "",
) -> List[Dict[str, Any]]:
    """Fetch recent Viltrox TikTok comments when the actor returns them."""
    if not _apify_available():
        return []
    profile_handle = (profile_handle or VIA_OFFICIAL_TIKTOK_HANDLE or "viltrox.global").lstrip("@").strip()
    logger.info("apify.viltrox.tiktok.start | handle=%s | videos=%s", profile_handle, max_videos)

    try:
        def _fetch_videos():
            run = _client.actor("clockworks/free-tiktok-scraper").call(timeout_secs=_APIFY_CALL_TIMEOUT_SECS, run_input={
                "profiles": [profile_handle],
                "resultsPerPage": max_videos,
                "shouldDownloadVideos": False,
                "shouldDownloadCovers": False,
            })
            items = list(_client.dataset(run["defaultDatasetId"]).iterate_items())
            _record_cost(run, "clockworks/free-tiktok-scraper", "tiktok", "viltrox_videos", len(items))
            return items

        videos = await asyncio.to_thread(_fetch_videos)
        logger.info("apify.viltrox.tiktok.videos_loaded | count=%s", len(videos))
        if not videos:
            return []

        logger.info(
            "apify.viltrox.tiktok.comments_todo | handle=%s | per_video=%s",
            profile_handle,
            max_comments_per_video,
        )
        return []
    except Exception as exc:
        logger.warning("apify.viltrox.tiktok.failed | handle=%s | error=%s", profile_handle, exc)
        return []


async def fetch_viltrox_comments(platform: str, **kwargs) -> List[Dict[str, Any]]:
    """Route official-account comment fetches by platform."""
    platform = platform.lower()
    if platform == "youtube":
        return await fetch_viltrox_youtube_comments(**kwargs)
    if platform == "instagram":
        return await fetch_viltrox_instagram_comments(**kwargs)
    if platform == "tiktok":
        return await fetch_viltrox_tiktok_comments(**kwargs)
    logger.warning("apify.viltrox.unsupported_platform | platform=%s", platform)
    return []


def normalize_comment(comment: Dict[str, Any], platform: str) -> Dict[str, Any]:
    """Normalize platform-specific comment records into one shape."""
    if platform == "youtube":
        return {
            "platform": "youtube",
            "comment_id": comment.get("cid", ""),
            "text": comment.get("comment", "") or "",
            "author": comment.get("author", "") or "",
            "published_text": comment.get("publishedTimeText", "") or "",
            "video_url": comment.get("pageUrl", "") or "",
            "video_title": comment.get("title", "") or "",
            "raw": comment,
        }
    if platform == "instagram":
        return {
            "platform": "instagram",
            "comment_id": comment.get("id", "") or comment.get("commentId", ""),
            "text": comment.get("text", "") or "",
            "author": comment.get("ownerUsername", "") or "",
            "published_text": comment.get("timestamp", "") or "",
            "video_url": comment.get("postUrl", "") or "",
            "video_title": "",
            "raw": comment,
        }
    if platform == "tiktok":
        return {
            "platform": "tiktok",
            "comment_id": comment.get("cid", "") or comment.get("id", ""),
            "text": comment.get("text", "") or "",
            "author": comment.get("uniqueId", "") or "",
            "published_text": comment.get("createTimeISO", "") or "",
            "video_url": comment.get("videoWebUrl", "") or "",
            "video_title": "",
            "raw": comment,
        }
    return {
        "platform": platform,
        "comment_id": "",
        "text": "",
        "author": "",
        "published_text": "",
        "video_url": "",
        "video_title": "",
        "raw": comment,
    }
