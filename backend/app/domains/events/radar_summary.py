"""Read-only Event Radar summary aggregation.

Trust-boundary checks and tenant resolution remain in :mod:`events.radar`.
This module only aggregates rows from the already validated connection.
"""
from __future__ import annotations

from collections import Counter
from datetime import datetime
from typing import Any, Callable

from app.shared.us_jurisdiction_coverage import (
    US_STATE_AND_DC_CODES,
    registered_us_jurisdiction_matrix,
)


def build_summary(
    conn: Any,
    *,
    organization_id: int,
    as_of: datetime,
    table_exists: Callable[[str], bool],
    row: Callable[[Any], dict[str, Any]],
    freshness: Callable[[Any], str],
    passport_is_current: Callable[..., bool],
) -> dict[str, Any]:
    """Aggregate organization-scoped Radar truth from a validated schema."""
    source_rows = conn.execute(
        "SELECT source_kind, status, country_code, evidence_grade, COUNT(*) AS n FROM vkpi_event_watch_targets GROUP BY 1,2,3,4"
    ).fetchall()
    opportunity_rows = conn.execute(
        """
        SELECT o.lane, o.decision_status, o.verification_status, o.evidence_grade,
               o.event_status, o.country_code, o.region, COUNT(*) AS n
        FROM vkpi_event_opportunities o
        JOIN vkpi_event_watch_targets s ON s.id=o.source_id
        WHERE o.organization_id=? AND s.status='active'
          AND COALESCE(s.enabled,FALSE)=TRUE
        GROUP BY 1,2,3,4,5,6,7
        """,
        (organization_id,),
    ).fetchall()
    source_total = sum(int(row(item).get("n") or 0) for item in source_rows)
    opportunity_total = sum(int(row(item).get("n") or 0) for item in opportunity_rows)
    source_status = Counter()
    source_kind = Counter()
    source_countries: set[str] = set()
    for raw in source_rows:
        item = row(raw)
        source_status[str(item.get("status") or "unknown")] += int(item.get("n") or 0)
        source_kind[str(item.get("source_kind") or "unknown")] += int(item.get("n") or 0)
        if item.get("country_code"):
            source_countries.add(str(item["country_code"]))
    lanes = Counter()
    decisions = Counter()
    verification = Counter()
    evidence = Counter()
    us_opportunity_states: list[str] = []
    us_opportunity_counts: Counter[str] = Counter()
    us_verified_status_counts: Counter[str] = Counter()
    for raw in opportunity_rows:
        item = row(raw)
        count = int(item.get("n") or 0)
        lanes[str(item.get("lane") or "unknown")] += count
        decisions[str(item.get("decision_status") or "unknown")] += count
        verification[str(item.get("verification_status") or "unknown")] += count
        evidence[str(item.get("evidence_grade") or "unknown")] += count
        if str(item.get("country_code") or "").strip().upper() == "US":
            region = str(item.get("region") or "").strip().upper()
            us_opportunity_states.append(region)
            if region in US_STATE_AND_DC_CODES:
                us_opportunity_counts[region] += count
                if str(item.get("verification_status") or "").strip().casefold() in {
                    "verified",
                    "current",
                }:
                    us_verified_status_counts[region] += count
    us_jurisdiction_matrix = {
        **registered_us_jurisdiction_matrix(us_opportunity_states),
        # These are organization-scoped Event Radar opportunity rows, not a
        # denominator for the US event market and not venue coordinates.
        "opportunity_counts_by_state_dc": dict(sorted(us_opportunity_counts.items())),
        "verification_marked_counts_by_state_dc": dict(
            sorted(us_verified_status_counts.items())
        ),
        "opportunity_entity_count": sum(us_opportunity_counts.values()),
        "map_precision": "state_dc_aggregate_not_venue_coordinates",
    }
    freshness_rows = conn.execute(
        """
        SELECT o.last_verified_at,o.source_checked_at
        FROM vkpi_event_opportunities o JOIN vkpi_event_watch_targets s ON s.id=o.source_id
        WHERE o.organization_id=? AND s.status='active'
          AND COALESCE(s.enabled,FALSE)=TRUE
        """,
        (organization_id,),
    ).fetchall()
    freshness_counts = Counter(
        freshness(row(item).get("last_verified_at") or row(item).get("source_checked_at"))
        for item in freshness_rows
    )
    source_freshness_rows = conn.execute(
        "SELECT last_success_at,last_checked_at FROM vkpi_event_watch_targets"
    ).fetchall()
    source_freshness = Counter(
        freshness(row(item).get("last_success_at") or row(item).get("last_checked_at"))
        for item in source_freshness_rows
    )
    passport_counts = {
        "event_sources": 0,
        "event_sources_verified_fresh": 0,
        "event_opportunities": 0,
        "event_opportunities_verified_fresh": 0,
    }
    if table_exists("vkpi_source_passports"):
        passport_rows = conn.execute(
            """
            SELECT p.entity_type,p.verification_status,p.freshness_status_at_write,
                   p.verified_at,p.stale_after_days
            FROM vkpi_source_passports p
            JOIN vkpi_event_watch_targets s ON s.id=p.event_source_id
            WHERE p.organization_id=? AND p.entity_type='event_source' AND s.status='active'
            UNION ALL
            SELECT p.entity_type,p.verification_status,p.freshness_status_at_write,
                   p.verified_at,p.stale_after_days
            FROM vkpi_source_passports p
            JOIN vkpi_event_opportunities o
              ON o.organization_id=p.organization_id AND o.id=p.event_opportunity_id
            JOIN vkpi_event_watch_targets s ON s.id=o.source_id
            WHERE p.organization_id=? AND p.entity_type='event_opportunity' AND s.status='active'
            """,
            (organization_id, organization_id),
        ).fetchall()
        for raw in passport_rows:
            item = row(raw)
            entity_type = str(item.get("entity_type") or "")
            if entity_type == "event_source":
                passport_counts["event_sources"] += 1
                if passport_is_current(item, as_of=as_of):
                    passport_counts["event_sources_verified_fresh"] += 1
            elif entity_type == "event_opportunity":
                passport_counts["event_opportunities"] += 1
                if passport_is_current(item, as_of=as_of):
                    passport_counts["event_opportunities_verified_fresh"] += 1
    changed = int(row(conn.execute(
        "SELECT COUNT(*) AS n FROM vkpi_event_opportunity_changes WHERE organization_id=?", (organization_id,)
    ).fetchone()).get("n") or 0)
    promoted = int(row(conn.execute(
        "SELECT COUNT(*) AS n FROM vkpi_event_opportunity_promotions WHERE organization_id=?", (organization_id,)
    ).fetchone()).get("n") or 0)
    last_run = row(conn.execute(
        "SELECT run_key,status,started_at,finished_at,discovered_count,inserted_count,updated_count,unchanged_count,error_count FROM vkpi_event_source_runs WHERE organization_id=? ORDER BY started_at DESC LIMIT 1",
        (organization_id,),
    ).fetchone())
    last_refresh_at = (last_run or {}).get("finished_at") or (last_run or {}).get("started_at")
    compatibility = {
        "total": opportunity_total,
        "lane_counts": dict(lanes),
        "decision_counts": dict(decisions),
        "verification_counts": dict(verification),
        "evidence_counts": dict(evidence),
        "freshness_counts": dict(freshness_counts),
        "country_count": len(source_countries),
        "source_count": source_total,
        "source_freshness_counts": dict(source_freshness),
        "source_identity_verified_count": passport_counts["event_sources_verified_fresh"],
        "source_identity_coverage_rate": (
            round(passport_counts["event_sources_verified_fresh"] / source_total, 4)
            if source_total else None
        ),
        "stale_count": int(freshness_counts.get("stale", 0) + freshness_counts.get("unverified", 0)),
        "conflict_count": int(verification.get("conflict", 0)),
        "converted_count": promoted,
        "last_refresh_at": last_refresh_at,
    }
    return {
        **compatibility,
        "coverage_claim": "registered_publisher_owned_public_entries_only",
        "global_complete": False,
        "organization_id": organization_id,
        "truth_note": "Database snapshot of registered publisher-owned public entries only. Source-jurisdiction discovery coverage and extracted event-entity coverage are separate; authorization, participation, stock, attendance, ROI, sales, and local impact remain separate evidence.",
        "us_jurisdiction_matrix": us_jurisdiction_matrix,
        "sources": {
            "total": source_total,
            "countries": len(source_countries),
            "by_status": dict(source_status),
            "by_kind": dict(source_kind),
            "by_freshness": dict(source_freshness),
            "verified_fresh_passports": passport_counts["event_sources_verified_fresh"],
            "passport_coverage_rate": (
                round(passport_counts["event_sources_verified_fresh"] / source_total, 4)
                if source_total else None
            ),
        },
        "opportunities": {
            "total": opportunity_total,
            "by_lane": dict(lanes),
            "by_decision": dict(decisions),
            "by_verification": dict(verification),
            "by_evidence": dict(evidence),
            "by_freshness": dict(freshness_counts),
            "change_records": changed,
            "promoted": promoted,
            "verified_fresh_passports": passport_counts["event_opportunities_verified_fresh"],
            "passport_coverage_rate": (
                round(passport_counts["event_opportunities_verified_fresh"] / opportunity_total, 4)
                if opportunity_total else None
            ),
        },
        "last_run": last_run or None,
        "as_of": as_of.isoformat(),
    }


__all__ = ["build_summary"]
