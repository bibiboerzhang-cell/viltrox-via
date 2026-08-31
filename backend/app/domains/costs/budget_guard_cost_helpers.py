"""Pure scope planning helpers for cost-ledger writes."""
from __future__ import annotations

from typing import Any, Callable


def normalized_cost_provider(value: str) -> str:
    provider = str(value or "unknown").strip().lower() or "unknown"
    return "gemini" if provider == "google" else provider


def configured_optional_cost_scopes(
    conn: Any,
    *,
    optional_scopes: list[str] | tuple[str, ...] | None,
    update_budget_scopes: bool,
    normalize_scope: Callable[[str], str],
) -> tuple[set[str], set[str]]:
    optional_scope_keys = {normalize_scope(item) for item in (optional_scopes or []) if item}
    configured_optional_scopes = (
        {
            item
            for item in optional_scope_keys
            if conn.execute(
                "SELECT 1 FROM vkpi_provider_budget_caps WHERE scope=?", (item,)
            ).fetchone()
        }
        if update_budget_scopes
        else set()
    )
    return optional_scope_keys, configured_optional_scopes


def cost_scopes_to_update(
    *,
    scope_key: str,
    extra_scopes: list[str] | tuple[str, ...] | None,
    optional_scope_keys: set[str],
    configured_optional_scopes: set[str],
    update_budget_scopes: bool,
    normalize_scope: Callable[[str], str],
    is_single_call_ceiling_scope: Callable[[str], bool],
) -> list[str]:
    requested_scopes = [
        item
        for item in [
            scope_key,
            *(normalize_scope(item) for item in (extra_scopes or [])),
        ]
        if item and (item not in optional_scope_keys or item in configured_optional_scopes)
    ]
    if not update_budget_scopes:
        return []
    return [
        item
        for item in dict.fromkeys(requested_scopes)
        if not is_single_call_ceiling_scope(item)
    ]
