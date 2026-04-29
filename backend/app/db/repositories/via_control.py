"""
db/repositories/via_control.py — Via control-loop ledgers
"""
from __future__ import annotations

import hashlib
import json
import secrets
from datetime import datetime, timezone
from typing import Any

from app.db.connection import get_conn, is_postgres_runtime


def _utcnow() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _json(value: Any, default: Any) -> str:
    data = default if value is None else value
    return json.dumps(data, ensure_ascii=False)


def _nullable_timestamp(value: Any) -> Any:
    text = str(value or "").strip()
    return text or None


def _load_json(raw: Any, default: Any) -> Any:
    if raw in (None, ""):
        return default
    if isinstance(raw, (dict, list)):
        return raw
    try:
        return json.loads(str(raw))
    except Exception:
        return default


def _table_columns(conn: Any, table_name: str) -> set[str]:
    try:
        if is_postgres_runtime():
            rows = conn.execute(
                """
                SELECT column_name
                FROM information_schema.columns
                WHERE table_name = %s
                """,
                (str(table_name or "").strip(),),
            ).fetchall()
            return {str(row["column_name"] or "").strip() for row in rows}
        rows = conn.execute(f"PRAGMA table_info({table_name})").fetchall()
        return {str(row["name"] or "").strip() for row in rows}
    except Exception:
        return set()


def _decision_from_row(row: Any) -> dict[str, Any]:
    if not row:
        return {}
    return {
        "id": int(row["id"]),
        "decision_id": row["decision_id"] or "",
        "session_key": row["session_key"] or "",
        "session_id": int(row["session_id"] or 0),
        "user_id": int(row["user_id"] or 0),
        "persona_id": int(row["persona_id"] or 0),
        "decision_type": row["decision_type"] or "",
        "trigger_type": row["trigger_type"] or "",
        "trigger_payload": _load_json(row["trigger_payload_json"], {}),
        "state_snapshot": _load_json(row["state_snapshot_json"], {}),
        "candidates": _load_json(row["candidates_json"], []),
        "chosen_action": _load_json(row["chosen_action_json"], {}),
        "policy_key": row["policy_key"] or "",
        "policy_version": row["policy_version"] or "",
        "context_refs": _load_json(row["context_refs_json"], []),
        "latency_ms": float(row["latency_ms"] or 0.0),
        "cost_estimate": float(row["cost_estimate"] or 0.0),
        "created_at": row["created_at"] or "",
    }


def _outcome_from_row(row: Any) -> dict[str, Any]:
    if not row:
        return {}
    return {
        "id": int(row["id"]),
        "outcome_id": row["outcome_id"] or "",
        "decision_id": row["decision_id"] or "",
        "session_key": row["session_key"] or "",
        "accepted": bool(row["accepted"]),
        "followup_depth": int(row["followup_depth"] or 0),
        "rephrase_needed": bool(row["rephrase_needed"]),
        "clicked_product": bool(row["clicked_product"]),
        "added_to_cart": bool(row["added_to_cart"]),
        "purchased": bool(row["purchased"]),
        "thumb_feedback": int(row["thumb_feedback"] or 0),
        "abuse_flag": int(row["abuse_flag"] or 0),
        "reward_score": float(row["reward_score"] or 0.0),
        "outcome_payload": _load_json(row["outcome_payload_json"], {}),
        "created_at": row["created_at"] or "",
    }


def _reward_trace_from_row(row: Any) -> dict[str, Any]:
    if not row:
        return {}
    return {
        "id": int(row["id"]),
        "trace_id": row["trace_id"] or "",
        "session_key": row["session_key"] or "",
        "decision_id": row["decision_id"] or "",
        "user_id": int(row["user_id"] or 0),
        "event_type": row["event_type"] or "",
        "surface": row["surface"] or "",
        "source": row["source"] or "",
        "origin": row["origin"] or "",
        "product_key": row["product_key"] or "",
        "event_value": float(row["event_value"] or 0.0),
        "idempotency_key": row["idempotency_key"] or "",
        "event_payload": _load_json(row["event_payload_json"], {}),
        "created_at": row["created_at"] or "",
    }


def _retrieval_evidence_from_row(row: Any) -> dict[str, Any]:
    if not row:
        return {}
    return {
        "id": int(row["id"]),
        "evidence_id": row["evidence_id"] or "",
        "session_key": row["session_key"] or "",
        "decision_id": row["decision_id"] or "",
        "policy_key": row["policy_key"] or "",
        "policy_version": row["policy_version"] or "",
        "retrieval_mode": row["retrieval_mode"] or "",
        "candidate_sources": _load_json(row["candidate_sources_json"], []),
        "selected_sources": _load_json(row["selected_sources_json"], []),
        "vector_hit_count": int(row["vector_hit_count"] or 0),
        "bundle_hit_count": int(row["bundle_hit_count"] or 0),
        "seed_hit_count": int(row["seed_hit_count"] or 0),
        "vector_limit": int(row["vector_limit"] or 0),
        "top_score": float(row["top_score"] or 0.0),
        "avg_score": float(row["avg_score"] or 0.0),
        "score_spread": float(row["score_spread"] or 0.0),
        "rerank_applied": bool(row["rerank_applied"]),
        "rerank_summary": _load_json(row["rerank_summary_json"], {}),
        "evidence_payload": _load_json(row["evidence_payload_json"], {}),
        "created_at": row["created_at"] or "",
    }


