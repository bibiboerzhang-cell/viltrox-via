"""V-KPI audit helpers for sensitive access, exports, and settings changes."""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timedelta
from typing import Any

from starlette.requests import Request

from app.core.logging import get_logger
from app.db.connection import get_conn
from app.platform.db.schema_audit import ensure_vkpi_audit_schema
from app.domains.projects.workflow import staff_id as resolve_staff_id

logger = get_logger(__name__)

SENSITIVE_ROUTE_RULES: tuple[tuple[str, str, str], ...] = (
    ("/reports", "view_financial", "page"),
    ("/exports", "view_export_history", "export"),
    ("/costs", "view_financial", "cost"),
    ("/product-costs", "view_financial", "cost"),
    ("/audit", "view_audit_log", "audit"),
    ("/kpi-ledger", "view_kpi_detail", "kpi"),
    ("/staff-kpi", "view_kpi_detail", "staff"),
    ("/attribution", "view_financial", "attribution"),
)


def _utcnow() -> str:
    return datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")


def _json(value: Any) -> str:
    return json.dumps(value or {}, ensure_ascii=False, default=str)


def _clean_staff_id(value: Any) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def _last_id(table: str, uid_col: str = "id", uid_value: Any | None = None) -> int:
    conn = get_conn()
    if uid_value is not None:
        row = conn.execute(f"SELECT id FROM {table} WHERE {uid_col}=? ORDER BY id DESC LIMIT 1", (uid_value,)).fetchone()
    else:
        row = conn.execute(f"SELECT id FROM {table} ORDER BY id DESC LIMIT 1").fetchone()
    return int(row["id"]) if row else 0


