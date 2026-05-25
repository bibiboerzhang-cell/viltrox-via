"""Backwards-compat shim. Real implementation lives in the intelligence domain."""

from app.domains.intelligence.recommendation_use_case import *  # noqa: F401,F403
from app.domains.intelligence.recommendation_use_case import build_recommendation_agent_v0
