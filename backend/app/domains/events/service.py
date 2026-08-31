"""V-KPI Events(活动)domain service — CRUD + 多人协作 + 员工 scope。

迁移 122 的 4 表(vkpi_events / vkpi_event_tasks / vkpi_event_expenses / vkpi_event_kol_invites)。
id 用 VARCHAR(前端 evt_/tsk_ 串生成)。DB 走 get_conn(? 占位)应用路径。
员工 scope:非管理层只看当前组织内自己 owner/team_ids 的活动;管理层看当前组织全部。
绝不碰 viltrox_fit_score / rule_v0;与 KOL Pool 物理隔离。
"""
from __future__ import annotations

from datetime import date, datetime, timezone
from typing import Any

from app.db.connection import get_conn, is_postgres_runtime
from app.domains.access import scope
from app.domains.events import service_helpers as helpers
from app.domains.events.service_helpers import (
    EVENT_FLOAT_BOUNDS as _EVENT_FLOAT_BOUNDS,
    EVENT_INT_BOUNDS as _EVENT_INT_BOUNDS,
    EVENT_JSON_FIELDS as _EVENT_JSON_FIELDS,
    EVENT_UPDATABLE as _EVENT_UPDATABLE,
    dumps_json as _dumps,
    loads_json as _loads,
    normalize_due_date as _normalize_due_date,
)
from app.domains.events.service_rows import (
    material_row as _material_row,
    product_row as _product_row,
)

from app.core.logging import get_logger

logger = get_logger(__name__)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _as_of_date(raw: Any = None) -> date:
    """Normalize one explicit calendar boundary for date-based reads.

    Runtime defaults are calculated in the application in UTC.  Queries bind
    this value instead of using PostgreSQL ``CURRENT_DATE`` because the
    database session timezone can differ from the application/browser day.
    """
    if raw in (None, ""):
        return _now().date()
    if isinstance(raw, datetime):
        if raw.tzinfo is not None:
            return raw.astimezone(timezone.utc).date()
        return raw.date()
    if isinstance(raw, date):
        return raw
    try:
        return date.fromisoformat(str(raw).strip())
    except (TypeError, ValueError) as exc:
        raise ValueError("as_of_date must be YYYY-MM-DD") from exc


def _gen_id(prefix: str) -> str:
    # 时间戳 + 随机后缀避免碰撞:批量并发建任务(Promise.all 18 条)会同毫秒撞 id
    # → duplicate key value violates "vkpi_event_tasks_pkey"。随机段保证唯一。
    import uuid

    return f"{prefix}_{int(_now().timestamp() * 1000)}_{uuid.uuid4().hex[:6]}"


def _event_row(r: Any) -> dict[str, Any]:
    row = dict(r)
    for k in ("budget_json", "team_ids", "related_project_ids", "invited_kols_json"):
        row[k] = _loads(row.get(k), {} if k == "budget_json" else [])
    return row


def _task_row(r: Any) -> dict[str, Any]:
    """tasks 的 jsonb 列(collaborators/checklist/details)psycopg 可能回字符串,解析成对象。"""
    row = dict(r)
    for k in ("collaborators", "checklist"):
        row[k] = _loads(row.get(k), [])
    row["details"] = _loads(row.get("details"), {})
    return row


def _staff_id(staff: dict[str, Any] | None) -> int | None:
    try:
        return scope.actor_staff_id(staff)
    except Exception:
        return (staff or {}).get("staff_id") or (staff or {}).get("id")


def _can_view_all(staff: dict[str, Any] | None) -> bool:
    try:
        return bool(scope.can_view_all(staff))
    except Exception:
        return False


def _event_visibility_sql() -> str:
    """Return the employee visibility predicate for the active DB dialect.

    PostgreSQL stores ``team_ids`` as jsonb.  Hermetic/local SQLite stores the
    same logical value as JSON text and must not receive PostgreSQL's ``@>`` or
    ``to_jsonb`` operators.  ``json_each`` preserves exact integer membership
    semantics; unlike ``LIKE '%1%'`` it cannot confuse staff 1 with staff 10.
    Both variants deliberately keep the same three bind parameters:
    owner/team/share staff id.
    """
    if is_postgres_runtime():
        return (
            "(owner_id = ? OR team_ids @> to_jsonb(?::bigint) "
            "OR id IN (SELECT event_id FROM vkpi_event_members WHERE staff_id = ?) "
            "OR COALESCE(is_public, FALSE) = TRUE)"
        )
    return (
        "(owner_id = ? OR EXISTS ("
        "SELECT 1 FROM json_each(CASE WHEN json_valid(team_ids) THEN team_ids ELSE '[]' END) AS event_team "
        "WHERE CAST(event_team.value AS INTEGER) = ?"
        ") OR id IN (SELECT event_id FROM vkpi_event_members WHERE staff_id = ?) "
        "OR COALESCE(is_public, 0) = 1)"
    )


