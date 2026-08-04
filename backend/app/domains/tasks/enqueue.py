"""V-KPI async task facade over the shared job queue."""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from typing import Any

from app.core.logging import get_logger
from app.db.connection import get_conn, is_postgres_runtime
from app.domains.access import scope
from app.domains import channels


VKPI_TASK_SOURCE = "vkpi"
VKPI_OFFICIAL_CHANNEL_SYNC = "vkpi_official_channel_sync"
VKPI_VIDEO_CACHE = "vkpi_video_cache"
VKPI_KOL_POOL_ON_DEMAND_REFRESH = "vkpi_kol_pool_on_demand_refresh"
SUPPORTED_TASK_TYPES = {VKPI_OFFICIAL_CHANNEL_SYNC, VKPI_VIDEO_CACHE, VKPI_KOL_POOL_ON_DEMAND_REFRESH}
ACTIVE_STATUSES = {"queued", "retrying", "processing", "running"}
TERMINAL_RETRY_STATUSES = {"failed", "timeout", "cancelled"}
_SCHEMA_READY = False
logger = get_logger(__name__)


def _utcnow() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _json(value: Any) -> str:
    return json.dumps(value or {}, ensure_ascii=False, default=str)


def _loads(raw: Any, default: Any) -> Any:
    if raw in (None, "", b""):
        return default
    if isinstance(raw, (dict, list)):
        return raw
    try:
        return json.loads(str(raw))
    except Exception:
        return default


def _int(value: Any, default: int = 0) -> int:
    try:
        return int(value or default)
    except (TypeError, ValueError):
        return default


def _bool(value: Any, default: bool = False) -> bool:
    if value in (None, ""):
        return default
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "y", "on"}
    return bool(value)


def _table_columns(table_name: str) -> set[str]:
    try:
        rows = get_conn().execute(f"PRAGMA table_info({table_name})").fetchall()
    except Exception:
        return set()
    columns: set[str] = set()
    for row in rows:
        try:
            columns.add(str(row["name"]))
        except Exception:
            columns.add(str(row[1]))
    return columns


def _add_column_if_missing(table_name: str, column_name: str, column_def: str) -> None:
    if column_name in _table_columns(table_name):
        return
    get_conn().execute(f"ALTER TABLE {table_name} ADD COLUMN {column_name} {column_def}")


