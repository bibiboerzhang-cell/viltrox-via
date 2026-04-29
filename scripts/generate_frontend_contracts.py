#!/usr/bin/env python3
from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "shared" / "contracts.json"
TARGET = ROOT / "frontend" / "src" / "lib" / "contracts.generated.ts"


def main() -> None:
    payload = json.loads(SOURCE.read_text(encoding="utf-8"))
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
    TARGET.write_text("\n".join(lines), encoding="utf-8")


if __name__ == "__main__":
    main()
