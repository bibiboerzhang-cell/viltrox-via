#!/usr/bin/env python3
"""Build UI-safe IntelligenceCard payloads from market intelligence artifacts."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from app.domains.market.intelligence_cards import (  # noqa: E402
    build_market_intelligence_cards_from_files,
    write_market_intelligence_cards,
)


def main() -> int:
    parser = argparse.ArgumentParser(description="Build V-KPI market intelligence card package.")
    parser.add_argument("market_report_json")
    parser.add_argument("--llm-report-json", default="")
    parser.add_argument("--external-smoke-report-json", default="")
    parser.add_argument("--brand-limit", type=int, default=5)
    parser.add_argument("--out-dir", default="runtime/ops")
    args = parser.parse_args()

    payload = build_market_intelligence_cards_from_files(
        args.market_report_json,
        llm_report_path=args.llm_report_json or None,
        external_smoke_report_path=args.external_smoke_report_json or None,
        brand_limit=args.brand_limit,
    )
    paths = write_market_intelligence_cards(payload, out_dir=args.out_dir)
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
