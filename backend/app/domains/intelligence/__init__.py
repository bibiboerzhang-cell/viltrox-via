"""Intelligence domain facade."""

from app.domains.intelligence.brain_acceptance import (
    ACCEPTANCE_VERSION,
    REQUIRED_REPORTS,
    build_brain_layer_acceptance_report,
    build_brain_layer_module_row,
)
from app.domains.intelligence.today_signals import (
    DEFAULT_LIMIT,
    DEFAULT_LOOKBACK_HOURS,
    DIGEST_VERSION,
    build_today_new_signals_report,
)
from app.domains.intelligence.weekly_plan import (
    PLAN_VERSION,
    build_weekly_action_plan_report,
)

__all__ = [
    "ACCEPTANCE_VERSION",
    "DEFAULT_LIMIT",
    "DEFAULT_LOOKBACK_HOURS",
    "DIGEST_VERSION",
    "PLAN_VERSION",
    "REQUIRED_REPORTS",
    "build_brain_layer_acceptance_report",
    "build_brain_layer_module_row",
    "build_today_new_signals_report",
    "build_weekly_action_plan_report",
]
