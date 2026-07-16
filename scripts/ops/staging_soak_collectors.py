#!/usr/bin/env python3
"""Read-only host collectors for the release-bound staging soak.

The module deliberately projects only operational counters.  It never stores
database or Redis connection strings, journal messages, environment values, or
raw application payloads in the soak evidence.
"""

from __future__ import annotations

import hashlib
import json
import stat
import shutil
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Sequence
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener


class CollectionError(RuntimeError):
    """One fail-closed sampling failure with a non-secret category."""

    def __init__(self, category: str):
        self.category = category
        super().__init__(f"staging soak collection failed: {category}")


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: ANN001
        return None


_HTTP = build_opener(ProxyHandler({}), _NoRedirect())


@dataclass(frozen=True)
class CollectorConfig:
    health_url: str
    env_file: Path
    root: Path
    systemd_units: tuple[str, ...]
    timeout_seconds: float = 10.0


def _plain_env(path: Path) -> dict[str, str]:
    if not path.is_file() or path.is_symlink():
        raise CollectionError("environment_not_regular")
    values: dict[str, str] = {}
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeError):
        raise CollectionError("environment_unreadable") from None
    for line in lines:
        raw = line.strip()
        if not raw or raw.startswith("#") or "=" not in raw:
            continue
        key, value = raw.split("=", 1)
        key = key.strip()
        if key in values:
            raise CollectionError("environment_duplicate_key")
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
            value = value[1:-1]
        values[key] = value
    return values


def collect_environment_fingerprint(path: Path) -> dict[str, Any]:
    try:
        info = path.lstat()
    except OSError:
        raise CollectionError("environment_unreadable") from None
    if (
        path.is_symlink()
        or not stat.S_ISREG(info.st_mode)
        or info.st_size <= 0
        or info.st_size > 2 * 1024 * 1024
        or info.st_mode & (stat.S_IWGRP | stat.S_IRWXO)
    ):
        raise CollectionError("environment_file_unsafe")
    digest = hashlib.sha256()
    try:
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(128 * 1024), b""):
                digest.update(chunk)
    except OSError:
        raise CollectionError("environment_unreadable") from None
    return {"content_sha256": digest.hexdigest(), "bytes": int(info.st_size)}


def _validate_health_url(value: str) -> str:
    try:
        parsed = urlsplit(value)
        port = parsed.port
    except ValueError:
        raise CollectionError("health_url_invalid") from None
    loopback = parsed.hostname in {"127.0.0.1", "localhost", "::1"}
    valid = (
        parsed.scheme in {"http", "https"}
        and parsed.hostname is not None
        and parsed.username is None
        and parsed.password is None
        and parsed.path == "/health"
        and not parsed.query
        and not parsed.fragment
        and (parsed.scheme == "https" or loopback)
        and (port is not None or parsed.scheme == "https")
    )
    if not valid:
        raise CollectionError("health_url_invalid")
    return value


def _sha_set(rows: Any) -> list[str]:
    values: list[str] = []
    if isinstance(rows, list):
        for row in rows:
            if not isinstance(row, Mapping):
                continue
            value = str(row.get("boot_nonce_sha256") or "").strip().lower()
            if len(value) == 64 and all(char in "0123456789abcdef" for char in value):
                values.append(value)
    return sorted(set(values))


def collect_health(url: str, *, timeout: float) -> dict[str, Any]:
    target = _validate_health_url(url)
    started = time.monotonic()
    request = Request(target, method="GET", headers={"Accept": "application/json"})
    try:
        with _HTTP.open(request, timeout=timeout) as response:
            status_code = int(response.status)
            body = response.read(2 * 1024 * 1024 + 1)
    except Exception:
        raise CollectionError("health_request_failed") from None
    latency_ms = round((time.monotonic() - started) * 1000.0, 3)
    if status_code != 200 or len(body) > 2 * 1024 * 1024:
        raise CollectionError("health_response_invalid")
    try:
        payload = json.loads(body)
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise CollectionError("health_json_invalid") from None
    if not isinstance(payload, Mapping):
        raise CollectionError("health_json_invalid")
    trust = payload.get("trust") if isinstance(payload.get("trust"), Mapping) else {}
    build = payload.get("build") if isinstance(payload.get("build"), Mapping) else {}
    apify = trust.get("worker_fleet") if isinstance(trust.get("worker_fleet"), Mapping) else {}
    redis_fleet = (
        trust.get("redis_worker_fleet")
        if isinstance(trust.get("redis_worker_fleet"), Mapping)
        else {}
    )
    return {
        "latency_ms": latency_ms,
        "status": str(payload.get("status") or ""),
        "server_git_sha": str(trust.get("server_git_sha") or build.get("git_sha") or ""),
        "client_git_sha": str(trust.get("client_git_sha") or build.get("client_build") or ""),
        "sha_aligned": trust.get("sha_aligned") is True,
        "migration_max": str(trust.get("db_migration_max") or ""),
        "apify": {
            "online_count": apify.get("online_count"),
            "unique_names": apify.get("unique_names") is True,
            "unique_pids": apify.get("unique_pids") is True,
            "all_worker_sha_aligned": apify.get("all_worker_sha_aligned") is True,
            "all_heartbeats_fresh": apify.get("all_heartbeats_fresh") is True,
            "lane_coverage": sorted(str(item) for item in (apify.get("lane_coverage") or [])),
            "boot_nonce_sha256_set": _sha_set(apify.get("workers")),
        },
        "redis_worker": {
            "online_count": redis_fleet.get("online_count"),
            "all_worker_sha_aligned": redis_fleet.get("all_worker_sha_aligned") is True,
            "all_heartbeats_fresh": redis_fleet.get("all_heartbeats_fresh") is True,
            "all_redis_ready": redis_fleet.get("all_redis_ready") is True,
            "boot_nonce_sha256_set": _sha_set(redis_fleet.get("workers")),
        },
    }


