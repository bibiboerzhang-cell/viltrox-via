"""Fail-closed tenant gate for legacy-global system administration.

The system administration tables and runtime/provider controls predate tenant
scoping.  Until those surfaces carry and enforce ``organization_id``, only the
original Viltrox workspace (organization 1) may reach them.  Authentication
still has to resolve exactly one active membership; owner/RBAC status never
substitutes for tenant resolution.
"""
from __future__ import annotations

from typing import Any

from fastapi import Depends, HTTPException

from app.api.dependencies.auth import get_user_required
from app.core.permissions import staff_context_for_user


LEGACY_SYSTEM_ADMIN_ORGANIZATION_ID = 1


def _organization_id(staff: dict[str, Any] | None) -> int:
    try:
        organization_id = int((staff or {}).get("organization_id") or 0)
    except (TypeError, ValueError):
        return 0
    return organization_id if organization_id > 0 else 0


def legacy_system_admin_scope_guard(
    staff: dict[str, Any] | None,
    *,
    surface: str = "legacy-global system administration",
    reason: str | None = None,
) -> dict[str, Any] | None:
    """Return ``None`` only for an explicitly resolved organization 1."""

    organization_id = _organization_id(staff)
    scope_status = str((staff or {}).get("organization_scope_status") or "unresolved")
    if (
        organization_id == LEGACY_SYSTEM_ADMIN_ORGANIZATION_ID
        and scope_status == "resolved"
    ):
        return None
    return {
        "status": "scope_unavailable",
        "claim_status": "descriptive_only",
        "organization_id": organization_id or None,
        "organization_scope_status": scope_status,
        "reason": reason
        or (
            f"{surface} remains backed by legacy-global state without enforced "
            "organization_id isolation; this request stopped before any read or write."
        ),
        "writes": False,
    }


async def require_legacy_system_admin_scope(
    user: dict[str, Any] = Depends(get_user_required),
) -> dict[str, Any]:
    """FastAPI dependency that stops unresolved/cross-tenant admin requests."""

    staff = staff_context_for_user(user)
    unavailable = legacy_system_admin_scope_guard(staff)
    if unavailable is not None:
        raise HTTPException(status_code=403, detail=unavailable)
    return staff


__all__ = [
    "LEGACY_SYSTEM_ADMIN_ORGANIZATION_ID",
    "legacy_system_admin_scope_guard",
    "require_legacy_system_admin_scope",
]
