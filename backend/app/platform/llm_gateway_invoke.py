"""Implementation of the public LLM gateway invoke facade.

The public symbol stays in :mod:`app.platform.llm_gateway`; this module only
owns the large orchestration body. Dependencies are supplied from the facade's
live namespace so existing monkeypatch and operator override contracts remain
intact.
"""
from __future__ import annotations

from typing import Any, Iterable

from app.platform import llm_gateway_result_cache as _result_cache
from app.platform.llm_gateway_call_hooks import (
    cache_model_label,
    deferred_or_none,
    serve_cached_result,
    store_cached_result,
)


def invoke_impl(
    prompt: str,
    *,
    purpose: str = "",
    max_output_tokens: int = 800,
    preferred_provider: str | None = None,
    model_override: str | None = None,
    model_fallbacks: Iterable[tuple[str, str]] | None = None,
    require_runtime_verified: bool = True,
    skip_budget_check: bool = False,
    require_configured_budget: bool = False,
    cost_tag: str | None = None,
    triggered_by: Any = None,
    metadata: dict[str, Any] | None = None,
    staff: dict[str, Any] | None = None,
    enforce_atomic_reservation: bool = False,
    namespace: dict[str, Any],
) -> dict[str, Any]:
    """Invoke an LLM with safe fallback and ledger recording.

    Preliminary global/scope checks are recorded as telemetry; immediately
    before each billable provider attempt, the provider and scoped budget CAS
    is a hard stop.  A blocked attempt records zero cost and never sends HTTP.

    ``model_override`` is intentionally narrow: exact models must be registered,
    transport-ready, priced, and runtime-verified. ``model_fallbacks`` is an
    authoritative provider/model chain and may contain multiple models for the
    same provider.  Without it, an exact request degrades directly to rule_v0;
    it never falls into unrelated global defaults. Existing callers that omit
    both arguments retain the provider-default order and two-argument adapter
    contract, but those defaults must now also have runtime-verification evidence.

    ``require_runtime_verified`` remains accepted for API compatibility. Runtime
    verification is a hard production gate, so passing False does not disable it.

    ``enforce_atomic_reservation`` atomically reserves the monthly/provider
    allowance before network I/O. The public gateway forces this flag in
    production; focused tests and local evaluation remain explicit opt-ins.
    After the provider returns, the resolved response contract is committed to
    the call ledger before the reservation is settled.
    """
    # Resolve every dependency from the facade namespace at call time. This is
    # deliberate: callers and tests historically monkeypatch llm_gateway
    # symbols, including provider callers, budget guards, readiness gates, and
    # ledger writers. Capturing imports here would silently break that contract.
    _rule_fallback = namespace["_rule_fallback"]
    record_call = namespace["record_call"]
    _cost_scope_for_purpose = namespace["_cost_scope_for_purpose"]
    _budget_guard = namespace["_budget_guard"]
    logger = namespace["logger"]
    _monthly_budget_cents = namespace["_monthly_budget_cents"]
    _budget_remaining_cents = namespace["_budget_remaining_cents"]
    _ordered_model_candidates = namespace["_ordered_model_candidates"]
    _resolve_gateway_binding = namespace["_resolve_gateway_binding"]
    _binding_call_blocker = namespace["_binding_call_blocker"]
    _is_provider_configured = namespace["_is_provider_configured"]
    _PROVIDER_CALLERS = namespace["_PROVIDER_CALLERS"]
    _estimated_cost_usd = namespace["_estimated_cost_usd"]
    _budget_allows_provider = namespace["_budget_allows_provider"]
    _record_budget_blocked_attempt = namespace["_record_budget_blocked_attempt"]
    _llm_budget_reservations = namespace["_llm_budget_reservations"]
    _acquire_strict_fleet_breaker = namespace["_acquire_strict_fleet_breaker"]
    _complete_strict_fleet_breaker = namespace["_complete_strict_fleet_breaker"]
    _abandon_strict_fleet_breaker = namespace["_abandon_strict_fleet_breaker"]
    SINGLE_CALL_BUDGET_SCOPE = namespace["SINGLE_CALL_BUDGET_SCOPE"]
    _mark_reserved_attempt_unknown = namespace["_mark_reserved_attempt_unknown"]
    _record_reserved_provider_attempt = namespace["_record_reserved_provider_attempt"]
    _safe_int = namespace["_safe_int"]
    _estimate_cost_micro_usd = namespace["_estimate_cost_micro_usd"]
    _micro_usd_to_cents = namespace["_micro_usd_to_cents"]

    # Clamp floor to the strictest provider minimum (openai /v1/responses requires
    # max_output_tokens >= 16). Callers passing <16 previously slipped through the
    # provider adapters' `max(1, ...)` floor and made openai return http_400, which
    # then silently fell back down the chain (or to rule_v0). Floor here so every
    # provider gets a valid budget; ceiling stays with the adapters (min(4000, ...)).
    max_output_tokens = max(16, int(max_output_tokens or 0))
    safe_prompt = str(prompt or "")
    if not safe_prompt.strip():
        result = _rule_fallback(safe_prompt, purpose=purpose, reason="empty_prompt")
        record_call(
            provider="rule_v0",
            model="rule_v0",
            purpose=purpose,
            prompt=safe_prompt,
            status="empty_prompt",
            fallback_used=True,
            cost_tag=cost_tag,
            triggered_by=triggered_by,
            metadata={**(metadata or {}), "reason": result["reason"]},
            staff=staff,
        )
        return result

    cost_scope = _cost_scope_for_purpose(purpose, cost_tag)
    budget_warnings: list[dict[str, Any]] = []
    if cost_scope:
        try:
            if not _budget_guard().check_budget(cost_scope, 0, require_configured=True):
                budget_warnings.append({"stage": "scope_preflight", "reason": "ai_budget_hard_stop", "cost_tag": cost_scope})
                logger.warning(
                    "vkpi.llm_gateway.ai_budget_hard_stop_record_only",
                    extra={"cost_tag": cost_scope, "purpose": purpose},
                )
        except Exception:
            logger.warning("vkpi.llm_gateway.ai_budget_check_failed", exc_info=True)

    if not skip_budget_check:
        monthly_budget = _monthly_budget_cents()
        remaining = _budget_remaining_cents()
        if monthly_budget <= 0 or remaining <= 0:
            reason = "budget_disabled" if monthly_budget <= 0 else "budget_exhausted"
            budget_warnings.append(
                {
                    "stage": "monthly_preflight",
                    "reason": reason,
                    "monthly_budget_cents": monthly_budget,
                    "remaining_cents": remaining,
                }
            )
            logger.warning(
                "vkpi.llm_gateway.monthly_budget_record_only",
                extra={"reason": reason, "purpose": purpose, "remaining_cents": remaining},
            )

    errors: list[dict[str, Any]] = []

    def _reserved_hard_stop(
        *,
        provider: str,
        model: str,
        reservation_key: str,
        reason: str,
        error_type: str = "",
    ) -> dict[str, Any]:
        """Return a bounded failure after provider I/O without another audit write.

        Once provider I/O has happened, an unavailable audit or settlement
        boundary makes the outcome uncertain.  Keep the reservation open for
        reconciliation and stop the candidate chain so a second provider cannot
        create another billable attempt for the same logical request.
        """

        _mark_reserved_attempt_unknown(reservation_key)
        error = {
            "provider": provider,
            "model": model,
            "status": reason,
            "error": reason,
        }
        if error_type:
            error["error_type"] = error_type
        errors.append(error)
        fallback = _rule_fallback(
            safe_prompt,
            purpose=purpose,
            reason=reason,
            errors=errors,
        )
        fallback["budget_reservation_key"] = reservation_key
        return fallback

    def _settle_reserved_attempt(
        *,
        provider: str,
        model: str,
        reservation_key: str,
        cost_micro_usd: int,
    ) -> dict[str, Any] | None:
        if not reservation_key:
            return None
        try:
            settlement = _llm_budget_reservations().settle_llm_reservation(
                reservation_key,
                float(cost_micro_usd) / 1_000_000,
            )
            if not bool(settlement.get("settled")):
                raise RuntimeError(
                    str(settlement.get("reason") or "reservation_not_settled")
                )
        except Exception as exc:  # noqa: BLE001 - cost happened; keep open
            logger.error(
                "vkpi.llm_gateway.reservation_settlement_failed",
                extra={
                    "provider": provider,
                    "purpose": purpose,
                    "reservation_key": reservation_key,
                    "settlement_error_type": type(exc).__name__,
                },
            )
            return _reserved_hard_stop(
                provider=provider,
                model=model,
                reservation_key=reservation_key,
                reason="reservation_settlement_failed",
                error_type=type(exc).__name__,
            )
        return None

    def _fleet_breaker_hard_stop(
        *,
        provider: str,
        model: str,
        reservation_key: str,
        error_type: str,
    ) -> dict[str, Any]:
        reason = "fleet_breaker_store_unavailable_after_provider"
        if reservation_key:
            return _reserved_hard_stop(
                provider=provider,
                model=model,
                reservation_key=reservation_key,
                reason=reason,
                error_type=error_type,
            )
        errors.append(
            {
                "provider": provider,
                "model": model,
                "status": reason,
                "error": error_type,
            }
        )
        return _rule_fallback(
            safe_prompt,
            purpose=purpose,
            reason=reason,
            errors=errors,
        )

    candidates = _ordered_model_candidates(
        preferred_provider,
        model_override,
        model_fallbacks,
    )
    # W-L1 结果缓存:同 purpose + 同规范化提示 + 同模型 + 同 UTC 桶 → 直接回放,零成本零 HTTP。
    cache_plan = _result_cache.build_cache_plan(
        purpose,
        safe_prompt,
        model=cache_model_label(candidates),
        contract="text",
        max_output_tokens=max_output_tokens,
        metadata=metadata,
    )
    cached_hit = serve_cached_result(
        plan=cache_plan,
        purpose=purpose,
        prompt=safe_prompt,
        contract="text",
        record_call=record_call,
        triggered_by=triggered_by,
        metadata=metadata,
        staff=staff,
        cost_scope=cost_scope,
    )
    if cached_hit is not None:
        return cached_hit
    for candidate_index, (provider, model_id, explicit_model) in enumerate(candidates):
        binding = _resolve_gateway_binding(provider, model_id)
        binding_blocker = _binding_call_blocker(
            binding,
            explicit_model=explicit_model,
            require_runtime_verified=require_runtime_verified,
        )
        if binding_blocker:
            errors.append(
                {
                    "provider": provider or "unknown",
                    "model": model_id,
                    "binding": binding.binding,
                    "status": "model_binding_blocked",
                    "error": binding_blocker,
                }
            )
            continue
        if not _is_provider_configured(provider):
            errors.append({"provider": provider, "model": model_id, "status": "not_configured"})
            continue
        caller = _PROVIDER_CALLERS.get(provider)
        if caller is None:
            errors.append({"provider": provider, "model": model_id, "status": "not_implemented"})
            continue
        estimated_cost = _estimated_cost_usd(
            provider,
            prompt=safe_prompt,
            max_output_tokens=max_output_tokens,
            binding=binding,
        )
        try:
            provider_allowed, budget_checks = _budget_allows_provider(
                provider,
                cost_scope=cost_scope,
                estimated_cost_usd=estimated_cost,
                require_configured=require_configured_budget,
            )
        except Exception as exc:  # noqa: BLE001 - fail closed before a billable request
            errors.append(
                {
                    "provider": provider,
                    "model": model_id,
                    "status": "budget_check_failed",
                    "error": f"{type(exc).__name__}: {str(exc)[:260]}",
                }
            )
            continue
        if not provider_allowed:
            budget_warnings.append(
                {
                    "stage": "provider_preflight",
                    "provider": provider,
                    "model": model_id,
                    "reason": "budget_blocked",
                    "estimated_cost_usd": estimated_cost,
                    "budget_checks": budget_checks,
                }
            )
            logger.warning(
                "vkpi.llm_gateway.provider_budget_hard_stop",
                extra={"provider": provider, "purpose": purpose, "estimated_cost_usd": estimated_cost},
            )
            # 护栏① enforce:超预算 provider 不再发请求——记零成本台账后跳过,for 循环续 fallback;
            # 全部 provider 被拦则落 _rule_fallback(rule_v0 不计费),不 raise 不阻断上层。
            _record_budget_blocked_attempt(
                provider,
                binding=binding,
                purpose=purpose,
                prompt=safe_prompt,
                cost_scope=cost_scope,
                estimated_cost_usd=estimated_cost,
                budget_checks=budget_checks,
                triggered_by=triggered_by,
                metadata=metadata,
                staff=staff,
            )
            errors.append(
                {
                    "provider": provider,
                    "model": model_id,
                    "status": "budget_blocked",
                    "error": "budget_blocked",
                }
            )
            continue
        reservation_key = ""
        breaker_permit = None
        try:
            if enforce_atomic_reservation:
                reservation = _llm_budget_reservations().reserve_llm_budget(
                    provider=provider,
                    model=binding.model_id,
                    purpose=purpose,
                    prompt=safe_prompt,
                    estimated_cost_usd=estimated_cost,
                    cost_scope=cost_scope,
                    metadata=metadata,
                    staff=staff,
                    triggered_by=triggered_by,
                )
                reservation_key = str(reservation.reservation_key or "")
            breaker_permit = _acquire_strict_fleet_breaker(
                provider=provider,
                model=binding.model_id,
                enforce_atomic_reservation=enforce_atomic_reservation,
            )
            if enforce_atomic_reservation:
                # This committed state transition is the last operation before
                # provider network I/O.
                _llm_budget_reservations().mark_llm_provider_started(
                    reservation_key
                )
        except Exception as exc:  # noqa: BLE001 - fail closed before network
            if breaker_permit is not None:
                try:
                    _abandon_strict_fleet_breaker(breaker_permit)
                except Exception:
                    logger.error(
                        "vkpi.llm_gateway.fleet_breaker_abandon_failed",
                        extra={"provider": provider, "model": binding.model_id},
                        exc_info=True,
                    )
            if reservation_key:
                try:
                    _llm_budget_reservations().release_llm_reservation(
                        reservation_key
                    )
                except Exception:
                    logger.error(
                        "vkpi.llm_gateway.reservation_release_failed",
                        extra={"reservation_key": reservation_key},
                        exc_info=True,
                    )
            reason = str(getattr(exc, "reason", "") or type(exc).__name__)
            blocked_scope = str(getattr(exc, "scope", "") or "")
            failure_reason = str(getattr(exc, "reason", "") or "")
            blocked_status = (
                failure_reason
                if failure_reason.startswith("fleet_breaker_")
                else "budget_blocked"
            )
            record_call(
                provider=provider,
                model=binding.model_id,
                purpose=purpose,
                prompt=safe_prompt,
                status=blocked_status,
                fallback_used=True,
                cost_tag=cost_scope or SINGLE_CALL_BUDGET_SCOPE,
                triggered_by=triggered_by,
                metadata={
                    **(metadata or {}),
                    "reservation_key": reservation_key,
                    "reservation_reason": reason,
                    "reservation_scope": blocked_scope,
                    "fleet_breaker_blocked": blocked_status.startswith("fleet_breaker_"),
                    "estimated_cost_usd": estimated_cost,
                    "resolved_model_binding": binding.to_dict(),
                    "request_content_recorded": False,
                },
                staff=staff,
                update_budget_scopes=not enforce_atomic_reservation,
                force_cost_ledger=enforce_atomic_reservation,
            )
            errors.append(
                {
                    "provider": provider,
                    "model": model_id,
                    "status": blocked_status,
                    "error": reason,
                    "scope": blocked_scope,
                }
            )
            continue
        try:
            if explicit_model:
                raw_result = caller(
                    safe_prompt,
                    max_output_tokens,
                    model_override=binding.model_id,
                )
            else:
                raw_result = caller(safe_prompt, max_output_tokens)
        except Exception as exc:  # noqa: BLE001 - provider failures must never escape the gateway
            if reservation_key:
                try:
                    _record_reserved_provider_attempt(
                        provider=provider,
                        binding=binding,
                        purpose=purpose,
                        prompt=safe_prompt,
                        cost_scope=cost_scope,
                        status="provider_exception",
                        reservation_key=reservation_key,
                        estimated_cost_usd=estimated_cost,
                        triggered_by=triggered_by,
                        metadata={
                            **(metadata or {}),
                            "provider_error_type": type(exc).__name__,
                        },
                        staff=staff,
                    )
                except Exception as audit_exc:  # noqa: BLE001 - hard stop after I/O
                    logger.error(
                        "vkpi.llm_gateway.provider_audit_failed",
                        extra={
                            "provider": provider,
                            "purpose": purpose,
                            "reservation_key": reservation_key,
                            "audit_error_type": type(audit_exc).__name__,
                        },
                    )
                    return _reserved_hard_stop(
                        provider=provider,
                        model=model_id,
                        reservation_key=reservation_key,
                        reason="audit_ledger_unavailable",
                        error_type=type(audit_exc).__name__,
                    )
                _mark_reserved_attempt_unknown(reservation_key)
            try:
                _complete_strict_fleet_breaker(breaker_permit, exc)
            except Exception as breaker_exc:  # noqa: BLE001 - no second provider after state loss
                logger.error(
                    "vkpi.llm_gateway.fleet_breaker_completion_failed",
                    extra={
                        "provider": provider,
                        "model": binding.model_id,
                        "breaker_error_type": type(breaker_exc).__name__,
                    },
                )
                return _fleet_breaker_hard_stop(
                    provider=provider,
                    model=model_id,
                    reservation_key=reservation_key,
                    error_type=type(breaker_exc).__name__,
                )
            errors.append(
                {
                    "provider": provider,
                    "model": model_id,
                    "status": "provider_exception",
                    "error": (
                        type(exc).__name__
                        if reservation_key
                        else f"{type(exc).__name__}: {str(exc)[:260]}"
                    ),
                }
            )
            continue
        if not isinstance(raw_result, dict):
            if reservation_key:
                try:
                    _record_reserved_provider_attempt(
                        provider=provider,
                        binding=binding,
                        purpose=purpose,
                        prompt=safe_prompt,
                        cost_scope=cost_scope,
                        status="invalid_response",
                        reservation_key=reservation_key,
                        estimated_cost_usd=estimated_cost,
                        triggered_by=triggered_by,
                        metadata=metadata,
                        staff=staff,
                    )
                except Exception as audit_exc:  # noqa: BLE001 - hard stop after I/O
                    logger.error(
                        "vkpi.llm_gateway.provider_audit_failed",
                        extra={
                            "provider": provider,
                            "purpose": purpose,
                            "reservation_key": reservation_key,
                            "audit_error_type": type(audit_exc).__name__,
                        },
                    )
                    return _reserved_hard_stop(
                        provider=provider,
                        model=model_id,
                        reservation_key=reservation_key,
                        reason="audit_ledger_unavailable",
                        error_type=type(audit_exc).__name__,
                    )
                _mark_reserved_attempt_unknown(reservation_key)
            try:
                _complete_strict_fleet_breaker(breaker_permit, "invalid_response")
            except Exception as breaker_exc:  # noqa: BLE001 - no second provider after state loss
                return _fleet_breaker_hard_stop(
                    provider=provider,
                    model=model_id,
                    reservation_key=reservation_key,
                    error_type=type(breaker_exc).__name__,
                )
            errors.append(
                {
                    "provider": provider,
                    "model": model_id,
                    "status": "invalid_response",
                    "error": "provider returned a non-object result",
                }
            )
            continue
        result = raw_result
        status = str(result.get("status") or "")
        try:
            _complete_strict_fleet_breaker(breaker_permit, result)
        except Exception as breaker_exc:  # noqa: BLE001 - strict shared state is mandatory
            logger.error(
                "vkpi.llm_gateway.fleet_breaker_completion_failed",
                extra={
                    "provider": provider,
                    "model": binding.model_id,
                    "breaker_error_type": type(breaker_exc).__name__,
                },
            )
            return _fleet_breaker_hard_stop(
                provider=provider,
                model=model_id,
                reservation_key=reservation_key,
                error_type=type(breaker_exc).__name__,
            )
        result_in_tokens = _safe_int(result.get("input_tokens"))
        result_out_tokens = _safe_int(result.get("output_tokens"))
        actual_model = str(result.get("model") or "").strip()
        result_micro = _estimate_cost_micro_usd(
            provider,
            result_in_tokens,
            result_out_tokens,
            binding=binding,
        )
        result_cents = _micro_usd_to_cents(result_micro)

        if status == "success" and str(result.get("text") or "").strip():
            # Exact routes must prove the provider served the requested model.
            # A mismatched response still incurred cost, so it gets its own
            # ledger row before the next model-level fallback is attempted.
            if explicit_model and not binding.matches_response_model(actual_model):
                mismatch_error = {
                    "provider": provider,
                    "model": actual_model,
                    "requested_model": binding.model_id,
                    "status": "model_mismatch",
                    "error": "provider response model did not match exact request",
                }
                try:
                    record_call(
                        provider=provider,
                        model=actual_model,
                        purpose=purpose,
                        prompt=safe_prompt,
                        input_tokens=result_in_tokens,
                        output_tokens=result_out_tokens,
                        cost_cents=result_cents,
                        cost_micro_usd=result_micro,
                        status="model_mismatch",
                        fallback_used=True,
                        cost_tag=cost_scope,
                        triggered_by=triggered_by,
                        metadata={
                            **(metadata or {}),
                            "requested_model": binding.model_id,
                            "actual_model": actual_model,
                            "resolved_model_binding": binding.to_dict(),
                            "attempt_errors": errors,
                            "reservation_key": reservation_key,
                        },
                        staff=staff,
                        update_budget_scopes=not bool(reservation_key),
                        force_cost_ledger=bool(reservation_key),
                    )
                except Exception as audit_exc:  # noqa: BLE001 - strict calls stop on audit loss
                    if not reservation_key:
                        raise
                    logger.error(
                        "vkpi.llm_gateway.provider_audit_failed",
                        extra={
                            "provider": provider,
                            "purpose": purpose,
                            "reservation_key": reservation_key,
                            "audit_error_type": type(audit_exc).__name__,
                        },
                    )
                    return _reserved_hard_stop(
                        provider=provider,
                        model=model_id,
                        reservation_key=reservation_key,
                        reason="audit_ledger_unavailable",
                        error_type=type(audit_exc).__name__,
                    )
                settlement_failure = _settle_reserved_attempt(
                    provider=provider,
                    model=model_id,
                    reservation_key=reservation_key,
                    cost_micro_usd=result_micro,
                )
                if settlement_failure is not None:
                    return settlement_failure
                errors.append(mismatch_error)
                continue
            try:
                success_audit = record_call(
                    provider=provider,
                    model=actual_model or binding.model_id,
                    purpose=purpose,
                    prompt=safe_prompt,
                    input_tokens=result_in_tokens,
                    output_tokens=result_out_tokens,
                    cost_cents=result_cents,
                    cost_micro_usd=result_micro,
                    status="success",
                    fallback_used=bool(errors),
                    cost_tag=cost_scope,
                    triggered_by=triggered_by,
                    metadata={
                        **(metadata or {}),
                        "requested_model": binding.model_id if explicit_model else "",
                        "actual_model": actual_model or binding.model_id,
                        "resolved_model_binding": binding.to_dict(),
                        "model_fallback_index": candidate_index,
                        "latency_ms": result.get("latency_ms"),
                        "attempt_errors": errors,
                        "budget_checks": budget_checks,
                        "budget_warnings": budget_warnings,
                        "budget_gate": (
                            "atomic_reservation"
                            if reservation_key
                            else "provider_hard_stop"
                        ),
                        "estimated_cost_usd": estimated_cost,
                        "reservation_key": reservation_key,
                    },
                    staff=staff,
                    update_budget_scopes=not bool(reservation_key),
                    force_cost_ledger=bool(reservation_key),
                )
            except Exception as audit_exc:  # noqa: BLE001 - strict calls stop on audit loss
                if not reservation_key:
                    raise
                logger.error(
                    "vkpi.llm_gateway.provider_audit_failed",
                    extra={
                        "provider": provider,
                        "purpose": purpose,
                        "reservation_key": reservation_key,
                        "audit_error_type": type(audit_exc).__name__,
                    },
                )
                return _reserved_hard_stop(
                    provider=provider,
                    model=model_id,
                    reservation_key=reservation_key,
                    reason="audit_ledger_unavailable",
                    error_type=type(audit_exc).__name__,
                )
            settlement_failure = _settle_reserved_attempt(
                provider=provider,
                model=model_id,
                reservation_key=reservation_key,
                cost_micro_usd=result_micro,
            )
            if settlement_failure is not None:
                return settlement_failure
            result["fallback_used"] = bool(errors)
            result["purpose"] = purpose
            # 精度透出:调用方(content_fit 等)据此记账,不再吃 cost_cents 整数归零(小调用恒 $0 的根因)。
            result["cost_micro_usd"] = result_micro
            result["cost_cents"] = result_cents
            result["resolved_model_binding"] = binding.to_dict()
            if reservation_key:
                result["budget_reservation_key"] = reservation_key
            # 只缓存真实 provider 成功(status=success、正文非空);降级/错误永不入缓存。
            store_cached_result(cache_plan, result, success_audit)
            return result

        if reservation_key:
            if status == "success":
                # Empty output is a failed response contract, but provider cost
                # is known.  Audit that exact outcome before settlement.
                try:
                    _record_reserved_provider_attempt(
                        provider=provider,
                        binding=binding,
                        purpose=purpose,
                        prompt=safe_prompt,
                        cost_scope=cost_scope,
                        status="empty_response",
                        reservation_key=reservation_key,
                        estimated_cost_usd=estimated_cost,
                        input_tokens=result_in_tokens,
                        output_tokens=result_out_tokens,
                        cost_micro_usd=result_micro,
                        triggered_by=triggered_by,
                        metadata=metadata,
                        staff=staff,
                    )
                except Exception as audit_exc:  # noqa: BLE001 - hard stop after I/O
                    logger.error(
                        "vkpi.llm_gateway.provider_audit_failed",
                        extra={
                            "provider": provider,
                            "purpose": purpose,
                            "reservation_key": reservation_key,
                            "audit_error_type": type(audit_exc).__name__,
                        },
                    )
                    return _reserved_hard_stop(
                        provider=provider,
                        model=model_id,
                        reservation_key=reservation_key,
                        reason="audit_ledger_unavailable",
                        error_type=type(audit_exc).__name__,
                    )
                settlement_failure = _settle_reserved_attempt(
                    provider=provider,
                    model=model_id,
                    reservation_key=reservation_key,
                    cost_micro_usd=result_micro,
                )
                if settlement_failure is not None:
                    return settlement_failure
            else:
                try:
                    _record_reserved_provider_attempt(
                        provider=provider,
                        binding=binding,
                        purpose=purpose,
                        prompt=safe_prompt,
                        cost_scope=cost_scope,
                        status=status or "provider_failed",
                        reservation_key=reservation_key,
                        estimated_cost_usd=estimated_cost,
                        triggered_by=triggered_by,
                        metadata=metadata,
                        staff=staff,
                    )
                except Exception as audit_exc:  # noqa: BLE001 - hard stop after I/O
                    logger.error(
                        "vkpi.llm_gateway.provider_audit_failed",
                        extra={
                            "provider": provider,
                            "purpose": purpose,
                            "reservation_key": reservation_key,
                            "audit_error_type": type(audit_exc).__name__,
                        },
                    )
                    return _reserved_hard_stop(
                        provider=provider,
                        model=model_id,
                        reservation_key=reservation_key,
                        reason="audit_ledger_unavailable",
                        error_type=type(audit_exc).__name__,
                    )
                _mark_reserved_attempt_unknown(reservation_key)
        errors.append(
            {
                "provider": provider,
                "model": model_id,
                "status": "empty_response" if status == "success" else status or "failed",
                "error": str(result.get("error") or "")[:300],
            }
        )

    # W-L1:推迟型 purpose 被预算/就绪闸整链拦下 → 诚实 deferred,不落 rule_v0 占位。
    deferred = deferred_or_none(
        prompt=safe_prompt,
        purpose=purpose,
        errors=errors,
        normalise_error=namespace["_normalise_runtime_error"],
        record_call=record_call,
        cost_scope=cost_scope,
        triggered_by=triggered_by,
        metadata=metadata,
        staff=staff,
    )
    if deferred is not None:
        return deferred
    fallback = _rule_fallback(safe_prompt, purpose=purpose, reason="all_providers_failed", errors=errors)
    # 其他 purpose 保持降级行为,但台账与结果都必须说清「为什么降级」。
    fallback_reason = str(fallback.get("failure_code") or fallback.get("reason") or "all_providers_failed")
    fallback["fallback_reason"] = fallback_reason
    record_call(
        provider="rule_v0",
        model="rule_v0",
        purpose=purpose,
        prompt=safe_prompt,
        status="all_providers_failed",
        fallback_used=True,
        cost_tag=cost_scope,
        triggered_by=triggered_by,
        metadata={
            **(metadata or {}),
            "errors": errors,
            "fallback_reason": fallback_reason,
            # 如实口径:空的 fallback 链(绑定钉死)= 没有会被尝试的模型级后备胎。
            "model_level_fallback": bool(model_fallbacks),
        },
        staff=staff,
    )
    return fallback
