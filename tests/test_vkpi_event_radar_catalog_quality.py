from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
import importlib.util
from pathlib import Path

import pytest

from app.domains.events import radar


@pytest.fixture(autouse=True)
def _migration_244_capability_is_ready_for_catalog_units(monkeypatch):
    monkeypatch.setattr(
        radar,
        "_require_organization_schema",
        lambda conn=None: conn or radar.get_conn(),
    )


_SCRIPT_PATH = Path(__file__).resolve().parents[1] / "scripts/audit_vkpi_event_radar_catalog.py"
_SPEC = importlib.util.spec_from_file_location("audit_vkpi_event_radar_catalog", _SCRIPT_PATH)
assert _SPEC is not None and _SPEC.loader is not None
_MODULE = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(_MODULE)
audit_catalog = _MODULE.audit_catalog


def _codes(report: dict) -> set[str]:
    return {str(item["code"]) for item in report["issues"]}


def test_reviewed_catalog_passes_offline_quality_gate_with_explicit_truth_bounds():
    report = audit_catalog(radar.load_reviewed_catalog())

    assert report["ok"] is True
    assert report["read_only"] is True
    assert report["coverage"] == {
        "claim": "registered_publisher_owned_public_entries_only",
        "global_complete": False,
    }
    assert report["counts"]["sources"] == 72
    assert report["counts"]["opportunities"] == 25
    assert report["dealer_linkage"] == {
        "candidate_opportunities": 12,
        "matched_by_name": 9,
        "unmatched": 3,
        "coverage_rate": 0.75,
        "unmatched_opportunity_ids": [
            "opp_adorama_portrait_light_20260714",
            "opp_adorama_event_revenue_20260721",
            "opp_hunts_beginner_manchester_20260804",
        ],
    }
    assert report["issue_counts"] == {"errors": 0, "warnings": 3}
    assert _codes(report) == {"opportunity.dealer_match_missing"}


def test_reviewed_2027_us_expo_dates_and_pages_match_publisher_entries():
    opportunities = {
        item["id"]: item for item in radar.load_reviewed_catalog()["opportunities"]
    }

    nab = opportunities["opp_nab_lv_2027"]
    assert nab["canonical_key"] == "nab-las-vegas|2027-04-03|las-vegas|nab"
    assert (nab["start_date"], nab["end_date"]) == ("2027-04-03", "2027-04-07")
    assert nab["official_url"] == "https://www.nabshow.com/las-vegas/plan-your-show/"

    imaging = opportunities["opp_imaging_usa_2027"]
    assert imaging["source_id"] == "expo_imaging_usa_us"
    assert (imaging["start_date"], imaging["end_date"]) == ("2027-01-31", "2027-02-02")
    assert (imaging["venue"], imaging["city"], imaging["region"]) == (
        "Charlotte Convention Center",
        "Charlotte",
        "NC",
    )
    assert imaging["official_url"] == "https://imagingusa.com/news/save-the-date-imaging-usa-2027"
    assert imaging["evidence_grade"] == "A1"
    assert "descriptive relevance only" in imaging["relevance_basis"]
    assert "Viltrox participation is not inferred" in imaging["relevance_basis"]


def test_quality_gate_rejects_duplicate_or_orphan_keys():
    catalog = radar.load_reviewed_catalog()
    catalog["sources"].append(deepcopy(catalog["sources"][0]))
    catalog["opportunities"][0]["source_id"] = "missing_source"
    catalog["opportunities"].append(deepcopy(catalog["opportunities"][1]))

    report = audit_catalog(catalog)

    assert report["ok"] is False
    assert {
        "source.id_duplicate",
        "source.canonical_url_duplicate",
        "opportunity.source_orphan",
        "opportunity.id_duplicate",
        "opportunity.canonical_key_duplicate",
        "opportunity.external_key_duplicate",
    } <= _codes(report)


