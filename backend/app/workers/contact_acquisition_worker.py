"""Bounded worker entrypoint for provider-free contact L0 reconciliation.

This cycle is attached to the dedicated Redis worker runtime as an independent
low-frequency task.  It is feature-flagged, bounded and schema-aware; it never
enters the provider job dispatcher or acquires a provider execution claim.
"""
from __future__ import annotations

import asyncio
import os
from typing import Any

from app.core.logging import get_logger
from app.core.release_validation import release_validation_active
from app.db.connection import db_connection_sync_reusing_scope, get_conn


logger = get_logger(__name__)

ENABLED_ENV = "VKPI_CONTACT_ACQUISITION_L0_ENABLED"
BRAND_SCOPE_ENV = "VKPI_CONTACT_ACQUISITION_BRAND_SCOPE"
CADENCE_ENV = "VKPI_CONTACT_ACQUISITION_CADENCE_SECONDS"
BATCH_LIMIT_ENV = "VKPI_CONTACT_ACQUISITION_BATCH_LIMIT"
DEFAULT_CADENCE_SECONDS = 300
DEFAULT_BATCH_LIMIT = 50


def _enabled() -> bool:
    return str(os.getenv(ENABLED_ENV, "0")).strip().lower() in {"1", "true", "yes", "on"}


def _bounded_int(name: str, default: int, *, minimum: int, maximum: int) -> int:
    try:
        value = int(os.getenv(name, str(default)))
    except (TypeError, ValueError):
        value = default
    return max(minimum, min(maximum, value))


def cadence_seconds() -> int:
    return _bounded_int(CADENCE_ENV, DEFAULT_CADENCE_SECONDS, minimum=30, maximum=3600)


def batch_limit() -> int:
    return _bounded_int(BATCH_LIMIT_ENV, DEFAULT_BATCH_LIMIT, minimum=1, maximum=500)


def _schema_available(conn: Any) -> bool:
    try:
        conn.execute("SELECT 1 FROM vkpi_kol_contact_acquisition_queue LIMIT 1").fetchone()
        return True
    except Exception:
        try:
            conn.rollback()
        except Exception as rollback_exc:
            logger.debug(
                "contact acquisition schema probe rollback failed error_type=%s",
                type(rollback_exc).__name__,
            )
        return False


def run_once(
    *,
    brand_scope: str,
    limit: int = 100,
    seed_existing: bool = True,
    conn: Any | None = None,
) -> dict[str, Any]:
    from app.domains.kol.contact_acquisition_queue import (
        reconcile_pending_contact_acquisition,
        seed_existing_contact_acquisition_queue,
    )

    seeded = (
        seed_existing_contact_acquisition_queue(limit=limit, conn=conn)
        if seed_existing
        else {"queued": 0}
    )
    reconciled = reconcile_pending_contact_acquisition(
        brand_scope=brand_scope,
        limit=limit,
        conn=conn,
    )
    return {
        "status": str(reconciled.get("status") or "completed"),
        "seeded": int(seeded.get("queued") or 0),
        "processed": int(reconciled.get("processed") or 0),
        "state_counts": dict(reconciled.get("state_counts") or {}),
        "priority_tier_counts": dict(reconciled.get("priority_tier_counts") or {}),
        "provider_calls": False,
        "website_crawls": False,
        "messages_sent": False,
    }


def _safe_result(status: str, reason: str) -> dict[str, Any]:
    return {
        "status": status,
        "reason": reason,
        "processed": 0,
        "provider_calls": False,
        "website_crawls": False,
        "messages_sent": False,
    }


def _run_enabled_cycle(*, scope: str, db: Any) -> dict[str, Any]:
    if not scope:
        return _safe_result("blocked", "brand_scope_unconfigured")
    if not _schema_available(db):
        return _safe_result("skipped", "queue_schema_unavailable")
    try:
        return run_once(
            brand_scope=scope,
            limit=batch_limit(),
            seed_existing=True,
            conn=db,
        )
    except ValueError:
        return _safe_result("blocked", "brand_scope_invalid")
    except Exception as exc:
        logger.warning("contact acquisition cycle failed error_type=%s", type(exc).__name__)
        return _safe_result("error", "cycle_failed")


def run_configured_cycle(*, conn: Any | None = None) -> dict[str, Any]:
    """Run one activated cycle, otherwise return an observable safe skip.

    Standalone worker calls own one bounded reusable DB scope.  Tests/callers
    that explicitly supply a connection retain ownership of that connection.
    """

    if not _enabled():
        return _safe_result("disabled", "feature_flag_disabled")
    if release_validation_active():
        return _safe_result("skipped", "release_validation_fenced")
    scope = str(os.getenv(BRAND_SCOPE_ENV, "")).strip().lower()
    if not scope:
        return _safe_result("blocked", "brand_scope_unconfigured")
    if conn is not None:
        return _run_enabled_cycle(scope=scope, db=conn)
    with db_connection_sync_reusing_scope() as scoped_conn:
        db = scoped_conn if scoped_conn is not None else get_conn()
        return _run_enabled_cycle(scope=scope, db=db)


async def periodic_cycle_loop(
    stop_event: asyncio.Event,
    *,
    cadence: int | None = None,
    cycle: Any = None,
) -> None:
    """Long-lived bounded cadence loop used by ``worker_main``."""

    interval = max(1, int(cadence or cadence_seconds()))
    cycle_fn = cycle or run_configured_cycle
    while not stop_event.is_set():
        try:
            result = await asyncio.to_thread(cycle_fn)
        except Exception as exc:
            logger.warning("contact acquisition periodic cycle failed error_type=%s", type(exc).__name__)
            result = {"status": "error", "processed": 0, "seeded": 0}
        logger.info(
            "contact acquisition cycle status=%s processed=%s seeded=%s",
            result.get("status"),
            int(result.get("processed") or 0),
            int(result.get("seeded") or 0),
        )
        try:
            await asyncio.wait_for(stop_event.wait(), timeout=interval)
        except asyncio.TimeoutError:
            continue


__all__ = [
    "ENABLED_ENV",
    "BRAND_SCOPE_ENV",
    "CADENCE_ENV",
    "BATCH_LIMIT_ENV",
    "run_once",
    "run_configured_cycle",
    "periodic_cycle_loop",
]
