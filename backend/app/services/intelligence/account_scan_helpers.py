"""Normalization helpers for public account scanning."""
from __future__ import annotations

import os
import re
from typing import Any, Dict, List
from urllib.parse import quote_plus


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


__all__ = [name for name in globals() if name.startswith("_") or name.endswith("_KEYS")]
