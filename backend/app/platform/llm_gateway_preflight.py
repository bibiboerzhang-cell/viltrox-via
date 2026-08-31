"""Read-only LLM budget preflight implementation.

The public entrypoint remains in :mod:`app.platform.llm_gateway`.  Its live
module namespace is injected here so existing monkeypatch-based tests and
operator overrides keep observing exactly the same dependencies.
"""
from __future__ import annotations

from typing import Any, Iterable, Mapping


def _candidate_readiness_and_authorization(
    *,
    binding: str,
    provider_allowed: bool,
    resolved_execution_class: str,
    production_class: str,
    evaluation_class: str,
    namespace: Mapping[str, Any],
) -> dict[str, Any]:
    """Keep signed model readiness separate from temporary runtime authority."""

    readiness_item: Mapping[str, Any] = {}
    evidence_source: Mapping[str, Any] = {}
    readiness_check_failed = False
    readiness_reader = namespace.get("exact_binding_readiness_from_environment")
    try:
        if not callable(readiness_reader):
            raise RuntimeError("readiness reader unavailable")
        raw_item, raw_source = readiness_reader(binding)
        if isinstance(raw_item, Mapping):
            readiness_item = raw_item
        if isinstance(raw_source, Mapping):
            evidence_source = raw_source
    except Exception:  # noqa: BLE001 - malformed evidence must stay fail-closed
        readiness_check_failed = True

    signed_ready = readiness_item.get("production_ready") is True
    signed_status = (
        "production_ready"
        if signed_ready
        else "readiness_check_failed"
        if readiness_check_failed
        else "not_production_ready"
    )
    ack_reader = namespace.get("_readiness_operator_ack_bindings")
    operator_acknowledged = bool(
        callable(ack_reader) and binding in ack_reader()
    )
    production_authorized = bool(
        provider_allowed and resolved_execution_class == production_class
    )
    evaluation_authorized = bool(
        provider_allowed and resolved_execution_class == evaluation_class
    )
    if production_authorized:
        authorization_source = (
            "signed_evidence"
            if signed_ready
            else "operator_ack"
            if operator_acknowledged
            else "runtime_policy"
        )
        authorization_status = "operationally_authorized"
    elif evaluation_authorized:
        authorization_source = "local_evaluation"
        authorization_status = "evaluation_only_authorized"
    else:
        authorization_source = "blocked"
        authorization_status = "blocked"
    authorization_temporary = bool(
        production_authorized
        and authorization_source == "operator_ack"
        and not signed_ready
    )
    compatibility_status = (
        "production_ready"
        if signed_ready
        else "operationally_authorized_temporary"
        if authorization_temporary
        else "evaluation_only_not_production_ready"
        if evaluation_authorized
        else "not_ready"
    )
    return {
        # Compatibility field: it may describe a temporary operational
        # authorization, but only signed evidence may use production_ready.
        "model_readiness_status": compatibility_status,
        "signed_model_production_ready": signed_ready,
        "signed_model_readiness_status": signed_status,
        "signed_model_readiness_claim_status": str(
            readiness_item.get("claim_status") or "descriptive_only"
        ),
        "signed_model_readiness_evidence_source": str(
            evidence_source.get("source") or "not_configured"
        ),
        "operator_acknowledged": operator_acknowledged,
        "operationally_authorized": production_authorized,
        "operational_authorization_status": authorization_status,
        "operational_authorization_source": authorization_source,
        "operational_authorization_temporary": authorization_temporary,
    }


