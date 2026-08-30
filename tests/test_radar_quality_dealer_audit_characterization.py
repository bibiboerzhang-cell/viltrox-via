"""Frozen behavior and structural guards for the Dealer audit split."""
from __future__ import annotations

import ast
import hashlib
import json
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path

import pytest

from app.domains.commerce.dealer_identity import (
    propose_stable_location_key,
    propose_stable_org_key,
)
from app.domains.events import (
    radar_quality,
    radar_quality_audits,
    radar_quality_dealer_audit,
)
from app.domains.events.radar_quality_core import _canonical_source_url
from scripts.vkpi_engineering_health_collect import collect_complexity


AS_OF = datetime(2026, 7, 13, 20, tzinfo=timezone.utc)
CHECKED_AT = "2026-07-13T18:00:00Z"
REPORT_KEYS = [
    "contract",
    "ok",
    "quality_status",
    "claim_status",
    "read_only",
    "network_accessed",
    "database_accessed",
    "business_rows_written",
    "as_of",
    "stale_after_days",
    "counts",
    "coverage",
    "evidence_records",
    "deduplication",
    "identity_proposals",
    "import_gate",
    "claim_boundaries",
    "issue_counts",
    "issues",
]


def _dealer() -> dict:
    org_key = propose_stable_org_key(
        "Example Camera",
        country_code="US",
        official_domain="dealer.example",
    )
    location_key = propose_stable_location_key(
        org_key,
        country_code="US",
        address="1 Main St",
        postal_code="10001",
    )
    return {
        "source_id": "dealer_source_example_midtown",
        "organization_name": "Example Camera",
        "name": "Example Camera · Midtown",
        "official_domain": "dealer.example",
        "stable_org_key": org_key,
        "stable_location_key": location_key,
        "address": "1 Main St",
        "city": "New York",
        "state": "NY",
        "postal_code": "10001",
        "country": "US",
        "location_source_url": "https://dealer.example/stores/midtown",
        "brand_listing_url": "https://dealer.example/brands/viltrox",
        "source_checked_at": CHECKED_AT,
        "source_status": "public_listing_verified",
        "reviewer_id": "staff_7",
        "evidence_scope": "dealer_location_listing",
        "value_status": "observed",
        "authorization_status": "needs_viltrox_confirmation",
        "phone": "+1 212 555 0100",
        "contact_evidence": {
            "phone": {
                "status": "verified",
                "source_url": "https://dealer.example/stores/midtown",
                "checked_at": CHECKED_AT,
                "reviewer_id": "staff_7",
                "evidence_scope": "dealer_contact_field",
                "value_status": "observed",
            }
        },
        "social_evidence": {
            "instagram": {
                "status": "unknown",
                "source_url": "",
                "checked_at": None,
            },
            "youtube": {
                "status": "verified",
                "source_url": "https://youtube.com/@examplecamera",
                "checked_at": CHECKED_AT,
                "reviewer_id": "staff_7",
                "evidence_scope": "dealer_social_profile",
                "value_status": "observed",
            },
        },
        "viltrox_product_evidence": {
            "status": "public_listing_observed",
            "source_url": "https://dealer.example/brands/viltrox",
            "checked_at": CHECKED_AT,
            "reviewer_id": "staff_7",
            "evidence_scope": "dealer_viltrox_product_page",
            "value_status": "observed",
        },
    }


def _manifest(row: dict) -> dict:
    entity_ids = [str(row["stable_location_key"]).strip()]
    source_inventory = [
        {
            "source_id": str(row["source_id"]).strip(),
            "canonical_url": _canonical_source_url(row["location_source_url"]),
        }
    ]
    return {
        "manifest_version": 1,
        "scope": "dealer_locations",
        "denominator": 1,
        "entity_ids": entity_ids,
        "source_inventory": source_inventory,
        "entity_ids_sha256": _digest(entity_ids, sort_keys=True),
        "source_inventory_sha256": _digest(source_inventory, sort_keys=True),
        "as_of": CHECKED_AT,
        "methodology": "Hermetic exact-id fixture inventory.",
        "reviewer_id": "staff_7",
    }


def _digest(value: object, *, sort_keys: bool = False) -> str:
    encoded = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=sort_keys,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _mixed_rows() -> list[object]:
    valid = _dealer()
    conflicting = deepcopy(valid)
    conflicting.update(
        source_id=valid["source_id"],
        location_source_url="https://dealer.example/stores/downtown",
        source_checked_at="2025-01-01T00:00:00Z",
        organization_name="Other Camera",
        name="Other Camera · Downtown",
        address="2 Main St",
        country="usa",
        authorization_status="authorized",
        in_stock=True,
    )
    conflicting["viltrox_product_evidence"] = {
        "status": "unknown",
        "source_url": "http://bad",
        "checked_at": None,
    }
    conflicting["contact_evidence"] = "bad"
    conflicting["social_evidence"] = []
    missing = {
        "name": "  ",
        "address": "",
        "country": "",
        "source_id": "!",
        "location_source_url": "http://bad",
        "authorization_status": "yes",
        "sells_viltrox": True,
    }
    return [valid, conflicting, "invalid", missing]


