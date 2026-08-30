"""Leaf computations for Via control-window summaries.

This module has no dependency on the public ``via_learning_summaries`` facade.
The facade supplies the three nested summarizers so its historical monkeypatch
surface and invocation order remain intact.
"""
from __future__ import annotations

from collections import Counter
from statistics import mean
from typing import Any, Callable


SummaryCallback = Callable[[list[dict[str, Any]]], dict[str, Any]]


def _decision_identity_facets(
    decisions: list[dict[str, Any]],
) -> tuple[dict[str, dict[str, Any]], Counter[str], Counter[str], Counter[str]]:
    decision_by_id = {
        str(item.get("decision_id") or ""): item
        for item in decisions
        if str(item.get("decision_id") or "").strip()
    }
    decision_types = Counter(
        str(item.get("decision_type") or "unknown") for item in decisions
    )
    triggers = Counter(str(item.get("trigger_type") or "unknown") for item in decisions)
    policies = Counter(str(item.get("policy_key") or "unknown") for item in decisions)
    return decision_by_id, decision_types, triggers, policies


def _decision_action_facets(
    decisions: list[dict[str, Any]],
) -> tuple[Counter[str], Counter[str], Counter[str]]:
    reply_modes = Counter(
        str((item.get("chosen_action") or {}).get("mode") or "")
        for item in decisions
        if str(item.get("decision_type") or "") == "reply_mode"
    )
    providers = Counter(
        str((item.get("chosen_action") or {}).get("provider") or "")
        for item in decisions
        if str(item.get("decision_type") or "") in {"reply_mode", "model_choice"}
    )
    promotion_tiers = Counter(
        str((item.get("chosen_action") or {}).get("tier") or "")
        for item in decisions
        if str(item.get("decision_type") or "") == "memory_promotion"
    )
    return reply_modes, providers, promotion_tiers


def _shadow_decision_facets(
    decisions: list[dict[str, Any]],
) -> tuple[Counter[str], list[dict[str, Any]]]:
    shadow_targets = Counter(
        str(
            (item.get("chosen_action") or {}).get("target")
            or item.get("trigger_type")
            or ""
        )
        for item in decisions
        if str(item.get("decision_type") or "") == "shadow_eval"
    )
    shadow_changed = [
        item
        for item in decisions
        if str(item.get("decision_type") or "") == "shadow_eval"
        and bool((item.get("chosen_action") or {}).get("would_change"))
    ]
    return shadow_targets, shadow_changed


def _decision_facets(
    decisions: list[dict[str, Any]],
) -> tuple[
    dict[str, dict[str, Any]],
    Counter[str],
    Counter[str],
    Counter[str],
    Counter[str],
    Counter[str],
    Counter[str],
    Counter[str],
    list[dict[str, Any]],
]:
    decision_by_id, decision_types, triggers, policies = _decision_identity_facets(
        decisions
    )
    reply_modes, providers, promotion_tiers = _decision_action_facets(decisions)
    shadow_targets, shadow_changed = _shadow_decision_facets(decisions)
    return (
        decision_by_id,
        decision_types,
        triggers,
        policies,
        reply_modes,
        providers,
        promotion_tiers,
        shadow_targets,
        shadow_changed,
    )


def _outcome_facets(
    outcomes: list[dict[str, Any]],
) -> tuple[
    list[float],
    list[dict[str, Any]],
    list[dict[str, Any]],
    list[dict[str, Any]],
    list[dict[str, Any]],
    list[dict[str, Any]],
]:
    rewards = [float(item.get("reward_score") or 0.0) for item in outcomes]
    accepted = [item for item in outcomes if bool(item.get("accepted"))]
    clicked = [item for item in outcomes if bool(item.get("clicked_product"))]
    added_to_cart = [item for item in outcomes if bool(item.get("added_to_cart"))]
    purchased = [item for item in outcomes if bool(item.get("purchased"))]
    abuse = [item for item in outcomes if int(item.get("abuse_flag") or 0) > 0]
    return rewards, accepted, clicked, added_to_cart, purchased, abuse


