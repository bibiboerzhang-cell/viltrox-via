"""Scheduler-task registry (S1) — visibility + enable toggles ONLY.

诚实 by design:本模块只读 / 只翻 ``scheduler_tasks.enabled`` 开关,绝不执行任何调度。
- 没有调度循环、没有 worker 触发、没有 auto-run。``enabled=TRUE`` 仅是一个标记,
  后端不据此运行任何任务;执行链(调度器接线)留待后续单独接入。
- 写操作只触碰 ``scheduler_tasks``(UPDATE enabled + updated_at),零触
  viltrox_fit_score / rule_v0 / 任何既有表。
- 表缺失时(迁移 130 未跑)所有读返回空骨架,写抛 LookupError,绝不编造。
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from app.core.logging import get_logger
from app.db.connection import get_conn, table_exists


logger = get_logger(__name__)

_TABLE = "scheduler_tasks"
_RISK_LEVELS = ("low", "medium", "high")
# risk_level 文本无法直接 ORDER BY 出 low<medium<high,用显式权重排序。
_RISK_ORDER = {"low": 0, "medium": 1, "high": 2}


def _to_iso(value: Any) -> str | None:
    if value in (None, ""):
        return None
    if isinstance(value, datetime):
        dt = value if value.tzinfo else value.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")
    return str(value)


def _staff_label(staff: dict[str, Any] | None) -> str:
    """Best-effort owner label for the audit-ish ``owner`` stamp; never raises."""
    if not staff:
        return ""
    for key in ("email", "name", "username", "id", "staff_id", "user_id"):
        val = staff.get(key)
        if val not in (None, ""):
            return str(val)
    return ""


def _row_to_dict(row: Any) -> dict[str, Any]:
    if not row:
        return {}
    item = dict(row)
    return {
        "id": int(item.get("id") or 0),
        "task_key": str(item.get("task_key") or ""),
        "label": str(item.get("label") or ""),
        "enabled": bool(item.get("enabled")),
        "max_daily_runs": int(item.get("max_daily_runs") or 0),
        "max_daily_cost_cents": int(item.get("max_daily_cost_cents") or 0),
        "allowed_hours": str(item.get("allowed_hours") or ""),
        "owner": str(item.get("owner") or ""),
        "risk_level": str(item.get("risk_level") or "low"),
        "last_run_at": _to_iso(item.get("last_run_at")),
        "last_success_at": _to_iso(item.get("last_success_at")),
        "last_error": str(item.get("last_error") or ""),
        "created_at": _to_iso(item.get("created_at")),
        "updated_at": _to_iso(item.get("updated_at")),
    }


def list_scheduler_tasks(conn: Any | None = None) -> list[dict[str, Any]]:
    """All registry rows, ordered by risk_level (low→medium→high) then task_key.

    Pure SELECT. Returns [] when the table is absent (migration 130 not yet applied).
    """
    if not table_exists(_TABLE):
        return []
    if conn is None:
        conn = get_conn()
    try:
        rows = conn.execute(f"SELECT * FROM {_TABLE}").fetchall()
    except Exception:
        logger.debug("scheduler_registry: list read failed", exc_info=True)
        return []
    items = [_row_to_dict(r) for r in rows]
    items.sort(key=lambda r: (_RISK_ORDER.get(r["risk_level"], 99), r["task_key"]))
    return items


def set_scheduler_task_enabled(task_key: str, enabled: bool, staff: dict[str, Any] | None = None) -> dict[str, Any]:
    """Flip ``enabled`` (and ``updated_at`` / ``owner``) for one task_key. Returns the row.

    Touches ONLY ``scheduler_tasks``. Does NOT run, queue, or trigger anything — the flag
    is purely advisory until the execution link is wired in a later round.
    """
    key = str(task_key or "").strip()
    if not key:
        raise ValueError("task_key required")
    if not table_exists(_TABLE):
        raise LookupError("scheduler_tasks table not found (migration 130 not applied)")

    conn = get_conn()
    existing = conn.execute(
        f"SELECT * FROM {_TABLE} WHERE task_key=?", (key,)
    ).fetchone()
    if not existing:
        raise LookupError(f"scheduler task not found: {key}")

    now = datetime.now(tz=timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")
    owner = _staff_label(staff)
    conn.execute(
        f"UPDATE {_TABLE} SET enabled=?, owner=?, updated_at=? WHERE task_key=?",
        (bool(enabled), owner, now, key),
    )
    conn.commit()
    logger.info(
        "scheduler_registry: task %s enabled=%s by %s (flag only — no execution)",
        key, bool(enabled), owner or "?",
    )
    row = conn.execute(f"SELECT * FROM {_TABLE} WHERE task_key=?", (key,)).fetchone()
    return _row_to_dict(row)


def scheduler_status(conn: Any | None = None) -> dict[str, Any]:
    """Honest counts: {total, enabled, by_risk:{low,medium,high}}.

    Pure aggregate read. Zero counts (and available=False) when the table is absent.
    """
    base = {
        "total": 0,
        "enabled": 0,
        "by_risk": {level: 0 for level in _RISK_LEVELS},
    }
    if not table_exists(_TABLE):
        return {**base, "available": False}
    if conn is None:
        conn = get_conn()
    try:
        row = conn.execute(
            f"""
            SELECT
                COUNT(*) AS total,
                COUNT(*) FILTER (WHERE enabled) AS enabled,
                COUNT(*) FILTER (WHERE risk_level='low')    AS low,
                COUNT(*) FILTER (WHERE risk_level='medium') AS medium,
                COUNT(*) FILTER (WHERE risk_level='high')   AS high
            FROM {_TABLE}
            """
        ).fetchone()
    except Exception:
        logger.debug("scheduler_registry: status read failed", exc_info=True)
        return {**base, "available": False}

    item = dict(row) if row else {}
    return {
        "total": int(item.get("total") or 0),
        "enabled": int(item.get("enabled") or 0),
        "by_risk": {
            "low": int(item.get("low") or 0),
            "medium": int(item.get("medium") or 0),
            "high": int(item.get("high") or 0),
        },
        "available": True,
    }
