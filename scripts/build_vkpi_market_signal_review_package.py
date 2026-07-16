#!/usr/bin/env python3
"""Build a dry-run review package for market-signal promotion candidates."""
from __future__ import annotations
from stdout_utils import out as stdout_out

import argparse
import json

from app.domains.market.signal_review_package import (
    build_market_signal_review_package_from_file,
)
from app.domains.market.signal_review_reports import (
    write_review_package_report,
)


def main() -> int:
    parser = argparse.ArgumentParser(description="Build a V-KPI market signal promotion review package.")
    parser.add_argument("classifier_json", help="Path to market-signal-classifier-v0 JSON output.")
    parser.add_argument("--out-dir", default="runtime/ops")
    args = parser.parse_args()

    payload = build_market_signal_review_package_from_file(args.classifier_json)
    paths = write_review_package_report(payload, out_dir=args.out_dir)
    stdout_out(
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
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
