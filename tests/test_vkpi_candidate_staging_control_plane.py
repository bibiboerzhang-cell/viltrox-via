from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import pytest
from fastapi import HTTPException

from app.api.routers import vkpi_dealers, vkpi_event_radar
from app.domains.events import candidate_staging


NOW = datetime.now(timezone.utc)
MANAGER = {
    "id": 991001,
    "staff_id": 991001,
    "organization_id": 17,
    "role": "manager",
    "is_owner": 0,
    "permissions": {"vkpi": "write"},
}
EMPLOYEE = {
    "id": 991002,
    "staff_id": 991002,
    "organization_id": 17,
    "role": "employee",
    "is_owner": 0,
    "permissions": {"vkpi": "write"},
}


def _dealer_stage(**overrides):
    body = {
        "record_only": False,
        "source_registry_id": "dealer_canon_us_where_to_buy",
        "source_entity_key": "store.example.midtown",
        "source_url": "https://dealer.example/stores/midtown?utm_source=registry",
        "stable_org_key": "dealer_org_aaaaaaaa",
        "stable_location_key": "dealer_loc_aaaaaaaa",
        "candidate_payload": {
            "organization_name": "Example Camera",
            "branch_name": "Midtown",
            "address": {"address1": "1 Main St", "region": "NY", "country_code": "US"},
        },
    }
    body.update(overrides)
    return body


def _event_stage(**overrides):
    body = {
        "record_only": False,
        "source_registry_id": "community_ppa_events_us",
        "source_entity_key": "event.example.2026-08-01",
        "source_url": "https://events.example/2026/example",
        "candidate_payload": {
            "title": "Example Photography Workshop",
            "start_at": "2026-08-01T10:00:00-04:00",
            "country_code": "US",
        },
    }
    body.update(overrides)
    return body


class _Result:
    def __init__(self, *, row=None, rows=None, rowcount=1):
        self._row = row
        self._rows = list(rows or [])
        self.rowcount = rowcount

    def fetchone(self):
        return self._row

    def fetchall(self):
        return list(self._rows)


class RaiseException(Exception):
    """Fake PostgreSQL P0001 class name recognized by the domain boundary."""


