"""Health payload helpers for the FastAPI entrypoint."""
from __future__ import annotations

import asyncio
import math
import os
import time
from concurrent.futures import Future, ThreadPoolExecutor
from datetime import datetime, timezone
from threading import Lock
from typing import Any, Callable, Mapping

from app.core.config import (
    APP_ROLE,
    DATABASE_URL,
    DB_RUNTIME_BACKEND,
    DB_TARGET_BACKEND,
    ENABLE_LOCAL_ORCHESTRATOR,
    PLATFORM_INGEST_SOURCES,
    REDIS_URL,
    USE_REDIS_JOBS,
    WORKER_ASYNC_CONSUMERS,
    WORKER_CLUSTER_TIER,
    WORKER_CONFIGURED_CONCURRENCY,
    WORKER_SERVICE_PROCESSES,
)
from app.db.connection import get_db_actor_stats, probe_postgres_connectivity

from app.core.logging import get_logger

logger = get_logger(__name__)


_RUNTIME_TRUST_STAGE_NAMES = (
    "db_startup",
    "release_validation",
    "release_identity",
    "db_migration",
    "worker_heartbeat",
    "redis_worker",
    "scheduler",
)


def _runtime_trust_timeout_seconds() -> float:
    """Keep the public trust projection inside the caller's three-second SLA."""

    raw = str(os.getenv("VKPI_RUNTIME_TRUST_TIMEOUT_SECONDS", "1.0") or "1.0").strip()
    try:
        parsed = float(raw)
    except ValueError:
        parsed = 1.0
    if not math.isfinite(parsed):
        parsed = 1.0
    return min(2.0, max(0.1, parsed))


RUNTIME_TRUST_TIMEOUT_SECONDS = _runtime_trust_timeout_seconds()

# A1 W1 observability: the minimal unauthenticated read-only path an external
# uptime probe may poll.  In production the anonymous ``/health`` body is only
# ``{"status","service","version"}``; trust/heartbeat fields need the ops token.
EXTERNAL_PING_HINT: dict[str, Any] = {
    "path": "/health",
    "method": "GET",
    "auth": "none",
    "expect_http_status": 200,
    "expect_json": {"status": "ok"},
    "note": "liveness only; heartbeat_age_seconds requires the ops/admin token",
}


def external_ping_hint() -> dict[str, Any]:
    return {**EXTERNAL_PING_HINT, "expect_json": dict(EXTERNAL_PING_HINT["expect_json"])}


def _iso_age_seconds(value: Any, now: datetime) -> float | None:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return round((now - parsed.astimezone(timezone.utc)).total_seconds(), 1)


def _freshest_age_by_role(trust: Mapping[str, object], now: datetime) -> dict[str, float]:
    """Freshest heartbeat age per critical role (apify lanes + redis worker)."""

    roles: dict[str, float] = {}

    def offer(role: str, age: Any) -> None:
        if isinstance(age, bool) or not isinstance(age, (int, float)) or not math.isfinite(age):
            return
        if role not in roles or float(age) < roles[role]:
            roles[role] = float(age)

    apify_fleet = trust.get("worker_fleet")
    apify_rows = apify_fleet.get("workers") if isinstance(apify_fleet, Mapping) else None
    for row in apify_rows or []:
        if isinstance(row, Mapping):
            offer(f"apify:{str(row.get('lane') or 'all')}", row.get("heartbeat_age_seconds"))
    if not any(role.startswith("apify:") for role in roles):
        offer("apify:primary", _iso_age_seconds(trust.get("worker_heartbeat"), now))
    redis_fleet = trust.get("redis_worker_fleet")
    redis_rows = redis_fleet.get("workers") if isinstance(redis_fleet, Mapping) else None
    for row in redis_rows or []:
        if isinstance(row, Mapping):
            offer("redis-worker", row.get("heartbeat_age_seconds"))
    return roles


