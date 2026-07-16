"""Read-only LLM budget preflight implementation.

The public entrypoint remains in :mod:`app.platform.llm_gateway`.  Its live
module namespace is injected here so existing monkeypatch-based tests and
operator overrides keep observing exactly the same dependencies.
"""
from __future__ import annotations

from typing import Any, Iterable, Mapping


def budget_preflight_impl(
    prompt: str,
    *,
    purpose: str = "",
    max_output_tokens: int = 800,
    preferred_provider: str | None = None,
    model_override: str | None = None,
    model_fallbacks: Iterable[tuple[str, str]] | None = None,
    require_runtime_verified: bool = True,
    execution_class: str,
    cost_tag: str | None = None,
    skip_monthly_env_check: bool = False,
    require_configured: bool = True,
    namespace: Mapping[str, Any],
) -> dict[str, Any]:
    """Evaluate every exact provider candidate without making provider calls."""

    execution_class_fn = namespace["_execution_class"]
    production_class = namespace["PRODUCTION_EXECUTION_CLASS"]
    evaluation_class = namespace["LOCAL_EVALUATION_EXECUTION_CLASS"]
    safe_prompt = str(prompt or "")
    resolved_execution_class = execution_class_fn(execution_class)
    cost_scope = namespace["_cost_scope_for_purpose"](purpose, cost_tag)
    monthly_budget = namespace["_monthly_budget_cents"]()
    monthly_remaining = namespace["_budget_remaining_cents"]()
    forced_offline = namespace["_truthy_env"]("VKPI_LLM_GATEWAY_FORCE_OFFLINE")
    providers: list[dict[str, Any]] = []
    candidates = namespace["_ordered_model_candidates"](
        preferred_provider, model_override, model_fallbacks
    )
    for index, (provider, model_id, explicit_model) in enumerate(candidates):
        binding = namespace["_resolve_gateway_binding"](provider, model_id)
        binding_blocker = namespace["_binding_call_blocker"](
            binding,
            explicit_model=explicit_model,
            require_runtime_verified=require_runtime_verified,
            execution_class=resolved_execution_class,
        )
        estimated_cost = namespace["_estimated_cost_usd"](
            provider,
            prompt=safe_prompt,
            max_output_tokens=max_output_tokens,
            binding=binding,
        )
        scopes = namespace["_budget_scopes_for_provider"](provider, cost_scope)
        plan = namespace["_budget_guard"]().check_budget_scopes(
            scopes, estimated_cost, require_configured=require_configured
        )
        env_allowed = bool(skip_monthly_env_check) or monthly_budget > 0 and monthly_remaining > 0
        configured = namespace["_is_provider_configured"](provider)
        provider_allowed = (
            bool(plan.get("allowed"))
            and configured
            and env_allowed
            and not forced_offline
            and not binding_blocker
        )
        runtime_gate = namespace["_build_runtime_error"](
            "model_binding_blocked" if binding_blocker else "ready",
            detail=binding_blocker,
            provider=provider,
            model=binding.model_id,
            binding=binding.binding,
            failure_reasons=[binding_blocker] if binding_blocker else [],
        )
        providers.append(
            {
                "provider": provider,
                "model": binding.model_id,
                "binding": binding.binding,
                "binding_source": binding.registry_source,
                "pricing_version": binding.pricing_version,
                "input_cents_per_million": binding.input_cents_per_million,
                "output_cents_per_million": binding.output_cents_per_million,
                "pricing_known": binding.pricing_known,
                "transport_ready": binding.transport_ready,
                "runtime_availability": binding.runtime_availability,
                "runtime_evidence_source": binding.runtime_evidence_source,
                "binding_gate_reason": binding_blocker or "ready",
                "runtime_gate": runtime_gate,
                "execution_class": resolved_execution_class,
                "evaluation_only": resolved_execution_class == evaluation_class,
                "production_authorized": bool(
                    provider_allowed and resolved_execution_class == production_class
                ),
                "authorization_scope": (
                    "evaluation_only"
                    if provider_allowed and resolved_execution_class == evaluation_class
                    else "production" if provider_allowed else "blocked"
                ),
                "claim_status": "descriptive_only",
                "model_claim_status": "descriptive_only",
                "model_readiness_status": (
                    "production_ready"
                    if provider_allowed and resolved_execution_class == production_class
                    else "evaluation_only_not_production_ready"
                    if provider_allowed and resolved_execution_class == evaluation_class
                    else "not_ready"
                ),
                "explicit_model": explicit_model,
                "model_fallback_index": index,
                "configured": configured,
                "estimated_cost_usd": estimated_cost,
                "budget_allowed": bool(plan.get("allowed")),
                "env_monthly_allowed": env_allowed,
                "provider_calls_allowed": provider_allowed,
                "scopes": scopes,
                "checks": plan.get("checks") if isinstance(plan.get("checks"), list) else [],
            }
        )
    provider_calls_allowed = any(bool(item.get("provider_calls_allowed")) for item in providers)
    if forced_offline:
        reason = "force_offline"
    elif not (bool(skip_monthly_env_check) or monthly_budget > 0):
        reason = "monthly_env_budget_disabled"
    elif not providers:
        reason = "no_provider_candidates"
    elif not any(item.get("binding_gate_reason") == "ready" for item in providers):
        reason = "model_binding_blocked"
    elif not any(bool(item.get("configured")) for item in providers):
        reason = "providers_not_configured"
    elif not any(bool(item.get("budget_allowed")) for item in providers):
        reason = "budget_hard_stop"
    elif not provider_calls_allowed:
        reason = "provider_calls_blocked"
    else:
        reason = "provider_calls_allowed"
    blocked_gates = [
        item["runtime_gate"]
        for item in providers
        if not bool(item.get("provider_calls_allowed"))
        and isinstance(item.get("runtime_gate"), dict)
        and item["runtime_gate"].get("code") != "ready"
    ]
    if provider_calls_allowed:
        provider_gate = namespace["_build_runtime_error"]("ready")
    elif blocked_gates:
        provider_gate = namespace["_summarise_runtime_errors"](
            blocked_gates, fallback_status=reason
        )
    else:
        provider_gate = namespace["_build_runtime_error"](reason)
    return {
        "mode": "llm_gateway_budget_preflight_v0",
        "provider_calls_allowed": provider_calls_allowed,
        "provider_gate_reason": reason,
        "provider_gate_detail": provider_gate["code"],
        "provider_gate": provider_gate,
        "purpose": purpose,
        "cost_scope": cost_scope,
        "max_output_tokens": max(1, min(4000, int(max_output_tokens or 800))),
        "prompt_tokens_estimate": namespace["_estimate_prompt_tokens"](safe_prompt),
        "monthly_env_budget_usd": monthly_budget / 100,
        "monthly_env_spent_usd": namespace["_current_month_spent_cents"]() / 100,
        "monthly_env_remaining_usd": max(0, monthly_remaining) / 100,
        "force_offline": forced_offline,
        "single_call_scope": namespace["SINGLE_CALL_BUDGET_SCOPE"],
        "model_level_fallback": model_fallbacks is not None,
        "execution_class": resolved_execution_class,
        "evaluation_only": resolved_execution_class == evaluation_class,
        "production_authorized": bool(
            provider_calls_allowed and resolved_execution_class == production_class
        ),
        "claim_status": "descriptive_only",
        "model_readiness_status": (
            "production_ready"
            if provider_calls_allowed and resolved_execution_class == production_class
            else "evaluation_only_not_production_ready"
            if provider_calls_allowed and resolved_execution_class == evaluation_class
            else "not_ready"
        ),
        "require_runtime_verified": resolved_execution_class == production_class,
        "providers": providers,
    }
