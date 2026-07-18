"""
services/scraping/ytdlp.py — yt-dlp 视频下载 + 字幕获取
"""
from __future__ import annotations

import asyncio
import json
import os
import re
import shutil
import signal
import subprocess
import sys
from pathlib import Path
from urllib.parse import urlparse

from app.core.logging import get_logger
from app.services.scoring.core import compute_weighted_scores, get_vertical
from app.services.scoring.verticals import apply_learned_weights
from app.services.scoring.creator import get_creator_profile
from app.services.scraping.ytdlp_media import _fetch_fresh_metrics_ytdlp, download_video_ytdlp

try:
    from google.genai import types as genai_types
except ImportError:
    genai_types = None

_YTDLP_BIN = shutil.which("yt-dlp")
if not _YTDLP_BIN:
    candidate = Path(sys.executable).with_name("yt-dlp")
    if candidate.exists():
        _YTDLP_BIN = str(candidate)

YTDLP_BIN = _YTDLP_BIN or "yt-dlp"
YTDLP_AVAILABLE = _YTDLP_BIN is not None
logger = get_logger(__name__)
if not YTDLP_AVAILABLE:
    logger.warning("ytdlp_binary_missing")


def _proxy_host_port(proxy_url: str) -> str:
    parsed = urlparse(str(proxy_url or ""))
    host = parsed.hostname or ""
    if not host:
        return "configured"
    return f"{host}:{parsed.port}" if parsed.port else host


YTDLP_PROXY: str = os.getenv("YTDLP_PROXY", "")
if YTDLP_PROXY:
    logger.info("ytdlp_proxy_enabled", extra={"proxy_host": _proxy_host_port(YTDLP_PROXY)})

GEMINI_VIDEO_YTDLP_DOWNLOAD_TIMEOUT_SECONDS = max(
    60,
    int(os.environ.get("GEMINI_VIDEO_YTDLP_DOWNLOAD_TIMEOUT_SEC", "900")),
)

# ──────────────────────────────────────────────
# YouTube subtitle fetcher (yt-dlp)
# ──────────────────────────────────────────────
def _subtitle_timeout_seconds() -> int:
    try:
        value = int(os.getenv("YTDLP_SUBTITLE_TIMEOUT_SECONDS", "30") or "30")
    except ValueError:
        value = 30
    return max(1, min(120, value))


def _run_ytdlp_subtitle_cmd(cmd: list[str], *, timeout_seconds: int) -> tuple[int, str]:
    proc = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        start_new_session=True,
    )
    try:
        _stdout, stderr = proc.communicate(timeout=timeout_seconds)
    except subprocess.TimeoutExpired:
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except Exception:
            proc.kill()
        _stdout, stderr = proc.communicate()
        logger.warning(
            "youtube_subtitles_timeout_fallback",
            extra={"timeout_seconds": timeout_seconds, "stderr_tail": stderr.decode(errors="ignore")[-300:]},
        )
        return -1, stderr.decode(errors="ignore")
    return int(proc.returncode or 0), stderr.decode(errors="ignore")


def fetch_youtube_subtitles(url: str, max_chars: int = 6000) -> str:
    """
    Download auto-generated subtitles via yt-dlp and return a clean
    timestamped transcript string for injection into AI prompts.
    Format: [00:05] text text text\\n[00:12] more text...
    Returns '' if subtitles unavailable or yt-dlp not installed.
    """
    if not YTDLP_AVAILABLE:
        return ""
    try:
        import tempfile, re as _re
        with tempfile.TemporaryDirectory() as tmpdir:
            sub_path = os.path.join(tmpdir, "sub")
            timeout_seconds = _subtitle_timeout_seconds()
            cmd = [
                YTDLP_BIN,
                "--write-auto-sub",
                "--sub-lang", "en,zh-Hans,zh-Hant,zh",
                "--sub-format", "vtt",
                "--skip-download",
                "--no-playlist",
                "-o", sub_path,
                "--quiet",
                url,
            ]
            logger.info("youtube_subtitles_start", extra={"timeout_seconds": timeout_seconds})
            returncode, stderr_text = _run_ytdlp_subtitle_cmd(cmd, timeout_seconds=timeout_seconds)
            if returncode < 0:
                return ""
            if returncode != 0:
                logger.warning(
                    "youtube_subtitles_command_warning",
                    extra={"returncode": returncode, "stderr_tail": stderr_text[-300:]},
                )

            # Find the downloaded vtt file
            vtt_file = None
            for f in os.listdir(tmpdir):
                if f.endswith(".vtt"):
                    vtt_file = os.path.join(tmpdir, f)
                    break
            if not vtt_file:
                logger.info("youtube_subtitles_empty_fallback", extra={"reason": "no_vtt_file"})
                return ""

            raw = open(vtt_file, encoding="utf-8", errors="ignore").read()

            # Parse VTT -> list of (seconds, text)
            entries = []
            blocks = raw.split("\n\n")
            for block in blocks:
                lines = block.strip().splitlines()
                # Find timestamp line: 00:00:05.000 --> 00:00:08.000
                ts_line = next((l for l in lines if "-->" in l), None)
                if not ts_line:
                    continue
                # Parse start time
                start_str = ts_line.split("-->")[0].strip().split(" ")[0]
                parts = start_str.replace(",", ".").split(":")
                try:
                    if len(parts) == 3:
                        secs = int(parts[0]) * 3600 + int(parts[1]) * 60 + float(parts[2])
                    elif len(parts) == 2:
                        secs = int(parts[0]) * 60 + float(parts[1])
                    else:
                        continue
                except ValueError:
                    continue
                # Collect text lines (skip cue settings, position tags)
                text_lines = []
                for l in lines:
                    if "-->" in l or l.strip().isdigit() or l.startswith("WEBVTT"):
                        continue
                    clean = _re.sub(r"<[^>]+>", "", l).strip()
                    if clean:
                        text_lines.append(clean)
                text = " ".join(text_lines).strip()
                if text:
                    entries.append((secs, text))

            if not entries:
                logger.info("youtube_subtitles_empty_fallback", extra={"reason": "no_entries"})
                return ""

            # De-duplicate consecutive identical lines (VTT often repeats)
            deduped = [entries[0]]
            for e in entries[1:]:
                if e[1] != deduped[-1][1]:
                    deduped.append(e)

            # Format as timestamped lines
            def _fmt(s):
                m = int(s) // 60
                sec = int(s) % 60
                return f"{m:02d}:{sec:02d}"

            lines_out = [f"[{_fmt(s)}] {t}" for s, t in deduped]
            result = "\n".join(lines_out)
            # Trim to max_chars from the start (most important info)
            if len(result) > max_chars:
                result = result[:max_chars] + "\n...[字幕截断]"
            logger.info("youtube_subtitles_loaded", extra={"lines": len(deduped), "chars": len(result)})
            return result
    except Exception as e:
        logger.warning("youtube_subtitles_error", extra={"error": str(e)})
        return ""


# ──────────────────────────────────────────────
# GPT-4o mini — 快速预筛 + 批量数据处理
# ──────────────────────────────────────────────
def gpt_prefilter_caption(title: str, caption: str, platform: str) -> dict:
    from app.services.ai.analyzers.gpt_prefilter import gpt_prefilter_caption as strict_prefilter

    return strict_prefilter(title, caption, platform)


def gpt_analyze_engagement_anomaly(
    metrics: dict, platform: str, handle: str,
    history: list
) -> dict:
    from app.services.ai.analyzers.gpt_prefilter import (
        gpt_analyze_engagement_anomaly as strict_anomaly,
    )

    return strict_anomaly(metrics, platform, handle, history)
