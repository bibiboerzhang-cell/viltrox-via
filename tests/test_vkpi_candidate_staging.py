from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from app.api.routers import vkpi_dealers, vkpi_event_radar
from app.domains import source_passport_store
from app.domains.events import candidate_staging


NOW = datetime(2026, 7, 15, 5, tzinfo=timezone.utc)


def _dealer_preview(**overrides):
    payload = {
        "record_only": True,
        "source_registry_id": "dealer_canon_us_where_to_buy",
        "source_entity_key": "store.example.midtown",
        "source_url": "https://dealer.example/stores/midtown?utm_source=registry",
        "stable_org_key": "dealer_org_aaaaaaaa",
        "stable_location_key": "dealer_loc_aaaaaaaa",
        "candidate_payload": {
            "name": "Example Camera Midtown",
            "address": "1 Main St",
            "country_code": "US",
        },
    }
    payload.update(overrides)
    return payload


def _event_preview(**overrides):
    payload = {
        "record_only": True,
        "source_registry_id": "community_ppa_events_us",
        "source_entity_key": "event.example.2026-08-01",
        "source_url": "https://events.example/2026/example",
        "candidate_payload": {
            "title": "Example Photography Workshop",
            "start_date": "2026-08-01",
            "country_code": "US",
        },
    }
    payload.update(overrides)
    return payload


def test_preview_is_deterministic_read_only_and_always_blocked(monkeypatch):
    monkeypatch.setattr(
        candidate_staging,
        "get_conn",
        lambda: (_ for _ in ()).throw(AssertionError("preview touched database")),
    )
    monkeypatch.setattr(
        candidate_staging,
        "table_exists",
        lambda _name: (_ for _ in ()).throw(AssertionError("preview inspected schema")),
    )

    first = candidate_staging.preview_candidate(
        _dealer_preview(), candidate_type="dealer_location", organization_id=7
    )
    second = candidate_staging.preview_candidate(
        _dealer_preview(), candidate_type="dealer_location", organization_id=7
    )

    assert first == second
    assert first["candidate"]["id"].startswith("cand_")
    assert first["candidate"]["source_url"] == "https://dealer.example/stores/midtown"
    assert first["promotion_gate"]["status"] == "blocked"
    assert first["promotion_gate"]["eligible"] is False
    assert first["promotion_gate"]["automatic_promotion"] is False
    assert first["contract"] == {
        "id": "vkpi.dealer_event.candidate_staging.preview",
        "version": 1,
        "network_accessed": False,
        "database_accessed": False,
        "business_rows_written": 0,
        "candidate_rows_written": 0,
    }
    assert first["claim_status"] == "descriptive_only"
    assert first["global_denominator"] is None
    assert "candidate_payload" not in first["candidate"]


def test_preview_rejects_persistence_unknown_sources_and_business_claims():
    with pytest.raises(ValueError, match="record_only"):
        candidate_staging.preview_candidate(
            _event_preview(record_only=False),
            candidate_type="event_opportunity",
            organization_id=1,
        )
    with pytest.raises(ValueError, match="not registered"):
        candidate_staging.preview_candidate(
            _event_preview(source_registry_id="community_unknown_us"),
            candidate_type="event_opportunity",
            organization_id=1,
        )
    with pytest.raises(ValueError, match="unsupported business claims"):
        candidate_staging.preview_candidate(
            _dealer_preview(candidate_payload={"authorization_status": "authorized"}),
            candidate_type="dealer_location",
            organization_id=1,
        )


def test_event_preview_does_not_require_or_infer_a_dealer_link():
    result = candidate_staging.preview_candidate(
        _event_preview(), candidate_type="event_opportunity", organization_id=3
    )
    assert result["candidate"]["stable_location_key"] is None
    assert "exact_stable_dealer_location_required" not in result["promotion_gate"]["reasons"]
    assert result["claim_boundaries"]["event_listing_proves_viltrox_participation"] is False


class _Rows:
    def __init__(self, *, rows=None, row=None):
        self.rows = list(rows or [])
        self.row = row

    def fetchall(self):
        return list(self.rows)

    def fetchone(self):
        return self.row


