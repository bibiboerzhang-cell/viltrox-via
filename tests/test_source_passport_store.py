from __future__ import annotations

import hashlib
from datetime import datetime, timedelta, timezone

import pytest

from app.domains import source_passport_store as store
from app.domains.commerce.dealer_identity import (
    propose_stable_location_key,
    propose_stable_org_key,
)


NOW = datetime(2026, 7, 14, 12, tzinfo=timezone.utc)


def _dealer_payload(**overrides):
    org_key = propose_stable_org_key(
        "Example Camera", country_code="US", official_domain="dealer.example"
    )
    location_key = propose_stable_location_key(
        org_key,
        country_code="US",
        address="1 Main St",
        postal_code="10001",
    )
    payload = {
        "entity_type": "dealer_location",
        "dealer_id": 7,
        "stable_org_key": org_key,
        "exact_location_key": location_key,
        "publisher_name": "Example Camera",
        "publisher_tier": "retailer_owned",
        "canonical_url": "https://dealer.example/stores/main?utm_source=test",
        "identity_status": "exact",
        "verification_status": "verified",
        "verified_at": (NOW - timedelta(hours=1)).isoformat(),
        "stale_after_days": 30,
    }
    payload.update(overrides)
    return payload


def _field_payload(passport_id: str, **overrides):
    payload = {
        "passport_id": passport_id,
        "field_name": "contact.phone",
        "value_sha256": hashlib.sha256(b"+1 212 555 0100").hexdigest(),
        "source_url": "https://dealer.example/stores/main",
        "publisher_tier": "retailer_owned",
        "evidence_scope": "dealer_location_field",
        "value_status": "observed",
        "verification_status": "verified",
        "observed_at": (NOW - timedelta(hours=2)).isoformat(),
        "verified_at": (NOW - timedelta(hours=1)).isoformat(),
    }
    payload.update(overrides)
    return payload


def test_default_passport_is_unknown_and_never_promotes_claims():
    record = store.build_passport_record(
        {"entity_type": "event_source", "event_source_id": "event_source_example"},
        organization_id=4,
        reviewer_staff_id=9,
        as_of=NOW,
    )
    assert record["publisher_tier"] == "unknown"
    assert record["identity_status"] == "unknown"
    assert record["verification_status"] == "unknown"
    assert record["freshness_status_at_write"] == "unavailable"
    assert record["claim_status"] == "descriptive_only"
    assert record["entity_key"] == "event_source_example"
    assert record["id"].startswith("spp_")
    assert len(record["record_sha256"]) == 64


def test_verified_dealer_requires_exact_keys_known_publisher_and_fresh_timestamp():
    record = store.build_passport_record(
        _dealer_payload(), organization_id=1, reviewer_staff_id=7, as_of=NOW
    )
    assert record["identity_status"] == "exact"
    assert record["freshness_status_at_write"] == "fresh"
    assert record["canonical_url"] == "https://dealer.example/stores/main"
    assert record["identity_evidence_json"]["reviewer_id"] == "staff_7"

    with pytest.raises(ValueError, match="exact stable"):
        store.build_passport_record(
            _dealer_payload(stable_org_key="", exact_location_key=""),
            organization_id=1,
            reviewer_staff_id=7,
            as_of=NOW,
        )
    with pytest.raises(ValueError, match="known publisher"):
        store.build_passport_record(
            _dealer_payload(publisher_tier="unknown"),
            organization_id=1,
            reviewer_staff_id=7,
            as_of=NOW,
        )
    with pytest.raises(ValueError, match="current verified_at"):
        store.build_passport_record(
            _dealer_payload(verified_at=(NOW - timedelta(days=40)).isoformat()),
            organization_id=1,
            reviewer_staff_id=7,
            as_of=NOW,
        )


def test_unknown_fields_and_unsafe_urls_fail_closed():
    with pytest.raises(ValueError, match="global_coverage"):
        store.build_passport_record(
            {**_dealer_payload(), "global_coverage": True},
            organization_id=1,
            reviewer_staff_id=7,
            as_of=NOW,
        )
    with pytest.raises(ValueError, match="credential-free"):
        store.build_passport_record(
            _dealer_payload(canonical_url="https://user:secret@dealer.example/store"),
            organization_id=1,
            reviewer_staff_id=7,
            as_of=NOW,
        )


