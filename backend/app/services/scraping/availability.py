"""YouTube video availability probe (oEmbed-based, no API key, stdlib only).

Classify ``content_unavailable`` before spending yt-dlp / Gemini cycles on
videos that are deleted or private. This module deliberately has no DB access
and no writes.
"""
from __future__ import annotations

import json
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass

OEMBED_ENDPOINT = "https://www.youtube.com/oembed"
DEFAULT_TIMEOUT_SEC = 8.0

_VIDEO_ID_PATTERNS = (
    re.compile(r"(?:youtube\.com/watch\?(?:.*&)?v=)([A-Za-z0-9_-]{6,20})"),
    re.compile(r"(?:youtu\.be/)([A-Za-z0-9_-]{6,20})"),
    re.compile(r"(?:youtube\.com/shorts/)([A-Za-z0-9_-]{6,20})"),
    re.compile(r"(?:youtube\.com/embed/)([A-Za-z0-9_-]{6,20})"),
    re.compile(r"(?:youtube\.com/live/)([A-Za-z0-9_-]{6,20})"),
)


@dataclass
class ProbeResult:
    status: str
    http_code: int
    reason: str
    video_id: str
    title: str = ""

    @property
    def terminal(self) -> bool:
        """True only for the bucket that must never be retried."""
        return self.status == "unavailable"


def extract_video_id(url: str) -> str:
    """Pull a YouTube video id out of common YouTube URL shapes."""
    text = (url or "").strip()
    for pattern in _VIDEO_ID_PATTERNS:
        match = pattern.search(text)
        if match:
            return match.group(1)
    return ""


def _default_fetch(probe_url: str, timeout: float) -> tuple[int, str]:
    request = urllib.request.Request(
        probe_url,
        headers={"User-Agent": "vkpi-availability-probe/1.0"},
        method="GET",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as resp:
            return int(resp.status), resp.read(4096).decode("utf-8", "replace")
    except urllib.error.HTTPError as exc:
        return int(exc.code), ""


def probe_video_availability(
    url: str,
    timeout: float = DEFAULT_TIMEOUT_SEC,
    fetch: Callable[[str, float], tuple[int, str]] | None = None,
) -> ProbeResult:
    """Probe one YouTube URL. Never raises; worst case returns ``unknown``."""
    video_id = extract_video_id(url)
    if not video_id:
        return ProbeResult("unknown", 0, "unrecognized_youtube_url", "")

    canonical = f"https://www.youtube.com/watch?v={video_id}"
    probe_url = f"{OEMBED_ENDPOINT}?{urllib.parse.urlencode({'url': canonical, 'format': 'json'})}"
    fetcher = fetch or _default_fetch
    try:
        code, body = fetcher(probe_url, timeout)
    except Exception as exc:  # noqa: BLE001 - probes must not take down callers
        return ProbeResult("unknown", 0, f"transport_error:{exc.__class__.__name__}", video_id)

    if code == 200:
        title = ""
        try:
            title = str(json.loads(body).get("title", ""))[:200]
        except Exception:  # noqa: BLE001 - title is best-effort decoration
            pass
        return ProbeResult("available", code, "ok", video_id, title)
    if code in (404, 410):
        return ProbeResult("unavailable", code, "deleted_or_private", video_id)
    if code in (401, 403):
        return ProbeResult("restricted", code, "exists_embed_restricted", video_id)
    return ProbeResult("unknown", code, f"unexpected_http_{code}", video_id)


def probe_many(
    urls: list[str],
    qps: float = 2.0,
    timeout: float = DEFAULT_TIMEOUT_SEC,
    fetch: Callable[[str, float], tuple[int, str]] | None = None,
    sleeper: Callable[[float], None] = time.sleep,
) -> list[ProbeResult]:
    """Rate-limited batch probe. ``qps<=0`` disables pacing sleep."""
    gap = (1.0 / qps) if qps and qps > 0 else 0.0
    results: list[ProbeResult] = []
    for index, url in enumerate(urls):
        if gap and index:
            sleeper(gap)
        results.append(probe_video_availability(url, timeout=timeout, fetch=fetch))
    return results
