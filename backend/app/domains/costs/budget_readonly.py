"""Strictly read-only budget inspection for previews and GET paths."""
from __future__ import annotations

import json
import math
import sqlite3
from datetime import datetime
from typing import Any

from app.db.connection import get_conn
from app.domains.costs.budget_guard import (
    _budget_payload,
    _clean_row,
    _float,
    _is_single_call_ceiling_scope,
    _normalize_scope,
    _single_call_ceiling_allowed,
)
from app.domains.costs.budget_windows import project_budget_window


_BUDGET_TABLE = "vkpi_provider_budget_caps"


def _is_missing_legacy_sqlite_budget_table(
    exc: sqlite3.OperationalError,
) -> bool:
    """Match only the known pre-migration SQLite compatibility boundary.

    PostgreSQL relation drift and unrelated SQLite operational failures must
    remain visible to operators instead of being mislabeled as an unconfigured
    budget.  SQLite does not expose a table-specific error code, so the table
    name is checked exactly after first narrowing to ``OperationalError``.
    """

    message = " ".join(str(exc).strip().lower().split())
    prefix = "no such table:"
    if not message.startswith(prefix):
        return False
    table_name = message[len(prefix):].strip().strip('"`[]')
    return table_name in {_BUDGET_TABLE, f"main.{_BUDGET_TABLE}"}


def _not_configured_payload(
    scope_key: str,
    *,
    estimated_cost: float,
    reason: str,
) -> dict[str, Any]:
    """Return an honest, fail-closed status without mutating compatibility DBs.

    A deterministic zero-cost preview may continue.  Positive-cost/provider
    work stays blocked until the migration and exact scope configuration exist.
    """

    estimated = max(0.0, float(estimated_cost or 0))
    zero_cost_preview = estimated == 0.0
    return {
        "scope": scope_key,
        "status": "not_configured",
        "reason": reason,
        "configured": False,
        "allowed": zero_cost_preview,
        "provider_calls_allowed": False,
        "estimated_cost_usd": estimated,
        "read_only": True,
        "projected": False,
        "projection_status": "not_configured",
        "projection_warnings": [],
        "window_roll_pending": False,
    }


def _projection_warnings(row: dict[str, Any]) -> list[str]:
    warnings: list[str] = []
    numeric_bounds = {
        "cap_usd": (0.0, None),
        "current_spend": (0.0, None),
        "warning_at": (0.0, 1.0),
        "hard_stop_at": (0.0, 1.0),
    }
    for field, (lower, upper) in numeric_bounds.items():
        raw = row.get(field)
        try:
            value = float(raw)
        except (TypeError, ValueError):
            warnings.append(f"invalid_{field}")
            continue
        if (
            not math.isfinite(value)
            or value < lower
            or (upper is not None and value > upper)
        ):
            warnings.append(f"invalid_{field}")

    try:
        metadata = row.get("metadata_json")
        parsed_metadata = metadata if isinstance(metadata, dict) else json.loads(metadata)
        if not isinstance(parsed_metadata, dict):
            warnings.append("invalid_metadata_json")
    except (TypeError, ValueError, json.JSONDecodeError):
        warnings.append("invalid_metadata_json")

    try:
        warning_at = float(row.get("warning_at"))
        hard_stop_at = float(row.get("hard_stop_at"))
        if math.isfinite(warning_at) and math.isfinite(hard_stop_at) and warning_at > hard_stop_at:
            warnings.append("invalid_threshold_order")
    except (TypeError, ValueError):
        pass

    reset_raw = str(row.get("reset_at") or "").strip()
    if reset_raw:
        try:
            reset_at = datetime.fromisoformat(reset_raw.replace("Z", "+00:00"))
            if reset_at.tzinfo is None:
                warnings.append("invalid_reset_at")
        except (TypeError, ValueError):
            warnings.append("invalid_reset_at")
    return warnings


