"""
services/system/runtime.py — Runtime admin (batch 5)

Surfaces worker health, queue depths, route performance, system resources,
scheduler control, and cache clear operations. Hooks into existing
services/jobs, services/scheduler, services/cache.
"""
from __future__ import annotations

import os
from datetime import datetime
from typing import Any

from app.core.logging import get_logger
from app.db.connection import get_conn

logger = get_logger(__name__)


# =========================================================================
# Workers
# =========================================================================

def worker_states() -> dict:
    """
    Return snapshot of the 10 async video orchestrator workers.
    Reads from services.jobs worker registry if present.
    """
    try:
        from app.services.jobs.worker import get_worker_states
        raw = get_worker_states()
        return {"workers": raw}
    except ImportError:
        # Fallback: infer from jobs table
        conn = get_conn()
        try:
            rows = conn.execute(
                """SELECT worker_id, status, current_job_id,
                          started_at, last_heartbeat
                   FROM job_workers
                   ORDER BY worker_id"""
            ).fetchall()
            return {"workers": [dict(r) for r in rows]}
        except Exception:
            return {"workers": [], "note": "worker registry not available"}


# =========================================================================
# Queues
# =========================================================================

def queue_depths() -> dict:
    """Depth + age of oldest item per queue."""
    conn = get_conn()
    try:
        rows = conn.execute(
            """SELECT job_type, status, COUNT(*) AS n,
                      MIN(created_at) AS oldest
               FROM job_execution_ledger
               WHERE status IN ('queued', 'retrying', 'processing', 'running')
               GROUP BY job_type, status"""
        ).fetchall()
        queues = []
        for r in rows:
            oldest_age = None
            if r["oldest"]:
                try:
                    oldest_dt = datetime.fromisoformat(r["oldest"].replace("Z", ""))
                    oldest_age = int((datetime.utcnow() - oldest_dt).total_seconds())
                except Exception:
                    logger.debug(
                        "runtime.queue_oldest_parse_failed",
                        extra={"oldest": r["oldest"]},
                        exc_info=True,
                    )
            queues.append({
                "job_type": r["job_type"],
                "status": r["status"],
                "depth": r["n"],
                "oldest_age_seconds": oldest_age,
            })
        return {"queues": queues}
    except Exception as e:
        logger.warning("queue_depths failed: %s", e)
        return {"queues": []}


# =========================================================================
# Route performance
# =========================================================================

def route_performance(*, limit: int = 20, order_by: str = "p95") -> dict:
    """
    Aggregated route metrics. Reads from request_log table if present,
    otherwise returns empty. Wire your actual metrics source here.
    """
    conn = get_conn()
    try:
        order_map = {
            "p95": "p95 DESC",
            "error_rate": "error_rate DESC",
            "requests": "requests DESC",
        }
        ordering = order_map.get(order_by, "p95 DESC")
        rows = conn.execute(
            f"""SELECT path, method,
                       COUNT(*) AS requests,
                       AVG(duration_ms) AS avg_ms,
                       MAX(duration_ms) AS p95,
                       SUM(CASE WHEN status_code >= 500 THEN 1 ELSE 0 END) AS errors,
                       ROUND(SUM(CASE WHEN status_code >= 500 THEN 1.0 ELSE 0 END)
                             / COUNT(*) * 100, 2) AS error_rate
                FROM request_log
                WHERE occurred_at > datetime('now','-24 hours')
                GROUP BY path, method
                ORDER BY {ordering}
                LIMIT ?""",
            (limit,),
        ).fetchall()
        return {"routes": [dict(r) for r in rows]}
    except Exception:
        return {"routes": [], "note": "request_log table not found"}


# =========================================================================
# System resources
# =========================================================================

def system_resources() -> dict:
    """CPU/memory/disk from psutil if available, otherwise stub."""
    try:
        import psutil
        vm = psutil.virtual_memory()
        disk = psutil.disk_usage("/")
        return {
            "cpu_pct": psutil.cpu_percent(interval=0.1),
            "memory_pct": vm.percent,
            "memory_used_mb": int(vm.used / 1024 / 1024),
            "memory_total_mb": int(vm.total / 1024 / 1024),
            "disk_pct": disk.percent,
            "disk_free_gb": round(disk.free / 1024 / 1024 / 1024, 1),
            "load_avg": list(os.getloadavg()) if hasattr(os, "getloadavg") else None,
        }
    except ImportError:
        return {
            "cpu_pct": None, "memory_pct": None, "disk_pct": None,
            "note": "psutil not installed",
        }


# =========================================================================
# Scheduler control
# =========================================================================

async def run_job_now(job_id: str) -> dict:
    """Dispatch locally or durably queue for the standalone scheduler leader."""
    from app.services.scheduler.jobs import (
        enqueue_job_run_request,
        trigger_job_now,
    )

    result = trigger_job_now(job_id)
    if result.get("status") != "not_started":
        return result
    return enqueue_job_run_request(job_id)


def job_history(job_id: str, limit: int = 20) -> dict:
    conn = get_conn()
    try:
        rows = conn.execute(
            """SELECT * FROM scheduler_run_log
               WHERE job_id = ?
               ORDER BY ran_at DESC LIMIT ?""",
            (job_id, limit),
        ).fetchall()
        return {"job_id": job_id, "history": [dict(r) for r in rows]}
    except Exception:
        return {"job_id": job_id, "history": [],
                "note": "scheduler_run_log table not present"}


# =========================================================================
# Cache clear
# =========================================================================

def clear_cache(tier: str) -> dict:
    """Clear specific cache tier. Tiers: route_responses, bh_products, creator_stats, memory, vector."""
    try:
        from app.services.cache import cache_clear

        prefix_map = {
            "route_responses": "admin_",
            "bh_products": "bh:",
            "creator_stats": "creator:",
            "memory": "memory:",
            "vector": "vec:",
        }
        prefix = prefix_map.get(tier)
        if not prefix:
            return {"error": f"unknown tier: {tier}"}
        deleted = cache_clear(prefix=prefix)
        return {"tier": tier, "keys_deleted": deleted}
    except Exception as e:
        logger.exception("clear_cache failed")
        return {"error": str(e), "tier": tier}
