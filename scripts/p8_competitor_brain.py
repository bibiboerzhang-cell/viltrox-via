#!/usr/bin/env python3
"""P8 deterministic competitor brain preview CLI."""
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
from app.services.vkpi.competitor_brain import (  # noqa: E402
    FORBIDDEN_WRITE_FLAGS,
    build_competitor_brain_preview,
    format_preview_summary,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build a P8 competitor brain dry-run preview.")
    parser.add_argument("--limit", type=int, default=20, help="Competitor brand limit, default 20, max 200")
    parser.add_argument("--json-out", default="", help="Write JSON preview to this path")
    parser.add_argument("--md-out", default="", help="Write Markdown report to this path")
    parser.add_argument("--dry-run", action="store_true", default=True, help="P8-1 is always dry-run")
    parser.add_argument("--json", action="store_true", help="Print full JSON payload to stdout")
    return parser.parse_args()


def _reject_forbidden_flags(argv: list[str]) -> None:
    used = sorted(FORBIDDEN_WRITE_FLAGS.intersection(argv))
    if used:
        raise ValueError(f"P8-1 preview rejects write/provider/crawler flags: {', '.join(used)}")


def main() -> int:
    try:
        _reject_forbidden_flags(sys.argv[1:])
        args = parse_args()
        payload = build_competitor_brain_preview(
            limit=args.limit,
            json_out=args.json_out,
            md_out=args.md_out,
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
