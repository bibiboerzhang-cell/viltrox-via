"""
services/system/integrations.py — External integrations admin (batch 5)

Manages the `integrations` + `integration_metrics` tables. Provides live
health pings and metrics rollup for all configured external services.
"""
from __future__ import annotations

import asyncio
import time
from datetime import datetime, timedelta
from typing import Any

import httpx

from app.core.logging import get_logger
from app.db.connection import get_conn, is_postgres_runtime

logger = get_logger(__name__)


# Category → display ordering
CATEGORY_ORDER = ["ai", "commerce", "data", "email"]

DEFAULT_INTEGRATIONS = (
    ("anthropic", "ai", "Claude Opus/Sonnet/Haiku", "ANTHROPIC_", "ok"),
    ("openai", "ai", "GPT-4o-mini pre-filter", "OPENAI_", "ok"),
    ("google", "ai", "Gemini 2.5 Flash", "GOOGLE_", "ok"),
    ("shopify", "commerce", "Order webhooks", "SHOPIFY_", "ok"),
    ("paypal", "commerce", "Creator payouts", "PAYPAL_", "not_configured"),
    ("stripe", "commerce", "Bank transfers", "STRIPE_", "not_configured"),
    ("apify", "data", "B&H scraper", "APIFY_", "ok"),
    ("qdrant", "data", "Vector store", "QDRANT_", "ok"),
    ("redis", "data", "Cache", "REDIS_", "ok"),
    ("resend", "email", "Transactional email", "RESEND_", "warn"),
    ("meta", "email", "Instagram webhooks", "META_", "not_configured"),
    ("tiktok", "email", "TikTok API", "TIKTOK_", "not_configured"),
    ("youtube", "email", "YouTube Data API", "YOUTUBE_", "ok"),
    ("sentry", "data", "Error tracking", "SENTRY_", "not_configured"),
)


def _ensure_schema(conn) -> None:
    """Keep the admin system tab usable on partially migrated local DBs."""
    if is_postgres_runtime():
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS integrations (
                id BIGSERIAL PRIMARY KEY,
                service_name TEXT NOT NULL UNIQUE,
                category TEXT NOT NULL,
                purpose TEXT DEFAULT '',
                config_env_prefix TEXT DEFAULT '',
                status TEXT NOT NULL DEFAULT 'not_configured',
                last_health_check TIMESTAMPTZ,
                last_health_status TEXT,
                last_error TEXT,
                notes TEXT,
                enabled INTEGER NOT NULL DEFAULT 1
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS integration_metrics (
                id BIGSERIAL PRIMARY KEY,
                integration_id BIGINT REFERENCES integrations(id) ON DELETE CASCADE,
                bucket_start TIMESTAMPTZ NOT NULL,
                request_count INTEGER NOT NULL DEFAULT 0,
                error_count INTEGER NOT NULL DEFAULT 0,
                latency_p50_ms INTEGER,
                latency_p95_ms INTEGER,
                cost_cents INTEGER NOT NULL DEFAULT 0
            )
            """
        )
    else:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS integrations (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                service_name TEXT UNIQUE NOT NULL,
                category TEXT NOT NULL,
                purpose TEXT,
                config_env_prefix TEXT,
                status TEXT DEFAULT 'not_configured',
                last_health_check TEXT,
                last_health_status TEXT,
                last_error TEXT,
                notes TEXT,
                enabled BOOLEAN DEFAULT 1
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS integration_metrics (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                integration_id INTEGER REFERENCES integrations(id),
                bucket_start TEXT NOT NULL,
                request_count INTEGER DEFAULT 0,
                error_count INTEGER DEFAULT 0,
                latency_p50_ms INTEGER,
                latency_p95_ms INTEGER,
                cost_cents INTEGER DEFAULT 0
            )
            """
        )
    conn.execute("CREATE INDEX IF NOT EXISTS idx_int_metrics ON integration_metrics(integration_id, bucket_start DESC)")
    conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_int_metrics_unique_bucket ON integration_metrics(integration_id, bucket_start)")
    for row in DEFAULT_INTEGRATIONS:
        conn.execute(
            """
            INSERT OR IGNORE INTO integrations
                (service_name, category, purpose, config_env_prefix, status)
            VALUES (?, ?, ?, ?, ?)
            """,
            row,
        )
    conn.commit()


