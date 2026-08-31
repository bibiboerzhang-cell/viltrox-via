"""
services/via/shadow_learning.py — Staged policy shadow evaluation helpers
"""
from __future__ import annotations

from typing import Any

from app.services.via.model_router import preview_via_routes
from app.services.via.shadow_learning_model_choice import (
    select_shadow_model_choice,
    shadow_model_choice_changed,
    shadow_provider_preferences,
)


def evaluate_shadow_retrieval_plan(
    *,
    route_info: dict[str, Any] | None,
    live_policy: dict[str, Any] | None,
    shadow_policy: dict[str, Any] | None,
    vector_refs: list[dict[str, Any]] | None = None,
    bundle_memory_count: int = 0,
    live_evidence: dict[str, Any] | None = None,
) -> dict[str, Any]:
    route_info = dict(route_info or {})
    live_policy = dict(live_policy or {})
    shadow_policy = dict(shadow_policy or {})
    vector_refs = list(vector_refs or [])
    live_evidence = dict(live_evidence or {})
    if not shadow_policy:
        return {}
    staged_mode = str(shadow_policy.get("retrieval_mode") or "").strip() or "bundle_memory_only"
    fallback_order = list(shadow_policy.get("fallback_order") or ["bundle_memory", "vector_memory", "seed_knowledge"])
    live_mode = "vector_memory" if vector_refs else "bundle_memory_only"
    if route_info.get("needs_memory"):
        if staged_mode == "hybrid_vector_seed":
            staged_plan = "hybrid_vector_seed"
        elif staged_mode:
            staged_plan = staged_mode
        else:
            staged_plan = live_mode
    else:
        staged_plan = "bundle_memory_only"
    return {
        "target": "retrieval_plan",
        "policy_key": str(shadow_policy.get("policy_key") or live_policy.get("policy_key") or ""),
        "live_policy_version": str(live_policy.get("policy_version") or ""),
        "shadow_policy_version": str(shadow_policy.get("policy_version") or ""),
        "shadow_version_key": str(shadow_policy.get("staged_version_key") or ""),
        "live_plan": live_mode,
        "shadow_plan": staged_plan,
        "would_change": staged_plan != live_mode,
        "retrieval_mode": staged_mode,
        "fallback_order": fallback_order,
        "vector_ref_count": len(vector_refs),
        "bundle_memory_count": int(bundle_memory_count),
        "vector_hit_threshold": float(shadow_policy.get("vector_hit_threshold") or 0.0),
        "candidate_sources": live_evidence.get("candidate_sources") or [],
        "selected_sources": live_evidence.get("selected_sources") or [],
        "top_score": float(live_evidence.get("top_score") or 0.0),
        "avg_score": float(live_evidence.get("avg_score") or 0.0),
        "score_spread": float(live_evidence.get("score_spread") or 0.0),
        "rerank_applied": bool(live_evidence.get("rerank_applied")),
    }


def evaluate_shadow_model_choice(
    *,
    route_info: dict[str, Any] | None,
    live_policy: dict[str, Any] | None,
    shadow_policy: dict[str, Any] | None,
    model_plan: dict[str, Any] | None,
) -> dict[str, Any]:
    route_info = dict(route_info or {})
    live_policy = dict(live_policy or {})
    shadow_policy = dict(shadow_policy or {})
    model_plan = dict(model_plan or {})
    if not shadow_policy:
        return {}
    dialogue = dict(model_plan.get("dialogue") or {})
    execution_mode = str(shadow_policy.get("execution_mode") or "").strip() or "single_preferred"
    providers = shadow_provider_preferences(shadow_policy, dialogue)
    preview = preview_via_routes("dialogue", preferred_override=providers or None, limit=max(1, len(providers) or 1))
    shadow_provider, shadow_model, shadow_strategy = select_shadow_model_choice(
        preview,
        dialogue,
        execution_mode=execution_mode,
        use_deep_reasoning=bool(route_info.get("use_deep_reasoning")),
    )
    return {
        "target": "model_choice",
        "policy_key": str(shadow_policy.get("policy_key") or live_policy.get("policy_key") or ""),
        "live_policy_version": str(live_policy.get("policy_version") or ""),
        "shadow_policy_version": str(shadow_policy.get("policy_version") or ""),
        "shadow_version_key": str(shadow_policy.get("staged_version_key") or ""),
        "live_primary_provider": str(dialogue.get("primary_provider") or ""),
        "live_primary_model": str(dialogue.get("primary_model") or ""),
        "shadow_primary_provider": shadow_provider,
        "shadow_primary_model": shadow_model,
        "shadow_strategy": shadow_strategy,
        "execution_mode": execution_mode,
        "exploration_ratio": float(shadow_policy.get("exploration_ratio") or 0.0),
        "shadow_routes": preview,
        "would_change": shadow_model_choice_changed(
            dialogue,
            shadow_provider=shadow_provider,
            shadow_model=shadow_model,
            shadow_strategy=shadow_strategy,
        ),
    }


def evaluate_shadow_memory_promotion(
    *,
    live_policy: dict[str, Any] | None,
    shadow_policy: dict[str, Any] | None,
    promotions: list[dict[str, Any]] | None,
    learning_signals: dict[str, Any] | None,
    reward_score: float,
    retention_stats: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    live_policy = dict(live_policy or {})
    shadow_policy = dict(shadow_policy or {})
    promotions = list(promotions or [])
    learning_signals = dict(learning_signals or {})
    retention_stats = list(retention_stats or [])
    if not shadow_policy:
        return {}
    live_tiers = [str(item.get("tier") or "") for item in promotions if str(item.get("tier") or "").strip()]
    suggested_extra: list[str] = []
    threshold = int(shadow_policy.get("semantic_confirmed_hit_threshold") or 3)
    has_learning_signal = bool(learning_signals.get("traits") or learning_signals.get("keywords"))
    confirmed_hits = max((int(item.get("confirmed_hits") or 0) for item in retention_stats), default=0)
    if threshold <= 2 and "semantic" not in live_tiers and has_learning_signal and reward_score >= 0.5:
        suggested_extra.append("semantic")
    if confirmed_hits >= max(1, threshold - 1) and "semantic" not in live_tiers and has_learning_signal and reward_score >= 0.46:
        suggested_extra.append("semantic")
    if bool(shadow_policy.get("track_semantic_retention")) and "procedural" not in live_tiers and reward_score >= 0.62:
        suggested_extra.append("procedural")
    return {
        "target": "memory_promotion",
        "policy_key": str(shadow_policy.get("policy_key") or live_policy.get("policy_key") or ""),
        "live_policy_version": str(live_policy.get("policy_version") or ""),
        "shadow_policy_version": str(shadow_policy.get("policy_version") or ""),
        "shadow_version_key": str(shadow_policy.get("staged_version_key") or ""),
        "live_tiers": live_tiers,
        "shadow_suggested_tiers": suggested_extra,
        "semantic_confirmed_hit_threshold": threshold,
        "track_semantic_retention": bool(shadow_policy.get("track_semantic_retention")),
        "confirmed_hits": confirmed_hits,
        "would_change": bool(suggested_extra),
    }
