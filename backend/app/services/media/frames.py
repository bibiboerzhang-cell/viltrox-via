"""
services/media/frames.py — 视频帧提取（ffmpeg）
"""
from __future__ import annotations

import base64
import json
import os
import shutil
import subprocess
import tempfile
from typing import List, Tuple

from app.core.logging import get_logger
from app.services.ai.clients.claude_client import ANTHROPIC_AVAILABLE
from app.services.ai.analyzers.claude_text import analyze_text_content

FFMPEG_AVAILABLE = shutil.which("ffmpeg") is not None
logger = get_logger(__name__)

def extract_video_frames(video_path: str, max_frames: int = 6) -> list[str]:
    """
    Extract key frames from video with smart sampling strategy.
    Returns list of base64 frames. Use extract_video_frames_with_ts for timestamp data.
    """
    return [f for f, _ in extract_video_frames_with_ts(video_path, max_frames)]


def extract_video_frames_with_ts(video_path: str, max_frames: int = 6) -> list[tuple]:
    """
    Extract key frames AND their timestamps from video.
    Returns list of (base64_str, timestamp_seconds) tuples.
    Strategy:
    - High density in first 12s (gear b-roll usually appears here)
    - Scene-change detection for unique content frames
    - Evenly spread remaining frames
    """
    if not FFMPEG_AVAILABLE:
        return []
    frames_with_ts: list[tuple] = []
    try:
        # Probe duration
        probe = subprocess.run(
            ["ffprobe", "-v", "quiet", "-print_format", "json",
             "-show_format", video_path],
            capture_output=True, text=True, timeout=15
        )
        duration = 60
        try:
            duration = float(json.loads(probe.stdout)["format"]["duration"])
        except Exception:
            logger.warning(
                "media.frame_probe_duration_parse_failed",
                extra={"video_path": video_path},
                exc_info=True,
            )

        with tempfile.TemporaryDirectory() as tmpdir:

            # Strategy 1: 1 frame per 5 seconds for first 3 minutes
            # (meaningful timestamps, not every-second noise)
            dense_end = min(180, duration)
            subprocess.run(
                ["ffmpeg", "-i", video_path,
                 "-t", str(dense_end),
                 "-vf", "fps=1/5,scale=960:-1",   # 1 frame per 5 seconds
                 "-frames:v", "24",                 # max 24 frames = 2 min coverage
                 "-q:v", "4",
                 os.path.join(tmpdir, "dense_%04d.jpg")],
                capture_output=True, timeout=120
            )

            # Strategy 2: 1 frame per 60 seconds for rest of video
            if duration > 180:
                subprocess.run(
                    ["ffmpeg", "-i", video_path,
                     "-ss", "180",
                     "-vf", "fps=1/60,scale=960:-1",
                     "-frames:v", "8",
                     "-q:v", "4",
                     os.path.join(tmpdir, "sparse_%04d.jpg")],
                    capture_output=True, timeout=90
                )

            # ── Build frames with accurate timestamps ──
            all_frames = sorted(os.listdir(tmpdir))
            seen_sizes = set()
            dense_idx = 0
            sparse_idx = 0

            for fname in all_frames:
                if not fname.endswith(".jpg"):
                    continue
                fpath = os.path.join(tmpdir, fname)
                fsize = os.path.getsize(fpath)
                rounded = round(fsize / 800) * 800
                if rounded in seen_sizes:
                    continue
                seen_sizes.add(rounded)

                if fname.startswith("dense_"):
                    ts = dense_idx * 5   # every 5 seconds
                    dense_idx += 1
                else:
                    ts = 180 + sparse_idx * 60
                    sparse_idx += 1

                with open(fpath, "rb") as f:
                    b64 = base64.b64encode(f.read()).decode()
                frames_with_ts.append((b64, min(ts, duration)))

                if len(frames_with_ts) >= 20:
                    break

    except Exception:
        logger.exception("media.frame_extraction_failed", extra={"video_path": video_path})
    return frames_with_ts


def _run_claude_text_for_scoring(result: dict, title: str, caption: str,
                                  url: str, platform: str, scraped_text: str):
    """Run Claude text analysis to fill quality scores when Gemini handled content."""
    if not ANTHROPIC_AVAILABLE:
        return
    try:
        text_result = analyze_text_content(title, caption, url, platform, scraped_text)
        if text_result:
            for field in ["quality_scores", "quality_overall", "quality_summary",
                          "reference_value", "improvements", "marketing_potential",
                          "marketing_notes", "reference_reasons"]:
                if not result.get(field) and text_result.get(field):
                    result[field] = text_result[field]
    except Exception:
        logger.exception(
            "media.claude_text_scoring_failed",
            extra={"url": url, "platform": platform},
        )
