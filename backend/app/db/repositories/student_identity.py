"""
db/repositories/student_identity.py — QR-first student identity ledgers
"""
from __future__ import annotations

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


def _school_from_row(row: Any) -> dict[str, Any]:
    if not row:
        return {}
    return {
        "id": int(row["id"]),
        "school_id": row["school_id"] or "",
        "school_code": row["school_code"] or "",
        "school_name": row["school_name"] or "",
        "school_name_native": row["school_name_native"] or "",
        "country": row["country"] or "",
        "region": row["region"] or "",
        "school_type": row["school_type"] or "film",
        "tier": row["tier"] or "standard",
        "partnership_status": row["partnership_status"] or "pilot",
        "visual_theme": _load_json(row["visual_theme_json"], {}),
        "metadata": _load_json(row["metadata_json"], {}),
        "created_at": row["created_at"] or "",
        "updated_at": row["updated_at"] or "",
    }


def _qr_from_row(row: Any) -> dict[str, Any]:
    if not row:
        return {}
    return {
        "id": int(row["id"]),
        "qr_id": row["qr_id"] or "",
        "school_id": row["school_id"] or "",
        "issued_batch": row["issued_batch"] or "",
        "display_serial": row["display_serial"] or "",
        "claim_token": row["claim_token"] or "",
        "claim_signature": row["claim_signature"] or "",
        "claim_url": row["claim_url"] or "",
        "qr_code_url": row["qr_code_url"] or "",
        "card_image_url": row["card_image_url"] or "",
        "manifest_url": row["manifest_url"] or "",
        "status": row["status"] or "issued",
        "roster_mode": row["roster_mode"] or "anonymous",
        "bound_user_id": int(row["bound_user_id"] or 0),
        "bound_at": row["bound_at"] or "",
        "issued_at": row["issued_at"] or "",
        "expires_at": row["expires_at"] or "",
        "revoked_reason": row["revoked_reason"] or "",
        "prefilled": _load_json(row["prefilled_json"], {}),
        "metadata": _load_json(row["metadata_json"], {}),
    }


def _verification_from_row(row: Any) -> dict[str, Any]:
    if not row:
        return {}
    return {
        "id": int(row["id"]),
        "user_id": int(row["user_id"] or 0),
        "school_id": row["school_id"] or "",
        "student_id_code": row["student_id_code"] or "",
        "verification_method": row["verification_method"] or "qr_scan",
        "verification_proof": _load_json(row["verification_proof_json"], {}),
        "status": row["status"] or "active",
        "commission_rate_override": float(row["commission_rate_override"] or 0.0),
        "verified_by": row["verified_by"] or "",
        "verified_at": row["verified_at"] or "",
        "expires_at": row["expires_at"] or "",
        "created_at": row["created_at"] or "",
        "updated_at": row["updated_at"] or "",
    }


def _scan_event_from_row(row: Any) -> dict[str, Any]:
    if not row:
        return {}
    return {
        "id": int(row["id"]),
        "event_key": row["event_key"] or "",
        "qr_id": row["qr_id"] or "",
        "user_id": int(row["user_id"] or 0),
        "school_id": row["school_id"] or "",
        "event_type": row["event_type"] or "",
        "location": row["location"] or "",
        "event_payload": _load_json(row["event_payload_json"], {}),
        "created_at": row["created_at"] or "",
    }


def _audit_row(row: Any) -> dict[str, Any]:
    if not row:
        return {}
    return {
        "id": int(row["id"]),
        "audit_key": row["audit_key"] or "",
        "qr_id": row["qr_id"] or "",
        "user_id": int(row["user_id"] or 0),
        "school_id": row["school_id"] or "",
        "audit_type": row["audit_type"] or "",
        "actor": row["actor"] or "",
        "reason": row["reason"] or "",
        "payload": _load_json(row["payload_json"], {}),
        "created_at": row["created_at"] or "",
    }


