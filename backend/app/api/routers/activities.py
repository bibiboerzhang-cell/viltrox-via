"""
api/routers/activities.py — Activities Growth OS Phase A.
"""
from __future__ import annotations

import json
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import RedirectResponse

from app.api.dependencies.auth import get_admin
from app.api.dependencies.perms import require_tab
from app.core.logging import get_logger
from app.db.connection import get_conn
from app.services.activities import attribution as activity_svc
from app.services.audit_log import record_admin_action
from app.services.security.rate_limiter import rate_limit

router = APIRouter(prefix="/api/admin/activities", tags=["activities"])
public_router = APIRouter(prefix="/api/public/event", tags=["activities-public"])
logger = get_logger(__name__)


def _int(value: Any, default: int = 0) -> int:
    try:
        return int(float(value or 0))
    except (TypeError, ValueError):
        return default


def _json_loads(value: Any) -> dict[str, Any]:
    try:
        parsed = json.loads(str(value or "{}"))
        return parsed if isinstance(parsed, dict) else {}
    except (TypeError, json.JSONDecodeError):
        logger.warning("activities.invalid_json_payload")
        return {}


def _row_dict(row: Any) -> dict[str, Any]:
    return dict(row) if row is not None else {}


def _money_usd(cents: Any) -> float:
    return round(_int(cents) / 100, 2)


def _roi_pct(revenue_cents: int, spend_cents: int) -> float | None:
    if spend_cents <= 0:
        return None
    return round((revenue_cents - spend_cents) / spend_cents * 100, 1)


def _roas(revenue_cents: int, spend_cents: int) -> float | None:
    if spend_cents <= 0:
        return None
    return round(revenue_cents / spend_cents, 2)


@router.get("")
def list_activities(
    page: int = Query(default=1, ge=1),
    limit: int = Query(default=20, ge=1, le=100),
    q: str = "",
    status: str = "",
    activity_type: str = "",
    market: str = "",
    country: str = "",
    platform: str = "",
    product_sku: str = "",
    date_from: str = "",
    date_to: str = "",
    _staff=Depends(require_tab("activities", "read")),
):
    conn = get_conn()
    where = ["1=1"]
    params: list[Any] = []
    if q:
        where.append("(LOWER(title) LIKE ? OR LOWER(activity_id) LIKE ? OR LOWER(dealer_name) LIKE ?)")
        needle = f"%{q.lower()}%"
        params.extend([needle, needle, needle])
    for field, value in (
        ("status", status),
        ("activity_type", activity_type),
        ("market", market),
        ("country", country),
        ("platform", platform),
        ("product_sku", product_sku),
    ):
        if value and value != "all":
            where.append(f"{field}=?")
            params.append(value.upper() if field in {"market", "country"} else value)
    if date_from:
        where.append("starts_at >= ?")
        params.append(date_from)
    if date_to:
        where.append("starts_at <= ?")
        params.append(date_to)
    where_sql = " AND ".join(where)
    total = int(conn.execute(f"SELECT COUNT(*) AS n FROM activities WHERE {where_sql}", params).fetchone()["n"] or 0)
    offset = (page - 1) * limit
    rows = conn.execute(
        f"""
        SELECT a.*,
               (SELECT COUNT(*) FROM activity_events ae WHERE ae.activity_id=a.id AND ae.event_type='scan') AS scan_count,
               (SELECT COUNT(DISTINCT user_id) FROM activity_attributions aa WHERE aa.activity_id=a.id AND aa.user_id IS NOT NULL) AS attributed_user_count,
               (SELECT COUNT(*) FROM activity_attributions aa WHERE aa.activity_id=a.id AND aa.first_register_at IS NOT NULL) AS register_count,
               (SELECT COALESCE(SUM(submission_count), 0) FROM activity_attributions aa WHERE aa.activity_id=a.id) AS submission_count,
               (SELECT COALESCE(SUM(order_count), 0) FROM activity_attributions aa WHERE aa.activity_id=a.id) AS order_count,
               (SELECT COALESCE(SUM(revenue_cents), 0) FROM activity_attributions aa WHERE aa.activity_id=a.id) AS revenue_cents,
               (SELECT COALESCE(SUM(commission_cents), 0) FROM activity_attributions aa WHERE aa.activity_id=a.id) AS commission_cents
        FROM activities a
        WHERE {where_sql}
        ORDER BY a.starts_at DESC, a.id DESC
        LIMIT ? OFFSET ?
        """,
        [*params, limit, offset],
    ).fetchall()
    items: list[dict[str, Any]] = []
    for row in rows:
        item = dict(row)
        spend = _int(item.get("actual_spend_cents")) or _int(item.get("planned_budget_cents"))
        revenue = _int(item.get("revenue_cents"))
        item["roi_pct"] = _roi_pct(revenue, spend)
        item["roas"] = _roas(revenue, spend)
        item["spend_cents_effective"] = spend
        items.append(item)
    return {
        "activities": items,
        "pagination": {"page": page, "limit": limit, "total": total, "pages": (total + limit - 1) // limit},
    }


@router.post("")
def create_activity(
    body: dict[str, Any],
    request: Request,
    admin=Depends(get_admin),
    _staff=Depends(require_tab("activities", "write")),
):
    try:
        result = activity_svc.create_activity(body, staff_id=int(admin.get("id") or 0))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    record_admin_action(
        actor=admin,
        action="activity_created",
        target_type="activity",
        target_id=str(result.get("id") or ""),
        detail={"activity_id": result.get("activity_id"), "title": body.get("title")},
        request=request,
    )
    base = str(request.base_url).rstrip("/")
    result["qr_url"] = f"{base}{result['qr_url']}"
    result["qr_landing_url"] = f"{base}{result['qr_landing_url']}"
    return result


@router.get("/map")
def activity_map(_staff=Depends(require_tab("activities", "read"))):
    conn = get_conn()
    rows = conn.execute(
        """
        SELECT id, activity_id, title, status, activity_type, market, country, platform,
               location_name, location_lat, location_lng, planned_budget_cents,
               actual_spend_cents
        FROM activities
        WHERE location_lat IS NOT NULL AND location_lng IS NOT NULL
        ORDER BY starts_at DESC, id DESC
        LIMIT 250
        """
    ).fetchall()
    return {"activities": [dict(row) for row in rows]}


@router.get("/{activity_id}")
def get_activity(activity_id: int, _staff=Depends(require_tab("activities", "read"))):
    row = get_conn().execute("SELECT * FROM activities WHERE id=?", (int(activity_id),)).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="activity not found")
    item = dict(row)
    item["budget_breakdown"] = _json_loads(item.get("budget_breakdown_json"))
    return item


