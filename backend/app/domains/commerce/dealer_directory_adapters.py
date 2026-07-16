"""Offline, fail-closed adapters for US Dealer discovery snapshots.

The adapters normalize publisher-owned directory snapshots into *candidate
staging envelopes*.  They do not fetch a URL, write a database row, schedule a
job, promote a candidate, or claim that a store is a Viltrox dealer.

There are two deliberately separate controls:

* a format adapter says how an already captured snapshot can be interpreted;
* the source registry gate says whether that adapter may run for a source.

The checked-in registry currently projects every source as disabled with
``terms_robots_status=pending_review``.  Consequently the public entry point
returns zero candidates until a reviewed registry implementation explicitly
marks one source ``enabled`` and ``reviewed_allowed``.
"""
from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timezone
from html.parser import HTMLParser
from typing import Any, Mapping
from urllib.parse import urlsplit

from app.domains.events import us_coverage_registry
from app.domains.source_passport_core import canonical_json_sha256
from app.domains.source_passport_urls import source_url_identity


CONTRACT_ID = "vkpi.us_dealer.directory_candidate_adapter"
CONTRACT_VERSION = 2
CLAIM_STATUS = "descriptive_only"
TERMS_ROBOTS_ALLOWED = "reviewed_allowed"

ADAPTER_BY_SOURCE = {
    "dealer_bestbuy_us_store_directory": "sitemap_html",
    "dealer_blackmagic_us_resellers": "published_html_rows",
    "dealer_canon_us_where_to_buy": "tabular_pdf",
    "dealer_fujifilm_us_shop": "published_html_rows",
    "dealer_godox_us_authorized_distributors": "published_html_rows",
    "dealer_hasselblad_us_locator": "published_html_rows",
    "dealer_leica_us_locator": "published_html_rows",
    "dealer_omsystem_us_locator": "json_locator",
    "dealer_panasonic_us_authorized": "published_html_rows",
    "dealer_phaseone_us_partner_locator": "published_html_rows",
    "dealer_profoto_us_locator": "json_locator",
    "dealer_nikon_us_authorized_imaging": "tabular_pdf",
    "dealer_sigma_us_authorized": "published_html_rows",
    "dealer_sony_us_where_to_buy": "published_html_rows",
    "dealer_tamron_americas_locator": "published_html_rows",
    "dealer_microcenter_us_store_directory": "published_html_rows",
    "dealer_bh_nyc_superstore_us": "published_html_rows",
    "dealer_adorama_nyc_store_us": "published_html_rows",
    "dealer_samys_retail_locations_us": "published_html_rows",
    "dealer_glazers_store_us": "published_html_rows",
    "dealer_unique_store_locations_us": "published_html_rows",
    "dealer_hunts_store_locations_us": "published_html_rows",
    "dealer_precision_store_locations_us": "published_html_rows",
    "dealer_roberts_store_us": "published_html_rows",
    "dealer_dans_store_us": "published_html_rows",
    "dealer_natcam_store_us": "published_html_rows",
    "dealer_mikes_camera_locations_us": "published_html_rows",
    "dealer_pauls_photo_location_us": "published_html_rows",
    "dealer_bedfords_store_locations_us": "published_html_rows",
    "dealer_dodd_store_locator_us": "published_html_rows",
    "dealer_pro_photo_supply_location_us": "published_html_rows",
    "dealer_bc_camera_location_us": "published_html_rows",
    "dealer_rockbrook_locations_us": "published_html_rows",
    "dealer_competitive_camera_location_us": "published_html_rows",
    "dealer_district_camera_locations_us": "published_html_rows",
}

# The manufacturer entries above deliberately share the most conservative
# page contract.  This only records that an exact, publisher-bound HTML
# document can be represented as already-extracted rows.  It does not assert a
# source-specific DOM/API shape, a successful capture, permission to fetch, or
# any Viltrox business fact.