def create_or_update_school(
    *,
    school_id: str,
    school_code: str,
    school_name: str,
    school_name_native: str = "",
    country: str = "",
    region: str = "",
    school_type: str = "film",
    tier: str = "standard",
    partnership_status: str = "pilot",
    visual_theme: Any = None,
    metadata: Any = None,
) -> dict[str, Any]:
    conn = get_conn()
    now = _utcnow()
    params = (
        str(school_id or "").strip(),
        str(school_code or "").strip().upper(),
        str(school_name or "").strip(),
        str(school_name_native or "").strip(),
        str(country or "").strip(),
        str(region or "").strip(),
        str(school_type or "film").strip(),
        str(tier or "standard").strip(),
        str(partnership_status or "pilot").strip(),
        _json(visual_theme, {}),
        _json(metadata, {}),
        now,
        now,
    )
    conn.execute(
        """
        INSERT INTO schools (
            school_id, school_code, school_name, school_name_native, country, region,
            school_type, tier, partnership_status, visual_theme_json, metadata_json,
            created_at, updated_at
        ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)
        ON CONFLICT(school_id) DO UPDATE SET
            school_code=excluded.school_code,
            school_name=excluded.school_name,
            school_name_native=excluded.school_name_native,
            country=excluded.country,
            region=excluded.region,
            school_type=excluded.school_type,
            tier=excluded.tier,
            partnership_status=excluded.partnership_status,
            visual_theme_json=excluded.visual_theme_json,
            metadata_json=excluded.metadata_json,
            updated_at=excluded.updated_at
        """,
        params,
    )
    row = conn.execute("SELECT * FROM schools WHERE school_id=?", (str(school_id).strip(),)).fetchone()
    conn.commit()
    return _school_from_row(row)


def get_school(school_id: str) -> dict[str, Any]:
    conn = get_conn()
    row = conn.execute(
        "SELECT * FROM schools WHERE school_id=?",
        (str(school_id or "").strip(),),
    ).fetchone()
    return _school_from_row(row)


def list_schools(limit: int = 200) -> list[dict[str, Any]]:
    conn = get_conn()
    rows = conn.execute(
        """
        SELECT * FROM schools
        ORDER BY school_name ASC, id ASC
        LIMIT ?
        """,
        (int(limit),),
    ).fetchall()
    return [_school_from_row(row) for row in rows]


def create_student_qr_code(
    *,
    school_id: str,
    issued_batch: str,
    display_serial: str,
    claim_token: str,
    claim_signature: str,
    claim_url: str,
    qr_code_url: str = "",
    card_image_url: str = "",
    manifest_url: str = "",
    roster_mode: str = "anonymous",
    expires_at: str = "",
    prefilled: Any = None,
    metadata: Any = None,
    qr_id: str = "",
) -> dict[str, Any]:
    conn = get_conn()
    now = _utcnow()
    qr_key = str(qr_id or f"sqr_{secrets.token_hex(8)}").strip()
    params = (
        qr_key,
        str(school_id or "").strip(),
        str(issued_batch or "").strip(),
        str(display_serial or "").strip(),
        str(claim_token or "").strip(),
        str(claim_signature or "").strip(),
        str(claim_url or "").strip(),
        str(qr_code_url or "").strip(),
        str(card_image_url or "").strip(),
        str(manifest_url or "").strip(),
        "issued",
        str(roster_mode or "anonymous").strip(),
        0,
        None,
        now,
        _nullable_timestamp(expires_at),
        "",
        _json(prefilled, {}),
        _json(metadata, {}),
    )
    sql = """
        INSERT INTO student_qr_codes (
            qr_id, school_id, issued_batch, display_serial, claim_token,
            claim_signature, claim_url, qr_code_url, card_image_url, manifest_url,
            status, roster_mode, bound_user_id, bound_at, issued_at, expires_at,
            revoked_reason, prefilled_json, metadata_json
        ) VALUES (?,?,?,?,?,?,?,?,?, ?,?,?,?,?,?,?,?,?,?)
    """
    if is_postgres_runtime():
        cur = conn.execute(sql + " RETURNING id", params)
        row = cur.fetchone()
        record_id = int(row["id"]) if row else 0
    else:
        cur = conn.execute(sql, params)
        record_id = int(cur.lastrowid or 0)
    row = conn.execute("SELECT * FROM student_qr_codes WHERE id=?", (record_id,)).fetchone()
    conn.commit()
    return _qr_from_row(row)


def get_student_qr_code(qr_id: str) -> dict[str, Any]:
    conn = get_conn()
    row = conn.execute(
        "SELECT * FROM student_qr_codes WHERE qr_id=?",
        (str(qr_id or "").strip(),),
    ).fetchone()
    return _qr_from_row(row)


def list_student_qr_codes(
    *,
    school_id: str = "",
    batch_name: str = "",
    limit: int = 200,
) -> list[dict[str, Any]]:
    conn = get_conn()
    params: list[Any] = []
    where: list[str] = []
    if str(school_id or "").strip():
        where.append("school_id=?")
        params.append(str(school_id).strip())
    if str(batch_name or "").strip():
        where.append("issued_batch=?")
        params.append(str(batch_name).strip())
    params.append(int(limit))
    rows = conn.execute(
        f"""
        SELECT * FROM student_qr_codes
        {'WHERE ' + ' AND '.join(where) if where else ''}
        ORDER BY issued_at DESC, id DESC
        LIMIT ?
        """,
        tuple(params),
    ).fetchall()
    return [_qr_from_row(row) for row in rows]


