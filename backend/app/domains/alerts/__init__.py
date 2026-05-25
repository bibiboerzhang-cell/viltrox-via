"""Alert domain facade."""

from app.domains.alerts.detail import get_alert_detail
from app.domains.alerts.service import (
    generate_alerts,
    generate_budget_guard_alerts,
    generate_comment_intelligence_alerts,
    generate_content_brain_backlog_alerts,
    generate_recommendation_review_gap_alerts,
    generate_stalled_project_alerts,
    list_alerts,
    resolve_alert,
    upsert_alert,
)
from app.domains.alerts.triage import apply_alert_triage_suggestions, build_alert_triage_suggestions

__all__ = [
    "apply_alert_triage_suggestions",
    "build_alert_triage_suggestions",
    "generate_alerts",
    "generate_budget_guard_alerts",
    "generate_comment_intelligence_alerts",
    "generate_content_brain_backlog_alerts",
    "generate_recommendation_review_gap_alerts",
    "generate_stalled_project_alerts",
    "get_alert_detail",
    "list_alerts",
    "resolve_alert",
    "upsert_alert",
]
