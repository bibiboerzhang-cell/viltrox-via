"""Pure projection components for the public Dealer directory.

The public entry point remains :func:`dealer_directory_view.project_dealer`.
This module keeps the evidence, authorization, location, and presentation
decisions separate so each truth boundary can be reviewed independently.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any
from urllib.parse import urlsplit, urlunsplit


REVIEWED_DEALER_PERSISTENCE_VERSION = 1


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
    """Preserve a manager-recorded HTTP(S) deep link without trusting it."""
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


@dataclass(frozen=True)
class _ReviewState:
    source_status: str
    schema_visible: bool
    version: int
    contract_valid: bool
    evidence_receipt_present: bool
    listing_verified: bool
    coordinate_receipt: dict[str, Any]


@dataclass(frozen=True)
class _AuthorizationState:
    stored_status: str
    status: str
    confirmed: bool
    official_url: str | None
    verified_at: str | None


@dataclass(frozen=True)
class _LocationState:
    schema_visible: bool
    contract_version: int
    canonical_status: str
    physical_store_status: str
    google_place_status: str
    coordinate_provider: str | None
    coordinate_status: str | None
    coordinate_provenance_valid: bool
    map_eligible: bool


def _parse_json(value: Any, *, fallback: Any) -> Any:
    if not isinstance(value, str):
        return value
    try:
        return json.loads(value)
    except (TypeError, ValueError, json.JSONDecodeError):
        return fallback


def _integer(value: Any) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def _review_state(item: dict[str, Any]) -> _ReviewState:
    source_status = _text(item.get("source_status")) or "unverified"
    raw_review_version = item.get("review_contract_version")
    schema_visible = raw_review_version is not None
    version = _integer(raw_review_version)
    durable_identity = all(
        _is_present(item.get(field))
        for field in (
            "source_id",
            "stable_org_key",
            "stable_location_key",
            "reviewer_id",
            "reviewed_at",
        )
    )
    raw_receipt = _parse_json(item.get("evidence_json"), fallback=None)
    source_receipt = raw_receipt.get("source") if isinstance(raw_receipt, dict) else None
    product_receipt = raw_receipt.get("product") if isinstance(raw_receipt, dict) else None
    durable_receipt = bool(
        isinstance(raw_receipt, dict)
        and raw_receipt.get("claim_status") == "descriptive_only"
        and isinstance(source_receipt, dict)
        and source_receipt.get("source_id") == _text(item.get("source_id"))
        and source_receipt.get("source_url")
        == _text(item.get("location_source_url"))
        and source_receipt.get("reviewer_id") == _text(item.get("reviewer_id"))
        and source_receipt.get("value_status") == "observed"
        and isinstance(product_receipt, dict)
        and product_receipt.get("source_url")
        == _text(item.get("brand_listing_url"))
        and product_receipt.get("value_status") == "observed"
    )
    coordinate_receipt = (
        raw_receipt.get("coordinate")
        if isinstance(raw_receipt, dict)
        and isinstance(raw_receipt.get("coordinate"), dict)
        else {}
    )
    contract_valid = bool(
        version == REVIEWED_DEALER_PERSISTENCE_VERSION
        and durable_identity
        and durable_receipt
    )
    listing_verified = bool(
        source_status == "public_listing_verified"
        and (not schema_visible or contract_valid)
    )
    return _ReviewState(
        source_status=source_status,
        schema_visible=schema_visible,
        version=version,
        contract_valid=contract_valid,
        evidence_receipt_present=durable_receipt,
        listing_verified=listing_verified,
        coordinate_receipt=coordinate_receipt,
    )


def _authorization_state(
    item: dict[str, Any],
    *,
    now: datetime,
) -> _AuthorizationState:
    stored_status = (
        _text(item.get("authorization_status")) or "needs_viltrox_confirmation"
    )
    raw_evidence = item.get("authorization_evidence")
    if not isinstance(raw_evidence, dict):
        raw_evidence = {}
    official_url = _official_viltrox_url(
        raw_evidence.get("official_viltrox_source_url")
    )
    verified_at = _text(raw_evidence.get("verified_at")) or None
    checked = _as_utc(verified_at)
    confirmed = bool(
        stored_status == "authorized_confirmed"
        and official_url
        and checked is not None
        and checked <= now + timedelta(minutes=5)
    )
    return _AuthorizationState(
        stored_status=stored_status,
        status=(
            "authorized_confirmed" if confirmed else "needs_viltrox_confirmation"
        ),
        confirmed=confirmed,
        official_url=official_url,
        verified_at=verified_at,
    )


def _social_links(item: dict[str, Any]) -> list[dict[str, str]]:
    raw_links = _parse_json(item.get("social_links_json"), fallback=[])
    if not isinstance(raw_links, list):
        raw_links = []
    links: list[dict[str, str]] = []
    for raw_link in raw_links[:12]:
        if not isinstance(raw_link, dict):
            continue
        platform = _text(raw_link.get("platform")).casefold()
        url = _text(raw_link.get("url"))
        parsed = urlsplit(url)
        if platform and parsed.scheme in {"http", "https"} and parsed.netloc:
            links.append({"platform": platform, "url": url})
    return links


def _brand_relationships(
    item: dict[str, Any],
) -> tuple[list[dict[str, Any]], list[str]]:
    raw_relationships = item.get("brand_relationships")
    if not isinstance(raw_relationships, list):
        raw_relationships = []
    relationships: list[dict[str, Any]] = []
    for raw_relationship in raw_relationships:
        if not isinstance(raw_relationship, dict):
            continue
        brand_key = _text(raw_relationship.get("brand_key")).casefold()
        if not brand_key:
            continue
        relationships.append(
            {
                "brand_key": brand_key,
                "relationship_status": (
                    _text(raw_relationship.get("relationship_status")) or "unverified"
                ),
                "authorization_status": "unverified",
                "evidence_url": _text(raw_relationship.get("evidence_url")) or None,
                "source_checked_at": (
                    _text(raw_relationship.get("source_checked_at")) or None
                ),
            }
        )
    relationships.sort(key=lambda relationship: relationship["brand_key"])
    brand_codes = sorted({relationship["brand_key"] for relationship in relationships})
    return relationships, brand_codes


def _publication_status(item: dict[str, Any], *, listing_verified: bool) -> str:
    stored_status = _text(item.get("publication_status"))
    if stored_status in {"draft", "published"}:
        return stored_status
    return "published" if listing_verified else "draft"


def _managed_status(item: dict[str, Any], field: str, allowed: set[str], default: str) -> str:
    status = _text(item.get(field))
    return status if status in allowed else default


def _location_state(
    item: dict[str, Any],
    *,
    coordinate_receipt: dict[str, Any],
    publication_status: str,
) -> _LocationState:
    schema_visible = item.get("location_verification_contract_version") is not None
    contract_version = _integer(item.get("location_verification_contract_version"))
    canonical_status = _text(item.get("canonical_location_status")) or "pending"
    physical_status = _text(item.get("physical_store_status")) or "pending"
    google_status = _text(item.get("google_place_verification_status")) or "pending"
    coordinate_provider = _text(coordinate_receipt.get("provider")) or None
    coordinate_status = _text(coordinate_receipt.get("value_status")) or None
    provenance_valid = bool(
        coordinate_provider == "us_census_geocoder"
        and coordinate_status == "observed"
        and coordinate_receipt.get("google_derived") is False
    )
    map_eligible = bool(
        schema_visible
        and contract_version == 1
        and canonical_status == "official_site_verified"
        and physical_status == "verified_physical_store"
        and publication_status == "published"
        and item.get("lat") is not None
        and item.get("lng") is not None
    )
    return _LocationState(
        schema_visible=schema_visible,
        contract_version=contract_version,
        canonical_status=canonical_status,
        physical_store_status=physical_status,
        google_place_status=google_status,
        coordinate_provider=coordinate_provider,
        coordinate_status=coordinate_status,
        coordinate_provenance_valid=provenance_valid,
        map_eligible=map_eligible,
    )


def _review_contract_view(review: _ReviewState) -> dict[str, Any]:
    if review.listing_verified and review.contract_valid:
        status = "verified"
    elif (
        review.schema_visible
        and review.source_status == "public_listing_verified"
        and review.version == 0
    ):
        status = "legacy_unverified"
    elif review.schema_visible and review.source_status == "public_listing_verified":
        status = "review_receipt_invalid"
    elif review.source_status != "public_listing_verified":
        status = "not_applicable"
    else:
        status = "legacy_schema"
    return {
        "schema_visible": review.schema_visible,
        "version": review.version,
        "valid": review.contract_valid if review.schema_visible else None,
        "evidence_receipt_present": (
            review.evidence_receipt_present if review.schema_visible else None
        ),
        "status": status,
        "automatic_promotion": False,
        "claim_status": "descriptive_only",
    }


def _location_verification_view(
    item: dict[str, Any],
    *,
    location: _LocationState,
    coordinate_receipt: dict[str, Any],
) -> dict[str, Any]:
    return {
        "schema_visible": location.schema_visible,
        "contract_version": location.contract_version,
        "canonical_location_status": location.canonical_status,
        "canonical_location_checked_at": (
            _text(item.get("canonical_location_checked_at")) or None
        ),
        "physical_store_status": location.physical_store_status,
        "physical_store_checked_at": (
            _text(item.get("physical_store_checked_at")) or None
        ),
        "physical_store_verification_note": _text(
            item.get("physical_store_verification_note")
        ),
        "coordinate": {
            "provider": location.coordinate_provider,
            "match_level": _text(coordinate_receipt.get("match_level")) or None,
            "value_status": location.coordinate_status,
            "google_derived": bool(coordinate_receipt.get("google_derived")),
            "provenance_valid": location.coordinate_provenance_valid,
        },
        "google_place_cross_check": {
            "status": location.google_place_status,
            "place_id": _text(item.get("google_place_id")) or None,
            "maps_url": _text(item.get("google_maps_url")) or None,
            "checked_at": _text(item.get("google_place_checked_at")) or None,
            "canonical_source": False,
        },
        "map_eligible": location.map_eligible,
        "claim_status": "descriptive_only",
    }


def _channel_views(
    *,
    physical_registered: bool,
    public_location_evidence: bool,
    product_status: str,
    listing_verified: bool,
    authorization_confirmed: bool,
) -> tuple[dict[str, Any], dict[str, Any]]:
    if public_location_evidence:
        offline_location = "public_listing_verified"
    elif physical_registered:
        offline_location = "candidate"
    else:
        offline_location = "unavailable"
    return (
        {
            "physical_location_registered": physical_registered,
            "offline_location": offline_location,
            "online_product_page": product_status,
            "online_sales": "unknown",
            "current_inventory": "unknown",
        },
        {
            "candidate": not listing_verified,
            "public_listing": "verified" if listing_verified else "unverified",
            "product_evidence": product_status,
            "viltrox_authorization": (
                "confirmed" if authorization_confirmed else "pending"
            ),
            "current_inventory": "unknown",
        },
    )


def _authorization_evidence_view(
    authorization: _AuthorizationState,
) -> dict[str, Any]:
    return {
        "status": authorization.status,
        "official_viltrox_source_url": authorization.official_url,
        "verified_at": authorization.verified_at if authorization.confirmed else None,
        "stored_status": authorization.stored_status,
        "block_reason": (
            None
            if authorization.confirmed
            else "official_viltrox_source_url_and_verified_at_required"
        ),
        "claim_status": "descriptive_only",
    }


def _contact_provenance(
    *,
    contact_available: bool,
    listing_verified: bool,
    location_url: str | None,
    checked_at: str | None,
) -> dict[str, Any]:
    if contact_available and listing_verified:
        status = "public_listing_contact"
    elif contact_available:
        status = "unverified"
    else:
        status = "unavailable"
    return {
        "status": status,
        "source_url": location_url if contact_available else None,
        "checked_at": checked_at if contact_available else None,
    }


def _website_provenance(
    *,
    stored_url: str | None,
    website_url: str | None,
    location_url: str | None,
    product_url: str | None,
    checked_at: str | None,
) -> dict[str, Any]:
    if stored_url:
        status = "manager_recorded_public_url"
    elif website_url:
        status = "derived_from_public_source_url"
    else:
        status = "unavailable"
    return {
        "status": status,
        "source_url": stored_url or location_url or product_url,
        "checked_at": None if stored_url else checked_at if website_url else None,
    }


def _provenance_view(
    item: dict[str, Any],
    *,
    review: _ReviewState,
    authorization: _AuthorizationState,
    product_status: str,
    product_url: str | None,
    product_declared: bool,
    location_url: str | None,
    checked_at: str | None,
    contact_available: bool,
    stored_website_url: str | None,
    website_url: str | None,
    social_links: list[dict[str, str]],
    brand_relationships: list[dict[str, Any]],
    brand_codes: list[str],
    activity_status: str,
) -> dict[str, Any]:
    return {
        "public_listing": {
            "status": "verified" if review.listing_verified else "unverified",
            "source_url": location_url,
            "checked_at": checked_at if review.listing_verified else None,
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
        "contact": _contact_provenance(
            contact_available=contact_available,
            listing_verified=review.listing_verified,
            location_url=location_url,
            checked_at=checked_at,
        ),
        "website": _website_provenance(
            stored_url=stored_website_url,
            website_url=website_url,
            location_url=location_url,
            product_url=product_url,
            checked_at=checked_at,
        ),
        "social": {
            "status": "recorded" if social_links else "not_collected",
            "source_url": None,
            "checked_at": None,
            "claim_status": "descriptive_only",
        },
        "viltrox_authorization": {
            "status": "confirmed" if authorization.confirmed else "pending",
            "source_url": authorization.official_url,
            "checked_at": (
                authorization.verified_at if authorization.confirmed else None
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
    }


def _remove_internal_fields(item: dict[str, Any]) -> None:
    item.pop("evidence_json", None)
    for internal_field in (
        "canonical_location_checked_by",
        "physical_store_checked_by",
        "google_place_checked_by",
        "google_place_evidence_json",
    ):
        item.pop(internal_field, None)


def project_dealer(
    row: dict[str, Any],
    *,
    as_of: datetime | None = None,
    stale_after_days: int = 30,
) -> dict[str, Any]:
    """Return the truth-bounded Dealer projection without mutating ``row``."""
    now = as_of or datetime.now(timezone.utc)
    if now.tzinfo is None:
        raise ValueError("as_of must be timezone-aware")
    now = now.astimezone(timezone.utc)
    stale_days = max(1, min(int(stale_after_days or 30), 3650))

    item = dict(row)
    review = _review_state(item)
    authorization = _authorization_state(item, now=now)
    product_url = _text(item.get("brand_listing_url")) or None
    location_url = _text(item.get("location_source_url")) or None
    physical_registered = all(
        _is_present(item.get(field)) for field in ("address", "city", "state")
    )
    public_location_evidence = bool(
        review.listing_verified and physical_registered and location_url
    )
    product_declared = product_url is not None
    checked_at = _text(item.get("source_checked_at")) or None
    product_status = _product_evidence_status(
        product_url=product_url,
        listing_verified=review.listing_verified,
        checked_at=checked_at,
        as_of=now,
        stale_after_days=stale_days,
    )
    contact_available = any(
        _is_present(item.get(field)) for field in ("phone", "contact_email")
    )
    stored_website_url = _text(item.get("website_url")) or None
    website_url = _recorded_public_url(stored_website_url) or _canonical_website(
        location_url, product_url
    )
    social_links = _social_links(item)
    brand_relationships, brand_codes = _brand_relationships(item)
    publication_status = _publication_status(
        item, listing_verified=review.listing_verified
    )
    deployment_status = _managed_status(
        item,
        "viltrox_deployment_status",
        {"not_deployed", "planned", "deployed", "paused"},
        "not_deployed",
    )
    activity_status = _managed_status(
        item,
        "activity_status",
        {"unknown", "none_observed", "active"},
        "unknown",
    )
    location = _location_state(
        item,
        coordinate_receipt=review.coordinate_receipt,
        publication_status=publication_status,
    )
    channel_evidence, truth_status = _channel_views(
        physical_registered=physical_registered,
        public_location_evidence=public_location_evidence,
        product_status=product_status,
        listing_verified=review.listing_verified,
        authorization_confirmed=authorization.confirmed,
    )
    provenance = _provenance_view(
        item,
        review=review,
        authorization=authorization,
        product_status=product_status,
        product_url=product_url,
        product_declared=product_declared,
        location_url=location_url,
        checked_at=checked_at,
        contact_available=contact_available,
        stored_website_url=stored_website_url,
        website_url=website_url,
        social_links=social_links,
        brand_relationships=brand_relationships,
        brand_codes=brand_codes,
        activity_status=activity_status,
    )
    _remove_internal_fields(item)

    item.update(
        {
            "stored_source_status": review.source_status,
            "source_status": (
                "public_listing_verified" if review.listing_verified else "unverified"
            ),
            "stored_authorization_status": authorization.stored_status,
            "authorization_status": authorization.status,
            "review_contract": _review_contract_view(review),
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
            "location_verification": _location_verification_view(
                item,
                location=location,
                coordinate_receipt=review.coordinate_receipt,
            ),
            "social_links": social_links,
            "social_status": "recorded" if social_links else "not_collected",
            "last_verified_at": checked_at if review.listing_verified else None,
            "freshness_status": (
                _freshness(checked_at, as_of=now, stale_after_days=stale_days)
                if review.listing_verified
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
            "channel_evidence": channel_evidence,
            "truth_status": truth_status,
            "product_evidence": {
                "status": product_status,
                "url": product_url,
                "checked_at": checked_at if product_declared else None,
                "current_inventory": "unknown",
                "claim_status": "descriptive_only",
            },
            "authorization_evidence": _authorization_evidence_view(authorization),
            "provenance": provenance,
        }
    )
    return item
