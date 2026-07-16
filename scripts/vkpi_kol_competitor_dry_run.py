#!/usr/bin/env python3
"""Dry-run KOL competitor relation scoring from existing local KOL pool data."""
from __future__ import annotations
from stdout_utils import out as stdout_out

import argparse
import asyncio
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from app.db.connection import close_db_runtime  # noqa: E402
from app.domains.kol.competitor_detector import (  # noqa: E402
    batch_evaluate_kol_pool,
    evaluate_kol_competitors,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--kol-pool-id", type=int, default=0, help="Evaluate one KOL pool row.")
    parser.add_argument("--brand", default="", help="Optional competitor brand filter.")
    parser.add_argument("--limit", type=int, default=100, help="Batch row limit, max 1200.")
    parser.add_argument(
        "--source-type",
        default="legacy_excel_p2d",
        help="KOL pool source_type filter. Use empty string to scan all sources.",
    )
    parser.add_argument(
        "--write-db",
        action="store_true",
        help="Persist relation snapshots to vkpi_competitor_relation. Omit for read-only dry-run.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        if args.kol_pool_id:
            result = evaluate_kol_competitors(args.kol_pool_id, write_db=args.write_db)
        else:
            result = batch_evaluate_kol_pool(
                brand=args.brand,
                limit=args.limit,
                source_type=args.source_type,
                write_db=args.write_db,
            )
        stdout_out(json.dumps(result, ensure_ascii=False, indent=2, default=str))
        return 0
    finally:
        asyncio.run(close_db_runtime())


if __name__ == "__main__":
    raise SystemExit(main())
