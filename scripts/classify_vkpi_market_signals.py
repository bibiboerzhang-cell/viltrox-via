#!/usr/bin/env python3
"""Run V-KPI market signal classifier v0 as a dry-run report."""
from __future__ import annotations

import argparse
import asyncio
import json

from app.db.connection import close_db_runtime
from app.domains.market.signal_classifier import (
    build_market_signal_classification,
    write_classification_report,
)


def main() -> int:
    parser = argparse.ArgumentParser(description="Classify raw V-KPI market mentions without writing promotion rows.")
    parser.add_argument("--limit", type=int, default=100)
    parser.add_argument("--run-id", type=int)
    parser.add_argument("--out-dir", default="runtime/ops")
    args = parser.parse_args()

    payload = build_market_signal_classification(limit=args.limit, run_id=args.run_id)
    paths = write_classification_report(payload, out_dir=args.out_dir)
    print(
        json.dumps(
            {
                **paths,
                "passed": payload["passed"],
                "summary": payload["summary"],
                "checks": payload["checks"],
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
