"""Self-owned V-KPI short link center."""
from __future__ import annotations

import hashlib
import json
import os
import re
import secrets
from datetime import datetime
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

from fastapi import Request

from app.db.connection import get_conn
from app.services.vkpi import audit, scope
from app.services.vkpi.schema import ensure_vkpi_schema
from app.services.vkpi.schema_audit import ensure_vkpi_audit_schema
from app.services.vkpi.workflow import staff_id

DEFAULT_ALLOWED_HOSTS = {
    "viltrox.com",
    "www.viltrox.com",
    "viltroxstore.com",
    "www.viltroxstore.com",
    "shop.viltrox.com",
    "amazon.com",
    "www.amazon.com",
    "amzn.to",
    "bit.ly",
    "geni.us",
}
SLUG_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{2,80}$")
BOT_HINTS = ("bot", "spider", "crawler", "preview", "slurp", "facebookexternalhit", "whatsapp")


def utcnow() -> str:
    return datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")


def _json(value: Any) -> str:
    return json.dumps(value or {}, ensure_ascii=False, default=str)


def _loads(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    try:
        parsed = json.loads(str(value or "{}"))
    except Exception:
        parsed = {}
    return parsed if isinstance(parsed, dict) else {}


def _int(value: Any, default: int = 0) -> int:
    try:
        return int(value or default)
    except (TypeError, ValueError):
        return default


def _allowed_hosts() -> set[str]:
    raw = os.environ.get("VKPI_LINK_ALLOWED_HOSTS", "").strip()
    if not raw:
        return set(DEFAULT_ALLOWED_HOSTS)
    return {item.strip().lower() for item in raw.split(",") if item.strip()}


def _is_allowed_destination(url: str) -> bool:
    parsed = urlparse(str(url or "").strip())
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        return False
    host = parsed.netloc.lower().split("@")[-1].split(":", 1)[0]
    if host in _allowed_hosts():
        return True
    return host.endswith(".amazon.com") or host.endswith(".viltrox.com")


def _request_ip_hash(request: Request) -> str:
    forwarded = str(request.headers.get("x-forwarded-for") or "").split(",", 1)[0].strip()
    raw = forwarded or str(getattr(request.client, "host", "") or "")
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:24] if raw else ""


def _is_bot_user_agent(user_agent: str) -> tuple[bool, int]:
    lower = str(user_agent or "").lower()
    hit = any(token in lower for token in BOT_HINTS)
    return hit, 80 if hit else 0


def _device_type(user_agent: str) -> str:
    lower = str(user_agent or "").lower()
    if any(token in lower for token in ("iphone", "android", "mobile")):
        return "mobile"
    if any(token in lower for token in ("ipad", "tablet")):
        return "tablet"
    return "desktop" if lower else "unknown"


def _generate_slug() -> str:
    return f"k-{secrets.token_urlsafe(5).replace('_', '').replace('-', '')[:8]}"


def _append_tracking_params(destination: str, link: dict[str, Any], click_id: str) -> str:
    parsed = urlparse(destination)
    params = dict(parse_qsl(parsed.query, keep_blank_values=True))
    for key in ("utm_source", "utm_medium", "utm_campaign", "utm_content"):
        value = str(link.get(key) or "").strip()
        if value and key not in params:
            params[key] = value
    params.setdefault("vkpi_click_id", click_id)
    metadata_raw = str(link.get("metadata_json") or "{}")
    try:
        metadata = json.loads(metadata_raw) if metadata_raw else {}
    except Exception:
        metadata = {}
    amazon_tag = str(metadata.get("amazon_tag") or metadata.get("amazon_campaign_id") or "").strip()
    if amazon_tag and "tag" not in params and "amazon." in parsed.netloc.lower():
        params["tag"] = amazon_tag
    return urlunparse(parsed._replace(query=urlencode(params)))


def _audit_metadata(link: dict[str, Any], extra: dict[str, Any] | None = None) -> dict[str, Any]:
    metadata = _loads(link.get("metadata_json"))
    payload: dict[str, Any] = {
        "link_id": link.get("id"),
        "slug": link.get("slug"),
        "project_id": link.get("project_id"),
        "kol_id": link.get("kol_id"),
        "staff_id": link.get("staff_id"),
        "product_sku": link.get("product_sku"),
        "campaign_name": link.get("campaign_name"),
        "destination_url": link.get("destination_url"),
        "status": link.get("status"),
    }
    if metadata.get("marker"):
        payload["marker"] = metadata.get("marker")
    payload["link_metadata"] = metadata
    payload.update(extra or {})
    return payload


