"""Runtime identity and pre-consumption gates for the Redis Streams worker."""
from __future__ import annotations

import asyncio
import hashlib
import os
import re
import secrets
import socket
import subprocess
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Awaitable, Callable, Mapping

from app.core.logging import get_logger
from app.db.connection import (
    db_connection_sync_reusing_scope,
    db_connection_sync_scope,
    get_conn,
    is_postgres_runtime,
    table_exists,
)


logger = get_logger(__name__)
PROJECT_ROOT = Path(__file__).resolve().parents[3]
_SHA_RE = re.compile(r"^[0-9a-f]{40}$")
REDIS_WORKER_NAME_PREFIX = "redis-worker-"
_REQUIRED_RUNTIME_COLUMNS: dict[str, tuple[str, ...]] = {
    "schema_migrations": ("version_key",),
    "job_execution_ledger": (
        "task_id",
        "job_type",
        "status",
        "retry_count",
        "payload_json",
        "created_at",
        "updated_at",
        "started_at",
        "finished_at",
        "timeout_seconds",
        "stream_id",
        "consumer_name",
        "extra_json",
    ),
    "vkpi_async_task_items": ("task_id", "status", "error", "updated_at"),
    "vkpi_worker_heartbeat": (
        "worker_name",
        "last_heartbeat_at",
        "pid",
        "updated_at",
        "worker_git_sha",
        "boot_nonce_sha256",
        "started_at",
        "redis_ready",
        "redis_readiness_at",
        "redis_stream_key",
        "redis_group_name",
        "redis_consumer_count",
        "redis_ready_sequence",
        "redis_heartbeat_interval_seconds",
        "redis_readiness_error_code",
    ),
}


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _iso(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _release_sha() -> str:
    candidates: list[str] = [str(os.getenv("APP_GIT_SHA") or "").strip().lower()]
    build_file = PROJECT_ROOT / "BUILD_GIT_SHA"
    try:
        candidates.append(build_file.read_text(encoding="utf-8").strip().lower())
    except OSError:
        pass
    try:
        candidates.append(
            subprocess.run(
                ["git", "rev-parse", "HEAD"],
                cwd=PROJECT_ROOT,
                capture_output=True,
                check=True,
                text=True,
                timeout=5,
            ).stdout.strip().lower()
        )
    except (OSError, subprocess.SubprocessError):
        pass
    for value in candidates:
        if _SHA_RE.fullmatch(value):
            return value
    raise RuntimeError("redis worker requires an exact 40-hex release identity")


@dataclass(frozen=True)
class RedisWorkerIdentity:
    worker_name: str
    pid: int
    worker_git_sha: str
    boot_nonce_sha256: str
    started_at: str

    def payload(self) -> dict[str, Any]:
        return asdict(self)


def build_redis_worker_identity() -> RedisWorkerIdentity:
    default_name = f"{REDIS_WORKER_NAME_PREFIX}{socket.gethostname()}-{os.getpid()}"
    worker_name = str(os.getenv("VKPI_REDIS_WORKER_HEARTBEAT_NAME") or default_name).strip()
    if not worker_name.startswith(REDIS_WORKER_NAME_PREFIX):
        raise RuntimeError(f"redis worker heartbeat name must start with {REDIS_WORKER_NAME_PREFIX}")
    nonce = str(os.getenv("VKPI_REDIS_WORKER_BOOT_NONCE") or "").strip() or secrets.token_urlsafe(32)
    return RedisWorkerIdentity(
        worker_name=worker_name,
        pid=os.getpid(),
        worker_git_sha=_release_sha(),
        boot_nonce_sha256=hashlib.sha256(nonce.encode("utf-8")).hexdigest(),
        started_at=_iso(_utcnow()),
    )


def redis_worker_concurrency(requested: int) -> int:
    """Reject accidental 15-consumer starts; the production unit is 2-way."""

    try:
        hard_max = max(1, min(4, int(os.getenv("VKPI_REDIS_WORKER_MAX_CONSUMERS", "2"))))
    except (TypeError, ValueError):
        hard_max = 2
    value = max(1, int(requested or 1))
    if value > hard_max:
        raise RuntimeError(
            f"redis worker concurrency {value} exceeds reviewed hard max {hard_max}"
        )
    return value


def redis_worker_db_preflight() -> dict[str, Any]:
    """Open the existing Postgres runtime and validate schema without writes.

    Migrations and runtime seeders are owned by the single release migration
    phase.  A worker process must never repeat them on startup.  Selecting the
    required columns with an always-false predicate proves that both the tables
    and the exact columns used by the queue/heartbeat runtime already exist.
    """

    if not is_postgres_runtime():
        raise RuntimeError("dedicated redis worker requires the Postgres runtime")
    try:
        with db_connection_sync_scope():
            conn = get_conn()
            probe = conn.execute("SELECT 1 AS ok").fetchone()
            if not probe or int(probe["ok"] or 0) != 1:
                raise RuntimeError("Postgres connectivity probe returned an invalid result")
            for table_name, columns in _REQUIRED_RUNTIME_COLUMNS.items():
                conn.execute(
                    f"SELECT {', '.join(columns)} FROM {table_name} WHERE 1=0"
                )
    except Exception as exc:
        raise RuntimeError(
            f"redis worker database preflight failed ({type(exc).__name__})"
        ) from exc
    return {
        "pass": True,
        "backend": "postgres",
        "required_tables": sorted(_REQUIRED_RUNTIME_COLUMNS),
        "read_only": True,
        "migrations_run": False,
        "seeders_run": False,
    }


def stale_backlog_preflight() -> dict[str, Any]:
    """Read-only refusal gate; never claims or acknowledges a Stream message."""

    try:
        max_age_hours = max(
            1,
            min(24 * 30, int(os.getenv("VKPI_REDIS_WORKER_MAX_BACKLOG_AGE_HOURS", "24"))),
        )
    except (TypeError, ValueError):
        max_age_hours = 24
    allow_stale = str(os.getenv("VKPI_REDIS_WORKER_ALLOW_STALE_BACKLOG", "0")).strip().lower() in {
        "1", "true", "yes", "on",
    }
    cutoff = _utcnow() - timedelta(hours=max_age_hours)
    # This function runs through ``asyncio.to_thread`` during worker startup.
    # Without an explicit scope, ``get_conn()`` falls back to an executor
    # thread-local connection which is never reachable from the event-loop
    # thread's shutdown cleanup.  Bound the complete proof (including the
    # schema lookup) so every pool lease is returned on success or failure.
    with db_connection_sync_reusing_scope():
        if not table_exists("job_execution_ledger"):
            raise RuntimeError("job_execution_ledger is required before redis worker startup")
        row = get_conn().execute(
            """
            SELECT COUNT(*) AS n, MIN(created_at) AS oldest
            FROM job_execution_ledger
            WHERE status IN ('queued', 'retrying', 'processing', 'running')
              AND created_at < ?
            """,
            (cutoff,),
        ).fetchone()
    stale_count = int((row["n"] if row else 0) or 0)
    oldest = str((row["oldest"] if row else "") or "") or None
    result = {
        "pass": stale_count == 0 or allow_stale,
        "stale_active_count": stale_count,
        "oldest_stale_created_at": oldest,
        "max_backlog_age_hours": max_age_hours,
        "override_enabled": allow_stale,
        "read_only": True,
    }
    if not result["pass"]:
        raise RuntimeError(
            f"redis worker startup blocked by {stale_count} active jobs older than {max_age_hours}h"
        )
    return result


def redis_worker_heartbeat_interval() -> int:
    try:
        return max(5, min(60, int(os.getenv("VKPI_REDIS_WORKER_HEARTBEAT_SECONDS", "15"))))
    except (TypeError, ValueError):
        return 15


def upsert_redis_worker_heartbeat(
    identity: RedisWorkerIdentity,
    readiness: Mapping[str, Any],
    *,
    interval_seconds: int | None = None,
    error_code: str = "",
) -> None:
    # Periodic writes also run in ``asyncio.to_thread`` and may land on a
    # different executor thread each cycle.  Own and release one bounded lease
    # per heartbeat instead of accumulating unreachable thread-local leases.
    with db_connection_sync_reusing_scope():
        if not table_exists("vkpi_worker_heartbeat"):
            raise RuntimeError("vkpi_worker_heartbeat is required for redis worker release identity")
        conn = get_conn()
        conn.execute(
            """
            INSERT INTO vkpi_worker_heartbeat (
                worker_name, last_heartbeat_at, pid, updated_at,
                worker_git_sha, boot_nonce_sha256, started_at,
                redis_ready, redis_readiness_at, redis_stream_key,
                redis_group_name, redis_consumer_count, redis_ready_sequence,
                redis_heartbeat_interval_seconds, redis_readiness_error_code
            )
            VALUES (?, NOW(), ?, NOW(), ?, ?, ?, ?,
                    CASE WHEN ? THEN NOW() ELSE NULL END, ?, ?, ?,
                    CASE WHEN ? THEN 1 ELSE 0 END, ?, ?)
            ON CONFLICT (worker_name) DO UPDATE
            SET last_heartbeat_at = EXCLUDED.last_heartbeat_at,
                pid = EXCLUDED.pid,
                updated_at = EXCLUDED.updated_at,
                worker_git_sha = EXCLUDED.worker_git_sha,
                boot_nonce_sha256 = EXCLUDED.boot_nonce_sha256,
                started_at = EXCLUDED.started_at,
                redis_ready = EXCLUDED.redis_ready,
                redis_readiness_at = EXCLUDED.redis_readiness_at,
                redis_stream_key = EXCLUDED.redis_stream_key,
                redis_group_name = EXCLUDED.redis_group_name,
                redis_consumer_count = EXCLUDED.redis_consumer_count,
                redis_ready_sequence = CASE
                    WHEN EXCLUDED.redis_ready
                     AND vkpi_worker_heartbeat.redis_ready
                     AND vkpi_worker_heartbeat.pid = EXCLUDED.pid
                     AND vkpi_worker_heartbeat.boot_nonce_sha256 = EXCLUDED.boot_nonce_sha256
                    THEN vkpi_worker_heartbeat.redis_ready_sequence + 1
                    WHEN EXCLUDED.redis_ready THEN 1
                    ELSE 0
                END,
                redis_heartbeat_interval_seconds = EXCLUDED.redis_heartbeat_interval_seconds,
                redis_readiness_error_code = EXCLUDED.redis_readiness_error_code
            """,
            (
                identity.worker_name,
                identity.pid,
                identity.worker_git_sha,
                identity.boot_nonce_sha256,
                identity.started_at,
                bool(readiness.get("redis_ready")),
                bool(readiness.get("redis_ready")),
                str(readiness.get("redis_stream_key") or ""),
                str(readiness.get("redis_group_name") or ""),
                max(0, int(readiness.get("redis_consumer_count") or 0)),
                bool(readiness.get("redis_ready")),
                int(interval_seconds or redis_worker_heartbeat_interval()),
                str(error_code or "")[:80] or None,
            ),
        )
        conn.commit()


async def redis_worker_heartbeat_loop(
    identity: RedisWorkerIdentity,
    stop_event: asyncio.Event,
    readiness_check: Callable[[], Awaitable[Mapping[str, Any]]],
) -> None:
    interval = redis_worker_heartbeat_interval()
    while not stop_event.is_set():
        # The boot path writes sequence 1 only after the first successful
        # Redis probe.  Each later sequence therefore represents one complete
        # heartbeat interval on the same PID/boot nonce.
        try:
            await asyncio.wait_for(stop_event.wait(), timeout=interval)
            break
        except asyncio.TimeoutError:
            pass
        try:
            readiness = await readiness_check()
            await asyncio.to_thread(
                upsert_redis_worker_heartbeat,
                identity,
                readiness,
                interval_seconds=interval,
            )
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.error(
                "redis worker heartbeat failed | name=%s error=%s",
                identity.worker_name,
                type(exc).__name__,
            )
            try:
                await asyncio.to_thread(
                    upsert_redis_worker_heartbeat,
                    identity,
                    {"redis_ready": False},
                    interval_seconds=interval,
                    error_code=f"{type(exc).__name__}",
                )
            except Exception:
                logger.error("redis worker failed-readiness heartbeat write failed", exc_info=True)
            raise RuntimeError(
                f"redis worker periodic readiness failed ({type(exc).__name__})"
            ) from exc


__all__ = [
    "REDIS_WORKER_NAME_PREFIX",
    "RedisWorkerIdentity",
    "build_redis_worker_identity",
    "redis_worker_concurrency",
    "redis_worker_db_preflight",
    "redis_worker_heartbeat_loop",
    "redis_worker_heartbeat_interval",
    "stale_backlog_preflight",
    "upsert_redis_worker_heartbeat",
]
