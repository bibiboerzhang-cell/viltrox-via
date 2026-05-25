#!/usr/bin/env python3
"""Acknowledge a blocked V-KPI sync guard with an auditable reason.

Usage:
    PYTHONPATH=backend .venv/bin/python scripts/vkpi_sync_ack.py --reason "checked provider outage; safe to resume"
    PYTHONPATH=backend .venv/bin/python scripts/vkpi_sync_ack.py --target-run-id daily_incremental_sync_x --reason "manual review"
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from app.domains.sync.daily_sync import ack_daily_sync_guard, check_daily_sync_guard  # noqa: E402


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Acknowledge V-KPI daily sync guard")
    parser.add_argument("--reason", required=True, help="Required human reason for resuming sync.")
    parser.add_argument("--ack-by", default="cli", help="Actor name recorded in the ack ledger.")
    parser.add_argument("--scope", default="daily_incremental_sync", help="Guard scope.")
    parser.add_argument("--target-run-id", default="", help="Optional blocking run_id to acknowledge.")
    parser.add_argument("--check", action="store_true", help="Check current guard state after writing ack.")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    result = ack_daily_sync_guard(
        reason=args.reason,
        acknowledged_by=args.ack_by,
        scope=args.scope,
        target_run_id=args.target_run_id,
        metadata={"source": "scripts/vkpi_sync_ack.py"},
    )
    if args.check:
        try:
            result["guard"] = check_daily_sync_guard({"skip_sync_guard": False})
        except Exception as exc:
            result["guard"] = {"allowed": False, "error": f"{type(exc).__name__}: {exc}"}
    print(json.dumps(result, ensure_ascii=False, default=str, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
