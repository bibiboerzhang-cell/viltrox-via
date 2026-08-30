"""Leaf computations for Via shadow-rollout readiness summaries."""
from __future__ import annotations

from collections import Counter, defaultdict
from statistics import mean
from typing import Any, Mapping


def _index_by_session(
    rows: list[dict[str, Any]],
) -> defaultdict[str, list[dict[str, Any]]]:
    indexed: defaultdict[str, list[dict[str, Any]]] = defaultdict(list)
    for item in rows:
        key = str(item.get("session_key") or "").strip()
        if key:
            indexed[key].append(item)
    return indexed


def _shadow_rows(
    decisions: list[dict[str, Any]],
    *,
    version_key: str,
    target: str,
) -> list[dict[str, Any]]:
    return [
        item
        for item in decisions
        if str(item.get("decision_type") or "") == "shadow_eval"
        and str((item.get("chosen_action") or {}).get("shadow_version_key") or "")
        == version_key
        and str(
            (item.get("chosen_action") or {}).get("target")
            or item.get("trigger_type")
            or ""
        )
        == target
    ]


def _session_keys(rows: list[dict[str, Any]]) -> set[str]:
    return {
        str(item.get("session_key") or "").strip()
        for item in rows
        if str(item.get("session_key") or "").strip()
    }


def _linked_rows(
    session_keys: set[str],
    rows_by_session: Mapping[str, list[dict[str, Any]]],
) -> list[dict[str, Any]]:
    return [
        item
        for session_key in session_keys
        for item in rows_by_session.get(session_key, [])
    ]


def _rates(
    *,
    shadow_rows: list[dict[str, Any]],
    linked_outcomes: list[dict[str, Any]],
) -> tuple[int, int, float, float, float, float]:
    changed_count = sum(
        1
        for item in shadow_rows
        if bool((item.get("chosen_action") or {}).get("would_change"))
    )
    accepted_count = sum(
        1 for item in linked_outcomes if bool(item.get("accepted"))
    )
    abuse_count = sum(
        1 for item in linked_outcomes if int(item.get("abuse_flag") or 0) > 0
    )
    reward_values = [
        float(item.get("reward_score") or 0.0) for item in linked_outcomes
    ]
    accepted_rate = round(accepted_count / max(1, len(linked_outcomes)), 4)
    change_rate = round(changed_count / max(1, len(shadow_rows)), 4)
    abuse_rate = round(abuse_count / max(1, len(linked_outcomes)), 4)
    avg_reward = round(mean(reward_values), 4) if reward_values else 0.0
    return (
        changed_count,
        accepted_count,
        accepted_rate,
        change_rate,
        abuse_rate,
        avg_reward,
    )


def _readiness_reasons(
    *,
    shadow_count: int,
    linked_outcomes: list[dict[str, Any]],
    change_rate: float,
    accepted_rate: float,
    avg_reward: float,
    abuse_rate: float,
    rules: Mapping[str, Any],
) -> list[str]:
    reasons: list[str] = []
    if shadow_count < int(rules.get("min_shadow_samples") or 1):
        reasons.append("need_more_shadow_samples")
    if not linked_outcomes:
        reasons.append("missing_outcome_feedback")
    if change_rate < float(rules.get("min_change_rate") or 0.0):
        reasons.append("shadow_delta_too_small")
    if linked_outcomes and accepted_rate < float(
        rules.get("min_acceptance_rate") or 0.0
    ):
        reasons.append("acceptance_below_threshold")
    if linked_outcomes and avg_reward < float(rules.get("min_avg_reward") or 0.0):
        reasons.append("reward_below_threshold")
    if linked_outcomes and abuse_rate > float(rules.get("max_abuse_rate") or 1.0):
        reasons.append("abuse_rate_too_high")
    return reasons


def _promotion_state(
    *,
    shadow_count: int,
    positive_signals: int,
    avg_reward: float,
    reasons: list[str],
    rules: Mapping[str, Any],
) -> tuple[str, float]:
    status = "hold"
    recommended_rollout_percentage = 0.0
    if shadow_count >= int(rules.get("min_shadow_samples") or 1) and not reasons:
        status = "eligible_for_limited_rollout"
        recommended_rollout_percentage = 0.05
        if (
            shadow_count
            >= int(rules.get("promote_shadow_samples") or shadow_count)
            and positive_signals >= 2
            and avg_reward >= float(rules.get("min_avg_reward") or 0.0) + 0.05
        ):
            recommended_rollout_percentage = 0.15
            status = "eligible_for_broader_limited_rollout"
    return status, recommended_rollout_percentage


