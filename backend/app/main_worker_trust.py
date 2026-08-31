"""Worker-fleet trust probe used by the application health endpoint."""
from __future__ import annotations

from typing import Any, Mapping


def _empty_worker_trust() -> dict[str, object | None]:
    return {
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


def _heartbeat_datetime(value: Any, datetime: Any, timezone: Any) -> Any:
    if isinstance(value, str):
        value = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _started_at_value(value: Any, datetime: Any, timezone: Any) -> Any:
    if isinstance(value, datetime):
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc).isoformat(timespec="seconds").replace(
            "+00:00", "Z"
        )
    if value not in (None, ""):
        return str(value)
    return value


def _fleet_worker(
    raw: Any,
    *,
    now: Any,
    datetime: Any,
    timezone: Any,
    worker_lane_from_name: Any,
) -> dict[str, object | None]:
    heartbeat = _heartbeat_datetime(raw["latest"], datetime, timezone)
    heartbeat_age = (now - heartbeat).total_seconds()
    name = str(raw["worker_name"] or "")
    return {
        "worker_name": name or None,
        "pid": int(raw["pid"]) if raw["pid"] is not None else None,
        "worker_sha": str(raw["worker_git_sha"] or "").strip().lower() or None,
        "boot_nonce_sha256": str(raw["boot_nonce_sha256"] or "").strip().lower() or None,
        "started_at": _started_at_value(raw["started_at"], datetime, timezone),
        "heartbeat": heartbeat.isoformat(timespec="seconds").replace("+00:00", "Z"),
        "heartbeat_age_seconds": round(heartbeat_age, 1),
        "online": bool(-30 <= heartbeat_age <= 120),
        "lane": worker_lane_from_name(name),
    }


def _fleet_projection(
    workers: list[dict[str, object | None]],
    *,
    expected_count: int | None,
    app_git_sha: str,
) -> dict[str, object]:
    online = [item for item in workers if item["online"] is True]
    names = [str(item.get("worker_name") or "") for item in online]
    pids = [item.get("pid") for item in online]
    lanes = sorted({str(item.get("lane") or "") for item in online if item.get("lane")})
    return {
        "online_count": len(online),
        "expected_count": expected_count,
        "total_heartbeat_rows": len(workers),
        "unique_names": len(names) == len(set(names)),
        "unique_pids": len(pids) == len(set(pids)),
        "all_worker_sha_aligned": bool(
            online
            and app_git_sha
            and all(item.get("worker_sha") == app_git_sha for item in online)
        ),
        "all_heartbeats_fresh": bool(online),
        "lane_coverage": lanes,
        "workers": workers,
    }


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
    result = _empty_worker_trust()
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
        latest_dt = _heartbeat_datetime(latest_dt, datetime, timezone)
        result["worker_heartbeat"] = (
            latest_dt.astimezone(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")
        )
        age = (datetime.now(tz=timezone.utc) - latest_dt).total_seconds()
        started_at = _started_at_value(
            row["started_at"] if row is not None else None,
            datetime,
            timezone,
        )
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
        fleet_workers = [
            _fleet_worker(
                raw,
                now=now,
                datetime=datetime,
                timezone=timezone,
                worker_lane_from_name=_worker_lane_from_name,
            )
            for raw in rows
        ]
        online_workers = [item for item in fleet_workers if item["online"] is True]
        expected_raw = str(os.getenv("APIFY_WORKER_EXPECTED_INSTANCES", "") or "").strip()
        expected_count = int(expected_raw) if expected_raw.isdigit() and int(expected_raw) > 0 else None
        result["worker_online"] = bool(online_workers)
        result["worker_fleet"] = _fleet_projection(
            fleet_workers,
            expected_count=expected_count,
            app_git_sha=APP_GIT_SHA,
        )
    except Exception:
        logger.debug("health: worker heartbeat read failed", exc_info=True)
    return result
