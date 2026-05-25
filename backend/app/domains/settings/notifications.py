"""Per-staff notification settings for V-KPI.

v3 only stores preferences. No outbound email, Slack, WeChat, or in-app push is
sent from this module.
"""
from __future__ import annotations

import json
import re
from datetime import datetime
from typing import Any

from app.db.connection import get_conn, is_postgres_runtime
from app.domains import audit
from app.domains.access import scope
from app.domains.settings.schema_notifications import ensure_vkpi_notification_settings_schema
from app.domains.projects.workflow import staff_id as resolve_staff_id

DEFAULT_NOTIFICATION_SETTINGS: dict[str, Any] = {
    "email_enabled": False,
    "in_app_enabled": True,
    "slack_enabled": False,
    "wechat_enabled": False,
    "daily_digest_enabled": True,
    "weekly_summary_enabled": True,
    "stalled_project_enabled": True,
    "claim_activity_enabled": True,
    "attribution_alert_enabled": True,
    "cost_alert_enabled": False,
    "system_alert_enabled": True,
    "quiet_hours_start": "22:00",
    "quiet_hours_end": "08:00",
    "timezone": "Asia/Shanghai",
    "preferences_json": {},
}

_TIME_PATTERN = re.compile(r"^(?:[01]\d|2[0-3]):[0-5]\d$")


def _utcnow() -> str:
    return datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")


def _json(value: Any) -> str:
    return json.dumps(value or {}, ensure_ascii=False, default=str)

def _db_bool(value: Any) -> bool | int:
    return bool(value) if is_postgres_runtime() else (1 if value else 0)


def _load_json(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    try:
        parsed = json.loads(str(value or "{}"))
        return parsed if isinstance(parsed, dict) else {}
    except (TypeError, ValueError, json.JSONDecodeError):
        return {}


def _bool(value: Any, default: bool = False) -> bool:
    if value is None:
        return default
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "on", "enabled"}
    return bool(value)


def _int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _clean_time(value: Any, default: str) -> str:
    text = str(value or "").strip()
    return text if _TIME_PATTERN.match(text) else default


def _target_staff_id(staff: dict[str, Any] | None, requested_staff_id: int | None = None) -> int:
    actor = resolve_staff_id(staff)
    requested = _int(requested_staff_id, 0)
    if requested and requested != actor and not scope.can_view_all(staff):
        raise scope.ScopeDenied("notification settings scope denied")
    target = requested or actor
    if not target:
        raise scope.ScopeDenied("missing staff context")
    return int(target)


def _normalize(payload: dict[str, Any], existing: dict[str, Any] | None = None) -> dict[str, Any]:
    base = dict(DEFAULT_NOTIFICATION_SETTINGS)
    if existing:
        base.update(existing)

    def flag(name: str) -> bool:
        camel = "".join([name.split("_")[0], *[part.capitalize() for part in name.split("_")[1:]]])
        return _bool(payload.get(name, payload.get(camel, base.get(name))), bool(base.get(name)))

    extra = _load_json(payload.get("preferences") or payload.get("preferences_json") or base.get("preferences_json") or {})
    timezone = str(payload.get("timezone", base.get("timezone")) or DEFAULT_NOTIFICATION_SETTINGS["timezone"]).strip()[:80]
    if not timezone:
        timezone = str(DEFAULT_NOTIFICATION_SETTINGS["timezone"])
    return {
        "email_enabled": flag("email_enabled"),
        "in_app_enabled": flag("in_app_enabled"),
        "slack_enabled": flag("slack_enabled"),
        "wechat_enabled": flag("wechat_enabled"),
        "daily_digest_enabled": flag("daily_digest_enabled"),
        "weekly_summary_enabled": flag("weekly_summary_enabled"),
        "stalled_project_enabled": flag("stalled_project_enabled"),
        "claim_activity_enabled": flag("claim_activity_enabled"),
        "attribution_alert_enabled": flag("attribution_alert_enabled"),
        "cost_alert_enabled": flag("cost_alert_enabled"),
        "system_alert_enabled": flag("system_alert_enabled"),
        "quiet_hours_start": _clean_time(
            payload.get("quiet_hours_start", payload.get("quietHoursStart", base.get("quiet_hours_start"))),
            str(DEFAULT_NOTIFICATION_SETTINGS["quiet_hours_start"]),
        ),
        "quiet_hours_end": _clean_time(
            payload.get("quiet_hours_end", payload.get("quietHoursEnd", base.get("quiet_hours_end"))),
            str(DEFAULT_NOTIFICATION_SETTINGS["quiet_hours_end"]),
        ),
        "timezone": timezone,
        "preferences_json": extra,
    }


