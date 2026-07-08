"""Dashboard and reporting read models for student identity."""
from __future__ import annotations

import json
from typing import Any

from app.db.connection import get_conn
from app.db.repositories.student_identity import (
    count_student_verifications_for_school,
    get_school,
    get_student_verification_for_user,
    list_student_identity_audit,
    list_student_qr_codes,
    list_student_scan_events,
    list_schools,
)
from app.services.student_identity_common import STUDENT_COMMISSION_RATE, _load_json
from app.services.student_identity_defaults import ensure_student_school_defaults

def _school_stats(school_id: str) -> dict[str, int]:
    conn = get_conn()
    stats = {"issued": 0, "bound": 0, "active_students": 0}
    row = conn.execute(
        """
        SELECT
            COUNT(*) AS issued_count,
            SUM(CASE WHEN status='bound' THEN 1 ELSE 0 END) AS bound_count
        FROM student_qr_codes
        WHERE school_id=?
        """,
        (str(school_id or "").strip(),),
    ).fetchone()
    if row:
        stats["issued"] = int(row["issued_count"] or 0)
        stats["bound"] = int(row["bound_count"] or 0)
    stats["active_students"] = count_student_verifications_for_school(school_id)
    return stats

def list_student_schools_with_stats(limit: int = 200) -> list[dict[str, Any]]:
    ensure_student_school_defaults()
    items: list[dict[str, Any]] = []
    for school in list_schools(limit=limit):
        items.append({**school, "stats": _school_stats(str(school.get("school_id") or ""))})
    return items

def _student_batch_progress(limit: int = 48) -> list[dict[str, Any]]:
    conn = get_conn()
    rows = conn.execute(
        """
        SELECT
            q.school_id,
            s.school_name,
            q.issued_batch,
            COUNT(*) AS issued_count,
            SUM(CASE WHEN q.status='bound' THEN 1 ELSE 0 END) AS activated_count,
            SUM(CASE WHEN q.status='revoked' THEN 1 ELSE 0 END) AS revoked_count,
            MAX(COALESCE(q.bound_at, q.issued_at)) AS last_activity_at,
            MAX(q.issued_at) AS last_issued_at
        FROM student_qr_codes q
        LEFT JOIN schools s ON s.school_id = q.school_id
        GROUP BY q.school_id, s.school_name, q.issued_batch
        ORDER BY last_activity_at DESC, q.issued_batch DESC
        LIMIT ?
        """,
        (max(12, int(limit)),),
    ).fetchall()
    return [
        {
            "school_id": row["school_id"] or "",
            "school_name": row["school_name"] or "",
            "batch_name": row["issued_batch"] or "",
            "issued_count": int(row["issued_count"] or 0),
            "activated_count": int(row["activated_count"] or 0),
            "revoked_count": int(row["revoked_count"] or 0),
            "pending_count": max(0, int(row["issued_count"] or 0) - int(row["activated_count"] or 0) - int(row["revoked_count"] or 0)),
            "activation_rate": round((int(row["activated_count"] or 0) / max(1, int(row["issued_count"] or 0))), 4),
            "last_activity_at": row["last_activity_at"] or "",
            "last_issued_at": row["last_issued_at"] or "",
        }
        for row in rows
    ]

def _student_school_funnels(limit: int = 24) -> list[dict[str, Any]]:
    conn = get_conn()
    rows = conn.execute(
        """
        SELECT
            s.school_id,
            s.school_name,
            COUNT(q.id) AS issued_count,
            SUM(CASE WHEN q.status='bound' THEN 1 ELSE 0 END) AS activated_count,
            SUM(CASE WHEN q.status='revoked' THEN 1 ELSE 0 END) AS revoked_count,
            COUNT(DISTINCT sv.user_id) AS active_students,
            MAX(COALESCE(se.created_at, q.bound_at, q.issued_at)) AS last_activity_at
        FROM schools s
        LEFT JOIN student_qr_codes q ON q.school_id = s.school_id
        LEFT JOIN student_verifications sv ON sv.school_id = s.school_id AND sv.status='active'
        LEFT JOIN student_scan_events se ON se.school_id = s.school_id
        GROUP BY s.school_id, s.school_name
        ORDER BY activated_count DESC, issued_count DESC, s.school_name ASC
        LIMIT ?
        """,
        (max(8, int(limit)),),
    ).fetchall()
    return [
        {
            "school_id": row["school_id"] or "",
            "school_name": row["school_name"] or "",
            "issued_count": int(row["issued_count"] or 0),
            "activated_count": int(row["activated_count"] or 0),
            "revoked_count": int(row["revoked_count"] or 0),
            "pending_count": max(0, int(row["issued_count"] or 0) - int(row["activated_count"] or 0) - int(row["revoked_count"] or 0)),
            "active_students": int(row["active_students"] or 0),
            "activation_rate": round((int(row["activated_count"] or 0) / max(1, int(row["issued_count"] or 0))), 4),
            "last_activity_at": row["last_activity_at"] or "",
        }
        for row in rows
    ]

