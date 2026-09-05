"""Shared cooperative call limits; never detach or repeat an uncertain call."""
from __future__ import annotations

import math
import time
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Any, Callable, Iterator

from app.platform.llm_gateway_invoke_types import CandidateAttempt, InvocationContext


DEFAULT_PROVIDER_ATTEMPTS = 2
MAX_PROVIDER_ATTEMPTS = 3
DEFINITIVE_REJECTIONS = frozenset({
    "provider_429", "provider_config_unsupported", "not_configured",
})
_HTTP_DEADLINE: ContextVar[tuple[float, Callable[[], float]] | None] = ContextVar(
    "llm_http_deadline", default=None,
)


class GatewayDeadlineExceeded(TimeoutError):
    """The gateway stopped an HTTP request before any provider I/O."""


def resolve_deadline(seconds: Any, resolver: Any) -> float:
    """Clamp invalid budgets and inherit the caller's remaining allowance."""
    resolved = resolver(seconds)
    duration = resolved if math.isfinite(resolved) else 90.0
    prior = _HTTP_DEADLINE.get()
    if prior is not None:
        duration = min(duration, max(0.0, prior[0] - prior[1]()))
    return max(0.0, duration)


def normalize_attempt_limit(attempts: Any) -> int:
    try:
        limit = DEFAULT_PROVIDER_ATTEMPTS if attempts is None else int(attempts)
    except (ValueError, TypeError, OverflowError):
        limit = DEFAULT_PROVIDER_ATTEMPTS
    return max(1, min(MAX_PROVIDER_ATTEMPTS, limit))


def configure(ctx: InvocationContext, seconds: float | None, attempts: int | None) -> None:
    from app.platform.llm_gateway_json import _resolve_deadline_seconds

    ctx.clock = ctx.namespace.get("time", time).monotonic
    ctx.started = ctx.clock()
    resolver = ctx.namespace.get("_resolve_deadline_seconds", _resolve_deadline_seconds)
    ctx.deadline_seconds = resolve_deadline(seconds, resolver)
    ctx.deadline_at = ctx.started + ctx.deadline_seconds
    ctx.attempt_limit = normalize_attempt_limit(attempts)


def deadline_hit(ctx: InvocationContext) -> bool:
    if ctx.clock() < ctx.deadline_at:
        return False
    if ctx.stop_reason != "deadline_exceeded":
        ctx.errors.append({
            "provider": "gateway", "status": "deadline_exceeded",
            "error": "gateway deadline exceeded",
        })
    ctx.stop_reason = "deadline_exceeded"
    return True


def stop_before_provider(ctx: InvocationContext) -> bool:
    if deadline_hit(ctx) or ctx.stop_reason:
        return True
    if ctx.provider_attempts < ctx.attempt_limit:
        return False
    ctx.stop_reason = "provider_attempt_limit"
    ctx.errors.append({
        "provider": "gateway", "status": ctx.stop_reason,
        "error": f"provider attempt limit reached ({ctx.attempt_limit})",
    })
    return True


def annotate(ctx: InvocationContext, result: dict[str, Any]) -> dict[str, Any]:
    result.update({
        "deadline_seconds": ctx.deadline_seconds,
        "elapsed_ms": max(0, int((ctx.clock() - ctx.started) * 1000)),
        "provider_attempts": ctx.provider_attempts,
        "max_provider_attempts": ctx.attempt_limit,
    })
    if ctx.stop_reason and ctx.last_reservation_key:
        result.setdefault("budget_reservation_key", ctx.last_reservation_key)
    return result


@contextmanager
def provider_deadline(deadline_at: float, clock: Callable[[], float]) -> Iterator[None]:
    prior = _HTTP_DEADLINE.get()
    # A nested gateway must not extend its parent's remaining allowance.
    effective = min(deadline_at, prior[0]) if prior is not None else deadline_at
    token = _HTTP_DEADLINE.set((effective, clock))
    try:
        yield
    finally:
        _HTTP_DEADLINE.reset(token)


def bounded_http_timeout(configured: float) -> float:
    current = _HTTP_DEADLINE.get()
    if current is None:
        return float(configured)
    deadline_at, clock = current
    remaining = deadline_at - clock()
    if remaining <= 0:
        raise GatewayDeadlineExceeded("gateway deadline exceeded before HTTP request")
    return min(float(configured), remaining)


def release_unstarted_reservation(ctx: InvocationContext, attempt: CandidateAttempt) -> None:
    reservations = ctx.deps["_llm_budget_reservations"]()
    if not attempt.provider_marked_started:
        reservations.release_llm_reservation(attempt.reservation_key)
        return
    # mark-start itself can consume the final milliseconds. No provider caller
    # was entered, so settle zero rather than manufacture an uncertain spend.
    settlement = reservations.settle_llm_reservation(attempt.reservation_key, 0.0)
    if not settlement.get("settled"):
        raise RuntimeError("unstarted_reservation_not_settled")


def hold_unmetered_failure(
    ctx: InvocationContext, attempt: CandidateAttempt, input_tokens: int, output_tokens: int,
) -> bool:
    """A failed HTTP-success shape without usage is not proof of zero cost."""
    if input_tokens > 0 or output_tokens > 0:
        return False
    ctx.stop_reason = "provider_outcome_unknown"
    if attempt.reservation_key:
        ctx.deps["_mark_reserved_attempt_unknown"](attempt.reservation_key)
    return True


def execute(
    ctx: InvocationContext, attempt: CandidateAttempt, *, open_candidate: Any,
    cleanup: Any, handle_exception: Any, handle_invalid: Any, handle_mapping: Any,
) -> dict[str, Any] | None:
    if stop_before_provider(ctx) or not open_candidate(ctx, attempt):
        return None
    if deadline_hit(ctx):
        cleanup(ctx, attempt)
        return None
    ctx.last_reservation_key = attempt.reservation_key
    try:
        with provider_deadline(ctx.deadline_at, ctx.clock):
            ctx.provider_attempts += 1
            kwargs = {"model_override": attempt.binding.model_id} if attempt.explicit_model else {}
            raw = attempt.caller(ctx.safe_prompt, ctx.max_output_tokens, **kwargs)
    except Exception as exc:
        result = handle_exception(ctx, attempt, exc)
        ctx.stop_reason = "provider_outcome_unknown"
    else:
        if not isinstance(raw, dict):
            result = handle_invalid(ctx, attempt)
            ctx.stop_reason = "provider_outcome_unknown"
        else:
            result = handle_mapping(ctx, attempt, raw)
            if (
                str(raw.get("status")) != "success"
                and str(raw.get("status")) not in DEFINITIVE_REJECTIONS
                and raw.get("provider_io_started") is not False
                and not any(ctx.deps["_safe_int"](raw.get(key)) > 0 for key in ("input_tokens", "output_tokens"))
            ):
                ctx.stop_reason = "provider_outcome_unknown"
    if deadline_hit(ctx) and result is not None and result.get("status") == "success":
        return None
    return result
