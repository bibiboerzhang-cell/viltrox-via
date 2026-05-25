"""KOL lookup, claim, release, reassignment, and list actions."""
from __future__ import annotations

from typing import Any

from app.domains.kol import claim_audit
from app.domains.kol import claim_lifecycle
from app.domains.kol import claim_listing
from app.domains.kol import claim_lookup
from app.domains.kol import manual_update

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
    return claim_lifecycle.release(claim_id, body, staff=staff)

def reassign(claim_id: int, body: dict[str, Any], *, staff: dict[str, Any] | None = None) -> dict[str, Any]:
    return claim_lifecycle.reassign(claim_id, body, staff=staff)

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