_SOURCE_STORE_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$")
_STATE_RE = re.compile(r"^[A-Z]{2}$")
_POSTAL_RE = re.compile(r"^[0-9]{5}(?:-[0-9]{4})?$")
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_PHONE_ALLOWED_RE = re.compile(r"^[0-9+().\- xX]{7,32}$")
_REVIEWER_RE = re.compile(r"^staff_[1-9][0-9]{0,18}$")
_SPACE_RE = re.compile(r"\s+")
_US_STATE_CODES = frozenset(
    "AL AK AZ AR CA CO CT DE FL GA HI ID IL IN IA KS KY LA ME MD MA MI MN "
    "MS MO MT NE NV NH NJ NM NY NC ND OH OK OR PA RI SC SD TN TX UT VT VA "
    "WA WV WI WY DC".split()
)
_US_STATE_NAMES = {
    "alabama": "AL", "alaska": "AK", "arizona": "AZ", "arkansas": "AR",
    "california": "CA", "colorado": "CO", "connecticut": "CT", "delaware": "DE",
    "florida": "FL", "georgia": "GA", "hawaii": "HI", "idaho": "ID",
    "illinois": "IL", "indiana": "IN", "iowa": "IA", "kansas": "KS",
    "kentucky": "KY", "louisiana": "LA", "maine": "ME", "maryland": "MD",
    "massachusetts": "MA", "michigan": "MI", "minnesota": "MN",
    "mississippi": "MS", "missouri": "MO", "montana": "MT", "nebraska": "NE",
    "nevada": "NV", "new hampshire": "NH", "new jersey": "NJ",
    "new mexico": "NM", "new york": "NY", "north carolina": "NC",
    "north dakota": "ND", "ohio": "OH", "oklahoma": "OK", "oregon": "OR",
    "pennsylvania": "PA", "rhode island": "RI", "south carolina": "SC",
    "south dakota": "SD", "tennessee": "TN", "texas": "TX", "utah": "UT",
    "vermont": "VT", "virginia": "VA", "washington": "WA",
    "west virginia": "WV", "wisconsin": "WI", "wyoming": "WY",
    "district of columbia": "DC", "washington dc": "DC", "d c": "DC",
}


