"""
services/activities/attribution.py — Growth OS attribution ledger.

The public QR path records only low-risk scan/page events. Identity and value
events are attached from trusted server paths: auth registration, submission
creation, and normalized commerce orders.
"""
from __future__ import annotations

import hashlib
import json
import secrets
from datetime import datetime, timedelta
from typing import Any
from urllib.parse import urlencode, urlparse

from fastapi import Request

from app.core.config import CORS_ORIGINS, SITE_URL
from app.core.logging import get_logger
from app.db.connection import get_conn, is_postgres_runtime

logger = get_logger(__name__)

ACTIVITY_STATUSES = {"draft", "live", "paused", "ended", "archived"}
ACTIVITY_TYPES = {"campus", "event", "dealer", "online", "kol", "cinegear"}
PUBLIC_EVENT_TYPES = {"page_view", "checkin", "cta_click"}
ANON_COOKIE = "vos_activity_aid"
EVENT_COOKIE = "vos_event_token"


def utcnow() -> str:
    return datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")


def _parse_ts(value: Any) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    for fmt in ("%Y-%m-%dT%H:%M:%SZ", "%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M"):
        try:
            return datetime.strptime(text[: len(fmt)], fmt)
        except Exception:
            continue
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00")).replace(tzinfo=None)
    except Exception:
        return None


def _json(value: Any) -> str:
    return json.dumps(value or {}, ensure_ascii=False, default=str)


def _row_dict(row: Any) -> dict[str, Any]:
    return dict(row) if row is not None else {}


def _int(value: Any, default: int = 0) -> int:
    try:
        return int(float(value or 0))
    except Exception:
        return default


def _ip_hash(request: Request | None) -> str:
    if request is None:
        return ""
    forwarded = str(request.headers.get("x-forwarded-for", "") or "").split(",", 1)[0].strip()
    raw = forwarded or str(getattr(request.client, "host", "") or "")
    if not raw:
        return ""
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:24]


def _request_user_agent(request: Request | None) -> str:
    if request is None:
        return ""
    return str(request.headers.get("user-agent", "") or "")[:500]


def _request_referrer(request: Request | None) -> str:
    if request is None:
        return ""
    return str(request.headers.get("referer", "") or "")[:500]


def _insert_id(cursor, table: str, unique_col: str, unique_value: Any) -> int:
    if getattr(cursor, "lastrowid", None):
        return int(cursor.lastrowid)
    row = get_conn().execute(
        f"SELECT id FROM {table} WHERE {unique_col}=? ORDER BY id DESC LIMIT 1",
        (unique_value,),
    ).fetchone()
    return int(row["id"]) if row else 0


def _safe_landing_url(raw: str, request: Request | None) -> str:
    value = str(raw or "").strip()
    if not value:
        return "/?auth=register"
    if value.startswith("/") and not value.startswith("//"):
        return value

    parsed = urlparse(value)
    if not parsed.scheme or not parsed.netloc:
        return "/?auth=register"

    allowed_hosts = set()
    if request is not None:
        host = str(request.headers.get("host") or "").strip()
        if host:
            allowed_hosts.add(host.lower())
    site_host = urlparse(SITE_URL or "").netloc
    if site_host:
        allowed_hosts.add(site_host.lower())
    for origin in CORS_ORIGINS:
        host = urlparse(str(origin or "")).netloc
        if host:
            allowed_hosts.add(host.lower())
    if parsed.netloc.lower() in allowed_hosts:
        return value
    logger.warning("activities.blocked_open_redirect", extra={"target_host": parsed.netloc})
    return "/?auth=register"


def _append_query(url: str, params: dict[str, Any]) -> str:
    sep = "&" if "?" in url else "?"
    return f"{url}{sep}{urlencode({k: v for k, v in params.items() if v not in (None, '')})}"


def _insert_event(
    *,
    activity_id: int,
    event_type: str,
    user_id: int | None = None,
    anonymous_id: str = "",
    event_token: str = "",
    payload: dict[str, Any] | None = None,
    revenue_cents: int = 0,
    commission_cents: int = 0,
    request: Request | None = None,
    idempotency_key: str = "",
) -> None:
    conn = get_conn()
    now = utcnow()
    conn.execute(
        """
        INSERT OR IGNORE INTO activity_events (
            activity_id, user_id, anonymous_id, event_token, event_type, payload_json,
            revenue_cents, commission_cents, user_agent, ip_hash, referrer,
            source_path, idempotency_key, created_at
        ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """,
        (
            activity_id,
            user_id,
            anonymous_id,
            event_token,
            event_type,
            _json(payload),
            int(revenue_cents or 0),
            int(commission_cents or 0),
            _request_user_agent(request),
            _ip_hash(request),
            _request_referrer(request),
            str(request.url.path) if request is not None else "",
            idempotency_key,
            now,
        ),
    )


