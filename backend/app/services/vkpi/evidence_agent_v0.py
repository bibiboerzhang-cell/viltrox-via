"""Backwards-compat shim. Real implementation lives in the intelligence domain."""

from app.domains.intelligence.evidence_agent_use_case import *  # noqa: F401,F403
from app.domains.intelligence.evidence_agent_use_case import build_evidence_agent_v0
