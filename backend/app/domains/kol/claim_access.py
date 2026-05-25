"""KOL claim access checks."""
from __future__ import annotations

from typing import Any

from app.db.connection import get_conn
from app.services.vkpi import scope


def _int(value: Any, default: int = 0) -> int:
    try:
        return int(value or default)
    except (TypeError, ValueError):
        return default


def assert_kol_access(kol_id: int, staff: dict[str, Any] | None, *, allow_unclaimed: bool = False) -> None:
    if scope.can_view_all(staff):
        return
    actor = scope.actor_staff_id(staff)
    if not actor:
        raise scope.ScopeDenied("kol scope denied")
    conn = get_conn()
    kol = conn.execute(
        "SELECT assigned_staff_id, created_by_staff_id FROM kols WHERE id=?",
        (int(kol_id),),
    ).fetchone()
    if not kol:
        raise LookupError("kol not found")
    kol_data = dict(kol)
    assigned_staff_id = _int(kol_data.get("assigned_staff_id"))
    created_by_staff_id = _int(kol_data.get("created_by_staff_id"))
    active_claim = conn.execute(
        "SELECT staff_id FROM vkpi_kol_claims WHERE kol_id=? AND status='active' ORDER BY claimed_at DESC, id DESC LIMIT 1",
        (int(kol_id),),
    ).fetchone()
    if active_claim:
        claim_staff_id = _int(dict(active_claim).get("staff_id"))
        if claim_staff_id == actor:
            return
        raise scope.ScopeDenied("kol scope denied")
    if actor in {assigned_staff_id, created_by_staff_id}:
        return
    if allow_unclaimed and not assigned_staff_id:
        return
    found = conn.execute(
        """
        SELECT 1
        FROM vkpi_kol_claims
        WHERE kol_id=? AND staff_id=?
        UNION
        SELECT 1
        FROM vkpi_projects
        WHERE kol_id=? AND (assigned_staff_id=? OR created_by_staff_id=?)
        UNION
        SELECT 1
        FROM vkpi_links
        WHERE kol_id=? AND (staff_id=? OR created_by_staff_id=?)
        UNION
        SELECT 1
        FROM vkpi_sales_attributions
        WHERE kol_id=? AND staff_id=?
        LIMIT 1
        """,
        (int(kol_id), actor, int(kol_id), actor, actor, int(kol_id), actor, actor, int(kol_id), actor),
    ).fetchone()
    if not found:
        raise scope.ScopeDenied("kol scope denied")
