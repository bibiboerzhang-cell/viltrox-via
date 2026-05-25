"""KOL lookup use case."""
from __future__ import annotations

from typing import Any

from app.db.connection import get_conn
from app.domains.kol import claim_audit
from app.domains.kol import claim_store
from app.domains.kol.claim_payloads import claim_payload
from app.domains.kol.identity import (
    SUPPORTED_PLATFORMS,
    dedup_key,
    normalize_handle,
    normalize_platform,
)
from app.platform.db.schema import ensure_vkpi_schema
from app.domains.projects.workflow import staff_id


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
    kol = claim_store.find_kol(platform, handle)
    created = False
    if not kol and body.get("create_if_missing"):
        kol = claim_store.create_kol(platform, handle, body, actor_staff_id)
        created = True
        conn.commit()
        if kol:
            claim_audit.log_kol_audit(
                actor_staff_id=actor_staff_id,
                action_type="kol_lookup_create",
                kol_id=int(kol.get("id") or 0),
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
            (int(kol.get("id") or 0),),
        ).fetchone()
    return {
        "query": {"platform": platform, "handle": handle, "dedup_key": dedup_key(platform, handle, body.get("email", ""))},
        "kol": kol,
        "created": created,
        "claim": claim_payload(active_claim),
        "can_claim": bool(kol and not active_claim),
    }
