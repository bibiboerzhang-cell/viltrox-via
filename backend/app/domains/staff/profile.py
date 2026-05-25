"""Staff profile and workspace use cases."""
from __future__ import annotations

from typing import Any

from app.services.vkpi import audit, decision_engine, scope
from app.services.vkpi.workflow import staff_id as resolve_staff_id


def is_manager_staff(staff: dict[str, Any]) -> bool:
    role = str(staff.get("role") or "").strip().lower()
    if int(staff.get("is_owner") or 0) == 1:
        return True
    return role in {"admin", "manager", "lead", "marketing_lead", "marketing_manager", "marketing-manager"}


def staff_directory() -> dict[str, Any]:
    return decision_engine.staff_directory()


def build_staff_profile(
    staff_id: int,
    *,
    staff: dict[str, Any],
    window: str = "month",
    limit: int = 80,
) -> dict[str, Any]:
    result = decision_engine.staff_profile(staff_id, staff=staff, window=window, limit=limit)
    viewer_staff_id = resolve_staff_id(staff)
    audit.log_sensitive_access(
        staff_id=viewer_staff_id,
        action_type="view_staff_profile",
        resource_type="staff",
        resource_id=str(staff_id),
        page_path=f"/api/admin/vkpi/staff/{staff_id}/profile",
        metadata={"window": window, "limit": limit, "costs_visible": result.get("visibility", {}).get("costs_visible")},
    )
    audit.log_business_event(
        staff_id=viewer_staff_id,
        action_type="staff_profile_view",
        target_type="staff",
        target_id=staff_id,
        detail="view employee V-KPI profile",
        metadata={"window": window, "limit": limit},
    )
    return result


def build_staff_kpi(
    *,
    window: str = "month",
    staff_id: int | None = None,
    staff: dict[str, Any],
) -> dict[str, Any]:
    return decision_engine.staff_kpi(window=window, staff_id=scope.effective_staff_id(staff, staff_id))


def build_employee_workspace(
    *,
    staff_id: int | None = None,
    staff: dict[str, Any],
) -> dict[str, Any]:
    effective_staff_id = scope.effective_staff_id(staff, staff_id) or resolve_staff_id(staff)
    return decision_engine.employee_workspace(int(effective_staff_id or 0))
