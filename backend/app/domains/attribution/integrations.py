"""External attribution ingestion for V-KPI.

This module keeps the webhook/report adapters thin and writes into the same
vkpi_sales_attributions table used by manual attribution, so dashboard numbers
stay on one ledger.
"""
from __future__ import annotations

import hashlib
import json
import os
from datetime import datetime
from typing import Any
from urllib.parse import parse_qs, urlparse

from starlette.datastructures import Headers

from app.db.connection import get_conn
from app.services.ingestion.webhooks import parse_request_body, verify_webhook_request
from app.domains import attribution
from app.domains.access import scope
from app.domains.attribution.integrations_amazon import AMAZON_COLUMN_ALIASES, import_amazon_report, parse_amazon_report_bytes
from app.platform.db.schema import ensure_vkpi_schema
from app.platform.db.schema_reconciliation import ensure_vkpi_reconciliation_schema
from app.domains.projects.workflow import staff_id


def _utcnow() -> str:
    return datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")


def _as_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _as_list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _pick(*values: Any) -> str:
    for value in values:
        text = str(value or "").strip()
        if text:
            return text
    return ""


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        return int(float(str(value or "").replace(",", "").strip() or default))
    except (TypeError, ValueError):
        return default


def _money_cents(value: Any) -> int:
    text = str(value or "").strip()
    if not text:
        return 0
    clean = text.replace("$", "").replace(",", "").replace("USD", "").strip()
    try:
        return int(round(float(clean) * 100))
    except (TypeError, ValueError):
        return 0


def _note_attributes(payload: dict[str, Any]) -> dict[str, str]:
    attrs: dict[str, str] = {}
    for item in _as_list(payload.get("note_attributes")):
        record = _as_dict(item)
        key = str(record.get("name") or record.get("key") or "").strip().lower()
        if key:
            attrs[key] = str(record.get("value") or "").strip()
    return attrs


def _query_value(url: str, names: set[str]) -> str:
    if not url:
        return ""
    parsed = urlparse(str(url or ""))
    query = parse_qs(parsed.query or "")
    for name in names:
        values = query.get(name) or []
        for value in values:
            if str(value or "").strip():
                return str(value).strip()
    return ""


def _shopify_discount_codes(payload: dict[str, Any]) -> list[str]:
    values: list[str] = []
    for item in _as_list(payload.get("discount_codes")):
        code = str(_as_dict(item).get("code") or "").strip()
        if code:
            values.append(code)
    note = _note_attributes(payload)
    for key in ("discount_code", "code", "coupon"):
        if note.get(key):
            values.append(note[key])
    return list(dict.fromkeys(values))


def _shopify_line_items(payload: dict[str, Any]) -> list[dict[str, Any]]:
    return [_as_dict(item) for item in _as_list(payload.get("line_items"))]


def _first_sku(payload: dict[str, Any]) -> str:
    for item in _shopify_line_items(payload):
        sku = str(item.get("sku") or item.get("product_id") or "").strip()
        if sku:
            return sku
    return ""


def _find_link_by_click(click_id: str) -> dict[str, Any]:
    if not click_id:
        return {}
    row = get_conn().execute(
        """
        SELECT c.id AS click_row_id,
               c.event_id,
               l.id AS link_id,
               l.project_id,
               l.kol_id,
               l.staff_id,
               l.product_sku,
               l.campaign_name
        FROM vkpi_link_clicks c
        JOIN vkpi_links l ON l.id = c.link_id
        WHERE c.event_id=?
        LIMIT 1
        """,
        (click_id,),
    ).fetchone()
    return dict(row) if row else {}


def _find_project_by_discount(discount_codes: list[str]) -> dict[str, Any]:
    if not discount_codes:
        return {}
    lowered = [code.lower() for code in discount_codes if code]
    if not lowered:
        return {}
    placeholders = ",".join("?" for _ in lowered)
    row = get_conn().execute(
        f"""
        SELECT id AS project_id,
               kol_id,
               assigned_staff_id AS staff_id,
               product_sku,
               project_name AS campaign_name
        FROM vkpi_projects
        WHERE lower(shopify_discount_code) IN ({placeholders})
        ORDER BY updated_at DESC, id DESC
        LIMIT 1
        """,
        tuple(lowered),
    ).fetchone()
    return dict(row) if row else {}


