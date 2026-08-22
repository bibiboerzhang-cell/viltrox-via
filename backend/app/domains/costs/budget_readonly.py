"""Strictly read-only budget inspection for previews and GET paths."""
from __future__ import annotations

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


def get_budget_status_readonly(
    scope: str,
    *,
    estimated_cost: float = 0.0,
) -> dict[str, Any]:
    """Inspect effective status without schema bootstrap, UPDATE, or commit."""

    scope_key = _normalize_scope(scope)
    if not scope_key:
        raise ValueError("scope required")
    row = get_conn().execute(
        "SELECT * FROM vkpi_provider_budget_caps WHERE scope=?",
        (scope_key,),
    ).fetchone()
    if not row:
        return {
            "scope": scope_key,
            "configured": False,
            "allowed": True,
            "estimated_cost_usd": max(0.0, float(estimated_cost or 0)),
            "read_only": True,
            "window_roll_pending": False,
        }

    clean = _clean_row(row)
    projected, roll_pending, _ = project_budget_window(clean)
    payload = _budget_payload(
        projected,
        estimated_cost=float(estimated_cost or 0),
    )
    if _is_single_call_ceiling_scope(scope_key):
        ceiling_allowed = _single_call_ceiling_allowed(
            _float(payload.get("cap_usd")),
            float(estimated_cost or 0),
        )
        payload["allowed"] = ceiling_allowed
        payload["hard_stopped"] = not ceiling_allowed
    return {
        "configured": True,
        **payload,
        "read_only": True,
        "window_roll_pending": roll_pending,
    }
