"""Direct video download helpers for analysis workers."""
from __future__ import annotations

import os
import time
import urllib.request
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


def download_direct_video_url(
    video_url: str,
    output_dir: str,
    *,
    referer: str = "",
    max_mb: int | None = None,
    socket_timeout_sec: int | None = None,
    total_timeout_sec: int | None = None,
) -> dict[str, Any]:
    """Download a direct CDN/play URL to a local MP4 file."""
    result: dict[str, Any] = {"success": False, "path": None, "error": None, "bytes": 0}
    clean_url = str(video_url or "").strip()
    if not clean_url.startswith(("http://", "https://")):
        result["error"] = "direct video url missing"
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
