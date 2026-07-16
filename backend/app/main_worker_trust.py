"""Worker-fleet trust probe used by the application health endpoint."""
from __future__ import annotations

from typing import Any, Mapping


def trust_worker_impl(namespace: Mapping[str, Any]) -> dict[str, object | None]:
    APP_GIT_SHA = namespace['APP_GIT_SHA']
    _worker_lane_from_name = namespace['_worker_lane_from_name']
    datetime = namespace['datetime']
    logger = namespace['logger']
    os = namespace['os']
    timezone = namespace['timezone']

    """Worker 真存活:优先读 vkpi_worker_heartbeat(worker 每轮 poll 都写,空闲也写),
    在线 = 心跳在 2 分钟内;表空/缺失才回退 MAX(updated_at) on apify_jobs(任务活动启发式)。
    W0/T5:此前只用 apify_jobs 活动 → 空闲 worker 误判离线;现与 system_health._worker_online 同源。"""
    result: dict[str, object | None] = {
        "worker_heartbeat": None,
        "worker_online": None,
        "worker_name": None,
        "worker_pid": None,
        "worker_sha": None,
        "worker_sha_source": "unavailable",
        "worker_boot_nonce_sha256": None,
        "worker_started_at": None,
        "worker_heartbeat_source": "unavailable",
        "worker_fleet": {
            "online_count": 0,
            "expected_count": None,
            "total_heartbeat_rows": 0,
            "all_worker_sha_aligned": False,
            "lane_coverage": [],
            "workers": [],
        },
    }
    try:
        from app.db.connection import get_conn, table_exists

        conn = get_conn()
        if not table_exists("vkpi_worker_heartbeat"):
            return result
        rows = conn.execute(
            """
            SELECT
                worker_name,
                last_heartbeat_at AS latest,
                pid,
                worker_git_sha,
                boot_nonce_sha256,
                started_at
            FROM vkpi_worker_heartbeat
            WHERE last_heartbeat_at IS NOT NULL
              AND worker_name NOT LIKE ?
            ORDER BY last_heartbeat_at DESC
            LIMIT 32
            """,
            ("redis-worker-%",),
        ).fetchall()
        row = rows[0] if rows else None
        latest_dt = row["latest"] if row is not None else None
        if latest_dt in (None, ""):
            return result
        if isinstance(latest_dt, str):
            latest_dt = datetime.fromisoformat(latest_dt.strip().replace("Z", "+00:00"))
        if latest_dt.tzinfo is None:
            latest_dt = latest_dt.replace(tzinfo=timezone.utc)
        result["worker_heartbeat"] = (
            latest_dt.astimezone(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")
        )
        age = (datetime.now(tz=timezone.utc) - latest_dt).total_seconds()
        started_at = row["started_at"] if row is not None else None
        if isinstance(started_at, datetime):
            if started_at.tzinfo is None:
                started_at = started_at.replace(tzinfo=timezone.utc)
            started_at = started_at.astimezone(timezone.utc).isoformat(timespec="seconds").replace(
                "+00:00", "Z"
            )
        elif started_at not in (None, ""):
            started_at = str(started_at)
        result.update(
            {
                "worker_online": bool(age <= 120),
                "worker_name": str(row["worker_name"] or "") or None,
                "worker_pid": int(row["pid"]) if row["pid"] is not None else None,
                "worker_sha": str(row["worker_git_sha"] or "").strip().lower() or None,
                "worker_sha_source": "db_heartbeat",
                "worker_boot_nonce_sha256": str(row["boot_nonce_sha256"] or "").strip().lower()
                or None,
                "worker_started_at": started_at,
                "worker_heartbeat_source": "db_heartbeat",
            }
        )
        now = datetime.now(tz=timezone.utc)
        fleet_workers: list[dict[str, object | None]] = []
        for raw in rows:
            heartbeat = raw["latest"]
            if isinstance(heartbeat, str):
                heartbeat = datetime.fromisoformat(heartbeat.strip().replace("Z", "+00:00"))
            if heartbeat.tzinfo is None:
                heartbeat = heartbeat.replace(tzinfo=timezone.utc)
            heartbeat = heartbeat.astimezone(timezone.utc)
            heartbeat_age = (now - heartbeat).total_seconds()
            raw_started = raw["started_at"]
            if isinstance(raw_started, datetime):
                if raw_started.tzinfo is None:
                    raw_started = raw_started.replace(tzinfo=timezone.utc)
                raw_started = raw_started.astimezone(timezone.utc).isoformat(timespec="seconds").replace(
                    "+00:00", "Z"
                )
            elif raw_started not in (None, ""):
                raw_started = str(raw_started)
            name = str(raw["worker_name"] or "")
            lane = _worker_lane_from_name(name)
            fleet_workers.append(
                {
                    "worker_name": name or None,
                    "pid": int(raw["pid"]) if raw["pid"] is not None else None,
                    "worker_sha": str(raw["worker_git_sha"] or "").strip().lower() or None,
                    "boot_nonce_sha256": str(raw["boot_nonce_sha256"] or "").strip().lower() or None,
                    "started_at": raw_started,
                    "heartbeat": heartbeat.isoformat(timespec="seconds").replace("+00:00", "Z"),
                    "heartbeat_age_seconds": round(heartbeat_age, 1),
                    "online": bool(-30 <= heartbeat_age <= 120),
                    "lane": lane,
                }
            )
        online_workers = [item for item in fleet_workers if item["online"] is True]
        expected_raw = str(os.getenv("APIFY_WORKER_EXPECTED_INSTANCES", "") or "").strip()
        expected_count = int(expected_raw) if expected_raw.isdigit() and int(expected_raw) > 0 else None
        names = [str(item.get("worker_name") or "") for item in online_workers]
        pids = [item.get("pid") for item in online_workers]
        lanes = sorted({str(item.get("lane") or "") for item in online_workers if item.get("lane")})
        result["worker_online"] = bool(online_workers)
        result["worker_fleet"] = {
            "online_count": len(online_workers),
            "expected_count": expected_count,
            "total_heartbeat_rows": len(fleet_workers),
            "unique_names": len(names) == len(set(names)),
            "unique_pids": len(pids) == len(set(pids)),
            "all_worker_sha_aligned": bool(
                online_workers
                and APP_GIT_SHA
                and all(item.get("worker_sha") == APP_GIT_SHA for item in online_workers)
            ),
            "all_heartbeats_fresh": bool(online_workers),
            "lane_coverage": lanes,
            "workers": fleet_workers,
        }
    except Exception:
        logger.debug("health: worker heartbeat read failed", exc_info=True)
    return result