def _proposal_from_row(row: Any) -> dict[str, Any]:
    if not row:
        return {}
    return {
        "id": int(row["id"]),
        "proposal_key": row["proposal_key"] or "",
        "proposal_type": row["proposal_type"] or "",
        "policy_key": row["policy_key"] or "",
        "status": row["status"] or "proposed",
        "confidence": float(row["confidence"] or 0.0),
        "impact_score": float(row["impact_score"] or 0.0),
        "evidence": _load_json(row["evidence_json"], {}),
        "proposal": _load_json(row["proposal_json"], {}),
        "window_days": int(row["window_days"] or 0),
        "evaluator_version": row["evaluator_version"] or "",
        "reviewed_by": row["reviewed_by"] or "",
        "review_note": row["review_note"] or "",
        "reviewed_at": row["reviewed_at"] or "",
        "applied_version_key": row["applied_version_key"] or "",
        "applied_by": row["applied_by"] or "",
        "created_at": row["created_at"] or "",
        "updated_at": row["updated_at"] or "",
        "applied_at": row["applied_at"] or "",
    }


def _policy_version_from_row(row: Any) -> dict[str, Any]:
    if not row:
        return {}
    return {
        "id": int(row["id"]),
        "version_key": row["version_key"] or "",
        "policy_key": row["policy_key"] or "",
        "version_label": row["version_label"] or "",
        "status": row["status"] or "",
        "source_proposal_key": row["source_proposal_key"] or "",
        "config": _load_json(row["config_json"], {}),
        "approved_by": row["approved_by"] or "",
        "approved_at": row["approved_at"] or "",
        "applied_by": row["applied_by"] or "",
        "applied_at": row["applied_at"] or "",
        "review_note": row["review_note"] or "",
        "created_at": row["created_at"] or "",
    }


def _rollout_alert_from_row(row: Any) -> dict[str, Any]:
    if not row:
        return {}
    return {
        "id": int(row["id"]),
        "alert_key": row["alert_key"] or "",
        "policy_key": row["policy_key"] or "",
        "version_key": row["version_key"] or "",
        "version_label": row["version_label"] or "",
        "alert_type": row["alert_type"] or "",
        "severity": row["severity"] or "",
        "status": row["status"] or "",
        "recommendation": row["recommendation"] or "",
        "reason_text": row["reason_text"] or "",
        "metrics": _load_json(row["metrics_json"], {}),
        "observed_at": row["observed_at"] or "",
        "created_at": row["created_at"] or "",
        "resolved_at": row["resolved_at"] or "",
    }


def _routing_provider_stat_from_row(row: Any) -> dict[str, Any]:
    if not row:
        return {}
    exposure_count = int(row["exposure_count"] or 0)
    success_count = int(row["success_count"] or 0)
    reward_sum = float(row["reward_sum"] or 0.0)
    return {
        "id": int(row["id"]),
        "bucket_key": row["bucket_key"] or "",
        "target": row["target"] or "",
        "provider": row["provider"] or "",
        "exposure_count": exposure_count,
        "success_count": success_count,
        "reward_sum": reward_sum,
        "guard_fail_count": int(row["guard_fail_count"] or 0),
        "avg_latency_ms": float(row["avg_latency_ms"] or 0.0),
        "avg_cost_estimate": float(row["avg_cost_estimate"] or 0.0),
        "avg_reward": round(reward_sum / max(1, exposure_count), 4),
        "success_rate": round(success_count / max(1, exposure_count), 4),
        "last_outcome_at": row["last_outcome_at"] or "",
        "metrics": _load_json(row["metrics_json"], {}),
        "updated_at": row["updated_at"] or "",
    }


def _memory_retention_from_row(row: Any) -> dict[str, Any]:
    if not row:
        return {}
    row_keys = set(row.keys()) if hasattr(row, "keys") else set()
    retention_key = row["retention_key"] if "retention_key" in row_keys else row["memory_key"]
    user_id = int(row["user_id"] or 0) if "user_id" in row_keys else 0
    session_key = row["session_key"] if "session_key" in row_keys else ""
    fact_key = row["fact_key"] if "fact_key" in row_keys else ""
    source_ref = row["source_ref"] if "source_ref" in row_keys else ":".join(
        part for part in (row["target_type"] if "target_type" in row_keys else "", row["target_id"] if "target_id" in row_keys else "") if part
    )
    decay_state = row["decay_state"] if "decay_state" in row_keys else ""
    return {
        "id": int(row["id"]),
        "retention_key": retention_key or "",
        "memory_key": retention_key or "",
        "user_id": user_id,
        "session_key": session_key or "",
        "memory_tier": row["memory_tier"] or "",
        "memory_kind": row["memory_kind"] or "",
        "fact_key": fact_key or "",
        "source_ref": source_ref or "",
        "target_type": row["target_type"] if "target_type" in row_keys else "",
        "target_id": row["target_id"] if "target_id" in row_keys else "",
        "confirmed_hits": int(row["confirmed_hits"] or 0),
        "reinforcement_count": int(row["reinforcement_count"] or 0),
        "cumulative_reward": float(row["cumulative_reward"] or 0.0),
        "last_hit_at": row["last_hit_at"] or "",
        "last_promoted_at": row["last_promoted_at"] or "",
        "decay_state": decay_state or "",
        "status": row["status"] or "",
        "metrics": _load_json(row["metrics_json"], {}),
        "updated_at": row["updated_at"] or "",
    }


def _update_policy_version_status(
    version_key: str,
    *,
    status: str,
    actor_field: str = "",
    actor_value: str = "",
    applied_at: str = "",
    review_note: str | None = None,
) -> dict[str, Any]:
    row = get_via_policy_version(version_key)
    if not row:
        return {}
    conn = get_conn()
    current_note = str(row.get("review_note") or "")
    next_note = current_note if review_note is None else str(review_note or current_note)
    assignments = ["status=?", "review_note=?"]
    params: list[Any] = [str(status or row.get("status") or ""), next_note]
    if actor_field:
        assignments.append(f"{actor_field}=?")
        params.append(str(actor_value or ""))
    if applied_at:
        assignments.append("applied_at=?")
        params.append(str(applied_at or ""))
    params.append(str(version_key or "").strip())
    conn.execute(
        f"UPDATE via_policy_versions SET {', '.join(assignments)} WHERE version_key=?",
        tuple(params),
    )
    updated = conn.execute(
        "SELECT * FROM via_policy_versions WHERE version_key=?",
        (str(version_key or "").strip(),),
    ).fetchone()
    conn.commit()
    return _policy_version_from_row(updated)


