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

logger = logging.getLogger(__name__)


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


def add_project_kols(project_id: int, body: dict[str, Any], *, staff: dict[str, Any] | None = None) -> dict[str, Any]:
    """Attach existing kol_pool rows to a project as discovered assignments."""
    ensure_vkpi_schema()
    scope.assert_project_access(project_id, staff, write=True)
    conn = get_conn()
    _require_project_for_kol_write(conn, int(project_id))
    ids = body.get("kol_pool_ids") or body.get("kolPoolIds") or body.get("kol_ids") or body.get("kolIds") or []
    if not isinstance(ids, list):
        raise ValueError("kol_pool_ids must be a list")
    kol_pool_ids = []
    seen: set[int] = set()
    for value in ids:
        kol_pool_id = _int(value)
        if kol_pool_id and kol_pool_id not in seen:
            seen.add(kol_pool_id)
            kol_pool_ids.append(kol_pool_id)
    if not kol_pool_ids:
        raise ValueError("kol_pool_ids required")
    existing_pool_ids = {
        int(row["id"])
        for row in conn.execute(
            f"SELECT id FROM vkpi_kol_pool WHERE id IN ({','.join('?' for _ in kol_pool_ids)})",
            kol_pool_ids,
        ).fetchall()
    }
    missing = [kol_pool_id for kol_pool_id in kol_pool_ids if kol_pool_id not in existing_pool_ids]
    actor_staff_id = staff_id(staff)
    requested_staff_id = _int(body.get("assigned_staff_id") or body.get("assignedStaffId"))
    # 负责人归属(用户拍板):每个 KOL 的负责人 = 其 active claim owner(vkpi_kol_claims),
    # 未认领 → can_view_all 且显式传了 requested_staff_id 则用之,否则归添加者 actor_staff_id;
    # 绝不再默认项目负责人。fallback 为「无 claim 时」的兜底,真正归属在写入循环里 per-KOL 计算。
    fallback_staff_id = requested_staff_id if scope.can_view_all(staff) and requested_staff_id else actor_staff_id
    # claim 主键经 vkpi_kol_pool.linked_main_kol_id 关联 vkpi_kol_claims.kol_id(非 kol_pool_id),
    # 与 _pool_claim_occupancy 的 claim 口径同源,只取 status='active' 的认领人。
    claim_owner_by_pool: dict[int, int] = {}
    if existing_pool_ids:
        _claim_ph = ",".join("?" for _ in sorted(existing_pool_ids))
        for _row in conn.execute(
            f"""
            SELECT p.id AS kol_pool_id, c.staff_id AS staff_id
            FROM vkpi_kol_pool p
            JOIN vkpi_kol_claims c ON c.kol_id = p.linked_main_kol_id AND c.status = 'active'
            WHERE p.id IN ({_claim_ph})
            ORDER BY c.id ASC
            """,
            sorted(existing_pool_ids),
        ).fetchall():
            _data = dict(_row)
            _pool_id = _int(_data.get("kol_pool_id"))
            _owner = _int(_data.get("staff_id"))
            if _pool_id and _owner and _pool_id not in claim_owner_by_pool:
                claim_owner_by_pool[_pool_id] = _owner
    # 乙案②(写入侧防绕过):与选择器同口径校验——被他人认领/在役跟进的 KOL 拒绝写入,
    # ValueError 带占用人姓名(路由已映 400);admin 可 body.force=true 强制,audit 留痕。
    occupancy = _locked_pool_claim_occupancy(conn, existing_pool_ids)
    blocked = {
        pool_id: occ
        for pool_id, occ in occupancy.items()
        if _int(occ.get("staff_id")) and _int(occ.get("staff_id")) != actor_staff_id
    }
    force = bool(body.get("force"))
    forced_conflicts: list[dict[str, Any]] = []
    if blocked:
        owner_names = _staff_display_names(conn, [occ["staff_id"] for occ in blocked.values()])
        if force and scope.can_view_all(staff):
            forced_conflicts = [
                {
                    "kol_pool_id": pool_id,
                    "occupied_by_staff_id": _int(occ.get("staff_id")),
                    "occupied_by_name": owner_names.get(_int(occ.get("staff_id")), ""),
                    "claim_source": str(occ.get("source") or ""),
                    "claim_id": occ.get("claim_id"),
                    "occupied_project_id": occ.get("project_id"),
                }
                for pool_id, occ in sorted(blocked.items())
            ]
        else:
            blocked_ids = sorted(blocked)
            placeholders = ",".join("?" for _ in blocked_ids)
            label_rows = conn.execute(
                f"SELECT id, COALESCE(display_name, handle, '') AS label FROM vkpi_kol_pool WHERE id IN ({placeholders})",
                blocked_ids,
            ).fetchall()
            labels = {_int(dict(row).get("id")): str(dict(row).get("label") or "").strip() for row in label_rows}
            parts = []
            for pool_id, occ in sorted(blocked.items()):
                kol_label = labels.get(pool_id) or f"KOL #{pool_id}"
                owner_staff_id = _int(occ.get("staff_id"))
                owner_label = owner_names.get(owner_staff_id) or f"员工#{owner_staff_id}"
                parts.append(f"「{kol_label}」已被 {owner_label} 跟进")
            raise ValueError(
                "以下 KOL 已被他人认领/跟进,未写入:" + ";".join(parts) + "。如确需加入,请管理员携 force=true 重试。"
            )
    now = utcnow()
    inserted = 0
    skipped_existing = 0
    inserted_pool_ids: list[int] = []
    for kol_pool_id in kol_pool_ids:
        if kol_pool_id not in existing_pool_ids:
            continue
        # per-KOL 负责人:命中 active claim → claim owner;否则兜底 fallback。
        assigned_staff_id = claim_owner_by_pool.get(kol_pool_id) or fallback_staff_id
        row = conn.execute(
            """
            INSERT INTO vkpi_project_kol_assignments (
                project_id, kol_pool_id, stage, stage_status, assigned_staff_id,
                source, source_ref, metadata_json, created_at, updated_at
            ) VALUES (?, ?, 'discovered', 'active', ?, 'manual', ?, ?, ?, ?)
            ON CONFLICT (project_id, kol_pool_id) DO NOTHING
            RETURNING id
            """,
            (
                int(project_id),
                int(kol_pool_id),
                assigned_staff_id or None,
                f"ui:add_kol:{project_id}",
                _json({"source": "project_detail_add_kol", "actor_staff_id": actor_staff_id}),
                now,
                now,
            ),
        ).fetchone()
        if row:
            inserted += 1
            inserted_pool_ids.append(int(kol_pool_id))
            # P0-4 触达历史回流:加入项目=一次明确触达(谁/何时/经哪个项目)。最薄记录,
            # ON CONFLICT 幂等(同人同项目同 channel 不重复堆);失败旁路不阻断 assignment 主写。
            conn.execute("SAVEPOINT vkpi_project_touch")
            try:
                conn.execute(
                    """
                    INSERT INTO vkpi_kol_pool_touches
                        (kol_pool_id, staff_id, channel, project_id, note, touched_at, created_at)
                    VALUES (?, ?, 'project_assignment', ?, ?, ?, ?)
                    ON CONFLICT (kol_pool_id, channel, project_id) DO UPDATE SET
                        staff_id=excluded.staff_id,
                        touched_at=excluded.touched_at
                    """,
                    (
                        int(kol_pool_id),
                        assigned_staff_id or actor_staff_id or None,
                        int(project_id),
                        "added to project",
                        now,
                        now,
                    ),
                )
                conn.execute("RELEASE SAVEPOINT vkpi_project_touch")
            except Exception:
                # PostgreSQL 中任意 SQL 错误都会令当前事务进入 aborted；仅 catch 不足以
                # “旁路”。回滚到 savepoint 后主 assignment 才能继续安全提交。
                try:
                    conn.execute("ROLLBACK TO SAVEPOINT vkpi_project_touch")
                    conn.execute("RELEASE SAVEPOINT vkpi_project_touch")
                except Exception:
                    logger.exception("kol_pool touch savepoint recovery failed")
                    raise
                logger.warning("kol_pool touch log skipped for pool_id=%s project_id=%s", kol_pool_id, project_id, exc_info=True)
        else:
            skipped_existing += 1
    if inserted:
        conn.execute("UPDATE vkpi_projects SET updated_at=?, last_activity_at=? WHERE id=?", (now, now, int(project_id)))
    # 裁决「查到的人先入收藏再可选」:加入项目=本人跟进=应在本人收藏内(幂等;含全池逃生门添加)。
    attached_ids = [kid for kid in kol_pool_ids if kid in existing_pool_ids]
    if actor_staff_id and attached_ids:
        for kid in attached_ids:
            conn.execute(
                """
                INSERT INTO vkpi_kol_pool_favorites (kol_pool_id, staff_id, note)
                VALUES (?, ?, ?)
                ON CONFLICT (kol_pool_id, staff_id) DO NOTHING
                """,
                (int(kid), int(actor_staff_id), "项目添加自动收藏"),
            )
    conn.commit()
    # 学习信号刻意放在业务事务提交之后。record_kol_signal 使用请求作用域连接并会
    # 自行 commit；若在循环中调用，会把 assignment 提前提交，破坏 assignment +
    # touch + auto-favorite 的原子性。学习账本仍是 best-effort，绝不反向阻断业务写入。
    if inserted_pool_ids:
        try:
            from app.domains.memory import agent_memory_writer

            for kol_pool_id in inserted_pool_ids:
                agent_memory_writer.record_kol_signal(
                    kol_pool_id,
                    "add_to_project",
                    staff=staff,
                    reason="added_to_project",
                    detail={"project_id": int(project_id)},
                )
        except Exception:
            logger.debug("add_project_kols.agent_signal_skipped", exc_info=True)
    if inserted:
        _log_project_audit(
            staff=staff,
            action_type="project_add_kols",
            project_id=int(project_id),
            detail=f"added {inserted} KOL assignments" + (f" (admin force, {len(forced_conflicts)} 个越过他人占用)" if forced_conflicts else ""),
            metadata={
                "kol_pool_ids": [kol_pool_id for kol_pool_id in kol_pool_ids if kol_pool_id in existing_pool_ids],
                # per-KOL 归属后,单一 assigned_staff_id 已无意义;记录无 claim 时的兜底 + 命中 claim 的明细。
                "assigned_staff_id_fallback": fallback_staff_id or None,
                "claim_owner_by_pool": {str(k): v for k, v in claim_owner_by_pool.items()},
                "missing_kol_pool_ids": missing,
                "skipped_existing": skipped_existing,
                "force": bool(forced_conflicts),
                "forced_claim_conflicts": forced_conflicts,
            },
        )
    return {
        "project_id": int(project_id),
        "requested": len(kol_pool_ids),
        "inserted": inserted,
        "skipped_existing": skipped_existing,
        "missing_kol_pool_ids": missing,
        "forced_claim_conflicts": forced_conflicts,
    }
