"""External attribution ingestion for V-KPI.

This module keeps the webhook/report adapters thin and writes into the same
vkpi_sales_attributions table used by manual attribution, so dashboard numbers
stay on one ledger.
"""
from __future__ import annotations

import hashlib
import json
import os
from typing import Any

from starlette.datastructures import Headers

from app.db.connection import get_conn
from app.services.ingestion.webhooks import parse_request_body, verify_webhook_request
from app.domains import attribution
from app.domains.access import scope
from app.domains.attribution.integrations_amazon import AMAZON_COLUMN_ALIASES, import_amazon_report, parse_amazon_report_bytes
from app.platform.db.schema import ensure_vkpi_schema
from app.platform.db.schema_reconciliation import ensure_vkpi_reconciliation_schema
from app.domains.projects.workflow import staff_id

from app.core.logging import get_logger

logger = get_logger(__name__)

SHOPIFY_GMV_FINANCIAL_STATUSES = frozenset({"paid", "partially_paid", "partially_refunded"})

# Shopify payload parsing + attribution-context helpers live in a sibling module
# (verbatim move); re-exported here so internal callers and tests keep using the
# bare names unchanged.
from app.domains.attribution.integrations_shopify_parse import (  # noqa: E402
    _as_dict,
    _as_list,
    _find_event_by_discount,
    _find_link_by_click,
    _find_link_by_utm,
    _find_project_by_discount,
    _first_sku,
    _money_cents,
    _note_attributes,
    _pick,
    _query_value,
    _safe_int,
    _shopify_discount_codes,
    _shopify_line_items,
    _shopify_ref_context,
    _utcnow,
)


def _upsert_shopify_order_snapshot(
    payload: dict[str, Any],
    raw_body: bytes,
    *,
    auth_mode: str,
) -> int | None:
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
            fulfillment_status, refund_status, cancelled_at, provider_auth_mode,
            provider_verified_at, discount_codes_json, landing_site,
            note_attributes_json, line_items_json, raw_payload_hash, raw_payload_json,
            created_at, updated_at
        ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
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
            refund_status=COALESCE(NULLIF(excluded.refund_status,''),vkpi_shopify_order_snapshots.refund_status),
            cancelled_at=excluded.cancelled_at,
            provider_auth_mode=excluded.provider_auth_mode,
            provider_verified_at=excluded.provider_verified_at,
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
            _money_cents(_pick(payload.get("subtotal_price"), payload.get("current_subtotal_price"))),
            _money_cents(_pick(payload.get("total_price"), payload.get("subtotal_price"), payload.get("current_total_price"))),
            str(payload.get("financial_status") or ""),
            str(payload.get("fulfillment_status") or ""),
            str(payload.get("refund_status") or ""),
            _pick(payload.get("cancelled_at"), payload.get("canceled_at")) or None,
            str(auth_mode or ""),
            now if auth_mode == "shopify-hmac" else None,
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