def _recent_student_events(limit: int = 32) -> list[dict[str, Any]]:
    conn = get_conn()
    rows = conn.execute(
        """
        SELECT
            se.*,
            s.school_name,
            u.name AS user_name,
            u.creator_code
        FROM student_scan_events se
        LEFT JOIN schools s ON s.school_id = se.school_id
        LEFT JOIN users u ON u.id = se.user_id
        ORDER BY se.created_at DESC, se.id DESC
        LIMIT ?
        """,
        (int(limit),),
    ).fetchall()
    items: list[dict[str, Any]] = []
    for row in rows:
        payload = dict(_load_json(row["event_payload_json"], {}))
        items.append(
            {
                "event_key": row["event_key"] or "",
                "qr_id": row["qr_id"] or "",
                "user_id": int(row["user_id"] or 0),
                "school_id": row["school_id"] or "",
                "school_name": row["school_name"] or "",
                "event_type": row["event_type"] or "",
                "location": row["location"] or "",
                "event_payload": payload,
                "user_name": row["user_name"] or "",
                "creator_code": row["creator_code"] or "",
                "created_at": row["created_at"] or "",
            }
        )
    return items

def _recent_student_anomalies(limit: int = 20) -> list[dict[str, Any]]:
    conn = get_conn()
    rows = conn.execute(
        """
        SELECT qr_id, school_id, issued_batch, display_serial, status, revoked_reason, expires_at, issued_at
        FROM student_qr_codes
        WHERE status='revoked' OR revoked_reason<>''
        ORDER BY issued_at DESC, id DESC
        LIMIT ?
        """,
        (int(limit),),
    ).fetchall()
    items: list[dict[str, Any]] = []
    for row in rows:
        items.append(
            {
                "qr_id": row["qr_id"] or "",
                "school_id": row["school_id"] or "",
                "batch_name": row["issued_batch"] or "",
                "display_serial": row["display_serial"] or "",
                "status": row["status"] or "",
                "reason": row["revoked_reason"] or "",
                "expires_at": row["expires_at"] or "",
                "issued_at": row["issued_at"] or "",
            }
        )
    return items