class _SummaryConnection:
    def __init__(self):
        self.calls = []

    def execute(self, sql, params=()):
        normalized = " ".join(str(sql).split())
        self.calls.append((normalized, tuple(params)))
        if normalized.startswith("SELECT review_status"):
            return _Rows(
                rows=[
                    {"review_status": "pending", "promotion_gate_status": "blocked", "n": 4},
                    {
                        "review_status": "approved",
                        "promotion_gate_status": "eligible_for_manual_promotion",
                        "n": 1,
                    },
                ]
            )
        if normalized.startswith("SELECT COUNT(*) AS n"):
            return _Rows(row={"n": 3})
        raise AssertionError(normalized)


def test_summary_is_org_scoped_and_never_returns_raw_payload(monkeypatch):
    conn = _SummaryConnection()
    monkeypatch.setattr(candidate_staging, "table_exists", lambda _name: True)
    monkeypatch.setattr(candidate_staging, "get_conn", lambda: conn)

    result = candidate_staging.staging_summary(
        organization_id=9, candidate_type="dealer_location"
    )

    assert result["total"] == 5
    assert result["review_status"] == {"approved": 1, "pending": 4}
    assert result["promotion_gate_status"] == {
        "blocked": 4,
        "eligible_for_manual_promotion": 1,
    }
    assert result["linked_field_evidence"] == 3
    assert result["automatic_promotion"] is False
    assert all(params == (9, "dealer_location") for _sql, params in conn.calls)
    assert "items" not in result


def test_summary_reports_migration_pending_without_database_query(monkeypatch):
    monkeypatch.setattr(candidate_staging, "table_exists", lambda _name: False)
    monkeypatch.setattr(
        candidate_staging,
        "get_conn",
        lambda: (_ for _ in ()).throw(AssertionError("pending schema queried database")),
    )
    result = candidate_staging.staging_summary(
        organization_id=1, candidate_type="event_opportunity"
    )
    assert result["status"] == "migration_pending"
    assert result["total"] == 0
    assert result["automatic_promotion"] is False


def test_source_registry_passport_and_field_evidence_are_supported():
    passport = source_passport_store.build_passport_record(
        {
            "entity_type": "source_registry",
            "registry_source_id": "community_ppa_events_us",
            "publisher_name": "Professional Photographers of America",
            "publisher_tier": "organizer_owned",
            "canonical_url": "https://www.ppa.com/events",
            "identity_status": "exact",
            "verification_status": "verified",
            "verified_at": (NOW - timedelta(hours=1)).isoformat(),
        },
        organization_id=1,
        reviewer_staff_id=7,
        as_of=NOW,
    )
    assert passport["entity_type"] == "source_registry"
    assert passport["registry_source_id"] == "community_ppa_events_us"
    evidence = source_passport_store.build_field_evidence_record(
        {
            "passport_id": passport["id"],
            "field_name": "candidate.activity",
            "value_sha256": "a" * 64,
            "source_url": "https://www.ppa.com/events/example",
            "publisher_tier": "organizer_owned",
            "evidence_scope": "source_registry_field",
            "value_status": "observed",
            "verification_status": "verified",
            "observed_at": (NOW - timedelta(hours=2)).isoformat(),
            "verified_at": (NOW - timedelta(hours=1)).isoformat(),
        },
        organization_id=1,
        reviewer_staff_id=7,
        entity_type="source_registry",
        as_of=NOW,
    )
    assert evidence["field_name"] == "candidate.activity"
    assert evidence["claim_status"] == "descriptive_only"


def test_router_preview_and_summary_remain_read_only(monkeypatch):
    dealer = vkpi_dealers.dealer_candidate_staging_preview(
        body=_dealer_preview(), staff={"id": 5, "organization_id": 11}
    )
    assert dealer["record_only"] is True
    assert dealer["candidate"]["organization_id"] == 11

    monkeypatch.setattr(vkpi_event_radar, "_organization_id", lambda _staff: 12)
    event = vkpi_event_radar.event_candidate_staging_preview(
        body=_event_preview(), staff={"id": 6}
    )
    assert event["record_only"] is True
    assert event["candidate"]["organization_id"] == 12
