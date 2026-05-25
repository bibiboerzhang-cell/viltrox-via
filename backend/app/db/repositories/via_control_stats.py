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
    conn = get_conn()
    key = str(retention_key or memory_key or "").strip()
    now = _utcnow()
    source_ref_value = str(source_ref or "").strip()
    if not source_ref_value and (str(target_type or "").strip() or str(target_id or "").strip()):
        source_ref_value = ":".join(
            part for part in (str(target_type or "").strip(), str(target_id or "").strip()) if part
        )
    existing = conn.execute(
        "SELECT * FROM via_memory_retention_stats WHERE retention_key=?",
        (key,),
    ).fetchone() if "retention_key" in _table_columns(conn, "via_memory_retention_stats") else conn.execute(
        """
        SELECT * FROM via_memory_retention_stats
        WHERE memory_key=? AND target_type=? AND target_id=?
        """,
        (
            key,
            str(target_type or "").strip(),
            str(target_id or "").strip(),
        ),
    ).fetchone()
    if existing:
        merged_metrics = dict(_load_json(existing["metrics_json"], {}))
        if isinstance(metrics, dict):
            merged_metrics.update(metrics)
        if str(target_type or "").strip():
            merged_metrics["target_type"] = str(target_type).strip()
        if str(target_id or "").strip():
            merged_metrics["target_id"] = str(target_id).strip()
        columns = _table_columns(conn, "via_memory_retention_stats")
        if "retention_key" in columns:
            conn.execute(
                """
                UPDATE via_memory_retention_stats
                SET user_id=?, session_key=?, memory_tier=?, memory_kind=?, fact_key=?, source_ref=?,
                    confirmed_hits=?, reinforcement_count=?, cumulative_reward=?, last_hit_at=?,
                    last_promoted_at=?, decay_state=?, status=?, metrics_json=?, updated_at=?
                WHERE retention_key=?
                """,
                (
                    int(user_id or existing["user_id"] or 0),
                    str(session_key or existing["session_key"] or ""),
                    str(memory_tier or existing["memory_tier"] or ""),
                    str(memory_kind or existing["memory_kind"] or ""),
                    str(fact_key or existing["fact_key"] or ""),
                    str(source_ref_value or existing["source_ref"] or ""),
                    int(existing["confirmed_hits"] or 0) + int(confirmed_hit_increment or 0),
                    int(existing["reinforcement_count"] or 0) + int(reinforcement_increment or 0),
                    float(existing["cumulative_reward"] or 0.0) + float(reward_delta or 0.0),
                    _nullable_timestamp(last_hit_at or existing["last_hit_at"]),
                    _nullable_timestamp(last_promoted_at or existing["last_promoted_at"]),
                    str(decay_state or existing["decay_state"] or "fresh"),
                    str(status or existing["status"] or "active"),
                    _json(merged_metrics, {}),
                    now,
                    key,
                ),
            )
        else:
            conn.execute(
                """
                UPDATE via_memory_retention_stats
                SET memory_kind=?, memory_tier=?, target_type=?, target_id=?, status=?,
                    confirmed_hits=?, reinforcement_count=?, cumulative_reward=?, last_hit_at=?,
                    last_promoted_at=?, metrics_json=?, updated_at=?
                WHERE memory_key=? AND target_type=? AND target_id=?
                """,
                (
                    str(memory_kind or existing["memory_kind"] or ""),
                    str(memory_tier or existing["memory_tier"] or ""),
                    str(target_type or existing["target_type"] or ""),
                    str(target_id or existing["target_id"] or ""),
                    str(status or existing["status"] or "active"),
                    int(existing["confirmed_hits"] or 0) + int(confirmed_hit_increment or 0),
                    int(existing["reinforcement_count"] or 0) + int(reinforcement_increment or 0),
                    float(existing["cumulative_reward"] or 0.0) + float(reward_delta or 0.0),
                    _nullable_timestamp(last_hit_at or existing["last_hit_at"]),
                    _nullable_timestamp(last_promoted_at or existing["last_promoted_at"]),
                    _json(merged_metrics, {}),
                    now,
                    key,
                    str(existing["target_type"] or ""),
                    str(existing["target_id"] or ""),
                ),
            )
    else:
        insert_metrics = dict(metrics) if isinstance(metrics, dict) else {}
        if str(target_type or "").strip():
            insert_metrics["target_type"] = str(target_type).strip()
        if str(target_id or "").strip():
            insert_metrics["target_id"] = str(target_id).strip()
        columns = _table_columns(conn, "via_memory_retention_stats")
        if "retention_key" in columns:
            try:
                conn.execute(
                    """
                    INSERT INTO via_memory_retention_stats (
                        retention_key, user_id, session_key, memory_tier, memory_kind, fact_key,
                        source_ref, confirmed_hits, reinforcement_count, cumulative_reward,
                        last_hit_at, last_promoted_at, decay_state, status, metrics_json, updated_at
                    ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                    """,
                    (
                        key,
                        int(user_id or 0),
                        str(session_key or "").strip(),
                        str(memory_tier or "").strip(),
                        str(memory_kind or "").strip(),
                        str(fact_key or "").strip(),
                        source_ref_value,
                        int(confirmed_hit_increment or 0),
                        int(reinforcement_increment or 0),
                        float(reward_delta or 0.0),
                        _nullable_timestamp(last_hit_at),
                        _nullable_timestamp(last_promoted_at or now),
                        str(decay_state or "fresh"),
                        str(status or "active"),
                        _json(insert_metrics, {}),
                        now,
                    ),
                )
            except Exception as exc:
                text = str(exc).lower()
                if "duplicate key" not in text and "unique" not in text:
                    raise
                existing = conn.execute(
                    "SELECT * FROM via_memory_retention_stats WHERE retention_key=?",
                    (key,),
                ).fetchone()
                if not existing:
                    raise
                merged_metrics = dict(_load_json(existing["metrics_json"], {}))
                merged_metrics.update(insert_metrics)
                conn.execute(
                    """
                    UPDATE via_memory_retention_stats
                    SET user_id=?, session_key=?, memory_tier=?, memory_kind=?, fact_key=?, source_ref=?,
                        confirmed_hits=?, reinforcement_count=?, cumulative_reward=?, last_hit_at=?,
                        last_promoted_at=?, decay_state=?, status=?, metrics_json=?, updated_at=?
                    WHERE retention_key=?
                    """,
                    (
                        int(user_id or existing["user_id"] or 0),
                        str(session_key or existing["session_key"] or ""),
                        str(memory_tier or existing["memory_tier"] or ""),
                        str(memory_kind or existing["memory_kind"] or ""),
                        str(fact_key or existing["fact_key"] or ""),
                        str(source_ref_value or existing["source_ref"] or ""),
                        int(existing["confirmed_hits"] or 0) + int(confirmed_hit_increment or 0),
                        int(existing["reinforcement_count"] or 0) + int(reinforcement_increment or 0),
                        float(existing["cumulative_reward"] or 0.0) + float(reward_delta or 0.0),
                        _nullable_timestamp(last_hit_at or existing["last_hit_at"]),
                        _nullable_timestamp(last_promoted_at or existing["last_promoted_at"] or now),
                        str(decay_state or existing["decay_state"] or "fresh"),
                        str(status or existing["status"] or "active"),
                        _json(merged_metrics, {}),
                        now,
                        key,
                    ),
                )
        else:
            conn.execute(
                """
                INSERT INTO via_memory_retention_stats (
                    memory_key, memory_kind, memory_tier, target_type, target_id, status,
                    confirmed_hits, reinforcement_count, cumulative_reward, last_hit_at,
                    last_promoted_at, metrics_json, updated_at
                ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    key,
                    str(memory_kind or "").strip(),
                    str(memory_tier or "").strip(),
                    str(target_type or "").strip(),
                    str(target_id or "").strip(),
                    str(status or "active"),
                    int(confirmed_hit_increment or 0),
                    int(reinforcement_increment or 0),
                    float(reward_delta or 0.0),
                    _nullable_timestamp(last_hit_at),
                    _nullable_timestamp(last_promoted_at or now),
                    _json(insert_metrics, {}),
                    now,
                ),
            )
    row = conn.execute(
        "SELECT * FROM via_memory_retention_stats WHERE retention_key=?",
        (key,),
    ).fetchone() if "retention_key" in _table_columns(conn, "via_memory_retention_stats") else conn.execute(
        """
        SELECT * FROM via_memory_retention_stats
        WHERE memory_key=? AND target_type=? AND target_id=?
        """,
        (
            key,
            str(target_type or "").strip(),
            str(target_id or "").strip(),
        ),
    ).fetchone()
    conn.commit()
    return _memory_retention_from_row(row)

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
