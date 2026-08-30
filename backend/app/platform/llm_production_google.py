"""Gemini multimodal strict adapter (split from llm_production, 2026-08-23).

Every call owns an independent reservation.  A confirmed response is ledgered
and settled exactly once; a provider exception or unverifiable usage remains
``unknown`` and therefore continues to consume the reserved allowance.  A
failure before provider I/O releases the reservation.

调用方 / 测试只认门面 ``app.platform.llm_production``(monkeypatch 也打在门面上);
本模块不得被业务代码直接 import。任务绑定经门面解析(见 llm_production_common)。
"""
from __future__ import annotations

import time
from typing import Any

from app.platform import llm_gateway
from app.platform.llm_production_common import (
    sdk_failure as _sdk_failure,
)
from app.platform.llm_production_google_helpers import (
    GOOGLE_GENERATE_INPUT_TOKENS_HARD_CAP,
    GOOGLE_GENERATE_MAX_OUTPUT_TOKENS_HARD_CAP,
    append_google_attempt as _append_google_attempt,
    google_config_with_output_limit as _google_config_with_output_limit,
    google_contents_fingerprint as _google_contents_fingerprint,
)
from app.platform.llm_production_google_stages import (
    google_actual_cost_micro as _google_actual_cost_micro,
    google_progress_metadata as _google_progress_metadata,
    google_token_limits as _google_token_limits,
    google_usage_and_status as _google_usage_and_status,
    validate_google_task_binding as _validate_google_task_binding,
)


def _google_budget_gate(
    *,
    request_identity: str,
    provider: str,
    exact_model: str,
    exact_purpose: str,
    actual_binding: str,
    progress_metadata: dict[str, Any],
    task_binding_fallback: bool,
    output_limit: int,
    input_estimate: int,
    cost_tag: str | None,
    triggered_by: Any,
    staff: dict[str, Any] | None,
    attempt_log: list[dict[str, Any]] | None,
) -> None:
    """Run budget preflight before any provider I/O; blocked → ledger + raise."""

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
            fallback_used=task_binding_fallback,
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


def _reserve_google_budget(
    *,
    provider: str,
    exact_model: str,
    exact_purpose: str,
    request_identity: str,
    estimated_cost: float,
    cost_scope: str,
    progress_metadata: dict[str, Any],
    input_estimate: int,
    output_limit: int,
    task_binding_fallback: bool,
    triggered_by: Any,
    staff: dict[str, Any] | None,
    attempt_log: list[dict[str, Any]] | None,
) -> tuple[str, Any]:
    """Reserve the spend allowance and acquire the strict fleet breaker."""

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
            fallback_used=task_binding_fallback,
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
    return reservation_key, breaker_session


def _start_google_reservation(
    *,
    reservation_key: str,
    breaker_session: Any,
    provider: str,
    exact_model: str,
    exact_purpose: str,
    progress_metadata: dict[str, Any],
    estimated_cost: float,
    attempt_log: list[dict[str, Any]] | None,
) -> None:
    """Flip the reservation into provider-started (failure → release/unknown)."""

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


def _call_google_provider(
    *,
    client: Any,
    contents: list[Any],
    provider_config: Any,
    provider: str,
    exact_model: str,
    exact_purpose: str,
    request_identity: str,
    reservation_key: str,
    breaker_session: Any,
    progress_metadata: dict[str, Any],
    estimated_cost: float,
    task_binding_fallback: bool,
    triggered_by: Any,
    staff: dict[str, Any] | None,
    attempt_log: list[dict[str, Any]] | None,
) -> tuple[Any, float]:
    """Perform the single provider request (exception → unknown + re-raise)."""

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
                fallback_used=task_binding_fallback,
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
    return response, started


def _finalize_google_breaker(
    *,
    breaker_session: Any,
    status: str,
    reservation_key: str,
    provider: str,
    exact_model: str,
    exact_purpose: str,
    progress_metadata: dict[str, Any],
    estimated_cost: float,
    attempt_log: list[dict[str, Any]] | None,
) -> None:
    """Complete the fleet breaker with the verified outcome (store down → raise)."""

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


def _ledger_google_call(
    *,
    provider: str,
    exact_model: str,
    exact_purpose: str,
    request_identity: str,
    input_tokens: int,
    output_tokens: int,
    actual_micro: int,
    status: str,
    task_binding_fallback: bool,
    cost_scope: str,
    progress_metadata: dict[str, Any],
    reservation_key: str,
    estimated_cost: float,
    started: float,
    response_model: str,
    output_limit: int,
    usage_metadata: dict[str, Any],
    triggered_by: Any,
    staff: dict[str, Any] | None,
    attempt_log: list[dict[str, Any]] | None,
) -> None:
    """Ledger the confirmed attempt exactly once (write failure → unknown)."""

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
            fallback_used=task_binding_fallback,
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


