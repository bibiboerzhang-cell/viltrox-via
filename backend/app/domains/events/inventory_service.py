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
