#!/usr/bin/env python3
"""Dry-run or execute V-KPI local video-cache migration to R2."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from app.domains.media import migrate_local_video_cache_to_r2  # noqa: E402


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Migrate V-KPI local video cache files to R2")
    parser.add_argument("--execute", action="store_true", help="Actually upload eligible files. Default is dry-run only.")
    parser.add_argument("--limit", type=int, default=100, help="Max sidecar rows to scan")
    parser.add_argument("--platform", default="", help="Optional platform filter, e.g. instagram or tiktok")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    result = migrate_local_video_cache_to_r2(execute=bool(args.execute), limit=int(args.limit or 100), platform=args.platform)
    print(json.dumps(result, ensure_ascii=False, default=str, indent=2))
    if args.execute and result.get("status") == "not_configured":
        return 2
    if args.execute and int(result.get("failed") or 0):
        return 3
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