class _CandidateConnection:
    def __init__(self):
        self.candidates: dict[tuple[int, str], dict] = {}
        self.links: set[tuple[int, str, str, str]] = set()
        self.calls: list[tuple[str, tuple]] = []
        self.commits = 0
        self.rollbacks = 0
        self.reject_review_update = False
        self.race_on_insert = False
        self.exact_dealer_target = True
        self.exact_event_target = True
        verified_at = (NOW - timedelta(hours=1)).isoformat()
        self.evidence = {
            "organization_id": 17,
            "id": "spe_" + "b" * 32,
            "passport_id": "spp_" + "a" * 32,
            "field_name": "candidate.location",
            "value_sha256": candidate_staging.canonical_json_sha256(
                {"address1": "1 Main St", "region": "NY", "country_code": "US"}
            ),
            "source_url": "https://dealer.example/stores/midtown",
            "publisher_tier": "retailer_owned",
            "evidence_scope": "source_registry_field",
            "value_status": "observed",
            "verification_status": "verified",
            "freshness_status_at_write": "fresh",
            "verified_at": verified_at,
            "stale_after_days": 30,
            "reviewer_staff_id": 88,
            "claim_status": "descriptive_only",
            "passport_entity_type": "source_registry",
            "passport_registry_source_id": "dealer_canon_us_where_to_buy",
            "passport_identity_status": "exact",
            "passport_verification_status": "verified",
            "passport_freshness_status_at_write": "fresh",
            "passport_verified_at": verified_at,
            "passport_stale_after_days": 30,
            "passport_reviewer_staff_id": 87,
            "passport_claim_status": "descriptive_only",
        }

    @staticmethod
    def _sql(sql):
        return " ".join(str(sql).split())

    def execute(self, sql, params=()):
        normalized = self._sql(sql)
        values = tuple(params)
        self.calls.append((normalized, values))

        if (
            normalized.startswith(
                "SELECT * FROM vkpi_dealer_event_candidates WHERE organization_id=? AND candidate_type=?"
            )
            and "source_registry_id=?" in normalized
        ):
            org_id, candidate_type, registry_id, entity_key = values
            row = next(
                (
                    item
                    for (org, _cid), item in self.candidates.items()
                    if org == org_id
                    and item["candidate_type"] == candidate_type
                    and item["source_registry_id"] == registry_id
                    and item["source_entity_key"] == entity_key
                ),
                None,
            )
            return _Result(row=row)

        if normalized.startswith("INSERT INTO vkpi_dealer_event_candidates"):
            (
                org_id,
                candidate_id,
                candidate_type,
                registry_id,
                entity_key,
                source_url,
                stable_org_key,
                stable_location_key,
                content_sha,
                payload_json,
                claim_status,
            ) = values
            self.candidates[(org_id, candidate_id)] = {
                "organization_id": org_id,
                "id": candidate_id,
                "candidate_type": candidate_type,
                "source_registry_id": registry_id,
                "source_entity_key": entity_key,
                "source_url": source_url,
                "stable_org_key": stable_org_key,
                "stable_location_key": stable_location_key,
                "content_sha256": content_sha,
                "candidate_payload_json": payload_json,
                "review_status": "pending",
                "reviewer_staff_id": None,
                "reviewed_at": None,
                "source_passport_id": None,
                "promotion_gate_status": "blocked",
                "promotion_target_type": "",
                "promotion_target_id": "",
                "promotion_reviewer_staff_id": None,
                "promoted_at": None,
                "claim_status": claim_status,
                "created_at": NOW,
                "updated_at": NOW,
            }
            return _Result(rowcount=0 if self.race_on_insert else 1)

        if normalized.startswith("SELECT * FROM vkpi_dealer_event_candidates WHERE organization_id=? AND id=?"):
            return _Result(row=self.candidates.get((values[0], values[1])))

        if normalized.startswith("UPDATE vkpi_dealer_event_candidates SET source_url=?"):
            source_url, stable_org, stable_loc, sha, payload_json, claim, org, cid = values
            item = self.candidates[(org, cid)]
            item.update(
                {
                    "source_url": source_url,
                    "stable_org_key": stable_org,
                    "stable_location_key": stable_loc,
                    "content_sha256": sha,
                    "candidate_payload_json": payload_json,
                    "review_status": "pending",
                    "reviewer_staff_id": None,
                    "reviewed_at": None,
                    "source_passport_id": None,
                    "promotion_gate_status": "blocked",
                    "promotion_target_type": "",
                    "promotion_target_id": "",
                    "promotion_reviewer_staff_id": None,
                    "promoted_at": None,
                    "claim_status": claim,
                    "updated_at": NOW,
                }
            )
            return _Result()

        if normalized.startswith("DELETE FROM vkpi_candidate_field_evidence_links"):
            org, cid = values
            self.links = {link for link in self.links if link[:2] != (org, cid)}
            return _Result()

        if normalized.startswith(
            "SELECT * FROM vkpi_dealer_event_candidates "
            "WHERE organization_id=? AND id=? AND candidate_type=?"
        ):
            row = self.candidates.get((values[0], values[1]))
            if row and row["candidate_type"] != values[2]:
                row = None
            return _Result(row=row)

        if (
            normalized.startswith(
                "SELECT * FROM vkpi_dealer_event_candidates "
                "WHERE organization_id=? AND candidate_type=?"
            )
            and "LIMIT ? OFFSET ?" in normalized
        ):
            org, candidate_type = values[:2]
            limit, offset = values[-2:]
            rows = [
                item
                for (item_org, _cid), item in self.candidates.items()
                if item_org == org and item["candidate_type"] == candidate_type
            ]
            return _Result(rows=rows[offset : offset + limit])

        if normalized.startswith("SELECT link.field_evidence_id"):
            org, cid = values
            rows = []
            for link_org, link_cid, evidence_id, role in self.links:
                if (link_org, link_cid) == (org, cid):
                    rows.append(
                        {
                            "field_evidence_id": evidence_id,
                            "evidence_role": role,
                            "added_by_staff_id": 991001,
                            "created_at": NOW,
                            "field_name": self.evidence["field_name"],
                            "value_status": "observed",
                            "verification_status": "verified",
                            "freshness_status_at_write": "fresh",
                            "verified_at": self.evidence["verified_at"],
                            "source_url": self.evidence["source_url"],
                        }
                    )
            return _Result(rows=rows)

        if normalized.startswith("SELECT evidence.*,passport.entity_type"):
            if "WHERE evidence.organization_id=? AND evidence.id=?" in normalized:
                org, evidence_id = values
                row = self.evidence if (org, evidence_id) == (17, self.evidence["id"]) else None
                return _Result(row=row)
            if "WHERE link.organization_id=?" in normalized:
                org, cid, role, passport_id = values
                linked = (org, cid, self.evidence["id"], role) in self.links
                row = self.evidence if linked and passport_id == self.evidence["passport_id"] else None
                return _Result(row=row)

        if normalized.startswith("UPDATE vkpi_dealer_event_candidates SET source_passport_id=?"):
            passport_id, org, cid = values
            self.candidates[(org, cid)]["source_passport_id"] = passport_id
            return _Result()

        if normalized.startswith("INSERT INTO vkpi_candidate_field_evidence_links"):
            org, cid, evidence_id, role, _staff = values
            key = (org, cid, evidence_id, role)
            inserted = key not in self.links
            self.links.add(key)
            return _Result(rowcount=1 if inserted else 0)

        if normalized.startswith("UPDATE vkpi_dealer_event_candidates SET review_status=?"):
            if self.reject_review_update:
                raise RaiseException("database trigger rejected transition")
            decision, staff_id, gate, org, cid = values
            self.candidates[(org, cid)].update(
                {
                    "review_status": decision,
                    "reviewer_staff_id": staff_id,
                    "reviewed_at": NOW,
                    "promotion_gate_status": gate,
                    "promotion_target_type": "",
                    "promotion_target_id": "",
                    "promotion_reviewer_staff_id": None,
                    "promoted_at": None,
                }
            )
            return _Result()

        if normalized.startswith("SELECT dealer.id FROM vkpi_dealers"):
            return _Result(row={"id": values[2]} if self.exact_dealer_target else None)

        if normalized.startswith("SELECT id FROM vkpi_event_opportunities"):
            return _Result(row={"id": values[1]} if self.exact_event_target else None)

        if normalized.startswith("UPDATE vkpi_dealer_event_candidates SET promotion_gate_status='manually_promoted'"):
            candidate_type, target_id, staff_id, org, cid = values
            self.candidates[(org, cid)].update(
                {
                    "promotion_gate_status": "manually_promoted",
                    "promotion_target_type": candidate_type,
                    "promotion_target_id": target_id,
                    "promotion_reviewer_staff_id": staff_id,
                    "promoted_at": NOW,
                }
            )
            return _Result()

        raise AssertionError(normalized)

    def commit(self):
        self.commits += 1

    def rollback(self):
        self.rollbacks += 1