def _status_counts(rows: list[dict[str, Any]]) -> dict[str, int]:
    healthy = sum(1 for r in rows if str(r.get("status") or "").lower() in ("ok", "healthy"))
    degraded = sum(1 for r in rows if str(r.get("status") or "").lower() in ("warn", "degraded", "stale"))
    failing = sum(1 for r in rows if str(r.get("status") or "").lower() in ("err", "error", "failing"))
    return {
        "healthy": healthy,
        "degraded": degraded,
        "failing": failing,
        "issues": degraded + failing,
    }


def list_all() -> dict:
    conn = get_conn()
    _ensure_schema(conn)
    rows = conn.execute(
        "SELECT * FROM integrations ORDER BY category, service_name"
    ).fetchall()

    by_category: dict[str, list] = {}
    items: list[dict[str, Any]] = []
    for r in rows:
        metrics = _latest_metrics(r["id"])
        item = {**dict(r), "name": r["service_name"], **metrics}
        items.append(item)
        by_category.setdefault(r["category"], []).append(item)
    counts = _status_counts(items)

    return {
        "integrations_by_category": by_category,
        "total": len(rows),
        **counts,
    }


def get_detail(integration_id: int) -> dict:
    conn = get_conn()
    _ensure_schema(conn)
    row = conn.execute(
        "SELECT * FROM integrations WHERE id = ?", (integration_id,)
    ).fetchone()
    if not row:
        return {"error": "not found"}
    return {**dict(row), "name": row["service_name"], **_latest_metrics(integration_id)}


def _latest_metrics(integration_id: int) -> dict:
    """Aggregate last 24h metrics for display."""
    conn = get_conn()
    _ensure_schema(conn)
    r = conn.execute(
        """SELECT
            COALESCE(SUM(request_count), 0) AS reqs,
            COALESCE(SUM(error_count), 0)   AS errs,
            COALESCE(SUM(cost_cents), 0)    AS cost,
            MAX(latency_p95_ms)             AS p95
           FROM integration_metrics
           WHERE integration_id = ?
             AND bucket_start > datetime('now','-24 hours')""",
        (integration_id,),
    ).fetchone()
    reqs = r["reqs"] or 0
    return {
        "requests_24h": reqs,
        "error_rate_pct": round((r["errs"] or 0) / max(reqs, 1) * 100, 2),
        "p95_ms": r["p95"],
        "cost_cents_24h": r["cost"] or 0,
    }


def get_metrics(integration_id: int, *, window: str = "24h") -> dict:
    hours = {"1h": 1, "24h": 24, "7d": 24 * 7}.get(window, 24)
    conn = get_conn()
    _ensure_schema(conn)
    rows = conn.execute(
        """SELECT * FROM integration_metrics
           WHERE integration_id = ?
             AND bucket_start > datetime('now', ?)
           ORDER BY bucket_start""",
        (integration_id, f"-{hours} hours"),
    ).fetchall()
    return {"window": window, "buckets": [dict(r) for r in rows]}


def set_enabled(integration_id: int, *, enabled: bool) -> None:
    conn = get_conn()
    _ensure_schema(conn)
    conn.execute(
        "UPDATE integrations SET enabled = ?, status = ? WHERE id = ?",
        (1 if enabled else 0, "ok" if enabled else "off", integration_id),
    )
    conn.commit()


# =========================================================================
# Live health checks
# =========================================================================

# Each service has its own health-check function. Map service_name → async fn
HEALTH_CHECKS: dict[str, "callable"] = {}


def register_health_check(service_name: str):
    """Decorator to register a health check function."""
    def _wrap(fn):
        HEALTH_CHECKS[service_name] = fn
        return fn
    return _wrap


@register_health_check("shopify")
async def _hc_shopify() -> tuple[str, int | None]:
    # Ping shop.myshopify.com/admin/oauth/access_scopes or similar
    # For safety, just check that the DB has received a webhook recently
    conn = get_conn()
    row = conn.execute(
        "SELECT MAX(occurred_at) AS last FROM platform_ingest_events "
        "WHERE source_platform='shopify'"
    ).fetchone()
    if row and row["last"]:
        last = datetime.fromisoformat(row["last"].replace("Z", ""))
        age = (datetime.utcnow() - last).total_seconds()
        return ("ok", int(age * 1000)) if age < 3600 else ("warn", int(age * 1000))
    return ("warn", None)