def create_activity(body: dict[str, Any], *, staff_id: int | None = None) -> dict[str, Any]:
    conn = get_conn()
    now = utcnow()
    title = str(body.get("title") or "").strip()
    if not title:
        raise ValueError("title required")
    activity_type = str(body.get("activity_type") or "event").strip().lower()
    if activity_type not in ACTIVITY_TYPES:
        raise ValueError(f"invalid activity_type: {activity_type}")
    status = str(body.get("status") or "draft").strip().lower()
    if status not in ACTIVITY_STATUSES:
        raise ValueError(f"invalid status: {status}")
    starts_at = str(body.get("starts_at") or "").strip()
    ends_at = str(body.get("ends_at") or "").strip()
    if not starts_at or not ends_at:
        raise ValueError("starts_at and ends_at required")

    activity_id = str(body.get("activity_id") or "").strip() or f"EVT-{secrets.token_hex(4).upper()}"
    qr_token = secrets.token_urlsafe(18)
    budget_breakdown = body.get("budget_breakdown")
    budget_breakdown_json = (
        _json(budget_breakdown)
        if isinstance(budget_breakdown, dict)
        else str(body.get("budget_breakdown_json") or "{}")
    )
    cur = conn.execute(
        """
        INSERT INTO activities (
            activity_id, title, title_en, description, activity_type, market, country, platform,
            product_sku, dealer_name, dealer_id, location_name, location_address,
            location_lat, location_lng, starts_at, ends_at, planned_budget_cents,
            actual_spend_cents, budget_breakdown_json, qr_token, landing_url, utm_source,
            utm_campaign, status, submit_attribution_hours, purchase_attribution_hours,
            eligible_school_id, require_edu_email, created_by_staff_id, metadata_json,
            created_at, updated_at
        ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """,
        (
            activity_id,
            title,
            str(body.get("title_en") or ""),
            str(body.get("description") or ""),
            activity_type,
            str(body.get("market") or "GLOBAL").strip().upper(),
            str(body.get("country") or "").strip().upper(),
            str(body.get("platform") or "offline").strip().lower(),
            str(body.get("product_sku") or "").strip(),
            str(body.get("dealer_name") or "").strip(),
            body.get("dealer_id") or None,
            str(body.get("location_name") or "").strip(),
            str(body.get("location_address") or "").strip(),
            body.get("location_lat") or None,
            body.get("location_lng") or None,
            starts_at,
            ends_at,
            _int(body.get("planned_budget_cents")),
            _int(body.get("actual_spend_cents")),
            budget_breakdown_json,
            qr_token,
            str(body.get("landing_url") or "").strip(),
            str(body.get("utm_source") or "activity").strip() or "activity",
            str(body.get("utm_campaign") or activity_id).strip(),
            status,
            max(1, _int(body.get("submit_attribution_hours"), 168)),
            max(1, _int(body.get("purchase_attribution_hours"), 720)),
            body.get("eligible_school_id") or None,
            1 if body.get("require_edu_email") else 0,
            staff_id,
            _json(body.get("metadata") or {}),
            now,
            now,
        ),
    )
    conn.commit()
    new_id = _insert_id(cur, "activities", "activity_id", activity_id)
    return {
        "id": new_id,
        "activity_id": activity_id,
        "qr_token": qr_token,
        "qr_url": f"/api/public/event/{qr_token}",
        "qr_landing_url": f"/api/public/event/{qr_token}",
    }


