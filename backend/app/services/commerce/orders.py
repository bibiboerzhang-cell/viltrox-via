"""
services/commerce/orders.py — Order normalization & admin queries

Orders originate as Shopify webhooks → platform_ingest_events. This module
extracts them into the normalized `orders` table and provides admin queries.
"""
from __future__ import annotations

import asyncio
import json
import os
from datetime import UTC, datetime
from typing import Any

from app.core.logging import get_logger
from app.db.connection import get_conn
from app.services.activities.attribution import lookup_user_id_by_email, record_purchase

logger = get_logger(__name__)
_MODULE_BACKFILL_CUTOFF_AT = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _configured_backfill_cutoff() -> str:
    return (
        os.environ.get("COMMERCE_ORDER_INGEST_CUTOFF_AT", "").strip()
        or os.environ.get("COMMERCE_ORDER_BACKFILL_CUTOFF_AT", "").strip()
    )


def _effective_backfill_cutoff(cutoff_at: str | None, include_history: bool) -> str:
    if include_history:
        return ""
    return str(cutoff_at or "").strip() or _configured_backfill_cutoff() or _MODULE_BACKFILL_CUTOFF_AT


def _shopify_external_order_id(payload: dict[str, Any], row: Any) -> str:
    raw = payload.get("order_number") or payload.get("id") or row["external_id"]
    value = str(raw or "").strip()
    if not value:
        return ""
    if str(payload.get("order_number") or "").strip() and not value.startswith("#"):
        return f"#{value}"
    return value


def _resolve_attribution_identity(conn, ref_code: str | None) -> tuple[int | None, str, int]:
    """
    Resolve creator/student attribution against the current viltrox-2.0 schema:
      - creator code lives in users.creator_code
      - student id lives in student_verifications.student_id_code
    """
    code = str(ref_code or "").strip()
    if not code:
        return None, "direct", 0

    row = conn.execute(
        """
        SELECT * FROM (
            SELECT u.id AS user_id, 'creator' AS attribution_type, 800 AS commission_rate_bps
            FROM users u
            WHERE u.creator_code = ?
            UNION ALL
            SELECT sv.user_id AS user_id, 'student' AS attribution_type, 1000 AS commission_rate_bps
            FROM student_verifications sv
            WHERE sv.student_id_code = ?
              AND COALESCE(sv.status, 'active') = 'active'
        )
        LIMIT 1
        """,
        (code, code),
    ).fetchone()
    if row:
        return row["user_id"], row["attribution_type"], row["commission_rate_bps"]

    guessed_student = code.upper().startswith("V_STU_")
    return None, ("student" if guessed_student else "creator"), (1000 if guessed_student else 800)


def _mark_event_ingested(conn, event_id: int) -> None:
    """Best-effort stamp back to platform_ingest_events when orders are normalized."""
    try:
        conn.execute(
            "UPDATE platform_ingest_events SET ingested_into_orders_at = datetime('now') WHERE id = ?",
            (event_id,),
        )
    except Exception:
        # Column is added by v5 compatibility migration; older DBs can ignore it.
        logger.debug("orders.mark_event_ingested_skipped", extra={"event_id": event_id}, exc_info=True)


# =========================================================================
# Ingestion (called from scheduler job)
# =========================================================================

async def ingest_order_from_event(event_id: int) -> int | None:
    return await asyncio.to_thread(_ingest_order_from_event_sync, event_id)


