"""
services/media/frames.py — 视频帧提取（ffmpeg）

抽帧策略走一个可插拔接口：
  1. PySceneDetect 场景切分（首选，若已安装 scenedetect）——按镜头取关键帧，
     喂给 LLM 的是「互不重复的内容帧」而非等间隔的近似重复帧，直接省视觉 token。
  2. ffmpeg 盲采样（保底，等间隔取帧 + 体积去重）——scenedetect 未装/检测失败时回退。
opencv-python-headless 已在依赖里；scenedetect 是可选增量包，缺席时优雅降级并 logger.info。
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

# PySceneDetect 插槽：装了就走场景切分，没装就把符号置空并在回退时降级到盲采样。
try:
    from scenedetect import ContentDetector, detect as scene_detect
    PYSCENEDETECT_AVAILABLE = True
except Exception:
    ContentDetector = None
    scene_detect = None
    PYSCENEDETECT_AVAILABLE = False

# 场景帧上限：与盲采样保底同量级(<=20)，避免镜头极多时把视觉 payload 撑爆。
_MAX_SCENE_FRAMES = 20

if not PYSCENEDETECT_AVAILABLE:
    logger.info("media.pyscenedetect_unavailable_blind_sampling_fallback")


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
    - PySceneDetect scene-cut keyframes when available (one frame per shot =
      unique content, fewer vision tokens).
    - Otherwise blind ffmpeg sampling: high density in first 3 min, sparse after,
      with per-frame size de-duplication to drop near-identical frames.
    """
    if not FFMPEG_AVAILABLE:
        return []

    duration = _probe_duration(video_path)

    scene_timestamps = _detect_scene_timestamps(video_path, duration)
    if scene_timestamps:
        scene_frames = _extract_frames_at_timestamps(video_path, scene_timestamps, duration)
        if scene_frames:
            logger.info(
                "media.frame_scene_detect_used",
                extra={"video_path": video_path, "frame_count": len(scene_frames)},
            )
            return scene_frames
        logger.info(
            "media.frame_scene_detect_empty_fallback_blind",
            extra={"video_path": video_path},
        )

    return _extract_frames_blind(video_path, duration)


def _probe_duration(video_path: str) -> float:
    """Best-effort video duration in seconds; defaults to 60 on probe failure."""
    duration = 60.0
    try:
        probe = subprocess.run(
            ["ffprobe", "-v", "quiet", "-print_format", "json",
             "-show_format", video_path],
            capture_output=True, text=True, timeout=15
        )
        duration = float(json.loads(probe.stdout)["format"]["duration"])
    except Exception:
        logger.warning(
            "media.frame_probe_duration_parse_failed",
            extra={"video_path": video_path},
            exc_info=True,
        )
    return duration


def _detect_scene_timestamps(video_path: str, duration: float) -> list[float] | None:
    """
    Scene-cut start timestamps via PySceneDetect, or None to fall back to blind sampling.

    Returns None when scenedetect is not installed or detection raises, so the
    caller keeps the proven ffmpeg blind-sampling path. On success the list is
    sorted, de-duplicated and capped at _MAX_SCENE_FRAMES.
    """
    if not PYSCENEDETECT_AVAILABLE:
        return None
    try:
        scenes = scene_detect(video_path, ContentDetector())
        timestamps = sorted(
            {min(max(0.0, float(start.get_seconds())), duration) for start, _end in scenes}
        )
    except Exception:
        logger.warning(
            "media.scene_detect_failed",
            extra={"video_path": video_path},
            exc_info=True,
        )
        return None
    if not timestamps:
        return None
    if len(timestamps) > _MAX_SCENE_FRAMES:
        step = len(timestamps) / _MAX_SCENE_FRAMES
        timestamps = [timestamps[int(i * step)] for i in range(_MAX_SCENE_FRAMES)]
    return timestamps


def _extract_frames_at_timestamps(
    video_path: str, timestamps: list[float], duration: float
) -> list[tuple]:
    """Extract one JPG per requested timestamp; skip any frame ffmpeg cannot render."""
    frames_with_ts: list[tuple] = []
    try:
        with tempfile.TemporaryDirectory() as tmpdir:
            for idx, ts in enumerate(timestamps):
                seek = max(0.0, float(ts))
                out_path = os.path.join(tmpdir, f"scene_{idx:04d}.jpg")
                proc = subprocess.run(
                    ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
                     "-ss", f"{seek:.3f}", "-i", video_path,
                     "-frames:v", "1", "-vf", "scale=960:-1", "-q:v", "4",
                     out_path],
                    capture_output=True, timeout=30
                )
                if (proc.returncode != 0 or not os.path.exists(out_path)
                        or os.path.getsize(out_path) <= 0):
                    continue
                with open(out_path, "rb") as f:
                    b64 = base64.b64encode(f.read()).decode()
                frames_with_ts.append((b64, min(seek, duration)))
                if len(frames_with_ts) >= _MAX_SCENE_FRAMES:
                    break
    except Exception:
        logger.exception(
            "media.scene_frame_extraction_failed", extra={"video_path": video_path}
        )
    return frames_with_ts


def _extract_frames_blind(video_path: str, duration: float) -> list[tuple]:
    """Fallback: evenly-spaced ffmpeg sampling with size-based de-duplication."""
    frames_with_ts: list[tuple] = []
    try:
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
