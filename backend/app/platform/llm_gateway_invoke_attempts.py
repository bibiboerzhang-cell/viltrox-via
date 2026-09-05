"""Candidate-attempt runtime for text LLM invocation.

This leaf imports only dependency-free invocation state. All provider, budget,
ledger, and logging operations remain injected from the public gateway.
"""
from __future__ import annotations

from typing import Any

from app.platform.llm_gateway_invoke_types import CandidateAttempt, InvocationContext
from app.platform import llm_gateway_invoke_limits as _limits


def _reserved_hard_stop(
    ctx: InvocationContext,
    attempt: CandidateAttempt,
    *,
    reason: str,
    error_type: str = "",
) -> dict[str, Any]:
    ctx.deps["_mark_reserved_attempt_unknown"](attempt.reservation_key)
    error = {
        "provider": attempt.provider,
        "model": attempt.model_id,
        "status": reason,
        "error": reason,
    }
    if error_type:
        error["error_type"] = error_type
    ctx.errors.append(error)
    fallback = ctx.deps["_rule_fallback"](
        ctx.safe_prompt,
        purpose=ctx.purpose,
        reason=reason,
        errors=ctx.errors,
    )
    fallback["budget_reservation_key"] = attempt.reservation_key
    return fallback


def _settle_reserved_attempt(
    ctx: InvocationContext,
    attempt: CandidateAttempt,
    cost_micro_usd: int,
) -> dict[str, Any] | None:
    if not attempt.reservation_key:
        return None
    try:
        settlement = ctx.deps["_llm_budget_reservations"]().settle_llm_reservation(
            attempt.reservation_key,
            float(cost_micro_usd) / 1_000_000,
        )
        if not bool(settlement.get("settled")):
            raise RuntimeError(
                str(settlement.get("reason") or "reservation_not_settled")
            )
    except Exception as exc:  # noqa: BLE001 - cost happened; keep open
        ctx.deps["logger"].error(
            "vkpi.llm_gateway.reservation_settlement_failed",
            extra={
                "provider": attempt.provider,
                "purpose": ctx.purpose,
                "reservation_key": attempt.reservation_key,
                "settlement_error_type": type(exc).__name__,
            },
        )
        return _reserved_hard_stop(
            ctx,
            attempt,
            reason="reservation_settlement_failed",
            error_type=type(exc).__name__,
        )
    return None


def _fleet_breaker_hard_stop(
    ctx: InvocationContext,
    attempt: CandidateAttempt,
    error_type: str,
) -> dict[str, Any]:
    reason = "fleet_breaker_store_unavailable_after_provider"
    if attempt.reservation_key:
        return _reserved_hard_stop(
            ctx,
            attempt,
            reason=reason,
            error_type=error_type,
        )
    ctx.errors.append(
        {
            "provider": attempt.provider,
            "model": attempt.model_id,
            "status": reason,
            "error": error_type,
        }
    )
    return ctx.deps["_rule_fallback"](
        ctx.safe_prompt,
        purpose=ctx.purpose,
        reason=reason,
        errors=ctx.errors,
    )


