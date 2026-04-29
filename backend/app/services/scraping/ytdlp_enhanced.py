"""
services/scraping/ytdlp.py — yt-dlp 视频下载 + 元数据（含发布时间）
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
from datetime import datetime, timedelta
from pathlib import Path

from app.core.config import VIDEO_EARLIEST, VIDEO_MAX_AGE_DAYS, YTDLP_PROXY
from app.core.logging import get_logger

logger = get_logger(__name__)

_YTDLP_BIN = shutil.which("yt-dlp")
if not _YTDLP_BIN:
    candidate = Path(sys.executable).with_name("yt-dlp")
    if candidate.exists():
        _YTDLP_BIN = str(candidate)

YTDLP_BIN = _YTDLP_BIN or "yt-dlp"
YTDLP_AVAILABLE = _YTDLP_BIN is not None


def _base_cmd(url: str, extra: list = None) -> list:
    cmd = [YTDLP_BIN, "--no-playlist", "--no-warnings", "--quiet"]
    if Path("cookies.txt").exists():
        cmd += ["--cookies", "cookies.txt"]
    if YTDLP_PROXY:
        cmd += ["--proxy", YTDLP_PROXY]
    if extra:
        cmd += extra
    cmd.append(url)
    return cmd


def get_video_metadata(url: str, platform: str) -> dict:
    """
    用 yt-dlp --dump-json 获取视频元数据，包括发布时间。
    支持：YouTube, TikTok, Instagram, Facebook
    """
    if not YTDLP_AVAILABLE:
        return {}
    if platform not in {"YouTube", "TikTok", "Instagram", "Facebook"}:
        return {}

    try:
        cmd = _base_cmd(url, ["--dump-json", "--skip-download"])
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
        if proc.returncode != 0 or not proc.stdout.strip():
            return {}

        data = json.loads(proc.stdout.strip())
        result = {}

        # ── 互动数据 ──
        for key, alias in [
            ("view_count", "views"), ("like_count", "likes"),
            ("comment_count", "comments"), ("repost_count", "shares"),
            ("share_count", "shares"), ("collect_count", "favorites"),
            ("favorite_count", "favorites"),
        ]:
            val = data.get(key)
            if val is not None and alias not in result:
                result[alias] = int(val)

        # ── 发布时间（关键：验证视频是否在活动期内发布）──
        upload_date = data.get("upload_date")  # yt-dlp 返回 YYYYMMDD
        if upload_date:
            try:
                d = str(upload_date)
                result["upload_date"] = upload_date
                result["published_at"] = f"{d[:4]}-{d[4:6]}-{d[6:8]}T00:00:00Z"
            except Exception:
                pass

        # ── 其他元数据 ──
        if data.get("title"):
            result["video_title"] = data["title"]
        if data.get("duration"):
            result["duration_seconds"] = int(data["duration"])
        if data.get("uploader"):
            result["uploader"] = data["uploader"]

        if result:
            logger.info(
                "ytdlp_enhanced.metadata_loaded",
                extra={"platform": platform, "views": result.get("views", "?"), "published_at": result.get("published_at", "?")},
            )
        return result

    except Exception as e:
        logger.warning("ytdlp_enhanced.metadata_failed", extra={"error": str(e)})
        return {}


def validate_video_publish_date(url: str, platform: str, scraped_data: dict = None) -> dict:
    """
    检查视频发布时间是否符合活动要求。
    默认要求：发布时间在最近 VIDEO_MAX_AGE_DAYS 天内。

    返回:
        {"valid": True/False, "published_at": "...", "reason": "..."}
    """
    # 优先用 scrape_url 已解析好的时间（Instagram 相对时间）
    pub = None
    source = "unknown"
    if scraped_data and scraped_data.get("published_at"):
        pub = scraped_data["published_at"]
        source = "scraped_page"

    # 没有则用 yt-dlp 获取
    if not pub:
        meta = get_video_metadata(url, platform)
        if meta.get("published_at"):
            pub = meta["published_at"]
            source = "ytdlp"

    # Reddit: 用 JSON API
    if not pub and platform == "Reddit":
        try:
            import urllib.request as _ur
            from datetime import timezone
            api_url = url.rstrip("/") + ".json"
            req = _ur.Request(api_url, headers={"User-Agent": "Mozilla/5.0"})
            with _ur.urlopen(req, timeout=10) as resp:
                data = json.loads(resp.read())
            created = data[0]["data"]["children"][0]["data"].get("created_utc")
            if created:
                dt = datetime.fromtimestamp(float(created), tz=timezone.utc)
                pub = dt.strftime("%Y-%m-%dT%H:%M:%SZ")
                source = "reddit_api"
        except Exception:
            pass

    if not pub:
        # 拿不到时间 → 不拒绝，给用户 benefit of the doubt
        return {
            "valid": True,
            "published_at": None,
            "reason": "无法获取发布时间，已放行",
            "source": source,
        }

    try:
        pub_dt = datetime.fromisoformat(pub.replace("Z", ""))
    except Exception:
        return {"valid": True, "published_at": pub, "reason": "时间解析失败，已放行"}

    cutoff_dt = max(VIDEO_EARLIEST, datetime.utcnow() - timedelta(days=max(1, VIDEO_MAX_AGE_DAYS)))
    if pub_dt < cutoff_dt:
        return {
            "valid": False,
            "published_at": pub,
            "reason": (
                f"视频发布于 {pub_dt.strftime('%Y年%m月%d日')}，"
                f"当前只接受最近 {VIDEO_MAX_AGE_DAYS} 天内、"
                f"即 {cutoff_dt.strftime('%Y年%m月%d日')} 之后发布的内容"
            ),
            "source": source,
            "cutoff_at": cutoff_dt.strftime("%Y-%m-%dT%H:%M:%SZ"),
        }

    return {
        "valid": True,
        "published_at": pub,
        "reason": (
            f"发布时间验证通过（{pub_dt.strftime('%Y-%m-%d')}，"
            f"最近 {VIDEO_MAX_AGE_DAYS} 天窗口）"
        ),
        "source": source,
        "cutoff_at": cutoff_dt.strftime("%Y-%m-%dT%H:%M:%SZ"),
    }


def download_video_for_analysis(url: str, max_mb: int = 200) -> str | None:
    """
    下载视频用于 Gemini/Claude 分析。
    返回临时文件路径（调用方负责删除），失败返回 None。
    """
    if not YTDLP_AVAILABLE:
        return None
    try:
        tmpdir = tempfile.mkdtemp()
        out_path = os.path.join(tmpdir, "video.mp4")
        cmd = _base_cmd(url, [
            "-f", "bestvideo[height<=720][ext=mp4]+bestaudio[ext=m4a]/best[height<=720]/best",
            "--merge-output-format", "mp4",
            "-o", out_path,
        ])
        proc = subprocess.run(cmd, capture_output=True, timeout=600)
        if proc.returncode != 0 or not os.path.exists(out_path):
            return None
        size_mb = os.path.getsize(out_path) / 1024 / 1024
        if size_mb > max_mb:
            os.unlink(out_path)
            return None
        return out_path
    except Exception as e:
        logger.warning("ytdlp_enhanced.download_failed", extra={"error": str(e)})
        return None