def _find_link_by_utm(utm_campaign: str, product_sku: str = "") -> dict[str, Any]:
    if not utm_campaign and not product_sku:
        return {}
    where: list[str] = []
    params: list[Any] = []
    if utm_campaign:
        where.append("lower(utm_campaign)=lower(?)")
        params.append(utm_campaign)
    if product_sku:
        where.append("lower(product_sku)=lower(?)")
        params.append(product_sku)
    row = get_conn().execute(
        f"""
        SELECT id AS link_id,
               project_id,
               kol_id,
               staff_id,
               product_sku,
               campaign_name
        FROM vkpi_links
        WHERE {' OR '.join(where)}
        ORDER BY updated_at DESC, id DESC
        LIMIT 1
        """,
        tuple(params),
    ).fetchone()
    return dict(row) if row else {}


def _shopify_ref_context(payload: dict[str, Any]) -> dict[str, Any]:
    note = _note_attributes(payload)
    landing = _pick(payload.get("landing_site"), payload.get("referring_site"), payload.get("order_status_url"))
    click_id = _pick(
        note.get("vkpi_click_id"),
        note.get("click_id"),
        _query_value(landing, {"vkpi_click_id", "click_id"}),
    )
    utm_campaign = _pick(note.get("utm_campaign"), _query_value(landing, {"utm_campaign"}))
    utm_source = _pick(note.get("utm_source"), _query_value(landing, {"utm_source"}))
    discount_codes = _shopify_discount_codes(payload)
    product_sku = _first_sku(payload)
    match = _find_link_by_click(click_id)
    match_source = "vkpi_click_id" if match else ""
    if not match:
        match = _find_project_by_discount(discount_codes)
        match_source = "discount_code" if match else ""
    if not match:
        match = _find_link_by_utm(utm_campaign, product_sku)
        match_source = "utm_or_sku" if match else ""
    return {
        "click_id": click_id,
        "landing_site": landing,
        "utm_campaign": utm_campaign,
        "utm_source": utm_source,
        "discount_codes": discount_codes,
        "product_sku": product_sku,
        "match": match,
        "match_source": match_source,
    }


def _upsert_shopify_order_snapshot(payload: dict[str, Any], raw_body: bytes) -> int | None:
    """Persist Shopify order payloads as evidence rows for GMV drilldown."""
    order_ref = _pick(payload.get("admin_graphql_api_id"), payload.get("id"), payload.get("order_number"))
    if not order_ref:
        return None
    now = _utcnow()
    raw_json = json.dumps(payload or {}, ensure_ascii=False, default=str)
    line_items = _shopify_line_items(payload)
    discounts = _shopify_discount_codes(payload)
    note_attrs = _note_attributes(payload)
    conn = get_conn()
    conn.execute(
        """
        INSERT INTO vkpi_shopify_order_snapshots (
            shopify_order_id, admin_graphql_api_id, order_name, order_number,
            processed_at, currency, subtotal_cents, total_cents, financial_status,
            fulfillment_status, refund_status, discount_codes_json, landing_site,
            note_attributes_json, line_items_json, raw_payload_hash, raw_payload_json,
            created_at, updated_at
        ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        ON CONFLICT(shopify_order_id) DO UPDATE SET
            admin_graphql_api_id=excluded.admin_graphql_api_id,
            order_name=excluded.order_name,
            order_number=excluded.order_number,
            processed_at=excluded.processed_at,
            currency=excluded.currency,
            subtotal_cents=excluded.subtotal_cents,
            total_cents=excluded.total_cents,
            financial_status=excluded.financial_status,
            fulfillment_status=excluded.fulfillment_status,
            refund_status=excluded.refund_status,
            discount_codes_json=excluded.discount_codes_json,
            landing_site=excluded.landing_site,
            note_attributes_json=excluded.note_attributes_json,
            line_items_json=excluded.line_items_json,
            raw_payload_hash=excluded.raw_payload_hash,
            raw_payload_json=excluded.raw_payload_json,
            updated_at=excluded.updated_at
        """,
        (
            str(order_ref),
            str(payload.get("admin_graphql_api_id") or ""),
            str(payload.get("name") or ""),
            str(payload.get("order_number") or ""),
            _pick(payload.get("processed_at"), payload.get("created_at"), payload.get("updated_at")),
            str(payload.get("currency") or "USD"),
            _money_cents(_pick(payload.get("current_subtotal_price"), payload.get("subtotal_price"))),
            _money_cents(_pick(payload.get("current_total_price"), payload.get("total_price"))),
            str(payload.get("financial_status") or ""),
            str(payload.get("fulfillment_status") or ""),
            str(payload.get("refund_status") or ""),
            json.dumps(discounts, ensure_ascii=False),
            str(payload.get("landing_site") or payload.get("referring_site") or ""),
            json.dumps(note_attrs, ensure_ascii=False),
            json.dumps(line_items, ensure_ascii=False, default=str),
            hashlib.sha256(raw_body or raw_json.encode("utf-8")).hexdigest(),
            raw_json,
            now,
            now,
        ),
    )
    conn.commit()
    row = conn.execute("SELECT id FROM vkpi_shopify_order_snapshots WHERE shopify_order_id=?", (str(order_ref),)).fetchone()
    return int(row["id"]) if row else None


