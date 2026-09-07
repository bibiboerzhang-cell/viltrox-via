"""Pure communication evidence shape shared by domains and KPI projections.

No KOL delivery/ingestion receipt adapter is configured. Manual records and
manager-attested GTM snapshots retain their separate evidence classes.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

COMMUNICATION_NODES = frozenset({"outreach_sent", "reply_received"})
LABEL_SEMANTICS = "operational_preference_not_transport_success"
LABEL_SEMANTICS_VERSION = "operational_preference_no_transport_v1"
_ACTION_TIMES = {
    "was_shortlisted": "shortlisted_at", "was_rejected": "rejected_at", "was_claimed": "claimed_at",
    "project_created": "project_created_at", "agreement_reached": "agreement_at",
    "content_published": "content_published_at", "order_attributed": "first_order_at",
}


def _first_noncommunication_action(row: dict[str, Any]) -> str | None:
    stamps = []
    for flag, column in _ACTION_TIMES.items():
        if str(row.get(flag)).strip().lower() not in {"1", "t", "true", "yes", "on"}:
            continue
        value = row.get(column)
        if not isinstance(value, (str, datetime)):
            continue
        try:
            parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        except ValueError:
            continue
        stamps.append((parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc), str(value)))
    return min(stamps, key=lambda pair: pair[0])[1] if stamps else None


def communication_evidence() -> dict[str, Any]:
    return {
        "schema": "recommendation_communication_evidence/v1",
        "status": "unknown", "reason": "provider_receipt_unavailable",
        "evidence_class": "unverified_record", "claim_status": "descriptive_only",
        "sent": None, "replied": None, "transport_outcome_eligible": False,
    }


def project_outcome_communications(row: Any) -> dict[str, Any] | None:
    if row is None:
        return None
    result = dict(row)
    result.update(outreach_sent=None, reply_received=None, outreach_sent_at=None,
                  reply_at=None, reply_sentiment=None,
                  first_action_at=_first_noncommunication_action(result),
                  communication_evidence=communication_evidence())
    return result


def blocked_communication_record() -> dict[str, Any]:
    return {"recorded": False, "status": "unverified",
            "communication_evidence": communication_evidence()}


__all__ = ["COMMUNICATION_NODES", "LABEL_SEMANTICS", "LABEL_SEMANTICS_VERSION",
           "communication_evidence", "project_outcome_communications", "blocked_communication_record"]
