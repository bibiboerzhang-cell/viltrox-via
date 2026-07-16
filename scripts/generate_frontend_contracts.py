#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "shared" / "contracts.json"
TARGET = ROOT / "frontend" / "src" / "lib" / "contracts.generated.ts"


def render_contracts(payload: dict) -> str:
    actor_tiers = payload["actor_tiers"]
    role_keys = payload["role_keys"]
    surface_keys = payload["surface_keys"]
    deprecated = payload["deprecated_literals"]

    lines = [
        "/* eslint-disable */",
        "/* This file is generated from shared/contracts.json. Do not edit manually. */",
        "",
        "export const ACTOR_TIERS = {",
    ]
    for key, value in actor_tiers.items():
        lines.append(f'  {key}: "{value}",')
    lines.extend(
        [
            "} as const;",
            "",
            "export type ActorTierKey = (typeof ACTOR_TIERS)[keyof typeof ACTOR_TIERS];",
            "",
            "export const ROLE_KEYS = "
            + json.dumps(role_keys, ensure_ascii=False)
            + " as const;",
            "export type RoleKey = (typeof ROLE_KEYS)[number];",
            "",
            "export const SURFACE_KEYS = "
            + json.dumps(surface_keys, ensure_ascii=False)
            + " as const;",
            "export type SurfaceKey = (typeof SURFACE_KEYS)[number];",
            "",
            "export const DEPRECATED_CONTRACT_LITERALS = "
            + json.dumps(deprecated, ensure_ascii=False)
            + " as const;",
            "",
        ]
    )
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--check",
        action="store_true",
        help="Fail when the checked-in TypeScript contract differs; never write files.",
    )
    args = parser.parse_args()

    rendered = render_contracts(json.loads(SOURCE.read_text(encoding="utf-8")))
    if args.check:
        current = TARGET.read_text(encoding="utf-8") if TARGET.exists() else ""
        if current != rendered:
            sys.stderr.write(
                "frontend/src/lib/contracts.generated.ts is stale; "
                "run scripts/generate_frontend_contracts.py and review the diff.\n"
            )
            return 1
        return 0

    TARGET.write_text(rendered, encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
