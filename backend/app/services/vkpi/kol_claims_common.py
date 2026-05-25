"""Shared KOL claim helpers for V-KPI."""
from __future__ import annotations

from datetime import datetime
from typing import Any

from app.domains.kol import claim_access
from app.domains.kol import claim_query_helpers
from app.domains.kol import claim_store
from app.domains.kol.claim_payloads import (
    claim_payload,
    json_array,
    json_object,
)
from app.domains.kol.identity import (
    HANDLE_RE,
    PLATFORM_ALIASES,
    SUPPORTED_PLATFORMS,
    dedup_key,
    normalize_handle,
    normalize_platform,
)

def utcnow() -> str:
    return datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")

def _json(value: Any) -> str:
    return json_object(value)

def _json_array(value: Any) -> str:
    return json_array(value)

def _int(value: Any, default: int = 0) -> int:
    try:
        return int(value or default)
    except (TypeError, ValueError):
        return default

def _claim_payload(row: Any) -> dict[str, Any]:
    return claim_payload(row)

def _find_kol(platform: str, handle: str) -> dict[str, Any] | None:
    return claim_store.find_kol(platform, handle)

def _safe_json_loads(value: Any, fallback: Any) -> Any:
    return claim_query_helpers.safe_json_loads(value, fallback)

def _rows_or_empty(sql: str, params: tuple[Any, ...] = ()) -> list[dict[str, Any]]:
    return claim_query_helpers.rows_or_empty(sql, params)

def _row_or_empty(sql: str, params: tuple[Any, ...] = ()) -> dict[str, Any]:
    return claim_query_helpers.row_or_empty(sql, params)

def assert_kol_access(kol_id: int, staff: dict[str, Any] | None, *, allow_unclaimed: bool = False) -> None:
    claim_access.assert_kol_access(kol_id, staff, allow_unclaimed=allow_unclaimed)

def _assert_kol_access(kol_id: int, staff: dict[str, Any] | None) -> None:
    assert_kol_access(kol_id, staff, allow_unclaimed=False)

def _create_kol(platform: str, handle: str, body: dict[str, Any], actor_staff_id: int) -> dict[str, Any]:
    return claim_store.create_kol(platform, handle, body, actor_staff_id)