def _provider_preflight_item(
    *,
    index: int,
    candidate: tuple[str, str, bool],
    safe_prompt: str,
    max_output_tokens: int,
    cost_scope: str,
    monthly_budget: int,
    monthly_remaining: int,
    forced_offline: bool,
    skip_monthly_env_check: bool,
    require_runtime_verified: bool,
    require_configured: bool,
    resolved_execution_class: str,
    production_class: str,
    evaluation_class: str,
    namespace: Mapping[str, Any],
) -> dict[str, Any]:
    provider, model_id, explicit_model = candidate
    binding = namespace["_resolve_gateway_binding"](provider, model_id)
    blocker = namespace["_binding_call_blocker"](
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
    provider_allowed = bool(
        plan.get("allowed")
        and configured
        and env_allowed
        and not forced_offline
        and not blocker
    )
    readiness = _candidate_readiness_and_authorization(
        binding=binding.binding,
        provider_allowed=provider_allowed,
        resolved_execution_class=resolved_execution_class,
        production_class=production_class,
        evaluation_class=evaluation_class,
        namespace=namespace,
    )
    runtime_gate = namespace["_build_runtime_error"](
        "model_binding_blocked" if blocker else "ready",
        detail=blocker,
        provider=provider,
        model=binding.model_id,
        binding=binding.binding,
        failure_reasons=[blocker] if blocker else [],
    )
    return {
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
        "binding_gate_reason": blocker or "ready",
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
        **readiness,
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


def _preflight_reason(
    *,
    providers: list[dict[str, Any]],
    provider_calls_allowed: bool,
    forced_offline: bool,
    skip_monthly_env_check: bool,
    monthly_budget: int,
) -> str:
    if forced_offline:
        return "force_offline"
    if not (bool(skip_monthly_env_check) or monthly_budget > 0):
        return "monthly_env_budget_disabled"
    if not providers:
        return "no_provider_candidates"
    if not any(item.get("binding_gate_reason") == "ready" for item in providers):
        return "model_binding_blocked"
    if not any(bool(item.get("configured")) for item in providers):
        return "providers_not_configured"
    if not any(bool(item.get("budget_allowed")) for item in providers):
        return "budget_hard_stop"
    if not provider_calls_allowed:
        return "provider_calls_blocked"
    return "provider_calls_allowed"


def _preflight_provider_gate(
    *,
    providers: list[dict[str, Any]],
    provider_calls_allowed: bool,
    reason: str,
    namespace: Mapping[str, Any],
) -> dict[str, Any]:
    blocked = [
        item["runtime_gate"]
        for item in providers
        if not bool(item.get("provider_calls_allowed"))
        and isinstance(item.get("runtime_gate"), dict)
        and item["runtime_gate"].get("code") != "ready"
    ]
    if provider_calls_allowed:
        return namespace["_build_runtime_error"]("ready")
    if blocked:
        return namespace["_summarise_runtime_errors"](
            blocked, fallback_status=reason
        )
    return namespace["_build_runtime_error"](reason)


def _preflight_response(
    *,
    providers: list[dict[str, Any]],
    selected_provider: dict[str, Any],
    provider_calls_allowed: bool,
    provider_gate: dict[str, Any],
    reason: str,
    purpose: str,
    cost_scope: str,
    max_output_tokens: int,
    safe_prompt: str,
    monthly_budget: int,
    monthly_remaining: int,
    forced_offline: bool,
    model_fallbacks: Iterable[tuple[str, str]] | None,
    resolved_execution_class: str,
    evaluation_class: str,
    production_class: str,
    namespace: Mapping[str, Any],
) -> dict[str, Any]:
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
        "model_level_fallback": bool(model_fallbacks),
        "execution_class": resolved_execution_class,
        "evaluation_only": resolved_execution_class == evaluation_class,
        "production_authorized": bool(
            provider_calls_allowed and resolved_execution_class == production_class
        ),
        "claim_status": "descriptive_only",
        "model_readiness_status": str(selected_provider.get("model_readiness_status") or "not_ready"),
        "signed_model_production_ready": bool(selected_provider.get("signed_model_production_ready")),
        "signed_model_readiness_status": str(selected_provider.get("signed_model_readiness_status") or "not_production_ready"),
        "signed_model_readiness_claim_status": str(selected_provider.get("signed_model_readiness_claim_status") or "descriptive_only"),
        "signed_model_readiness_evidence_source": str(selected_provider.get("signed_model_readiness_evidence_source") or "not_configured"),
        "operator_acknowledged": bool(selected_provider.get("operator_acknowledged")),
        "operationally_authorized": bool(selected_provider.get("operationally_authorized")),
        "operational_authorization_status": str(selected_provider.get("operational_authorization_status") or "blocked"),
        "operational_authorization_source": str(selected_provider.get("operational_authorization_source") or "blocked"),
        "operational_authorization_temporary": bool(selected_provider.get("operational_authorization_temporary")),
        "require_runtime_verified": resolved_execution_class == production_class,
        "providers": providers,
    }


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
    candidates = namespace["_ordered_model_candidates"](
        preferred_provider, model_override, model_fallbacks
    )
    providers = [
        _provider_preflight_item(
            index=index,
            candidate=candidate,
            safe_prompt=safe_prompt,
            max_output_tokens=max_output_tokens,
            cost_scope=cost_scope,
            monthly_budget=monthly_budget,
            monthly_remaining=monthly_remaining,
            forced_offline=forced_offline,
            skip_monthly_env_check=skip_monthly_env_check,
            require_runtime_verified=require_runtime_verified,
            require_configured=require_configured,
            resolved_execution_class=resolved_execution_class,
            production_class=production_class,
            evaluation_class=evaluation_class,
            namespace=namespace,
        )
        for index, candidate in enumerate(candidates)
    ]
    provider_calls_allowed = any(bool(item.get("provider_calls_allowed")) for item in providers)
    selected_provider = next(
        (item for item in providers if bool(item.get("provider_calls_allowed"))),
        providers[0] if providers else {},
    )
    reason = _preflight_reason(
        providers=providers,
        provider_calls_allowed=provider_calls_allowed,
        forced_offline=forced_offline,
        skip_monthly_env_check=skip_monthly_env_check,
        monthly_budget=monthly_budget,
    )
    provider_gate = _preflight_provider_gate(
        providers=providers,
        provider_calls_allowed=provider_calls_allowed,
        reason=reason,
        namespace=namespace,
    )
    return _preflight_response(
        providers=providers,
        selected_provider=selected_provider,
        provider_calls_allowed=provider_calls_allowed,
        provider_gate=provider_gate,
        reason=reason,
        purpose=purpose,
        cost_scope=cost_scope,
        max_output_tokens=max_output_tokens,
        safe_prompt=safe_prompt,
        monthly_budget=monthly_budget,
        monthly_remaining=monthly_remaining,
        forced_offline=forced_offline,
        model_fallbacks=model_fallbacks,
        resolved_execution_class=resolved_execution_class,
        evaluation_class=evaluation_class,
        production_class=production_class,
        namespace=namespace,
    )
