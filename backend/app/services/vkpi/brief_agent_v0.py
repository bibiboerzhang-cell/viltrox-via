"""Backwards-compat shim. Real implementation lives in the intelligence domain."""

from app.domains.intelligence.brief_use_case import *  # noqa: F401,F403
from app.domains.intelligence.brief_use_case import build_brief_agent_v0