# 子资源(task/expense/kol/material/product)主键 id 列为 VARCHAR(64)。超长 id 直入会触发
# StringDataRightTruncation 500,写前校验长度收敛成 400。自动生成 id(_gen_id)远短于此界。
_MAX_ID_LEN = 64


def _validate_event_numeric(payload: dict[str, Any]) -> None:
    """写前校验数值列范围;越界抛 ValueError(→ 400),不让坏值撞列上限触发 PG 500。
    只校验 payload 显式给了且非 None 的键(None = 置空,可空列放行)。"""
    helpers.validate_int_bounds(payload, _EVENT_INT_BOUNDS)
    helpers.validate_float_bounds(payload, _EVENT_FLOAT_BOUNDS)


def _assert_event_exists(
    conn: Any,
    event_id: str,
    staff: dict[str, Any] | None,
) -> tuple[int, bool]:
    """Require the parent Event inside the actor's current organization.

    Child tables still use the legacy globally-unique ``event_id`` key, so the
    parent is the tenancy boundary.  On pre-244 schemas only default org 1 may
    use the legacy lookup; non-default workspaces fail closed in
    ``event_organization_context``.
    """
    organization_id, organization_scoped = scope.event_organization_context(staff, conn)
    if organization_scoped:
        row = conn.execute(
            "SELECT id FROM vkpi_events WHERE id = ? AND organization_id = ?",
            (str(event_id), organization_id),
        ).fetchone()
    else:
        row = conn.execute("SELECT id FROM vkpi_events WHERE id = ?", (str(event_id),)).fetchone()
    if row is None:
        raise LookupError("event not found")
    return organization_id, organization_scoped


def _validate_id_len(value: str) -> str:
    """VARCHAR(64) 主键写前校验:超长 id → ValueError(→ 400),不撞列宽触发 PG 500。"""
    if len(value) > _MAX_ID_LEN:
        raise ValueError(f"id exceeds max length {_MAX_ID_LEN}")
    return value


def _fetch_event_row(
    conn: Any,
    event_id: str,
    organization_id: int,
    organization_scoped: bool,
    *,
    columns: str = "*",
    lock: str = "",
) -> Any:
    """组织内取单条活动行;两分支 SQL 与老实现逐字节一致(只是收拢到一处)。"""
    if organization_scoped:
        return conn.execute(
            f"SELECT {columns} FROM vkpi_events WHERE id = ? AND organization_id = ?" + lock,
            (str(event_id), organization_id),
        ).fetchone()
    return conn.execute(
        f"SELECT {columns} FROM vkpi_events WHERE id = ?" + lock, (str(event_id),)
    ).fetchone()


def _event_item_response(
    conn: Any, event_id: str, organization_id: int, organization_scoped: bool
) -> dict[str, Any]:
    row = _fetch_event_row(conn, event_id, organization_id, organization_scoped)
    return {"item": _event_row(row) if row else None}


# ── Events ────────────────────────────────────────────────────────────────
def _merge_invited_kols(conn: Any, event_id: Any, stored_json: Any) -> list[dict[str, Any]]:
    """回填 invited_kols_json:它建时只初始化、邀请后不更新 → KOL 计数长期偏低/为 0。
    用真 invites 表(邀请 UI 写这里=live 源)并集建时字段去重(by kol_id),既修计数又不丢建时数据。
    """
    out: list[dict[str, Any]] = []
    seen: set[str] = set()

    def _add(kol_id: Any, status: Any = "", travel: Any = "") -> None:
        k = str(kol_id or "").strip()
        if not k or k in seen:
            return
        seen.add(k)
        out.append({"kol_id": k, "status": str(status or "pending"), "travel_status": str(travel or "")})

    try:
        for r in conn.execute(
            "SELECT kol_id, status, travel_status FROM vkpi_event_kol_invites WHERE event_id = ? ORDER BY created_at ASC",
            (str(event_id),),
        ).fetchall():
            d = dict(r)
            _add(d.get("kol_id"), d.get("status"), d.get("travel_status"))
    except Exception:
        logger.warning("suppressed exception (hardening: was silent)", exc_info=True)
        pass
    for k in stored_json or []:
        if isinstance(k, dict):
            _add(k.get("kol_id") or k.get("kolId"), k.get("status"), k.get("travel_status") or k.get("travel"))
        elif isinstance(k, (str, int)):
            _add(k)
    return out


