from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path

import pytest

from app.domains.commerce import dealer_directory_adapters as adapters
from app.domains.events import candidate_staging


FIXTURES = Path(__file__).parent / "fixtures" / "dealer_directory_adapters"
OBSERVED_AT = "2026-07-15T12:00:00Z"


def _fixture(name: str):
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def _allowed_source(source_id: str) -> dict:
    scopes = {
        "dealer_bestbuy_us_store_directory": "Retailer-owned store identity only",
        "dealer_canon_us_where_to_buy": "Canon Consumer and Home Office products",
        "dealer_mikes_camera_locations_us": "Retailer-owned store identity and location only; no manufacturer authorization is inferred",
        "dealer_omsystem_us_locator": "OM SYSTEM cameras and lenses",
        "dealer_profoto_us_locator": "Profoto professional lighting products",
        "dealer_nikon_us_authorized_imaging": "Nikon Imaging",
    }
    canonical_urls = {
        "dealer_bestbuy_us_store_directory": "https://stores.bestbuy.com/index.html",
        "dealer_canon_us_where_to_buy": "https://www.usa.canon.com/content/dam/canon-assets/authorized-dealers/canon-ad-06-15-26.pdf",
        "dealer_mikes_camera_locations_us": "https://mikescamera.com/contact-info",
        "dealer_omsystem_us_locator": "https://explore.omsystem.com/us/en/store-locator/",
        "dealer_profoto_us_locator": "https://www.profoto.com/us/en/shop/find-dealer/",
        "dealer_nikon_us_authorized_imaging": "https://www.nikonusa.com/where-to-buy/nikon_img_auth_dealers.pdf",
    }
    return {
        "id": source_id,
        "scope": "dealer_discovery_sources",
        "publisher": {
            "dealer_bestbuy_us_store_directory": "Best Buy",
            "dealer_canon_us_where_to_buy": "Canon USA",
            "dealer_mikes_camera_locations_us": "Mike's Camera",
            "dealer_omsystem_us_locator": "OM Digital Solutions Americas",
            "dealer_profoto_us_locator": "Profoto",
            "dealer_nikon_us_authorized_imaging": "Nikon USA",
        }[source_id],
        "manufacturer_authorization_scope": scopes[source_id],
        "canonical_url": canonical_urls[source_id],
        "enabled": True,
        "status": "active",
        "terms_robots_status": "reviewed_allowed",
        "terms_robots_reviewer_id": "staff_7",
        "terms_robots_reviewed_at": "2026-07-15T11:30:00Z",
        "requires_human_review": True,
        "direct_import_allowed": False,
    }


def _allow(monkeypatch, source_id: str) -> None:
    monkeypatch.setattr(adapters, "_registered_source", lambda _value: _allowed_source(source_id))


