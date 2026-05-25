"""Dashboard view assembly use cases."""
from __future__ import annotations

from typing import Any

from app.domains import lineage as metric_lineage
from app.domains.dashboard import decision_dashboard as decision_engine
from app.services.vkpi import scope
from app.services.vkpi.workflow import staff_id as resolve_staff_id


def staff_id_from_context(view: str, staff: dict[str, Any]) -> int | None:
    if str(view or "").strip().lower() in {"staff", "employee"}:
        return resolve_staff_id(staff) or None
    return None


def build_dashboard_view_payload(
    *,
    view: str,
    window_days: int = 30,
    staff_id: int | None = None,
    staff: dict[str, Any],
) -> dict[str, Any]:
    requested_staff_id = staff_id if staff_id is not None else staff_id_from_context(view, staff)
    effective_staff_id = scope.effective_staff_id(staff, requested_staff_id)
    result = decision_engine.dashboard_view(view, window_days=window_days, staff_id=effective_staff_id)
    try:
        lineage = metric_lineage.dashboard_metrics(
            period_days=window_days,
            staff=staff,
            staff_id=effective_staff_id,
            generated_by_staff_id=resolve_staff_id(staff) or None,
        )
        result["metric_run"] = lineage.get("run") or {}
        result["metrics"] = lineage.get("metrics") or []
    except Exception:
        result["metric_run"] = {}
        result["metrics"] = []
    return result
