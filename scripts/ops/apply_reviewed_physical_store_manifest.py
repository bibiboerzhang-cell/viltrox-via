#!/usr/bin/env python3
"""Preview or explicitly publish the bounded five-store Dealer manifest."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
BACKEND = ROOT / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from app.domains.commerce import reviewed_physical_store_manifest as reviewed  # noqa: E402


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Dry-run the reviewed physical-store manifest. Add --publish to "
            "atomically write and publish all exact rows."
        )
    )
    parser.add_argument("--actor-id", required=True, type=int)
    parser.add_argument("--manifest", type=Path, default=reviewed.DEFAULT_MANIFEST_PATH)
    parser.add_argument(
        "--publish",
        action="store_true",
        help="explicitly authorize the bounded DB writes; omitted means zero-write dry run",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        manifest = reviewed.load_manifest(args.manifest)
        result = reviewed.apply_manifest(
            manifest,
            actor_id=args.actor_id,
            publish=bool(args.publish),
        )
    except reviewed.ReviewedPhysicalStoreManifestError as exc:
        sys.stdout.write(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False) + "\n")
        return 2
    sys.stdout.write(json.dumps({"ok": True, **result}, ensure_ascii=False, sort_keys=True) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