def _unique_texts(*values: Any) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        text = str(value or "").strip()
        if text and text not in seen:
            seen.add(text)
            result.append(text)
    return result


def _shopify_order_ref_candidates(payload: dict[str, Any]) -> list[str]:
    order = _as_dict(payload.get("order"))
    return _unique_texts(
        payload.get("order_admin_graphql_api_id"),
        order.get("admin_graphql_api_id"),
        payload.get("order_id"),
        order.get("id"),
        payload.get("shopify_order_id"),
        payload.get("order_number"),
        order.get("order_number"),
        order.get("name"),
    )


def _find_shopify_snapshot_by_refs(refs: list[str]) -> dict[str, Any]:
    conn = get_conn()
    for ref in refs:
        row = conn.execute(
            """
            SELECT *
            FROM vkpi_shopify_order_snapshots
            WHERE shopify_order_id=? OR admin_graphql_api_id=? OR order_number=? OR order_name=?
            LIMIT 1
            """,
            (ref, ref, ref, ref),
        ).fetchone()
        if row:
            return dict(row)
    # Shopify refund payloads often send only the numeric order_id while order
    # snapshots may be keyed by the GraphQL id. This bounded raw-json fallback
    # keeps refunds attached to the original evidence row without changing the
    # existing table shape.
    for ref in refs:
        if not ref.isdigit():
            continue
        for pattern in (f'%"id": {ref}%', f'%"id":{ref}%', f'%"order_id": {ref}%', f'%"order_id":{ref}%'):
            row = conn.execute(
                """
                SELECT *
                FROM vkpi_shopify_order_snapshots
                WHERE raw_payload_json LIKE ?
                ORDER BY updated_at DESC, id DESC
                LIMIT 1
                """,
                (pattern,),
            ).fetchone()
            if row:
                return dict(row)
    return {}


def _find_base_shopify_attribution(snapshot_id: int | None, refs: list[str]) -> dict[str, Any]:
    conn = get_conn()
    if snapshot_id:
        row = conn.execute(
            """
            SELECT *
            FROM vkpi_sales_attributions
            WHERE source_platform='shopify'
              AND shopify_order_snapshot_id=?
              AND revenue_cents > 0
              AND confidence != 'refund'
            ORDER BY occurred_at DESC, id DESC
            LIMIT 1
            """,
            (int(snapshot_id),),
        ).fetchone()
        if row:
            return dict(row)
    for ref in refs:
        source_ref = f"shopify:{ref}"
        row = conn.execute(
            """
            SELECT *
            FROM vkpi_sales_attributions
            WHERE source_platform='shopify' AND source_ref=? AND revenue_cents > 0
            ORDER BY occurred_at DESC, id DESC
            LIMIT 1
            """,
            (source_ref,),
        ).fetchone()
        if row:
            return dict(row)
    return {}


def _mark_shopify_snapshot_refunded(snapshot_id: int | None) -> None:
    if not snapshot_id:
        return
    get_conn().execute(
        "UPDATE vkpi_shopify_order_snapshots SET refund_status='refunded', updated_at=? WHERE id=?",
        (_utcnow(), int(snapshot_id)),
    )
    get_conn().commit()


