"""KOL platform and creator identity helpers.

The discovery stack receives the same account in several shapes: a YouTube
``UC...`` channel id, an ``@handle``, and multiple mobile/tracking URL
variants.  ``canonical_creator_aliases`` deliberately returns *all* observed
stable aliases.  Callers dedupe by alias intersection instead of picking one
field and losing the relationship carried by the others.

This module is pure: no database reads, provider calls, scoring, or writes.
"""
from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from typing import Any
from urllib.parse import urlparse

SUPPORTED_PLATFORMS = {
    "ig",
    "instagram",
    "tt",
    "tiktok",
    "yt",
    "youtube",
    "xhs",
    "bili",
    "bilibili",
    "fb",
    "facebook",
    "reddit",
    "x",
    "twitter",
    "threads",
    "twitch",
    "pinterest",
    "vimeo",
    "discord",
    "website",
    "blog",
    "weibo",
    "douyin",
    "zhihu",
    "linkedin",
    "telegram",
    "newsletter",
    "forum",
    "community",
    "other",
}
PLATFORM_ALIASES = {
    "instagram": "ig",
    "insta": "ig",
    "tiktok": "tt",
    "youtube": "yt",
    "bilibili": "bili",
    "facebook": "fb",
    "twitter": "x",
    "blog": "website",
    "community": "forum",
}
HANDLE_RE = re.compile(r"[^a-z0-9._-]+")
YOUTUBE_CHANNEL_ID_RE = re.compile(r"^UC[0-9A-Za-z_-]{10,}$", re.IGNORECASE)

_IDENTITY_PLATFORM_ALIASES = {
    "yt": "youtube",
    "youtube_shorts": "youtube",
    "ig": "instagram",
    "insta": "instagram",
    "ins": "instagram",
    "tt": "tiktok",
    "fb": "facebook",
    "x": "twitter",
}
_IDENTITY_HOST_PLATFORMS = {
    "youtube.com": "youtube",
    "youtu.be": "youtube",
    "instagram.com": "instagram",
    "tiktok.com": "tiktok",
    "facebook.com": "facebook",
    "fb.com": "facebook",
    "twitter.com": "twitter",
    "x.com": "twitter",
}
_NATIVE_ID_FIELDS = (
    "channel_id",
    "channelId",
    "account_id",
    "accountId",
    "platform_user_id",
    "platformUserId",
    "user_id",
    "userId",
    "native_id",
    "nativeId",
)
_EXTERNAL_ID_KIND_FIELDS = (
    "external_id_kind",
    "externalIdKind",
    "external_id_type",
    "externalIdType",
)
_CREATOR_EXTERNAL_ID_KINDS = {
    "account",
    "account_id",
    "channel",
    "channel_id",
    "creator",
    "creator_id",
    "native",
    "native_id",
    "profile",
    "profile_id",
    "user",
    "user_id",
}
_HANDLE_FIELDS = (
    "handle",
    "channel_handle",
    "channelHandle",
    "username",
    "userName",
)
_PROFILE_URL_FIELDS = (
    "profile_url",
    "profileUrl",
    "channel_url",
    "channelUrl",
    "source_url",
    "url",
)
_YOUTUBE_PROFILE_TABS = frozenset(
    {"about", "channels", "community", "featured", "playlists", "shorts", "streams", "videos"}
)
_INSTAGRAM_RESERVED_ROOTS = frozenset(
    {
        "about",
        "accounts",
        "api",
        "challenge",
        "developer",
        "direct",
        "directory",
        "emails",
        "explore",
        "legal",
        "p",
        "press",
        "privacy",
        "reel",
        "reels",
        "stories",
        "terms",
        "tv",
        "web",
    }
)
_FACEBOOK_RESERVED_ROOTS = frozenset(
    {
        "ads",
        "business",
        "dialog",
        "events",
        "gaming",
        "groups",
        "hashtag",
        "help",
        "login",
        "marketplace",
        "messages",
        "pages",
        "people",
        "photos",
        "posts",
        "privacy",
        "reel",
        "reels",
        "search",
        "settings",
        "share",
        "terms",
        "videos",
        "watch",
    }
)
_TWITTER_RESERVED_ROOTS = frozenset(
    {
        "compose",
        "explore",
        "hashtag",
        "home",
        "i",
        "intent",
        "login",
        "messages",
        "notifications",
        "privacy",
        "search",
        "settings",
        "share",
        "signup",
        "tos",
    }
)