def _ingest_order_from_event_sync(event_id: int) -> int | None:
    """
    Extract + normalize one shopify webhook event into `orders` table.
    Returns the new order_id, or None if skipped (duplicate/unsupported).
    """
    conn = get_conn()
    row = conn.execute(
        "SELECT id, source_platform, event_type, external_id, payload_json, occurred_at "
        "FROM platform_ingest_events WHERE id = ?",
        (event_id,),
    ).fetchone()
    if not row:
        return None
    platform = (row["source_platform"] or "").lower()
    if platform != "shopify":
        return None

    try:
        payload = json.loads(row["payload_json"])
    except Exception:
        logger.warning("order %s: invalid payload_json", event_id)
        return None

    external_id = _shopify_external_order_id(payload, row)
    if not external_id:
        logger.warning("order %s: missing external order id", event_id)
        return None

    # Dedupe
    existing = conn.execute(
        "SELECT id FROM orders WHERE external_order_id = ?", (external_id,)
    ).fetchone()
    if existing:
        _mark_event_ingested(conn, event_id)
        conn.commit()
        return existing["id"]

    subtotal_cents = int(float(payload.get("subtotal_price", 0)) * 100)
    currency = payload.get("currency", "USD")
    customer = payload.get("customer") or {}
    ship = payload.get("shipping_address") or {}

    # Attribution: look for ref in note_attributes, then utm_source
    attribution_source = None
    attribution_type = "direct"
    note_attrs = payload.get("note_attributes") or []
    for na in note_attrs:
        if na.get("name") == "ref":
            attribution_source = na.get("value")
            break

    utm_source = payload.get("utm_source") or None
    utm_medium = payload.get("utm_medium") or None
    utm_campaign = payload.get("utm_campaign") or None
    if not attribution_source and utm_source:
        attribution_source = utm_source

    # Resolve attribution type + user_id
    attribution_user_id = None
    commission_rate_bps = 0
    if attribution_source:
        attribution_user_id, attribution_type, commission_rate_bps = _resolve_attribution_identity(
            conn, attribution_source
        )

    commission_cents = int(subtotal_cents * commission_rate_bps / 10000)

    items = [
        {
            "sku": li.get("sku"),
            "name": li.get("name") or li.get("title"),
            "qty": li.get("quantity", 1),
            "price_cents": int(float(li.get("price", 0)) * 100),
        }
        for li in payload.get("line_items") or []
    ]

    cur = conn.cursor()
    cur.execute(
        """
        INSERT OR IGNORE INTO orders (
            external_order_id, source_platform,
            customer_email, customer_country,
            subtotal_cents, currency, items_json,
            attribution_source, attribution_type, attribution_user_id,
            utm_source, utm_medium, utm_campaign,
            commission_rate_bps, commission_cents,
            status, placed_at, webhook_event_ids_json, raw_payload
        ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """,
        (
            external_id, platform,
            customer.get("email"), ship.get("country") or ship.get("country_code"),
            subtotal_cents, currency, json.dumps(items),
            attribution_source, attribution_type, attribution_user_id,
            utm_source, utm_medium, utm_campaign,
            commission_rate_bps, commission_cents,
            "paid", row["occurred_at"], json.dumps([event_id]), row["payload_json"],
        ),
    )
    conn.commit()
    order_row = conn.execute(
        "SELECT id FROM orders WHERE external_order_id = ?",
        (external_id,),
    ).fetchone()
    if not order_row:
        return None
    order_id = int(order_row["id"])
    _mark_event_ingested(conn, event_id)
    conn.commit()

    # Mark click as converted if we have one matching
    if attribution_source:
        conn.execute(
            """
            UPDATE attribution_clicks
            SET converted_to_order_id = ?
            WHERE ref_code = ?
              AND converted_to_order_id IS NULL
              AND clicked_at > datetime('now', '-30 days')
            """,
            (order_id, attribution_source),
        )
        conn.commit()

    # ── Phase 1 middleware: party stitch + shop.purchase event (non-fatal) ──
    # Design: best-effort only. If PG / party layer unavailable or fails,
    # continue normally — the order is already persisted above.
    try:
        _emit_purchase_to_party_layer(
            order_id=order_id,
            external_id=external_id,
            customer_email=customer.get("email"),
            subtotal_cents=subtotal_cents,
            currency=currency,
            items=items,
            attribution_source=attribution_source,
            utm_source=utm_source,
            occurred_at_raw=row["occurred_at"],
        )
    except Exception:
        logger.exception("phase1 party-layer emit failed for order %s (non-fatal)", order_id)

    try:
        activity_user_id = attribution_user_id or lookup_user_id_by_email(customer.get("email"))
        record_purchase(
            order_id=order_id,
            user_id=activity_user_id,
            revenue_cents=subtotal_cents,
            commission_cents=commission_cents,
            external_order_id=external_id,
        )
    except Exception:
        logger.warning("orders.activity_purchase_attribution_failed", extra={"order_id": order_id}, exc_info=True)

    return order_id


def _emit_purchase_to_party_layer(
    *,
    order_id: int | None,
    external_id: str,
    customer_email: str | None,
    subtotal_cents: int,
    currency: str,
    items: list[dict],
    attribution_source: str | None,
    utm_source: str | None,
    occurred_at_raw: Any,
) -> None:
    """
    Phase 1 wire: Shopify order → party (via email) → shop.purchase event.

    Only runs when PG is the runtime and the party layer migrations have been
    applied. On SQLite dev envs or if anything fails, silently returns — the
    legacy orders row has already been written successfully.
    """
    from app.db.connection import is_postgres_runtime

    if not is_postgres_runtime():
        return

    from app.services.party.party_service import get_or_create_by_email, _touch_party
    from app.services.party.event_writer import emit_shop_purchase

    party_id = None
    if customer_email:
        party_id = get_or_create_by_email(
            customer_email,
            origin_source="shopify_order",
            origin_channel=utm_source or attribution_source or "",
        )
        if party_id:
            # Mark as paying customer
            _touch_party(party_id, is_customer=True, lifecycle_stage="customer")

    emit_shop_purchase(
        party_id=party_id,
        order_id=external_id,
        total=subtotal_cents / 100.0,
        currency=currency,
        line_items=items,
        ref_code=attribution_source or "",
        utm_source=utm_source or "",
        extra={
            "internal_order_id": order_id,
            "subtotal_cents": subtotal_cents,
            "occurred_at_raw": str(occurred_at_raw) if occurred_at_raw else "",
        },
    )


