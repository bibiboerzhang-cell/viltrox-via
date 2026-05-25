"""KOL platform and handle identity helpers."""
from __future__ import annotations

import hashlib
import re
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
