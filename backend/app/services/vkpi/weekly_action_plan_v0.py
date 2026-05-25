"""Backwards-compat shim. Real implementation lives in the intelligence domain."""

from app.domains.intelligence.weekly_plan_use_case import *  # noqa: F401,F403
from app.domains.intelligence.weekly_plan_use_case import build_weekly_action_plan_v0