@router.patch("/{activity_id}")
def update_activity(
    activity_id: int,
    body: dict[str, Any],
    request: Request,
    admin=Depends(get_admin),
    _staff=Depends(require_tab("activities", "write")),
):
    try:
        activity_svc.update_activity(int(activity_id), body)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    record_admin_action(
        actor=admin,
        action="activity_updated",
        target_type="activity",
        target_id=str(activity_id),
        detail={"fields": sorted(body.keys())},
        request=request,
    )
    return {"status": "updated"}


@router.post("/{activity_id}/{action}")
def activity_action(
    activity_id: int,
    action: str,
    request: Request,
    admin=Depends(get_admin),
    _staff=Depends(require_tab("activities", "write")),
):
    try:
        status = activity_svc.set_activity_status(int(activity_id), action)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    record_admin_action(
        actor=admin,
        action=f"activity_{action}",
        target_type="activity",
        target_id=str(activity_id),
        detail={"status": status},
        request=request,
    )
    return {"status": status}


@router.get("/{activity_id}/dashboard")
def activity_dashboard(
    activity_id: int,
    attribution_mode: str = Query(default="last_touch", pattern="^(first_touch|last_touch)$"),
    _staff=Depends(require_tab("activities", "read")),
):
    conn = get_conn()
    activity = conn.execute("SELECT * FROM activities WHERE id=?", (int(activity_id),)).fetchone()
    if not activity:
        raise HTTPException(status_code=404, detail="activity not found")
    activity_dict = dict(activity)
    scan_count = _int(conn.execute(
        "SELECT COUNT(*) AS n FROM activity_events WHERE activity_id=? AND event_type='scan'",
        (int(activity_id),),
    ).fetchone()["n"])
    unique_scan_count = _int(conn.execute(
        """
        SELECT COUNT(DISTINCT COALESCE(NULLIF(anonymous_id, ''), CAST(id AS TEXT))) AS n
        FROM activity_events
        WHERE activity_id=? AND event_type='scan'
        """,
        (int(activity_id),),
    ).fetchone()["n"])
    agg = conn.execute(
        """
        SELECT COUNT(*) AS attributed_rows,
               COUNT(DISTINCT user_id) AS attributed_users,
               SUM(CASE WHEN first_register_at IS NOT NULL THEN 1 ELSE 0 END) AS register_count,
               SUM(CASE WHEN edu_verified_at IS NOT NULL THEN 1 ELSE 0 END) AS edu_verified_count,
               SUM(CASE WHEN first_submit_at IS NOT NULL THEN 1 ELSE 0 END) AS active_creators,
               COALESCE(SUM(submission_count), 0) AS submission_count,
               SUM(CASE WHEN first_purchase_at IS NOT NULL THEN 1 ELSE 0 END) AS buyer_count,
               COALESCE(SUM(order_count), 0) AS order_count,
               COALESCE(SUM(revenue_cents), 0) AS revenue_cents,
               COALESCE(SUM(commission_cents), 0) AS commission_cents
        FROM activity_attributions
        WHERE activity_id=?
        """,
        (int(activity_id),),
    ).fetchone()
    register_count = _int(agg["register_count"])
    edu_count = _int(agg["edu_verified_count"])
    active_creators = _int(agg["active_creators"])
    buyer_count = _int(agg["buyer_count"])
    order_count = _int(agg["order_count"])
    submission_count = _int(agg["submission_count"])
    revenue_cents = _int(agg["revenue_cents"])
    commission_cents = _int(agg["commission_cents"])
    actual_spend_cents = _int(activity["actual_spend_cents"])
    planned_budget_cents = _int(activity["planned_budget_cents"])
    effective_spend_cents = actual_spend_cents or planned_budget_cents
    daily = conn.execute(
        """
        SELECT substr(created_at, 1, 10) AS day, event_type, COUNT(*) AS n,
               COALESCE(SUM(revenue_cents), 0) AS revenue_cents
        FROM activity_events
        WHERE activity_id=?
        GROUP BY substr(created_at, 1, 10), event_type
        ORDER BY day ASC, event_type ASC
        """,
        (int(activity_id),),
    ).fetchall()
    channels = conn.execute(
        """
        SELECT COALESCE(NULLIF(referrer, ''), 'direct') AS channel, COUNT(*) AS n
        FROM activity_events
        WHERE activity_id=? AND event_type='scan'
        GROUP BY COALESCE(NULLIF(referrer, ''), 'direct')
        ORDER BY n DESC
        LIMIT 10
        """,
        (int(activity_id),),
    ).fetchall()
    same_type = conn.execute(
        """
        SELECT a.id, a.title, a.actual_spend_cents, a.planned_budget_cents,
               (SELECT COALESCE(SUM(revenue_cents), 0) FROM activity_attributions aa WHERE aa.activity_id=a.id) AS revenue_cents,
               (SELECT COUNT(*) FROM activity_events ae WHERE ae.activity_id=a.id AND ae.event_type='scan') AS scan_count
        FROM activities a
        WHERE a.activity_type=? AND a.id != ? AND a.status='ended'
        ORDER BY a.ends_at DESC, a.id DESC
        LIMIT 20
        """,
        (activity["activity_type"], int(activity_id)),
    ).fetchall()
    roi_values = []
    for row in same_type:
        spend = _int(row["actual_spend_cents"]) or _int(row["planned_budget_cents"])
        roi = _roi_pct(_int(row["revenue_cents"]), spend)
        if roi is not None:
            roi_values.append(roi)
    avg_roi = round(sum(roi_values) / len(roi_values), 1) if roi_values else None
    last_row = dict(same_type[0]) if same_type else None
    roi = _roi_pct(revenue_cents, effective_spend_cents)
    return {
        "activity": activity_dict,
        "attribution_mode": attribution_mode,
        "kpi": {
            "scan_count": scan_count,
            "unique_scan_count": unique_scan_count,
            "register_count": register_count,
            "edu_verified_count": edu_count,
            "submission_count": submission_count,
            "active_creators": active_creators,
            "order_count": order_count,
            "buyer_count": buyer_count,
            "revenue_cents": revenue_cents,
            "revenue_usd": _money_usd(revenue_cents),
            "commission_usd": _money_usd(commission_cents),
            "actual_spend_usd": _money_usd(actual_spend_cents),
            "planned_budget_usd": _money_usd(planned_budget_cents),
            "effective_spend_usd": _money_usd(effective_spend_cents),
            "roi_pct": roi,
            "roas": _roas(revenue_cents, effective_spend_cents),
            "cpa_usd": _money_usd(effective_spend_cents / max(register_count, 1)) if register_count else None,
            "aov_usd": _money_usd(revenue_cents / max(order_count, 1)) if order_count else 0,
        },
        "funnel": {
            "scan": scan_count,
            "unique_scan": unique_scan_count,
            "register": register_count,
            "edu_verified": edu_count,
            "submit_active": active_creators,
            "purchase": buyer_count,
            "rate_scan_to_register": round(register_count / max(unique_scan_count, 1) * 100, 1),
            "rate_register_to_edu": round(edu_count / max(register_count, 1) * 100, 1),
            "rate_register_to_submit": round(active_creators / max(register_count, 1) * 100, 1),
            "rate_register_to_buy": round(buyer_count / max(register_count, 1) * 100, 1),
        },
        "daily_trends": [dict(row) for row in daily],
        "channel_breakdown": [dict(row) for row in channels],
        "comparison": {
            "vs_company_average": {
                "avg_roi_pct": avg_roi,
                "sample_size": len(roi_values),
                "this_vs_avg_roi_diff_pct": round(roi - avg_roi, 1) if roi is not None and avg_roi is not None else None,
            },
            "vs_last_similar": {
                "last_activity_id": last_row.get("id") if last_row else None,
                "last_activity_title": last_row.get("title") if last_row else None,
                "last_revenue_usd": _money_usd(last_row.get("revenue_cents")) if last_row else None,
            },
            "vs_plan": {
                "spend_completion_pct": round(actual_spend_cents / planned_budget_cents * 100, 1) if planned_budget_cents else None,
                "scans_per_dollar": round(scan_count / max(effective_spend_cents / 100, 1), 2) if effective_spend_cents else None,
            },
        },
    }


