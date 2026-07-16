from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path

from app.api.routers import vkpi_dealers, vkpi_event_radar
from app.domains.events import us_coverage_registry


SOURCE_IDENTITY_FIXTURE = (
    Path(__file__).parent
    / "fixtures"
    / "us_dealer_source_registry"
    / "official_source_identity_review.json"
)


def test_registry_is_read_only_disabled_and_truth_bounded():
    report = us_coverage_registry.audit_registry()

    assert report["ok"] is True
    assert report["contract"] == {
        "id": "vkpi.us_event_dealer.source_registry",
        "version": 2,
        "read_only": True,
        "network_accessed": False,
        "database_accessed": False,
        "business_rows_written": 0,
    }
    assert report["counts"] == {
        "event_sources": 72,
        "dealer_discovery_sources": 34,
        "dealer_official_ingest_sources": 2,
        "event_source_kinds": {
            "association_directory": 2,
            "brand_event": 10,
            "community_calendar": 9,
            "dealer_event": 18,
            "major_expo": 15,
            "photo_club": 5,
            "school_calendar": 9,
            "university_calendar": 4,
        },
        "dealer_source_kinds": {
            "manufacturer_dealer_directory": 13,
            "retailer_location_directory": 21,
        },
        "dealer_official_ingest_kinds": {
            "company_feed": 1,
            "manual_official_entry": 1,
        },
        "event_source_jurisdictions": 51,
        "dealer_source_jurisdictions": 51,
        "dealer_discovery_scopes": 15,
        "dealer_manufacturer_scopes": 13,
        "enabled": 0,
        "direct_import_allowed": 0,
    }
    assert report["full_us_coverage"] is False
    assert report["global_denominator"] is None
    assert report["global_coverage_rate"] is None
    assert report["claim_status"] == "descriptive_only"
    assert report["import_gate"]["allowed"] is False
    assert all(row["status"] == "hold" for row in report["event_sources"])
    assert all(row["enabled"] is False for row in report["event_sources"])
    assert all(
        row["status"] == "awaiting_review"
        and row["enabled"] is False
        and row["terms_robots_status"] == "pending_review"
        for row in report["dealer_discovery_sources"]
    )
    assert all(
        row["status"] == "awaiting_review"
        and row["enabled"] is False
        and row["terms_robots_status"] == "pending_review"
        for row in report["dealer_official_ingest_sources"]
    )
    assert all(row["direct_import_allowed"] is False for row in report["dealer_discovery_sources"])


def test_manufacturer_directories_never_upgrade_viltrox_truth():
    report = us_coverage_registry.dealer_registry()

    assert report["event_sources"] == []
    assert report["dealer_discovery_sources"]
    for row in report["dealer_discovery_sources"]:
        assert row["candidate_only"] is True
        assert row["site_has_viltrox_product"] == "unknown"
        assert row["viltrox_authorized"] == "unknown"
        assert row["viltrox_authorization_evidence"] is False
        assert row["manufacturer_authorization_scope"]
        assert "Viltrox" not in row["manufacturer_authorization_scope"]
    assert report["claim_boundaries"] == {
        "manufacturer_authorization_proves_viltrox_authorization": False,
        "dealer_candidate_proves_viltrox_product_presence": False,
        "product_page_proves_current_inventory": False,
        "registered_sources_equal_all_us_sources": False,
        "source_jurisdiction_coverage_proves_entity_coverage": False,
        "registered_event_source_proves_viltrox_participation": False,
        "official_ingest_entry_proves_feed_received": False,
        "company_feed_entry_proves_authorized_dealers": False,
        "manual_entry_proves_viltrox_authorization": False,
    }


def test_registry_rejects_duplicate_or_unsafe_sources():
    payload = us_coverage_registry.load_registry()
    duplicate = deepcopy(payload["event_sources"][0])
    payload["event_sources"].append(duplicate)
    payload["dealer_discovery_sources"][0]["canonical_url"] = "http://unsafe.example/dealers"
    payload["full_us_coverage"] = True

    report = us_coverage_registry.audit_registry(payload)
    codes = {item["code"] for item in report["issues"]}

    assert report["ok"] is False
    assert {
        "source_id_invalid_or_duplicate",
        "source_url_invalid_or_duplicate",
        "full_us_coverage_forbidden",
    } <= codes
    assert report["import_gate"]["allowed"] is False


