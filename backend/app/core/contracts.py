"""
core/contracts.py — shared vocabulary and canonical contract values
"""
from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Any

_PROJECT_ROOT = Path(__file__).resolve().parents[3]
_CONTRACTS_PATH = _PROJECT_ROOT / "shared" / "contracts.json"


@lru_cache(maxsize=1)
def load_contracts() -> dict[str, Any]:
    return json.loads(_CONTRACTS_PATH.read_text(encoding="utf-8"))


_DATA = load_contracts()

ACTOR_TIERS: dict[str, str] = dict(_DATA.get("actor_tiers") or {})
ACTOR_TIER_KEYS: tuple[str, ...] = tuple(ACTOR_TIERS.values())
ACTOR_TIER_ALIASES: dict[str, str] = {
    str(key).strip().lower(): str(value).strip()
    for key, value in dict(_DATA.get("actor_tier_aliases") or {}).items()
}
ROLE_KEYS: tuple[str, ...] = tuple(str(item).strip() for item in (_DATA.get("role_keys") or []))
SURFACE_KEYS: tuple[str, ...] = tuple(str(item).strip() for item in (_DATA.get("surface_keys") or []))
DEPRECATED_CONTRACT_LITERALS: tuple[str, ...] = tuple(
    str(item) for item in (_DATA.get("deprecated_literals") or [])
)


def _normalize_token(value: str) -> str:
    return str(value or "").strip().lower().replace("-", "_").replace(" ", "_").replace("/", "_")


def normalize_actor_tier(value: str, default: str = "authenticated") -> str:
    raw = str(value or "").strip()
    if not raw:
        return default
    alias = ACTOR_TIER_ALIASES.get(raw.lower())
    if alias:
        return alias
    normalized = _normalize_token(raw)
    if normalized in ACTOR_TIER_KEYS:
        return normalized
    return default


def normalize_surface(value: str, default: str = "upload") -> str:
    normalized = _normalize_token(value)
    if normalized in SURFACE_KEYS:
        return normalized
    return default


def is_valid_contract_value(group: str, value: str) -> bool:
    token = _normalize_token(value)
    if group == "actor_tiers":
        return token in ACTOR_TIER_KEYS
    if group == "role_keys":
        return token in ROLE_KEYS
    if group == "surface_keys":
        return token in SURFACE_KEYS
    raise KeyError(f"Unsupported contract group: {group}")