def log_sensitive_access(
    *,
    staff_id: int,
    action_type: str,
    resource_type: str,
    resource_id: str = "",
    page_path: str = "",
    ip: str = "",
    user_agent: str = "",
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    if not staff_id:
        return {"skipped": True, "reason": "missing_staff"}
    ensure_vkpi_audit_schema()
    conn = get_conn()
    conn.execute(
        """
        INSERT INTO vkpi_sensitive_access_logs
            (staff_id, action_type, resource_type, resource_id, page_path, ip, user_agent, metadata_json, created_at)
        VALUES (?,?,?,?,?,?,?,?,?)
        """,
        (int(staff_id), action_type, resource_type, str(resource_id or ""), page_path, ip, user_agent, _json(metadata), _utcnow()),
    )
    conn.commit()
    return {"id": _last_id("vkpi_sensitive_access_logs"), "status": "logged"}


def log_export(
    *,
    staff_id: int,
    export_kind: str,
    export_target: str,
    filters: dict[str, Any] | None = None,
    row_count: int = 0,
    file_size_bytes: int = 0,
    file_sha256: str = "",
    download_url: str = "",
    contains_pii: bool = False,
    contains_financial: bool = False,
    purpose: str = "",
    ip: str = "",
    user_agent: str = "",
    metric_keys: list[str] | None = None,
) -> dict[str, Any]:
    if not staff_id:
        return {"skipped": True, "reason": "missing_staff"}
    ensure_vkpi_audit_schema()
    conn = get_conn()
    conn.execute(
        """
        INSERT INTO vkpi_export_logs
            (staff_id, export_kind, export_target, metric_keys_json, filters_json, row_count,
             file_size_bytes, file_sha256, download_url, contains_pii, contains_financial, purpose,
             ip, user_agent, created_at)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """,
        (
            int(staff_id),
            export_kind,
            export_target,
            _json(metric_keys or []),
            _json(filters or {}),
            int(row_count or 0),
            int(file_size_bytes or 0),
            file_sha256,
            download_url,
            bool(contains_pii),
            bool(contains_financial),
            purpose,
            ip,
            user_agent,
            _utcnow(),
        ),
    )
    conn.commit()
    return {"id": _last_id("vkpi_export_logs"), "status": "logged"}


def log_settings_change(
    *,
    staff_id: int,
    change_type: str,
    setting_key: str,
    old_value_redacted: str = "",
    new_value_redacted: str = "",
    full_value: str = "",
    ip: str = "",
    user_agent: str = "",
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    if not staff_id:
        return {"skipped": True, "reason": "missing_staff"}
    ensure_vkpi_audit_schema()
    full_hash = hashlib.sha256(full_value.encode("utf-8")).hexdigest() if full_value else ""
    conn = get_conn()
    conn.execute(
        """
        INSERT INTO vkpi_settings_change_logs
            (staff_id, change_type, setting_key, old_value_redacted, new_value_redacted,
             full_value_hash, ip, user_agent, metadata_json, changed_at)
        VALUES (?,?,?,?,?,?,?,?,?,?)
        """,
        (int(staff_id), change_type, setting_key, old_value_redacted, new_value_redacted, full_hash, ip, user_agent, _json(metadata), _utcnow()),
    )
    conn.commit()
    return {"id": _last_id("vkpi_settings_change_logs"), "status": "logged"}


def log_business_event(
    *,
    staff_id: int,
    action_type: str,
    target_type: str,
    target_id: str | int = "",
    detail: str = "",
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    if not staff_id:
        return {"skipped": True, "reason": "missing_staff"}
    ensure_vkpi_audit_schema()
    conn = get_conn()
    conn.execute(
        """
        INSERT INTO vkpi_business_audit_logs
            (staff_id, action_type, target_type, target_id, detail, metadata_json, created_at)
        VALUES (?,?,?,?,?,?,?)
        """,
        (int(staff_id), action_type, target_type, str(target_id or ""), detail, _json(metadata), _utcnow()),
    )
    conn.commit()
    return {"id": _last_id("vkpi_business_audit_logs"), "status": "logged"}


def _where(filters: dict[str, Any], time_col: str = "created_at") -> tuple[str, list[Any]]:
    where: list[str] = []
    params: list[Any] = []
    if filters.get("staff_id"):
        where.append("staff_id=?")
        params.append(int(filters["staff_id"]))
    if filters.get("date_from"):
        where.append(f"{time_col}>=?")
        params.append(str(filters["date_from"]))
    if filters.get("date_to"):
        where.append(f"{time_col}<=?")
        params.append(str(filters["date_to"]))
    return ("WHERE " + " AND ".join(where)) if where else "", params


def list_sensitive_access(limit: int = 100, **filters: Any) -> dict[str, Any]:
    ensure_vkpi_audit_schema()
    clause, params = _where(filters)
    if filters.get("action_type"):
        clause = f"{clause} {'AND' if clause else 'WHERE'} action_type=?"
        params.append(str(filters["action_type"]))
    rows = get_conn().execute(
        f"SELECT * FROM vkpi_sensitive_access_logs {clause} ORDER BY created_at DESC, id DESC LIMIT ?",
        (*params, max(1, min(500, int(limit or 100)))),
    ).fetchall()
    return {"logs": [dict(row) for row in rows]}


def list_export_logs(limit: int = 100, **filters: Any) -> dict[str, Any]:
    ensure_vkpi_audit_schema()
    clause, params = _where(filters)
    rows = get_conn().execute(
        f"SELECT * FROM vkpi_export_logs {clause} ORDER BY created_at DESC, id DESC LIMIT ?",
        (*params, max(1, min(500, int(limit or 100)))),
    ).fetchall()
    return {"logs": [dict(row) for row in rows]}


def list_settings_changes(limit: int = 100, **filters: Any) -> dict[str, Any]:
    ensure_vkpi_audit_schema()
    clause, params = _where(filters, time_col="changed_at")
    rows = get_conn().execute(
        f"SELECT * FROM vkpi_settings_change_logs {clause} ORDER BY changed_at DESC, id DESC LIMIT ?",
        (*params, max(1, min(500, int(limit or 100)))),
    ).fetchall()
    return {"logs": [dict(row) for row in rows]}


def unified(limit: int = 100, event_category: str = "", staff_id: int | None = None) -> dict[str, Any]:
    ensure_vkpi_audit_schema()
    where: list[str] = []
    params: list[Any] = []
    if event_category:
        where.append("event_category=?")
        params.append(event_category)
    if staff_id:
        where.append("staff_id=?")
        params.append(int(staff_id))
    clause = "WHERE " + " AND ".join(where) if where else ""
    conn = get_conn()
    view_ok = True
    try:
        rows = conn.execute(
            f"SELECT * FROM vkpi_unified_audit {clause} ORDER BY occurred_at DESC LIMIT ?",
            (*params, max(1, min(500, int(limit or 100)))),
        ).fetchall()
    except Exception as exc:
        logger.warning("vkpi unified audit view query failed, using fallback tables: %s", exc)
        view_ok = False
        rows = []
    events = [dict(row) for row in rows]
    if not view_ok:
        fallback_limit = max(1, min(500, int(limit or 100)))

        def append_fallback(category: str, sql: str, params3: tuple[Any, ...] = ()) -> None:
            if event_category and event_category != category:
                return
            try:
                events.extend(dict(row) for row in conn.execute(sql, (*params3, fallback_limit)).fetchall())
            except Exception as exc:
                logger.warning("vkpi audit fallback query failed for %s: %s", category, exc)
                return

        staff_clause = "WHERE staff_id=?" if staff_id else ""
        staff_params: tuple[Any, ...] = (int(staff_id),) if staff_id else ()
        append_fallback(
            "sensitive_access",
            f"""
            SELECT 'sensitive_access' AS event_category, id AS event_id, staff_id,
                   action_type AS action, resource_type AS target_type, resource_id AS target_id,
                   page_path AS detail, ip, user_agent, created_at AS occurred_at, metadata_json
            FROM vkpi_sensitive_access_logs
            {staff_clause}
            ORDER BY created_at DESC, id DESC
            LIMIT ?
            """,
            staff_params,
        )
        append_fallback(
            "export",
            f"""
            SELECT 'export' AS event_category, id AS event_id, staff_id,
                   'export_' || export_kind AS action, 'export' AS target_type, export_target AS target_id,
                   'rows=' || row_count || ' size=' || file_size_bytes AS detail,
                   ip, user_agent, created_at AS occurred_at, filters_json AS metadata_json
            FROM vkpi_export_logs
            {staff_clause}
            ORDER BY created_at DESC, id DESC
            LIMIT ?
            """,
            staff_params,
        )
        append_fallback(
            "settings_change",
            f"""
            SELECT 'settings_change' AS event_category, id AS event_id, staff_id,
                   change_type AS action, 'setting' AS target_type, setting_key AS target_id,
                   'old=' || COALESCE(old_value_redacted, '') || ' -> new=' || COALESCE(new_value_redacted, '') AS detail,
                   ip, user_agent, changed_at AS occurred_at, metadata_json
            FROM vkpi_settings_change_logs
            {staff_clause}
            ORDER BY changed_at DESC, id DESC
            LIMIT ?
            """,
            staff_params,
        )
    if not event_category or event_category == "business":
        where2: list[str] = []
        params2: list[Any] = []
        if staff_id:
            where2.append("staff_id=?")
            params2.append(int(staff_id))
        clause2 = "WHERE " + " AND ".join(where2) if where2 else ""
        try:
            business_rows = conn.execute(
                f"""
                SELECT 'business' AS event_category, id AS event_id, staff_id,
                       action_type AS action, target_type, target_id, detail,
                       '' AS ip, '' AS user_agent, created_at AS occurred_at, metadata_json
                FROM vkpi_business_audit_logs
                {clause2}
                ORDER BY created_at DESC, id DESC
                LIMIT ?
                """,
                (*params2, max(1, min(500, int(limit or 100)))),
            ).fetchall()
            events.extend(dict(row) for row in business_rows)
        except Exception as exc:
            logger.warning("vkpi audit business fallback query failed: %s", exc)
    events.sort(key=lambda item: str(item.get("occurred_at") or ""), reverse=True)
    return {"events": events[: max(1, min(500, int(limit or 100)))]}


def _loads_json(value: Any) -> Any:
    if isinstance(value, (dict, list)):
        return value
    if not value:
        return {}
    try:
        return json.loads(str(value))
    except Exception as exc:
        logger.warning("vkpi audit json parse failed: %s", exc)
        return {}


def _staff_lookup() -> dict[int, dict[str, str]]:
    try:
        rows = get_conn().execute(
            """
            SELECT s.id AS staff_id, COALESCE(u.name, '') AS staff_name, COALESCE(u.email, '') AS staff_email
            FROM staff s
            LEFT JOIN users u ON u.id = s.user_id
            """
        ).fetchall()
        return {
            int(row["staff_id"]): {
                "staff_name": str(row["staff_name"] or ""),
                "staff_email": str(row["staff_email"] or ""),
            }
            for row in rows
        }
    except Exception as exc:
        logger.warning("vkpi audit staff lookup failed: %s", exc)
        return {}


def _count_since(table: str, time_col: str, since: str, extra_where: str = "", params: tuple[Any, ...] = ()) -> int:
    try:
        clause = f"WHERE {time_col}>=?"
        if extra_where:
            clause += f" AND {extra_where}"
        row = get_conn().execute(f"SELECT COUNT(*) AS n FROM {table} {clause}", (since, *params)).fetchone()
        return int(row["n"] or 0) if row else 0
    except Exception:
        return 0


def overview(limit: int = 100, event_category: str = "", staff_id: int | None = None, days: int = 7) -> dict[str, Any]:
    """Management audit payload for the front-end Audit page.

    This endpoint intentionally returns only masked/summary audit context. It is
    for answering "API / export / sensitive access / settings / business action
    worked or not", not for exposing secrets.
    """
    ensure_vkpi_audit_schema()
    days = max(1, min(365, int(days or 7)))
    limit = max(1, min(500, int(limit or 100)))
    since = (datetime.utcnow() - timedelta(days=days)).strftime("%Y-%m-%dT%H:%M:%SZ")
    raw_events = unified(limit=limit, event_category=event_category, staff_id=staff_id).get("events", [])
    staff_map = _staff_lookup()

    events: list[dict[str, Any]] = []
    for raw in raw_events:
        occurred_at = str(raw.get("occurred_at") or "")
        if occurred_at and occurred_at < since:
            continue
        sid = _clean_staff_id(raw.get("staff_id"))
        staff_info = staff_map.get(sid, {})
        metadata = _loads_json(raw.get("metadata_json"))
        # Never echo request bodies or raw secret-like values into the UI.
        if isinstance(metadata, dict):
            metadata = {
                str(key): ("***" if "key" in str(key).lower() or "secret" in str(key).lower() else value)
                for key, value in metadata.items()
            }
        events.append(
            {
                "id": f"{raw.get('event_category')}-{raw.get('event_id')}",
                "event_id": raw.get("event_id"),
                "event_category": raw.get("event_category") or "business",
                "action": raw.get("action") or "",
                "staff_id": sid or None,
                "staff_name": staff_info.get("staff_name") or staff_info.get("staff_email") or (str(sid) if sid else "-"),
                "staff_email": staff_info.get("staff_email") or "",
                "target_type": raw.get("target_type") or "",
                "target_id": raw.get("target_id") or "",
                "detail": raw.get("detail") or "",
                "ip": raw.get("ip") or "",
                "user_agent": raw.get("user_agent") or "",
                "occurred_at": occurred_at,
                "metadata": metadata,
            }
        )

    category_counts: dict[str, int] = {}
    action_counts: dict[str, int] = {}
    for event in events:
        category = str(event.get("event_category") or "unknown")
        action = str(event.get("action") or "unknown")
        category_counts[category] = category_counts.get(category, 0) + 1
        action_counts[action] = action_counts.get(action, 0) + 1

    base_summary = summary(days=days)
    base_summary["business_event_count"] = _count_since("vkpi_business_audit_logs", "created_at", since)
    base_summary["event_count"] = len(events)
    base_summary["by_category"] = [{"label": key, "count": value} for key, value in sorted(category_counts.items())]
    base_summary["by_action"] = [{"label": key, "count": value} for key, value in sorted(action_counts.items(), key=lambda item: item[1], reverse=True)[:20]]
    return {
        "summary": base_summary,
        "events": events[:limit],
        "filters": {"event_category": event_category, "staff_id": staff_id, "days": days, "limit": limit},
    }


def summary(days: int = 7) -> dict[str, Any]:
    ensure_vkpi_audit_schema()
    since = (datetime.utcnow() - timedelta(days=max(1, int(days or 7)))).strftime("%Y-%m-%dT%H:%M:%SZ")
    conn = get_conn()
    sensitive = conn.execute("SELECT COUNT(*) AS n FROM vkpi_sensitive_access_logs WHERE created_at>=?", (since,)).fetchone()
    exports = conn.execute("SELECT COUNT(*) AS n FROM vkpi_export_logs WHERE created_at>=?", (since,)).fetchone()
    settings = conn.execute("SELECT COUNT(*) AS n FROM vkpi_settings_change_logs WHERE changed_at>=?", (since,)).fetchone()
    try:
        adjustments = conn.execute("SELECT COUNT(*) AS n FROM vkpi_attribution_adjustments WHERE adjusted_at>=?", (since,)).fetchone()
        adjustment_count = int(adjustments["n"] or 0) if adjustments else 0
    except Exception:
        adjustment_count = 0
    return {
        "days": int(days or 7),
        "sensitive_access_count": int(sensitive["n"] or 0) if sensitive else 0,
        "export_count": int(exports["n"] or 0) if exports else 0,
        "settings_change_count": int(settings["n"] or 0) if settings else 0,
        "attribution_adjustment_count": adjustment_count,
    }


def log_request_if_sensitive(request: Request, response_status: int, staff: dict[str, Any] | None = None) -> None:
    """Best-effort middleware hook. Must never block the business request."""
    try:
        path = str(request.url.path or "")
        if not (path.startswith("/api/admin/vkpi") or path.startswith("/api/marketing") or path.startswith("/api/vkpi/webhooks")):
            return
        matched = None
        normalized = path.replace("/api/marketing", "/api/admin/vkpi", 1)
        for needle, action, resource in SENSITIVE_ROUTE_RULES:
            if needle in normalized:
                matched = (action, resource)
                break
        if not matched:
            return
        actor = staff or getattr(request.state, "staff", None) or getattr(request.state, "user", None) or {}
        actor_id = _clean_staff_id(resolve_staff_id(actor) if isinstance(actor, dict) else 0)
        if not actor_id:
            return
        log_sensitive_access(
            staff_id=actor_id,
            action_type=matched[0],
            resource_type=matched[1],
            resource_id=str(request.path_params or ""),
            page_path=path,
            ip=getattr(getattr(request, "client", None), "host", "") or "",
            user_agent=str(request.headers.get("user-agent") or ""),
            metadata={"method": request.method, "status": response_status, "query": str(request.url.query or "")},
        )
    except Exception as exc:  # pragma: no cover - audit must never block primary flow
        logger.warning("vkpi.audit.middleware_skipped", extra={"error": str(exc)})
