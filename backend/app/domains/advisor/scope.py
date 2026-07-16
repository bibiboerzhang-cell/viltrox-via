"""Fail-closed organization/staff scope for private Marketing Advisor data."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping


class AdvisorScopeError(ValueError):
    """The authenticated identity cannot be resolved to one private-data owner."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


@dataclass(frozen=True, slots=True)
class AdvisorScope:
    organization_id: int
    staff_id: int
    user_id: int

    def as_dict(self) -> dict[str, int]:
        return {
            "organization_id": self.organization_id,
            "staff_id": self.staff_id,
            "user_id": self.user_id,
        }


def _positive_int(value: Any) -> int:
    try:
        parsed = int(value or 0)
    except (TypeError, ValueError):
        return 0
    return parsed if parsed > 0 else 0


def advisor_scope_from_staff(staff: Mapping[str, Any] | None) -> AdvisorScope:
    """Resolve exactly one tenant and one staff owner; never infer or fall back.

    ``staff_context_for_user`` is the sole upstream resolver. In particular,
    this function must not call ``current_org_id`` because that legacy helper
    may fall back to organization 1 when request context is missing.
    """

    if not isinstance(staff, Mapping):
        raise AdvisorScopeError("staff_context_missing")
    scope_status = str(staff.get("organization_scope_status") or "").strip().lower()
    if scope_status != "resolved":
        raise AdvisorScopeError(f"organization_scope_{scope_status or 'missing'}")
    organization_id = _positive_int(staff.get("organization_id"))
    staff_id = _positive_int(staff.get("id") or staff.get("staff_id"))
    user_id = _positive_int(staff.get("user_id"))
    if organization_id <= 0:
        raise AdvisorScopeError("organization_id_missing")
    if staff_id <= 0:
        raise AdvisorScopeError("staff_id_missing")
    if user_id <= 0:
        raise AdvisorScopeError("user_id_missing")
    return AdvisorScope(
        organization_id=organization_id,
        staff_id=staff_id,
        user_id=user_id,
    )