def prepare_candidate(
    ctx: InvocationContext,
    index: int,
    candidate: tuple[str, str, bool],
) -> CandidateAttempt | None:
    provider, model_id, explicit_model = candidate
    binding = ctx.deps["_resolve_gateway_binding"](provider, model_id)
    blocker = ctx.deps["_binding_call_blocker"](
        binding,
        explicit_model=explicit_model,
        require_runtime_verified=ctx.require_runtime_verified,
    )
    if blocker:
        ctx.errors.append(
            {
                "provider": provider or "unknown",
                "model": model_id,
                "binding": binding.binding,
                "status": "model_binding_blocked",
                "error": blocker,
            }
        )
        return None
    if not ctx.deps["_is_provider_configured"](provider):
        ctx.errors.append(
            {"provider": provider, "model": model_id, "status": "not_configured"}
        )
        return None
    caller = ctx.deps["_PROVIDER_CALLERS"].get(provider)
    if caller is None:
        ctx.errors.append(
            {"provider": provider, "model": model_id, "status": "not_implemented"}
        )
        return None
    estimated_cost = ctx.deps["_estimated_cost_usd"](
        provider,
        prompt=ctx.safe_prompt,
        max_output_tokens=ctx.max_output_tokens,
        binding=binding,
    )
    try:
        provider_allowed, budget_checks = ctx.deps["_budget_allows_provider"](
            provider,
            cost_scope=ctx.cost_scope,
            estimated_cost_usd=estimated_cost,
            require_configured=ctx.require_configured_budget,
        )
    except Exception as exc:  # noqa: BLE001 - fail closed before provider I/O
        ctx.errors.append(
            {
                "provider": provider,
                "model": model_id,
                "status": "budget_check_failed",
                "error": f"{type(exc).__name__}: {str(exc)[:260]}",
            }
        )
        return None
    if not provider_allowed:
        _record_provider_budget_block(
            ctx,
            provider=provider,
            model_id=model_id,
            binding=binding,
            estimated_cost=estimated_cost,
            budget_checks=budget_checks,
        )
        return None
    return CandidateAttempt(
        index=index,
        provider=provider,
        model_id=model_id,
        explicit_model=explicit_model,
        binding=binding,
        caller=caller,
        estimated_cost=estimated_cost,
        budget_checks=budget_checks,
    )


def _record_provider_budget_block(
    ctx: InvocationContext,
    *,
    provider: str,
    model_id: str,
    binding: Any,
    estimated_cost: float,
    budget_checks: Any,
) -> None:
    ctx.budget_warnings.append(
        {
            "stage": "provider_preflight",
            "provider": provider,
            "model": model_id,
            "reason": "budget_blocked",
            "estimated_cost_usd": estimated_cost,
            "budget_checks": budget_checks,
        }
    )
    ctx.deps["logger"].warning(
        "vkpi.llm_gateway.provider_budget_hard_stop",
        extra={
            "provider": provider,
            "purpose": ctx.purpose,
            "estimated_cost_usd": estimated_cost,
        },
    )
    ctx.deps["_record_budget_blocked_attempt"](
        provider,
        binding=binding,
        purpose=ctx.purpose,
        prompt=ctx.safe_prompt,
        cost_scope=ctx.cost_scope,
        estimated_cost_usd=estimated_cost,
        budget_checks=budget_checks,
        triggered_by=ctx.triggered_by,
        metadata=ctx.metadata,
        staff=ctx.staff,
    )
    ctx.errors.append(
        {
            "provider": provider,
            "model": model_id,
            "status": "budget_blocked",
            "error": "budget_blocked",
        }
    )


def _cleanup_open_failure(ctx: InvocationContext, attempt: CandidateAttempt) -> None:
    if attempt.breaker_permit is not None:
        try:
            ctx.deps["_abandon_strict_fleet_breaker"](attempt.breaker_permit)
        except Exception:
            ctx.deps["logger"].error(
                "vkpi.llm_gateway.fleet_breaker_abandon_failed",
                extra={
                    "provider": attempt.provider,
                    "model": attempt.binding.model_id,
                },
                exc_info=True,
            )
    if attempt.reservation_key:
        try:
            _limits.release_unstarted_reservation(ctx, attempt)
        except Exception:
            ctx.deps["logger"].error(
                "vkpi.llm_gateway.reservation_release_failed",
                extra={"reservation_key": attempt.reservation_key},
                exc_info=True,
            )


def _record_open_failure(
    ctx: InvocationContext,
    attempt: CandidateAttempt,
    exc: Exception,
) -> None:
    reason = str(getattr(exc, "reason", "") or type(exc).__name__)
    blocked_scope = str(getattr(exc, "scope", "") or "")
    failure_reason = str(getattr(exc, "reason", "") or "")
    blocked_status = (
        failure_reason
        if failure_reason.startswith("fleet_breaker_")
        else "budget_blocked"
    )
    ctx.deps["record_call"](
        provider=attempt.provider,
        model=attempt.binding.model_id,
        purpose=ctx.purpose,
        prompt=ctx.safe_prompt,
        status=blocked_status,
        fallback_used=True,
        cost_tag=ctx.cost_scope or ctx.deps["SINGLE_CALL_BUDGET_SCOPE"],
        triggered_by=ctx.triggered_by,
        metadata={
            **(ctx.metadata or {}),
            "reservation_key": attempt.reservation_key,
            "reservation_reason": reason,
            "reservation_scope": blocked_scope,
            "fleet_breaker_blocked": blocked_status.startswith("fleet_breaker_"),
            "estimated_cost_usd": attempt.estimated_cost,
            "resolved_model_binding": attempt.binding.to_dict(),
            "request_content_recorded": False,
        },
        staff=ctx.staff,
        update_budget_scopes=not ctx.enforce_atomic_reservation,
        force_cost_ledger=ctx.enforce_atomic_reservation,
    )
    ctx.errors.append(
        {
            "provider": attempt.provider,
            "model": attempt.model_id,
            "status": blocked_status,
            "error": reason,
            "scope": blocked_scope,
        }
    )