@pytest.fixture()
def fake_db(monkeypatch):
    conn = _CandidateConnection()
    monkeypatch.setattr(candidate_staging, "table_exists", lambda _name: True)
    monkeypatch.setattr(candidate_staging, "get_conn", lambda: conn)
    return conn


def test_stage_requires_explicit_write_and_is_pending_blocked_idempotent(fake_db):
    with pytest.raises(ValueError, match="record_only=false"):
        candidate_staging.stage_candidate(
            {**_dealer_stage(), "record_only": True},
            candidate_type="dealer_location",
            organization_id=17,
        )

    first = candidate_staging.stage_candidate(
        _dealer_stage(), candidate_type="dealer_location", organization_id=17
    )
    candidate_id = first["candidate"]["id"]
    fake_db.links.add((17, candidate_id, fake_db.evidence["id"], "location"))
    second = candidate_staging.stage_candidate(
        _dealer_stage(candidate_payload={"branch_name": "Midtown refreshed"}),
        candidate_type="dealer_location",
        organization_id=17,
    )

    assert first["created"] is True
    assert second["created"] is False
    assert second["restaged"] is True
    assert second["candidate"]["review_status"] == "pending"
    assert second["candidate"]["promotion_gate_status"] == "blocked"
    assert fake_db.links == set()
    assert first["contract"]["business_rows_written"] == 0
    mutation_sql = [sql for sql, _params in fake_db.calls if sql.startswith(("INSERT", "UPDATE"))]
    assert not any("INSERT INTO vkpi_dealers" in sql for sql in mutation_sql)
    assert not any("INSERT INTO vkpi_event_opportunities" in sql for sql in mutation_sql)