def insert_via_decision_record(
    *,
    session_key: str,
    decision_type: str,
    trigger_type: str = "",
    session_id: int = 0,
    user_id: int = 0,
    persona_id: int = 0,
    decision_id: str = "",
    trigger_payload: Any = None,
    state_snapshot: Any = None,
    candidates: Any = None,
    chosen_action: Any = None,
    policy_key: str = "",
    policy_version: str = "",
    context_refs: Any = None,
    latency_ms: float = 0.0,
    cost_estimate: float = 0.0,
) -> dict[str, Any]:
    conn = get_conn()
    now = _utcnow()
    decision_key = str(decision_id or f"vd_{secrets.token_hex(10)}").strip()
    params = (
        decision_key,
        session_key,
        int(session_id or 0),
        int(user_id or 0),
        int(persona_id or 0),
        decision_type,
        trigger_type,
        _json(trigger_payload, {}),
        _json(state_snapshot, {}),
        _json(candidates, []),
        _json(chosen_action, {}),
        policy_key,
        policy_version,
        _json(context_refs, []),
        float(latency_ms or 0.0),
        float(cost_estimate or 0.0),
        now,
    )
    sql = """
        INSERT INTO via_decision_ledger (
            decision_id, session_key, session_id, user_id, persona_id,
            decision_type, trigger_type, trigger_payload_json, state_snapshot_json,
            candidates_json, chosen_action_json, policy_key, policy_version,
            context_refs_json, latency_ms, cost_estimate, created_at
        ) VALUES (?,?,?,?,?,?,?,?,?, ?,?,?,?,?,?,?,?)
    """
    if is_postgres_runtime():
        cur = conn.execute(sql + " RETURNING id", params)
        row = cur.fetchone()
        record_id = int(row["id"]) if row else 0
    else:
        cur = conn.execute(sql, params)
        record_id = int(cur.lastrowid)
    row = conn.execute("SELECT * FROM via_decision_ledger WHERE id=?", (record_id,)).fetchone()
    conn.commit()
    return _decision_from_row(row)


def insert_via_outcome_record(
    *,
    decision_id: str,
    session_key: str,
    outcome_id: str = "",
    accepted: bool = False,
    followup_depth: int = 0,
    rephrase_needed: bool = False,
    clicked_product: bool = False,
    added_to_cart: bool = False,
    purchased: bool = False,
    thumb_feedback: int = 0,
    abuse_flag: int = 0,
    reward_score: float = 0.0,
    outcome_payload: Any = None,
) -> dict[str, Any]:
    conn = get_conn()
    now = _utcnow()
    outcome_key = str(outcome_id or f"vo_{secrets.token_hex(10)}").strip()
    params = (
        outcome_key,
        decision_id,
        session_key,
        1 if accepted else 0,
        int(followup_depth or 0),
        1 if rephrase_needed else 0,
        1 if clicked_product else 0,
        1 if added_to_cart else 0,
        1 if purchased else 0,
        int(thumb_feedback or 0),
        int(abuse_flag or 0),
        float(reward_score or 0.0),
        _json(outcome_payload, {}),
        now,
    )
    sql = """
        INSERT INTO via_outcome_ledger (
            outcome_id, decision_id, session_key, accepted, followup_depth,
            rephrase_needed, clicked_product, added_to_cart, purchased,
            thumb_feedback, abuse_flag, reward_score, outcome_payload_json, created_at
        ) VALUES (?,?,?,?,?,?,?,?,?, ?,?,?,?,?)
    """
    if is_postgres_runtime():
        cur = conn.execute(sql + " RETURNING id", params)
        row = cur.fetchone()
        record_id = int(row["id"]) if row else 0
    else:
        cur = conn.execute(sql, params)
        record_id = int(cur.lastrowid)
    row = conn.execute("SELECT * FROM via_outcome_ledger WHERE id=?", (record_id,)).fetchone()
    conn.commit()
    return _outcome_from_row(row)


def update_via_outcome_record(
    outcome_id: str,
    *,
    clicked_product: bool | None = None,
    added_to_cart: bool | None = None,
    purchased: bool | None = None,
    thumb_feedback: int | None = None,
    reward_score: float | None = None,
    outcome_payload: Any | None = None,
) -> dict[str, Any]:
    conn = get_conn()
    sets: list[str] = []
    params: list[Any] = []
    if clicked_product is not None:
        sets.append("clicked_product=?")
        params.append(1 if clicked_product else 0)
    if added_to_cart is not None:
        sets.append("added_to_cart=?")
        params.append(1 if added_to_cart else 0)
    if purchased is not None:
        sets.append("purchased=?")
        params.append(1 if purchased else 0)
    if thumb_feedback is not None:
        sets.append("thumb_feedback=?")
        params.append(int(thumb_feedback or 0))
    if reward_score is not None:
        sets.append("reward_score=?")
        params.append(float(reward_score or 0.0))
    if outcome_payload is not None:
        sets.append("outcome_payload_json=?")
        params.append(_json(outcome_payload, {}))
    if not sets:
        row = conn.execute(
            "SELECT * FROM via_outcome_ledger WHERE outcome_id=?",
            (str(outcome_id or "").strip(),),
        ).fetchone()
        return _outcome_from_row(row)
    params.append(str(outcome_id or "").strip())
    conn.execute(
        f"UPDATE via_outcome_ledger SET {', '.join(sets)} WHERE outcome_id=?",
        tuple(params),
    )
    row = conn.execute(
        "SELECT * FROM via_outcome_ledger WHERE outcome_id=?",
        (str(outcome_id or "").strip(),),
    ).fetchone()
    conn.commit()
    return _outcome_from_row(row)


