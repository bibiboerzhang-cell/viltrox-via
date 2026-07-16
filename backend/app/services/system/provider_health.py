"""
services/system/provider_health.py — provider health probe and local status.

The probe functions are intentionally small and reusable by key rotation
sandbox tests. They avoid expensive usage queries on page load.
"""
from __future__ import annotations

import asyncio
import html
import os
import re
from datetime import datetime, timedelta
from typing import Any

import httpx

from app.core.logging import get_logger
from app.db.connection import get_conn
from app.services.auth.email import send_email
from app.services.system.secrets_admin import provider_env_keys, provider_key_prefix

logger = get_logger(__name__)

PROVIDERS = ["anthropic", "openai", "google", "apify", "youtube", "resend"]
SECURITY_NOTIFY_EMAIL = os.environ.get("SECURITY_NOTIFY_EMAIL", "jianboz@viltrox.com").strip()
_SECRET_QUERY_RE = re.compile(
    r"(?i)((?:[?&]|\b)(?:api[-_]?key|key|token|access_token)\s*=\s*)[^&\s,;\"']+"
)
_SECRET_HEADER_RE = re.compile(
    r"(?i)((?:x-goog-api-key|x-api-key)\s*['\"]?\s*[:=]\s*['\"]?)[^,\s}\"']+"
)
_BEARER_SECRET_RE = re.compile(r"(?i)(\bbearer\s+)[A-Za-z0-9._~+/=-]+")


def _redact_probe_error(
    value: Any,
    api_key: str = "",
    *,
    max_length: int = 160,
) -> str:
    text = str(value or "")
    if api_key:
        text = text.replace(api_key, "[redacted]")
    text = _SECRET_QUERY_RE.sub(r"\1[redacted]", text)
    text = _SECRET_HEADER_RE.sub(r"\1[redacted]", text)
    text = _BEARER_SECRET_RE.sub(r"\1[redacted]", text)
    return text[:max_length]


def _canonical_provider(provider: str) -> str:
    key = str(provider or "").strip().lower()
    if key == "gemini":
        return "google"
    return key


def utcnow() -> str:
    return datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")


def ensure_provider_status_schema() -> None:
    conn = get_conn()
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS provider_status (
            provider TEXT PRIMARY KEY,
            latest_status TEXT NOT NULL DEFAULT 'unknown',
            last_ok_at TEXT,
            last_error TEXT DEFAULT '',
            consecutive_failures INTEGER NOT NULL DEFAULT 0,
            alert_sent_at TEXT,
            quota_json TEXT NOT NULL DEFAULT '{}',
            updated_at TEXT NOT NULL DEFAULT ''
        )
        """
    )
    conn.commit()


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
        safe_error = _redact_probe_error(exc, key)
        logger.warning(
            "provider_health.probe_failed",
            extra={"provider": provider_key, "error": safe_error},
        )
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
                # Never put provider credentials in a URL.  Query strings are
                # routinely captured by httpx, proxy and application logs.
                headers={"x-goog-api-key": api_key},
            )
        elif provider == "apify":
            response = await client.get(
                "https://api.apify.com/v2/users/me",
                # Apify supports both forms and explicitly recommends Bearer
                # authentication because query tokens leak into URL logs.
                headers={"Authorization": f"Bearer {api_key}"},
            )
        elif provider == "youtube":
            response = await client.get(
                "https://www.googleapis.com/youtube/v3/channels",
                params={"part": "id", "id": "UC_x5XG1OV2P6uZZ5FSM9Ttw"},
                # Google recommends the API-key header because URL query
                # parameters are retained by access logs and URL scanners.
                headers={"x-goog-api-key": api_key},
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
    ensure_provider_status_schema()
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
    ensure_provider_status_schema()
    conn = get_conn()
    now = utcnow()
    row = conn.execute("SELECT consecutive_failures, alert_sent_at FROM provider_status WHERE provider = ?", (provider,)).fetchone()
    previous_failures = int(row["consecutive_failures"] or 0) if row else 0
    failures = 0 if ok else previous_failures + 1
    status = "healthy" if ok else ("down" if failures >= 3 else "degraded")
    safe_error = _redact_probe_error(error, max_length=500)
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
        (provider, status, now if ok else None, "" if ok else safe_error, failures, now),
    )
    conn.commit()


def should_send_down_alert(provider: str) -> bool:
    provider = _canonical_provider(provider)
    ensure_provider_status_schema()
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
    ensure_provider_status_schema()
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
    safe_error = html.escape(_redact_probe_error(error, max_length=500) or "unknown")
    try:
        send_email(
            SECURITY_NOTIFY_EMAIL,
            f"V-OS provider down: {provider}",
            (
                "<p>Provider health probe marked a provider as down.</p>"
                f"<p><b>Provider:</b> {provider}</p>"
                f"<p><b>Error:</b> {safe_error}</p>"
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
