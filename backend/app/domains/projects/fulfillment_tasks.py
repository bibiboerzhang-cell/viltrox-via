"""P2 履约 stage-2 · 人工复核观察任务(SAFE,零自动裁决)。

本模块只做两件事:CREATE「待人看」的观察任务 + READ。绝不自动改业务状态——
不写 vkpi_projects.stage/closed_at、不写 vkpi_project_kol_assignments.stage、
不写 vkpi_cost_ledger、不碰 viltrox_fit_score / rule_v0。任务的 reviewed/dismissed
也只动 vkpi_fulfillment_observation_tasks 自己这一行,绝不连带改项目/派单/费用。

- create_observation_task:插一条 pending 任务(同 project/KOL/类型已有 pending 则跳过,
  与迁移里的 partial-unique 双保险)。无任何状态裁决。
- list_observation_tasks:纯 SELECT,RBAC 经 scope.project_filter("p", staff) 收口
  (own-only 员工只见自己负责/创建项目的任务,管理层全见),与 fulfillment_observation.py 同款。
- mark_observation_task:把一条任务标 reviewed/dismissed(置 status + reviewed_by + closed_at + note),
  仅触本表行。
"""
from __future__ import annotations

import json
from datetime import datetime
from typing import Any

from app.db.connection import get_conn
from app.domains.access import scope


_VALID_TASK_TYPES = {"content_due", "delivery_check"}
_VALID_ACTIONS = {"reviewed", "dismissed"}


def _dump_reason(reason: Any) -> str:
    """把 reason 规整成 JSON 文本(本表 reason_json 是 TEXT 列,与 124 系迁移同款存法)。"""
    if reason is None:
        return "{}"
    if isinstance(reason, str):
        return reason
    try:
        return json.dumps(reason, ensure_ascii=False)
    except (TypeError, ValueError):
        return "{}"


def _row_to_task(row: Any) -> dict[str, Any]:
    item = dict(row)
    for col in ("reason_json", "metadata_json"):
        raw = item.get(col)
        if isinstance(raw, str):
            try:
                item[col] = json.loads(raw) if raw else {}
            except (TypeError, ValueError):
                item[col] = {}
    for col in ("created_at", "updated_at", "closed_at"):
        val = item.get(col)
        if val is not None and not isinstance(val, str):
            item[col] = str(val)
    return item


def create_observation_task(
    project_id: int,
    task_type: str,
    reason: Any,
    staff: dict[str, Any] | None = None,
    kol_pool_id: int | None = None,
) -> dict[str, Any]:
    """插一条 pending 观察任务。去重:同 (project_id, kol_pool_id, task_type) 已有 pending 则跳过。

    红线:只写 vkpi_fulfillment_observation_tasks。绝不改 project/assignment/cost 状态。
    """
    pid = int(project_id or 0)
    if pid <= 0:
        return {"status": "error", "error": "project_id required"}
    ttype = str(task_type or "").strip()
    if ttype not in _VALID_TASK_TYPES:
        return {"status": "error", "error": f"invalid task_type: {ttype}"}
    kpid = int(kol_pool_id) if kol_pool_id not in (None, "", 0, "0") else None
    actor = scope.actor_staff_id(staff) or None

    conn = get_conn()
    # 先查后插兜底(迁移 partial-unique 是首道闸,这里覆盖 partial-unique 支持脆弱的环境)。
    # NULL kol_pool_id 用 IS NULL 比较(SQL 里 NULL = NULL 不成立)。
    if kpid is None:
        existing = conn.execute(
            """
            SELECT id FROM vkpi_fulfillment_observation_tasks
            WHERE project_id = ? AND kol_pool_id IS NULL AND task_type = ? AND status = 'pending'
            LIMIT 1
            """,
            (pid, ttype),
        ).fetchone()
    else:
        existing = conn.execute(
            """
            SELECT id FROM vkpi_fulfillment_observation_tasks
            WHERE project_id = ? AND kol_pool_id = ? AND task_type = ? AND status = 'pending'
            LIMIT 1
            """,
            (pid, kpid, ttype),
        ).fetchone()
    if existing is not None:
        return {"status": "skipped", "reason": "duplicate_pending", "task_id": int(dict(existing)["id"])}

    cursor = conn.execute(
        """
        INSERT INTO vkpi_fulfillment_observation_tasks
            (project_id, kol_pool_id, task_type, reason_json, status, created_by_staff_id)
        VALUES (?, ?, ?, ?, 'pending', ?)
        RETURNING *
        """,
        (pid, kpid, ttype, _dump_reason(reason), actor),
    )
    row = cursor.fetchone()
    conn.commit()
    return {"status": "created", "task": _row_to_task(row)}