def update_activity(activity_id: int, body: dict[str, Any]) -> None:
    allowed = {
        "title", "title_en", "description", "activity_type", "market", "country", "platform",
        "product_sku", "dealer_name", "dealer_id", "location_name", "location_address",
        "location_lat", "location_lng", "starts_at", "ends_at", "planned_budget_cents",
        "actual_spend_cents", "budget_breakdown_json", "landing_url", "utm_source",
        "utm_campaign", "submit_attribution_hours", "purchase_attribution_hours",
        "eligible_school_id", "require_edu_email",
    }
    updates: dict[str, Any] = {k: v for k, v in body.items() if k in allowed}
    if "budget_breakdown" in body and isinstance(body["budget_breakdown"], dict):
        updates["budget_breakdown_json"] = _json(body["budget_breakdown"])
    if not updates:
        raise ValueError("no valid fields to update")
    if "activity_type" in updates and str(updates["activity_type"]).lower() not in ACTIVITY_TYPES:
        raise ValueError("invalid activity_type")
    set_sql = ", ".join(f"{key}=?" for key in updates)
    params = list(updates.values()) + [utcnow(), int(activity_id)]
    conn = get_conn()
    cur = conn.execute(f"UPDATE activities SET {set_sql}, updated_at=? WHERE id=?", params)
    conn.commit()
    if getattr(cur, "rowcount", 0) == 0:
        raise LookupError("activity not found")


def set_activity_status(activity_id: int, action: str) -> str:
    new_status = {
        "activate": "live",
        "pause": "paused",
        "end": "ended",
        "archive": "archived",
        "draft": "draft",
    }.get(str(action or "").lower())
    if not new_status:
        raise ValueError("invalid activity action")
    conn = get_conn()
    cur = conn.execute(
        "UPDATE activities SET status=?, updated_at=? WHERE id=?",
        (new_status, utcnow(), int(activity_id)),
    )
    conn.commit()
    if getattr(cur, "rowcount", 0) == 0:
        raise LookupError("activity not found")
    return new_status


def record_scan(qr_token: str, request: Request, *, anonymous_id: str = "") -> dict[str, Any]:
    conn = get_conn()
    activity = conn.execute("SELECT * FROM activities WHERE qr_token=?", (qr_token,)).fetchone()
    if not activity:
        raise LookupError("invalid activity")
    if str(activity["status"] or "") != "live":
        raise RuntimeError(f"activity not active: {activity['status']}")

    now = utcnow()
    aid = anonymous_id or secrets.token_urlsafe(16)
    event_token = secrets.token_urlsafe(18)
    idem = f"scan:{activity['id']}:{aid}:{now[:10]}"
    _insert_event(
        activity_id=int(activity["id"]),
        event_type="scan",
        anonymous_id=aid,
        event_token=event_token,
        payload={"qr_token": qr_token, "activity_id": activity["activity_id"]},
        request=request,
        idempotency_key=idem,
    )
    existing = conn.execute(
        "SELECT id FROM activity_attributions WHERE activity_id=? AND anonymous_id=?",
        (int(activity["id"]), aid),
    ).fetchone()
    if existing:
        conn.execute(
            "UPDATE activity_attributions SET event_token=?, last_touch_at=?, updated_at=? WHERE id=?",
            (event_token, now, now, int(existing["id"])),
        )
    else:
        conn.execute(
            """
            INSERT INTO activity_attributions (
                activity_id, anonymous_id, event_token, first_scan_at, last_touch_at,
                attribution_type, user_agent, ip_hash, created_at, updated_at
            ) VALUES (?,?,?,?,?,?,?,?,?,?)
            """,
            (
                int(activity["id"]),
                aid,
                event_token,
                now,
                now,
                "first_touch",
                _request_user_agent(request),
                _ip_hash(request),
                now,
                now,
            ),
        )
    conn.commit()
    landing = _safe_landing_url(str(activity["landing_url"] or ""), request)
    redirect_url = _append_query(
        landing,
        {
            "event_token": event_token,
            "activity": str(activity["activity_id"] or ""),
            "utm_source": str(activity["utm_source"] or "activity"),
            "utm_campaign": str(activity["utm_campaign"] or activity["activity_id"] or ""),
        },
    )
    return {
        "activity": _row_dict(activity),
        "anonymous_id": aid,
        "event_token": event_token,
        "redirect_url": redirect_url,
    }


def record_public_event(qr_token: str, event_type: str, body: dict[str, Any], request: Request) -> None:
    if event_type not in PUBLIC_EVENT_TYPES:
        raise ValueError("unsupported public event_type")
    conn = get_conn()
    activity = conn.execute("SELECT * FROM activities WHERE qr_token=?", (qr_token,)).fetchone()
    if not activity:
        raise LookupError("invalid activity")
    anonymous_id = str(body.get("anonymous_id") or request.cookies.get(ANON_COOKIE) or "").strip()
    event_token = str(body.get("event_token") or request.cookies.get(EVENT_COOKIE) or "").strip()
    _insert_event(
        activity_id=int(activity["id"]),
        event_type=event_type,
        anonymous_id=anonymous_id,
        event_token=event_token,
        payload={k: v for k, v in body.items() if k not in {"user_id", "revenue_cents", "commission_cents"}},
        request=request,
    )
    conn.commit()