def list_events(
    staff: dict[str, Any] | None,
    *,
    limit: int = 200,
    offset: int = 0,
    status: str | None = None,
    owner_id: int | None = None,
) -> dict[str, Any]:
    """列活动:管理层看当前组织全部;员工只看当前组织内 own/team/share/public。

    Filtering and the count query share exactly the same scope predicates so
    callers can verify server-side pagination instead of inferring it from a
    truncated page.
    """
    conn = get_conn()
    organization_id, organization_scoped = scope.event_organization_context(staff, conn)
    safe_limit, safe_offset = helpers.normalize_page_args(limit, offset, default_limit=200, max_limit=500)
    normalized_status = helpers.normalized_status_filter(status)
    normalized_owner_id = helpers.normalized_owner_filter(owner_id)

    clauses: list[str] = []
    params: list[Any] = []
    if organization_scoped:
        clauses.append("organization_id = ?")
        params.append(organization_id)
    if not _can_view_all(staff):
        sid = _staff_id(staff)
        # 员工:owner_id 是自己 或 team_ids(jsonb 数组)含自己。
        # 活动共享接线(2026-06-14,ADDITIVE):再 OR 进「被显式共享给自己」的活动
        # (vkpi_event_members.staff_id=自己)。这只是加宽一条 OR 分支,绝不去掉
        # 既有 owner/team_ids 可见性,也绝不扩到未共享的活动。镜像 131 项目共享。
        # 公司公共活动接线(2026-06-14,ADDITIVE,迁移 133 is_public):再 OR 进
        # 「COALESCE(is_public,FALSE)=TRUE」——public 活动对所有员工可见。只「加宽」读,
        # 写仍由 assert_event_access 严格 gate(public 不放开写)。镜像 scope.project_filter。
        clauses.append(_event_visibility_sql())
        params.extend((sid, sid, sid))
    if normalized_status is not None:
        clauses.append("LOWER(COALESCE(status, '')) = ?")
        params.append(normalized_status)
    if normalized_owner_id is not None:
        clauses.append("owner_id = ?")
        params.append(normalized_owner_id)

    where_sql = " WHERE " + " AND ".join(clauses) if clauses else ""
    total_row = conn.execute(
        "SELECT COUNT(*) AS n FROM vkpi_events" + where_sql,
        tuple(params),
    ).fetchone()
    total_count = helpers.total_count_scalar(total_row)
    rows = conn.execute(
        "SELECT * FROM vkpi_events" + where_sql + " "
        "ORDER BY start_date DESC, created_at DESC, id DESC LIMIT ? OFFSET ?",
        (*params, safe_limit, safe_offset),
    ).fetchall()
    items = [_event_row(r) for r in rows]
    for it in items:
        it["invited_kols_json"] = _merge_invited_kols(conn, it.get("id"), it.get("invited_kols_json"))
    return helpers.page_envelope(
        items, total_count=total_count, safe_offset=safe_offset, safe_limit=safe_limit
    )


def list_upcoming_events(
    staff: dict[str, Any] | None,
    *,
    limit: int = 50,
    as_of_date: date | str | None = None,
) -> dict[str, Any]:
    """upcoming/进行中活动(end_date >= 今天),给 dashboard 地图 + 报告「活动进度」用。
    员工 scope 同 list_events;返回精简形(location + budget + status,无 tasks/expenses)。"""
    conn = get_conn()
    organization_id, organization_scoped = scope.event_organization_context(staff, conn)
    safe_limit = max(1, min(int(limit or 50), 200))
    effective_date = _as_of_date(as_of_date)
    terminal_statuses = ("done", "ended", "cancelled", "canceled", "closed")
    clauses: list[str] = []
    params: list[Any] = []
    if organization_scoped:
        clauses.append("organization_id = ?")
        params.append(organization_id)
    clauses.append("end_date >= ?")
    params.append(effective_date.isoformat())
    clauses.append(
        "LOWER(COALESCE(status, '')) NOT IN (" + ",".join("?" for _ in terminal_statuses) + ")"
    )
    params.extend(terminal_statuses)
    if not _can_view_all(staff):
        sid = _staff_id(staff)
        clauses.append(_event_visibility_sql())
        params.extend((sid, sid, sid))
    rows = conn.execute(
        "SELECT * FROM vkpi_events WHERE " + " AND ".join(clauses) + " "
        "ORDER BY start_date ASC, created_at ASC, id ASC LIMIT ?",
        (*params, safe_limit),
    ).fetchall()
    items = [helpers.upcoming_event_item(_event_row(r)) for r in rows]
    return {
        "items": items,
        "count": len(items),
        "as_of_date": effective_date.isoformat(),
    }


