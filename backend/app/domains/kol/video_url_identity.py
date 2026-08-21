"""Strict, provider-safe identity parsing for supported social video URLs."""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlparse

from app.domains.kol.url_deep_crawl_helpers import _video_id


MAX_VIDEO_URL_LENGTH = 2048
SUPPORTED_VIDEO_HOSTS = {
    "youtube": ("youtube.com", "youtu.be"),
    "instagram": ("instagram.com",),
    "tiktok": ("tiktok.com",),
}
VIDEO_ID_PATTERNS = {
    "youtube": re.compile(r"^[A-Za-z0-9_-]{6,32}$"),
    "instagram": re.compile(r"^[A-Za-z0-9_-]{3,80}$"),
    "tiktok": re.compile(r"^[0-9]{8,32}$"),
}


class VideoUrlIdentityError(ValueError):
    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


@dataclass(frozen=True)
class VideoUrlIdentity:
    normalized_url: str
    platform: str
    video_id: str


def _text(value: Any) -> str:
    return str(value or "").strip()


def _host_matches(host: str, domains: tuple[str, ...]) -> bool:
    return any(host == domain or host.endswith("." + domain) for domain in domains)


def parse_supported_video_url(value: Any) -> VideoUrlIdentity:
    """Fail closed unless ``value`` is a public URL with a strict video identity."""

    raw = _text(value)
    if (
        not raw
        or len(raw) > MAX_VIDEO_URL_LENGTH
        or any(character.isspace() for character in raw)
    ):
        raise VideoUrlIdentityError("video_url_invalid")
    try:
        parsed = urlparse(raw)
        hostname = parsed.hostname
    except ValueError as exc:
        raise VideoUrlIdentityError("video_url_invalid") from exc
    if parsed.scheme.lower() not in {"http", "https"} or not hostname:
        raise VideoUrlIdentityError("video_url_invalid")
    if parsed.username or parsed.password:
        raise VideoUrlIdentityError("video_url_credentials_forbidden")
    try:
        port = parsed.port
    except ValueError as exc:
        raise VideoUrlIdentityError("video_url_invalid_port") from exc
    if port not in (None, 80, 443):
        raise VideoUrlIdentityError("video_url_port_forbidden")

    host = hostname.lower().rstrip(".")
    platform = next(
        (
            name
            for name, domains in SUPPORTED_VIDEO_HOSTS.items()
            if _host_matches(host, domains)
        ),
        "",
    )
    if not platform:
        raise VideoUrlIdentityError("video_platform_unsupported")
    video_id = _text(
        _video_id(
            platform,
            host.removeprefix("www."),
            parsed.path.strip("/"),
            parsed.query,
        )
    )
    if not video_id:
        raise VideoUrlIdentityError("explicit_video_url_required")
    if not VIDEO_ID_PATTERNS[platform].fullmatch(video_id):
        raise VideoUrlIdentityError("video_id_invalid")
    if platform == "youtube":
        normalized_url = f"https://www.youtube.com/watch?v={video_id}"
    elif platform == "instagram":
        parts = [part for part in parsed.path.split("/") if part]
        route = next(
            (part.lower() for part in parts if part.lower() in {"p", "reel", "tv"}),
            "p",
        )
        normalized_url = f"https://www.instagram.com/{route}/{video_id}/"
    else:
        parts = [part for part in parsed.path.split("/") if part]
        video_index = next(
            (index for index, part in enumerate(parts) if part.lower() == "video"),
            -1,
        )
        handle = parts[video_index - 1] if video_index > 0 else ""
        prefix = f"/{handle}" if handle.startswith("@") else ""
        normalized_url = f"https://www.tiktok.com{prefix}/video/{video_id}/"
    return VideoUrlIdentity(normalized_url, platform, video_id)
