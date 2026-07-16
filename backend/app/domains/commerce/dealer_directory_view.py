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

import json
from datetime import datetime, timedelta, timezone
from typing import Any, Iterable
from urllib.parse import urlsplit, urlunsplit


DEALER_CHANNEL_FILTERS = frozenset(
    {"all", "offline_location", "online_product_page", "both"}
)
DEALER_EVIDENCE_FILTERS = frozenset(
    {"all", "candidate", "public_listing_verified"}
)
DEALER_PRODUCT_FILTERS = frozenset({"all", "available", "missing"})
DEALER_AUTHORIZATION_FILTERS = frozenset({"all", "confirmed", "pending"})

REVIEWED_DEALER_PERSISTENCE_VERSION = 1
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


def _is_present(value: Any) -> bool:
    return bool(_text(value))


def _canonical_website(*urls: Any) -> str | None:
    """Return only the source URL origin; never invent a retailer homepage."""
    for raw in urls:
        text = _text(raw)
        if not text:
            continue
        parsed = urlsplit(text)
        if parsed.scheme in {"http", "https"} and parsed.netloc:
            return urlunsplit((parsed.scheme, parsed.netloc, "", "", ""))
    return None


def _recorded_public_url(value: Any) -> str | None:
    """Preserve a manager-recorded HTTP(S) deep link without trusting it as evidence."""
    text = _text(value)
    if not text:
        return None
    parsed = urlsplit(text)
    if parsed.scheme in {"http", "https"} and parsed.netloc:
        return text
    return None


def _official_viltrox_url(value: Any) -> str | None:
    """Return a Viltrox-owned public URL, never a retailer/self-claim URL."""
    text = _text(value)
    if not text:
        return None
    parsed = urlsplit(text)
    hostname = str(parsed.hostname or "").strip().casefold().rstrip(".")
    if parsed.scheme not in {"http", "https"} or not hostname:
        return None
    if hostname != "viltrox.com" and not hostname.endswith(".viltrox.com"):
        return None
    return text


def _as_utc(value: Any) -> datetime | None:
    text = _text(value)
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    if parsed.tzinfo is None:
        return None
    return parsed.astimezone(timezone.utc)


def _freshness(value: Any, *, as_of: datetime, stale_after_days: int) -> str:
    checked = _as_utc(value)
    if checked is None:
        return "unavailable"
    if checked > as_of + timedelta(minutes=5):
        return "invalid_future"
    if checked >= as_of - timedelta(days=stale_after_days):
        return "fresh"
    return "stale"


def _product_evidence_status(
    *,
    product_url: str | None,
    listing_verified: bool,
    checked_at: Any,
    as_of: datetime,
    stale_after_days: int,
) -> str:
    """Separate a declared product URL from reviewed/current evidence."""
    if not product_url:
        return "unavailable"
    if not listing_verified:
        return "declared_public_url"
    freshness = _freshness(
        checked_at,
        as_of=as_of,
        stale_after_days=stale_after_days,
    )
    if freshness == "fresh":
        return "current_public_url"
    if freshness == "stale":
        return "verified_public_url"
    return "declared_public_url"


