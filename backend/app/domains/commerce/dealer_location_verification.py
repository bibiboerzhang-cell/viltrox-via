"""Fail-closed physical Dealer location verification.

The retailer/store-owned website and its published address are canonical.
Google Places is an optional cross-check only.  This module performs no HTTP
requests and never derives a Place ID.  Product/brand evidence is exposed as a
separate fact and cannot make a row map-eligible.
"""
from __future__ import annotations

import json
import os
from typing import Any, Mapping
from urllib.parse import urlsplit

from app.db.connection import get_conn, is_postgres_runtime, table_exists


CONTRACT_VERSION = 1
REQUIRED_FIELDS = frozenset(
    {
        "location_verification_contract_version",
        "canonical_location_status",
        "canonical_location_checked_at",
        "canonical_location_checked_by",
        "physical_store_status",
        "physical_store_checked_at",
        "physical_store_checked_by",
        "physical_store_verification_note",
        "google_place_verification_status",
        "google_place_id",
        "google_maps_url",
        "google_place_checked_at",
        "google_place_checked_by",
        "google_place_evidence_json",
    }
)
CANONICAL_STATUSES = frozenset(
    {"pending", "official_site_verified", "conflict", "rejected"}
)
PHYSICAL_STORE_STATUSES = frozenset(
    {"pending", "verified_physical_store", "not_physical_store", "conflict"}
)


class LocationVerificationSchemaUnavailable(RuntimeError):
    pass


def _row_get(row: Any, key: str, default: Any = None) -> Any:
    if row is None:
        return default
    if hasattr(row, "get"):
        return row.get(key, default)
    try:
        return row[key]
    except Exception:
        return default


def _staff_ref(actor_id: Any) -> str:
    try:
        normalized = int(actor_id)
    except (TypeError, ValueError) as exc:
        raise ValueError("staff identity is required") from exc
    if normalized <= 0:
        raise ValueError("staff identity is required")
    return f"staff_{normalized}"


def _absolute_http_url(value: Any) -> str | None:
    text = str(value or "").strip()
    if not text:
        return None
    parsed = urlsplit(text)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        return None
    return text


def _is_manufacturer_directory_url(value: Any) -> bool:
    """Reject vendor-owned directory pages as canonical store websites."""
    text = _absolute_http_url(value)
    if not text:
        return False
    hostname = str(urlsplit(text).hostname or "").casefold().rstrip(".")
    manufacturer_hosts = (
        "nikonusa.com",
        "nikon.com",
        "canon.com",
        "sony.com",
        "godox.com",
    )
    return any(
        hostname == suffix or hostname.endswith("." + suffix)
        for suffix in manufacturer_hosts
    )


def _table_columns() -> set[str]:
    if not table_exists("vkpi_dealers"):
        return set()
    conn = get_conn()
    if is_postgres_runtime():
        rows = conn.execute(
            """
            SELECT column_name FROM information_schema.columns
            WHERE table_schema=current_schema() AND table_name=?
            """,
            ("vkpi_dealers",),
        ).fetchall()
        return {
            str(_row_get(row, "column_name") or "").strip()
            for row in rows
            if str(_row_get(row, "column_name") or "").strip()
        }
    rows = conn.execute("PRAGMA table_info(vkpi_dealers)").fetchall()
    return {
        str(_row_get(row, "name", _row_get(row, 1, "")) or "").strip()
        for row in rows
        if str(_row_get(row, "name", _row_get(row, 1, "")) or "").strip()
    }


def google_places_capability(environ: Mapping[str, str] | None = None) -> dict[str, Any]:
    """Describe capability without reading, logging or using a secret value."""
    source = environ if environ is not None else os.environ
    configured = any(
        bool(str(source.get(name) or "").strip())
        for name in ("GOOGLE_PLACES_API_KEY", "GOOGLE_MAPS_API_KEY")
    )
    return {
        "configured": configured,
        "automatic_lookup_enabled": False,
        "network_request_performed": False,
        "status_when_unavailable": "pending",
        "canonical_source": False,
        "purpose": "cross_check_only",
    }