def test_contract_manifest_separates_source_formats_and_never_imports():
    contract = adapters.adapter_contracts()

    assert contract["contract"] == {
        "id": "vkpi.us_dealer.directory_candidate_adapter",
        "version": 2,
    }
    assert contract["formats"]["sitemap_html"]["sources"] == [
        "dealer_bestbuy_us_store_directory"
    ]
    assert set(contract["formats"]["json_locator"]["sources"]) == {
        "dealer_omsystem_us_locator",
        "dealer_profoto_us_locator",
    }
    assert contract["formats"]["tabular_pdf"]["sources"] == [
        "dealer_canon_us_where_to_buy",
        "dealer_nikon_us_authorized_imaging",
    ]
    assert set(contract["formats"]["published_html_rows"]["sources"]) == {
        "dealer_adorama_nyc_store_us",
        "dealer_blackmagic_us_resellers",
        "dealer_bc_camera_location_us",
        "dealer_bedfords_store_locations_us",
        "dealer_bh_nyc_superstore_us",
        "dealer_competitive_camera_location_us",
        "dealer_dans_store_us",
        "dealer_district_camera_locations_us",
        "dealer_dodd_store_locator_us",
        "dealer_fujifilm_us_shop",
        "dealer_godox_us_authorized_distributors",
        "dealer_glazers_store_us",
        "dealer_hasselblad_us_locator",
        "dealer_hunts_store_locations_us",
        "dealer_leica_us_locator",
        "dealer_microcenter_us_store_directory",
        "dealer_mikes_camera_locations_us",
        "dealer_natcam_store_us",
        "dealer_pauls_photo_location_us",
        "dealer_panasonic_us_authorized",
        "dealer_phaseone_us_partner_locator",
        "dealer_precision_store_locations_us",
        "dealer_pro_photo_supply_location_us",
        "dealer_roberts_store_us",
        "dealer_rockbrook_locations_us",
        "dealer_samys_retail_locations_us",
        "dealer_sigma_us_authorized",
        "dealer_sony_us_where_to_buy",
        "dealer_tamron_americas_locator",
        "dealer_unique_store_locations_us",
    }
    assert contract["source_gate"]["terms_robots_status_required"] == "reviewed_allowed"
    assert contract["candidate_truth_defaults"] == {
        "viltrox_authorization": "unknown",
        "viltrox_product_page": "unknown",
        "current_inventory": "unknown",
    }
    assert len(contract["source_readiness"]) == 35
    assert all(row["format_mapped"] is True for row in contract["source_readiness"])
    assert all(
        row["source_fixture_verified"] is False
        and row["terms_robots_reviewed"] is False
        and row["snapshot_import_readiness"] == "blocked"
        and row["direct_business_import"] is False
        for row in contract["source_readiness"]
    )
    assert all(
        {
            "source_specific_fixture_not_verified",
            "source_registry_disabled",
            "terms_robots_review_pending",
        }
        <= set(row["blockers"])
        for row in contract["source_readiness"]
    )
    assert contract["network_accessed"] is False
    assert contract["database_accessed"] is False
    assert contract["provider_calls"] == 0
    assert contract["scheduler_enabled"] is False
    assert contract["business_rows_written"] == 0
    readiness = contract["registry_adapter_readiness"]
    assert readiness["registered_source_count"] == 35
    assert readiness["adapter_source_count"] == 35
    assert readiness["mapped_adapter_source_count"] == 35
    assert readiness["adapter_sources_not_registered"] == []
    assert readiness["sources_without_mapped_adapter"] == []
    assert readiness["all_registered_sources_have_mapped_adapter"] is True
    assert readiness["source_fixture_verified_count"] == 0
    assert len(readiness["sources_without_source_fixture_verification"]) == 35
    assert readiness["all_registered_sources_have_source_fixture_verification"] is False
    assert readiness["all_registered_sources_have_verified_adapter"] is False
    assert readiness["source_coverage_is_not_entity_coverage"] is True
    assert {
        "dealer_blackmagic_us_resellers",
        "dealer_fujifilm_us_shop",
        "dealer_godox_us_authorized_distributors",
        "dealer_hasselblad_us_locator",
        "dealer_leica_us_locator",
        "dealer_panasonic_us_authorized",
        "dealer_phaseone_us_partner_locator",
        "dealer_sigma_us_authorized",
        "dealer_sony_us_where_to_buy",
        "dealer_tamron_americas_locator",
    } <= set(readiness["sources_without_verified_adapter"])
    assert len(readiness["sources_without_verified_adapter"]) == 35
    assert readiness["mapping_blocker"] is None
    assert readiness["activation_blocker"] == (
        "source_specific_fixture_and_terms_robots_review_required"
    )
    assert readiness["blocker"] == (
        "source_specific_fixture_and_terms_robots_review_required"
    )


@pytest.mark.parametrize(
    "source_id",
    [
        "dealer_blackmagic_us_resellers",
        "dealer_fujifilm_us_shop",
        "dealer_godox_us_authorized_distributors",
        "dealer_hasselblad_us_locator",
        "dealer_leica_us_locator",
        "dealer_panasonic_us_authorized",
        "dealer_phaseone_us_partner_locator",
        "dealer_sigma_us_authorized",
        "dealer_sony_us_where_to_buy",
        "dealer_tamron_americas_locator",
    ],
)
def test_new_publisher_page_mappings_remain_fail_closed_without_review(source_id):
    result = adapters.adapt_registered_snapshot(
        source_id,
        {
            "extraction": {
                "media_type": "text/html",
                "document_url": "https://untrusted.invalid/never-consulted",
                "document_sha256": "d" * 64,
                "extractor": "offline-contract-test-v1",
                "rows": [],
            }
        },
        observed_at=OBSERVED_AT,
    )

    assert result["adapter"] == "published_html_rows"
    assert result["ok"] is False
    assert result["status"] == "blocked"
    assert result["candidates"] == []
    assert "adapter_not_registered" not in result["issues"]
    assert {
        "source_registry_disabled",
        "terms_robots_not_reviewed_allowed",
        "terms_robots_reviewer_missing",
        "terms_robots_review_timestamp_invalid",
        "source_registry_not_active",
    } <= set(result["issues"])
    assert result["contract"]["network_accessed"] is False
    assert result["contract"]["database_accessed"] is False
    assert result["contract"]["candidate_rows_written"] == 0
    assert result["contract"]["business_rows_written"] == 0