def compute_heartbeat_age(
    trust: Mapping[str, object], *, now: datetime | None = None
) -> dict[str, object]:
    """Oldest critical heartbeat age: max over roles of each role's freshest row.

    A dead lane keeps its last row forever, so the projection takes the freshest
    heartbeat inside every role first and only then the oldest across roles.
    ``None`` means no heartbeat evidence at all; the existing ``worker_online``
    contract already turns that into a degraded status.
    """

    roles = _freshest_age_by_role(trust, now or datetime.now(tz=timezone.utc))
    ordered = {role: round(age, 1) for role, age in sorted(roles.items())}
    oldest = max(ordered.values()) if ordered else None
    return {"heartbeat_age_seconds": oldest, "heartbeat_age_roles": ordered}


class _RuntimeTrustCoordinator:
    """Single-flight executor plus observable progress for sync trust probes.

    A database pool acquisition can remain blocked after the HTTP deadline.
    Reusing one in-flight Future prevents repeated health calls from filling the
    default executor with identical blocked probes.
    """

    def __init__(self) -> None:
        self._lock = Lock()
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="runtime-trust")
        self._future: Future[dict[str, object]] | None = None
        self._started_at: float | None = None
        self._stages: dict[str, dict[str, Any]] = {}

    def submit(self, probe: Callable[[], dict[str, object]]) -> Future[dict[str, object]]:
        with self._lock:
            if self._future is None or self._future.done():
                self._future = self._executor.submit(probe)
            return self._future

    def begin(self) -> None:
        now = time.perf_counter()
        with self._lock:
            self._started_at = now
            self._stages = {
                name: {"status": "pending", "duration_ms": None}
                for name in _RUNTIME_TRUST_STAGE_NAMES
            }

    def stage_started(self, name: str) -> float:
        started = time.perf_counter()
        with self._lock:
            self._stages[name] = {
                "status": "running",
                "duration_ms": None,
                "_started_at": started,
            }
        return started

    def stage_finished(self, name: str, started: float, *, error_type: str | None = None) -> None:
        stage: dict[str, Any] = {
            "status": "error" if error_type else "completed",
            "duration_ms": round((time.perf_counter() - started) * 1000, 2),
        }
        if error_type:
            stage["error_type"] = error_type
        with self._lock:
            self._stages[name] = stage

    def stage_skipped(self, name: str, reason: str) -> None:
        with self._lock:
            self._stages[name] = {
                "status": "skipped",
                "duration_ms": 0.0,
                "reason": str(reason),
            }

    def snapshot(self, status: str, *, timeout_seconds: float | None = None) -> dict[str, Any]:
        now = time.perf_counter()
        with self._lock:
            started_at = self._started_at
            stages: dict[str, dict[str, Any]] = {}
            for name, raw in self._stages.items():
                stage = {key: value for key, value in raw.items() if not key.startswith("_")}
                if raw.get("status") == "running" and raw.get("_started_at") is not None:
                    stage["duration_ms"] = round((now - float(raw["_started_at"])) * 1000, 2)
                stages[name] = stage
        payload: dict[str, Any] = {
            "status": status,
            "duration_ms": round((now - started_at) * 1000, 2) if started_at else 0.0,
            "stages": stages,
            "external_ping_hint": external_ping_hint(),
        }
        if timeout_seconds is not None:
            payload["timeout_ms"] = round(float(timeout_seconds) * 1000, 2)
            payload["in_flight"] = True
        return payload


_RUNTIME_TRUST_COORDINATOR = _RuntimeTrustCoordinator()


def begin_runtime_trust_probe() -> None:
    _RUNTIME_TRUST_COORDINATOR.begin()


def run_runtime_trust_stage(
    name: str,
    probe: Callable[[], Any],
    fallback: Any,
) -> Any:
    started = _RUNTIME_TRUST_COORDINATOR.stage_started(name)
    try:
        value = probe()
    except Exception as exc:
        _RUNTIME_TRUST_COORDINATOR.stage_finished(
            name,
            started,
            error_type=type(exc).__name__,
        )
        logger.debug("health: runtime trust stage %s failed", name, exc_info=True)
        return fallback() if callable(fallback) else fallback
    _RUNTIME_TRUST_COORDINATOR.stage_finished(name, started)
    return value


def skip_runtime_trust_stage(name: str, reason: str) -> None:
    _RUNTIME_TRUST_COORDINATOR.stage_skipped(name, reason)