def contract_status() -> dict[str, Any]:
    present = _table_columns()
    missing = sorted(REQUIRED_FIELDS.difference(present))
    return {
        "supported": not missing,
        "status": "ready" if not missing else "migration_required",
        "contract_version": CONTRACT_VERSION,
        "missing_fields": missing,
        "google_places": google_places_capability(),
        "truth_boundaries": {
            "canonical_address_source": "retailer_or_store_owned_website",
            "google_place_is_canonical": False,
            "product_evidence_proves_physical_store": False,
            "manufacturer_directory_name_is_map_eligible": False,
            "automatic_promotion": False,
        },
    }


def _require_schema() -> None:
    status = contract_status()
    if not status["supported"]:
        raise LocationVerificationSchemaUnavailable(
            "dealer location verification migration is required"
        )


def _evidence_summary(value: Any) -> dict[str, Any]:
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except (TypeError, ValueError, json.JSONDecodeError):
            value = {}
    if not isinstance(value, dict):
        value = {}
    return {
        "provider": str(value.get("provider") or "").strip() or None,
        "value_status": str(value.get("value_status") or "").strip() or None,
    }


def _project(row: Any) -> dict[str, Any]:
    version = int(_row_get(row, "location_verification_contract_version", 0) or 0)
    canonical_status = str(_row_get(row, "canonical_location_status") or "pending")
    physical_status = str(_row_get(row, "physical_store_status") or "pending")
    publication_status = str(_row_get(row, "publication_status") or "draft")
    return {
        "dealer_id": int(_row_get(row, "id") or 0),
        "name": _row_get(row, "name"),
        "canonical_location": {
            "address": _row_get(row, "address"),
            "city": _row_get(row, "city"),
            "state": _row_get(row, "state"),
            "official_source_url": _row_get(row, "location_source_url"),
            "status": canonical_status,
            "checked_at": _row_get(row, "canonical_location_checked_at"),
            "checked_by": _row_get(row, "canonical_location_checked_by") or None,
        },
        "physical_store": {
            "status": physical_status,
            "checked_at": _row_get(row, "physical_store_checked_at"),
            "checked_by": _row_get(row, "physical_store_checked_by") or None,
            "note": _row_get(row, "physical_store_verification_note") or "",
        },
        "google_place_cross_check": {
            "status": _row_get(row, "google_place_verification_status") or "pending",
            "place_id": _row_get(row, "google_place_id"),
            "maps_url": _row_get(row, "google_maps_url"),
            "checked_at": _row_get(row, "google_place_checked_at"),
            "checked_by": _row_get(row, "google_place_checked_by") or None,
            "evidence": _evidence_summary(_row_get(row, "google_place_evidence_json")),
            **google_places_capability(),
        },
        "product_evidence": {
            "viltrox_public_listing_url": _row_get(row, "brand_listing_url"),
            "proves_physical_store": False,
            "proves_current_inventory": False,
        },
        "contract_version": version,
        "publication_status": publication_status,
        "map_eligible": bool(
            version == CONTRACT_VERSION
            and canonical_status == "official_site_verified"
            and physical_status == "verified_physical_store"
            and _row_get(row, "canonical_location_checked_at") is not None
            and bool(str(_row_get(row, "canonical_location_checked_by") or "").strip())
            and _row_get(row, "physical_store_checked_at") is not None
            and bool(str(_row_get(row, "physical_store_checked_by") or "").strip())
            and bool(
                str(_row_get(row, "physical_store_verification_note") or "").strip()
            )
            and publication_status == "published"
            and _row_get(row, "lat") is not None
            and _row_get(row, "lng") is not None
        ),
    }


_SELECT = """
SELECT id, name, address, city, state, lat, lng, location_source_url,
       brand_listing_url, publication_status,
       location_verification_contract_version,
       canonical_location_status, canonical_location_checked_at,
       canonical_location_checked_by, physical_store_status,
       physical_store_checked_at, physical_store_checked_by,
       physical_store_verification_note, google_place_verification_status,
       google_place_id, google_maps_url, google_place_checked_at,
       google_place_checked_by, google_place_evidence_json
FROM vkpi_dealers WHERE id = ? LIMIT 1
"""


