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
from urllib.parse import quote_plus

from app.core.logging import get_logger

logger = get_logger(__name__)

EMAIL_RE = re.compile(r"\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b", re.IGNORECASE)
URL_RE = re.compile(r"(?:https?://|mailto:)[^\s<>'\"）)]+", re.IGNORECASE)

PROFILE_SOURCE_KEYS = (
    "owner",
    "author",
    "authorMeta",
    "author_meta",
    "user",
    "userInfo",
    "profile",
    "page",
    "channel",
)
AVATAR_KEYS = (
    "avatar_url",
    "avatarUrl",
    "avatar",
    "avatarUri",
    "profilePicUrl",
    "profilePictureUrl",
    "profilePicture",
    "profileImage",
    "ownerProfilePicUrl",
    "displayProfilePicUrl",
    "channelAvatar",
    "channelThumbnail",
    "authorMeta.avatar",
    "authorMeta.avatarThumb",
    "author.avatar",
    "user.avatar",
    "user.avatarThumb",
)
PROFILE_URL_KEYS = (
    "profile_url",
    "profileUrl",
    "profileURL",
    "accountUrl",
    "account_url",
    "ownerProfileUrl",
    "channelUrl",
    "channelURL",
    "authorMeta.profileUrl",
    "author.url",
    "user.url",
)
BIO_KEYS = (
    "bio",
    "biography",
    "signature",
    "description",
    "about",
    "aboutText",
    "profileDescription",
    "businessCategoryName",
    "caption",
    "text",
    "message",
    "title",
)
DISPLAY_NAME_KEYS = (
    "display_name",
    "displayName",
    "fullName",
    "ownerFullName",
    "nickname",
    "nickName",
    "name",
    "channelName",
    "authorName",
)
FOLLOWER_KEYS = (
    "followersCount",
    "followerCount",
    "followers",
    "subscribers",
    "subscriberCount",
    "fansCount",
    "fans",
    "authorMeta.fans",
    "authorMeta.followers",
    "author.followerCount",
    "user.followerCount",
)
CONTACT_FIELD_KEYS = (
    "email",
    "contactEmail",
    "businessEmail",
    "publicEmail",
    "externalUrl",
    "website",
    "bioLink",
    "contactUrl",
    "link",
)


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


def provider_ready() -> bool:
    return _client() is not None


def _build_scan_result(
    platform: str,
    handle: str,
    posts: List[Dict[str, Any]],
    duration_sec: float,
    profile: Dict[str, Any] | None = None,
) -> Dict[str, Any]:
    profile = profile if isinstance(profile, dict) else {}
    contact_emails = list(profile.get("contact_emails") or [])
    contact_links = list(profile.get("contact_links") or [])
    return {
        "platform": platform,
        "handle": handle,
        "profile": profile,
        "avatar_url": str(profile.get("avatar_url") or ""),
        "profile_url": str(profile.get("profile_url") or ""),
        "bio": str(profile.get("bio") or ""),
        "display_name": str(profile.get("display_name") or ""),
        "follower_count": _normalize_int(profile.get("follower_count")),
        "contact_email": str(profile.get("contact_email") or (contact_emails[0] if contact_emails else "")),
        "contact_emails": contact_emails,
        "contact_links": contact_links,
        "posts": posts,
        "stats": {
            "total_posts": len(posts),
            "total_views": sum(item.get("views", 0) for item in posts),
            "total_likes": sum(item.get("likes", 0) for item in posts),
            "total_comments": sum(item.get("comments", 0) for item in posts),
        },
        "duration_sec": round(duration_sec, 1),
    }


def _douyin_actor_id(kind: str) -> str:
    specific = os.getenv(f"APIFY_DOUYIN_{kind.upper()}_ACTOR_ID", "").strip()
    return specific or os.getenv("APIFY_DOUYIN_ACTOR_ID", "").strip()


def _actor_slug(actor_id: str) -> str:
    return str(actor_id or "").strip().lower()