def _readiness_row(
    *,
    version: dict[str, Any],
    rules: dict[str, Any],
    policy_key: str,
    target: str,
    status: str,
    recommended_rollout_percentage: float,
    reasons: list[str],
    shadow_count: int,
    changed_count: int,
    change_rate: float,
    session_count: int,
    accepted_rate: float,
    avg_reward: float,
    abuse_rate: float,
    trace_types: Counter[str],
    positive_signals: int,
    window_days: int,
) -> dict[str, Any]:
    return {
        "policy_key": policy_key,
        "target": target,
        "version_key": str(version.get("version_key") or ""),
        "version_label": str(version.get("version_label") or ""),
        "status": status,
        "recommended_action": (
            "promote_limited" if recommended_rollout_percentage > 0 else "hold"
        ),
        "recommended_rollout_percentage": recommended_rollout_percentage,
        "reasons": reasons,
        "metrics": {
            "shadow_eval_count": shadow_count,
            "shadow_change_count": changed_count,
            "shadow_change_rate": change_rate,
            "session_count": session_count,
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


def summarize_shadow_rollout_readiness(
    decisions: list[dict[str, Any]],
    outcomes: list[dict[str, Any]],
    reward_traces: list[dict[str, Any]],
    staged_versions: list[dict[str, Any]],
    *,
    window_days: int,
    rules_by_policy: Mapping[str, Mapping[str, Any]],
) -> list[dict[str, Any]]:
    outcomes_by_session = _index_by_session(outcomes)
    traces_by_session = _index_by_session(reward_traces)
    readiness: list[dict[str, Any]] = []
    for version in staged_versions:
        policy_key = str(version.get("policy_key") or "").strip()
        rules = dict(rules_by_policy.get(policy_key) or {})
        if not rules:
            continue
        version_key = str(version.get("version_key") or "").strip()
        target = str(rules.get("target") or "").strip()
        rows = _shadow_rows(
            decisions,
            version_key=version_key,
            target=target,
        )
        session_keys = _session_keys(rows)
        linked_outcomes = _linked_rows(session_keys, outcomes_by_session)
        linked_traces = _linked_rows(session_keys, traces_by_session)
        trace_types = Counter(
            str(item.get("event_type") or "unknown") for item in linked_traces
        )
        (
            changed_count,
            _accepted_count,
            accepted_rate,
            change_rate,
            abuse_rate,
            avg_reward,
        ) = _rates(shadow_rows=rows, linked_outcomes=linked_outcomes)
        positive_signals = int(
            trace_types.get("compare", 0)
            + trace_types.get("add_to_cart", 0)
            + trace_types.get("purchase", 0)
            + trace_types.get("affiliate_order", 0)
        )
        reasons = _readiness_reasons(
            shadow_count=len(rows),
            linked_outcomes=linked_outcomes,
            change_rate=change_rate,
            accepted_rate=accepted_rate,
            avg_reward=avg_reward,
            abuse_rate=abuse_rate,
            rules=rules,
        )
        status, recommended_percentage = _promotion_state(
            shadow_count=len(rows),
            positive_signals=positive_signals,
            avg_reward=avg_reward,
            reasons=reasons,
            rules=rules,
        )
        readiness.append(
            _readiness_row(
                version=version,
                rules=rules,
                policy_key=policy_key,
                target=target,
                status=status,
                recommended_rollout_percentage=recommended_percentage,
                reasons=reasons,
                shadow_count=len(rows),
                changed_count=changed_count,
                change_rate=change_rate,
                session_count=len(session_keys),
                accepted_rate=accepted_rate,
                avg_reward=avg_reward,
                abuse_rate=abuse_rate,
                trace_types=trace_types,
                positive_signals=positive_signals,
                window_days=window_days,
            )
        )
    return readiness


__all__ = ["summarize_shadow_rollout_readiness"]
