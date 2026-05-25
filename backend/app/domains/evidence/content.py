"""Content post and asset operations for V-KPI evidence resources."""
from __future__ import annotations

from typing import Any

from app.db.connection import get_conn, is_postgres_runtime
from app.domains import audit
from app.domains.access import scope
from app.domains.evidence.common import (
    _actor_id,
    _assert_content_access,
    _assert_kol_access,
    _int,
    _json,
    _project_context,
    _row,
    _rows,
    _utcnow,
)
from app.platform.db.schema import ensure_vkpi_schema

def _db_bool(value: Any) -> bool | int:
    return bool(value) if is_postgres_runtime() else (1 if value else 0)

def list_content(
    *,
    project_id: int | None = None,
    kol_id: int | None = None,
    staff_id: int | None = None,
    limit: int = 100,
    staff: dict[str, Any] | None = None,
) -> dict[str, Any]:
    ensure_vkpi_schema()
    conn = get_conn()
    where: list[str] = []
    params: list[Any] = []
    if project_id:
        _project_context(int(project_id), staff)
        where.append("cp.project_id = ?")
        params.append(int(project_id))
    if kol_id:
        _assert_kol_access(int(kol_id), staff)
        where.append("cp.kol_id = ?")
        params.append(int(kol_id))
    scoped_staff = scope.effective_staff_id(staff, staff_id)
    if scoped_staff:
        where.append(
            """
            (
                p.assigned_staff_id = ?
                OR p.created_by_staff_id = ?
                OR c.staff_id = ?
            )
            """
        )
        params.extend([scoped_staff, scoped_staff, scoped_staff])
    sql = """
        SELECT
            cp.*,
            p.project_name,
            p.stage AS project_stage,
            k.channel_name AS kol_name,
            k.channel_url AS kol_url,
            COALESCE(a.asset_count, 0) AS asset_count
        FROM vkpi_content_posts cp
        LEFT JOIN vkpi_projects p ON p.id = cp.project_id
        LEFT JOIN kols k ON k.id = cp.kol_id
        LEFT JOIN vkpi_kol_claims c ON c.kol_id = cp.kol_id AND c.status = 'active'
        LEFT JOIN (
            SELECT post_id, COUNT(*) AS asset_count
            FROM vkpi_content_assets
            GROUP BY post_id
        ) a ON a.post_id = cp.id
    """
    if where:
        sql += " WHERE " + " AND ".join(f"({item})" for item in where)
    sql += " ORDER BY cp.published_at DESC, cp.id DESC LIMIT ?"
    params.append(max(1, min(500, int(limit or 100))))
    rows = _rows(conn.execute(sql, params).fetchall())
    return {"content": rows, "count": len(rows)}

