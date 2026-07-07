"""
services/monitoring/runtime.py — lightweight runtime metrics
"""
from __future__ import annotations

import json
import threading
import time
from collections import defaultdict, deque
from datetime import datetime, timedelta, timezone
from typing import Any

from app.core.logging import get_logger

logger = get_logger(__name__)

_STARTED_AT = time.time()
_LOCK = threading.RLock()
_RECENT_LATENCIES_MS: deque[float] = deque(maxlen=2000)
_ROUTE_STATS: dict[str, dict[str, float]] = defaultdict(
    lambda: {
        "count": 0,
        "errors": 0,
        "total_ms": 0.0,
        "max_ms": 0.0,
    }
)
_TOTAL_REQUESTS = 0
_TOTAL_ERRORS = 0
_BACKGROUND_STATS: dict[str, dict[str, Any]] = defaultdict(
    lambda: {
        "runs": 0,
        "errors": 0,
        "last_value": 0.0,
        "last_ok": 1,
        "last_at": 0.0,
        "last_fields": {},
    }
)


def record_request_metric(path: str, method: str, status_code: int, duration_ms: float) -> None:
    global _TOTAL_REQUESTS, _TOTAL_ERRORS
    route_key = f"{method.upper()} {path}"
    with _LOCK:
        _TOTAL_REQUESTS += 1
        if int(status_code) >= 500:
            _TOTAL_ERRORS += 1
        _RECENT_LATENCIES_MS.append(float(duration_ms))
        bucket = _ROUTE_STATS[route_key]
        bucket["count"] += 1
        if int(status_code) >= 500:
            bucket["errors"] += 1
        bucket["total_ms"] += float(duration_ms)
        bucket["max_ms"] = max(float(bucket["max_ms"]), float(duration_ms))


def record_background_metric(name: str, value: float = 0.0, *, ok: bool = True, **fields: Any) -> None:
    metric_name = str(name or "").strip()
    if not metric_name:
        return
    with _LOCK:
        bucket = _BACKGROUND_STATS[metric_name]
        bucket["runs"] += 1
        if not ok:
            bucket["errors"] += 1
        bucket["last_value"] = float(value or 0.0)
        bucket["last_ok"] = 1 if ok else 0
        bucket["last_at"] = time.time()
        bucket["last_fields"] = dict(fields or {})


def _percentile(values: list[float], ratio: float) -> float:
    if not values:
        return 0.0
    idx = max(0, min(len(values) - 1, int(round((len(values) - 1) * ratio))))
    return float(values[idx])


def get_request_metrics_snapshot() -> dict[str, Any]:
    with _LOCK:
        latencies = sorted(float(v) for v in _RECENT_LATENCIES_MS)
        hottest = sorted(
            (
                {
                    "route": route,
                    "count": int(stats["count"]),
                    "errors": int(stats["errors"]),
                    "avg_ms": round(float(stats["total_ms"]) / max(1, int(stats["count"])), 2),
                    "max_ms": round(float(stats["max_ms"]), 2),
                }
                for route, stats in _ROUTE_STATS.items()
            ),
            key=lambda item: (-item["count"], -item["avg_ms"], item["route"]),
        )[:8]
        background = sorted(
            (
                {
                    "name": name,
                    "runs": int(stats["runs"]),
                    "errors": int(stats["errors"]),
                    "last_value": float(stats["last_value"]),
                    "last_ok": bool(stats["last_ok"]),
                    "last_at": round(float(stats["last_at"]), 3),
                    "last_fields": dict(stats["last_fields"] or {}),
                }
                for name, stats in _BACKGROUND_STATS.items()
            ),
            key=lambda item: item["name"],
        )
        return {
            "uptime_sec": round(time.time() - _STARTED_AT, 1),
            "total_requests": int(_TOTAL_REQUESTS),
            "total_errors": int(_TOTAL_ERRORS),
            "recent_window": {
                "sample_size": len(latencies),
                "p50_ms": round(_percentile(latencies, 0.50), 2),
                "p95_ms": round(_percentile(latencies, 0.95), 2),
                "p99_ms": round(_percentile(latencies, 0.99), 2),
                "max_ms": round(latencies[-1], 2) if latencies else 0.0,
            },
            "hottest_routes": hottest,
            "background_metrics": background,
        }


