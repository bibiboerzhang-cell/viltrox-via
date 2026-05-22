"""Cache inline official-channel comments from latest synced snapshots.

This script does not call external providers. It only persists comment bodies
already present in each channel's latest raw sync payload.

Usage:
    PYTHONPATH=backend .venv/bin/python scripts/cache_official_channel_comments.py --dry-run
    PYTHONPATH=backend .venv/bin/python scripts/cache_official_channel_comments.py --execute
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
from app.services.vkpi import channel_comments


def _staff() -> dict[str, Any]:
    return {"id": 0, "role": "admin", "is_owner": 1, "permissions": ["vkpi:admin"]}


def _channel_rows(platform: str = "", limit: int = 200) -> list[dict[str, Any]]:
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
        LIMIT ?
        """,
        (*params, max(1, min(300, int(limit or 200)))),
    ).fetchall()
    return [dict(row) for row in rows]


def run(*, execute: bool, platform: str = "", limit_channels: int = 200, limit_per_post: int = 300) -> dict[str, Any]:
    channels = _channel_rows(platform=platform, limit=limit_channels)
    results: list[dict[str, Any]] = []
    total_seen = 0
    total_new = 0
    posts_with_inline_comments = 0
    for row in channels:
        result = channel_comments.cache_inline_channel_comments(
            int(row["id"]),
            staff=_staff(),
            execute=execute,
            limit_per_post=limit_per_post,
        )
        results.append(result)
        total_seen += int(result.get("comments_seen") or 0)
        total_new += int(result.get("new_count") or 0)
        posts_with_inline_comments += int(result.get("posts_with_inline_comments") or 0)
    return {
        "execute": execute,
        "platform": platform or "all",
        "channels": len(channels),
        "posts_with_inline_comments": posts_with_inline_comments,
        "comments_seen": total_seen,
        "new_count": total_new,
        "results": results,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--execute", action="store_true", help="Persist comments to vkpi_comments")
    parser.add_argument("--dry-run", action="store_true", help="Preview without writes")
    parser.add_argument("--platform", default="", help="Optional platform filter")
    parser.add_argument("--limit-channels", type=int, default=200)
    parser.add_argument("--limit-per-post", type=int, default=300)
    args = parser.parse_args()
    execute = bool(args.execute and not args.dry_run)
    try:
        result = run(
            execute=execute,
            platform=args.platform.strip().lower(),
            limit_channels=args.limit_channels,
            limit_per_post=args.limit_per_post,
        )
        print(json.dumps(result, ensure_ascii=False, indent=2))
    finally:
        asyncio.run(close_db_runtime())


if __name__ == "__main__":
    main()