def test_authenticated_router_views_remain_read_only():
    event = vkpi_event_radar.event_radar_us_source_registry(staff={"id": 1})
    dealer = vkpi_dealers.dealer_us_source_registry(staff={"id": 1})

    assert len(event["event_sources"]) == 72
    assert event["dealer_discovery_sources"] == []
    assert event["dealer_official_ingest_sources"] == []
    assert len(dealer["dealer_discovery_sources"]) == 34
    assert len(dealer["dealer_official_ingest_sources"]) == 2
    assert dealer["event_sources"] == []
    assert dealer["adapter_readiness"]["registered_source_count"] == 34
    assert dealer["adapter_readiness"]["adapter_source_count"] == 34
    assert dealer["adapter_readiness"]["mapped_adapter_source_count"] == 34
    assert dealer["adapter_readiness"]["sources_without_mapped_adapter"] == []
    assert dealer["adapter_readiness"]["all_registered_sources_have_mapped_adapter"] is True
    assert dealer["adapter_readiness"]["readiness_level"] == (
        "format_mapping_only_not_source_fixture_verified"
    )
    assert dealer["adapter_readiness"]["source_fixture_verified_count"] == 0
    assert len(dealer["adapter_readiness"]["sources_without_verified_adapter"]) == 34
    assert dealer["adapter_readiness"]["all_registered_sources_have_verified_adapter"] is False
    assert dealer["adapter_readiness"]["activation_blocker"] == (
        "source_specific_fixture_and_terms_robots_review_required"
    )
    assert len(dealer["adapter_source_readiness"]) == 34
    assert all(
        row["snapshot_import_readiness"] == "blocked"
        for row in dealer["adapter_source_readiness"]
    )
    assert dealer["reviewed_persistence_readiness"]["read_only"] is True
    assert dealer["reviewed_persistence_readiness"]["business_rows_written"] == 0
    assert event["contract"]["business_rows_written"] == 0
    assert dealer["contract"]["database_accessed"] is True
    assert dealer["contract"]["business_rows_written"] == 0


def test_registry_covers_major_expos_retailer_events_schools_and_dealer_universes():
    report = us_coverage_registry.audit_registry()
    event_ids = {row["id"] for row in report["event_sources"]}
    dealer_ids = {row["id"] for row in report["dealer_discovery_sources"]}
    explicit_states = {
        state
        for row in report["event_sources"]
        for state in row.get("state_codes", [])
    }

    assert {
        "major_imaging_usa_us",
        "major_wppi_expo_us",
        "major_nab_show_us",
        "major_cine_gear_us",
        "major_infocomm_us",
        "major_bild_expo_us",
        "major_siggraph_us",
        "major_namm_show_us",
        "major_vidcon_anaheim_us",
        "dealer_bh_event_space_us",
        "dealer_adorama_events_us",
        "dealer_hunts_photo_calendar_us",
        "dealer_unique_university_us",
        "university_artcenter_events_us",
        "brand_nikon_tour_us",
        "brand_sony_alpha_universe_calendar_us",
        "brand_sony_creative_space_tour_us",
        "brand_canon_cps_events_us",
        "brand_fujifilm_events_us",
        "brand_leica_events_us",
        "brand_sigma_events_us",
        "brand_tamron_events_us",
        "brand_omsystem_events_us",
        "school_maine_media_photography_us",
        "school_santa_fe_online_workshops_us",
        "school_lacp_event_calendar_us",
        "school_hcp_events_us",
        "dealer_district_camera_classes_us",
        "community_nanpa_calendar_us",
        "major_psa_photo_festival_us",
        "major_shutterfest_us",
        "major_fotofest_biennial_us",
        "dealer_rockbrook_class_calendar_us",
        "dealer_bc_camera_classes_us",
        "school_pcnw_workshops_us",
        "school_princeton_photo_workshop_us",
        "community_center_photographic_art_events_us",
        "major_neaf_neaic_us",
        "major_aipad_photography_show_us",
        "dealer_kenmore_camera_events_us",
        "dealer_pictureline_events_us",
        "major_photoville_festival_us",
        "school_texas_school_professional_photography_us",
        "community_out_of_chicago_conferences_us",
        "university_usc_sca_events_us",
        "community_nppa_events_us",
        "community_smpte_events_us",
        "photo_neccc_events_us",
        "photo_texas_photographic_society_us",
    } <= event_ids
    assert {
        "dealer_canon_us_where_to_buy",
        "dealer_nikon_us_authorized_imaging",
        "dealer_sony_us_where_to_buy",
        "dealer_fujifilm_us_shop",
        "dealer_panasonic_us_authorized",
        "dealer_omsystem_us_locator",
        "dealer_leica_us_locator",
        "dealer_blackmagic_us_resellers",
        "dealer_tamron_americas_locator",
        "dealer_sigma_us_authorized",
        "dealer_hasselblad_us_locator",
        "dealer_profoto_us_locator",
        "dealer_phaseone_us_partner_locator",
        "dealer_bestbuy_us_store_directory",
        "dealer_microcenter_us_store_directory",
        "dealer_bh_nyc_superstore_us",
        "dealer_adorama_nyc_store_us",
        "dealer_samys_retail_locations_us",
        "dealer_glazers_store_us",
        "dealer_unique_store_locations_us",
        "dealer_hunts_store_locations_us",
        "dealer_precision_store_locations_us",
        "dealer_roberts_store_us",
        "dealer_dans_store_us",
        "dealer_natcam_store_us",
        "dealer_mikes_camera_locations_us",
        "dealer_pauls_photo_location_us",
        "dealer_bedfords_store_locations_us",
        "dealer_dodd_store_locator_us",
        "dealer_pro_photo_supply_location_us",
        "dealer_bc_camera_location_us",
        "dealer_rockbrook_locations_us",
        "dealer_competitive_camera_location_us",
        "dealer_district_camera_locations_us",
    } == dealer_ids
    for row in report["dealer_discovery_sources"]:
        assert set(row.get("related_event_source_ids", [])) <= event_ids
    assert len(explicit_states) == 51
    assert all(row["requires_human_review"] is True for row in report["event_sources"])
    assert all(row["candidate_only"] is True for row in report["dealer_discovery_sources"])

    event_rows = {row["id"]: row for row in report["event_sources"]}
    assert event_rows["dealer_bc_camera_classes_us"]["current_feed_state"] == (
        "degraded_empty_no_dated_rows"
    )
    assert event_rows["major_neaf_neaic_us"]["current_feed_state"] == (
        "quarantined_mixed_edition"
    )
    assert event_rows["major_aipad_photography_show_us"]["current_feed_state"] == (
        "rollover_date_pending"
    )
    assert event_rows["dealer_pictureline_events_us"]["current_feed_state"] == (
        "degraded_program_page_no_visible_dated_rows"
    )
    assert event_rows["community_smpte_events_us"]["current_feed_state"] == (
        "review_required_mixed_display_year_metadata"
    )
    assert event_rows["photo_neccc_events_us"]["current_feed_state"] == (
        "publisher_surface_challenge_interstitial"
    )
    assert all(
        event_rows[source_id]["candidate_generation_allowed"] is False
        for source_id in (
            "dealer_bc_camera_classes_us",
            "major_neaf_neaic_us",
            "major_aipad_photography_show_us",
            "dealer_pictureline_events_us",
            "community_smpte_events_us",
            "photo_neccc_events_us",
        )
    )