@pytest.mark.parametrize(
    ("source_id", "fixture_name"),
    [
        ("dealer_bestbuy_us_store_directory", "bestbuy_sitemap_html.json"),
        ("dealer_canon_us_where_to_buy", "canon_pdf_organization_rows.json"),
        ("dealer_mikes_camera_locations_us", "mikes_published_html_rows.json"),
        ("dealer_omsystem_us_locator", "omsystem_locator.json"),
        ("dealer_profoto_us_locator", "profoto_locator.json"),
        ("dealer_nikon_us_authorized_imaging", "nikon_pdf_rows.json"),
    ],
)
def test_checked_in_registry_is_fail_closed_and_emits_no_candidates(source_id, fixture_name):
    result = adapters.adapt_registered_snapshot(
        source_id,
        _fixture(fixture_name),
        observed_at=OBSERVED_AT,
    )

    assert result["ok"] is False
    assert result["status"] == "blocked"
    assert result["candidates"] == []
    assert "source_registry_disabled" in result["issues"]
    assert "terms_robots_not_reviewed_allowed" in result["issues"]
    assert "terms_robots_reviewer_missing" in result["issues"]
    assert "terms_robots_review_timestamp_invalid" in result["issues"]
    assert result["contract"]["network_accessed"] is False
    assert result["contract"]["database_accessed"] is False
    assert result["contract"]["provider_calls"] == 0
    assert result["contract"]["candidate_rows_written"] == 0
    assert result["contract"]["business_rows_written"] == 0


def test_bestbuy_sitemap_html_uses_only_listed_store_json_ld(monkeypatch):
    source_id = "dealer_bestbuy_us_store_directory"
    _allow(monkeypatch, source_id)
    result = adapters.adapt_registered_snapshot(
        source_id,
        _fixture("bestbuy_sitemap_html.json"),
        observed_at=OBSERVED_AT,
    )

    assert result["ok"] is True
    assert result["adapter"] == "sitemap_html"
    candidate = result["candidates"][0]
    assert candidate["source_store_id"] == "1028"
    assert candidate["organization_name"] == "Best Buy"
    assert candidate["branch_name"] == "Best Buy Union Square"
    assert candidate["address"]["formatted"] == "52 E 14th St, New York, NY 10003, US"
    assert candidate["phone"] == "(212) 466-4789"
    assert candidate["lat"] == 40.734
    assert candidate["lng"] == -73.9901
    assert len(candidate["source_artifact_sha256"]) == 64
    assert candidate["source_artifact_kind"] == "captured_html_page"
    assert candidate["source_provenance"] == {
        "source_registry_id": source_id,
        "source_url": "https://stores.bestbuy.com/ny/new-york/52-e-14th-st-1028.html",
        "publisher_bound": True,
        "observed_at": OBSERVED_AT,
        "artifact_sha256": candidate["source_artifact_sha256"],
        "artifact_kind": "captured_html_page",
        "extractor": "json_ld_html_parser_v1",
        "terms_robots_review": {
            "status": "reviewed_allowed",
            "reviewer_id": "staff_7",
            "reviewed_at": "2026-07-15T11:30:00Z",
        },
    }
    assert len(candidate["content_sha256"]) == 64
    assert candidate["staging_preview"]["record_only"] is True
    assert candidate["staging_preview"]["stable_location_key"] == ""
    assert candidate["promotion_gate"]["eligible"] is False

    staging = candidate_staging.preview_candidate(
        candidate["staging_preview"],
        candidate_type="dealer_location",
        organization_id=7,
    )
    assert staging["candidate"]["content_sha256"] == candidate["content_sha256"]
    assert staging["promotion_gate"]["eligible"] is False


