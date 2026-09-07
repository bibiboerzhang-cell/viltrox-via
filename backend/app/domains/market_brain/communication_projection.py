"""Public GTM communication observations are not provider transport receipts.

Apply only after immutable window evidence has been verified. This projection
never changes stored snapshots or the separate action-bound human review result.
"""
from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from app.shared.communication_truth import communication_evidence

COMMUNICATION_FIELDS = ("contacted", "outreach_sent_n", "replied", "reply_n", "reply_rate")


def project_communication_metrics(metrics: Mapping[str, Any]) -> dict[str, Any]:
    result = dict(metrics)
    result.setdefault("recorded_communication_metrics", {
        key: metrics[key] for key in COMMUNICATION_FIELDS if key in metrics
    })
    result.update({key: None for key in COMMUNICATION_FIELDS})
    result.update(communication_evidence=communication_evidence(),
                  communication_label_semantics="manual_records_not_transport_results")
    return result


def project_public_7d_window(window: Any) -> Any:
    if not isinstance(window, Mapping):
        return window
    flat_fields = any(key in window for key in COMMUNICATION_FIELDS)
    nested_metrics = isinstance(window.get("metrics"), Mapping)
    if not flat_fields and not nested_metrics:
        return window
    result = project_communication_metrics(window) if flat_fields else dict(window)
    if nested_metrics:
        result["metrics"] = project_communication_metrics(window["metrics"])
    result.update(public_projection="communication_truth_v1",
                  evidence_sha256_basis="original_stored_window_not_public_projection")
    return result


def project_public_outcomes(items: list[dict[str, Any]], claimable: Any) -> list[dict[str, Any]]:
    """Finalize public rows only after their original window evidence was checked."""
    return [{**item, "claimable": bool(claimable) and bool(item.get("finalized") and item.get("evidence_backed")),
             "window_7d": project_public_7d_window(item.get("window_7d"))} for item in items]
