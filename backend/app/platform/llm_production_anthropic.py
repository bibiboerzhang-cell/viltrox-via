"""Anthropic image/text strict adapter (split from llm_production, 2026-08-23).

Text generation uses the canonical gateway. Anthropic image+text calls have a
narrow SDK adapter because the text gateway cannot preserve multimodal message
blocks; that adapter applies the same exact-model readiness, atomic reservation,
progress and response-verification contract without changing caller payloads.

调用方 / 测试只认门面 ``app.platform.llm_production``(monkeypatch 也打在门面上);
本模块不得被业务代码直接 import。任务绑定链通过
:func:`llm_production_common.allowed_task_bindings`(主绑定仍经
expected_task_binding → 门面)解析,保证
``monkeypatch.setattr(llm_production, "current_task_model_binding", ...)`` 仍然生效。
"""
from __future__ import annotations

import time
from typing import Any

from app.platform import llm_gateway
from app.platform.llm_production_common import (
    assert_chain_bound_binding as _assert_chain_bound_binding,
    progress_metadata as _progress_metadata,
    sdk_failure as _sdk_failure,
)
from app.platform.llm_production_anthropic_helpers import (
    anthropic_checked_response as _anthropic_checked_response,
    anthropic_create_kwargs as _anthropic_create_kwargs,
    anthropic_input_token_estimate as _anthropic_input_token_estimate,
    anthropic_messages_fingerprint as _anthropic_messages_fingerprint,
    anthropic_preflight_candidate as _anthropic_preflight_candidate,
    anthropic_provider_gate_reason as _anthropic_provider_gate_reason,
)