@pytest.mark.parametrize(
    ("source_id", "fixture_name", "store_id", "scope"),
    [
        (
            "dealer_omsystem_us_locator",
            "omsystem_locator.json",
            "om-nyc-001",
            "OM SYSTEM cameras and lenses",
        ),
        (
            "dealer_profoto_us_locator",
            "profoto_locator.json",
            "pro-la-042",
            "Profoto professional lighting products",
        ),
    ],
)
def test_json_locator_normalizes_records_without_upgrading_viltrox_truth(
    monkeypatch, source_id, fixture_name, store_id, scope
):
    _allow(monkeypatch, source_id)
    result = adapters.adapt_registered_snapshot(
        source_id,
        _fixture(fixture_name),
        observed_at=OBSERVED_AT,
    )

    assert result["ok"] is True
    candidate = result["candidates"][0]
    assert candidate["source_store_id"] == store_id
    assert candidate["observed_at"] == OBSERVED_AT
    assert candidate["address"]["country_code"] == "US"
    assert candidate["source_url"] == _allowed_source(source_id)["canonical_url"].rstrip("/")
    assert candidate["site_url"] != candidate["source_url"]
    assert candidate["truth_dimensions"] == {
        "manufacturer_authorization_scope": scope,
        "viltrox_authorization": "unknown",
        "viltrox_product_page": "unknown",
        "current_inventory": "unknown",
    }
    assert candidate["candidate_only"] is True
    assert result["direct_business_import"] is False
    assert result["promotion_gate"]["business_table_promotion_available"] is False


def test_nikon_pdf_contract_accepts_only_pre_extracted_hashed_rows(monkeypatch):
    source_id = "dealer_nikon_us_authorized_imaging"
    _allow(monkeypatch, source_id)
    fixture = _fixture("nikon_pdf_rows.json")
    result = adapters.adapt_registered_snapshot(
        source_id,
        fixture,
        observed_at=OBSERVED_AT,
    )

    assert result["ok"] is True
    assert result["adapter"] == "tabular_pdf"
    assert result["candidates"][0]["source_store_id"] == "nikon-wa-007"
    assert result["candidates"][0]["source_url"].endswith("nikon_img_auth_dealers.pdf")
    assert result["candidates"][0]["source_document_sha256"] == "a" * 64
    assert result["candidates"][0]["source_extractor"] == "tabula-fixture-v1"

    unsafe = deepcopy(fixture)
    unsafe["extraction"]["pdf_bytes"] = "not accepted"
    blocked = adapters.adapt_registered_snapshot(
        source_id,
        unsafe,
        observed_at=OBSERVED_AT,
    )
    assert blocked["candidates"] == []
    assert blocked["issues"] == ["adapter_accepts_extracted_rows_only"]


def test_canon_pdf_is_an_organization_universe_not_map_locations(monkeypatch):
    source_id = "dealer_canon_us_where_to_buy"
    _allow(monkeypatch, source_id)

    result = adapters.adapt_registered_snapshot(
        source_id,
        _fixture("canon_pdf_organization_rows.json"),
        observed_at=OBSERVED_AT,
    )

    assert result["ok"] is False
    assert result["status"] == "partial_candidate_snapshot"
    assert result["candidates"] == []
    assert result["issues"] == ["rows[0]:complete_us_address_required"]
    assert result["direct_business_import"] is False


def test_published_location_page_rows_require_exact_hashed_offline_document(monkeypatch):
    source_id = "dealer_mikes_camera_locations_us"
    _allow(monkeypatch, source_id)
    fixture = _fixture("mikes_published_html_rows.json")

    result = adapters.adapt_registered_snapshot(
        source_id,
        fixture,
        observed_at=OBSERVED_AT,
    )

    assert result["ok"] is True
    assert result["adapter"] == "published_html_rows"
    assert len(result["candidates"]) == 1
    candidate = result["candidates"][0]
    assert candidate["source_store_id"] == "mikes-co-boulder"
    assert candidate["organization_name"] == "Mike's Camera"
    assert candidate["branch_name"] == "Boulder"
    assert candidate["address"]["formatted"] == "2500 Pearl Street, Boulder, CO 80302, US"
    assert candidate["phone"] == "(303) 443-1715"
    assert candidate["source_document_sha256"] == "b" * 64
    assert candidate["truth_dimensions"] == {
        "manufacturer_authorization_scope": "Retailer-owned store identity and location only; no manufacturer authorization is inferred",
        "viltrox_authorization": "unknown",
        "viltrox_product_page": "unknown",
        "current_inventory": "unknown",
    }

    raw_html = deepcopy(fixture)
    raw_html["extraction"]["html"] = "<html>not accepted</html>"
    blocked = adapters.adapt_registered_snapshot(
        source_id,
        raw_html,
        observed_at=OBSERVED_AT,
    )
    assert blocked["candidates"] == []
    assert blocked["issues"] == ["adapter_accepts_extracted_rows_only"]

    cross_domain = deepcopy(fixture)
    cross_domain["extraction"]["document_url"] = "https://hostile.example/locations"
    blocked = adapters.adapt_registered_snapshot(
        source_id,
        cross_domain,
        observed_at=OBSERVED_AT,
    )
    assert blocked["candidates"] == []
    assert blocked["issues"] == ["html_document_not_bound_to_registered_publisher"]


