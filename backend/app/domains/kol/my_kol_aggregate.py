"""P3: single read-only MY KOL aggregate (one SELECT bundle per staff member).

Assembles what MyKolPage currently fetches across 3-4 calls (dashboard slice,
official-matrix, pool favorites + detail bundles) into one additive payload.

Hard red lines honored here:
  - Pure SELECT. Zero writes. viltrox_fit_score is read-only; scoring left alone.
  - All access via the app's get_conn() with ? placeholders (sqlite-style, the
    runtime translates to Postgres). Values are always bound as params.
  - Field shapes for official_matrix / pool_favorites mirror the existing
    channels.official_account_matrix and pool_favorites.list_favorites services
    so the frontend can swap callers later.
"""
from __future__ import annotations

import json
from typing import Any


def _json(value: Any, default: Any) -> Any:
    """psycopg may hand back jsonb as str OR already-parsed; parse defensively."""
    if value is None or value == "":
        return default
    if isinstance(value, (list, dict)):
        return value
    try:
        return json.loads(value)
    except (TypeError, ValueError):
        return default


def _int(value: Any, default: int = 0) -> int:
    try:
        return int(value or default)
    except (TypeError, ValueError):
        return default


def _staff_row(conn: Any, staff_id: int) -> dict[str, Any]:
    """staff has no name/email of its own — those live on the joined users row."""
    row = conn.execute(
        """
        SELECT s.id, s.role, s.active, s.user_id,
               COALESCE(u.name, u.email, 'Staff ' || s.id) AS name,
               COALESCE(u.email, '') AS email,
               COALESCE(u.avatar_url, '') AS avatar_url
        FROM staff s
        LEFT JOIN users u ON u.id = s.user_id
        WHERE s.id = ?
        """,
        (staff_id,),
    ).fetchone()
    if not row:
        raise LookupError(f"staff {staff_id} not found")
    item = dict(row)
    item["active"] = bool(_int(item.get("active"), 1))
    return item


def _official_matrix(conn: Any, staff_id: int) -> dict[str, Any]:
    """This staff's own channels + latest metrics, grouped by platform.

    Account-field names mirror channels.official_account_matrix so the frontend
    matrix renderer can read the same keys.
    """
    rows = conn.execute(
        """
        SELECT c.id, c.platform, c.account_handle, c.account_display_name,
               c.account_url, c.avatar_url, c.status,
               c.last_sync_at, c.last_sync_status, c.last_sync_error,
               m.snapshot_date,
               m.followers AS metric_followers,
               m.posts_count AS metric_posts,
               m.total_views AS metric_views,
               m.total_likes AS metric_likes,
               m.total_comments AS metric_comments,
               m.engagement_rate AS metric_engagement_rate,
               m.followers_delta AS metric_followers_delta,
               m.posts_delta AS metric_posts_delta,
               m.views_delta_24h AS metric_views_delta,
               m.captured_at AS metric_captured_at
        FROM vkpi_employee_channels c
        LEFT JOIN vkpi_channel_metrics m ON m.id = (
            SELECT mm.id FROM vkpi_channel_metrics mm
            WHERE mm.channel_id = c.id
            ORDER BY mm.snapshot_date DESC, mm.captured_at DESC, mm.id DESC
            LIMIT 1
        )
        WHERE c.staff_id = ? AND c.deleted_at IS NULL
        ORDER BY c.platform ASC, c.account_handle ASC, c.id ASC
        """,
        (staff_id,),
    ).fetchall()

    platforms: dict[str, dict[str, Any]] = {}
    total_views = total_posts = total_followers = 0
    for raw in rows:
        row = dict(raw)
        platform = str(row.get("platform") or "other").lower()
        entry = platforms.setdefault(
            platform,
            {"platform": platform, "total_followers": 0, "total_posts": 0, "total_views": 0, "accounts": []},
        )
        followers = _int(row.get("metric_followers"))
        posts = _int(row.get("metric_posts"))
        views = _int(row.get("metric_views"))
        entry["total_followers"] += followers
        entry["total_posts"] += posts
        entry["total_views"] += views
        total_followers += followers
        total_posts += posts
        total_views += views
        entry["accounts"].append(
            {
                "id": _int(row.get("id")),
                "platform": platform,
                "handle": str(row.get("account_handle") or ""),
                "display_name": str(row.get("account_display_name") or row.get("account_handle") or ""),
                "account_url": str(row.get("account_url") or ""),
                "avatar_url": str(row.get("avatar_url") or ""),
                "status": str(row.get("status") or ""),
                "sync_status": str(row.get("last_sync_status") or "not_configured"),
                "last_sync_at": row.get("last_sync_at") or row.get("metric_captured_at"),
                "last_sync_error": str(row.get("last_sync_error") or ""),
                "followers": followers,
                "followers_delta": _int(row.get("metric_followers_delta")),
                "posts_count": posts,
                "posts_delta": _int(row.get("metric_posts_delta")),
                "total_views": views,
                "views_delta": _int(row.get("metric_views_delta")),
                "total_likes": _int(row.get("metric_likes")),
                "total_comments": _int(row.get("metric_comments")),
                "engagement_rate": row.get("metric_engagement_rate"),
            }
        )
    return {
        "platforms": sorted(platforms.values(), key=lambda p: p["platform"]),
        "account_count": len(rows),
        "total_followers": total_followers,
        "total_posts": total_posts,
        "total_views": total_views,
    }


