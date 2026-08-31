"""Database-independent outbound alerts for failure-path observability.

This module deliberately imports only the Python standard library.  It is safe
to import from systemd ``OnFailure`` handlers even when application config,
database setup, migrations, or domain package initializers are broken.

Webhook URLs and signing secrets are read only from the process environment.
Neither value is returned to callers or written to logs.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import logging
import os
import re
import time
from datetime import datetime, timezone
from typing import Any, Callable
from urllib import error as urllib_error
from urllib import request as urllib_request
from urllib.parse import urlsplit


ENV_WEBHOOK_URL = "VKPI_ALERT_WEBHOOK_URL"
ENV_WEBHOOK_KIND = "VKPI_ALERT_WEBHOOK_KIND"
ENV_WEBHOOK_SECRET = "VKPI_ALERT_WEBHOOK_SECRET"
ENV_WEBHOOK_TIMEOUT_S = "VKPI_ALERT_WEBHOOK_TIMEOUT_S"
ENV_SILENCE_KEYS = "VKPI_ALERT_SILENCE_KEYS"

KINDS = ("feishu", "slack", "generic")
_DEFAULT_KIND = "generic"
_DEFAULT_TIMEOUT_S = 5.0
_MAX_TIMEOUT_S = 30.0
_TEXT_LIMIT = 3500
_SEVERITY_ICON = {"danger": "🔴", "warning": "🟠", "info": "🔵"}

logger = logging.getLogger("viltrox.stateless_alert")

# transport(payload_dict, timeout_s) -> (http_status, reason)
Transport = Callable[[dict[str, Any], float], tuple[int, str]]


class _NoRedirectHandler(urllib_request.HTTPRedirectHandler):
    """Reject redirects so alert POSTs cannot be rewritten or downgraded."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):  # type: ignore[no-untyped-def]
        return None


def _env_float(name: str, default: float) -> float:
    try:
        raw = os.environ.get(name, "").strip()
        return float(raw) if raw else float(default)
    except (TypeError, ValueError):
        return float(default)


def _webhook_url() -> str:
    """Return a validated HTTPS URL internally; never expose it in results."""

    raw = os.environ.get(ENV_WEBHOOK_URL, "").strip()
    if not raw:
        return ""
    try:
        parsed = urlsplit(raw)
    except ValueError:
        return ""
    if parsed.scheme.lower() != "https" or not parsed.hostname:
        return ""
    if parsed.username is not None or parsed.password is not None:
        return ""
    return raw


def webhook_kind() -> str:
    raw = os.environ.get(ENV_WEBHOOK_KIND, "").strip().lower()
    return raw if raw in KINDS else _DEFAULT_KIND


def silenced_keys() -> frozenset[str]:
    raw = os.environ.get(ENV_SILENCE_KEYS, "")
    return frozenset(part.strip() for part in raw.split(",") if part.strip())


def outbound_status() -> dict[str, Any]:
    """Return safe channel metadata without URL or secret material."""

    return {
        "configured": bool(_webhook_url()),
        "kind": webhook_kind(),
        "signed": bool(os.environ.get(ENV_WEBHOOK_SECRET, "").strip()),
    }


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _clip(value: Any, limit: int = _TEXT_LIMIT) -> str:
    text = str(value or "")
    return text if len(text) <= limit else text[: limit - 1] + "…"


def redact(message: str) -> str:
    """Remove webhook URL, host, signing secret, and arbitrary URLs from text."""

    text = str(message or "")
    url = os.environ.get(ENV_WEBHOOK_URL, "").strip()
    secret = os.environ.get(ENV_WEBHOOK_SECRET, "").strip()
    if url:
        text = text.replace(url, "<webhook-url>")
        try:
            host = urlsplit(url).netloc
        except ValueError:
            host = ""
        if host:
            text = text.replace(host, "<webhook-host>")
    if secret:
        text = text.replace(secret, "<webhook-secret>")
    return re.sub(r"https?://\S+", "<url>", text)[:300]


def _headline(event: dict[str, Any]) -> str:
    icon = _SEVERITY_ICON.get(str(event.get("severity") or "info"), "🔵")
    prefix = "[升级] " if event.get("escalated") else ""
    if event.get("event") == "recovery":
        icon, prefix = "🟢", "[恢复] "
    return f"{icon} {prefix}{_clip(event.get('title') or event.get('key') or 'vkpi alert', 500)}"


