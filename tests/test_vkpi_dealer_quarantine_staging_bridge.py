from __future__ import annotations

import hashlib
import json
from copy import deepcopy
from pathlib import Path

import pytest

from app.domains.commerce import dealer_quarantine_staging_bridge as bridge
from app.domains.events import candidate_staging, us_coverage_registry


def _sha(value) -> str:
    return hashlib.sha256(
        json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
            default=str,
        ).encode("utf-8")
    ).hexdigest()


def _registry_sha() -> str:
    path = Path(us_coverage_registry.__file__).with_name(
        "us_coverage_source_registry.json"
    )
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _source(source_id: str) -> dict:
    report = us_coverage_registry.audit_registry()
    return next(
        row
        for row in report["dealer_discovery_sources"]
        if row["id"] == source_id
    )


def _artifact(
    source_id: str = "dealer_adorama_nyc_store_us",
) -> dict:
    source = _source(source_id)
    url = source["canonical_url"]
    address = {
        "line1": "42 West 18th Street",
        "line2": "",
        "city": "New York",
        "state": "NY",
        "postal_code": "10011",
        "country_code": "US",
        "formatted": "42 West 18th Street, New York, NY 10011, US",
    }
    snapshot_sha = "a" * 64
    candidate = {
        "source_registry_id": source_id,
        "source_entity_key": f"dealer_candidate.{source_id}.store_001",
        "cross_source_dedupe_key": "us_address." + "b" * 32,
        "organization_name": str(source["publisher"]),
        "branch_name": f"{source['publisher']} Midtown",
        "address": address,
        "contact": {
            "phone": "+1-212-555-0199",
            "email": "store@example.com",
            "website": url,
        },
        "map_fields": {
            "latitude": 40.7395,
            "longitude": -73.9941,
            "geocoding_status": "publisher_coordinates",
        },
        "evidence": {
            "method": "json_ld_complete_us_address",
            "locator": "json_ld[0]",
            "quality_tier": "high",
            "quality_score": 0.95,
        },
        "provenance": {
            "source_url": url,
            "captured_at": "2026-07-15T20:00:00Z",
            "snapshot_sha256": snapshot_sha,
            "publisher_bound": True,
        },
        "truth_dimensions": {
            "source_publisher": source["publisher"],
            "physical_location": "public_candidate_requires_human_review",
        },
        "legal_approval": False,
        "source_activation": False,
        "promotion_eligible": False,
        "business_rows_written": 0,
        "candidate_only": True,
        "claim_status": "descriptive_only",
    }
    candidate["content_sha256"] = _sha(candidate)
    payload = {
        "contract": {
            "id": "vkpi.us_dealer.technical_candidate_quarantine",
            "version": 1,
            "read_only": True,
            "technical_quarantine_only": True,
            "database_accessed": False,
            "candidate_rows_written": 0,
            "business_rows_written": 0,
            "direct_import_available": False,
            "geocoding_performed": False,
            "legal_approval": False,
            "source_activation": False,
        },
        "generated_at": "2026-07-15T20:00:00Z",
        "registry_version": us_coverage_registry.audit_registry()[
            "registry_version"
        ],
        "input_provenance": {
            "technical_preflight_sha256": "c" * 64,
            "source_registry_sha256": _registry_sha(),
        },
        "summary": {"candidate_count": 1},
        "sources": [
            {
                "source_registry_id": source_id,
                "publisher": source["publisher"],
                "source_kind": source["source_kind"],
                "canonical_url": url,
                "preflight_gate": {"terms_legal_approval": False},
                "candidate_count": 1,
                "snapshot": {"sha256": snapshot_sha},
                "candidates": [candidate],
                "legal_approval": False,
                "source_activation": False,
                "business_rows_written": 0,
            }
        ],
        "claim_status": "descriptive_only",
    }
    payload["artifact_content_sha256"] = _sha(payload)
    return payload


def _resign(artifact: dict) -> dict:
    artifact["artifact_content_sha256"] = _sha(
        {key: value for key, value in artifact.items() if key != "artifact_content_sha256"}
    )
    return artifact


