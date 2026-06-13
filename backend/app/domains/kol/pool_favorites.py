"""KOL Pool 收藏持久层(四环漏斗 C2,战役第一段)。

表 vkpi_kol_pool_favorites(migration 107):staff 个人的 "My KOL" 归宿。
语义边界(四环裁决):收藏 ≠ promote(入役主表)≠ claim(认领跟进)。
staff 隔离:全部操作以真 staff.id 为键;list 仅返回本人收藏。
红线:零触 viltrox_fit_score / kol_pool 主表零写入(仅独立收藏表)。
"""
from __future__ import annotations

from typing import Any

from app.core.logging import get_logger
from app.db.connection import get_conn

logger = get_logger("viltrox.domains.kol.pool_favorites")


def _staff_id(staff: dict[str, Any] | None) -> int:
    if not staff:
        return 0
    for key in ("id", "staff_id"):
        try:
            value = int(staff.get(key) or 0)
        except (TypeError, ValueError):
            value = 0
        if value:
            return value
    return 0


def add_favorite(kol_pool_id: int, *, staff: dict[str, Any] | None = None, note: str = "") -> dict[str, Any]:
    """收藏一个 pool KOL。幂等:重复收藏返回 already_favorited 而非 500。"""
    sid = _staff_id(staff)
    if not sid:
        raise PermissionError("favorite requires a staff identity")
    conn = get_conn()
    row = conn.execute(
        "SELECT id FROM vkpi_kol_pool WHERE id=?", (int(kol_pool_id),)
    ).fetchone()
    if not row:
        raise LookupError("kol pool item not found")
    existing = conn.execute(
        "SELECT id FROM vkpi_kol_pool_favorites WHERE kol_pool_id=? AND staff_id=?",
        (int(kol_pool_id), sid),
    ).fetchone()
    if existing:
        return {"status": "already_favorited", "kol_pool_id": int(kol_pool_id), "favorite_id": int(dict(existing)["id"])}
    conn.execute(
        "INSERT INTO vkpi_kol_pool_favorites (kol_pool_id, staff_id, note) VALUES (?, ?, ?)",
        (int(kol_pool_id), sid, str(note or "")[:500] or None),
    )
    conn.commit()
    created = conn.execute(
        "SELECT id FROM vkpi_kol_pool_favorites WHERE kol_pool_id=? AND staff_id=?",
        (int(kol_pool_id), sid),
    ).fetchone()
    return {"status": "favorited", "kol_pool_id": int(kol_pool_id), "favorite_id": int(dict(created)["id"]) if created else None}


def remove_favorite(kol_pool_id: int, *, staff: dict[str, Any] | None = None) -> dict[str, Any]:
    """取消收藏。幂等:未收藏返回 not_favorited。

    注:在役软禁止(C8,取消收藏在役 KOL → 409+项目清单+force)按战役计划属第一段
    后续子单,落地时在此函数前置检查;本端点先行版本不拦截(收藏数据可随时重收)。
    """
    sid = _staff_id(staff)
    if not sid:
        raise PermissionError("unfavorite requires a staff identity")
    conn = get_conn()
    existing = conn.execute(
        "SELECT id FROM vkpi_kol_pool_favorites WHERE kol_pool_id=? AND staff_id=?",
        (int(kol_pool_id), sid),
    ).fetchone()
    if not existing:
        return {"status": "not_favorited", "kol_pool_id": int(kol_pool_id)}
    conn.execute(
        "DELETE FROM vkpi_kol_pool_favorites WHERE kol_pool_id=? AND staff_id=?",
        (int(kol_pool_id), sid),
    )
    conn.commit()
    return {"status": "unfavorited", "kol_pool_id": int(kol_pool_id)}


def list_favorites(*, staff: dict[str, Any] | None = None, limit: int = 2000) -> dict[str, Any]:
    """本人收藏清单(staff 隔离),附 pool 行摘要供 My KOL/Pool 星标渲染。"""
    sid = _staff_id(staff)
    if not sid:
        raise PermissionError("list favorites requires a staff identity")
    safe_limit = max(1, min(5000, int(limit or 2000)))
    rows = get_conn().execute(
        """
        SELECT f.id AS favorite_id, f.kol_pool_id, f.note, f.created_at,
               kp.platform, kp.handle, kp.display_name, kp.followers,
               kp.viltrox_fit_score, kp.profile_url, kp.avatar_url,
               (
                 SELECT json_agg(json_build_object(
                   'project_id', p.id, 'project_name', p.project_name,
                   'stage', a.stage, 'stage_status', a.stage_status))
                 FROM vkpi_project_kol_assignments a
                 JOIN vkpi_projects p ON p.id = a.project_id
                 WHERE a.kol_pool_id = f.kol_pool_id
                   AND COALESCE(a.stage,'') NOT IN ('churned','cancelled','lost')
                   AND COALESCE(p.restricted, FALSE) = FALSE
               ) AS projects_json
        FROM vkpi_kol_pool_favorites f
        JOIN vkpi_kol_pool kp ON kp.id = f.kol_pool_id
        WHERE f.staff_id=?
          AND kp.duplicate_of_id IS NULL
        ORDER BY f.created_at DESC, f.id DESC
        LIMIT ?
        """,
        (sid, safe_limit),
    ).fetchall()
    items = [dict(row) for row in rows]
    return {"items": items, "total": len(items), "staff_id": sid}