def test_quality_gate_rejects_invalid_domain_date_country_timezone_and_url():
    catalog = radar.load_reviewed_catalog()
    opportunity = catalog["opportunities"][0]
    opportunity.update(
        lane="unknown_lane",
        start_date="2026-09-14",
        end_date="2026-09-11",
        country_code="usa",
        timezone="Mars/Olympus",
        official_url="http://example.test/event",
    )

    report = audit_catalog(catalog)

    assert report["ok"] is False
    assert {
        "opportunity.lane",
        "opportunity.date_order",
        "opportunity.country_code",
        "opportunity.timezone",
        "opportunity.source_country_mismatch",
        "opportunity.source_timezone_mismatch",
        "opportunity.official_url",
        "opportunity.official_host",
    } <= _codes(report)


def test_quality_gate_quarantines_nonactive_sources_and_unsupported_business_claims():
    catalog = radar.load_reviewed_catalog()
    source_id = catalog["opportunities"][0]["source_id"]
    source = next(item for item in catalog["sources"] if item["id"] == source_id)
    source["status"] = "hold"
    source["enabled"] = True
    opportunity = catalog["opportunities"][0]
    opportunity["authorization_status"] = "authorized"
    opportunity["stock_status"] = "in_stock"
    opportunity["roi"] = 2.5
    catalog["global_complete"] = True

    report = audit_catalog(catalog)

    assert report["ok"] is False
    assert {
        "catalog.global_complete",
        "source.nonactive_enabled",
        "opportunity.nonactive_source",
        "source.nonactive_has_opportunities",
        "opportunity.unsupported_business_claim",
    } <= _codes(report)


def test_positive_viltrox_presence_requires_separate_official_evidence():
    catalog = radar.load_reviewed_catalog()
    opportunity = catalog["opportunities"][0]
    opportunity["viltrox_presence_status"] = "confirmed_exhibitor"
    opportunity["viltrox_evidence_url"] = ""

    report = audit_catalog(catalog)

    assert report["ok"] is False
    assert "opportunity.viltrox_presence_evidence" in _codes(report)


class _Result:
    def __init__(self, row):
        self._row = row

    def fetchone(self):
        return self._row


class _PromotionGuardConnection:
    def __init__(self, opportunity: dict):
        self.opportunity = opportunity
        self.statements: list[str] = []

    def execute(self, sql, _params=()):
        self.statements.append(" ".join(str(sql).split()))
        if "FROM vkpi_event_opportunities" in sql:
            return _Result(self.opportunity)
        if "FROM vkpi_event_opportunity_promotions" in sql:
            return _Result(None)
        if "SELECT 1 FROM staff" in sql or "SELECT 1 FROM organization_members" in sql:
            return _Result({"present": 1})
        if "SELECT organization_id FROM organization_members" in sql:
            return _Result({"organization_id": 1})
        raise AssertionError(f"promotion guard unexpectedly reached a write/query: {sql}")

    def rollback(self):
        return None


@pytest.mark.parametrize(
    ("overrides", "expected"),
    [
        ({"decision_status": "watching"}, "approved"),
        ({"decision_status": "approved", "verification_status": "needs_review"}, "verified scheduled"),
        ({"decision_status": "approved", "verification_status": "verified", "event_status": "cancelled"}, "verified scheduled"),
        ({"decision_status": "approved", "verification_status": "verified", "event_status": "scheduled", "end_date": None}, "start_date and end_date"),
    ],
)
def test_promotion_boundary_stops_before_internal_event_write(monkeypatch, overrides, expected):
    opportunity = {
        "id": "opp_guard",
        "organization_id": 1,
        "source_status": "active",
        "source_enabled": True,
        "decision_status": "new",
        "verification_status": "needs_review",
        "event_status": "scheduled",
        "start_date": "2026-08-01",
        "end_date": "2026-08-01",
        "last_verified_at": datetime.now(timezone.utc).isoformat(),
    }
    opportunity.update(overrides)
    conn = _PromotionGuardConnection(opportunity)
    monkeypatch.setattr(radar, "get_conn", lambda: conn)

    with pytest.raises(ValueError, match=expected):
        radar.promote("opp_guard", staff={"id": 1, "organization_id": 1})

    assert not any(statement.startswith("INSERT INTO vkpi_events") for statement in conn.statements)
