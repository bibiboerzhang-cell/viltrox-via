#!/usr/bin/env python3
"""Run the hermetic Dealer/Event source-passport audit.

The command reads a JSON catalog and the literal reviewed Dealer candidate list.
It never imports application routers, connects to PostgreSQL, or performs HTTP.
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[1]
BACKEND_ROOT = REPO_ROOT / "backend"
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from app.domains.source_passport_quality import (  # noqa: E402
    DEFAULT_STALE_AFTER_DAYS,
    build_source_passport_quality_audit,
)
from scripts.ops.event_radar_audit_common import (  # noqa: E402
    DEFAULT_CATALOG,
    DEFAULT_DEALER_SOURCE,
    load_reviewed_dealer_candidates,
    parse_as_of,
)


def _load_json(path: Path) -> dict[str, Any]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"JSON document must be an object: {path}")
    return data


def _previous_snapshot(path: Path | None) -> dict[str, Any] | None:
    if path is None:
        return None
    data = _load_json(path)
    snapshot = data.get("snapshot", data)
    if not isinstance(snapshot, dict):
        raise ValueError("previous snapshot must be a JSON object")
    return snapshot


def build_report(
    *,
    catalog_path: Path,
    dealer_source_path: Path,
    as_of: datetime,
    stale_after_days: int,
    previous_snapshot_path: Path | None = None,
) -> dict[str, Any]:
    catalog = _load_json(catalog_path)
    dealer_candidates = load_reviewed_dealer_candidates(dealer_source_path)
    report = build_source_passport_quality_audit(
        catalog,
        dealer_candidates,
        as_of=as_of,
        stale_after_days=stale_after_days,
        previous_snapshot=_previous_snapshot(previous_snapshot_path),
    )
    report["inputs"] = {
        "catalog_path": str(catalog_path),
        "dealer_source_path": str(dealer_source_path),
        "previous_snapshot_path": (
            str(previous_snapshot_path) if previous_snapshot_path is not None else None
        ),
    }
    return report


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Offline source identity, evidence and change audit for Event/Dealer catalogs."
    )
    parser.add_argument("--catalog", type=Path, default=DEFAULT_CATALOG)
    parser.add_argument("--dealer-source", type=Path, default=DEFAULT_DEALER_SOURCE)
    parser.add_argument("--as-of", default=None, help="timezone-aware ISO timestamp")
    parser.add_argument(
        "--stale-after-days",
        type=int,
        default=DEFAULT_STALE_AFTER_DAYS,
    )
    parser.add_argument("--previous-snapshot", type=Path, default=None)
    parser.add_argument("--output", type=Path, default=None)
    parser.add_argument(
        "--snapshot-only",
        action="store_true",
        help="write only content hashes and entity keys for the next comparison",
    )
    parser.add_argument(
        "--fail-on-errors",
        action="store_true",
        help="exit 1 only for structural errors; evidence gaps remain reportable warnings",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    as_of = parse_as_of(args.as_of) if args.as_of else datetime.now(timezone.utc)
    report = build_report(
        catalog_path=args.catalog.resolve(),
        dealer_source_path=args.dealer_source.resolve(),
        as_of=as_of,
        stale_after_days=args.stale_after_days,
        previous_snapshot_path=(
            args.previous_snapshot.resolve() if args.previous_snapshot else None
        ),
    )
    output_payload = report["snapshot"] if args.snapshot_only else report
    rendered = json.dumps(output_payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    if args.output is None:
        sys.stdout.write(rendered)
    else:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered, encoding="utf-8")
    return 1 if args.fail_on_errors and report["issue_counts"]["errors"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