def ensure_vkpi_task_schema() -> None:
    """Create local runtime guards for SQLite development databases.

    PostgreSQL is migration-owned (009 + 056) and the application startup gate
    completes migrations before serving requests.  Running DDL from a GET/POST
    handler makes every fresh web process contend on schema locks, so production
    requests must never attempt to repair the schema themselves.
    """
    global _SCHEMA_READY
    if _SCHEMA_READY:
        return
    if is_postgres_runtime():
        _SCHEMA_READY = True
        return
    conn = get_conn()
    timestamp_type = "TEXT"
    _add_column_if_missing("job_execution_ledger", "priority", "INTEGER NOT NULL DEFAULT 5")
    _add_column_if_missing("job_execution_ledger", "lock_key", "TEXT DEFAULT ''")
    _add_column_if_missing("job_execution_ledger", "timeout_seconds", "INTEGER NOT NULL DEFAULT 300")
    _add_column_if_missing("job_execution_ledger", "heartbeat_at", timestamp_type)
    _add_column_if_missing("job_execution_ledger", "cancel_requested_at", timestamp_type)
    _add_column_if_missing("job_execution_ledger", "estimated_cost", "NUMERIC(10,4)")
    _add_column_if_missing("job_execution_ledger", "actual_cost", "NUMERIC(10,4)")
    try:
        conn.execute(
            """
            CREATE UNIQUE INDEX IF NOT EXISTS uniq_job_active_lock
              ON job_execution_ledger(lock_key)
              WHERE lock_key <> ''
                AND status IN ('queued', 'retrying', 'processing', 'running')
            """
        )
    except Exception as exc:
        # Some SQLite builds can be stricter about partial indexes in old local
        # DBs. Locking still works through the pre-insert lookup in queue.py.
        logger.warning("vkpi async task active-lock index creation skipped: %s", exc)
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS vkpi_async_task_items (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          task_id TEXT NOT NULL,
          item_key TEXT NOT NULL,
          status TEXT NOT NULL DEFAULT 'pending',
          attempt INTEGER NOT NULL DEFAULT 0,
          result_json TEXT NOT NULL DEFAULT '{}',
          error TEXT DEFAULT '',
          updated_at TEXT NOT NULL,
          UNIQUE(task_id, item_key)
        )
        """
    )
    conn.execute("CREATE INDEX IF NOT EXISTS idx_vkpi_task_items_task ON vkpi_async_task_items(task_id, status)")
    conn.commit()
    _SCHEMA_READY = True


def _staff_payload(staff: dict[str, Any] | None) -> dict[str, Any]:
    staff = dict(staff or {})
    return {
        "id": _int(staff.get("id") or staff.get("staff_id") or staff.get("user_id")),
        "staff_id": _int(staff.get("id") or staff.get("staff_id") or staff.get("user_id")),
        "user_id": _int(staff.get("user_id")),
        "role": str(staff.get("role") or ""),
        "is_owner": _int(staff.get("is_owner")),
        "email": str(staff.get("email") or ""),
    }


def _created_user_id(staff: dict[str, Any] | None) -> int:
    payload = _staff_payload(staff)
    return _int(payload.get("user_id")) or _int(payload.get("staff_id"))


def lock_key_for_task(task_type: str, params: dict[str, Any]) -> str:
    if task_type == VKPI_OFFICIAL_CHANNEL_SYNC:
        return f"{task_type}:channel:{_int(params.get('channel_id'))}"
    if task_type == "vkpi_official_weekly_deep_refresh":
        return f"{task_type}:channel:{_int(params.get('channel_id'))}"
    if task_type == "vkpi_video_cache":
        return f"{task_type}:media:{str(params.get('platform') or '').strip().lower()}:{str(params.get('video_id') or '').strip()}"
    if task_type == VKPI_KOL_POOL_ON_DEMAND_REFRESH:
        return f"{task_type}:kol_pool:{_int(params.get('kol_pool_id'))}"
    if task_type == "vkpi_apify_kol_scrape":
        raw = _json(params)
        return f"{task_type}:query:{hashlib.sha256(raw.encode('utf-8')).hexdigest()[:24]}"
    if task_type == "vkpi_product_recommendation_run":
        return f"{task_type}:launch:{_int(params.get('launch_id'))}"
    if task_type == "vkpi_ai_analysis":
        scope_id = str(params.get("scope_id") or params.get("target_scope") or "default").strip()
        analysis_type = str(params.get("analysis_type") or "generic").strip()
        return f"{task_type}:scope:{scope_id}:{analysis_type}"
    raise ValueError(f"unsupported V-KPI task type: {task_type}")


def _default_priority(task_type: str) -> int:
    if task_type == VKPI_OFFICIAL_CHANNEL_SYNC:
        return 1
    if task_type == VKPI_VIDEO_CACHE:
        return 3
    if task_type == VKPI_KOL_POOL_ON_DEMAND_REFRESH:
        return 4
    return 5


def _default_timeout(task_type: str) -> int:
    if task_type == VKPI_OFFICIAL_CHANNEL_SYNC:
        return 300
    if task_type == VKPI_VIDEO_CACHE:
        return 300
    if task_type == VKPI_KOL_POOL_ON_DEMAND_REFRESH:
        return 300
    return 300


async def enqueue_vkpi_task(
    queue: Any,
    task_type: str,
    params: dict[str, Any],
    *,
    staff: dict[str, Any] | None = None,
    priority: int | None = None,
    timeout_seconds: int | None = None,
) -> dict[str, Any]:
    ensure_vkpi_task_schema()
    if queue is None:
        raise RuntimeError("job queue unavailable")
    task_type = str(task_type or "").strip()
    if task_type not in SUPPORTED_TASK_TYPES:
        raise ValueError(f"unsupported V-KPI task type: {task_type}")
    params = dict(params or {})
    if task_type == VKPI_OFFICIAL_CHANNEL_SYNC:
        channel_id = _int(params.get("channel_id"))
        if not channel_id:
            raise ValueError("channel_id required")
        channels.get_channel(channel_id, staff=staff, write=True)
        params["channel_id"] = channel_id
        params["max_posts"] = max(1, min(1000, _int(params.get("max_posts"), 12)))
    if task_type == VKPI_VIDEO_CACHE:
        platform = str(params.get("platform") or "").strip().lower()
        video_id = str(params.get("video_id") or "").strip()
        source_url = str(params.get("source_url") or params.get("url") or "").strip()
        if not platform or not video_id:
            raise ValueError("platform and video_id required")
        if platform in {"instagram", "tiktok"} and not source_url:
            raise ValueError("source_url required")
        params["platform"] = platform
        params["video_id"] = video_id
        params["source_url"] = source_url
        params["force_refresh"] = _bool(params.get("force_refresh"))
        if _int(params.get("channel_id")):
            params["channel_id"] = _int(params.get("channel_id"))
    if task_type == VKPI_KOL_POOL_ON_DEMAND_REFRESH:
        from app.domains.kol import pool as kol_pool

        kol_pool_id = _int(params.get("kol_pool_id"))
        if not kol_pool_id:
            raise ValueError("kol_pool_id required")
        item = kol_pool.get_item(kol_pool_id)["item"]
        platform = str(item.get("platform") or "").strip().lower()
        if platform not in kol_pool.ENRICHABLE_PLATFORMS:
            raise ValueError(f"{platform or 'unknown'} is not enrichable")
        params["kol_pool_id"] = kol_pool_id
        params["platform"] = platform
        params["handle"] = str(item.get("handle") or "")
        params["max_posts"] = max(1, min(3, _int(params.get("max_posts"), 1)))
        params["reason"] = str(params.get("reason") or "stale_while_revalidate")[:80]
    user_id = _created_user_id(staff)
    staff_id = scope.actor_staff_id(staff)
    payload = {
        **params,
        "user_id": user_id,
        "staff_id": staff_id,
        "created_by_user_id": user_id,
        "source": VKPI_TASK_SOURCE,
        "task_class": task_type,
        "staff": _staff_payload(staff),
    }
    lock_key = lock_key_for_task(task_type, params)
    task_id = await queue.enqueue(
        task_type,
        payload,
        priority=max(1, int(priority if priority is not None else _default_priority(task_type))),
        lock_key=lock_key,
        timeout_seconds=max(1, int(timeout_seconds if timeout_seconds is not None else _default_timeout(task_type))),
    )
    return {"task_id": task_id, "status": "queued", "task_type": task_type, "lock_key": lock_key}


async def enqueue_official_channel_sync(
    queue: Any,
    channel_id: int,
    *,
    max_posts: int = 12,
    staff: dict[str, Any] | None = None,
    priority: int | None = None,
) -> dict[str, Any]:
    return await enqueue_vkpi_task(
        queue,
        VKPI_OFFICIAL_CHANNEL_SYNC,
        {"channel_id": int(channel_id), "max_posts": max_posts},
        staff=staff,
        priority=priority if priority is not None else 1,
        timeout_seconds=300,
    )


async def enqueue_video_cache(
    queue: Any,
    *,
    platform: str,
    video_id: str,
    source_url: str,
    channel_id: int | None = None,
    force_refresh: bool = False,
    staff: dict[str, Any] | None = None,
    priority: int | None = None,
) -> dict[str, Any]:
    params: dict[str, Any] = {
        "platform": platform,
        "video_id": video_id,
        "source_url": source_url,
        "force_refresh": force_refresh,
    }
    if channel_id:
        params["channel_id"] = int(channel_id)
    return await enqueue_vkpi_task(
        queue,
        VKPI_VIDEO_CACHE,
        params,
        staff=staff,
        priority=priority if priority is not None else 3,
        timeout_seconds=300,
    )


async def enqueue_kol_pool_on_demand_refresh(
    queue: Any,
    kol_pool_id: int,
    *,
    reason: str = "stale_while_revalidate",
    max_posts: int = 1,
    staff: dict[str, Any] | None = None,
    priority: int | None = None,
) -> dict[str, Any]:
    return await enqueue_vkpi_task(
        queue,
        VKPI_KOL_POOL_ON_DEMAND_REFRESH,
        {"kol_pool_id": int(kol_pool_id), "reason": reason, "max_posts": max_posts},
        staff=staff,
        priority=priority if priority is not None else 4,
        timeout_seconds=300,
    )


def _row_payload(row: Any) -> dict[str, Any]:
    return _loads(dict(row).get("payload_json") if row else None, {})


def _can_manage_all(staff: dict[str, Any] | None) -> bool:
    return scope.can_view_all(staff)


def _can_access_row(row: Any, staff: dict[str, Any] | None) -> bool:
    if row is None:
        return False
    if _can_manage_all(staff):
        return True
    actor_user_id = _created_user_id(staff)
    if actor_user_id and _int(dict(row).get("user_id")) == actor_user_id:
        return True
    payload = _row_payload(row)
    return bool(actor_user_id and _int(payload.get("created_by_user_id")) == actor_user_id)


def _task_row(task_id: str) -> Any | None:
    ensure_vkpi_task_schema()
    return get_conn().execute(
        "SELECT * FROM job_execution_ledger WHERE task_id=? AND job_type LIKE ?",
        (str(task_id), "vkpi_%"),
    ).fetchone()


def _items(task_id: str, limit: int = 100) -> list[dict[str, Any]]:
    rows = get_conn().execute(
        "SELECT * FROM vkpi_async_task_items WHERE task_id=? ORDER BY id ASC LIMIT ?",
        (str(task_id), max(1, min(500, int(limit or 100)))),
    ).fetchall()
    items: list[dict[str, Any]] = []
    for row in rows:
        item = dict(row)
        item["result"] = _loads(item.get("result_json"), {})
        items.append(item)
    return items


def _progress_from_items(items: list[dict[str, Any]]) -> int:
    if not items:
        return 0
    terminal = sum(1 for item in items if str(item.get("status") or "") in {"done", "failed", "skipped", "cancelled"})
    return int(round((terminal / max(1, len(items))) * 100))


_COMPACT_RESULT_KEYS = (
    "summary",
    "message",
    "cached_url",
    "skip_reason",
    "reason",
    "count",
    "total",
)


def serialize_task(
    row: Any,
    *,
    include_items: bool = False,
    include_payload: bool = True,
    compact_result: bool = False,
) -> dict[str, Any]:
    data = dict(row)
    extra = _loads(data.get("extra_json"), {})
    result = _loads(data.get("result_json"), {})
    if compact_result:
        result = (
            {key: result[key] for key in _COMPACT_RESULT_KEYS if key in result}
            if isinstance(result, dict)
            else {}
        )
    items = _items(str(data.get("task_id"))) if include_items else []
    progress_pct = _int(extra.get("progress_pct"))
    if not progress_pct and items:
        progress_pct = _progress_from_items(items)
    serialized: dict[str, Any] = {
        "task_id": data.get("task_id"),
        "task_type": data.get("job_type"),
        "status": data.get("status"),
        "progress_pct": progress_pct,
        "progress_text": extra.get("progress_text") or data.get("summary") or data.get("stage") or "",
        "result": result,
        "error": data.get("error_message") or "",
        "created_at": data.get("created_at"),
        "started_at": data.get("started_at"),
        "finished_at": data.get("finished_at"),
    }
    if not compact_result:
        serialized.update(
            {
                "items": items,
                "estimated_cost": data.get("estimated_cost"),
                "actual_cost": data.get("actual_cost"),
                "cancel_requested_at": data.get("cancel_requested_at"),
            }
        )
    if include_payload:
        serialized["payload"] = _loads(data.get("payload_json"), {})
    return serialized


def get_task(task_id: str, *, staff: dict[str, Any] | None = None) -> dict[str, Any]:
    row = _task_row(task_id)
    if not row or not _can_access_row(row, staff):
        raise LookupError("task not found")
    return serialize_task(row, include_items=True)


def list_tasks(
    *,
    status: str = "",
    task_type: str = "",
    user_id: int | None = None,
    limit: int = 50,
    compact: bool = False,
    staff: dict[str, Any] | None = None,
) -> dict[str, Any]:
    ensure_vkpi_task_schema()
    where = ["job_type LIKE ?"]
    params: list[Any] = ["vkpi_%"]
    if status:
        where.append("status=?")
        params.append(status)
    if task_type:
        where.append("job_type=?")
        params.append(task_type)
    if _can_manage_all(staff):
        if user_id:
            where.append("user_id=?")
            params.append(int(user_id))
    else:
        where.append("user_id=?")
        params.append(_created_user_id(staff))
    select_fields = "*"
    if compact:
        select_fields = """
            task_id, job_type, status, summary, stage, error_message,
            extra_json, result_json, created_at, started_at, finished_at
        """
    rows = get_conn().execute(
        f"SELECT {select_fields} FROM job_execution_ledger WHERE {' AND '.join(where)} ORDER BY id DESC LIMIT ?",
        (*params, max(1, min(200, int(limit or 50)))),
    ).fetchall()
    return {
        "tasks": [
            serialize_task(
                row,
                include_items=False,
                include_payload=not compact,
                compact_result=compact,
            )
            for row in rows
        ]
    }


def cancel_task(task_id: str, *, staff: dict[str, Any] | None = None) -> dict[str, Any]:
    row = _task_row(task_id)
    if not row or not _can_access_row(row, staff):
        raise LookupError("task not found")
    status = str(dict(row).get("status") or "").lower()
    if status not in ACTIVE_STATUSES:
        return serialize_task(row, include_items=True)
    now = _utcnow()
    get_conn().execute("UPDATE job_execution_ledger SET cancel_requested_at=?, updated_at=? WHERE task_id=?", (now, now, str(task_id)))
    get_conn().commit()
    return get_task(task_id, staff=staff)


async def retry_task(queue: Any, task_id: str, *, staff: dict[str, Any] | None = None) -> dict[str, Any]:
    row = _task_row(task_id)
    if not row or not _can_access_row(row, staff):
        raise LookupError("task not found")
    data = dict(row)
    status = str(data.get("status") or "").lower()
    if status not in TERMINAL_RETRY_STATUSES:
        raise ValueError("task is not retryable")
    payload = _row_payload(row)
    task_type = str(data.get("job_type") or "")
    if task_type == VKPI_OFFICIAL_CHANNEL_SYNC:
        return await enqueue_official_channel_sync(
            queue,
            _int(payload.get("channel_id")),
            max_posts=_int(payload.get("max_posts"), 12),
            staff=staff,
        )
    if task_type == VKPI_VIDEO_CACHE:
        return await enqueue_video_cache(
            queue,
            platform=str(payload.get("platform") or ""),
            video_id=str(payload.get("video_id") or ""),
            source_url=str(payload.get("source_url") or ""),
            channel_id=_int(payload.get("channel_id")) or None,
            force_refresh=_bool(payload.get("force_refresh")),
            staff=staff,
        )
    if task_type == VKPI_KOL_POOL_ON_DEMAND_REFRESH:
        return await enqueue_kol_pool_on_demand_refresh(
            queue,
            _int(payload.get("kol_pool_id")),
            reason=str(payload.get("reason") or "retry"),
            max_posts=_int(payload.get("max_posts"), 1),
            staff=staff,
        )
    raise ValueError(f"unsupported retry task type: {task_type}")


def upsert_task_item(task_id: str, item_key: str, *, status: str, result: dict[str, Any] | None = None, error: str = "") -> None:
    ensure_vkpi_task_schema()
    now = _utcnow()
    conn = get_conn()
    initial_attempt = 0 if str(status or "").lower() == "pending" else 1
    conn.execute(
        """
        INSERT INTO vkpi_async_task_items (task_id, item_key, status, attempt, result_json, error, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(task_id, item_key) DO UPDATE SET
            status=excluded.status,
            attempt=vkpi_async_task_items.attempt + CASE WHEN excluded.status='running' THEN 1 ELSE 0 END,
            result_json=excluded.result_json,
            error=excluded.error,
            updated_at=excluded.updated_at
        """,
        (str(task_id), str(item_key), status, initial_attempt, _json(result or {}), str(error or ""), now),
    )
    conn.commit()


def task_cancel_requested(task_id: str) -> bool:
    ensure_vkpi_task_schema()
    row = get_conn().execute("SELECT cancel_requested_at FROM job_execution_ledger WHERE task_id=?", (str(task_id),)).fetchone()
    return bool(row and dict(row).get("cancel_requested_at"))
