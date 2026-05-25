"""Dashboard domain facade."""

from app.domains.dashboard.agents import (
    DASHBOARD_AGENT_SPECS,
    _build_dashboard_agents_inbox,
    _build_dashboard_agents_status,
    _build_dashboard_copilot_brief,
    _build_dashboard_tasks,
)

__all__ = [
    "DASHBOARD_AGENT_SPECS",
    "_build_dashboard_agents_inbox",
    "_build_dashboard_agents_status",
    "_build_dashboard_copilot_brief",
    "_build_dashboard_tasks",
]