def list_observation_tasks(
    staff: dict[str, Any] | None = None,
    status: str = "pending",
    project_id: int | None = None,
) -> dict[str, Any]:
    """纯 SELECT 列任务。RBAC 经 scope.project_filter("p", staff) 收口(own-only / 管理层全见)。"""
    conn = get_conn()
    where_parts: list[str] = []
    params: list[Any] = []

    status_key = str(status or "").strip().lower()
    if status_key and status_key != "all":
        where_parts.append("t.status = ?")
        params.append(status_key)

    if project_id not in (None, "", 0, "0"):
        where_parts.append("t.project_id = ?")
        params.append(int(project_id))

    scope_sql, scope_params = scope.project_filter("p", staff)
    if scope_sql:
        where_parts.append(scope_sql)
        params.extend(scope_params)

    where_clause = f"WHERE {' AND '.join(where_parts)}" if where_parts else ""
    rows = conn.execute(
        f"""
        SELECT t.*, p.project_name, p.product_name
        FROM vkpi_fulfillment_observation_tasks t
        JOIN vkpi_projects p ON p.id = t.project_id
        {where_clause}
        ORDER BY t.created_at ASC, t.id ASC
        LIMIT 500
        """,
        tuple(params),
    ).fetchall()
    items = [_row_to_task(r) for r in rows]
    return {
        "status": "ok",
        "count": len(items),
        "items": items,
        "filter_status": status_key or "all",
        "scope_mode": scope.scope_context(staff)["scope_mode"],
    }


def mark_observation_task(
    task_id: int,
    action: str,
    staff: dict[str, Any] | None = None,
    note: str = "",
) -> dict[str, Any]:
    """把一条任务标 reviewed/dismissed,仅触本表行——绝不改项目/派单/费用。

    RBAC:own-only 员工只能标自己可见(负责/创建项目下)的任务;管理层全可标。
    """
    tid = int(task_id or 0)
    if tid <= 0:
        return {"status": "error", "error": "task_id required"}
    act = str(action or "").strip().lower()
    if act not in _VALID_ACTIONS:
        return {"status": "error", "error": f"invalid action: {act}"}

    conn = get_conn()
    # RBAC 收口:复用 project_filter,确认 actor 能看到这条任务所属项目,再放行标记。
    scope_sql, scope_params = scope.project_filter("p", staff)
    scope_clause = f"AND {scope_sql}" if scope_sql else ""
    target = conn.execute(
        f"""
        SELECT t.id, t.status
        FROM vkpi_fulfillment_observation_tasks t
        JOIN vkpi_projects p ON p.id = t.project_id
        WHERE t.id = ? {scope_clause}
        """,
        (tid, *scope_params),
    ).fetchone()
    if target is None:
        return {"status": "error", "error": "task not found or out of scope"}

    actor = scope.actor_staff_id(staff) or None
    closed_at = datetime.utcnow()
    cursor = conn.execute(
        """
        UPDATE vkpi_fulfillment_observation_tasks
        SET status = ?, reviewed_by_staff_id = ?, note = ?, closed_at = ?, updated_at = ?
        WHERE id = ?
        RETURNING *
        """,
        (act, actor, str(note or ""), closed_at, closed_at, tid),
    )
    row = cursor.fetchone()
    conn.commit()
    return {"status": "ok", "action": act, "task": _row_to_task(row)}