def _open_candidate(ctx: InvocationContext, attempt: CandidateAttempt) -> bool:
    try:
        if ctx.enforce_atomic_reservation:
            reservation = ctx.deps["_llm_budget_reservations"]().reserve_llm_budget(
                provider=attempt.provider,
                model=attempt.binding.model_id,
                purpose=ctx.purpose,
                prompt=ctx.safe_prompt,
                estimated_cost_usd=attempt.estimated_cost,
                cost_scope=ctx.cost_scope,
                metadata=ctx.metadata,
                staff=ctx.staff,
                triggered_by=ctx.triggered_by,
            )
            attempt.reservation_key = str(reservation.reservation_key or "")
        if _limits.deadline_hit(ctx):
            _cleanup_open_failure(ctx, attempt)
            return False
        attempt.breaker_permit = ctx.deps["_acquire_strict_fleet_breaker"](
            provider=attempt.provider,
            model=attempt.binding.model_id,
            enforce_atomic_reservation=ctx.enforce_atomic_reservation,
        )
        if _limits.deadline_hit(ctx):
            _cleanup_open_failure(ctx, attempt)
            return False
        if ctx.enforce_atomic_reservation:
            ctx.deps["_llm_budget_reservations"]().mark_llm_provider_started(
                attempt.reservation_key
            )
            attempt.provider_marked_started = True
    except Exception as exc:  # noqa: BLE001 - fail closed before provider I/O
        _cleanup_open_failure(ctx, attempt)
        _record_open_failure(ctx, attempt, exc)
        return False
    return True


def _complete_breaker_or_stop(
    ctx: InvocationContext,
    attempt: CandidateAttempt,
    outcome: Any,
    *,
    log_failure: bool,
) -> dict[str, Any] | None:
    try:
        if isinstance(outcome, dict) and outcome.get("provider_io_started") is False:
            ctx.deps["_abandon_strict_fleet_breaker"](attempt.breaker_permit)
        else:
            ctx.deps["_complete_strict_fleet_breaker"](attempt.breaker_permit, outcome)
    except Exception as exc:  # noqa: BLE001 - no fallback after state loss
        if log_failure:
            ctx.deps["logger"].error(
                "vkpi.llm_gateway.fleet_breaker_completion_failed",
                extra={
                    "provider": attempt.provider,
                    "model": attempt.binding.model_id,
                    "breaker_error_type": type(exc).__name__,
                },
            )
        return _fleet_breaker_hard_stop(ctx, attempt, type(exc).__name__)
    return None


def _record_reserved_or_stop(
    ctx: InvocationContext,
    attempt: CandidateAttempt,
    **kwargs: Any,
) -> dict[str, Any] | None:
    try:
        ctx.deps["_record_reserved_provider_attempt"](
            provider=attempt.provider,
            binding=attempt.binding,
            purpose=ctx.purpose,
            prompt=ctx.safe_prompt,
            cost_scope=ctx.cost_scope,
            reservation_key=attempt.reservation_key,
            estimated_cost_usd=attempt.estimated_cost,
            triggered_by=ctx.triggered_by,
            staff=ctx.staff,
            **kwargs,
        )
    except Exception as exc:  # noqa: BLE001 - hard stop after provider I/O
        ctx.deps["logger"].error(
            "vkpi.llm_gateway.provider_audit_failed",
            extra={
                "provider": attempt.provider,
                "purpose": ctx.purpose,
                "reservation_key": attempt.reservation_key,
                "audit_error_type": type(exc).__name__,
            },
        )
        return _reserved_hard_stop(
            ctx,
            attempt,
            reason="audit_ledger_unavailable",
            error_type=type(exc).__name__,
        )
    return None


