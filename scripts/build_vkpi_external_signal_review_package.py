#!/usr/bin/env python3
"""Build a no-write review package from external Google/RSS smoke reports."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from app.domains.market.signal_review_package import (  # noqa: E402
    build_external_signal_review_package_from_files,
)
from app.domains.market.signal_review_reports import (  # noqa: E402
    write_external_signal_review_package_report,
)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("external_smoke_json", nargs="+", help="Path(s) to market-external-signal-smoke-v0 JSON output.")
    parser.add_argument("--out-dir", default="runtime/ops")
    args = parser.parse_args()

    payload = build_external_signal_review_package_from_files(args.external_smoke_json)
    paths = write_external_signal_review_package_report(payload, out_dir=args.out_dir)
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
    return 0 if payload.get("passed") else 2


if __name__ == "__main__":
    raise SystemExit(main())
