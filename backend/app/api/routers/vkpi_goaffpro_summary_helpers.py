"""Pure query and projection helpers for the cached GOAFFPRO summary route."""
from __future__ import annotations

from fastapi import HTTPException

from app.domains.access import scope


def project_kol_ids(conn, project_id: int) -> set[int]:
    rows = conn.execute(
        "SELECT DISTINCT kol_pool_id FROM vkpi_project_kol_assignments WHERE project_id = ?",
        (project_id,),
    ).fetchall()
    return {
        int(dict(row)["kol_pool_id"])
        for row in rows
        if dict(row).get("kol_pool_id") is not None
    }


def build_where(
    staff,
    project_ids: set[int] | None,
    search: str | None,
) -> tuple[str, list]:
    where = "COALESCE(l.affiliate_id, '') <> '' AND COALESCE(l.ref_code, '') <> ''"
    params: list = []
    if not scope.can_view_all(staff):
        actor_id = scope.actor_staff_id(staff)
        if actor_id <= 0:
            raise HTTPException(status_code=403, detail="staff_identity_required")
        where += """
            AND (
                EXISTS (
                    SELECT 1 FROM vkpi_kol_pool_favorites own_favorite
                    WHERE own_favorite.kol_pool_id = l.kol_pool_id
                      AND own_favorite.staff_id = ?
                )
                OR EXISTS (
                    SELECT 1 FROM vkpi_kol_pool_members shared_member
                    WHERE shared_member.kol_pool_id = l.kol_pool_id
                      AND shared_member.staff_id = ?
                )
            )
        """
        params.extend([int(actor_id), int(actor_id)])
    if project_ids is not None:
        where += " AND l.kol_pool_id IN (" + ",".join(["?"] * len(project_ids)) + ")"
        params.extend(sorted(project_ids))
    keyword = str(search or "").strip().lower()
    if keyword:
        like = f"%{keyword}%"
        where += (
            " AND (LOWER(COALESCE(kp.display_name,'')) LIKE ? OR LOWER(COALESCE(kp.handle,'')) LIKE ?"
            " OR LOWER(COALESCE(l.ref_code,'')) LIKE ? OR LOWER(COALESCE(l.coupon,'')) LIKE ?)"
        )
        params.extend([like, like, like, like])
    return where, params


def _summary_item(row) -> tuple[dict, bool, bool, str] | None:
    data = dict(row)
    affiliate_id = str(data.get("affiliate_id") or "")
    if not affiliate_id:
        return None
    handle = str(data.get("handle") or "").strip()
    name = str(data.get("display_name") or "").strip() or handle
    synced_at = data.get("m_synced_at")
    stale = synced_at in (None, "")
    is_partial = bool(data.get("m_partial"))
    gmv_cents = int(data.get("m_gmv_cents") or 0)
    commission_cents = int(data.get("m_commission_cents") or 0)
    item = {
        "kol_pool_id": data.get("kol_pool_id"),
        "kol_name": name or f"KOL#{data.get('kol_pool_id')}",
        "kol_handle": handle,
        "kol_avatar": str(data.get("avatar_url") or ""),
        "kol_platform": str(data.get("platform") or ""),
        "affiliate_id": affiliate_id,
        "ref_code": data.get("ref_code"),
        "coupon": data.get("coupon"),
        "commission_rate": str(data.get("m_commission_rate") or ""),
        "status": str(data.get("m_status") or ""),
        "tracking_url": data.get("tracking_url"),
        "source_label": "GOAFFPRO",
        "source_type": "goaffpro",
        "product_sku": "—",
        "clicks": int(data.get("m_clicks") or 0),
        "orders": int(data.get("m_orders") or 0),
        "gmv_usd": round(gmv_cents / 100, 2),
        "commission_usd": round(commission_cents / 100, 2),
        "currency": str(data.get("m_currency") or ""),
        "partial": is_partial,
        "stale": stale,
    }
    return item, is_partial, stale, str(synced_at or "")


def summary_items(links) -> tuple[list[dict], int, int, str]:
    items: list[dict] = []
    partial_count = 0
    stale_count = 0
    last_synced_at = ""
    for row in links:
        resolved = _summary_item(row)
        if resolved is None:
            continue
        item, is_partial, stale, synced_at = resolved
        items.append(item)
        partial_count += int(is_partial)
        stale_count += int(stale)
        if not stale and synced_at > last_synced_at:
            last_synced_at = synced_at
    return items, partial_count, stale_count, last_synced_at


def summary_totals(items: list[dict]) -> dict:
    return {
        "kol_count": len(items),
        "clicks": sum(int(item.get("clicks") or 0) for item in items),
        "orders": sum(int(item.get("orders") or 0) for item in items),
        "gmv_usd": round(sum(float(item.get("gmv_usd") or 0) for item in items), 2),
        "commission_usd": round(
            sum(float(item.get("commission_usd") or 0) for item in items), 2
        ),
    }


def summary_note(items: list[dict], stale_count: int, partial_count: int) -> str | None:
    note = None if items else "尚无已建链的 KOL;在 KOL 详情或项目里生成追踪链后出现在此。"
    if stale_count:
        return f"{stale_count} 个 KOL 刚建链还没同步,点「刷新」拉取最新数据。"
    if partial_count:
        return f"⚠️ {partial_count} 个 KOL 上次同步查询失败,显示值可能偏低(非真零)。"
    return note