class _JsonLdCollector(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._inside_json_ld = False
        self._chunks: list[str] = []
        self.documents: list[Any] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag.casefold() != "script":
            return
        attributes = {str(key).casefold(): str(value or "") for key, value in attrs}
        if attributes.get("type", "").casefold().split(";", 1)[0].strip() == "application/ld+json":
            self._inside_json_ld = True
            self._chunks = []

    def handle_data(self, data: str) -> None:
        if self._inside_json_ld:
            self._chunks.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag.casefold() != "script" or not self._inside_json_ld:
            return
        self._inside_json_ld = False
        text = "".join(self._chunks).strip()
        self._chunks = []
        if not text:
            return
        try:
            self.documents.append(json.loads(text))
        except (TypeError, ValueError, json.JSONDecodeError):
            # One broken script must not make unrelated page text executable or
            # trigger heuristic extraction.  The row simply stays unresolved.
            return


def _text(value: Any, *, limit: int = 500) -> str | None:
    text = " ".join(str(value or "").split())
    return text[:limit] or None


def _float(value: Any, *, low: float, high: float) -> float | None:
    if value in (None, ""):
        return None
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    return parsed if low <= parsed <= high else None


def _timestamp(value: Any) -> str | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    if parsed.tzinfo is None:
        return None
    return parsed.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _first(mapping: Mapping[str, Any], *keys: str) -> Any:
    for key in keys:
        if key in mapping and mapping.get(key) not in (None, ""):
            return mapping.get(key)
    return None


def _normalize_us_state(value: Any) -> str:
    text = _SPACE_RE.sub(" ", str(value or "").strip())
    if not text:
        return ""
    upper = text.upper()
    if upper in _US_STATE_CODES:
        return upper
    # Dotted abbreviations such as N.Y. and D.C. are common in PDF rows.
    letters_only = re.sub(r"[^A-Za-z]", "", text).upper()
    if len(letters_only) == 2 and letters_only in _US_STATE_CODES:
        return letters_only
    name = _SPACE_RE.sub(" ", re.sub(r"[^A-Za-z]+", " ", text)).strip().casefold()
    return _US_STATE_NAMES.get(name, "")


def _normalize_us_postal(value: Any) -> str:
    text = str(value or "").strip()
    if re.fullmatch(r"[0-9]{5}", text):
        return text
    if re.fullmatch(r"[0-9]{9}", text):
        return f"{text[:5]}-{text[5:]}"
    matched = re.fullmatch(r"([0-9]{5})[- ]([0-9]{4})", text)
    if matched:
        return f"{matched.group(1)}-{matched.group(2)}"
    return ""


def _flatten_json_ld(value: Any) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    if isinstance(value, list):
        for item in value:
            rows.extend(_flatten_json_ld(item))
    elif isinstance(value, Mapping):
        graph = value.get("@graph")
        if isinstance(graph, (list, Mapping)):
            rows.extend(_flatten_json_ld(graph))
        kind = value.get("@type")
        kinds = {str(item).casefold() for item in (kind if isinstance(kind, list) else [kind])}
        if kinds & {"store", "localbusiness", "electronicsstore", "organization"}:
            rows.append(dict(value))
    return rows


def _source_gate(source: Mapping[str, Any] | None, source_registry_id: str) -> list[str]:
    if not isinstance(source, Mapping):
        return ["source_registry_id_not_registered"]
    reasons: list[str] = []
    if str(source.get("id") or "") != source_registry_id:
        reasons.append("source_registry_identity_mismatch")
    if str(source.get("scope") or "") != "dealer_discovery_sources":
        reasons.append("source_registry_scope_not_dealer_discovery")
    if not source_url_identity(source.get("canonical_url"))["valid"]:
        reasons.append("source_registry_canonical_url_invalid")
    if source.get("enabled") is not True:
        reasons.append("source_registry_disabled")
    if str(source.get("terms_robots_status") or "").strip().casefold() != TERMS_ROBOTS_ALLOWED:
        reasons.append("terms_robots_not_reviewed_allowed")
    if not _REVIEWER_RE.fullmatch(
        str(source.get("terms_robots_reviewer_id") or "").strip()
    ):
        reasons.append("terms_robots_reviewer_missing")
    if _timestamp(source.get("terms_robots_reviewed_at")) is None:
        reasons.append("terms_robots_review_timestamp_invalid")
    if str(source.get("status") or "").strip().casefold() != "active":
        reasons.append("source_registry_not_active")
    if source.get("requires_human_review") is not True:
        reasons.append("candidate_human_review_not_required")
    if source.get("direct_import_allowed") is not False:
        reasons.append("direct_business_import_must_remain_disabled")
    return reasons


def _publisher_observation_url(
    source: Mapping[str, Any],
    value: Any,
    *,
    exact_document: bool = False,
) -> str | None:
    """Return an observation URL only when it remains publisher-bound.

    The registry canonical URL is always allowed.  HTML store pages may use the
    same registered host, or a host explicitly listed in
    ``publisher_observation_hosts``.  A PDF is stricter: the document URL must
    be the registry canonical URL or one of ``publisher_observation_urls``.
    Retailer-owned websites from locator records are never consulted here.
    """
    canonical_identity = source_url_identity(source.get("canonical_url"))
    proposed_identity = source_url_identity(value)
    if not canonical_identity["valid"] or not proposed_identity["valid"]:
        return None
    canonical_url = str(canonical_identity["canonical_url"])
    proposed_url = str(proposed_identity["canonical_url"])
    explicit_urls = {
        str(item["canonical_url"])
        for raw in source.get("publisher_observation_urls", [])
        if (item := source_url_identity(raw))["valid"]
    }
    if exact_document:
        return proposed_url if proposed_url in {canonical_url, *explicit_urls} else None
    canonical_host = str(urlsplit(canonical_url).hostname or "").casefold()
    allowed_hosts = {canonical_host}
    raw_hosts = source.get("publisher_observation_hosts", [])
    if isinstance(raw_hosts, list):
        for raw in raw_hosts:
            host = str(raw or "").strip().rstrip(".").casefold()
            if host and "/" not in host and "@" not in host and ":" not in host:
                allowed_hosts.add(host)
    proposed_host = str(urlsplit(proposed_url).hostname or "").casefold()
    return proposed_url if proposed_host in allowed_hosts else None


def _registered_source(source_registry_id: str) -> Mapping[str, Any] | None:
    report = us_coverage_registry.dealer_registry()
    for row in report.get("dealer_discovery_sources", []):
        if isinstance(row, Mapping) and str(row.get("id") or "") == source_registry_id:
            return row
    return None


def _address(record: Mapping[str, Any]) -> dict[str, Any] | None:
    raw = record.get("address")
    nested = raw if isinstance(raw, Mapping) else {}
    line1 = _text(
        _first(nested, "streetAddress", "address1", "line1", "street", "address")
        or _first(record, "streetAddress", "address1", "line1", "street")
    )
    line2 = _text(
        _first(nested, "address2", "line2", "suite")
        or _first(record, "address2", "line2", "suite")
    )
    city = _text(
        _first(nested, "addressLocality", "city", "locality")
        or _first(record, "addressLocality", "city", "locality")
    )
    state = _text(
        _first(nested, "addressRegion", "state", "region")
        or _first(record, "addressRegion", "state", "region")
    )
    postal_code = _text(
        _first(nested, "postalCode", "postal_code", "zip", "zipcode")
        or _first(record, "postalCode", "postal_code", "zip", "zipcode")
    )
    country = _first(nested, "addressCountry", "country", "country_code") or _first(
        record, "addressCountry", "country", "country_code"
    )
    if isinstance(country, Mapping):
        country = _first(country, "name", "@id")
    country_code = str(country or "US").strip().upper()
    state = _normalize_us_state(state)
    postal_code = _normalize_us_postal(postal_code)
    if (
        not line1
        or not city
        or not _STATE_RE.fullmatch(state)
        or not _POSTAL_RE.fullmatch(postal_code)
        or country_code not in {"US", "USA", "UNITED STATES", "UNITED STATES OF AMERICA"}
    ):
        return None
    country_code = "US"
    formatted = ", ".join(
        part for part in (line1, line2, f"{city}, {state} {postal_code}", country_code) if part
    )
    return {
        "line1": line1,
        "line2": line2,
        "city": city,
        "state": state,
        "postal_code": postal_code,
        "country_code": country_code,
        "formatted": formatted,
    }


def _coordinates(record: Mapping[str, Any]) -> tuple[float | None, float | None]:
    geo = record.get("geo")
    nested = geo if isinstance(geo, Mapping) else {}
    lat = _float(
        _first(nested, "latitude", "lat") or _first(record, "latitude", "lat"),
        low=-90,
        high=90,
    )
    lng = _float(
        _first(nested, "longitude", "lng", "lon")
        or _first(record, "longitude", "lng", "lon"),
        low=-180,
        high=180,
    )
    return (lat, lng) if (lat is None) == (lng is None) else (None, None)


def _store_id(
    record: Mapping[str, Any],
    source_url: str,
    *,
    organization: str,
    branch: str,
    address: Mapping[str, Any],
) -> str:
    value = _text(
        _first(
            record,
            "source_store_id",
            "store_id",
            "storeId",
            "location_id",
            "locationId",
            "identifier",
            "id",
            "@id",
        ),
        limit=128,
    )
    if value and _SOURCE_STORE_ID_RE.fullmatch(value):
        return value
    # A locator endpoint can contain thousands of rows behind one URL.  Hashing
    # that URL alone would assign every id-less store the same identity.  The
    # fallback therefore uses exact normalized row fields; it is deterministic
    # but deliberately remains a source-local candidate identity, not a fuzzy
    # cross-publisher business match.
    return "derived_" + canonical_json_sha256(
        {
            "source_url": source_url,
            "organization": organization,
            "branch": branch,
            "address": dict(address),
        }
    )[:24]


def _normalize_record(
    record: Mapping[str, Any],
    *,
    source: Mapping[str, Any],
    source_url: Any,
    observed_at: str,
) -> tuple[dict[str, Any] | None, str | None]:
    observation_url = _publisher_observation_url(source, source_url)
    if observation_url is None:
        return None, "observation_source_not_publisher_bound"
    address = _address(record)
    if address is None:
        return None, "complete_us_address_required"
    branch = _text(_first(record, "branch_name", "branch", "name", "title"))
    organization = _text(
        _first(record, "organization_name", "organization", "company", "brand")
        or source.get("publisher")
    )
    if not organization or not branch:
        return None, "organization_and_branch_required"
    phone = _text(_first(record, "phone", "telephone", "phoneNumber"), limit=32)
    if phone and not _PHONE_ALLOWED_RE.fullmatch(phone):
        phone = None
    site_identity = source_url_identity(
        _first(record, "site_url", "website", "url") or observation_url
    )
    site_url = site_identity["canonical_url"] if site_identity["valid"] else None
    lat, lng = _coordinates(record)
    source_store_id = _store_id(
        record,
        observation_url,
        organization=organization,
        branch=branch,
        address=address,
    )
    artifact_sha256 = _text(
        record.get("extraction_artifact_sha256")
        or record.get("extraction_document_sha256"),
        limit=64,
    )
    if artifact_sha256 and not _SHA256_RE.fullmatch(artifact_sha256):
        artifact_sha256 = None
    artifact_kind = _text(record.get("extraction_artifact_kind"), limit=80)
    source_extractor = _text(record.get("extraction_tool"), limit=100)
    source_provenance = {
        "source_registry_id": str(source.get("id") or ""),
        "source_url": observation_url,
        "publisher_bound": True,
        "observed_at": observed_at,
        "artifact_sha256": artifact_sha256,
        "artifact_kind": artifact_kind,
        "extractor": source_extractor,
        "terms_robots_review": {
            "status": str(source.get("terms_robots_status") or ""),
            "reviewer_id": str(source.get("terms_robots_reviewer_id") or ""),
            "reviewed_at": _timestamp(source.get("terms_robots_reviewed_at")),
        },
    }
    normalized_payload = {
        "source_store_id": source_store_id,
        "organization_name": organization,
        "branch_name": branch,
        "address": address,
        "phone": phone,
        "site_url": site_url,
        "site_url_status": "unverified_candidate_field" if site_url else None,
        "lat": lat,
        "lng": lng,
        "observed_at": observed_at,
        "source_document_sha256": _text(
            record.get("extraction_document_sha256"), limit=64
        ),
        "source_artifact_sha256": artifact_sha256,
        "source_artifact_kind": artifact_kind,
        "source_extractor": source_extractor,
        "source_provenance": source_provenance,
    }
    content_sha256 = canonical_json_sha256(normalized_payload)
    source_registry_id = str(source.get("id") or "")
    entity_material = re.sub(r"[^A-Za-z0-9_.:-]+", "_", source_store_id).strip("_.:-")
    source_entity_key = f"store.{source_registry_id}.{entity_material}"[:160]
    candidate = {
        **normalized_payload,
        "source_registry_id": source_registry_id,
        "source_entity_key": source_entity_key,
        "source_url": observation_url,
        "content_sha256": content_sha256,
        "candidate_type": "dealer_location",
        "candidate_only": True,
        "claim_status": CLAIM_STATUS,
        "truth_dimensions": {
            "manufacturer_authorization_scope": _text(
                source.get("manufacturer_authorization_scope")
            ),
            "viltrox_authorization": "unknown",
            "viltrox_product_page": "unknown",
            "current_inventory": "unknown",
        },
        "staging_preview": {
            "record_only": True,
            "source_registry_id": source_registry_id,
            "source_entity_key": source_entity_key,
            "source_url": observation_url,
            "stable_org_key": "",
            "stable_location_key": "",
            "candidate_payload": normalized_payload,
        },
        "promotion_gate": {
            "status": "blocked",
            "eligible": False,
            "automatic_promotion": False,
            "reason": "candidate_requires_exact_identity_human_review_and_field_evidence",
        },
    }
    return candidate, None


def _sitemap_html_rows(
    snapshot: Mapping[str, Any], source: Mapping[str, Any]
) -> tuple[list[dict[str, Any]], list[str]]:
    raw_urls = snapshot.get("sitemap_urls")
    pages = snapshot.get("pages")
    if not isinstance(raw_urls, list) or not isinstance(pages, list):
        return [], ["sitemap_urls_and_pages_arrays_required"]
    sitemap_urls = {
        item["canonical_url"]
        for value in raw_urls
        if (item := source_url_identity(value))["valid"]
    }
    rows: list[dict[str, Any]] = []
    issues: list[str] = []
    for index, page in enumerate(pages):
        if not isinstance(page, Mapping):
            issues.append(f"pages[{index}]:page_object_required")
            continue
        page_identity = source_url_identity(page.get("url"))
        if not page_identity["valid"] or page_identity["canonical_url"] not in sitemap_urls:
            issues.append(f"pages[{index}]:page_not_in_snapshot_sitemap")
            continue
        observation_url = _publisher_observation_url(source, page_identity["canonical_url"])
        if observation_url is None:
            issues.append(f"pages[{index}]:publisher_observation_url_not_allowlisted")
            continue
        html = page.get("html")
        if not isinstance(html, str) or not html.strip():
            issues.append(f"pages[{index}]:html_required")
            continue
        parser = _JsonLdCollector()
        parser.feed(html)
        records = [row for document in parser.documents for row in _flatten_json_ld(document)]
        if not records:
            issues.append(f"pages[{index}]:store_json_ld_not_found")
            continue
        for record in records:
            enriched = dict(record)
            enriched["extraction_artifact_sha256"] = hashlib.sha256(
                html.encode("utf-8")
            ).hexdigest()
            enriched["extraction_artifact_kind"] = "captured_html_page"
            enriched["extraction_tool"] = "json_ld_html_parser_v1"
            rows.append({"record": enriched, "source_url": observation_url})
    return rows, issues


def _json_locator_rows(
    snapshot: Mapping[str, Any], source: Mapping[str, Any]
) -> tuple[list[dict[str, Any]], list[str]]:
    records = snapshot.get("records")
    if not isinstance(records, list):
        return [], ["records_array_required"]
    rows: list[dict[str, Any]] = []
    issues: list[str] = []
    for index, record in enumerate(records):
        if not isinstance(record, Mapping):
            issues.append(f"records[{index}]:record_object_required")
            continue
        # Locator records often contain the candidate retailer's own website.
        # It belongs in site_url, never in the publisher observation chain.
        enriched = dict(record)
        enriched["extraction_artifact_sha256"] = canonical_json_sha256(dict(record))
        enriched["extraction_artifact_kind"] = "captured_locator_record"
        enriched["extraction_tool"] = "explicit_json_field_mapping_v1"
        rows.append({"record": enriched, "source_url": source.get("canonical_url")})
    return rows, issues


def _tabular_pdf_rows(
    snapshot: Mapping[str, Any], source: Mapping[str, Any]
) -> tuple[list[dict[str, Any]], list[str]]:
    extraction = snapshot.get("extraction")
    if not isinstance(extraction, Mapping):
        return [], ["extraction_object_required"]
    if extraction.get("pdf_bytes") is not None or extraction.get("download_url") is not None:
        return [], ["adapter_accepts_extracted_rows_only"]
    if str(extraction.get("media_type") or "").casefold() != "application/pdf":
        return [], ["pdf_media_type_required"]
    document_sha256 = str(extraction.get("document_sha256") or "").strip().casefold()
    if not _SHA256_RE.fullmatch(document_sha256):
        return [], ["document_sha256_required"]
    extractor = _text(extraction.get("extractor"), limit=100)
    rows_value = extraction.get("rows")
    if not extractor or not isinstance(rows_value, list):
        return [], ["extractor_and_rows_required"]
    document_url = extraction.get("document_url")
    document_identity = source_url_identity(document_url)
    if not document_identity["valid"]:
        return [], ["document_url_invalid"]
    observation_url = _publisher_observation_url(
        source,
        document_identity["canonical_url"],
        exact_document=True,
    )
    if observation_url is None:
        return [], ["pdf_document_not_bound_to_registered_publisher"]
    rows: list[dict[str, Any]] = []
    issues: list[str] = []
    for index, record in enumerate(rows_value):
        if not isinstance(record, Mapping):
            issues.append(f"extraction.rows[{index}]:record_object_required")
            continue
        enriched = dict(record)
        enriched["extraction_document_sha256"] = document_sha256
        enriched["extraction_artifact_sha256"] = document_sha256
        enriched["extraction_artifact_kind"] = "publisher_pdf_document"
        enriched["extraction_tool"] = extractor
        rows.append({"record": enriched, "source_url": observation_url})
    return rows, issues


def _published_html_rows(
    snapshot: Mapping[str, Any], source: Mapping[str, Any]
) -> tuple[list[dict[str, Any]], list[str]]:
    """Accept only pre-extracted rows bound to one official location page.

    Retailer location pages use incompatible markup and often change without
    notice.  This adapter deliberately does not contain a generic HTML scraper:
    a separate reviewed capture step must supply the exact document URL/hash,
    extractor identity and explicit rows.  Raw HTML and download instructions
    are rejected so this module remains network-free and deterministic.
    """
    extraction = snapshot.get("extraction")
    if not isinstance(extraction, Mapping):
        return [], ["extraction_object_required"]
    if any(
        extraction.get(field) is not None
        for field in ("html", "raw_html", "html_bytes", "download_url")
    ):
        return [], ["adapter_accepts_extracted_rows_only"]
    if str(extraction.get("media_type") or "").casefold() != "text/html":
        return [], ["html_media_type_required"]
    document_sha256 = str(extraction.get("document_sha256") or "").strip().casefold()
    if not _SHA256_RE.fullmatch(document_sha256):
        return [], ["document_sha256_required"]
    extractor = _text(extraction.get("extractor"), limit=100)
    rows_value = extraction.get("rows")
    if not extractor or not isinstance(rows_value, list):
        return [], ["extractor_and_rows_required"]
    document_identity = source_url_identity(extraction.get("document_url"))
    if not document_identity["valid"]:
        return [], ["document_url_invalid"]
    observation_url = _publisher_observation_url(
        source,
        document_identity["canonical_url"],
        exact_document=True,
    )
    if observation_url is None:
        return [], ["html_document_not_bound_to_registered_publisher"]
    rows: list[dict[str, Any]] = []
    issues: list[str] = []
    for index, record in enumerate(rows_value):
        if not isinstance(record, Mapping):
            issues.append(f"extraction.rows[{index}]:record_object_required")
            continue
        enriched = dict(record)
        enriched["extraction_document_sha256"] = document_sha256
        enriched["extraction_artifact_sha256"] = document_sha256
        enriched["extraction_artifact_kind"] = "publisher_html_document"
        enriched["extraction_tool"] = extractor
        rows.append({"record": enriched, "source_url": observation_url})
    return rows, issues


_ROW_BUILDERS = {
    "sitemap_html": _sitemap_html_rows,
    "json_locator": _json_locator_rows,
    "tabular_pdf": _tabular_pdf_rows,
    "published_html_rows": _published_html_rows,
}


def adapt_registered_snapshot(
    source_registry_id: str,
    snapshot: Mapping[str, Any],
    *,
    observed_at: Any,
) -> dict[str, Any]:
    """Normalize one offline snapshot only when the registered source is open.

    A successful result still contains candidate staging envelopes only.  There
    is intentionally no persistence or promotion code path in this module.
    """
    source_registry_id = str(source_registry_id or "").strip()
    source = _registered_source(source_registry_id)
    adapter = ADAPTER_BY_SOURCE.get(source_registry_id)
    reasons = _source_gate(source, source_registry_id)
    if not adapter:
        reasons.append("adapter_not_registered")
    observed = _timestamp(observed_at)
    if observed is None:
        reasons.append("timezone_aware_observed_at_required")
    if not isinstance(snapshot, Mapping):
        reasons.append("snapshot_object_required")
    base = {
        "contract": {
            "id": CONTRACT_ID,
            "version": CONTRACT_VERSION,
            "read_only": True,
            "offline_snapshot_only": True,
            "network_accessed": False,
            "database_accessed": False,
            "provider_calls": 0,
            "candidate_rows_written": 0,
            "business_rows_written": 0,
            "scheduler_enabled": False,
        },
        "source_registry_id": source_registry_id or None,
        "adapter": adapter,
        "candidate_only": True,
        "claim_status": CLAIM_STATUS,
        "direct_business_import": False,
    }
    if reasons:
        return {
            **base,
            "ok": False,
            "status": "blocked",
            "candidates": [],
            "issues": sorted(set(reasons)),
        }

    assert isinstance(source, Mapping) and isinstance(snapshot, Mapping) and observed is not None
    raw_rows, issues = _ROW_BUILDERS[adapter](snapshot, source)
    candidates: list[dict[str, Any]] = []
    seen_source_entities: dict[str, str] = {}
    for index, item in enumerate(raw_rows):
        candidate, issue = _normalize_record(
            item["record"],
            source=source,
            source_url=item["source_url"],
            observed_at=observed,
        )
        if issue:
            issues.append(f"rows[{index}]:{issue}")
        elif candidate is not None:
            entity_key = str(candidate["source_entity_key"])
            prior_hash = seen_source_entities.get(entity_key)
            if prior_hash is not None:
                issue_code = (
                    "duplicate_source_entity_key"
                    if prior_hash == candidate["content_sha256"]
                    else "conflicting_source_entity_key"
                )
                issues.append(f"rows[{index}]:{issue_code}")
                continue
            seen_source_entities[entity_key] = str(candidate["content_sha256"])
            candidates.append(candidate)
    candidates.sort(key=lambda item: (item["source_store_id"], item["source_url"]))
    return {
        **base,
        "ok": not issues,
        "status": "candidate_snapshot_normalized" if not issues else "partial_candidate_snapshot",
        "candidates": candidates,
        "issues": issues,
        "promotion_gate": {
            "status": "blocked",
            "automatic_promotion": False,
            "business_table_promotion_available": False,
        },
    }


def adapter_contracts() -> dict[str, Any]:
    """Expose a static, network-free format/readiness manifest."""
    registry_rows = us_coverage_registry.dealer_registry().get(
        "dealer_discovery_sources", []
    )
    registered_sources = sorted(
        str(item.get("id") or "")
        for item in registry_rows
        if str(item.get("id") or "")
    )
    adapter_sources = sorted(ADAPTER_BY_SOURCE)
    mapped_adapter_sources = sorted(set(registered_sources).intersection(adapter_sources))
    adapter_sources_not_registered = sorted(
        set(adapter_sources).difference(registered_sources)
    )
    sources_without_mapped_adapter = sorted(
        set(registered_sources).difference(adapter_sources)
    )
    # A format mapping is intentionally weaker than a reviewed source fixture.
    # No source-specific captured payload is checked in as verification, so a
    # A complete format mapping must never collapse the separate activation/verification
    # gate to "ready".
    sources_without_source_fixture_verification = list(registered_sources)
    source_readiness = []
    for source in sorted(
        (item for item in registry_rows if isinstance(item, Mapping)),
        key=lambda item: str(item.get("id") or ""),
    ):
        source_id = str(source.get("id") or "")
        adapter = ADAPTER_BY_SOURCE.get(source_id)
        blockers: list[str] = []
        if not adapter:
            blockers.append("adapter_not_mapped")
        # No source-specific checked-in capture has passed review yet.  Test
        # fixtures exercise formats only and must not be promoted to live proof.
        blockers.append("source_specific_fixture_not_verified")
        if source.get("enabled") is not True:
            blockers.append("source_registry_disabled")
        if str(source.get("status") or "") != "active":
            blockers.append("source_registry_not_active")
        if str(source.get("terms_robots_status") or "") != TERMS_ROBOTS_ALLOWED:
            blockers.append("terms_robots_review_pending")
        source_readiness.append(
            {
                "source_registry_id": source_id,
                "publisher": str(source.get("publisher") or ""),
                "source_kind": str(source.get("source_kind") or ""),
                "adapter": adapter,
                "format_mapped": adapter is not None,
                "source_fixture_verified": False,
                "terms_robots_status": str(
                    source.get("terms_robots_status") or "pending_review"
                ),
                "terms_robots_reviewed": False,
                "source_enabled": source.get("enabled") is True,
                "snapshot_import_readiness": "blocked",
                "candidate_envelope_readiness": "blocked",
                "direct_business_import": False,
                "blockers": sorted(set(blockers)),
                "claim_status": CLAIM_STATUS,
            }
        )
    return {
        "contract": {"id": CONTRACT_ID, "version": CONTRACT_VERSION},
        "formats": {
            "sitemap_html": {
                "sources": ["dealer_bestbuy_us_store_directory"],
                "input": "captured sitemap URLs plus captured store-page HTML",
                "extraction": "LocalBusiness/Store JSON-LD only",
            },
            "json_locator": {
                "sources": ["dealer_omsystem_us_locator", "dealer_profoto_us_locator"],
                "input": "captured locator records array",
                "extraction": "explicit field mapping; no recursive page scraping",
            },
            "tabular_pdf": {
                "sources": [
                    "dealer_canon_us_where_to_buy",
                    "dealer_nikon_us_authorized_imaging",
                ],
                "input": "pre-extracted PDF rows with document URL/hash/extractor",
                "extraction": "contract validation only; no PDF download or parser",
            },
            "published_html_rows": {
                "sources": sorted(
                    source_id
                    for source_id, adapter in ADAPTER_BY_SOURCE.items()
                    if adapter == "published_html_rows"
                ),
                "input": "pre-extracted official location-page rows with exact URL/hash/extractor",
                "extraction": "contract validation only; no HTML download, DOM heuristics or recursive scraping",
            },
        },
        "source_gate": {
            "enabled_required": True,
            "status_required": "active",
            "terms_robots_status_required": TERMS_ROBOTS_ALLOWED,
            "terms_robots_reviewer_required": True,
            "terms_robots_reviewed_at_required": True,
            "candidate_human_review_required": True,
            "direct_import_allowed_required": False,
        },
        "registry_adapter_readiness": {
            "registered_source_count": len(registered_sources),
            "adapter_source_count": len(adapter_sources),
            "mapped_adapter_source_count": len(mapped_adapter_sources),
            "adapter_sources_not_registered": adapter_sources_not_registered,
            "sources_without_mapped_adapter": sources_without_mapped_adapter,
            "all_registered_sources_have_mapped_adapter": not sources_without_mapped_adapter,
            "readiness_level": "format_mapping_only_not_source_fixture_verified",
            "source_fixture_verified_count": 0,
            "sources_without_source_fixture_verification": (
                sources_without_source_fixture_verification
            ),
            "all_registered_sources_have_source_fixture_verification": False,
            # Deprecated compatibility aliases. Keep these conservative: a
            # mapping is not proof that a source-specific captured fixture has
            # been reviewed, even when every registry id has a format mapping.
            "sources_without_verified_adapter": (
                sources_without_source_fixture_verification
            ),
            "all_registered_sources_have_verified_adapter": False,
            "mapping_blocker": (
                "official_payload_format_and_terms_robots_review_required"
                if sources_without_mapped_adapter
                else None
            ),
            "activation_blocker": (
                "source_specific_fixture_and_terms_robots_review_required"
            ),
            "blocker": "source_specific_fixture_and_terms_robots_review_required",
            "source_coverage_is_not_entity_coverage": True,
        },
        "source_readiness": source_readiness,
        "candidate_truth_defaults": {
            "viltrox_authorization": "unknown",
            "viltrox_product_page": "unknown",
            "current_inventory": "unknown",
        },
        "network_accessed": False,
        "database_accessed": False,
        "provider_calls": 0,
        "scheduler_enabled": False,
        "business_rows_written": 0,
    }


__all__ = [
    "ADAPTER_BY_SOURCE",
    "CONTRACT_ID",
    "CONTRACT_VERSION",
    "TERMS_ROBOTS_ALLOWED",
    "adapt_registered_snapshot",
    "adapter_contracts",
]
