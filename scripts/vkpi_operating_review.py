#!/usr/bin/env python3
"""Read-only V-KPI operating review CLI."""
from __future__ import annotations

from stdout_utils import out

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
from app.domains.operations.operating_review import build_operating_review, format_operating_review  # noqa: E402


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build a read-only V-KPI operating review snapshot.")
    parser.add_argument("--limit", type=int, default=25, help="Rows to include per review queue")
    parser.add_argument("--json-out", default="", help="Write JSON output to this path")
    parser.add_argument("--md-out", default="", help="Write Markdown output to this path")
    parser.add_argument("--json", action="store_true", help="Print JSON output")
    return parser.parse_args()


def main() -> int:
    try:
        args = parse_args()
        payload = build_operating_review(limit=args.limit, json_out=args.json_out, md_out=args.md_out)
        if args.json:
            out(json.dumps({key: value for key, value in payload.items() if key != "markdown"}, ensure_ascii=False, indent=2, default=str))
        else:
            out(format_operating_review(payload))
            if args.json_out:
                out(f"json_out={args.json_out}")
            if args.md_out:
                out(f"md_out={args.md_out}")
        return 0
    finally:
        asyncio.run(close_db_runtime())


if __name__ == "__main__":
    raise SystemExit(main())
