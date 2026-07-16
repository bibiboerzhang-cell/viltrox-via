from __future__ import annotations

import hashlib
import json
from copy import deepcopy
from datetime import datetime, timezone

import pytest
from fastapi import HTTPException

from app.api.routers import vkpi_dealers, vkpi_event_radar
from app.domains.commerce import dealer_scrape
from app.domains.commerce.dealer_identity import (
    propose_stable_location_key,
    propose_stable_org_key,
)
from app.domains.events import radar, radar_quality
from app.domains.events.radar_quality_core import _canonical_source_url


AS_OF = datetime(2026, 7, 13, 20, tzinfo=timezone.utc)


def _manifest(scope: str, rows: list[dict]) -> dict:
    if scope == "event_sources":
        entity_ids = sorted(str(row["id"]).strip() for row in rows)
        source_inventory = [
            {
                "source_id": str(row["id"]).strip(),
                "canonical_url": _canonical_source_url(row["canonical_url"]),
            }
            for row in rows
        ]
    else:
        entity_ids = sorted(str(row["stable_location_key"]).strip() for row in rows)
        source_inventory = [
            {
                "source_id": str(row["source_id"]).strip(),
                "canonical_url": _canonical_source_url(row["location_source_url"]),
            }
            for row in rows
        ]
    source_inventory.sort(key=lambda item: (item["source_id"], item["canonical_url"]))

    def digest(value) -> str:
        canonical = json.dumps(
            value, ensure_ascii=False, separators=(",", ":"), sort_keys=True
        ).encode("utf-8")
        return hashlib.sha256(canonical).hexdigest()

    return {
        "manifest_version": 1,
        "scope": scope,
        "denominator": len(entity_ids),
        "entity_ids": entity_ids,
        "source_inventory": source_inventory,
        "entity_ids_sha256": digest(entity_ids),
        "source_inventory_sha256": digest(source_inventory),
        "as_of": "2026-07-13T18:00:00Z",
        "methodology": "Exact-id hermetic inventory.",
        "reviewer_id": "staff_7",
    }


def _reviewed_dealer() -> dict:
    checked_at = "2026-07-13T18:00:00Z"
    org_key = propose_stable_org_key(
        "Example Camera",
        country_code="US",
        official_domain="dealer.example",
    )
    return {
        "source_id": "dealer_source_example_midtown",
        "organization_name": "Example Camera",
        "name": "Example Camera · Midtown",
        "official_domain": "dealer.example",
        "stable_org_key": org_key,
        "stable_location_key": propose_stable_location_key(
            org_key,
            country_code="US",
            address="1 Main St",
            postal_code="10001",
        ),
        "address": "1 Main St",
        "city": "New York",
        "state": "NY",
        "postal_code": "10001",
        "country": "US",
        "location_source_url": "https://dealer.example/stores/midtown",
        "brand_listing_url": "https://dealer.example/brands/viltrox",
        "source_checked_at": checked_at,
        "source_status": "public_listing_verified",
        "authorization_status": "needs_viltrox_confirmation",
        "reviewer_id": "staff_7",
        "evidence_scope": "dealer_location_listing",
        "value_status": "observed",
        "viltrox_product_evidence": {
            "status": "public_listing_observed",
            "source_url": "https://dealer.example/brands/viltrox",
            "checked_at": checked_at,
            "reviewer_id": "staff_7",
            "evidence_scope": "dealer_viltrox_product_page",
            "value_status": "observed",
        },
    }


@pytest.mark.parametrize("record_only", [None, 0, 1, "false", "true", "", [], {}])
def test_event_refresh_only_literal_json_false_can_persist(monkeypatch, record_only):
    calls: list[tuple[bool, int]] = []
    monkeypatch.setattr(
        vkpi_event_radar.radar,
        "import_reviewed_catalog",
        lambda *, record_only=True, organization_id=1: calls.append(
            (record_only, organization_id)
        )
        or {"record_only": record_only},
    )
    monkeypatch.setattr(
        vkpi_event_radar.radar,
        "organization_id_for_staff",
        lambda _staff: (_ for _ in ()).throw(AssertionError("preview resolved organization")),
    )

    result = vkpi_event_radar.event_radar_refresh(
        {"record_only": record_only},
        staff={"id": 3, "role": "employee"},
    )

    assert result["record_only"] is True
    assert calls == [(True, 1)]