def finish_runtime_trust_probe(trust: Mapping[str, object]) -> dict[str, Any]:
    probe = _RUNTIME_TRUST_COORDINATOR.snapshot("ok")
    reasons: list[str] = []
    stages = probe.get("stages") if isinstance(probe.get("stages"), Mapping) else {}
    if any(
        isinstance(stage, Mapping) and stage.get("status") == "error"
        for stage in stages.values()
    ):
        reasons.append("stage_error")
    db_startup = trust.get("db_startup")
    if not isinstance(db_startup, Mapping) or db_startup.get("state") != "completed":
        reasons.append("db_startup_unready")
    if trust.get("db_migration_source") != "schema_migrations":
        reasons.append("db_migration_unavailable")
    if "db_migration_complete" in trust and trust.get("db_migration_complete") is not True:
        reasons.append("db_migration_set_incomplete")
    if trust.get("worker_online") is not True or trust.get("worker_heartbeat_source") != "db_heartbeat":
        reasons.append("worker_unavailable")
    redis_fleet = trust.get("redis_worker_fleet")
    if isinstance(redis_fleet, Mapping) and redis_fleet.get("expected_count") not in (None, 0):
        if redis_fleet.get("online") is not True:
            reasons.append("redis_worker_unavailable")
    release_validation = trust.get("release_validation")
    if not isinstance(release_validation, Mapping) or release_validation.get("valid") is not True:
        reasons.append("release_validation_untrusted")
    if trust.get("sha_aligned") is not True:
        reasons.append("release_sha_unaligned")
    if reasons:
        probe["status"] = "degraded"
        probe["failure_reasons"] = reasons
    return probe


def build_runtime_trust(
    *,
    db_startup_probe: Callable[[], object],
    release_validation_probe: Callable[[], object],
    client_git_sha_probe: Callable[[], str],
    db_migration_probe: Callable[[], object],
    worker_probe: Callable[[], dict[str, object]],
    redis_worker_probe: Callable[[], dict[str, object]],
    scheduler_probe: Callable[[], object],
    worker_sha_fallback_probe: Callable[[], dict[str, object]],
    server_git_sha: str,
    postgres_runtime: bool,
) -> dict[str, object]:
    """Run the synchronous trust stages once and preserve their evidence."""

    begin_runtime_trust_probe()
    trust: dict[str, object] = {
        "db_startup": run_runtime_trust_stage(
            "db_startup", db_startup_probe, {"state": "unknown"}
        ),
        "release_validation": run_runtime_trust_stage(
            "release_validation",
            release_validation_probe,
            {"active": True, "valid": False, "source": "status_error"},
        ),
    }
    server_sha, client_sha = run_runtime_trust_stage(
        "release_identity",
        lambda: (server_git_sha or None, client_git_sha_probe() or None),
        (server_git_sha or None, None),
    )
    trust.update(
        {
            "server_git_sha": server_sha,
            "client_git_sha": client_sha,
            "sha_aligned": bool(server_sha == client_sha) if server_sha and client_sha else None,
        }
    )
    migration_identity = run_runtime_trust_stage("db_migration", db_migration_probe, None)
    if isinstance(migration_identity, Mapping):
        migration_max = str(migration_identity.get("max") or "") or None
        trust.update(
            {
                "db_migration_complete": migration_identity.get("set_complete") is True,
                "db_migration_exact": migration_identity.get("set_exact") is True,
                "db_migration_applied_count": migration_identity.get("applied_count"),
                "db_migration_expected_count": migration_identity.get("expected_count"),
                "db_migration_missing_count": migration_identity.get("missing_count"),
                "db_migration_unexpected_count": migration_identity.get("unexpected_count"),
                "db_migration_set_sha256": migration_identity.get("set_sha256"),
            }
        )
    else:
        migration_max = migration_identity
    trust["db_migration_max"] = migration_max
    trust["db_migration_source"] = "schema_migrations" if migration_max else "unavailable"
    worker_unavailable = {
        "worker_heartbeat": None,
        "worker_online": None,
        "worker_sha": None,
        "worker_sha_source": "unavailable",
        "worker_heartbeat_source": "unavailable",
    }
    redis_unavailable = {
        "online": False,
        "online_count": 0,
        "expected_count": None,
        "workers": [],
    }
    if postgres_runtime and not migration_max:
        for stage in ("worker_heartbeat", "redis_worker", "scheduler"):
            skip_runtime_trust_stage(stage, "db_migration_unavailable")
        trust.update(worker_unavailable)
        trust["redis_worker_fleet"] = redis_unavailable
        trust["scheduler_status"] = "unavailable"
    else:
        trust.update(
            run_runtime_trust_stage("worker_heartbeat", worker_probe, worker_unavailable)
        )
        trust["redis_worker_fleet"] = run_runtime_trust_stage(
            "redis_worker", redis_worker_probe, redis_unavailable
        )
        trust["scheduler_status"] = run_runtime_trust_stage(
            "scheduler", scheduler_probe, "not_configured"
        )
    if "worker_sha" not in trust:
        try:
            trust.update(worker_sha_fallback_probe())
        except Exception:
            trust["worker_sha"] = None
            trust["worker_sha_source"] = "unavailable"
    trust.update(compute_heartbeat_age(trust))
    trust["probe"] = finish_runtime_trust_probe(trust)
    return trust