def test_stage_rejects_unbounded_or_non_json_payload_before_sql(fake_db):
    with pytest.raises(ValueError, match="UTF-8 bytes"):
        candidate_staging.stage_candidate(
            _dealer_stage(candidate_payload={"description": "x" * 300_000}),
            candidate_type="dealer_location",
            organization_id=17,
        )
    with pytest.raises(ValueError, match="JSON-compatible"):
        candidate_staging.stage_candidate(
            _dealer_stage(candidate_payload={"bad": object()}),
            candidate_type="dealer_location",
            organization_id=17,
        )
    with pytest.raises(ValueError, match="JSON-compatible"):
        candidate_staging.stage_candidate(
            _dealer_stage(candidate_payload={"bad": float("nan")}),
            candidate_type="dealer_location",
            organization_id=17,
        )
    with pytest.raises(ValueError, match="unsupported business claims"):
        candidate_staging.stage_candidate(
            _dealer_stage(candidate_payload={"currentInventory": 17}),
            candidate_type="dealer_location",
            organization_id=17,
        )
    with pytest.raises(ValueError, match="unsupported business claims"):
        candidate_staging.stage_candidate(
            _dealer_stage(
                candidate_payload={"rows": [{} for _ in range(501)] + [{"ROI": 9}]}
            ),
            candidate_type="dealer_location",
            organization_id=17,
        )
    assert fake_db.calls == []


def test_concurrent_identity_conflict_is_resolved_as_idempotent_restage(fake_db):
    fake_db.race_on_insert = True
    result = candidate_staging.stage_candidate(
        _dealer_stage(), candidate_type="dealer_location", organization_id=17
    )
    assert result["created"] is False
    assert result["restaged"] is True
    assert result["candidate"]["review_status"] == "pending"
    assert result["candidate"]["promotion_gate_status"] == "blocked"


def test_manager_list_and_detail_are_org_scoped_bounded_and_payload_opt_in(fake_db):
    staged = candidate_staging.stage_candidate(
        _dealer_stage(), candidate_type="dealer_location", organization_id=17
    )
    candidate_id = staged["candidate"]["id"]
    fake_db.candidates[(18, candidate_id)] = {
        **fake_db.candidates[(17, candidate_id)],
        "organization_id": 18,
    }

    hidden = candidate_staging.list_candidates(
        organization_id=17,
        candidate_type="dealer_location",
        include_payload=False,
        limit=999,
    )
    visible = candidate_staging.list_candidates(
        organization_id=17,
        candidate_type="dealer_location",
        include_payload=True,
        limit=999,
    )
    detail = candidate_staging.get_candidate(
        candidate_id, organization_id=17, candidate_type="dealer_location"
    )

    assert hidden["count"] == 1
    assert hidden["limit"] == 100
    assert visible["limit"] == 20
    assert "candidate_payload" not in hidden["items"][0]
    assert visible["items"][0]["candidate_payload"]["branch_name"] == "Midtown"
    assert detail["candidate"]["organization_id"] == 17
    assert detail["candidate"]["candidate_payload"]["branch_name"] == "Midtown"
    assert all(
        params[0] == 17
        for sql, params in fake_db.calls
        if "FROM vkpi_dealer_event_candidates" in sql and params
    )


