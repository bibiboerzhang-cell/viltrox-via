#!/usr/bin/env python3
"""Persist reviewed market-signal candidates into competitor signal queue."""
from __future__ import annotations
from stdout_utils import out as stdout_out

import argparse
import asyncio
import json

from app.db.connection import close_db_runtime
from app.domains.market.signal_review_package import (
    build_market_signal_review_package_from_file,
)
from app.domains.market.signal_review_reports import (
    write_competitor_signal_write_report,
)
from app.domains.market.signal_review_persistence import (
    write_reviewed_competitor_signals,
)


def main() -> int:
    parser = argparse.ArgumentParser(description="Write reviewed V-KPI market signals into competitor signal queue.")
    parser.add_argument("review_package_json", help="Path to market-signal-promotion-review-package-v0 JSON output.")
    parser.add_argument("--backup-ref", required=True, help="Database backup artifact path created before this write.")
    parser.add_argument("--committed-by", default="cli")
    parser.add_argument("--out-dir", default="runtime/ops")
    parser.add_argument("--yes", action="store_true", help="Required to perform the DB write.")
    args = parser.parse_args()

    if not args.yes:
        raise SystemExit("Refusing to write without --yes")

    package = build_market_signal_review_package_from_file(args.review_package_json)
    result = write_reviewed_competitor_signals(
        package,
        backup_ref=args.backup_ref,
        committed_by=args.committed_by,
    )
    paths = write_competitor_signal_write_report(result, out_dir=args.out_dir)
    stdout_out(
        json.dumps(
            {
                **paths,
                "inserted": result["inserted"],
                "skipped_existing": result["skipped_existing"],
                "ready_candidates": result["ready_candidates"],
                "run_uid": result["run_uid"],
                "run_id": result["run_id"],
                "checks": result["checks"],
            },
            ensure_ascii=False,
            indent=2,
            default=str,
        )
    )
    asyncio.run(close_db_runtime())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
