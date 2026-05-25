"""Compatibility bridge for KOL decision audit use cases."""
from __future__ import annotations

from app.domains.kol.decision_audit import (
    DECISION_OPTIONS,
    FOLLOWUP_OUTCOMES,
    SEVERITIES,
    create_decision,
    create_followup,
    ensure_kol_decision_followup_schema,
    ensure_kol_decision_schema,
    list_decisions,
    list_followup_queue,
)

__all__ = [
    "DECISION_OPTIONS",
    "FOLLOWUP_OUTCOMES",
    "SEVERITIES",
    "create_decision",
    "create_followup",
    "ensure_kol_decision_followup_schema",
    "ensure_kol_decision_schema",
    "list_decisions",
    "list_followup_queue",
]
