"""KOL lookup, claim, release, reassignment, and list actions."""
from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

from app.db.connection import get_conn
from app.domains.kol import claim_audit
from app.domains.kol import claim_listing
from app.services.vkpi import scope
from app.services.vkpi.schema import ensure_vkpi_schema
from app.services.vkpi.workflow import staff_id
from app.services.vkpi.kol_claims_common import (
    SUPPORTED_PLATFORMS,
    assert_kol_access,
    _claim_payload,
    _create_kol,
    _find_kol,
    _int,
    _json,
    _json_array,
    dedup_key,
    normalize_handle,
    normalize_platform,
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
    ensure_vkpi_schema()
    platform = normalize_platform(str(body.get("platform") or ""))
    handle = normalize_handle(str(body.get("handle") or body.get("handle_or_url") or body.get("url") or ""), platform)
    if not platform or platform not in SUPPORTED_PLATFORMS:
        raise ValueError("supported platform required")
    if not handle:
        raise ValueError("handle_or_url required")
    actor_staff_id = staff_id(staff)
    conn = get_conn()
    kol = _find_kol(platform, handle)
    created = False
    if not kol and body.get("create_if_missing"):
        kol = _create_kol(platform, handle, body, actor_staff_id)
        created = True
        conn.commit()
        if kol:
            _log_kol_audit(
                actor_staff_id=actor_staff_id,
                action_type="kol_lookup_create",
                kol_id=_int(kol.get("id")),
                detail=f"{platform}:{handle}",
                metadata={"platform": platform, "handle": handle, "source": "lookup_create_if_missing"},
            )
    active_claim = None
    if kol:
        active_claim = conn.execute(
            """
            SELECT c.*, u.name AS staff_name, u.email AS staff_email
            FROM vkpi_kol_claims c
            LEFT JOIN staff st ON st.id = c.staff_id
            LEFT JOIN users u ON u.id = st.user_id
            WHERE c.kol_id=? AND c.status='active'
            ORDER BY c.claimed_at DESC
            LIMIT 1
            """,
            (_int(kol.get("id")),),
        ).fetchone()
    return {
        "query": {"platform": platform, "handle": handle, "dedup_key": dedup_key(platform, handle, body.get("email", ""))},
        "kol": kol,
        "created": created,
        "claim": _claim_payload(active_claim),
        "can_claim": bool(kol and not active_claim),
    }

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
            _json(payload.get("metadata")),
            now,
            now,
        ),
    )
    conn.execute("UPDATE kols SET assigned_staff_id=?, updated_at=? WHERE id=?", (actor_staff_id, now, _int(kol_id)))
    conn.commit()
    row = conn.execute("SELECT * FROM vkpi_kol_claims WHERE kol_id=? AND status='active'", (_int(kol_id),)).fetchone()
    claim_id = _int(dict(row).get("id")) if row else 0
    _log_kol_audit(
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
    return {"claim": _claim_payload(row)}

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
    ensure_vkpi_schema()
    assert_kol_access(int(kol_id), staff, allow_unclaimed=True)
    actor_staff_id = staff_id(staff)
    allowed = {
        "avatar_url": "avatar_url",
        "profile_url": "profile_url",
        "contact_email": "contact_email",
        "contact_phone": "contact_phone",
        "notes": "notes",
        "media_name": "media_name",
        "owner_name": "owner_name",
        "country": "country",
        "niche": "niche",
        "primary_category": "primary_category",
        "promoted_product": "promoted_product",
        "follower_count": "follower_count",
        "avg_views": "avg_views",
    }
    updates: list[str] = []
    params: list[Any] = []
    for key, column in allowed.items():
        if key not in body:
            continue
        value = body.get(key)
        if column in {"follower_count", "avg_views"}:
            params.append(_int(value))
        else:
            params.append(str(value or "").strip())
        updates.append(f"{column}=?")
    if "contact_links" in body:
        updates.append("contact_links_json=?")
        params.append(_json_array(body.get("contact_links")))
    if "contact_raw" in body:
        updates.append("contact_raw_json=?")
        params.append(_json(body.get("contact_raw")))
    if not updates:
        row = get_conn().execute("SELECT * FROM kols WHERE id=?", (int(kol_id),)).fetchone()
        return {"kol": dict(row) if row else {}}
    now = utcnow()
    updates.append("updated_at=?")
    params.append(now)
    params.append(int(kol_id))
    conn = get_conn()
    conn.execute(f"UPDATE kols SET {', '.join(updates)} WHERE id=?", params)
    conn.commit()
    row = conn.execute("SELECT * FROM kols WHERE id=?", (int(kol_id),)).fetchone()
    changed_fields = [allowed[key] for key in allowed if key in body]
    if "contact_links" in body:
        changed_fields.append("contact_links_json")
    if "contact_raw" in body:
        changed_fields.append("contact_raw_json")
    _log_kol_audit(
        actor_staff_id=actor_staff_id,
        action_type="kol_manual_update",
        kol_id=int(kol_id),
        detail=",".join(changed_fields),
        metadata={"changed_fields": changed_fields},
    )
    return {"kol": dict(row) if row else {}}