def _shopify_order_is_gmv_eligible(payload: dict[str, Any], topic: str) -> bool:
    """Return whether a native Shopify order event may enter attributed GMV."""

    financial_status = str(payload.get("financial_status") or "").strip().lower()
    normalized_topic = str(topic or "").strip().lower()
    cancelled = bool(
        payload.get("cancelled_at")
        or payload.get("canceled_at")
        or payload.get("cancel_reason")
        or payload.get("cancelled") is True
        or payload.get("canceled") is True
        or normalized_topic in {"orders/cancelled", "orders/canceled", "orders/voided"}
    )
    return financial_status in SHOPIFY_GMV_FINANCIAL_STATUSES and not cancelled


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
    for ref in _unique_texts(*refs, *(f"gid://shopify/Order/{ref}" for ref in refs if ref.isdigit())):
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
            candidates = conn.execute(
                """
                SELECT *
                FROM vkpi_shopify_order_snapshots
                WHERE raw_payload_json LIKE ?
                ORDER BY updated_at DESC, id DESC
                LIMIT 20
                """,
                (pattern,),
            ).fetchall()
            for row in candidates:
                item = dict(row)
                try:
                    raw = json.loads(item.get("raw_payload_json") or "{}")
                except (TypeError, ValueError):
                    continue
                if str(raw.get("id") or "") == ref:
                    return item
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
        """UPDATE vkpi_shopify_order_snapshots SET refund_status=CASE WHEN
           (SELECT -COALESCE(SUM(revenue_cents),0) FROM vkpi_sales_attributions
            WHERE shopify_order_snapshot_id=? AND source_platform='shopify' AND confidence='refund')
            >= total_cents THEN 'refunded' ELSE 'partially_refunded' END, updated_at=? WHERE id=?""",
        (int(snapshot_id), _utcnow(), int(snapshot_id)),
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
    from app.domains.commerce import shopify_connect

    credentials = shopify_connect.get_credentials()
    shop_domain = str(credentials.get("shop_domain") or "").strip()
    access_token = str(credentials.get("access_token") or "").strip()
    sync_uid = f"shopify-{mode}-{hashlib.sha1((now + shop_domain + str(payload)).encode('utf-8')).hexdigest()[:16]}"
    configured = bool(shop_domain and access_token)
    status = "not_queued" if configured else "not_configured"
    metadata = {
        "mode": mode,
        "shop_domain_configured": bool(shop_domain),
        "access_token_configured": bool(access_token),
        "credential_source": credentials.get("source") or "none",
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
            now,
            status,
            0,
            0,
            0,
            0,
            "Use the async sync/backfill endpoint with a durable queue; direct status recording does not enqueue" if configured else "Shopify credentials not configured in encrypted DB or environment",
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
        "message": "凭据已配置；请使用异步 sync/backfill API，当前直接记录状态的调用未加入队列。" if configured else "Shopify Admin API 未配置；未生成任何假订单或假销售额。",
    }


def list_shopify_sync_runs(limit: int = 10) -> list[dict[str, Any]]:
    """Return the most recent Shopify sync run rows for the connect-page history table."""
    ensure_vkpi_reconciliation_schema()
    safe_limit = max(1, min(int(limit or 10), 100))
    rows = get_conn().execute(
        """
        SELECT id, sync_uid, source, started_at, completed_at, status,
               orders_received, orders_matched, orders_unmatched, orders_failed,
               error_message, triggered_by_staff_id, metadata_json
        FROM vkpi_shopify_sync_runs
        ORDER BY started_at DESC, id DESC
        LIMIT ?
        """,
        (safe_limit,),
    ).fetchall()
    runs: list[dict[str, Any]] = []
    for row in rows:
        item = dict(row)
        meta_raw = item.pop("metadata_json", None)
        try:
            item["metadata"] = json.loads(meta_raw) if isinstance(meta_raw, str) and meta_raw else {}
        except json.JSONDecodeError:
            item["metadata"] = {}
        runs.append(item)
    return runs


def shopify_provider_status(*, limit: int = 10) -> dict[str, Any]:
    """Read-only Shopify connection status for the connect page.

    Reports whether SHOPIFY_SHOP_DOMAIN + SHOPIFY_ADMIN_ACCESS_TOKEN are present
    in the environment without writing a sync run or fabricating any orders.
    """
    ensure_vkpi_reconciliation_schema()
    from app.domains.commerce import shopify_connect

    connection = shopify_connect.connection_status()
    shop_domain = str(connection.get("shop_domain") or "").strip()
    configured = bool(shop_domain and connection.get("token_configured"))
    provider_status = "connected" if connection.get("status") == "connected" else ("configured" if configured else "not_configured")
    return {
        "provider_status": provider_status,
        "shop_domain": shop_domain,
        "shop_domain_configured": bool(shop_domain),
        "access_token_configured": bool(connection.get("token_configured")),
        "webhook_secret_configured": bool(connection.get("webhook_secret_configured")),
        "credential_source": connection.get("source") or "none",
        "credential_fields": {
            "shop_domain": bool(shop_domain),
            "access_token": bool(connection.get("token_configured")),
            "webhook_secret": bool(connection.get("webhook_secret_configured")),
        },
        "webhooks": {
            "orders_create": "/api/vkpi/webhooks/shopify/orders",
            "orders_refund_create": "/api/vkpi/webhooks/shopify/refunds",
        },
        "sync_runs": list_shopify_sync_runs(limit=limit),
        "message": (
            "Shopify Admin API 已配置，可接入真实拉取任务。"
            if configured
            else "Shopify Admin API 未配置；设置 SHOPIFY_SHOP_DOMAIN 与 SHOPIFY_ADMIN_ACCESS_TOKEN 后即可连接。"
        ),
    }