def get_event_detail(event_id: str, staff: dict[str, Any] | None) -> dict[str, Any]:
    """单活动 + 内嵌 tasks/expenses/invites(供详情页一次拉取)。"""
    conn = get_conn()
    organization_id, organization_scoped = scope.event_organization_context(staff, conn)
    if organization_scoped:
        row = conn.execute(
            "SELECT * FROM vkpi_events WHERE id = ? AND organization_id = ?",
            (str(event_id), organization_id),
        ).fetchone()
    else:
        row = conn.execute("SELECT * FROM vkpi_events WHERE id = ?", (str(event_id),)).fetchone()
    if not row:
        return {"item": None, "tasks": [], "expenses": [], "invites": [], "materials": [], "products": []}
    tasks = conn.execute(
        "SELECT * FROM vkpi_event_tasks WHERE event_id = ? ORDER BY due_date ASC NULLS LAST, created_at ASC",
        (str(event_id),),
    ).fetchall()
    expenses = conn.execute(
        "SELECT * FROM vkpi_event_expenses WHERE event_id = ? ORDER BY created_at DESC", (str(event_id),)
    ).fetchall()
    invites = conn.execute(
        "SELECT * FROM vkpi_event_kol_invites WHERE event_id = ? ORDER BY created_at ASC", (str(event_id),)
    ).fetchall()
    materials = conn.execute(
        "SELECT * FROM vkpi_event_materials WHERE event_id = ? ORDER BY created_at ASC", (str(event_id),)
    ).fetchall()
    products = conn.execute(
        "SELECT * FROM vkpi_event_products WHERE event_id = ? ORDER BY created_at ASC", (str(event_id),)
    ).fetchall()
    item = _event_row(row)
    item["invited_kols_json"] = _merge_invited_kols(conn, event_id, item.get("invited_kols_json"))
    return {
        "item": item,
        "tasks": [_task_row(t) for t in tasks],
        "expenses": [dict(x) for x in expenses],
        "invites": [dict(i) for i in invites],
        "materials": [_material_row(m) for m in materials],
        "products": [_product_row(p) for p in products],
    }


def _validated_owner_id(conn: Any, owner_id: Any, organization_id: int) -> int:
    # owner_id → staff(id) 外键。给了却不存在会撞 FK 违约 500 + DETAIL 泄露;写前校验 → 400。
    # None 放行(列可空 ON DELETE SET NULL,由调用方处理);非数字/不存在 → ValueError(→400)。
    try:
        owner_id_int = int(owner_id)
    except (TypeError, ValueError) as exc:
        raise ValueError("owner_id must be an integer") from exc
    if conn.execute("SELECT 1 FROM staff WHERE id = ?", (owner_id_int,)).fetchone() is None:
        raise ValueError("owner_id not found")
    if not scope.staff_belongs_to_event_organization(conn, owner_id_int, organization_id):
        raise ValueError("owner_id organization mismatch")
    return owner_id_int


def _validate_team_ids(conn: Any, team_ids: Any, organization_id: int) -> None:
    if not isinstance(team_ids, list):
        raise ValueError("team_ids must be a list")
    for team_staff_id in team_ids:
        if not scope.staff_belongs_to_event_organization(conn, team_staff_id, organization_id):
            raise ValueError("team member organization mismatch")


