"""Orchestration runtime for JSON-contract LLM invocation."""
from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from typing import Any


@dataclass
class JsonInvocationState:
    gateway: Any
    prompt: str
    purpose: str
    max_output_tokens: int
    preferred_provider: str | None
    model_override: str | None
    model_fallbacks: Any
    require_runtime_verified: bool
    skip_budget_check: bool
    require_configured_budget: bool
    cost_tag: str | None
    triggered_by: Any
    metadata: dict[str, Any] | None
    staff: dict[str, Any] | None
    required_keys: tuple[str, ...]
    validator: Callable[[Any], Any] | None
    started: float
    deadline_seconds: float
    deadline_at: float
    attempt_limit: int | None
    enforce_atomic_reservation: bool
    preflight_candidate: Callable[..., Any]
    run_candidate: Callable[..., Any]
    build_cache_plan: Callable[..., Any]
    cache_model_label: Callable[..., Any]
    serve_cached_result: Callable[..., Any]
    deferred_or_none: Callable[..., Any]
    store_cached_result: Callable[..., Any]
    cost_scope: str = ""
    errors: list[dict[str, Any]] = field(default_factory=list)
    budget_warnings: list[dict[str, Any]] = field(default_factory=list)
    provider_attempts: int = 0
    deadline_hit: bool = False
    cache_plan: Any = None


def _elapsed_ms(state: JsonInvocationState) -> int:
    return max(
        0,
        int((state.gateway.time.monotonic() - state.started) * 1000),
    )


def _prepare_state(
    gateway: Any,
    prompt: str,
    *,
    purpose: str,
    max_output_tokens: int,
    preferred_provider: str | None,
    model_override: str | None,
    model_fallbacks: Iterable[tuple[str, str]] | None,
    require_runtime_verified: bool,
    skip_budget_check: bool,
    require_configured_budget: bool,
    cost_tag: str | None,
    triggered_by: Any,
    metadata: dict[str, Any] | None,
    staff: dict[str, Any] | None,
    required_keys: Iterable[str] | None,
    validator: Callable[[Any], Any] | None,
    deadline_seconds: float | None,
    max_provider_attempts: int | None,
    enforce_atomic_reservation: bool,
    preflight_candidate: Callable[..., Any],
    run_candidate: Callable[..., Any],
    build_cache_plan: Callable[..., Any],
    cache_model_label: Callable[..., Any],
    serve_cached_result: Callable[..., Any],
    deferred_or_none: Callable[..., Any],
    store_cached_result: Callable[..., Any],
) -> JsonInvocationState:
    atomic_reservation = gateway._strict_atomic_reservation_enabled(
        enforce_atomic_reservation
    )
    started = gateway.time.monotonic()
    resolved_deadline = gateway._resolve_deadline_seconds(deadline_seconds)
    deadline_at = started + resolved_deadline
    normalized_max_output_tokens = max(16, int(max_output_tokens or 0))
    safe_prompt = str(prompt or "")
    contract_keys = gateway._normalise_required_keys(required_keys)
    attempt_limit = (
        None
        if max_provider_attempts is None
        else max(1, int(max_provider_attempts))
    )
    return JsonInvocationState(
        gateway=gateway,
        prompt=safe_prompt,
        purpose=purpose,
        max_output_tokens=normalized_max_output_tokens,
        preferred_provider=preferred_provider,
        model_override=model_override,
        model_fallbacks=model_fallbacks,
        require_runtime_verified=require_runtime_verified,
        skip_budget_check=skip_budget_check,
        require_configured_budget=require_configured_budget,
        cost_tag=cost_tag,
        triggered_by=triggered_by,
        metadata=metadata,
        staff=staff,
        required_keys=contract_keys,
        validator=validator,
        started=started,
        deadline_seconds=resolved_deadline,
        deadline_at=deadline_at,
        attempt_limit=attempt_limit,
        enforce_atomic_reservation=atomic_reservation,
        preflight_candidate=preflight_candidate,
        run_candidate=run_candidate,
        build_cache_plan=build_cache_plan,
        cache_model_label=cache_model_label,
        serve_cached_result=serve_cached_result,
        deferred_or_none=deferred_or_none,
        store_cached_result=store_cached_result,
    )