def _shopify_refund_ref(payload: dict[str, Any], order_refs: list[str]) -> str:
    refund_ref = _pick(payload.get("admin_graphql_api_id"), payload.get("id"), payload.get("refund_id"))
    if not refund_ref:
        raise ValueError("shopify refund id required for replay safety")
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
    auth_mode = verify_webhook_request("shopify", headers, raw_body, client_host)
    if auth_mode != "shopify-hmac":
        raise PermissionError("shopify native hmac required")
    parsed = parse_request_body(raw_body, str(headers.get("content-type") or "application/json"))
    payload = _as_dict(parsed)
    if not payload:
        raise ValueError("empty shopify payload")
    topic = str(headers.get("x-shopify-topic") or "orders/create").strip().lower()
    order_ref = _pick(payload.get("admin_graphql_api_id"), payload.get("id"), payload.get("order_number"), headers.get("x-shopify-order-id"))
    if not order_ref:
        raise ValueError("shopify order id missing")
    if topic not in {"orders/create", "orders/updated", "orders/paid", "orders/cancelled"}:
        raise ValueError("unsupported Shopify order topic")
    from app.domains.attribution.integrations_money import currency_code, exact_cents

    currency = currency_code(payload.get("currency"))
    # This ledger has separate negative refund rows. Keep the original order
    # amount, not current_* (which already subtracts refunds).
    revenue_cents = exact_cents(_pick(payload.get("total_price"), payload.get("subtotal_price")))
    ensure_vkpi_schema()
    context = _shopify_ref_context(payload)
    snapshot_id = _upsert_shopify_order_snapshot(payload, raw_body, auth_mode=auth_mode)
    match = context["match"]
    financially_eligible = _shopify_order_is_gmv_eligible(payload, topic)
    line_items = _shopify_line_items(payload)
    source_ref = f"shopify:{order_ref}"
    row = attribution.create_attribution(
        {
            "source_platform": "shopify",
            "source_ref": source_ref,
            "project_id": match.get("project_id"),
            "link_id": match.get("link_id"),
            "kol_id": match.get("kol_id"),
            "staff_id": match.get("staff_id"),
            "shopify_order_snapshot_id": snapshot_id,
            "product_sku": context.get("product_sku") or match.get("product_sku") or "",
            "order_id": _safe_int(payload.get("id")),
            "revenue_cents": revenue_cents,
            "currency": currency,
            "confidence": "confirmed" if match and financially_eligible else "unmatched",
            "occurred_at": _pick(payload.get("processed_at"), payload.get("created_at"), payload.get("updated_at")),
            "evidence": {
                "source": "shopify_webhook",
                "auth_mode": auth_mode,
                "topic": topic,
                "financial_status": str(payload.get("financial_status") or ""),
                "financially_eligible": financially_eligible,
                "amount_basis": "original_order_total_with_separate_refund_deltas",
                "order_ref": order_ref,
                "source_order_id": order_ref if order_ref.startswith("gid://") else f"gid://shopify/Order/{order_ref}",
                "match_source": context.get("match_source"),
                "click_id": context.get("click_id"),
                "utm_source": context.get("utm_source"),
                "utm_campaign": context.get("utm_campaign"),
                "discount_codes": context.get("discount_codes"),
                "event_id": context.get("event_id") or "",
                "landing_site": context.get("landing_site"),
                "line_items": line_items,
                "raw": payload,
            },
        },
        # A valid Shopify HMAC proves the order exists, but an unmatched order
        # is not yet attributable to a KOL/project.  Keep it as provider
        # evidence without promoting it into attributed GMV.
        ingest_class="provider_verified" if match and financially_eligible else "provider_observed",
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
    # GMV 归因桥(no-op until token):把这条订单的归因促升回对应推荐的 outcome
    # (order_attributed + GMV)。缺 shpat_ token 时内部直接 no-op,绝不抛、不写脏数据;
    # 任何异常都被吞,绝不让 outcome 桥拖垮 webhook 主路径。
    try:
        from app.domains.attribution import gmv_outcome_bridge

        if financially_eligible:
            gmv_outcome_bridge.handle_attribution_row(row)
    except Exception:
        logger.debug("ingest_shopify_order_webhook.gmv_outcome_bridge_failed", exc_info=True)
    return {
        "status": "ok",
        "source": "shopify",
        "auth_mode": auth_mode,
        "topic": topic,
        "matched": bool(match),
        "financially_eligible": financially_eligible,
        "match_source": context.get("match_source"),
        "event_id": context.get("event_id") or "",
        "shopify_order_snapshot_id": snapshot_id,
        "attribution": row,
    }


def ingest_shopify_refund_webhook(headers: Headers, raw_body: bytes, client_host: str = "") -> dict[str, Any]:
    """Verify and ingest Shopify refund webhooks as append-only negative attribution."""
    auth_mode = verify_webhook_request("shopify", headers, raw_body, client_host)
    if auth_mode != "shopify-hmac":
        raise PermissionError("shopify native hmac required")
    parsed = parse_request_body(raw_body, str(headers.get("content-type") or "application/json"))
    payload = _as_dict(parsed)
    if not payload:
        raise ValueError("empty shopify refund payload")
    topic = str(headers.get("x-shopify-topic") or "refunds/create").strip().lower()
    if topic != "refunds/create":
        raise ValueError("unsupported Shopify refund topic")
    order_refs = _shopify_order_ref_candidates(payload)
    if not order_refs:
        raise ValueError("shopify refund order id missing")
    source_ref = _shopify_refund_ref(payload, order_refs)
    ensure_vkpi_schema()
    snapshot = _find_shopify_snapshot_by_refs(order_refs)
    snapshot_id = _safe_int(snapshot.get("id")) if snapshot else 0
    base = _find_base_shopify_attribution(snapshot_id or None, order_refs)
    from app.domains.attribution.integrations_shopify_money import refund_money

    refund_cents, currency = refund_money(payload, str(base.get("currency") or snapshot.get("currency") or ""))
    import app.domains.attribution.reconciliation as reconciliation

    result = reconciliation.refund(
        {
            "source_platform": "shopify",
            "source_ref": source_ref,
            "refund_cents": refund_cents,
            "currency": currency,
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
        },
        ingest_class="provider_refund" if base.get("confidence") == "confirmed" else "provider_observed",
    )
    _mark_shopify_snapshot_refunded(snapshot_id or None)
    # GMV 归因桥(no-op until token):退款写入负归因后,把对应推荐的 outcome 重算一遍 ——
    # refresh_business_outcome 从含本次退款负行的 ledger 重新聚合 attributed_orders/GMV,
    # 即「退款 -> outcome 负向修正(重算)」。缺 shpat_ token 时桥内部直接 no-op;任何异常被吞,
    # 绝不让 outcome 桥拖垮 refund webhook 主路径(与 order webhook 同一防护口径)。
    try:
        from app.domains.attribution import gmv_outcome_bridge

        gmv_outcome_bridge.handle_refund_row(result.get("attribution") or {})
    except Exception:
        logger.debug("ingest_shopify_refund_webhook.gmv_outcome_bridge_failed", exc_info=True)
    return {
        "status": "ok",
        "source": "shopify",
        "auth_mode": auth_mode,
        "matched": bool(base),
        "shopify_order_snapshot_id": snapshot_id or base.get("shopify_order_snapshot_id"),
        "base_attribution_id": base.get("id"),
        **result,
    }
