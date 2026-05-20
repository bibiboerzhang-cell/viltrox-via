#!/usr/bin/env python3
"""Dry-run or commit V-KPI brand signals from cached local data only."""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from app.db.connection import close_db_runtime  # noqa: E402
from app.services.vkpi.brand_signal_detector import scan_cached_brand_signals  # noqa: E402


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", default="all", choices=["all", "industry_posts", "kol_pool", "channel_metrics", "industry", "kol", "official"])
    parser.add_argument("--since", default="", help="Optional ISO date lower bound.")
    parser.add_argument("--limit", type=int, default=200, help="Rows per source, max 2000.")
    parser.add_argument("--write-db", action="store_true", help="Commit detected signals to vkpi_brand_signal.")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        result = scan_cached_brand_signals(
            source=args.source,
            since=args.since,
            limit=args.limit,
            write_db=args.write_db,
        )
        print(json.dumps(result, ensure_ascii=False, indent=2, default=str))
        return 0
    finally:
        asyncio.run(close_db_runtime())


if __name__ == "__main__":
    raise SystemExit(main())
