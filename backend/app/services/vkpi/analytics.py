"""V-KPI product analysis and suggested outreach adapter."""
from __future__ import annotations

from app.services.vkpi.analytics_actions import (
    claim_suggestion,
    create_project_from_suggestion,
    dismiss_suggestion,
)
from app.services.vkpi.analytics_common import DEFAULT_PLATFORMS
from app.services.vkpi.analytics_digest import (
    daily_staff_outreach_digest_status,
    generate_daily_staff_outreach_digest,
    list_daily_staff_outreach_digest,
)
from app.services.vkpi.analytics_monitor import (
    compare_products,
    delete_monitored_product,
    get_run,
    list_monitored_products,
    list_runs,
    list_suggestions,
    monitor_product,
    suggestions_overview,
    upsert_monitored_product,
)
from app.services.vkpi.analytics_suggestions import rank_uncontacted_suggestions

__all__ = [
    "DEFAULT_PLATFORMS",
    "claim_suggestion",
    "compare_products",
    "create_project_from_suggestion",
    "daily_staff_outreach_digest_status",
    "delete_monitored_product",
    "dismiss_suggestion",
    "generate_daily_staff_outreach_digest",
    "get_run",
    "list_daily_staff_outreach_digest",
    "list_monitored_products",
    "list_runs",
    "list_suggestions",
    "monitor_product",
    "rank_uncontacted_suggestions",
    "suggestions_overview",
    "upsert_monitored_product",
]
