"""Read-only system-health projection for the P5 dashboard health bar.

Honest by design: every field reflects a real table read. When a source table
or column is absent the field returns ``null`` with a ``待接入`` label rather
than a fabricated number. Each query is one light bounded read (grouped counts,
a single MAX, a same-day SUM) — no heavy scans, no writes, no worker touch.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from app.core.logging import get_logger
from app.db.connection import get_conn, table_exists


logger = get_logger(__name__)

# Worker liveness:首选真实心跳 vkpi_worker_heartbeat —— apify_jobs_worker 每轮 poll(含空闲)
# UPSERT last_heartbeat_at,2 分钟内有心跳即在线(空闲 worker 也算在线,不再误判离线)。
# 心跳表缺失/为空时回退到旧的 apify_jobs.updated_at 启发式(5 分钟内任意行被写动 → 视为在线)。
_WORKER_HEARTBEAT_WINDOW_MIN = 2
_WORKER_ONLINE_WINDOW_MIN = 5

# 队列状态归一:基础迁移 095 只约束 queued/running/done/failed,097 加 blocked。
# running 桶吸收同义 processing/retrying,与 tasks/queue_view 的口径一致。
_QUEUE_STATUS_ORDER = ["done", "failed", "blocked", "running", "queued"]
_RUNNING_ALIASES = {"running", "processing", "retrying"}


def _now_iso() -> str:
    return datetime.now(tz=timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _to_iso(value: Any) -> str | None:
    if value in (None, ""):
        return None
    if isinstance(value, datetime):
        dt = value if value.tzinfo else value.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")
    return str(value)


def _parse_dt(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    if isinstance(value, str) and value.strip():
        raw = value.strip().replace("Z", "+00:00")
        try:
            parsed = datetime.fromisoformat(raw)
        except ValueError:
            return None
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
    return None


def _queue_health(conn: Any) -> dict[str, Any]:
    """apify_jobs grouped by status (one bounded GROUP BY)."""
    counts: dict[str, int] = {key: 0 for key in _QUEUE_STATUS_ORDER}
    if not table_exists("apify_jobs"):
        return {
            "available": False,
            "label": "待接入",
            "by_status": None,
            "done": None,
            "failed": None,
            "blocked": None,
            "running": None,
            "queued": None,
            "total": None,
        }
    try:
        rows = conn.execute(
            "SELECT status, COUNT(*) AS n FROM apify_jobs GROUP BY status"
        ).fetchall()
    except Exception:
        logger.debug("system-health queue read failed", exc_info=True)
        return {
            "available": False,
            "label": "待接入",
            "by_status": None,
            "done": None,
            "failed": None,
            "blocked": None,
            "running": None,
            "queued": None,
            "total": None,
        }
    by_status: dict[str, int] = {}
    total = 0
    for row in rows:
        raw = str((row["status"] if row is not None else "") or "").strip().lower()
        n = int(row["n"] or 0)
        total += n
        by_status[raw] = by_status.get(raw, 0) + n
        bucket = "running" if raw in _RUNNING_ALIASES else raw
        if bucket in counts:
            counts[bucket] += n
    return {
        "available": True,
        "by_status": by_status,
        "done": counts["done"],
        "failed": counts["failed"],
        "blocked": counts["blocked"],
        "running": counts["running"],
        "queued": counts["queued"],
        "total": total,
    }


def _blocked_reasons(conn: Any, limit: int = 3) -> dict[str, Any]:
    """Top error families of currently-blocked apify_jobs (bounded GROUP BY)."""
    if not table_exists("apify_jobs"):
        return {"available": False, "label": "待接入", "items": None}
    try:
        rows = conn.execute(
            """
            SELECT COALESCE(NULLIF(last_error_category, ''), '未分类') AS reason,
                   COUNT(*) AS n
            FROM apify_jobs
            WHERE status = 'blocked'
            GROUP BY COALESCE(NULLIF(last_error_category, ''), '未分类')
            ORDER BY n DESC
            LIMIT ?
            """,
            (max(1, min(int(limit or 3), 10)),),
        ).fetchall()
    except Exception:
        logger.debug("system-health blocked-reasons read failed", exc_info=True)
        return {"available": False, "label": "待接入", "items": None}
    items = [
        {"reason": str(row["reason"] or "未分类"), "count": int(row["n"] or 0)}
        for row in rows
    ]
    return {"available": True, "items": items}


def _llm_cost_today(conn: Any) -> dict[str, Any]:
    """SUM(cost_cents) of today's registered LLM calls (same-day filtered SUM).

    诚实化:cost 列在本库为 cost_cents(分,migration 045)。表/列缺失时回 null+待接入。
    """
    if not table_exists("vkpi_llm_calls"):
        return {
            "available": False,
            "label": "待接入",
            "cost_cents": None,
            "cost_usd": None,
            "call_count": None,
        }
    try:
        row = conn.execute(
            """
            SELECT COALESCE(SUM(cost_cents), 0) AS cents,
                   COUNT(*) AS n
            FROM vkpi_llm_calls
            WHERE created_at::date = CURRENT_DATE
            """
        ).fetchone()
    except Exception:
        logger.debug("system-health llm-cost read failed", exc_info=True)
        return {
            "available": False,
            "label": "待接入",
            "cost_cents": None,
            "cost_usd": None,
            "call_count": None,
        }
    cents = int((row["cents"] if row is not None else 0) or 0)
    return {
        "available": True,
        "cost_cents": cents,
        "cost_usd": round(cents / 100.0, 2),
        "call_count": int((row["n"] if row is not None else 0) or 0),
    }


def _data_freshness(conn: Any) -> dict[str, Any]:
    """MAX(updated_at) across a key table (KOL pool) — single aggregate read."""
    if not table_exists("vkpi_kol_pool"):
        return {
            "available": False,
            "label": "待接入",
            "source_table": "vkpi_kol_pool",
            "max_updated_at": None,
            "age_seconds": None,
        }
    try:
        row = conn.execute(
            "SELECT MAX(updated_at) AS latest FROM vkpi_kol_pool"
        ).fetchone()
    except Exception:
        logger.debug("system-health data-freshness read failed", exc_info=True)
        return {
            "available": False,
            "label": "待接入",
            "source_table": "vkpi_kol_pool",
            "max_updated_at": None,
            "age_seconds": None,
        }
    latest_raw = row["latest"] if row is not None else None
    latest_dt = _parse_dt(latest_raw)
    age_seconds: int | None = None
    if latest_dt is not None:
        age_seconds = max(0, int((datetime.now(tz=timezone.utc) - latest_dt).total_seconds()))
    return {
        "available": True,
        "source_table": "vkpi_kol_pool",
        "max_updated_at": _to_iso(latest_raw),
        "age_seconds": age_seconds,
    }


def _worker_online_from_heartbeat(conn: Any) -> dict[str, Any] | None:
    """Real liveness via vkpi_worker_heartbeat (online if heartbeat within ~2 min).

    Returns ``None`` when the table is absent or empty so the caller can fall back
    to the legacy apify_jobs heuristic. Idle workers still heartbeat every poll, so
    an idle-but-alive worker reads as online here.
    """
    if not table_exists("vkpi_worker_heartbeat"):
        return None
    try:
        row = conn.execute(
            "SELECT MAX(last_heartbeat_at) AS latest FROM vkpi_worker_heartbeat"
        ).fetchone()
    except Exception:
        logger.debug("system-health worker-heartbeat read failed", exc_info=True)
        return None
    latest_raw = row["latest"] if row is not None else None
    if latest_raw in (None, ""):
        # 表存在但无任何心跳行 → 回退到 apify_jobs 启发式。
        return None
    latest_dt = _parse_dt(latest_raw)
    online = False
    if latest_dt is not None:
        age = (datetime.now(tz=timezone.utc) - latest_dt).total_seconds()
        online = age <= _WORKER_HEARTBEAT_WINDOW_MIN * 60
    return {
        "available": True,
        "online": bool(online),
        "last_heartbeat_at": _to_iso(latest_raw),
        "window_minutes": _WORKER_HEARTBEAT_WINDOW_MIN,
        "method": "vkpi_worker_heartbeat_within_window",
    }


def _worker_online(conn: Any) -> dict[str, Any]:
    """Liveness:首选 vkpi_worker_heartbeat 真实心跳;表缺失/为空时回退 apify_jobs 启发式。"""
    heartbeat = _worker_online_from_heartbeat(conn)
    if heartbeat is not None:
        return heartbeat
    if not table_exists("apify_jobs"):
        return {
            "available": False,
            "label": "待接入",
            "online": None,
            "last_heartbeat_at": None,
            "window_minutes": _WORKER_ONLINE_WINDOW_MIN,
        }
    try:
        row = conn.execute(
            "SELECT MAX(updated_at) AS latest FROM apify_jobs"
        ).fetchone()
    except Exception:
        logger.debug("system-health worker-online read failed", exc_info=True)
        return {
            "available": False,
            "label": "待接入",
            "online": None,
            "last_heartbeat_at": None,
            "window_minutes": _WORKER_ONLINE_WINDOW_MIN,
        }
    latest_raw = row["latest"] if row is not None else None
    latest_dt = _parse_dt(latest_raw)
    online = False
    if latest_dt is not None:
        age = (datetime.now(tz=timezone.utc) - latest_dt).total_seconds()
        online = age <= _WORKER_ONLINE_WINDOW_MIN * 60
    return {
        "available": True,
        "online": bool(online),
        "last_heartbeat_at": _to_iso(latest_raw),
        "window_minutes": _WORKER_ONLINE_WINDOW_MIN,
        "method": "apify_jobs_updated_at_within_window_fallback",
    }


def build_dashboard_system_health() -> dict[str, Any]:
    """Assemble the read-only system-health payload for the dashboard bar.

    One light read per source. Never fabricates: missing tables/columns surface
    as ``available: false`` with a ``待接入`` label so the frontend shows the
    honest placeholder rather than a fake value.
    """
    conn = get_conn()
    queue = _queue_health(conn)
    blocked = _blocked_reasons(conn)
    llm_cost = _llm_cost_today(conn)
    freshness = _data_freshness(conn)
    worker = _worker_online(conn)
    return {
        "queue": queue,
        "blocked_reasons": blocked,
        "llm_cost_today": llm_cost,
        "data_freshness": freshness,
        "worker_online": worker,
        "generated_at": _now_iso(),
        "source": "apify_jobs + vkpi_llm_calls + vkpi_kol_pool",
        "method": "system_health_bar_v0_read_only",
    }