def collect_database(env_file: Path, *, timeout: float) -> dict[str, Any]:
    values = _plain_env(env_file)
    database_url = values.get("DATABASE_URL", "").strip()
    if not database_url:
        raise CollectionError("database_url_missing")
    try:
        import psycopg
    except Exception:
        raise CollectionError("psycopg_unavailable") from None
    try:
        with psycopg.connect(database_url, connect_timeout=max(1, int(timeout))) as conn:
            with conn.transaction():
                with conn.cursor() as cur:
                    cur.execute("SET TRANSACTION READ ONLY")
                    cur.execute(
                        """
                        SELECT current_database(),
                               (SELECT MAX(version_key) FROM schema_migrations),
                               COUNT(*) FILTER (WHERE state = 'active'),
                               COUNT(*) FILTER (WHERE state = 'idle'),
                               COUNT(*) FILTER (
                                 WHERE state = 'idle in transaction'
                                   AND xact_start < now() - interval '30 seconds'
                               )
                        FROM pg_stat_activity
                        """
                    )
                    database_name, migration, active, idle, idle_in_tx = cur.fetchone()
                    cur.execute("SELECT COUNT(*) FROM pg_locks WHERE NOT granted")
                    lock_waits = int(cur.fetchone()[0])
                    cur.execute("SELECT to_regclass('public.apify_jobs')")
                    jobs_present = cur.fetchone()[0] is not None
                    queue = {
                        "present": jobs_present,
                        "queued": 0,
                        "oldest_queued_age_seconds": None,
                        "failed_or_triage": 0,
                    }
                    if jobs_present:
                        cur.execute(
                            """
                            SELECT COUNT(*) FILTER (WHERE status = 'queued'),
                                   EXTRACT(
                                     EPOCH FROM (
                                       now() - MIN(created_at) FILTER (WHERE status = 'queued')
                                     )
                                   ),
                                   COUNT(*) FILTER (WHERE status IN ('failed', 'triage'))
                            FROM apify_jobs
                            """
                        )
                        queued, oldest, failed = cur.fetchone()
                        queue.update(
                            {
                                "queued": int(queued or 0),
                                "oldest_queued_age_seconds": (
                                    round(float(oldest), 3) if oldest is not None else None
                                ),
                                "failed_or_triage": int(failed or 0),
                            }
                        )
    except CollectionError:
        raise
    except Exception:
        raise CollectionError("database_read_failed") from None
    return {
        "database_name_sha256": hashlib.sha256(str(database_name).encode()).hexdigest(),
        "migration_max": str(migration or ""),
        "connections": {"active": int(active or 0), "idle": int(idle or 0)},
        "idle_in_transaction_over_30s": int(idle_in_tx or 0),
        "lock_waits": lock_waits,
        "queue": queue,
        "transaction_mode": "read_only",
    }


def collect_redis(env_file: Path, *, timeout: float) -> dict[str, Any]:
    values = _plain_env(env_file)
    redis_url = values.get("REDIS_URL", "").strip()
    if not redis_url:
        raise CollectionError("redis_url_missing")
    try:
        import redis
    except Exception:
        raise CollectionError("redis_client_unavailable") from None
    try:
        client = redis.Redis.from_url(
            redis_url,
            socket_connect_timeout=timeout,
            socket_timeout=timeout,
            decode_responses=True,
        )
        if client.ping() is not True:
            raise CollectionError("redis_ping_failed")
        persistence = client.info("persistence")
        server = client.info("server")
        memory = client.info("memory")
        client.close()
    except CollectionError:
        raise
    except Exception:
        raise CollectionError("redis_read_failed") from None
    return {
        "aof_enabled": int(persistence.get("aof_enabled") or 0) == 1,
        "aof_last_write_status": str(persistence.get("aof_last_write_status") or ""),
        "rdb_last_bgsave_status": str(persistence.get("rdb_last_bgsave_status") or ""),
        "uptime_in_seconds": int(server.get("uptime_in_seconds") or 0),
        "run_id_sha256": hashlib.sha256(str(server.get("run_id") or "").encode()).hexdigest(),
        "used_memory": int(memory.get("used_memory") or 0),
    }


