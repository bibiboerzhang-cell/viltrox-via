"""Dashboard summary assembly use cases."""
from __future__ import annotations

from typing import Any

from app.domains.dashboard.recent_content import _dashboard_official_matrix_summary
from app.services.vkpi import decision_engine, metric_lineage, scope
from app.services.vkpi.workflow import staff_id as resolve_staff_id


def build_dashboard_summary(
    *,
    window_days: int = 30,
    staff_id: int | None = None,
    staff: dict[str, Any],
) -> dict[str, Any]:
    effective_staff_id = scope.effective_staff_id(staff, staff_id)
    result = (
        decision_engine.dashboard_view("staff", window_days=window_days, staff_id=effective_staff_id)
        if effective_staff_id
        else decision_engine.dashboard(window_days=window_days)
    )
    lineage = metric_lineage.dashboard_metrics(
        period_days=window_days,
        staff=staff,
        staff_id=effective_staff_id,
        generated_by_staff_id=resolve_staff_id(staff) or None,
    )
    result["metric_run"] = lineage.get("run") or {}
    result["metrics"] = lineage.get("metrics") or []
    official_summary = _dashboard_official_matrix_summary(limit=20)
    if official_summary:
        result["official_matrix_summary"] = official_summary
        summary = result.get("summary") if isinstance(result.get("summary"), dict) else {}
        summary["official_account_count"] = official_summary["account_count"]
        summary["official_post_count"] = official_summary["post_count"]
        summary["official_total_views"] = official_summary["total_views"]
        result["summary"] = summary
    return result
