#!/usr/bin/env python3
"""Run the V-KPI P2A read-only legacy Excel audit."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from app.services.vkpi.legacy_import_audit import audit_legacy_file, write_reports  # noqa: E402


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Audit a legacy V-KPI Excel/CSV file without writing main tables.")
    parser.add_argument("input", help="Path to a .xlsx or .csv legacy file")
    parser.add_argument("--sheet", default="", help="Optional .xlsx sheet name filter")
    parser.add_argument("--max-rows", type=int, default=0, help="Optional data row limit for a fast sample audit")
    parser.add_argument("--out-dir", default=str(ROOT / "docs/audits"), help="Directory for Markdown and CSV audit outputs")
    parser.add_argument("--prefix", default="", help="Optional output filename prefix, defaults to UTC date")
    parser.add_argument("--json", action="store_true", help="Print full JSON audit result")
    parser.add_argument("--no-write", action="store_true", help="Do not write Markdown/CSV report files")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        result = audit_legacy_file(args.input, sheet_name=args.sheet, max_rows=max(0, int(args.max_rows or 0)))
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2

    paths: dict[str, str] = {}
    if not args.no_write:
        paths = write_reports(result, args.out_dir, prefix=args.prefix or None)
        result["outputs"] = paths

    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2, default=str))
    else:
        summary = result.get("summary") or {}
        print(f"source={result.get('source', {}).get('path', '')}")
        print(f"total_rows={summary.get('total_rows', 0)}")
        print(f"recognizable_kol_rows={summary.get('recognizable_kol_rows', 0)}")
        print(f"duplicate_groups={summary.get('duplicate_groups', 0)}")
        print(f"manual_review_rows={summary.get('manual_review_rows', 0)}")
        print(f"high_risk_rows={summary.get('high_risk_rows', 0)}")
        for key, value in paths.items():
            print(f"{key}={value}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
