"""Intelligence domain facade."""

from app.domains.intelligence.brain_acceptance import (
    ACCEPTANCE_VERSION,
    REQUIRED_REPORTS,
    build_brain_layer_acceptance_report,
    build_brain_layer_module_row,
)
from app.domains.intelligence.brain_acceptance_use_case import build_brain_layer_acceptance_v0
from app.domains.intelligence.brief_agent import (
    BRIEF_AGENT_VERSION,
    build_brief_agent_report,
)
from app.domains.intelligence.brief_use_case import build_brief_agent_v0
from app.domains.intelligence.evidence_agent import (
    EVIDENCE_AGENT_VERSION,
    build_evidence_agent_report,
    build_evidence_chain_from_summary,
)
from app.domains.intelligence.evidence_agent_use_case import build_evidence_agent_v0
from app.domains.intelligence.recommendation_agent import (
    RECOMMENDATION_AGENT_VERSION,
    build_recommendation_agent_report,
    build_recommendation_candidate,
)
from app.domains.intelligence.recommendation_use_case import build_recommendation_agent_v0
from app.domains.intelligence.today_signals import (
    DEFAULT_LIMIT,
    DEFAULT_LOOKBACK_HOURS,
    DIGEST_VERSION,
    build_today_new_signals_report,
)
from app.domains.intelligence.today_signals_use_case import build_today_new_signals_v0
from app.domains.intelligence.weekly_plan import (
    PLAN_VERSION,
    build_weekly_action_plan_report,
)
from app.domains.intelligence.weekly_plan_use_case import build_weekly_action_plan_v0

__all__ = [
    "ACCEPTANCE_VERSION",
    "BRIEF_AGENT_VERSION",
    "DEFAULT_LIMIT",
    "DEFAULT_LOOKBACK_HOURS",
    "DIGEST_VERSION",
    "EVIDENCE_AGENT_VERSION",
    "PLAN_VERSION",
    "RECOMMENDATION_AGENT_VERSION",
    "REQUIRED_REPORTS",
    "build_brain_layer_acceptance_report",
    "build_brain_layer_acceptance_v0",
    "build_brain_layer_module_row",
    "build_brief_agent_report",
    "build_brief_agent_v0",
    "build_evidence_agent_report",
    "build_evidence_agent_v0",
    "build_evidence_chain_from_summary",
    "build_recommendation_agent_report",
    "build_recommendation_agent_v0",
    "build_recommendation_candidate",
    "build_today_new_signals_report",
    "build_today_new_signals_v0",
    "build_weekly_action_plan_report",
    "build_weekly_action_plan_v0",
]
