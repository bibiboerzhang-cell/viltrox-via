"""
services/via/decision_ledger.py — Decision/outcome helpers for the Via control loop
"""
from __future__ import annotations

from typing import Any


_MODEL_COST_RATES: dict[str, tuple[float, float]] = {
    "gpt-5-nano": (0.00000005, 0.0000004),
    "gpt-5.4-nano": (0.0000002, 0.00000125),
    "gpt-5-mini": (0.00000025, 0.000002),
    "gpt-4o-mini": (0.00000015, 0.0000006),
    "gemini-2.5-flash-lite": (0.0000001, 0.0000004),
    "gemini-2.5-flash": (0.0000003, 0.0000025),
    "claude-haiku-4-5-20251001": (0.000001, 0.000005),
}


def build_context_refs(bundle: dict[str, Any], vector_refs: list[dict[str, Any]] | None = None, limit: int = 10) -> list[str]:
    refs: list[str] = []
    for item in list(vector_refs or []) + list(bundle.get("memory_refs") or []):
        source_ref = str(item.get("source_ref") or item.get("memory_key") or "").strip()
        if source_ref and source_ref not in refs:
            refs.append(source_ref)
        if len(refs) >= max(1, int(limit)):
            break
    return refs


def build_decision_candidates(
    decision_type: str,
    *,
    route_info: dict[str, Any] | None = None,
    guarded: dict[str, Any] | None = None,
    model_plan: dict[str, Any] | None = None,
    vector_refs: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    route_info = dict(route_info or {})
    model_plan = dict(model_plan or {})
    vector_refs = list(vector_refs or [])
    if decision_type == "intent_route":
        return [
            {"intent": "business_support", "brain": "business_fast"},
            {"intent": "product", "brain": "product_fast"},
            {"intent": "memory", "brain": "memory_fast"},
            {"intent": "creative_guidance", "brain": "creative_fast"},
            {"intent": "deep_reasoning", "brain": "deep_reasoning"},
            {"intent": "quick_chat", "brain": "quick_chat"},
        ]
    if decision_type == "retrieval_plan":
        return [
            {"plan": "bundle_memory_only", "vector_refs": 0},
            {"plan": "vector_memory", "vector_refs": len(vector_refs)},
            {"plan": "seed_only", "vector_refs": 0},
        ]
    if decision_type == "reply_mode":
        candidates = [
            {"mode": "policy_guard"},
            {"mode": "fast_brain"},
            {"mode": "ai_dialogue"},
        ]
        if guarded:
            candidates[0]["locked"] = True
        return candidates
    if decision_type == "model_choice":
        dialogue = dict(model_plan.get("dialogue") or {})
        providers = [
            item.strip()
            for item in str(dialogue.get("consulted_providers") or "").split(",")
            if item.strip()
        ]
        models = [
            item.strip()
            for item in str(dialogue.get("consulted_models") or "").split(",")
            if item.strip()
        ]
        return [
            {
                "provider": providers[idx] if idx < len(providers) else "",
                "model": models[idx] if idx < len(models) else "",
            }
            for idx in range(max(len(providers), len(models), 1))
        ]
    if decision_type == "memory_promotion":
        return [
            {"tier": "working"},
            {"tier": "episodic"},
            {"tier": "semantic"},
            {"tier": "procedural"},
            {"tier": "defer"},
        ]
    return []


def estimate_model_cost(
    *,
    model: str,
    provider: str = "",
    input_text: str = "",
    output_text: str = "",
    collab_count: int = 1,
) -> float:
    model_key = str(model or "").strip().lower()
    input_rate, output_rate = _MODEL_COST_RATES.get(model_key, (0.0, 0.0))
    if not input_rate and not output_rate:
        return 0.0
    input_tokens = max(24, len(str(input_text or "")) // 4)
    output_tokens = max(24, len(str(output_text or "")) // 4)
    base = (input_tokens * input_rate) + (output_tokens * output_rate)
    if str(provider or "").strip().lower() == "collab" or collab_count > 1:
        base *= max(1, int(collab_count))
    return round(base, 8)


def summarize_control_loop(
    *,
    trigger_snapshot: dict[str, Any],
    decisions: list[dict[str, Any]],
    outcomes: list[dict[str, Any]],
    promotions: list[dict[str, Any]],
) -> dict[str, Any]:
    policy_versions: dict[str, str] = {}
    for item in decisions:
        decision_type = str(item.get("decision_type") or "").strip()
        policy_version = str(item.get("policy_version") or "").strip()
        if decision_type and policy_version:
            policy_versions[decision_type] = policy_version
    reward_score = 0.0
    if outcomes:
        reward_score = max(float(item.get("reward_score") or 0.0) for item in outcomes)
    return {
        "primary_trigger": trigger_snapshot.get("primary_trigger") or "general_chat",
        "trigger_count": sum(
            len(trigger_snapshot.get(bucket) or [])
            for bucket in ("semantic", "state", "confidence", "business", "risk", "learning")
        ),
        "decision_count": len(decisions),
        "decision_ids": [str(item.get("decision_id") or "") for item in decisions if str(item.get("decision_id") or "").strip()],
        "outcome_ids": [str(item.get("outcome_id") or "") for item in outcomes if str(item.get("outcome_id") or "").strip()],
        "promotion_tiers": [str(item.get("tier") or "") for item in promotions if str(item.get("tier") or "").strip()],
        "reward_score": round(reward_score, 4),
        "policy_versions": policy_versions,
    }