def project_dealer(
    row: dict[str, Any],
    *,
    as_of: datetime | None = None,
    stale_after_days: int = 30,
) -> dict[str, Any]:
    """Add display/query truth fields without changing the stored record."""
    now = as_of or datetime.now(timezone.utc)
    if now.tzinfo is None:
        raise ValueError("as_of must be timezone-aware")
    now = now.astimezone(timezone.utc)
    stale_days = max(1, min(int(stale_after_days or 30), 3650))

    item = dict(row)
    source_status = _text(item.get("source_status")) or "unverified"
    stored_authorization_status = (
        _text(item.get("authorization_status")) or "needs_viltrox_confirmation"
    )
    raw_review_version = item.get("review_contract_version")
    review_schema_visible = raw_review_version is not None
    try:
        review_version = int(raw_review_version or 0)
    except (TypeError, ValueError):
        review_version = 0
    durable_review_identity = all(
        _is_present(item.get(field))
        for field in (
            "source_id",
            "stable_org_key",
            "stable_location_key",
            "reviewer_id",
            "reviewed_at",
        )
    )
    raw_evidence_receipt = item.get("evidence_json")
    if isinstance(raw_evidence_receipt, str):
        try:
            raw_evidence_receipt = json.loads(raw_evidence_receipt)
        except (TypeError, ValueError, json.JSONDecodeError):
            raw_evidence_receipt = None
    source_receipt = (
        raw_evidence_receipt.get("source")
        if isinstance(raw_evidence_receipt, dict)
        else None
    )
    product_receipt = (
        raw_evidence_receipt.get("product")
        if isinstance(raw_evidence_receipt, dict)
        else None
    )
    durable_evidence_receipt = bool(
        isinstance(raw_evidence_receipt, dict)
        and raw_evidence_receipt.get("claim_status") == "descriptive_only"
        and isinstance(source_receipt, dict)
        and source_receipt.get("source_id") == _text(item.get("source_id"))
        and source_receipt.get("source_url") == _text(item.get("location_source_url"))
        and source_receipt.get("reviewer_id") == _text(item.get("reviewer_id"))
        and source_receipt.get("value_status") == "observed"
        and isinstance(product_receipt, dict)
        and product_receipt.get("source_url") == _text(item.get("brand_listing_url"))
        and product_receipt.get("value_status") == "observed"
    )
    coordinate_receipt = (
        raw_evidence_receipt.get("coordinate")
        if isinstance(raw_evidence_receipt, dict)
        and isinstance(raw_evidence_receipt.get("coordinate"), dict)
        else {}
    )
    review_contract_valid = bool(
        review_version == REVIEWED_DEALER_PERSISTENCE_VERSION
        and durable_review_identity
        and durable_evidence_receipt
    )
    # The public directory never returns the raw evidence payload.  It exposes
    # bounded receipt metadata and the already-reviewed public fields only.
    item.pop("evidence_json", None)
    # Before migration 259 the legacy read contract remains compatible.  Once
    # the version column is visible, old rows default to v0 and fail closed
    # until a human-reviewed evidence receipt is durably written.
    listing_verified = bool(
        source_status == "public_listing_verified"
        and (not review_schema_visible or review_contract_valid)
    )
    product_url = _text(item.get("brand_listing_url")) or None
    location_url = _text(item.get("location_source_url")) or None
    physical_registered = all(
        _is_present(item.get(field)) for field in ("address", "city", "state")
    )
    public_location_evidence = bool(
        listing_verified and physical_registered and location_url
    )
    product_declared = product_url is not None
    checked_at = _text(item.get("source_checked_at")) or None
    product_status = _product_evidence_status(
        product_url=product_url,
        listing_verified=listing_verified,
        checked_at=checked_at,
        as_of=now,
        stale_after_days=stale_days,
    )
    raw_authorization_evidence = item.get("authorization_evidence")
    if not isinstance(raw_authorization_evidence, dict):
        raw_authorization_evidence = {}
    official_authorization_url = _official_viltrox_url(
        raw_authorization_evidence.get("official_viltrox_source_url")
    )
    authorization_verified_at = (
        _text(raw_authorization_evidence.get("verified_at")) or None
    )
    authorization_checked = _as_utc(authorization_verified_at)
    authorization_confirmed = bool(
        stored_authorization_status == "authorized_confirmed"
        and official_authorization_url
        and authorization_checked is not None
        and authorization_checked <= now + timedelta(minutes=5)
    )
    authorization_status = (
        "authorized_confirmed"
        if authorization_confirmed
        else "needs_viltrox_confirmation"
    )
    contact_available = any(
        _is_present(item.get(field)) for field in ("phone", "contact_email")
    )
    stored_website_url = _text(item.get("website_url")) or None
    # ``website_url`` is an explicitly managed field and may intentionally be
    # a store/detail page.  Keep that deep link intact so a later edit does not
    # silently replace it with the site root.  Legacy source fallbacks remain
    # origin-only and descriptive; neither path upgrades evidence or auth.
    website_url = _recorded_public_url(stored_website_url) or _canonical_website(
        location_url, product_url
    )
    raw_social_links = item.get("social_links_json")
    if isinstance(raw_social_links, str):
        try:
            raw_social_links = json.loads(raw_social_links)
        except (TypeError, ValueError, json.JSONDecodeError):
            raw_social_links = []
    if not isinstance(raw_social_links, list):
        raw_social_links = []
    social_links: list[dict[str, str]] = []
    for raw_link in raw_social_links[:12]:
        if not isinstance(raw_link, dict):
            continue
        platform = _text(raw_link.get("platform")).casefold()
        url = _text(raw_link.get("url"))
        parsed_social_url = urlsplit(url)
        if (
            platform
            and parsed_social_url.scheme in {"http", "https"}
            and parsed_social_url.netloc
        ):
            social_links.append({"platform": platform, "url": url})
    raw_brand_relationships = item.get("brand_relationships")
    if not isinstance(raw_brand_relationships, list):
        raw_brand_relationships = []
    brand_relationships: list[dict[str, Any]] = []
    for raw_relationship in raw_brand_relationships:
        if not isinstance(raw_relationship, dict):
            continue
        brand_key = _text(raw_relationship.get("brand_key")).casefold()
        if not brand_key:
            continue
        brand_relationships.append(
            {
                "brand_key": brand_key,
                "relationship_status": (
                    _text(raw_relationship.get("relationship_status"))
                    or "unverified"
                ),
                "authorization_status": (
                    "unverified"
                ),
                "evidence_url": _text(raw_relationship.get("evidence_url")) or None,
                "source_checked_at": (
                    _text(raw_relationship.get("source_checked_at")) or None
                ),
            }
        )
    brand_relationships.sort(key=lambda relationship: relationship["brand_key"])
    brand_codes = sorted(
        {relationship["brand_key"] for relationship in brand_relationships}
    )
    stored_publication_status = _text(item.get("publication_status"))
    publication_status = (
        stored_publication_status
        if stored_publication_status in {"draft", "published"}
        else "published"
        if listing_verified
        else "draft"
    )
    deployment_status = _text(item.get("viltrox_deployment_status"))
    if deployment_status not in {"not_deployed", "planned", "deployed", "paused"}:
        deployment_status = "not_deployed"
    activity_status = _text(item.get("activity_status"))
    if activity_status not in {"unknown", "none_observed", "active"}:
        activity_status = "unknown"
    location_schema_visible = item.get("location_verification_contract_version") is not None
    try:
        location_contract_version = int(item.get("location_verification_contract_version") or 0)
    except (TypeError, ValueError):
        location_contract_version = 0
    canonical_location_status = _text(item.get("canonical_location_status")) or "pending"
    physical_store_status = _text(item.get("physical_store_status")) or "pending"
    google_place_status = _text(item.get("google_place_verification_status")) or "pending"
    coordinate_provider = _text(coordinate_receipt.get("provider")) or None
    coordinate_status = _text(coordinate_receipt.get("value_status")) or None
    coordinate_provenance_valid = bool(
        coordinate_provider == "us_census_geocoder"
        and coordinate_status == "observed"
        and coordinate_receipt.get("google_derived") is False
    )
    location_map_eligible = bool(
        location_schema_visible
        and location_contract_version == 1
        and canonical_location_status == "official_site_verified"
        and physical_store_status == "verified_physical_store"
        and publication_status == "published"
        and item.get("lat") is not None
        and item.get("lng") is not None
    )

    # Keep internal reviewer identifiers and raw provider receipts out of the
    # public directory projection.  The bounded status/timestamp projection is
    # sufficient for UI truth labels.
    for internal_field in (
        "canonical_location_checked_by",
        "physical_store_checked_by",
        "google_place_checked_by",
        "google_place_evidence_json",
    ):
        item.pop(internal_field, None)

    item.update(
        {
            "stored_source_status": source_status,
            "source_status": (
                "public_listing_verified" if listing_verified else "unverified"
            ),
            "stored_authorization_status": stored_authorization_status,
            "authorization_status": authorization_status,
            "review_contract": {
                "schema_visible": review_schema_visible,
                "version": review_version,
                "valid": review_contract_valid if review_schema_visible else None,
                "evidence_receipt_present": (
                    durable_evidence_receipt if review_schema_visible else None
                ),
                "status": (
                    "verified"
                    if listing_verified and review_contract_valid
                    else "legacy_unverified"
                    if review_schema_visible
                    and source_status == "public_listing_verified"
                    and review_version == 0
                    else "review_receipt_invalid"
                    if review_schema_visible and source_status == "public_listing_verified"
                    else "not_applicable"
                    if source_status != "public_listing_verified"
                    else "legacy_schema"
                ),
                "automatic_promotion": False,
                "claim_status": "descriptive_only",
            },
            "website_url": website_url,
            "brand_codes": brand_codes,
            "brand_relationships": brand_relationships,
            "publication_status": publication_status,
            "published_at": (
                _text(item.get("published_at")) or None
                if publication_status == "published"
                else None
            ),
            "viltrox_deployment": {
                "status": deployment_status,
                "deployed_at": _text(item.get("viltrox_deployed_at")) or None,
                "note": _text(item.get("viltrox_deployment_note")),
                "claim_status": "internal_operational_state_only",
                "proves_authorization": False,
                "proves_product_presence": False,
                "proves_inventory": False,
            },
            "activity": {
                "status": activity_status,
                "page_url": _text(item.get("activity_page_url")) or None,
                "checked_at": _text(item.get("activity_checked_at")) or None,
                "next_event_at": _text(item.get("next_activity_at")) or None,
                "note": _text(item.get("activity_note")),
                "claim_status": "descriptive_only",
            },
            "location_verification": {
                "schema_visible": location_schema_visible,
                "contract_version": location_contract_version,
                "canonical_location_status": canonical_location_status,
                "canonical_location_checked_at": _text(
                    item.get("canonical_location_checked_at")
                )
                or None,
                "physical_store_status": physical_store_status,
                "physical_store_checked_at": _text(item.get("physical_store_checked_at"))
                or None,
                "physical_store_verification_note": _text(
                    item.get("physical_store_verification_note")
                ),
                "coordinate": {
                    "provider": coordinate_provider,
                    "match_level": _text(coordinate_receipt.get("match_level")) or None,
                    "value_status": coordinate_status,
                    "google_derived": bool(coordinate_receipt.get("google_derived")),
                    "provenance_valid": coordinate_provenance_valid,
                },
                "google_place_cross_check": {
                    "status": google_place_status,
                    "place_id": _text(item.get("google_place_id")) or None,
                    "maps_url": _text(item.get("google_maps_url")) or None,
                    "checked_at": _text(item.get("google_place_checked_at")) or None,
                    "canonical_source": False,
                },
                "map_eligible": location_map_eligible,
                "claim_status": "descriptive_only",
            },
            "social_links": social_links,
            "social_status": "recorded" if social_links else "not_collected",
            "last_verified_at": checked_at if listing_verified else None,
            "freshness_status": (
                _freshness(checked_at, as_of=now, stale_after_days=stale_days)
                if listing_verified
                else "unverified"
            ),
            "coverage_scope": {
                "scope": "registered_location_only",
                "country": _text(item.get("country")) or "US",
                "state": _text(item.get("state")) or None,
                "city": _text(item.get("city")) or None,
                "service_area": None,
                "claim_status": "descriptive_only",
            },
            "channel_evidence": {
                "physical_location_registered": physical_registered,
                "offline_location": (
                    "public_listing_verified"
                    if public_location_evidence
                    else "candidate"
                    if physical_registered
                    else "unavailable"
                ),
                "online_product_page": (
                    product_status
                ),
                "online_sales": "unknown",
                "current_inventory": "unknown",
            },
            "truth_status": {
                "candidate": not listing_verified,
                "public_listing": (
                    "verified" if listing_verified else "unverified"
                ),
                "product_evidence": (
                    product_status
                ),
                "viltrox_authorization": (
                    "confirmed" if authorization_confirmed else "pending"
                ),
                "current_inventory": "unknown",
            },
            "product_evidence": {
                "status": (
                    product_status
                ),
                "url": product_url,
                "checked_at": checked_at if product_declared else None,
                "current_inventory": "unknown",
                "claim_status": "descriptive_only",
            },
            "authorization_evidence": {
                "status": (
                    "authorized_confirmed"
                    if authorization_confirmed
                    else "needs_viltrox_confirmation"
                ),
                "official_viltrox_source_url": official_authorization_url,
                "verified_at": (
                    authorization_verified_at if authorization_confirmed else None
                ),
                "stored_status": stored_authorization_status,
                "block_reason": (
                    None
                    if authorization_confirmed
                    else "official_viltrox_source_url_and_verified_at_required"
                ),
                "claim_status": "descriptive_only",
            },
            "provenance": {
                "public_listing": {
                    "status": "verified" if listing_verified else "unverified",
                    "source_url": location_url,
                    "checked_at": checked_at if listing_verified else None,
                    "source_id": _text(item.get("source_id")) or None,
                    "stable_org_key": _text(item.get("stable_org_key")) or None,
                    "stable_location_key": _text(item.get("stable_location_key")) or None,
                    "reviewer_id": _text(item.get("reviewer_id")) or None,
                    "reviewed_at": _text(item.get("reviewed_at")) or None,
                },
                "product": {
                    "status": product_status,
                    "source_url": product_url,
                    "checked_at": checked_at if product_declared else None,
                },
                "contact": {
                    "status": (
                        "public_listing_contact"
                        if contact_available and listing_verified
                        else "unverified"
                        if contact_available
                        else "unavailable"
                    ),
                    "source_url": location_url if contact_available else None,
                    "checked_at": checked_at if contact_available else None,
                },
                "website": {
                    "status": (
                        "manager_recorded_public_url"
                        if stored_website_url
                        else "derived_from_public_source_url"
                        if website_url
                        else "unavailable"
                    ),
                    "source_url": stored_website_url or location_url or product_url,
                    "checked_at": (
                        None
                        if stored_website_url
                        else checked_at
                        if website_url
                        else None
                    ),
                },
                "social": {
                    "status": "recorded" if social_links else "not_collected",
                    "source_url": None,
                    "checked_at": None,
                    "claim_status": "descriptive_only",
                },
                "viltrox_authorization": {
                    "status": (
                        "confirmed" if authorization_confirmed else "pending"
                    ),
                    "source_url": official_authorization_url,
                    "checked_at": (
                        authorization_verified_at if authorization_confirmed else None
                    ),
                },
                "brand_relationships": {
                    "status": "recorded" if brand_relationships else "unavailable",
                    "brand_codes": brand_codes,
                    "claim_status": "per_brand_evidence_only",
                    "proves_viltrox_authorization": False,
                },
                "activity": {
                    "status": activity_status,
                    "source_url": _text(item.get("activity_page_url")) or None,
                    "checked_at": _text(item.get("activity_checked_at")) or None,
                },
            },
        }
    )
    return item


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