def _insert_event(conn: Any, values: tuple, organization_id: int, organization_scoped: bool) -> None:
    if organization_scoped:
        conn.execute(
            """
            INSERT INTO vkpi_events
              (organization_id, id, title, type_key, status, health_score, note, start_date, end_date,
               location_name, location_city, location_country, location_lat, location_lng,
               budget_total, budget_json, owner_id, team_ids, related_project_ids, invited_kols_json,
               product_sku, product_name, retrospective, created_at, updated_at)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?::jsonb,?,?::jsonb,?::jsonb,?::jsonb,?,?,?, NOW(), NOW())
            """,
            (organization_id, *values),
        )
    else:
        conn.execute(
            """
            INSERT INTO vkpi_events
              (id, title, type_key, status, health_score, note, start_date, end_date,
               location_name, location_city, location_country, location_lat, location_lng,
               budget_total, budget_json, owner_id, team_ids, related_project_ids, invited_kols_json,
               product_sku, product_name, retrospective, created_at, updated_at)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?::jsonb,?,?::jsonb,?::jsonb,?::jsonb,?,?,?, NOW(), NOW())
            """,
            values,
        )


def create_event(payload: dict[str, Any], staff: dict[str, Any] | None) -> dict[str, Any]:
    _validate_event_numeric(payload)  # health_score/budget_total/location_lat/lng 越界 → 400,不撞列上限 500
    conn = get_conn()
    organization_id, organization_scoped = scope.event_organization_context(staff, conn)
    eid = _validate_id_len(str(payload.get("id") or _gen_id("evt")))  # VARCHAR(64) 超长 → 400
    owner_id = payload.get("owner_id") or _staff_id(staff)
    if owner_id is not None:
        owner_id = _validated_owner_id(conn, owner_id, organization_id)
    team_ids = payload.get("team_ids") or ([owner_id] if owner_id else [])
    _validate_team_ids(conn, team_ids, organization_id)
    values = helpers.event_insert_values(
        payload, eid=eid, owner_id=owner_id, team_ids=team_ids, today=str(_now().date())
    )
    _insert_event(conn, values, organization_id, organization_scoped)
    conn.commit()
    row = _fetch_event_row(conn, eid, organization_id, organization_scoped)
    return {"item": _event_row(row)}


def update_event(event_id: str, payload: dict[str, Any], staff: dict[str, Any] | None) -> dict[str, Any]:
    _validate_event_numeric(payload)  # 数值越界 → 400,不撞 INTEGER/NUMERIC 列上限触发 500
    conn = get_conn()
    organization_id, organization_scoped = _assert_event_exists(conn, event_id, staff)
    sets: list[str] = []
    vals: list[Any] = []
    helpers.event_scalar_update_sets(payload, sets, vals)
    helpers.event_json_update_sets(
        payload, sets, vals,
        validate_team=lambda team: _validate_team_ids(conn, team, organization_id),
    )
    if not sets:
        return _event_item_response(conn, event_id, organization_id, organization_scoped)
    sets.append("updated_at = NOW()")
    if organization_scoped:
        vals.extend([str(event_id), organization_id])
        conn.execute(
            f"UPDATE vkpi_events SET {', '.join(sets)} WHERE id = ? AND organization_id = ?", tuple(vals)
        )
    else:
        vals.append(str(event_id))
        conn.execute(f"UPDATE vkpi_events SET {', '.join(sets)} WHERE id = ?", tuple(vals))
    conn.commit()
    return _event_item_response(conn, event_id, organization_id, organization_scoped)


def delete_event(event_id: str, staff: dict[str, Any] | None) -> dict[str, Any]:
    conn = get_conn()
    organization_id, organization_scoped = _assert_event_exists(conn, event_id, staff)
    if organization_scoped:
        conn.execute(
            "DELETE FROM vkpi_events WHERE id = ? AND organization_id = ?", (str(event_id), organization_id)
        )
    else:
        conn.execute("DELETE FROM vkpi_events WHERE id = ?", (str(event_id),))
    conn.commit()
    return {"ok": True, "id": str(event_id)}


# ── 多人协作:team members ──────────────────────────────────────────────────
def _set_team(
    conn: Any,
    event_id: str,
    team: list[Any],
    organization_id: int,
    organization_scoped: bool,
) -> dict[str, Any]:
    if organization_scoped:
        conn.execute(
            "UPDATE vkpi_events SET team_ids = ?::jsonb, updated_at = NOW() "
            "WHERE id = ? AND organization_id = ?",
            (_dumps(team), str(event_id), organization_id),
        )
    else:
        conn.execute(
            "UPDATE vkpi_events SET team_ids = ?::jsonb, updated_at = NOW() WHERE id = ?",
            (_dumps(team), str(event_id)),
        )
    conn.commit()
    if organization_scoped:
        row = conn.execute(
            "SELECT * FROM vkpi_events WHERE id = ? AND organization_id = ?",
            (str(event_id), organization_id),
        ).fetchone()
    else:
        row = conn.execute("SELECT * FROM vkpi_events WHERE id = ?", (str(event_id),)).fetchone()
    return {"item": _event_row(row) if row else None}