def build_payload(
    kind: str,
    event: dict[str, Any],
    *,
    secret: str = "",
    now_ts: int | None = None,
) -> dict[str, Any]:
    """Build a bounded Feishu, Slack, or generic webhook payload."""

    kind = kind if kind in KINDS else _DEFAULT_KIND
    headline = _headline(event)
    body = _clip(event.get("body"))
    key = _clip(event.get("key"), 240)
    severity = _clip(event.get("severity") or "info", 40)
    meta_line = f"key={key} severity={severity}"
    if event.get("consecutive"):
        meta_line += f" consecutive={int(event.get('consecutive') or 0)}"
    if event.get("alert_key"):
        meta_line += f" alert={_clip(event.get('alert_key'), 240)}"
    text = f"{headline}\n{body}\n{meta_line}".strip()
    if kind == "feishu":
        payload: dict[str, Any] = {"msg_type": "text", "content": {"text": text}}
        if secret:
            timestamp = int(now_ts if now_ts is not None else time.time())
            string_to_sign = f"{timestamp}\n{secret}"
            digest = hmac.new(
                string_to_sign.encode("utf-8"),
                b"",
                digestmod=hashlib.sha256,
            ).digest()
            payload["timestamp"] = str(timestamp)
            payload["sign"] = base64.b64encode(digest).decode("utf-8")
        return payload
    if kind == "slack":
        return {
            "text": headline,
            "blocks": [
                {"type": "section", "text": {"type": "mrkdwn", "text": f"*{headline}*\n{body}"}},
                {"type": "context", "elements": [{"type": "mrkdwn", "text": meta_line}]},
            ],
        }
    return {
        "source": "vkpi",
        "event": _clip(event.get("event") or "alert", 40),
        "key": key,
        "alert_key": event.get("alert_key"),
        "rule_key": event.get("rule_key"),
        "severity": severity,
        "title": _clip(event.get("title"), 500),
        "body": body,
        "escalated": bool(event.get("escalated")),
        "consecutive": int(event.get("consecutive") or 0),
        "sent_at": _iso(_utcnow()),
    }


def http_transport(payload: dict[str, Any], timeout_s: float) -> tuple[int, str]:
    url = _webhook_url()
    if not url:
        raise ValueError("webhook is not securely configured")
    data = json.dumps(payload, ensure_ascii=False, default=str).encode("utf-8")
    request = urllib_request.Request(
        url,
        data=data,
        method="POST",
        headers={"Content-Type": "application/json"},
    )
    opener = urllib_request.build_opener(_NoRedirectHandler())
    with opener.open(request, timeout=timeout_s) as response:  # noqa: S310 - validated HTTPS env only
        final_url = str(response.geturl() or "")
        # The handler above rejects HTTP redirects.  Retain this exact final-URL
        # assertion as a second boundary for custom handlers and test doubles.
        if final_url != url or urlsplit(final_url).scheme.lower() != "https":
            raise RuntimeError("webhook response URL changed unexpectedly")
        return int(getattr(response, "status", 200) or 200), "ok"


def deliver(payload: dict[str, Any], transport: Transport | None = None) -> dict[str, Any]:
    timeout_s = max(1.0, min(_MAX_TIMEOUT_S, _env_float(ENV_WEBHOOK_TIMEOUT_S, _DEFAULT_TIMEOUT_S)))
    send = transport or http_transport
    try:
        status, reason = send(payload, timeout_s)
        status = int(status)
    except urllib_error.HTTPError as exc:
        status = int(getattr(exc, "code", 0) or 0)
        logger.warning("stateless alert HTTP error status=%s", status or "unknown")
        return {"sent": False, "reason": "http_error", "status": status}
    except Exception as exc:
        logger.warning(
            "stateless alert delivery failed %s: %s",
            type(exc).__name__,
            redact(str(exc)),
        )
        return {"sent": False, "reason": "delivery_error", "error": type(exc).__name__}
    if 200 <= status < 300:
        return {"sent": True, "reason": "sent", "status": status}
    logger.warning("stateless alert non-2xx status=%s reason=%s", status, redact(reason))
    return {"sent": False, "reason": "http_error", "status": status}


def notify_stateless(
    *,
    key: str,
    title: str,
    body: str = "",
    severity: str = "danger",
    rule_key: str | None = None,
    transport: Transport | None = None,
) -> dict[str, Any]:
    """Send one DB-independent alert; timer cadence is the retry boundary."""

    normalized_key = _clip(key, 240).strip()
    base = {
        "configured": bool(_webhook_url()),
        "kind": webhook_kind(),
        "key": normalized_key,
    }
    if not base["configured"]:
        return {**base, "sent": False, "reason": "not_configured"}
    if normalized_key in silenced_keys():
        return {**base, "sent": False, "reason": "silenced"}
    event = {
        "event": "alert",
        "key": normalized_key,
        "rule_key": rule_key,
        "severity": severity,
        "title": title,
        "body": body,
        "escalated": False,
        "consecutive": 1,
    }
    payload = build_payload(
        webhook_kind(),
        event,
        secret=os.environ.get(ENV_WEBHOOK_SECRET, "").strip(),
    )
    result = deliver(payload, transport)
    logger.info(
        "stateless alert kind=%s sent=%s reason=%s",
        base["kind"],
        result.get("sent"),
        result.get("reason"),
    )
    return {**base, **result}


__all__ = [
    "ENV_SILENCE_KEYS",
    "ENV_WEBHOOK_KIND",
    "ENV_WEBHOOK_SECRET",
    "ENV_WEBHOOK_TIMEOUT_S",
    "ENV_WEBHOOK_URL",
    "KINDS",
    "Transport",
    "build_payload",
    "deliver",
    "http_transport",
    "notify_stateless",
    "outbound_status",
    "redact",
    "silenced_keys",
    "webhook_kind",
]
