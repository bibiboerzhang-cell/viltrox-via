#!/usr/bin/env python3
"""Compute or initialize P1.X.A KOL refresh tiers.

Default mode is read-only. Use ``--commit`` only after backup-first production
preflight, because it creates/updates ``vkpi_kol_refresh_tier`` rows.
"""
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
from app.services.vkpi import refresh_tier  # noqa: E402


def _platforms(value: str) -> set[str]:
    return {item.strip().lower() for item in str(value or "").split(",") if item.strip()}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Plan or initialize V-KPI KOL refresh tiers")
    parser.add_argument("--commit", action="store_true", help="Write vkpi_kol_refresh_tier rows. Requires backup-first in production.")
    parser.add_argument("--limit", type=int, default=0, help="Limit KOL rows for smoke runs. 0 means all rows.")
    parser.add_argument("--sample-limit", type=int, default=10, help="Sample rows per tier in the output")
    parser.add_argument("--selector-limit", type=int, default=25, help="Qualified refresh sample size")
    parser.add_argument("--platforms", default="", help="Comma-separated platform filter for selector sample")
    parser.add_argument("--tiers", default="hot", help="Comma-separated tiers for selector sample")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        result = refresh_tier.recompute_all_tiers(
            dry_run=not bool(args.commit),
            limit=max(0, int(args.limit or 0)),
            sample_limit=max(0, min(50, int(args.sample_limit or 10))),
        )
        if args.commit:
            plan = refresh_tier.qualified_refresh_plan(
                limit=max(1, min(200, int(args.selector_limit or 25))),
                platforms=_platforms(args.platforms),
                tiers=_platforms(args.tiers),
            )
        else:
            plan = {
                "selector": "qualified",
                "skipped": True,
                "reason": "selector sample requires --commit or an existing vkpi_kol_refresh_tier table",
            }
            if refresh_tier._table_exists("vkpi_kol_refresh_tier"):  # read-only when the table already exists
                plan = refresh_tier.qualified_refresh_plan(
                    limit=max(1, min(200, int(args.selector_limit or 25))),
                    platforms=_platforms(args.platforms),
                    tiers=_platforms(args.tiers),
                )
        print(json.dumps({"tier_recompute": result, "qualified_selector_plan": plan}, ensure_ascii=False, default=str, indent=2))
        return 1 if result.get("error_count") else 0
    finally:
        asyncio.run(close_db_runtime())


if __name__ == "__main__":
    raise SystemExit(main())