def generate_anthropic_messages(
    *,
    client: Any,
    messages: list[dict[str, Any]],
    model: str,
    purpose: str,
    max_output_tokens: int,
    cost_tag: str | None = None,
    triggered_by: Any = None,
    staff: dict[str, Any] | None = None,
    metadata: dict[str, Any] | None = None,
) -> Any:
    """Execute one exact Anthropic image/text SDK attempt under strict gates.

    The caller-provided ``messages`` object is passed to ``messages.create``
    unchanged. This boundary owns only authorization, conservative media-cost
    reservation, progress, response-model/usage verification and settlement;
    callers retain their existing retry and JSON parsing semantics.
    """

    provider = "anthropic"
    exact_model = str(model or "").strip()
    exact_purpose = str(purpose or "").strip()
    if not exact_model or not isinstance(messages, list) or not messages:
        raise ValueError("model and non-empty messages are required")
    progress_metadata = _progress_metadata(
        exact_purpose,
        metadata,
        phase="multimodal_generation",
    )
    task_binding = str(progress_metadata.get("task_binding") or "").strip()
    actual_binding = f"{provider}/{exact_model}"
    # 2026-08-30:绑定校验认整条链(主 + 回退,链外仍 mismatch),语义与
    # llm_production_google_stages.validate_google_task_binding 对齐。
    _assert_chain_bound_binding(
        task_binding,
        actual_binding,
        provider=provider,
        model=exact_model,
        purpose=exact_purpose,
    )

    request_identity = _anthropic_messages_fingerprint(messages)
    preflight = llm_gateway.budget_preflight(
        request_identity,
        purpose=exact_purpose,
        max_output_tokens=max_output_tokens,
        preferred_provider=provider,
        model_override=exact_model,
        model_fallbacks=(),
        require_runtime_verified=True,
        cost_tag=cost_tag,
        require_configured=False,
    )
    candidate = _anthropic_preflight_candidate(preflight, actual_binding)
    if not bool(candidate.get("provider_calls_allowed")):
        reason = _anthropic_provider_gate_reason(candidate, preflight)
        llm_gateway.record_call(
            provider=provider,
            model=exact_model,
            purpose=exact_purpose,
            prompt=request_identity,
            status="provider_blocked",
            fallback_used=True,
            metadata={
                **progress_metadata,
                "entrypoint": "llm_production_anthropic_messages_v1",
                "request_content_recorded": False,
                "provider_gate_reason": reason,
            },
            triggered_by=triggered_by,
            staff=staff,
            update_budget_scopes=False,
        )
        raise _sdk_failure(
            reason,
            provider=provider,
            model=exact_model,
            purpose=exact_purpose,
        )

    binding = llm_gateway._resolve_gateway_binding(provider, exact_model)
    input_estimate = _anthropic_input_token_estimate(messages, model=exact_model)
    output_limit = max(1, int(max_output_tokens or 800))
    estimated_micro = llm_gateway._estimate_cost_micro_usd(
        provider,
        input_estimate,
        output_limit,
        binding=binding,
    )
    estimated_cost = float(estimated_micro) / 1_000_000
    cost_scope = llm_gateway._cost_scope_for_purpose(exact_purpose, cost_tag)
    reservation_key = ""
    breaker_session = None
    try:
        reservation = llm_gateway._llm_budget_reservations().reserve_llm_budget(
            provider=provider,
            model=exact_model,
            purpose=exact_purpose,
            prompt=request_identity,
            estimated_cost_usd=estimated_cost,
            cost_scope=cost_scope,
            metadata={
                **progress_metadata,
                "entrypoint": "llm_production_anthropic_messages_v1",
                "request_content_recorded": False,
                "estimated_input_tokens": input_estimate,
            },
            staff=staff,
            triggered_by=triggered_by,
        )
        reservation_key = str(reservation.reservation_key or "")
        breaker_session = llm_gateway._acquire_strict_fleet_breaker(
            provider=provider,
            model=exact_model,
            enforce_atomic_reservation=True,
        )
        llm_gateway._llm_budget_reservations().mark_llm_provider_started(
            reservation_key
        )
    except Exception as exc:
        if breaker_session is not None:
            try:
                llm_gateway._abandon_strict_fleet_breaker(breaker_session)
            except Exception:
                llm_gateway.logger.error(
                    "vkpi.llm_production.breaker_abandon_failed_before_provider",
                    extra={"provider": provider, "model": exact_model},
                    exc_info=True,
                )
        if reservation_key:
            try:
                llm_gateway._llm_budget_reservations().release_llm_reservation(
                    reservation_key
                )
            except Exception:
                llm_gateway.logger.error(
                    "vkpi.llm_production.reservation_release_failed_before_provider",
                    extra={
                        "provider": provider,
                        "model": exact_model,
                        "purpose": exact_purpose,
                        "reservation_key": reservation_key,
                    },
                    exc_info=True,
                )
        reason = str(getattr(exc, "reason", "") or type(exc).__name__)
        llm_gateway.record_call(
            provider=provider,
            model=exact_model,
            purpose=exact_purpose,
            prompt=request_identity,
            status="budget_blocked",
            fallback_used=True,
            metadata={
                **progress_metadata,
                "entrypoint": "llm_production_anthropic_messages_v1",
                "request_content_recorded": False,
                "reservation_reason": reason,
                "estimated_cost_usd": estimated_cost,
            },
            triggered_by=triggered_by,
            staff=staff,
            update_budget_scopes=False,
        )
        raise _sdk_failure(
            reason,
            provider=provider,
            model=exact_model,
            purpose=exact_purpose,
        ) from exc

    started = time.monotonic()
    try:
        response = _anthropic_checked_response(client.messages.create(
            **_anthropic_create_kwargs(exact_model, max_output_tokens, messages)
        ))
    except Exception as provider_exc:
        breaker_completion_error: Exception | None = None
        try:
            llm_gateway._complete_strict_fleet_breaker(
                breaker_session, provider_exc
            )
        except Exception as exc:  # shared health state is a hard boundary
            breaker_completion_error = exc
        try:
            llm_gateway.record_call(
                provider=provider,
                model=exact_model,
                purpose=exact_purpose,
                prompt=request_identity,
                status="provider_exception",
                fallback_used=False,
                metadata={
                    **progress_metadata,
                    "entrypoint": "llm_production_anthropic_messages_v1",
                    "request_content_recorded": False,
                    "reservation_key": reservation_key,
                    "reservation_estimated_cost_usd": estimated_cost,
                    "latency_ms": max(
                        0, int((time.monotonic() - started) * 1000)
                    ),
                },
                triggered_by=triggered_by,
                staff=staff,
                update_budget_scopes=False,
            )
        except Exception:
            llm_gateway.logger.error(
                "vkpi.llm_production.multimodal_exception_audit_failed",
                extra={
                    "provider": provider,
                    "model": exact_model,
                    "purpose": exact_purpose,
                    "reservation_key": reservation_key,
                },
                exc_info=True,
            )
        finally:
            llm_gateway._mark_reserved_attempt_unknown(reservation_key)
        if breaker_completion_error is not None:
            raise _sdk_failure(
                "fleet_breaker_store_unavailable_after_provider",
                provider=provider,
                model=exact_model,
                purpose=exact_purpose,
            ) from breaker_completion_error
        raise

    usage = getattr(response, "usage", None)
    response_model = str(getattr(response, "model", "") or "").strip()
    try:
        input_tokens = int(getattr(usage, "input_tokens", 0) or 0)
        output_tokens = int(getattr(usage, "output_tokens", 0) or 0)
    except (TypeError, ValueError):
        input_tokens = 0
        output_tokens = 0
    status = "success"
    if not binding.matches_response_model(response_model):
        status = "model_mismatch"
    elif input_tokens <= 0 or output_tokens <= 0:
        status = "usage_missing"
    try:
        llm_gateway._complete_strict_fleet_breaker(
            breaker_session,
            {"status": status},
        )
    except Exception as exc:
        llm_gateway._mark_reserved_attempt_unknown(reservation_key)
        raise _sdk_failure(
            "fleet_breaker_store_unavailable_after_provider",
            provider=provider,
            model=exact_model,
            purpose=exact_purpose,
        ) from exc
    actual_micro = (
        llm_gateway._estimate_cost_micro_usd(
            provider,
            input_tokens,
            output_tokens,
            binding=binding,
        )
        if status == "success"
        else 0
    )
    try:
        llm_gateway.record_call(
            provider=provider,
            model=exact_model,
            purpose=exact_purpose,
            prompt=request_identity,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cost_micro_usd=actual_micro,
            status=status,
            fallback_used=False,
            metadata={
                **progress_metadata,
                "entrypoint": "llm_production_anthropic_messages_v1",
                "request_content_recorded": False,
                "reservation_key": reservation_key,
                "reservation_estimated_cost_usd": estimated_cost,
                "latency_ms": max(0, int((time.monotonic() - started) * 1000)),
                "response_model": response_model,
            },
            triggered_by=triggered_by,
            staff=staff,
            update_budget_scopes=False,
        )
    except Exception:
        llm_gateway._mark_reserved_attempt_unknown(reservation_key)
        raise
    if status != "success":
        llm_gateway._mark_reserved_attempt_unknown(reservation_key)
        raise _sdk_failure(
            status,
            provider=provider,
            model=exact_model,
            purpose=exact_purpose,
        )
    try:
        settlement = llm_gateway._llm_budget_reservations().settle_llm_reservation(
            reservation_key,
            float(actual_micro) / 1_000_000,
        )
        if not bool(settlement.get("settled")):
            raise RuntimeError(str(settlement.get("reason") or "not_settled"))
    except Exception:
        llm_gateway._mark_reserved_attempt_unknown(reservation_key)
        raise
    return response


__all__ = ["generate_anthropic_messages"]
