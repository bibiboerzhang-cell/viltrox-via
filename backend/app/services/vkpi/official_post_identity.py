"""Shared post identity helpers for V-KPI official-channel content."""
from __future__ import annotations

import hashlib
import re
import urllib.parse
from typing import Any


def _text(*values: Any) -> str:
    for value in values:
        text = str(value or "").strip()
        if text:
            return text
    return ""


def _platform(value: Any) -> str:
    return _text(value).lower().replace("twitter", "x")


def _clean_id(value: Any) -> str:
    text = _text(value)
    if text.startswith("t3_"):
        return text[3:]
    return text.strip()


def _normalize_url(value: Any) -> str:
    raw = _text(value)
    if not raw or not raw.startswith(("http://", "https://")):
        return ""
    parsed = urllib.parse.urlparse(raw)
    scheme = "https"
    host = (parsed.hostname or "").lower()
    if host in {"mobile.twitter.com", "twitter.com", "www.twitter.com"}:
        host = "x.com"
    if host in {"m.facebook.com", "web.facebook.com"}:
        host = "www.facebook.com"
    if host and not host.startswith("www.") and host in {"instagram.com", "youtube.com", "reddit.com"}:
        host = f"www.{host}"
    path = re.sub(r"/+", "/", parsed.path or "/")
    if path != "/":
        path = path.rstrip("/")
    return urllib.parse.urlunparse((scheme, host, path, "", "", ""))


def _youtube_id(post: dict[str, Any], url: str) -> str:
    raw_id = post.get("id")
    if isinstance(raw_id, dict):
        candidate = _text(raw_id.get("videoId"), raw_id.get("id"))
        if candidate:
            return candidate
    for candidate in (post.get("source_id"), raw_id, post.get("videoId"), post.get("video_id")):
        value = _text(candidate)
        if re.fullmatch(r"[A-Za-z0-9_-]{11}", value):
            return value
    parsed = urllib.parse.urlparse(url)
    query_id = urllib.parse.parse_qs(parsed.query).get("v", [""])[0]
    if query_id:
        return query_id
    parts = [part for part in parsed.path.split("/") if part]
    if parsed.hostname and parsed.hostname.endswith("youtu.be") and parts:
        return parts[0]
    if "shorts" in parts:
        index = parts.index("shorts")
        if index + 1 < len(parts):
            return parts[index + 1]
    return ""


def _instagram_id(post: dict[str, Any], url: str) -> str:
    for candidate in (post.get("shortCode"), post.get("short_code"), post.get("source_id"), post.get("id")):
        value = _clean_id(candidate)
        if value:
            return value
    parts = [part for part in urllib.parse.urlparse(url).path.split("/") if part]
    for marker in ("p", "reel", "tv"):
        if marker in parts:
            index = parts.index(marker)
            if index + 1 < len(parts):
                return parts[index + 1]
    return ""


def _tiktok_id(post: dict[str, Any], url: str) -> str:
    for candidate in (post.get("source_id"), post.get("id"), post.get("awemeId")):
        value = _clean_id(candidate)
        if value:
            return value
    parts = [part for part in urllib.parse.urlparse(url).path.split("/") if part]
    if "video" in parts:
        index = parts.index("video")
        if index + 1 < len(parts):
            return parts[index + 1]
    return ""


def _facebook_id(post: dict[str, Any], url: str) -> str:
    for candidate in (post.get("postId"), post.get("post_id"), post.get("source_id"), post.get("id")):
        value = _clean_id(candidate)
        if value:
            return value
    parsed = urllib.parse.urlparse(url)
    query = urllib.parse.parse_qs(parsed.query)
    for key in ("story_fbid", "fbid", "id"):
        if query.get(key, [""])[0]:
            return query[key][0]
    return ""


def _reddit_id(post: dict[str, Any], url: str) -> str:
    for candidate in (post.get("parsedId"), post.get("source_id"), post.get("id"), post.get("name")):
        value = _clean_id(candidate)
        if value:
            return value
    path = urllib.parse.urlparse(url).path
    if "/comments/" in path:
        return _clean_id(path.split("/comments/", 1)[1].split("/", 1)[0])
    return ""


def _x_id(post: dict[str, Any], url: str) -> str:
    for candidate in (post.get("source_id"), post.get("id"), post.get("tweetId"), post.get("tweet_id")):
        value = _clean_id(candidate)
        if value:
            return value
    parts = [part for part in urllib.parse.urlparse(url).path.split("/") if part]
    for marker in ("status", "statuses"):
        if marker in parts:
            index = parts.index(marker)
            if index + 1 < len(parts):
                return parts[index + 1]
    return ""


def canonical_post_identity(platform: str, post: dict[str, Any]) -> dict[str, str]:
    """Return the shared official-post identity contract."""

    platform_key = _platform(platform or post.get("platform"))
    raw_url = _text(
        post.get("url"),
        post.get("postUrl"),
        post.get("webVideoUrl"),
        post.get("twitterUrl"),
        post.get("permalink"),
    )
    canonical_url = _normalize_url(raw_url)
    if platform_key == "youtube":
        provider_post_id = _youtube_id(post, raw_url)
        if provider_post_id:
            canonical_url = f"https://www.youtube.com/watch?v={provider_post_id}"
    elif platform_key == "instagram":
        provider_post_id = _instagram_id(post, raw_url)
    elif platform_key == "tiktok":
        provider_post_id = _tiktok_id(post, raw_url)
    elif platform_key == "facebook":
        provider_post_id = _facebook_id(post, raw_url)
    elif platform_key == "reddit":
        provider_post_id = _reddit_id(post, raw_url)
    elif platform_key == "x":
        provider_post_id = _x_id(post, raw_url)
        if provider_post_id and not canonical_url:
            canonical_url = f"https://x.com/i/web/status/{provider_post_id}"
    else:
        provider_post_id = _clean_id(post.get("source_id") or post.get("id"))

    if provider_post_id:
        canonical_post_uid = f"{platform_key}:{provider_post_id}"
        source = "provider_post_id"
    elif canonical_url:
        digest = hashlib.sha256(canonical_url.encode("utf-8")).hexdigest()[:24]
        canonical_post_uid = f"{platform_key}:url:{digest}"
        source = "canonical_url"
    else:
        canonical_post_uid = ""
        source = "missing"

    return {
        "platform": platform_key,
        "canonical_post_uid": canonical_post_uid,
        "provider_post_id": provider_post_id,
        "canonical_url": canonical_url,
        "post_identity_source": source,
    }


def legacy_post_uid(platform: str, post: dict[str, Any], *preferred_values: Any) -> str:
    """Return the current metrics/comment storage key without forcing migration."""

    for value in preferred_values:
        text = _text(value)
        if text:
            return text
    identity = canonical_post_identity(platform, post)
    return identity["provider_post_id"] or identity["canonical_url"]
