"""KOL claim/profile use case facade.

乙案(独占体制·项目维,2026-06-12)补充:auto_release_claims_for_project
为释放双轨之自动轨——项目进入终态(closed/released/lost/cancelled,含软删
cancelled)时,若 KOL 的 active claim 持有人与该项目 assignment 负责人为
同一人,自动释放认领并 audit 留痕。手动轨复用既有端点
POST /api/marketing/claims/{claim_id}/release(vkpi_kol_links.py)。
认领真实所在表:vkpi_kol_claims(FK kols.id,经 vkpi_kol_pool.linked_main_kol_id
join 池行)——复用既有设施,不另建表。
"""
from __future__ import annotations

from typing import Any

from app.db.connection import get_conn
from app.domains.kol import claim_audit
from app.domains.kol.claim_access import assert_kol_access
from app.domains.kol.claim_lifecycle import claim, reassign, release
from app.domains.kol.claim_listing import list_claims, list_kols
from app.domains.kol.claim_lookup import lookup
from app.domains.kol.claim_store import utcnow
from app.domains.kol.manual_update import update_kol_manual
from app.domains.kol.payload_utils import _int
from app.domains.kol.profile_detail import profile
from app.platform.db.schema import ensure_vkpi_schema

__all__ = [
    "assert_kol_access",
    "auto_release_claims_for_project",
    "claim",
    "list_claims",
    "list_kols",
    "lookup",
    "profile",
    "reassign",
    "release",
    "update_kol_manual",
]


def auto_release_claims_for_project(
    project_id: int,
    *,
    to_stage: str,
    actor_staff_id: int = 0,
    reason: str = "",
) -> dict[str, Any]:
    """项目终态自动释放认领(乙案④自动轨)。

    口径:该项目的每条 assignment,若其 kol_pool 行经 linked_main_kol_id 命中
    active claim 且 claim.staff_id == assignment.assigned_staff_id(认领人=负责人)
    则释放;守卫——该负责人在其他活跃项目仍在役跟进同一 KOL 时不释放(在役口径
    与 workflow_projects._pool_claim_occupancy 一致)。释放语义与
    claim_lifecycle.release 同源(status='released' + kols.assigned_staff_id 清空),
    每条释放走 claim_audit 留痕(action_type=kol_claim_auto_release)。
    """
    ensure_vkpi_schema()
    conn = get_conn()
    rows = conn.execute(
        """
        SELECT a.kol_pool_id,
               a.assigned_staff_id,
               c.id AS claim_id,
               c.kol_id,
               c.staff_id AS claim_staff_id
        FROM vkpi_project_kol_assignments a
        JOIN vkpi_kol_pool p ON p.id = a.kol_pool_id
        JOIN vkpi_kol_claims c ON c.kol_id = p.linked_main_kol_id AND c.status = 'active'
        WHERE a.project_id = ?
          AND a.assigned_staff_id IS NOT NULL
          AND c.staff_id = a.assigned_staff_id
        """,
        (_int(project_id),),
    ).fetchall()
    now = utcnow()
    stage_clean = str(to_stage or "").strip().lower()
    release_reason = str(reason or f"auto_release:project_terminal:{stage_clean}")[:120]
    released: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []
    for row in rows:
        data = dict(row)
        claim_id = _int(data.get("claim_id"))
        kol_pool_id = _int(data.get("kol_pool_id"))
        owner_staff_id = _int(data.get("claim_staff_id"))
        kol_id = _int(data.get("kol_id"))
        if not claim_id or not owner_staff_id:
            continue
        still_active_elsewhere = conn.execute(
            """
            SELECT 1
            FROM vkpi_project_kol_assignments a2
            JOIN vkpi_projects pr ON pr.id = a2.project_id
            WHERE a2.kol_pool_id = ?
              AND a2.assigned_staff_id = ?
              AND a2.project_id <> ?
              AND COALESCE(a2.stage, '') NOT IN ('churned', 'cancelled', 'lost', 'closed')
              AND COALESCE(a2.stage_status, '') NOT IN ('lost', 'cancelled', 'released', 'deleted')
              AND pr.stage <> 'cancelled'
              AND COALESCE(pr.stage_status, '') <> 'deleted'
            LIMIT 1
            """,
            (kol_pool_id, owner_staff_id, _int(project_id)),
        ).fetchone()
        if still_active_elsewhere:
            skipped.append({
                "claim_id": claim_id,
                "kol_pool_id": kol_pool_id,
                "staff_id": owner_staff_id,
                "reason": "owner_still_active_elsewhere",
            })
            continue
        conn.execute(
            """
            UPDATE vkpi_kol_claims
            SET status='released', release_reason=?, released_at=?, released_by_staff_id=?, updated_at=?
            WHERE id=? AND status='active'
            """,
            (release_reason, now, _int(actor_staff_id) or None, now, claim_id),
        )
        conn.execute("UPDATE kols SET assigned_staff_id=NULL, updated_at=? WHERE id=?", (now, kol_id))
        released.append({
            "claim_id": claim_id,
            "kol_pool_id": kol_pool_id,
            "kol_id": kol_id,
            "staff_id": owner_staff_id,
        })
    if released:
        conn.commit()
        for item in released:
            claim_audit.log_kol_audit(
                actor_staff_id=_int(actor_staff_id) or _int(item.get("staff_id")),
                action_type="kol_claim_auto_release",
                kol_id=_int(item.get("kol_id")),
                detail=release_reason,
                metadata={
                    "claim_id": _int(item.get("claim_id")),
                    "kol_pool_id": _int(item.get("kol_pool_id")),
                    "project_id": _int(project_id),
                    "to_stage": stage_clean,
                    "claim_staff_id": _int(item.get("staff_id")),
                    "trigger": "project_terminal",
                },
            )
    return {
        "project_id": _int(project_id),
        "to_stage": stage_clean,
        "checked": len(rows),
        "released": released,
        "skipped": skipped,
    }
