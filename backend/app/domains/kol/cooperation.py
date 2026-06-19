"""KOL 合作动作 domain(平台为基准)。

续约/加大投入/退出合作/评估/备注 → append-only 事件时间线(vkpi_kol_cooperation_events,迁移 171)。
当前合作状态 = 该 KOL 最新事件的 status_after。按真 kol_pool_id 落库 = 平台为基准源,数据互传方便。
红线:只写本表,绝不触 viltrox_fit_score / rule_v0 或 vkpi_kol_pool 主表。
"""
from __future__ import annotations

from typing import Any

from app.db.connection import get_conn

# 动作 → 落定后的合作状态(note 不改状态,沿用上一状态)
_ACTION_STATUS = {
    "renew": "active",
    "scale_up": "scaling",
    "exit": "exited",
    "evaluate": "evaluating",
    "note": "",
}
_ACTION_LABELS = {
    "renew": "续约",
    "scale_up": "加大投入",
    "exit": "退出合作",
    "evaluate": "评估",
    "note": "备注",
}
_STATUS_LABELS = {
    "active": "合作中",
    "scaling": "加大投入",
    "exited": "已退出",
    "evaluating": "评估中",
    "": "未设定",
}


def _staff_id(staff: dict[str, Any] | None) -> int | None:
    for key in ("staff_id", "id", "user_id"):
        try:
            value = int((staff or {}).get(key) or 0)
        except (TypeError, ValueError):
            value = 0
        if value:
            return value
    return None


def _event_row(r: Any) -> dict[str, Any]:
    d = dict(r)
    d["action_label"] = _ACTION_LABELS.get(d.get("action_type"), d.get("action_type"))
    d["status_label"] = _STATUS_LABELS.get(d.get("status_after"), d.get("status_after"))
    return d


def get_cooperation(kol_pool_id: int) -> dict[str, Any]:
    """读 KOL 当前合作状态 + 最近时间线(最多 50 条,倒序)。"""
    conn = get_conn()
    rows = conn.execute(
        "SELECT * FROM vkpi_kol_cooperation_events WHERE kol_pool_id=? ORDER BY created_at DESC LIMIT 50",
        (int(kol_pool_id),),
    ).fetchall()
    events = [_event_row(r) for r in rows]
    current = events[0]["status_after"] if events else ""
    return {
        "kol_pool_id": int(kol_pool_id),
        "status": current,
        "status_label": _STATUS_LABELS.get(current, current or "未设定"),
        "events": events,
    }


def record_action(kol_pool_id: int, action_type: str, *, note: str = "", staff: dict[str, Any] | None = None) -> dict[str, Any]:
    """记一条合作动作。action_type ∈ renew/scale_up/exit/evaluate/note。
    note 不改状态(沿用最新);其余动作落定对应 status_after。返回最新合作快照。"""
    action_type = str(action_type or "").strip()
    if action_type not in _ACTION_STATUS:
        raise ValueError(f"unknown action_type: {action_type}")
    conn = get_conn()
    if not conn.execute("SELECT id FROM vkpi_kol_pool WHERE id=?", (int(kol_pool_id),)).fetchone():
        raise LookupError("kol pool item not found")
    status = _ACTION_STATUS[action_type]
    if action_type == "note":
        last = conn.execute(
            "SELECT status_after FROM vkpi_kol_cooperation_events WHERE kol_pool_id=? ORDER BY created_at DESC LIMIT 1",
            (int(kol_pool_id),),
        ).fetchone()
        status = (dict(last).get("status_after") if last else "") or ""
    conn.execute(
        """
        INSERT INTO vkpi_kol_cooperation_events
            (kol_pool_id, action_type, status_after, note, actor_staff_id, created_at)
        VALUES (?,?,?,?,?, NOW())
        """,
        (int(kol_pool_id), action_type, status, str(note or ""), _staff_id(staff)),
    )
    conn.commit()
    return get_cooperation(int(kol_pool_id))
