"""Offline bridge from Dealer technical quarantine to candidate staging.

The technical quarantine intentionally captures public address/contact facts
before legal approval and source activation.  This module maps those facts to
the existing migration-257 candidate-staging preview contract while keeping
three boundaries explicit:

* a validated quarantine candidate may be prepared for manager-controlled
  *candidate* staging;
* it may not be approved as a business import while legal/source controls are
  absent;
* it may not create or publish a Dealer map row.

No function in this module performs network access, SQL, geocoding, business
import, map publication, or source activation.
"""
from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from app.domains.commerce.dealer_candidate_quarantine import (
    CLAIM_STATUS as QUARANTINE_CLAIM_STATUS,
    CONTRACT_ID as QUARANTINE_CONTRACT_ID,
    CONTRACT_VERSION as QUARANTINE_CONTRACT_VERSION,
)
from app.domains.commerce.dealer_identity import (
    propose_stable_location_key,
    propose_stable_org_key,
)
from app.domains.events import candidate_staging, us_coverage_registry
from app.domains.source_passport_urls import source_url_identity


CONTRACT_ID = "vkpi.us_dealer.quarantine_candidate_staging_bridge"
CONTRACT_VERSION = 1
CLAIM_STATUS = "descriptive_only"
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_EMAIL_RE = re.compile(r"^[^@\s]{1,128}@[^@\s]{1,190}$")
_US_STATE_CODES = frozenset(
    "AL AK AZ AR CA CO CT DE FL GA HI ID IL IN IA KS KY LA ME MD MA MI MN "
    "MS MO MT NE NV NH NJ NM NY NC ND OH OK OR PA RI SC SD TN TX UT VT VA "
    "WA WV WI WY DC".split()
)

# Only manufacturer-owned discovery sources receive a descriptive brand-scope
# candidate.  Retailer-owned directories never imply a manufacturer relation.
_MANUFACTURER_BRAND_BY_SOURCE_ID = {
    "dealer_nikon_us_authorized_imaging": "nikon",
    "dealer_canon_us_where_to_buy": "canon",
    "dealer_sony_us_where_to_buy": "sony",
    "dealer_fujifilm_us_shop": "fujifilm",
    "dealer_panasonic_us_authorized": "panasonic",
    "dealer_omsystem_us_locator": "om_system",
    "dealer_leica_us_locator": "leica",
    "dealer_blackmagic_us_resellers": "blackmagic_design",
    "dealer_tamron_americas_locator": "tamron",
    "dealer_sigma_us_authorized": "sigma",
    "dealer_hasselblad_us_locator": "hasselblad",
    "dealer_profoto_us_locator": "profoto",
    "dealer_phaseone_us_partner_locator": "phase_one",
}


class DealerQuarantineBridgeError(ValueError):
    """The supplied quarantine cannot safely enter candidate staging."""


def _canonical_sha256(value: Any) -> str:
    encoded = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
        default=str,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _registry_file_sha256() -> str:
    path = Path(us_coverage_registry.__file__).with_name(
        "us_coverage_source_registry.json"
    )
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _mapping(value: Any, field: str) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise DealerQuarantineBridgeError(f"{field}_must_be_object")
    return dict(value)


def _text(value: Any, field: str, *, maximum: int = 500) -> str:
    text = " ".join(str(value or "").split())
    if not text:
        raise DealerQuarantineBridgeError(f"{field}_required")
    if len(text) > maximum:
        raise DealerQuarantineBridgeError(f"{field}_too_long")
    return text


def _optional_text(value: Any, field: str, *, maximum: int) -> str | None:
    text = " ".join(str(value or "").split())
    if len(text) > maximum:
        raise DealerQuarantineBridgeError(f"{field}_too_long")
    return text or None


def _public_https(value: Any, field: str) -> str:
    identity = source_url_identity(value)
    if not identity["valid"]:
        raise DealerQuarantineBridgeError(f"{field}_must_be_public_https")
    return str(identity["canonical_url"])


