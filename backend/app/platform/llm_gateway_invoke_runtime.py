"""Top-level orchestration for text LLM invocation."""
from __future__ import annotations

from typing import Any, Iterable

from app.platform import llm_gateway_invoke_attempts as _invoke_attempts
from app.platform.llm_gateway_invoke_types import InvocationContext, InvocationHooks


_DEPENDENCY_NAMES = (
    "_rule_fallback",
    "record_call",
    "_cost_scope_for_purpose",
    "_budget_guard",
    "logger",
    "_monthly_budget_cents",
    "_budget_remaining_cents",
    "_ordered_model_candidates",
    "_resolve_gateway_binding",
    "_binding_call_blocker",
    "_is_provider_configured",
    "_PROVIDER_CALLERS",
    "_estimated_cost_usd",
    "_budget_allows_provider",
    "_record_budget_blocked_attempt",
    "_llm_budget_reservations",
    "_acquire_strict_fleet_breaker",
    "_complete_strict_fleet_breaker",
    "_abandon_strict_fleet_breaker",
    "SINGLE_CALL_BUDGET_SCOPE",
    "_mark_reserved_attempt_unknown",
    "_record_reserved_provider_attempt",
    "_safe_int",
    "_estimate_cost_micro_usd",
    "_micro_usd_to_cents",
)


def _resolve_dependencies(namespace: dict[str, Any]) -> dict[str, Any]:
    return {name: namespace[name] for name in _DEPENDENCY_NAMES}


def _empty_prompt_result(
    deps: dict[str, Any],
    *,
    prompt: str,
    purpose: str,
    cost_tag: str | None,
    triggered_by: Any,
    metadata: dict[str, Any] | None,
    staff: dict[str, Any] | None,
) -> dict[str, Any]:
    result = deps["_rule_fallback"](
        prompt,
        purpose=purpose,
        reason="empty_prompt",
    )
    deps["record_call"](
        provider="rule_v0",
        model="rule_v0",
        purpose=purpose,
        prompt=prompt,
        status="empty_prompt",
        fallback_used=True,
        cost_tag=cost_tag,
        triggered_by=triggered_by,
        metadata={**(metadata or {}), "reason": result["reason"]},
        staff=staff,
    )
    return result


def _scope_budget_preflight(ctx: InvocationContext) -> None:
    if not ctx.cost_scope:
        return
    try:
        if not ctx.deps["_budget_guard"]().check_budget(
            ctx.cost_scope,
            0,
            require_configured=True,
        ):
            ctx.budget_warnings.append(
                {
                    "stage": "scope_preflight",
                    "reason": "ai_budget_hard_stop",
                    "cost_tag": ctx.cost_scope,
                }
            )
            ctx.deps["logger"].warning(
                "vkpi.llm_gateway.ai_budget_hard_stop_record_only",
                extra={"cost_tag": ctx.cost_scope, "purpose": ctx.purpose},
            )
    except Exception:
        ctx.deps["logger"].warning(
            "vkpi.llm_gateway.ai_budget_check_failed",
            exc_info=True,
        )


def _monthly_budget_preflight(ctx: InvocationContext) -> None:
    if ctx.skip_budget_check:
        return
    monthly_budget = ctx.deps["_monthly_budget_cents"]()
    remaining = ctx.deps["_budget_remaining_cents"]()
    if monthly_budget > 0 and remaining > 0:
        return
    reason = "budget_disabled" if monthly_budget <= 0 else "budget_exhausted"
    ctx.budget_warnings.append(
        {
            "stage": "monthly_preflight",
            "reason": reason,
            "monthly_budget_cents": monthly_budget,
            "remaining_cents": remaining,
        }
    )
    ctx.deps["logger"].warning(
        "vkpi.llm_gateway.monthly_budget_record_only",
        extra={
            "reason": reason,
            "purpose": ctx.purpose,
            "remaining_cents": remaining,
        },
    )