def _runtime_trust_failure_payload(
    *,
    server_git_sha: str,
    client_git_sha: str,
    probe: Mapping[str, Any],
) -> dict[str, object]:
    """Return a schema-compatible, fail-closed trust body after timeout/error."""

    return {
        "db_startup": {"state": "unknown"},
        "db_migration_max": None,
        "db_migration_source": "probe_unavailable",
        "db_migration_complete": None,
        "db_migration_exact": None,
        "db_migration_applied_count": None,
        "db_migration_expected_count": None,
        "db_migration_missing_count": None,
        "db_migration_unexpected_count": None,
        "db_migration_set_sha256": None,
        "worker_heartbeat": None,
        "worker_online": None,
        "worker_name": None,
        "worker_pid": None,
        "worker_sha": None,
        "worker_sha_source": "probe_unavailable",
        "worker_boot_nonce_sha256": None,
        "worker_started_at": None,
        "worker_heartbeat_source": "probe_unavailable",
        "worker_fleet": {
            "online_count": 0,
            "expected_count": None,
            "total_heartbeat_rows": 0,
            "all_worker_sha_aligned": False,
            "lane_coverage": [],
            "workers": [],
        },
        "redis_worker_fleet": {
            "online": False,
            "online_count": 0,
            "expected_count": None,
            "workers": [],
        },
        "scheduler_status": "unavailable",
        "release_validation": {"active": True, "valid": False, "source": "probe_unavailable"},
        "server_git_sha": str(server_git_sha or "").strip() or None,
        "client_git_sha": str(client_git_sha or "").strip() or None,
        "sha_aligned": None,
        "heartbeat_age_seconds": None,
        "heartbeat_age_roles": {},
        "probe": dict(probe),
    }


async def bounded_runtime_trust(
    probe: Callable[[], dict[str, object]],
    *,
    server_git_sha: str,
    client_git_sha: str,
    timeout_seconds: float = RUNTIME_TRUST_TIMEOUT_SECONDS,
) -> dict[str, object]:
    timeout = min(2.0, max(0.1, float(timeout_seconds)))
    future = _RUNTIME_TRUST_COORDINATOR.submit(probe)
    try:
        result = await asyncio.wait_for(
            asyncio.shield(asyncio.wrap_future(future)),
            timeout=timeout,
        )
    except TimeoutError:
        snapshot = _RUNTIME_TRUST_COORDINATOR.snapshot("timeout", timeout_seconds=timeout)
        return _runtime_trust_failure_payload(
            server_git_sha=server_git_sha,
            client_git_sha=client_git_sha,
            probe=snapshot,
        )
    except Exception as exc:
        snapshot = _RUNTIME_TRUST_COORDINATOR.snapshot("error")
        snapshot["error_type"] = type(exc).__name__
        return _runtime_trust_failure_payload(
            server_git_sha=server_git_sha,
            client_git_sha=client_git_sha,
            probe=snapshot,
        )
    if not isinstance(result, dict):
        snapshot = _RUNTIME_TRUST_COORDINATOR.snapshot("error")
        snapshot["error_type"] = "InvalidProbeResult"
        return _runtime_trust_failure_payload(
            server_git_sha=server_git_sha,
            client_git_sha=client_git_sha,
            probe=snapshot,
        )
    return result


