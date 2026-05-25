"""KOL manual update use case."""
from __future__ import annotations

from typing import Any

from app.db.connection import get_conn
from app.domains.kol import claim_access
from app.domains.kol import claim_audit
from app.domains.kol.claim_payloads import json_array, json_object
from app.domains.kol.claim_store import utcnow
from app.domains.kol.payload_utils import _int
from app.platform.db.schema import ensure_vkpi_schema
from app.domains.projects.workflow import staff_id


ALLOWED_MANUAL_FIELDS = {
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


def update_kol_manual(kol_id: int, body: dict[str, Any], *, staff: dict[str, Any] | None = None) -> dict[str, Any]:
    ensure_vkpi_schema()
    claim_access.assert_kol_access(int(kol_id), staff, allow_unclaimed=True)
    actor_staff_id = staff_id(staff)
    updates: list[str] = []
    params: list[Any] = []
    for key, column in ALLOWED_MANUAL_FIELDS.items():
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
        params.append(json_array(body.get("contact_links")))
    if "contact_raw" in body:
        updates.append("contact_raw_json=?")
        params.append(json_object(body.get("contact_raw")))
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
    changed_fields = [ALLOWED_MANUAL_FIELDS[key] for key in ALLOWED_MANUAL_FIELDS if key in body]
    if "contact_links" in body:
        changed_fields.append("contact_links_json")
    if "contact_raw" in body:
        changed_fields.append("contact_raw_json")
    claim_audit.log_kol_audit(
        actor_staff_id=actor_staff_id,
        action_type="kol_manual_update",
        kol_id=int(kol_id),
        detail=",".join(changed_fields),
        metadata={"changed_fields": changed_fields},
    )
    return {"kol": dict(row) if row else {}}
