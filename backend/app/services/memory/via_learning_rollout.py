"""Guarded policy rollout workflows for Via learning."""
from __future__ import annotations

from app.services.memory.via_learning_common import *
from app.services.memory.via_learning_affiliate import _filter_recent_control_rows
from app.services.memory.via_learning_summaries import (
    _summarize_live_rollout_health,
    _summarize_memory_retention,
    _summarize_retrieval_evidence,
    _summarize_routing_learner_stats,
    _summarize_shadow_rollout_readiness,
)

def _persist_rollout_alerts(
    shadow_rows: list[dict[str, Any]],
    live_rows: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    alerts: list[dict[str, Any]] = []
    for item in shadow_rows:
        if str(item.get("status") or "") not in {"hold"}:
            continue
        alerts.append(
            upsert_via_rollout_alert(
                policy_key=str(item.get("policy_key") or ""),
                version_key=str(item.get("version_key") or ""),
                version_label=str(item.get("version_label") or ""),
                alert_type="shadow_hold",
                severity="medium",
                recommendation=str(item.get("recommended_action") or "hold"),
                reason_text=", ".join(list(item.get("reasons") or [])) or "shadow_not_ready",
                metrics=item.get("metrics") or {},
                observed_at=datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            )
        )
    for item in live_rows:
        status = str(item.get("status") or "")
        if status not in {"hold", "rollback_candidate"}:
            continue
        alerts.append(
            upsert_via_rollout_alert(
                policy_key=str(item.get("policy_key") or ""),
                version_key=str(item.get("version_key") or ""),
                version_label=str(item.get("version_label") or ""),
                alert_type="rollback_candidate" if status == "rollback_candidate" else "rollout_hold",
                severity="high" if status == "rollback_candidate" else "medium",
                recommendation=str(item.get("recommended_action") or "hold"),
                reason_text=", ".join(list(item.get("reasons") or [])) or status,
                metrics=item.get("metrics") or {},
                observed_at=datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            )
        )
    return alerts


async def list_via_shadow_rollout_readiness(
    *,
    window_days: int = 14,
    limit: int = 300,
    version_key: str = "",
    policy_key: str = "",
) -> list[dict[str, Any]]:
    decisions = await asyncio.to_thread(list_recent_via_decisions, max(80, int(limit)))
    outcomes = await asyncio.to_thread(list_recent_via_outcomes, max(80, int(limit)))
    reward_traces = await asyncio.to_thread(list_recent_via_reward_traces, max(120, int(limit)))
    active_versions = await asyncio.to_thread(list_active_via_policy_versions)
    decisions = _filter_recent_control_rows(decisions, window_days)
    outcomes = _filter_recent_control_rows(outcomes, window_days)
    reward_traces = _filter_recent_control_rows(reward_traces, window_days)
    staged_versions = [
        item
        for item in active_versions
        if str(item.get("status") or "").lower() == "staged"
        and (not policy_key or str(item.get("policy_key") or "") == str(policy_key or ""))
        and (not version_key or str(item.get("version_key") or "") == str(version_key or ""))
    ]
    return _summarize_shadow_rollout_readiness(
        decisions,
        outcomes,
        reward_traces,
        staged_versions,
        window_days=window_days,
    )


async def list_via_live_rollout_health(
    *,
    window_days: int = 14,
    limit: int = 300,
    version_key: str = "",
    policy_key: str = "",
) -> list[dict[str, Any]]:
    decisions = await asyncio.to_thread(list_recent_via_decisions, max(80, int(limit)))
    outcomes = await asyncio.to_thread(list_recent_via_outcomes, max(80, int(limit)))
    reward_traces = await asyncio.to_thread(list_recent_via_reward_traces, max(120, int(limit)))
    active_versions = await asyncio.to_thread(list_active_via_policy_versions)
    version_history = await asyncio.to_thread(list_via_policy_version_history, max(120, int(limit) * 2), policy_key, "", "")
    decisions = _filter_recent_control_rows(decisions, window_days)
    outcomes = _filter_recent_control_rows(outcomes, window_days)
    reward_traces = _filter_recent_control_rows(reward_traces, window_days)
    live_versions = [
        item
        for item in active_versions
        if str(item.get("status") or "").lower() == "live"
        and (not policy_key or str(item.get("policy_key") or "") == str(policy_key or ""))
        and (not version_key or str(item.get("version_key") or "") == str(version_key or ""))
    ]
    return _summarize_live_rollout_health(
        decisions,
        outcomes,
        reward_traces,
        live_versions,
        window_days=window_days,
        version_history=version_history,
    )


async def get_via_rollout_alert_snapshot(
    *,
    limit: int = 80,
    policy_key: str = "",
    version_key: str = "",
    status: str = "",
) -> dict[str, Any]:
    rows = await asyncio.to_thread(
        list_via_rollout_alerts,
        int(limit),
        str(policy_key or ""),
        str(version_key or ""),
        str(status or ""),
    )
    severity = Counter(str(item.get("severity") or "medium") for item in rows)
    return {
        "count": len(rows),
        "severity": dict(severity.most_common()),
        "items": rows[: int(limit)],
    }


async def get_via_retrieval_evidence_snapshot(
    *,
    window_days: int = 14,
    limit: int = 120,
    policy_key: str = "",
) -> dict[str, Any]:
    rows = await asyncio.to_thread(list_recent_via_retrieval_evidence, max(40, int(limit)), str(policy_key or ""))
    rows = _filter_recent_control_rows(rows, window_days)
    summary = _summarize_retrieval_evidence(rows)
    return {"summary": summary, "items": rows[: int(limit)]}


async def get_via_routing_learner_snapshot(
    *,
    window_days: int = 21,
    limit: int = 120,
    bucket_key: str = "",
    target: str = "",
) -> dict[str, Any]:
    rows = await asyncio.to_thread(list_via_routing_provider_stats, max(40, int(limit)), str(bucket_key or ""), str(target or ""))
    rows = _filter_recent_control_rows(rows, window_days)
    summary = _summarize_routing_learner_stats(rows)
    return {"summary": summary, "items": rows[: int(limit)]}


async def get_via_memory_retention_snapshot(
    *,
    window_days: int = 45,
    limit: int = 120,
    memory_tier: str = "",
    status: str = "",
) -> dict[str, Any]:
    rows = await asyncio.to_thread(list_via_memory_retention_stats, max(40, int(limit)), str(memory_tier or ""), str(status or ""))
    rows = _filter_recent_control_rows(rows, window_days)
    summary = _summarize_memory_retention(rows)
    return {"summary": summary, "items": rows[: int(limit)]}


async def promote_via_policy_version_guarded(
    version_key: str,
    *,
    actor: str = "",
    note: str = "",
    window_days: int = 14,
    limit: int = 300,
    force: bool = False,
) -> dict[str, Any]:
    staged = await asyncio.to_thread(get_via_policy_version, version_key)
    if not staged:
        raise ValueError("Policy version not found")
    policy_key = str(staged.get("policy_key") or "")
    readiness_items = await list_via_shadow_rollout_readiness(
        window_days=window_days,
        limit=limit,
        version_key=version_key,
        policy_key=policy_key,
    )
    readiness = readiness_items[0] if readiness_items else {}
    config_override: dict[str, Any] | None = None
    if policy_key in _P1_SHADOW_ROLLOUT_RULES:
        recommended_rollout = float((readiness.get("recommended_rollout_percentage") or 0.0) if readiness else 0.0)
        if not force and recommended_rollout <= 0:
            reason_text = ", ".join(list(readiness.get("reasons") or [])) if readiness else "shadow_not_ready"
            raise ValueError(f"Shadow readiness has not cleared promote gate: {reason_text}")
        if recommended_rollout > 0:
            config_override = dict(staged.get("config") or {})
            config_override.update(
                {
                    "rollout_mode": "limited",
                    "rollout_percentage": recommended_rollout,
                    "rollout_stage": "p1_guarded",
                    "shadow_source_version_key": version_key,
                    "shadow_window_days": int(window_days or 14),
                    "shadow_readiness": {
                        "status": str(readiness.get("status") or ""),
                        "metrics": readiness.get("metrics") or {},
                        "reasons": readiness.get("reasons") or [],
                    },
                }
            )
    result = await asyncio.to_thread(
        promote_via_policy_version,
        version_key,
        actor=actor,
        note=note,
        config_override=config_override,
    )
    result["shadow_readiness"] = readiness
    return result


async def advance_via_live_policy_rollout_guarded(
    version_key: str,
    *,
    actor: str = "",
    note: str = "",
    window_days: int = 14,
    limit: int = 300,
    force: bool = False,
) -> dict[str, Any]:
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    live_version = await asyncio.to_thread(get_via_policy_version, version_key)
    if not live_version:
        raise ValueError("Policy version not found")
    if str(live_version.get("status") or "").lower() != "live":
        raise ValueError("Only live versions can advance rollout")
    health_items = await list_via_live_rollout_health(
        window_days=window_days,
        limit=limit,
        version_key=version_key,
        policy_key=str(live_version.get("policy_key") or ""),
    )
    health = health_items[0] if health_items else {}
    next_rollout = float((health.get("next_rollout_percentage") or 0.0) if health else 0.0)
    if next_rollout <= 0:
        raise ValueError("This live version is already at full rollout")
    if not force and str(health.get("status") or "") != "healthy":
        reason_text = ", ".join(list(health.get("reasons") or [])) if health else "rollout_not_ready"
        raise ValueError(f"Live rollout has not cleared advance gate: {reason_text}")
    config = dict(live_version.get("config") or {})
    config.update(
        {
            "rollout_mode": "limited" if next_rollout < 1.0 else "full",
            "rollout_percentage": next_rollout,
            "rollout_stage": f"p1_ramp_{int(round(next_rollout * 100))}",
            "rollout_from_version_key": version_key,
            "live_rollout_health": {
                "status": str(health.get("status") or ""),
                "metrics": health.get("metrics") or {},
                "reasons": health.get("reasons") or [],
            },
        }
    )
    current_label = str(live_version.get("version_label") or "")
    next_label = f"{current_label}.r{int(round(next_rollout * 100))}" if current_label else f"rollout.r{int(round(next_rollout * 100))}"
    next_live = await asyncio.to_thread(
        create_via_policy_version,
        policy_key=str(live_version.get("policy_key") or ""),
        config=config,
        version_label=next_label,
        source_proposal_key=str(live_version.get("source_proposal_key") or f"rollout:{version_key}"),
        status="live",
        approved_by=str(live_version.get("approved_by") or actor or ""),
        approved_at=str(live_version.get("approved_at") or now),
        applied_by=str(actor or ""),
        applied_at=now,
        review_note=str(note or live_version.get("review_note") or f"Advance rollout from {current_label or version_key}"),
    )
    return {
        "previous_live_version": live_version,
        "live_version": next_live,
        "live_rollout_health": health,
    }


__all__ = [name for name in globals() if not name.startswith("__")]
