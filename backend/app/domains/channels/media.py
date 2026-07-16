"""Shared media URL extraction helpers for channel services."""
from __future__ import annotations

from app.domains.channels.common import *


def _looks_like_image_media_url(url: str, *, key_hint: str = "") -> bool:
    parsed = urllib.parse.urlparse(url)
    host = (parsed.hostname or "").lower()
    path = parsed.path.lower()
    if not host or host.startswith("v.redd.it") or host in {"facebook.com", "www.facebook.com", "m.facebook.com"}:
        return False
    image_hosts = (
        "cdninstagram.com",
        "fbcdn.net",
        "xx.fbcdn.net",
        "ytimg.com",
        "googleusercontent.com",
        "tiktokcdn.com",
        "tiktokcdn-eu.com",
        "byteoversea.com",
        "apifyusercontent.com",
        "redd.it",
        "redditmedia.com",
        "twimg.com",
    )
    if any(part in host for part in image_hosts):
        return True
    if path.endswith((".jpg", ".jpeg", ".png", ".webp", ".gif", ".avif")):
        return True
    return key_hint not in {"url", "permalink", "postUrl", "topLevelUrl", "facebookUrl"}


def _media_type_kind(value: Any) -> str:
    text = _text(value).lower()
    if text in {"video", "reel", "reels", "clips"}:
        return "video"
    if text in {"sidecar", "carousel", "album"}:
        return "carousel"
    if text in {"image", "photo"}:
        return "image"
    return text


def _media_urls(*values: Any) -> list[str]:
    candidates: list[tuple[int, int, str, str]] = []
    seen: set[str] = set()

    def score(value: Any) -> int:
        if not isinstance(value, dict):
            return 0
        width = _int(
            value.get("width"),
            _int(value.get("config_width"), _int(value.get("naturalWidth"), _int(value.get("displayWidth")))),
        )
        height = _int(
            value.get("height"),
            _int(value.get("config_height"), _int(value.get("naturalHeight"), _int(value.get("displayHeight")))),
        )
        return max(width * height, width, height)

    def push(value: Any, *, key_hint: str = "", resource_score: int = 0) -> None:
        if isinstance(value, dict):
            next_score = max(resource_score, score(value))
            for key in (
                "displayUrl",
                "imageUrl",
                "src",
                "uri",
                "thumbnailUrl",
                "thumbnail",
                "picture",
                "photo_image",
                "thumbnailImage",
                "profilePictureUrl",
                "profilePicUrlHD",
                "profilePicUrl",
                "displayResources",
                "sidecarChildren",
                "edge_sidecar_to_children",
                "coverPhotoUrl",
                "originalCoverUrl",
                "coverUrl",
                "media_url",
                "media_url_https",
                "preview_image_url",
                "url",
            ):
                push(value.get(key), key_hint=key, resource_score=next_score)
            return
        if isinstance(value, list):
            ordered = sorted(value, key=score, reverse=True) if any(isinstance(item, dict) and score(item) for item in value) else value
            for item in ordered:
                push(item, key_hint=key_hint, resource_score=max(resource_score, score(item)))
            return
        url = _text(value)
        if url.startswith("["):
            try:
                push(json.loads(url), key_hint=key_hint)
            except Exception as exc:
                logger.debug("vkpi channel media url json parse failed: %s", exc)
            return
        if not url or not url.startswith(("http://", "https://")) or url in seen:
            return
        if not _looks_like_image_media_url(url, key_hint=key_hint):
            return
        seen.add(url)
        cached = cached_image_url(url)
        candidates.append((1 if cached else 0, resource_score, url, cached or url))

    for value in values:
        push(value)
    return [item[3] for item in sorted(candidates, key=lambda item: (item[0], item[1]), reverse=True)]


def _video_url(value: Any, *, depth: int = 0) -> str:
    if depth > 7:
        return ""
    if isinstance(value, str):
        url = _text(value)
        if not url.startswith(("http://", "https://")):
            return ""
        parsed = urllib.parse.urlparse(url)
        host = parsed.hostname or ""
        if ".mp4" in parsed.path.lower() or "googlevideo.com" in host or host.endswith("v.redd.it") or host.endswith("video.twimg.com") or "video-" in host:
            return url
        return ""
    if isinstance(value, list):
        for item in value:
            found = _video_url(item, depth=depth + 1)
            if found:
                return found
        return ""
    if isinstance(value, dict):
        for key in ("videoUrl", "browser_native_hd_url", "browser_native_sd_url", "playable_url", "fallback_url", "url"):
            found = _video_url(value.get(key), depth=depth + 1)
            if found:
                return found
        for item in value.values():
            found = _video_url(item, depth=depth + 1)
            if found:
                return found
    return ""


__all__ = [name for name in globals() if not name.startswith("__")]
