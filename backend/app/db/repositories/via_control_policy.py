"""Policy proposal and version repository functions for Via control."""
from __future__ import annotations

import secrets
from typing import Any

from app.db.connection import get_conn, is_postgres_runtime
from app.db.repositories.via_control_common import (
    _json,
    _policy_version_from_row,
    _proposal_from_row,
    _utcnow,
)

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