def _snapshot_public_row(row: dict[str, Any]) -> dict[str, Any]:
    item = dict(row)
    for key in ("raw_payload_json", "note_attributes_json", "line_items_json", "discount_codes_json"):
        value = item.get(key)
        if isinstance(value, str):
            try:
                item[key.replace("_json", "")] = json.loads(value or "{}")
            except json.JSONDecodeError:
                item[key.replace("_json", "")] = value
    return item


def get_shopify_order_snapshot(order_ref: str, *, staff: dict[str, Any] | None = None) -> dict[str, Any]:
    """Return a Shopify order evidence bundle with scoped attribution rows."""
    ensure_vkpi_schema()
    ref = str(order_ref or "").strip()
    if not ref:
        raise ValueError("order_ref required")
    conn = get_conn()
    row = conn.execute(
        """
        SELECT *
        FROM vkpi_shopify_order_snapshots
        WHERE CAST(id AS TEXT)=?
           OR shopify_order_id=?
           OR admin_graphql_api_id=?
           OR order_number=?
           OR order_name=?
        LIMIT 1
        """,
        (ref, ref, ref, ref, ref),
    ).fetchone()
    if not row:
        raise LookupError("shopify order snapshot not found")
    snapshot = dict(row)
    where = ["sa.shopify_order_snapshot_id=?"]
    params: list[Any] = [int(snapshot["id"])]
    scope_clause, scope_params = scope.row_staff_filter("sa", staff)
    if scope_clause:
        where.append(scope_clause)
        params.extend(scope_params)
    rows = conn.execute(
        f"""
        SELECT sa.*,
               p.project_name,
               p.project_uid,
               p.stage,
               k.channel_name AS kol_name,
               k.channel_url AS kol_url,
               l.slug AS link_slug,
               l.destination_url AS link_destination_url,
               u.name AS staff_name,
               u.email AS staff_email
        FROM vkpi_sales_attributions sa
        LEFT JOIN vkpi_projects p ON p.id = sa.project_id
        LEFT JOIN kols k ON k.id = sa.kol_id
        LEFT JOIN vkpi_links l ON l.id = sa.link_id
        LEFT JOIN staff s ON s.id = sa.staff_id
        LEFT JOIN users u ON u.id = s.user_id
        WHERE {' AND '.join(where)}
        ORDER BY sa.occurred_at DESC, sa.id DESC
        """,
        tuple(params),
    ).fetchall()
    attributions = [dict(item) for item in rows]
    if not attributions and not scope.can_view_all(staff, domain="attribution"):
        raise scope.ScopeDenied("shopify order scope denied")
    return {
        "order_snapshot": _snapshot_public_row(snapshot),
        "attributions": attributions,
        "source_row_count": len(attributions),
    }


def shopify_sync_status(body: dict[str, Any] | None = None, *, staff: dict[str, Any] | None = None, mode: str = "sync") -> dict[str, Any]:
    """Record a Shopify sync request without fabricating data when API keys are absent."""
    ensure_vkpi_schema()
    ensure_vkpi_reconciliation_schema()
    payload = body or {}
    now = _utcnow()
    shop_domain = str(payload.get("shop_domain") or os.environ.get("SHOPIFY_SHOP_DOMAIN") or "").strip()
    access_token = str(os.environ.get("SHOPIFY_ADMIN_ACCESS_TOKEN") or os.environ.get("SHOPIFY_API_ACCESS_TOKEN") or "").strip()
    sync_uid = f"shopify-{mode}-{hashlib.sha1((now + shop_domain + str(payload)).encode('utf-8')).hexdigest()[:16]}"
    configured = bool(shop_domain and access_token)
    status = "queued" if configured else "not_configured"
    metadata = {
        "mode": mode,
        "shop_domain_configured": bool(shop_domain),
        "access_token_configured": bool(access_token),
        "window": payload.get("window") or payload.get("range") or {},
        "note": "真实 Shopify Admin API 未配置时只记录请求，不生成订单或销售额。",
    }
    conn = get_conn()
    conn.execute(
        """
        INSERT INTO vkpi_shopify_sync_runs
            (sync_uid, source, started_at, completed_at, status, orders_received,
             orders_matched, orders_unmatched, orders_failed, error_message,
             triggered_by_staff_id, metadata_json)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
        """,
        (
            sync_uid,
            mode,
            now,
            now if not configured else None,
            status,
            0,
            0,
            0,
            0,
            "" if configured else "SHOPIFY_SHOP_DOMAIN/SHOPIFY_ADMIN_ACCESS_TOKEN not configured",
            staff_id(staff) or None,
            json.dumps(metadata, ensure_ascii=False, default=str),
        ),
    )
    conn.commit()
    return {
        "sync_uid": sync_uid,
        "status": status,
        "provider_status": "configured" if configured else "not_configured",
        "orders_received": 0,
        "orders_matched": 0,
        "orders_unmatched": 0,
        "message": "Shopify Admin API 已配置，可接入真实拉取任务。" if configured else "Shopify Admin API 未配置；未生成任何假订单或假销售额。",
    }