def add_member(event_id: str, user_id: Any, staff: dict[str, Any] | None) -> dict[str, Any]:
    conn = get_conn()
    organization_id, organization_scoped = _assert_event_exists(conn, event_id, staff)
    if not scope.staff_belongs_to_event_organization(conn, user_id, organization_id):
        raise ValueError("team member organization mismatch")
    lock = " FOR UPDATE" if is_postgres_runtime() else ""
    if organization_scoped:
        row = conn.execute(
            "SELECT team_ids FROM vkpi_events WHERE id = ? AND organization_id = ?" + lock,
            (str(event_id), organization_id),
        ).fetchone()
    else:
        row = conn.execute(
            "SELECT team_ids FROM vkpi_events WHERE id = ?" + lock,
            (str(event_id),),
        ).fetchone()
    team = _loads(dict(row).get("team_ids"), []) if row else []
    if user_id not in team:
        team.append(user_id)
    return _set_team(conn, event_id, team, organization_id, organization_scoped)


def remove_member(event_id: str, user_id: Any, staff: dict[str, Any] | None) -> dict[str, Any]:
    conn = get_conn()
    organization_id, organization_scoped = _assert_event_exists(conn, event_id, staff)
    lock = " FOR UPDATE" if is_postgres_runtime() else ""
    if organization_scoped:
        row = conn.execute(
            "SELECT team_ids FROM vkpi_events WHERE id = ? AND organization_id = ?" + lock,
            (str(event_id), organization_id),
        ).fetchone()
    else:
        row = conn.execute(
            "SELECT team_ids FROM vkpi_events WHERE id = ?" + lock,
            (str(event_id),),
        ).fetchone()
    team = [u for u in (_loads(dict(row).get("team_ids"), []) if row else []) if str(u) != str(user_id)]
    return _set_team(conn, event_id, team, organization_id, organization_scoped)


# ── Tasks(含 collaborators / done_by 多人协作)──────────────────────────────
# _normalize_due_date 实现已平移到 service_helpers.normalize_due_date(顶部按老名字引入)。
def add_task(event_id: str, payload: dict[str, Any], staff: dict[str, Any] | None) -> dict[str, Any]:
    conn = get_conn()
    _assert_event_exists(conn, event_id, staff)  # 父活动必须属于当前组织
    tid = _validate_id_len(str(payload.get("id") or _gen_id("tsk")))  # VARCHAR(64) 超长 → 400
    conn.execute(
        """
        INSERT INTO vkpi_event_tasks
          (id, event_id, title, phase, owner, collaborators, due_date, kind, checklist, details, created_at, updated_at)
        VALUES (?,?,?,?,?,?::jsonb,?,?,?::jsonb,?::jsonb, NOW(), NOW())
        """,
        (
            tid, str(event_id), str(payload.get("title") or ""), str(payload.get("phase") or "prep"),
            str(payload.get("owner") or ""), _dumps(payload.get("collaborators") or []),
            _normalize_due_date(payload.get("due_date")), str(payload.get("kind") or "task"),
            _dumps(payload.get("checklist") or []), _dumps(payload.get("details") or {}),
        ),
    )
    conn.commit()
    row = conn.execute("SELECT * FROM vkpi_event_tasks WHERE id = ?", (tid,)).fetchone()
    return {"item": _task_row(row) if row else None}


def update_task(event_id: str, task_id: str, payload: dict[str, Any], staff: dict[str, Any] | None) -> dict[str, Any]:
    conn = get_conn()
    _assert_event_exists(conn, event_id, staff)
    sets, vals = helpers.task_update_sets(payload, _now())
    if not sets:
        return {"ok": True}
    sets.append("updated_at = NOW()")
    vals.extend([str(task_id), str(event_id)])
    conn.execute(f"UPDATE vkpi_event_tasks SET {', '.join(sets)} WHERE id = ? AND event_id = ?", tuple(vals))
    conn.commit()
    row = conn.execute(
        "SELECT * FROM vkpi_event_tasks WHERE id = ? AND event_id = ?", (str(task_id), str(event_id))
    ).fetchone()
    return {"item": _task_row(row) if row else None}


