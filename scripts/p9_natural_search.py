#!/usr/bin/env python3
"""P9 deterministic natural search CLI."""
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
from app.domains.search.natural_search import format_search_summary, search  # noqa: E402


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run deterministic P9 natural search.")
    parser.add_argument("query", help="Plain text search query")
    parser.add_argument("--limit", type=int, default=20, help="Result limit, default 20, max 100")
    parser.add_argument("--json-out", default="", help="Write JSON output to this path")
    parser.add_argument("--md-out", default="", help="Write Markdown output to this path")
    parser.add_argument("--json", action="store_true", help="Print JSON output")
    return parser.parse_args()


def main() -> int:
    try:
        args = parse_args()
        payload = search(args.query, limit=args.limit, json_out=args.json_out, md_out=args.md_out)
        if args.json:
            print(json.dumps({key: value for key, value in payload.items() if key != "markdown"}, ensure_ascii=False, indent=2, default=str))
        else:
            print(format_search_summary(payload))
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