def _shopify_refund_ref(payload: dict[str, Any], order_refs: list[str]) -> str:
    refund_ref = _pick(payload.get("admin_graphql_api_id"), payload.get("id"), payload.get("refund_id"))
    if not refund_ref:
        basis = ":".join(order_refs) or json.dumps(payload, sort_keys=True, default=str)
        refund_ref = hashlib.sha256(basis.encode("utf-8")).hexdigest()[:24]
    return f"shopify:refund:{refund_ref}"


def _shopify_refund_amount(payload: dict[str, Any]) -> Any:
    for key in ("refund_amount", "amount", "total_refunded", "total_refunded_set"):
        value = payload.get(key)
        if value not in (None, ""):
            if isinstance(value, dict):
                presentment = _as_dict(value.get("presentment_money"))
                shop = _as_dict(value.get("shop_money"))
                return _pick(presentment.get("amount"), shop.get("amount"))
            return value
    total = 0.0
    found = False
    for transaction in _as_list(payload.get("transactions")):
        record = _as_dict(transaction)
        raw = record.get("amount")
        if raw in (None, ""):
            continue
        try:
            total += float(str(raw).replace("$", "").replace(",", ""))
            found = True
        except (TypeError, ValueError):
            continue
    return total if found else 0


def ingest_shopify_order_webhook(headers: Headers, raw_body: bytes, client_host: str = "") -> dict[str, Any]:
    ensure_vkpi_schema()
    auth_mode = verify_webhook_request("shopify", headers, raw_body, client_host)
    parsed = parse_request_body(raw_body, str(headers.get("content-type") or "application/json"))
    payload = _as_dict(parsed)
    if not payload:
        raise ValueError("empty shopify payload")
    topic = str(headers.get("x-shopify-topic") or "orders/create").strip().lower()
    order_ref = _pick(payload.get("admin_graphql_api_id"), payload.get("id"), payload.get("order_number"), headers.get("x-shopify-order-id"))
    if not order_ref:
        raise ValueError("shopify order id missing")
    context = _shopify_ref_context(payload)
    snapshot_id = _upsert_shopify_order_snapshot(payload, raw_body)
    match = context["match"]
    line_items = _shopify_line_items(payload)
    revenue_cents = _money_cents(
        _pick(payload.get("current_subtotal_price"), payload.get("subtotal_price"), payload.get("current_total_price"), payload.get("total_price"))
    )
    source_ref = f"shopify:{order_ref}"
    row = attribution.create_attribution(
        {
            "source_platform": "shopify",
            "source_ref": source_ref,
            "system_trusted": True,
            "project_id": match.get("project_id"),
            "link_id": match.get("link_id"),
            "kol_id": match.get("kol_id"),
            "staff_id": match.get("staff_id"),
            "shopify_order_snapshot_id": snapshot_id,
            "product_sku": context.get("product_sku") or match.get("product_sku") or "",
            "order_id": _safe_int(payload.get("id")),
            "revenue_cents": revenue_cents,
            "currency": payload.get("currency") or "USD",
            "confidence": "confirmed" if match else "unmatched",
            "occurred_at": _pick(payload.get("processed_at"), payload.get("created_at"), payload.get("updated_at")),
            "evidence": {
                "source": "shopify_webhook",
                "auth_mode": auth_mode,
                "topic": topic,
                "order_ref": order_ref,
                "match_source": context.get("match_source"),
                "click_id": context.get("click_id"),
                "utm_source": context.get("utm_source"),
                "utm_campaign": context.get("utm_campaign"),
                "discount_codes": context.get("discount_codes"),
                "landing_site": context.get("landing_site"),
                "line_items": line_items,
                "raw": payload,
            },
        }
    )["attribution"]
    if not match:
        import app.domains.attribution.reconciliation as reconciliation

        reconciliation.enqueue_unmatched(
            {
                "source_platform": "shopify",
                "source_ref": source_ref,
                "order_id": str(payload.get("id") or order_ref),
                "revenue_cents": revenue_cents,
                "currency": payload.get("currency") or "USD",
                "occurred_at": _pick(payload.get("processed_at"), payload.get("created_at"), payload.get("updated_at")),
                "product_sku": context.get("product_sku") or "",
                "discount_codes": context.get("discount_codes"),
                "utm": {"utm_source": context.get("utm_source"), "utm_campaign": context.get("utm_campaign")},
                "landing_referrer": context.get("landing_site"),
                "raw_payload": {
                    "source": "shopify_webhook",
                    "auth_mode": auth_mode,
                    "topic": topic,
                    "order_ref": order_ref,
                    "context": context,
                    "payload": payload,
                },
            }
        )
    if context.get("click_id") and row.get("order_id"):
        get_conn().execute(
            "UPDATE vkpi_link_clicks SET order_id=? WHERE event_id=?",
            (_safe_int(row.get("order_id")), str(context["click_id"])),
        )
        get_conn().commit()
    return {
        "status": "ok",
        "source": "shopify",
        "auth_mode": auth_mode,
        "topic": topic,
        "matched": bool(match),
        "match_source": context.get("match_source"),
        "shopify_order_snapshot_id": snapshot_id,
        "attribution": row,
    }


