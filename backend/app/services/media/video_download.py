"""Direct video download helpers for analysis workers."""
from __future__ import annotations

import os
import time
import urllib.error
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from app.core.constants import USER_AGENT
from app.core.logging import get_logger


logger = get_logger(__name__)


def _int_env(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, str(default)))
    except (TypeError, ValueError):
        return default


# Point 8 — download precheck.
# A cheap HEAD probe in front of the real byte download. The direct CDN/play URL
# we get from the IG/TikTok media resolver expires or geo-locks; when it is gone
# we want to discover that with one tiny request instead of opening a download
# slot, streaming, timing out, and burning a retry attempt.
#
# Verdicts deliberately carry a ``reason`` whose text is already a marker that the
# worker's ``_error_category()`` recognises, so a terminal precheck routes into the
# SAME terminal bucket (content_unavailable / content_restricted / content_blocked)
# as a real failure — no change to _error_category or the retry/backoff logic.
PRECHECK_PROCEED = "proceed"
PRECHECK_TERMINAL = "terminal"

# HTTP code -> (verdict, reason). The reason string contains exactly one
# _error_category marker so downstream classification is deterministic.
#   404/410/451-gone  -> "404 not found"        -> content_unavailable
#   401/403           -> "requires authentication" -> content_restricted
#                        (a 403 on a signed CDN play URL means the token expired /
#                         the edge needs valid credentials; it is not retryable as-is)
#   451               -> "blocked in" (legal)   -> content_blocked
_PRECHECK_HTTP_VERDICTS: dict[int, tuple[str, str]] = {
    404: (PRECHECK_TERMINAL, "precheck: 404 not found"),
    410: (PRECHECK_TERMINAL, "precheck: 404 not found (410 gone)"),
    401: (PRECHECK_TERMINAL, "precheck: requires authentication (HTTP 401)"),
    403: (PRECHECK_TERMINAL, "precheck: requires authentication (HTTP 403)"),
    451: (PRECHECK_TERMINAL, "precheck: blocked in region (HTTP 451)"),
}


@dataclass
class PrecheckOutcome:
    verdict: str  # PRECHECK_PROCEED | PRECHECK_TERMINAL
    http_code: int
    reason: str

    @property
    def terminal(self) -> bool:
        return self.verdict == PRECHECK_TERMINAL


def _default_head_fetch(url: str, headers: dict[str, str], timeout: float) -> int:
    """Issue a HEAD request and return the HTTP status code.

    Falls back gracefully: redirects are followed by urllib; HTTPError yields its
    code; any transport error is re-raised for the caller to treat as ambiguous.
    """
    request = urllib.request.Request(url, headers=headers, method="HEAD")
    try:
        with urllib.request.urlopen(request, timeout=timeout) as resp:
            return int(resp.status)
    except urllib.error.HTTPError as exc:
        return int(exc.code)


def precheck_direct_video_url(
    video_url: str,
    *,
    referer: str = "",
    timeout: float | None = None,
    fetch: Callable[[str, dict[str, str], float], int] | None = None,
) -> PrecheckOutcome:
    """Cheap availability probe for a direct CDN/play URL.

    Never raises. Returns a terminal verdict ONLY when the URL is confidently
    unavailable (404/410/403/401/451). Anything ambiguous — 2xx, unexpected codes,
    timeouts, DNS errors, malformed URLs — returns ``proceed`` so the real download
    (which has its own size/timeout guards) stays the source of truth. The probe
    must never be the reason a recoverable job gets killed.
    """
    clean_url = str(video_url or "").strip()
    if not clean_url.startswith(("http://", "https://")):
        # Not our job to validate — the downloader already rejects this with its
        # own "direct video url missing" error. Proceed and let it handle.
        return PrecheckOutcome(PRECHECK_PROCEED, 0, "precheck_skipped:not_http")

    probe_timeout = (
        timeout if timeout is not None else float(_int_env("APIFY_WORKER_VIDEO_PRECHECK_TIMEOUT_SEC", 6))
    )
    headers = {"User-Agent": USER_AGENT}
    if referer:
        headers["Referer"] = referer
    fetcher = fetch or _default_head_fetch
    try:
        code = fetcher(clean_url, headers, probe_timeout)
    except Exception as exc:  # noqa: BLE001 - a flaky probe must never block a download
        return PrecheckOutcome(PRECHECK_PROCEED, 0, f"precheck_inconclusive:{exc.__class__.__name__}")

    verdict, reason = _PRECHECK_HTTP_VERDICTS.get(int(code), (PRECHECK_PROCEED, ""))
    if verdict == PRECHECK_TERMINAL:
        return PrecheckOutcome(PRECHECK_TERMINAL, int(code), reason)
    return PrecheckOutcome(PRECHECK_PROCEED, int(code), reason or f"precheck_ok:http_{int(code)}")