def test_persistent_event_and_dealer_imports_require_manager(monkeypatch):
    with pytest.raises(HTTPException) as event_error:
        vkpi_event_radar.event_radar_refresh(
            {"record_only": False},
            staff={"id": 3, "role": "employee"},
        )
    assert event_error.value.status_code == 403

    with pytest.raises(HTTPException) as dealer_error:
        vkpi_dealers.scrape_enqueue_route(
            {"record_only": False},
            staff={"id": 3, "role": "employee"},
        )
    assert dealer_error.value.status_code == 403


def test_event_decision_promotion_and_manual_dealer_create_require_manager(monkeypatch):
    employee = {"id": 3, "role": "employee"}
    with pytest.raises(HTTPException, match="management permission required"):
        vkpi_event_radar.event_radar_decision(
            "opp_1",
            {"decision_status": "watching"},
            staff=employee,
        )
    with pytest.raises(HTTPException, match="management permission required"):
        vkpi_event_radar.event_radar_promote("opp_1", staff=employee)
    with pytest.raises(HTTPException, match="management permission required"):
        vkpi_dealers.create_dealer_route(
            {"name": "Manual", "address": "1 Main St"},
            staff=employee,
        )


def test_manual_dealer_is_explicitly_unverified(monkeypatch):
    captured: dict = {}

    def upsert(payload):
        captured.update(payload)
        return {"ok": True, **payload}

    monkeypatch.setattr(vkpi_dealers.dealer_scrape, "upsert_dealer", upsert)
    result = vkpi_dealers.create_dealer_route(
        {"name": "Manual", "address": "1 Main St", "state": "ny", "lat": 1, "lng": 2},
        staff={"id": 1, "role": "manager"},
    )

    assert result["source_status"] == "unverified"
    assert result["authorization_status"] == "needs_viltrox_confirmation"
    assert "have not been reviewed" in result["verification_note"]
    assert captured["source_status"] == "unverified"
    assert captured["state"] == "NY"


def test_manual_us_dealer_rejects_invalid_state_code(monkeypatch):
    monkeypatch.setattr(
        vkpi_dealers.dealer_scrape,
        "upsert_dealer",
        lambda _payload: pytest.fail("invalid state must be rejected before persistence"),
    )

    with pytest.raises(HTTPException) as exc_info:
        vkpi_dealers.create_dealer_route(
            {"name": "Manual", "address": "1 Main St", "state": "XX"},
            staff={"id": 1, "role": "manager"},
        )

    assert exc_info.value.status_code == 400
    assert exc_info.value.detail == "state must be a US state/DC code"


def test_manual_upsert_cannot_downgrade_an_existing_verified_listing(monkeypatch):
    class Result:
        def __init__(self, row):
            self.row = row

        def fetchone(self):
            return self.row

    class Conn:
        def __init__(self):
            self.upsert_sql = ""

        def execute(self, sql, _params=()):
            normalized = " ".join(str(sql).split())
            if normalized.startswith("SELECT 1 FROM vkpi_dealers"):
                return Result({"present": 1})
            self.upsert_sql = normalized
            return Result(
                {
                    "id": 9,
                    "source_status": "public_listing_verified",
                    "authorization_status": "needs_viltrox_confirmation",
                    "source_checked_at": "2026-07-13T18:00:00Z",
                    "verification_note": "reviewed public listing",
                }
            )

        def commit(self):
            return None

    conn = Conn()
    monkeypatch.setattr(dealer_scrape, "get_conn", lambda: conn)

    result = dealer_scrape.upsert_dealer(
        {
            "name": "Reviewed",
            "address": "1 Main St",
            "source_status": "unverified",
            "verification_note": "manual entry",
        }
    )

    assert result["inserted"] is False
    assert result["source_status"] == "public_listing_verified"
    assert result["verification_note"] == "reviewed public listing"
    assert "excluded.source_status = 'unverified'" in conn.upsert_sql


