"""Pure social-profile identity parsing shared below services and repositories."""
from __future__ import annotations

import re
from typing import Optional
from urllib.parse import urlparse


def normalize_claimed_handle(handle: str, platform: str) -> str:
    value = (handle or "").strip()
    plat = (platform or "").lower().strip()
    if not value:
        return ""
    if plat == "reddit":
        if value.lower().startswith("u/"):
            return "u/" + value[2:].lstrip("/")
        if value.lower().startswith("user/"):
            return "u/" + value[5:].lstrip("/")
        return "u/" + value.lstrip("@/")
    if plat in {"instagram", "tiktok", "youtube", "facebook", "twitter"}:
        return "@" + value.lstrip("@")
    return value


def detect_platform_from_profile_url(url: str) -> Optional[str]:
    """Return the supported social platform encoded by a profile URL."""

    if not url:
        return None
    host = (urlparse(url).netloc or "").lower()
    if "youtube.com" in host or "youtu.be" in host:
        return "youtube"
    if "instagram.com" in host:
        return "instagram"
    if "tiktok.com" in host:
        return "tiktok"
    if "reddit.com" in host:
        return "reddit"
    if "facebook.com" in host or "fb.com" in host:
        return "facebook"
    if "twitter.com" in host or "x.com" in host:
        return "twitter"
    return None


def extract_handle_from_profile_url(
    url: str,
    platform: Optional[str] = None,
) -> str:
    """Extract a claimed handle from a supported social profile URL."""

    if not url:
        return ""
    resolved_platform = platform or detect_platform_from_profile_url(url)
    if not resolved_platform:
        return ""
    path = urlparse(url.strip()).path.strip("/")
    if not path:
        return ""
    path = path.split("?")[0].split("#")[0]

    if resolved_platform == "instagram":
        match = re.match(r"^([^/]+)", path)
        return match.group(1) if match else ""
    if resolved_platform == "tiktok":
        match = re.match(r"^@([^/]+)", path)
        return match.group(1) if match else path.split("/")[0]
    if resolved_platform == "youtube":
        if path.startswith("@"):
            return path[1:].split("/")[0]
        if path.startswith("c/"):
            return path[2:].split("/")[0]
        if path.startswith("channel/"):
            return path[8:].split("/")[0]
        if path.startswith("user/"):
            return path[5:].split("/")[0]
        return ""
    if resolved_platform == "reddit":
        match = re.match(r"^u(?:ser)?/([^/]+)", path)
        return match.group(1) if match else ""
    if resolved_platform == "facebook":
        if path.startswith("pages/"):
            parts = path.split("/")
            return parts[-1] if len(parts) >= 3 else ""
        return path.split("/")[0]
    if resolved_platform == "twitter":
        return path.split("/")[0]
    return ""
