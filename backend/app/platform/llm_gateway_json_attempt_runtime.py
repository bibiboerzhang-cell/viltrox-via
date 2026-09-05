"""One-candidate execution stages for JSON-contract LLM invocation."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from . import llm_gateway_invoke_limits as _limits
from . import llm_gateway_json_attempt_limits as _attempt_limits


@dataclass
class JsonCandidate:
    index: int
    provider: str
    model_id: str
    explicit_model: bool
    binding: Any
    caller: Any
    estimated_cost_usd: float
    budget_checks: list[dict[str, Any]]
    reservation_key: str = ""
    breaker_permit: Any = None
    provider_marked_started: bool = False


@dataclass
class JsonAttemptEvaluation:
    result: dict[str, Any]
    provider_status: str
    attempt_status: str
    attempt_error: str
    parsed_value: Any
    completed_after_deadline: bool
    result_micro_for_settlement: int
    outcome_unknown: bool = False


@dataclass(frozen=True)
class CandidateDecision:
    action: str
    result: dict[str, Any] | None = None


CONTINUE = CandidateDecision("continue")
BREAK = CandidateDecision("break")


def _elapsed_ms(state: Any) -> int:
    return max(
        0,
        int((state.gateway.time.monotonic() - state.started) * 1000),
    )


def _fallback_result(
    state: Any,
    *,
    reason: str,
    reservation_key: str | None,
) -> dict[str, Any]:
    fallback = state.gateway._rule_fallback(
        state.prompt,
        purpose=state.purpose,
        reason=reason,
        errors=state.errors,
    )
    fallback.update(
        {
            "json": None,
            "deadline_seconds": state.deadline_seconds,
            "elapsed_ms": _elapsed_ms(state),
            "provider_attempts": state.provider_attempts,
            "max_provider_attempts": state.attempt_limit,
            "budget_reservation_key": reservation_key,
        }
    )
    return fallback


def _budget_blocked_scopes(
    budget_checks: list[dict[str, Any]],
) -> list[dict[str, str]]:
    return [
        {
            "scope": str(check.get("scope") or ""),
            "reason": (
                "scope_not_configured"
                if not check.get("configured")
                else "hard_stopped"
                if check.get("hard_stopped")
                else "cap_exceeded"
            ),
        }
        for check in budget_checks
        if isinstance(check, dict) and not check.get("allowed")
    ]


def _budget_candidate(
    state: Any,
    *,
    index: int,
    provider: str,
    model_id: str,
    explicit_model: bool,
    binding: Any,
    caller: Any,
) -> JsonCandidate | None:
    gateway = state.gateway
    estimated_cost = gateway._estimated_cost_usd(
        provider,
        prompt=state.prompt,
        max_output_tokens=state.max_output_tokens,
        binding=binding,
    )
    try:
        provider_allowed, budget_checks = gateway._budget_allows_provider(
            provider,
            cost_scope=state.cost_scope,
            estimated_cost_usd=estimated_cost,
            require_configured=state.require_configured_budget,
        )
    except Exception as exc:
        state.errors.append(
            {
                "provider": provider,
                "model": model_id,
                "status": "budget_check_failed",
                "error": f"{type(exc).__name__}: {str(exc)[:260]}",
            }
        )
        return None
    if provider_allowed:
        return JsonCandidate(
            index=index,
            provider=provider,
            model_id=model_id,
            explicit_model=explicit_model,
            binding=binding,
            caller=caller,
            estimated_cost_usd=estimated_cost,
            budget_checks=budget_checks,
        )
    state.budget_warnings.append(
        {
            "stage": "provider_preflight",
            "provider": provider,
            "model": model_id,
            "reason": "budget_blocked",
            "estimated_cost_usd": estimated_cost,
            "budget_checks": budget_checks,
        }
    )
    gateway._record_budget_blocked_attempt(
        provider,
        binding=binding,
        purpose=state.purpose,
        prompt=state.prompt,
        cost_scope=state.cost_scope,
        estimated_cost_usd=estimated_cost,
        budget_checks=budget_checks,
        triggered_by=state.triggered_by,
        metadata=state.metadata,
        staff=state.staff,
    )
    state.errors.append(
        {
            "provider": provider,
            "model": model_id,
            "status": "budget_blocked",
            "error": "budget_blocked",
            "blocked_scopes": _budget_blocked_scopes(budget_checks),
        }
    )
    return None


def preflight_candidate(
    state: Any,
    index: int,
    raw_candidate: tuple[str, str, bool],
) -> JsonCandidate | CandidateDecision:
    gateway = state.gateway
    provider, model_id, explicit_model = raw_candidate
    if gateway.time.monotonic() >= state.deadline_at:
        state.errors.append(
            {
                "provider": provider or "unknown",
                "model": model_id,
                "status": "deadline_exceeded",
                "error": "gateway deadline exceeded",
            }
        )
        state.deadline_hit = True
        return BREAK
    binding = gateway._resolve_gateway_binding(provider, model_id)
    blocker = gateway._binding_call_blocker(
        binding,
        explicit_model=explicit_model,
        require_runtime_verified=state.require_runtime_verified,
    )
    if blocker:
        state.errors.append(
            {
                "provider": provider or "unknown",
                "model": model_id,
                "binding": binding.binding,
                "status": "model_binding_blocked",
                "error": blocker,
            }
        )
        return CONTINUE
    if not gateway._is_provider_configured(provider):
        state.errors.append(
            {"provider": provider, "model": model_id, "status": "not_configured"}
        )
        return CONTINUE
    caller = gateway._PROVIDER_CALLERS.get(provider)
    if caller is None:
        state.errors.append(
            {"provider": provider, "model": model_id, "status": "not_implemented"}
        )
        return CONTINUE
    candidate = _budget_candidate(
        state,
        index=index,
        provider=provider,
        model_id=model_id,
        explicit_model=explicit_model,
        binding=binding,
        caller=caller,
    )
    if candidate is None:
        return CONTINUE
    if gateway.time.monotonic() >= state.deadline_at:
        state.errors.append(
            {
                "provider": provider,
                "status": "deadline_exceeded",
                "error": "gateway deadline exceeded",
            }
        )
        state.deadline_hit = True
        return BREAK
    if (
        state.attempt_limit is not None
        and state.provider_attempts >= state.attempt_limit
    ):
        state.errors.append(
            {
                "provider": "gateway",
                "status": "provider_attempt_limit",
                "error": f"provider attempt limit reached ({state.attempt_limit})",
            }
        )
        return BREAK
    return candidate


def _release_failed_start(
    state: Any,
    candidate: JsonCandidate,
) -> None:
    gateway = state.gateway
    if candidate.breaker_permit is not None:
        try:
            gateway._abandon_strict_fleet_breaker(candidate.breaker_permit)
        except Exception:
            gateway.logger.error(
                "vkpi.llm_gateway.json_fleet_breaker_abandon_failed",
                extra={
                    "provider": candidate.provider,
                    "model": candidate.binding.model_id,
                },
                exc_info=True,
            )
    if candidate.reservation_key:
        try:
            _attempt_limits.release_unstarted(state, candidate)
        except Exception:
            gateway.logger.error(
                "vkpi.llm_gateway.json_reservation_release_failed",
                extra={"reservation_key": candidate.reservation_key},
                exc_info=True,
            )


def _record_start_block(
    state: Any,
    candidate: JsonCandidate,
    exc: Exception,
) -> None:
    gateway = state.gateway
    reason = str(getattr(exc, "reason", "") or type(exc).__name__)
    blocked_scope = str(getattr(exc, "scope", "") or "")
    failure_reason = str(getattr(exc, "reason", "") or "")
    blocked_status = (
        failure_reason
        if failure_reason.startswith("fleet_breaker_")
        else "budget_blocked"
    )
    gateway.record_call(
        provider=candidate.provider,
        model=candidate.binding.model_id,
        purpose=state.purpose,
        prompt=state.prompt,
        status=blocked_status,
        fallback_used=True,
        cost_tag=state.cost_scope or gateway.SINGLE_CALL_BUDGET_SCOPE,
        triggered_by=state.triggered_by,
        metadata={
            **(state.metadata or {}),
            "json_contract": True,
            "reservation_key": candidate.reservation_key,
            "reservation_reason": reason,
            "reservation_scope": blocked_scope,
            "fleet_breaker_blocked": blocked_status.startswith("fleet_breaker_"),
            "estimated_cost_usd": candidate.estimated_cost_usd,
            "resolved_model_binding": candidate.binding.to_dict(),
            "request_content_recorded": False,
        },
        staff=state.staff,
        update_budget_scopes=not state.enforce_atomic_reservation,
        force_cost_ledger=state.enforce_atomic_reservation,
    )
    state.errors.append(
        {
            "provider": candidate.provider,
            "model": candidate.model_id,
            "status": blocked_status,
            "error": reason,
            "scope": blocked_scope,
        }
    )


def _start_provider_attempt(
    state: Any,
    candidate: JsonCandidate,
) -> bool:
    gateway = state.gateway
    try:
        _attempt_limits.checkpoint(state)
        if state.enforce_atomic_reservation:
            reservation = gateway._llm_budget_reservations().reserve_llm_budget(
                provider=candidate.provider,
                model=candidate.binding.model_id,
                purpose=state.purpose,
                prompt=state.prompt,
                estimated_cost_usd=candidate.estimated_cost_usd,
                cost_scope=state.cost_scope,
                require_cost_scope=bool(state.require_configured_budget),
                metadata=state.metadata,
                staff=state.staff,
                triggered_by=state.triggered_by,
            )
            candidate.reservation_key = str(reservation.reservation_key or "")
        _attempt_limits.checkpoint(state)
        candidate.breaker_permit = gateway._acquire_strict_fleet_breaker(
            provider=candidate.provider,
            model=candidate.binding.model_id,
            enforce_atomic_reservation=state.enforce_atomic_reservation,
        )
        _attempt_limits.checkpoint(state)
        if state.enforce_atomic_reservation:
            gateway._llm_budget_reservations().mark_llm_provider_started(
                candidate.reservation_key
            )
            candidate.provider_marked_started = True
        _attempt_limits.checkpoint(state)
        return True
    except _limits.GatewayDeadlineExceeded:
        _release_failed_start(state, candidate)
        state.deadline_hit = True
        state.errors.append({"provider": "gateway", "status": "deadline_exceeded"})
        return False
    except Exception as exc:
        _release_failed_start(state, candidate)
        _record_start_block(state, candidate, exc)
        return False


def _call_provider(
    state: Any,
    candidate: JsonCandidate,
) -> dict[str, Any]:
    gateway = state.gateway
    try:
        with _limits.provider_deadline(state.deadline_at, gateway.time.monotonic):
            state.provider_attempts += 1
            kwargs = {"model_override": candidate.binding.model_id} if candidate.explicit_model else {}
            raw_result = candidate.caller(state.prompt, state.max_output_tokens, **kwargs)
        if isinstance(raw_result, dict):
            return dict(raw_result)
        return {
            "status": "failed",
            "provider": candidate.provider,
            "error": "provider returned a non-object result",
        }
    except _limits.GatewayDeadlineExceeded:
        return {"status": "deadline_exceeded", "provider_io_started": False}
    except Exception as exc:
        return {
            "status": "provider_exception",
            "provider": candidate.provider,
            "error": (
                type(exc).__name__
                if candidate.reservation_key
                else f"{type(exc).__name__}: {str(exc)[:300]}"
            ),
        }


def _complete_breaker(
    state: Any,
    candidate: JsonCandidate,
    result: dict[str, Any],
) -> dict[str, Any] | None:
    gateway = state.gateway
    try:
        if result.get("provider_io_started") is False:
            gateway._abandon_strict_fleet_breaker(candidate.breaker_permit)
            return None
        gateway._complete_strict_fleet_breaker(candidate.breaker_permit, result)
        return None
    except Exception as exc:
        if candidate.reservation_key:
            gateway._mark_reserved_attempt_unknown(candidate.reservation_key)
        gateway.logger.error(
            "vkpi.llm_gateway.json_fleet_breaker_completion_failed",
            extra={
                "provider": candidate.provider,
                "model": candidate.binding.model_id,
                "breaker_error_type": type(exc).__name__,
            },
        )
        state.errors.append(
            {
                "provider": candidate.provider,
                "model": candidate.model_id,
                "status": "fleet_breaker_store_unavailable_after_provider",
                "error": type(exc).__name__,
            }
        )
        return _fallback_result(
            state,
            reason="fleet_breaker_store_unavailable_after_provider",
            reservation_key=candidate.reservation_key or None,
        )


def _evaluate_result(
    state: Any,
    candidate: JsonCandidate,
    result: dict[str, Any],
) -> JsonAttemptEvaluation:
    gateway = state.gateway
    completed_after_deadline = gateway.time.monotonic() >= state.deadline_at
    provider_status = str(result.get("status") or "failed")
    response_text = str(result.get("text") or "")
    attempt_status = provider_status
    attempt_error = str(result.get("error") or "")[:300]
    parsed_value: Any = None
    input_tokens = gateway._safe_int(result.get("input_tokens"))
    output_tokens = gateway._safe_int(result.get("output_tokens"))
    result_micro_raw = result.get("cost_micro_usd")
    result_micro_for_settlement = (
        gateway._safe_int(result_micro_raw)
        if result_micro_raw is not None
        else gateway._estimate_cost_micro_usd(
            candidate.provider,
            input_tokens,
            output_tokens,
            binding=candidate.binding,
        )
    )
    if provider_status == "success":
        actual_model = str(result.get("model") or "").strip()
        if candidate.explicit_model and not candidate.binding.matches_response_model(
            actual_model
        ):
            attempt_status = "model_mismatch"
            attempt_error = "provider response model did not match exact request"
        elif not response_text.strip():
            attempt_status = "empty_response"
            attempt_error = "provider returned an empty response"
        else:
            try:
                parsed_value = gateway._extract_json_value(response_text)
            except ValueError as exc:
                attempt_status = "parse_failure"
                attempt_error = str(exc)[:300]
            else:
                validation_error = gateway._validate_json_contract(
                    parsed_value,
                    required_keys=state.required_keys,
                    validator=state.validator,
                )
                if validation_error:
                    attempt_status = "validation_failure"
                    attempt_error = validation_error
                elif completed_after_deadline:
                    attempt_status = "deadline_exceeded"
                    attempt_error = "provider completed after gateway deadline"
                else:
                    attempt_status = "success"
                    attempt_error = ""
    # A caller-supplied validator is inside the same cooperative allowance.
    completed_after_deadline = gateway.time.monotonic() >= state.deadline_at
    if completed_after_deadline and attempt_status == "success":
        attempt_status = "deadline_exceeded"
        attempt_error = "JSON validation completed after gateway deadline"
    unknown = _attempt_limits.outcome_unknown(state, candidate, result, attempt_status)
    if unknown and attempt_status == "success":
        attempt_status = "provider_outcome_unknown"
        attempt_error = "successful response has no reliable usage"
    return JsonAttemptEvaluation(
        result=result,
        provider_status=provider_status,
        attempt_status=attempt_status,
        attempt_error=attempt_error,
        parsed_value=parsed_value,
        completed_after_deadline=completed_after_deadline,
        result_micro_for_settlement=result_micro_for_settlement,
        outcome_unknown=unknown,
    )


def _audit_attempt(
    state: Any,
    candidate: JsonCandidate,
    evaluation: JsonAttemptEvaluation,
) -> tuple[int, dict[str, Any], dict[str, Any] | None]:
    gateway = state.gateway
    audit: dict[str, Any] = {}
    try:
        result_micro = gateway._record_json_provider_attempt(
            candidate.provider,
            evaluation.result,
            binding=candidate.binding,
            explicit_model=candidate.explicit_model,
            candidate_index=candidate.index,
            status=evaluation.attempt_status,
            purpose=state.purpose,
            prompt=state.prompt,
            cost_scope=state.cost_scope,
            triggered_by=state.triggered_by,
            metadata={**(state.metadata or {}), "provider_outcome_unknown": evaluation.outcome_unknown},
            staff=state.staff,
            attempt_errors=list(state.errors),
            budget_checks=candidate.budget_checks,
            budget_warnings=state.budget_warnings,
            estimated_cost_usd=candidate.estimated_cost_usd,
            deadline_seconds=state.deadline_seconds,
            reservation_key=candidate.reservation_key,
            error=evaluation.attempt_error,
            audit_sink=audit,
        )
        return result_micro, audit, None
    except Exception as exc:
        if not candidate.reservation_key:
            raise
        gateway._mark_reserved_attempt_unknown(candidate.reservation_key)
        gateway.logger.error(
            "vkpi.llm_gateway.json_provider_audit_failed",
            extra={
                "provider": candidate.provider,
                "purpose": state.purpose,
                "reservation_key": candidate.reservation_key,
                "audit_error_type": type(exc).__name__,
            },
        )
        if evaluation.attempt_status != "success":
            state.errors.append(
                {
                    "provider": candidate.provider,
                    "model": candidate.model_id,
                    "status": evaluation.attempt_status,
                    "error": evaluation.attempt_error,
                }
            )
        state.errors.append(
            {
                "provider": candidate.provider,
                "model": candidate.model_id,
                "status": "audit_ledger_unavailable",
                "error": type(exc).__name__,
            }
        )
        return (
            0,
            audit,
            _fallback_result(
                state,
                reason="audit_ledger_unavailable",
                reservation_key=candidate.reservation_key,
            ),
        )


def _settle_attempt(
    state: Any,
    candidate: JsonCandidate,
    evaluation: JsonAttemptEvaluation,
) -> dict[str, Any] | None:
    if not candidate.reservation_key:
        return None
    gateway = state.gateway
    try:
        settlement = gateway._llm_budget_reservations().settle_llm_reservation(
            candidate.reservation_key,
            float(evaluation.result_micro_for_settlement) / 1_000_000,
        )
        if not bool(settlement.get("settled")):
            raise RuntimeError(
                str(settlement.get("reason") or "reservation_not_settled")
            )
        return None
    except Exception as exc:
        gateway._mark_reserved_attempt_unknown(candidate.reservation_key)
        gateway.logger.error(
            "vkpi.llm_gateway.json_reservation_settlement_failed",
            extra={
                "provider": candidate.provider,
                "purpose": state.purpose,
                "reservation_key": candidate.reservation_key,
                "settlement_error_type": type(exc).__name__,
            },
        )
        state.errors.append(
            {
                "provider": candidate.provider,
                "model": candidate.model_id,
                "status": "reservation_settlement_failed",
                "error": "reservation_settlement_failed",
                "error_type": type(exc).__name__,
            }
        )
        return _fallback_result(
            state,
            reason="reservation_settlement_failed",
            reservation_key=candidate.reservation_key,
        )


def _success_result(
    state: Any,
    candidate: JsonCandidate,
    evaluation: JsonAttemptEvaluation,
    result_micro: int,
    audit: dict[str, Any],
) -> dict[str, Any]:
    result = evaluation.result
    result.update(
        {
            "json": evaluation.parsed_value,
            "fallback_used": bool(state.errors),
            "purpose": state.purpose,
            "cost_micro_usd": result_micro,
            "errors": [
                state.gateway._normalise_runtime_error(item)
                for item in state.errors
            ],
            "deadline_seconds": state.deadline_seconds,
            "elapsed_ms": _elapsed_ms(state),
            "provider_attempts": state.provider_attempts,
            "max_provider_attempts": state.attempt_limit,
            "resolved_model_binding": candidate.binding.to_dict(),
            "budget_reservation_key": candidate.reservation_key or None,
        }
    )
    state.store_cached_result(state.cache_plan, result, audit)
    return result


def run_candidate(
    state: Any,
    candidate: JsonCandidate,
) -> CandidateDecision:
    if not _start_provider_attempt(state, candidate):
        return CONTINUE
    result = _call_provider(state, candidate)
    breaker_fallback = _complete_breaker(state, candidate, result)
    if breaker_fallback is not None:
        return CandidateDecision("return", breaker_fallback)
    evaluation = _evaluate_result(state, candidate, result)
    result_micro, audit, audit_fallback = _audit_attempt(
        state, candidate, evaluation
    )
    if audit_fallback is not None:
        return CandidateDecision("return", audit_fallback)
    if evaluation.outcome_unknown:
        if candidate.reservation_key:
            state.gateway._mark_reserved_attempt_unknown(candidate.reservation_key)
        state.errors.append({"provider": candidate.provider,
                             "status": evaluation.attempt_status,
                             "error": evaluation.attempt_error})
        return CandidateDecision("return", _fallback_result(
            state, reason="provider_outcome_unknown",
            reservation_key=candidate.reservation_key or None,
        ))
    settlement_fallback = _settle_attempt(state, candidate, evaluation)
    if settlement_fallback is not None:
        return CandidateDecision("return", settlement_fallback)
    # Usage must still be audited and settled if post-processing is slow, but
    # late output must not be delivered or added to the response cache.
    if state.gateway.time.monotonic() >= state.deadline_at:
        state.deadline_hit = True
        state.errors.append({"provider": "gateway", "status": "deadline_exceeded"})
        return BREAK
    if evaluation.attempt_status == "success":
        return CandidateDecision(
            "return",
            _success_result(state, candidate, evaluation, result_micro, audit),
        )
    state.errors.append(
        {
            "provider": candidate.provider,
            "status": evaluation.attempt_status,
            "error": evaluation.attempt_error,
        }
    )
    if evaluation.completed_after_deadline:
        if evaluation.attempt_status != "deadline_exceeded":
            state.errors.append(
                {
                    "provider": "gateway",
                    "status": "deadline_exceeded",
                    "error": "gateway deadline exceeded",
                }
            )
        state.deadline_hit = True
        return BREAK
    return CONTINUE