def delete_task(event_id: str, task_id: str, staff: dict[str, Any] | None) -> dict[str, Any]:
    conn = get_conn()
    _assert_event_exists(conn, event_id, staff)
    conn.execute("DELETE FROM vkpi_event_tasks WHERE id = ? AND event_id = ?", (str(task_id), str(event_id)))
    conn.commit()
    return {"ok": True, "id": str(task_id)}


# ── Expenses ────────────────────────────────────────────────────────────────
def add_expense(event_id: str, payload: dict[str, Any], staff: dict[str, Any] | None) -> dict[str, Any]:
    conn = get_conn()
    _assert_event_exists(conn, event_id, staff)  # 父活动必须属于当前组织
    _validate_event_numeric(payload)  # amount 越界 → 400,不撞 int32 列上限 500
    xid = _validate_id_len(str(payload.get("id") or _gen_id("exp")))  # VARCHAR(64) 超长 → 400
    conn.execute(
        """
        INSERT INTO vkpi_event_expenses
          (id, event_id, amount, category, description, paid_by, payment_method, reimbursement_status, created_at)
        VALUES (?,?,?,?,?,?,?,?, NOW())
        """,
        (
            xid, str(event_id), int(payload.get("amount") or 0), str(payload.get("category") or "other"),
            str(payload.get("description") or ""), str(payload.get("paid_by") or ""),
            str(payload.get("payment_method") or "other"), str(payload.get("reimbursement_status") or "pending"),
        ),
    )
    conn.commit()
    row = conn.execute("SELECT * FROM vkpi_event_expenses WHERE id = ?", (xid,)).fetchone()
    return {"item": dict(row) if row else None}


def delete_expense(event_id: str, expense_id: str, staff: dict[str, Any] | None) -> dict[str, Any]:
    conn = get_conn()
    _assert_event_exists(conn, event_id, staff)
    conn.execute("DELETE FROM vkpi_event_expenses WHERE id = ? AND event_id = ?", (str(expense_id), str(event_id)))
    conn.commit()
    return {"ok": True, "id": str(expense_id)}


# ── KOL invites ─────────────────────────────────────────────────────────────
def invite_kol(event_id: str, payload: dict[str, Any], staff: dict[str, Any] | None) -> dict[str, Any]:
    conn = get_conn()
    _assert_event_exists(conn, event_id, staff)  # 父活动必须属于当前组织
    iid = _validate_id_len(str(payload.get("id") or _gen_id("inv")))  # VARCHAR(64) 超长 → 400
    conn.execute(
        """
        INSERT INTO vkpi_event_kol_invites
          (id, event_id, kol_id, status, days, travel_status, created_at)
        VALUES (?,?,?,?,?,?, NOW())
        """,
        (
            iid, str(event_id), str(payload.get("kol_id") or ""), str(payload.get("status") or "pending"),
            str(payload.get("days") or ""), str(payload.get("travel_status") or ""),
        ),
    )
    conn.commit()
    row = conn.execute("SELECT * FROM vkpi_event_kol_invites WHERE id = ?", (iid,)).fetchone()
    return {"item": dict(row) if row else None}


def remove_kol(event_id: str, invite_id: str, staff: dict[str, Any] | None) -> dict[str, Any]:
    conn = get_conn()
    _assert_event_exists(conn, event_id, staff)
    conn.execute("DELETE FROM vkpi_event_kol_invites WHERE id = ? AND event_id = ?", (str(invite_id), str(event_id)))
    conn.commit()
    return {"ok": True, "id": str(invite_id)}


# ── Materials(活动营销物料,逐项落库)──────────────────────────────────────────
def add_material(event_id: str, payload: dict[str, Any], staff: dict[str, Any] | None) -> dict[str, Any]:
    conn = get_conn()
    _assert_event_exists(conn, event_id, staff)  # 父活动必须属于当前组织
    _validate_event_numeric(payload)  # qty 越界 → 400,不撞 int32 列上限 500
    mid = _validate_id_len(str(payload.get("id") or _gen_id("mat")))  # VARCHAR(64) 超长 → 400
    conn.execute(
        """
        INSERT INTO vkpi_event_materials
          (id, event_id, name, category, source, qty, status, owner, note, tracking_no, file_url, alert, created_at, updated_at)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?, NOW(), NOW())
        """,
        helpers.material_insert_values(payload, mid=mid, event_id=event_id),
    )
    conn.commit()
    row = conn.execute("SELECT * FROM vkpi_event_materials WHERE id = ?", (mid,)).fetchone()
    return {"item": _material_row(row) if row else None}


