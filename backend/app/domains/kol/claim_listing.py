"""KOL claim listing use cases."""
from __future__ import annotations

from typing import Any

from app.db.connection import get_conn
from app.services.vkpi import scope
from app.services.vkpi.schema import ensure_vkpi_schema


def list_claims(status: str = "active", limit: int = 100, *, staff: dict[str, Any] | None = None, staff_id: int | None = None) -> dict[str, Any]:
    ensure_vkpi_schema()
    limit_i = max(1, min(500, int(limit or 100)))
    params: list[Any] = []
    where_parts: list[str] = []
    if status:
        where_parts.append("c.status=?")
        params.append(status)
    scoped_staff_id = scope.effective_staff_id(staff, staff_id)
    if scoped_staff_id:
        where_parts.append("c.staff_id=?")
        params.append(int(scoped_staff_id))
    where = "WHERE " + " AND ".join(where_parts) if where_parts else ""
    rows = get_conn().execute(
        f"""
        SELECT c.*, k.channel_name AS kol_name, k.platform AS platform, u.name AS staff_name, u.email AS staff_email
        FROM vkpi_kol_claims c
        LEFT JOIN kols k ON k.id = c.kol_id
        LEFT JOIN staff st ON st.id = c.staff_id
        LEFT JOIN users u ON u.id = st.user_id
        {where}
        ORDER BY c.updated_at DESC, c.id DESC
        LIMIT ?
        """,
        (*params, limit_i),
    ).fetchall()
    return {"claims": [dict(row) for row in rows], "scope": scope.scope_context(staff, staff_id)}