def test_field_evidence_is_entity_whitelisted_and_value_hash_bound():
    passport = store.build_passport_record(
        _dealer_payload(), organization_id=1, reviewer_staff_id=7, as_of=NOW
    )
    record = store.build_field_evidence_record(
        _field_payload(passport["id"]),
        organization_id=1,
        reviewer_staff_id=7,
        entity_type="dealer_location",
        as_of=NOW,
    )
    assert record["field_name"] == "contact.phone"
    assert record["value_status"] == "observed"
    assert record["verification_status"] == "verified"
    assert record["freshness_status_at_write"] == "fresh"
    assert record["claim_status"] == "descriptive_only"
    assert record["id"].startswith("spe_")

    with pytest.raises(ValueError, match="requires value_sha256"):
        store.build_field_evidence_record(
            _field_payload(passport["id"], value_sha256=""),
            organization_id=1,
            reviewer_staff_id=7,
            entity_type="dealer_location",
            as_of=NOW,
        )
    with pytest.raises(ValueError, match="not allowed"):
        store.build_field_evidence_record(
            _field_payload(passport["id"], field_name="sales.gmv"),
            organization_id=1,
            reviewer_staff_id=7,
            entity_type="dealer_location",
            as_of=NOW,
        )


class _Result:
    def __init__(self, row=None, rows=None, rowcount=1):
        self.row = row
        self.rows = rows or []
        self.rowcount = rowcount

    def fetchone(self):
        return self.row

    def fetchall(self):
        return list(self.rows)


class _Conn:
    def __init__(self, *, existing=None, list_rows=None):
        self.existing = existing
        self.list_rows = list_rows or []
        self.calls = []
        self.commits = 0
        self.rollbacks = 0

    def execute(self, sql, params=()):
        normalized = " ".join(str(sql).split())
        self.calls.append((normalized, tuple(params)))
        if normalized.startswith("SELECT * FROM vkpi_source_passports WHERE"):
            if "LIMIT ? OFFSET ?" in normalized:
                return _Result(rows=self.list_rows)
            return _Result(row=self.existing)
        if normalized.startswith("SELECT id,entity_type FROM vkpi_source_passports"):
            return _Result(row={"id": params[1], "entity_type": "dealer_location"})
        return _Result(rowcount=1)

    def commit(self):
        self.commits += 1

    def rollback(self):
        self.rollbacks += 1


def _ready(monkeypatch, conn):
    monkeypatch.setattr(store, "table_exists", lambda _name: True)
    monkeypatch.setattr(store, "is_postgres_runtime", lambda: False)
    monkeypatch.setattr(store, "get_conn", lambda: conn)


def test_save_passport_appends_revision_without_business_claims(monkeypatch):
    conn = _Conn()
    _ready(monkeypatch, conn)
    result = store.save_passport(
        _dealer_payload(),
        organization_id=1,
        reviewer_staff_id=7,
        as_of=NOW,
    )
    statements = [sql for sql, _params in conn.calls]
    assert any(sql.startswith("INSERT INTO vkpi_source_passports") for sql in statements)
    assert any(sql.startswith("INSERT INTO vkpi_source_passport_revisions") for sql in statements)
    assert conn.commits == 1
    assert result["created"] is True
    assert result["passport"]["claim_status"] == "descriptive_only"
    assert result["claim_boundaries"]["global_full_coverage_claim_allowed"] is False


def test_append_field_evidence_is_idempotent_content_addressed(monkeypatch):
    conn = _Conn()
    _ready(monkeypatch, conn)
    passport_id = store.build_passport_record(
        _dealer_payload(), organization_id=1, reviewer_staff_id=7, as_of=NOW
    )["id"]
    first = store.append_field_evidence(
        _field_payload(passport_id),
        organization_id=1,
        reviewer_staff_id=7,
        as_of=NOW,
    )
    second = store.append_field_evidence(
        _field_payload(passport_id),
        organization_id=1,
        reviewer_staff_id=7,
        as_of=NOW,
    )
    assert first["evidence"]["id"] == second["evidence"]["id"]
    assert first["evidence"]["claim_status"] == "descriptive_only"
    insert_sql = next(
        sql for sql, _params in conn.calls if sql.startswith("INSERT INTO vkpi_source_field_evidence")
    )
    assert "ON CONFLICT (organization_id,id) DO NOTHING" in insert_sql


def test_list_recomputes_freshness_and_keeps_global_denominator_null(monkeypatch):
    row = store.build_passport_record(
        _dealer_payload(), organization_id=1, reviewer_staff_id=7, as_of=NOW
    )
    row.update({"revision_no": 1, "created_at": NOW, "updated_at": NOW})
    conn = _Conn(list_rows=[row])
    _ready(monkeypatch, conn)
    result = store.list_passports(organization_id=1, as_of=NOW + timedelta(days=40))
    assert result["items"][0]["freshness"]["status"] == "stale"
    assert result["items"][0]["freshness_status_at_write"] == "fresh"
    assert result["global_coverage"] == {
        "denominator": None,
        "rate": None,
        "status": "unavailable",
        "reason": "global_source_universe_not_registered",
    }
    assert result["claim_status"] == "descriptive_only"


def test_schema_absence_is_explicit_not_an_empty_success(monkeypatch):
    monkeypatch.setattr(store, "table_exists", lambda _name: False)
    with pytest.raises(store.SourcePassportSchemaUnavailable, match="migration_248_pending"):
        store.list_passports(organization_id=1)