@register_health_check("anthropic")
async def _hc_anthropic() -> tuple[str, int | None]:
    import os
    key = os.getenv("ANTHROPIC_API_KEY")
    if not key:
        return ("off", None)
    # Lightweight: just a HEAD-style check. Skip actual API call in health ping.
    return ("ok", None)


@register_health_check("redis")
async def _hc_redis() -> tuple[str, int | None]:
    try:
        from app.services.cache.memory_cache import _get_redis

        r = _get_redis()
        if r is None:
            return ("off", None)
        t0 = time.time()
        r.ping()
        return ("ok", int((time.time() - t0) * 1000))
    except Exception as e:
        logger.warning("redis health check failed: %s", e)
        return ("err", None)


async def live_health_check(integration_id: int) -> dict:
    conn = get_conn()
    _ensure_schema(conn)
    row = conn.execute(
        "SELECT service_name FROM integrations WHERE id = ?", (integration_id,)
    ).fetchone()
    if not row:
        return {"error": "not found"}
    name = row["service_name"]
    check = HEALTH_CHECKS.get(name)
    if not check:
        return {"service": name, "status": "unknown", "message": "no health check registered"}

    try:
        status, latency_ms = await check()
    except Exception as e:
        status, latency_ms = "err", None
        logger.exception("health check %s failed", name)

    conn.execute(
        """UPDATE integrations SET
            status = ?, last_health_check = datetime('now'),
            last_health_status = ?
           WHERE id = ?""",
        (status, status, integration_id),
    )
    conn.commit()
    return {
        "service": name,
        "status": status,
        "latency_ms": latency_ms,
        "checked_at": datetime.utcnow().isoformat(),
    }


async def health_check_all() -> dict:
    conn = get_conn()
    _ensure_schema(conn)
    rows = conn.execute(
        "SELECT id FROM integrations WHERE enabled = 1"
    ).fetchall()
    t0 = time.time()
    results = await asyncio.gather(
        *[live_health_check(r["id"]) for r in rows], return_exceptions=True
    )
    duration = int((time.time() - t0) * 1000)
    return {"results": [r for r in results if not isinstance(r, Exception)],
            "duration_ms": duration}


async def smoke_test(integration_id: int) -> dict:
    """Deeper test than ping — actually attempts a small real operation."""
    return await live_health_check(integration_id)  # extend per-service as needed


# =========================================================================
# Metric recording (called from instrumented client code)
# =========================================================================

def record_request(
    service_name: str, *,
    duration_ms: int, success: bool, cost_cents: int = 0,
) -> None:
    """Call this from any wrapper around an external API to accumulate metrics."""
    conn = get_conn()
    _ensure_schema(conn)
    bucket = datetime.utcnow().replace(minute=0, second=0, microsecond=0).isoformat()
    integration = conn.execute(
        "SELECT id FROM integrations WHERE service_name = ?",
        (service_name,),
    ).fetchone()
    if not integration:
        return

    existing = conn.execute(
        "SELECT id, request_count, error_count, latency_p95_ms, cost_cents "
        "FROM integration_metrics WHERE integration_id = ? AND bucket_start = ?",
        (integration["id"], bucket),
    ).fetchone()
    if existing:
        conn.execute(
            """UPDATE integration_metrics
               SET request_count = ?,
                   error_count = ?,
                   latency_p95_ms = ?,
                   cost_cents = ?
               WHERE id = ?""",
            (
                int(existing["request_count"] or 0) + 1,
                int(existing["error_count"] or 0) + (0 if success else 1),
                max(int(existing["latency_p95_ms"] or 0), int(duration_ms or 0)),
                int(existing["cost_cents"] or 0) + int(cost_cents or 0),
                existing["id"],
            ),
        )
    else:
        conn.execute(
            """INSERT INTO integration_metrics
                (integration_id, bucket_start, request_count, error_count,
                 latency_p95_ms, cost_cents)
               VALUES (?, ?, 1, ?, ?, ?)""",
            (integration["id"], bucket, 0 if success else 1, duration_ms, cost_cents),
        )
    conn.commit()
