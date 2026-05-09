"""Decision-ready V-KPI dashboard aggregate compatibility exports."""
from __future__ import annotations

from app.services.vkpi.decision_dashboard import dashboard, dashboard_view, product_performance, revenue_trend
from app.services.vkpi.decision_staff import employee_workspace, staff_directory, staff_kpi, staff_profile

__all__ = [
    "dashboard",
    "dashboard_view",
    "product_performance",
    "revenue_trend",
    "employee_workspace",
    "staff_directory",
    "staff_kpi",
    "staff_profile",
]