def update_material(event_id: str, material_id: str, payload: dict[str, Any], staff: dict[str, Any] | None) -> dict[str, Any]:
    _validate_event_numeric(payload)  # qty 越界 → 400,不撞 int32 列上限 500
    conn = get_conn()
    _assert_event_exists(conn, event_id, staff)
    sets, vals = helpers.material_update_sets(payload)
    if not sets:
        row = conn.execute(
            "SELECT * FROM vkpi_event_materials WHERE id = ? AND event_id = ?", (str(material_id), str(event_id))
        ).fetchone()
        return {"item": _material_row(row) if row else None}
    sets.append("updated_at = NOW()")
    vals.extend([str(material_id), str(event_id)])
    conn.execute(f"UPDATE vkpi_event_materials SET {', '.join(sets)} WHERE id = ? AND event_id = ?", tuple(vals))
    conn.commit()
    row = conn.execute(
        "SELECT * FROM vkpi_event_materials WHERE id = ? AND event_id = ?",
        (str(material_id), str(event_id)),
    ).fetchone()
    return {"item": _material_row(row) if row else None}


def delete_material(event_id: str, material_id: str, staff: dict[str, Any] | None) -> dict[str, Any]:
    conn = get_conn()
    _assert_event_exists(conn, event_id, staff)
    conn.execute("DELETE FROM vkpi_event_materials WHERE id = ? AND event_id = ?", (str(material_id), str(event_id)))
    conn.commit()
    return {"ok": True, "id": str(material_id)}


# ── Products(活动产品准备,逐项落库)──────────────────────────────────────────
def add_product(event_id: str, payload: dict[str, Any], staff: dict[str, Any] | None) -> dict[str, Any]:
    conn = get_conn()
    _assert_event_exists(conn, event_id, staff)  # 父活动必须属于当前组织
    _validate_event_numeric(payload)  # qty 越界 → 400,不撞 int32 列上限 500
    pid = _validate_id_len(str(payload.get("id") or _gen_id("pp")))  # VARCHAR(64) 超长 → 400
    conn.execute(
        """
        INSERT INTO vkpi_event_products
          (id, event_id, name, category, source, qty, status, owner, note, tracking_no, arrive_by, return_after, created_at, updated_at)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?, NOW(), NOW())
        """,
        helpers.product_insert_values(payload, pid=pid, event_id=event_id),
    )
    conn.commit()
    row = conn.execute("SELECT * FROM vkpi_event_products WHERE id = ?", (pid,)).fetchone()
    return {"item": _product_row(row) if row else None}


def update_product(event_id: str, product_id: str, payload: dict[str, Any], staff: dict[str, Any] | None) -> dict[str, Any]:
    _validate_event_numeric(payload)  # qty 越界 → 400,不撞 int32 列上限 500
    conn = get_conn()
    _assert_event_exists(conn, event_id, staff)
    sets, vals = helpers.product_update_sets(payload)
    if not sets:
        row = conn.execute(
            "SELECT * FROM vkpi_event_products WHERE id = ? AND event_id = ?", (str(product_id), str(event_id))
        ).fetchone()
        return {"item": _product_row(row) if row else None}
    sets.append("updated_at = NOW()")
    vals.extend([str(product_id), str(event_id)])
    conn.execute(f"UPDATE vkpi_event_products SET {', '.join(sets)} WHERE id = ? AND event_id = ?", tuple(vals))
    conn.commit()
    row = conn.execute(
        "SELECT * FROM vkpi_event_products WHERE id = ? AND event_id = ?",
        (str(product_id), str(event_id)),
    ).fetchone()
    return {"item": _product_row(row) if row else None}


def delete_product(event_id: str, product_id: str, staff: dict[str, Any] | None) -> dict[str, Any]:
    conn = get_conn()
    _assert_event_exists(conn, event_id, staff)
    conn.execute("DELETE FROM vkpi_event_products WHERE id = ? AND event_id = ?", (str(product_id), str(event_id)))
    conn.commit()
    return {"ok": True, "id": str(product_id)}
