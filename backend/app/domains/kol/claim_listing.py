"""KOL claim listing use cases."""
from __future__ import annotations

from typing import Any

from app.db.connection import get_conn
from app.domains.kol.identity import normalize_platform
from app.domains.access import scope
from app.platform.db.schema import ensure_vkpi_schema


def staff_scope_where(staff: dict[str, Any] | None, staff_id: int | None = None) -> tuple[str, list[Any]]:
    scoped_staff_id = scope.effective_staff_id(staff, staff_id)
    if not scoped_staff_id:
        return "", []
    return (
        """
        WHERE (
            k.assigned_staff_id=?
            OR k.created_by_staff_id=?
            OR EXISTS (
                SELECT 1
                FROM vkpi_kol_claims c2
                WHERE c2.kol_id=k.id AND c2.staff_id=?
            )
            OR EXISTS (
                SELECT 1
                FROM vkpi_projects p2
                WHERE p2.kol_id=k.id AND (p2.assigned_staff_id=? OR p2.created_by_staff_id=?)
            )
        )
        """,
        [scoped_staff_id, scoped_staff_id, scoped_staff_id, scoped_staff_id, scoped_staff_id],
    )


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


def list_kols(
    *,
    limit: int = 100,
    search: str = "",
    platform: str = "",
    staff: dict[str, Any] | None = None,
    staff_id: int | None = None,
) -> dict[str, Any]:
    ensure_vkpi_schema()
    limit_i = max(1, min(500, int(limit or 100)))
    base_where, params = staff_scope_where(staff, staff_id)
    extra: list[str] = []
    search_text = str(search or "").strip().lower()
    if search_text:
        extra.append(
            """
            (
                lower(k.channel_name) LIKE ?
                OR lower(k.media_name) LIKE ?
                OR lower(k.owner_name) LIKE ?
                OR lower(k.channel_url) LIKE ?
                OR lower(k.contact_email) LIKE ?
            )
            """
        )
        like = f"%{search_text}%"
        params.extend([like, like, like, like, like])
    platform_text = normalize_platform(str(platform or ""))
    if platform_text:
        extra.append("lower(k.platform)=lower(?)")
        params.append(platform_text)
    if base_where and extra:
        where = base_where + " AND " + " AND ".join(f"({item})" for item in extra)
    elif base_where:
        where = base_where
    elif extra:
        where = "WHERE " + " AND ".join(f"({item})" for item in extra)
    else:
        where = ""
    rows = get_conn().execute(
        f"""
        SELECT
            k.*,
            c.id AS active_claim_id,
            c.staff_id AS claim_staff_id,
            u.name AS claim_staff_name,
            u.email AS claim_staff_email,
            s.follower_count AS snapshot_follower_count,
            s.content_count AS snapshot_content_count,
            s.avg_views AS snapshot_avg_views,
            s.engagement_rate AS snapshot_engagement_rate,
            s.total_likes AS snapshot_total_likes,
            s.scan_status AS snapshot_scan_status,
            s.scanned_at AS snapshot_scanned_at
        FROM kols k
        LEFT JOIN vkpi_kol_claims c ON c.kol_id=k.id AND c.status='active'
        LEFT JOIN staff st ON st.id=c.staff_id
        LEFT JOIN users u ON u.id=st.user_id
        LEFT JOIN kol_account_snapshots s ON s.id = (
            SELECT s2.id
            FROM kol_account_snapshots s2
            WHERE s2.kol_id=k.id
            ORDER BY s2.scanned_at DESC, s2.id DESC
            LIMIT 1
        )
        {where}
        ORDER BY k.updated_at DESC, k.id DESC
        LIMIT ?
        """,
        (*params, limit_i),
    ).fetchall()
    from app.domains.kol.pool_common import mask_pool_item

    return {
        # Bulk roster responses expose contact availability but never plaintext.
        "kols": [mask_pool_item(dict(row)) for row in rows],
        "scope": scope.scope_context(staff, staff_id),
    }
