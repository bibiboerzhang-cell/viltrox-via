"""Strict production entrypoints for reviewed LLM call sites.

Text generation uses the canonical gateway. Anthropic image+text calls have a
narrow SDK adapter because the text gateway cannot preserve multimodal message
blocks; that adapter applies the same exact-model readiness, atomic reservation,
progress and response-verification contract without changing caller payloads.
Other multimodal/provider SDK paths remain visible in the migration inventory.
"""
from __future__ import annotations

import time
from collections.abc import Callable, Iterable
from typing import Any

from app.core.model_registry import current_task_model_binding
from app.platform import llm_gateway
from app.platform.llm_production_common import (
    ProductionLlmUnavailable,
    progress_metadata as _progress_metadata,
    sdk_failure as _sdk_failure,
)
from app.platform.llm_production_anthropic_helpers import (
    anthropic_input_token_estimate as _anthropic_input_token_estimate,
    anthropic_messages_fingerprint as _anthropic_messages_fingerprint,
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
    expected_binding = current_task_model_binding().get(task_binding, "")
    actual_binding = f"{provider}/{exact_model}"
    if not task_binding or expected_binding != actual_binding:
        raise _sdk_failure(
            "task_binding_model_mismatch",
            provider=provider,
            model=exact_model,
            purpose=exact_purpose,
            details={
                "task_binding": task_binding,
                "expected_binding": expected_binding,
                "actual_binding": actual_binding,
            },
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
    candidates = (
        preflight.get("providers")
        if isinstance(preflight.get("providers"), list)
        else []
    )
    candidate = next(
        (
            item
            for item in candidates
            if str(item.get("binding") or "") == actual_binding
        ),
        {},
    )
    if not bool(candidate.get("provider_calls_allowed")):
        reason = str(
            candidate.get("binding_gate_reason")
            or preflight.get("provider_gate_detail")
            or preflight.get("provider_gate_reason")
            or "provider_calls_blocked"
        )
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
        response = client.messages.create(
            model=exact_model,
            max_tokens=max_output_tokens,
            messages=messages,
        )
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


from app.platform.llm_production_google_helpers import (  # noqa: E402
    GOOGLE_GENERATE_INPUT_TOKENS_HARD_CAP,
    GOOGLE_GENERATE_MAX_OUTPUT_TOKENS_HARD_CAP,
    append_google_attempt as _append_google_attempt,
    google_config_with_output_limit as _google_config_with_output_limit,
    google_contents_fingerprint as _google_contents_fingerprint,
    google_usage_cost_micro_usd as _google_usage_cost_micro_usd,
    google_usage_metadata as _google_usage_metadata,
    usage_int as _usage_int,
)


def generate_google_content(
    *,
    client: Any,
    contents: list[Any],
    config: Any,
    model: str,
    purpose: str,
    max_output_tokens: int,
    estimated_input_tokens: int,
    cost_tag: str | None = None,
    triggered_by: Any = None,
    staff: dict[str, Any] | None = None,
    metadata: dict[str, Any] | None = None,
    execution_class: str = llm_gateway.PRODUCTION_EXECUTION_CLASS,
    attempt_log: list[dict[str, Any]] | None = None,
) -> Any:
    """Execute one Gemini multimodal attempt under the strict spend boundary.

    Every call owns an independent reservation.  A confirmed response is
    ledgered and settled exactly once; a provider exception or unverifiable
    usage remains ``unknown`` and therefore continues to consume the reserved
    allowance.  A failure before provider I/O releases the reservation.
    """

    provider = "google"
    exact_model = str(model or "").strip()
    exact_purpose = str(purpose or "").strip()
    if not exact_model or not isinstance(contents, list) or not contents:
        raise ValueError("model and non-empty contents are required")
    try:
        output_limit = min(
            GOOGLE_GENERATE_MAX_OUTPUT_TOKENS_HARD_CAP,
            max(1, int(max_output_tokens)),
        )
        input_estimate = min(
            GOOGLE_GENERATE_INPUT_TOKENS_HARD_CAP,
            max(1, int(estimated_input_tokens)),
        )
    except (TypeError, ValueError) as exc:
        raise ValueError("valid token limits are required") from exc
    provider_config = _google_config_with_output_limit(
        config, output_limit, model=exact_model
    )
    progress_metadata = _progress_metadata(
        exact_purpose,
        metadata,
        phase="video_analysis",
    )
    progress_metadata["execution_class"] = str(
        execution_class or llm_gateway.PRODUCTION_EXECUTION_CLASS
    )
    task_binding = str(progress_metadata.get("task_binding") or "").strip()
    actual_binding = f"{provider}/{exact_model}"
    if task_binding:
        expected_binding = current_task_model_binding().get(task_binding, "")
        if expected_binding != actual_binding:
            raise _sdk_failure(
                "task_binding_model_mismatch",
                provider=provider,
                model=exact_model,
                purpose=exact_purpose,
                details={
                    "task_binding": task_binding,
                    "expected_binding": expected_binding,
                    "actual_binding": actual_binding,
                },
            )

    request_identity = _google_contents_fingerprint(contents)
    preflight = llm_gateway.budget_preflight(
        request_identity,
        purpose=exact_purpose,
        max_output_tokens=output_limit,
        preferred_provider=provider,
        model_override=exact_model,
        model_fallbacks=(),
        require_runtime_verified=True,
        execution_class=progress_metadata["execution_class"],
        cost_tag=cost_tag,
        require_configured=False,
    )
    candidates = (
        preflight.get("providers")
        if isinstance(preflight.get("providers"), list)
        else []
    )
    candidate = next(
        (
            item
            for item in candidates
            if str(item.get("binding") or "") == actual_binding
        ),
        {},
    )
    if not bool(candidate.get("provider_calls_allowed")):
        reason = str(
            candidate.get("binding_gate_reason")
            or preflight.get("provider_gate_detail")
            or preflight.get("provider_gate_reason")
            or "provider_calls_blocked"
        )
        llm_gateway.record_call(
            provider=provider,
            model=exact_model,
            purpose=exact_purpose,
            prompt=request_identity,
            status="provider_blocked",
            fallback_used=True,
            metadata={
                **progress_metadata,
                "entrypoint": "llm_production_google_generate_content_v1",
                "request_content_recorded": False,
                "provider_gate_reason": reason,
                "max_output_tokens": output_limit,
                "estimated_input_tokens": input_estimate,
            },
            triggered_by=triggered_by,
            staff=staff,
            update_budget_scopes=False,
        )
        _append_google_attempt(
            attempt_log,
            model=exact_model,
            metadata=progress_metadata,
            state="provider_blocked",
            estimated_cost_usd=0.0,
        )
        raise _sdk_failure(
            reason,
            provider=provider,
            model=exact_model,
            purpose=exact_purpose,
        )

    binding = llm_gateway._resolve_gateway_binding(provider, exact_model)
    estimated_micro = llm_gateway._estimate_cost_micro_usd(
        provider,
        input_estimate,
        output_limit,
        binding=binding,
    )
    estimated_cost = max(0.000001, float(estimated_micro) / 1_000_000)
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
                "entrypoint": "llm_production_google_generate_content_v1",
                "request_content_recorded": False,
                "estimated_input_tokens": input_estimate,
                "max_output_tokens": output_limit,
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
    except Exception as exc:
        if breaker_session is not None:
            try:
                llm_gateway._abandon_strict_fleet_breaker(breaker_session)
            except Exception:
                llm_gateway.logger.error(
                    "vkpi.llm_production.google_breaker_abandon_failed_during_gate",
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
                    "vkpi.llm_production.google_reservation_release_failed_during_gate",
                    extra={"reservation_key": reservation_key},
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
            cost_tag=cost_scope,
            metadata={
                **progress_metadata,
                "entrypoint": "llm_production_google_generate_content_v1",
                "request_content_recorded": False,
                "reservation_reason": reason,
                "estimated_cost_usd": estimated_cost,
            },
            triggered_by=triggered_by,
            staff=staff,
            update_budget_scopes=False,
            force_cost_ledger=True,
        )
        _append_google_attempt(
            attempt_log,
            model=exact_model,
            metadata=progress_metadata,
            state="budget_blocked",
            estimated_cost_usd=estimated_cost,
        )
        raise _sdk_failure(
            reason,
            provider=provider,
            model=exact_model,
            purpose=exact_purpose,
        ) from exc

    try:
        llm_gateway._llm_budget_reservations().mark_llm_provider_started(
            reservation_key
        )
    except Exception as exc:
        if breaker_session is not None:
            try:
                llm_gateway._abandon_strict_fleet_breaker(breaker_session)
            except Exception:
                llm_gateway.logger.error(
                    "vkpi.llm_production.google_breaker_abandon_failed_before_provider",
                    extra={"provider": provider, "model": exact_model},
                    exc_info=True,
                )
        released = False
        try:
            released = bool(
                llm_gateway._llm_budget_reservations().release_llm_reservation(
                    reservation_key
                )
            )
        except Exception:
            released = False
        if not released:
            llm_gateway._mark_reserved_attempt_unknown(reservation_key)
        _append_google_attempt(
            attempt_log,
            model=exact_model,
            metadata=progress_metadata,
            state="released" if released else "unknown",
            estimated_cost_usd=estimated_cost,
        )
        raise _sdk_failure(
            "reservation_not_startable",
            provider=provider,
            model=exact_model,
            purpose=exact_purpose,
        ) from exc

    started = time.monotonic()
    try:
        response = client.models.generate_content(
            model=exact_model,
            contents=contents,
            config=provider_config,
        )
    except Exception as provider_exc:
        breaker_completion_error: Exception | None = None
        try:
            llm_gateway._complete_strict_fleet_breaker(
                breaker_session, provider_exc
            )
        except Exception as exc:
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
                    "entrypoint": "llm_production_google_generate_content_v1",
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
        finally:
            llm_gateway._mark_reserved_attempt_unknown(reservation_key)
            _append_google_attempt(
                attempt_log,
                model=exact_model,
                metadata=progress_metadata,
                state="unknown",
                estimated_cost_usd=estimated_cost,
            )
        if breaker_completion_error is not None:
            raise _sdk_failure(
                "fleet_breaker_store_unavailable_after_provider",
                provider=provider,
                model=exact_model,
                purpose=exact_purpose,
            ) from breaker_completion_error
        raise

    usage_metadata = _google_usage_metadata(response)
    input_tokens = _usage_int(
        usage_metadata, "prompt_token_count", "promptTokenCount"
    )
    output_tokens = _usage_int(
        usage_metadata, "candidates_token_count", "candidatesTokenCount"
    ) + _usage_int(
        usage_metadata, "thoughts_token_count", "thoughtsTokenCount"
    )
    response_model = str(
        getattr(response, "model_version", "")
        or getattr(response, "model", "")
        or ""
    ).strip()
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
        _append_google_attempt(
            attempt_log,
            model=exact_model,
            metadata=progress_metadata,
            state="unknown",
            estimated_cost_usd=estimated_cost,
        )
        raise _sdk_failure(
            "fleet_breaker_store_unavailable_after_provider",
            provider=provider,
            model=exact_model,
            purpose=exact_purpose,
        ) from exc
    actual_micro = (
        _google_usage_cost_micro_usd(
            model=exact_model,
            binding=binding,
            usage_metadata=usage_metadata,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
        )
        if input_tokens > 0 and output_tokens > 0
        else 0
    )
    if status == "model_mismatch":
        actual_micro = max(actual_micro, estimated_micro)
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
            fallback_used=status != "success",
            cost_tag=cost_scope,
            metadata={
                **progress_metadata,
                "entrypoint": "llm_production_google_generate_content_v1",
                "request_content_recorded": False,
                "reservation_key": reservation_key,
                "reservation_estimated_cost_usd": estimated_cost,
                "latency_ms": max(0, int((time.monotonic() - started) * 1000)),
                "response_model": response_model,
                "max_output_tokens": output_limit,
                "usage_metadata": usage_metadata,
            },
            triggered_by=triggered_by,
            staff=staff,
            update_budget_scopes=False,
            force_cost_ledger=True,
        )
    except Exception:
        llm_gateway._mark_reserved_attempt_unknown(reservation_key)
        _append_google_attempt(
            attempt_log,
            model=exact_model,
            metadata=progress_metadata,
            state="unknown",
            estimated_cost_usd=estimated_cost,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
        )
        raise

    if status == "usage_missing":
        llm_gateway._mark_reserved_attempt_unknown(reservation_key)
        _append_google_attempt(
            attempt_log,
            model=exact_model,
            metadata=progress_metadata,
            state="unknown",
            estimated_cost_usd=estimated_cost,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
        )
        raise _sdk_failure(
            status,
            provider=provider,
            model=exact_model,
            purpose=exact_purpose,
        )

    actual_cost = float(actual_micro) / 1_000_000
    try:
        settlement = llm_gateway._llm_budget_reservations().settle_llm_reservation(
            reservation_key,
            actual_cost,
        )
        if not bool(settlement.get("settled")):
            raise RuntimeError(str(settlement.get("reason") or "not_settled"))
    except Exception:
        llm_gateway._mark_reserved_attempt_unknown(reservation_key)
        _append_google_attempt(
            attempt_log,
            model=exact_model,
            metadata=progress_metadata,
            state="unknown",
            estimated_cost_usd=estimated_cost,
            actual_cost_usd=actual_cost,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
        )
        raise
    _append_google_attempt(
        attempt_log,
        model=exact_model,
        metadata=progress_metadata,
        state="settled" if status == "success" else status,
        estimated_cost_usd=estimated_cost,
        actual_cost_usd=actual_cost,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
    )
    if status != "success":
        raise _sdk_failure(
            status,
            provider=provider,
            model=exact_model,
            purpose=exact_purpose,
        )
    return response


def generate_text(
    prompt: str,
    *,
    provider: str,
    model: str,
    purpose: str,
    max_output_tokens: int = 800,
    cost_tag: str | None = None,
    triggered_by: Any = None,
    staff: dict[str, Any] | None = None,
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Generate text through the exact-model, reservation-backed boundary.

    The authoritative empty fallback chain prevents a requested Anthropic model
    from silently falling into an unrelated OpenAI/Google global default.
    """

    provider_key = str(provider or "").strip().lower()
    provider_key = {"gemini": "google", "claude": "anthropic"}.get(
        provider_key, provider_key
    )
    exact_model = str(model or "").strip()
    if not provider_key or not exact_model:
        raise ValueError("provider and exact model are required")
    progress_metadata = _progress_metadata(
        purpose,
        metadata,
        phase="provider_generation",
    )
    result = llm_gateway.invoke(
        str(prompt or ""),
        purpose=str(purpose or ""),
        max_output_tokens=max_output_tokens,
        preferred_provider=provider_key,
        model_override=exact_model,
        model_fallbacks=(),
        require_runtime_verified=True,
        require_configured_budget=False,
        cost_tag=cost_tag,
        triggered_by=triggered_by,
        metadata={
            **progress_metadata,
            "entrypoint": "llm_production_text_v1",
            "execution_class": "production",
            "request_content_recorded": False,
        },
        staff=staff,
        enforce_atomic_reservation=True,
    )
    if (
        str(result.get("status") or "") != "success"
        or str(result.get("provider") or "").strip().lower() != provider_key
        or not str(result.get("text") or "").strip()
    ):
        raise ProductionLlmUnavailable(result)
    return result


def generate_json(
    prompt: str,
    *,
    provider: str,
    model: str,
    purpose: str,
    max_output_tokens: int = 800,
    cost_tag: str | None = None,
    triggered_by: Any = None,
    staff: dict[str, Any] | None = None,
    required_keys: Iterable[str] | None = None,
    validator: Callable[[Any], Any] | None = None,
    deadline_seconds: float | None = None,
    metadata: dict[str, Any] | None = None,
    require_configured_budget: bool = True,
) -> dict[str, Any]:
    """Generate one exact-model JSON result through the atomic boundary.

    Unlike :func:`generate_text`, this returns the gateway's bounded fallback
    object when the provider is blocked or its JSON contract fails.  Existing
    business callers already use that object to distinguish an honest degraded
    result from a validated model result; raising would erase that distinction.
    """

    provider_key = str(provider or "").strip().lower()
    provider_key = {"gemini": "google", "claude": "anthropic"}.get(
        provider_key, provider_key
    )
    exact_model = str(model or "").strip()
    if not provider_key or not exact_model:
        raise ValueError("provider and exact model are required")
    progress_metadata = _progress_metadata(
        purpose,
        metadata,
        phase="structured_generation",
    )
    task_binding = str(progress_metadata.get("task_binding") or "").strip()
    actual_binding = f"{provider_key}/{exact_model}"
    if task_binding:
        expected_binding = current_task_model_binding().get(task_binding, "")
        if expected_binding != actual_binding:
            raise _sdk_failure(
                "task_binding_model_mismatch",
                provider=provider_key,
                model=exact_model,
                purpose=str(purpose or "").strip(),
                details={
                    "task_binding": task_binding,
                    "expected_binding": expected_binding,
                    "actual_binding": actual_binding,
                },
            )
    return llm_gateway.invoke_json(
        str(prompt or ""),
        purpose=str(purpose or ""),
        max_output_tokens=max_output_tokens,
        preferred_provider=provider_key,
        model_override=exact_model,
        model_fallbacks=(),
        require_runtime_verified=True,
        require_configured_budget=bool(require_configured_budget),
        cost_tag=cost_tag,
        triggered_by=triggered_by,
        metadata={
            **progress_metadata,
            "entrypoint": "llm_production_json_v1",
            "execution_class": "production",
            "request_content_recorded": False,
        },
        staff=staff,
        required_keys=required_keys,
        validator=validator,
        deadline_seconds=deadline_seconds,
        max_provider_attempts=1,
        enforce_atomic_reservation=True,
    )


__all__ = [
    "GOOGLE_GENERATE_MAX_OUTPUT_TOKENS_HARD_CAP",
    "ProductionLlmUnavailable",
    "generate_anthropic_messages",
    "generate_google_content",
    "generate_json",
    "generate_text",
]
