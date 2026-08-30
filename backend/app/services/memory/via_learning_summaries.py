"""Control-window summarizers for Via learning and rollout decisions."""
from __future__ import annotations

from app.services.memory.via_learning_common import *
from app.services.memory import via_learning_control_summary as __control_summary
from app.services.memory import via_learning_live_rollout_summary as __live_rollout_summary
from app.services.memory import via_learning_shadow_rollout_summary as __shadow_rollout_summary

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
    return __control_summary.summarize_control_window(
        decisions,
        outcomes,
        reward_traces=reward_traces,
        retrieval_evidence=retrieval_evidence,
        routing_stats=routing_stats,
        memory_retention=memory_retention,
        window_days=window_days,
        summarize_retrieval=_summarize_retrieval_evidence,
        summarize_routing=_summarize_routing_learner_stats,
        summarize_memory=_summarize_memory_retention,
    )


def _summarize_shadow_rollout_readiness(
    decisions: list[dict[str, Any]],
    outcomes: list[dict[str, Any]],
    reward_traces: list[dict[str, Any]],
    staged_versions: list[dict[str, Any]],
    *,
    window_days: int,
) -> list[dict[str, Any]]:
    return __shadow_rollout_summary.summarize_shadow_rollout_readiness(
        decisions,
        outcomes,
        reward_traces,
        staged_versions,
        window_days=window_days,
        rules_by_policy=_P1_SHADOW_ROLLOUT_RULES,
    )


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
    return __live_rollout_summary.summarize_live_rollout_health(
        decisions,
        outcomes,
        reward_traces,
        live_versions,
        window_days=window_days,
        version_history=version_history,
        rules_by_policy=_P1_SHADOW_ROLLOUT_RULES,
        next_rollout_percentage=_next_rollout_percentage,
        required_live_samples=_required_live_samples,
        parse_timestamp=_parse_timestamp,
        now_utc=lambda: datetime.now(timezone.utc),
    )


__all__ = [name for name in globals() if not name.startswith("__")]
