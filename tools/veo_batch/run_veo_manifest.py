#!/usr/bin/env python3
"""Run or dry-run a Veo batch manifest prepared from the Viltrox prompt pack."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Iterable


def eprint(message: str) -> None:
    print(message, file=sys.stderr, flush=True)


def load_google_sdk():
    try:
        from google import genai
        from google.genai import types
    except Exception as exc:  # pragma: no cover - environment guard
        raise SystemExit(
            "google-genai is not available in this Python environment. "
            "Use the system python3 where `import google.genai` works."
        ) from exc
    return genai, types


def selected_segments(manifest: dict, chapters: set[str] | None, clips: set[str] | None) -> list[dict]:
    segments = list(manifest["segments"])
    if chapters:
        segments = [s for s in segments if s["chapter_id"] in chapters]
    if clips:
        segments = [s for s in segments if s["id"] in clips]
    return segments


def ffmpeg_path() -> str:
    configured = os.environ.get("FFMPEG")
    if configured:
        return configured
    for candidate in ("/opt/homebrew/bin/ffmpeg", "/usr/local/bin/ffmpeg", "ffmpeg"):
        try:
            subprocess.run([candidate, "-version"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True)
            return candidate
        except Exception:
            continue
    raise SystemExit("ffmpeg not found. Install ffmpeg or set FFMPEG=/path/to/ffmpeg.")


def run_cmd(cmd: list[str]) -> None:
    eprint("+ " + " ".join(cmd))
    result = subprocess.run(cmd, text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    if result.returncode != 0:
        print(result.stdout, file=sys.stderr)
        result.check_returncode()


def normalize_clip(ffmpeg: str, raw_path: Path, clip_path: Path, use_duration: int, resolution: str) -> None:
    if resolution == "1080p":
        width, height = 1920, 1080
    else:
        width, height = 1280, 720
    clip_path.parent.mkdir(parents=True, exist_ok=True)
    vf = (
        f"scale={width}:{height}:force_original_aspect_ratio=decrease,"
        f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2,fps=24,setsar=1"
    )
    run_cmd(
        [
            ffmpeg,
            "-hide_banner",
            "-loglevel",
            "error",
            "-y",
            "-i",
            str(raw_path),
            "-t",
            str(use_duration),
            "-vf",
            vf,
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            "-preset",
            "medium",
            "-crf",
            "18",
            "-c:a",
            "aac",
            "-ar",
            "48000",
            "-ac",
            "2",
            str(clip_path),
        ]
    )


def concat_clips(ffmpeg: str, clip_paths: Iterable[Path], output_path: Path) -> None:
    clip_paths = list(clip_paths)
    if not clip_paths:
        return
    output_path.parent.mkdir(parents=True, exist_ok=True)
    list_path = output_path.with_suffix(".concat.txt")
    list_path.write_text(
        "".join(f"file '{clip.resolve().as_posix()}'\n" for clip in clip_paths),
        encoding="utf-8",
    )
    run_cmd([ffmpeg, "-hide_banner", "-loglevel", "error", "-y", "-f", "concat", "-safe", "0", "-i", str(list_path), "-c", "copy", str(output_path)])


def planned_cost(segments: list[dict], usd_per_second: float) -> tuple[int, int, float]:
    generated_seconds = sum(int(s["generated_duration_seconds"]) for s in segments)
    final_seconds = sum(int(s["use_duration_seconds"]) for s in segments)
    return generated_seconds, final_seconds, round(generated_seconds * usd_per_second, 2)


def generate_one(client, types, *, model: str, prompt: str, duration: int, aspect_ratio: str, resolution: str, poll_seconds: int):
    operation = client.models.generate_videos(
        model=model,
        source=types.GenerateVideosSource(prompt=prompt),
        config=types.GenerateVideosConfig(
            number_of_videos=1,
            duration_seconds=duration,
            aspect_ratio=aspect_ratio,
            resolution=resolution,
        ),
    )
    while not operation.done:
        eprint(f"operation pending: {getattr(operation, 'name', 'unknown')}")
        time.sleep(poll_seconds)
        operation = client.operations.get(operation)
    if not operation.result or not operation.result.generated_videos:
        raise RuntimeError(f"Video generation completed without a video result: {operation}")
    return operation.result.generated_videos[0]


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--manifest", required=True, type=Path)
    parser.add_argument("--output-dir", type=Path)
    parser.add_argument("--chapters", nargs="*", help="Chapter ids such as C01 C02. Default: all.")
    parser.add_argument("--clips", nargs="*", help="Exact clip ids such as C01_S01. Default: all selected chapters.")
    parser.add_argument("--model", default=None)
    parser.add_argument("--aspect-ratio", default=None)
    parser.add_argument("--resolution", default=None, choices=["720p", "1080p"])
    parser.add_argument("--poll-seconds", default=20, type=int)
    parser.add_argument("--retries", default=3, type=int)
    parser.add_argument("--retry-delay-seconds", default=60, type=int)
    parser.add_argument("--cooldown-seconds", default=0, type=int, help="Sleep after each successful generated clip.")
    parser.add_argument("--usd-per-second", default=0.10, type=float)
    parser.add_argument("--execute", action="store_true", help="Actually call the Gemini/Veo API.")
    parser.add_argument("--force", action="store_true", help="Regenerate even when output files exist.")
    parser.add_argument("--no-concat", action="store_true")
    args = parser.parse_args()

    manifest = json.loads(args.manifest.read_text(encoding="utf-8"))
    manifest_dir = args.manifest.parent.resolve()
    output_dir = (args.output_dir or manifest_dir / "renders").resolve()
    chapters = set(args.chapters) if args.chapters else None
    clips = set(args.clips) if args.clips else None
    segments = selected_segments(manifest, chapters, clips)
    if not segments:
        raise SystemExit("No segments selected.")

    model = args.model or manifest.get("default_model", "veo-3.1-fast-generate-preview")
    aspect_ratio = args.aspect_ratio or manifest.get("default_aspect_ratio", "16:9")
    resolution = args.resolution or manifest.get("default_resolution", "720p")
    generated_seconds, final_seconds, estimate = planned_cost(segments, args.usd_per_second)

    print(
        json.dumps(
            {
                "execute": args.execute,
                "model": model,
                "aspect_ratio": aspect_ratio,
                "resolution": resolution,
                "selected_segments": len(segments),
                "generated_seconds": generated_seconds,
                "final_seconds": final_seconds,
                "estimated_usd_before_retries": estimate,
                "audio": "post-production Mandarin voiceover",
                "output_dir": str(output_dir),
            },
            ensure_ascii=False,
            indent=2,
        )
    )

    if not args.execute:
        print("Dry-run only. Add --execute to call Gemini/Veo.")
        return

    api_key = os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY")
    if not api_key:
        raise SystemExit("Set GEMINI_API_KEY or GOOGLE_API_KEY before running with --execute.")

    genai, types = load_google_sdk()
    client = genai.Client(api_key=api_key)
    ffmpeg = ffmpeg_path()

    raw_dir = output_dir / "raw"
    clips_dir = output_dir / "clips"
    raw_dir.mkdir(parents=True, exist_ok=True)
    clips_dir.mkdir(parents=True, exist_ok=True)

    completed: list[Path] = []
    for segment_index, segment in enumerate(segments):
        clip_id = segment["id"]
        chapter_id = segment["chapter_id"]
        prompt_path = manifest_dir / str(segment["prompt_path"])
        prompt = prompt_path.read_text(encoding="utf-8")
        raw_path = raw_dir / chapter_id / f"{clip_id}_raw.mp4"
        clip_path = clips_dir / chapter_id / str(segment["default_output_name"])
        raw_path.parent.mkdir(parents=True, exist_ok=True)
        clip_path.parent.mkdir(parents=True, exist_ok=True)

        if clip_path.exists() and not args.force:
            eprint(f"skip existing clip: {clip_path}")
            completed.append(clip_path)
            continue

        if not raw_path.exists() or args.force:
            generated = None
            last_exc: Exception | None = None
            for attempt in range(1, args.retries + 2):
                try:
                    eprint(f"generating {clip_id}: {segment['generated_duration_seconds']}s attempt {attempt}/{args.retries + 1}")
                    generated = generate_one(
                        client,
                        types,
                        model=model,
                        prompt=prompt,
                        duration=int(segment["generated_duration_seconds"]),
                        aspect_ratio=aspect_ratio,
                        resolution=resolution,
                        poll_seconds=args.poll_seconds,
                    )
                    break
                except Exception as exc:
                    last_exc = exc
                    if attempt > args.retries:
                        break
                    eprint(f"{clip_id} failed: {exc}. Retrying in {args.retry_delay_seconds}s.")
                    time.sleep(args.retry_delay_seconds)
            if generated is None:
                raise RuntimeError(f"{clip_id} failed after {args.retries + 1} attempts") from last_exc
            data = client.files.download(file=generated)
            raw_path.write_bytes(data)
            (raw_path.with_suffix(".json")).write_text(
                json.dumps({"clip_id": clip_id, "model": model}, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )

        normalize_clip(ffmpeg, raw_path, clip_path, int(segment["use_duration_seconds"]), resolution)
        completed.append(clip_path)
        if args.cooldown_seconds > 0 and segment_index < len(segments) - 1:
            eprint(f"cooldown after {clip_id}: sleeping {args.cooldown_seconds}s")
            time.sleep(args.cooldown_seconds)

    if args.no_concat:
        return

    selected_chapters = sorted({s["chapter_id"] for s in segments})
    chapter_outputs: list[Path] = []
    for chapter_id in selected_chapters:
        chapter_manifest_segments = [s for s in manifest["segments"] if s["chapter_id"] == chapter_id]
        chapter_clip_paths = [clips_dir / chapter_id / str(s["default_output_name"]) for s in chapter_manifest_segments]
        if all(path.exists() for path in chapter_clip_paths):
            chapter_output = output_dir / "chapters" / f"{chapter_id}_final_60s.mp4"
            concat_clips(ffmpeg, chapter_clip_paths, chapter_output)
            chapter_outputs.append(chapter_output)
        else:
            eprint(f"chapter {chapter_id} incomplete; skipping chapter concat")

    all_chapter_ids = sorted(manifest["chapters"].keys())
    all_outputs = [output_dir / "chapters" / f"{chapter_id}_final_60s.mp4" for chapter_id in all_chapter_ids]
    if all(path.exists() for path in all_outputs):
        concat_clips(ffmpeg, all_outputs, output_dir / "viltrox_reborn_full_10min.mp4")


if __name__ == "__main__":
    main()