def test_link_approve_and_exact_manual_receipt_never_create_business_target(fake_db):
    staged = candidate_staging.stage_candidate(
        _dealer_stage(), candidate_type="dealer_location", organization_id=17
    )
    candidate_id = staged["candidate"]["id"]
    linked = candidate_staging.link_field_evidence(
        candidate_id,
        organization_id=17,
        candidate_type="dealer_location",
        field_evidence_id=fake_db.evidence["id"],
        evidence_role="location",
        reviewer_staff_id=991001,
    )
    duplicate = candidate_staging.link_field_evidence(
        candidate_id,
        organization_id=17,
        candidate_type="dealer_location",
        field_evidence_id=fake_db.evidence["id"],
        evidence_role="location",
        reviewer_staff_id=991001,
    )
    approved = candidate_staging.review_candidate(
        candidate_id,
        organization_id=17,
        candidate_type="dealer_location",
        decision="approved",
        reviewer_staff_id=991001,
    )
    receipt = candidate_staging.record_manual_promotion_receipt(
        candidate_id,
        organization_id=17,
        candidate_type="dealer_location",
        target_id="42",
        reviewer_staff_id=991001,
    )
    replay = candidate_staging.record_manual_promotion_receipt(
        candidate_id,
        organization_id=17,
        candidate_type="dealer_location",
        target_id="42",
        reviewer_staff_id=991001,
    )

    assert linked["inserted"] is True
    assert duplicate["inserted"] is False
    assert approved["candidate"]["review_status"] == "approved"
    assert approved["candidate"]["promotion_gate_status"] == "eligible_for_manual_promotion"
    assert receipt["candidate"]["promotion_gate_status"] == "manually_promoted"
    assert receipt["candidate"]["promotion_target_id"] == "42"
    assert replay["idempotent"] is True
    assert all(result["business_rows_written"] == 0 for result in (linked, approved, receipt))
    sql = "\n".join(call for call, _params in fake_db.calls)
    assert "INSERT INTO vkpi_dealers" not in sql
    assert "UPDATE vkpi_dealers" not in sql
    assert "INSERT INTO vkpi_event_opportunities" not in sql
    assert "UPDATE vkpi_event_opportunities" not in sql


def test_event_candidate_has_the_same_evidence_review_and_receipt_controls(fake_db):
    staged = candidate_staging.stage_candidate(
        _event_stage(), candidate_type="event_opportunity", organization_id=17
    )
    candidate_id = staged["candidate"]["id"]
    stored = fake_db.candidates[(17, candidate_id)]
    fake_db.evidence.update(
        {
            "field_name": "candidate.activity",
            "value_sha256": stored["content_sha256"],
            "source_url": stored["source_url"],
            "passport_registry_source_id": stored["source_registry_id"],
        }
    )

    candidate_staging.link_field_evidence(
        candidate_id,
        organization_id=17,
        candidate_type="event_opportunity",
        field_evidence_id=fake_db.evidence["id"],
        evidence_role="activity",
        reviewer_staff_id=991001,
    )
    approved = candidate_staging.review_candidate(
        candidate_id,
        organization_id=17,
        candidate_type="event_opportunity",
        decision="approved",
        reviewer_staff_id=991001,
    )
    receipt = candidate_staging.record_manual_promotion_receipt(
        candidate_id,
        organization_id=17,
        candidate_type="event_opportunity",
        target_id="event_existing_exact_2026",
        reviewer_staff_id=991001,
    )

    assert approved["candidate"]["promotion_gate_status"] == "eligible_for_manual_promotion"
    assert receipt["candidate"]["promotion_target_type"] == "event_opportunity"
    assert receipt["candidate"]["promotion_target_id"] == "event_existing_exact_2026"
    assert receipt["full_us_coverage"] is False


def test_evidence_role_url_and_database_trigger_fail_closed(fake_db):
    staged = candidate_staging.stage_candidate(
        _dealer_stage(), candidate_type="dealer_location", organization_id=17
    )
    candidate_id = staged["candidate"]["id"]
    with pytest.raises(candidate_staging.CandidateStagingStateConflict, match="role_mismatch"):
        candidate_staging.link_field_evidence(
            candidate_id,
            organization_id=17,
            candidate_type="dealer_location",
            field_evidence_id=fake_db.evidence["id"],
            evidence_role="activity",
            reviewer_staff_id=991001,
        )
    fake_db.evidence["source_url"] = "https://other.example/location"
    with pytest.raises(candidate_staging.CandidateStagingStateConflict, match="url_mismatch"):
        candidate_staging.link_field_evidence(
            candidate_id,
            organization_id=17,
            candidate_type="dealer_location",
            field_evidence_id=fake_db.evidence["id"],
            evidence_role="location",
            reviewer_staff_id=991001,
        )
    fake_db.evidence["source_url"] = "https://dealer.example/stores/midtown"
    candidate_staging.link_field_evidence(
        candidate_id,
        organization_id=17,
        candidate_type="dealer_location",
        field_evidence_id=fake_db.evidence["id"],
        evidence_role="location",
        reviewer_staff_id=991001,
    )
    fake_db.reject_review_update = True
    with pytest.raises(
        candidate_staging.CandidateStagingStateConflict,
        match="database_trigger",
    ):
        candidate_staging.review_candidate(
            candidate_id,
            organization_id=17,
            candidate_type="dealer_location",
            decision="approved",
            reviewer_staff_id=991001,
        )
    assert fake_db.rollbacks >= 3


