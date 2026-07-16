"""Organization-scoped Dealer activity projection from Event Radar.

The Dealer directory owns addresses and contact details.  Event Radar owns
dated activities.  This read model joins them only through the durable
``vkpi_event_opportunity_dealers`` relation; names, cities, and free text are
never used as a fuzzy fallback.
"""
from __future__ import annotations

from typing import Any

from app.db.connection import get_conn, table_exists


_REQUIRED_TABLES = (
    "vkpi_dealers",
    "vkpi_event_watch_targets",
    "vkpi_event_opportunities",
    "vkpi_event_opportunity_dealers",
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
    connection: Any | None = None,
) -> dict[str, Any]:
    """Return exact, enabled-source Event Radar links for one Dealer.

    Supplying ``connection`` is an explicit test/transaction seam.  Runtime
    calls fail closed when the Radar schema is unavailable.
    """
    normalized_dealer_id = _positive_int(dealer_id, "dealer_id")
    normalized_org_id = _positive_int(organization_id, "organization_id")
    safe_limit = max(1, min(_positive_int(limit, "limit"), 100))
    if connection is None and not _schema_ready():
        return {
            "status": "migration_pending",
            "dealer_id": normalized_dealer_id,
            "activities": [],
            "count": 0,
            "next_activity_at": None,
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

    time_clause = "" if include_past else (
        "AND (o.end_date IS NULL OR o.end_date >= CURRENT_DATE) "
        "AND COALESCE(o.event_status,'') NOT IN ('ended','cancelled')"
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
        (normalized_org_id, normalized_dealer_id),
    ).fetchone()
    rows = conn.execute(
        f"""
        SELECT o.id,o.title,o.lane,o.organizer,o.start_date,o.end_date,
               o.timezone,o.local_time_text,o.venue,o.address,o.city,o.region,
               o.country_code,o.official_url,o.registration_url,o.event_status,
               o.decision_status,o.verification_status,o.last_verified_at,
               o.source_checked_at,s.id AS source_id,s.name AS source_name,
               s.source_kind
        FROM vkpi_event_opportunity_dealers od
        JOIN vkpi_event_opportunities o ON o.id=od.opportunity_id
        JOIN vkpi_event_watch_targets s ON s.id=o.source_id
        WHERE {association_where}
          AND s.status='active' AND COALESCE(s.enabled,FALSE)=TRUE
        ORDER BY o.start_date ASC NULLS LAST,o.id ASC
        LIMIT ?
        """,
        (normalized_org_id, normalized_dealer_id, safe_limit),
    ).fetchall()
    activities = [
        {
            **_row(raw),
            "association": "exact_dealer_id",
            "claim_status": "descriptive_only",
        }
        for raw in rows
    ]
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
        "association_policy": "exact_dealer_id_only",
        # This view projects exact, already-promoted Dealer/Event relations.
        # Candidate feed ingestion is a separate review-only control plane.
        "automatic_sync": False,
        "candidate_sync_capability": "separate_review_only_pipeline",
        "source": "vkpi_event_opportunity_dealers",
        "business_rows_written": 0,
        "claim_status": "descriptive_only",
    }


__all__ = ["list_dealer_activities"]