# ── 快照落库(可观测性)──────────────────────────────────────────────
# 上面的计数器是进程内的,重启即失。后台任务每 5 分钟调用 snapshot_request_metrics()
# 把当前快照写 persistent_cache(latest 键 + 当日键,health_sentinel 同款模式,零新表),
# 重启后仍可经 ops 端点读到最后一次运行健康度。
_METRICS_LATEST_KEY = "runtime_metrics:latest"
_METRICS_DAY_KEY_PREFIX = "runtime_metrics:day:"
_METRICS_HISTORY_DAYS = 7


def snapshot_request_metrics() -> dict[str, Any]:
    """Persist the current in-process request-metrics snapshot to persistent_cache.

    Read-only aggregate + a two-row cache upsert (latest key + per-UTC-day key).
    Best-effort observability: logs and returns a status dict, never raises.
    """
    from app.db.connection import get_conn, table_exists

    payload = get_request_metrics_snapshot()
    now = datetime.now(timezone.utc)
    payload["captured_at"] = now.strftime("%Y-%m-%dT%H:%M:%SZ")
    if not table_exists("persistent_cache"):
        logger.warning("runtime_metrics: persistent_cache table missing, snapshot not persisted")
        return {"persisted": False, "reason": "persistent_cache_missing"}
    try:
        conn = get_conn()
        value_json = json.dumps(payload, ensure_ascii=False, default=str)
        created = now.strftime("%Y-%m-%dT%H:%M:%SZ")
        expires = (now + timedelta(days=_METRICS_HISTORY_DAYS + 1)).strftime("%Y-%m-%dT%H:%M:%SZ")
        day_key = _METRICS_DAY_KEY_PREFIX + now.strftime("%Y-%m-%d")
        for cache_key in (_METRICS_LATEST_KEY, day_key):
            conn.execute("DELETE FROM persistent_cache WHERE cache_key=?", (cache_key,))
            conn.execute(
                "INSERT INTO persistent_cache (cache_key, value_json, expires_at, created_at) VALUES (?,?,?,?)",
                (cache_key, value_json, expires, created),
            )
        conn.commit()
    except Exception:
        logger.warning("runtime_metrics: snapshot persist failed", exc_info=True)
        return {"persisted": False, "reason": "persist_failed"}
    return {"persisted": True, "captured_at": payload["captured_at"]}


def get_persisted_request_metrics() -> dict[str, Any]:
    """Read the last persisted request-metrics snapshot (survives restarts).

    Honest ``available=False`` when never captured / table absent; never raises.
    """
    from app.db.connection import get_conn, table_exists

    if not table_exists("persistent_cache"):
        return {"available": False, "reason": "persistent_cache_missing"}
    try:
        row = get_conn().execute(
            "SELECT value_json FROM persistent_cache WHERE cache_key=?", (_METRICS_LATEST_KEY,)
        ).fetchone()
    except Exception:
        logger.warning("runtime_metrics: persisted read failed", exc_info=True)
        return {"available": False, "reason": "read_failed"}
    if not row:
        return {"available": False, "reason": "never_captured"}
    try:
        payload = json.loads(dict(row).get("value_json") or "{}")
    except (TypeError, ValueError):
        return {"available": False, "reason": "corrupt_payload"}
    if not isinstance(payload, dict):
        return {"available": False, "reason": "empty_payload"}
    return {"available": True, **payload}


def job_runtime_metrics_snapshot() -> dict[str, Any]:
    """Scheduler entrypoint (every 5 min): persist the runtime metrics snapshot.

    Sync + self-contained so APScheduler runs it in its threadpool off the event
    loop; logs its own outcome and never propagates.
    """
    result = snapshot_request_metrics()
    logger.info("runtime_metrics.snapshot", extra={"persisted": result.get("persisted")})
    return result
