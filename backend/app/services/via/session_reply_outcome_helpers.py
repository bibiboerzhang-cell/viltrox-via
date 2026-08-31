"""Low-complexity outcome side effects for Via session orchestration."""
from __future__ import annotations

from typing import Any, Awaitable, Callable


_NON_ROUTED_PROVIDERS = {
    "product_brain",
    "business_brain",
    "rule_brain",
    "identity",
    "policy",
}


async def record_routing_provider_stat(
    *,
    reply: dict[str, Any],
    primary_outcome: dict[str, Any],
    reply_mode_decision: dict[str, Any],
    route: dict[str, Any],
    surface: str,
    to_thread: Callable[..., Awaitable[Any]],
    upsert_stat: Callable[..., Any],
    routing_bucket_key: Callable[[dict[str, Any], str], str],
) -> None:
    provider = str(reply["payload"].get("provider") or "")
    if not provider or provider in _NON_ROUTED_PROVIDERS:
        return
    accepted = bool(primary_outcome.get("accepted"))
    abuse_flag = int(primary_outcome.get("abuse_flag") or 0)
    await to_thread(
        upsert_stat,
        bucket_key=routing_bucket_key(route, surface),
        provider=provider,
        exposure_increment=1,
        success_increment=1 if accepted and abuse_flag <= 0 else 0,
        reward_delta=float(primary_outcome.get("reward_score") or 0.0),
        guard_fail_increment=1 if abuse_flag > 0 else 0,
        latency_ms=float((reply_mode_decision or {}).get("latency_ms") or 0.0),
        cost_estimate=float((reply_mode_decision or {}).get("cost_estimate") or 0.0),
        last_outcome_at=str(primary_outcome.get("created_at") or ""),
        metrics={
            "intent": route.get("intent") or "",
            "surface": surface,
            "strategy": reply["payload"].get("provider_strategy") or "",
            "policy_version": reply["payload"].get("model") or "",
        },
    )


def reward_trace_target(
    session_key: str,
    reply_mode_decision: dict[str, Any],
    intent_decision: dict[str, Any],
) -> dict[str, str]:
    return {
        "session_key": session_key,
        "decision_id": str(reply_mode_decision.get("decision_id") or intent_decision.get("decision_id") or ""),
        "policy_key": str(reply_mode_decision.get("policy_key") or intent_decision.get("policy_key") or ""),
        "policy_version": str(reply_mode_decision.get("policy_version") or intent_decision.get("policy_version") or ""),
    }


async def record_promotion_controls(
    promotion: dict[str, Any],
    *,
    session_key: str,
    session: dict[str, Any],
    persona: dict[str, Any],
    policy_route: dict[str, Any],
    trigger_snapshot: dict[str, Any],
    context_refs: list[dict[str, Any]],
    reply_outcome: dict[str, Any],
    primary_outcome: dict[str, Any],
    control_source_ref: str,
    decision_records: list[dict[str, Any]],
    outcome_records: list[dict[str, Any]],
    to_thread: Callable[..., Awaitable[Any]],
    upsert_retention: Callable[..., Any],
    get_policy: Callable[..., dict[str, Any]],
    build_candidates: Callable[..., list[dict[str, Any]]],
    insert_decision: Callable[..., dict[str, Any]],
    insert_outcome: Callable[..., dict[str, Any]],
) -> None:
    retention_key = (
        f"retain:{promotion.get('source_ref') or control_source_ref}:"
        f"{promotion.get('fact_key') or promotion.get('memory_kind') or ''}"
    )
    await to_thread(
        upsert_retention,
        retention_key=retention_key,
        user_id=int(session.get("user_id") or 0),
        session_key=session_key,
        memory_tier=str(promotion.get("tier") or ""),
        memory_kind=str(promotion.get("memory_kind") or ""),
        fact_key=str(promotion.get("fact_key") or ""),
        source_ref=str(promotion.get("source_ref") or control_source_ref),
        reinforcement_increment=1,
        reward_delta=float(reply_outcome.get("reward_score") or 0.0),
        last_promoted_at=str(primary_outcome.get("created_at") or ""),
        metrics={
            "reason": promotion.get("reason") or "",
            "persisted_ref_id": int(promotion.get("persisted_ref_id") or 0),
        },
    )
    promotion_policy = get_policy("memory_promotion", route_info=policy_route)
    promotion_decision = await to_thread(
        insert_decision,
        session_key=session_key,
        session_id=int(session.get("id") or 0),
        user_id=int(session.get("user_id") or 0),
        persona_id=int(persona.get("id") or 0),
        decision_type="memory_promotion",
        trigger_type=str(promotion.get("reason") or "learning_signal"),
        trigger_payload={
            "tier": promotion.get("tier") or "",
            "reason": promotion.get("reason") or "",
        },
        state_snapshot=trigger_snapshot.get("state_snapshot") or {},
        candidates=build_candidates("memory_promotion"),
        chosen_action={
            "tier": promotion.get("tier") or "",
            "memory_kind": promotion.get("memory_kind") or "",
            "fact_key": promotion.get("fact_key") or "",
            "persisted_ref_id": int(promotion.get("persisted_ref_id") or 0),
        },
        policy_key=str(promotion_policy.get("policy_key") or ""),
        policy_version=str(promotion_policy.get("policy_version") or ""),
        context_refs=context_refs,
        cost_estimate=0.0,
    )
    decision_records.append(promotion_decision)
    promotion_outcome = await to_thread(
        insert_outcome,
        decision_id=str(promotion_decision.get("decision_id") or ""),
        session_key=session_key,
        accepted=bool(promotion.get("persisted_ref_id")),
        followup_depth=int(reply_outcome.get("followup_depth") or 0),
        rephrase_needed=False,
        clicked_product=False,
        added_to_cart=False,
        purchased=False,
        thumb_feedback=0,
        abuse_flag=0,
        reward_score=float(reply_outcome.get("reward_score") or 0.0),
        outcome_payload={
            "tier": promotion.get("tier") or "",
            "memory_kind": promotion.get("memory_kind") or "",
            "reason": promotion.get("reason") or "",
        },
    )
    outcome_records.append(promotion_outcome)
