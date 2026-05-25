"""KOL claim/profile use cases."""
from __future__ import annotations

from typing import Any

from app.services.vkpi import kol_claims


def lookup(body: dict[str, Any], *, staff: dict[str, Any]) -> dict[str, Any]:
    return kol_claims.lookup(body, staff=staff)


def assert_kol_access(
    kol_id: int,
    staff: dict[str, Any],
    *,
    allow_unclaimed: bool = False,
) -> None:
    kol_claims.assert_kol_access(kol_id, staff, allow_unclaimed=allow_unclaimed)


def list_kols(
    *,
    search: str = "",
    platform: str = "",
    staff_id: int | None = None,
    limit: int = 100,
    staff: dict[str, Any],
) -> dict[str, Any]:
    return kol_claims.list_kols(search=search, platform=platform, staff_id=staff_id, limit=limit, staff=staff)


def profile(kol_id: int, *, staff: dict[str, Any]) -> dict[str, Any]:
    return kol_claims.profile(kol_id, staff=staff)


def update_kol_manual(kol_id: int, body: dict[str, Any], *, staff: dict[str, Any]) -> dict[str, Any]:
    return kol_claims.update_kol_manual(kol_id, body, staff=staff)


def list_claims(*, status: str = "active", limit: int = 100, staff: dict[str, Any]) -> dict[str, Any]:
    return kol_claims.list_claims(status=status, limit=limit, staff=staff)


def claim(kol_id: int, body: dict[str, Any], *, staff: dict[str, Any]) -> dict[str, Any]:
    return kol_claims.claim(kol_id, body, staff=staff)


def release(claim_id: int, body: dict[str, Any], *, staff: dict[str, Any]) -> dict[str, Any]:
    return kol_claims.release(claim_id, body, staff=staff)


def reassign(claim_id: int, body: dict[str, Any], *, staff: dict[str, Any]) -> dict[str, Any]:
    return kol_claims.reassign(claim_id, body, staff=staff)
