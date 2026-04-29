"""
api/routers/ops.py — admin runtime metrics
"""
from __future__ import annotations

import asyncio
from datetime import datetime

from fastapi import APIRouter, Request

from app.core.security import require_admin_async
from app.db.connection import get_db_actor_stats, probe_postgres_connectivity
from app.services.ai.runtime_guards import list_provider_guard_stats
from app.services.monitoring.runtime import get_request_metrics_snapshot
from app.services.security.rate_limiter import get_rate_limit_stats

router = APIRouter(prefix="/api/admin/runtime", tags=["ops"])


@router.get("/metrics")
async def get_runtime_metrics(request: Request):
    admin = await require_admin_async(request)
    queue = getattr(request.app.state, "job_queue", None)
    via_event_bus = getattr(request.app.state, "via_event_bus", None)
    queue_stats = await queue.runtime_stats() if queue is not None else {"backend": "none"}
    via_stats = await via_event_bus.runtime_stats() if via_event_bus is not None else {"backend": "none"}
    postgres = await asyncio.to_thread(probe_postgres_connectivity)
    return {
        "generated_at": datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ"),
        "requested_by": admin.get("email", ""),
        "requests": get_request_metrics_snapshot(),
        "rate_limit": get_rate_limit_stats(),
        "db_actor": get_db_actor_stats(),
        "queue": queue_stats,
        "via": via_stats,
        "postgres": postgres,
        "ai_providers": list_provider_guard_stats(),
    }