def record_registration(user_id: int, event_token: str, request: Request | None = None) -> None:
    token = str(event_token or "").strip()
    if not token or not user_id:
        return
    conn = get_conn()
    scan_event = conn.execute(
        """
        SELECT ae.*, a.qr_token
        FROM activity_events ae
        JOIN activities a ON a.id = ae.activity_id
        WHERE ae.event_token=?
        ORDER BY ae.created_at DESC
        LIMIT 1
        """,
        (token,),
    ).fetchone()
    activity = None
    if scan_event:
        activity = conn.execute("SELECT * FROM activities WHERE id=?", (int(scan_event["activity_id"]),)).fetchone()
    if not activity:
        activity = conn.execute("SELECT * FROM activities WHERE qr_token=?", (token,)).fetchone()
    if not activity:
        return

    now = utcnow()
    activity_pk = int(activity["id"])
    scan = _row_dict(scan_event)
    anonymous_id = str(scan.get("anonymous_id") or "").strip()
    first_scan_at = str(scan.get("created_at") or now)
    existing_user = conn.execute(
        "SELECT id FROM activity_attributions WHERE activity_id=? AND user_id=?",
        (activity_pk, int(user_id)),
    ).fetchone()
    existing_anon = None
    if anonymous_id:
        existing_anon = conn.execute(
            "SELECT id FROM activity_attributions WHERE activity_id=? AND anonymous_id=?",
            (activity_pk, anonymous_id),
        ).fetchone()
    if existing_user:
        conn.execute(
            """
            UPDATE activity_attributions
            SET first_register_at=COALESCE(first_register_at, ?),
                last_touch_at=?, event_token=?, updated_at=?
            WHERE id=?
            """,
            (now, now, token, now, int(existing_user["id"])),
        )
    elif existing_anon:
        conn.execute(
            """
            UPDATE activity_attributions
            SET user_id=?, first_register_at=COALESCE(first_register_at, ?),
                last_touch_at=?, event_token=?, updated_at=?
            WHERE id=?
            """,
            (int(user_id), now, now, token, now, int(existing_anon["id"])),
        )
    else:
        conn.execute(
            """
            INSERT INTO activity_attributions (
                activity_id, user_id, anonymous_id, event_token, first_scan_at,
                last_touch_at, first_register_at, attribution_type, user_agent,
                ip_hash, created_at, updated_at
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                activity_pk,
                int(user_id),
                anonymous_id,
                token,
                first_scan_at,
                now,
                now,
                "first_touch",
                _request_user_agent(request),
                _ip_hash(request),
                now,
                now,
            ),
        )
    _insert_event(
        activity_id=activity_pk,
        event_type="register",
        user_id=int(user_id),
        anonymous_id=anonymous_id,
        event_token=token,
        payload={"source": "auth_register"},
        request=request,
        idempotency_key=f"register:{activity_pk}:{int(user_id)}",
    )
    conn.commit()


def _eligible_attributions_for_user(user_id: int, event_kind: str) -> list[Any]:
    if not user_id:
        return []
    conn = get_conn()
    rows = conn.execute(
        """
        SELECT aa.*, a.submit_attribution_hours, a.purchase_attribution_hours
        FROM activity_attributions aa
        JOIN activities a ON a.id = aa.activity_id
        WHERE aa.user_id=?
          AND a.status IN ('live', 'paused', 'ended')
        ORDER BY COALESCE(aa.last_touch_at, aa.first_scan_at) DESC, aa.id DESC
        """,
        (int(user_id),),
    ).fetchall()
    now_dt = datetime.utcnow()
    eligible = []
    for row in rows:
        scan_dt = _parse_ts(row["first_scan_at"])
        if not scan_dt:
            continue
        raw_hours = row["purchase_attribution_hours"] if event_kind == "purchase" else row["submit_attribution_hours"]
        hours = int(raw_hours or 0)
        if scan_dt >= now_dt - timedelta(hours=max(1, hours)):
            eligible.append(row)
    return eligible


def record_submission(submission_id: int, user_id: int | None) -> None:
    if not submission_id or not user_id:
        return
    rows = _eligible_attributions_for_user(int(user_id), "submit")
    if not rows:
        return
    conn = get_conn()
    now = utcnow()
    # Direct conversion goes to last touch; first touch remains visible through mode toggle.
    target = rows[0]
    conn.execute(
        """
        UPDATE activity_attributions
        SET first_submit_at=COALESCE(first_submit_at, ?),
            submission_count=submission_count + 1,
            last_touch_at=?, attribution_type='last_touch', updated_at=?
        WHERE id=?
        """,
        (now, now, now, int(target["id"])),
    )
    _insert_event(
        activity_id=int(target["activity_id"]),
        event_type="submit",
        user_id=int(user_id),
        anonymous_id=str(target["anonymous_id"] or ""),
        event_token=str(target["event_token"] or ""),
        payload={"submission_id": int(submission_id)},
        idempotency_key=f"submit:{int(target['activity_id'])}:{int(submission_id)}",
    )
    conn.commit()


def record_purchase(
    *,
    order_id: int,
    user_id: int | None,
    revenue_cents: int,
    commission_cents: int = 0,
    external_order_id: str = "",
) -> None:
    if not order_id or not user_id:
        return
    rows = _eligible_attributions_for_user(int(user_id), "purchase")
    if not rows:
        return
    conn = get_conn()
    now = utcnow()
    target = rows[0]
    conn.execute(
        """
        UPDATE activity_attributions
        SET first_purchase_at=COALESCE(first_purchase_at, ?),
            order_count=order_count + 1,
            revenue_cents=revenue_cents + ?,
            commission_cents=commission_cents + ?,
            last_touch_at=?, attribution_type='last_touch', updated_at=?
        WHERE id=?
        """,
        (now, int(revenue_cents or 0), int(commission_cents or 0), now, now, int(target["id"])),
    )
    _insert_event(
        activity_id=int(target["activity_id"]),
        event_type="purchase",
        user_id=int(user_id),
        anonymous_id=str(target["anonymous_id"] or ""),
        event_token=str(target["event_token"] or ""),
        payload={"order_id": int(order_id), "external_order_id": external_order_id},
        revenue_cents=int(revenue_cents or 0),
        commission_cents=int(commission_cents or 0),
        idempotency_key=f"purchase:{int(target['activity_id'])}:{int(order_id)}",
    )
    conn.commit()


def manual_attribute(activity_id: int, user_id: int, admin_id: int | None, note: str) -> None:
    if not note.strip():
        raise ValueError("note required")
    conn = get_conn()
    activity = conn.execute("SELECT id FROM activities WHERE id=?", (int(activity_id),)).fetchone()
    if not activity:
        raise LookupError("activity not found")
    user = conn.execute("SELECT id FROM users WHERE id=?", (int(user_id),)).fetchone()
    if not user:
        raise LookupError("user not found")
    now = utcnow()
    existing = conn.execute(
        "SELECT id FROM activity_attributions WHERE activity_id=? AND user_id=?",
        (int(activity_id), int(user_id)),
    ).fetchone()
    if existing:
        conn.execute(
            """
            UPDATE activity_attributions
            SET attribution_type='manual', manual_attributed_by=?, manual_note=?,
                last_touch_at=?, updated_at=?
            WHERE id=?
            """,
            (admin_id, note, now, now, int(existing["id"])),
        )
    else:
        conn.execute(
            """
            INSERT INTO activity_attributions (
                activity_id, user_id, first_scan_at, last_touch_at, first_register_at,
                attribution_type, manual_attributed_by, manual_note, created_at, updated_at
            ) VALUES (?,?,?,?,?,?,?,?,?,?)
            """,
            (int(activity_id), int(user_id), now, now, now, "manual", admin_id, note, now, now),
        )
    _insert_event(
        activity_id=int(activity_id),
        event_type="manual",
        user_id=int(user_id),
        payload={"note": note, "admin_id": admin_id},
    )
    conn.commit()


def lookup_user_id_by_email(email: str | None) -> int | None:
    value = str(email or "").strip().lower()
    if not value:
        return None
    row = get_conn().execute("SELECT id FROM users WHERE LOWER(email)=?", (value,)).fetchone()
    return int(row["id"]) if row else None