def _settle_google_reservation(
    *,
    reservation_key: str,
    actual_cost: float,
    exact_model: str,
    progress_metadata: dict[str, Any],
    estimated_cost: float,
    input_tokens: int,
    output_tokens: int,
    attempt_log: list[dict[str, Any]] | None,
) -> None:
    """Settle the reservation exactly once (failure → unknown + re-raise)."""

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
    output_limit, input_estimate = _google_token_limits(
        max_output_tokens, estimated_input_tokens
    )
    provider_config = _google_config_with_output_limit(
        config, output_limit, model=exact_model
    )
    progress_metadata = _google_progress_metadata(
        exact_purpose, metadata, execution_class
    )
    actual_binding, task_binding_fallback = _validate_google_task_binding(
        progress_metadata,
        provider=provider,
        exact_model=exact_model,
        exact_purpose=exact_purpose,
    )

    request_identity = _google_contents_fingerprint(contents)
    _google_budget_gate(
        request_identity=request_identity,
        provider=provider,
        exact_model=exact_model,
        exact_purpose=exact_purpose,
        actual_binding=actual_binding,
        progress_metadata=progress_metadata,
        task_binding_fallback=task_binding_fallback,
        output_limit=output_limit,
        input_estimate=input_estimate,
        cost_tag=cost_tag,
        triggered_by=triggered_by,
        staff=staff,
        attempt_log=attempt_log,
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
    reservation_key, breaker_session = _reserve_google_budget(
        provider=provider,
        exact_model=exact_model,
        exact_purpose=exact_purpose,
        request_identity=request_identity,
        estimated_cost=estimated_cost,
        cost_scope=cost_scope,
        progress_metadata=progress_metadata,
        input_estimate=input_estimate,
        output_limit=output_limit,
        task_binding_fallback=task_binding_fallback,
        triggered_by=triggered_by,
        staff=staff,
        attempt_log=attempt_log,
    )
    _start_google_reservation(
        reservation_key=reservation_key,
        breaker_session=breaker_session,
        provider=provider,
        exact_model=exact_model,
        exact_purpose=exact_purpose,
        progress_metadata=progress_metadata,
        estimated_cost=estimated_cost,
        attempt_log=attempt_log,
    )

    response, started = _call_google_provider(
        client=client,
        contents=contents,
        provider_config=provider_config,
        provider=provider,
        exact_model=exact_model,
        exact_purpose=exact_purpose,
        request_identity=request_identity,
        reservation_key=reservation_key,
        breaker_session=breaker_session,
        progress_metadata=progress_metadata,
        estimated_cost=estimated_cost,
        task_binding_fallback=task_binding_fallback,
        triggered_by=triggered_by,
        staff=staff,
        attempt_log=attempt_log,
    )

    usage_metadata, input_tokens, output_tokens, response_model, status = (
        _google_usage_and_status(response, binding)
    )
    _finalize_google_breaker(
        breaker_session=breaker_session,
        status=status,
        reservation_key=reservation_key,
        provider=provider,
        exact_model=exact_model,
        exact_purpose=exact_purpose,
        progress_metadata=progress_metadata,
        estimated_cost=estimated_cost,
        attempt_log=attempt_log,
    )
    actual_micro = _google_actual_cost_micro(
        exact_model=exact_model,
        binding=binding,
        usage_metadata=usage_metadata,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        status=status,
        estimated_micro=estimated_micro,
        progress_metadata=progress_metadata,
    )
    _ledger_google_call(
        provider=provider,
        exact_model=exact_model,
        exact_purpose=exact_purpose,
        request_identity=request_identity,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        actual_micro=actual_micro,
        status=status,
        task_binding_fallback=task_binding_fallback,
        cost_scope=cost_scope,
        progress_metadata=progress_metadata,
        reservation_key=reservation_key,
        estimated_cost=estimated_cost,
        started=started,
        response_model=response_model,
        output_limit=output_limit,
        usage_metadata=usage_metadata,
        triggered_by=triggered_by,
        staff=staff,
        attempt_log=attempt_log,
    )

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
    _settle_google_reservation(
        reservation_key=reservation_key,
        actual_cost=actual_cost,
        exact_model=exact_model,
        progress_metadata=progress_metadata,
        estimated_cost=estimated_cost,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        attempt_log=attempt_log,
    )
    _append_google_attempt(
        attempt_log,
        model=exact_model,
        metadata=progress_metadata,
        state="settled" if status == "success" else status,
        estimated_cost_usd=estimated_cost,
        actual_cost_usd=actual_cost,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        response_model=response_model,
    )
    if status != "success":
        raise _sdk_failure(
            status,
            provider=provider,
            model=exact_model,
            purpose=exact_purpose,
        )
    return response


__all__ = [
    "GOOGLE_GENERATE_INPUT_TOKENS_HARD_CAP",
    "GOOGLE_GENERATE_MAX_OUTPUT_TOKENS_HARD_CAP",
    "generate_google_content",
]