def _pool_favorites(conn: Any, staff_id: int) -> list[dict[str, Any]]:
    """This staff's pool favorites OR pool KOLs shared to them (P-GROUP-7) +
    active project links + public contacts.

    Drives off vkpi_kol_pool kp (LEFT JOIN the favorite row) so a pool KOL that
    was *shared* to this staff via vkpi_kol_pool_members (migration 159) — but
    never favorited by them — still surfaces in their MY KOL view. Honesty: the
    share grant is read-only visibility; favorite_id is NULL and is_shared=true
    for those rows (no ownership/claim is created). Mirrors
    pool_favorites.list_favorites field names; viltrox_fit_score is read only;
    projects_json comes back as jsonb (string or list) — parsed defensively.

    【M5】共享来源:shared_by(迁移 159,可空容旧)经 staff→users(sst/su)取共享人展示名
    shared_by_name,前端 MY KOL 卡片据此标「来自 XX 的共享」。sm 行按
    UNIQUE(kol_pool_id, staff_id) 至多一条,新增两个 LEFT JOIN 不会放大行数;
    纯读,零触归属/收藏/认领。
    """
    rows = conn.execute(
        """
        SELECT f.id AS favorite_id, kp.id AS kol_pool_id,
               COALESCE(f.note, '') AS note,
               COALESCE(f.created_at, sm.created_at) AS created_at,
               (f.id IS NULL) AS is_shared,
               sm.shared_by AS shared_by_staff_id,
               COALESCE(su.name, su.email, '') AS shared_by_name,
               kp.platform, kp.handle, kp.display_name, kp.followers,
               kp.viltrox_fit_score, kp.profile_url, kp.avatar_url, kp.country,
               (
                 SELECT json_agg(json_build_object(
                   'project_id', p.id, 'project_name', p.project_name,
                   'stage', a.stage, 'stage_status', a.stage_status))
                 FROM vkpi_project_kol_assignments a
                 JOIN vkpi_projects p ON p.id = a.project_id
                 WHERE a.kol_pool_id = kp.id
                   AND COALESCE(a.stage,'') NOT IN ('churned','cancelled','lost')
                   AND COALESCE(p.restricted, FALSE) = FALSE
               ) AS projects_json,
               (
                 SELECT json_agg(json_build_object(
                   'contact_type', ct.contact_type, 'contact_value', ct.contact_value,
                   'contact_source', ct.contact_source, 'consent_basis', ct.consent_basis))
                 FROM vkpi_kol_pool_contacts ct
                 WHERE ct.kol_pool_id = kp.id
               ) AS contacts_json
        FROM vkpi_kol_pool kp
        LEFT JOIN vkpi_kol_pool_favorites f
               ON f.kol_pool_id = kp.id AND f.staff_id = ?
        LEFT JOIN vkpi_kol_pool_members sm
               ON sm.kol_pool_id = kp.id AND sm.staff_id = ?
        LEFT JOIN staff sst ON sst.id = sm.shared_by
        LEFT JOIN users su ON su.id = sst.user_id
        WHERE (f.id IS NOT NULL OR sm.id IS NOT NULL)
          AND kp.duplicate_of_id IS NULL
        ORDER BY COALESCE(f.created_at, sm.created_at) DESC, kp.id DESC
        """,
        (staff_id, staff_id),
    ).fetchall()
    items: list[dict[str, Any]] = []
    for raw in rows:
        item = dict(raw)
        item["projects"] = _json(item.pop("projects_json", None), [])
        item["contacts"] = _json(item.pop("contacts_json", None), [])
        items.append(item)
    return items