async def backfill_orders_from_events(
    *,
    limit: int = 500,
    cutoff_at: str | None = None,
    include_history: bool = False,
) -> dict:
    """
    Normalize historical Shopify webhook rows into `orders`.
    Safe to run repeatedly because ingest_order_from_event dedupes by
    external_order_id and stamps processed events.
    """
    conn = get_conn()
    effective_cutoff = _effective_backfill_cutoff(cutoff_at, include_history)
    where = [
        "LOWER(COALESCE(source_platform, '')) = 'shopify'",
        "LOWER(COALESCE(entity_type, '')) = 'order'",
        "COALESCE(CAST(ingested_into_orders_at AS TEXT), '') = ''",
    ]
    params: list[Any] = []
    if effective_cutoff:
        where.append("occurred_at >= ?")
        params.append(effective_cutoff)
    params.append(limit)
    rows = conn.execute(
        f"""
        SELECT id
        FROM platform_ingest_events
        WHERE {" AND ".join(where)}
        ORDER BY occurred_at ASC, id ASC
        LIMIT ?
        """,
        params,
    ).fetchall()

    created = 0
    deduped = 0
    failed = 0
    for row in rows:
        try:
            order_id = await ingest_order_from_event(int(row["id"]))
            if order_id:
                existing = conn.execute(
                    "SELECT COUNT(*) AS n FROM orders WHERE id = ?",
                    (order_id,),
                ).fetchone()
                if (existing["n"] or 0) > 0:
                    created += 1
                else:
                    deduped += 1
            else:
                deduped += 1
        except Exception:
            logger.exception("backfill order event %s failed", row["id"])
            failed += 1

    return {
        "scanned": len(rows),
        "created_or_seen": created,
        "skipped_or_deduped": deduped,
        "failed": failed,
        "cutoff_at": effective_cutoff,
        "include_history": include_history,
    }


# =========================================================================
# Admin queries
# =========================================================================

def list_orders(
    *,
    status: str | None = None,
    attribution_type: str | None = None,
    platform: str | None = None,
    country: str | None = None,
    utm_source: str | None = None,
    from_date: str | None = None,
    to_date: str | None = None,
    page: int = 1,
    limit: int = 50,
) -> dict:
    conn = get_conn()
    where, params = [], []

    if status:
        where.append("status = ?"); params.append(status)
    if attribution_type:
        where.append("attribution_type = ?"); params.append(attribution_type)
    if platform:
        where.append("source_platform = ?"); params.append(platform)
    if country:
        where.append("customer_country = ?"); params.append(country)
    if utm_source:
        where.append("utm_source = ?"); params.append(utm_source)
    if from_date:
        where.append("placed_at >= ?"); params.append(from_date)
    if to_date:
        where.append("placed_at <= ?"); params.append(to_date)

    where_sql = ("WHERE " + " AND ".join(where)) if where else ""

    total_row = conn.execute(
        f"SELECT COUNT(*) AS n, COALESCE(SUM(subtotal_cents),0) AS gmv, "
        f"       COALESCE(SUM(commission_cents),0) AS comm_owed, "
        f"       COALESCE(SUM(CASE WHEN attribution_type != 'direct' THEN 1 ELSE 0 END), 0) AS attributed "
        f"FROM orders {where_sql}",
        params,
    ).fetchone()

    offset = max(0, (page - 1) * limit)
    rows = conn.execute(
        f"SELECT id, external_order_id, customer_email, customer_country, "
        f"  subtotal_cents, commission_cents, attribution_source, attribution_type, "
        f"  utm_source, status, placed_at, flagged_reason "
        f"FROM orders {where_sql} "
        f"ORDER BY placed_at DESC LIMIT ? OFFSET ?",
        [*params, limit, offset],
    ).fetchall()

    return {
        "orders": [dict(r) for r in rows],
        "total": total_row["n"],
        "attributed_count": total_row["attributed"],
        "gmv_cents": total_row["gmv"],
        "commission_owed_cents": total_row["comm_owed"],
    }


