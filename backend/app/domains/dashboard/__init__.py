"""Dashboard domain facade."""

from app.domains.dashboard.agents import (
    DASHBOARD_AGENT_SPECS,
    _build_dashboard_agents_inbox,
    _build_dashboard_agents_status,
    _build_dashboard_copilot_brief,
    _build_dashboard_tasks,
    build_dashboard_agents_status,
)
from app.domains.dashboard.kol_distribution import (
    build_dashboard_kol_distribution,
    build_dashboard_kol_distribution_pack,
)
from app.domains.dashboard.performance import build_product_performance, build_revenue_trend
from app.domains.dashboard.recent_content import (
    _dashboard_int,
    _dashboard_official_matrix_summary,
    _dashboard_recent_official_content,
    _dashboard_recent_ugc_content,
    _recent_content_sort_key,
    build_dashboard_recent_content,
)
from app.domains.dashboard.summary import build_dashboard_summary
from app.domains.dashboard.system_health import build_dashboard_system_health
from app.domains.dashboard.views import build_dashboard_view_payload, staff_id_from_context

__all__ = [
    "DASHBOARD_AGENT_SPECS",
    "_dashboard_int",
    "_dashboard_official_matrix_summary",
    "_dashboard_recent_official_content",
    "_dashboard_recent_ugc_content",
    "_build_dashboard_agents_inbox",
    "_build_dashboard_agents_status",
    "_build_dashboard_copilot_brief",
    "_build_dashboard_tasks",
    "_recent_content_sort_key",
    "build_dashboard_agents_status",
    "build_dashboard_kol_distribution",
    "build_dashboard_kol_distribution_pack",
    "build_dashboard_recent_content",
    "build_dashboard_summary",
    "build_dashboard_system_health",
    "build_dashboard_view_payload",
    "build_product_performance",
    "build_revenue_trend",
    "staff_id_from_context",
]