def update_student_qr_code(
    qr_id: str,
    *,
    status: str | None = None,
    claim_token: str | None = None,
    claim_signature: str | None = None,
    claim_url: str | None = None,
    qr_code_url: str | None = None,
    card_image_url: str | None = None,
    manifest_url: str | None = None,
    expires_at: str | None = None,
    revoked_reason: str | None = None,
    prefilled: Any = None,
    metadata: Any = None,
) -> dict[str, Any]:
    existing = get_student_qr_code(qr_id)
    if not existing:
        return {}
    conn = get_conn()
    conn.execute(
        """
        UPDATE student_qr_codes
        SET status=?, claim_token=?, claim_signature=?, claim_url=?, qr_code_url=?,
            card_image_url=?, manifest_url=?, expires_at=?, revoked_reason=?,
            prefilled_json=?, metadata_json=?
        WHERE qr_id=?
        """,
        (
            str(status if status is not None else existing.get("status") or "issued"),
            str(claim_token if claim_token is not None else existing.get("claim_token") or ""),
            str(claim_signature if claim_signature is not None else existing.get("claim_signature") or ""),
            str(claim_url if claim_url is not None else existing.get("claim_url") or ""),
            str(qr_code_url if qr_code_url is not None else existing.get("qr_code_url") or ""),
            str(card_image_url if card_image_url is not None else existing.get("card_image_url") or ""),
            str(manifest_url if manifest_url is not None else existing.get("manifest_url") or ""),
            _nullable_timestamp(expires_at if expires_at is not None else existing.get("expires_at")),
            str(revoked_reason if revoked_reason is not None else existing.get("revoked_reason") or ""),
            _json(prefilled if prefilled is not None else existing.get("prefilled") or {}, {}),
            _json(metadata if metadata is not None else existing.get("metadata") or {}, {}),
            str(qr_id or "").strip(),
        ),
    )
    row = conn.execute("SELECT * FROM student_qr_codes WHERE qr_id=?", (str(qr_id).strip(),)).fetchone()
    conn.commit()
    return _qr_from_row(row)


def bind_student_qr_code(qr_id: str, *, user_id: int) -> dict[str, Any]:
    conn = get_conn()
    now = _utcnow()
    conn.execute(
        """
        UPDATE student_qr_codes
        SET status='bound', bound_user_id=?, bound_at=?
        WHERE qr_id=?
        """,
        (int(user_id), now, str(qr_id or "").strip()),
    )
    row = conn.execute("SELECT * FROM student_qr_codes WHERE qr_id=?", (str(qr_id).strip(),)).fetchone()
    conn.commit()
    return _qr_from_row(row)


def create_student_verification(
    *,
    user_id: int,
    school_id: str,
    student_id_code: str,
    verification_method: str = "qr_scan",
    verification_proof: Any = None,
    status: str = "active",
    commission_rate_override: float = 0.10,
    verified_by: str = "system_qr",
    verified_at: str = "",
    expires_at: str = "",
) -> dict[str, Any]:
    conn = get_conn()
    now = _utcnow()
    verified_value = str(verified_at or now)
    params = (
        int(user_id),
        str(school_id or "").strip(),
        str(student_id_code or "").strip(),
        str(verification_method or "qr_scan").strip(),
        _json(verification_proof, {}),
        str(status or "active").strip(),
        float(commission_rate_override or 0.0),
        str(verified_by or "system_qr").strip(),
        verified_value,
        _nullable_timestamp(expires_at),
        now,
        now,
    )
    conn.execute(
        """
        INSERT INTO student_verifications (
            user_id, school_id, student_id_code, verification_method,
            verification_proof_json, status, commission_rate_override, verified_by,
            verified_at, expires_at, created_at, updated_at
        ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
        ON CONFLICT(user_id, school_id) DO UPDATE SET
            student_id_code=excluded.student_id_code,
            verification_method=excluded.verification_method,
            verification_proof_json=excluded.verification_proof_json,
            status=excluded.status,
            commission_rate_override=excluded.commission_rate_override,
            verified_by=excluded.verified_by,
            verified_at=excluded.verified_at,
            expires_at=excluded.expires_at,
            updated_at=excluded.updated_at
        """,
        params,
    )
    row = conn.execute(
        "SELECT * FROM student_verifications WHERE user_id=? AND school_id=?",
        (int(user_id), str(school_id).strip()),
    ).fetchone()
    conn.commit()
    return _verification_from_row(row)


def get_student_verification_for_user(user_id: int) -> dict[str, Any]:
    conn = get_conn()
    row = conn.execute(
        """
        SELECT * FROM student_verifications
        WHERE user_id=?
        ORDER BY verified_at DESC, id DESC
        LIMIT 1
        """,
        (int(user_id),),
    ).fetchone()
    return _verification_from_row(row)


