"""Compatibility facade for V-KPI KOL claim/profile services."""
from __future__ import annotations

from app.services.vkpi.kol_claims_actions import claim, list_claims, list_kols, lookup, reassign, release, update_kol_manual
from app.services.vkpi.kol_claims_common import (
    HANDLE_RE,
    PLATFORM_ALIASES,
    SUPPORTED_PLATFORMS,
    _assert_kol_access,
    _claim_payload,
    _create_kol,
    _find_kol,
    _int,
    _json,
    _json_array,
    _row_or_empty,
    _rows_or_empty,
    _safe_json_loads,
    assert_kol_access,
    dedup_key,
    normalize_handle,
    normalize_platform,
    utcnow,
)
from app.services.vkpi.kol_claims_profile import profile

__all__ = [
    "HANDLE_RE",
    "PLATFORM_ALIASES",
    "SUPPORTED_PLATFORMS",
    "_assert_kol_access",
    "_claim_payload",
    "_create_kol",
    "_find_kol",
    "_int",
    "_json",
    "_json_array",
    "_row_or_empty",
    "_rows_or_empty",
    "_safe_json_loads",
    "assert_kol_access",
    "claim",
    "dedup_key",
    "list_claims",
    "list_kols",
    "lookup",
    "normalize_handle",
    "normalize_platform",
    "profile",
    "reassign",
    "release",
    "update_kol_manual",
    "utcnow",
]