def _validate_artifact_envelope(artifact: Mapping[str, Any]) -> str:
    contract = _mapping(artifact.get("contract"), "contract")
    expected = {
        "id": QUARANTINE_CONTRACT_ID,
        "version": QUARANTINE_CONTRACT_VERSION,
        "read_only": True,
        "technical_quarantine_only": True,
        "database_accessed": False,
        "candidate_rows_written": 0,
        "business_rows_written": 0,
        "direct_import_available": False,
        "geocoding_performed": False,
        "legal_approval": False,
        "source_activation": False,
    }
    for field, expected_value in expected.items():
        if contract.get(field) != expected_value:
            raise DealerQuarantineBridgeError(
                f"quarantine_contract_{field}_invalid"
            )
    if artifact.get("claim_status") != QUARANTINE_CLAIM_STATUS:
        raise DealerQuarantineBridgeError("quarantine_claim_status_invalid")
    supplied_sha = str(artifact.get("artifact_content_sha256") or "")
    if not _SHA256_RE.fullmatch(supplied_sha):
        raise DealerQuarantineBridgeError("artifact_content_sha256_invalid")
    unsigned = dict(artifact)
    unsigned.pop("artifact_content_sha256", None)
    if _canonical_sha256(unsigned) != supplied_sha:
        raise DealerQuarantineBridgeError("artifact_content_sha256_mismatch")
    provenance = _mapping(artifact.get("input_provenance"), "input_provenance")
    source_registry_sha = str(provenance.get("source_registry_sha256") or "")
    if not _SHA256_RE.fullmatch(source_registry_sha):
        raise DealerQuarantineBridgeError("source_registry_sha256_invalid")
    if source_registry_sha != _registry_file_sha256():
        raise DealerQuarantineBridgeError("source_registry_snapshot_drift")
    return supplied_sha


def _address(value: Any) -> dict[str, str]:
    raw = _mapping(value, "candidate_address")
    line1 = _text(raw.get("line1"), "candidate_address_line1", maximum=240)
    line2 = _optional_text(
        raw.get("line2"), "candidate_address_line2", maximum=120
    )
    city = _text(raw.get("city"), "candidate_address_city", maximum=100)
    state = str(raw.get("state") or "").strip().upper()
    postal_code = str(raw.get("postal_code") or "").strip()
    country_code = str(raw.get("country_code") or "").strip().upper()
    if state not in _US_STATE_CODES:
        raise DealerQuarantineBridgeError("candidate_address_state_invalid")
    if not re.fullmatch(r"[0-9]{5}(?:-[0-9]{4})?", postal_code):
        raise DealerQuarantineBridgeError("candidate_address_postal_code_invalid")
    if country_code != "US":
        raise DealerQuarantineBridgeError("candidate_address_country_must_be_us")
    formatted = ", ".join(
        item
        for item in (
            line1,
            line2,
            f"{city}, {state} {postal_code}",
            "US",
        )
        if item
    )
    if str(raw.get("formatted") or "").strip() != formatted:
        raise DealerQuarantineBridgeError("candidate_address_formatted_mismatch")
    return {
        "line1": line1,
        "line2": line2 or "",
        "city": city,
        "state": state,
        "postal_code": postal_code,
        "country_code": "US",
        "formatted": formatted,
    }


def _contact(value: Any) -> dict[str, str | None]:
    raw = _mapping(value, "candidate_contact")
    phone = _optional_text(raw.get("phone"), "candidate_contact_phone", maximum=48)
    email = _optional_text(raw.get("email"), "candidate_contact_email", maximum=254)
    if email and not _EMAIL_RE.fullmatch(email):
        raise DealerQuarantineBridgeError("candidate_contact_email_invalid")
    website = _public_https(raw.get("website"), "candidate_contact_website")
    return {"phone": phone, "email": email, "website": website}


