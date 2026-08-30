"""Pure receipt-to-binding validation for outreach reply truth."""
from __future__ import annotations

import json
from typing import Any, Callable


def _loads(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    try:
        parsed = json.loads(str(value or "{}"))
    except (TypeError, ValueError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _snapshot_matches(
    receipt: dict[str, Any],
    binding: dict[str, Any],
    *,
    dt: Callable[[Any], Any],
) -> tuple[bool, Any, Any, Any]:
    first = dt(binding.get("first_outbound_at"))
    end = dt(binding.get("observation_end_at"))
    verified = dt(receipt.get("verified_at"))
    candidate_observed = dt(receipt.get("candidate_observed_at"))
    matches = not (
        str(receipt.get("binding_fingerprint") or "")
        != str(binding.get("binding_fingerprint") or "")
        or dt(receipt.get("first_outbound_at")) != first
        or dt(receipt.get("observation_end_at")) != end
        or first is None
        or end is None
        or verified is None
        or candidate_observed is None
        or not end <= candidate_observed <= verified
        or len(str(receipt.get("review_candidate_sha256") or "")) != 64
    )
    return matches, first, end, verified


def _candidate_matches(
    candidate: dict[str, Any],
    binding: dict[str, Any],
    *,
    dt: Callable[[Any], Any],
    channel: Callable[[Any], str],
) -> bool:
    candidate_first = candidate.get("first_outbound")
    scalar_pairs = (
        (
            int(candidate.get("action_inbox_id") or 0),
            int(binding.get("action_inbox_id") or 0),
        ),
        (
            str(candidate.get("prediction_run_id") or ""),
            str(binding.get("prediction_run_id") or ""),
        ),
        (int(candidate.get("project_id") or 0), int(binding.get("project_id") or 0)),
        (
            int(candidate.get("kol_pool_id") or 0),
            int(binding.get("kol_pool_id") or 0),
        ),
        (int(candidate.get("kol_id") or 0), int(binding.get("kol_id") or 0)),
        (
            str(candidate.get("product_sku") or ""),
            str(binding.get("product_sku") or ""),
        ),
        (
            str(candidate.get("approval_snapshot_sha256") or ""),
            str(binding.get("approval_snapshot_sha256") or ""),
        ),
    )
    if any(left != right for left, right in scalar_pairs):
        return False
    if channel(candidate.get("channel")) != channel(binding.get("channel")):
        return False
    if dt(candidate.get("action_approved_at")) != dt(binding.get("action_approved_at")):
        return False
    if dt(candidate.get("observation_start_at")) != dt(
        binding.get("observation_start_at")
    ):
        return False
    if not isinstance(candidate_first, dict):
        return False
    return bool(
        int(candidate_first.get("message_id") or 0)
        == int(binding.get("first_outbound_message_id") or 0)
        and dt(candidate_first.get("created_at"))
        == dt(binding.get("first_outbound_created_at"))
    )


def _outcome_matches(
    receipt: dict[str, Any],
    *,
    first: Any,
    end: Any,
    verified: Any,
    dt: Callable[[Any], Any],
) -> bool:
    outcome = str(receipt.get("outcome") or "")
    captured = dt(receipt.get("inbound_captured_at"))
    created = dt(receipt.get("inbound_created_at"))
    if outcome == "replied":
        return not (
            receipt.get("inbound_message_id") is None
            or captured is None
            or created is None
            or not first < captured <= created <= end
            or created > verified
        )
    if outcome == "no_reply":
        return not (
            receipt.get("inbound_message_id") is not None
            or captured is not None
            or created is not None
            or verified < end
        )
    return False


def verified_receipt_matches_binding(
    receipt: dict[str, Any],
    binding: dict[str, Any],
    *,
    dt: Callable[[Any], Any],
    channel: Callable[[Any], str],
) -> bool:
    snapshot_matches, first, end, verified = _snapshot_matches(
        receipt,
        binding,
        dt=dt,
    )
    if not snapshot_matches:
        return False
    if not _candidate_matches(
        _loads(receipt.get("review_candidate_json")),
        binding,
        dt=dt,
        channel=channel,
    ):
        return False
    return _outcome_matches(
        receipt,
        first=first,
        end=end,
        verified=verified,
        dt=dt,
    )


__all__ = ["verified_receipt_matches_binding"]
