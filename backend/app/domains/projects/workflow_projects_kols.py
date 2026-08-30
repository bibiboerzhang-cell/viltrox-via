"""Project KOL availability + attach operations for V-KPI workflow.

Moved verbatim from workflow_projects.py (behavior-preserving extraction).
"""
from __future__ import annotations

import logging
from typing import Any

from app.db.connection import PostgresCompatConnection, get_conn
from app.domains.access import scope
from app.platform.db.schema import ensure_vkpi_schema
from app.domains.projects.workflow_common import _int, _json, staff_id, utcnow
from app.domains.projects.workflow_projects_occupancy import (
    _log_project_audit,
    _pool_claim_occupancy,
    _staff_display_names,
)
from app.domains.projects import workflow_projects_kols_add
from app.shared.project_creator_lifecycle_ports import RecommendationFeedbackSink

logger = logging.getLogger(__name__)


def _record_feedback_best_effort(
    feedback_sink: RecommendationFeedbackSink | None,
    kol_pool_id: int,
    action: str,
    *,
    staff: dict[str, Any] | None,
    payload: dict[str, Any],
    source: str,
) -> None:
    if feedback_sink is None:
        logger.warning(
            "project.feedback_sink_missing kol_pool_id=%s source=%s",
            kol_pool_id,
            source,
        )
        return
    try:
        feedback_sink.record_pool_action(
            kol_pool_id,
            action,
            staff=staff,
            payload=payload,
            source=source,
        )
    except Exception:
        logger.warning(
            "project.feedback_sink_failed kol_pool_id=%s source=%s",
            kol_pool_id,
            source,
            exc_info=True,
        )


def _record_agent_signals_best_effort(
    kol_pool_ids: list[int],
    project_id: int,
    staff: dict[str, Any] | None,
) -> None:
    if not kol_pool_ids:
        return
    try:
        from app.domains.memory import agent_memory_writer

        for kol_pool_id in kol_pool_ids:
            agent_memory_writer.record_kol_signal(
                kol_pool_id,
                "add_to_project",
                staff=staff,
                reason="added_to_project",
                detail={"project_id": int(project_id)},
            )
    except Exception:
        logger.debug("add_project_kols.agent_signal_skipped", exc_info=True)


def _require_project_for_kol_write(conn: Any, project_id: int) -> dict[str, Any]:
    """Lock one live project before attaching KOLs.

    A project picker can be stale while another request soft-deletes the project.
    PostgreSQL therefore locks the project row in the same business transaction
    used for assignments.  A delete that won first is observed as deleted; a
    delete that arrives later waits until this attachment commits.  SQLite and
    lightweight test doubles retain the same state check without ``FOR UPDATE``.
    """
    sql = "SELECT id, stage_status FROM vkpi_projects WHERE id=?"
    if isinstance(conn, PostgresCompatConnection):
        sql += " FOR UPDATE"
    row = conn.execute(sql, (int(project_id),)).fetchone()
    item = dict(row) if row else {}
    if not item or str(item.get("stage_status") or "").strip().lower() == "deleted":
        # A soft-deleted row is deliberately indistinguishable from a missing
        # project so stale clients cannot write to an invisible project.
        raise LookupError("project not found")
    return item


def _locked_pool_claim_occupancy(
    conn: Any,
    kol_pool_ids: list[int] | set[int],
) -> dict[int, dict[str, Any]]:
    """Serialize assignment decisions for the same pool rows on PostgreSQL.

    ``vkpi_project_kol_assignments`` is unique per project, not globally per
    KOL.  Without a stable parent-row lock two requests can both observe an
    empty occupancy set and assign the same creator to different staff.  Lock
    all requested parent rows in id order, then read occupancy while that lock
    is held by the caller's business transaction.  SQLite and lightweight test
    connections retain the same ordered read without unsupported lock syntax.
    """
    ids = sorted({_int(value) for value in kol_pool_ids if _int(value)})
    if not ids:
        return {}
    placeholders = ",".join("?" for _ in ids)
    sql = f"""
        SELECT id
        FROM vkpi_kol_pool
        WHERE id IN ({placeholders})
        ORDER BY id
    """
    if isinstance(conn, PostgresCompatConnection):
        sql += " FOR UPDATE"
    # Fetch the complete result so PostgreSQL has acquired every selected row
    # lock before the occupancy snapshot is evaluated.
    conn.execute(sql, ids).fetchall()
    return _pool_claim_occupancy(conn, ids)