def _empty_prompt_result(state: JsonInvocationState) -> dict[str, Any]:
    gateway = state.gateway
    fallback = gateway._rule_fallback(
        state.prompt,
        purpose=state.purpose,
        reason="empty_prompt",
    )
    fallback.update(
        {
            "json": None,
            "deadline_seconds": state.deadline_seconds,
            "elapsed_ms": 0,
        }
    )
    gateway.record_call(
        provider="rule_v0",
        model="rule_v0",
        purpose=state.purpose,
        prompt=state.prompt,
        status="empty_prompt",
        fallback_used=True,
        cost_tag=state.cost_tag,
        triggered_by=state.triggered_by,
        metadata={
            **(state.metadata or {}),
            "reason": fallback["reason"],
            "json_contract": True,
        },
        staff=state.staff,
    )
    return fallback


def _scope_budget_preflight(state: JsonInvocationState) -> None:
    if not state.cost_scope:
        return
    gateway = state.gateway
    try:
        allowed = gateway._budget_guard().check_budget(
            state.cost_scope,
            0,
            require_configured=True,
        )
        if not allowed:
            state.budget_warnings.append(
                {
                    "stage": "scope_preflight",
                    "reason": "ai_budget_hard_stop",
                    "cost_tag": state.cost_scope,
                }
            )
            gateway.logger.warning(
                "vkpi.llm_gateway.ai_budget_hard_stop_record_only",
                extra={"cost_tag": state.cost_scope, "purpose": state.purpose},
            )
    except Exception:
        gateway.logger.warning(
            "vkpi.llm_gateway.ai_budget_check_failed",
            exc_info=True,
        )


def _monthly_budget_preflight(state: JsonInvocationState) -> None:
    if state.skip_budget_check:
        return
    gateway = state.gateway
    monthly_budget = gateway._monthly_budget_cents()
    remaining = gateway._budget_remaining_cents()
    if monthly_budget > 0 and remaining > 0:
        return
    reason = "budget_disabled" if monthly_budget <= 0 else "budget_exhausted"
    state.budget_warnings.append(
        {
            "stage": "monthly_preflight",
            "reason": reason,
            "monthly_budget_cents": monthly_budget,
            "remaining_cents": remaining,
        }
    )
    gateway.logger.warning(
        "vkpi.llm_gateway.monthly_budget_record_only",
        extra={
            "reason": reason,
            "purpose": state.purpose,
            "remaining_cents": remaining,
        },
    )


def _cached_result(
    state: JsonInvocationState,
    candidates: list[tuple[str, str, bool]],
) -> dict[str, Any] | None:
    state.cache_plan = state.build_cache_plan(
        state.purpose,
        state.prompt,
        model=state.cache_model_label(candidates),
        contract="json",
        max_output_tokens=state.max_output_tokens,
        metadata=state.metadata,
    )
    cached = state.serve_cached_result(
        plan=state.cache_plan,
        purpose=state.purpose,
        prompt=state.prompt,
        contract="json",
        record_call=state.gateway.record_call,
        triggered_by=state.triggered_by,
        metadata=state.metadata,
        staff=state.staff,
        cost_scope=state.cost_scope,
    )
    if cached is None:
        return None
    cached.update(
        {
            "deadline_seconds": state.deadline_seconds,
            "elapsed_ms": _elapsed_ms(state),
            "provider_attempts": 0,
            "budget_reservation_key": None,
        }
    )
    return cached


def _run_candidates(
    state: JsonInvocationState,
    candidates: list[tuple[str, str, bool]],
) -> dict[str, Any] | None:
    for index, raw_candidate in enumerate(candidates):
        prepared = state.preflight_candidate(state, index, raw_candidate)
        prepared_action = str(getattr(prepared, "action", ""))
        if prepared_action:
            if prepared_action == "break":
                break
            if prepared_action == "return":
                return prepared.result
            continue
        if prepared is None:
            continue
        decision = state.run_candidate(state, prepared)
        if decision.action == "return":
            return decision.result
        if decision.action == "break":
            break
    return None