def insert_via_reward_trace(
    *,
    session_key: str,
    event_type: str,
    decision_id: str = "",
    user_id: int = 0,
    trace_id: str = "",
    surface: str = "",
    source: str = "",
    origin: str = "",
    product_key: str = "",
    event_value: float = 0.0,
    idempotency_key: str = "",
    event_payload: Any = None,
) -> dict[str, Any]:
    conn = get_conn()
    now = _utcnow()
    trace_key = str(trace_id or f"vrt_{secrets.token_hex(10)}").strip()
    dedupe_key = str(idempotency_key or "").strip()
    if dedupe_key:
        existing = conn.execute(
            """
            SELECT * FROM via_reward_traces
            WHERE session_key=? AND idempotency_key=?
            ORDER BY id DESC
            LIMIT 1
            """,
            (str(session_key or "").strip(), dedupe_key),
        ).fetchone()
        if existing:
            return _reward_trace_from_row(existing)
    params = (
        trace_key,
        session_key,
        str(decision_id or "").strip(),
        int(user_id or 0),
        str(event_type or "").strip(),
        str(surface or "").strip(),
        str(source or "").strip(),
        str(origin or "").strip(),
        str(product_key or "").strip(),
        float(event_value or 0.0),
        dedupe_key,
        _json(event_payload, {}),
        now,
    )
    sql = """
        INSERT INTO via_reward_traces (
            trace_id, session_key, decision_id, user_id, event_type,
            surface, source, origin, product_key, event_value,
            idempotency_key, event_payload_json, created_at
        ) VALUES (?,?,?,?,?,?,?,?,?, ?,?,?,?)
    """
    if is_postgres_runtime():
        cur = conn.execute(sql + " RETURNING id", params)
        row = cur.fetchone()
        record_id = int(row["id"]) if row else 0
    else:
        cur = conn.execute(sql, params)
        record_id = int(cur.lastrowid)
    row = conn.execute("SELECT * FROM via_reward_traces WHERE id=?", (record_id,)).fetchone()
    conn.commit()
    return _reward_trace_from_row(row)


def list_via_decision_records(session_key: str, limit: int = 50) -> list[dict[str, Any]]:
    conn = get_conn()
    rows = conn.execute(
        """
        SELECT * FROM via_decision_ledger
        WHERE session_key=?
        ORDER BY id DESC
        LIMIT ?
        """,
        (session_key, int(limit)),
    ).fetchall()
    return [_decision_from_row(row) for row in rows]


def list_via_outcome_records(session_key: str, limit: int = 50) -> list[dict[str, Any]]:
    conn = get_conn()
    rows = conn.execute(
        """
        SELECT * FROM via_outcome_ledger
        WHERE session_key=?
        ORDER BY id DESC
        LIMIT ?
        """,
        (session_key, int(limit)),
    ).fetchall()
    return [_outcome_from_row(row) for row in rows]


def get_latest_via_outcome_record(session_key: str, decision_id: str = "") -> dict[str, Any]:
    conn = get_conn()
    params: list[Any] = [str(session_key or "").strip()]
    where = "WHERE session_key=?"
    if str(decision_id or "").strip():
        where += " AND decision_id=?"
        params.append(str(decision_id or "").strip())
    row = conn.execute(
        f"""
        SELECT * FROM via_outcome_ledger
        {where}
        ORDER BY id DESC
        LIMIT 1
        """,
        tuple(params),
    ).fetchone()
    return _outcome_from_row(row)


def list_via_reward_traces(
    session_key: str,
    *,
    decision_id: str = "",
    event_type: str = "",
    limit: int = 100,
) -> list[dict[str, Any]]:
    conn = get_conn()
    params: list[Any] = [str(session_key or "").strip()]
    where = ["session_key=?"]
    if str(decision_id or "").strip():
        where.append("decision_id=?")
        params.append(str(decision_id or "").strip())
    if str(event_type or "").strip():
        where.append("event_type=?")
        params.append(str(event_type or "").strip())
    params.append(int(limit))
    rows = conn.execute(
        f"""
        SELECT * FROM via_reward_traces
        WHERE {' AND '.join(where)}
        ORDER BY id DESC
        LIMIT ?
        """,
        tuple(params),
    ).fetchall()
    return [_reward_trace_from_row(row) for row in rows]


def list_recent_via_decisions(limit: int = 100, decision_type: str = "") -> list[dict[str, Any]]:
    conn = get_conn()
    params: list[Any] = []
    where = ""
    if str(decision_type or "").strip():
        where = "WHERE decision_type=?"
        params.append(str(decision_type).strip())
    params.append(int(limit))
    rows = conn.execute(
        f"""
        SELECT * FROM via_decision_ledger
        {where}
        ORDER BY id DESC
        LIMIT ?
        """,
        tuple(params),
    ).fetchall()
    return [_decision_from_row(row) for row in rows]


def list_recent_via_outcomes(limit: int = 100) -> list[dict[str, Any]]:
    conn = get_conn()
    rows = conn.execute(
        """
        SELECT * FROM via_outcome_ledger
        ORDER BY id DESC
        LIMIT ?
        """,
        (int(limit),),
    ).fetchall()
    return [_outcome_from_row(row) for row in rows]


def list_recent_via_reward_traces(limit: int = 200, event_type: str = "") -> list[dict[str, Any]]:
    conn = get_conn()
    params: list[Any] = []
    where = ""
    if str(event_type or "").strip():
        where = "WHERE event_type=?"
        params.append(str(event_type).strip())
    params.append(int(limit))
    rows = conn.execute(
        f"""
        SELECT * FROM via_reward_traces
        {where}
        ORDER BY id DESC
        LIMIT ?
        """,
        tuple(params),
    ).fetchall()
    return [_reward_trace_from_row(row) for row in rows]


