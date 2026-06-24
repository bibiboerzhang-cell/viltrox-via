"""路线4 · 活动复盘聚合(只读:活动结束后自动算复盘 + 待补数据)。

compute-on-read 从既有活动数据组装一份复盘摘要:预算 vs 实际花费、任务完成度、KOL 邀约/到场、
结果(roi/leads/videos/retrospective)、待补数据清单、完整度。让"活动壳"变成真复盘闭环。
红线:全程只读;绝不臆造业务数值(缺 roi/leads 就列入待补,不填 0);零触 viltrox_fit_score。
"""
from __future__ import annotations

import json
from typing import Any

from app.core.logging import get_logger
from app.db.connection import get_conn, table_exists

logger = get_logger(__name__)


def _scalar(sql: str, params: tuple[Any, ...]) -> Any:
    try:
        row = get_conn().execute(sql, params).fetchone()
        return dict(row).get("v") if row else None
    except Exception:
        logger.debug("event_retro.scalar_failed", extra={"sql": sql[:60]}, exc_info=True)
        return None


def _loads(value: Any, default: Any) -> Any:
    if isinstance(value, (dict, list)):
        return value
    try:
        return json.loads(value or "")
    except Exception:
        return default


def aggregate_event_retrospective(event_id: str, staff: dict[str, Any] | None = None) -> dict[str, Any]:
    """活动复盘摘要(只读)。不存在 → status='not_found'。staff 位置参数(与 event service 同款,适配 _guard)。"""
    del staff
    eid = str(event_id or "").strip()
    if not eid:
        return {"status": "not_found"}
    if not table_exists("vkpi_events"):
        return {"status": "unavailable", "reason": "events_table_absent"}
    conn = get_conn()
    row = conn.execute("SELECT * FROM vkpi_events WHERE id = ?", (eid,)).fetchone()
    if not row:
        return {"status": "not_found", "event_id": eid}
    ev = dict(row)

    # 任务完成度。
    tasks_total = _scalar("SELECT COUNT(*) AS v FROM vkpi_event_tasks WHERE event_id = ?", (eid,)) if table_exists("vkpi_event_tasks") else None
    tasks_done = _scalar("SELECT COUNT(*) AS v FROM vkpi_event_tasks WHERE event_id = ? AND COALESCE(done, FALSE) = TRUE", (eid,)) if table_exists("vkpi_event_tasks") else None

    # 实际花费(尽力:expenses 有 amount_cents 列才求和)。
    actual_spend_cents = None
    if table_exists("vkpi_event_expenses"):
        actual_spend_cents = _scalar("SELECT COALESCE(SUM(amount_cents), 0) AS v FROM vkpi_event_expenses WHERE event_id = ?", (eid,))
        if actual_spend_cents is None:
            actual_spend_cents = _scalar("SELECT COUNT(*) AS v FROM vkpi_event_expenses WHERE event_id = ?", (eid,))  # 退化为笔数

    # KOL 邀约 / 到场。
    invites_total = _scalar("SELECT COUNT(*) AS v FROM vkpi_event_kol_invites WHERE event_id = ?", (eid,)) if table_exists("vkpi_event_kol_invites") else None

    # 结果字段 + 待补数据(缺就列入待补,绝不填 0)。
    missing: list[str] = []
    for label, key in (("ROI", "roi"), ("线索数", "leads"), ("视频数", "videos"), ("复盘", "retrospective")):
        if str(ev.get(key) or "").strip() in ("", "0", "None"):
            missing.append(label)

    related_projects = _loads(ev.get("related_project_ids"), [])
    invited_kols = _loads(ev.get("invited_kols_json"), [])

    # 完整度:有任务/有花费/无待补 → 越完整。
    filled = 4 - len(missing)
    completeness = round(min(1.0, max(0.0, filled / 4.0)), 2)

    return {
        "status": "ok",
        "event_id": eid,
        "title": ev.get("title"),
        "event_status": ev.get("status"),
        "start_date": str(ev.get("start_date") or ""),
        "end_date": str(ev.get("end_date") or ""),
        "budget": {
            "budget_total_cents": ev.get("budget_total"),
            "actual_spend_cents": actual_spend_cents,
        },
        "tasks": {"total": tasks_total, "done": tasks_done, "pending": (tasks_total - tasks_done) if (tasks_total is not None and tasks_done is not None) else None},
        "kol": {"invited": invites_total, "invited_kols_count": len(invited_kols) if isinstance(invited_kols, list) else None},
        "results": {
            "roi": ev.get("roi"),
            "leads": ev.get("leads"),
            "videos": ev.get("videos"),
            "retrospective": ev.get("retrospective"),
        },
        "related_project_ids": related_projects if isinstance(related_projects, list) else [],
        "missing_data": missing,
        "completeness": completeness,
        "note": "活动复盘只读聚合;缺 ROI/线索/视频/复盘列入待补(不填 0);真值仍需人工回填。零触 viltrox_fit_score。",
    }


_RETRO_TABLE = "vkpi_event_retrospectives"


def finalize_event_retrospective(event_id: str, staff: dict[str, Any] | None = None) -> dict[str, Any]:
    """F4 · 定格复盘:聚合当前复盘 → 落库一行快照(vkpi_event_retrospectives)。

    返回快照 + finalized 标记。活动不存在 → status='not_found'。零触 viltrox_fit_score。
    """
    snap = aggregate_event_retrospective(event_id, staff)
    if snap.get("status") != "ok":
        return snap
    if not table_exists(_RETRO_TABLE):
        return {**snap, "finalized": False, "reason": "migration_184_not_applied"}
    actor = None
    try:
        from app.domains.access import scope

        actor = int(scope.actor_staff_id(staff)) or None
    except Exception:
        actor = None
    try:
        conn = get_conn()
        row = conn.execute(
            f"INSERT INTO {_RETRO_TABLE} (event_id, snapshot_json, completeness, finalized_by) "
            f"VALUES (?, ?::jsonb, ?, ?) RETURNING id, created_at",
            (str(event_id), json.dumps(snap, ensure_ascii=False, default=str), float(snap.get("completeness") or 0.0), actor),
        ).fetchone()
        conn.commit()
        rec = dict(row) if row else {}
        return {**snap, "finalized": True, "retrospective_id": rec.get("id"), "finalized_at": str(rec.get("created_at") or "")}
    except Exception:
        logger.warning("event_retro.finalize_failed", extra={"event_id": event_id}, exc_info=True)
        return {**snap, "finalized": False, "reason": "write_failed"}


def get_latest_retrospective(event_id: str, staff: dict[str, Any] | None = None) -> dict[str, Any]:
    """读最近一次定格快照;无快照则回退到实时聚合(标 source)。"""
    del staff
    if table_exists(_RETRO_TABLE):
        try:
            row = get_conn().execute(
                f"SELECT id, snapshot_json, completeness, finalized_by, created_at FROM {_RETRO_TABLE} "
                f"WHERE event_id = ? ORDER BY created_at DESC LIMIT 1",
                (str(event_id),),
            ).fetchone()
            if row:
                rec = dict(row)
                return {
                    "source": "finalized_snapshot",
                    "retrospective_id": rec.get("id"),
                    "finalized_at": str(rec.get("created_at") or ""),
                    "finalized_by": rec.get("finalized_by"),
                    "snapshot": _loads(rec.get("snapshot_json"), {}),
                }
        except Exception:
            logger.debug("event_retro.read_latest_failed", exc_info=True)
    return {"source": "live_aggregate", "snapshot": aggregate_event_retrospective(event_id, None)}