def _handle_provider_exception(
    ctx: InvocationContext,
    attempt: CandidateAttempt,
    exc: Exception,
) -> dict[str, Any] | None:
    if attempt.reservation_key:
        hard_stop = _record_reserved_or_stop(
            ctx,
            attempt,
            status="provider_exception",
            metadata={
                **(ctx.metadata or {}),
                "provider_error_type": type(exc).__name__,
            },
        )
        if hard_stop is not None:
            return hard_stop
        ctx.deps["_mark_reserved_attempt_unknown"](attempt.reservation_key)
    breaker_stop = _complete_breaker_or_stop(
        ctx,
        attempt,
        exc,
        log_failure=True,
    )
    if breaker_stop is not None:
        return breaker_stop
    ctx.errors.append(
        {
            "provider": attempt.provider,
            "model": attempt.model_id,
            "status": "provider_exception",
            "error": (
                type(exc).__name__
                if attempt.reservation_key
                else f"{type(exc).__name__}: {str(exc)[:260]}"
            ),
        }
    )
    return None


def _handle_invalid_response(
    ctx: InvocationContext,
    attempt: CandidateAttempt,
) -> dict[str, Any] | None:
    if attempt.reservation_key:
        hard_stop = _record_reserved_or_stop(
            ctx,
            attempt,
            status="invalid_response",
            metadata=ctx.metadata,
        )
        if hard_stop is not None:
            return hard_stop
        ctx.deps["_mark_reserved_attempt_unknown"](attempt.reservation_key)
    breaker_stop = _complete_breaker_or_stop(
        ctx,
        attempt,
        "invalid_response",
        log_failure=False,
    )
    if breaker_stop is not None:
        return breaker_stop
    ctx.errors.append(
        {
            "provider": attempt.provider,
            "model": attempt.model_id,
            "status": "invalid_response",
            "error": "provider returned a non-object result",
        }
    )
    return None


def _response_metrics(
    ctx: InvocationContext,
    attempt: CandidateAttempt,
    result: dict[str, Any],
) -> tuple[int, int, str, int, int]:
    input_tokens = ctx.deps["_safe_int"](result.get("input_tokens"))
    output_tokens = ctx.deps["_safe_int"](result.get("output_tokens"))
    actual_model = str(result.get("model") or "").strip()
    cost_micro = ctx.deps["_estimate_cost_micro_usd"](
        attempt.provider,
        input_tokens,
        output_tokens,
        binding=attempt.binding,
    )
    cost_cents = ctx.deps["_micro_usd_to_cents"](cost_micro)
    return input_tokens, output_tokens, actual_model, cost_micro, cost_cents


def _record_success_or_stop(
    ctx: InvocationContext,
    attempt: CandidateAttempt,
    *,
    actual_model: str,
    input_tokens: int,
    output_tokens: int,
    cost_micro: int,
    cost_cents: int,
    result: dict[str, Any],
) -> tuple[Any, dict[str, Any] | None]:
    try:
        audit = ctx.deps["record_call"](
            provider=attempt.provider,
            model=actual_model or attempt.binding.model_id,
            purpose=ctx.purpose,
            prompt=ctx.safe_prompt,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cost_cents=cost_cents,
            cost_micro_usd=cost_micro,
            status="success",
            fallback_used=bool(ctx.errors),
            cost_tag=ctx.cost_scope,
            triggered_by=ctx.triggered_by,
            metadata={
                **(ctx.metadata or {}),
                "requested_model": (
                    attempt.binding.model_id if attempt.explicit_model else ""
                ),
                "actual_model": actual_model or attempt.binding.model_id,
                "resolved_model_binding": attempt.binding.to_dict(),
                "model_fallback_index": attempt.index,
                "latency_ms": result.get("latency_ms"),
                "attempt_errors": ctx.errors,
                "budget_checks": attempt.budget_checks,
                "budget_warnings": ctx.budget_warnings,
                "budget_gate": (
                    "atomic_reservation"
                    if attempt.reservation_key
                    else "provider_hard_stop"
                ),
                "estimated_cost_usd": attempt.estimated_cost,
                "reservation_key": attempt.reservation_key,
            },
            staff=ctx.staff,
            update_budget_scopes=not bool(attempt.reservation_key),
            force_cost_ledger=bool(attempt.reservation_key),
        )
    except Exception as exc:  # noqa: BLE001 - strict calls stop on audit loss
        if not attempt.reservation_key:
            raise
        ctx.deps["logger"].error(
            "vkpi.llm_gateway.provider_audit_failed",
            extra={
                "provider": attempt.provider,
                "purpose": ctx.purpose,
                "reservation_key": attempt.reservation_key,
                "audit_error_type": type(exc).__name__,
            },
        )
        return None, _reserved_hard_stop(
            ctx,
            attempt,
            reason="audit_ledger_unavailable",
            error_type=type(exc).__name__,
        )
    return audit, None


