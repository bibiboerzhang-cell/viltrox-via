"""Frozen behavior and structural bounds for the Dealer projection split."""
from __future__ import annotations

import ast
import hashlib
import json
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path

from app.domains.commerce import (
    dealer_directory_projection,
    dealer_directory_view,
)
from scripts.vkpi_engineering_health_collect import collect_complexity


AS_OF = datetime(2026, 7, 15, 12, tzinfo=timezone.utc)


def _row() -> dict:
    location_url = "https://dealer.example/stores/new-york"
    product_url = "https://dealer.example/products/viltrox-lens"
    return {
        "id": 7,
        "name": "Example Camera",
        "address": "1 Main St",
        "city": "New York",
        "state": "NY",
        "country": "US",
        "lat": 40.7,
        "lng": -74.0,
        "source_status": "public_listing_verified",
        "source_checked_at": "2026-07-14T12:00:00Z",
        "location_source_url": location_url,
        "brand_listing_url": product_url,
        "phone": "212-555-0100",
        "contact_email": "store@dealer.example",
        "source_id": "dealer_source_example_midtown",
        "stable_org_key": "dealer_org_12345678",
        "stable_location_key": "dealer_loc_12345678",
        "reviewer_id": "staff_7",
        "reviewed_at": "2026-07-14T12:05:00Z",
        "review_contract_version": 1,
        "evidence_json": {
            "claim_status": "descriptive_only",
            "source": {
                "source_id": "dealer_source_example_midtown",
                "source_url": location_url,
                "reviewer_id": "staff_7",
                "value_status": "observed",
            },
            "product": {
                "source_url": product_url,
                "value_status": "observed",
            },
            "coordinate": {
                "provider": "us_census_geocoder",
                "match_level": "exact_address",
                "value_status": "observed",
                "google_derived": False,
            },
        },
        "authorization_status": "authorized_confirmed",
        "authorization_evidence": {
            "official_viltrox_source_url": "https://www.viltrox.com/dealers/example",
            "verified_at": "2026-07-15T11:00:00Z",
        },
        "publication_status": "published",
        "published_at": "2026-07-14T12:05:00Z",
        "viltrox_deployment_status": "deployed",
        "activity_status": "active",
        "activity_page_url": "https://dealer.example/events",
        "website_url": "https://dealer.example/about",
        "social_links_json": [
            {"platform": "YouTube", "url": "https://youtube.com/@example"},
            {"platform": "unsafe", "url": "javascript:alert(1)"},
        ],
        "brand_relationships": [
            {"brand_key": "Sony", "relationship_status": "listed"},
            {"brand_key": "canon", "relationship_status": "listed"},
        ],
        "location_verification_contract_version": 1,
        "canonical_location_status": "official_site_verified",
        "physical_store_status": "verified_physical_store",
        "google_place_verification_status": "pending",
        "canonical_location_checked_by": "internal-reviewer",
        "google_place_evidence_json": {"raw": "private"},
    }


def _digest(value: object) -> str:
    encoded = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def test_project_dealer_keeps_frozen_full_projection() -> None:
    row = _row()
    before = deepcopy(row)

    projected = dealer_directory_view.project_dealer(row, as_of=AS_OF)

    assert _digest(projected) == (
        "ad198e50c99868d25a4bd9bc5f9b889823c4761f50e694aa7a7e871725f093eb"
    )
    assert row == before
    assert projected["brand_codes"] == ["canon", "sony"]
    assert [item["brand_key"] for item in projected["brand_relationships"]] == [
        "canon",
        "sony",
    ]
    assert projected["truth_status"]["viltrox_authorization"] == "confirmed"
    assert "evidence_json" not in projected
    assert "canonical_location_checked_by" not in projected
    assert "google_place_evidence_json" not in projected


def test_dealer_projection_complexity_and_modules_stay_bounded() -> None:
    modules = (dealer_directory_view, dealer_directory_projection)
    all_rows = []
    for module in modules:
        module_path = Path(module.__file__)
        tree = ast.parse(module_path.read_text(encoding="utf-8"))
        all_rows.extend(collect_complexity({str(module_path): tree}))
        assert len(module_path.read_text(encoding="utf-8").splitlines()) <= 800

    assert max(row.cc for row in all_rows) <= 30
    public = next(
        row
        for row in all_rows
        if row.qualified_name == "project_dealer"
        and row.path.endswith("dealer_directory_view.py")
    )
    assert public.cc <= 30
