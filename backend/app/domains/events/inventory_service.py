"""V-KPI 公司库存(Company Inventory)domain service — CRUD + 调动审计账本。

迁移 135(vkpi_inventory,sku UNIQUE)+ 136(vkpi_inventory_movements,审计流水)。
库存项跨 Event 共享(样品 / 量产 / 配件 / 设备),前端以 sku 为资源键做 PATCH/DELETE。
每一次 mutation(create/update/delete/调量)都在同一事务里向 vkpi_inventory_movements
落一条不可篡改流水:谁(moved_by_staff_id)/动作(action)/数量变化(delta_qty)/
变化后(new_qty)/事由(reason)/关联活动(event_id)/上下文(metadata_json)。

DB 走 get_conn(? 占位)应用路径,镜像 events/service.py。员工身份用 scope.actor_staff_id。
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


def _staff_id(staff: dict[str, Any] | None) -> int | None:
    try:
        sid = scope.actor_staff_id(staff)
        return sid or None
    except Exception:
        return (staff or {}).get("staff_id") or (staff or {}).get("id")


def _item_row(r: Any) -> dict[str, Any]:
    return dict(r) if r is not None else None


def _movement_row(r: Any) -> dict[str, Any]:
    row = dict(r)
    row["metadata_json"] = _loads(row.get("metadata_json"), {})
    return row


def _log_movement(
    conn: Any,
    *,
    inventory_sku: str,
    action: str,
    staff: dict[str, Any] | None,
    delta_qty: int | None = None,
    new_qty: int | None = None,
    reason: str = "",
    event_id: str | None = None,
    metadata: dict[str, Any] | None = None,
) -> None:
    """向审计账本落一条流水(与调用方共用同一事务,由调用方 commit)。"""
    mid = _gen_id("mov")
    conn.execute(
        """
        INSERT INTO vkpi_inventory_movements
          (id, inventory_sku, event_id, action, delta_qty, new_qty, reason,
           moved_by_staff_id, metadata_json, created_at)
        VALUES (?,?,?,?,?,?,?,?,?::jsonb, NOW())
        """,
        (
            mid,
            str(inventory_sku),
            str(event_id) if event_id not in (None, "") else None,
            str(action or "adjust"),
            int(delta_qty) if delta_qty is not None else None,
            int(new_qty) if new_qty is not None else None,
            str(reason or ""),
            _staff_id(staff),
            _dumps(metadata or {}),
        ),
    )


# ── Inventory CRUD ──────────────────────────────────────────────────────────
def list_inventory() -> dict[str, Any]:
    """列全部库存项(跨 Event 共享,不按员工 scope 过滤——公司公共库存表)。"""
    conn = get_conn()
    rows = conn.execute(
        "SELECT * FROM vkpi_inventory ORDER BY is_sample DESC, category ASC, created_at ASC"
    ).fetchall()
    return {"items": [_item_row(r) for r in rows]}


def _get_by_sku(conn: Any, sku: str) -> dict[str, Any] | None:
    row = conn.execute("SELECT * FROM vkpi_inventory WHERE sku = ?", (str(sku),)).fetchone()
    return _item_row(row) if row else None


def create_item(body: dict[str, Any], staff: dict[str, Any] | None) -> dict[str, Any]:
    """新建库存项 + 落一条 action='add' 流水(delta=new_qty=初始数量)。"""
    conn = get_conn()
    sku = str((body or {}).get("sku") or "").strip()
    if not sku:
        raise ValueError("sku required")
    if _get_by_sku(conn, sku):
        raise ValueError(f"sku already exists: {sku}")
    iid = str((body or {}).get("id") or _gen_id("s"))
    qty = int((body or {}).get("qty") or 0)
    conn.execute(
        """
        INSERT INTO vkpi_inventory
          (id, sku, name, category, qty, location, note, is_sample, created_by_staff_id, created_at, updated_at)
        VALUES (?,?,?,?,?,?,?,?,?, NOW(), NOW())
        """,
        (
            iid,
            sku,
            str((body or {}).get("name") or ""),
            str((body or {}).get("category") or "lens"),
            qty,
            str((body or {}).get("location") or ""),
            str((body or {}).get("note") or ""),
            bool((body or {}).get("is_sample") or False),
            _staff_id(staff),
        ),
    )
    _log_movement(
        conn,
        inventory_sku=sku,
        action="add",
        staff=staff,
        delta_qty=qty,
        new_qty=qty,
        reason=str((body or {}).get("reason") or "新建库存项"),
        event_id=(body or {}).get("event_id"),
        metadata={"name": str((body or {}).get("name") or ""), "category": str((body or {}).get("category") or "lens")},
    )
    conn.commit()
    return {"item": _get_by_sku(conn, sku)}


_UPDATABLE = {"name": str, "category": str, "location": str, "note": str}


def update_item(sku: str, body: dict[str, Any], staff: dict[str, Any] | None) -> dict[str, Any]:
    """改库存项。qty 变化 → action='adjust' 流水(记 delta/new_qty);
    仅改字段(name/location/...)→ action='edit' 流水(记改了哪些字段)。"""
    conn = get_conn()
    body = body or {}
    current = _get_by_sku(conn, str(sku))
    if not current:
        raise ValueError(f"inventory not found: {sku}")

    sets: list[str] = []
    vals: list[Any] = []
    changed_fields: list[str] = []
    for key, caster in _UPDATABLE.items():
        if key in body:
            sets.append(f"{key} = ?")
            vals.append(caster(body[key]) if body[key] is not None else "")
            changed_fields.append(key)
    if "is_sample" in body:
        sets.append("is_sample = ?")
        vals.append(bool(body["is_sample"]))
        changed_fields.append("is_sample")

    qty_changed = "qty" in body
    old_qty = int(current.get("qty") or 0)
    new_qty = old_qty
    if qty_changed:
        new_qty = max(0, int(body.get("qty") or 0))
        sets.append("qty = ?")
        vals.append(new_qty)

    if not sets:
        return {"item": current}

    sets.append("updated_at = NOW()")
    vals.append(str(sku))
    conn.execute(f"UPDATE vkpi_inventory SET {', '.join(sets)} WHERE sku = ?", tuple(vals))

    if qty_changed:
        delta = new_qty - old_qty
        _log_movement(
            conn,
            inventory_sku=str(sku),
            action="adjust",
            staff=staff,
            delta_qty=delta,
            new_qty=new_qty,
            reason=str(body.get("reason") or ("增加库存" if delta > 0 else "减少库存" if delta < 0 else "调整库存")),
            event_id=body.get("event_id"),
            metadata={"old_qty": old_qty, "fields": changed_fields},
        )
    elif changed_fields:
        _log_movement(
            conn,
            inventory_sku=str(sku),
            action="edit",
            staff=staff,
            delta_qty=None,
            new_qty=new_qty,
            reason=str(body.get("reason") or f"修改字段: {', '.join(changed_fields)}"),
            event_id=body.get("event_id"),
            metadata={"fields": changed_fields},
        )
    conn.commit()
    return {"item": _get_by_sku(conn, str(sku))}


def delete_item(sku: str, body: dict[str, Any] | None, staff: dict[str, Any] | None) -> dict[str, Any]:
    """删库存项 + 落一条 action='delete' 流水(delta = -剩余数量)。
    流水先于删除写入(不带库存外键),库存项删了历史仍可追溯。"""
    conn = get_conn()
    body = body or {}
    current = _get_by_sku(conn, str(sku))
    if not current:
        raise ValueError(f"inventory not found: {sku}")
    old_qty = int(current.get("qty") or 0)
    _log_movement(
        conn,
        inventory_sku=str(sku),
        action="delete",
        staff=staff,
        delta_qty=-old_qty,
        new_qty=0,
        reason=str(body.get("reason") or "删除库存项"),
        event_id=body.get("event_id"),
        metadata={"name": current.get("name"), "category": current.get("category"), "qty_at_delete": old_qty},
    )
    conn.execute("DELETE FROM vkpi_inventory WHERE sku = ?", (str(sku),))
    conn.commit()
    return {"ok": True, "sku": str(sku)}


# ── Groups 组团(逻辑分组,不扣库存)─────────────────────────────────────────
def _group_row(conn: Any, g: dict[str, Any]) -> dict[str, Any]:
    """补上组内 items(JOIN 库存表拿 name/category/总库存,诚实显示是否充足)。"""
    rows = conn.execute(
        """
        SELECT gi.id, gi.inventory_sku, gi.qty_in_group, gi.note,
               i.name AS item_name, i.category AS item_category, i.qty AS total_available
        FROM vkpi_inventory_group_items gi
        LEFT JOIN vkpi_inventory i ON i.sku = gi.inventory_sku
        WHERE gi.group_id = ?
        ORDER BY gi.added_at ASC
        """,
        (str(g["id"]),),
    ).fetchall()
    items = [dict(r) for r in rows]
    g = dict(g)
    g["items"] = items
    g["item_count"] = len(items)
    g["total_units"] = sum(int(it.get("qty_in_group") or 0) for it in items)
    return g


def list_groups(event_id: str | None = None) -> dict[str, Any]:
    """列全部组(或某 event 的组),每组带成员清单。"""
    conn = get_conn()
    if event_id:
        rows = conn.execute(
            "SELECT * FROM vkpi_inventory_groups WHERE event_id = ? ORDER BY created_at DESC",
            (str(event_id),),
        ).fetchall()
    else:
        rows = conn.execute("SELECT * FROM vkpi_inventory_groups ORDER BY created_at DESC").fetchall()
    return {"groups": [_group_row(conn, dict(r)) for r in rows]}


def create_group(body: dict[str, Any], staff: dict[str, Any] | None) -> dict[str, Any]:
    """新建组(可带初始成员 items=[{sku, qty, note}])。"""
    conn = get_conn()
    body = body or {}
    name = str(body.get("name") or "").strip()
    if not name:
        raise ValueError("group name required")
    gid = _gen_id("grp")
    conn.execute(
        """
        INSERT INTO vkpi_inventory_groups
          (id, name, note, location, event_id, created_by_staff_id, created_at, updated_at)
        VALUES (?,?,?,?,?,?, NOW(), NOW())
        """,
        (
            gid,
            name,
            str(body.get("note") or ""),
            str(body.get("location") or ""),
            str(body.get("event_id")) if body.get("event_id") else None,
            _staff_id(staff),
        ),
    )
    for i, it in enumerate(body.get("items") or []):
        sku = str((it or {}).get("sku") or "").strip()
        if not sku:
            continue
        conn.execute(
            "INSERT INTO vkpi_inventory_group_items (id, group_id, inventory_sku, qty_in_group, note, added_at) "
            "VALUES (?,?,?,?,?, NOW())",
            (f"{_gen_id('gi')}_{i}", gid, sku, max(1, int((it or {}).get("qty") or 1)), str((it or {}).get("note") or "")),
        )
    conn.commit()
    row = conn.execute("SELECT * FROM vkpi_inventory_groups WHERE id = ?", (gid,)).fetchone()
    return {"group": _group_row(conn, dict(row))}


def update_group(group_id: str, body: dict[str, Any], staff: dict[str, Any] | None) -> dict[str, Any]:
    """改组名/备注/位置/关联 event。"""
    conn = get_conn()
    body = body or {}
    sets, vals = [], []
    for key in ("name", "note", "location", "event_id"):
        if key in body:
            sets.append(f"{key} = ?")
            vals.append(str(body[key]) if body[key] not in (None, "") else None)
    if not sets:
        row = conn.execute("SELECT * FROM vkpi_inventory_groups WHERE id = ?", (str(group_id),)).fetchone()
        if not row:
            raise ValueError(f"group not found: {group_id}")
        return {"group": _group_row(conn, dict(row))}
    sets.append("updated_at = NOW()")
    vals.append(str(group_id))
    conn.execute(f"UPDATE vkpi_inventory_groups SET {', '.join(sets)} WHERE id = ?", tuple(vals))
    conn.commit()
    row = conn.execute("SELECT * FROM vkpi_inventory_groups WHERE id = ?", (str(group_id),)).fetchone()
    if not row:
        raise ValueError(f"group not found: {group_id}")
    return {"group": _group_row(conn, dict(row))}


def delete_group(group_id: str, body: dict[str, Any] | None, staff: dict[str, Any] | None) -> dict[str, Any]:
    """删组(成员行 ON DELETE CASCADE 一并删;不动各项独立库存)。"""
    conn = get_conn()
    row = conn.execute("SELECT * FROM vkpi_inventory_groups WHERE id = ?", (str(group_id),)).fetchone()
    if not row:
        raise ValueError(f"group not found: {group_id}")
    conn.execute("DELETE FROM vkpi_inventory_groups WHERE id = ?", (str(group_id),))
    conn.commit()
    return {"ok": True, "group_id": str(group_id)}


def add_to_group(group_id: str, body: dict[str, Any], staff: dict[str, Any] | None) -> dict[str, Any]:
    """往组里加一个 SKU × 数量(逻辑加入,不扣库存)。"""
    conn = get_conn()
    body = body or {}
    sku = str(body.get("sku") or "").strip()
    if not sku:
        raise ValueError("sku required")
    g = conn.execute("SELECT id FROM vkpi_inventory_groups WHERE id = ?", (str(group_id),)).fetchone()
    if not g:
        raise ValueError(f"group not found: {group_id}")
    conn.execute(
        "INSERT INTO vkpi_inventory_group_items (id, group_id, inventory_sku, qty_in_group, note, added_at) "
        "VALUES (?,?,?,?,?, NOW())",
        (_gen_id("gi"), str(group_id), sku, max(1, int(body.get("qty") or 1)), str(body.get("note") or "")),
    )
    conn.commit()
    row = conn.execute("SELECT * FROM vkpi_inventory_groups WHERE id = ?", (str(group_id),)).fetchone()
    return {"group": _group_row(conn, dict(row))}


def remove_from_group(group_id: str, item_id: str, staff: dict[str, Any] | None) -> dict[str, Any]:
    """从组里移除一个成员行(不动各项独立库存)。"""
    conn = get_conn()
    conn.execute(
        "DELETE FROM vkpi_inventory_group_items WHERE id = ? AND group_id = ?",
        (str(item_id), str(group_id)),
    )
    conn.commit()
    row = conn.execute("SELECT * FROM vkpi_inventory_groups WHERE id = ?", (str(group_id),)).fetchone()
    if not row:
        return {"ok": True, "group_id": str(group_id)}
    return {"group": _group_row(conn, dict(row))}


def find_sku_groups(sku: str) -> dict[str, Any]:
    """查某 SKU 在哪些组里(用于库存表行显示「所在分组」)。"""
    conn = get_conn()
    rows = conn.execute(
        """
        SELECT g.id AS group_id, g.name AS group_name, g.location, gi.qty_in_group
        FROM vkpi_inventory_group_items gi
        JOIN vkpi_inventory_groups g ON g.id = gi.group_id
        WHERE gi.inventory_sku = ?
        ORDER BY g.created_at DESC
        """,
        (str(sku),),
    ).fetchall()
    return {"groups": [dict(r) for r in rows]}


# ── 从产品库批量补缺 SKU(qty=0,幂等,只增不改)──────────────────────────────
_CAT_MAP = {
    "lens": "lens", "lenses": "lens",
    "lighting": "equipment", "flash": "equipment", "monitor": "equipment", "equipment": "equipment",
    "accessories": "accessory", "accessory": "accessory", "battery": "accessory",
    "uv filter": "accessory", "filter": "accessory", "macro extension tube": "accessory",
}


def import_missing_from_catalog(staff: dict[str, Any] | None) -> dict[str, Any]:
    """把 vkpi_products 里有、vkpi_inventory 里没有的 SKU 批量补进库存(qty=0,location 待填)。
    幂等:已存在的 sku ON CONFLICT DO NOTHING;绝不覆盖已有项的 qty/字段。"""
    conn = get_conn()
    # 产品库列名兼容(category_main / category)
    cols = {c[0] for c in conn.execute(
        "SELECT column_name FROM information_schema.columns WHERE table_name = 'vkpi_products'"
    ).fetchall() if isinstance(c, (tuple, list))} or {
        dict(r)["column_name"] for r in conn.execute(
            "SELECT column_name FROM information_schema.columns WHERE table_name = 'vkpi_products'"
        ).fetchall()
    }
    cat_col = "category_main" if "category_main" in cols else ("category" if "category" in cols else None)
    name_col = "name" if "name" in cols else ("product_name" if "product_name" in cols else "sku")
    sel = f"SELECT p.sku AS sku, p.{name_col} AS name" + (f", p.{cat_col} AS cat" if cat_col else ", '' AS cat")
    sel += " FROM vkpi_products p LEFT JOIN vkpi_inventory i ON i.sku = p.sku WHERE i.sku IS NULL AND p.sku IS NOT NULL AND p.sku <> ''"
    rows = conn.execute(sel).fetchall()
    # created_by_staff_id 有 FK→staff:校验存在,否则置 NULL(批量导入容忍无效/系统身份)。
    sid = _staff_id(staff)
    if sid is not None and not conn.execute("SELECT 1 FROM staff WHERE id = ?", (sid,)).fetchone():
        sid = None
    added = 0
    for i, r in enumerate(rows):
        d = dict(r)
        sku = str(d.get("sku") or "").strip()
        if not sku:
            continue
        cat = _CAT_MAP.get(str(d.get("cat") or "").strip().lower(), "accessory")
        conn.execute(
            """
            INSERT INTO vkpi_inventory (id, sku, name, category, qty, location, note, is_sample, created_by_staff_id, created_at, updated_at)
            VALUES (?,?,?,?,0,'','从产品库导入·待填库存量',FALSE,?, NOW(), NOW())
            ON CONFLICT (sku) DO NOTHING
            """,
            (f"{_gen_id('s')}_{i}", sku, str(d.get("name") or sku), cat, sid),
        )
        added += 1
    conn.commit()
    return {"ok": True, "imported": added}


# ── Movements ledger ────────────────────────────────────────────────────────
def list_movements(sku: str | None = None, limit: int = 100) -> dict[str, Any]:
    """读审计流水。sku 给定则只看该 SKU,否则全量(均按时间倒序)。"""
    conn = get_conn()
    safe_limit = max(1, min(int(limit or 100), 500))
    if sku:
        rows = conn.execute(
            "SELECT * FROM vkpi_inventory_movements WHERE inventory_sku = ? "
            "ORDER BY created_at DESC LIMIT ?",
            (str(sku), safe_limit),
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT * FROM vkpi_inventory_movements ORDER BY created_at DESC LIMIT ?",
            (safe_limit,),
        ).fetchall()
    return {"items": [_movement_row(r) for r in rows]}
