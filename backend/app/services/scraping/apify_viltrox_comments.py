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
            run = _client.actor("streamers/youtube-channel-scraper").call(run_input={
                "startUrls": [{"url": f"https://www.youtube.com/@{channel_handle}/videos"}],
                "maxResults": max_videos,
            })
            return list(_client.dataset(run["defaultDatasetId"]).iterate_items())

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
            run = _client.actor("streamers/youtube-comments-scraper").call(run_input={
                "startUrls": video_urls,
                "maxComments": max_comments_per_video,
            })
            return list(_client.dataset(run["defaultDatasetId"]).iterate_items())

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

        post_urls = [post.get("url") for post in posts if post.get("url")]
        if not post_urls:
            return []

        def _fetch_comments():
            run = _client.actor("apify/instagram-comment-scraper").call(run_input={
                "directUrls": post_urls,
                "resultsLimit": max_comments_per_post,
            })
            return list(_client.dataset(run["defaultDatasetId"]).iterate_items())

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