def _handle_model_mismatch(
    ctx: InvocationContext,
    attempt: CandidateAttempt,
    *,
    actual_model: str,
    input_tokens: int,
    output_tokens: int,
    cost_micro: int,
    cost_cents: int,
) -> dict[str, Any] | None:
    mismatch = {
        "provider": attempt.provider,
        "model": actual_model,
        "requested_model": attempt.binding.model_id,
        "status": "model_mismatch",
        "error": "provider response model did not match exact request",
    }
    try:
        ctx.deps["record_call"](
            provider=attempt.provider,
            model=actual_model,
            purpose=ctx.purpose,
            prompt=ctx.safe_prompt,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cost_cents=cost_cents,
            cost_micro_usd=cost_micro,
            status="model_mismatch",
            fallback_used=True,
            cost_tag=ctx.cost_scope,
            triggered_by=ctx.triggered_by,
            metadata={
                **(ctx.metadata or {}),
                "requested_model": attempt.binding.model_id,
                "actual_model": actual_model,
                "resolved_model_binding": attempt.binding.to_dict(),
                "attempt_errors": ctx.errors,
                "reservation_key": attempt.reservation_key,
            },
            staff=ctx.staff,
            update_budget_scopes=not bool(attempt.reservation_key),
            force_cost_ledger=bool(attempt.reservation_key),
        )
    except Exception as exc:  # noqa: BLE001 - strict calls stop on audit loss
        if not attempt.reservation_key:
            raise
        ctx.deps["logger"].error(
            "vkpi.llm_gateway.provider_audit_failed",
            extra={
                "provider": attempt.provider,
                "purpose": ctx.purpose,
                "reservation_key": attempt.reservation_key,
                "audit_error_type": type(exc).__name__,
            },
        )
        return _reserved_hard_stop(
            ctx,
            attempt,
            reason="audit_ledger_unavailable",
            error_type=type(exc).__name__,
        )
    ctx.errors.append(mismatch)
    if _limits.hold_unmetered_failure(ctx, attempt, input_tokens, output_tokens):
        return None
    settlement_stop = _settle_reserved_attempt(ctx, attempt, cost_micro)
    if settlement_stop is not None:
        return settlement_stop
    return None


def _handle_success_result(
    ctx: InvocationContext,
    attempt: CandidateAttempt,
    result: dict[str, Any],
    metrics: tuple[int, int, str, int, int],
) -> dict[str, Any] | None:
    input_tokens, output_tokens, actual_model, cost_micro, cost_cents = metrics
    if attempt.explicit_model and not attempt.binding.matches_response_model(actual_model):
        return _handle_model_mismatch(
            ctx,
            attempt,
            actual_model=actual_model,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cost_micro=cost_micro,
            cost_cents=cost_cents,
        )
    if attempt.reservation_key and input_tokens <= 0 and output_tokens <= 0:
        ctx.stop_reason = "provider_outcome_unknown"
        return _handle_reserved_failure(ctx, attempt, result, ctx.stop_reason, metrics)
    audit, hard_stop = _record_success_or_stop(
        ctx,
        attempt,
        actual_model=actual_model,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        cost_micro=cost_micro,
        cost_cents=cost_cents,
        result=result,
    )
    if hard_stop is not None:
        return hard_stop
    settlement_stop = _settle_reserved_attempt(ctx, attempt, cost_micro)
    if settlement_stop is not None:
        return settlement_stop
    if _limits.deadline_hit(ctx):
        return None
    result["fallback_used"] = bool(ctx.errors)
    result["purpose"] = ctx.purpose
    result["cost_micro_usd"] = cost_micro
    result["cost_cents"] = cost_cents
    result["resolved_model_binding"] = attempt.binding.to_dict()
    if attempt.reservation_key:
        result["budget_reservation_key"] = attempt.reservation_key
    ctx.hooks.store_cached_result(ctx.cache_plan, result, audit)
    return result


