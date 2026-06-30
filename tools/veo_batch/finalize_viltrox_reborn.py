#!/usr/bin/env python3
"""Mix original Veo audio with Mandarin voiceover and burn subtitles."""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
from pathlib import Path


def ffmpeg_path() -> str:
    for candidate in ("/opt/homebrew/bin/ffmpeg", "/usr/local/bin/ffmpeg", "ffmpeg"):
        if shutil.which(candidate) or Path(candidate).exists():
            return candidate
    raise SystemExit("ffmpeg not found")


def ffprobe_path() -> str:
    for candidate in ("/opt/homebrew/bin/ffprobe", "/usr/local/bin/ffprobe", "ffprobe"):
        if shutil.which(candidate) or Path(candidate).exists():
            return candidate
    raise SystemExit("ffprobe not found")


def run(cmd: list[str]) -> None:
    print("+ " + " ".join(cmd), flush=True)
    subprocess.run(cmd, check=True)


def probe(path: Path) -> dict:
    result = subprocess.run(
        [
            ffprobe_path(),
            "-v",
            "error",
            "-show_format",
            "-show_streams",
            "-of",
            "json",
            str(path),
        ],
        check=True,
        text=True,
        stdout=subprocess.PIPE,
    )
    return json.loads(result.stdout)


def has_audio(path: Path) -> bool:
    data = probe(path)
    return any(stream.get("codec_type") == "audio" for stream in data.get("streams", []))


def duration(path: Path) -> float:
    data = probe(path)
    return float(data.get("format", {}).get("duration", 0.0))


def subtitle_filter_path(path: Path) -> str:
    # ffmpeg subtitles filter uses ':' as option separator. macOS absolute
    # paths here have no drive colon, so only single quotes need escaping.
    return path.resolve().as_posix().replace("'", r"\\'")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--video", required=True, type=Path)
    parser.add_argument("--voiceover", required=True, type=Path)
    parser.add_argument("--subtitles", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--original-volume", default=0.24, type=float)
    parser.add_argument("--voice-volume", default=1.35, type=float)
    args = parser.parse_args()

    if not args.video.exists():
        raise SystemExit(f"video not found: {args.video}")
    if not args.voiceover.exists():
        raise SystemExit(f"voiceover not found: {args.voiceover}")
    if not args.subtitles.exists():
        raise SystemExit(f"subtitles not found: {args.subtitles}")

    ffmpeg = ffmpeg_path()
    args.output.parent.mkdir(parents=True, exist_ok=True)

    vf = f"subtitles=filename='{subtitle_filter_path(args.subtitles)}'"
    if has_audio(args.video):
        filter_complex = (
            f"[0:a]volume={args.original_volume}[orig];"
            f"[1:a]volume={args.voice_volume}[vox];"
            "[orig][vox]amix=inputs=2:duration=first:dropout_transition=2[a]"
        )
        cmd = [
            ffmpeg,
            "-hide_banner",
            "-y",
            "-i",
            str(args.video),
            "-i",
            str(args.voiceover),
            "-vf",
            vf,
            "-filter_complex",
            filter_complex,
            "-map",
            "0:v:0",
            "-map",
            "[a]",
            "-c:v",
            "libx264",
            "-preset",
            "medium",
            "-crf",
            "18",
            "-pix_fmt",
            "yuv420p",
            "-c:a",
            "aac",
            "-b:a",
            "192k",
            "-movflags",
            "+faststart",
            str(args.output),
        ]
    else:
        cmd = [
            ffmpeg,
            "-hide_banner",
            "-y",
            "-i",
            str(args.video),
            "-i",
            str(args.voiceover),
            "-vf",
            vf,
            "-map",
            "0:v:0",
            "-map",
            "1:a:0",
            "-c:v",
            "libx264",
            "-preset",
            "medium",
            "-crf",
            "18",
            "-pix_fmt",
            "yuv420p",
            "-c:a",
            "aac",
            "-b:a",
            "192k",
            "-shortest",
            "-movflags",
            "+faststart",
            str(args.output),
        ]
    run(cmd)
    print(json.dumps({"output": str(args.output), "duration": duration(args.output)}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