def _log_link_event(
    *,
    staff: dict[str, Any] | None,
    action_type: str,
    link: dict[str, Any],
    detail: str,
    metadata: dict[str, Any] | None = None,
) -> None:
    actor = staff_id(staff) or _int(link.get("created_by_staff_id") or link.get("staff_id"))
    if not actor or not link.get("id"):
        return
    audit.log_business_event(
        staff_id=actor,
        action_type=action_type,
        target_type="link",
        target_id=str(link.get("id")),
        detail=detail,
        metadata=_audit_metadata(link, metadata),
    )


def list_links(limit: int = 50, status: str = "", *, staff: dict[str, Any] | None = None, staff_id_filter: int | None = None) -> dict[str, Any]:
    ensure_vkpi_schema()
    conn = get_conn()
    limit_i = max(1, min(200, int(limit or 50)))
    params: list[Any] = []
    where_parts: list[str] = []
    if status:
        where_parts.append("l.status=?")
        params.append(status)
    scope_clause, scope_params = scope.link_filter("l", staff, staff_id_filter)
    if scope_clause:
        where_parts.append(scope_clause)
        params.extend(scope_params)
    where = f"WHERE {' AND '.join(where_parts)}" if where_parts else ""
    rows = conn.execute(
        f"""
        SELECT l.*,
               k.channel_name AS kol_name,
               p.project_name AS project_name
        FROM vkpi_links l
        LEFT JOIN kols k ON k.id = l.kol_id
        LEFT JOIN vkpi_projects p ON p.id = l.project_id
        {where}
        ORDER BY l.updated_at DESC, l.id DESC
        LIMIT ?
        """,
        (*params, limit_i),
    ).fetchall()
    return {"links": [dict(row) for row in rows]}


def link_clicks(link_id: int, *, staff: dict[str, Any] | None = None, limit: int = 100) -> dict[str, Any]:
    """Return real click rows for one V-KPI short link."""
    ensure_vkpi_schema()
    scope.assert_link_access(link_id, staff)
    click_limit_i = max(1, min(500, int(limit or 100)))
    conn = get_conn()
    rows = [
        dict(item)
        for item in conn.execute(
            """
            SELECT id, event_id, clicked_at, referrer, country_code, device_type,
                   bot_score, is_bot, is_unique, session_id, destination_url,
                   order_id, metadata_json
            FROM vkpi_link_clicks
            WHERE link_id=?
            ORDER BY clicked_at DESC, id DESC
            LIMIT ?
            """,
            (int(link_id), click_limit_i),
        ).fetchall()
    ]
    valid_count = sum(1 for row in rows if not _int(row.get("is_bot")))
    bot_count = sum(1 for row in rows if _int(row.get("is_bot")))
    return {
        "link_id": int(link_id),
        "clicks": rows,
        "summary": {
            "click_count": len(rows),
            "valid_click_count": valid_count,
            "bot_click_count": bot_count,
            "unique_click_count": sum(1 for row in rows if _int(row.get("is_unique"))),
        },
    }


def link_orders(link_id: int, *, staff: dict[str, Any] | None = None, limit: int = 100) -> dict[str, Any]:
    """Return sales attribution and Shopify order evidence for one short link."""
    ensure_vkpi_schema()
    scope.assert_link_access(link_id, staff)
    order_limit_i = max(1, min(500, int(limit or 100)))
    conn = get_conn()
    sales = [
        dict(item)
        for item in conn.execute(
            """
            SELECT sa.*,
                   p.project_name,
                   k.channel_name AS kol_name,
                   os.shopify_order_id,
                   os.order_name,
                   os.order_number,
                   os.processed_at AS shopify_processed_at,
                   os.currency AS order_currency,
                   os.subtotal_cents AS order_subtotal_cents,
                   os.total_cents AS order_total_cents,
                   os.financial_status,
                   os.fulfillment_status,
                   os.refund_status,
                   os.landing_site
            FROM vkpi_sales_attributions sa
            LEFT JOIN vkpi_projects p ON p.id = sa.project_id
            LEFT JOIN kols k ON k.id = sa.kol_id
            LEFT JOIN vkpi_shopify_order_snapshots os ON os.id = sa.shopify_order_snapshot_id
            WHERE sa.link_id=?
            ORDER BY sa.occurred_at DESC, sa.id DESC
            LIMIT ?
            """,
            (int(link_id), order_limit_i),
        ).fetchall()
    ]
    orders = [
        {
            "sales_attribution_id": item.get("id"),
            "shopify_order_snapshot_id": item.get("shopify_order_snapshot_id"),
            "shopify_order_id": item.get("shopify_order_id"),
            "order_id": item.get("order_id"),
            "order_name": item.get("order_name"),
            "order_number": item.get("order_number"),
            "processed_at": item.get("shopify_processed_at") or item.get("occurred_at"),
            "currency": item.get("order_currency") or item.get("currency"),
            "financial_status": item.get("financial_status"),
            "fulfillment_status": item.get("fulfillment_status"),
            "refund_status": item.get("refund_status"),
            "landing_site": item.get("landing_site"),
            "revenue_cents": item.get("revenue_cents"),
            "source_platform": item.get("source_platform"),
            "source_ref": item.get("source_ref"),
            "confidence": item.get("confidence"),
            "project_id": item.get("project_id"),
            "project_name": item.get("project_name"),
            "kol_id": item.get("kol_id"),
            "kol_name": item.get("kol_name"),
            "staff_id": item.get("staff_id"),
        }
        for item in sales
        if item.get("shopify_order_snapshot_id") or item.get("order_id") or item.get("source_ref")
    ]
    revenue_cents = sum(_int(item.get("revenue_cents")) for item in sales)
    return {
        "link_id": int(link_id),
        "sales_attributions": sales,
        "orders": orders,
        "summary": {
            "order_count": len(orders),
            "attribution_count": len(sales),
            "revenue_cents": revenue_cents,
        },
    }


