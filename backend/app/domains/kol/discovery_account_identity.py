"""Conservative identity proof for exact brand-like discovery handles.

Imported publisher, retailer and website rows sometimes reuse ``handle`` for
a brand mentioned in an old spreadsheet.  A bare brand handle on those
sources is not proof that the row is the brand's official social account.
This module keeps that source/profile validation separate from the broader
discovery filters while remaining pure: no provider, database or score writes.
"""
from __future__ import annotations

import re
from typing import Any
from urllib.parse import unquote, urlparse


_NON_CREATOR_EXACT_HANDLE_PLATFORMS = frozenset({
    "blog", "community", "dealer", "forum", "media", "newsletter", "other",
    "press", "publication", "retailer", "store", "website",
})
_CREATOR_PROFILE_HOST_PLATFORMS = {
    "youtube.com": "youtube",
    "youtu.be": "youtube",
    "instagram.com": "instagram",
    "tiktok.com": "tiktok",
    "facebook.com": "facebook",
    "fb.com": "facebook",
    "twitter.com": "twitter",
    "x.com": "twitter",
}
_EXACT_HANDLE_PLATFORM_ALIASES = {
    "fb": "facebook",
    "ig": "instagram",
    "insta": "instagram",
    "tt": "tiktok",
    "x": "twitter",
    "yt": "youtube",
    "youtube_shorts": "youtube",
}


def _normalized_brand_locator(value: Any) -> str:
    return re.sub(r"[^a-z0-9]", "", unquote(str(value or "")).lower().lstrip("@"))


def _profile_url_confirms_exact_brand_handle(
    value: Any,
    *,
    platform: str,
    brand_norm: str,
) -> bool:
    """Require a social profile URL to carry the same public brand locator.

    A YouTube ``/channel/UC...`` URL proves a channel but not that its public
    handle equals an imported brand word, so it deliberately fails open here.
    Explicit ``official`` identity or corporate bio evidence can still reach
    the normal high-confidence signal path in ``discovery_filters``.
    """
    raw = str(value or "").strip()
    if not raw:
        return False
    try:
        parsed = urlparse(raw if "://" in raw else f"https://{raw}")
    except ValueError:
        return False
    host = parsed.netloc.lower().split(":", 1)[0]
    for prefix in ("www.", "m.", "mobile."):
        if host.startswith(prefix):
            host = host[len(prefix):]
    url_platform = ""
    for suffix, candidate in _CREATOR_PROFILE_HOST_PLATFORMS.items():
        if host == suffix or host.endswith(f".{suffix}"):
            url_platform = candidate
            break
    if not url_platform or (platform and platform != url_platform):
        return False
    parts = [unquote(part).strip() for part in parsed.path.split("/") if part.strip()]
    locator = ""
    if url_platform == "youtube":
        if parts and parts[0].startswith("@"):
            locator = parts[0]
        elif len(parts) >= 2 and parts[0].lower() in {"c", "user"}:
            locator = parts[1]
    elif url_platform == "tiktok":
        if parts and parts[0].startswith("@"):
            locator = parts[0]
    elif parts:
        locator = parts[0]
    return bool(locator) and _normalized_brand_locator(locator) == brand_norm


def exact_brand_handle_confirmed(item: dict[str, Any], brand_norm: str) -> bool:
    """Keep the legacy exact-handle shortcut conservative across sources."""
    platform = str(
        item.get("platform") or item.get("normalized_platform") or ""
    ).strip().lower()
    platform = _EXACT_HANDLE_PLATFORM_ALIASES.get(platform, platform)
    if platform in _NON_CREATOR_EXACT_HANDLE_PLATFORMS:
        return False
    profile_urls = [
        item.get(field)
        for field in ("profile_url", "channel_url", "source_url", "url")
        if str(item.get(field) or "").strip()
    ]
    # Provider candidates can arrive before a URL.  Exact brand handle remains
    # a strong signal unless the source explicitly says it is non-creator.
    if not profile_urls:
        return True
    return any(
        _profile_url_confirms_exact_brand_handle(
            value,
            platform=platform,
            brand_norm=brand_norm,
        )
        for value in profile_urls
    )


__all__ = ["exact_brand_handle_confirmed"]