def get_via_reward_trace_by_idempotency(session_key: str, idempotency_key: str) -> dict[str, Any]:
    conn = get_conn()
    row = conn.execute(
        """
        SELECT * FROM via_reward_traces
        WHERE session_key=? AND idempotency_key=?
        ORDER BY id DESC
        LIMIT 1
        """,
        (str(session_key or "").strip(), str(idempotency_key or "").strip()),
    ).fetchone()
    return _reward_trace_from_row(row)


def get_via_reward_trace_by_idempotency_key(idempotency_key: str) -> dict[str, Any]:
    conn = get_conn()
    row = conn.execute(
        """
        SELECT * FROM via_reward_traces
        WHERE idempotency_key=?
        ORDER BY id DESC
        LIMIT 1
        """,
        (str(idempotency_key or "").strip(),),
    ).fetchone()
    return _reward_trace_from_row(row)


def insert_via_retrieval_evidence(
    *,
    session_key: str,
    decision_id: str,
    policy_key: str = "",
    policy_version: str = "",
    retrieval_mode: str = "",
    candidate_sources: Any = None,
    selected_sources: Any = None,
    vector_hit_count: int = 0,
    bundle_hit_count: int = 0,
    seed_hit_count: int = 0,
    vector_limit: int = 0,
    top_score: float = 0.0,
    avg_score: float = 0.0,
    score_spread: float = 0.0,
    rerank_applied: bool = False,
    rerank_summary: Any = None,
    evidence_payload: Any = None,
    evidence_id: str = "",
) -> dict[str, Any]:
    conn = get_conn()
    row = conn.execute(
        "SELECT * FROM via_retrieval_evidence WHERE decision_id=? ORDER BY id DESC LIMIT 1",
        (str(decision_id or "").strip(),),
    ).fetchone()
    if row:
        conn.execute(
            """
            UPDATE via_retrieval_evidence
            SET session_key=?, policy_key=?, policy_version=?, retrieval_mode=?,
                candidate_sources_json=?, selected_sources_json=?, vector_hit_count=?,
                bundle_hit_count=?, seed_hit_count=?, vector_limit=?, top_score=?,
                avg_score=?, score_spread=?, rerank_applied=?, rerank_summary_json=?,
                evidence_payload_json=?, created_at=?
            WHERE decision_id=?
            """,
            (
                str(session_key or "").strip(),
                str(policy_key or "").strip(),
                str(policy_version or "").strip(),
                str(retrieval_mode or "").strip(),
                _json(candidate_sources, []),
                _json(selected_sources, []),
                int(vector_hit_count or 0),
                int(bundle_hit_count or 0),
                int(seed_hit_count or 0),
                int(vector_limit or 0),
                float(top_score or 0.0),
                float(avg_score or 0.0),
                float(score_spread or 0.0),
                1 if rerank_applied else 0,
                _json(rerank_summary, {}),
                _json(evidence_payload, {}),
                _utcnow(),
                str(decision_id or "").strip(),
            ),
        )
        updated = conn.execute(
            "SELECT * FROM via_retrieval_evidence WHERE decision_id=? ORDER BY id DESC LIMIT 1",
            (str(decision_id or "").strip(),),
        ).fetchone()
        conn.commit()
        return _retrieval_evidence_from_row(updated)
    now = _utcnow()
    key = str(evidence_id or f"vre_{secrets.token_hex(10)}").strip()
    params = (
        key,
        str(session_key or "").strip(),
        str(decision_id or "").strip(),
        str(policy_key or "").strip(),
        str(policy_version or "").strip(),
        str(retrieval_mode or "").strip(),
        _json(candidate_sources, []),
        _json(selected_sources, []),
        int(vector_hit_count or 0),
        int(bundle_hit_count or 0),
        int(seed_hit_count or 0),
        int(vector_limit or 0),
        float(top_score or 0.0),
        float(avg_score or 0.0),
        float(score_spread or 0.0),
        1 if rerank_applied else 0,
        _json(rerank_summary, {}),
        _json(evidence_payload, {}),
        now,
    )
    sql = """
        INSERT INTO via_retrieval_evidence (
            evidence_id, session_key, decision_id, policy_key, policy_version,
            retrieval_mode, candidate_sources_json, selected_sources_json,
            vector_hit_count, bundle_hit_count, seed_hit_count, vector_limit,
            top_score, avg_score, score_spread, rerank_applied,
            rerank_summary_json, evidence_payload_json, created_at
        ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
    """
    if is_postgres_runtime():
        cur = conn.execute(sql + " RETURNING id", params)
        inserted = cur.fetchone()
        record_id = int(inserted["id"]) if inserted else 0
    else:
        cur = conn.execute(sql, params)
        record_id = int(cur.lastrowid or 0)
    stored = conn.execute("SELECT * FROM via_retrieval_evidence WHERE id=?", (record_id,)).fetchone()
    conn.commit()
    return _retrieval_evidence_from_row(stored)


def list_recent_via_retrieval_evidence(limit: int = 200, policy_key: str = "") -> list[dict[str, Any]]:
    conn = get_conn()
    params: list[Any] = []
    where = ""
    if str(policy_key or "").strip():
        where = "WHERE policy_key=?"
        params.append(str(policy_key).strip())
    params.append(int(limit))
    rows = conn.execute(
        f"""
        SELECT * FROM via_retrieval_evidence
        {where}
        ORDER BY id DESC
        LIMIT ?
        """,
        tuple(params),
    ).fetchall()
    return [_retrieval_evidence_from_row(row) for row in rows]


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


