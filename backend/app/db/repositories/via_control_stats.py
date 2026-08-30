"""Rollout, routing-provider, and memory-retention stats for Via control."""
from __future__ import annotations

import hashlib
from typing import Any

from app.db.connection import get_conn
from app.db.repositories.via_control_common import (
    _json,
    _load_json,
    _memory_retention_from_row,
    _nullable_timestamp,
    _rollout_alert_from_row,
    _routing_provider_stat_from_row,
    _table_columns,
    _utcnow,
)
from app.db.repositories.via_memory_retention_writer import (
    MemoryRetentionDependencies,
    upsert_via_memory_retention_stat as _upsert_via_memory_retention_stat,
)

def upsert_via_rollout_alert(
    *,
    policy_key: str,
    version_key: str,
    version_label: str = "",
    alert_type: str,
    severity: str = "medium",
    status: str = "open",
    recommendation: str = "",
    reason_text: str = "",
    metrics: Any = None,
    observed_at: str = "",
    resolved_at: str = "",
    alert_key: str = "",
) -> dict[str, Any]:
    conn = get_conn()
    now = _utcnow()
    if str(alert_key or "").strip():
        key = str(alert_key).strip()
    else:
        digest = hashlib.sha256(
            "|".join(
                [
                    str(policy_key or "").strip(),
                    str(version_key or "").strip(),
                    str(alert_type or "").strip(),
                    str(reason_text or "").strip(),
                ]
            ).encode("utf-8")
        ).hexdigest()[:20]
        key = f"vra_{digest}"
    params = (
        key,
        str(policy_key or "").strip(),
        str(version_key or "").strip(),
        str(version_label or "").strip(),
        str(alert_type or "").strip(),
        str(severity or "medium").strip(),
        str(status or "open").strip(),
        str(recommendation or "").strip(),
        str(reason_text or "").strip(),
        _json(metrics, {}),
        str(observed_at or now),
        now,
        str(resolved_at or "").strip(),
    )
    conn.execute(
        """
        INSERT INTO via_rollout_alerts (
            alert_key, policy_key, version_key, version_label, alert_type, severity,
            status, recommendation, reason_text, metrics_json, observed_at, created_at, resolved_at
        ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)
        ON CONFLICT(alert_key) DO UPDATE SET
            severity=excluded.severity,
            status=excluded.status,
            recommendation=excluded.recommendation,
            reason_text=excluded.reason_text,
            metrics_json=excluded.metrics_json,
            observed_at=excluded.observed_at,
            resolved_at=excluded.resolved_at
        """,
        params,
    )
    row = conn.execute("SELECT * FROM via_rollout_alerts WHERE alert_key=?", (key,)).fetchone()
    conn.commit()
    return _rollout_alert_from_row(row)

def list_via_rollout_alerts(limit: int = 80, policy_key: str = "", version_key: str = "", status: str = "") -> list[dict[str, Any]]:
    conn = get_conn()
    params: list[Any] = []
    where_parts: list[str] = []
    if str(policy_key or "").strip():
        where_parts.append("policy_key=?")
        params.append(str(policy_key).strip())
    if str(version_key or "").strip():
        where_parts.append("version_key=?")
        params.append(str(version_key).strip())
    if str(status or "").strip():
        where_parts.append("status=?")
        params.append(str(status).strip())
    params.append(int(limit))
    where = f"WHERE {' AND '.join(where_parts)}" if where_parts else ""
    rows = conn.execute(
        f"""
        SELECT * FROM via_rollout_alerts
        {where}
        ORDER BY created_at DESC, id DESC
        LIMIT ?
        """,
        tuple(params),
    ).fetchall()
    return [_rollout_alert_from_row(row) for row in rows]

