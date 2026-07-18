"""Read-only query helpers for Event Radar opportunities.

This module owns filter construction, pagination, and result normalization for
the public opportunity list.  It deliberately does not resolve tenants or
probe schema state; those trust boundaries remain in :mod:`events.radar`.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Callable


_EVIDENCE_STATUSES = {"verified", "review", "conflict"}
_TIME_WINDOWS = {"upcoming", "30d", "90d", "past", "date_pending"}


def page_bounds(*, limit: int, offset: int) -> tuple[int, int]:
    """Return the bounded pagination values used by the Radar API."""
    return (
        max(1, min(int(limit or 100), 500)),
        max(0, min(int(offset or 0), 1_000_000)),
    )


def migration_pending_page(*, limit: int, offset: int) -> dict[str, Any]:
    """Return the truthful empty response used before the Radar table exists."""
    safe_limit = max(1, min(int(limit or 100), 500))
    safe_offset = max(0, int(offset or 0))
    return {
        "items": [],
        "count": 0,
        "page": {
            "limit": safe_limit,
            "offset": safe_offset,
            "returned": 0,
            "next_offset": None,
            "has_more": False,
        },
        "coverage_claim": "migration_pending",
        "global_complete": False,
    }


def _append_evidence_filter(
    where: list[str],
    params: list[Any],
    evidence_status: str | None,
) -> None:
    evidence_key = str(evidence_status or "").strip().casefold()
    if evidence_key and evidence_key not in _EVIDENCE_STATUSES:
        raise ValueError("unsupported evidence_status")
    if not evidence_key:
        return

    verified_at = "COALESCE(o.last_verified_at,o.source_checked_at)"
    now = datetime.now(timezone.utc)
    cutoff = (now - timedelta(days=7)).isoformat()
    future_tolerance = (now + timedelta(hours=1)).isoformat()
    if evidence_key == "verified":
        where.extend(
            [
                "COALESCE(o.verification_status,'') IN ('verified','current')",
                f"{verified_at} >= ?",
                f"{verified_at} <= ?",
            ]
        )
        params.extend((cutoff, future_tolerance))
    elif evidence_key == "conflict":
        where.append("COALESCE(o.verification_status,'') IN ('conflict','conflicted')")
    else:
        where.extend(
            [
                "COALESCE(o.verification_status,'') NOT IN ('conflict','conflicted')",
                f"(COALESCE(o.verification_status,'') NOT IN ('verified','current') OR {verified_at} IS NULL OR {verified_at} < ? OR {verified_at} > ?)",
            ]
        )
        params.extend((cutoff, future_tolerance))


def _append_time_filter(
    where: list[str],
    params: list[Any],
    time_window: str | None,
    *,
    include_past: bool,
) -> None:
    time_key = str(time_window or "").strip().casefold()
    if time_key and time_key not in _TIME_WINDOWS:
        raise ValueError("unsupported time_window")

    today = datetime.now(timezone.utc).date()
    if time_key == "past":
        where.append("o.end_date IS NOT NULL AND o.end_date < ?")
        params.append(today.isoformat())
    elif time_key == "date_pending":
        where.append("(o.start_date IS NULL OR o.end_date IS NULL)")
    elif time_key in {"30d", "90d"}:
        until = today + timedelta(days=30 if time_key == "30d" else 90)
        where.extend(
            [
                "(o.end_date IS NULL OR o.end_date >= ?)",
                "o.start_date IS NOT NULL AND o.start_date <= ?",
                "o.event_status NOT IN ('ended','cancelled')",
            ]
        )
        params.extend((today.isoformat(), until.isoformat()))
    elif time_key == "upcoming" or not include_past:
        where.append("(o.end_date IS NULL OR o.end_date >= CURRENT_DATE)")
        where.append("o.event_status NOT IN ('ended','cancelled')")


def _filters(
    *,
    organization_id: int,
    lane: str | None,
    source_kind: str | None,
    decision_status: str | None,
    evidence_status: str | None,
    time_window: str | None,
    country: str | None,
    region: str | None,
    city: str | None,
    start_from: str | None,
    start_to: str | None,
    include_past: bool,
) -> tuple[str, list[Any]]:
    # A disabled source is an operational kill switch, even when its review
    # status is still ``active``.  Fail closed so disabled catalog rows cannot
    # appear as actionable opportunities or inflate state/entity coverage.
    where = [
        "o.organization_id = ?",
        "s.status = 'active'",
        "COALESCE(s.enabled,FALSE) = TRUE",
    ]
    params: list[Any] = [organization_id]
    if lane:
        where.append("o.lane = ?")
        params.append(str(lane))
    if source_kind:
        where.append("s.source_kind = ?")
        params.append(str(source_kind))
    if decision_status:
        where.append("o.decision_status = ?")
        params.append(str(decision_status))
    if country:
        where.append("o.country_code = ?")
        params.append(str(country).upper())
    if region:
        where.append("o.region = ?")
        params.append(str(region).strip().upper())
    if city:
        # Case-insensitive exact match; the UI feeds it from the observed city
        # set so an exact predicate stays index-friendly and avoids LIKE/%.
        where.append("LOWER(o.city) = LOWER(?)")
        params.append(str(city).strip())
    if start_from:
        where.append("o.start_date IS NOT NULL AND o.start_date >= ?")
        params.append(str(start_from).strip())
    if start_to:
        where.append("o.start_date IS NOT NULL AND o.start_date <= ?")
        params.append(str(start_to).strip())
    _append_evidence_filter(where, params, evidence_status)
    _append_time_filter(where, params, time_window, include_past=include_past)
    return " AND ".join(where), params


def list_opportunities(
    conn: Any,
    *,
    table_name: str,
    organization_id: int,
    limit: int,
    offset: int,
    lane: str | None,
    source_kind: str | None,
    decision_status: str | None,
    evidence_status: str | None,
    time_window: str | None,
    country: str | None,
    region: str | None,
    city: str | None,
    start_from: str | None,
    start_to: str | None,
    include_past: bool,
    loads: Callable[[Any, Any], Any],
    freshness: Callable[[Any], str],
) -> dict[str, Any]:
    """Execute the organization-scoped opportunity query and normalize rows."""
    safe_limit, safe_offset = page_bounds(limit=limit, offset=offset)
    where_sql, params = _filters(
        organization_id=organization_id,
        lane=lane,
        source_kind=source_kind,
        decision_status=decision_status,
        evidence_status=evidence_status,
        time_window=time_window,
        country=country,
        region=region,
        city=city,
        start_from=start_from,
        start_to=start_to,
        include_past=include_past,
    )
    total_row = conn.execute(
        f"SELECT COUNT(*) AS n FROM {table_name} o JOIN vkpi_event_watch_targets s ON s.id=o.source_id WHERE {where_sql}",
        tuple(params),
    ).fetchone()
    total = int((dict(total_row) if total_row is not None else {}).get("n") or 0)
    rows = conn.execute(
        f"""
        SELECT o.*, s.name AS source_name, s.status AS source_status,
               s.enabled AS source_enabled,
               s.source_kind AS source_kind,
               s.canonical_url AS source_catalog_url, s.requires_human_review AS source_requires_human_review,
               (SELECT od.dealer_id FROM vkpi_event_opportunity_dealers od
                 WHERE od.opportunity_id=o.id AND od.organization_id=o.organization_id
                   AND od.relation_type='host' ORDER BY od.created_at ASC LIMIT 1) AS dealer_id,
               (SELECT d.name FROM vkpi_event_opportunity_dealers od
                  JOIN vkpi_dealers d ON d.id=od.dealer_id
                 WHERE od.opportunity_id=o.id AND od.organization_id=o.organization_id
                   AND od.relation_type='host' ORDER BY od.created_at ASC LIMIT 1) AS dealer_name,
               (SELECT COUNT(*) FROM vkpi_event_opportunity_changes c WHERE c.opportunity_id=o.id AND c.organization_id=o.organization_id) AS change_count,
               (SELECT p.event_id FROM vkpi_event_opportunity_promotions p WHERE p.opportunity_id=o.id AND p.organization_id=o.organization_id) AS converted_event_id
        FROM {table_name} o
        JOIN vkpi_event_watch_targets s ON s.id=o.source_id
        WHERE {where_sql}
        ORDER BY o.start_date ASC NULLS LAST,
                 CASE o.lane WHEN 'dealer_event' THEN 0 WHEN 'local_activity' THEN 1 WHEN 'major_expo' THEN 2 ELSE 3 END,
                 o.relevance_score DESC NULLS LAST, o.id ASC
        LIMIT ? OFFSET ?
        """,
        (*params, safe_limit, safe_offset),
    ).fetchall()
    items: list[dict[str, Any]] = []
    for raw in rows:
        item = dict(raw)
        item["metadata_json"] = loads(item.get("metadata_json"), {})
        # 2026-07-18 体检修:compat BOOLEAN 读回 int 1/0,序列化给前端后
        # `=== true` 恒 false → 全部机会动作面被判死;此处归一真 bool。
        enabled = item.get("source_enabled")
        item["source_enabled"] = enabled if isinstance(enabled, bool) else enabled in (1, "1", "t", "true")
        review = item.get("source_requires_human_review")
        item["source_requires_human_review"] = (
            review if isinstance(review, bool) else review in (1, "1", "t", "true")
        )
        item["freshness_status"] = freshness(
            item.get("last_verified_at") or item.get("source_checked_at")
        )
        item["is_source_backed_lead"] = True
        item["business_outcome_status"] = "unmeasured"
        items.append(item)

    next_offset = safe_offset + len(items)
    return {
        "items": items,
        "count": total,
        "page": {
            "limit": safe_limit,
            "offset": safe_offset,
            "returned": len(items),
            "next_offset": next_offset if next_offset < total else None,
            "has_more": next_offset < total,
        },
        "coverage_claim": "registered_publisher_owned_public_entries_only",
        "global_complete": False,
        "organization_id": organization_id,
        "as_of": datetime.now(timezone.utc).isoformat(),
    }


__all__ = ["list_opportunities", "migration_pending_page", "page_bounds"]