def _row_to_payload(row: Any) -> dict[str, Any]:
    if not row:
        return {}
    item = dict(row)
    extra = _load_json(item.get("preferences_json"))
    settings = {
        **extra,
        "email_enabled": bool(item.get("email_enabled")),
        "in_app_enabled": bool(item.get("in_app_enabled")),
        "slack_enabled": bool(item.get("slack_enabled")),
        "wechat_enabled": bool(item.get("wechat_enabled")),
        "daily_digest_enabled": bool(item.get("daily_digest_enabled")),
        "weekly_summary_enabled": bool(item.get("weekly_summary_enabled")),
        "stalled_project_enabled": bool(item.get("stalled_project_enabled")),
        "claim_activity_enabled": bool(item.get("claim_activity_enabled")),
        "attribution_alert_enabled": bool(item.get("attribution_alert_enabled")),
        "cost_alert_enabled": bool(item.get("cost_alert_enabled")),
        "system_alert_enabled": bool(item.get("system_alert_enabled")),
        "quiet_hours_start": item.get("quiet_hours_start") or DEFAULT_NOTIFICATION_SETTINGS["quiet_hours_start"],
        "quiet_hours_end": item.get("quiet_hours_end") or DEFAULT_NOTIFICATION_SETTINGS["quiet_hours_end"],
        "timezone": item.get("timezone") or DEFAULT_NOTIFICATION_SETTINGS["timezone"],
    }
    return {
        "id": int(item.get("id") or 0),
        "staff_id": int(item.get("staff_id") or 0),
        "settings": settings,
        "delivery_enabled": False,
        "delivery_note": "v3 only stores notification preferences; delivery is disabled.",
        "updated_by_staff_id": item.get("updated_by_staff_id"),
        "created_at": item.get("created_at") or "",
        "updated_at": item.get("updated_at") or "",
    }


def _ensure_row(staff_id: int, *, actor_staff_id: int | None = None) -> dict[str, Any]:
    ensure_vkpi_notification_settings_schema()
    conn = get_conn()
    row = conn.execute("SELECT * FROM vkpi_notification_settings WHERE staff_id=?", (int(staff_id),)).fetchone()
    if row:
        return _row_to_payload(row)
    now = _utcnow()
    defaults = _normalize({})
    conn.execute(
        """
        INSERT INTO vkpi_notification_settings
            (staff_id, email_enabled, in_app_enabled, slack_enabled, wechat_enabled,
             daily_digest_enabled, weekly_summary_enabled, stalled_project_enabled,
             claim_activity_enabled, attribution_alert_enabled, cost_alert_enabled,
             system_alert_enabled, quiet_hours_start, quiet_hours_end, timezone,
             preferences_json, updated_by_staff_id, created_at, updated_at)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """,
        (
            int(staff_id),
            _db_bool(defaults["email_enabled"]),
            _db_bool(defaults["in_app_enabled"]),
            _db_bool(defaults["slack_enabled"]),
            _db_bool(defaults["wechat_enabled"]),
            _db_bool(defaults["daily_digest_enabled"]),
            _db_bool(defaults["weekly_summary_enabled"]),
            _db_bool(defaults["stalled_project_enabled"]),
            _db_bool(defaults["claim_activity_enabled"]),
            _db_bool(defaults["attribution_alert_enabled"]),
            _db_bool(defaults["cost_alert_enabled"]),
            _db_bool(defaults["system_alert_enabled"]),
            defaults["quiet_hours_start"],
            defaults["quiet_hours_end"],
            defaults["timezone"],
            _json(defaults["preferences_json"]),
            actor_staff_id or int(staff_id),
            now,
            now,
        ),
    )
    conn.commit()
    row = conn.execute("SELECT * FROM vkpi_notification_settings WHERE staff_id=?", (int(staff_id),)).fetchone()
    return _row_to_payload(row)