def upsert_via_policy_proposal(
    *,
    proposal_key: str,
    proposal_type: str,
    policy_key: str,
    status: str = "proposed",
    confidence: float = 0.0,
    impact_score: float = 0.0,
    evidence: Any = None,
    proposal: Any = None,
    window_days: int = 0,
    evaluator_version: str = "",
    reviewed_by: str = "",
    review_note: str = "",
    reviewed_at: str = "",
    applied_version_key: str = "",
    applied_by: str = "",
    applied_at: str = "",
) -> dict[str, Any]:
    conn = get_conn()
    now = _utcnow()
    applied_value = applied_at or (None if is_postgres_runtime() else "")
    reviewed_value = reviewed_at or (None if is_postgres_runtime() else "")
    params = (
        proposal_key,
        proposal_type,
        policy_key,
        status,
        float(confidence or 0.0),
        float(impact_score or 0.0),
        _json(evidence, {}),
        _json(proposal, {}),
        int(window_days or 0),
        evaluator_version,
        reviewed_by,
        review_note,
        reviewed_value,
        applied_version_key,
        applied_by,
        now,
        now,
        applied_value,
    )
    conn.execute(
        """
        INSERT INTO via_policy_proposals (
            proposal_key, proposal_type, policy_key, status, confidence,
            impact_score, evidence_json, proposal_json, window_days,
            evaluator_version, reviewed_by, review_note, reviewed_at,
            applied_version_key, applied_by, created_at, updated_at, applied_at
        ) VALUES (?,?,?,?,?,?,?,?,?, ?,?,?,?,?,?,?,?,?)
        ON CONFLICT(proposal_key) DO UPDATE SET
            proposal_type=excluded.proposal_type,
            policy_key=excluded.policy_key,
            status=excluded.status,
            confidence=excluded.confidence,
            impact_score=excluded.impact_score,
            evidence_json=excluded.evidence_json,
            proposal_json=excluded.proposal_json,
            window_days=excluded.window_days,
            evaluator_version=excluded.evaluator_version,
            reviewed_by=excluded.reviewed_by,
            review_note=excluded.review_note,
            reviewed_at=excluded.reviewed_at,
            applied_version_key=excluded.applied_version_key,
            applied_by=excluded.applied_by,
            updated_at=excluded.updated_at,
            applied_at=excluded.applied_at
        """,
        params,
    )
    row = conn.execute("SELECT * FROM via_policy_proposals WHERE proposal_key=?", (proposal_key,)).fetchone()
    conn.commit()
    return _proposal_from_row(row)


def list_via_policy_proposals(
    limit: int = 50,
    status: str = "",
    policy_key: str = "",
    audit_actor: str = "",
) -> list[dict[str, Any]]:
    conn = get_conn()
    params: list[Any] = []
    where_parts: list[str] = []
    if str(status or "").strip():
        where_parts.append("status=?")
        params.append(str(status).strip())
    if str(policy_key or "").strip():
        where_parts.append("policy_key=?")
        params.append(str(policy_key).strip())
    if str(audit_actor or "").strip():
        where_parts.append("(reviewed_by LIKE ? OR applied_by LIKE ?)")
        token = f"%{str(audit_actor).strip()}%"
        params.extend([token, token])
    params.append(int(limit))
    where = f"WHERE {' AND '.join(where_parts)}" if where_parts else ""
    rows = conn.execute(
        f"""
        SELECT * FROM via_policy_proposals
        {where}
        ORDER BY updated_at DESC, id DESC
        LIMIT ?
        """,
        tuple(params),
    ).fetchall()
    return [_proposal_from_row(row) for row in rows]


def get_via_policy_proposal(proposal_key: str) -> dict[str, Any]:
    conn = get_conn()
    row = conn.execute(
        "SELECT * FROM via_policy_proposals WHERE proposal_key=?",
        (str(proposal_key or "").strip(),),
    ).fetchone()
    return _proposal_from_row(row)


def review_via_policy_proposal(
    proposal_key: str,
    *,
    action: str,
    actor: str = "",
    note: str = "",
) -> dict[str, Any]:
    action_key = str(action or "").strip().lower()
    if action_key not in {"approve", "reject"}:
        raise ValueError("Unsupported proposal review action")
    proposal = get_via_policy_proposal(proposal_key)
    if not proposal:
        raise ValueError("Proposal not found")
    now = _utcnow()
    return upsert_via_policy_proposal(
        proposal_key=proposal["proposal_key"],
        proposal_type=proposal["proposal_type"],
        policy_key=proposal["policy_key"],
        status="approved" if action_key == "approve" else "rejected",
        confidence=float(proposal.get("confidence") or 0.0),
        impact_score=float(proposal.get("impact_score") or 0.0),
        evidence=proposal.get("evidence") or {},
        proposal=proposal.get("proposal") or {},
        window_days=int(proposal.get("window_days") or 0),
        evaluator_version=str(proposal.get("evaluator_version") or ""),
        reviewed_by=str(actor or ""),
        review_note=str(note or ""),
        reviewed_at=now,
        applied_version_key=str(proposal.get("applied_version_key") or ""),
        applied_by=str(proposal.get("applied_by") or ""),
        applied_at=str(proposal.get("applied_at") or ""),
    )


def create_via_policy_version(
    *,
    policy_key: str,
    config: Any,
    version_label: str = "",
    source_proposal_key: str = "",
    status: str = "live",
    approved_by: str = "",
    approved_at: str = "",
    applied_by: str = "",
    applied_at: str = "",
    review_note: str = "",
) -> dict[str, Any]:
    conn = get_conn()
    now = _utcnow()
    label = str(version_label or f"{now[:10]}.{secrets.token_hex(2)}").strip()
    version_key = f"vpv_{secrets.token_hex(8)}"
    status_key = str(status or "live").strip().lower() or "live"
    if status_key == "live":
        conn.execute(
            "UPDATE via_policy_versions SET status='superseded' WHERE policy_key=? AND status='live'",
            (str(policy_key or "").strip(),),
        )
    params = (
        version_key,
        str(policy_key or "").strip(),
        label,
        status_key,
        str(source_proposal_key or ""),
        _json(config, {}),
        str(approved_by or ""),
        approved_at or (None if is_postgres_runtime() else ""),
        str(applied_by or ""),
        applied_at or (None if is_postgres_runtime() else ""),
        str(review_note or ""),
        now,
    )
    sql = """
        INSERT INTO via_policy_versions (
            version_key, policy_key, version_label, status, source_proposal_key,
            config_json, approved_by, approved_at, applied_by, applied_at,
            review_note, created_at
        ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
    """
    if is_postgres_runtime():
        cur = conn.execute(sql + " RETURNING id", params)
        row = cur.fetchone()
        record_id = int(row["id"]) if row else 0
    else:
        cur = conn.execute(sql, params)
        record_id = int(cur.lastrowid or 0)
    row = conn.execute("SELECT * FROM via_policy_versions WHERE id=?", (record_id,)).fetchone()
    conn.commit()
    return _policy_version_from_row(row)


