"""Operator-reviewed physical Dealer manifest application.

The bundled manifest contains five retailer-owned store-page observations and
address-level coordinates returned by the US Census geocoder.  It is not a
crawler, a Google Places adapter, or a manufacturer-authorization importer.

Execution is deliberately fail closed:

* loading and planning never writes;
* a positive staff actor and an explicit ``publish=True`` are both required;
* the active schema must include migrations 259, 260 and 270;
* every database row must match the manifest's exact name/address identity;
* Google fields remain pending/empty;
* manufacturer relationships stay descriptive with authorization unverified.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Mapping
from urllib.parse import urlsplit

from app.db.connection import get_conn, is_postgres_runtime, table_exists


MANIFEST_SCHEMA = "vkpi.reviewed-physical-store-manifest/v1"
DEFAULT_MANIFEST_PATH = (
    Path(__file__).resolve().parent / "reviewed_physical_stores_us_v1.json"
)
MAX_ROWS = 20
REQUIRED_COLUMNS = frozenset(
    {
        "source_id",
        "stable_org_key",
        "stable_location_key",
        "reviewer_id",
        "reviewed_at",
        "evidence_json",
        "review_contract_version",
        "publication_status",
        "published_at",
        "published_by",
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


class ReviewedPhysicalStoreManifestError(RuntimeError):
    """Stable operator-facing error for a rejected manifest/application."""


def _text(value: Any) -> str:
    return str(value or "").strip()


def _staff_ref(actor_id: Any) -> str:
    try:
        numeric = int(actor_id)
    except (TypeError, ValueError) as exc:
        raise ReviewedPhysicalStoreManifestError("--actor-id must be a positive integer") from exc
    if numeric <= 0:
        raise ReviewedPhysicalStoreManifestError("--actor-id must be a positive integer")
    return f"staff_{numeric}"


def _http_url(value: Any, *, field: str) -> str:
    text = _text(value)
    parsed = urlsplit(text)
    if parsed.scheme != "https" or not parsed.netloc:
        raise ReviewedPhysicalStoreManifestError(f"{field} must be an absolute HTTPS URL")
    return text


def _coordinate(value: Any, *, field: str, minimum: float, maximum: float) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise ReviewedPhysicalStoreManifestError(f"{field} must be numeric") from exc
    if not minimum <= number <= maximum:
        raise ReviewedPhysicalStoreManifestError(f"{field} is outside its valid range")
    return number


def _stable_key(prefix: str, value: str) -> str:
    digest = hashlib.sha256(value.casefold().encode("utf-8")).hexdigest()[:24]
    return f"{prefix}_{digest}"


def _load_json(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ReviewedPhysicalStoreManifestError("reviewed Dealer manifest is unreadable") from exc
    if not isinstance(payload, dict):
        raise ReviewedPhysicalStoreManifestError("reviewed Dealer manifest must be an object")
    return payload


def load_manifest(path: Path | None = None) -> dict[str, Any]:
    """Load and fully validate the bounded, network-free reviewed manifest."""
    payload = _load_json(path or DEFAULT_MANIFEST_PATH)
    if payload.get("schema") != MANIFEST_SCHEMA:
        raise ReviewedPhysicalStoreManifestError("reviewed Dealer manifest schema is unsupported")
    if payload.get("claim_status") != "descriptive_only":
        raise ReviewedPhysicalStoreManifestError("manifest must remain descriptive_only")
    if payload.get("google_places_status") != "pending":
        raise ReviewedPhysicalStoreManifestError("manifest cannot pre-approve Google Places")
    rows = payload.get("stores")
    if not isinstance(rows, list) or not 1 <= len(rows) <= MAX_ROWS:
        raise ReviewedPhysicalStoreManifestError("manifest must contain 1-20 stores")

    identities: set[tuple[str, str]] = set()
    for index, row in enumerate(rows):
        prefix = f"stores[{index}]"
        if not isinstance(row, dict):
            raise ReviewedPhysicalStoreManifestError(f"{prefix} must be an object")
        name = _text(row.get("name"))
        address = _text(row.get("address"))
        city = _text(row.get("city"))
        state = _text(row.get("state")).upper()
        if not all((name, address, city)) or len(state) != 2:
            raise ReviewedPhysicalStoreManifestError(f"{prefix} has an incomplete exact identity")
        identity = (name.casefold(), address.casefold())
        if identity in identities:
            raise ReviewedPhysicalStoreManifestError(f"{prefix} duplicates a store identity")
        identities.add(identity)
        source_url = _http_url(row.get("location_source_url"), field=f"{prefix}.location_source_url")
        source_host = str(urlsplit(source_url).hostname or "").casefold()
        expected_host = _text(row.get("retailer_host")).casefold()
        if source_host != expected_host and not source_host.endswith("." + expected_host):
            raise ReviewedPhysicalStoreManifestError(f"{prefix} source is not retailer-owned")
        website_url = _http_url(row.get("website_url"), field=f"{prefix}.website_url")
        website_host = str(urlsplit(website_url).hostname or "").casefold()
        if website_host != expected_host and not website_host.endswith("." + expected_host):
            raise ReviewedPhysicalStoreManifestError(f"{prefix} website is not retailer-owned")
        phone = _text(row.get("phone"))
        if len(phone) < 7:
            raise ReviewedPhysicalStoreManifestError(f"{prefix}.phone is incomplete")
        _http_url(row.get("brand_listing_url"), field=f"{prefix}.brand_listing_url")
        coordinates = row.get("coordinates")
        if not isinstance(coordinates, dict):
            raise ReviewedPhysicalStoreManifestError(f"{prefix}.coordinates must be an object")
        if coordinates.get("provider") != "us_census_geocoder":
            raise ReviewedPhysicalStoreManifestError(f"{prefix} coordinate provider must be US Census")
        if coordinates.get("value_status") != "observed":
            raise ReviewedPhysicalStoreManifestError(f"{prefix} coordinates must be observed")
        _coordinate(coordinates.get("lat"), field=f"{prefix}.lat", minimum=-90, maximum=90)
        _coordinate(coordinates.get("lng"), field=f"{prefix}.lng", minimum=-180, maximum=180)
        observed_at = _text(row.get("observed_at"))
        if not observed_at.endswith("Z"):
            raise ReviewedPhysicalStoreManifestError(f"{prefix}.observed_at must be UTC")
        if any(key in row for key in ("google_place_id", "google_maps_url")):
            raise ReviewedPhysicalStoreManifestError(f"{prefix} must not carry Google evidence")
        for brand_index, brand in enumerate(row.get("brand_relationships") or []):
            if not isinstance(brand, dict):
                raise ReviewedPhysicalStoreManifestError(
                    f"{prefix}.brand_relationships[{brand_index}] must be an object"
                )
            if brand.get("relationship_status") != "official_directory_listed":
                raise ReviewedPhysicalStoreManifestError("brand relationship must stay directory-listed")
            if brand.get("authorization_status") != "unverified":
                raise ReviewedPhysicalStoreManifestError("brand authorization must stay unverified")
            _http_url(brand.get("evidence_url"), field="brand evidence_url")
    return payload


def _table_columns(conn: Any) -> set[str]:
    rows = conn.execute(
        """
        SELECT column_name FROM information_schema.columns
        WHERE table_schema=current_schema() AND table_name=?
        """,
        ("vkpi_dealers",),
    ).fetchall()
    return {_text(row["column_name"] if hasattr(row, "keys") else row[0]) for row in rows}


def _evidence(row: Mapping[str, Any], *, actor_ref: str) -> dict[str, Any]:
    coordinates = dict(row["coordinates"])
    return {
        "claim_status": "descriptive_only",
        "source": {
            "source_id": _text(row["source_id"]),
            "source_url": _text(row["location_source_url"]),
            "observed_at": _text(row["observed_at"]),
            "reviewer_id": actor_ref,
            "evidence_scope": "retailer_owned_exact_physical_store_page",
            "value_status": "observed",
        },
        "contact": {
            "phone": _text(row["phone"]),
            "website_url": _text(row["website_url"]),
            "value_status": "observed",
        },
        "product": {
            "source_url": _text(row["brand_listing_url"]),
            "observed_at": _text(row["observed_at"]),
            "value_status": "observed",
            "scope": "public_product_page_only",
            "proves_current_inventory": False,
            "proves_authorization": False,
        },
        "coordinate": {
            **coordinates,
            "canonical_source": False,
            "google_derived": False,
        },
    }

def build_plan(
    manifest: Mapping[str, Any],
    *,
    actor_id: Any,
    connection: Any | None = None,
) -> dict[str, Any]:
    """Resolve exact rows and return a zero-write plan."""
    actor_ref = _staff_ref(actor_id)
    if not is_postgres_runtime():
        raise ReviewedPhysicalStoreManifestError("reviewed Dealer manifest requires PostgreSQL")
    if not table_exists("vkpi_dealers") or not table_exists("vkpi_dealer_brand_relationships"):
        raise ReviewedPhysicalStoreManifestError("Dealer map tables are unavailable")
    conn = connection or get_conn()
    missing = sorted(REQUIRED_COLUMNS.difference(_table_columns(conn)))
    if missing:
        raise ReviewedPhysicalStoreManifestError(
            "Dealer location verification migration is required: " + ", ".join(missing)
        )

    plan_rows: list[dict[str, Any]] = []
    for row in manifest["stores"]:
        matches = conn.execute(
            """
            SELECT id,name,address,city,state,postal_code,location_source_url,
                   brand_listing_url,lat,lng,publication_status,
                   location_verification_contract_version,
                   canonical_location_status,physical_store_status,
                   google_place_verification_status
            FROM vkpi_dealers
            WHERE LOWER(name)=LOWER(?) AND LOWER(address)=LOWER(?)
            ORDER BY id
            """,
            (_text(row["name"]), _text(row["address"])),
        ).fetchall()
        if len(matches) != 1:
            raise ReviewedPhysicalStoreManifestError(
                f"exact Dealer identity must resolve once: {_text(row['name'])}"
            )
        current = matches[0]
        for field in ("name", "address", "city", "state", "location_source_url", "brand_listing_url"):
            expected = _text(row[field]).casefold()
            actual = _text(current[field] if hasattr(current, "keys") else current[field]).casefold()
            if actual != expected:
                raise ReviewedPhysicalStoreManifestError(
                    f"Dealer identity/source drift for {_text(row['name'])}: {field}"
                )
        plan_rows.append(
            {
                "dealer_id": int(current["id"]),
                "name": _text(row["name"]),
                "phone": _text(row["phone"]),
                "website_url": _text(row["website_url"]),
                "coordinate_provider": "us_census_geocoder",
                "google_place_status": "pending",
                "publish_requested": True,
                "brand_relationships": [
                    {
                        "brand_key": _text(brand["brand_key"]),
                        "relationship_status": "official_directory_listed",
                        "authorization_status": "unverified",
                        "evidence_url": _text(brand["evidence_url"]),
                    }
                    for brand in row.get("brand_relationships") or []
                ],
            }
        )
    return {
        "schema": MANIFEST_SCHEMA,
        "mode": "dry_run",
        "actor": actor_ref,
        "store_count": len(plan_rows),
        "stores": plan_rows,
        "network_requests": 0,
        "database_writes": 0,
        "google_places_status": "pending",
        "truth_boundaries": manifest["truth_boundaries"],
    }


def apply_manifest(
    manifest: Mapping[str, Any],
    *,
    actor_id: Any,
    publish: bool = False,
    connection: Any | None = None,
) -> dict[str, Any]:
    """Apply all five rows atomically; default remains a zero-write dry run."""
    plan = build_plan(manifest, actor_id=actor_id, connection=connection)
    if not publish:
        return plan
    actor_ref = _staff_ref(actor_id)
    conn = connection or get_conn()
    try:
        for row, planned in zip(manifest["stores"], plan["stores"], strict=True):
            coordinates = row["coordinates"]
            observed_at = _text(row["observed_at"])
            evidence = _evidence(row, actor_ref=actor_ref)
            org_key = _stable_key("dealer_org", _text(row["organization_key_material"]))
            location_key = _stable_key(
                "dealer_loc",
                "|".join((_text(row["name"]), _text(row["address"]), _text(row["city"]), _text(row["state"]))),
            )
            note = (
                "Retailer-owned store page reviewed for exact address and physical-store use; "
                "coordinates are US Census geocoder address-level output; Google Places remains pending."
            )
            conn.execute(
                """
                UPDATE vkpi_dealers
                SET lat=?, lng=?, phone=?, website_url=?, source_status='public_listing_verified',
                    source_checked_at=?, source_id=?, stable_org_key=?,
                    stable_location_key=?, reviewer_id=?, reviewed_at=?,
                    evidence_json=?::jsonb, review_contract_version=1,
                    location_verification_contract_version=1,
                    canonical_location_status='official_site_verified',
                    canonical_location_checked_at=?, canonical_location_checked_by=?,
                    physical_store_status='verified_physical_store',
                    physical_store_checked_at=?, physical_store_checked_by=?,
                    physical_store_verification_note=?,
                    google_place_verification_status='pending',
                    google_place_id=NULL, google_maps_url=NULL,
                    google_place_checked_at=NULL, google_place_checked_by='',
                    google_place_evidence_json='{}'::jsonb,
                    publication_status='published', published_at=?, published_by=?,
                    updated_at=NOW()
                WHERE id=?
                """,
                (
                    float(coordinates["lat"]),
                    float(coordinates["lng"]),
                    _text(row["phone"]),
                    _text(row["website_url"]),
                    observed_at,
                    _text(row["source_id"]),
                    org_key,
                    location_key,
                    actor_ref,
                    observed_at,
                    json.dumps(evidence, ensure_ascii=False, separators=(",", ":"), sort_keys=True),
                    observed_at,
                    actor_ref,
                    observed_at,
                    actor_ref,
                    note,
                    observed_at,
                    actor_ref,
                    int(planned["dealer_id"]),
                ),
            )
            for brand in row.get("brand_relationships") or []:
                conn.execute(
                    """
                    INSERT INTO vkpi_dealer_brand_relationships(
                      dealer_id,brand_key,relationship_status,authorization_status,
                      evidence_url,source_checked_at,reviewer_id,updated_at
                    ) VALUES (?,?, 'official_directory_listed','unverified',?,?,?,NOW())
                    ON CONFLICT (dealer_id,brand_key) DO UPDATE SET
                      relationship_status='official_directory_listed',
                      authorization_status='unverified', evidence_url=EXCLUDED.evidence_url,
                      source_checked_at=EXCLUDED.source_checked_at,
                      reviewer_id=EXCLUDED.reviewer_id, updated_at=NOW()
                    """,
                    (
                        int(planned["dealer_id"]),
                        _text(brand["brand_key"]),
                        _text(brand["evidence_url"]),
                        observed_at,
                        actor_ref,
                    ),
                )
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    return {
        **plan,
        "mode": "published",
        "database_writes": len(plan["stores"]),
        "published_count": len(plan["stores"]),
    }
