"""Organization-scoped Dealer activity projection from Event Radar.

The Dealer directory owns addresses and contact details.  Event Radar owns
dated activities.  This read model joins them only through the durable
``vkpi_event_opportunity_dealers`` relation; names, cities, and free text are
never used as a fuzzy fallback.
"""
from __future__ import annotations

from datetime import date, datetime, timezone
from typing import Any

from app.db.connection import get_conn, table_exists


_REQUIRED_TABLES = (
    "vkpi_dealers",
    "vkpi_event_watch_targets",
    "vkpi_event_opportunities",
    "vkpi_event_opportunity_dealers",
    "vkpi_event_opportunity_promotions",
)


def _positive_int(value: Any, field: str) -> int:
    if isinstance(value, bool):
        raise ValueError(f"{field} must be a positive integer")
    try:
        parsed = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field} must be a positive integer") from exc
    if parsed <= 0:
        raise ValueError(f"{field} must be a positive integer")
    return parsed


def _row(value: Any) -> dict[str, Any]:
    return dict(value) if value is not None else {}


def _as_of_date(raw: Any = None) -> date:
    """Return one application-owned UTC date boundary for Radar reads."""
    if raw in (None, ""):
        return datetime.now(timezone.utc).date()
    if isinstance(raw, datetime):
        if raw.tzinfo is not None:
            return raw.astimezone(timezone.utc).date()
        return raw.date()
    if isinstance(raw, date):
        return raw
    try:
        return date.fromisoformat(str(raw).strip())
    except (TypeError, ValueError) as exc:
        raise ValueError("as_of_date must be YYYY-MM-DD") from exc


def _schema_ready() -> bool:
    try:
        return all(table_exists(name) for name in _REQUIRED_TABLES)
    except Exception:
        return False


