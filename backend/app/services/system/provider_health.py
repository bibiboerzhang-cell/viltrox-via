"""
services/system/provider_health.py — provider health probe and local status.

The probe functions are intentionally small and reusable by key rotation
sandbox tests. They avoid expensive usage queries on page load.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta
from typing import Any

from app.core.logging import get_logger
from app.db.connection import get_conn
from app.services.system.secrets_admin import provider_key_prefix

logger = get_logger(__name__)

PROVIDERS = ["anthropic", "openai", "google", "apify", "resend"]


def utcnow() -> str:
    return datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")


async def probe_provider(provider: str, api_key: str | None = None) -> dict[str, Any]:
    provider_key = str(provider or "").lower()
    # Real provider calls are wired here so key rotation can reuse one path.
    # The current implementation is conservative: it validates configuration
    # presence and leaves network smoke calls to provider-specific follow-up.
    if provider_key not in PROVIDERS:
        return {"provider": provider_key, "status": "unknown", "ok": False, "error": "unknown provider"}
    if api_key is not None and not str(api_key).strip():
        return {"provider": provider_key, "status": "down", "ok": False, "error": "empty key"}
    await asyncio.sleep(0)
    return {"provider": provider_key, "status": "unknown", "ok": True, "error": ""}


def seed_provider_status() -> None:
    conn = get_conn()
    for provider in PROVIDERS:
        conn.execute(
            """
            INSERT INTO provider_status (provider, latest_status, consecutive_failures, updated_at)
            VALUES (?, 'unknown', 0, ?)
            ON CONFLICT(provider) DO NOTHING
            """,
            (provider, utcnow()),
        )
    conn.commit()


def list_provider_status() -> dict[str, Any]:
    conn = get_conn()
    try:
        seed_provider_status()
        rows = conn.execute("SELECT * FROM provider_status ORDER BY provider").fetchall()
        providers = []
        for row in rows:
            item = dict(row)
            item["key_prefix"] = provider_key_prefix(str(item.get("provider") or ""))
            providers.append(item)
        return {"providers": providers}
    except Exception:
        logger.warning("provider_status.read_failed", exc_info=True)
        return {"providers": [{"provider": p, "latest_status": "unknown"} for p in PROVIDERS]}


def record_provider_probe(provider: str, ok: bool, error: str = "") -> None:
    conn = get_conn()
    now = utcnow()
    row = conn.execute("SELECT consecutive_failures, alert_sent_at FROM provider_status WHERE provider = ?", (provider,)).fetchone()
    previous_failures = int(row["consecutive_failures"] or 0) if row else 0
    failures = 0 if ok else previous_failures + 1
    status = "healthy" if ok else ("down" if failures >= 3 else "degraded")
    conn.execute(
        """
        INSERT INTO provider_status
            (provider, latest_status, last_ok_at, last_error, consecutive_failures, updated_at)
        VALUES (?, ?, ?, ?, ?, ?)
        ON CONFLICT(provider) DO UPDATE SET
            latest_status = excluded.latest_status,
            last_ok_at = CASE WHEN excluded.last_ok_at IS NOT NULL THEN excluded.last_ok_at ELSE provider_status.last_ok_at END,
            last_error = excluded.last_error,
            consecutive_failures = excluded.consecutive_failures,
            updated_at = excluded.updated_at
        """,
        (provider, status, now if ok else None, "" if ok else error[:500], failures, now),
    )
    conn.commit()


def should_send_down_alert(provider: str) -> bool:
    conn = get_conn()
    row = conn.execute("SELECT latest_status, alert_sent_at FROM provider_status WHERE provider = ?", (provider,)).fetchone()
    if not row or row["latest_status"] != "down":
        return False
    last = str(row["alert_sent_at"] or "")
    if not last:
        return True
    try:
        last_dt = datetime.strptime(last.replace("Z", ""), "%Y-%m-%dT%H:%M:%S")
        return datetime.utcnow() - last_dt >= timedelta(hours=6)
    except Exception:
        return True
