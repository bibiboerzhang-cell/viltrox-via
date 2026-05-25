"""KOL lookup, claim, release, reassignment, and list actions."""
from __future__ import annotations

from typing import Any

from app.db.connection import get_conn
from app.domains.kol import claim_audit
from app.domains.kol import claim_lifecycle
from app.domains.kol import claim_listing
from app.domains.kol import claim_lookup
from app.domains.kol import manual_update
from app.services.vkpi import scope
from app.services.vkpi.schema import ensure_vkpi_schema
from app.services.vkpi.workflow import staff_id
from app.services.vkpi.kol_claims_common import (
    _int,
    utcnow,
)

def _log_kol_audit(
    *,
    actor_staff_id: int,
    action_type: str,
    kol_id: int,
    detail: str = "",
    metadata: dict[str, Any] | None = None,
) -> None:
    claim_audit.log_kol_audit(
        actor_staff_id=actor_staff_id,
        action_type=action_type,
        kol_id=kol_id,
        detail=detail,
        metadata=metadata,
    )


def lookup(body: dict[str, Any], *, staff: dict[str, Any] | None = None) -> dict[str, Any]:
    return claim_lookup.lookup(body, staff=staff)

def claim(kol_id: int, body: dict[str, Any] | None = None, *, staff: dict[str, Any] | None = None) -> dict[str, Any]:
    return claim_lifecycle.claim(kol_id, body, staff=staff)

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
    _log_kol_audit(
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
    _log_kol_audit(
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

def list_claims(status: str = "active", limit: int = 100, *, staff: dict[str, Any] | None = None, staff_id: int | None = None) -> dict[str, Any]:
    return claim_listing.list_claims(status=status, limit=limit, staff=staff, staff_id=staff_id)


def list_kols(
    *,
    limit: int = 100,
    search: str = "",
    platform: str = "",
    staff: dict[str, Any] | None = None,
    staff_id: int | None = None,
) -> dict[str, Any]:
    return claim_listing.list_kols(search=search, platform=platform, limit=limit, staff=staff, staff_id=staff_id)


def update_kol_manual(kol_id: int, body: dict[str, Any], *, staff: dict[str, Any] | None = None) -> dict[str, Any]:
    return manual_update.update_kol_manual(kol_id, body, staff=staff)