def _reward_trace_facets(
    reward_traces: list[dict[str, Any]],
) -> tuple[Counter[str], float, float]:
    trace_types = Counter(
        str(item.get("event_type") or "unknown") for item in reward_traces
    )
    trace_value_total = sum(
        float(item.get("event_value") or 0.0) for item in reward_traces
    )
    trace_commission_total = sum(
        float((item.get("event_payload") or {}).get("estimated_commission") or 0.0)
        for item in reward_traces
    )
    return trace_types, trace_value_total, trace_commission_total


def _learning_decision_rows(
    decisions: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    retrieval_rows = [
        item
        for item in decisions
        if str(item.get("decision_type") or "") == "retrieval_plan"
    ]
    memory_required_rows = [
        item
        for item in decisions
        if str(item.get("decision_type") or "") == "intent_route"
        and bool((item.get("chosen_action") or {}).get("needs_memory"))
    ]
    vector_hit_rows = [
        item
        for item in retrieval_rows
        if str((item.get("chosen_action") or {}).get("plan") or "")
        == "vector_memory"
    ]
    model_choices = [
        item
        for item in decisions
        if str(item.get("decision_type") or "") == "model_choice"
    ]
    return retrieval_rows, memory_required_rows, vector_hit_rows, model_choices


def _enrich_outcomes(
    outcomes: list[dict[str, Any]],
    decision_by_id: dict[str, dict[str, Any]],
) -> list[dict[str, Any]]:
    enriched_outcomes: list[dict[str, Any]] = []
    for item in outcomes[:]:
        linked = decision_by_id.get(str(item.get("decision_id") or ""))
        enriched_outcomes.append(
            {
                **item,
                "decision_type": str((linked or {}).get("decision_type") or ""),
                "policy_key": str((linked or {}).get("policy_key") or ""),
                "trigger_type": str((linked or {}).get("trigger_type") or ""),
            }
        )
    return enriched_outcomes


def _control_metrics(
    *,
    decisions: list[dict[str, Any]],
    outcomes: list[dict[str, Any]],
    reward_traces: list[dict[str, Any]],
    rewards: list[float],
    accepted: list[dict[str, Any]],
    clicked: list[dict[str, Any]],
    added_to_cart: list[dict[str, Any]],
    purchased: list[dict[str, Any]],
    abuse: list[dict[str, Any]],
    reply_modes: Counter[str],
    shadow_targets: Counter[str],
    shadow_changed: list[dict[str, Any]],
    trace_types: Counter[str],
    trace_value_total: float,
    trace_commission_total: float,
    memory_required_rows: list[dict[str, Any]],
    vector_hit_rows: list[dict[str, Any]],
    model_choices: list[dict[str, Any]],
    retrieval_summary: dict[str, Any],
    routing_summary: dict[str, Any],
    memory_summary: dict[str, Any],
) -> dict[str, Any]:
    return {
        "decision_count": len(decisions),
        "outcome_count": len(outcomes),
        "accepted_count": len(accepted),
        "accepted_rate": round(len(accepted) / max(1, len(outcomes)), 4),
        "clicked_product_rate": round(len(clicked) / max(1, len(outcomes)), 4),
        "add_to_cart_rate": round(len(added_to_cart) / max(1, len(outcomes)), 4),
        "purchase_rate": round(len(purchased) / max(1, len(outcomes)), 4),
        "abuse_rate": round(len(abuse) / max(1, len(outcomes)), 4),
        "avg_reward": round(mean(rewards), 4) if rewards else 0.0,
        "avg_latency_ms": round(
            mean([float(item.get("latency_ms") or 0.0) for item in decisions]), 2
        )
        if decisions
        else 0.0,
        "estimated_cost_total": round(
            sum(float(item.get("cost_estimate") or 0.0) for item in decisions), 6
        ),
        "fallback_count": int(reply_modes.get("fallback", 0)),
        "ai_dialogue_count": int(reply_modes.get("ai_dialogue", 0)),
        "fast_brain_count": int(reply_modes.get("fast_brain", 0)),
        "memory_required_count": len(memory_required_rows),
        "vector_hit_count": len(vector_hit_rows),
        "vector_hit_rate": round(
            len(vector_hit_rows) / max(1, len(memory_required_rows)), 4
        ),
        "model_choice_count": len(model_choices),
        "shadow_eval_count": sum(shadow_targets.values()),
        "shadow_change_count": len(shadow_changed),
        "reward_trace_count": len(reward_traces),
        "compare_trace_count": int(trace_types.get("compare", 0)),
        "cart_trace_count": int(trace_types.get("add_to_cart", 0)),
        "purchase_trace_count": int(trace_types.get("purchase", 0)),
        "affiliate_order_trace_count": int(trace_types.get("affiliate_order", 0)),
        "reward_trace_value_total": round(trace_value_total, 2),
        "reward_trace_commission_total": round(trace_commission_total, 2),
        "retrieval_evidence_count": int(retrieval_summary.get("evidence_count") or 0),
        "routing_provider_count": int(routing_summary.get("provider_count") or 0),
        "memory_retention_tracked": int(memory_summary.get("tracked") or 0),
    }


def summarize_control_window(
    decisions: list[dict[str, Any]],
    outcomes: list[dict[str, Any]],
    *,
    reward_traces: list[dict[str, Any]] | None,
    retrieval_evidence: list[dict[str, Any]] | None,
    routing_stats: list[dict[str, Any]] | None,
    memory_retention: list[dict[str, Any]] | None,
    window_days: int,
    summarize_retrieval: SummaryCallback,
    summarize_routing: SummaryCallback,
    summarize_memory: SummaryCallback,
) -> dict[str, Any]:
    reward_traces = list(reward_traces or [])
    retrieval_evidence = list(retrieval_evidence or [])
    routing_stats = list(routing_stats or [])
    memory_retention = list(memory_retention or [])
    (
        decision_by_id,
        decision_types,
        triggers,
        policies,
        reply_modes,
        providers,
        promotion_tiers,
        shadow_targets,
        shadow_changed,
    ) = _decision_facets(decisions)
    rewards, accepted, clicked, added_to_cart, purchased, abuse = _outcome_facets(
        outcomes
    )
    trace_types, trace_value_total, trace_commission_total = _reward_trace_facets(
        reward_traces
    )
    (
        _retrieval_rows,
        memory_required_rows,
        vector_hit_rows,
        model_choices,
    ) = _learning_decision_rows(decisions)
    retrieval_summary = summarize_retrieval(retrieval_evidence)
    routing_summary = summarize_routing(routing_stats)
    memory_summary = summarize_memory(memory_retention)
    enriched_outcomes = _enrich_outcomes(outcomes, decision_by_id)
    metrics = _control_metrics(
        decisions=decisions,
        outcomes=outcomes,
        reward_traces=reward_traces,
        rewards=rewards,
        accepted=accepted,
        clicked=clicked,
        added_to_cart=added_to_cart,
        purchased=purchased,
        abuse=abuse,
        reply_modes=reply_modes,
        shadow_targets=shadow_targets,
        shadow_changed=shadow_changed,
        trace_types=trace_types,
        trace_value_total=trace_value_total,
        trace_commission_total=trace_commission_total,
        memory_required_rows=memory_required_rows,
        vector_hit_rows=vector_hit_rows,
        model_choices=model_choices,
        retrieval_summary=retrieval_summary,
        routing_summary=routing_summary,
        memory_summary=memory_summary,
    )
    return {
        "window_days": int(window_days),
        "metrics": metrics,
        "decision_types": dict(decision_types.most_common()),
        "triggers": dict(triggers.most_common(12)),
        "policies": dict(policies.most_common()),
        "reply_modes": {key: value for key, value in reply_modes.items() if key},
        "providers": {key: value for key, value in providers.items() if key},
        "promotion_tiers": {
            key: value for key, value in promotion_tiers.items() if key
        },
        "shadow_targets": {
            key: value for key, value in shadow_targets.items() if key
        },
        "reward_trace_types": dict(trace_types.most_common()),
        "retrieval_evidence": retrieval_summary,
        "routing_learner": routing_summary,
        "memory_retention": memory_summary,
        "recent_decisions": decisions[:24],
        "recent_outcomes": enriched_outcomes[:24],
        "recent_reward_traces": reward_traces[:24],
    }


__all__ = ["summarize_control_window"]
