"""KOL claim lifecycle use cases."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from app.db.connection import get_conn
from app.domains.kol import claim_audit
from app.domains.kol.claim_payloads import claim_payload, json_object
from app.domains.kol.claim_store import utcnow
from app.domains.kol.payload_utils import _int
from app.domains.access import scope
from app.platform.db.schema import ensure_vkpi_schema
from app.domains.projects.workflow import staff_id


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
    # 写前校验 project 存在:不存在则 404(LookupError),别让 project_id FK 违约冒 500。
    project_id = _int(payload.get("project_id")) or None
    if project_id:
        project = conn.execute("SELECT id FROM vkpi_projects WHERE id=?", (project_id,)).fetchone()
        if not project:
            raise LookupError("project not found")
    now = utcnow()
    expires_days = max(1, min(90, _int(payload.get("expires_days"), 14)))
    expires_at = (datetime.now(timezone.utc) + timedelta(days=expires_days)).strftime("%Y-%m-%dT%H:%M:%SZ")
    try:
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
                project_id,
                "active",
                now,
                expires_at,
                now,
                json_object(payload.get("metadata")),
                now,
                now,
            ),
        )
    except Exception as exc:  # noqa: BLE001
        # 并发兜底:SELECT-then-INSERT 有 TOCTOU 窗口,输的一方撞 idx_vkpi_kol_claims_one_active
        # 部分唯一索引(每 KOL 只允许一条 active);把裸 UniqueViolation 归一为干净 ValueError,
        # 避免 poisoned txn → 500。非唯一违约(真错)照旧上抛。
        conn.rollback()
        if "unique" in str(exc).lower() or "duplicate" in str(exc).lower() or "one_active" in str(exc).lower():
            raise ValueError("kol already claimed") from exc
        raise
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
            "project_id": project_id,
            "expires_at": expires_at,
        },
    )
    return {"claim": claim_payload(row)}


def release(claim_id: int, body: dict[str, Any] | None = None, *, staff: dict[str, Any] | None = None) -> dict[str, Any]:
    ensure_vkpi_schema()
    payload = body or {}
    actor_staff_id = staff_id(staff)
    conn = get_conn()
    row = conn.execute("SELECT * FROM vkpi_kol_claims WHERE id=?", (_int(claim_id),)).fetchone()
    if not row:
        raise LookupError("claim not found")
    row_data = dict(row)
    if not scope.can_view_all(staff):
        actor = actor_staff_id
        if not actor or actor != _int(row_data.get("staff_id")):
            raise scope.ScopeDenied("claim scope denied")
    now = utcnow()
    reason = str(payload.get("reason") or payload.get("release_reason") or "manual_release").strip()
    conn.execute(
        """
        UPDATE vkpi_kol_claims
        SET status='released', release_reason=?, released_at=?, released_by_staff_id=?, updated_at=?
        WHERE id=?
        """,
        (reason, now, actor_staff_id or None, now, _int(claim_id)),
    )
    conn.execute("UPDATE kols SET assigned_staff_id=NULL, updated_at=? WHERE id=?", (now, _int(row_data["kol_id"])))
    conn.commit()
    claim_audit.log_kol_audit(
        actor_staff_id=actor_staff_id,
        action_type="kol_claim_release",
        kol_id=_int(row_data["kol_id"]),
        detail=reason,
        metadata={"claim_id": _int(claim_id), "reason": reason},
    )
    return {"id": _int(claim_id), "status": "released", "release_reason": reason}


def reassign(claim_id: int, body: dict[str, Any], *, staff: dict[str, Any] | None = None) -> dict[str, Any]:
    ensure_vkpi_schema()
    next_staff_id = _int(body.get("staff_id") or body.get("to_staff_id"))
    if not next_staff_id:
        raise ValueError("to_staff_id required")
    conn = get_conn()
    row = conn.execute("SELECT * FROM vkpi_kol_claims WHERE id=?", (_int(claim_id),)).fetchone()
    if not row:
        raise LookupError("claim not found")
    actor_staff_id = staff_id(staff)
    previous_staff_id = _int(row["staff_id"])
    release(claim_id, {"reason": str(body.get("reason") or "reassigned")}, staff=staff)
    result = claim(
        _int(row["kol_id"]),
        {"staff_id": next_staff_id, "project_id": row["project_id"], "metadata": {"reassigned_from_claim_id": claim_id}},
        staff=None,
    )
    claim_audit.log_kol_audit(
        actor_staff_id=actor_staff_id,
        action_type="kol_claim_reassign",
        kol_id=_int(row["kol_id"]),
        detail=str(body.get("reason") or "reassigned"),
        metadata={
            "from_claim_id": _int(claim_id),
            "from_staff_id": previous_staff_id,
            "to_staff_id": next_staff_id,
            "new_claim_id": _int((result.get("claim") or {}).get("id")),
        },
    )
    return result
