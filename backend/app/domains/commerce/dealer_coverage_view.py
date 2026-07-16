"""Pure Dealer coverage projections shared by the scrape/read model.

This module deliberately owns no database connection or persistence seam.  The
public ``dealer_coverage_summary`` API remains in ``dealer_scrape`` so tests and
callers can keep patching that module's ``get_conn``/``table_exists`` boundary.
"""
from __future__ import annotations

from collections import Counter
from collections.abc import Callable
from typing import Any

from app.shared.us_jurisdiction_coverage import (
    US_STATE_AND_DC_CODES,
    registered_us_jurisdiction_matrix,
)

CoordinateCleaner = Callable[[Any], float | None]


def empty_dealer_coverage_counts() -> dict[str, Any]:
    """Return the zero-state shape used before the Dealer migration exists."""
    return {
        "total": 0,
        "public_listing_verified": 0,
        "authorized_confirmed": 0,
        "authorization_pending": 0,
        "located": 0,
        "published_map_pins": 0,
        "evidence_qualified_locations": 0,
        "coordinate_present": 0,
        "states": 0,
        "countries": 0,
        "product_page_declared": 0,
        "contacts": {"phone": 0, "email": 0, "hours": 0, "services": 0},
        "freshness": {"fresh": 0, "stale": 0, "unavailable": 0},
        "identity": {"reviewed_alias_dealers": 0, "exact_location_dealers": 0},
        "passports": {"dealer_locations": 0, "verified_fresh": 0},
        "us_jurisdiction_matrix": {
            **registered_us_jurisdiction_matrix([]),
            "dealer_counts_by_state_dc": {},
            "public_listing_verified_counts_by_state_dc": {},
            "coordinate_present_counts_by_state_dc": {},
            "map_eligible_counts_by_state_dc": {},
            "published_map_pin_counts_by_state_dc": {},
            "evidence_qualified_counts_by_state_dc": {},
            "located_counts_by_state_dc": {},
            "dealer_entity_count": 0,
            "map_precision": "registered_state_dc_aggregate_not_store_coordinates",
        },
    }


def build_us_jurisdiction_matrix(
    items: list[dict[str, Any]],
    *,
    clean_lat: CoordinateCleaner,
    clean_lng: CoordinateCleaner,
) -> dict[str, Any]:
    """Project exact registered-entity counters without claiming US completeness."""
    us_items = [
        item
        for item in items
        if str(item.get("country") or "US").strip().upper() == "US"
    ]
    dealer_counts = Counter(
        state
        for item in us_items
        if (state := str(item.get("state") or "").strip().upper())
        in US_STATE_AND_DC_CODES
    )
    verified_counts = Counter(
        state
        for item in us_items
        if str(item.get("source_status") or "") == "public_listing_verified"
        and (state := str(item.get("state") or "").strip().upper())
        in US_STATE_AND_DC_CODES
    )
    coordinate_counts = Counter(
        state
        for item in us_items
        if clean_lat(item.get("lat")) is not None
        and clean_lng(item.get("lng")) is not None
        and (state := str(item.get("state") or "").strip().upper())
        in US_STATE_AND_DC_CODES
    )
    evidence_qualified_counts = Counter(
        state
        for item in us_items
        if str(item.get("source_status") or "") == "public_listing_verified"
        and clean_lat(item.get("lat")) is not None
        and clean_lng(item.get("lng")) is not None
        and (state := str(item.get("state") or "").strip().upper())
        in US_STATE_AND_DC_CODES
    )
    published_map_counts = Counter(
        state
        for item in us_items
        if (
            str(item.get("publication_status") or "") == "published"
            if "publication_status" in item
            else str(item.get("source_status") or "")
            == "public_listing_verified"
        )
        and clean_lat(item.get("lat")) is not None
        and clean_lng(item.get("lng")) is not None
        and (state := str(item.get("state") or "").strip().upper())
        in US_STATE_AND_DC_CODES
    )
    return {
        **registered_us_jurisdiction_matrix(dealer_counts),
        "dealer_counts_by_state_dc": dict(sorted(dealer_counts.items())),
        "public_listing_verified_counts_by_state_dc": dict(
            sorted(verified_counts.items())
        ),
        "coordinate_present_counts_by_state_dc": dict(
            sorted(coordinate_counts.items())
        ),
        "published_map_pin_counts_by_state_dc": dict(
            sorted(published_map_counts.items())
        ),
        "evidence_qualified_counts_by_state_dc": dict(
            sorted(evidence_qualified_counts.items())
        ),
        # Correctly named map visibility plus the legacy evidence metric.
        "map_eligible_counts_by_state_dc": dict(sorted(published_map_counts.items())),
        "located_counts_by_state_dc": dict(sorted(evidence_qualified_counts.items())),
        "dealer_entity_count": sum(dealer_counts.values()),
        "map_precision": "registered_state_dc_aggregate_not_store_coordinates",
    }