def _douyin_search_payload(query: str, limit: int) -> Dict[str, Any]:
    actor = _actor_slug(_douyin_actor_id("search"))
    if actor == "kuaima/douyin-search":
        return {
            "search_by_keywords": query,
        }
    if actor == "agentflow/douyin-profile-search-scraper":
        return {
            "searchKeywords": [query],
            "maxItemsPerSource": limit,
            "maxScrollRounds": max(1, min(10, limit // 10 + 1)),
            "forceChinaProxy": True,
            "proxyConfiguration": {
                "useApifyProxy": True,
                "apifyProxyGroups": ["RESIDENTIAL"],
                "apifyProxyCountry": "CN",
            },
        }
    return {
        "query": query,
        "keyword": query,
        "keywords": [query],
        "searchQueries": [query],
        "maxItems": limit,
        "maxResults": limit,
        "limit": limit,
        "shouldDownloadVideos": False,
        "shouldDownloadCovers": False,
        "proxyConfiguration": {"useApifyProxy": True},
    }


def _douyin_account_payload(handle: str, limit: int) -> Dict[str, Any]:
    raw = handle.strip()
    start_url = raw if raw.startswith("http") else f"https://www.douyin.com/search/{quote_plus(raw.lstrip('@'))}"
    actor = _actor_slug(_douyin_actor_id("account"))
    if actor == "agentflow/douyin-profile-search-scraper":
        profile_urls = [start_url] if "/user/" in start_url else []
        search_keywords = [] if profile_urls else [raw.lstrip("@")]
        return {
            "profileUrls": profile_urls,
            "searchKeywords": search_keywords,
            "maxItemsPerSource": limit,
            "maxScrollRounds": max(1, min(10, limit // 10 + 1)),
            "forceChinaProxy": True,
            "proxyConfiguration": {
                "useApifyProxy": True,
                "apifyProxyGroups": ["RESIDENTIAL"],
                "apifyProxyCountry": "CN",
            },
        }
    return {
        "profile": raw,
        "profiles": [raw],
        "handle": raw.lstrip("@"),
        "handles": [raw.lstrip("@")],
        "startUrls": [{"url": start_url}],
        "maxItems": limit,
        "maxResults": limit,
        "limit": limit,
        "shouldDownloadVideos": False,
        "shouldDownloadCovers": False,
        "proxyConfiguration": {"useApifyProxy": True},
    }


def _nested_value(item: Dict[str, Any], key: str) -> Any:
    current: Any = item
    for part in key.split("."):
        if not isinstance(current, dict):
            return None
        current = current.get(part)
    return current


def _clean_url(value: Any) -> str:
    text = str(value or "").strip().strip(".,;，。；")
    if not text:
        return ""
    return text


def _clean_email(value: str) -> str:
    return str(value or "").strip().lower().strip(".,;，。；")


def _unique_strings(values: list[str], limit: int = 20) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        clean = str(value or "").strip()
        key = clean.lower()
        if not clean or key in seen:
            continue
        seen.add(key)
        result.append(clean)
        if len(result) >= limit:
            break
    return result


def _extract_emails(text: Any) -> list[str]:
    return _unique_strings([_clean_email(match.group(0)) for match in EMAIL_RE.finditer(str(text or ""))], limit=10)


def _link_label(url: str) -> str:
    lower = url.lower()
    if lower.startswith("mailto:"):
        return "Email"
    if "instagram.com" in lower:
        return "Instagram"
    if "tiktok.com" in lower:
        return "TikTok"
    if "youtube.com" in lower or "youtu.be" in lower:
        return "YouTube"
    if "facebook.com" in lower:
        return "Facebook"
    if "reddit.com" in lower:
        return "Reddit"
    if "twitter.com" in lower or "x.com" in lower:
        return "X"
    if "linktr.ee" in lower or "beacons.ai" in lower or "bio.link" in lower:
        return "Bio link"
    return "Website"


def _extract_urls(text: Any) -> list[str]:
    return _unique_strings([_clean_url(match.group(0)) for match in URL_RE.finditer(str(text or ""))], limit=20)


def _profile_sources(item: Dict[str, Any]) -> list[Dict[str, Any]]:
    sources = [item]
    for key in PROFILE_SOURCE_KEYS:
        value = item.get(key)
        if isinstance(value, dict):
            sources.append(value)
    return sources


def _first_from_sources(sources: list[Dict[str, Any]], keys: tuple[str, ...]) -> Any:
    for source in sources:
        for key in keys:
            value = _nested_value(source, key) if "." in key else source.get(key)
            if isinstance(value, list):
                for child in value:
                    if isinstance(child, str) and child.strip():
                        return child
                    if isinstance(child, dict):
                        nested = _first_from_sources([child], keys)
                        if nested:
                            return nested
            elif value not in (None, ""):
                return value
    return None


def _text_blobs_from_item(item: Dict[str, Any]) -> list[str]:
    blobs: list[str] = []
    for source in _profile_sources(item):
        for key in BIO_KEYS + CONTACT_FIELD_KEYS:
            value = _nested_value(source, key) if "." in key else source.get(key)
            if isinstance(value, str) and value.strip():
                blobs.append(value.strip())
    return blobs


def _contact_links_from_sources(sources: list[Dict[str, Any]], text_blobs: list[str]) -> list[Dict[str, str]]:
    urls: list[str] = []
    for source in sources:
        for key in CONTACT_FIELD_KEYS:
            value = _nested_value(source, key) if "." in key else source.get(key)
            if isinstance(value, str):
                if value.startswith("http") or value.startswith("mailto:"):
                    urls.append(_clean_url(value))
                urls.extend(_extract_urls(value))
    for text in text_blobs:
        urls.extend(_extract_urls(text))
    return [{"label": _link_label(url), "value": url, "url": url} for url in _unique_strings(urls, limit=12)]


def _profile_from_items(platform: str, handle: str, items: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Extract public account profile fields from real provider payloads.

    We do not fabricate profile data. Missing provider fields remain empty so
    the UI can show "待同步/未抓到" instead of fake zeroes.
    """
    sources: list[Dict[str, Any]] = []
    text_blobs: list[str] = []
    for item in items[:50]:
        if not isinstance(item, dict):
            continue
        item_sources = _profile_sources(item)
        sources.extend(item_sources)
        text_blobs.extend(_text_blobs_from_item(item))

    avatar_url = _clean_url(_first_from_sources(sources, AVATAR_KEYS))
    profile_url = _clean_url(_first_from_sources(sources, PROFILE_URL_KEYS))
    display_name = str(_first_from_sources(sources, DISPLAY_NAME_KEYS) or "").strip()
    follower_count = _normalize_int(_first_from_sources(sources, FOLLOWER_KEYS))
    bio = "\n".join(_unique_strings(text_blobs, limit=6))[:2000]

    direct_emails: list[str] = []
    for source in sources:
        for key in ("email", "contactEmail", "businessEmail", "publicEmail"):
            value = source.get(key)
            if isinstance(value, str):
                direct_emails.extend(_extract_emails(value))
    for blob in text_blobs:
        direct_emails.extend(_extract_emails(blob))
    contact_emails = _unique_strings(direct_emails, limit=8)
    contact_links = _contact_links_from_sources(sources, text_blobs)
    for email in contact_emails:
        mailto = f"mailto:{email}"
        if not any(str(link.get("url", "")).lower() == mailto for link in contact_links):
            contact_links.insert(0, {"label": "Email", "value": email, "url": mailto})

    return {
        "platform": platform,
        "handle": handle,
        "display_name": display_name,
        "avatar_url": avatar_url,
        "profile_url": profile_url,
        "bio": bio,
        "follower_count": follower_count,
        "contact_email": contact_emails[0] if contact_emails else "",
        "contact_emails": contact_emails,
        "contact_links": contact_links[:12],
        "sync_status": "done" if items else "not_configured",
    }


def _raw_comments(item: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Best-effort extraction from Apify actor payloads.

    Actors differ by platform and plan. We only persist comments that the actor
    actually returns; missing comments are represented as an empty list instead
    of synthetic data.
    """
    candidates = []
    for key in ("comments", "latestComments", "topComments", "commentsList"):
        value = item.get(key)
        if isinstance(value, list):
            candidates.extend(value)
    normalized: List[Dict[str, Any]] = []
    for comment in candidates[:200]:
        if isinstance(comment, str):
            text = comment.strip()
            author = ""
            likes = 0
        elif isinstance(comment, dict):
            text = str(comment.get("text") or comment.get("comment") or comment.get("message") or "").strip()
            author = str(
                comment.get("ownerUsername")
                or comment.get("username")
                or comment.get("author")
                or comment.get("authorName")
                or ""
            ).strip()
            likes = _normalize_int(comment.get("likesCount") or comment.get("likeCount") or comment.get("likes"))
        else:
            continue
        if text:
            normalized.append({"text": text[:1000], "author": author, "likes": likes})
    return normalized


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
                "searchKeywords": f"viltrox {normalized}",
                "maxResults": max_posts,
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


def _market_query(query: str, market: str = "") -> str:
    q = (query or "").strip()
    m = (market or "").strip()
    if not m or m.lower() in {"global", "all", "worldwide"}:
        return q
    return f"{q} {m}"


def _normalize_int(value: Any) -> int:
    try:
        if isinstance(value, str):
            value = re.sub(r"[^\d.-]", "", value)
        return int(float(value or 0))
    except (TypeError, ValueError):
        return 0


def _source_key(item: Dict[str, Any], *keys: str) -> str:
    for key in keys:
        raw = _nested_value(item, key) if "." in key else item.get(key)
        value = str(raw or "").strip()
        if value:
            return value
    return ""


def _known_text(*values: Any) -> str:
    for value in values:
        text = str(value or "").strip()
        if text and text.lower() != "unknown creator":
            return text
    return ""


def _published_value(item: Dict[str, Any]) -> str:
    return _source_key(item, "date", "uploadDate", "published", "timestamp", "createTimeISO", "time")


def _douyin_profile_url(handle: str) -> str:
    safe = handle.strip()
    if safe.startswith("http"):
        return safe
    if safe:
        return f"https://www.douyin.com/search/{quote_plus(safe.lstrip('@'))}"
    return ""


def _normalize_douyin_item(item: Dict[str, Any], fallback_handle: str = "") -> Dict[str, Any]:
    author = item.get("author") if isinstance(item.get("author"), dict) else {}
    user = item.get("user") if isinstance(item.get("user"), dict) else {}
    author = {**user, **author}
    stats = item.get("statistics") if isinstance(item.get("statistics"), dict) else {}
    if isinstance(item.get("stats"), dict):
        stats = {**stats, **item["stats"]}
    handle = (
        _source_key(author, "uniqueId", "shortId", "secUid", "uid", "id")
        or _source_key(item, "uniqueId", "authorId", "secUid", "uid")
        or fallback_handle.lstrip("@")
    )
    channel_name = (
        _source_key(author, "nickname", "nickName", "name")
        or _source_key(item, "nickname", "authorName", "author")
        or handle
        or "Unknown creator"
    )
    title = _source_key(item, "desc", "description", "text", "title", "caption")
    views = _normalize_int(item.get("playCount") or item.get("play_count") or stats.get("playCount") or stats.get("play_count") or stats.get("play"))
    likes = _normalize_int(item.get("diggCount") or item.get("likeCount") or stats.get("digg_count") or stats.get("diggCount"))
    comments = _normalize_int(item.get("commentCount") or item.get("comment_count") or stats.get("comment_count") or stats.get("commentCount"))
    shares = _normalize_int(item.get("shareCount") or item.get("share_count") or stats.get("share_count") or stats.get("shareCount"))
    return {
        "title": title[:300],
        "url": _source_key(item, "shareUrl", "share_url", "url", "awemeUrl", "webVideoUrl", "videoUrl"),
        "thumbnail": _source_key(item, "cover", "coverUrl", "dynamicCover"),
        "views": views,
        "likes": likes,
        "comments": comments,
        "shares": shares,
        "published": _published_value(item) or _source_key(item, "createTime", "create_time"),
        "type": "video",
        "channel": channel_name,
        "handle": handle,
        "channel_url": _source_key(author, "url", "profileUrl") or _douyin_profile_url(handle or fallback_handle),
        "raw_comments": _raw_comments(item),
    }


async def search_platform_content(
    platform: str,
    query: str,
    *,
    market: str = "",
    max_results: int = 25,
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
        payload = {
            "searchQueries": [search_query],
            "resultsPerPage": safe_limit,
            "shouldDownloadVideos": False,
            "shouldDownloadCovers": False,
        }
    elif normalized_platform == "instagram":
        actor_id = "apify/instagram-hashtag-scraper"
        hashtag = "".join(ch for ch in search_query.lower() if ch.isalnum() or ch == "_")[:80]
        if not hashtag:
            return {"status": "invalid_query", "items": [], "message": "instagram hashtag query is empty after normalization"}
        payload = {
            "hashtags": [hashtag],
            "resultsLimit": safe_limit,
            "resultsType": "posts",
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

        clean_channel_name = _known_text(channel_name, handle, normalized_query) or "Unknown creator"
        items.append(
            {
                "platform": normalized_platform,
                "channel_name": clean_channel_name,
                "handle": _known_text(handle, channel_name, normalized_query),
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
