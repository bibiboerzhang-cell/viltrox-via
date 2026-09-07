"""Recompute discovery freshness from auditable content, not provider verdicts."""
from __future__ import annotations

from datetime import datetime
from typing import Any

from app.domains.kol.profile_recall_activity_gate import activity_gate_evidence, evaluate_activity
from app.services.intelligence.account_search_provider_policy import validate_provider_discovery_policy


def discovery_time_gate(
    evidence: dict[str, Any], *, policy: dict[str, Any] | None, now: datetime,
) -> dict[str, Any] | None:
    """Historical relevance, recent activity and retrieval windows are distinct.

    A provider status flag, a channel creation date or fetched_at cannot satisfy
    this gate. Existing local/legacy calls without the versioned policy keep
    their original activity window. No extra fetch or refill occurs here.
    """
    contract = validate_provider_discovery_policy(policy)
    if contract is None:
        return None
    days = contract["discovery_max_age_days"]
    verdict = evaluate_activity(
        latest=evidence.get("latest_real_video"), now=now,
        max_video_age_days=days, fresh_priority_days=days,
    )
    reasons = {
        "latest_video_unknown": "discovery_content_date_unknown",
        "latest_video_in_future": "discovery_content_date_in_future",
        "latest_video_stale": "discovery_content_outside_window",
        "latest_video_not_active_video": "discovery_content_not_active",
        "latest_video_identity_missing": "discovery_content_identity_missing",
    }
    reason = reasons.get(verdict["reason"], verdict["reason"])
    proof = activity_gate_evidence(verdict, maximum_age_days=days, deferred=False)
    return {
        **proof, "schema": "discovery_content_window_gate_v1",
        "policy_hash": contract["policy_hash"], "reason": reason or None,
        "status": "passed" if verdict["passed"] else "pending" if reason in {
            "discovery_content_date_unknown", "discovery_content_identity_missing",
        } else "rejected",
        "basis": "observed_content_sample", "latest_content_proven": False,
        "evaluated_at": now.isoformat(), "provider_status_trusted": False,
        "historical_relevance_evaluated": False,
    }
