"""Safe identity and activity projections for strict online KOL discovery.

This module deliberately accepts only bounded public account identifiers and
auditable platform-content URLs.  Provider payloads remain in memory; callers
may persist only ``safe_native_identity`` and the opaque fingerprint.
"""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import re
import unicodedata
from typing import Any
from urllib.parse import parse_qs, unquote, urlsplit

from app.domains.kol import profile_recall_qualification
from app.domains.kol.search_sessions_serde import (
    contains_contact_route,
    project_public_profile_text,
    project_public_profile_url,
)


AUDITABLE_VIDEO_SOURCES = frozenset({
    "account_recent_video",
    "platform_content_search",
    "platform_video_api",
    "provider_video_item",
    "representative_video",
})
_VIDEO_ID_RE = re.compile(r"^[A-Za-z0-9_.-]{5,160}$")
_YT_CHANNEL_ID_RE = re.compile(r"^UC[A-Za-z0-9_-]{6,64}$")
from app.domains.kol.search_platform_policy import STRICT_DISCOVERY_PLATFORMS
_SUPPORTED_PLATFORMS = frozenset(STRICT_DISCOVERY_PLATFORMS)
_VIDEO_HOSTS = {
    "youtube": frozenset({"youtube.com", "www.youtube.com", "m.youtube.com", "youtu.be", "www.youtu.be"}),
    "instagram": frozenset({"instagram.com", "www.instagram.com"}),
    "tiktok": frozenset({"tiktok.com", "www.tiktok.com", "m.tiktok.com"}),
    "x": frozenset({"x.com", "www.x.com", "twitter.com", "www.twitter.com"}),
    "reddit": frozenset({"reddit.com", "www.reddit.com", "old.reddit.com"}),
}


def _text(value: Any) -> str:
    return str(value or "").strip()


def normalize_platform(value: Any) -> str:
    platform = project_public_profile_text(value, limit=40).lower()
    return {"yt": "youtube", "ig": "instagram", "tt": "tiktok", "twitter": "x"}.get(platform, platform)


def safe_native_identity(raw: dict[str, Any], *, platform: Any = "") -> dict[str, str]:
    """Return a bounded non-contact native-ID projection, never a raw blob."""
    platform_key = normalize_platform(platform or raw.get("platform"))
    projected: dict[str, str] = {}
    for field in ("channel_id", "account_id", "platform_user_id", "native_id"):
        value = unicodedata.normalize("NFKC", _text(raw.get(field)))[:160]
        if (
            platform_key
            and value
            and (not contains_contact_route(value) or (platform_key == "x" and value.isdigit() and len(value) <= 25))
            and all(char.isalnum() or char in "._-" for char in value)
        ):
            projected[field] = value
    if platform_key == "youtube":
        channel_id = projected.get("channel_id", "")
        if channel_id and not _YT_CHANNEL_ID_RE.fullmatch(channel_id):
            projected.pop("channel_id", None)
    return projected


def _profile_locator_values(raw: dict[str, Any]) -> list[str]:
    return [
        value
        for key in ("profile_url", "channel_url")
        if (value := _text(raw.get(key)))
    ]


def _youtube_channel_id_from_url(value: Any) -> str:
    try:
        parsed = urlsplit(_text(value))
    except ValueError:
        return ""
    host = (parsed.hostname or "").lower()
    parts = [unquote(part) for part in parsed.path.split("/") if part]
    if host in {"youtube.com", "www.youtube.com", "m.youtube.com"} and len(parts) == 2:
        if parts[0].lower() == "channel" and _YT_CHANNEL_ID_RE.fullmatch(parts[1]):
            return parts[1]
    return ""


def _explicit_handle(raw: dict[str, Any]) -> str:
    for field in ("handle", "channel_handle", "username", "ownerUsername"):
        value = project_public_profile_text(raw.get(field), limit=160).lstrip("@")
        value = unicodedata.normalize("NFKC", value)
        if value and all(char.isalnum() or char in "._-" for char in value):
            return value
    return ""


def stable_creator_identity(raw: dict[str, Any]) -> dict[str, Any]:
    """Project a platform account identity; display names are never handles."""
    platform = normalize_platform(raw.get("platform"))
    if platform not in _SUPPORTED_PLATFORMS:
        return {"platform": platform, "handle": "", "profile_url": "", "native_ids": {}, "passed": False}

    native_ids = safe_native_identity(raw, platform=platform)
    locators = _profile_locator_values(raw)
    if platform == "youtube" and not native_ids.get("channel_id"):
        for locator in locators:
            channel_id = _youtube_channel_id_from_url(locator)
            if channel_id:
                native_ids["channel_id"] = channel_id
                break

    explicit_handle = _explicit_handle(raw)
    handle = explicit_handle or (native_ids.get("channel_id") if platform == "youtube" else "")
    if not handle:
        return {"platform": platform, "handle": "", "profile_url": "", "native_ids": native_ids, "passed": False}

    profile_url = ""
    for locator in locators:
        if (
            platform == "youtube"
            and native_ids.get("channel_id")
            and _youtube_channel_id_from_url(locator) == native_ids["channel_id"]
        ):
            profile_url = f"https://www.youtube.com/channel/{native_ids['channel_id']}"
            break
        profile_url = project_public_profile_url(platform, handle, locator)
        if profile_url:
            break
    # A supplied account locator must match the projected identity.  Content
    # URLs are handled separately and are not account locators.
    if locators and not profile_url:
        return {"platform": platform, "handle": handle, "profile_url": "", "native_ids": native_ids, "passed": False}
    if not profile_url:
        if platform == "youtube" and native_ids.get("channel_id"):
            profile_url = f"https://www.youtube.com/channel/{native_ids['channel_id']}"
        elif platform == "youtube":
            profile_url = f"https://www.youtube.com/@{handle}"
        elif platform == "instagram":
            profile_url = f"https://www.instagram.com/{handle}/"
        elif platform == "tiktok":
            profile_url = f"https://www.tiktok.com/@{handle}"
        elif platform == "x":
            profile_url = f"https://x.com/{handle}"
        elif platform == "reddit":
            profile_url = f"https://www.reddit.com/user/{handle}/"
    if platform in {"x", "reddit"}:
        from app.platform.industry_crawlers.reddit_people_normalize import person_handle
        if person_handle(platform, profile_url).casefold() != handle.casefold():
            return {"platform": platform, "handle": handle, "profile_url": "", "native_ids": native_ids, "passed": False}
    return {
        "platform": platform,
        "handle": handle,
        "profile_url": profile_url,
        "native_ids": native_ids,
        "passed": bool(platform and handle and profile_url),
    }