def _coordinates(value: Any) -> dict[str, Any]:
    raw = _mapping(value, "candidate_map_fields")
    latitude = raw.get("latitude")
    longitude = raw.get("longitude")
    if (latitude is None) != (longitude is None):
        raise DealerQuarantineBridgeError("publisher_coordinates_must_be_paired")
    if latitude is not None:
        try:
            latitude = float(latitude)
            longitude = float(longitude)
        except (TypeError, ValueError) as exc:
            raise DealerQuarantineBridgeError(
                "publisher_coordinates_invalid"
            ) from exc
        if not (-90 <= latitude <= 90 and -180 <= longitude <= 180):
            raise DealerQuarantineBridgeError("publisher_coordinates_out_of_range")
        if raw.get("geocoding_status") != "publisher_coordinates":
            raise DealerQuarantineBridgeError(
                "publisher_coordinates_status_mismatch"
            )
    elif raw.get("geocoding_status") != "not_performed":
        raise DealerQuarantineBridgeError("geocoding_status_mismatch")
    return {
        "latitude": latitude,
        "longitude": longitude,
        "coordinate_source": (
            "publisher_observation" if latitude is not None else None
        ),
        "geocoding_performed": False,
    }


def _brand_relationships(
    source: Mapping[str, Any], *, source_url: str
) -> list[dict[str, Any]]:
    if source.get("source_kind") != "manufacturer_dealer_directory":
        return []
    source_id = str(source.get("id") or "")
    brand_key = _MANUFACTURER_BRAND_BY_SOURCE_ID.get(source_id)
    if not brand_key:
        raise DealerQuarantineBridgeError(
            "manufacturer_source_brand_mapping_missing"
        )
    return [
        {
            "brand_key": brand_key,
            "relationship_status": "unverified_directory_candidate",
            "source_scope_note": str(
                source.get("manufacturer_authorization_scope") or ""
            ),
            "evidence_url": source_url,
            "requires_human_review": True,
            "claim_status": CLAIM_STATUS,
        }
    ]