def list_available_project_kols(
    project_id: int,
    *,
    query: str = "",
    limit: int = 200,
    scope_mode: str = "favorites",
    staff: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Return kol_pool rows not yet assigned to this project.

    诊断 P0-2 裁决:默认 scope_mode="favorites" 只返本人收藏子集(三环闭环——收藏即跟进归宿);
    scope_mode="all" 是显式逃生门(前端「从全池查找」入口触发,查到的人经 add_project_kols
    自动入收藏)。两种 scope 下乙案占用置灰(claimed_by_other)均生效。
    """
    ensure_vkpi_schema()
    scope.assert_project_access(project_id, staff)
    conn = get_conn()
    if not conn.execute(
        "SELECT id FROM vkpi_projects WHERE id=? AND COALESCE(stage_status, '') <> 'deleted'",
        (int(project_id),),
    ).fetchone():
        raise LookupError("project not found")
    limit_i = max(1, min(500, int(limit or 200)))
    actor_staff_id = staff_id(staff)
    want_all = str(scope_mode or "").strip().lower() in {"all", "pool", "global", "全池"}
    favorites_scoped = bool(not want_all and actor_staff_id)
    filters = [
        """
        NOT EXISTS (
            SELECT 1
            FROM vkpi_project_kol_assignments a
            WHERE a.project_id = ? AND a.kol_pool_id = p.id
        )
        """,
        # P0-4:项目可选池滤归并从行,避免同一人多平台条目重复入选。
        "p.duplicate_of_id IS NULL",
    ]
    params: list[Any] = [int(project_id)]
    # 默认收藏子集:仅本人已收藏的 pool 行可选;want_all 逃生门跳过此闸。
    if favorites_scoped:
        filters.append(
            """
            EXISTS (
                SELECT 1 FROM vkpi_kol_pool_favorites f
                WHERE f.kol_pool_id = p.id AND f.staff_id = ?
            )
            """
        )
        params.append(int(actor_staff_id))
    search = str(query or "").strip().lower()
    if search:
        # P2:LIKE 字面语义——转义用户输入中的 \ % _ 并显式 ESCAPE,防通配符注入/全表慢扫。
        escaped = search.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        like = f"%{escaped}%"
        filters.append(
            """
            (
                LOWER(COALESCE(p.display_name, '')) LIKE ? ESCAPE '\\'
                OR LOWER(COALESCE(p.handle, '')) LIKE ? ESCAPE '\\'
                OR LOWER(COALESCE(p.platform, '')) LIKE ? ESCAPE '\\'
            )
            """
        )
        params.extend([like, like, like])
    rows = conn.execute(
        f"""
        SELECT
            p.id,
            p.handle,
            p.display_name,
            p.display_name AS channel_name,
            p.platform,
            p.profile_url,
            p.avatar_url,
            p.followers,
            p.followers AS follower_count,
            p.dashboard_account_type,
            p.dashboard_tier,
            p.has_video_evidence,
            p.video_evidence_count
        FROM vkpi_kol_pool p
        WHERE {' AND '.join(filters)}
        ORDER BY COALESCE(p.followers, 0) DESC, LOWER(COALESCE(p.display_name, p.handle, '')) ASC
        LIMIT ?
        """,
        (*params, limit_i),
    ).fetchall()
    items = [dict(row) for row in rows]
    # 乙案①(选择器置灰):候选行补 claim/在役占用字段。claim_staff_id/claim_staff_name/
    # active_claim_id 仅在「被他人占用」时下发(前端 buildKolOptions 据此映射 claimStaffId/
    # claimOwner/activeClaimId 灰显「已被 X 跟进」);本人占用与裸数据走 occupied_* 诚实字段。
    occupancy = _pool_claim_occupancy(conn, [_int(item.get("id")) for item in items])
    owner_names = _staff_display_names(conn, [occ["staff_id"] for occ in occupancy.values()])
    actor_staff_id = staff_id(staff)
    for item in items:
        occ = occupancy.get(_int(item.get("id")))
        owner_staff_id = _int(occ.get("staff_id")) if occ else 0
        owner_name = owner_names.get(owner_staff_id, "") if owner_staff_id else ""
        claimed_by_other = bool(owner_staff_id and owner_staff_id != actor_staff_id)
        item["occupied_by_staff_id"] = owner_staff_id or None
        item["occupied_by_name"] = owner_name
        item["claim_source"] = str(occ.get("source") or "") if occ else ""
        item["claimed_by_other"] = claimed_by_other
        if claimed_by_other:
            item["active_claim_id"] = occ.get("claim_id")
            item["claim_staff_id"] = owner_staff_id
            item["claim_staff_name"] = owner_name
    # 总量(同 filters,不含 limit)——消除「静默截断」错觉(诊断 P1-7 后端半)。
    total_row = conn.execute(
        f"SELECT COUNT(*) AS n FROM vkpi_kol_pool p WHERE {' AND '.join(filters)}",
        tuple(params),
    ).fetchone()
    total_available = int(dict(total_row).get("n") or 0) if total_row else len(items)
    return {
        "kols": items,
        "project_id": int(project_id),
        "scope": "favorites" if favorites_scoped else "all",
        "total_available": total_available,
        "returned": len(items),
        "has_more": total_available > len(items),
    }


def add_project_kols(
    project_id: int,
    body: dict[str, Any],
    *,
    staff: dict[str, Any] | None = None,
    feedback_sink: RecommendationFeedbackSink | None = None,
) -> dict[str, Any]:
    """Attach existing kol_pool rows to a project as discovered assignments."""
    runtime = workflow_projects_kols_add.AddProjectKolsRuntime(
        ensure_schema=ensure_vkpi_schema,
        assert_project_access=scope.assert_project_access,
        get_conn=get_conn,
        require_project=_require_project_for_kol_write,
        to_int=_int,
        to_json=_json,
        staff_id=staff_id,
        utcnow=utcnow,
        can_view_all=scope.can_view_all,
        locked_occupancy=_locked_pool_claim_occupancy,
        staff_names=_staff_display_names,
        record_agent_signals=_record_agent_signals_best_effort,
        record_feedback=_record_feedback_best_effort,
        log_audit=_log_project_audit,
        logger=logger,
    )
    return workflow_projects_kols_add.execute_add_project_kols(
        project_id,
        body,
        staff=staff,
        feedback_sink=feedback_sink,
        runtime=runtime,
    )
