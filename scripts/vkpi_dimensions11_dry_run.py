#!/usr/bin/env python3
"""Dry-run v2.1 11-dimension KOL scoring from cached KOL pool data."""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from app.db.connection import close_db_runtime  # noqa: E402
from app.services.vkpi.eleven_dimensions import (  # noqa: E402
    backfill_existing_profile_deep_dimensions11,
    batch_preview_dimensions11,
    compose_dimensions_11,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--kol-pool-id", type=int, default=0, help="Preview one KOL pool row.")
    parser.add_argument("--limit", type=int, default=20, help="Batch preview row limit, max 200.")
    parser.add_argument("--source-type", default="legacy_excel_p2d", help="KOL pool source_type filter.")
    parser.add_argument(
        "--write-db",
        action="store_true",
        help="Update existing vkpi_kol_profile_deep rows with dimensions_11_json. Does not insert profile rows.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        if args.write_db:
            result = backfill_existing_profile_deep_dimensions11(limit=args.limit, source_type=args.source_type)
        elif args.kol_pool_id:
            result = compose_dimensions_11(args.kol_pool_id)
        else:
            result = batch_preview_dimensions11(
                limit=args.limit,
                source_type=args.source_type,
            )
        print(json.dumps(result, ensure_ascii=False, indent=2, default=str))
        return 0
    finally:
        asyncio.run(close_db_runtime())


if __name__ == "__main__":
    raise SystemExit(main())
