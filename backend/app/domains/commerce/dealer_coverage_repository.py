"""Read-only Dealer coverage repository.

Database and coordinate dependencies are injected so ``dealer_scrape`` keeps
its long-standing monkeypatch boundary while the coverage implementation stays
below the release line guard.  This module never writes Dealer rows.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Callable

from app.domains.commerce.dealer_coverage_view import (
    build_us_jurisdiction_matrix,
    empty_dealer_coverage_counts,
)
from app.domains.commerce.dealer_directory_view import (
    MANAGED_DEALER_DURABLE_FIELDS,
    REVIEWED_DEALER_DURABLE_FIELDS,
    REVIEWED_DEALER_PERSISTENCE_VERSION,
    reviewed_persistence_contract,
)
from app.domains.source_passport_core import as_utc, freshness


def _utc_timestamp(value: Any) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    if parsed.tzinfo is None:
        return None
    return parsed.astimezone(timezone.utc)


def _passport_is_current(row: dict[str, Any], *, as_of_value: datetime) -> bool:
    if str(row.get("verification_status") or "") != "verified":
        return False
    if str(row.get("freshness_status_at_write") or "") != "fresh":
        return False
    stale_value = row.get("stale_after_days")
    if isinstance(stale_value, bool):
        return False
    try:
        stale_days = int(stale_value)
    except (TypeError, ValueError):
        return False
    if not 1 <= stale_days <= 3650:
        return False
    return freshness(
        row.get("verified_at"),
        as_of=as_of_value,
        stale_after_days=stale_days,
    )["status"] == "fresh"


def dealer_coverage_summary(
    *,
    organization_id: int,
    stale_after_days: int,
    as_of_value: datetime | None,
    get_conn: Callable[[], Any],
    table_exists: Callable[[str], bool],
    table_columns: Callable[[], set[str]],
    clean_lat: Callable[[Any], float | None],
    clean_lng: Callable[[Any], float | None],
) -> dict[str, Any]:
    """Build registered-entity coverage without a national completeness claim."""
    try:
        org_id = int(organization_id)
    except (TypeError, ValueError) as exc:
        raise ValueError("organization_id must be a positive integer") from exc
    if org_id <= 0:
        raise ValueError("organization_id must be a positive integer")
    try:
        stale_days = int(stale_after_days)
    except (TypeError, ValueError) as exc:
        raise ValueError("stale_after_days must be a positive integer") from exc
    if stale_days <= 0:
        raise ValueError("stale_after_days must be a positive integer")
    now = as_utc(as_of_value)

    empty = empty_dealer_coverage_counts()
    if not table_exists("vkpi_dealers"):
        return {
            **empty,
            "status": "migration_pending",
            "coverage_claim": "registered_public_listings_only",
            "global_complete": False,
            "global_denominator": None,
            "global_coverage_rate": None,
            "claim_status": "descriptive_only",
            "as_of": now.isoformat(),
        }

    columns = table_columns()
    review_contract_enforced = REVIEWED_DEALER_DURABLE_FIELDS.issubset(columns)
    publication_enforced = MANAGED_DEALER_DURABLE_FIELDS.issubset(columns)
    review_version_select = ",review_contract_version" if review_contract_enforced else ""
    publication_select = ",publication_status" if publication_enforced else ""
    rows = get_conn().execute(
        f"""
        SELECT id,state,country,lat,lng,brand_listing_url,source_status,
               authorization_status,source_checked_at,phone,contact_email,
               store_hours,public_services{review_version_select}{publication_select}
        FROM vkpi_dealers
        """,
        (),
    ).fetchall()
    items = [dict(row) for row in rows]
    if review_contract_enforced:
        items = [
            {
                **item,
                "source_status": (
                    item.get("source_status")
                    if int(item.get("review_contract_version") or 0)
                    == REVIEWED_DEALER_PERSISTENCE_VERSION
                    else "unverified"
                ),
            }
            for item in items
        ]

    threshold = now - timedelta(days=stale_days)
    fresh = stale = unavailable = 0
    for item in items:
        checked = _utc_timestamp(item.get("source_checked_at"))
        if checked is None:
            unavailable += 1
        elif threshold <= checked <= now + timedelta(minutes=5):
            fresh += 1
        else:
            stale += 1

    aliases: list[dict[str, Any]] = []
    if table_exists("vkpi_dealer_identity_aliases"):
        aliases = [
            dict(row)
            for row in get_conn().execute(
                """
                SELECT dealer_id,stable_location_key,verified_at
                FROM vkpi_dealer_identity_aliases
                WHERE organization_id=?
                """,
                (org_id,),
            ).fetchall()
        ]
    passports: list[dict[str, Any]] = []
    if table_exists("vkpi_source_passports"):
        passports = [
            dict(row)
            for row in get_conn().execute(
                """
                SELECT dealer_id,verification_status,freshness_status_at_write,
                       verified_at,stale_after_days
                FROM vkpi_source_passports
                WHERE organization_id=? AND entity_type='dealer_location'
                """,
                (org_id,),
            ).fetchall()
        ]

    alias_dealers = {
        int(row["dealer_id"])
        for row in aliases
        if row.get("dealer_id") not in (None, "")
        and row.get("verified_at") not in (None, "")
    }
    exact_location_dealers = {
        int(row["dealer_id"])
        for row in aliases
        if row.get("dealer_id") not in (None, "")
        and str(row.get("stable_location_key") or "").startswith("dealer_loc_")
        and row.get("verified_at") not in (None, "")
    }
    registered_dealer_ids = {
        int(item["id"]) for item in items if item.get("id") not in (None, "")
    }
    passport_dealers = {
        int(row["dealer_id"])
        for row in passports
        if row.get("dealer_id") not in (None, "")
        and int(row["dealer_id"]) in registered_dealer_ids
    }
    verified_fresh_passports = {
        int(row["dealer_id"])
        for row in passports
        if row.get("dealer_id") not in (None, "")
        and int(row["dealer_id"]) in registered_dealer_ids
        and _passport_is_current(row, as_of_value=now)
    }
    us_jurisdiction_matrix = build_us_jurisdiction_matrix(
        items,
        clean_lat=clean_lat,
        clean_lng=clean_lng,
    )
    total = len(items)
    verified = sum(
        str(item.get("source_status") or "") == "public_listing_verified"
        for item in items
    )
    evidence_qualified_locations = sum(
        str(item.get("source_status") or "") == "public_listing_verified"
        and clean_lat(item.get("lat")) is not None
        and clean_lng(item.get("lng")) is not None
        for item in items
    )
    published_map_pins = sum(
        (
            str(item.get("publication_status") or "") == "published"
            if publication_enforced
            else str(item.get("source_status") or "")
            == "public_listing_verified"
        )
        and clean_lat(item.get("lat")) is not None
        and clean_lng(item.get("lng")) is not None
        for item in items
    )
    return {
        "status": "ready" if total else "empty",
        "total": total,
        "public_listing_verified": verified,
        "authorized_confirmed": 0,
        "authorization_pending": total,
        # Backward-compatible evidence-qualified metric.  This is not map
        # visibility once migration 260 adds explicit publication receipts.
        "located": evidence_qualified_locations,
        "evidence_qualified_locations": evidence_qualified_locations,
        "published_map_pins": published_map_pins,
        "coordinate_present": sum(
            clean_lat(item.get("lat")) is not None
            and clean_lng(item.get("lng")) is not None
            for item in items
        ),
        "states": len(
            {
                str(item.get("state") or "").strip().upper()
                for item in items
                if str(item.get("state") or "").strip()
            }
        ),
        "countries": len(
            {str(item.get("country") or "US").strip().upper() for item in items}
        ),
        "product_page_declared": sum(
            bool(str(item.get("brand_listing_url") or "").strip()) for item in items
        ),
        "contacts": {
            key: sum(bool(str(item.get(field) or "").strip()) for item in items)
            for key, field in {
                "phone": "phone",
                "email": "contact_email",
                "hours": "store_hours",
                "services": "public_services",
            }.items()
        },
        "freshness": {"fresh": fresh, "stale": stale, "unavailable": unavailable},
        "identity": {
            "reviewed_alias_dealers": len(alias_dealers),
            "exact_location_dealers": len(exact_location_dealers),
        },
        "passports": {
            "dealer_locations": len(passport_dealers),
            "verified_fresh": len(verified_fresh_passports),
        },
        "us_jurisdiction_matrix": us_jurisdiction_matrix,
        "coverage_claim": "registered_rows_with_separate_publication_and_evidence",
        "global_complete": False,
        "global_denominator": None,
        "global_coverage_rate": None,
        "claim_status": "descriptive_only",
        "reviewed_persistence_contract": reviewed_persistence_contract(
            columns if review_contract_enforced else []
        ),
        "claim_boundaries": {
            "public_listing_proves_authorization": False,
            "product_page_proves_current_inventory": False,
            "contacts_prove_response_or_sales": False,
            "registered_rows_equal_all_us_dealers": False,
            "map_publication_proves_public_listing_evidence": False,
            "map_publication_proves_authorization": False,
            "map_publication_proves_inventory": False,
        },
        "metric_semantics": {
            "published_map_pins": "publication_status_published_with_valid_coordinates",
            "located": "legacy_evidence_qualified_location_count",
            "evidence_qualified_locations": "public_listing_verified_with_valid_coordinates",
        },
        "stale_after_days": stale_days,
        "organization_id": org_id,
        "as_of": now.isoformat(),
    }


__all__ = ["dealer_coverage_summary"]
