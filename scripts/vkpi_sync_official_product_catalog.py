"""Preview or apply the audited viltrox.com public product catalog sync.

Dry-run is the default and performs no database writes. ``--apply`` records an
audited run and atomically updates product rows after the complete feed passes
validation. Public storefront availability is not warehouse inventory.
"""
from __future__ import annotations
from stdout_utils import out as stdout_out

import argparse
import asyncio
import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from app.db.connection import close_db_runtime
from app.domains.products.official_catalog_sync import sync_official_catalog


async def _run(*, apply: bool) -> dict:
    try:
        return await sync_official_catalog(dry_run=not apply)
    finally:
        await close_db_runtime()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--dry-run", action="store_true", help="Read and validate without writes (default).")
    mode.add_argument("--apply", action="store_true", help="Apply one audited atomic sync.")
    args = parser.parse_args()
    result = asyncio.run(_run(apply=bool(args.apply)))
    stdout_out(json.dumps(result, ensure_ascii=False, indent=2, default=str))


if __name__ == "__main__":
    main()