def create_content(body: dict[str, Any], *, staff: dict[str, Any] | None = None) -> dict[str, Any]:
    ensure_vkpi_schema()
    conn = get_conn()
    post_url = str(body.get("post_url") or body.get("url") or "").strip()
    if not post_url:
        raise ValueError("post_url required")
    project_id = _int(body.get("project_id"))
    kol_id = _int(body.get("kol_id"))
    project = _project_context(project_id, staff, write=True) if project_id else {}
    if project and not kol_id:
        kol_id = _int(project.get("kol_id"))
    if kol_id and not project_id:
        _assert_kol_access(kol_id, staff, write=True)
    if not project_id and not kol_id and not scope.can_view_all(staff):
        raise scope.ScopeDenied("content must be attached to a project or KOL")
    now = _utcnow()
    platform = str(body.get("platform") or project.get("platform") or "")
    existing = conn.execute(
        """
        SELECT id
        FROM vkpi_content_posts
        WHERE post_url=? AND COALESCE(project_id, 0)=? AND COALESCE(kol_id, 0)=?
        ORDER BY id DESC
        LIMIT 1
        """,
        (post_url, project_id or 0, kol_id or 0),
    ).fetchone()
    if existing:
        post_id = int(existing["id"])
        conn.execute(
            """
            UPDATE vkpi_content_posts
            SET link_id=?, platform=?, title=?, thumbnail_url=?, published_at=?,
                content_type=?, views=?, likes=?, comments=?, shares=?,
                rights_status=?, ad_usage_allowed=?, metadata_json=?, updated_at=?
            WHERE id=?
            """,
            (
                _int(body.get("link_id")) or None,
                platform,
                str(body.get("title") or ""),
                str(body.get("thumbnail_url") or ""),
                body.get("published_at"),
                str(body.get("content_type") or "video"),
                _int(body.get("views")),
                _int(body.get("likes")),
                _int(body.get("comments")),
                _int(body.get("shares")),
                str(body.get("rights_status") or "unknown"),
                _db_bool(body.get("ad_usage_allowed")),
                _json(body.get("metadata")),
                now,
                post_id,
            ),
        )
    else:
        conn.execute(
            """
            INSERT INTO vkpi_content_posts (
                project_id, kol_id, link_id, platform, post_url, title, thumbnail_url,
                published_at, content_type, views, likes, comments, shares,
                rights_status, ad_usage_allowed, metadata_json, created_at, updated_at
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                project_id or None,
                kol_id or None,
                _int(body.get("link_id")) or None,
                platform,
                post_url,
                str(body.get("title") or ""),
                str(body.get("thumbnail_url") or ""),
                body.get("published_at"),
                str(body.get("content_type") or "video"),
                _int(body.get("views")),
                _int(body.get("likes")),
                _int(body.get("comments")),
                _int(body.get("shares")),
                str(body.get("rights_status") or "unknown"),
                _db_bool(body.get("ad_usage_allowed")),
                _json(body.get("metadata")),
                now,
                now,
            ),
        )
        post_id = int(
            conn.execute(
                "SELECT id FROM vkpi_content_posts WHERE post_url=? ORDER BY id DESC LIMIT 1",
                (post_url,),
            ).fetchone()["id"]
        )
    conn.commit()
    row = conn.execute("SELECT * FROM vkpi_content_posts WHERE id=?", (post_id,)).fetchone()
    item = _row(row)
    asset_url = str(body.get("asset_url") or "").strip()
    if asset_url:
        add_content_asset(post_id, {"asset_url": asset_url, "asset_type": body.get("asset_type") or body.get("content_type") or "content", "usage_rights": body.get("rights_status")}, staff=staff)
    audit.log_business_event(
        staff_id=_actor_id(staff),
        action_type="content_capture",
        target_type="content_post",
        target_id=post_id,
        detail=post_url,
        metadata={"project_id": project_id or None, "kol_id": kol_id or None, "platform": platform},
    )
    return item

def get_content(post_id: int, *, staff: dict[str, Any] | None = None) -> dict[str, Any]:
    ensure_vkpi_schema()
    post = _assert_content_access(int(post_id), staff)
    assets = _rows(
        get_conn()
        .execute(
            "SELECT * FROM vkpi_content_assets WHERE post_id=? ORDER BY created_at DESC, id DESC",
            (int(post_id),),
        )
        .fetchall()
    )
    return {"content": post, "assets": assets}

def add_content_asset(post_id: int, body: dict[str, Any], *, staff: dict[str, Any] | None = None) -> dict[str, Any]:
    ensure_vkpi_schema()
    post = _assert_content_access(int(post_id), staff, write=True)
    asset_url = str(body.get("asset_url") or body.get("url") or "").strip()
    if not asset_url:
        raise ValueError("asset_url required")
    conn = get_conn()
    now = _utcnow()
    conn.execute(
        """
        INSERT INTO vkpi_content_assets (
            post_id, project_id, asset_url, asset_type, usage_rights, metadata_json, created_at
        ) VALUES (?,?,?,?,?,?,?)
        """,
        (
            int(post_id),
            _int(post.get("project_id")) or None,
            asset_url,
            str(body.get("asset_type") or ""),
            str(body.get("usage_rights") or "unknown"),
            _json(body.get("metadata")),
            now,
        ),
    )
    conn.commit()
    row = conn.execute(
        "SELECT * FROM vkpi_content_assets WHERE post_id=? ORDER BY id DESC LIMIT 1",
        (int(post_id),),
    ).fetchone()
    item = _row(row)
    if item:
        audit.log_business_event(
            staff_id=_actor_id(staff),
            action_type="content_asset_add",
            target_type="content_post",
            target_id=post_id,
            detail=asset_url,
            metadata={"asset_id": item.get("id"), "project_id": post.get("project_id"), "kol_id": post.get("kol_id")},
        )
    return item
