"""Local attempt boundaries for the fixed Stage-1 connectivity canary.

These helpers never grant readiness or make provider calls.  A reservation
release is delegated to the canonical manager only before provider I/O, and
its actual return value is retained.
"""
from __future__ import annotations

import math
from typing import Any, Callable, Mapping


def nonnegative_int(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    if not math.isfinite(number) or number < 0 or not number.is_integer():
        return None
    return int(number)


def actual_cost_micro_usd(
    row: Any,
    raw: Mapping[str, Any],
    *,
    estimate_cost: Callable[..., int],
) -> int | None:
    if raw.get("usage_complete") is False or raw.get("cost_usage_supported") is False:
        return None
    # Adapters normalize missing raw provider usage to zero.  A zero cost field
    # alone therefore cannot prove that this paid request consumed nothing.
    # This only checks normalized usage; raw-response completeness is not proven.
    input_tokens = nonnegative_int(raw.get("input_tokens"))
    output_tokens = nonnegative_int(raw.get("output_tokens"))
    if input_tokens is None or output_tokens is None or input_tokens + output_tokens == 0:
        return None
    reported = nonnegative_int(raw.get("cost_micro_usd"))
    if reported is not None:
        return reported
    return int(estimate_cost(row.provider, input_tokens, output_tokens, binding=row.resolved))


def remaining_timeout(deadline: float, per_call_seconds: int, now: float) -> int:
    remaining = deadline - now
    if remaining < 1:
        return 0
    return min(per_call_seconds, int(math.floor(remaining)))


def release_before_provider(reservations: Any, reservation_key: str, reason: str) -> str:
    """Attempt release before HTTP I/O without claiming an unconfirmed release.

    The canonical API releases only state=reserved.  After mark_started, even
    if this process knows HTTP has not begun, that API returns false: keep the
    open reservation and report it for reconciliation instead of relaxing it.
    """
    try:
        released = reservations.release_llm_reservation(reservation_key)
    except Exception:
        released = False
    return reason if released is True else f"{reason}_reservation_retained"
