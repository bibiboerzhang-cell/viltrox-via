"""
services/monitoring/runtime.py — lightweight runtime metrics
"""
from __future__ import annotations

import threading
import time
from collections import defaultdict, deque
from typing import Any

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