def count_student_verifications_for_school(school_id: str) -> int:
    conn = get_conn()
    row = conn.execute(
        """
        SELECT COUNT(*) AS total
        FROM student_verifications
        WHERE school_id=?
        """,
        (str(school_id or "").strip(),),
    ).fetchone()
    return int((row["total"] if row else 0) or 0)


def create_student_scan_event(
    *,
    event_type: str,
    qr_id: str = "",
    user_id: int = 0,
    school_id: str = "",
    location: str = "",
    event_payload: Any = None,
    event_key: str = "",
) -> dict[str, Any]:
    conn = get_conn()
    now = _utcnow()
    key = str(event_key or f"sse_{secrets.token_hex(10)}").strip()
    params = (
        key,
        str(qr_id or "").strip(),
        int(user_id or 0),
        str(school_id or "").strip(),
        str(event_type or "").strip(),
        str(location or "").strip(),
        _json(event_payload, {}),
        now,
    )
    sql = """
        INSERT INTO student_scan_events (
            event_key, qr_id, user_id, school_id, event_type, location, event_payload_json, created_at
        ) VALUES (?,?,?,?,?,?,?,?)
    """
    if is_postgres_runtime():
        cur = conn.execute(sql + " RETURNING id", params)
        row = cur.fetchone()
        record_id = int(row["id"]) if row else 0
    else:
        cur = conn.execute(sql, params)
        record_id = int(cur.lastrowid or 0)
    row = conn.execute("SELECT * FROM student_scan_events WHERE id=?", (record_id,)).fetchone()
    conn.commit()
    return _scan_event_from_row(row)


def list_student_scan_events(*, user_id: int = 0, qr_id: str = "", limit: int = 50) -> list[dict[str, Any]]:
    conn = get_conn()
    params: list[Any] = []
    where: list[str] = []
    if int(user_id or 0) > 0:
        where.append("user_id=?")
        params.append(int(user_id))
    if str(qr_id or "").strip():
        where.append("qr_id=?")
        params.append(str(qr_id).strip())
    params.append(int(limit))
    rows = conn.execute(
        f"""
        SELECT * FROM student_scan_events
        {'WHERE ' + ' AND '.join(where) if where else ''}
        ORDER BY created_at DESC, id DESC
        LIMIT ?
        """,
        tuple(params),
    ).fetchall()
    return [_scan_event_from_row(row) for row in rows]


def create_student_identity_audit(
    *,
    audit_type: str,
    qr_id: str = "",
    user_id: int = 0,
    school_id: str = "",
    actor: str = "",
    reason: str = "",
    payload: Any = None,
    audit_key: str = "",
) -> dict[str, Any]:
    conn = get_conn()
    now = _utcnow()
    key = str(audit_key or f"sia_{secrets.token_hex(10)}").strip()
    params = (
        key,
        str(qr_id or "").strip(),
        int(user_id or 0),
        str(school_id or "").strip(),
        str(audit_type or "").strip(),
        str(actor or "").strip(),
        str(reason or "").strip(),
        _json(payload, {}),
        now,
    )
    sql = """
        INSERT INTO student_identity_audit_log (
            audit_key, qr_id, user_id, school_id, audit_type, actor, reason, payload_json, created_at
        ) VALUES (?,?,?,?,?,?,?,?,?)
    """
    if is_postgres_runtime():
        cur = conn.execute(sql + " RETURNING id", params)
        row = cur.fetchone()
        record_id = int(row["id"]) if row else 0
    else:
        cur = conn.execute(sql, params)
        record_id = int(cur.lastrowid or 0)
    row = conn.execute("SELECT * FROM student_identity_audit_log WHERE id=?", (record_id,)).fetchone()
    conn.commit()
    return _audit_row(row)


def list_student_identity_audit(
    *,
    qr_id: str = "",
    user_id: int = 0,
    school_id: str = "",
    limit: int = 50,
) -> list[dict[str, Any]]:
    conn = get_conn()
    params: list[Any] = []
    where: list[str] = []
    if str(qr_id or "").strip():
        where.append("qr_id=?")
        params.append(str(qr_id).strip())
    if int(user_id or 0) > 0:
        where.append("user_id=?")
        params.append(int(user_id))
    if str(school_id or "").strip():
        where.append("school_id=?")
        params.append(str(school_id).strip())
    params.append(int(limit))
    rows = conn.execute(
        f"""
        SELECT * FROM student_identity_audit_log
        {'WHERE ' + ' AND '.join(where) if where else ''}
        ORDER BY created_at DESC, id DESC
        LIMIT ?
        """,
        tuple(params),
    ).fetchall()
    return [_audit_row(row) for row in rows]