def test_source_jurisdiction_matrices_are_complete_but_never_entity_coverage_claims():
    report = us_coverage_registry.audit_registry()

    for scope in ("event_sources", "dealer_discovery_sources"):
        matrix = report["source_jurisdiction_matrix"][scope]
        assert matrix["scope"] == "registered_source_discovery_jurisdictions_only"
        assert matrix["covered_count"] == 51
        assert matrix["jurisdiction_count"] == 51
        assert matrix["missing_states_dc"] == []
        assert matrix["source_discovery_rate"] == 1.0
        assert matrix["extracted_candidate_count"] is None
        assert matrix["verified_business_row_count"] is None
        assert matrix["entity_coverage_rate"] is None
        assert matrix["claim_status"] == "descriptive_only"
        assert "not a count of extracted events" in matrix["truth_note"]


def test_registry_rejects_invalid_or_duplicate_state_codes():
    payload = us_coverage_registry.load_registry()
    payload["event_sources"][0]["state_codes"] = ["CA", "CA", "XX"]

    report = us_coverage_registry.audit_registry(payload)
    codes = {item["code"] for item in report["issues"]}

    assert report["ok"] is False
    assert {"state_code_invalid", "state_code_duplicate"} <= codes


def test_event_discovery_entries_point_to_publisher_event_surfaces():
    report = us_coverage_registry.audit_registry()
    urls = {row["id"]: row["canonical_url"] for row in report["event_sources"]}

    assert urls["school_icp_events_us"] == "https://www.icp.org/events"
    assert urls["dealer_bh_event_space_us"] == "https://www.bhphotovideo.com/find/EventSpace.jsp"
    assert urls["dealer_precision_camera_classes_us"] == "https://www.precision-camera.com/classes-pcv/"
    assert urls["dealer_kenmore_camera_events_us"] == "https://www.events.kenmorecamera.com/"
    assert urls["major_photoville_festival_us"] == "https://photoville.nyc/"
    assert urls["university_usc_sca_events_us"] == "https://cinema.usc.edu/events/"