def normalize_platform(platform: str) -> str:
    clean = str(platform or "").strip().lower().replace(" ", "_")
    return PLATFORM_ALIASES.get(clean, clean)


def normalize_handle(value: str, platform: str = "") -> str:
    raw = str(value or "").strip()
    if not raw:
        return ""
    parsed = urlparse(raw if "://" in raw else f"https://placeholder/{raw}")
    candidate = raw
    if parsed.netloc and parsed.netloc != "placeholder":
        parts = [part for part in parsed.path.split("/") if part]
        candidate = parts[-1] if parts else parsed.netloc
    candidate = candidate.strip().lstrip("@").split("?", 1)[0].split("#", 1)[0].lower()
    if normalize_platform(platform) == "yt" and candidate.startswith("channel/"):
        candidate = candidate.rsplit("/", 1)[-1]
    return HANDLE_RE.sub("", candidate)


def dedup_key(platform: str, handle: str, email: str = "") -> str:
    parts = [f"{normalize_platform(platform)}:{normalize_handle(handle, platform)}"]
    email_clean = str(email or "").strip().lower()
    if email_clean:
        parts.append(f"email:{email_clean}")
    return hashlib.sha256("|".join(sorted(parts)).encode("utf-8")).hexdigest()


def canonical_identity_platform(value: Any) -> str:
    """Return the long platform name used by creator-identity aliases."""
    text = unicodedata.normalize("NFKC", str(value or "")).casefold().strip()
    text = text.replace(" ", "_")
    return _IDENTITY_PLATFORM_ALIASES.get(text, text)


def _identity_text(value: Any) -> str:
    return unicodedata.normalize("NFKC", str(value or "")).casefold().strip()


def _identity_handle(value: Any) -> str:
    text = _identity_text(value).lstrip("@").strip().strip("/")
    return "".join(char for char in text if char.isalnum() or char in "._-")


