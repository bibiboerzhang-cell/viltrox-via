#!/usr/bin/env python3
"""Cache official-channel item videos with platform + post id sidecars.

This is intentionally separate from vkpi_prewarm_official_media.py:
that script can cache raw video URLs, but the frontend lookup path needs
cache_video_for_item(platform, post_id, url) sidecars.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from app.db.connection import close_db_runtime, get_conn  # noqa: E402
from app.services.vkpi import channels  # noqa: E402
from app.services.vkpi.media_cache import cache_video_for_item, cached_video_url_for_item  # noqa: E402


STAFF = {"id": 1, "role": "admin", "is_owner": 1}
DEFAULT_PLATFORMS = ("instagram", "tiktok")


def _utc_stamp() -> str:
    return datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")


def _text(value: Any) -> str:
    return str(value or "").strip()


def _first_text(*values: Any) -> str:
    for value in values:
        text = _text(value)
        if text:
            return text
    return ""


def _platforms(value: str) -> set[str]:
    raw = value or ",".join(DEFAULT_PLATFORMS)
    return {item.strip().lower() for item in raw.split(",") if item.strip()}


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


def _post_id(post: dict[str, Any]) -> str:
    return _first_text(post.get("source_id"), post.get("id"), post.get("short_code"), post.get("url"))


def _collect_candidates(channel_id: int, *, max_videos: int) -> tuple[dict[str, Any], list[dict[str, Any]], str, int]:
    row = channels._latest_channel_row(channel_id, staff=STAFF)
    posts, source, _package_dir = channels._all_posts_for_channel(row)
    platform = _text(row.get("platform")).lower()
    candidates: list[dict[str, Any]] = []
    seen: set[str] = set()
    for post in posts:
        video_url = _text(post.get("video_url"))
        post_id = _post_id(post)
        if not video_url or not post_id:
            continue
        key = f"{platform}:{post_id}"
        if key in seen:
            continue
        seen.add(key)
        candidates.append(
            {
                "platform": platform,
                "channel_id": int(channel_id),
                "account_handle": _text(row.get("account_handle")),
                "post_id": post_id,
                "source_url": video_url,
                "title": _text(post.get("title"))[:160],
                "posted_at": _text(post.get("posted_at")),
            }
        )
        if max_videos and len(candidates) >= max_videos:
            break
    return row, candidates, source, len(posts)


def run(args: argparse.Namespace) -> dict[str, Any]:
    report: dict[str, Any] = {
        "started_at": _utc_stamp(),
        "dry_run": bool(args.dry_run),
        "platforms": sorted(_platforms(args.platforms)),
        "channels": [],
        "summary": {"channels": 0, "candidates": 0, "cached": 0, "existing": 0, "failed": 0, "skipped": 0},
    }
    rows = _channel_rows(_platforms(args.platforms), set(args.channel_id or []))
    for item in rows:
        channel_id = int(item["id"])
        row, candidates, source, post_count = _collect_candidates(channel_id, max_videos=max(0, int(args.max_videos_per_channel or 0)))
        channel_summary: dict[str, Any] = {
            "channel_id": channel_id,
            "platform": _text(row.get("platform")),
            "account_handle": _text(row.get("account_handle")),
            "source": source,
            "posts": post_count,
            "candidates": len(candidates),
            "items": [],
        }
        report["summary"]["channels"] += 1
        report["summary"]["candidates"] += len(candidates)
        for candidate in candidates:
            cached_url = cached_video_url_for_item(candidate["platform"], candidate["post_id"])
            if cached_url and not args.force_refresh:
                result = {
                    "status": "existing",
                    "cached": True,
                    "platform": candidate["platform"],
                    "post_id": candidate["post_id"],
                    "cached_url": cached_url,
                }
                report["summary"]["existing"] += 1
            elif args.dry_run:
                result = {"status": "would_cache", "cached": False, **candidate}
            else:
                result = cache_video_for_item(
                    candidate["platform"],
                    candidate["post_id"],
                    candidate["source_url"],
                    force_refresh=bool(args.force_refresh),
                    timeout=max(1, int(args.timeout or 30)),
                )
                status = str(result.get("status") or "")
                report["summary"]["cached"] += int(status == "cached")
                report["summary"]["failed"] += int(status == "failed")
                report["summary"]["skipped"] += int(status not in {"cached", "failed"})
            channel_summary["items"].append(
                {
                    "post_id": candidate["post_id"],
                    "title": candidate["title"],
                    "status": result.get("status"),
                    "cached": result.get("cached"),
                    "reason": result.get("reason") or result.get("skip_reason") or "",
                    "cached_url": result.get("cached_url") or "",
                    "storage_backend": result.get("storage_backend") or "",
                    "size_bytes": result.get("size_bytes") or 0,
                }
            )
        report["channels"].append(channel_summary)
        print(json.dumps(channel_summary, ensure_ascii=False, default=str), flush=True)
    report["finished_at"] = _utc_stamp()
    return report


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--platforms", default=",".join(DEFAULT_PLATFORMS), help="Comma-separated platforms.")
    parser.add_argument("--channel-id", action="append", type=int, default=[], help="Limit to one or more channel ids.")
    parser.add_argument("--max-videos-per-channel", type=int, default=2)
    parser.add_argument("--timeout", type=int, default=30)
    parser.add_argument("--force-refresh", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--report", default="")
    args = parser.parse_args()

    try:
        report = run(args)
        report_path = Path(args.report) if args.report else ROOT / "tmp" / "vkpi_video_cache_runs" / f"{report['started_at']}.json"
        report_path.parent.mkdir(parents=True, exist_ok=True)
        report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
        print(f"report={report_path}", flush=True)
        return 0 if not report["summary"]["failed"] else 2
    finally:
        asyncio.run(close_db_runtime())


if __name__ == "__main__":
    raise SystemExit(main())
