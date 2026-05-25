"""Offline evaluator and debug snapshots for Via learning."""
from __future__ import annotations

from app.services.memory.via_learning_common import *
from app.services.memory.via_learning_affiliate import _filter_recent_control_rows, _sync_affiliate_order_reward_traces
from app.services.memory.via_learning_rollout import _persist_rollout_alerts
from app.services.memory.via_learning_summaries import (
    _summarize_control_window,
    _summarize_live_rollout_health,
    _summarize_shadow_rollout_readiness,
)

def _build_policy_proposals(control_summary: dict[str, Any]) -> list[dict[str, Any]]:
    metrics = dict(control_summary.get("metrics") or {})
    proposals: list[dict[str, Any]] = []
    window_days = int(control_summary.get("window_days") or 14)
    reply_modes = dict(control_summary.get("reply_modes") or {})
    providers = dict(control_summary.get("providers") or {})
    promotion_tiers = dict(control_summary.get("promotion_tiers") or {})
    retrieval_evidence = dict(control_summary.get("retrieval_evidence") or {})
    routing_learner = dict(control_summary.get("routing_learner") or {})
    memory_retention = dict(control_summary.get("memory_retention") or {})

    if int(metrics.get("memory_required_count") or 0) >= 8 and float(metrics.get("vector_hit_rate") or 0.0) < 0.45:
        proposals.append(
            {
                "proposal_key": f"retrieval-{window_days}-{int(metrics.get('memory_required_count') or 0)}",
                "proposal_type": "retrieval_tuning",
                "policy_key": "via.retrieval.selective",
                "status": "proposed",
                "confidence": 0.78,
                "impact_score": 0.72,
                "evidence": {
                    "memory_required_count": int(metrics.get("memory_required_count") or 0),
                    "vector_hit_rate": float(metrics.get("vector_hit_rate") or 0.0),
                    "retrieval_evidence": {
                        "avg_score": float(retrieval_evidence.get("avg_score") or 0.0),
                        "score_drift": str(retrieval_evidence.get("score_drift") or "stable"),
                        "source_mix": retrieval_evidence.get("source_mix") or {},
                    },
                },
                "proposal": {
                    "summary": "Memory-required turns are outrunning useful vector hits. Add hybrid retrieval and trigger-based fallback ordering.",
                    "actions": [
                        "prioritize hybrid retrieval when memory_required and vector_hit_rate < 0.45",
                        "log retrieval score spread to support later rerank learning",
                    ],
                    "candidate_config": {
                        "policy_version": f"{VIA_EVALUATOR_VERSION}.retrieval.hybrid",
                        "retrieval_mode": "hybrid_vector_seed",
                        "vector_hit_threshold": 0.45,
                        "fallback_order": ["bundle_memory", "vector_memory", "seed_knowledge"],
                    },
                },
                "window_days": window_days,
            }
        )

    if int(metrics.get("model_choice_count") or 0) >= 6 and len([key for key in providers.keys() if key]) <= 1:
        observed_providers = [key for key in providers.keys() if key]
        rollout_providers = observed_providers if len(observed_providers) > 1 else ([observed_providers[0]] if observed_providers else []) + [item for item in ["openai", "gemini", "claude"] if item not in observed_providers]
        proposals.append(
            {
                "proposal_key": f"routing-{window_days}-{int(metrics.get('model_choice_count') or 0)}",
                "proposal_type": "routing_exploration",
                "policy_key": "via.model.route",
                "status": "proposed",
                "confidence": 0.74,
                "impact_score": 0.63,
                "evidence": {
                    "model_choice_count": int(metrics.get("model_choice_count") or 0),
                    "providers": providers,
                    "routing_learner": {
                        "provider_count": int(routing_learner.get("provider_count") or 0),
                        "providers": routing_learner.get("providers") or {},
                    },
                },
                "proposal": {
                    "summary": "Model routing has enough traffic to start exploration. Introduce bandit-style provider sampling before hard-coding one route.",
                    "actions": [
                        "sample secondary provider on 10-15% of eligible dialogue turns",
                        "compare reward_score, latency_ms, and cost_estimate by provider",
                    ],
                    "candidate_config": {
                        "policy_version": f"{VIA_EVALUATOR_VERSION}.routing.explore",
                        "execution_mode": "bandit_explore",
                        "exploration_ratio": 0.12,
                        "providers": rollout_providers[:3] or ["openai", "gemini", "claude"],
                    },
                },
                "window_days": window_days,
            }
        )

    if int(reply_modes.get("fallback") or 0) >= 3:
        proposals.append(
            {
                "proposal_key": f"fallback-{window_days}-{int(reply_modes.get('fallback') or 0)}",
                "proposal_type": "fallback_reduction",
                "policy_key": "via.reply.mode",
                "status": "proposed",
                "confidence": 0.69,
                "impact_score": 0.57,
                "evidence": {
                    "fallback_count": int(reply_modes.get("fallback") or 0),
                    "reply_modes": reply_modes,
                },
                "proposal": {
                    "summary": "Fallback replies are still showing up often enough to justify better graceful degradation.",
                    "actions": [
                        "add deterministic lightweight fallback copy for empty AI returns",
                        "capture provider-level error reasons into decision ledger",
                    ],
                    "candidate_config": {
                        "policy_version": f"{VIA_EVALUATOR_VERSION}.reply.fallback",
                        "fallback_mode": "deterministic_soft_landing",
                        "capture_provider_error_reason": True,
                    },
                },
                "window_days": window_days,
            }
        )

    episodic = int(promotion_tiers.get("episodic") or 0)
    semantic = int(promotion_tiers.get("semantic") or 0)
    if episodic >= 6 and semantic <= max(1, episodic // 4):
        proposals.append(
            {
                "proposal_key": f"memory-{window_days}-{episodic}-{semantic}",
                "proposal_type": "memory_promotion_tuning",
                "policy_key": "via.memory.promotion",
                "status": "proposed",
                "confidence": 0.76,
                "impact_score": 0.68,
                "evidence": {
                    "episodic": episodic,
                    "semantic": semantic,
                    "promotion_tiers": promotion_tiers,
                    "memory_retention": {
                        "tracked": int(memory_retention.get("tracked") or 0),
                        "decaying": int(memory_retention.get("decaying") or 0),
                        "confirmed_hits": int(memory_retention.get("confirmed_hits") or 0),
                    },
                },
                "proposal": {
                    "summary": "The system is storing plenty of episodes but not promoting enough stable traits into semantic memory.",
                    "actions": [
                        "promote repeated traits after two confirmed hits instead of waiting for three",
                        "track semantic retention hit rate in the next evaluation window",
                    ],
                    "candidate_config": {
                        "policy_version": f"{VIA_EVALUATOR_VERSION}.memory.semantic",
                        "semantic_confirmed_hit_threshold": 2,
                        "track_semantic_retention": True,
                    },
                },
                "window_days": window_days,
            }
        )

    if float(metrics.get("abuse_rate") or 0.0) > 0.08:
        proposals.append(
            {
                "proposal_key": f"risk-{window_days}-{int((metrics.get('abuse_rate') or 0)*1000)}",
                "proposal_type": "risk_review",
                "policy_key": "via.guard.policy",
                "status": "proposed",
                "confidence": 0.67,
                "impact_score": 0.61,
                "evidence": {
                    "abuse_rate": float(metrics.get("abuse_rate") or 0.0),
                    "triggers": control_summary.get("triggers") or {},
                },
                "proposal": {
                    "summary": "Guarded traffic is high enough to review sensitive-trigger phrasing and pre-guard education.",
                    "actions": [
                        "cluster top guarded prompts by trigger_type",
                        "add softer public-safe redirect copy for the most frequent guard buckets",
                    ],
                    "candidate_config": {
                        "policy_version": f"{VIA_EVALUATOR_VERSION}.risk.redirect",
                        "guard_copy_mode": "softer_public_redirect",
                        "cluster_guard_buckets": True,
                    },
                },
                "window_days": window_days,
            }
        )

    return proposals


async def run_via_offline_evaluator(window_days: int = 14, limit: int = 300) -> dict[str, Any]:
    reward_trace_sync = await asyncio.to_thread(_sync_affiliate_order_reward_traces, max(160, int(limit) * 2), max(window_days, 21))
    decisions = await asyncio.to_thread(list_recent_via_decisions, max(40, int(limit)))
    outcomes = await asyncio.to_thread(list_recent_via_outcomes, max(40, int(limit)))
    decisions = _filter_recent_control_rows(decisions, window_days)
    outcomes = _filter_recent_control_rows(outcomes, window_days)
    reward_traces = _filter_recent_control_rows(list_recent_via_reward_traces(limit=240), window_days)
    retrieval_evidence = _filter_recent_control_rows(list_recent_via_retrieval_evidence(limit=max(80, int(limit))), window_days)
    routing_stats = _filter_recent_control_rows(list_via_routing_provider_stats(limit=max(40, int(limit))), max(window_days, 21))
    memory_retention = _filter_recent_control_rows(list_via_memory_retention_stats(limit=max(40, int(limit))), max(window_days, 45))
    control_summary = _summarize_control_window(
        decisions,
        outcomes,
        reward_traces=reward_traces,
        retrieval_evidence=retrieval_evidence,
        routing_stats=routing_stats,
        memory_retention=memory_retention,
        window_days=window_days,
    )
    active_versions = await asyncio.to_thread(list_active_via_policy_versions)
    policy_history = await asyncio.to_thread(list_via_policy_version_history, max(120, int(limit) * 2))
    staged_versions = [item for item in active_versions if str(item.get("status") or "").lower() == "staged"]
    live_versions = [item for item in active_versions if str(item.get("status") or "").lower() == "live"]
    shadow_rollout_readiness = _summarize_shadow_rollout_readiness(
        decisions,
        outcomes,
        reward_traces,
        staged_versions,
        window_days=window_days,
    )
    live_rollout_health = _summarize_live_rollout_health(
        decisions,
        outcomes,
        reward_traces,
        live_versions,
        window_days=window_days,
        version_history=policy_history,
    )
    rollout_alerts = await asyncio.to_thread(_persist_rollout_alerts, shadow_rollout_readiness, live_rollout_health)
    proposals = _build_policy_proposals(control_summary)
    stored = {"proposals": 0, "observations": 0, "feedback": 0}

    persisted: list[dict[str, Any]] = []
    for proposal in proposals:
        persisted_item = await asyncio.to_thread(
            upsert_via_policy_proposal,
            proposal_key=str(proposal.get("proposal_key") or ""),
            proposal_type=str(proposal.get("proposal_type") or ""),
            policy_key=str(proposal.get("policy_key") or ""),
            status=str(proposal.get("status") or "proposed"),
            confidence=float(proposal.get("confidence") or 0.0),
            impact_score=float(proposal.get("impact_score") or 0.0),
            evidence=proposal.get("evidence") or {},
            proposal=proposal.get("proposal") or {},
            window_days=int(proposal.get("window_days") or window_days),
            evaluator_version=VIA_EVALUATOR_VERSION,
        )
        persisted.append(persisted_item)
        stored["proposals"] += 1

    await asyncio.to_thread(
        record_market_observation,
        source_platform="via_control",
        subject_type="offline_evaluator",
        subject_key=f"window:{window_days}",
        observation_type="control_window_summary",
        summary=(
            f"Via offline evaluator reviewed {control_summary['metrics']['decision_count']} decisions "
            f"and {control_summary['metrics']['outcome_count']} outcomes across the last {window_days} days."
        ),
        metrics=control_summary.get("metrics") or {},
        evidence={
            "decision_types": control_summary.get("decision_types") or {},
            "reply_modes": control_summary.get("reply_modes") or {},
            "providers": control_summary.get("providers") or {},
            "shadow_targets": control_summary.get("shadow_targets") or {},
            "retrieval_evidence": control_summary.get("retrieval_evidence") or {},
            "routing_learner": control_summary.get("routing_learner") or {},
            "memory_retention": control_summary.get("memory_retention") or {},
            "shadow_rollout_readiness": shadow_rollout_readiness,
            "live_rollout_health": live_rollout_health,
            "rollout_alerts": rollout_alerts[:24],
            "reward_trace_sync": reward_trace_sync,
            "proposal_count": len(persisted),
        },
    )
    stored["observations"] += 1
    await asyncio.to_thread(
        record_feedback_signal,
        source_type="via_control",
        source_id=f"window:{window_days}",
        event_type="offline_evaluator_run",
        actor_role="via",
        payload={
            "metrics": control_summary.get("metrics") or {},
            "proposal_count": len(persisted),
            "evaluator_version": VIA_EVALUATOR_VERSION,
            "reward_trace_sync": reward_trace_sync,
        },
    )
    stored["feedback"] += 1

    return {
        "window_days": window_days,
        "metrics": control_summary.get("metrics") or {},
        "decision_types": control_summary.get("decision_types") or {},
        "triggers": control_summary.get("triggers") or {},
        "reply_modes": control_summary.get("reply_modes") or {},
        "providers": control_summary.get("providers") or {},
        "promotion_tiers": control_summary.get("promotion_tiers") or {},
        "shadow_targets": control_summary.get("shadow_targets") or {},
        "retrieval_evidence": control_summary.get("retrieval_evidence") or {},
        "routing_learner": control_summary.get("routing_learner") or {},
        "memory_retention": control_summary.get("memory_retention") or {},
        "shadow_rollout_readiness": shadow_rollout_readiness,
        "live_rollout_health": live_rollout_health,
        "rollout_alerts": rollout_alerts[: int(limit)],
        "recent_decisions": control_summary.get("recent_decisions") or [],
        "recent_outcomes": control_summary.get("recent_outcomes") or [],
        "proposals": persisted,
        "stored": stored,
        "reward_trace_sync": reward_trace_sync,
        "evaluator_version": VIA_EVALUATOR_VERSION,
    }


async def get_via_control_debug_snapshot(window_days: int = 14, limit: int = 24) -> dict[str, Any]:
    decisions = await asyncio.to_thread(list_recent_via_decisions, max(24, int(limit) * 4))
    outcomes = await asyncio.to_thread(list_recent_via_outcomes, max(24, int(limit) * 4))
    decisions = _filter_recent_control_rows(decisions, window_days)
    outcomes = _filter_recent_control_rows(outcomes, window_days)
    reward_traces = _filter_recent_control_rows(list_recent_via_reward_traces(limit=max(48, limit * 3)), window_days)
    retrieval_evidence = _filter_recent_control_rows(list_recent_via_retrieval_evidence(limit=max(48, limit * 4)), window_days)
    routing_stats = _filter_recent_control_rows(list_via_routing_provider_stats(limit=max(48, limit * 4)), max(window_days, 21))
    memory_retention = _filter_recent_control_rows(list_via_memory_retention_stats(limit=max(48, limit * 4)), max(window_days, 45))
    summary = _summarize_control_window(
        decisions,
        outcomes,
        reward_traces=reward_traces,
        retrieval_evidence=retrieval_evidence,
        routing_stats=routing_stats,
        memory_retention=memory_retention,
        window_days=window_days,
    )
    proposals = await asyncio.to_thread(list_via_policy_proposals, max(12, int(limit)))
    live_policies = await asyncio.to_thread(list_active_via_policy_versions)
    policy_history = await asyncio.to_thread(list_via_policy_version_history, max(24, int(limit) * 2))
    rollout_alerts = await asyncio.to_thread(list_via_rollout_alerts, max(24, int(limit) * 3))
    shadow_rollout_readiness = _summarize_shadow_rollout_readiness(
        decisions,
        outcomes,
        reward_traces,
        [item for item in live_policies if str(item.get("status") or "").lower() == "staged"],
        window_days=window_days,
    )
    live_rollout_health = _summarize_live_rollout_health(
        decisions,
        outcomes,
        reward_traces,
        [item for item in live_policies if str(item.get("status") or "").lower() == "live"],
        window_days=window_days,
        version_history=policy_history,
    )
    return {
        "window_days": window_days,
        "metrics": summary.get("metrics") or {},
        "decision_types": summary.get("decision_types") or {},
        "reply_modes": summary.get("reply_modes") or {},
        "providers": summary.get("providers") or {},
        "promotion_tiers": summary.get("promotion_tiers") or {},
        "triggers": summary.get("triggers") or {},
        "shadow_targets": summary.get("shadow_targets") or {},
        "retrieval_evidence": summary.get("retrieval_evidence") or {},
        "routing_learner": summary.get("routing_learner") or {},
        "memory_retention": summary.get("memory_retention") or {},
        "shadow_rollout_readiness": shadow_rollout_readiness,
        "live_rollout_health": live_rollout_health,
        "rollout_alerts": rollout_alerts[: max(int(limit), 12)],
        "recent_decisions": list(summary.get("recent_decisions") or [])[: int(limit)],
        "recent_outcomes": list(summary.get("recent_outcomes") or [])[: int(limit)],
        "proposals": proposals[: int(limit)],
        "live_policies": live_policies[: int(limit)],
        "policy_history": policy_history[: max(int(limit), 12)],
        "evaluator_version": VIA_EVALUATOR_VERSION,
    }


__all__ = [name for name in globals() if not name.startswith("__")]