def test_json_locator_cross_domain_source_never_replaces_publisher_provenance(monkeypatch):
    source_id = "dealer_omsystem_us_locator"
    _allow(monkeypatch, source_id)
    fixture = _fixture("omsystem_locator.json")
    fixture["records"][0]["source_url"] = "https://hostile.example/forged-observation"

    result = adapters.adapt_registered_snapshot(
        source_id,
        fixture,
        observed_at=OBSERVED_AT,
    )

    candidate = result["candidates"][0]
    assert candidate["source_url"] == "https://explore.omsystem.com/us/en/store-locator"
    assert candidate["site_url"] == "https://example-camera.invalid/locations/downtown"
    assert "hostile.example" not in json.dumps(candidate)


def test_bestbuy_store_page_must_be_on_registered_or_allowlisted_publisher_host(monkeypatch):
    source_id = "dealer_bestbuy_us_store_directory"
    _allow(monkeypatch, source_id)
    fixture = _fixture("bestbuy_sitemap_html.json")
    hostile = "https://hostile.example/ny/new-york/store-1028.html"
    fixture["sitemap_urls"] = [hostile]
    fixture["pages"][0]["url"] = hostile

    result = adapters.adapt_registered_snapshot(
        source_id,
        fixture,
        observed_at=OBSERVED_AT,
    )

    assert result["candidates"] == []
    assert result["issues"] == ["pages[0]:publisher_observation_url_not_allowlisted"]


def test_nikon_pdf_document_identity_must_match_registered_publisher(monkeypatch):
    source_id = "dealer_nikon_us_authorized_imaging"
    _allow(monkeypatch, source_id)
    fixture = _fixture("nikon_pdf_rows.json")
    fixture["extraction"]["document_url"] = "https://hostile.example/forged.pdf"

    result = adapters.adapt_registered_snapshot(
        source_id,
        fixture,
        observed_at=OBSERVED_AT,
    )

    assert result["candidates"] == []
    assert result["issues"] == ["pdf_document_not_bound_to_registered_publisher"]


@pytest.mark.parametrize(
    ("field", "value", "reason"),
    [
        ("enabled", False, "source_registry_disabled"),
        ("status", "hold", "source_registry_not_active"),
        ("terms_robots_status", "pending_review", "terms_robots_not_reviewed_allowed"),
        ("terms_robots_reviewer_id", None, "terms_robots_reviewer_missing"),
        ("terms_robots_reviewed_at", "", "terms_robots_review_timestamp_invalid"),
        ("requires_human_review", False, "candidate_human_review_not_required"),
        ("direct_import_allowed", True, "direct_business_import_must_remain_disabled"),
    ],
)
def test_each_registry_gate_fails_closed(monkeypatch, field, value, reason):
    source_id = "dealer_omsystem_us_locator"
    source = _allowed_source(source_id)
    source[field] = value
    monkeypatch.setattr(adapters, "_registered_source", lambda _value: source)

    result = adapters.adapt_registered_snapshot(
        source_id,
        _fixture("omsystem_locator.json"),
        observed_at=OBSERVED_AT,
    )

    assert result["status"] == "blocked"
    assert result["candidates"] == []
    assert reason in result["issues"]


def test_incomplete_address_is_not_promoted_to_a_candidate(monkeypatch):
    source_id = "dealer_omsystem_us_locator"
    _allow(monkeypatch, source_id)
    fixture = _fixture("omsystem_locator.json")
    del fixture["records"][0]["address"]["postal_code"]

    result = adapters.adapt_registered_snapshot(
        source_id,
        fixture,
        observed_at=OBSERVED_AT,
    )

    assert result["ok"] is False
    assert result["status"] == "partial_candidate_snapshot"
    assert result["candidates"] == []
    assert result["issues"] == ["rows[0]:complete_us_address_required"]


