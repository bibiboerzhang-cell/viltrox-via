"""Read-only health projection for the dedicated Redis worker fleet."""
from __future__ import annotations

import os
from datetime import datetime, timezone
from typing import Any

from app.db.connection import get_conn, table_exists


def _utc(value: Any) -> datetime | None:
    if value in (None, ""):
        return None
    if isinstance(value, datetime):
        parsed = value
    else:
        try:
            parsed = datetime.fromisoformat(str(value).strip().replace("Z", "+00:00"))
        except ValueError:
            return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def redis_worker_fleet_health(app_git_sha: str = "") -> dict[str, Any]:
    expected_raw = str(os.getenv("VKPI_REDIS_WORKER_EXPECTED_INSTANCES", "1") or "1").strip()
    expected = int(expected_raw) if expected_raw.isdigit() and int(expected_raw) > 0 else 1
    empty = {
        "online": False,
        "online_count": 0,
        "expected_count": expected,
        "total_heartbeat_rows": 0,
        "unique_names": True,
        "unique_pids": True,
        "all_worker_sha_aligned": False,
        "all_heartbeats_fresh": False,
        "all_redis_ready": False,
        "workers": [],
    }
    if not table_exists("vkpi_worker_heartbeat"):
        return empty
    rows = get_conn().execute(
        """
        SELECT worker_name, last_heartbeat_at, pid, worker_git_sha,
               boot_nonce_sha256, started_at, redis_ready,
               redis_readiness_at, redis_stream_key, redis_group_name,
               redis_consumer_count, redis_ready_sequence,
               redis_heartbeat_interval_seconds, redis_readiness_error_code
        FROM vkpi_worker_heartbeat
        WHERE last_heartbeat_at IS NOT NULL
          AND worker_name LIKE ?
        ORDER BY last_heartbeat_at DESC
        LIMIT 32
        """,
        ("redis-worker-%",),
    ).fetchall()
    now = datetime.now(timezone.utc)
    workers: list[dict[str, Any]] = []
    for row in rows:
        heartbeat = _utc(row["last_heartbeat_at"])
        started = _utc(row["started_at"])
        readiness_at = _utc(row["redis_readiness_at"])
        age = (now - heartbeat).total_seconds() if heartbeat else None
        readiness_age = (now - readiness_at).total_seconds() if readiness_at else None
        redis_ready = bool(row["redis_ready"])
        ready_fresh = bool(readiness_age is not None and -30 <= readiness_age <= 120)
        heartbeat_fresh = bool(age is not None and -30 <= age <= 120)
        workers.append(
            {
                "worker_name": str(row["worker_name"] or "") or None,
                "pid": int(row["pid"]) if row["pid"] is not None else None,
                "worker_sha": str(row["worker_git_sha"] or "").strip().lower() or None,
                "boot_nonce_sha256": str(row["boot_nonce_sha256"] or "").strip().lower() or None,
                "started_at": started.isoformat(timespec="seconds").replace("+00:00", "Z") if started else None,
                "heartbeat": heartbeat.isoformat(timespec="seconds").replace("+00:00", "Z") if heartbeat else None,
                "heartbeat_age_seconds": round(age, 1) if age is not None else None,
                "redis_ready": redis_ready,
                "redis_readiness_at": readiness_at.isoformat(timespec="seconds").replace("+00:00", "Z") if readiness_at else None,
                "redis_readiness_age_seconds": round(readiness_age, 1) if readiness_age is not None else None,
                "redis_stream_key": str(row["redis_stream_key"] or "") or None,
                "redis_group_name": str(row["redis_group_name"] or "") or None,
                "redis_consumer_count": int(row["redis_consumer_count"] or 0),
                "redis_ready_sequence": int(row["redis_ready_sequence"] or 0),
                "redis_heartbeat_interval_seconds": int(row["redis_heartbeat_interval_seconds"] or 0),
                "redis_readiness_error_code": str(row["redis_readiness_error_code"] or "") or None,
                "online": bool(heartbeat_fresh and redis_ready and ready_fresh),
            }
        )
    online = [worker for worker in workers if worker["online"] is True]
    names = [str(worker.get("worker_name") or "") for worker in online]
    pids = [worker.get("pid") for worker in online]
    release_sha = str(app_git_sha or "").strip().lower()
    aligned = bool(
        online
        and release_sha
        and all(worker.get("worker_sha") == release_sha for worker in online)
    )
    return {
        "online": len(online) == expected and aligned,
        "online_count": len(online),
        "expected_count": expected,
        "total_heartbeat_rows": len(workers),
        "unique_names": len(names) == len(set(names)),
        "unique_pids": len(pids) == len(set(pids)),
        "all_worker_sha_aligned": aligned,
        "all_heartbeats_fresh": bool(online) and all(worker["online"] for worker in online),
        "all_redis_ready": bool(online) and all(worker["redis_ready"] for worker in online),
        "workers": workers,
    }


__all__ = ["redis_worker_fleet_health"]