@pytest.mark.parametrize(
    "endpoint,body",
    [
        (vkpi_dealers.dealer_candidate_stage, _dealer_stage()),
        (vkpi_event_radar.event_candidate_stage, _event_stage()),
    ],
)
def test_candidate_mutations_are_manager_only_before_domain_call(monkeypatch, endpoint, body):
    called = []
    monkeypatch.setattr(
        candidate_staging,
        "stage_candidate",
        lambda *_args, **_kwargs: called.append(True),
    )
    with pytest.raises(HTTPException) as exc:
        endpoint(body=body, staff=EMPLOYEE)
    assert exc.value.status_code == 403
    assert called == []


def test_raw_list_and_detail_are_manager_only_before_domain_call(monkeypatch):
    called = []
    monkeypatch.setattr(
        candidate_staging,
        "list_candidates",
        lambda **_kwargs: called.append("list"),
    )
    monkeypatch.setattr(
        candidate_staging,
        "get_candidate",
        lambda *_args, **_kwargs: called.append("detail"),
    )
    with pytest.raises(HTTPException) as list_exc:
        vkpi_dealers.dealer_candidate_staging_items(
            review_status=None,
            promotion_gate_status=None,
            include_payload=True,
            offset=0,
            limit=50,
            staff=EMPLOYEE,
        )
    with pytest.raises(HTTPException) as detail_exc:
        vkpi_event_radar.event_candidate_staging_detail(
            "cand_" + "d" * 32,
            staff=EMPLOYEE,
        )
    assert list_exc.value.status_code == 403
    assert detail_exc.value.status_code == 403
    assert called == []


@pytest.mark.parametrize("router_guard", [vkpi_dealers._guard, vkpi_event_radar._guard])
def test_candidate_schema_missing_is_503_not_500(router_guard):
    def _missing():
        raise candidate_staging.CandidateStagingSchemaUnavailable("migration_257_pending")

    with pytest.raises(HTTPException) as exc:
        router_guard(_missing)
    assert exc.value.status_code == 503
    assert exc.value.detail == "migration_257_pending"


def test_event_and_dealer_routes_keep_source_coverage_distinct_from_entities(monkeypatch):
    captured = []

    def _list(**kwargs):
        captured.append(kwargs)
        return {
            "items": [],
            "count": 0,
            "claim_status": "descriptive_only",
            "automatic_promotion": False,
            "business_rows_written": 0,
            "full_us_coverage": False,
            "global_denominator": None,
        }

    monkeypatch.setattr(candidate_staging, "list_candidates", _list)
    dealer = vkpi_dealers.dealer_candidate_staging_items(
        review_status=None,
        promotion_gate_status=None,
        include_payload=False,
        offset=0,
        limit=50,
        staff=MANAGER,
    )
    monkeypatch.setattr(vkpi_event_radar, "_organization_id", lambda _staff: 17)
    event = vkpi_event_radar.event_candidate_staging_items(
        review_status=None,
        promotion_gate_status=None,
        include_payload=False,
        offset=0,
        limit=50,
        staff=MANAGER,
    )

    assert dealer["items"] == [] and event["items"] == []
    assert [item["candidate_type"] for item in captured] == [
        "dealer_location",
        "event_opportunity",
    ]
    assert all(item["organization_id"] == 17 for item in captured)
    assert all(result["claim_status"] == "descriptive_only" for result in (dealer, event))
    assert all(result["full_us_coverage"] is False for result in (dealer, event))
    assert all(result["global_denominator"] is None for result in (dealer, event))
    serialized = json.dumps([dealer, event], sort_keys=True)
    assert "jurisdiction" not in serialized
