"""Health payload helpers for the FastAPI entrypoint."""
from __future__ import annotations

import asyncio
from typing import Any

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