@router.get("/{activity_id}/events")
def activity_events(
    activity_id: int,
    page: int = Query(default=1, ge=1),
    limit: int = Query(default=50, ge=1, le=200),
    event_type: str = "",
    _staff=Depends(require_tab("activities", "read")),
):
    conn = get_conn()
    where = ["activity_id=?"]
    params: list[Any] = [int(activity_id)]
    if event_type and event_type != "all":
        where.append("event_type=?")
        params.append(event_type)
    where_sql = " AND ".join(where)
    total = _int(conn.execute(f"SELECT COUNT(*) AS n FROM activity_events WHERE {where_sql}", params).fetchone()["n"])
    offset = (page - 1) * limit
    rows = conn.execute(
        f"""
        SELECT id, activity_id, user_id, anonymous_id, event_token, event_type,
               payload_json, revenue_cents, commission_cents, referrer, source_path, created_at
        FROM activity_events
        WHERE {where_sql}
        ORDER BY created_at DESC, id DESC
        LIMIT ? OFFSET ?
        """,
        [*params, limit, offset],
    ).fetchall()
    events = []
    for row in rows:
        item = dict(row)
        item["payload"] = _json_loads(item.pop("payload_json", "{}"))
        events.append(item)
    return {"events": events, "pagination": {"page": page, "limit": limit, "total": total, "pages": (total + limit - 1) // limit}}


@router.post("/{activity_id}/attribute-user")
def attribute_user(
    activity_id: int,
    body: dict[str, Any],
    request: Request,
    admin=Depends(get_admin),
    _staff=Depends(require_tab("activities", "write")),
):
    try:
        activity_svc.manual_attribute(
            int(activity_id),
            int(body.get("user_id") or 0),
            int(admin.get("id") or 0),
            str(body.get("note") or ""),
        )
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    record_admin_action(
        actor=admin,
        action="activity_manual_attribute",
        target_type="activity",
        target_id=str(activity_id),
        detail={"user_id": body.get("user_id"), "note": body.get("note")},
        request=request,
    )
    return {"status": "attributed"}


@public_router.get("/{qr_token}")
@rate_limit("activity_scan", max_requests=120, window_sec=60)
def event_landing(qr_token: str, request: Request):
    try:
        result = activity_svc.record_scan(qr_token, request, anonymous_id=str(request.cookies.get(activity_svc.ANON_COOKIE) or ""))
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=410, detail=str(exc)) from exc
    response = RedirectResponse(url=result["redirect_url"], status_code=302)
    response.set_cookie(activity_svc.ANON_COOKIE, result["anonymous_id"], max_age=60 * 60 * 24 * 30, samesite="lax")
    response.set_cookie(activity_svc.EVENT_COOKIE, result["event_token"], max_age=60 * 60 * 24 * 30, samesite="lax")
    return response


@public_router.post("/{qr_token}/track")
@rate_limit("activity_track", max_requests=300, window_sec=60)
def track_public_event(qr_token: str, body: dict[str, Any], request: Request):
    event_type = str(body.get("event_type") or "").strip()
    try:
        activity_svc.record_public_event(qr_token, event_type, body, request)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"status": "tracked", "event_type": event_type}