def _handle_empty_success(
    ctx: InvocationContext,
    attempt: CandidateAttempt,
    metrics: tuple[int, int, str, int, int],
) -> dict[str, Any] | None:
    input_tokens, output_tokens, _actual_model, cost_micro, _cost_cents = metrics
    hard_stop = _record_reserved_or_stop(
        ctx,
        attempt,
        status="empty_response",
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        cost_micro_usd=cost_micro,
        metadata=ctx.metadata,
    )
    if hard_stop is not None:
        return hard_stop
    if _limits.hold_unmetered_failure(ctx, attempt, input_tokens, output_tokens):
        return None
    return _settle_reserved_attempt(ctx, attempt, cost_micro)


def _handle_reserved_failure(
    ctx: InvocationContext,
    attempt: CandidateAttempt,
    result: dict[str, Any],
    status: str,
    metrics: tuple[int, int, str, int, int],
) -> dict[str, Any] | None:
    input_tokens, output_tokens, _actual_model, cost_micro, _cost_cents = metrics
    usage_known = input_tokens > 0 or output_tokens > 0
    hard_stop = _record_reserved_or_stop(
        ctx,
        attempt,
        status=status or "provider_failed",
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        cost_micro_usd=cost_micro if usage_known else 0,
        metadata=ctx.metadata,
    )
    if hard_stop is not None:
        return hard_stop
    if usage_known or status in _limits.DEFINITIVE_REJECTIONS or result.get("provider_io_started") is False:
        return _settle_reserved_attempt(ctx, attempt, cost_micro)
    ctx.deps["_mark_reserved_attempt_unknown"](attempt.reservation_key)
    return None


def _handle_failed_result(
    ctx: InvocationContext,
    attempt: CandidateAttempt,
    result: dict[str, Any],
    status: str,
    metrics: tuple[int, int, str, int, int],
) -> dict[str, Any] | None:
    if status == "success" and not attempt.reservation_key:
        _limits.hold_unmetered_failure(ctx, attempt, metrics[0], metrics[1])
    if attempt.reservation_key:
        hard_stop = (
            _handle_empty_success(ctx, attempt, metrics)
            if status == "success"
            else _handle_reserved_failure(ctx, attempt, result, status, metrics)
        )
        if hard_stop is not None:
            return hard_stop
    ctx.errors.append(
        {
            "provider": attempt.provider,
            "model": attempt.model_id,
            "status": "empty_response" if status == "success" else status or "failed",
            "error": str(result.get("error") or "")[:300],
        }
    )
    return None


def _handle_mapping_result(
    ctx: InvocationContext,
    attempt: CandidateAttempt,
    result: dict[str, Any],
) -> dict[str, Any] | None:
    status = str(result.get("status") or "")
    breaker_stop = _complete_breaker_or_stop(
        ctx,
        attempt,
        result,
        log_failure=True,
    )
    if breaker_stop is not None:
        return breaker_stop
    metrics = _response_metrics(ctx, attempt, result)
    if status == "success" and str(result.get("text") or "").strip():
        return _handle_success_result(ctx, attempt, result, metrics)
    return _handle_failed_result(ctx, attempt, result, status, metrics)


def execute_candidate(
    ctx: InvocationContext,
    attempt: CandidateAttempt,
) -> dict[str, Any] | None:
    return _limits.execute(
        ctx, attempt, open_candidate=_open_candidate, cleanup=_cleanup_open_failure,
        handle_exception=_handle_provider_exception, handle_invalid=_handle_invalid_response,
        handle_mapping=_handle_mapping_result,
    )