def ingest_shopify_refund_webhook(headers: Headers, raw_body: bytes, client_host: str = "") -> dict[str, Any]:
    """Verify and ingest Shopify refund webhooks as append-only negative attribution."""
    ensure_vkpi_schema()
    auth_mode = verify_webhook_request("shopify", headers, raw_body, client_host)
    parsed = parse_request_body(raw_body, str(headers.get("content-type") or "application/json"))
    payload = _as_dict(parsed)
    if not payload:
        raise ValueError("empty shopify refund payload")
    order_refs = _shopify_order_ref_candidates(payload)
    snapshot = _find_shopify_snapshot_by_refs(order_refs)
    snapshot_id = _safe_int(snapshot.get("id")) if snapshot else 0
    base = _find_base_shopify_attribution(snapshot_id or None, order_refs)
    _mark_shopify_snapshot_refunded(snapshot_id or None)
    import app.domains.attribution.reconciliation as reconciliation

    result = reconciliation.refund(
        {
            "source_platform": "shopify",
            "source_ref": _shopify_refund_ref(payload, order_refs),
            "system_trusted": True,
            "refund_usd": _shopify_refund_amount(payload),
            "order_id": payload.get("order_id"),
            "project_id": base.get("project_id"),
            "link_id": base.get("link_id"),
            "kol_id": base.get("kol_id"),
            "staff_id": base.get("staff_id"),
            "shopify_order_snapshot_id": snapshot_id or base.get("shopify_order_snapshot_id"),
            "product_sku": base.get("product_sku") or "",
            "base_attribution_id": base.get("id"),
            "occurred_at": payload.get("created_at") or payload.get("processed_at"),
            "evidence_text": "shopify refund webhook",
            "raw_payload": payload,
            "auth_mode": auth_mode,
            "topic": str(headers.get("x-shopify-topic") or "refunds/create").strip().lower(),
        }
    )
    return {
        "status": "ok",
        "source": "shopify",
        "auth_mode": auth_mode,
        "matched": bool(base),
        "shopify_order_snapshot_id": snapshot_id or base.get("shopify_order_snapshot_id"),
        "base_attribution_id": base.get("id"),
        **result,
    }