def test_dealer_map_query_excludes_unverified_rows_and_exposes_truth(monkeypatch):
    class Rows:
        def fetchall(self):
            return [
                {
                    "name": "Reviewed",
                    "address": "1 Main St",
                    "city": "New York",
                    "state": "NY",
                    "lat": 40.7,
                    "lng": -74.0,
                    "source_status": "public_listing_verified",
                    "authorization_status": "needs_viltrox_confirmation",
                    "source_checked_at": "2026-07-13T18:00:00Z",
                    "verification_note": "public listing only",
                }
            ]

    class Conn:
        sql = ""

        def execute(self, sql, _params=()):
            self.sql = " ".join(str(sql).split())
            return Rows()

    conn = Conn()
    monkeypatch.setattr(dealer_scrape, "table_exists", lambda name: name == "vkpi_dealers")
    monkeypatch.setattr(dealer_scrape, "get_conn", lambda: conn)

    pins = dealer_scrape.list_dealer_pins()

    assert "source_status = 'public_listing_verified'" in conn.sql
    assert pins[0]["source_status"] == "public_listing_verified"
    assert pins[0]["authorization_status"] == "needs_viltrox_confirmation"


def test_dealer_generic_500_is_client_safe(monkeypatch):
    monkeypatch.setattr(vkpi_dealers.logger, "error", lambda *_args, **_kwargs: None)

    def fail():
        raise RuntimeError("password=do-not-leak")

    with pytest.raises(HTTPException) as exc_info:
        vkpi_dealers._guard(fail)

    assert exc_info.value.status_code == 500
    assert exc_info.value.detail == "internal dealer error"
    assert "password" not in exc_info.value.detail


def test_dealer_preview_requires_quality_and_persistence_gates(monkeypatch):
    monkeypatch.setattr(
        radar_quality,
        "audit_dealer_candidates",
        lambda _rows: {
            "import_gate": {"allowed": True},
            "quality_status": "verified_descriptive",
            "claim_status": "descriptive_only",
            "issues": [],
        },
    )
    monkeypatch.setattr(
        dealer_scrape,
        "reviewed_persistence_contract",
        lambda: {
            "supported": False,
            "status": "migration_required",
            "reason": "reviewed_identity_and_evidence_columns_unavailable",
        },
    )

    preview = dealer_scrape.scrape_dealers_enqueue(record_only=True, limit=1)

    assert preview["quality_contract"]["import_gate"]["allowed"] is True
    assert preview["persistence_contract"]["supported"] is False
    assert preview["import_allowed"] is False
    assert preview["import_block_reason"] == "reviewed_identity_and_evidence_columns_unavailable"


def test_batch_result_is_false_on_any_upsert_failure(monkeypatch):
    candidates = [{"name": "One"}, {"name": "Two"}]
    quality = {
        "import_gate": {"allowed": True},
        "quality_status": "verified_descriptive",
        "claim_status": "descriptive_only",
        "issues": [],
    }
    responses = iter(
        [
            {"ok": True, "inserted": True, "geocoded": True},
            {"ok": False, "inserted": False, "geocoded": False},
        ]
    )
    audits: list[dict] = []
    monkeypatch.setattr(dealer_scrape, "_fetch_candidates", lambda _source, _limit: candidates)
    monkeypatch.setattr(radar_quality, "audit_dealer_candidates", lambda _rows: quality)
    monkeypatch.setattr(
        dealer_scrape,
        "reviewed_persistence_contract",
        lambda: {"supported": True, "status": "supported", "reason": ""},
    )
    monkeypatch.setattr(
        dealer_scrape,
        "upsert_dealer",
        lambda _payload, **_kwargs: next(responses),
    )
    monkeypatch.setattr(dealer_scrape.time, "sleep", lambda _seconds: None)
    monkeypatch.setattr(dealer_scrape, "_record_scrape_audit", lambda **kwargs: audits.append(kwargs))

    result = dealer_scrape.scrape_dealers_enqueue(record_only=False, limit=2)

    assert result["ok"] is False
    assert result["import_completed"] is False
    assert result["write_status"] == "partial"
    assert result["succeeded"] == 1
    assert result["failed"] == 1
    assert audits[0]["errors"]


