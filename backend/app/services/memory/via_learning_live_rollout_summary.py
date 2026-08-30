"""Leaf computations for Via limited-rollout health summaries."""
from __future__ import annotations

from collections import Counter, defaultdict
from datetime import datetime, timedelta
from statistics import mean
from typing import Any, Callable, Mapping


PercentageCallback = Callable[[float], float]
SampleCallback = Callable[[float], int]
TimestampParser = Callable[[Any], datetime | None]
NowCallback = Callable[[], datetime]


def _index_by_session(
    rows: list[dict[str, Any]],
) -> defaultdict[str, list[dict[str, Any]]]:
    indexed: defaultdict[str, list[dict[str, Any]]] = defaultdict(list)
    for item in rows:
        key = str(item.get("session_key") or "").strip()
        if key:
            indexed[key].append(item)
    return indexed


def _target_rows(
    decisions: list[dict[str, Any]],
    *,
    target: str,
    policy_key: str,
    version_label: str,
) -> list[dict[str, Any]]:
    return [
        item
        for item in decisions
        if str(item.get("decision_type") or "") == target
        and str(item.get("policy_key") or "") == policy_key
        and str(item.get("policy_version") or "") == version_label
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


def _outcome_metrics(rows: list[dict[str, Any]]) -> tuple[float, float, float]:
    accepted_count = sum(1 for item in rows if bool(item.get("accepted")))
    abuse_count = sum(1 for item in rows if int(item.get("abuse_flag") or 0) > 0)
    reward_values = [float(item.get("reward_score") or 0.0) for item in rows]
    accepted_rate = round(accepted_count / max(1, len(rows)), 4)
    abuse_rate = round(abuse_count / max(1, len(rows)), 4)
    avg_reward = round(mean(reward_values), 4) if reward_values else 0.0
    return accepted_rate, abuse_rate, avg_reward


def _trace_metrics(
    rows: list[dict[str, Any]],
) -> tuple[Counter[str], int]:
    trace_types = Counter(str(item.get("event_type") or "unknown") for item in rows)
    positive_signals = int(
        trace_types.get("compare", 0)
        + trace_types.get("add_to_cart", 0)
        + trace_types.get("purchase", 0)
        + trace_types.get("affiliate_order", 0)
    )
    return trace_types, positive_signals


def _hold_reasons(
    *,
    linked_outcomes: list[dict[str, Any]],
    target_row_count: int,
    min_live_samples: int,
    accepted_rate: float,
    avg_reward: float,
    abuse_rate: float,
    current_rollout_percentage: float,
    positive_signals: int,
    rules: Mapping[str, Any],
) -> list[str]:
    reasons: list[str] = []
    if not linked_outcomes:
        reasons.append("missing_live_outcomes")
    if target_row_count < min_live_samples:
        reasons.append("need_more_live_samples")
    if linked_outcomes and accepted_rate < float(
        rules.get("min_acceptance_rate") or 0.0
    ):
        reasons.append("acceptance_below_threshold")
    if linked_outcomes and avg_reward < float(rules.get("min_avg_reward") or 0.0):
        reasons.append("reward_below_threshold")
    if linked_outcomes and abuse_rate > float(rules.get("max_abuse_rate") or 1.0):
        reasons.append("abuse_rate_too_high")
    if current_rollout_percentage >= 0.15 and positive_signals <= 0:
        reasons.append("missing_positive_signals")
    return reasons


def _previous_version(
    version_history: list[dict[str, Any]],
    *,
    policy_key: str,
    current_version_key: str,
) -> dict[str, Any]:
    previous_versions = [
        item
        for item in version_history
        if str(item.get("policy_key") or "") == policy_key
        and str(item.get("version_key") or "") != current_version_key
        and str(item.get("status") or "").lower() in {"superseded", "live"}
    ]
    return previous_versions[0] if previous_versions else {}


def _baseline_outcome_metrics(
    rows: list[dict[str, Any]],
) -> tuple[float, float]:
    previous_accepted_rate = (
        round(
            sum(1 for item in rows if bool(item.get("accepted")))
            / max(1, len(rows)),
            4,
        )
        if rows
        else 0.0
    )
    previous_avg_reward = (
        round(mean([float(item.get("reward_score") or 0.0) for item in rows]), 4)
        if rows
        else 0.0
    )
    return previous_accepted_rate, previous_avg_reward


def _recent_rows(
    rows: list[dict[str, Any]],
    *,
    half_cutoff: datetime,
    parse_timestamp: TimestampParser,
    now_utc: NowCallback,
) -> list[dict[str, Any]]:
    return [
        item
        for item in rows
        if (parse_timestamp(item.get("created_at") or "") or now_utc()) >= half_cutoff
    ]


def _recent_outcome_metrics(rows: list[dict[str, Any]]) -> tuple[float, float]:
    accepted_rate = (
        round(
            sum(1 for item in rows if bool(item.get("accepted")))
            / max(1, len(rows)),
            4,
        )
        if rows
        else 0.0
    )
    avg_reward = (
        round(mean([float(item.get("reward_score") or 0.0) for item in rows]), 4)
        if rows
        else 0.0
    )
    return accepted_rate, avg_reward


def _is_rollback_candidate(
    *,
    previous_outcomes: list[dict[str, Any]],
    accepted_rate: float,
    avg_reward: float,
    previous_accepted_rate: float,
    previous_avg_reward: float,
    current_recent_accepted_rate: float,
    current_recent_avg_reward: float,
    previous_recent_accepted_rate: float,
    previous_recent_avg_reward: float,
) -> bool:
    return bool(previous_outcomes) and (
        accepted_rate + 0.05 < previous_accepted_rate
        and avg_reward + 0.04 < previous_avg_reward
        and current_recent_accepted_rate + 0.05 < previous_recent_accepted_rate
        and current_recent_avg_reward + 0.04 < previous_recent_avg_reward
    )


def _resolve_status(
    *,
    reasons: list[str],
    rollback_candidate: bool,
    next_rollout_percentage: float,
) -> str:
    if rollback_candidate:
        reasons.append("underperforming_previous_stable")
        return "rollback_candidate"
    if next_rollout_percentage <= 0 and not reasons:
        return "at_full_rollout"
    if not reasons:
        return "healthy"
    return "hold"


def _recommended_action(status: str, next_rollout_percentage: float) -> str:
    if status == "rollback_candidate":
        return "rollback_review"
    if status == "healthy" and next_rollout_percentage > 0:
        return "advance_rollout"
    return "hold"


def _health_row(
    *,
    version: dict[str, Any],
    rules: dict[str, Any],
    policy_key: str,
    target: str,
    version_label: str,
    status: str,
    current_rollout_percentage: float,
    next_rollout_percentage: float,
    reasons: list[str],
    target_rows: list[dict[str, Any]],
    session_keys: set[str],
    accepted_rate: float,
    avg_reward: float,
    abuse_rate: float,
    trace_types: Counter[str],
    positive_signals: int,
    previous_accepted_rate: float,
    previous_avg_reward: float,
    current_recent_accepted_rate: float,
    current_recent_avg_reward: float,
    previous_recent_accepted_rate: float,
    previous_recent_avg_reward: float,
    min_live_samples: int,
    window_days: int,
) -> dict[str, Any]:
    return {
        "policy_key": policy_key,
        "target": target,
        "version_key": str(version.get("version_key") or ""),
        "version_label": version_label,
        "status": status,
        "current_rollout_percentage": current_rollout_percentage,
        "next_rollout_percentage": next_rollout_percentage,
        "recommended_action": _recommended_action(status, next_rollout_percentage),
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
            "previous_accepted_rate": previous_accepted_rate,
            "previous_avg_reward": previous_avg_reward,
            "current_recent_accepted_rate": current_recent_accepted_rate,
            "current_recent_avg_reward": current_recent_avg_reward,
            "previous_recent_accepted_rate": previous_recent_accepted_rate,
            "previous_recent_avg_reward": previous_recent_avg_reward,
        },
        "thresholds": {**rules, "min_live_samples": min_live_samples},
        "window_days": int(window_days or 14),
    }


