"""Media fetch and download helpers for Claude vision analyzers."""
from __future__ import annotations

import base64
import json
import os
import subprocess
import tempfile
import urllib.request
from pathlib import Path

from app.core.constants import USER_AGENT
from app.core.logging import get_logger
from app.services.scraping.ytdlp import YTDLP_AVAILABLE

logger = get_logger(__name__)


def _download_direct_video_url(video_url: str, output_dir: str) -> dict:
    """Download a platform-provided direct MP4/play URL for Gemini/Claude analysis."""
    result = {"success": False, "path": None, "duration": 0, "error": None, "platform": "direct"}
    clean_url = str(video_url or "").strip()
    if not clean_url.startswith(("http://", "https://")):
        result["error"] = "direct video url missing"
        return result
    try:
        out_path = Path(output_dir) / "direct_video.mp4"
        req = urllib.request.Request(
            clean_url,
            headers={
                "User-Agent": USER_AGENT,
                "Referer": "https://www.douyin.com/",
            },
        )
        max_bytes = 500 * 1024 * 1024
        read_bytes = 0
        with urllib.request.urlopen(req, timeout=60) as resp, open(out_path, "wb") as fh:
            while True:
                chunk = resp.read(1024 * 1024)
                if not chunk:
                    break
                read_bytes += len(chunk)
                if read_bytes > max_bytes:
                    result["error"] = "direct video exceeds 500MB"
                    return result
                fh.write(chunk)
        if not out_path.exists() or out_path.stat().st_size <= 0:
            result["error"] = "direct video download produced empty file"
            return result
        probe = subprocess.run(
            ["ffprobe", "-v", "quiet", "-print_format", "json", "-show_format", str(out_path)],
            capture_output=True,
            text=True,
            timeout=10,
        )
        try:
            result["duration"] = float(json.loads(probe.stdout)["format"]["duration"])
        except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
            logger.debug("direct video ffprobe duration parse failed: %s", exc)
        result["success"] = True
        result["path"] = str(out_path)
        return result
    except Exception as exc:
        result["error"] = f"direct video download failed: {str(exc)[:200]}"
        return result


def fetch_all_images_from_post(url: str, og_image: str = "") -> list[str]:
    """
    Fetch ALL images from a multi-image post (Instagram carousel, Reddit gallery, etc.)
    Returns list of base64-encoded image strings.
    """
    images_b64 = []

    # Try yt-dlp to get all images (it supports image galleries too)
    if YTDLP_AVAILABLE:
        try:
            with tempfile.TemporaryDirectory() as tmpdir:
                cmd = [
                    "yt-dlp",
                    "--no-playlist",
                    "-f", "jpg/png/webp/best",
                    "--write-thumbnail",
                    "--skip-video-download",
                    "-o", os.path.join(tmpdir, "img_%(autonumber)s.%(ext)s"),
                    "--no-warnings", "--quiet",
                    url
                ]
                cookie_file = Path("cookies.txt")
                if cookie_file.exists():
                    cmd += ["--cookies", str(cookie_file)]

                subprocess.run(cmd, capture_output=True, timeout=30)

                # Collect all images
                for fname in sorted(os.listdir(tmpdir)):
                    if fname.lower().endswith(('.jpg', '.jpeg', '.png', '.webp')):
                        fpath = os.path.join(tmpdir, fname)
                        if os.path.getsize(fpath) > 5000:  # skip tiny files
                            with open(fpath, "rb") as f:
                                images_b64.append(base64.b64encode(f.read()).decode())
                            if len(images_b64) >= 10:  # max 10 images
                                break
        except Exception as e:
            logger.warning("yt-dlp images fetch error: %s", e)

    # Fallback: use og_image
    if not images_b64 and og_image:
        try:
            import urllib.request
            req = urllib.request.Request(og_image, headers={"User-Agent": USER_AGENT})
            with urllib.request.urlopen(req, timeout=8) as r:
                data = r.read()
            if len(data) > 5000:
                images_b64.append(base64.b64encode(data).decode())
        except Exception as exc:
            logger.debug("og image fetch for Claude vision failed: %s", exc)

    return images_b64