def test_dealer_directories_bind_exact_official_location_surfaces():
    report = us_coverage_registry.audit_registry()
    rows = {row["id"]: row for row in report["dealer_discovery_sources"]}

    assert rows["dealer_canon_us_where_to_buy"]["canonical_url"] == (
        "https://www.usa.canon.com/content/dam/canon-assets/authorized-dealers/"
        "canon-ad-06-15-26.pdf"
    )
    assert rows["dealer_canon_us_where_to_buy"]["state_codes"] == sorted(
        rows["dealer_canon_us_where_to_buy"]["state_codes"]
    )
    assert "headquarters states" in rows["dealer_canon_us_where_to_buy"][
        "jurisdiction_evidence_basis"
    ]
    assert rows["dealer_microcenter_us_store_directory"]["canonical_url"] == (
        "https://www.microcenter.com/site/stores/"
    )
    assert rows["dealer_hunts_store_locations_us"]["state_codes"] == [
        "MA",
        "ME",
        "NH",
        "RI",
    ]
    assert rows["dealer_mikes_camera_locations_us"]["state_codes"] == ["CA", "CO"]
    assert rows["dealer_bedfords_store_locations_us"]["state_codes"] == [
        "AR",
        "KS",
        "MO",
        "OK",
    ]
    assert all(row["status"] == "awaiting_review" for row in rows.values())
    assert all(row["enabled"] is False for row in rows.values())


def test_manufacturer_and_viltrox_source_identities_match_review_fixture():
    fixture = json.loads(SOURCE_IDENTITY_FIXTURE.read_text(encoding="utf-8"))
    report = us_coverage_registry.audit_registry()
    registry_rows = {
        row["id"]: row
        for row in [
            *report["dealer_discovery_sources"],
            *report["dealer_official_ingest_sources"],
        ]
    }
    expected_rows = {row["id"]: row for row in fixture["sources"]}

    assert fixture["fixture_kind"] == "source_identity_review_only"
    assert fixture["contains_dealer_rows"] is False
    assert fixture["contains_contact_rows"] is False
    assert fixture["contains_inventory"] is False
    assert fixture["network_capture"] is False
    assert len(expected_rows) == 15
    assert len(
        [
            row
            for row in report["dealer_discovery_sources"]
            if row["source_kind"] == "manufacturer_dealer_directory"
        ]
    ) == 13
    for source_id, expected in expected_rows.items():
        row = registry_rows[source_id]
        assert row["publisher"] == expected["publisher"]
        assert row["canonical_url"] == expected["canonical_url"]
        assert row["source_channel"] == expected["source_channel"]
        assert row["status"] == "awaiting_review"
        assert row["enabled"] is False
        assert row["terms_robots_status"] == "pending_review"
        assert row["fixture_status"] in {
            "format_fixture_only_not_source_verified",
            "not_provided",
        }
        assert row["direct_import_allowed"] is False


def test_viltrox_inputs_are_authorization_slots_not_inferred_directories():
    report = us_coverage_registry.dealer_registry()
    rows = {
        row["id"]: row for row in report["dealer_official_ingest_sources"]
    }

    assert set(rows) == {
        "dealer_viltrox_us_company_feed",
        "dealer_viltrox_us_manual_official_entry",
    }
    assert rows["dealer_viltrox_us_company_feed"]["authorization_status"] == (
        "awaiting_company_feed"
    )
    assert rows["dealer_viltrox_us_manual_official_entry"][
        "authorization_status"
    ] == "awaiting_authorized_submitter_and_receipt"
    assert all(row["state_codes"] == [] for row in rows.values())
    assert all(row["candidate_only"] is False for row in rows.values())
    assert all(row["viltrox_authorized"] == "unknown" for row in rows.values())
    assert all(
        row["viltrox_authorization_evidence"] is False for row in rows.values()
    )
    assert report["import_gate"]["allowed"] is False


def test_registry_rejects_preactivated_or_unreviewed_manufacturer_metadata():
    payload = us_coverage_registry.load_registry()
    source = payload["dealer_discovery_sources"][0]
    source["enabled"] = True
    source["status"] = "active"
    source["terms_robots_status"] = "reviewed_allowed"
    source["fixture_status"] = "verified"
    source["source_channel"] = "third_party_aggregator"

    report = us_coverage_registry.audit_registry(payload)
    codes = {item["code"] for item in report["issues"]}

    assert report["ok"] is False
    assert {
        "dealer_source_channel_invalid",
        "dealer_fixture_status_invalid",
        "dealer_status_not_awaiting_review",
        "dealer_source_not_disabled",
        "dealer_terms_status_not_pending",
    } <= codes
    assert report["import_gate"]["allowed"] is False
