"""Staff profile and workspace use cases."""
from __future__ import annotations

from typing import Any

from app.domains.staff import decision_staff as decision_engine
from app.domains import audit
from app.domains.access import scope
from app.domains.projects.workflow import staff_id as resolve_staff_id
from app.shared import staff_role_policy


def is_manager_staff(staff: dict[str, Any]) -> bool:
    return staff_role_policy.has_manager_staff_role(staff)


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
