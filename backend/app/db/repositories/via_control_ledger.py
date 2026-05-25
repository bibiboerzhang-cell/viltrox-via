"""Decision, outcome, reward, and retrieval evidence ledgers for Via control."""
from __future__ import annotations

import secrets
from typing import Any

from app.db.connection import get_conn, is_postgres_runtime
from app.db.repositories.via_control_common import (
    _decision_from_row,
    _json,
    _outcome_from_row,
    _retrieval_evidence_from_row,
    _reward_trace_from_row,
    _utcnow,
)

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