def _prepare_cache(ctx: InvocationContext) -> dict[str, Any] | None:
    ctx.candidates = ctx.deps["_ordered_model_candidates"](
        ctx.preferred_provider,
        ctx.model_override,
        ctx.model_fallbacks,
    )
    ctx.cache_plan = ctx.hooks.result_cache.build_cache_plan(
        ctx.purpose,
        ctx.safe_prompt,
        model=ctx.hooks.cache_model_label(ctx.candidates),
        contract="text",
        max_output_tokens=ctx.max_output_tokens,
        metadata=ctx.metadata,
    )
    return ctx.hooks.serve_cached_result(
        plan=ctx.cache_plan,
        purpose=ctx.purpose,
        prompt=ctx.safe_prompt,
        contract="text",
        record_call=ctx.deps["record_call"],
        triggered_by=ctx.triggered_by,
        metadata=ctx.metadata,
        staff=ctx.staff,
        cost_scope=ctx.cost_scope,
    )


def _run_candidates(ctx: InvocationContext) -> dict[str, Any] | None:
    for index, candidate in enumerate(ctx.candidates):
        attempt = _invoke_attempts.prepare_candidate(ctx, index, candidate)
        if attempt is None:
            continue
        result = _invoke_attempts.execute_candidate(ctx, attempt)
        if result is not None:
            return result
    return None


def _final_fallback(ctx: InvocationContext) -> dict[str, Any]:
    deferred = ctx.hooks.deferred_or_none(
        prompt=ctx.safe_prompt,
        purpose=ctx.purpose,
        errors=ctx.errors,
        normalise_error=ctx.namespace["_normalise_runtime_error"],
        record_call=ctx.deps["record_call"],
        cost_scope=ctx.cost_scope,
        triggered_by=ctx.triggered_by,
        metadata=ctx.metadata,
        staff=ctx.staff,
    )
    if deferred is not None:
        return deferred
    fallback = ctx.deps["_rule_fallback"](
        ctx.safe_prompt,
        purpose=ctx.purpose,
        reason="all_providers_failed",
        errors=ctx.errors,
    )
    fallback_reason = str(
        fallback.get("failure_code")
        or fallback.get("reason")
        or "all_providers_failed"
    )
    fallback["fallback_reason"] = fallback_reason
    ctx.deps["record_call"](
        provider="rule_v0",
        model="rule_v0",
        purpose=ctx.purpose,
        prompt=ctx.safe_prompt,
        status="all_providers_failed",
        fallback_used=True,
        cost_tag=ctx.cost_scope,
        triggered_by=ctx.triggered_by,
        metadata={
            **(ctx.metadata or {}),
            "errors": ctx.errors,
            "fallback_reason": fallback_reason,
            "model_level_fallback": bool(ctx.model_fallbacks),
        },
        staff=ctx.staff,
    )
    return fallback


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
    hooks: InvocationHooks,
) -> dict[str, Any]:
    deps = _resolve_dependencies(namespace)
    bounded_tokens = max(16, int(max_output_tokens or 0))
    safe_prompt = str(prompt or "")
    if not safe_prompt.strip():
        return _empty_prompt_result(
            deps,
            prompt=safe_prompt,
            purpose=purpose,
            cost_tag=cost_tag,
            triggered_by=triggered_by,
            metadata=metadata,
            staff=staff,
        )
    ctx = InvocationContext(
        prompt=prompt,
        purpose=purpose,
        max_output_tokens=bounded_tokens,
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
        enforce_atomic_reservation=enforce_atomic_reservation,
        namespace=namespace,
        deps=deps,
        hooks=hooks,
        safe_prompt=safe_prompt,
        cost_scope=deps["_cost_scope_for_purpose"](purpose, cost_tag),
    )
    _scope_budget_preflight(ctx)
    _monthly_budget_preflight(ctx)
    cached = _prepare_cache(ctx)
    if cached is not None:
        return cached
    result = _run_candidates(ctx)
    if result is not None:
        return result
    return _final_fallback(ctx)
