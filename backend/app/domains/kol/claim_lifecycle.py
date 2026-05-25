"""KOL claim lifecycle use cases."""
from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

from app.db.connection import get_conn
from app.domains.kol import claim_audit
from app.domains.kol.claim_payloads import claim_payload, json_object
from app.domains.kol.claim_store import utcnow
from app.domains.kol.payload_utils import _int
from app.services.vkpi.schema import ensure_vkpi_schema
from app.services.vkpi.workflow import staff_id


def claim(kol_id: int, body: dict[str, Any] | None = None, *, staff: dict[str, Any] | None = None) -> dict[str, Any]:
    ensure_vkpi_schema()
    payload = body or {}
    actor_staff_id = staff_id(staff) or _int(payload.get("staff_id"))
    if not actor_staff_id:
        raise ValueError("staff_id required")
    conn = get_conn()
    kol = conn.execute("SELECT * FROM kols WHERE id=?", (_int(kol_id),)).fetchone()
    if not kol:
        raise LookupError("kol not found")
    existing = conn.execute(
        "SELECT * FROM vkpi_kol_claims WHERE kol_id=? AND status='active' LIMIT 1",
        (_int(kol_id),),
    ).fetchone()
    if existing:
        raise ValueError("kol already claimed")
    now = utcnow()
    expires_days = max(1, min(90, _int(payload.get("expires_days"), 14)))
    expires_at = (datetime.utcnow() + timedelta(days=expires_days)).strftime("%Y-%m-%dT%H:%M:%SZ")
    conn.execute(
        """
        INSERT INTO vkpi_kol_claims (
            kol_id, staff_id, project_id, status, claimed_at, expires_at,
            last_effective_touch_at, metadata_json, created_at, updated_at
        ) VALUES (?,?,?,?,?,?,?,?,?,?)
        """,
        (
            _int(kol_id),
            actor_staff_id,
            _int(payload.get("project_id")) or None,
            "active",
            now,
            expires_at,
            now,
            json_object(payload.get("metadata")),
            now,
            now,
        ),
    )
    conn.execute("UPDATE kols SET assigned_staff_id=?, updated_at=? WHERE id=?", (actor_staff_id, now, _int(kol_id)))
    conn.commit()
    row = conn.execute("SELECT * FROM vkpi_kol_claims WHERE kol_id=? AND status='active'", (_int(kol_id),)).fetchone()
    claim_id = _int(dict(row).get("id")) if row else 0
    claim_audit.log_kol_audit(
        actor_staff_id=actor_staff_id,
        action_type="kol_claim_create",
        kol_id=_int(kol_id),
        detail=f"claim_id={claim_id}",
        metadata={
            "claim_id": claim_id,
            "project_id": _int(payload.get("project_id")) or None,
            "expires_at": expires_at,
        },
    )
    return {"claim": claim_payload(row)}
