"""
services/system/provider_health.py — provider health probe and local status.

The probe functions are intentionally small and reusable by key rotation
sandbox tests. They avoid expensive usage queries on page load.
"""
from __future__ import annotations

import asyncio
import os
from datetime import datetime, timedelta
from typing import Any

import httpx

from app.core.logging import get_logger
from app.db.connection import get_conn
from app.services.auth.email import send_email
from app.services.system.secrets_admin import provider_env_keys, provider_key_prefix

logger = get_logger(__name__)

PROVIDERS = ["anthropic", "openai", "google", "apify", "resend"]
SECURITY_NOTIFY_EMAIL = os.environ.get("SECURITY_NOTIFY_EMAIL", "jianboz@viltrox.com").strip()


def _canonical_provider(provider: str) -> str:
    key = str(provider or "").strip().lower()
    if key == "gemini":
        return "google"
    return key


def utcnow() -> str:
    return datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")


async def probe_provider(provider: str, api_key: str | None = None) -> dict[str, Any]:
    provider_key = _canonical_provider(provider)
    if provider_key not in PROVIDERS:
        return {"provider": provider_key, "status": "unknown", "ok": False, "error": "unknown provider"}
    key = str(api_key if api_key is not None else _provider_key_from_env(provider_key) or "").strip()
    if not key:
        return {"provider": provider_key, "status": "down", "ok": False, "error": "missing key"}
    try:
        return await _probe_provider_http(provider_key, key)
    except httpx.TimeoutException:
        return {"provider": provider_key, "status": "down", "ok": False, "error": "probe timeout"}
    except httpx.HTTPError as exc:
        return {"provider": provider_key, "status": "down", "ok": False, "error": f"probe http error: {exc.__class__.__name__}"}
    except Exception as exc:
        logger.warning("provider_health.probe_failed", extra={"provider": provider_key, "error": str(exc)[:160]})
        return {"provider": provider_key, "status": "down", "ok": False, "error": exc.__class__.__name__}


def _provider_key_from_env(provider: str) -> str:
    env_key, _previous = provider_env_keys(provider)
    return os.environ.get(env_key, "").strip()


async def _probe_provider_http(provider: str, api_key: str) -> dict[str, Any]:
    timeout = httpx.Timeout(12.0, connect=5.0)
    async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as client:
        if provider == "anthropic":
            response = await client.get(
                "https://api.anthropic.com/v1/models",
                headers={"x-api-key": api_key, "anthropic-version": "2023-06-01"},
            )
        elif provider == "openai":
            response = await client.get(
                "https://api.openai.com/v1/models",
                headers={"Authorization": f"Bearer {api_key}"},
            )
        elif provider == "google":
            response = await client.get(
                "https://generativelanguage.googleapis.com/v1beta/models",
                params={"key": api_key},
            )
        elif provider == "apify":
            response = await client.get(
                "https://api.apify.com/v2/users/me",
                params={"token": api_key},
            )
        elif provider == "resend":
            response = await client.get(
                "https://api.resend.com/domains",
                headers={"Authorization": f"Bearer {api_key}"},
            )
        else:
            return {"provider": provider, "status": "unknown", "ok": False, "error": "unknown provider"}
    if 200 <= response.status_code < 300:
        return {"provider": provider, "status": "healthy", "ok": True, "http_status": response.status_code, "error": ""}
    if response.status_code in {401, 403}:
        return {"provider": provider, "status": "down", "ok": False, "http_status": response.status_code, "error": "invalid credentials"}
    if response.status_code in {408, 409, 425, 429} or 500 <= response.status_code < 600:
        return {"provider": provider, "status": "degraded", "ok": False, "http_status": response.status_code, "error": f"provider unavailable or limited ({response.status_code})"}
    return {"provider": provider, "status": "down", "ok": False, "http_status": response.status_code, "error": f"unexpected status {response.status_code}"}


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
    provider = _canonical_provider(provider)
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
    provider = _canonical_provider(provider)
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


def mark_alert_sent(provider: str) -> None:
    get_conn().execute(
        "UPDATE provider_status SET alert_sent_at = ?, updated_at = ? WHERE provider = ?",
        (utcnow(), utcnow(), _canonical_provider(provider)),
    )
    get_conn().commit()


def _send_down_alert(provider: str, error: str) -> None:
    if not SECURITY_NOTIFY_EMAIL:
        return
    if not should_send_down_alert(provider):
        return
    try:
        send_email(
            SECURITY_NOTIFY_EMAIL,
            f"V-OS provider down: {provider}",
            (
                "<p>Provider health probe marked a provider as down.</p>"
                f"<p><b>Provider:</b> {provider}</p>"
                f"<p><b>Error:</b> {error or 'unknown'}</p>"
                "<p>Alert frequency is capped at once per provider every 6 hours.</p>"
            ),
        )
        mark_alert_sent(provider)
    except Exception:
        logger.warning("provider_health.alert_failed", exc_info=True, extra={"provider": provider})


async def run_provider_health_check() -> dict[str, Any]:
    results: list[dict[str, Any]] = []
    for provider in PROVIDERS:
        result = await probe_provider(provider)
        ok = bool(result.get("ok"))
        error = str(result.get("error") or "")
        record_provider_probe(provider, ok, error)
        if not ok:
            row = get_conn().execute(
                "SELECT latest_status FROM provider_status WHERE provider = ?",
                (provider,),
            ).fetchone()
            if row and str(row["latest_status"]) == "down":
                _send_down_alert(provider, error)
        results.append(result)
        await asyncio.sleep(0)
    return {"ok": all(bool(item.get("ok")) for item in results), "providers": results}
