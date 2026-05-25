"""Control-window summarizers for Via learning and rollout decisions."""
from __future__ import annotations

from app.services.memory.via_learning_common import *

def _summarize_retrieval_evidence(evidence_rows: list[dict[str, Any]]) -> dict[str, Any]:
    evidence_rows = list(evidence_rows or [])
    if not evidence_rows:
        return {
            "evidence_count": 0,
            "avg_top_score": 0.0,
            "avg_score": 0.0,
            "avg_score_spread": 0.0,
            "source_mix": {},
            "retrieval_modes": {},
            "rerank_rate": 0.0,
            "score_drift": "stable",
        }
    source_mix = Counter()
    retrieval_modes = Counter(str(item.get("retrieval_mode") or "unknown") for item in evidence_rows)
    top_scores = [float(item.get("top_score") or 0.0) for item in evidence_rows]
    avg_scores = [float(item.get("avg_score") or 0.0) for item in evidence_rows]
    spreads = [float(item.get("score_spread") or 0.0) for item in evidence_rows]
    rerank_count = sum(1 for item in evidence_rows if bool(item.get("rerank_applied")))
    for item in evidence_rows:
        for source in list(item.get("selected_sources") or []):
            key = str(source or "").strip()
            if key:
                source_mix[key] += 1
    avg_score = mean(avg_scores) if avg_scores else 0.0
    score_drift = "stable"
    if avg_score < 0.34:
        score_drift = "low_confidence"
    elif (mean(spreads) if spreads else 0.0) > 0.42:
        score_drift = "high_spread"
    return {
        "evidence_count": len(evidence_rows),
        "avg_top_score": round(mean(top_scores), 4) if top_scores else 0.0,
        "avg_score": round(avg_score, 4),
        "avg_score_spread": round(mean(spreads), 4) if spreads else 0.0,
        "source_mix": dict(source_mix.most_common()),
        "retrieval_modes": dict(retrieval_modes.most_common()),
        "rerank_rate": round(rerank_count / max(1, len(evidence_rows)), 4),
        "score_drift": score_drift,
        "recent": evidence_rows[:16],
    }


def _summarize_routing_learner_stats(rows: list[dict[str, Any]]) -> dict[str, Any]:
    rows = list(rows or [])
    if not rows:
        return {"provider_count": 0, "bucket_count": 0, "providers": {}, "buckets": {}, "recent": []}
    provider_rollup: defaultdict[str, dict[str, float]] = defaultdict(lambda: {"exposure": 0.0, "success": 0.0, "reward": 0.0, "guard": 0.0})
    bucket_rollup: defaultdict[str, dict[str, float]] = defaultdict(lambda: {"exposure": 0.0, "reward": 0.0})
    for item in rows:
        provider = str(item.get("provider") or "unknown")
        bucket = str(item.get("bucket_key") or "unknown")
        provider_rollup[provider]["exposure"] += float(item.get("exposure_count") or 0)
        provider_rollup[provider]["success"] += float(item.get("success_count") or 0)
        provider_rollup[provider]["reward"] += float(item.get("reward_sum") or 0.0)
        provider_rollup[provider]["guard"] += float(item.get("guard_fail_count") or 0)
        bucket_rollup[bucket]["exposure"] += float(item.get("exposure_count") or 0)
        bucket_rollup[bucket]["reward"] += float(item.get("reward_sum") or 0.0)
    return {
        "provider_count": len(provider_rollup),
        "bucket_count": len(bucket_rollup),
        "providers": {
            key: {
                "exposure_count": int(value["exposure"]),
                "success_rate": round(value["success"] / max(1.0, value["exposure"]), 4),
                "avg_reward": round(value["reward"] / max(1.0, value["exposure"]), 4),
                "guard_fail_rate": round(value["guard"] / max(1.0, value["exposure"]), 4),
            }
            for key, value in provider_rollup.items()
        },
        "buckets": {
            key: {
                "exposure_count": int(value["exposure"]),
                "avg_reward": round(value["reward"] / max(1.0, value["exposure"]), 4),
            }
            for key, value in bucket_rollup.items()
        },
        "recent": rows[:16],
    }