def link_detail(link_id: int, *, staff: dict[str, Any] | None = None, click_limit: int = 100) -> dict[str, Any]:
    ensure_vkpi_schema()
    scope.assert_link_access(link_id, staff)
    conn = get_conn()
    row = conn.execute(
        """
        SELECT l.*,
               k.channel_name AS kol_name,
               k.channel_url AS kol_url,
               k.platform AS kol_platform,
               p.project_name AS project_name,
               p.product_sku AS project_product_sku,
               u.name AS staff_name,
               u.email AS staff_email,
               u.avatar_url AS staff_avatar_url
        FROM vkpi_links l
        LEFT JOIN kols k ON k.id = l.kol_id
        LEFT JOIN vkpi_projects p ON p.id = l.project_id
        LEFT JOIN staff st ON st.id = l.staff_id
        LEFT JOIN users u ON u.id = st.user_id
        WHERE l.id=?
        """,
        (int(link_id),),
    ).fetchone()
    if not row:
        raise LookupError("link not found")
    click_payload = link_clicks(link_id, staff=staff, limit=click_limit)
    order_payload = link_orders(link_id, staff=staff, limit=200)
    clicks = click_payload["clicks"]
    sales = order_payload["sales_attributions"]
    orders = order_payload["orders"]
    link = dict(row)
    revenue_cents = _int(order_payload.get("summary", {}).get("revenue_cents"))
    audit_events: list[dict[str, Any]] = []
    if scope.can_view_all(staff, domain="audit"):
        ensure_vkpi_audit_schema()
        audit_events = [
            dict(item)
            for item in conn.execute(
                """
                SELECT ba.*, s.name AS staff_name, u.email AS staff_email
                FROM vkpi_business_audit_logs ba
                LEFT JOIN staff st ON st.id = ba.staff_id
                LEFT JOIN users s ON s.id = st.user_id
                LEFT JOIN users u ON u.id = st.user_id
                WHERE (ba.target_type=? AND ba.target_id=?)
                   OR ba.metadata_json LIKE ?
                   OR ba.metadata_json LIKE ?
                ORDER BY ba.created_at DESC, ba.id DESC
                LIMIT 100
                """,
                ("link", str(int(link_id)), f'%"link_id": {int(link_id)}%', f'%"link_id":"{int(link_id)}"%'),
            ).fetchall()
        ]
    return {
        "link": link,
        "project": {
            "id": link.get("project_id"),
            "project_name": link.get("project_name"),
            "product_sku": link.get("project_product_sku") or link.get("product_sku"),
        },
        "kol": {
            "id": link.get("kol_id"),
            "channel_name": link.get("kol_name"),
            "channel_url": link.get("kol_url"),
            "platform": link.get("kol_platform") or link.get("platform"),
        },
        "owner": {
            "id": link.get("staff_id"),
            "name": link.get("staff_name"),
            "email": link.get("staff_email"),
            "avatar_url": link.get("staff_avatar_url"),
        },
        "clicks": clicks,
        "sales_attributions": sales,
        "orders": orders,
        "summary": {
            "click_count": _int(link.get("click_count")) or _int(click_payload.get("summary", {}).get("click_count")),
            "valid_click_count": _int(link.get("valid_click_count")) or _int(click_payload.get("summary", {}).get("valid_click_count")),
            "bot_click_count": _int(link.get("bot_click_count")) or _int(click_payload.get("summary", {}).get("bot_click_count")),
            "orders": len(orders),
            "revenue_cents": revenue_cents,
        },
        "audit_events": audit_events,
    }


