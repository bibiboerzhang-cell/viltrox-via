"""Rollback window helpers for legacy KOL commit batches."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from app.domains.legacy_import.legacy_import_audit import _text


def utcnow() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def utcnow_dt() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def format_ts(value: datetime | None) -> str | None:
    if not value:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.isoformat(timespec="seconds")


def parse_ts(value: Any) -> datetime | None:
    if not value:
        return None
    if isinstance(value, datetime):
        return value.replace(tzinfo=None)
    text = str(value).strip()
    if not text:
        return None
    if text.endswith("Z"):
        text = text[:-1]
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is not None:
        parsed = parsed.astimezone(timezone.utc).replace(tzinfo=None)
    return parsed


def rollback_until_for_policy(now: datetime, policy: str) -> datetime | None:
    if policy == "manual_24h":
        return now + timedelta(hours=24)
    if policy == "admin_only":
        return None
    if policy == "no_rollback":
        return None
    return now + timedelta(minutes=30)


def rollback_window(batch: dict[str, Any], *, force: bool = False) -> dict[str, Any]:
    policy = _text(batch.get("rollback_policy")) or "manual_30m"
    rollback_until = parse_ts(batch.get("rollback_until"))
    now = utcnow_dt()
    if force:
        return {
            "allowed": True,
            "forced": True,
            "policy": policy,
            "rollback_until": format_ts(rollback_until),
            "reason": "force_rollback",
        }
    if policy == "no_rollback":
        return {
            "allowed": False,
            "forced": False,
            "policy": policy,
            "rollback_until": format_ts(rollback_until),
            "reason": "no_rollback_policy",
        }
    if rollback_until and now > rollback_until:
        return {
            "allowed": False,
            "forced": False,
            "policy": policy,
            "rollback_until": format_ts(rollback_until),
            "reason": "rollback_window_expired",
        }
    return {
        "allowed": True,
        "forced": False,
        "policy": policy,
        "rollback_until": format_ts(rollback_until),
        "reason": "ok" if rollback_until else "no_window_configured",
    }