def _apply_retention_decay(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    now = datetime.now(timezone.utc)
    items: list[dict[str, Any]] = []
    for item in list(rows or []):
        row = dict(item)
        last_hit = _parse_timestamp(row.get("last_hit_at") or row.get("last_promoted_at") or "")
        age_days = (now - last_hit).days if last_hit else 0
        decay_state = "fresh"
        status = str(row.get("status") or "active")
        if age_days >= 45:
            decay_state = "inactive"
            status = "inactive"
        elif age_days >= 21:
            decay_state = "decaying"
        row["age_days"] = age_days
        row["decay_state"] = decay_state
        row["status"] = status
        items.append(row)
    return items


def _summarize_memory_retention(rows: list[dict[str, Any]]) -> dict[str, Any]:
    rows = _apply_retention_decay(rows)
    if not rows:
        return {"tracked": 0, "active": 0, "decaying": 0, "inactive": 0, "tiers": {}, "recent": []}
    tiers = Counter(str(item.get("memory_tier") or "unknown") for item in rows)
    decay = Counter(str(item.get("decay_state") or "fresh") for item in rows)
    avg_reward = mean([float(item.get("cumulative_reward") or 0.0) for item in rows]) if rows else 0.0
    confirmed = sum(int(item.get("confirmed_hits") or 0) for item in rows)
    reinforcements = sum(int(item.get("reinforcement_count") or 0) for item in rows)
    return {
        "tracked": len(rows),
        "active": int(decay.get("fresh", 0)),
        "decaying": int(decay.get("decaying", 0)),
        "inactive": int(decay.get("inactive", 0)),
        "tiers": dict(tiers.most_common()),
        "avg_cumulative_reward": round(avg_reward, 4),
        "confirmed_hits": confirmed,
        "reinforcement_count": reinforcements,
        "recent": rows[:16],
    }


def _summarize_control_window(
    decisions: list[dict[str, Any]],
    outcomes: list[dict[str, Any]],
    *,
    reward_traces: list[dict[str, Any]] | None = None,
    retrieval_evidence: list[dict[str, Any]] | None = None,
    routing_stats: list[dict[str, Any]] | None = None,
    memory_retention: list[dict[str, Any]] | None = None,
    window_days: int,
) -> dict[str, Any]:
    reward_traces = list(reward_traces or [])
    retrieval_evidence = list(retrieval_evidence or [])
    routing_stats = list(routing_stats or [])
    memory_retention = list(memory_retention or [])
    decision_by_id = {
        str(item.get("decision_id") or ""): item
        for item in decisions
        if str(item.get("decision_id") or "").strip()
    }
    decision_types = Counter(str(item.get("decision_type") or "unknown") for item in decisions)
    triggers = Counter(str(item.get("trigger_type") or "unknown") for item in decisions)
    policies = Counter(str(item.get("policy_key") or "unknown") for item in decisions)
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
    shadow_targets = Counter(
        str((item.get("chosen_action") or {}).get("target") or item.get("trigger_type") or "")
        for item in decisions
        if str(item.get("decision_type") or "") == "shadow_eval"
    )
    shadow_changed = [
        item for item in decisions
        if str(item.get("decision_type") or "") == "shadow_eval"
        and bool((item.get("chosen_action") or {}).get("would_change"))
    ]
    rewards = [float(item.get("reward_score") or 0.0) for item in outcomes]
    accepted = [item for item in outcomes if bool(item.get("accepted"))]
    clicked = [item for item in outcomes if bool(item.get("clicked_product"))]
    added_to_cart = [item for item in outcomes if bool(item.get("added_to_cart"))]
    purchased = [item for item in outcomes if bool(item.get("purchased"))]
    abuse = [item for item in outcomes if int(item.get("abuse_flag") or 0) > 0]
    trace_types = Counter(str(item.get("event_type") or "unknown") for item in reward_traces)
    trace_value_total = sum(float(item.get("event_value") or 0.0) for item in reward_traces)
    trace_commission_total = sum(
        float((item.get("event_payload") or {}).get("estimated_commission") or 0.0)
        for item in reward_traces
    )
    fallback_count = reply_modes.get("fallback", 0)
    ai_dialogue_count = reply_modes.get("ai_dialogue", 0)
    fast_brain_count = reply_modes.get("fast_brain", 0)
    retrieval_rows = [item for item in decisions if str(item.get("decision_type") or "") == "retrieval_plan"]
    memory_required_rows = [
        item for item in decisions
        if str(item.get("decision_type") or "") == "intent_route"
        and bool((item.get("chosen_action") or {}).get("needs_memory"))
    ]
    vector_hit_rows = [
        item for item in retrieval_rows
        if str((item.get("chosen_action") or {}).get("plan") or "") == "vector_memory"
    ]
    model_choices = [item for item in decisions if str(item.get("decision_type") or "") == "model_choice"]
    retrieval_summary = _summarize_retrieval_evidence(retrieval_evidence)
    routing_summary = _summarize_routing_learner_stats(routing_stats)
    memory_summary = _summarize_memory_retention(memory_retention)

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

    return {
        "window_days": int(window_days),
        "metrics": {
            "decision_count": len(decisions),
            "outcome_count": len(outcomes),
            "accepted_count": len(accepted),
            "accepted_rate": round(len(accepted) / max(1, len(outcomes)), 4),
            "clicked_product_rate": round(len(clicked) / max(1, len(outcomes)), 4),
            "add_to_cart_rate": round(len(added_to_cart) / max(1, len(outcomes)), 4),
            "purchase_rate": round(len(purchased) / max(1, len(outcomes)), 4),
            "abuse_rate": round(len(abuse) / max(1, len(outcomes)), 4),
            "avg_reward": round(mean(rewards), 4) if rewards else 0.0,
            "avg_latency_ms": round(mean([float(item.get("latency_ms") or 0.0) for item in decisions]), 2) if decisions else 0.0,
            "estimated_cost_total": round(sum(float(item.get("cost_estimate") or 0.0) for item in decisions), 6),
            "fallback_count": int(fallback_count),
            "ai_dialogue_count": int(ai_dialogue_count),
            "fast_brain_count": int(fast_brain_count),
            "memory_required_count": len(memory_required_rows),
            "vector_hit_count": len(vector_hit_rows),
            "vector_hit_rate": round(len(vector_hit_rows) / max(1, len(memory_required_rows)), 4),
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
        },
        "decision_types": dict(decision_types.most_common()),
        "triggers": dict(triggers.most_common(12)),
        "policies": dict(policies.most_common()),
        "reply_modes": {key: value for key, value in reply_modes.items() if key},
        "providers": {key: value for key, value in providers.items() if key},
        "promotion_tiers": {key: value for key, value in promotion_tiers.items() if key},
        "shadow_targets": {key: value for key, value in shadow_targets.items() if key},
        "reward_trace_types": dict(trace_types.most_common()),
        "retrieval_evidence": retrieval_summary,
        "routing_learner": routing_summary,
        "memory_retention": memory_summary,
        "recent_decisions": decisions[:24],
        "recent_outcomes": enriched_outcomes[:24],
        "recent_reward_traces": reward_traces[:24],
    }


def _summarize_shadow_rollout_readiness(
    decisions: list[dict[str, Any]],
    outcomes: list[dict[str, Any]],
    reward_traces: list[dict[str, Any]],
    staged_versions: list[dict[str, Any]],
    *,
    window_days: int,
) -> list[dict[str, Any]]:
    outcomes_by_session: defaultdict[str, list[dict[str, Any]]] = defaultdict(list)
    traces_by_session: defaultdict[str, list[dict[str, Any]]] = defaultdict(list)
    for item in outcomes:
        key = str(item.get("session_key") or "").strip()
        if key:
            outcomes_by_session[key].append(item)
    for item in reward_traces:
        key = str(item.get("session_key") or "").strip()
        if key:
            traces_by_session[key].append(item)

    readiness: list[dict[str, Any]] = []
    for version in staged_versions:
        policy_key = str(version.get("policy_key") or "").strip()
        rules = dict(_P1_SHADOW_ROLLOUT_RULES.get(policy_key) or {})
        if not rules:
            continue
        version_key = str(version.get("version_key") or "").strip()
        target = str(rules.get("target") or "").strip()
        shadow_rows = [
            item
            for item in decisions
            if str(item.get("decision_type") or "") == "shadow_eval"
            and str((item.get("chosen_action") or {}).get("shadow_version_key") or "") == version_key
            and str((item.get("chosen_action") or {}).get("target") or item.get("trigger_type") or "") == target
        ]
        session_keys = {
            str(item.get("session_key") or "").strip()
            for item in shadow_rows
            if str(item.get("session_key") or "").strip()
        }
        linked_outcomes = [item for session_key in session_keys for item in outcomes_by_session.get(session_key, [])]
        linked_traces = [item for session_key in session_keys for item in traces_by_session.get(session_key, [])]
        trace_types = Counter(str(item.get("event_type") or "unknown") for item in linked_traces)
        changed_count = sum(1 for item in shadow_rows if bool((item.get("chosen_action") or {}).get("would_change")))
        accepted_count = sum(1 for item in linked_outcomes if bool(item.get("accepted")))
        abuse_count = sum(1 for item in linked_outcomes if int(item.get("abuse_flag") or 0) > 0)
        reward_values = [float(item.get("reward_score") or 0.0) for item in linked_outcomes]
        shadow_count = len(shadow_rows)
        accepted_rate = round(accepted_count / max(1, len(linked_outcomes)), 4)
        change_rate = round(changed_count / max(1, shadow_count), 4)
        abuse_rate = round(abuse_count / max(1, len(linked_outcomes)), 4)
        avg_reward = round(mean(reward_values), 4) if reward_values else 0.0
        positive_signals = int(trace_types.get("compare", 0) + trace_types.get("add_to_cart", 0) + trace_types.get("purchase", 0) + trace_types.get("affiliate_order", 0))

        reasons: list[str] = []
        status = "hold"
        recommended_rollout_percentage = 0.0
        if shadow_count < int(rules.get("min_shadow_samples") or 1):
            reasons.append("need_more_shadow_samples")
        if not linked_outcomes:
            reasons.append("missing_outcome_feedback")
        if change_rate < float(rules.get("min_change_rate") or 0.0):
            reasons.append("shadow_delta_too_small")
        if linked_outcomes and accepted_rate < float(rules.get("min_acceptance_rate") or 0.0):
            reasons.append("acceptance_below_threshold")
        if linked_outcomes and avg_reward < float(rules.get("min_avg_reward") or 0.0):
            reasons.append("reward_below_threshold")
        if linked_outcomes and abuse_rate > float(rules.get("max_abuse_rate") or 1.0):
            reasons.append("abuse_rate_too_high")
        if shadow_count >= int(rules.get("min_shadow_samples") or 1) and not reasons:
            status = "eligible_for_limited_rollout"
            recommended_rollout_percentage = 0.05
            if shadow_count >= int(rules.get("promote_shadow_samples") or shadow_count) and positive_signals >= 2 and avg_reward >= float(rules.get("min_avg_reward") or 0.0) + 0.05:
                recommended_rollout_percentage = 0.15
                status = "eligible_for_broader_limited_rollout"
        readiness.append(
            {
                "policy_key": policy_key,
                "target": target,
                "version_key": version_key,
                "version_label": str(version.get("version_label") or ""),
                "status": status,
                "recommended_action": "promote_limited" if recommended_rollout_percentage > 0 else "hold",
                "recommended_rollout_percentage": recommended_rollout_percentage,
                "reasons": reasons,
                "metrics": {
                    "shadow_eval_count": shadow_count,
                    "shadow_change_count": changed_count,
                    "shadow_change_rate": change_rate,
                    "session_count": len(session_keys),
                    "accepted_rate": accepted_rate,
                    "avg_reward": avg_reward,
                    "abuse_rate": abuse_rate,
                    "compare_count": int(trace_types.get("compare", 0)),
                    "add_to_cart_count": int(trace_types.get("add_to_cart", 0)),
                    "purchase_count": int(trace_types.get("purchase", 0)),
                    "affiliate_order_count": int(trace_types.get("affiliate_order", 0)),
                    "positive_signal_count": positive_signals,
                },
                "thresholds": rules,
                "window_days": int(window_days or 14),
            }
        )
    return readiness


def _next_rollout_percentage(current: float) -> float:
    for step in _P1_LIVE_ROLLOUT_STEPS:
        if step > float(current or 0.0) + 1e-9:
            return step
    return 0.0


def _required_live_samples(current_rollout_percentage: float) -> int:
    pct = float(current_rollout_percentage or 0.0)
    if pct <= 0.05:
        return 6
    if pct <= 0.15:
        return 10
    if pct <= 0.30:
        return 16
    if pct <= 0.60:
        return 24
    return 32


def _summarize_live_rollout_health(
    decisions: list[dict[str, Any]],
    outcomes: list[dict[str, Any]],
    reward_traces: list[dict[str, Any]],
    live_versions: list[dict[str, Any]],
    *,
    window_days: int,
    version_history: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    version_history = list(version_history or [])
    outcomes_by_session: defaultdict[str, list[dict[str, Any]]] = defaultdict(list)
    traces_by_session: defaultdict[str, list[dict[str, Any]]] = defaultdict(list)
    for item in outcomes:
        key = str(item.get("session_key") or "").strip()
        if key:
            outcomes_by_session[key].append(item)
    for item in reward_traces:
        key = str(item.get("session_key") or "").strip()
        if key:
            traces_by_session[key].append(item)

    health_rows: list[dict[str, Any]] = []
    for version in live_versions:
        policy_key = str(version.get("policy_key") or "").strip()
        rules = dict(_P1_SHADOW_ROLLOUT_RULES.get(policy_key) or {})
        if not rules:
            continue
        config = dict(version.get("config") or {})
        rollout_mode = str(config.get("rollout_mode") or "").strip().lower()
        if rollout_mode != "limited":
            continue
        target = str(rules.get("target") or "").strip()
        version_label = str(version.get("version_label") or "")
        current_rollout_percentage = float(config.get("rollout_percentage") or 0.0)
        next_rollout_percentage = _next_rollout_percentage(current_rollout_percentage)
        target_rows = [
            item
            for item in decisions
            if str(item.get("decision_type") or "") == target
            and str(item.get("policy_key") or "") == policy_key
            and str(item.get("policy_version") or "") == version_label
        ]
        session_keys = {
            str(item.get("session_key") or "").strip()
            for item in target_rows
            if str(item.get("session_key") or "").strip()
        }
        linked_outcomes = [item for session_key in session_keys for item in outcomes_by_session.get(session_key, [])]
        linked_traces = [item for session_key in session_keys for item in traces_by_session.get(session_key, [])]
        trace_types = Counter(str(item.get("event_type") or "unknown") for item in linked_traces)
        accepted_count = sum(1 for item in linked_outcomes if bool(item.get("accepted")))
        abuse_count = sum(1 for item in linked_outcomes if int(item.get("abuse_flag") or 0) > 0)
        reward_values = [float(item.get("reward_score") or 0.0) for item in linked_outcomes]
        accepted_rate = round(accepted_count / max(1, len(linked_outcomes)), 4)
        abuse_rate = round(abuse_count / max(1, len(linked_outcomes)), 4)
        avg_reward = round(mean(reward_values), 4) if reward_values else 0.0
        positive_signals = int(trace_types.get("compare", 0) + trace_types.get("add_to_cart", 0) + trace_types.get("purchase", 0) + trace_types.get("affiliate_order", 0))
        min_live_samples = _required_live_samples(current_rollout_percentage)
        reasons: list[str] = []
        status = "hold"
        if not linked_outcomes:
            reasons.append("missing_live_outcomes")
        if len(target_rows) < min_live_samples:
            reasons.append("need_more_live_samples")
        if linked_outcomes and accepted_rate < float(rules.get("min_acceptance_rate") or 0.0):
            reasons.append("acceptance_below_threshold")
        if linked_outcomes and avg_reward < float(rules.get("min_avg_reward") or 0.0):
            reasons.append("reward_below_threshold")
        if linked_outcomes and abuse_rate > float(rules.get("max_abuse_rate") or 1.0):
            reasons.append("abuse_rate_too_high")
        if current_rollout_percentage >= 0.15 and positive_signals <= 0:
            reasons.append("missing_positive_signals")
        previous_versions = [
            item for item in version_history
            if str(item.get("policy_key") or "") == policy_key
            and str(item.get("version_key") or "") != str(version.get("version_key") or "")
            and str(item.get("status") or "").lower() in {"superseded", "live"}
        ]
        previous_version = previous_versions[0] if previous_versions else {}
        previous_label = str(previous_version.get("version_label") or "")
        previous_rows = [
            item for item in decisions
            if str(item.get("decision_type") or "") == target
            and str(item.get("policy_key") or "") == policy_key
            and str(item.get("policy_version") or "") == previous_label
        ]
        previous_sessions = {
            str(item.get("session_key") or "").strip()
            for item in previous_rows
            if str(item.get("session_key") or "").strip()
        }
        previous_outcomes = [item for session_key in previous_sessions for item in outcomes_by_session.get(session_key, [])]
        prev_accept = round(sum(1 for item in previous_outcomes if bool(item.get("accepted"))) / max(1, len(previous_outcomes)), 4) if previous_outcomes else 0.0
        prev_reward = round(mean([float(item.get("reward_score") or 0.0) for item in previous_outcomes]), 4) if previous_outcomes else 0.0
        half_cutoff = datetime.now(timezone.utc) - timedelta(days=max(1, int(window_days or 14) // 2))
        current_recent = [item for item in linked_outcomes if (_parse_timestamp(item.get("created_at") or "") or datetime.now(timezone.utc)) >= half_cutoff]
        previous_recent = [item for item in previous_outcomes if (_parse_timestamp(item.get("created_at") or "") or datetime.now(timezone.utc)) >= half_cutoff]
        current_recent_accept = round(sum(1 for item in current_recent if bool(item.get("accepted"))) / max(1, len(current_recent)), 4) if current_recent else 0.0
        current_recent_reward = round(mean([float(item.get("reward_score") or 0.0) for item in current_recent]), 4) if current_recent else 0.0
        prev_recent_accept = round(sum(1 for item in previous_recent if bool(item.get("accepted"))) / max(1, len(previous_recent)), 4) if previous_recent else 0.0
        prev_recent_reward = round(mean([float(item.get("reward_score") or 0.0) for item in previous_recent]), 4) if previous_recent else 0.0
        rollback_candidate = bool(previous_outcomes) and (
            accepted_rate + 0.05 < prev_accept and avg_reward + 0.04 < prev_reward
            and current_recent_accept + 0.05 < prev_recent_accept
            and current_recent_reward + 0.04 < prev_recent_reward
        )
        if rollback_candidate:
            status = "rollback_candidate"
            reasons.append("underperforming_previous_stable")
        elif next_rollout_percentage <= 0 and not reasons:
            status = "at_full_rollout"
        elif not reasons:
            status = "healthy"
        health_rows.append(
            {
                "policy_key": policy_key,
                "target": target,
                "version_key": str(version.get("version_key") or ""),
                "version_label": version_label,
                "status": status,
                "current_rollout_percentage": current_rollout_percentage,
                "next_rollout_percentage": next_rollout_percentage,
                "recommended_action": "rollback_review" if status == "rollback_candidate" else ("advance_rollout" if status == "healthy" and next_rollout_percentage > 0 else "hold"),
                "reasons": reasons,
                "metrics": {
                    "live_decision_count": len(target_rows),
                    "session_count": len(session_keys),
                    "accepted_rate": accepted_rate,
                    "avg_reward": avg_reward,
                    "abuse_rate": abuse_rate,
                    "compare_count": int(trace_types.get("compare", 0)),
                    "add_to_cart_count": int(trace_types.get("add_to_cart", 0)),
                    "purchase_count": int(trace_types.get("purchase", 0)),
                    "affiliate_order_count": int(trace_types.get("affiliate_order", 0)),
                    "positive_signal_count": positive_signals,
                    "previous_accepted_rate": prev_accept,
                    "previous_avg_reward": prev_reward,
                    "current_recent_accepted_rate": current_recent_accept,
                    "current_recent_avg_reward": current_recent_reward,
                    "previous_recent_accepted_rate": prev_recent_accept,
                    "previous_recent_avg_reward": prev_recent_reward,
                },
                "thresholds": {
                    **rules,
                    "min_live_samples": min_live_samples,
                },
                "window_days": int(window_days or 14),
            }
        )
    return health_rows


__all__ = [name for name in globals() if not name.startswith("__")]
