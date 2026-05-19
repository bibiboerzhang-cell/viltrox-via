#!/usr/bin/env python3
"""P12 read-only RBAC and staff invite status CLI."""
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
from app.services.vkpi.rbac_status import build_rbac_status, format_rbac_status  # noqa: E402


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build a read-only P12 RBAC and staff invite status snapshot.")
    parser.add_argument("--include-staff", action="store_true", help="Include redacted per-staff rows in JSON output")
    parser.add_argument("--json-out", default="", help="Write JSON output to this path")
    parser.add_argument("--md-out", default="", help="Write Markdown output to this path")
    parser.add_argument("--json", action="store_true", help="Print JSON output")
    return parser.parse_args()


def main() -> int:
    try:
        args = parse_args()
        payload = build_rbac_status(
            include_staff=args.include_staff,
            json_out=args.json_out,
            md_out=args.md_out,
        )
        if args.json:
            print(json.dumps({key: value for key, value in payload.items() if key != "markdown"}, ensure_ascii=False, indent=2, default=str))
        else:
            print(format_rbac_status(payload))
            if args.json_out:
                print(f"json_out={args.json_out}")
            if args.md_out:
                print(f"md_out={args.md_out}")
        return 0
    finally:
        asyncio.run(close_db_runtime())


if __name__ == "__main__":
    raise SystemExit(main())
