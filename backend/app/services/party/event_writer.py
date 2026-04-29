"""
services/party/event_writer.py

Single-entry event ingestion. Everything that constitutes a "business action"
should flow through write_event(). Phase 1 writes to Postgres events table only.

Phase 2 will add an outbox flag + async ClickHouse fan-out.
Phase 3 will partition events by day and drop >30d.

Safe to call from SQLite dev environment: if PG is unavailable, writes are
silently skipped and a debug-level log is emitted (not an error — dev envs
legitimately don't have the events table).

Usage (from anywhere in backend):

    from app.services.party.event_writer import write_event

    write_event(
        event_type="shop.purchase",
        event_source="shopify_webhook",
        party_id=party_id,                         # may be None
        payload={
            "order_id": order["id"],
            "total_usd": order["total_price"],
            "line_items": [...],
            "ref_code": order.get("note_attributes", {}).get("ref"),
        },
        source_ref=str(order["id"]),
        source_ref_type="shopify_order",
        session_id=order.get("note_attributes", {}).get("session_id", ""),
    )
"""
from __future__ import annotations

import json
import logging
import uuid
from datetime import datetime, timezone
from typing import Any, Optional

from app.db.connection import _get_pg_pool, is_postgres_runtime
from app.services.party.party_service import mark_activity

logger = logging.getLogger(__name__)


def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


def write_event(
    *,
    event_type: str,
    event_source: str,
    party_id: Optional[str] = None,
    payload: Optional[dict] = None,
    occurred_at: Optional[datetime] = None,
    session_id: str = "",
    device_fingerprint_hash: str = "",
    signed_device_id: str = "",
    source_ref: str = "",
    source_ref_type: str = "",
    payload_schema_version: int = 1,
    touch_party_activity: bool = True,
) -> Optional[str]:
    """
    Insert one event row. Returns event_id (UUID) on success, None on no-op.

    Rules:
        - event_type is required and must match '<domain>.<action>' pattern.
        - event_source is required.
        - occurred_at defaults to NOW() if not provided.
        - party_id is optional (anonymous events are allowed; backfilled later).
        - payload is JSON-serializable dict; defaults to {}.

    No exceptions are raised for invalid PG state — this function is designed
    to be called from webhook handlers and Via runtime where silently no-oping
    is better than 500-ing the upstream request.
    """
    if not event_type or not event_source:
        logger.warning(
            "write_event rejected: missing event_type or event_source "
            "(event_type=%r, event_source=%r)",
            event_type, event_source,
        )
        return None

    if not is_postgres_runtime():
        logger.debug("write_event skipped: PG runtime not available (event_type=%s)", event_type)
        return None

    pool = _get_pg_pool()
    if pool is None:
        return None

    event_id = str(uuid.uuid4())
    payload_json = json.dumps(payload or {}, default=str, ensure_ascii=False)
    occurred = occurred_at or _now_utc()

    try:
        with pool.connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO events (
                        event_id,
                        party_id,
                        event_type, event_source,
                        occurred_at, received_at,
                        session_id, device_fingerprint_hash, signed_device_id,
                        payload,
                        source_ref, source_ref_type,
                        payload_schema_version
                    ) VALUES (
                        %s,
                        %s,
                        %s, %s,
                        %s, NOW(),
                        %s, %s, %s,
                        %s::jsonb,
                        %s, %s,
                        %s
                    )
                    """,
                    (
                        event_id,
                        party_id,
                        event_type, event_source,
                        occurred,
                        session_id, device_fingerprint_hash, signed_device_id,
                        payload_json,
                        source_ref, source_ref_type,
                        payload_schema_version,
                    ),
                )
            conn.commit()
    except Exception:
        logger.exception(
            "write_event failed: event_type=%s event_source=%s party_id=%s source_ref=%s",
            event_type, event_source, party_id, source_ref,
        )
        return None

    # Mark party activity (non-fatal)
    if party_id and touch_party_activity:
        try:
            mark_activity(party_id)
        except Exception:
            logger.debug("mark_activity failed for party=%s (non-fatal)", party_id)

    return event_id


# =====================================================================
# Convenience wrappers for the 3 Phase-1 wire-up points
# =====================================================================

def emit_shop_purchase(
    *,
    party_id: Optional[str],
    order_id: Any,
    total: Any,
    currency: str,
    line_items: Optional[list] = None,
    ref_code: str = "",
    utm_source: str = "",
    session_id: str = "",
    extra: Optional[dict] = None,
) -> Optional[str]:
    """Shopify webhook → shop.purchase event"""
    payload = {
        "order_id": order_id,
        "total": total,
        "currency": currency,
        "line_items": line_items or [],
        "ref_code": ref_code,
        "utm_source": utm_source,
    }
    if extra:
        payload.update(extra)
    return write_event(
        event_type="shop.purchase",
        event_source="shopify_webhook",
        party_id=party_id,
        payload=payload,
        source_ref=str(order_id),
        source_ref_type="shopify_order",
        session_id=session_id,
    )


def emit_via_session_started(
    *,
    party_id: Optional[str],
    via_session_id: str,
    signed_device_id: str = "",
    client_fingerprint: str = "",
    entry_source: str = "",
    extra: Optional[dict] = None,
) -> Optional[str]:
    """Via session start → via.session_started event"""
    payload = {
        "via_session_id": via_session_id,
        "entry_source": entry_source,
    }
    if extra:
        payload.update(extra)
    return write_event(
        event_type="via.session_started",
        event_source="via_runtime",
        party_id=party_id,
        payload=payload,
        source_ref=via_session_id,
        source_ref_type="via_session",
        session_id=via_session_id,
        device_fingerprint_hash=client_fingerprint,
        signed_device_id=signed_device_id,
    )


def emit_creator_submission_created(
    *,
    party_id: Optional[str],
    submission_id: Any,
    platform: str,
    url: str,
    creator_code: str = "",
    handle: str = "",
    extra: Optional[dict] = None,
) -> Optional[str]:
    """Creator submits a video → creator.submission_created event"""
    payload = {
        "submission_id": submission_id,
        "platform": platform,
        "url": url,
        "creator_code": creator_code,
        "handle": handle,
    }
    if extra:
        payload.update(extra)
    return write_event(
        event_type="creator.submission_created",
        event_source="creator_api",
        party_id=party_id,
        payload=payload,
        source_ref=str(submission_id),
        source_ref_type="submission",
    )