def _json_object(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    if not isinstance(value, (str, bytes)):
        return {}
    try:
        parsed = json.loads(value)
    except (TypeError, ValueError, json.JSONDecodeError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _nested_identity_payloads(item: dict[str, Any]) -> list[dict[str, Any]]:
    """Read only identity-bearing nested objects, never arbitrary text."""
    payloads = [item]
    for field in (
        "raw",
        "raw_platform_data",
        "metadata_json",
        "historical_match",
        "creator_identity",
        "online_identity_v1",
        "payload",
    ):
        value = _json_object(item.get(field))
        if value:
            payloads.append(value)
            for nested_field in ("online_identity_v1", "discovery_identity_v1"):
                nested = _json_object(value.get(nested_field))
                if nested:
                    payloads.append(nested)
    return payloads


def _platform_from_url(value: Any) -> str:
    raw = str(value or "").strip()
    if not raw:
        return ""
    try:
        parsed = urlparse(raw if "://" in raw else f"https://{raw}")
    except ValueError:
        return ""
    host = parsed.netloc.casefold().split(":", 1)[0]
    for prefix in ("www.", "m.", "mobile."):
        if host.startswith(prefix):
            host = host[len(prefix):]
    for suffix, platform in _IDENTITY_HOST_PLATFORMS.items():
        if host == suffix or host.endswith(f".{suffix}"):
            return platform
    return ""


def _youtube_url_identity(host: str, parts: list[str]) -> tuple[set[str], str]:
    aliases: set[str] = set()
    if (
        host != "youtu.be"
        and len(parts) in {2, 3}
        and parts[0].casefold() == "channel"
        and (len(parts) == 2 or parts[2].casefold() in _YOUTUBE_PROFILE_TABS)
    ):
        native_id = _identity_text(parts[1])
        if YOUTUBE_CHANNEL_ID_RE.fullmatch(parts[1]):
            aliases.add(f"youtube:id:{native_id}")
            return aliases, f"/channel/{native_id}"
        return aliases, ""
    if host == "youtu.be" or not parts:
        return aliases, ""
    first = parts[0]
    is_profile_root_or_tab = len(parts) == 1 or (
        len(parts) == 2 and parts[1].casefold() in _YOUTUBE_PROFILE_TABS
    )
    if first.startswith("@") and is_profile_root_or_tab:
        handle = _identity_handle(first)
        if handle:
            aliases.add(f"youtube:handle:{handle}")
            return aliases, f"/@{handle}"
    is_legacy_locator = (
        len(parts) in {2, 3}
        and first.casefold() in {"c", "user"}
        and (len(parts) == 2 or parts[2].casefold() in _YOUTUBE_PROFILE_TABS)
    )
    if is_legacy_locator:
        locator = _identity_handle(parts[1])
        if locator:
            route = first.casefold()
            aliases.add(f"youtube:locator:{route}:{locator}")
            return aliases, f"/{route}/{locator}"
    return aliases, ""


def _handle_url_identity(
    platform: str,
    parts: list[str],
    *,
    reserved_roots: set[str],
    require_at: bool = False,
    reject_php: bool = False,
) -> tuple[set[str], str]:
    if len(parts) != 1:
        return set(), ""
    first = parts[0]
    handle = _identity_handle(first)
    route = _identity_text(first)
    invalid = (
        not handle
        or route in reserved_roots
        or "%" in first
        or (require_at and not first.startswith("@"))
        or (reject_php and route.endswith(".php"))
    )
    if invalid:
        return set(), ""
    prefix = "@" if require_at else ""
    return {f"{platform}:handle:{handle}"}, f"/{prefix}{handle}"


def _url_identity_aliases(value: Any, platform_hint: str = "") -> set[str]:
    raw = str(value or "").strip()
    if not raw:
        return set()
    try:
        parsed = urlparse(raw if "://" in raw else f"https://{raw}")
    except ValueError:
        return set()
    if not parsed.netloc:
        return set()
    platform = _platform_from_url(raw)
    hinted_platform = canonical_identity_platform(platform_hint)
    # A social creator identity is only carried by that platform's own host.
    # Historical rows sometimes contain a commerce/referral URL in
    # ``profile_url`` (for example two unrelated YouTube rows sharing one
    # Amazon product link).  Inheriting the YouTube hint for that external URL
    # makes the product URL a shared creator alias and incorrectly folds the
    # two people.  A website/blog row is the intentional exception: its custom
    # host is the identity being represented.
    if not platform and hinted_platform == "website":
        platform = "website"
    if not platform:
        return set()
    host = parsed.netloc.casefold().split(":", 1)[0]
    for prefix in ("www.", "m.", "mobile."):
        if host.startswith(prefix):
            host = host[len(prefix):]
    parts = [part for part in parsed.path.split("/") if part]
    if platform == "youtube":
        aliases, profile_path = _youtube_url_identity(host, parts)
    elif platform == "instagram":
        aliases, profile_path = _handle_url_identity(
            platform,
            parts,
            reserved_roots=_INSTAGRAM_RESERVED_ROOTS,
        )
    elif platform == "tiktok":
        aliases, profile_path = _handle_url_identity(
            platform,
            parts,
            reserved_roots=set(),
            require_at=True,
        )
    elif platform == "facebook":
        aliases, profile_path = _handle_url_identity(
            platform,
            parts,
            reserved_roots=_FACEBOOK_RESERVED_ROOTS,
            reject_php=True,
        )
    elif platform == "twitter":
        aliases, profile_path = _handle_url_identity(
            platform,
            parts,
            reserved_roots=_TWITTER_RESERVED_ROOTS,
        )
    else:
        aliases, profile_path = set(), ""
    if platform == "website":
        # Custom websites are the intentional host-hint exception. Preserve
        # their path contract while still dropping query/fragment trackers.
        profile_path = unicodedata.normalize("NFKC", parsed.path).rstrip("/").casefold() or "/"
    if profile_path:
        aliases.add(f"{platform}:url:https://{host}{profile_path}")
    return aliases


def explicit_creator_external_identity(
    item: dict[str, Any] | None,
) -> tuple[str, str]:
    """Return a typed external creator id, rejecting ambiguous content ids."""
    if not isinstance(item, dict):
        return "", ""
    external_kind = next(
        (
            _identity_text(item.get(field)).replace("-", "_").replace(" ", "_")
            for field in _EXTERNAL_ID_KIND_FIELDS
            if _identity_text(item.get(field))
        ),
        "",
    )
    external_id = _identity_text(
        item.get("external_id") or item.get("externalId")
    )
    if external_id and external_kind in _CREATOR_EXTERNAL_ID_KINDS:
        return external_id, external_kind
    return "", ""


def canonical_creator_aliases(item: dict[str, Any] | None) -> set[str]:
    """Return every stable identity alias observed for one creator-shaped row.

    A YouTube ``UC...`` value stored in ``handle`` is treated as a native id,
    not a custom handle.  When a provider supplies both channel id and handle,
    the returned set bridges legacy ``UC`` pool rows to modern ``@handle``
    candidates without guessing from display names.
    """
    if not isinstance(item, dict):
        return set()
    payloads = _nested_identity_payloads(item)
    platform = canonical_identity_platform(item.get("platform"))
    if not platform:
        for payload in payloads:
            for field in _PROFILE_URL_FIELDS:
                platform = _platform_from_url(payload.get(field))
                if platform:
                    break
            if platform:
                break
    aliases: set[str] = set()
    pool_alias = ""
    pool_id = item.get("kol_pool_id") or item.get("history_kol_pool_id")
    historical = _json_object(item.get("historical_match"))
    pool_id = pool_id or historical.get("kol_pool_id")
    try:
        if int(pool_id or 0) > 0:
            pool_alias = f"pool:{int(pool_id)}"
    except (TypeError, ValueError):
        pass
    for payload in payloads:
        payload_platform = canonical_identity_platform(payload.get("platform")) or platform
        for field in _NATIVE_ID_FIELDS:
            native_id = _identity_text(payload.get(field))
            if payload_platform and native_id:
                aliases.add(f"{payload_platform}:id:{native_id}")
        external_id, _external_kind = explicit_creator_external_identity(payload)
        if payload_platform and external_id:
            aliases.add(f"{payload_platform}:id:{external_id}")
        for field in _HANDLE_FIELDS:
            raw_handle = str(payload.get(field) or "").strip()
            handle = _identity_handle(raw_handle)
            if not payload_platform or not handle:
                continue
            if payload_platform == "youtube" and YOUTUBE_CHANNEL_ID_RE.fullmatch(raw_handle.lstrip("@")):
                aliases.add(f"youtube:id:{handle}")
            else:
                aliases.add(f"{payload_platform}:handle:{handle}")
        for field in _PROFILE_URL_FIELDS:
            aliases.update(_url_identity_aliases(payload.get(field), payload_platform))
    if not aliases and pool_alias:
        aliases.add(pool_alias)
    return aliases


def canonical_creator_key(item: dict[str, Any] | None) -> str:
    """Choose a deterministic display/storage key from the full alias set."""
    aliases = canonical_creator_aliases(item)
    for prefix in ("pool:", ":id:", ":handle:", ":locator:", ":url:"):
        matches = sorted(
            alias
            for alias in aliases
            if (alias.startswith(prefix) if prefix == "pool:" else prefix in alias)
        )
        if matches:
            return matches[0]
    return ""


def canonical_aliases_overlap(left: dict[str, Any] | None, right: dict[str, Any] | None) -> bool:
    """True only when two rows share an observed stable alias."""
    return bool(canonical_creator_aliases(left).intersection(canonical_creator_aliases(right)))