def get_location_verification(dealer_id: Any) -> dict[str, Any]:
    _require_schema()
    try:
        normalized_id = int(dealer_id)
    except (TypeError, ValueError) as exc:
        raise ValueError("dealer id must be a positive integer") from exc
    if normalized_id <= 0:
        raise ValueError("dealer id must be a positive integer")
    row = get_conn().execute(_SELECT, (normalized_id,)).fetchone()
    if row is None:
        raise LookupError("dealer not found")
    return _project(row)


def review_official_location(
    dealer_id: Any,
    body: dict[str, Any],
    *,
    actor_id: Any,
) -> dict[str, Any]:
    """Persist one human review; Google fields remain untouched and pending."""
    _require_schema()
    try:
        normalized_id = int(dealer_id)
    except (TypeError, ValueError) as exc:
        raise ValueError("dealer id must be a positive integer") from exc
    if normalized_id <= 0:
        raise ValueError("dealer id must be a positive integer")
    actor_ref = _staff_ref(actor_id)
    canonical_status = str(body.get("canonical_location_status") or "").strip()
    physical_status = str(body.get("physical_store_status") or "").strip()
    if canonical_status not in CANONICAL_STATUSES:
        raise ValueError("unsupported canonical_location_status")
    if physical_status not in PHYSICAL_STORE_STATUSES:
        raise ValueError("unsupported physical_store_status")
    if canonical_status == "pending" or physical_status == "pending":
        raise ValueError("a completed official review cannot remain pending")
    if (
        physical_status == "verified_physical_store"
        and canonical_status != "official_site_verified"
    ):
        raise ValueError("physical store verification requires official-site identity")
    note = str(body.get("note") or "").strip()
    if not note:
        raise ValueError("official location review requires a verification note")
    if len(note) > 2000:
        raise ValueError("note must be at most 2000 characters")

    conn = get_conn()
    current = conn.execute(
        """
        SELECT id, address, city, state, location_source_url
        FROM vkpi_dealers WHERE id = ? LIMIT 1
        """,
        (normalized_id,),
    ).fetchone()
    if current is None:
        raise LookupError("dealer not found")
    if canonical_status == "official_site_verified":
        required = {
            "address": _row_get(current, "address"),
            "city": _row_get(current, "city"),
            "state": _row_get(current, "state"),
        }
        missing = [key for key, value in required.items() if not str(value or "").strip()]
        if missing:
            raise ValueError("official location review requires: " + ", ".join(missing))
        source_url = _row_get(current, "location_source_url")
        if not _absolute_http_url(source_url):
            raise ValueError("official location review requires location_source_url")
        if _is_manufacturer_directory_url(source_url):
            raise ValueError(
                "manufacturer directory cannot be the canonical store website"
            )

    remains_published = physical_status == "verified_physical_store"
    conn.execute(
        """
        UPDATE vkpi_dealers
        SET location_verification_contract_version = 1,
            canonical_location_status = ?,
            canonical_location_checked_at = NOW(),
            canonical_location_checked_by = ?,
            physical_store_status = ?,
            physical_store_checked_at = NOW(),
            physical_store_checked_by = ?,
            physical_store_verification_note = ?,
            publication_status = CASE WHEN ? THEN publication_status ELSE 'draft' END,
            published_at = CASE WHEN ? THEN published_at ELSE NULL END,
            published_by = CASE WHEN ? THEN published_by ELSE '' END,
            updated_at = NOW()
        WHERE id = ?
        """,
        (
            canonical_status,
            actor_ref,
            physical_status,
            actor_ref,
            note,
            remains_published,
            remains_published,
            remains_published,
            normalized_id,
        ),
    )
    conn.commit()
    row = conn.execute(_SELECT, (normalized_id,)).fetchone()
    if row is None:
        raise LookupError("dealer not found")
    return _project(row)
