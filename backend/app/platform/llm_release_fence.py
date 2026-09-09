"""Fail-closed release admission for every reviewed LLM transport.

This is a local dispatch check, not proof of provider settlement.  It never
changes historical unknown reservations or infers zero spend from an error.
"""
from __future__ import annotations

import logging
from typing import Any

from app.core import release_validation


logger = logging.getLogger(__name__)


class LlmReleaseFenced(RuntimeError):
    """The trusted release marker refused this request before transport I/O."""

    reason = "release_validation_fenced"
    code = reason
    retryable = False

    def __init__(self, *, provider_attempted: bool = False) -> None:
        super().__init__(self.reason)
        self.provider_attempted = bool(provider_attempted)


def assert_llm_provider_io_allowed() -> None:
    """Only a valid, explicitly inactive marker permits a new dispatch."""
    try:
        status = release_validation.release_validation_status()
        allowed = (
            isinstance(status, dict)
            and status.get("active") is False
            and status.get("valid") is True
        )
    except Exception:
        raise LlmReleaseFenced() from None
    if not allowed:
        raise LlmReleaseFenced()


def finish_fenced_sdk_attempt(
    gateway: Any, reservation_key: str, breaker: Any, rejection: LlmReleaseFenced,
) -> None:
    """Terminate a fenced SDK call without erasing earlier retry attempts.

Only this live call's key is eligible for the existing no-I/O cleanup.  The
settlement API refuses unknown rows. Cleanup failure retains the reservation
and the typed dispatch rejection; it must not enter the provider-error path.
"""
    try:
        gateway._abandon_strict_fleet_breaker(breaker)
    except Exception:
        logger.error("llm.release_fence.breaker_cleanup_failed")
    try:
        if rejection.provider_attempted:
            gateway._mark_reserved_attempt_unknown(reservation_key)
        else:
            reservations = gateway._llm_budget_reservations()
            if not reservations.release_llm_reservation(reservation_key):
                settled = reservations.settle_llm_reservation(reservation_key, 0.0)
                if not settled.get("settled"):
                    logger.error("llm.release_fence.reservation_retained")
    except Exception:
        logger.error("llm.release_fence.reservation_cleanup_failed")
    raise rejection