def collect_disk(root: Path) -> dict[str, int]:
    try:
        usage = shutil.disk_usage(root)
    except OSError:
        raise CollectionError("disk_usage_failed") from None
    return {"total_bytes": int(usage.total), "used_bytes": int(usage.used), "free_bytes": int(usage.free)}


def collect_systemd(units: Sequence[str], *, timeout: float) -> dict[str, Any]:
    systemctl = shutil.which("systemctl")
    if not systemctl:
        raise CollectionError("systemctl_unavailable")
    rows: list[dict[str, Any]] = []
    for unit in units:
        command = [
            systemctl,
            "show",
            "--property=LoadState,ActiveState,SubState,NRestarts,MainPID",
            "--value",
            unit,
        ]
        try:
            result = subprocess.run(command, text=True, capture_output=True, timeout=timeout, check=False)
        except (OSError, subprocess.SubprocessError):
            raise CollectionError("systemctl_read_failed") from None
        values = result.stdout.splitlines()
        if result.returncode != 0 or len(values) != 5:
            raise CollectionError("systemctl_state_invalid")
        load, active, sub, restarts, main_pid = values
        try:
            restart_count = int(restarts or 0)
            pid = int(main_pid or 0)
        except ValueError:
            raise CollectionError("systemctl_state_invalid") from None
        rows.append(
            {
                "unit": unit,
                "load_state": load,
                "active_state": active,
                "sub_state": sub,
                "n_restarts": restart_count,
                "main_pid": pid,
            }
        )
    return {"units": rows}


def _journal_command(units: Sequence[str], *, after_cursor: str | None) -> list[str]:
    journalctl = shutil.which("journalctl")
    if not journalctl:
        raise CollectionError("journalctl_unavailable")
    command = [
        journalctl,
        "--no-pager",
        "--output=json",
        "--output-fields=__CURSOR,__REALTIME_TIMESTAMP,PRIORITY",
    ]
    for unit in units:
        command.extend(["--unit", unit])
    if after_cursor:
        command.extend(["--after-cursor", after_cursor])
    else:
        command.extend(["--lines", "1"])
    return command


def collect_journal(
    units: Sequence[str], *, cursor: str | None, timeout: float
) -> tuple[dict[str, Any], str]:
    try:
        result = subprocess.run(
            _journal_command(units, after_cursor=cursor),
            text=True,
            capture_output=True,
            timeout=timeout,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        raise CollectionError("journal_read_failed") from None
    if result.returncode != 0 or len(result.stdout.encode("utf-8")) > 16 * 1024 * 1024:
        raise CollectionError("journal_read_failed")
    entries = 0
    priority_error = 0
    priority_warning = 0
    next_cursor = cursor
    for line in result.stdout.splitlines():
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            raise CollectionError("journal_json_invalid") from None
        if not isinstance(row, Mapping):
            raise CollectionError("journal_json_invalid")
        candidate = str(row.get("__CURSOR") or "")
        if candidate:
            next_cursor = candidate
        if cursor is None:
            continue
        entries += 1
        try:
            priority = int(row.get("PRIORITY", 6))
        except (TypeError, ValueError):
            priority = 6
        if priority <= 3:
            priority_error += 1
        if priority <= 4:
            priority_warning += 1
    if not next_cursor:
        raise CollectionError("journal_cursor_missing")
    return (
        {
            "entries": entries,
            "priority_error_entries": priority_error,
            "priority_warning_entries": priority_warning,
            "cursor_sha256": hashlib.sha256(next_cursor.encode("utf-8")).hexdigest(),
            "raw_messages_persisted": False,
        },
        next_cursor,
    )


class HostCollector:
    """Collect one complete sample or fail without emitting partial evidence."""

    def __init__(self, config: CollectorConfig) -> None:
        self.config = config

    def collect(self, *, journal_cursor: str | None) -> tuple[dict[str, Any], str]:
        config = self.config
        environment = collect_environment_fingerprint(config.env_file)
        health = collect_health(config.health_url, timeout=config.timeout_seconds)
        database = collect_database(config.env_file, timeout=config.timeout_seconds)
        redis_state = collect_redis(config.env_file, timeout=config.timeout_seconds)
        disk = collect_disk(config.root)
        systemd = collect_systemd(config.systemd_units, timeout=config.timeout_seconds)
        journal, next_cursor = collect_journal(
            config.systemd_units,
            cursor=journal_cursor,
            timeout=config.timeout_seconds,
        )
        return (
            {
                "environment": environment,
                "health": health,
                "database": database,
                "redis": redis_state,
                "disk": disk,
                "systemd": systemd,
                "journal": journal,
            },
            next_cursor,
        )