@pytest.mark.parametrize(
    ("rows_factory", "denominator_factory", "expected_digest"),
    [
        (
            lambda: [_dealer()],
            lambda rows: _manifest(rows[0]),
            "ab3abaed24815c87acb6973657c7e833c2c279059c9554e1a62f97599b39c812",
        ),
        (
            _mixed_rows,
            lambda _rows: 2,
            "790282306a4022761f894bcedbc455dbca3fc58fbef13c5312965829bb2ea156",
        ),
        (
            list,
            lambda _rows: None,
            "007238ca18dc0bb2715929f998495a1d9f81023e40fe45fce824641eafe4039d",
        ),
    ],
)
def test_dealer_audit_keeps_frozen_ordered_output(
    rows_factory, denominator_factory, expected_digest: str
) -> None:
    rows = rows_factory()
    denominator = denominator_factory(rows)
    before_rows = deepcopy(rows)
    before_denominator = deepcopy(denominator)

    report = radar_quality_dealer_audit.audit_dealer_candidates(
        rows,  # type: ignore[arg-type]
        as_of=AS_OF,
        known_location_universe_denominator=denominator,
    )

    assert _digest(report) == expected_digest
    assert list(report) == REPORT_KEYS
    assert rows == before_rows
    assert denominator == before_denominator
    assert [record["candidate_index"] for record in report["evidence_records"]["items"]] == list(
        range(len(rows))
    )
    assert report["claim_status"] == "descriptive_only"
    assert report["read_only"] is True
    assert report["network_accessed"] is False
    assert report["database_accessed"] is False
    assert report["business_rows_written"] == 0


def test_mixed_dealer_audit_preserves_exact_rejections_and_deduplication() -> None:
    report = radar_quality_dealer_audit.audit_dealer_candidates(
        _mixed_rows(),
        as_of=AS_OF,
        known_location_universe_denominator=2,
    )

    assert report["deduplication"] == {
        "mode": "exact_keys_only_no_fuzzy_auto_merge",
        "source_id_key": "source_id",
        "entity_key": "stable_location_key",
        "natural_key_guard": ["casefold(name)", "casefold(address)"],
        "duplicate_source_ids": ["dealer_source_example_midtown"],
        "duplicate_location_keys": ["dealer_loc_40dd62ad85d5b8c5ed9ea21b"],
        "duplicate_natural_keys": [],
    }
    assert report["import_gate"] == {
        "allowed": False,
        "reason": "explicit_identity_or_current_evidence_missing",
        "does_not_prove_global_coverage": True,
    }
    assert report["evidence_records"]["items"][2]["review_status"] == (
        "invalid_candidate_row"
    )
    assert [(item["severity"], item["code"], item["path"]) for item in report["issues"]] == sorted(
        (item["severity"], item["code"], item["path"])
        for item in report["issues"]
    )


@pytest.mark.parametrize("value", [True, False, 0, -1])
def test_dealer_audit_preserves_staleness_threshold_errors(value: object) -> None:
    with pytest.raises(ValueError, match="^stale_after_days must be a positive integer$"):
        radar_quality_dealer_audit.audit_dealer_candidates(
            [_dealer()], as_of=AS_OF, stale_after_days=value  # type: ignore[arg-type]
        )


def test_dealer_audit_preserves_naive_as_of_error() -> None:
    with pytest.raises(ValueError, match="^as_of must include a timezone$"):
        radar_quality_dealer_audit.audit_dealer_candidates(
            [_dealer()], as_of=datetime(2026, 7, 13, 20)
        )


def test_dealer_audit_public_reexports_keep_one_function_object() -> None:
    assert radar_quality.audit_dealer_candidates is radar_quality_audits.audit_dealer_candidates
    assert (
        radar_quality_audits.audit_dealer_candidates
        is radar_quality_dealer_audit.audit_dealer_candidates
    )


def test_dealer_audit_family_complexity_size_and_dependency_are_bounded() -> None:
    modules = [radar_quality_dealer_audit]
    runtime = getattr(radar_quality_dealer_audit, "_runtime_module", None)
    if runtime is not None:
        modules.append(runtime)
    trees = {
        str(Path(module.__file__)):
        ast.parse(Path(module.__file__).read_text(encoding="utf-8"))
        for module in modules
    }
    rows = collect_complexity(trees)
    public = next(
        row for row in rows
        if row.qualified_name == "audit_dealer_candidates"
        and row.path == str(Path(radar_quality_dealer_audit.__file__))
    )

    assert public.cc <= 10
    assert max(row.cc for row in rows) < 50
    assert all(len(Path(module.__file__).read_text(encoding="utf-8").splitlines()) < 800 for module in modules)
    if runtime is not None:
        runtime_source = Path(runtime.__file__).read_text(encoding="utf-8")
        assert "radar_quality_dealer_audit import" not in runtime_source