def summarize_live_rollout_health(
    decisions: list[dict[str, Any]],
    outcomes: list[dict[str, Any]],
    reward_traces: list[dict[str, Any]],
    live_versions: list[dict[str, Any]],
    *,
    window_days: int,
    version_history: list[dict[str, Any]] | None,
    rules_by_policy: Mapping[str, Mapping[str, Any]],
    next_rollout_percentage: PercentageCallback,
    required_live_samples: SampleCallback,
    parse_timestamp: TimestampParser,
    now_utc: NowCallback,
) -> list[dict[str, Any]]:
    version_history = list(version_history or [])
    outcomes_by_session = _index_by_session(outcomes)
    traces_by_session = _index_by_session(reward_traces)
    health_rows: list[dict[str, Any]] = []
    for version in live_versions:
        policy_key = str(version.get("policy_key") or "").strip()
        rules = dict(rules_by_policy.get(policy_key) or {})
        if not rules:
            continue
        config = dict(version.get("config") or {})
        rollout_mode = str(config.get("rollout_mode") or "").strip().lower()
        if rollout_mode != "limited":
            continue
        target = str(rules.get("target") or "").strip()
        version_label = str(version.get("version_label") or "")
        current_percentage = float(config.get("rollout_percentage") or 0.0)
        next_percentage = next_rollout_percentage(current_percentage)
        current_rows = _target_rows(
            decisions,
            target=target,
            policy_key=policy_key,
            version_label=version_label,
        )
        current_sessions = _session_keys(current_rows)
        linked_outcomes = _linked_rows(current_sessions, outcomes_by_session)
        linked_traces = _linked_rows(current_sessions, traces_by_session)
        trace_types, positive_signals = _trace_metrics(linked_traces)
        accepted_rate, abuse_rate, avg_reward = _outcome_metrics(linked_outcomes)
        min_live_samples = required_live_samples(current_percentage)
        reasons = _hold_reasons(
            linked_outcomes=linked_outcomes,
            target_row_count=len(current_rows),
            min_live_samples=min_live_samples,
            accepted_rate=accepted_rate,
            avg_reward=avg_reward,
            abuse_rate=abuse_rate,
            current_rollout_percentage=current_percentage,
            positive_signals=positive_signals,
            rules=rules,
        )
        previous = _previous_version(
            version_history,
            policy_key=policy_key,
            current_version_key=str(version.get("version_key") or ""),
        )
        previous_rows = _target_rows(
            decisions,
            target=target,
            policy_key=policy_key,
            version_label=str(previous.get("version_label") or ""),
        )
        previous_outcomes = _linked_rows(
            _session_keys(previous_rows), outcomes_by_session
        )
        previous_accepted_rate, previous_avg_reward = _baseline_outcome_metrics(
            previous_outcomes
        )
        half_cutoff = now_utc() - timedelta(
            days=max(1, int(window_days or 14) // 2)
        )
        current_recent = _recent_rows(
            linked_outcomes,
            half_cutoff=half_cutoff,
            parse_timestamp=parse_timestamp,
            now_utc=now_utc,
        )
        previous_recent = _recent_rows(
            previous_outcomes,
            half_cutoff=half_cutoff,
            parse_timestamp=parse_timestamp,
            now_utc=now_utc,
        )
        current_recent_accepted_rate, current_recent_avg_reward = (
            _recent_outcome_metrics(current_recent)
        )
        previous_recent_accepted_rate, previous_recent_avg_reward = (
            _recent_outcome_metrics(previous_recent)
        )
        rollback_candidate = _is_rollback_candidate(
            previous_outcomes=previous_outcomes,
            accepted_rate=accepted_rate,
            avg_reward=avg_reward,
            previous_accepted_rate=previous_accepted_rate,
            previous_avg_reward=previous_avg_reward,
            current_recent_accepted_rate=current_recent_accepted_rate,
            current_recent_avg_reward=current_recent_avg_reward,
            previous_recent_accepted_rate=previous_recent_accepted_rate,
            previous_recent_avg_reward=previous_recent_avg_reward,
        )
        status = _resolve_status(
            reasons=reasons,
            rollback_candidate=rollback_candidate,
            next_rollout_percentage=next_percentage,
        )
        health_rows.append(
            _health_row(
                version=version,
                rules=rules,
                policy_key=policy_key,
                target=target,
                version_label=version_label,
                status=status,
                current_rollout_percentage=current_percentage,
                next_rollout_percentage=next_percentage,
                reasons=reasons,
                target_rows=current_rows,
                session_keys=current_sessions,
                accepted_rate=accepted_rate,
                avg_reward=avg_reward,
                abuse_rate=abuse_rate,
                trace_types=trace_types,
                positive_signals=positive_signals,
                previous_accepted_rate=previous_accepted_rate,
                previous_avg_reward=previous_avg_reward,
                current_recent_accepted_rate=current_recent_accepted_rate,
                current_recent_avg_reward=current_recent_avg_reward,
                previous_recent_accepted_rate=previous_recent_accepted_rate,
                previous_recent_avg_reward=previous_recent_avg_reward,
                min_live_samples=min_live_samples,
                window_days=window_days,
            )
        )
    return health_rows


__all__ = ["summarize_live_rollout_health"]
