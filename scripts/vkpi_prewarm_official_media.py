#!/usr/bin/env python3
"""Prewarm official-channel media cache from packaged post data.

This script is intentionally resumable at the channel level. It reads the
existing channel packages already written by the Apify/deep-dive flow, caches
images locally, and records video URLs as an inventory by default. Use
``--video-mode download`` only when local video copies are explicitly needed.
"""
from __future__ import annotations

import argparse
import json
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from app.db.connection import get_conn  # noqa: E402
from app.domains import channels  # noqa: E402
from app.domains.media import cache_image, cache_video  # noqa: E402


STAFF = {"id": 1, "role": "admin", "is_owner": 1}
DEFAULT_PLATFORMS = ("facebook", "instagram", "reddit", "tiktok", "x", "youtube")


def _utc_stamp() -> str:
    return datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")


def _text(value: Any) -> str:
    return str(value or "").strip()


def _platforms(value: str) -> set[str]:
    raw = value or ",".join(DEFAULT_PLATFORMS)
    return {item.strip().lower() for item in raw.split(",") if item.strip()}


def _dedupe(values: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        text = _text(value)
        if not text or text in seen:
            continue
        seen.add(text)
        result.append(text)
    return result


def _is_local_cache(value: str, kind: str) -> bool:
    if kind == "video":
        return value.startswith("/api/vkpi-media/video-cache") or value.startswith("/api/admin/vkpi/media/video-cache")
    return value.startswith("/api/vkpi-media/image-cache") or value.startswith("/api/admin/vkpi/media/image-cache")


def _channel_rows(platforms: set[str], channel_ids: set[int]) -> list[dict[str, Any]]:
    where = ["c.deleted_at IS NULL"]
    params: list[Any] = []
    if platforms:
        placeholders = ",".join(["?"] * len(platforms))
        where.append(f"lower(c.platform) IN ({placeholders})")
        params.extend(sorted(platforms))
    if channel_ids:
        placeholders = ",".join(["?"] * len(channel_ids))
        where.append(f"c.id IN ({placeholders})")
        params.extend(sorted(channel_ids))
    rows = get_conn().execute(
        f"""
        SELECT c.id, c.platform, c.account_handle
        FROM vkpi_employee_channels c
        WHERE {' AND '.join(where)}
        ORDER BY c.platform, c.account_handle, c.id
        """,
        tuple(params),
    ).fetchall()
    return [dict(row) for row in rows]


def _collect_channel_media(channel_id: int) -> tuple[dict[str, Any], list[str], list[str], int]:
    row = channels._latest_channel_row(channel_id, staff=STAFF)
    posts, _source, _package_dir = channels._all_posts_for_channel(row)
    image_urls = [_text(row.get("avatar_url"))]
    video_urls: list[str] = []
    for post in posts:
        image_urls.extend([_text(post.get("media_url")), *[_text(item) for item in post.get("image_urls") or []]])
        image_urls.extend([_text(item) for item in post.get("media_urls") or []])
        video_urls.append(_text(post.get("video_url")))
    return row, _dedupe(image_urls), _dedupe(video_urls), len(posts)


def _cache_images(image_urls: list[str], *, timeout: int, max_items: int, concurrency: int) -> dict[str, Any]:
    summary = {"existing": 0, "attempted": 0, "cached": 0, "failed": 0, "skipped": 0, "sample": []}
    candidates: list[str] = []
    for url in image_urls:
        if _is_local_cache(url, "image"):
            summary["existing"] += 1
            continue
        if max_items and len(candidates) >= max_items:
            break
        candidates.append(url)

    def run_cache(url: str) -> dict[str, Any]:
        return cache_image(url, timeout=timeout)

    worker_count = max(1, min(24, int(concurrency or 1)))
    with ThreadPoolExecutor(max_workers=worker_count) as pool:
        futures = [pool.submit(run_cache, url) for url in candidates]
        for future in as_completed(futures):
            result = future.result()
            status = result.get("status")
            summary["attempted"] += 1
            summary["cached"] += int(status == "cached")
            summary["failed"] += int(status == "failed")
            summary["skipped"] += int(status not in {"cached", "failed"})
            if len(summary["sample"]) < 8:
                summary["sample"].append({"status": status, "reason": result.get("reason")})
    return summary

def _handle_videos(video_urls: list[str], *, mode: str, timeout: int, max_items: int, max_mb: int) -> dict[str, Any]:
    summary = {"mode": mode, "existing": 0, "inventory": 0, "attempted": 0, "cached": 0, "failed": 0, "skipped": 0, "sample": []}
    attempted = 0
    for url in video_urls:
        if _is_local_cache(url, "video"):
            summary["existing"] += 1
            continue
        if mode == "none":
            continue
        if mode == "manifest":
            summary["inventory"] += 1
            continue
        if max_items and attempted >= max_items:
            break
        attempted += 1
        result = cache_video(url, timeout=timeout, max_bytes=max_mb * 1024 * 1024)
        status = result.get("status")
        summary["attempted"] += 1
        summary["cached"] += int(status == "cached")
        summary["failed"] += int(status == "failed")
        summary["skipped"] += int(status not in {"cached", "failed"})
        if len(summary["sample"]) < 8:
            summary["sample"].append({"status": status, "reason": result.get("reason")})
    return summary


def _update_metric(channel_id: int, media_summary: dict[str, Any]) -> None:
    conn = get_conn()
    row = conn.execute(
        """
        SELECT id, raw_payload_json
        FROM vkpi_channel_metrics
        WHERE channel_id=?
        ORDER BY snapshot_date DESC, captured_at DESC, id DESC
        LIMIT 1
        """,
        (int(channel_id),),
    ).fetchone()
    if not row:
        return
    try:
        raw = json.loads(row["raw_payload_json"] or "{}")
    except Exception:
        raw = {}
    raw["media_cache_full"] = media_summary
    conn.execute(
        "UPDATE vkpi_channel_metrics SET raw_payload_json=? WHERE id=?",
        (json.dumps(raw, ensure_ascii=False, default=str), int(row["id"])),
    )
    conn.commit()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--platforms", default=",".join(DEFAULT_PLATFORMS), help="Comma-separated platforms.")
    parser.add_argument("--channel-id", action="append", type=int, default=[], help="Limit to one or more channel ids.")
    parser.add_argument("--image-mode", choices=["none", "cache"], default="cache")
    parser.add_argument("--video-mode", choices=["none", "manifest", "download"], default="manifest")
    parser.add_argument("--max-images-per-channel", type=int, default=0, help="0 means all.")
    parser.add_argument("--max-videos-per-channel", type=int, default=0, help="0 means all in download mode.")
    parser.add_argument("--max-video-mb", type=int, default=64)
    parser.add_argument("--timeout", type=int, default=4)
    parser.add_argument("--concurrency", type=int, default=8)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--report", default="")
    args = parser.parse_args()

    rows = _channel_rows(_platforms(args.platforms), set(args.channel_id or []))
    report = {
        "started_at": _utc_stamp(),
        "dry_run": bool(args.dry_run),
        "image_mode": args.image_mode,
        "video_mode": args.video_mode,
        "channels": [],
    }
    for item in rows:
        channel_id = int(item["id"])
        row, image_urls, video_urls, post_count = _collect_channel_media(channel_id)
        summary: dict[str, Any] = {
            "strategy": "full_package_media_prewarm",
            "captured_at": _utc_stamp(),
            "channel_id": channel_id,
            "platform": _text(row.get("platform")),
            "account_handle": _text(row.get("account_handle")),
            "posts": post_count,
            "images_total": len(image_urls),
            "videos_total": len(video_urls),
            "images": {"mode": args.image_mode},
            "videos": {"mode": args.video_mode},
        }
        if not args.dry_run and args.image_mode == "cache":
            summary["images"] = _cache_images(
                image_urls,
                timeout=args.timeout,
                max_items=max(0, int(args.max_images_per_channel or 0)),
                concurrency=max(1, int(args.concurrency or 1)),
            )
        if not args.dry_run:
            summary["videos"] = _handle_videos(
                video_urls,
                mode=args.video_mode,
                timeout=args.timeout,
                max_items=max(0, int(args.max_videos_per_channel or 0)),
                max_mb=max(1, int(args.max_video_mb or 64)),
            )
            _update_metric(channel_id, summary)
        report["channels"].append(summary)
        print(json.dumps(summary, ensure_ascii=False), flush=True)

    report["finished_at"] = _utc_stamp()
    report_path = Path(args.report) if args.report else ROOT / "tmp" / "vkpi_media_cache_runs" / f"{report['started_at']}.json"
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    print(f"report={report_path}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