def _projects(conn: Any, staff: dict[str, Any], requested_staff_id: int | None) -> list[dict[str, Any]]:
    """This staff's projects via the shared project_filter scope (own-only for employees)."""
    from app.domains.access import scope

    where, params = scope.project_filter("p", staff, requested_staff_id)
    clause = f"WHERE {where}" if where else ""
    rows = conn.execute(
        f"""
        SELECT p.id, p.project_name, p.product_sku, p.product_name, p.platform,
               p.stage, p.stage_status, p.priority,
               p.assigned_staff_id, p.created_by_staff_id,
               p.target_post_date, p.due_at, p.last_activity_at, p.updated_at
        FROM vkpi_projects p
        {clause}
        ORDER BY p.updated_at DESC, p.id DESC
        """,  # noqa: S608 — clause is from scope.project_filter (parameterized, no literal interpolation)
        tuple(params),
    ).fetchall()
    return [dict(row) for row in rows]


def _claims(conn: Any, staff_id: int) -> list[dict[str, Any]]:
    """This staff's KOL claims (claims FK kols(id), so enrich with kols, not pool)."""
    rows = conn.execute(
        """
        SELECT cl.id, cl.kol_id, cl.project_id, cl.status,
               cl.claimed_at, cl.expires_at, cl.last_effective_touch_at,
               k.channel_name AS kol_name, k.platform AS kol_platform
        FROM vkpi_kol_claims cl
        LEFT JOIN kols k ON k.id = cl.kol_id
        WHERE cl.staff_id = ?
        ORDER BY cl.claimed_at DESC, cl.id DESC
        """,
        (staff_id,),
    ).fetchall()
    return [dict(row) for row in rows]


def _kpi_summary(
    favorites: list[dict[str, Any]],
    projects: list[dict[str, Any]],
    claims: list[dict[str, Any]],
) -> dict[str, int]:
    """Cheap counts derived in Python from already-fetched rows (no extra queries)."""
    favorites_count = len(favorites)
    in_project_count = sum(1 for f in favorites if f.get("projects"))
    published_stages = {"content_posted", "reviewed", "published"}
    published_count = sum(
        1
        for f in favorites
        for pr in (f.get("projects") or [])
        if str((pr or {}).get("stage") or "").lower() in published_stages
    )
    claimed_count = sum(1 for c in claims if str(c.get("status") or "").lower() == "active")
    return {
        "favorites_count": favorites_count,
        "claimed_count": claimed_count,
        "in_project_count": in_project_count,
        "published_count": published_count,
        "projects_count": len(projects),
    }


def build_my_kol_aggregate(
    conn: Any,
    staff_id: int,
    window_days: int = 30,
    *,
    actor: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Assemble the full MY KOL payload for one staff member in a single bundle.

    Pure read. `staff_id` is the already-scoped target (the router resolves it
    from the actor and enforces employee own-only). `actor` is the real logged-in
    staff dict, used only for project scope (a manager viewing a member still
    filters projects to that member via project_filter's requested_staff_id).
    """
    staff_id = _int(staff_id)
    staff = _staff_row(conn, staff_id)
    favorites = _pool_favorites(conn, staff_id)
    projects = _projects(conn, actor or staff, staff_id)
    claims = _claims(conn, staff_id)
    return {
        "staff": staff,
        "window_days": _int(window_days, 30),
        "official_matrix": _official_matrix(conn, staff_id),
        "pool_favorites": favorites,
        "projects": projects,
        "claims": claims,
        "kpi_summary": _kpi_summary(favorites, projects, claims),
    }