def build_student_funnel_snapshot(limit: int = 48) -> dict[str, Any]:
    return {
        "school_funnels": _student_school_funnels(limit=max(12, int(limit))),
        "batch_progress": _student_batch_progress(limit=max(12, int(limit))),
        "recent_events": _recent_student_events(limit=max(12, int(limit))),
        "recent_anomalies": _recent_student_anomalies(limit=max(8, int(limit // 2) or 8)),
        "recent_audit": list_student_identity_audit(limit=max(12, int(limit))),
    }

def build_student_overview(limit: int = 48) -> dict[str, Any]:
    schools = list_student_schools_with_stats()
    cards = list_student_qr_codes(limit=max(64, int(limit)))
    recent_bound = [item for item in cards if str(item.get("status") or "") == "bound"][: int(limit)]
    recent_students = build_student_roster(limit=limit)
    funnel = build_student_funnel_snapshot(limit=max(12, int(limit)))
    return {
        "schools": schools,
        "recent_cards": cards[: int(limit)],
        "recent_bound": recent_bound,
        "students": recent_students,
        "batch_progress": funnel["batch_progress"],
        "school_funnels": funnel["school_funnels"],
        "recent_events": funnel["recent_events"],
        "recent_anomalies": funnel["recent_anomalies"],
        "recent_audit": funnel["recent_audit"],
    }

def build_student_batch_detail(*, school_id: str, batch_name: str, limit: int = 240) -> dict[str, Any]:
    school = get_school(school_id)
    items = list_student_qr_codes(school_id=school_id, batch_name=batch_name, limit=limit)
    manifest_url = next((str(item.get("manifest_url") or "") for item in items if str(item.get("manifest_url") or "")), "")
    printable_url = manifest_url.replace("/manifest.csv", "/printable.html") if manifest_url.endswith("/manifest.csv") else ""
    recent_events = [item for item in _recent_student_events(limit=max(12, min(int(limit), 48))) if str(item.get("school_id") or "") == str(school_id or "")]
    recent_audit = [item for item in list_student_identity_audit(school_id=school_id, limit=max(12, min(int(limit), 48))) if str(item.get("qr_id") or "") in {str(card.get("qr_id") or "") for card in items}]
    return {
        "school": school,
        "batch_name": batch_name,
        "manifest_url": manifest_url,
        "printable_url": printable_url,
        "summary": next((item for item in _student_batch_progress(limit=max(12, int(limit))) if str(item.get("school_id") or "") == str(school_id or "") and str(item.get("batch_name") or "") == str(batch_name or "")), {}),
        "recent_events": recent_events[:24],
        "recent_audit": recent_audit[:24],
        "items": items,
    }

def build_student_roster(limit: int = 80) -> list[dict[str, Any]]:
    conn = get_conn()
    rows = conn.execute(
        """
        SELECT
            sv.user_id,
            sv.school_id,
            sv.student_id_code,
            sv.status,
            sv.commission_rate_override,
            sv.verified_at,
            sv.expires_at,
            u.creator_code,
            u.email,
            u.name,
            s.school_name
        FROM student_verifications sv
        LEFT JOIN users u ON u.id = sv.user_id
        LEFT JOIN schools s ON s.school_id = sv.school_id
        ORDER BY sv.verified_at DESC, sv.id DESC
        LIMIT ?
        """,
        (int(limit),),
    ).fetchall()
    items: list[dict[str, Any]] = []
    for row in rows:
        items.append(
            {
                "user_id": int(row["user_id"] or 0),
                "school_id": row["school_id"] or "",
                "school_name": row["school_name"] or "",
                "student_id_code": row["student_id_code"] or "",
                "status": row["status"] or "",
                "commission_rate_override": float(row["commission_rate_override"] or 0.0),
                "verified_at": row["verified_at"] or "",
                "expires_at": row["expires_at"] or "",
                "creator_code": row["creator_code"] or "",
                "email": row["email"] or "",
                "name": row["name"] or "",
            }
        )
    return items

def build_student_detail(user_id: int) -> dict[str, Any]:
    conn = get_conn()
    user = conn.execute("SELECT * FROM users WHERE id=?", (int(user_id),)).fetchone()
    if not user:
        raise ValueError("User not found")
    verification = get_student_verification_for_user(int(user_id))
    scans = list_student_scan_events(user_id=int(user_id), limit=20)
    qr_rows = conn.execute(
        """
        SELECT * FROM student_qr_codes
        WHERE bound_user_id=?
        ORDER BY bound_at DESC, id DESC
        LIMIT 12
        """,
        (int(user_id),),
    ).fetchall()
    submissions = conn.execute(
        """
        SELECT COUNT(*) AS total,
               SUM(CASE WHEN detection_status='confirmed' THEN 1 ELSE 0 END) AS confirmed,
               AVG(final_score) AS avg_campaign_score
        FROM submissions
        WHERE user_id=?
        """,
        (int(user_id),),
    ).fetchone()
    order_stats = conn.execute(
        """
        SELECT COUNT(*) AS total
        FROM platform_ingest_events
        WHERE source_platform='shopify' AND entity_type='order'
        """,
    ).fetchone()
    affiliate_orders = 0
    affiliate_gmv = 0.0
    creator_code = str(user["creator_code"] or "").strip().lower()
    if creator_code:
        rows = conn.execute(
            """
            SELECT payload_json
            FROM platform_ingest_events
            WHERE source_platform='shopify' AND entity_type='order'
            ORDER BY id DESC
            LIMIT 400
            """
        ).fetchall()
        for row in rows:
            payload = row["payload_json"] or "{}"
            try:
                doc = json.loads(payload)
            except Exception:
                doc = {}
            ref_code = str(doc.get("ref_code") or "").strip().lower()
            if ref_code != creator_code:
                continue
            body = doc.get("body") if isinstance(doc.get("body"), dict) else {}
            affiliate_orders += 1
            affiliate_gmv += float(body.get("current_total_price") or body.get("total_price") or 0.0)
    commission_rate = float(verification.get("commission_rate_override") or STUDENT_COMMISSION_RATE) if verification else 0.0
    return {
        "user": {
            "id": int(user["id"]),
            "name": user["name"] or "",
            "email": user["email"] or "",
            "creator_code": user["creator_code"] or "",
        },
        "student": verification,
        "identity_cards": [
            {
                "qr_id": row["qr_id"] or "",
                "school_id": row["school_id"] or "",
                "display_serial": row["display_serial"] or "",
                "status": row["status"] or "",
                "card_image_url": row["card_image_url"] or "",
                "claim_url": row["claim_url"] or "",
                "bound_at": row["bound_at"] or "",
            }
            for row in qr_rows
        ],
        "submissions": {
            "total": int((submissions["total"] if submissions else 0) or 0),
            "approved": int((submissions["confirmed"] if submissions else 0) or 0),
            "avg_campaign_score": round(float((submissions["avg_campaign_score"] if submissions else 0.0) or 0.0), 2),
        },
        "affiliate": {
            "orders": affiliate_orders,
            "gmv": round(affiliate_gmv, 2),
            "estimated_commission": round(affiliate_gmv * commission_rate, 2),
            "commission_rate": commission_rate,
            "shopify_orders_scanned": int((order_stats["total"] if order_stats else 0) or 0),
        },
        "recent_scans": scans,
        "audit_log": list_student_identity_audit(user_id=int(user_id), limit=24),
    }