def apply_via_policy_proposal(
    proposal_key: str,
    *,
    actor: str = "",
    note: str = "",
) -> dict[str, Any]:
    proposal = get_via_policy_proposal(proposal_key)
    if not proposal:
        raise ValueError("Proposal not found")
    if str(proposal.get("status") or "").lower() == "rejected":
        raise ValueError("Rejected proposal cannot be applied")
    if str(proposal.get("status") or "").lower() not in {"approved", "applied"}:
        raise ValueError("Proposal must be approved before apply")
    proposal_payload = dict(proposal.get("proposal") or {})
    candidate_config = proposal_payload.get("candidate_config")
    if not isinstance(candidate_config, dict) or not candidate_config:
        raise ValueError("Proposal is missing candidate_config")
    now = _utcnow()
    live_version = create_via_policy_version(
        policy_key=str(proposal.get("policy_key") or ""),
        config=candidate_config,
        version_label=str(candidate_config.get("policy_version") or f"{now[:10]}.{secrets.token_hex(2)}"),
        source_proposal_key=proposal["proposal_key"],
        approved_by=str(proposal.get("reviewed_by") or actor or ""),
        approved_at=str(proposal.get("reviewed_at") or now),
        applied_by=str(actor or ""),
        applied_at=now,
        review_note=str(note or proposal.get("review_note") or ""),
    )
    updated = upsert_via_policy_proposal(
        proposal_key=proposal["proposal_key"],
        proposal_type=proposal["proposal_type"],
        policy_key=proposal["policy_key"],
        status="applied",
        confidence=float(proposal.get("confidence") or 0.0),
        impact_score=float(proposal.get("impact_score") or 0.0),
        evidence=proposal.get("evidence") or {},
        proposal=proposal_payload,
        window_days=int(proposal.get("window_days") or 0),
        evaluator_version=str(proposal.get("evaluator_version") or ""),
        reviewed_by=str(proposal.get("reviewed_by") or actor or ""),
        review_note=str(note or proposal.get("review_note") or ""),
        reviewed_at=str(proposal.get("reviewed_at") or now),
        applied_version_key=str(live_version.get("version_key") or ""),
        applied_by=str(actor or ""),
        applied_at=now,
    )
    return {"proposal": updated, "live_version": live_version}


def stage_via_policy_proposal(
    proposal_key: str,
    *,
    actor: str = "",
    note: str = "",
) -> dict[str, Any]:
    proposal = get_via_policy_proposal(proposal_key)
    if not proposal:
        raise ValueError("Proposal not found")
    if str(proposal.get("status") or "").lower() == "rejected":
        raise ValueError("Rejected proposal cannot be staged")
    if str(proposal.get("status") or "").lower() not in {"approved", "applied"}:
        raise ValueError("Proposal must be approved before staging")
    proposal_payload = dict(proposal.get("proposal") or {})
    candidate_config = proposal_payload.get("candidate_config")
    if not isinstance(candidate_config, dict) or not candidate_config:
        raise ValueError("Proposal is missing candidate_config")
    now = _utcnow()
    staged_version = create_via_policy_version(
        policy_key=str(proposal.get("policy_key") or ""),
        config=candidate_config,
        version_label=str(candidate_config.get("policy_version") or f"{now[:10]}.{secrets.token_hex(2)}") + ".staged",
        source_proposal_key=proposal["proposal_key"],
        status="staged",
        approved_by=str(proposal.get("reviewed_by") or actor or ""),
        approved_at=str(proposal.get("reviewed_at") or now),
        review_note=str(note or proposal.get("review_note") or ""),
    )
    return {"proposal": proposal, "staged_version": staged_version}


def get_via_policy_version(version_key: str) -> dict[str, Any]:
    conn = get_conn()
    row = conn.execute(
        "SELECT * FROM via_policy_versions WHERE version_key=?",
        (str(version_key or "").strip(),),
    ).fetchone()
    return _policy_version_from_row(row)


def get_live_via_policy_version(policy_key: str) -> dict[str, Any]:
    conn = get_conn()
    row = conn.execute(
        """
        SELECT * FROM via_policy_versions
        WHERE policy_key=? AND status='live'
        ORDER BY id DESC
        LIMIT 1
        """,
        (str(policy_key or "").strip(),),
    ).fetchone()
    return _policy_version_from_row(row)


def get_staged_via_policy_version(policy_key: str) -> dict[str, Any]:
    conn = get_conn()
    row = conn.execute(
        """
        SELECT * FROM via_policy_versions
        WHERE policy_key=? AND status='staged'
        ORDER BY id DESC
        LIMIT 1
        """,
        (str(policy_key or "").strip(),),
    ).fetchone()
    return _policy_version_from_row(row)


def list_live_via_policy_versions() -> list[dict[str, Any]]:
    conn = get_conn()
    rows = conn.execute(
        """
        SELECT * FROM via_policy_versions
        WHERE status='live'
        ORDER BY policy_key ASC, id DESC
        """
    ).fetchall()
    return [_policy_version_from_row(row) for row in rows]


