#!/usr/bin/env python3
"""P6 content brain deterministic dry-run CLI."""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from app.db.connection import close_db_runtime  # noqa: E402
from app.domains.market.content_brain import (  # noqa: E402
    FORBIDDEN_WRITE_FLAGS,
    build_content_brain_preview,
    format_preview_summary,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build a P6 content brain dry-run preview.")
    parser.add_argument("--platform", default="", help="Optional platform filter")
    parser.add_argument("--account-id", type=int, default=0, help="Optional industry account id")
    parser.add_argument("--post-id", type=int, default=0, help="Optional industry post id")
    parser.add_argument("--query", default="", help="Optional title/caption/url search token")
    parser.add_argument("--include-media", action="store_true", help="Include linked vkpi_industry_post_media rows when the table exists")
    parser.add_argument("--limit", type=int, default=50, help="Preview item limit, default 50, max 500")
    parser.add_argument("--json-out", default="", help="Write JSON preview to this path")
    parser.add_argument("--md-out", default="", help="Write Markdown report to this path")
    parser.add_argument("--dry-run", action="store_true", default=True, help="Default mode; no analysis fields are written")
    parser.add_argument("--commit-analysis", action="store_true", help="P6-4: write deterministic analysis fields to posts/media")
    parser.add_argument("--confirm", action="store_true", help="Required with --commit-analysis")
    parser.add_argument("--force", action="store_true", help="Re-analyze rows already marked done")
    parser.add_argument("--json", action="store_true", help="Print full JSON payload to stdout")
    return parser.parse_args()


def _reject_forbidden_flags(argv: list[str]) -> None:
    used = sorted(FORBIDDEN_WRITE_FLAGS.intersection(argv))
    if used:
        raise ValueError(f"P6-2 dry-run rejects write/provider flags: {', '.join(used)}")


def main() -> int:
    try:
        _reject_forbidden_flags(sys.argv[1:])
        args = parse_args()
        if args.commit_analysis and not args.confirm:
            raise ValueError("--commit-analysis requires --confirm")
        payload = build_content_brain_preview(
            platform=args.platform,
            account_id=args.account_id,
            post_id=args.post_id,
            query=args.query,
            include_media=args.include_media,
            limit=args.limit,
            json_out=args.json_out,
            md_out=args.md_out,
            commit_analysis=args.commit_analysis,
            force=args.force,
        )
        if args.json:
            print(json.dumps({key: value for key, value in payload.items() if key != "markdown"}, ensure_ascii=False, indent=2, default=str))
        else:
            print(format_preview_summary(payload))
            if args.json_out:
                print(f"json_out={args.json_out}")
            if args.md_out:
                print(f"md_out={args.md_out}")
        return 0
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    finally:
        asyncio.run(close_db_runtime())


if __name__ == "__main__":
    raise SystemExit(main())