def create_link(body: dict[str, Any], *, staff: dict[str, Any] | None = None) -> dict[str, Any]:
    ensure_vkpi_schema()
    destination_url = str(body.get("destination_url") or "").strip()
    if not _is_allowed_destination(destination_url):
        raise ValueError("destination_url is not allowed")
    fallback_url = str(body.get("fallback_url") or "").strip()
    if fallback_url and not _is_allowed_destination(fallback_url):
        raise ValueError("fallback_url is not allowed")
    slug = str(body.get("slug") or _generate_slug()).strip()
    if not SLUG_RE.match(slug):
        raise ValueError("invalid slug")
    now = utcnow()
    actor_staff_id = staff_id(staff)
    owner_staff_id = _int(body.get("staff_id"), actor_staff_id) or None
    if not scope.can_view_all(staff):
        owner_staff_id = actor_staff_id or None
    if _int(body.get("project_id")):
        scope.assert_project_access(_int(body.get("project_id")), staff, write=True)
    link_uid = str(body.get("link_uid") or f"VL-{secrets.token_hex(5).upper()}").strip()
    conn = get_conn()
    if conn.execute("SELECT id FROM vkpi_links WHERE slug=?", (slug,)).fetchone():
        raise ValueError("slug already exists")
    conn.execute(
        """
        INSERT INTO vkpi_links (
            link_uid, slug, link_type, destination_url, fallback_url, platform,
            product_sku, campaign_name, kol_id, project_id, staff_id,
            created_by_staff_id, status, redirect_mode, allowlist_status,
            bot_filter_mode, utm_source, utm_medium, utm_campaign, utm_content,
            starts_at, expires_at, metadata_json, created_at, updated_at
        ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """,
        (
            link_uid,
            slug,
            str(body.get("link_type") or "generic"),
            destination_url,
            fallback_url,
            str(body.get("platform") or ""),
            str(body.get("product_sku") or ""),
            str(body.get("campaign_name") or ""),
            _int(body.get("kol_id")) or None,
            _int(body.get("project_id")) or None,
            owner_staff_id,
            actor_staff_id or None,
            str(body.get("status") or "live"),
            _int(body.get("redirect_mode"), 302) or 302,
            "allowed",
            str(body.get("bot_filter_mode") or "standard"),
            str(body.get("utm_source") or ""),
            str(body.get("utm_medium") or ""),
            str(body.get("utm_campaign") or ""),
            str(body.get("utm_content") or ""),
            body.get("starts_at"),
            body.get("expires_at"),
            _json(body.get("metadata")),
            now,
            now,
        ),
    )
    conn.commit()
    row = conn.execute("SELECT * FROM vkpi_links WHERE slug=?", (slug,)).fetchone()
    result = dict(row) if row else {"slug": slug, "link_uid": link_uid}
    if row:
        _log_link_event(
            staff=staff,
            action_type="link_create",
            link=result,
            detail=f"创建短链 {result.get('slug')}",
            metadata={"source": "link_center.create_link"},
        )
    return result


def update_link(
    link_id: int,
    body: dict[str, Any],
    *,
    staff: dict[str, Any] | None = None,
    action_type: str = "link_update",
) -> dict[str, Any]:
    ensure_vkpi_schema()
    scope.assert_link_access(link_id, staff, write=True)
    conn = get_conn()
    row = conn.execute("SELECT * FROM vkpi_links WHERE id=?", (int(link_id),)).fetchone()
    if not row:
        raise LookupError("link not found")
    updates: list[str] = []
    params: list[Any] = []
    allowed = {
        "destination_url",
        "fallback_url",
        "status",
        "platform",
        "product_sku",
        "campaign_name",
        "utm_source",
        "utm_medium",
        "utm_campaign",
        "utm_content",
        "expires_at",
        "metadata_json",
    }
    payload = dict(body)
    if isinstance(payload.get("metadata"), dict):
        payload["metadata_json"] = _json(payload.pop("metadata"))
    for key in allowed:
        if key not in payload:
            continue
        value = payload[key]
        if key in {"destination_url", "fallback_url"} and value and not _is_allowed_destination(str(value)):
            raise ValueError(f"{key} is not allowed")
        updates.append(f"{key}=?")
        params.append(value)
    if not updates:
        return dict(row)
    before = dict(row)
    updates.append("updated_at=?")
    params.append(utcnow())
    params.append(int(link_id))
    conn.execute(f"UPDATE vkpi_links SET {', '.join(updates)} WHERE id=?", tuple(params))
    conn.commit()
    fresh = conn.execute("SELECT * FROM vkpi_links WHERE id=?", (int(link_id),)).fetchone()
    result = dict(fresh) if fresh else {}
    if result:
        changed_keys = sorted(key for key in allowed if key in payload)
        _log_link_event(
            staff=staff,
            action_type=action_type,
            link=result,
            detail=f"更新短链 {result.get('slug')}：{','.join(changed_keys)}",
            metadata={
                "source": "link_center.update_link",
                "changed_fields": changed_keys,
                "previous_status": before.get("status"),
                "new_status": result.get("status"),
            },
        )
    return result


