#!/usr/bin/env python3
"""Build or inspect V-KPI Memory v0 from committed legacy batches."""
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
from app.services.vkpi import memory  # noqa: E402


DEFAULT_BATCH_UID = "vkpi_20260519033921_b36c6f28ec8d"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build V-KPI Memory v0 from P2D committed data.")
    parser.add_argument("--batch-uid", default=DEFAULT_BATCH_UID)
    parser.add_argument("--build-legacy", action="store_true", help="Build memory facts from the legacy batch")
    parser.add_argument("--summary", action="store_true", help="Print memory summary")
    parser.add_argument("--source-ref", default="", help="Optional source_ref prefix for summary")
    parser.add_argument("--json", action="store_true", help="Print JSON output")
    return parser.parse_args()


def _print_summary(result: dict) -> None:
    print(f"source_ref={result.get('source_ref', '')}")
    for key, value in sorted((result.get("entities") or {}).items()):
        print(f"entities.{key}={int(value)}")
    for key, value in sorted((result.get("facts") or {}).items()):
        print(f"facts.{key}={int(value)}")
    print(f"links={int(result.get('links', 0))}")
    print(f"snapshots={int(result.get('snapshots', 0))}")
    if result.get("batch_uid"):
        print(f"batch_uid={result['batch_uid']}")
    if result.get("snapshot_uid"):
        print(f"snapshot_uid={result['snapshot_uid']}")
    for key, value in sorted((result.get("build_counts") or {}).items()):
        print(f"build.{key}={int(value)}")


def main() -> int:
    args = parse_args()
    try:
        if args.build_legacy:
            result = memory.build_memory_from_legacy_batch(args.batch_uid)
        else:
            source_ref = args.source_ref
            if not source_ref and not args.summary:
                source_ref = f"legacy_batch:{args.batch_uid}"
            result = memory.summary(source_ref=source_ref)
        if args.json:
            print(json.dumps(result, ensure_ascii=False, indent=2, default=str))
        else:
            _print_summary(result)
        return 0
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    finally:
        asyncio.run(close_db_runtime())


if __name__ == "__main__":
    raise SystemExit(main())