def _candidate_envelope(
    raw_candidate: Mapping[str, Any],
    *,
    parent_source: Mapping[str, Any],
    registry_source: Mapping[str, Any],
    artifact_sha256: str,
    organization_id: int,
) -> dict[str, Any]:
    candidate = dict(raw_candidate)
    source_id = _text(
        candidate.get("source_registry_id"),
        "candidate_source_registry_id",
        maximum=128,
    )
    if source_id != parent_source.get("source_registry_id"):
        raise DealerQuarantineBridgeError("candidate_parent_source_mismatch")
    if source_id != registry_source.get("id"):
        raise DealerQuarantineBridgeError("candidate_registry_source_mismatch")
    if candidate.get("claim_status") != CLAIM_STATUS:
        raise DealerQuarantineBridgeError("candidate_claim_status_invalid")
    for field, expected in (
        ("candidate_only", True),
        ("legal_approval", False),
        ("source_activation", False),
        ("promotion_eligible", False),
        ("business_rows_written", 0),
    ):
        if candidate.get(field) != expected:
            raise DealerQuarantineBridgeError(f"candidate_{field}_invalid")
    candidate_sha = str(candidate.get("content_sha256") or "")
    if not _SHA256_RE.fullmatch(candidate_sha):
        raise DealerQuarantineBridgeError("candidate_content_sha256_invalid")
    unhashed = dict(candidate)
    unhashed.pop("content_sha256", None)
    if _canonical_sha256(unhashed) != candidate_sha:
        raise DealerQuarantineBridgeError("candidate_content_sha256_mismatch")

    source_url = _public_https(
        _mapping(candidate.get("provenance"), "candidate_provenance").get(
            "source_url"
        ),
        "candidate_provenance_source_url",
    )
    snapshot = _mapping(parent_source.get("snapshot"), "source_snapshot")
    snapshot_sha = str(snapshot.get("sha256") or "")
    if not _SHA256_RE.fullmatch(snapshot_sha):
        raise DealerQuarantineBridgeError("source_snapshot_sha256_invalid")
    provenance = _mapping(candidate.get("provenance"), "candidate_provenance")
    if provenance.get("snapshot_sha256") != snapshot_sha:
        raise DealerQuarantineBridgeError("candidate_snapshot_sha256_mismatch")
    if provenance.get("publisher_bound") is not True:
        raise DealerQuarantineBridgeError("candidate_not_publisher_bound")

    address = _address(candidate.get("address"))
    contact = _contact(candidate.get("contact"))
    coordinates = _coordinates(candidate.get("map_fields"))
    organization_name = _text(
        candidate.get("organization_name"),
        "candidate_organization_name",
        maximum=200,
    )
    raw_branch_name = _optional_text(
        candidate.get("branch_name"), "candidate_branch_name", maximum=200
    )
    # Some publisher pages expose a complete branch address but only the
    # organization name.  Preserve that fact instead of inventing a branch
    # label; the human review surface still receives an explicit status.
    branch_name = raw_branch_name or organization_name
    proposed_org_key = propose_stable_org_key(
        organization_name,
        country_code="US",
        official_domain=str(urlsplit(str(contact["website"])).hostname or ""),
    )
    proposed_location_key = propose_stable_location_key(
        proposed_org_key,
        country_code="US",
        address=address["formatted"],
        postal_code=address["postal_code"],
    )
    brand_relationships = _brand_relationships(
        registry_source, source_url=source_url
    )
    evidence = _mapping(candidate.get("evidence"), "candidate_evidence")
    candidate_payload = {
        "organization_name": organization_name,
        "branch_name": branch_name,
        "branch_name_status": (
            "publisher_observed" if raw_branch_name else "organization_name_fallback"
        ),
        "address": address,
        "contact": contact,
        "publisher_coordinates": coordinates,
        "brand_relationships": brand_relationships,
        "identity_proposal": {
            "stable_org_key": proposed_org_key,
            "stable_location_key": proposed_location_key,
            "status": "requires_human_acceptance",
        },
        "quarantine_evidence": {
            "contract_id": QUARANTINE_CONTRACT_ID,
            "artifact_content_sha256": artifact_sha256,
            "candidate_content_sha256": candidate_sha,
            "snapshot_sha256": snapshot_sha,
            "captured_at": provenance.get("captured_at"),
            "method": evidence.get("method"),
            "quality_tier": evidence.get("quality_tier"),
            "quality_score": evidence.get("quality_score"),
            "publisher_bound": True,
        },
        "source_control_snapshot": {
            "legal_approval": False,
            "source_activation": False,
            "registry_status": registry_source.get("status"),
            "registry_enabled": registry_source.get("enabled") is True,
            "terms_robots_status": registry_source.get("terms_robots_status"),
        },
        "claim_status": CLAIM_STATUS,
    }
    staging_envelope = {
        "record_only": True,
        "source_registry_id": source_id,
        "source_entity_key": _text(
            candidate.get("source_entity_key"),
            "candidate_source_entity_key",
            maximum=160,
        ),
        "source_url": source_url,
        # Proposed keys remain inside the payload until a human accepts exact
        # identity.  Leaving these empty makes migration-257 fail closed.
        "stable_org_key": "",
        "stable_location_key": "",
        "candidate_payload": candidate_payload,
    }
    staging_preview = candidate_staging.preview_candidate(
        staging_envelope,
        candidate_type="dealer_location",
        organization_id=organization_id,
    )
    source_blockers = [
        "legal_approval_missing",
        "source_activation_missing",
        "source_registry_not_active",
        "source_registry_disabled",
        "terms_robots_review_pending",
    ]
    return {
        "source_registry_id": source_id,
        "source_entity_key": staging_envelope["source_entity_key"],
        "cross_source_dedupe_key": candidate.get("cross_source_dedupe_key"),
        "address": address,
        "contact": contact,
        "brand_relationships": brand_relationships,
        "identity_proposal": candidate_payload["identity_proposal"],
        "staging_envelope": staging_envelope,
        "staging_preview": staging_preview,
        "candidate_staging_gate": {
            "status": "manager_stage_available",
            "eligible": True,
            "writes_performed": 0,
            "record_only_template": True,
            "requires_explicit_record_only_false": True,
            "requires_manager_write_permission": True,
        },
        "human_review_gate": {
            "status": "review_queue_ready_approval_blocked",
            "reviewable": True,
            "approval_eligible": False,
            "reasons": sorted(
                set(source_blockers + staging_preview["promotion_gate"]["reasons"])
            ),
        },
        "business_import_gate": {
            "status": "blocked",
            "eligible": False,
            "reasons": source_blockers,
        },
        "map_publication_gate": {
            "status": "blocked",
            "eligible": False,
            "reasons": source_blockers
            + [
                "approved_candidate_required",
                "exact_existing_dealer_target_required",
                "manual_publication_required",
            ],
        },
        "business_rows_written": 0,
        "map_rows_written": 0,
        "claim_status": CLAIM_STATUS,
    }


