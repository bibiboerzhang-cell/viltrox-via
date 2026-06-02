"""Keyframe extraction and multimodal image packaging helpers."""
from __future__ import annotations

import base64
import re
import shutil
import subprocess
import tempfile
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from app.core.logging import get_logger


logger = get_logger(__name__)


def parse_timestamp_seconds(value: Any) -> float | None:
    """Parse 15, "15", "00:15", "01:23", or "01:02:03.5" into seconds."""
    if isinstance(value, (int, float)):
        return max(0.0, float(value))
    raw = str(value or "").strip()
    if not raw:
        return None
    if re.fullmatch(r"\d+(?:\.\d+)?", raw):
        return max(0.0, float(raw))
    parts = raw.split(":")
    if len(parts) not in (2, 3):
        return None
    try:
        numbers = [float(part) for part in parts]
    except ValueError:
        return None
    if len(numbers) == 2:
        minutes, seconds = numbers
        return max(0.0, minutes * 60 + seconds)
    hours, minutes, seconds = numbers
    return max(0.0, hours * 3600 + minutes * 60 + seconds)


def _timestamp_label(seconds: float) -> str:
    rounded = int(max(0, round(seconds)))
    return f"{rounded // 60:02d}:{rounded % 60:02d}"


def _normalise_requests(keyframes: Sequence[Any]) -> list[dict[str, Any]]:
    output: list[dict[str, Any]] = []
    for item in keyframes:
        if isinstance(item, dict):
            timestamp = item.get("timestamp", item.get("time"))
            reason = str(item.get("reason") or item.get("what") or "").strip()
        else:
            timestamp = item
            reason = ""
        seconds = parse_timestamp_seconds(timestamp)
        if seconds is None:
            logger.warning("video_keyframe_timestamp_skipped", extra={"timestamp": str(timestamp)})
            continue
        output.append(
            {
                "timestamp": str(timestamp),
                "timestamp_seconds": seconds,
                "reason": reason,
            }
        )
    return output


def extract_keyframes(
    video_path: str,
    keyframes: Sequence[Any],
    output_dir: str,
    *,
    timeout_sec: int = 20,
) -> list[dict[str, Any]]:
    """Extract requested keyframes to JPG files, skipping failed frames."""
    if shutil.which("ffmpeg") is None:
        logger.warning("video_keyframe_ffmpeg_missing")
        return []
    source = Path(video_path)
    if not source.exists():
        logger.warning("video_keyframe_source_missing", extra={"video_path": str(video_path)})
        return []
    out_dir = Path(output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    frames: list[dict[str, Any]] = []
    for index, request in enumerate(_normalise_requests(keyframes), start=1):
        seconds = float(request["timestamp_seconds"])
        label = _timestamp_label(seconds)
        out_path = out_dir / f"frame_{index:06d}.jpg"
        cmd = [
            "ffmpeg",
            "-hide_banner",
            "-loglevel",
            "error",
            "-y",
            "-ss",
            f"{seconds:.3f}",
            "-i",
            str(source),
            "-frames:v",
            "1",
            "-q:v",
            "3",
            str(out_path),
        ]
        try:
            proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout_sec)
        except subprocess.TimeoutExpired:
            logger.warning("video_keyframe_extract_timeout", extra={"timestamp": label})
            continue
        if proc.returncode != 0 or not out_path.exists() or out_path.stat().st_size <= 0:
            logger.warning(
                "video_keyframe_extract_failed",
                extra={"timestamp": label, "stderr": (proc.stderr or "")[:300]},
            )
            continue
        frames.append(
            {
                "timestamp": label,
                "timestamp_seconds": seconds,
                "reason": request.get("reason") or "",
                "image_path": str(out_path),
            }
        )
    return frames


@contextmanager
def temporary_keyframes(video_path: str, keyframes: Sequence[Any]) -> Iterator[list[dict[str, Any]]]:
    """Extract keyframes into a TemporaryDirectory and clean them after use."""
    with tempfile.TemporaryDirectory(prefix="vkpi-keyframes-") as tmpdir:
        yield extract_keyframes(video_path, keyframes, tmpdir)


def image_path_to_base64(image_path: str) -> str:
    return base64.b64encode(Path(image_path).read_bytes()).decode("ascii")


def keyframes_to_base64(keyframes: Sequence[dict[str, Any]]) -> list[dict[str, Any]]:
    output: list[dict[str, Any]] = []
    for frame in keyframes:
        image_path = str(frame.get("image_path") or "")
        if not image_path or not Path(image_path).exists():
            continue
        output.append(
            {
                "timestamp": frame.get("timestamp"),
                "reason": frame.get("reason") or "",
                "mime_type": "image/jpeg",
                "base64": image_path_to_base64(image_path),
            }
        )
    return output


def build_openai_multimodal_content(text: str, keyframes: Sequence[dict[str, Any]], *, detail: str = "high") -> list[dict[str, Any]]:
    content: list[dict[str, Any]] = [{"type": "input_text", "text": text}]
    for frame in keyframes_to_base64(keyframes):
        content.append(
            {
                "type": "input_image",
                "image_url": f"data:{frame['mime_type']};base64,{frame['base64']}",
                "detail": detail,
            }
        )
    return content


def build_anthropic_multimodal_content(text: str, keyframes: Sequence[dict[str, Any]]) -> list[dict[str, Any]]:
    content: list[dict[str, Any]] = [{"type": "text", "text": text}]
    for frame in keyframes_to_base64(keyframes):
        content.append(
            {
                "type": "image",
                "source": {
                    "type": "base64",
                    "media_type": frame["mime_type"],
                    "data": frame["base64"],
                },
            }
        )
    return content


def build_gemini_multimodal_parts(text: str, keyframes: Sequence[dict[str, Any]]) -> list[Any]:
    parts: list[Any] = [text]
    for frame in keyframes_to_base64(keyframes):
        parts.append({"inline_data": {"mime_type": frame["mime_type"], "data": frame["base64"]}})
    return parts