def get_order_detail(order_id: int) -> dict | None:
    conn = get_conn()
    row = conn.execute("SELECT * FROM orders WHERE id = ?", (order_id,)).fetchone()
    if not row:
        return None
    order = dict(row)

    # Attribution chain (best effort)
    chain = None
    if order.get("attribution_source"):
        click = conn.execute(
            "SELECT * FROM attribution_clicks "
            "WHERE converted_to_order_id = ? LIMIT 1",
            (order_id,),
        ).fetchone()
        if click:
            chain = dict(click)

    # Webhook history
    try:
        event_ids = json.loads(order.get("webhook_event_ids_json") or "[]")
    except Exception:
        event_ids = []
    webhook_history = []
    if event_ids:
        placeholders = ",".join("?" * len(event_ids))
        wh_rows = conn.execute(
            f"SELECT id, event_type, ingest_status, occurred_at, processed_at, error_message "
            f"FROM platform_ingest_events WHERE id IN ({placeholders})",
            event_ids,
        ).fetchall()
        webhook_history = [dict(r) for r in wh_rows]

    return {
        "order": order,
        "attribution_chain": chain,
        "webhook_history": webhook_history,
    }


def override_attribution(
    order_id: int, creator_handle: str, reason: str, admin_id: int
) -> dict:
    conn = get_conn()
    order = conn.execute("SELECT * FROM orders WHERE id = ?", (order_id,)).fetchone()
    if not order:
        raise ValueError("order not found")

    user_id, new_type, new_rate = _resolve_attribution_identity(conn, creator_handle)
    new_comm = int(order["subtotal_cents"] * new_rate / 10000)

    conn.execute(
        """UPDATE orders SET
            attribution_source = ?, attribution_type = ?, attribution_user_id = ?,
            commission_rate_bps = ?, commission_cents = ?, updated_at = datetime('now')
           WHERE id = ?""",
        (
            creator_handle, new_type,
            user_id,
            new_rate, new_comm, order_id,
        ),
    )
    conn.commit()
    return {
        "ok": True,
        "order_id": order_id,
        "new_attribution": creator_handle,
        "new_commission_cents": new_comm,
    }


def flag_order(order_id: int, reason: str, admin_id: int) -> dict:
    conn = get_conn()
    conn.execute(
        "UPDATE orders SET flagged_reason = ?, flagged_by = ?, flagged_at = datetime('now') "
        "WHERE id = ?",
        (reason, admin_id, order_id),
    )
    conn.commit()
    return {"ok": True, "order_id": order_id, "flagged_reason": reason}


# =========================================================================
# Webhook event log
# =========================================================================

def list_webhook_events(
    *,
    source: str | None = None,
    status: str | None = None,
    from_date: str | None = None,
    to_date: str | None = None,
    page: int = 1,
    limit: int = 50,
) -> dict:
    conn = get_conn()
    where, params = [], []
    if source:
        where.append("source_platform = ?"); params.append(source)
    if status:
        # map ok/warn/err to ingest_status values
        if status == "ok":
            where.append("ingest_status = 'done' AND COALESCE(error_message, '') = ''")
        elif status == "warn":
            where.append("(ingest_status IN ('queued', 'processing') OR (ingest_status = 'done' AND COALESCE(error_message, '') != ''))")
        elif status == "err":
            where.append("ingest_status = 'failed'")
    if from_date:
        where.append("occurred_at >= ?"); params.append(from_date)
    if to_date:
        where.append("occurred_at <= ?"); params.append(to_date)
    where_sql = ("WHERE " + " AND ".join(where)) if where else ""

    totals = conn.execute(
        f"SELECT COUNT(*) AS n, "
        f"  COALESCE(SUM(CASE WHEN ingest_status = 'done' AND COALESCE(error_message, '') = '' THEN 1 ELSE 0 END), 0) AS ok, "
        f"  COALESCE(SUM(CASE WHEN ingest_status = 'failed' THEN 1 ELSE 0 END), 0) AS err "
        f"FROM platform_ingest_events {where_sql}",
        params,
    ).fetchone()

    offset = max(0, (page - 1) * limit)
    rows = conn.execute(
        f"SELECT id, source_platform, event_type, external_id, "
        f"       ingest_status, occurred_at, processed_at, error_message "
        f"FROM platform_ingest_events {where_sql} "
        f"ORDER BY occurred_at DESC LIMIT ? OFFSET ?",
        [*params, limit, offset],
    ).fetchall()

    n = max(totals["n"], 1)
    return {
        "events": [dict(r) for r in rows],
        "total": totals["n"],
        "success_rate": round(totals["ok"] / n * 100, 1),
        "error_count": totals["err"],
    }
