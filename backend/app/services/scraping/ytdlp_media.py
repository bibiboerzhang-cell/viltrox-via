"""yt-dlp media download and metadata helpers."""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import uuid
from pathlib import Path

from app.core.logging import get_logger

_YTDLP_BIN = shutil.which("yt-dlp")
if not _YTDLP_BIN:
    candidate = Path(sys.executable).with_name("yt-dlp")
    if candidate.exists():
        _YTDLP_BIN = str(candidate)

YTDLP_BIN = _YTDLP_BIN or "yt-dlp"
YTDLP_AVAILABLE = _YTDLP_BIN is not None
YTDLP_PROXY: str = os.getenv("YTDLP_PROXY", "")
logger = get_logger(__name__)


def _probe_downloaded_media(path: str) -> dict:
    """Return basic media facts and whether the file contains a video stream."""
    result = {"has_video": False, "duration": 0.0, "streams": []}
    try:
        probe = subprocess.run(
            [
                "ffprobe",
                "-v",
                "quiet",
                "-print_format",
                "json",
                "-show_streams",
                "-show_format",
                path,
            ],
            capture_output=True,
            text=True,
            timeout=10,
        )
        if probe.returncode != 0 or not probe.stdout.strip():
            return result
        payload = json.loads(probe.stdout)
        streams = payload.get("streams") if isinstance(payload, dict) else []
        if isinstance(streams, list):
            result["streams"] = [
                {
                    "codec_type": stream.get("codec_type"),
                    "codec_name": stream.get("codec_name"),
                    "width": stream.get("width"),
                    "height": stream.get("height"),
                }
                for stream in streams
                if isinstance(stream, dict)
            ]
            result["has_video"] = any(stream.get("codec_type") == "video" for stream in streams if isinstance(stream, dict))
        duration = (payload.get("format") or {}).get("duration")
        if duration is None:
            for stream in streams or []:
                if isinstance(stream, dict) and stream.get("duration"):
                    duration = stream.get("duration")
                    break
        if duration is not None:
            result["duration"] = float(duration)
    except Exception:
        return result
    return result


def download_video_ytdlp(url: str, output_dir: str, max_seconds: int = 120) -> dict:
    """
    Download video using yt-dlp for full-video analysis.
    Returns: {success, path, duration, error}
    Automatically deletes file after analysis.
    """
    result = {"success": False, "path": None, "duration": 0, "error": None, "platform": None}

    if not YTDLP_AVAILABLE:
        result["error"] = "yt-dlp not installed"
        return result

    try:
        vid_id = uuid.uuid4().hex[:8]
        out_template = os.path.join(output_dir, f"ytdl_{vid_id}.%(ext)s")

        # Detect platform
        platform = "unknown"
        if "tiktok.com" in url:      platform = "tiktok"
        elif "instagram.com" in url:  platform = "instagram"
        elif "youtube.com" in url or "youtu.be" in url: platform = "youtube"
        elif "bilibili.com" in url:   platform = "bilibili"
        elif "reddit.com" in url:     platform = "reddit"
        elif "twitter.com" in url or "x.com" in url: platform = "twitter"
        result["platform"] = platform

        # Build yt-dlp command
        # Prefer bounded 720p media, but keep an unconstrained fallback for
        # platforms like TikTok whose extractor formats sometimes lack height
        # metadata. Without the final b/best fallback yt-dlp can reject a valid
        # public video as "Requested format is not available".
        format_selector = (
            "bv*[ext=mp4][height<=720]+ba[ext=m4a]/"
            "bv*[height<=720]+ba/"
            "b[ext=mp4][height<=720]/"
            "b[height<=720]/"
            "b/best"
        )
        cmd = [
            YTDLP_BIN,
            "--no-playlist",
            "--max-filesize", "500m",
            "-f", format_selector,
            "--merge-output-format", "mp4",
            "-o", out_template,
            "--no-warnings",
            "--quiet",
        ]

        # Cookie file for better quality (optional)
        cookie_file = Path("cookies.txt")
        if cookie_file.exists():
            cmd += ["--cookies", str(cookie_file)]

        # 代理支持：.env 里设置 YTDLP_PROXY 即生效
        if YTDLP_PROXY:
            cmd += ["--proxy", YTDLP_PROXY]

        # For long videos: only download key sections
        if platform == "youtube":
            # First 3 min (gear intro) + we'll get chapters separately
            cmd += ["--download-sections", "*00:00-03:00"]
        elif platform == "bilibili":
            cmd += ["--download-sections", "*00:00-03:00"]
        # TikTok/Reels/Shorts are short by nature, download fully

        cmd.append(url)

        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=120)

        # Find the downloaded file
        found = list(Path(output_dir).glob(f"ytdl_{vid_id}.*"))
        if not found:
            result["error"] = f"Download failed: {proc.stderr[:200] if proc.stderr else 'no output file'}"
            return result

        found = sorted(
            found,
            key=lambda path: (
                0 if path.suffix.lower() in {".mp4", ".mov", ".m4v", ".webm"} else 1,
                -path.stat().st_size if path.exists() else 0,
            ),
        )
        probed_files = []
        video_path = None
        video_probe = None
        for candidate in found:
            probe_info = _probe_downloaded_media(str(candidate))
            probed_files.append(
                {
                    "file": candidate.name,
                    "has_video": probe_info.get("has_video"),
                    "streams": probe_info.get("streams"),
                    "size": candidate.stat().st_size if candidate.exists() else 0,
                }
            )
            if probe_info.get("has_video"):
                video_path = str(candidate)
                video_probe = probe_info
                break

        if not video_path:
            result["error"] = f"Download produced no video stream: {json.dumps(probed_files, ensure_ascii=False)[:500]}"
            return result

        result["path"] = video_path
        result["success"] = True
        result["duration"] = float((video_probe or {}).get("duration") or 0)

        logger.info(
            "ytdlp_download_complete",
            extra={
                "platform": platform,
                "filename": os.path.basename(video_path),
                "duration_sec": round(result["duration"]),
                "size_mb": os.path.getsize(video_path) // 1024 // 1024,
            },
        )

    except subprocess.TimeoutExpired:
        result["error"] = "Download timeout (120s)"
    except Exception as e:
        result["error"] = str(e)

    return result