def runtime_trust_service_status(trust: Mapping[str, object]) -> str:
    probe = trust.get("probe")
    if isinstance(probe, Mapping) and probe.get("status") != "ok":
        return "degraded"
    return "ok"


def _probe_redis() -> dict[str, Any]:
    """真 ping Redis(诚实健康)。配置了但不可达 → reachable=False(红),不再 fake-green。

    USE_REDIS_JOBS 仅表示配置存在(REDIS_URL 设了);这里实连一次,1s 超时。
    """
    if not USE_REDIS_JOBS or not REDIS_URL:
        return {"configured": False, "reachable": None}
    try:
        import redis as _redis

        client = _redis.from_url(REDIS_URL, socket_connect_timeout=1.0, socket_timeout=1.0)
        ok = bool(client.ping())
        try:
            client.close()
        except Exception:
            logger.warning("suppressed exception (hardening: was silent)", exc_info=True)
            pass
        return {"configured": True, "reachable": ok}
    except Exception as exc:
        return {"configured": True, "reachable": False, "error": str(exc)[:120]}


async def build_deep_health_payload(
    app: Any,
    *,
    app_version: str,
    build: dict[str, str | bool],
    is_production: bool,
    is_public_app: bool,
    is_admin_app: bool,
) -> dict[str, Any]:
    queue_backend = getattr(getattr(app.state, "job_queue", None), "backend_name", "none")
    via_backend = getattr(getattr(app.state, "via_event_bus", None), "backend_name", "none")
    queue = getattr(app.state, "job_queue", None)
    via_event_bus = getattr(app.state, "via_event_bus", None)
    db_runtime = get_db_actor_stats()
    postgres: dict[str, Any] = {
        "configured": bool(DATABASE_URL),
        "runtime_selected": DB_RUNTIME_BACKEND == "postgres",
        "pool_open": bool(db_runtime.get("running")),
        "ok": bool(db_runtime.get("running")) if DB_RUNTIME_BACKEND == "postgres" else True,
    }
    queue_stats: dict[str, Any] = {
        "backend": queue_backend,
        "configured_concurrency": WORKER_CONFIGURED_CONCURRENCY,
        "worker_processes": WORKER_SERVICE_PROCESSES,
        "worker_async_consumers": WORKER_ASYNC_CONSUMERS,
    }
    via_stats: dict[str, Any] = {"backend": via_backend}
    try:
        queue_stats = await queue.runtime_stats() if queue is not None else {"backend": "none"}
    except Exception as exc:
        queue_stats = {"backend": queue_backend, "ok": False, "error": str(exc)[:160]}
    try:
        via_stats = await via_event_bus.runtime_stats() if via_event_bus is not None else {"backend": "none"}
    except Exception as exc:
        via_stats = {"backend": via_backend, "ok": False, "error": str(exc)[:160]}
    postgres = await asyncio.to_thread(probe_postgres_connectivity)
    return {
        "status": "ok",
        "version": app_version,
        "build": build,
        "deep": True,
        "app_role": APP_ROLE,
        "production_mode": is_production,
        "surfaces": {"public_web": is_public_app, "admin_web": is_admin_app},
        "database": {
            "database_backend": DB_RUNTIME_BACKEND,
            "target_backend": DB_TARGET_BACKEND,
            "postgres_configured": bool(DATABASE_URL),
            "pool_health": postgres,
            "runtime": db_runtime,
        },
        "ingestion_sources": PLATFORM_INGEST_SOURCES,
        "queue_backend": queue_backend,
        "queue": queue_stats,
        "via_event_backend": via_backend,
        "via": via_stats,
        "redis_jobs": USE_REDIS_JOBS,
        "redis": await asyncio.to_thread(_probe_redis),
        "local_orchestrator": ENABLE_LOCAL_ORCHESTRATOR,
        "worker_role": {
            "identity": APP_ROLE,
            "separated": APP_ROLE == "worker",
            "cluster_tier": WORKER_CLUSTER_TIER,
            "processes": WORKER_SERVICE_PROCESSES,
            "async_consumers": WORKER_ASYNC_CONSUMERS,
            "configured_concurrency": WORKER_CONFIGURED_CONCURRENCY,
        },
    }
