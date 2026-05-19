#!/usr/bin/env python3
"""Run the V-KPI P2A read-only legacy Excel audit."""
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
from app.services.vkpi.legacy_import_audit import audit_legacy_file, write_reports  # noqa: E402
from app.services.vkpi.legacy_entity_resolution import (  # noqa: E402
    format_resolution_summary,
    inspect_resolution,
    resolve_batch,
)
from app.services.vkpi.legacy_import_staging import (  # noqa: E402
    ensure_legacy_staging_schema,
    format_batch_summary,
    inspect_batch,
    rollback_staging_batch,
    stage_legacy_file,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Audit a legacy V-KPI Excel/CSV file without writing main tables.")
    parser.add_argument("input", nargs="?", help="Path to a .xlsx or .csv legacy file")
    parser.add_argument("--sheet", default="", help="Optional .xlsx sheet name filter")
    parser.add_argument("--max-rows", type=int, default=0, help="Optional data row limit for a fast sample audit")
    parser.add_argument("--out-dir", default=str(ROOT / "docs/audits"), help="Directory for Markdown and CSV audit outputs")
    parser.add_argument("--prefix", default="", help="Optional output filename prefix, defaults to UTC date")
    parser.add_argument("--json", action="store_true", help="Print full JSON audit result")
    parser.add_argument("--no-write", action="store_true", help="Do not write Markdown/CSV report files")
    parser.add_argument("--stage", action="store_true", help="Write parsed rows into legacy staging tables")
    parser.add_argument("--batch-label", default="", help="Optional label recorded on a staging batch")
    parser.add_argument("--inspect-batch", default="", help="Print staging summary for an existing batch_uid")
    parser.add_argument("--rollback-batch", default="", help="Clear staging rows for a batch that has not been committed")
    parser.add_argument("--resolve-batch", default="", help="Run P2C canonical KOL resolution for a staged batch_uid")
    parser.add_argument("--inspect-resolution", default="", help="Print P2C resolution summary for a batch_uid")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if args.inspect_batch or args.rollback_batch or args.resolve_batch or args.inspect_resolution:
        try:
            ensure_legacy_staging_schema()
            if args.resolve_batch:
                print(format_resolution_summary(resolve_batch(args.resolve_batch)))
            elif args.inspect_resolution:
                print(format_resolution_summary(inspect_resolution(args.inspect_resolution)))
            elif args.inspect_batch:
                print(format_batch_summary(inspect_batch(args.inspect_batch)))
            else:
                result = rollback_staging_batch(args.rollback_batch)
                print(f"batch_uid={result['batch_uid']}")
                print(f"status={result['status']}")
                print(f"rolled_back_rows={result['rolled_back_rows']}")
        except Exception as exc:
            print(f"ERROR: {exc}", file=sys.stderr)
            return 2
        finally:
            asyncio.run(close_db_runtime())
        return 0

    if not args.input:
        print("ERROR: input is required unless a batch command is used", file=sys.stderr)
        return 2

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
    if args.stage:
        try:
            ensure_legacy_staging_schema()
            staged = stage_legacy_file(
                args.input,
                batch_label=args.batch_label,
                sheet_name=args.sheet,
                max_rows=max(0, int(args.max_rows or 0)),
            )
            print(format_batch_summary(staged))
        except Exception as exc:
            print(f"ERROR: {exc}", file=sys.stderr)
            return 2
        finally:
            asyncio.run(close_db_runtime())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
