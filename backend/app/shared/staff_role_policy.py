"""Dependency-free staff role policy shared across domain facades."""
from __future__ import annotations

from typing import Any


MANAGER_STAFF_ROLES = frozenset(
    {
        "admin",
        "manager",
        "lead",
        "marketing_lead",
        "marketing_manager",
        "marketing-manager",
    }
)


def has_manager_staff_role(staff: dict[str, Any]) -> bool:
    """Return the legacy manager-role verdict without importing a domain."""
    role = str(staff.get("role") or "").strip().lower()
    if int(staff.get("is_owner") or 0) == 1:
        return True
    return role in MANAGER_STAFF_ROLES
