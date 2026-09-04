"""Scheduler-task registry — visibility and future-run enable controls.

诚实 by design:本模块本身只读 / 只翻 ``scheduler_tasks.enabled``，不会在当前请求内
立即执行任务；但已经接入 scheduler 的任务会在后续调度窗口读取这个开关并执行。
- 开启前必须查看每行的风险、预算和 ``paid_execution`` 提示；关闭也不冒充取消
  所有既有工作，具体任务仍须在 worker 执行边界重验自己的开关/授权。
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
_TASK_EXECUTION_METADATA: dict[str, dict[str, Any]] = {
    "kol_profile_incremental_refresh": {
        "execution_wired": True,
        "paid_execution": True,
        "enable_warning": (
            "启用后会在每日调度窗口排队最多 5 个维护刷新任务；"
            "这是纽约自然日的数据库硬上限。5 个维护任务不等于 5 次外部 provider 调用，"
            "每个任务可能发生多次第三方抓取/API 请求并产生费用。"
            "本次切换不会立即运行。"
        ),
    },
}


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
    task_key = str(item.get("task_key") or "")
    execution = _TASK_EXECUTION_METADATA.get(task_key, {})
    return {
        "id": int(item.get("id") or 0),
        "task_key": task_key,
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
        "last_status": str(item.get("last_status") or ""),
        "created_at": _to_iso(item.get("created_at")),
        "updated_at": _to_iso(item.get("updated_at")),
        "execution_wired": execution.get("execution_wired"),
        "paid_execution": execution.get("paid_execution"),
        "enable_warning": str(execution.get("enable_warning") or ""),
        "toggle_effect": "future_scheduler_runs_only",
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

    Touches ONLY ``scheduler_tasks`` and never runs a task inside this request.
    Already-wired tasks may execute in a later scheduler window when enabled.
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
        "scheduler_registry: task %s enabled=%s by %s "
        "(toggle persisted; no immediate execution; future schedule may run)",
        key, bool(enabled), owner or "?",
    )
    row = conn.execute(f"SELECT * FROM {_TABLE} WHERE task_key=?", (key,)).fetchone()
    return _row_to_dict(row)


_LAST_STATUS_VALUES = ("ok", "failed", "blocked")
_last_status_column_present: bool | None = None
_last_run_summary_column_present: bool | None = None


def _has_column(conn: Any, column: str, cache_name: str) -> bool:
    """某个后加的列是否存在(进程内缓存一次;迁移晚于代码上线时降级为不写该列)。"""
    cached = globals().get(cache_name)
    if cached is None:
        try:
            conn.execute(f"SELECT {column} FROM {_TABLE} LIMIT 1").fetchone()
            cached = True
        except Exception:
            logger.info("scheduler_registry: %s column absent (migration pending); recording without it", column)
            cached = False
        globals()[cache_name] = cached
    return bool(cached)


def _has_last_status_column(conn: Any) -> bool:
    """迁移 294 加的 last_status 列是否存在。"""
    return _has_column(conn, "last_status", "_last_status_column_present")


def _has_last_run_summary_column(conn: Any) -> bool:
    """迁移 302 加的 last_run_summary 列是否存在。"""
    return _has_column(conn, "last_run_summary", "_last_run_summary_column_present")


def record_run(task_key: str, *, ok: bool, error: str = "", status: str = "", note: str = "") -> None:
    """S2:cron 任务每次运行后回写 last_run_at / last_success_at / last_error / last_status(让"定时真跑"可见)。

    status 省略时按 ok 推 ok|failed;前置闸挡住没真跑传 ``blocked``(记 last_run_at + last_error,不记 success)。
    ``note`` 是这一轮的记账明细(如 ``scanned=100 advanced=87 terminal=11``),写进迁移 302 的
    ``last_run_summary``——``last_status`` 被约束成三值装不下明细,两者语义分开不互相污染。
    best-effort:只更新 scheduler_tasks 元数据,失败只 debug 不抛(绝不拖垮调度任务本体)。
    表缺/行缺 → 静默跳过(诚实)。零触业务表 / viltrox_fit_score。
    """
    key = str(task_key or "").strip()
    if not key or not table_exists(_TABLE):
        return
    final_status = str(status or "").strip().lower() or ("ok" if ok else "failed")
    if final_status not in _LAST_STATUS_VALUES:
        final_status = "ok" if ok else "failed"
    try:
        conn = get_conn()
        now = datetime.now(tz=timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")
        assignments = ["last_run_at=?", "last_error=?"]
        params: list[Any] = [now, "" if ok else str(error or "")[:500]]
        if ok:
            assignments.append("last_success_at=?")
            params.append(now)
        if _has_last_status_column(conn):
            assignments.append("last_status=?")
            params.append(final_status)
        # note 为空时不动这一列:避免一次没传明细就把上一轮的记账抹成空。
        if str(note or "").strip() and _has_last_run_summary_column(conn):
            assignments.append("last_run_summary=?")
            params.append(str(note).strip()[:500])
        params.append(key)
        conn.execute(f"UPDATE {_TABLE} SET {', '.join(assignments)} WHERE task_key=?", tuple(params))
        conn.commit()
    except Exception:
        logger.debug("scheduler_registry: record_run failed for %s", key, exc_info=True)


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
