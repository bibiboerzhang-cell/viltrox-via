"""User-scoped V-KPI preferences.

Preferences are per staff account. Frontend `staff_id` is a hint only; non
manager users are always reduced to their own staff row by backend scope checks.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

from app.db.connection import get_conn
from app.domains import audit
from app.domains.access import scope
from app.domains.settings.schema_preferences import ensure_vkpi_preferences_schema
from app.domains.projects.workflow import staff_id as resolve_staff_id

DEFAULT_PREFS: dict[str, Any] = {
    "locale": "zh-CN",
    "timezone": "Asia/Shanghai",
    "date_range_default": "7d",
    "landing_page": "dashboard",
    "dashboard_scope_default": "self",
    "table_density": "comfortable",
    "rows_per_page": 20,
    "compact_mode": False,
    "right_panel_open": True,
    "enabled_widgets": [],
    "pinned_nav": [],
}

_LANDING_PAGES = {
    "dashboard",
    "discover",
    "projects",
    "links",
    "attribution",
    "analytics",
    "channels",
    "campaigns",
    "reports",
    "settings",
}
_DATE_RANGES = {"today", "7d", "14d", "30d", "mtd", "qtd"}
_SCOPES = {"self", "team", "all"}
_DENSITIES = {"comfortable", "compact", "spacious"}


def _utcnow() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _json(value: Any) -> str:
    return json.dumps(value or {}, ensure_ascii=False, default=str)


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


def _clamp_rows(value: Any) -> int:
    return max(5, min(200, _int(value, int(DEFAULT_PREFS["rows_per_page"]))))


def _target_staff_id(staff: dict[str, Any] | None, requested_staff_id: int | None = None) -> int:
    actor = resolve_staff_id(staff)
    requested = _int(requested_staff_id, 0)
    if requested and requested != actor and not scope.can_view_all(staff):
        raise scope.ScopeDenied("preference scope denied")
    target = requested or actor
    if not target:
        raise scope.ScopeDenied("missing staff context")
    return int(target)


def _normalize(payload: dict[str, Any], existing: dict[str, Any] | None = None) -> dict[str, Any]:
    base = dict(DEFAULT_PREFS)
    if existing:
        base.update(existing)
    extra = _load_json(payload.get("preferences") or payload.get("preferences_json") or base.get("preferences_json") or {})
    locale = str(payload.get("locale", base.get("locale")) or DEFAULT_PREFS["locale"]).strip()[:24] or str(DEFAULT_PREFS["locale"])
    timezone = str(payload.get("timezone", base.get("timezone")) or DEFAULT_PREFS["timezone"]).strip()[:80] or str(DEFAULT_PREFS["timezone"])
    date_range = str(payload.get("date_range_default", payload.get("dateRangeDefault", base.get("date_range_default"))) or "7d").strip().lower()
    landing_page = str(payload.get("landing_page", payload.get("landingPage", base.get("landing_page"))) or "dashboard").strip().lower()
    dashboard_scope = str(payload.get("dashboard_scope_default", payload.get("dashboardScopeDefault", base.get("dashboard_scope_default"))) or "self").strip().lower()
    density = str(payload.get("table_density", payload.get("tableDensity", base.get("table_density"))) or "comfortable").strip().lower()
    return {
        "locale": locale,
        "timezone": timezone,
        "date_range_default": date_range if date_range in _DATE_RANGES else str(DEFAULT_PREFS["date_range_default"]),
        "landing_page": landing_page if landing_page in _LANDING_PAGES else str(DEFAULT_PREFS["landing_page"]),
        "dashboard_scope_default": dashboard_scope if dashboard_scope in _SCOPES else str(DEFAULT_PREFS["dashboard_scope_default"]),
        "table_density": density if density in _DENSITIES else str(DEFAULT_PREFS["table_density"]),
        "rows_per_page": _clamp_rows(payload.get("rows_per_page", payload.get("rowsPerPage", base.get("rows_per_page")))),
        "compact_mode": _bool(payload.get("compact_mode", payload.get("compactMode", base.get("compact_mode"))), bool(DEFAULT_PREFS["compact_mode"])),
        "right_panel_open": _bool(payload.get("right_panel_open", payload.get("rightPanelOpen", base.get("right_panel_open"))), bool(DEFAULT_PREFS["right_panel_open"])),
        "preferences_json": extra,
    }


def _row_to_payload(row: Any) -> dict[str, Any]:
    if not row:
        return {}
    item = dict(row)
    extra = _load_json(item.get("preferences_json"))
    preferences = {
        **extra,
        "locale": item.get("locale") or DEFAULT_PREFS["locale"],
        "timezone": item.get("timezone") or DEFAULT_PREFS["timezone"],
        "date_range_default": item.get("date_range_default") or DEFAULT_PREFS["date_range_default"],
        "landing_page": item.get("landing_page") or DEFAULT_PREFS["landing_page"],
        "dashboard_scope_default": item.get("dashboard_scope_default") or DEFAULT_PREFS["dashboard_scope_default"],
        "table_density": item.get("table_density") or DEFAULT_PREFS["table_density"],
        "rows_per_page": int(item.get("rows_per_page") or DEFAULT_PREFS["rows_per_page"]),
        "compact_mode": bool(item.get("compact_mode")),
        "right_panel_open": bool(item.get("right_panel_open")),
    }
    return {
        "id": int(item.get("id") or 0),
        "staff_id": int(item.get("staff_id") or 0),
        "preferences": preferences,
        "updated_by_staff_id": item.get("updated_by_staff_id"),
        "created_at": item.get("created_at") or "",
        "updated_at": item.get("updated_at") or "",
    }


def _ensure_row(staff_id: int, *, actor_staff_id: int | None = None) -> dict[str, Any]:
    ensure_vkpi_preferences_schema()
    conn = get_conn()
    row = conn.execute("SELECT * FROM vkpi_user_preferences WHERE staff_id=?", (int(staff_id),)).fetchone()
    if row:
        return _row_to_payload(row)
    now = _utcnow()
    defaults = _normalize({})
    conn.execute(
        """
        INSERT INTO vkpi_user_preferences
            (staff_id, locale, timezone, date_range_default, landing_page, dashboard_scope_default,
             table_density, rows_per_page, compact_mode, right_panel_open, preferences_json,
             updated_by_staff_id, created_at, updated_at)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """,
        (
            int(staff_id),
            defaults["locale"],
            defaults["timezone"],
            defaults["date_range_default"],
            defaults["landing_page"],
            defaults["dashboard_scope_default"],
            defaults["table_density"],
            defaults["rows_per_page"],
            bool(defaults["compact_mode"]),
            bool(defaults["right_panel_open"]),
            _json(defaults["preferences_json"]),
            actor_staff_id or int(staff_id),
            now,
            now,
        ),
    )
    conn.commit()
    row = conn.execute("SELECT * FROM vkpi_user_preferences WHERE staff_id=?", (int(staff_id),)).fetchone()
    return _row_to_payload(row)


def get_preferences(*, staff: dict[str, Any] | None = None, staff_id: int | None = None) -> dict[str, Any]:
    target = _target_staff_id(staff, staff_id)
    return {"preference": _ensure_row(target, actor_staff_id=resolve_staff_id(staff)), "full_scope": scope.can_view_all(staff)}


def update_preferences(payload: dict[str, Any], *, staff: dict[str, Any] | None = None) -> dict[str, Any]:
    requested = payload.get("staff_id") or payload.get("staffId")
    target = _target_staff_id(staff, _int(requested, 0) or None)
    actor = resolve_staff_id(staff)
    current = _ensure_row(target, actor_staff_id=actor)
    normalized = _normalize(payload, current.get("preferences") or {})
    now = _utcnow()
    conn = get_conn()
    conn.execute(
        """
        UPDATE vkpi_user_preferences
        SET locale=?,
            timezone=?,
            date_range_default=?,
            landing_page=?,
            dashboard_scope_default=?,
            table_density=?,
            rows_per_page=?,
            compact_mode=?,
            right_panel_open=?,
            preferences_json=?,
            updated_by_staff_id=?,
            updated_at=?
        WHERE staff_id=?
        """,
        (
            normalized["locale"],
            normalized["timezone"],
            normalized["date_range_default"],
            normalized["landing_page"],
            normalized["dashboard_scope_default"],
            normalized["table_density"],
            normalized["rows_per_page"],
            bool(normalized["compact_mode"]),
            bool(normalized["right_panel_open"]),
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
        change_type="user_preference",
        setting_key=f"vkpi_user_preferences:{target}",
        old_value_redacted=_json({"staff_id": target, "keys": sorted((current.get("preferences") or {}).keys())}),
        new_value_redacted=_json({"staff_id": target, "keys": sorted((updated.get("preferences") or {}).keys())}),
        full_value=_json(updated.get("preferences") or {}),
        metadata={"target_staff_id": target},
    )
    audit.log_business_event(
        staff_id=actor,
        action_type="user_preference_update",
        target_type="staff",
        target_id=target,
        metadata={"target_staff_id": target},
    )
    return {"preference": updated, "full_scope": scope.can_view_all(staff)}


def list_preferences(*, staff: dict[str, Any] | None = None, limit: int = 200) -> dict[str, Any]:
    if not scope.can_view_all(staff):
        return {"preferences": [get_preferences(staff=staff)["preference"]], "full_scope": False}
    ensure_vkpi_preferences_schema()
    rows = get_conn().execute(
        "SELECT * FROM vkpi_user_preferences ORDER BY updated_at DESC, id DESC LIMIT ?",
        (max(1, min(500, int(limit or 200))),),
    ).fetchall()
    return {"preferences": [_row_to_payload(row) for row in rows], "full_scope": True}