def get_notification_settings(*, staff: dict[str, Any] | None = None, staff_id: int | None = None) -> dict[str, Any]:
    target = _target_staff_id(staff, staff_id)
    return {"notification_settings": _ensure_row(target, actor_staff_id=resolve_staff_id(staff)), "full_scope": scope.can_view_all(staff)}


def update_notification_settings(payload: dict[str, Any], *, staff: dict[str, Any] | None = None) -> dict[str, Any]:
    requested = payload.get("staff_id") or payload.get("staffId")
    target = _target_staff_id(staff, _int(requested, 0) or None)
    actor = resolve_staff_id(staff)
    current = _ensure_row(target, actor_staff_id=actor)
    normalized = _normalize(payload, current.get("settings") or {})
    now = _utcnow()
    conn = get_conn()
    conn.execute(
        """
        UPDATE vkpi_notification_settings
        SET email_enabled=?,
            in_app_enabled=?,
            slack_enabled=?,
            wechat_enabled=?,
            daily_digest_enabled=?,
            weekly_summary_enabled=?,
            stalled_project_enabled=?,
            claim_activity_enabled=?,
            attribution_alert_enabled=?,
            cost_alert_enabled=?,
            system_alert_enabled=?,
            quiet_hours_start=?,
            quiet_hours_end=?,
            timezone=?,
            preferences_json=?,
            updated_by_staff_id=?,
            updated_at=?
        WHERE staff_id=?
        """,
        (
            _db_bool(normalized["email_enabled"]),
            _db_bool(normalized["in_app_enabled"]),
            _db_bool(normalized["slack_enabled"]),
            _db_bool(normalized["wechat_enabled"]),
            _db_bool(normalized["daily_digest_enabled"]),
            _db_bool(normalized["weekly_summary_enabled"]),
            _db_bool(normalized["stalled_project_enabled"]),
            _db_bool(normalized["claim_activity_enabled"]),
            _db_bool(normalized["attribution_alert_enabled"]),
            _db_bool(normalized["cost_alert_enabled"]),
            _db_bool(normalized["system_alert_enabled"]),
            normalized["quiet_hours_start"],
            normalized["quiet_hours_end"],
            normalized["timezone"],
            _json(normalized["preferences_json"]),
            actor or None,
            now,
            target,
        ),
    )
    conn.commit()
    updated = _ensure_row(target, actor_staff_id=actor)
    audit.log_settings_change(
        staff_id=actor,
        change_type="notification_setting",
        setting_key=f"vkpi_notification_settings:{target}",
        old_value_redacted=_json({"staff_id": target, "delivery_enabled": False}),
        new_value_redacted=_json({"staff_id": target, "enabled_keys": _enabled_keys(updated.get("settings") or {})}),
        full_value=_json(updated.get("settings") or {}),
        metadata={"target_staff_id": target, "delivery_enabled": False},
    )
    audit.log_business_event(
        staff_id=actor,
        action_type="notification_setting_update",
        target_type="staff",
        target_id=target,
        metadata={"target_staff_id": target, "delivery_enabled": False},
    )
    return {"notification_settings": updated, "full_scope": scope.can_view_all(staff)}


def _enabled_keys(settings: dict[str, Any]) -> list[str]:
    return sorted([key for key, value in settings.items() if key.endswith("_enabled") and bool(value)])


def list_notification_settings(*, staff: dict[str, Any] | None = None, limit: int = 200) -> dict[str, Any]:
    if not scope.can_view_all(staff):
        return {"notification_settings": [get_notification_settings(staff=staff)["notification_settings"]], "full_scope": False}
    ensure_vkpi_notification_settings_schema()
    rows = get_conn().execute(
        "SELECT * FROM vkpi_notification_settings ORDER BY updated_at DESC, id DESC LIMIT ?",
        (max(1, min(500, int(limit or 200))),),
    ).fetchall()
    return {"notification_settings": [_row_to_payload(row) for row in rows], "full_scope": True}