def _configured_payload(
    row: Any,
    *,
    estimated_cost: float,
) -> dict[str, Any]:
    clean = _clean_row(row)
    warnings = _projection_warnings(clean)
    projected, roll_pending, _ = project_budget_window(clean)
    projected = dict(projected)
    for field, default in (
        ("cap_usd", 0.0),
        ("current_spend", 0.0),
        ("warning_at", 0.8),
        ("hard_stop_at", 1.0),
    ):
        if f"invalid_{field}" in warnings:
            projected[field] = default
    payload = _budget_payload(
        projected,
        estimated_cost=float(estimated_cost or 0),
    )
    scope_key = _normalize_scope(str(payload.get("scope") or ""))
    if _is_single_call_ceiling_scope(scope_key):
        ceiling_allowed = _single_call_ceiling_allowed(
            _float(payload.get("cap_usd")),
            float(estimated_cost or 0),
        )
        payload["allowed"] = ceiling_allowed
        payload["hard_stopped"] = not ceiling_allowed
    if warnings:
        # Never convert malformed persisted values into an apparently open
        # budget.  The read response remains inspectable but execution stays
        # fail-closed until an operator repairs the row.
        payload["allowed"] = False
    payload["provider_calls_allowed"] = bool(payload.get("allowed"))
    return {
        "configured": True,
        **payload,
        "status": "invalid_data" if warnings else "ready",
        "reason": "budget_row_invalid" if warnings else "",
        "read_only": True,
        "projected": roll_pending,
        "projection_status": (
            "invalid_source" if warnings else ("projected" if roll_pending else "current")
        ),
        "projection_warnings": warnings,
        "window_roll_pending": roll_pending,
    }


def _empty_list_payload(*, reason: str) -> dict[str, Any]:
    return {
        "status": "not_configured",
        "reason": reason,
        "configured": False,
        "read_only": True,
        "projected": False,
        "window_roll_pending": False,
        "budgets": [],
        "summary": {
            "scopes": 0,
            "cap_usd": 0.0,
            "current_spend_usd": 0.0,
            "warnings": 0,
            "hard_stopped": 0,
            "projected_windows": 0,
            "invalid_rows": 0,
        },
    }


def get_budget_status_readonly(
    scope: str,
    *,
    estimated_cost: float = 0.0,
) -> dict[str, Any]:
    """Inspect effective status without schema bootstrap, UPDATE, or commit."""

    scope_key = _normalize_scope(scope)
    if not scope_key:
        raise ValueError("scope required")
    try:
        row = get_conn().execute(
            f"SELECT * FROM {_BUDGET_TABLE} WHERE scope=?",
            (scope_key,),
        ).fetchone()
    except sqlite3.OperationalError as exc:
        if not _is_missing_legacy_sqlite_budget_table(exc):
            raise
        return _not_configured_payload(
            scope_key,
            estimated_cost=estimated_cost,
            reason="budget_registry_not_migrated",
        )
    if not row:
        return _not_configured_payload(
            scope_key,
            estimated_cost=estimated_cost,
            reason="budget_scope_not_configured",
        )

    return _configured_payload(row, estimated_cost=estimated_cost)


def list_budget_status_readonly() -> dict[str, Any]:
    """List effective budget windows without schema bootstrap or mutations."""

    try:
        rows = get_conn().execute(
            f"SELECT * FROM {_BUDGET_TABLE} ORDER BY scope"
        ).fetchall()
    except sqlite3.OperationalError as exc:
        if not _is_missing_legacy_sqlite_budget_table(exc):
            raise
        return _empty_list_payload(reason="budget_registry_not_migrated")
    if not rows:
        return _empty_list_payload(reason="budget_scopes_not_configured")

    budgets = [
        _configured_payload(row, estimated_cost=0.0)
        for row in rows
    ]
    invalid_rows = sum(1 for row in budgets if row.get("status") == "invalid_data")
    projected_windows = sum(1 for row in budgets if row.get("projected"))
    return {
        "status": "degraded" if invalid_rows else "ready",
        "reason": "budget_rows_invalid" if invalid_rows else "",
        "configured": True,
        "read_only": True,
        "projected": bool(projected_windows),
        "window_roll_pending": bool(projected_windows),
        "budgets": budgets,
        "summary": {
            "scopes": len(budgets),
            "cap_usd": sum(float(row.get("cap_usd") or 0) for row in budgets),
            "current_spend_usd": sum(
                float(row.get("current_spend") or 0) for row in budgets
            ),
            "warnings": sum(1 for row in budgets if row.get("warning")),
            "hard_stopped": sum(1 for row in budgets if row.get("hard_stopped")),
            "projected_windows": projected_windows,
            "invalid_rows": invalid_rows,
        },
    }
