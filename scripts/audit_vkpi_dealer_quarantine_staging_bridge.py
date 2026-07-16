#!/usr/bin/env python3
"""Build a read-only Dealer quarantine -> candidate-staging bridge artifact."""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any

from stdout_utils import out


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from app.domains.commerce.dealer_quarantine_staging_bridge import (  # noqa: E402
    build_quarantine_staging_plan,
)


DEFAULT_INPUT = (
    ROOT / "runtime" / "ops" / "dealer-candidate-quarantine-20260715.json"
)
DEFAULT_OUTPUT = (
    ROOT
    / "runtime"
    / "ops"
    / "dealer-quarantine-staging-bridge-20260715.json"
)


def _load(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"JSON object required: {path}")
    return value


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Map a locally captured Dealer quarantine to candidate-staging "
            "previews. No network, SQL, source activation, import or map write."
        )
    )
    parser.add_argument("--input", type=Path, default=DEFAULT_INPUT)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--organization-id", type=int, default=1)
    args = parser.parse_args()

    plan = build_quarantine_staging_plan(
        _load(args.input), organization_id=args.organization_id
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(plan, ensure_ascii=False, indent=2, default=str) + "\n",
        encoding="utf-8",
    )
    os.chmod(args.output, 0o600)
    out(args.output)
    out(json.dumps(plan["summary"], ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