def list_dealer_activities(
    dealer_id: Any,
    *,
    organization_id: Any,
    limit: Any = 20,
    include_past: bool = False,
    as_of_date: date | str | None = None,
    connection: Any | None = None,
) -> dict[str, Any]:
    """Return exact, enabled-source Event Radar links for one Dealer.

    Supplying ``connection`` is an explicit test/transaction seam.  Runtime
    calls fail closed when the Radar schema is unavailable.
    """
    normalized_dealer_id = _positive_int(dealer_id, "dealer_id")
    normalized_org_id = _positive_int(organization_id, "organization_id")
    safe_limit = max(1, min(_positive_int(limit, "limit"), 100))
    effective_date = _as_of_date(as_of_date)
    if connection is None and not _schema_ready():
        return {
            "status": "migration_pending",
            "dealer_id": normalized_dealer_id,
            "activities": [],
            "count": 0,
            "next_activity_at": None,
            "as_of_date": effective_date.isoformat(),
            "association_policy": "exact_dealer_id_only",
            "claim_status": "descriptive_only",
        }

    conn = connection or get_conn()
    dealer = conn.execute(
        "SELECT id,name FROM vkpi_dealers WHERE id=? LIMIT 1",
        (normalized_dealer_id,),
    ).fetchone()
    if dealer is None:
        raise LookupError("dealer not found")

    terminal_statuses = ("done", "ended", "cancelled", "canceled", "closed")
    time_clause = "" if include_past else (
        "AND (o.end_date IS NULL OR o.end_date >= ?) "
        "AND LOWER(COALESCE(o.event_status,'')) NOT IN ("
        + ",".join("?" for _ in terminal_statuses)
        + ")"
    )
    association_params: tuple[Any, ...] = (
        normalized_org_id,
        normalized_dealer_id,
        *(() if include_past else (effective_date.isoformat(), *terminal_statuses)),
    )
    association_where = f"""
        od.organization_id=? AND od.dealer_id=? AND od.relation_type='host'
        AND o.organization_id=od.organization_id
        {time_clause}
    """
    count_row = conn.execute(
        f"""
        SELECT COUNT(*) AS linked_n,
               COUNT(*) FILTER (
                   WHERE s.status='active' AND COALESCE(s.enabled,FALSE)=TRUE
               ) AS visible_n
        FROM vkpi_event_opportunity_dealers od
        JOIN vkpi_event_opportunities o ON o.id=od.opportunity_id
        LEFT JOIN vkpi_event_watch_targets s ON s.id=o.source_id
        WHERE {association_where}
        """,
        association_params,
    ).fetchone()
    rows = conn.execute(
        f"""
        SELECT o.id,o.title,o.lane,o.organizer,o.start_date,o.end_date,
               o.timezone,o.local_time_text,o.venue,o.address,o.city,o.region,
               o.country_code,o.official_url,o.registration_url,o.event_status,
               o.decision_status,o.verification_status,o.last_verified_at,
               o.source_checked_at,s.id AS source_id,s.name AS source_name,
               s.source_kind,p.event_id AS converted_event_id,
               p.promoted_at AS promotion_promoted_at,
               p.promoted_by AS promotion_promoted_by
        FROM vkpi_event_opportunity_dealers od
        JOIN vkpi_event_opportunities o ON o.id=od.opportunity_id
        JOIN vkpi_event_watch_targets s ON s.id=o.source_id
        LEFT JOIN vkpi_event_opportunity_promotions p
          ON p.organization_id=od.organization_id
         AND p.opportunity_id=o.id
        WHERE {association_where}
          AND s.status='active' AND COALESCE(s.enabled,FALSE)=TRUE
        ORDER BY o.start_date ASC NULLS LAST,o.id ASC
        LIMIT ?
        """,
        (*association_params, safe_limit),
    ).fetchall()
    activities: list[dict[str, Any]] = []
    for raw in rows:
        item = _row(raw)
        converted_event_id = item.get("converted_event_id")
        is_internal_event = converted_event_id not in (None, "")
        item.update(
            {
                "association": "exact_dealer_id",
                "record_type": (
                    "internal_event" if is_internal_event
                    else "external_opportunity_candidate"
                ),
                "is_internal_event": is_internal_event,
                "promotion_receipt": {
                    "present": is_internal_event,
                    "event_id": converted_event_id if is_internal_event else None,
                    "promoted_at": (
                        item.get("promotion_promoted_at") if is_internal_event else None
                    ),
                    "promoted_by": (
                        item.get("promotion_promoted_by") if is_internal_event else None
                    ),
                },
                "claim_status": "descriptive_only",
            }
        )
        activities.append(item)
    count_values = _row(count_row)
    linked_total = int(count_values.get("linked_n") or 0)
    total = int(count_values.get("visible_n") or 0)
    suppressed_total = max(0, linked_total - total)
    next_activity_at = next(
        (item.get("start_date") for item in activities if item.get("start_date")),
        None,
    )
    return {
        "status": (
            "ready" if activities
            else "pending_source_activation" if suppressed_total
            else "empty"
        ),
        "dealer_id": normalized_dealer_id,
        "dealer_name": _row(dealer).get("name"),
        "activities": activities,
        "count": total,
        "linked_count": linked_total,
        "suppressed_count": suppressed_total,
        "suppression_reason": (
            "source_not_active_or_enabled" if suppressed_total else None
        ),
        "returned": len(activities),
        "next_activity_at": next_activity_at,
        "include_past": bool(include_past),
        "as_of_date": effective_date.isoformat(),
        "association_policy": "exact_dealer_id_only",
        # This view intentionally retains external candidates.  Only a durable
        # promotion receipt makes an item an internal Event.
        "formal_event_rule": "promotion_receipt_required",
        "automatic_sync": False,
        "candidate_sync_capability": "separate_review_only_pipeline",
        "source": "vkpi_event_opportunity_dealers",
        "business_rows_written": 0,
        "claim_status": "descriptive_only",
    }


__all__ = ["list_dealer_activities"]