def build_quarantine_staging_plan(
    artifact: Mapping[str, Any],
    *,
    organization_id: Any,
) -> dict[str, Any]:
    """Return a deterministic, read-only candidate-staging bridge plan."""
    if not isinstance(artifact, Mapping):
        raise DealerQuarantineBridgeError("quarantine_artifact_must_be_object")
    try:
        org_id = int(organization_id)
    except (TypeError, ValueError) as exc:
        raise DealerQuarantineBridgeError(
            "organization_id_must_be_positive_integer"
        ) from exc
    if (
        isinstance(organization_id, bool)
        or org_id <= 0
        or (
            isinstance(organization_id, float)
            and not organization_id.is_integer()
        )
        or (
            isinstance(organization_id, str)
            and not re.fullmatch(r"[1-9][0-9]*", organization_id.strip())
        )
    ):
        raise DealerQuarantineBridgeError(
            "organization_id_must_be_positive_integer"
        )
    artifact_sha = _validate_artifact_envelope(artifact)
    registry = us_coverage_registry.audit_registry()
    if registry.get("ok") is not True:
        raise DealerQuarantineBridgeError("source_registry_audit_failed")
    if artifact.get("registry_version") != registry.get("registry_version"):
        raise DealerQuarantineBridgeError("source_registry_version_mismatch")
    registry_by_id = {
        str(row.get("id") or ""): row
        for row in registry.get("dealer_discovery_sources") or []
        if isinstance(row, Mapping)
    }

    rows: list[dict[str, Any]] = []
    seen_entity_keys: set[str] = set()
    source_rows = artifact.get("sources")
    if not isinstance(source_rows, list):
        raise DealerQuarantineBridgeError("quarantine_sources_must_be_array")
    for raw_source in source_rows:
        source = _mapping(raw_source, "quarantine_source")
        source_id = _text(
            source.get("source_registry_id"),
            "source_registry_id",
            maximum=128,
        )
        registry_source = registry_by_id.get(source_id)
        if registry_source is None:
            raise DealerQuarantineBridgeError("source_registry_id_not_registered")
        preflight_gate = _mapping(
            source.get("preflight_gate"), "source_preflight_gate"
        )
        for field, expected in (
            ("terms_legal_approval", False),
            ("legal_approval", False),
            ("source_activation", False),
            ("business_rows_written", 0),
        ):
            actual = (
                preflight_gate.get(field)
                if field == "terms_legal_approval"
                else source.get(field)
            )
            if actual != expected:
                raise DealerQuarantineBridgeError(
                    f"source_{field}_invalid"
                )
        candidates = source.get("candidates") or []
        if not isinstance(candidates, list):
            raise DealerQuarantineBridgeError("source_candidates_must_be_array")
        if int(source.get("candidate_count") or 0) != len(candidates):
            raise DealerQuarantineBridgeError("source_candidate_count_mismatch")
        for raw_candidate in candidates:
            row = _candidate_envelope(
                _mapping(raw_candidate, "quarantine_candidate"),
                parent_source=source,
                registry_source=registry_source,
                artifact_sha256=artifact_sha,
                organization_id=org_id,
            )
            entity_key = str(row["source_entity_key"])
            if entity_key in seen_entity_keys:
                raise DealerQuarantineBridgeError(
                    "duplicate_candidate_source_entity_key"
                )
            seen_entity_keys.add(entity_key)
            rows.append(row)

    rows.sort(
        key=lambda item: (
            str(item.get("source_registry_id") or ""),
            str(item.get("source_entity_key") or ""),
        )
    )
    summary = _mapping(artifact.get("summary"), "quarantine_summary")
    if int(summary.get("candidate_count") or 0) != len(rows):
        raise DealerQuarantineBridgeError("artifact_candidate_count_mismatch")
    address_count = sum(bool(row.get("address")) for row in rows)
    contact_count = sum(
        bool(row["contact"].get("phone") or row["contact"].get("email"))
        for row in rows
    )
    website_count = sum(bool(row["contact"].get("website")) for row in rows)
    publisher_coordinate_count = sum(
        row["staging_envelope"]["candidate_payload"]["publisher_coordinates"].get(
            "latitude"
        )
        is not None
        for row in rows
    )
    brand_relationship_count = sum(
        len(row.get("brand_relationships") or []) for row in rows
    )
    plan = {
        "contract": {
            "id": CONTRACT_ID,
            "version": CONTRACT_VERSION,
            "read_only": True,
            "network_accessed": False,
            "database_accessed": False,
            "candidate_rows_written": 0,
            "business_rows_written": 0,
            "map_rows_written": 0,
            "source_activation_performed": False,
        },
        "organization_id": org_id,
        "input_generated_at": artifact.get("generated_at"),
        "input_artifact_content_sha256": artifact_sha,
        "registry_version": registry.get("registry_version"),
        "summary": {
            "quarantine_candidate_count": len(rows),
            "staging_preview_ready_count": len(rows),
            "address_mapped_count": address_count,
            "contact_mapped_count": contact_count,
            "website_mapped_count": website_count,
            "publisher_coordinate_mapped_count": publisher_coordinate_count,
            "brand_relationship_mapped_count": brand_relationship_count,
            "human_review_queue_ready_count": len(rows),
            "approval_eligible_count": 0,
            "business_import_eligible_count": 0,
            "map_publication_eligible_count": 0,
            "candidate_rows_written": 0,
            "business_rows_written": 0,
            "map_rows_written": 0,
        },
        "control_plane": {
            "candidate_staging_endpoint": (
                "/api/admin/vkpi/dealers/candidate-staging"
            ),
            "candidate_staging_requires_manager": True,
            "candidate_staging_requires_explicit_record_only_false": True,
            "candidate_staging_is_business_import": False,
            "human_review_requires_current_exact_source_passport": True,
            "human_review_requires_current_location_field_evidence": True,
            "human_review_requires_accepted_stable_identity": True,
            "business_import_requires_legal_approval": True,
            "business_import_requires_source_activation": True,
            "map_publication_requires_existing_exact_dealer_target": True,
            "map_publication_requires_explicit_manager_action": True,
            "automatic_business_import": False,
            "automatic_map_publication": False,
        },
        "candidates": rows,
        "claim_status": CLAIM_STATUS,
        "full_us_coverage": False,
        "claim_boundaries": {
            "address_candidate_is_reviewed_dealer": False,
            "brand_scope_is_viltrox_authorization": False,
            "candidate_is_current_inventory": False,
            "candidate_is_map_publication": False,
        },
    }
    plan["artifact_content_sha256"] = _canonical_sha256(plan)
    return plan


__all__ = [
    "CONTRACT_ID",
    "CONTRACT_VERSION",
    "DealerQuarantineBridgeError",
    "build_quarantine_staging_plan",
]
