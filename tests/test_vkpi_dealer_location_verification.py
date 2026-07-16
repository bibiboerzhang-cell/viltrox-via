from __future__ import annotations

import pytest
from fastapi import HTTPException

from app.api.routers import ADMIN_ROUTER_MODULES
from app.api.routers import vkpi_dealer_location_verification as router_module
from app.domains.commerce import dealer_location_verification as contract


class _Result:
    def __init__(self, row=None, rows=None):
        self.row = row
        self.rows = rows or []

    def fetchone(self):
        return self.row

    def fetchall(self):
        return self.rows


def _row(**changes):
    base = {
        "id": 7,
        "name": "Independent Camera",
        "address": "7 Main St",
        "city": "Boston",
        "state": "MA",
        "lat": 42.36,
        "lng": -71.06,
        "location_source_url": "https://dealer.example/stores/boston",
        "brand_listing_url": "https://dealer.example/viltrox",
        "publication_status": "draft",
        "location_verification_contract_version": 0,
        "canonical_location_status": "pending",
        "canonical_location_checked_at": None,
        "canonical_location_checked_by": "",
        "physical_store_status": "pending",
        "physical_store_checked_at": None,
        "physical_store_checked_by": "",
        "physical_store_verification_note": "",
        "google_place_verification_status": "pending",
        "google_place_id": None,
        "google_maps_url": None,
        "google_place_checked_at": None,
        "google_place_checked_by": "",
        "google_place_evidence_json": {},
    }
    base.update(changes)
    return base


class _Conn:
    def __init__(self, row=None):
        self.row = row or _row()
        self.calls = []
        self.commits = 0

    def execute(self, sql, params=()):
        normalized = " ".join(str(sql).split())
        self.calls.append((normalized, tuple(params)))
        if normalized.startswith("SELECT id, address"):
            return _Result(self.row)
        if normalized.startswith("UPDATE vkpi_dealers"):
            (
                canonical_status,
                actor,
                physical_status,
                _actor2,
                note,
                remains_published,
                _remains2,
                _remains3,
                _dealer_id,
            ) = tuple(params)
            self.row.update(
                {
                    "location_verification_contract_version": 1,
                    "canonical_location_status": canonical_status,
                    "canonical_location_checked_at": "2026-07-16T12:00:00Z",
                    "canonical_location_checked_by": actor,
                    "physical_store_status": physical_status,
                    "physical_store_checked_at": "2026-07-16T12:00:00Z",
                    "physical_store_checked_by": actor,
                    "physical_store_verification_note": note,
                    "publication_status": (
                        self.row["publication_status"] if remains_published else "draft"
                    ),
                }
            )
            return _Result()
        if "FROM vkpi_dealers WHERE id = ?" in normalized:
            return _Result(self.row)
        raise AssertionError(normalized)

    def commit(self):
        self.commits += 1


@pytest.fixture(autouse=True)
def _schema_ready(monkeypatch):
    monkeypatch.setattr(contract, "_require_schema", lambda: None)


def test_google_places_missing_key_stays_pending_without_network():
    capability = contract.google_places_capability({})

    assert capability == {
        "configured": False,
        "automatic_lookup_enabled": False,
        "network_request_performed": False,
        "status_when_unavailable": "pending",
        "canonical_source": False,
        "purpose": "cross_check_only",
    }


def test_verification_router_is_registered_after_main_dealer_router():
    dealer_index = ADMIN_ROUTER_MODULES.index("vkpi_dealers")
    assert ADMIN_ROUTER_MODULES[dealer_index + 1] == (
        "vkpi_dealer_location_verification"
    )


def test_projection_keeps_product_and_physical_store_evidence_separate():
    projected = contract._project(
        _row(
            location_verification_contract_version=1,
            canonical_location_status="official_site_verified",
            physical_store_status="verified_physical_store",
            canonical_location_checked_at="2026-07-16T12:00:00Z",
            canonical_location_checked_by="staff_5",
            physical_store_checked_at="2026-07-16T12:00:00Z",
            physical_store_checked_by="staff_5",
            physical_store_verification_note="Exact store-owned page reviewed.",
            publication_status="published",
        )
    )

    assert projected["map_eligible"] is True
    assert projected["product_evidence"]["viltrox_public_listing_url"]
    assert projected["product_evidence"]["proves_physical_store"] is False
    assert projected["google_place_cross_check"]["status"] == "pending"
    assert projected["google_place_cross_check"]["canonical_source"] is False


def test_official_review_verifies_store_without_writing_google_or_product(monkeypatch):
    conn = _Conn()
    monkeypatch.setattr(contract, "get_conn", lambda: conn)

    result = contract.review_official_location(
        7,
        {
            "canonical_location_status": "official_site_verified",
            "physical_store_status": "verified_physical_store",
            "note": "Official store page shows this exact address.",
        },
        actor_id=5,
    )

    assert result["physical_store"]["status"] == "verified_physical_store"
    assert result["google_place_cross_check"]["status"] == "pending"
    update_sql = next(sql for sql, _ in conn.calls if sql.startswith("UPDATE"))
    assert "google_place_id" not in update_sql
    assert "google_maps_url" not in update_sql
    assert "brand_listing_url" not in update_sql
    assert conn.commits == 1


def test_non_store_review_unpublishes_without_deleting_product_evidence(monkeypatch):
    conn = _Conn(_row(publication_status="published"))
    monkeypatch.setattr(contract, "get_conn", lambda: conn)

    result = contract.review_official_location(
        7,
        {
            "canonical_location_status": "official_site_verified",
            "physical_store_status": "not_physical_store",
            "note": "Official page describes online fulfillment only.",
        },
        actor_id=5,
    )

    assert result["publication_status"] == "draft"
    assert result["product_evidence"]["viltrox_public_listing_url"]


def test_verified_store_requires_official_store_page(monkeypatch):
    conn = _Conn(_row(location_source_url=None))
    monkeypatch.setattr(contract, "get_conn", lambda: conn)

    with pytest.raises(ValueError, match="location_source_url"):
        contract.review_official_location(
            7,
            {
                "canonical_location_status": "official_site_verified",
                "physical_store_status": "verified_physical_store",
                "note": "Checked the exact store-owned page.",
            },
            actor_id=5,
        )
    assert conn.commits == 0


def test_manufacturer_directory_name_cannot_be_verified_as_store(monkeypatch):
    conn = _Conn(
        _row(location_source_url="https://www.nikonusa.com/content/where-to-buy")
    )
    monkeypatch.setattr(contract, "get_conn", lambda: conn)

    with pytest.raises(ValueError, match="manufacturer directory"):
        contract.review_official_location(
            7,
            {
                "canonical_location_status": "official_site_verified",
                "physical_store_status": "verified_physical_store",
                "note": "Only a manufacturer directory organization name.",
            },
            actor_id=5,
        )
    assert conn.commits == 0


def test_router_rejects_google_fields_in_manual_official_review(monkeypatch):
    monkeypatch.setattr(router_module, "require_manager_staff", lambda _staff: None)

    with pytest.raises(HTTPException) as error:
        router_module.review_dealer_official_location(
            7,
            {
                "canonical_location_status": "official_site_verified",
                "physical_store_status": "verified_physical_store",
                "google_place_id": "ChIJSampleFake",
            },
            staff={"id": 5},
        )

    assert error.value.status_code == 400
    assert "google_place_id" in error.value.detail
