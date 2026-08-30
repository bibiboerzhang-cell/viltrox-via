"""Truth-bounded Dealer directory projections and query filters.

This module deliberately keeps four different facts separate:

* a candidate row exists;
* a retailer-owned public location page was reviewed;
* a retailer-owned page mentions a Viltrox product;
* Viltrox itself confirmed an authorization relationship.

A product page is not current inventory, and another manufacturer's dealer
directory is never Viltrox authorization.  The helpers are pure so the same
contract can be tested without crawling providers or writing PostgreSQL.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any, Iterable

from app.domains.commerce.dealer_directory_projection import (
    REVIEWED_DEALER_PERSISTENCE_VERSION,
    project_dealer as _project_dealer,
)


DEALER_CHANNEL_FILTERS = frozenset(
    {"all", "offline_location", "online_product_page", "both"}
)
DEALER_EVIDENCE_FILTERS = frozenset(
    {"all", "candidate", "public_listing_verified"}
)
DEALER_PRODUCT_FILTERS = frozenset({"all", "available", "missing"})
DEALER_AUTHORIZATION_FILTERS = frozenset({"all", "confirmed", "pending"})

REVIEWED_DEALER_DURABLE_FIELDS = frozenset(
    {
        "source_id",
        "stable_org_key",
        "stable_location_key",
        "reviewer_id",
        "reviewed_at",
        "evidence_json",
        "review_contract_version",
    }
)

# Migration 260 fields are intentionally separate from the migration-259
# reviewed-evidence contract.  Publishing a pin or recording an internal
# rollout never upgrades public-listing, authorization, product, inventory or
# sales evidence.
MANAGED_DEALER_DURABLE_FIELDS = frozenset(
    {
        "publication_status",
        "published_at",
        "published_by",
        "viltrox_deployment_status",
        "viltrox_deployed_at",
        "viltrox_deployed_by",
        "viltrox_deployment_note",
        "activity_status",
        "activity_page_url",
        "activity_checked_at",
        "next_activity_at",
        "activity_note",
        "website_url",
        "social_links_json",
        "updated_at",
    }
)

# Migration 270 fields are read as one optional bundle.  Older databases keep
# the legacy response shape; once the bundle exists, the projection can prove
# map eligibility without treating a Google cross-check as canonical.
LOCATION_VERIFICATION_DURABLE_FIELDS = frozenset(
    {
        "location_verification_contract_version",
        "canonical_location_status",
        "canonical_location_checked_at",
        "physical_store_status",
        "physical_store_checked_at",
        "physical_store_verification_note",
        "google_place_verification_status",
        "google_place_id",
        "google_maps_url",
        "google_place_checked_at",
    }
)


def reviewed_persistence_contract(
    available_fields: Iterable[str] | None = None,
) -> dict[str, Any]:
    """Describe the durable reviewed-Dealer schema without probing the DB.

    Callers must pass the fields they actually observed on ``vkpi_dealers``.
    Omitting that evidence deliberately fails closed, so merely shipping the
    application code can never unlock reviewed imports before migration 259 is
    present on the active database.
    """
    present = {str(field or "").strip() for field in (available_fields or [])}
    missing = sorted(REVIEWED_DEALER_DURABLE_FIELDS.difference(present))
    if not missing:
        return {
            "supported": True,
            "status": "ready",
            "reason": None,
            "contract_version": REVIEWED_DEALER_PERSISTENCE_VERSION,
            "required_durable_fields": sorted(REVIEWED_DEALER_DURABLE_FIELDS),
            "missing_durable_fields": [],
            "automatic_promotion": False,
            "claim_status": "descriptive_only",
        }
    return {
        "supported": False,
        "status": "migration_required",
        "reason": "reviewed_identity_and_evidence_columns_unavailable",
        "contract_version": REVIEWED_DEALER_PERSISTENCE_VERSION,
        "required_durable_fields": sorted(REVIEWED_DEALER_DURABLE_FIELDS),
        "missing_durable_fields": missing,
        "automatic_promotion": False,
        "claim_status": "descriptive_only",
    }


def _text(value: Any) -> str:
    return str(value or "").strip()


def project_dealer(
    row: dict[str, Any],
    *,
    as_of: datetime | None = None,
    stale_after_days: int = 30,
) -> dict[str, Any]:
    """Add display/query truth fields without changing the stored record."""
    return _project_dealer(
        row,
        as_of=as_of,
        stale_after_days=stale_after_days,
    )


def validate_filter(value: Any, allowed: Iterable[str], *, field: str) -> str:
    normalized = _text(value).lower() or "all"
    allowed_set = frozenset(allowed)
    if normalized not in allowed_set:
        raise ValueError(f"{field} must be one of: {', '.join(sorted(allowed_set))}")
    return normalized


def dealer_matches(
    dealer: dict[str, Any],
    *,
    state: str | None = None,
    city: str | None = None,
    channel: str = "all",
    evidence_status: str = "all",
    product_evidence: str = "all",
    authorization: str = "all",
) -> bool:
    """Apply the public directory contract to one projected dealer."""
    channel_value = validate_filter(channel, DEALER_CHANNEL_FILTERS, field="channel")
    evidence_value = validate_filter(
        evidence_status, DEALER_EVIDENCE_FILTERS, field="evidence_status"
    )
    product_value = validate_filter(
        product_evidence, DEALER_PRODUCT_FILTERS, field="product_evidence"
    )
    authorization_value = validate_filter(
        authorization, DEALER_AUTHORIZATION_FILTERS, field="authorization"
    )

    if state and _text(dealer.get("state")).upper() != _text(state).upper():
        return False
    if city and _text(dealer.get("city")).casefold() != _text(city).casefold():
        return False

    truth = dealer.get("truth_status") or {}
    channels = dealer.get("channel_evidence") or {}
    offline = bool(channels.get("physical_location_registered"))
    product_statuses = {
        "declared_public_url",
        "verified_public_url",
        "current_public_url",
    }
    online = channels.get("online_product_page") in product_statuses
    if channel_value == "offline_location" and not offline:
        return False
    if channel_value == "online_product_page" and not online:
        return False
    if channel_value == "both" and not (offline and online):
        return False
    if evidence_value == "candidate" and not bool(truth.get("candidate")):
        return False
    if evidence_value == "public_listing_verified" and truth.get("public_listing") != "verified":
        return False
    has_product = truth.get("product_evidence") in product_statuses
    if product_value == "available" and not has_product:
        return False
    if product_value == "missing" and has_product:
        return False
    is_authorized = truth.get("viltrox_authorization") == "confirmed"
    if authorization_value == "confirmed" and not is_authorized:
        return False
    if authorization_value == "pending" and is_authorized:
        return False
    return True


def filter_dealers(
    rows: Iterable[dict[str, Any]],
    **filters: Any,
) -> list[dict[str, Any]]:
    """Project and filter rows, preserving their input order."""
    projected = [project_dealer(dict(row)) for row in rows]
    return [row for row in projected if dealer_matches(row, **filters)]


def _coordinate(value: Any, *, minimum: float, maximum: float) -> float | None:
    """Return a finite in-range map coordinate or fail closed."""
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if not minimum <= number <= maximum:
        return None
    return number


def build_dealer_pins(
    rows: Iterable[dict[str, Any]],
    *,
    color: str = "#10b981",
) -> list[dict[str, Any]]:
    """Project already-reviewed Dealer rows into safe map pins."""
    pins: list[dict[str, Any]] = []
    for row in rows:
        location_verification = row.get("location_verification")
        if (
            isinstance(location_verification, dict)
            and location_verification.get("schema_visible") is True
            and location_verification.get("map_eligible") is not True
        ):
            continue
        lat = _coordinate(row.get("lat"), minimum=-90, maximum=90)
        lng = _coordinate(row.get("lng"), minimum=-180, maximum=180)
        if lat is None or lng is None:
            continue
        pins.append(
            {
                "id": row.get("id"),
                "name": row.get("name"),
                "address": row.get("address"),
                "city": row.get("city"),
                "state": row.get("state"),
                "lat": lat,
                "lng": lng,
                "color": color,
                "website_url": row.get("website_url"),
                "phone": row.get("phone"),
                "contact_email": row.get("contact_email"),
                "social_links": row.get("social_links") or [],
                "social_status": row.get("social_status"),
                "brand_listing_url": row.get("brand_listing_url"),
                "location_source_url": row.get("location_source_url"),
                "source_status": row.get("source_status"),
                "authorization_status": row.get("authorization_status"),
                "source_checked_at": row.get("source_checked_at"),
                "last_verified_at": row.get("last_verified_at"),
                "freshness_status": row.get("freshness_status"),
                "verification_note": row.get("verification_note"),
                "brand_codes": row.get("brand_codes") or [],
                "brand_relationships": row.get("brand_relationships") or [],
                "publication_status": row.get("publication_status") or "draft",
                "published_at": row.get("published_at"),
                "viltrox_deployment": row.get("viltrox_deployment"),
                "activity": row.get("activity"),
                "coverage_scope": row.get("coverage_scope"),
                "channel_evidence": row.get("channel_evidence"),
                "truth_status": row.get("truth_status"),
                "product_evidence": row.get("product_evidence"),
                "authorization_evidence": row.get("authorization_evidence"),
                "provenance": row.get("provenance"),
                "location_verification": location_verification,
            }
        )
    return pins
