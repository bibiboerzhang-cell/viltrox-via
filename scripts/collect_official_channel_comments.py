"""Collect official-channel comments through platform providers in bounded batches.

Unlike cache_official_channel_comments.py, this script may call external
providers such as Apify, YouTube Data API, PRAW, or X API. Keep batches small.

Usage:
    .venv/bin/python scripts/collect_official_channel_comments.py --dry-run
    .venv/bin/python scripts/collect_official_channel_comments.py --execute --platform youtube --max-posts 25
    .venv/bin/python scripts/collect_official_channel_comments.py --execute --platform facebook --max-posts 5 --max-comments 50
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from app.db.connection import close_db_runtime, get_conn
from app.services.vkpi import channel_comments, channels


def _staff() -> dict[str, Any]:
    return {"id": 0, "role": "admin", "is_owner": 1, "permissions": ["vkpi:admin"]}


def _active_channels(platform: str = "") -> list[dict[str, Any]]:
    where = "WHERE deleted_at IS NULL AND status='active'"
    params: list[Any] = []
    if platform:
        where += " AND LOWER(platform)=LOWER(?)"
        params.append(platform)
    rows = get_conn().execute(
        f"""
        SELECT id, platform, account_handle
        FROM vkpi_employee_channels
        {where}
        ORDER BY platform, id
        """,
        tuple(params),
    ).fetchall()
    return [dict(row) for row in rows]


def _candidate_posts(
    *,
    platform: str = "",
    max_posts: int = 100,
    max_posts_per_channel: int = 50,
    force: bool = False,
) -> list[dict[str, Any]]:
    staff = _staff()
    candidates: list[dict[str, Any]] = []
    for channel in _active_channels(platform):
        if len(candidates) >= max_posts:
            break
        response = channels.channel_posts(
            int(channel["id"]),
            page=1,
            limit=max(1, min(50, int(max_posts_per_channel or 50))),
            sort="latest",
            direction="desc",
            window="all",
            staff=staff,
        )
        taken_for_channel = 0
        for post in response.get("posts") or []:
            if len(candidates) >= max_posts or taken_for_channel >= max_posts_per_channel:
                break
            declared_comments = int(post.get("comments") or 0)
            if declared_comments <= 0:
                continue
            post_id = str(post.get("source_id") or post.get("id") or "")
            url = str(post.get("url") or "")
            existing = channel_comments.channel_post_comments(
                int(channel["id"]),
                post_id=post_id,
                url=url,
                limit=300,
                staff=staff,
            )
            cached_count = int(existing.get("comment_count") or 0)
            if cached_count > 0 and not force:
                continue
            taken_for_channel += 1
            candidates.append(
                {
                    "channel_id": int(channel["id"]),
                    "platform": channel["platform"],
                    "handle": channel["account_handle"],
                    "post_id": post_id,
                    "url": url,
                    "title": str(post.get("title") or "")[:120],
                    "declared_comments": declared_comments,
                    "cached_comments": cached_count,
                }
            )
    return candidates


def run(
    *,
    execute: bool,
    platform: str = "",
    max_posts: int = 100,
    max_posts_per_channel: int = 50,
    max_comments: int = 50,
    force: bool = False,
) -> dict[str, Any]:
    candidates = _candidate_posts(
        platform=platform,
        max_posts=max(1, min(500, int(max_posts or 100))),
        max_posts_per_channel=max(1, min(50, int(max_posts_per_channel or 50))),
        force=force,
    )
    results: list[dict[str, Any]] = []
    summary: dict[str, Any] = {
        "execute": execute,
        "platform": platform or "all",
        "candidate_posts": len(candidates),
        "max_comments": max(1, min(300, int(max_comments or 50))),
        "by_status": {},
        "fetched_count": 0,
        "new_count": 0,
        "sample": candidates[:10],
    }
    if not execute:
        return summary
    staff = _staff()
    for candidate in candidates:
        result = channel_comments.collect_channel_post_comments(
            int(candidate["channel_id"]),
            post_id=str(candidate["post_id"]),
            url=str(candidate["url"]),
            limit=summary["max_comments"],
            staff=staff,
        )
        status = str(result.get("status") or "unknown")
        summary["by_status"][status] = int(summary["by_status"].get(status) or 0) + 1
        summary["fetched_count"] += int(result.get("fetched_count") or 0)
        summary["new_count"] += int(result.get("new_count") or 0)
        results.append({**candidate, "result": result})
    summary["results"] = results
    return summary


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--platform", default="")
    parser.add_argument("--max-posts", type=int, default=100)
    parser.add_argument("--max-posts-per-channel", type=int, default=50)
    parser.add_argument("--max-comments", type=int, default=50)
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()
    execute = bool(args.execute and not args.dry_run)
    try:
        result = run(
            execute=execute,
            platform=args.platform.strip().lower(),
            max_posts=args.max_posts,
            max_posts_per_channel=args.max_posts_per_channel,
            max_comments=args.max_comments,
            force=bool(args.force),
        )
        print(json.dumps(result, ensure_ascii=False, indent=2, default=str))
    finally:
        asyncio.run(close_db_runtime())


if __name__ == "__main__":
    main()