def set_status(link_id: int, status: str, *, staff: dict[str, Any] | None = None) -> dict[str, Any]:
    clean = str(status or "").strip().lower()
    if clean not in {"live", "paused", "archived"}:
        raise ValueError("unsupported link status")
    action = {"paused": "link_pause", "archived": "link_archive", "live": "link_resume"}[clean]
    return update_link(link_id, {"status": clean}, staff=staff, action_type=action)


def health_check(link_id: int, *, staff: dict[str, Any] | None = None) -> dict[str, Any]:
    ensure_vkpi_schema()
    conn = get_conn()
    row = conn.execute("SELECT * FROM vkpi_links WHERE id=?", (int(link_id),)).fetchone()
    if not row:
        raise LookupError("link not found")
    link = dict(row)
    ok = _is_allowed_destination(str(link.get("destination_url") or ""))
    status = "healthy" if ok else "blocked_destination"
    now = utcnow()
    conn.execute(
        "UPDATE vkpi_links SET health_status=?, last_health_checked_at=?, updated_at=? WHERE id=?",
        (status, now, now, int(link_id)),
    )
    conn.commit()
    refreshed = conn.execute("SELECT * FROM vkpi_links WHERE id=?", (int(link_id),)).fetchone()
    if refreshed:
        _log_link_event(
            staff=staff,
            action_type="link_health_check",
            link=dict(refreshed),
            detail=f"短链健康检查 {link.get('slug')}：{status}",
            metadata={"source": "link_center.health_check", "ok": ok, "health_status": status},
        )
    return {"id": int(link_id), "ok": ok, "health_status": status, "checked_at": now}


def resolve_link(slug: str, request: Request, *, session_id: str = "") -> dict[str, Any]:
    ensure_vkpi_schema()
    clean_slug = str(slug or "").strip()
    conn = get_conn()
    row = conn.execute("SELECT * FROM vkpi_links WHERE slug=?", (clean_slug,)).fetchone()
    if not row:
        raise LookupError("link not found")
    link = dict(row)
    if str(link.get("status") or "") != "live":
        raise RuntimeError(f"link not live: {link.get('status')}")
    destination = str(link.get("destination_url") or link.get("fallback_url") or "").strip()
    if not _is_allowed_destination(destination):
        destination = str(link.get("fallback_url") or "").strip()
    if not destination:
        raise RuntimeError("link has no valid destination")

    user_agent = str(request.headers.get("user-agent") or "")[:500]
    referrer = str(request.headers.get("referer") or "")[:500]
    bot, bot_score = _is_bot_user_agent(user_agent)
    sid = session_id or secrets.token_urlsafe(16)
    event_id = f"clk_{secrets.token_urlsafe(18)}"
    now = utcnow()
    conn.execute(
        """
        INSERT INTO vkpi_link_clicks (
            link_id, event_id, clicked_at, ip_hash, user_agent, referrer,
            country_code, device_type, bot_score, is_bot, is_unique,
            session_id, destination_url, metadata_json
        ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """,
        (
            int(link["id"]),
            event_id,
            now,
            _request_ip_hash(request),
            user_agent,
            referrer,
            str(request.headers.get("cf-ipcountry") or "")[:12],
            _device_type(user_agent),
            bot_score,
            1 if bot else 0,
            0,
            sid,
            destination,
            "{}",
        ),
    )
    conn.execute(
        """
        UPDATE vkpi_links
        SET click_count = click_count + 1,
            valid_click_count = valid_click_count + ?,
            bot_click_count = bot_click_count + ?,
            updated_at = ?
        WHERE id=?
        """,
        (0 if bot else 1, 1 if bot else 0, now, int(link["id"])),
    )
    conn.commit()
    return {
        "destination_url": _append_tracking_params(destination, link, event_id),
        "redirect_mode": _int(link.get("redirect_mode"), 302) or 302,
        "session_id": sid,
        "event_id": event_id,
        "is_bot": bot,
    }
