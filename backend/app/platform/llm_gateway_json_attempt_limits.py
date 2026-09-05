"""Pre-dispatch deadlines and spend evidence shared by JSON candidate stages."""
from __future__ import annotations

from typing import Any

from . import llm_gateway_invoke_limits as limits


def checkpoint(state: Any) -> None:
    if state.gateway.time.monotonic() >= state.deadline_at:
        raise limits.GatewayDeadlineExceeded("deadline exceeded before provider I/O")


def release_unstarted(state: Any, candidate: Any) -> None:
    reservations = state.gateway._llm_budget_reservations()
    if candidate.provider_marked_started:
        result = reservations.settle_llm_reservation(candidate.reservation_key, 0.0)
        if not result.get("settled"):
            raise RuntimeError("unstarted_reservation_not_settled")
    else:
        reservations.release_llm_reservation(candidate.reservation_key)


def outcome_unknown(
    state: Any, candidate: Any, result: dict[str, Any], attempt_status: str,
) -> bool:
    metered = any(
        state.gateway._safe_int(result.get(key)) > 0
        for key in ("input_tokens", "output_tokens")
    )
    no_io = result.get("provider_io_started") is False
    rejected = result.get("status") in limits.DEFINITIVE_REJECTIONS
    if metered or no_io or rejected:
        return False
    # Preserve legacy non-reserved valid results; strict production responses
    # need usage even when their JSON is valid. Failed shapes must never retry
    # another paid provider on the assumption that missing usage means free.
    return bool(candidate.reservation_key) or attempt_status != "success"