def test_reviewed_dealer_import_fails_before_upsert_when_evidence_cannot_persist(monkeypatch):
    quality = {
        "import_gate": {"allowed": True},
        "quality_status": "verified_descriptive",
        "claim_status": "descriptive_only",
        "issues": [],
    }
    monkeypatch.setattr(dealer_scrape, "_fetch_candidates", lambda _source, _limit: [_reviewed_dealer()])
    monkeypatch.setattr(radar_quality, "audit_dealer_candidates", lambda _rows: quality)
    monkeypatch.setattr(
        dealer_scrape,
        "upsert_dealer",
        lambda _payload: (_ for _ in ()).throw(AssertionError("persistence gate was bypassed")),
    )

    with pytest.raises(ValueError, match="reviewed_identity_and_evidence_columns_unavailable"):
        dealer_scrape.scrape_dealers_enqueue(record_only=False, limit=1)


def test_evidence_contract_requires_safe_reviewer_exact_scope_and_value_status():
    row = _reviewed_dealer()
    accepted = radar_quality.audit_dealer_candidates(
        [row],
        as_of=AS_OF,
        known_location_universe_denominator=_manifest("dealer_locations", [row]),
    )
    assert accepted["import_gate"]["allowed"] is True

    unsafe_reviewer = deepcopy(row)
    unsafe_reviewer["reviewer_id"] = "person@example.com"
    rejected = radar_quality.audit_dealer_candidates([unsafe_reviewer], as_of=AS_OF)
    assert rejected["import_gate"]["allowed"] is False
    assert "dealer.source_evidence_contract_invalid" in {
        issue["code"] for issue in rejected["issues"]
    }

    wrong_scope = deepcopy(row)
    wrong_scope["viltrox_product_evidence"]["evidence_scope"] = "something_else"
    rejected = radar_quality.audit_dealer_candidates([wrong_scope], as_of=AS_OF)
    assert rejected["import_gate"]["allowed"] is False
    assert "dealer.viltrox_product_evidence_missing_or_stale" in {
        issue["code"] for issue in rejected["issues"]
    }


def test_event_activity_evidence_contract_blocks_unsafe_reviewer():
    catalog = radar.load_reviewed_catalog()
    checked_at = str(catalog["checked_at"])
    review_as_of = datetime.fromisoformat(checked_at.replace("Z", "+00:00"))
    for source in catalog["sources"]:
        source.update(
            source_checked_at=checked_at,
            reviewer_id="staff_7",
            evidence_scope="event_source_listing",
            value_status="observed",
        )
    for opportunity in catalog["opportunities"]:
        opportunity.update(
            source_checked_at=checked_at,
            reviewer_id="staff_7",
            evidence_scope="event_official_listing",
            value_status="observed",
        )
    accepted = radar_quality.audit_event_catalog(catalog, as_of=review_as_of)
    assert accepted["import_gate"]["allowed"] is True

    catalog["opportunities"][0]["reviewer_id"] = "admin@example.com"
    rejected = radar_quality.audit_event_catalog(catalog, as_of=review_as_of)
    assert rejected["import_gate"]["allowed"] is False
    assert "event.activity_evidence_contract_invalid" in {
        issue["code"] for issue in rejected["issues"]
    }


def test_bare_denominator_never_unlocks_global_coverage_rate():
    row = _reviewed_dealer()
    bare = radar_quality.audit_dealer_candidates(
        [row],
        as_of=AS_OF,
        known_location_universe_denominator=1,
    )
    coverage = bare["coverage"]["global_location_coverage"]
    assert coverage["rate"] is None
    assert coverage["manifest_status"] == "invalid"
    assert "dealer.global_location_coverage.manifest_required" in {
        issue["code"] for issue in bare["issues"]
    }

    structured = radar_quality.audit_dealer_candidates(
        [row],
        as_of=AS_OF,
        known_location_universe_denominator=_manifest("dealer_locations", [row]),
    )
    assert structured["coverage"]["global_location_coverage"]["rate"] == 1.0
    assert structured["coverage"]["global_location_coverage"]["manifest_status"] == "accepted"


def test_domain_record_only_is_literal_false_not_truthiness(monkeypatch):
    monkeypatch.setattr(
        radar,
        "_require_organization_schema",
        lambda: (_ for _ in ()).throw(AssertionError("event preview reached DB")),
    )
    assert radar.import_reviewed_catalog(record_only=0)["record_only"] is True

    monkeypatch.setattr(
        dealer_scrape,
        "upsert_dealer",
        lambda _payload: (_ for _ in ()).throw(AssertionError("dealer preview wrote")),
    )
    assert dealer_scrape.scrape_dealers_enqueue(record_only=0, limit=1)["record_only"] is True