def upsert_via_routing_provider_stat(
    *,
    bucket_key: str,
    provider: str,
    target: str = "dialogue_generation",
    exposure_increment: int = 0,
    success_increment: int = 0,
    reward_delta: float = 0.0,
    guard_fail_increment: int = 0,
    latency_ms: float = 0.0,
    cost_estimate: float = 0.0,
    metrics: Any = None,
    last_outcome_at: str = "",
) -> dict[str, Any]:
    conn = get_conn()
    existing = conn.execute(
        """
        SELECT * FROM via_routing_provider_stats
        WHERE bucket_key=? AND target=? AND provider=?
        """,
        (str(bucket_key or "").strip(), str(target or "dialogue_generation").strip(), str(provider or "").strip().lower()),
    ).fetchone()
    now = _utcnow()
    if existing:
        exposure_total = int(existing["exposure_count"] or 0) + int(exposure_increment or 0)
        success_total = int(existing["success_count"] or 0) + int(success_increment or 0)
        guard_total = int(existing["guard_fail_count"] or 0) + int(guard_fail_increment or 0)
        reward_total = float(existing["reward_sum"] or 0.0) + float(reward_delta or 0.0)
        prior_exposure = int(existing["exposure_count"] or 0)
        avg_latency = float(existing["avg_latency_ms"] or 0.0)
        avg_cost = float(existing["avg_cost_estimate"] or 0.0)
        if int(exposure_increment or 0) > 0:
            avg_latency = ((avg_latency * prior_exposure) + float(latency_ms or 0.0)) / max(1, exposure_total)
            avg_cost = ((avg_cost * prior_exposure) + float(cost_estimate or 0.0)) / max(1, exposure_total)
        merged_metrics = dict(_load_json(existing["metrics_json"], {}))
        if isinstance(metrics, dict):
            merged_metrics.update(metrics)
        conn.execute(
            """
            UPDATE via_routing_provider_stats
            SET exposure_count=?, success_count=?, reward_sum=?, guard_fail_count=?,
                avg_latency_ms=?, avg_cost_estimate=?, last_outcome_at=?, metrics_json=?, updated_at=?
            WHERE id=?
            """,
            (
                exposure_total,
                success_total,
                reward_total,
                guard_total,
                avg_latency,
                avg_cost,
                str(last_outcome_at or existing["last_outcome_at"] or now),
                _json(merged_metrics, {}),
                now,
                int(existing["id"]),
            ),
        )
    else:
        conn.execute(
            """
            INSERT INTO via_routing_provider_stats (
                bucket_key, target, provider, exposure_count, success_count, reward_sum,
                guard_fail_count, avg_latency_ms, avg_cost_estimate, last_outcome_at,
                metrics_json, updated_at
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                str(bucket_key or "").strip(),
                str(target or "dialogue_generation").strip(),
                str(provider or "").strip().lower(),
                int(exposure_increment or 0),
                int(success_increment or 0),
                float(reward_delta or 0.0),
                int(guard_fail_increment or 0),
                float(latency_ms or 0.0),
                float(cost_estimate or 0.0),
                str(last_outcome_at or now),
                _json(metrics, {}),
                now,
            ),
        )
    row = conn.execute(
        """
        SELECT * FROM via_routing_provider_stats
        WHERE bucket_key=? AND target=? AND provider=?
        """,
        (str(bucket_key or "").strip(), str(target or "dialogue_generation").strip(), str(provider or "").strip().lower()),
    ).fetchone()
    conn.commit()
    return _routing_provider_stat_from_row(row)

def list_via_routing_provider_stats(limit: int = 120, bucket_key: str = "", target: str = "") -> list[dict[str, Any]]:
    conn = get_conn()
    params: list[Any] = []
    where_parts: list[str] = []
    if str(bucket_key or "").strip():
        where_parts.append("bucket_key=?")
        params.append(str(bucket_key).strip())
    if str(target or "").strip():
        where_parts.append("target=?")
        params.append(str(target).strip())
    params.append(int(limit))
    where = f"WHERE {' AND '.join(where_parts)}" if where_parts else ""
    rows = conn.execute(
        f"""
        SELECT * FROM via_routing_provider_stats
        {where}
        ORDER BY updated_at DESC, exposure_count DESC, id DESC
        LIMIT ?
        """,
        tuple(params),
    ).fetchall()
    return [_routing_provider_stat_from_row(row) for row in rows]

def upsert_via_memory_retention_stat(
    *,
    retention_key: str = "",
    memory_key: str = "",
    user_id: int = 0,
    session_key: str = "",
    memory_tier: str = "",
    memory_kind: str = "",
    fact_key: str = "",
    source_ref: str = "",
    target_type: str = "",
    target_id: str = "",
    confirmed_hit_increment: int = 0,
    reinforcement_increment: int = 0,
    reward_delta: float = 0.0,
    last_hit_at: str = "",
    last_promoted_at: str = "",
    decay_state: str = "",
    status: str = "",
    metrics: Any = None,
) -> dict[str, Any]:
    return _upsert_via_memory_retention_stat(
        deps=MemoryRetentionDependencies(
            get_conn=get_conn,
            utcnow=_utcnow,
            table_columns=_table_columns,
            load_json=_load_json,
            dump_json=_json,
            nullable_timestamp=_nullable_timestamp,
            row_mapper=_memory_retention_from_row,
        ),
        retention_key=retention_key,
        memory_key=memory_key,
        user_id=user_id,
        session_key=session_key,
        memory_tier=memory_tier,
        memory_kind=memory_kind,
        fact_key=fact_key,
        source_ref=source_ref,
        target_type=target_type,
        target_id=target_id,
        confirmed_hit_increment=confirmed_hit_increment,
        reinforcement_increment=reinforcement_increment,
        reward_delta=reward_delta,
        last_hit_at=last_hit_at,
        last_promoted_at=last_promoted_at,
        decay_state=decay_state,
        status=status,
        metrics=metrics,
    )

def list_via_memory_retention_stats(limit: int = 120, memory_tier: str = "", status: str = "") -> list[dict[str, Any]]:
    conn = get_conn()
    params: list[Any] = []
    where_parts: list[str] = []
    if str(memory_tier or "").strip():
        where_parts.append("memory_tier=?")
        params.append(str(memory_tier).strip())
    if str(status or "").strip():
        where_parts.append("status=?")
        params.append(str(status).strip())
    params.append(int(limit))
    where = f"WHERE {' AND '.join(where_parts)}" if where_parts else ""
    rows = conn.execute(
        f"""
        SELECT * FROM via_memory_retention_stats
        {where}
        ORDER BY updated_at DESC, id DESC
        LIMIT ?
        """,
        tuple(params),
    ).fetchall()
    return [_memory_retention_from_row(row) for row in rows]
