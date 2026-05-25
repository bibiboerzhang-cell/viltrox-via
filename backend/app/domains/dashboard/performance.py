"""Dashboard performance use cases."""
from __future__ import annotations

from typing import Any

from app.domains.dashboard import decision_dashboard as decision_engine
from app.services.vkpi import scope


def build_revenue_trend(
    *,
    window_days: int = 7,
    staff_id: int | None = None,
    staff: dict[str, Any],
) -> dict[str, Any]:
    return decision_engine.revenue_trend(
        window_days=window_days,
        staff_id=scope.effective_staff_id(staff, staff_id),
    )


def build_product_performance(
    *,
    window_days: int = 30,
    staff_id: int | None = None,
    limit: int = 20,
    staff: dict[str, Any],
) -> dict[str, Any]:
    return decision_engine.product_performance(
        window_days=window_days,
        staff_id=scope.effective_staff_id(staff, staff_id),
        limit=limit,
    )