def list_active_via_policy_versions() -> list[dict[str, Any]]:
    conn = get_conn()
    rows = conn.execute(
        """
        SELECT * FROM via_policy_versions
        WHERE status IN ('live', 'staged')
        ORDER BY
            CASE WHEN status='live' THEN 0 ELSE 1 END ASC,
            policy_key ASC,
            created_at DESC,
            id DESC
        """
    ).fetchall()
    return [_policy_version_from_row(row) for row in rows]


def promote_via_policy_version(
    version_key: str,
    *,
    actor: str = "",
    note: str = "",
    config_override: Any | None = None,
) -> dict[str, Any]:
    staged = get_via_policy_version(version_key)
    if not staged:
        raise ValueError("Policy version not found")
    if str(staged.get("status") or "").lower() != "staged":
        raise ValueError("Only staged versions can be promoted")
    now = _utcnow()
    live_config = dict(staged.get("config") or {})
    if isinstance(config_override, dict) and config_override:
        live_config.update(config_override)
    _update_policy_version_status(
        version_key,
        status="superseded",
        review_note=str(note or staged.get("review_note") or ""),
    )
    live_version = create_via_policy_version(
        policy_key=str(staged.get("policy_key") or ""),
        config=live_config,
        version_label=str(staged.get("version_label") or f"{now[:10]}.{secrets.token_hex(2)}"),
        source_proposal_key=str(staged.get("source_proposal_key") or ""),
        status="live",
        approved_by=str(staged.get("approved_by") or actor or ""),
        approved_at=str(staged.get("approved_at") or now),
        applied_by=str(actor or ""),
        applied_at=now,
        review_note=str(note or staged.get("review_note") or ""),
    )
    proposal_key = str(staged.get("source_proposal_key") or "")
    proposal = {}
    if proposal_key:
        proposal_row = get_via_policy_proposal(proposal_key)
        if proposal_row:
            proposal = upsert_via_policy_proposal(
                proposal_key=proposal_row["proposal_key"],
                proposal_type=proposal_row["proposal_type"],
                policy_key=proposal_row["policy_key"],
                status="applied",
                confidence=float(proposal_row.get("confidence") or 0.0),
                impact_score=float(proposal_row.get("impact_score") or 0.0),
                evidence=proposal_row.get("evidence") or {},
                proposal=proposal_row.get("proposal") or {},
                window_days=int(proposal_row.get("window_days") or 0),
                evaluator_version=str(proposal_row.get("evaluator_version") or ""),
                reviewed_by=str(proposal_row.get("reviewed_by") or actor or ""),
                review_note=str(note or proposal_row.get("review_note") or ""),
                reviewed_at=str(proposal_row.get("reviewed_at") or now),
                applied_version_key=str(live_version.get("version_key") or ""),
                applied_by=str(actor or ""),
                applied_at=now,
            )
    return {"staged_version": staged, "live_version": live_version, "proposal": proposal}


def rollback_via_policy_version(
    version_key: str,
    *,
    actor: str = "",
    note: str = "",
) -> dict[str, Any]:
    current = get_via_policy_version(version_key)
    if not current:
        raise ValueError("Policy version not found")
    if str(current.get("status") or "").lower() != "live":
        raise ValueError("Only live versions can be rolled back")
    conn = get_conn()
    previous = conn.execute(
        """
        SELECT * FROM via_policy_versions
        WHERE policy_key=? AND version_key!=? AND status IN ('superseded', 'live')
        ORDER BY created_at DESC, id DESC
        LIMIT 1
        """,
        (str(current.get("policy_key") or ""), str(version_key or "").strip()),
    ).fetchone()
    if not previous:
        raise ValueError("No previous live-compatible version is available for rollback")
    previous_row = _policy_version_from_row(previous)
    now = _utcnow()
    rolled_back = _update_policy_version_status(
        version_key,
        status="rolled_back",
        actor_field="applied_by",
        actor_value=str(actor or ""),
        applied_at=now,
        review_note=str(note or current.get("review_note") or ""),
    )
    restored_live = create_via_policy_version(
        policy_key=str(previous_row.get("policy_key") or ""),
        config=previous_row.get("config") or {},
        version_label=f"{str(previous_row.get('version_label') or 'policy')}.rollback",
        source_proposal_key=f"rollback:{version_key}->{previous_row.get('version_key') or ''}",
        status="live",
        approved_by=str(previous_row.get("approved_by") or actor or ""),
        approved_at=str(previous_row.get("approved_at") or now),
        applied_by=str(actor or ""),
        applied_at=now,
        review_note=str(note or f"Rollback to {previous_row.get('version_label') or previous_row.get('version_key') or 'previous version'}"),
    )
    return {
        "rolled_back_version": rolled_back,
        "restored_live_version": restored_live,
        "restored_from_version": previous_row,
    }


def list_via_policy_version_history(
    limit: int = 50,
    policy_key: str = "",
    status: str = "",
    audit_actor: str = "",
) -> list[dict[str, Any]]:
    conn = get_conn()
    params: list[Any] = []
    where_parts: list[str] = []
    if str(policy_key or "").strip():
        where_parts.append("policy_key=?")
        params.append(str(policy_key).strip())
    if str(status or "").strip():
        where_parts.append("status=?")
        params.append(str(status).strip())
    if str(audit_actor or "").strip():
        where_parts.append("(approved_by LIKE ? OR applied_by LIKE ?)")
        token = f"%{str(audit_actor).strip()}%"
        params.extend([token, token])
    params.append(int(limit))
    where = f"WHERE {' AND '.join(where_parts)}" if where_parts else ""
    rows = conn.execute(
        f"""
        SELECT * FROM via_policy_versions
        {where}
        ORDER BY created_at DESC, id DESC
        LIMIT ?
        """,
        tuple(params),
    ).fetchall()
    return [_policy_version_from_row(row) for row in rows]
