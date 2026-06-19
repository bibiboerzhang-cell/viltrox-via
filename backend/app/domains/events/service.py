"""V-KPI Events(活动)domain service — CRUD + 多人协作 + 员工 scope。

迁移 122 的 4 表(vkpi_events / vkpi_event_tasks / vkpi_event_expenses / vkpi_event_kol_invites)。
id 用 VARCHAR(前端 evt_/tsk_ 串生成)。DB 走 get_conn(? 占位)应用路径。
员工 scope:非管理层只看自己 owner/team_ids 的活动;管理层看全部(复用 access/scope.py)。
绝不碰 viltrox_fit_score / rule_v0;与 KOL Pool 物理隔离。
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

from app.db.connection import get_conn
from app.domains.access import scope


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _gen_id(prefix: str) -> str:
    # 时间戳 + 计数避免碰撞(Date.now 在服务端无限制,这里是普通运行时)。
    return f"{prefix}_{int(_now().timestamp() * 1000)}"


def _loads(value: Any, default: Any) -> Any:
    if value in (None, ""):
        return default
    if isinstance(value, (dict, list)):
        return value
    try:
        return json.loads(value)
    except Exception:
        return default


def _dumps(value: Any) -> str:
    return json.dumps(value if value is not None else None, default=str, ensure_ascii=False)


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


def _short_date(raw: Any) -> str:
    """updated_at(datetime / ISO 串)→ 展示用 "M/D"(给物料/产品 updatedAt 列)。
    诚实派生服务端时间,绝不硬编码「刚刚/实时」(时间机制铁律)。"""
    if not raw:
        return ""
    try:
        dt = datetime.fromisoformat(str(raw).replace("Z", "+00:00")) if isinstance(raw, str) else raw
        return f"{dt.month}/{dt.day}"
    except Exception:
        return str(raw)


def _material_row(r: Any) -> dict[str, Any]:
    """vkpi_event_materials 行 → 前端 MarketingMaterialsPanel 期望的 camelCase 形态。"""
    d = dict(r)
    return {
        "id": d.get("id"),
        "category": d.get("category") or "display",
        "name": d.get("name") or "",
        "source": d.get("source") or "ship",
        "qty": int(d.get("qty") or 0),
        "status": d.get("status") or "pending",
        "owner": d.get("owner") or "",
        "note": d.get("note") or "",
        "trackingNo": d.get("tracking_no") or "",
        "fileUrl": d.get("file_url") or "",
        "alert": d.get("alert") or "",
        "updatedAt": _short_date(d.get("updated_at")),
    }


def _product_row(r: Any) -> dict[str, Any]:
    """vkpi_event_products 行 → 前端 ProductPrepPanel 期望的 camelCase 形态。"""
    d = dict(r)
    return {
        "id": d.get("id"),
        "category": d.get("category") or "lens",
        "name": d.get("name") or "",
        "source": d.get("source") or "new_purchase",
        "qty": int(d.get("qty") or 0),
        "status": d.get("status") or "ordered",
        "owner": d.get("owner") or "",
        "note": d.get("note") or "",
        "trackingNo": d.get("tracking_no") or "",
        "arriveBy": d.get("arrive_by") or "",
        "returnAfter": bool(d.get("return_after")),
    }


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
        pass
    for k in stored_json or []:
        if isinstance(k, dict):
            _add(k.get("kol_id") or k.get("kolId"), k.get("status"), k.get("travel_status") or k.get("travel"))
        elif isinstance(k, (str, int)):
            _add(k)
    return out


def list_events(staff: dict[str, Any] | None, *, limit: int = 200) -> dict[str, Any]:
    """列活动:管理层看全部;员工只看自己 owner 或在 team_ids 里的。"""
    conn = get_conn()
    safe_limit = max(1, min(int(limit or 200), 500))
    if _can_view_all(staff):
        rows = conn.execute(
            "SELECT * FROM vkpi_events ORDER BY start_date DESC, created_at DESC LIMIT ?",
            (safe_limit,),
        ).fetchall()
    else:
        sid = _staff_id(staff)
        # 员工:owner_id 是自己 或 team_ids(jsonb 数组)含自己。
        # 活动共享接线(2026-06-14,ADDITIVE):再 OR 进「被显式共享给自己」的活动
        # (vkpi_event_members.staff_id=自己)。这只是加宽一条 OR 分支,绝不去掉
        # 既有 owner/team_ids 可见性,也绝不扩到未共享的活动。镜像 131 项目共享。
        # 公司公共活动接线(2026-06-14,ADDITIVE,迁移 133 is_public):再 OR 进
        # 「COALESCE(is_public,FALSE)=TRUE」——public 活动对所有员工可见。只「加宽」读,
        # 写仍由 assert_event_access 严格 gate(public 不放开写)。镜像 scope.project_filter。
        rows = conn.execute(
            "SELECT * FROM vkpi_events "
            "WHERE owner_id = ? OR team_ids @> to_jsonb(?::bigint) "
            "OR id IN (SELECT event_id FROM vkpi_event_members WHERE staff_id = ?) "
            "OR COALESCE(is_public, FALSE) = TRUE "
            "ORDER BY start_date DESC, created_at DESC LIMIT ?",
            (sid, sid, sid, safe_limit),
        ).fetchall()
    items = [_event_row(r) for r in rows]
    for it in items:
        it["invited_kols_json"] = _merge_invited_kols(conn, it.get("id"), it.get("invited_kols_json"))
    return {"items": items}


def list_upcoming_events(staff: dict[str, Any] | None, *, limit: int = 50) -> dict[str, Any]:
    """upcoming/进行中活动(end_date >= 今天),给 dashboard 地图 + 报告「活动进度」用。
    员工 scope 同 list_events;返回精简形(location + budget + status,无 tasks/expenses)。"""
    conn = get_conn()
    safe_limit = max(1, min(int(limit or 50), 200))
    if _can_view_all(staff):
        rows = conn.execute(
            "SELECT * FROM vkpi_events WHERE end_date >= CURRENT_DATE "
            "ORDER BY start_date ASC, created_at ASC LIMIT ?",
            (safe_limit,),
        ).fetchall()
    else:
        sid = _staff_id(staff)
        rows = conn.execute(
            "SELECT * FROM vkpi_events "
            "WHERE end_date >= CURRENT_DATE AND ("
            "owner_id = ? OR team_ids @> to_jsonb(?::bigint) "
            "OR id IN (SELECT event_id FROM vkpi_event_members WHERE staff_id = ?) "
            "OR COALESCE(is_public, FALSE) = TRUE) "
            "ORDER BY start_date ASC, created_at ASC LIMIT ?",
            (sid, sid, sid, safe_limit),
        ).fetchall()
    items: list[dict[str, Any]] = []
    for r in rows:
        row = _event_row(r)
        items.append({
            "id": row.get("id"),
            "title": row.get("title"),
            "type_key": row.get("type_key"),
            "status": row.get("status"),
            "health_score": row.get("health_score"),
            "start_date": str(row.get("start_date") or ""),
            "end_date": str(row.get("end_date") or ""),
            "location_name": row.get("location_name") or "",
            "location_city": row.get("location_city") or "",
            "location_country": row.get("location_country") or "",
            "location_lat": row.get("location_lat"),
            "location_lng": row.get("location_lng"),
            "budget_total": row.get("budget_total") or 0,
        })
    return {"items": items, "count": len(items)}


def get_event_detail(event_id: str, staff: dict[str, Any] | None) -> dict[str, Any]:
    """单活动 + 内嵌 tasks/expenses/invites(供详情页一次拉取)。"""
    conn = get_conn()
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


def create_event(payload: dict[str, Any], staff: dict[str, Any] | None) -> dict[str, Any]:
    conn = get_conn()
    eid = str(payload.get("id") or _gen_id("evt"))
    owner_id = payload.get("owner_id") or _staff_id(staff)
    conn.execute(
        """
        INSERT INTO vkpi_events
          (id, title, type_key, status, health_score, note, start_date, end_date,
           location_name, location_city, location_country, location_lat, location_lng,
           budget_total, budget_json, owner_id, team_ids, related_project_ids, invited_kols_json,
           product_sku, product_name, retrospective, created_at, updated_at)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?::jsonb,?,?::jsonb,?::jsonb,?::jsonb,?,?,?, NOW(), NOW())
        """,
        (
            eid,
            str(payload.get("title") or "未命名活动"),
            str(payload.get("type_key") or "other"),
            str(payload.get("status") or "planning"),
            int(payload.get("health_score") or 100),
            str(payload.get("note") or ""),
            payload.get("start_date") or str(_now().date()),
            payload.get("end_date") or str(_now().date()),
            str(payload.get("location_name") or ""),
            str(payload.get("location_city") or ""),
            str(payload.get("location_country") or ""),
            payload.get("location_lat"),
            payload.get("location_lng"),
            int(payload.get("budget_total") or 0),
            _dumps(payload.get("budget_json") or {}),
            owner_id,
            _dumps(payload.get("team_ids") or ([owner_id] if owner_id else [])),
            _dumps(payload.get("related_project_ids") or []),
            _dumps(payload.get("invited_kols_json") or []),
            str(payload.get("product_sku") or ""),
            str(payload.get("product_name") or ""),
            str(payload.get("retrospective") or ""),
        ),
    )
    conn.commit()
    row = conn.execute("SELECT * FROM vkpi_events WHERE id = ?", (eid,)).fetchone()
    return {"item": _event_row(row)}


_EVENT_UPDATABLE = {
    "title": str, "type_key": str, "status": str, "health_score": int, "note": str,
    "start_date": None, "end_date": None, "location_name": str, "location_city": str,
    "location_country": str, "location_lat": None, "location_lng": None,
    "budget_total": int, "retrospective": str, "roi": None, "leads": None, "videos": None,
    "product_sku": str, "product_name": str,
}
_EVENT_JSON_FIELDS = {"budget_json", "team_ids", "related_project_ids", "invited_kols_json"}


def update_event(event_id: str, payload: dict[str, Any], staff: dict[str, Any] | None) -> dict[str, Any]:
    conn = get_conn()
    sets: list[str] = []
    vals: list[Any] = []
    for key, caster in _EVENT_UPDATABLE.items():
        if key in payload:
            v = payload[key]
            sets.append(f"{key} = ?")
            vals.append(caster(v) if (caster and v is not None) else v)
    for key in _EVENT_JSON_FIELDS:
        if key in payload:
            sets.append(f"{key} = ?::jsonb")
            vals.append(_dumps(payload[key]))
    if not sets:
        row = conn.execute("SELECT * FROM vkpi_events WHERE id = ?", (str(event_id),)).fetchone()
        return {"item": _event_row(row) if row else None}
    sets.append("updated_at = NOW()")
    vals.append(str(event_id))
    conn.execute(f"UPDATE vkpi_events SET {', '.join(sets)} WHERE id = ?", tuple(vals))
    conn.commit()
    row = conn.execute("SELECT * FROM vkpi_events WHERE id = ?", (str(event_id),)).fetchone()
    return {"item": _event_row(row) if row else None}


def delete_event(event_id: str, staff: dict[str, Any] | None) -> dict[str, Any]:
    conn = get_conn()
    conn.execute("DELETE FROM vkpi_events WHERE id = ?", (str(event_id),))
    conn.commit()
    return {"ok": True, "id": str(event_id)}


# ── 多人协作:team members ──────────────────────────────────────────────────
def _set_team(conn: Any, event_id: str, team: list[Any]) -> dict[str, Any]:
    conn.execute(
        "UPDATE vkpi_events SET team_ids = ?::jsonb, updated_at = NOW() WHERE id = ?",
        (_dumps(team), str(event_id)),
    )
    conn.commit()
    row = conn.execute("SELECT * FROM vkpi_events WHERE id = ?", (str(event_id),)).fetchone()
    return {"item": _event_row(row) if row else None}


def add_member(event_id: str, user_id: Any, staff: dict[str, Any] | None) -> dict[str, Any]:
    conn = get_conn()
    row = conn.execute("SELECT team_ids FROM vkpi_events WHERE id = ?", (str(event_id),)).fetchone()
    team = _loads(dict(row).get("team_ids"), []) if row else []
    if user_id not in team:
        team.append(user_id)
    return _set_team(conn, event_id, team)


def remove_member(event_id: str, user_id: Any, staff: dict[str, Any] | None) -> dict[str, Any]:
    conn = get_conn()
    row = conn.execute("SELECT team_ids FROM vkpi_events WHERE id = ?", (str(event_id),)).fetchone()
    team = [u for u in (_loads(dict(row).get("team_ids"), []) if row else []) if str(u) != str(user_id)]
    return _set_team(conn, event_id, team)


# ── Tasks(含 collaborators / done_by 多人协作)──────────────────────────────
def add_task(event_id: str, payload: dict[str, Any], staff: dict[str, Any] | None) -> dict[str, Any]:
    conn = get_conn()
    tid = str(payload.get("id") or _gen_id("tsk"))
    conn.execute(
        """
        INSERT INTO vkpi_event_tasks
          (id, event_id, title, phase, owner, collaborators, due_date, kind, checklist, details, created_at, updated_at)
        VALUES (?,?,?,?,?,?::jsonb,?,?,?::jsonb,?::jsonb, NOW(), NOW())
        """,
        (
            tid, str(event_id), str(payload.get("title") or ""), str(payload.get("phase") or "prep"),
            str(payload.get("owner") or ""), _dumps(payload.get("collaborators") or []),
            payload.get("due_date"), str(payload.get("kind") or "task"),
            _dumps(payload.get("checklist") or []), _dumps(payload.get("details") or {}),
        ),
    )
    conn.commit()
    row = conn.execute("SELECT * FROM vkpi_event_tasks WHERE id = ?", (tid,)).fetchone()
    return {"item": _task_row(row) if row else None}


def update_task(event_id: str, task_id: str, payload: dict[str, Any], staff: dict[str, Any] | None) -> dict[str, Any]:
    conn = get_conn()
    sets: list[str] = []
    vals: list[Any] = []
    for key in ("title", "phase", "owner", "due_date", "kind"):
        if key in payload:
            sets.append(f"{key} = ?")
            vals.append(payload[key])
    if "done" in payload:
        sets.append("done = ?")
        vals.append(bool(payload["done"]))
        sets.append("done_at = ?")
        vals.append(_now() if payload["done"] else None)
        if "done_by" in payload:
            sets.append("done_by = ?")
            vals.append(str(payload.get("done_by") or ""))
    for key in ("collaborators", "checklist", "details"):
        if key in payload:
            sets.append(f"{key} = ?::jsonb")
            vals.append(_dumps(payload[key]))
    if not sets:
        return {"ok": True}
    sets.append("updated_at = NOW()")
    vals.extend([str(task_id), str(event_id)])
    conn.execute(f"UPDATE vkpi_event_tasks SET {', '.join(sets)} WHERE id = ? AND event_id = ?", tuple(vals))
    conn.commit()
    row = conn.execute("SELECT * FROM vkpi_event_tasks WHERE id = ?", (str(task_id),)).fetchone()
    return {"item": _task_row(row) if row else None}


def delete_task(event_id: str, task_id: str, staff: dict[str, Any] | None) -> dict[str, Any]:
    conn = get_conn()
    conn.execute("DELETE FROM vkpi_event_tasks WHERE id = ? AND event_id = ?", (str(task_id), str(event_id)))
    conn.commit()
    return {"ok": True, "id": str(task_id)}


# ── Expenses ────────────────────────────────────────────────────────────────
def add_expense(event_id: str, payload: dict[str, Any], staff: dict[str, Any] | None) -> dict[str, Any]:
    conn = get_conn()
    xid = str(payload.get("id") or _gen_id("exp"))
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
    conn.execute("DELETE FROM vkpi_event_expenses WHERE id = ? AND event_id = ?", (str(expense_id), str(event_id)))
    conn.commit()
    return {"ok": True, "id": str(expense_id)}


# ── KOL invites ─────────────────────────────────────────────────────────────
def invite_kol(event_id: str, payload: dict[str, Any], staff: dict[str, Any] | None) -> dict[str, Any]:
    conn = get_conn()
    iid = str(payload.get("id") or _gen_id("inv"))
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
    conn.execute("DELETE FROM vkpi_event_kol_invites WHERE id = ? AND event_id = ?", (str(invite_id), str(event_id)))
    conn.commit()
    return {"ok": True, "id": str(invite_id)}


# ── Materials(活动营销物料,逐项落库)──────────────────────────────────────────
def add_material(event_id: str, payload: dict[str, Any], staff: dict[str, Any] | None) -> dict[str, Any]:
    conn = get_conn()
    mid = str(payload.get("id") or _gen_id("mat"))
    conn.execute(
        """
        INSERT INTO vkpi_event_materials
          (id, event_id, name, category, source, qty, status, owner, note, tracking_no, file_url, alert, created_at, updated_at)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?, NOW(), NOW())
        """,
        (
            mid, str(event_id), str(payload.get("name") or ""), str(payload.get("category") or "display"),
            str(payload.get("source") or "ship"), int(payload.get("qty") or 1),
            str(payload.get("status") or "pending"), str(payload.get("owner") or ""),
            str(payload.get("note") or ""), str(payload.get("trackingNo") or payload.get("tracking_no") or ""),
            str(payload.get("fileUrl") or payload.get("file_url") or ""), str(payload.get("alert") or ""),
        ),
    )
    conn.commit()
    row = conn.execute("SELECT * FROM vkpi_event_materials WHERE id = ?", (mid,)).fetchone()
    return {"item": _material_row(row) if row else None}


def update_material(event_id: str, material_id: str, payload: dict[str, Any], staff: dict[str, Any] | None) -> dict[str, Any]:
    conn = get_conn()
    sets: list[str] = []
    vals: list[Any] = []
    for key in ("name", "category", "source", "status", "owner", "note", "alert"):
        if key in payload:
            sets.append(f"{key} = ?")
            vals.append(str(payload[key]) if payload[key] is not None else "")
    if "qty" in payload:
        sets.append("qty = ?")
        vals.append(int(payload.get("qty") or 0))
    if "trackingNo" in payload or "tracking_no" in payload:
        sets.append("tracking_no = ?")
        vals.append(str(payload.get("trackingNo") or payload.get("tracking_no") or ""))
    if "fileUrl" in payload or "file_url" in payload:
        sets.append("file_url = ?")
        vals.append(str(payload.get("fileUrl") or payload.get("file_url") or ""))
    if not sets:
        row = conn.execute(
            "SELECT * FROM vkpi_event_materials WHERE id = ? AND event_id = ?", (str(material_id), str(event_id))
        ).fetchone()
        return {"item": _material_row(row) if row else None}
    sets.append("updated_at = NOW()")
    vals.extend([str(material_id), str(event_id)])
    conn.execute(f"UPDATE vkpi_event_materials SET {', '.join(sets)} WHERE id = ? AND event_id = ?", tuple(vals))
    conn.commit()
    row = conn.execute("SELECT * FROM vkpi_event_materials WHERE id = ?", (str(material_id),)).fetchone()
    return {"item": _material_row(row) if row else None}


def delete_material(event_id: str, material_id: str, staff: dict[str, Any] | None) -> dict[str, Any]:
    conn = get_conn()
    conn.execute("DELETE FROM vkpi_event_materials WHERE id = ? AND event_id = ?", (str(material_id), str(event_id)))
    conn.commit()
    return {"ok": True, "id": str(material_id)}


# ── Products(活动产品准备,逐项落库)──────────────────────────────────────────
def add_product(event_id: str, payload: dict[str, Any], staff: dict[str, Any] | None) -> dict[str, Any]:
    conn = get_conn()
    pid = str(payload.get("id") or _gen_id("pp"))
    ra = payload.get("returnAfter")
    if ra is None:
        ra = payload.get("return_after")
    conn.execute(
        """
        INSERT INTO vkpi_event_products
          (id, event_id, name, category, source, qty, status, owner, note, tracking_no, arrive_by, return_after, created_at, updated_at)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?, NOW(), NOW())
        """,
        (
            pid, str(event_id), str(payload.get("name") or ""), str(payload.get("category") or "lens"),
            str(payload.get("source") or "new_purchase"), int(payload.get("qty") or 1),
            str(payload.get("status") or "ordered"), str(payload.get("owner") or ""),
            str(payload.get("note") or ""), str(payload.get("trackingNo") or payload.get("tracking_no") or ""),
            str(payload.get("arriveBy") or payload.get("arrive_by") or ""), bool(ra or False),
        ),
    )
    conn.commit()
    row = conn.execute("SELECT * FROM vkpi_event_products WHERE id = ?", (pid,)).fetchone()
    return {"item": _product_row(row) if row else None}


def update_product(event_id: str, product_id: str, payload: dict[str, Any], staff: dict[str, Any] | None) -> dict[str, Any]:
    conn = get_conn()
    sets: list[str] = []
    vals: list[Any] = []
    for key in ("name", "category", "source", "status", "owner", "note"):
        if key in payload:
            sets.append(f"{key} = ?")
            vals.append(str(payload[key]) if payload[key] is not None else "")
    if "qty" in payload:
        sets.append("qty = ?")
        vals.append(int(payload.get("qty") or 0))
    if "trackingNo" in payload or "tracking_no" in payload:
        sets.append("tracking_no = ?")
        vals.append(str(payload.get("trackingNo") or payload.get("tracking_no") or ""))
    if "arriveBy" in payload or "arrive_by" in payload:
        sets.append("arrive_by = ?")
        vals.append(str(payload.get("arriveBy") or payload.get("arrive_by") or ""))
    if "returnAfter" in payload or "return_after" in payload:
        ra = payload.get("returnAfter")
        if ra is None:
            ra = payload.get("return_after")
        sets.append("return_after = ?")
        vals.append(bool(ra or False))
    if not sets:
        row = conn.execute(
            "SELECT * FROM vkpi_event_products WHERE id = ? AND event_id = ?", (str(product_id), str(event_id))
        ).fetchone()
        return {"item": _product_row(row) if row else None}
    sets.append("updated_at = NOW()")
    vals.extend([str(product_id), str(event_id)])
    conn.execute(f"UPDATE vkpi_event_products SET {', '.join(sets)} WHERE id = ? AND event_id = ?", tuple(vals))
    conn.commit()
    row = conn.execute("SELECT * FROM vkpi_event_products WHERE id = ?", (str(product_id),)).fetchone()
    return {"item": _product_row(row) if row else None}


def delete_product(event_id: str, product_id: str, staff: dict[str, Any] | None) -> dict[str, Any]:
    conn = get_conn()
    conn.execute("DELETE FROM vkpi_event_products WHERE id = ? AND event_id = ?", (str(product_id), str(event_id)))
    conn.commit()
    return {"ok": True, "id": str(product_id)}