def is_platform_video_url(value: Any, *, platform: Any) -> bool:
    """Accept only exact known hosts and the candidate's own platform."""
    raw = _text(value)
    platform_key = normalize_platform(platform)
    if not raw or platform_key not in _VIDEO_HOSTS:
        return False
    try:
        parsed = urlsplit(raw)
        if parsed.port:
            return False
    except ValueError:
        return False
    host = (parsed.hostname or "").lower()
    if parsed.scheme.lower() not in {"http", "https"} or host not in _VIDEO_HOSTS[platform_key] or parsed.username or parsed.password:
        return False
    path = unquote(parsed.path).lower()
    if platform_key == "youtube":
        if host in {"youtu.be", "www.youtu.be"}:
            return bool(path.strip("/"))
        return (path == "/watch" and bool(parse_qs(parsed.query).get("v"))) or path.startswith("/shorts/")
    if platform_key == "instagram":
        return path.startswith("/p/") or path.startswith("/reel/")
    if platform_key == "x":
        return bool(re.fullmatch(r"/[a-z0-9_]{1,15}/status/[0-9]+/?", path))
    if platform_key == "reddit":
        return bool(re.fullmatch(r"/(?:r/[a-z0-9_]+/)?comments/[a-z0-9]+(?:/[^/]+)?/?", path))
    return "/video/" in path and bool(path.rsplit("/video/", 1)[-1].strip("/"))


def _stable_video_identity(raw: dict[str, Any], *, platform: str) -> tuple[str, str]:
    supplied_url = ""
    for key in ("content_url", "video_url", "post_url"):
        if (supplied_url := _text(raw.get(key))):
            break
    if supplied_url and not is_platform_video_url(supplied_url, platform=platform):
        return "", ""
    for key in ("video_id", "native_video_id", "content_id"):
        value = _text(raw.get(key))
        evidence_platform = normalize_platform(raw.get("platform"))
        if _VIDEO_ID_RE.fullmatch(value) and (supplied_url or evidence_platform == platform):
            return "video_id", value
    if supplied_url:
        return "content_url", supplied_url
    return "", ""


def latest_video_evidence(raw: dict[str, Any]) -> dict[str, Any]:
    """Select the newest auditable real-video evidence for this platform."""
    platform = normalize_platform(raw.get("platform"))
    candidates: list[dict[str, Any]] = []
    latest = raw.get("latest_real_video")
    if isinstance(latest, dict):
        candidates.append(latest)
    for key in ("representative_evidence", "video_evidence", "recent_videos"):
        values = raw.get(key)
        if isinstance(values, list):
            candidates.extend(item for item in values[:12] if isinstance(item, dict))
    flat_url = _text(raw.get("content_url") or raw.get("source_url"))
    flat_posted_at = _text(raw.get("posted_at") or raw.get("published_at") or raw.get("published"))
    if flat_posted_at and is_platform_video_url(flat_url, platform=platform):
        candidates.append({
            "posted_at": flat_posted_at,
            "content_url": flat_url,
            "title": _text(raw.get("sample_title") or raw.get("title")),
            "source": "platform_content_search",
        })

    accepted: list[dict[str, Any]] = []
    for candidate in candidates:
        source = _text(candidate.get("source")).lower()
        posted_at = _text(candidate.get("posted_at") or candidate.get("published_at"))
        identity_kind, identity = _stable_video_identity(candidate, platform=platform)
        if source not in AUDITABLE_VIDEO_SOURCES or not posted_at or not identity:
            continue
        accepted.append({
            "posted_at": posted_at,
            "evidence_type": "post" if platform in {"x", "reddit"} else "video",
            **({"platform": platform, "content_kind": "post"} if platform in {"x", "reddit"} else {}),
            identity_kind: identity,
            "title": _text(candidate.get("title"))[:500],
            "is_active": candidate.get("is_active") is not False,
            "source": source,
        })
    if not accepted:
        return {}

    def timestamp(item: dict[str, Any]) -> float:
        value = _text(item.get("posted_at")).replace("Z", "+00:00")
        try:
            parsed = datetime.fromisoformat(value)
        except ValueError:
            return float("-inf")
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.timestamp()

    return max(accepted, key=timestamp)


def canonical_fingerprint(item: dict[str, Any]) -> str:
    """Hash the full server alias set so Unicode/raw aliases never leave memory."""
    aliases = sorted(profile_recall_qualification.canonical_creator_aliases(item))
    if not aliases:
        return ""
    return hashlib.sha256("\x00".join(aliases).encode("utf-8")).hexdigest()