def download_direct_video_url(
    video_url: str,
    output_dir: str,
    *,
    referer: str = "",
    max_mb: int | None = None,
    socket_timeout_sec: int | None = None,
    total_timeout_sec: int | None = None,
    precheck: bool = True,
    precheck_fetch: Callable[[str, dict[str, str], float], int] | None = None,
) -> dict[str, Any]:
    """Download a direct CDN/play URL to a local MP4 file.

    Point 8: a cheap HEAD precheck runs first (``precheck=True``). When it returns
    a terminal verdict the function returns early WITHOUT opening a download — the
    result carries ``precheck_terminal=True`` and an ``error`` whose text the
    worker's ``_error_category`` maps to a content_* terminal bucket. Ambiguous
    probes fall through to the real download unchanged.
    """
    result: dict[str, Any] = {
        "success": False,
        "path": None,
        "error": None,
        "bytes": 0,
        "precheck_terminal": False,
    }
    clean_url = str(video_url or "").strip()
    if not clean_url.startswith(("http://", "https://")):
        result["error"] = "direct video url missing"
        return result

    if precheck:
        outcome = precheck_direct_video_url(
            clean_url, referer=referer, fetch=precheck_fetch
        )
        if outcome.terminal:
            result["precheck_terminal"] = True
            result["error"] = outcome.reason
            logger.info(
                "direct_video_precheck_blocked",
                extra={"http_code": outcome.http_code, "reason": outcome.reason},
            )
            return result

    max_bytes = (max_mb if max_mb is not None else _int_env("APIFY_WORKER_VIDEO_MAX_MB", 200)) * 1024 * 1024
    socket_timeout = socket_timeout_sec if socket_timeout_sec is not None else _int_env("APIFY_WORKER_VIDEO_SOCKET_TIMEOUT_SEC", 20)
    total_timeout = total_timeout_sec if total_timeout_sec is not None else _int_env("APIFY_WORKER_VIDEO_TOTAL_TIMEOUT_SEC", 90)
    out_path = Path(output_dir) / "direct_video.mp4"
    headers = {"User-Agent": USER_AGENT}
    if referer:
        headers["Referer"] = referer

    started = time.monotonic()
    try:
        req = urllib.request.Request(clean_url, headers=headers)
        with urllib.request.urlopen(req, timeout=socket_timeout) as resp, open(out_path, "wb") as fh:
            content_length = resp.headers.get("Content-Length")
            if content_length:
                try:
                    if int(content_length) > max_bytes:
                        result["error"] = f"direct video exceeds {max_bytes // 1024 // 1024}MB"
                        return result
                except ValueError:
                    pass
            while True:
                if time.monotonic() - started > total_timeout:
                    result["error"] = f"direct video download timed out after {total_timeout}s"
                    return result
                chunk = resp.read(1024 * 1024)
                if not chunk:
                    break
                result["bytes"] += len(chunk)
                if result["bytes"] > max_bytes:
                    result["error"] = f"direct video exceeds {max_bytes // 1024 // 1024}MB"
                    return result
                fh.write(chunk)
        if not out_path.exists() or out_path.stat().st_size <= 0:
            result["error"] = "direct video download produced empty file"
            return result
        result["success"] = True
        result["path"] = str(out_path)
        logger.info("direct_video_download_success", extra={"bytes": result["bytes"]})
        return result
    except Exception as exc:
        result["error"] = f"direct video download failed: {str(exc)[:200]}"
        return result