# _fetch_fresh_metrics_ytdlp (lines 7444-7494)
def _fetch_fresh_metrics_ytdlp(url: str, platform: str) -> dict:
    """
    Fetch current view/like/comment counts using yt-dlp --dump-json.
    Returns dict with fresh metrics, or {} on failure.
    Supported: YouTube, TikTok, Bilibili, Instagram (public).
    """
    if not YTDLP_AVAILABLE:
        return {}
    supported = {"YouTube", "TikTok", "Bilibili", "Instagram"}
    if platform not in supported:
        return {}
    try:
        cmd = [
            YTDLP_BIN,
            "--dump-json",
            "--no-playlist",
            "--skip-download",
            "--no-warnings",
            "--quiet",
        ]
        cookie_file = Path("cookies.txt")
        if cookie_file.exists():
            cmd += ["--cookies", str(cookie_file)]
        if YTDLP_PROXY:
            cmd += ["--proxy", YTDLP_PROXY]
        cmd.append(url)

        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
        if proc.returncode != 0 or not proc.stdout.strip():
            return {}

        data = json.loads(proc.stdout.strip())
        result = {}
        view_count = data.get("view_count")
        like_count = data.get("like_count")
        comment_count = data.get("comment_count")
        repost_count = data.get("repost_count") or data.get("share_count")
        collect_count = data.get("collect_count") or data.get("favorite_count")

        if view_count is not None:   result["views"]     = int(view_count)
        if like_count is not None:   result["likes"]     = int(like_count)
        if comment_count is not None: result["comments"] = int(comment_count)
        if repost_count is not None: result["shares"]    = int(repost_count)
        if collect_count is not None: result["favorites"]= int(collect_count)

        if result:
            logger.info(
                "ytdlp_refresh_complete",
                extra={"platform": platform, "url_prefix": url[:60], "metrics": result},
            )
        return result
    except Exception as e:
        logger.warning("ytdlp_refresh_failed", extra={"url_prefix": url[:60], "error": str(e)})
        return {}

__all__ = ["download_video_ytdlp", "_fetch_fresh_metrics_ytdlp"]