def _deferred_result(state: JsonInvocationState) -> dict[str, Any] | None:
    if state.deadline_hit or state.provider_attempts != 0:
        return None
    return state.deferred_or_none(
        prompt=state.prompt,
        purpose=state.purpose,
        errors=state.errors,
        normalise_error=state.gateway._normalise_runtime_error,
        record_call=state.gateway.record_call,
        cost_scope=state.cost_scope,
        triggered_by=state.triggered_by,
        metadata={**(state.metadata or {}), "json_contract": True},
        staff=state.staff,
        extra={
            "deadline_seconds": state.deadline_seconds,
            "elapsed_ms": _elapsed_ms(state),
            "provider_attempts": 0,
        },
    )


def _final_fallback(state: JsonInvocationState) -> dict[str, Any]:
    gateway = state.gateway
    fallback_reason = (
        "deadline_exceeded" if state.deadline_hit else "all_providers_failed"
    )
    fallback = gateway._rule_fallback(
        state.prompt,
        purpose=state.purpose,
        reason=fallback_reason,
        errors=state.errors,
    )
    degrade_reason = str(
        fallback.get("failure_code")
        or fallback.get("reason")
        or fallback_reason
    )
    fallback.update(
        {
            "json": None,
            "fallback_reason": degrade_reason,
            "deadline_seconds": state.deadline_seconds,
            "elapsed_ms": _elapsed_ms(state),
            "provider_attempts": state.provider_attempts,
        }
    )
    gateway.record_call(
        provider="rule_v0",
        model="rule_v0",
        purpose=state.purpose,
        prompt=state.prompt,
        status=fallback_reason,
        fallback_used=True,
        cost_tag=state.cost_scope,
        triggered_by=state.triggered_by,
        metadata={
            **(state.metadata or {}),
            "errors": state.errors,
            "fallback_reason": degrade_reason,
            "json_contract": True,
            "deadline_seconds": state.deadline_seconds,
            "model_level_fallback": bool(state.model_fallbacks),
            "runtime_verification_hard_gate": True,
        },
        staff=state.staff,
    )
    return fallback


def invoke_json_runtime(
    gateway: Any,
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
    required_keys: Iterable[str] | None = None,
    validator: Callable[[Any], Any] | None = None,
    deadline_seconds: float | None = None,
    max_provider_attempts: int | None = None,
    enforce_atomic_reservation: bool = False,
    preflight_candidate: Callable[..., Any],
    run_candidate: Callable[..., Any],
    build_cache_plan: Callable[..., Any],
    cache_model_label: Callable[..., Any],
    serve_cached_result: Callable[..., Any],
    deferred_or_none: Callable[..., Any],
    store_cached_result: Callable[..., Any],
) -> dict[str, Any]:
    state = _prepare_state(
        gateway,
        prompt,
        purpose=purpose,
        max_output_tokens=max_output_tokens,
        preferred_provider=preferred_provider,
        model_override=model_override,
        model_fallbacks=model_fallbacks,
        require_runtime_verified=require_runtime_verified,
        skip_budget_check=skip_budget_check,
        require_configured_budget=require_configured_budget,
        cost_tag=cost_tag,
        triggered_by=triggered_by,
        metadata=metadata,
        staff=staff,
        required_keys=required_keys,
        validator=validator,
        deadline_seconds=deadline_seconds,
        max_provider_attempts=max_provider_attempts,
        enforce_atomic_reservation=enforce_atomic_reservation,
        preflight_candidate=preflight_candidate,
        run_candidate=run_candidate,
        build_cache_plan=build_cache_plan,
        cache_model_label=cache_model_label,
        serve_cached_result=serve_cached_result,
        deferred_or_none=deferred_or_none,
        store_cached_result=store_cached_result,
    )
    if not state.prompt.strip():
        return _empty_prompt_result(state)
    state.cost_scope = gateway._cost_scope_for_purpose(purpose, cost_tag)
    _scope_budget_preflight(state)
    _monthly_budget_preflight(state)
    candidates = gateway._ordered_model_candidates(
        preferred_provider,
        model_override,
        model_fallbacks,
    )
    cached = _cached_result(state, candidates)
    if cached is not None:
        return cached
    result = _run_candidates(state, candidates)
    if result is not None:
        return result
    deferred = _deferred_result(state)
    if deferred is not None:
        return deferred
    return _final_fallback(state)
