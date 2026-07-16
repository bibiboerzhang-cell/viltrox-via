"""CLI adapter for the offline Event/Dealer audit."""
from __future__ import annotations
import sys as _stdout_sys
from pathlib import Path as _StdoutPath

_STDOUT_UTILS_DIR = _StdoutPath(__file__).resolve().parents[1]
if str(_STDOUT_UTILS_DIR) not in _stdout_sys.path:
    _stdout_sys.path.insert(1, str(_STDOUT_UTILS_DIR))
from stdout_utils import out as stdout_out  # noqa: E402

import argparse
import json
from pathlib import Path
from typing import Any, Callable

from scripts.ops.event_radar_audit_common import (
    DEFAULT_CATALOG,
    DEFAULT_DEALER_SOURCE,
    DEFAULT_STALE_AFTER_DAYS,
    parse_as_of,
)


def run_cli(
    audit_files: Callable[..., dict[str, Any]],
    argv: list[str] | None = None,
) -> int:
    parser = argparse.ArgumentParser(
        description="Read-only coverage/quality audit for V-KPI Event Radar and Dealer candidates."
    )
    parser.add_argument("--catalog", type=Path, default=DEFAULT_CATALOG)
    parser.add_argument("--dealer-source", type=Path, default=DEFAULT_DEALER_SOURCE)
    parser.add_argument("--as-of", help="ISO timestamp with timezone; defaults to current UTC time")
    parser.add_argument("--stale-after-days", type=int, default=DEFAULT_STALE_AFTER_DAYS)
    parser.add_argument("--compact", action="store_true", help="emit compact JSON")
    args = parser.parse_args(argv)
    try:
        report = audit_files(
            args.catalog,
            args.dealer_source,
            as_of=parse_as_of(args.as_of),
            stale_after_days=args.stale_after_days,
        )
    except Exception as exc:  # noqa: BLE001 - CLI returns machine-readable failure
        report = {
            "ok": False,
            "read_only": True,
            "catalog_path": str(args.catalog),
            "dealer_source_path": str(args.dealer_source),
            "issue_counts": {"errors": 1, "warnings": 0},
            "issues": [{
                "severity": "error", "code": "catalog.read_failed",
                "path": str(args.catalog), "message": str(exc),
            }],
        }
    stdout_out(json.dumps(
        report, ensure_ascii=False, sort_keys=True,
        indent=None if args.compact else 2,
    ))
    return 0 if report.get("ok") else 1