def test_bridge_maps_address_contact_and_keeps_all_business_gates_closed(
    monkeypatch,
) -> None:
    def forbidden(*_args, **_kwargs):
        raise AssertionError("bridge must not touch the database")

    monkeypatch.setattr(candidate_staging, "get_conn", forbidden)
    monkeypatch.setattr(candidate_staging, "table_exists", forbidden)

    result = bridge.build_quarantine_staging_plan(
        _artifact(), organization_id=7
    )

    assert result["summary"] == {
        "quarantine_candidate_count": 1,
        "staging_preview_ready_count": 1,
        "address_mapped_count": 1,
        "contact_mapped_count": 1,
        "website_mapped_count": 1,
        "publisher_coordinate_mapped_count": 1,
        "brand_relationship_mapped_count": 0,
        "human_review_queue_ready_count": 1,
        "approval_eligible_count": 0,
        "business_import_eligible_count": 0,
        "map_publication_eligible_count": 0,
        "candidate_rows_written": 0,
        "business_rows_written": 0,
        "map_rows_written": 0,
    }
    row = result["candidates"][0]
    assert row["address"]["postal_code"] == "10011"
    assert row["contact"]["email"] == "store@example.com"
    assert row["staging_envelope"]["record_only"] is True
    assert row["staging_envelope"]["stable_org_key"] == ""
    assert row["staging_preview"]["promotion_gate"]["eligible"] is False
    assert row["candidate_staging_gate"]["eligible"] is True
    assert row["human_review_gate"]["reviewable"] is True
    assert row["human_review_gate"]["approval_eligible"] is False
    assert row["business_import_gate"]["eligible"] is False
    assert row["map_publication_gate"]["eligible"] is False
    assert {
        "legal_approval_missing",
        "source_activation_missing",
        "source_registry_disabled",
    } <= set(row["map_publication_gate"]["reasons"])
    assert result["contract"]["database_accessed"] is False
    assert result["contract"]["network_accessed"] is False
    output_sha = result["artifact_content_sha256"]
    assert output_sha == _sha(
        {
            key: value
            for key, value in result.items()
            if key != "artifact_content_sha256"
        }
    )


def test_manufacturer_source_maps_only_unverified_brand_scope() -> None:
    result = bridge.build_quarantine_staging_plan(
        _artifact("dealer_nikon_us_authorized_imaging"), organization_id=7
    )

    relationships = result["candidates"][0]["brand_relationships"]
    assert relationships == [
        {
            "brand_key": "nikon",
            "relationship_status": "unverified_directory_candidate",
            "source_scope_note": "Nikon Imaging",
            "evidence_url": "https://www.nikonusa.com/where-to-buy/nikon_img_auth_dealers.pdf",
            "requires_human_review": True,
            "claim_status": "descriptive_only",
        }
    ]
    assert result["summary"]["brand_relationship_mapped_count"] == 1
    assert result["claim_boundaries"]["brand_scope_is_viltrox_authorization"] is False


def test_bridge_rejects_unsigned_tampering() -> None:
    artifact = _artifact()
    artifact["sources"][0]["candidates"][0]["address"]["city"] = "Brooklyn"
    with pytest.raises(
        bridge.DealerQuarantineBridgeError,
        match="artifact_content_sha256_mismatch",
    ):
        bridge.build_quarantine_staging_plan(artifact, organization_id=7)


@pytest.mark.parametrize(
    ("scope", "field", "error"),
    [
        ("contract", "legal_approval", "quarantine_contract_legal_approval_invalid"),
        ("source", "source_activation", "source_source_activation_invalid"),
        ("candidate", "promotion_eligible", "candidate_promotion_eligible_invalid"),
    ],
)
def test_bridge_rejects_any_preapproved_or_promotion_enabled_quarantine(
    scope, field, error
) -> None:
    artifact = _artifact()
    if scope == "contract":
        artifact["contract"][field] = True
    elif scope == "source":
        artifact["sources"][0][field] = True
    else:
        candidate = artifact["sources"][0]["candidates"][0]
        candidate[field] = True
        candidate["content_sha256"] = _sha(
            {key: value for key, value in candidate.items() if key != "content_sha256"}
        )
    _resign(artifact)
    with pytest.raises(bridge.DealerQuarantineBridgeError, match=error):
        bridge.build_quarantine_staging_plan(artifact, organization_id=7)


def test_bridge_rejects_registry_drift_and_incomplete_address() -> None:
    drifted = _artifact()
    drifted["input_provenance"]["source_registry_sha256"] = "f" * 64
    _resign(drifted)
    with pytest.raises(
        bridge.DealerQuarantineBridgeError,
        match="source_registry_snapshot_drift",
    ):
        bridge.build_quarantine_staging_plan(drifted, organization_id=7)

    incomplete = _artifact()
    candidate = incomplete["sources"][0]["candidates"][0]
    candidate["address"]["postal_code"] = ""
    candidate["content_sha256"] = _sha(
        {key: value for key, value in candidate.items() if key != "content_sha256"}
    )
    _resign(incomplete)
    with pytest.raises(
        bridge.DealerQuarantineBridgeError,
        match="candidate_address_postal_code_invalid",
    ):
        bridge.build_quarantine_staging_plan(incomplete, organization_id=7)