def test_normalization_and_content_hash_are_deterministic(monkeypatch):
    source_id = "dealer_profoto_us_locator"
    _allow(monkeypatch, source_id)
    fixture = _fixture("profoto_locator.json")

    first = adapters.adapt_registered_snapshot(source_id, fixture, observed_at=OBSERVED_AT)
    second = adapters.adapt_registered_snapshot(source_id, fixture, observed_at=OBSERVED_AT)

    assert first == second
    assert first["candidates"][0]["content_sha256"] == second["candidates"][0]["content_sha256"]
    assert "authorization_status" not in first["candidates"][0]["staging_preview"]["candidate_payload"]
    assert "inventory_status" not in first["candidates"][0]["staging_preview"]["candidate_payload"]


def test_full_state_names_dotted_dc_and_zip9_normalize_before_candidate_identity(monkeypatch):
    source_id = "dealer_omsystem_us_locator"
    _allow(monkeypatch, source_id)
    fixture = _fixture("omsystem_locator.json")
    row = fixture["records"][0]
    row["address"]["state"] = "District of Columbia"
    row["address"]["postal_code"] = "20001 1234"
    row["address"]["city"] = "Washington"
    row["address"]["address1"] = "1 First St NE"

    result = adapters.adapt_registered_snapshot(
        source_id,
        fixture,
        observed_at=OBSERVED_AT,
    )

    assert result["ok"] is True
    candidate = result["candidates"][0]
    assert candidate["address"]["state"] == "DC"
    assert candidate["address"]["postal_code"] == "20001-1234"
    assert candidate["address"]["formatted"].endswith("Washington, DC 20001-1234, US")

    row["address"]["state"] = "D.C."
    second = adapters.adapt_registered_snapshot(
        source_id,
        fixture,
        observed_at=OBSERVED_AT,
    )
    assert second["candidates"][0]["address"]["state"] == "DC"

    row["address"]["postal_code"] = "20001abcd"
    rejected = adapters.adapt_registered_snapshot(
        source_id,
        fixture,
        observed_at=OBSERVED_AT,
    )
    assert rejected["candidates"] == []
    assert rejected["issues"] == ["rows[0]:complete_us_address_required"]


def test_idless_locator_rows_get_distinct_source_local_exact_identities(monkeypatch):
    source_id = "dealer_omsystem_us_locator"
    _allow(monkeypatch, source_id)
    fixture = _fixture("omsystem_locator.json")
    first = fixture["records"][0]
    first.pop("storeId")
    second = deepcopy(first)
    second["name"] = "Example Camera Uptown"
    second["address"]["address1"] = "999 Broadway"
    second["address"]["postal_code"] = "10010"
    fixture["records"].append(second)

    result = adapters.adapt_registered_snapshot(
        source_id,
        fixture,
        observed_at=OBSERVED_AT,
    )

    assert result["ok"] is True
    assert len(result["candidates"]) == 2
    source_store_ids = {item["source_store_id"] for item in result["candidates"]}
    source_entity_keys = {item["source_entity_key"] for item in result["candidates"]}
    assert len(source_store_ids) == 2
    assert all(value.startswith("derived_") for value in source_store_ids)
    assert len(source_entity_keys) == 2
    assert all(
        item["staging_preview"]["stable_location_key"] == ""
        for item in result["candidates"]
    )


def test_duplicate_and_conflicting_source_ids_fail_closed_without_double_candidate(monkeypatch):
    source_id = "dealer_omsystem_us_locator"
    _allow(monkeypatch, source_id)
    duplicate = _fixture("omsystem_locator.json")
    duplicate["records"].append(deepcopy(duplicate["records"][0]))

    duplicate_result = adapters.adapt_registered_snapshot(
        source_id,
        duplicate,
        observed_at=OBSERVED_AT,
    )

    assert duplicate_result["ok"] is False
    assert duplicate_result["status"] == "partial_candidate_snapshot"
    assert len(duplicate_result["candidates"]) == 1
    assert duplicate_result["issues"] == ["rows[1]:duplicate_source_entity_key"]

    conflicting = _fixture("omsystem_locator.json")
    changed = deepcopy(conflicting["records"][0])
    changed["address"]["address1"] = "999 Broadway"
    conflicting["records"].append(changed)
    conflict_result = adapters.adapt_registered_snapshot(
        source_id,
        conflicting,
        observed_at=OBSERVED_AT,
    )

    assert conflict_result["ok"] is False
    assert len(conflict_result["candidates"]) == 1
    assert conflict_result["issues"] == ["rows[1]:conflicting_source_entity_key"]
