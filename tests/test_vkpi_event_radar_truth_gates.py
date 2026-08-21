from __future__ import annotations

from copy import deepcopy
import json
from datetime import datetime, timedelta, timezone
from typing import Any

import pytest

from app.domains.events import radar, radar_import


IMPORT_REQUIRED_TABLES = {
    "vkpi_event_watch_targets",
    "vkpi_event_source_runs",
    "vkpi_event_opportunities",
    "vkpi_event_source_observations",
    "vkpi_event_opportunity_changes",
    "vkpi_event_opportunity_dealers",
    "vkpi_dealers",
    "vkpi_dealer_identity_aliases",
}

FUTURE_EVENT_DATE = "2099-08-01"


@pytest.fixture(autouse=True)
def _migration_244_capability_is_ready_for_truth_gate_units(monkeypatch):
    monkeypatch.setattr(
        radar,
        "_require_organization_schema",
        lambda conn=None: conn or radar.get_conn(),
    )


class _Result:
    def __init__(self, row: Any = None, *, rowcount: int = 0):
        self._row = row
        self.rowcount = rowcount

    def fetchone(self):
        return self._row


class _ImportConnection:
    def __init__(
        self,
        old_by_canonical: dict[str, dict[str, Any]] | None = None,
        dealer_by_location: dict[str, int] | None = None,
    ):
        self.opportunity_index = 0
        self.observation_index = 0
        self.old_by_canonical = old_by_canonical or {}
        self.dealer_by_location = dealer_by_location or {}
        self.statements: list[tuple[str, tuple[Any, ...]]] = []
        self.commits = 0
        self.rollbacks = 0

    def execute(self, sql, params=()):
        normalized = " ".join(str(sql).split())
        bound = tuple(params)
        self.statements.append((normalized, bound))
        assert normalized.count("?") == len(bound), (normalized, len(bound))
        if normalized.startswith("INSERT INTO vkpi_event_source_runs"):
            return _Result({"id": 1})
        if normalized.startswith("SELECT * FROM vkpi_event_opportunities"):
            old = self.old_by_canonical.get(str(bound[0]))
            return _Result(dict(old) if old else None)
        if normalized.startswith("SELECT id FROM vkpi_event_opportunities"):
            self.opportunity_index += 1
            return _Result({"id": f"opp-import-{self.opportunity_index}"})
        if normalized.startswith("INSERT INTO vkpi_event_source_observations"):
            self.observation_index += 1
            return _Result({"id": self.observation_index})
        if "FROM vkpi_dealer_identity_aliases" in normalized:
            return _Result({"id": self.dealer_by_location.get(str(bound[1]))})
        return _Result(None)

    def commit(self):
        self.commits += 1

    def rollback(self):
        self.rollbacks += 1


class _TruthGateConnection:
    def __init__(self, opportunity: dict[str, Any], *, actor_org: int = 1):
        self.opportunity = dict(opportunity)
        self.actor_org = actor_org
        self.statements: list[tuple[str, tuple[Any, ...]]] = []
        self.commits = 0
        self.rollbacks = 0

    def execute(self, sql, params=()):
        normalized = " ".join(str(sql).split())
        bound = tuple(params)
        self.statements.append((normalized, bound))
        if normalized.startswith("SELECT source_id FROM vkpi_event_opportunities"):
            requested_org = int(bound[-1])
            if requested_org != int(self.opportunity.get("organization_id") or 1):
                return _Result(None)
            return _Result({"source_id": self.opportunity.get("source_id")})
        if normalized.startswith("SELECT id,status,enabled FROM vkpi_event_watch_targets"):
            if str(bound[0]) != str(self.opportunity.get("source_id") or ""):
                return _Result(None)
            return _Result(
                {
                    "id": self.opportunity.get("source_id"),
                    "status": self.opportunity.get("source_status"),
                    "enabled": self.opportunity.get("source_enabled"),
                }
            )
        if "FROM vkpi_event_opportunities o" in normalized:
            requested_org = int(bound[-1])
            if requested_org != int(self.opportunity.get("organization_id") or 1):
                return _Result(None)
            return _Result(dict(self.opportunity))
        if normalized.startswith("SELECT * FROM vkpi_event_opportunities"):
            return _Result(dict(self.opportunity))
        if normalized.startswith("UPDATE vkpi_event_opportunities SET decision_status=?"):
            self.opportunity["decision_status"] = bound[0]
            return _Result(None, rowcount=1)
        if normalized.startswith("UPDATE vkpi_event_opportunities SET decision_status='promoted'"):
            self.opportunity["decision_status"] = "promoted"
            return _Result(None, rowcount=1)
        if "FROM vkpi_event_opportunity_promotions" in normalized:
            return _Result(None)
        if "SELECT 1 FROM staff" in normalized:
            return _Result({"present": 1})
        if "SELECT 1 FROM organization_members" in normalized:
            requested_org = int(bound[-1])
            return _Result({"present": 1}) if requested_org == self.actor_org else _Result(None)
        if "SELECT organization_id FROM organization_members" in normalized:
            return _Result({"organization_id": self.actor_org})
        return _Result(None)

    def commit(self):
        self.commits += 1

    def rollback(self):
        self.rollbacks += 1


class _PromoteWinsDecisionRaceConnection(_TruthGateConnection):
    """Simulate promotion committing after decide read but before its CAS."""

    def execute(self, sql, params=()):
        normalized = " ".join(str(sql).split())
        if normalized.startswith("UPDATE vkpi_event_opportunities SET decision_status=?"):
            self.opportunity["decision_status"] = "promoted"
            self.statements.append((normalized, tuple(params)))
            return _Result(None, rowcount=0)
        return super().execute(sql, params)


class _DecisionWinsPromotionRaceConnection(_TruthGateConnection):
    """Simulate a human decision committing before promote's CAS claim."""

    def execute(self, sql, params=()):
        normalized = " ".join(str(sql).split())
        if normalized.startswith("UPDATE vkpi_event_opportunities SET decision_status='promoted'"):
            self.opportunity["decision_status"] = "watching"
            self.statements.append((normalized, tuple(params)))
            return _Result(None, rowcount=0)
        return super().execute(sql, params)


class _PromotionWinsPromotionRaceConnection(_TruthGateConnection):
    """Simulate another promote request winning the CAS and receipt insert."""

    def execute(self, sql, params=()):
        normalized = " ".join(str(sql).split())
        if normalized.startswith("UPDATE vkpi_event_opportunities SET decision_status='promoted'"):
            self.opportunity["decision_status"] = "promoted"
            self.statements.append((normalized, tuple(params)))
            return _Result(None, rowcount=0)
        if normalized.startswith("SELECT o.decision_status,p.event_id,p.promoted_at"):
            self.statements.append((normalized, tuple(params)))
            return _Result(
                {
                    "decision_status": "promoted",
                    "event_id": "evt_other_request",
                    "promoted_at": "2026-07-13T00:00:01Z",
                }
            )
        return super().execute(sql, params)


class _CatalogRefreshWinsDecisionRaceConnection(_TruthGateConnection):
    """Simulate a source refresh changing truth fields before decision CAS."""

    def execute(self, sql, params=()):
        normalized = " ".join(str(sql).split())
        if normalized.startswith("UPDATE vkpi_event_opportunities SET decision_status=?"):
            self.opportunity["content_hash"] = "hash-v2"
            self.opportunity["event_status"] = "cancelled"
            self.statements.append((normalized, tuple(params)))
            return _Result(None, rowcount=0)
        return super().execute(sql, params)


class _SourceDisableWinsPromotionRaceConnection(_TruthGateConnection):
    """Simulate the watch source being disabled before promotion CAS."""

    def execute(self, sql, params=()):
        normalized = " ".join(str(sql).split())
        if normalized.startswith("UPDATE vkpi_event_opportunities SET decision_status='promoted'"):
            self.opportunity["source_status"] = "blocked"
            self.statements.append((normalized, tuple(params)))
            return _Result(None, rowcount=0)
        return super().execute(sql, params)


class _FreshnessDriftsBeforeApprovalCasConnection(_TruthGateConnection):
    """Simulate a refresh changing the exact freshness anchor before approval CAS."""

    def execute(self, sql, params=()):
        normalized = " ".join(str(sql).split())
        bound = tuple(params)
        if (
            normalized.startswith("UPDATE vkpi_event_opportunities SET decision_status=?")
            and bound[0] == "approved"
        ):
            self.opportunity["last_verified_at"] = (
                datetime.now(timezone.utc) - timedelta(days=31)
            ).isoformat()
            self.statements.append((normalized, bound))
            return _Result(None, rowcount=0)
        return super().execute(sql, params)


class _FreshnessDriftsBeforePromotionCasConnection(_TruthGateConnection):
    """Simulate a refresh changing the exact freshness anchor before promotion CAS."""

    def execute(self, sql, params=()):
        normalized = " ".join(str(sql).split())
        bound = tuple(params)
        if normalized.startswith("UPDATE vkpi_event_opportunities SET decision_status='promoted'"):
            self.opportunity["last_verified_at"] = (
                datetime.now(timezone.utc) - timedelta(days=31)
            ).isoformat()
            self.statements.append((normalized, bound))
            return _Result(None, rowcount=0)
        return super().execute(sql, params)


class _SourceIdentityChangesAfterLockConnection(_TruthGateConnection):
    """Simulate an opportunity being rebound after its original source is locked."""

    def execute(self, sql, params=()):
        normalized = " ".join(str(sql).split())
        bound = tuple(params)
        if "FROM vkpi_event_opportunities o" in normalized and "FOR UPDATE OF o" in normalized:
            changed = dict(self.opportunity)
            changed["source_id"] = "source_reassigned"
            self.statements.append((normalized, bound))
            return _Result(changed)
        return super().execute(sql, params)


class _IdempotentPromotionConnection(_TruthGateConnection):
    def execute(self, sql, params=()):
        normalized = " ".join(str(sql).split())
        if normalized.startswith("SELECT o.*, s.status AS source_status"):
            stale = dict(self.opportunity)
            stale["decision_status"] = "approved"
            self.statements.append((normalized, tuple(params)))
            return _Result(stale)
        if normalized.startswith(
            "SELECT event_id,promoted_at FROM vkpi_event_opportunity_promotions"
        ):
            self.statements.append((normalized, tuple(params)))
            return _Result({"event_id": "evt_existing", "promoted_at": "2026-07-13T00:00:00Z"})
        return super().execute(sql, params)


def _opportunity(**overrides) -> dict[str, Any]:
    event_day = (datetime.now(timezone.utc) + timedelta(days=30)).date().isoformat()
    item = {
        "id": "opp_gate",
        "organization_id": 1,
        "source_id": "source_active",
        "source_status": "active",
        "source_enabled": True,
        "decision_status": "new",
        "verification_status": "verified",
        "event_status": "scheduled",
        "start_date": event_day,
        "end_date": event_day,
        "last_verified_at": datetime.now(timezone.utc).isoformat(),
        "content_hash": "hash-v1",
        "title": "Reviewed Dealer Event",
        "lane": "dealer_event",
        "official_url": "https://dealer.example/events/reviewed",
    }
    item.update(overrides)
    return item


def _install(monkeypatch, item: dict[str, Any], *, actor_org: int = 1) -> _TruthGateConnection:
    conn = _TruthGateConnection(item, actor_org=actor_org)
    monkeypatch.setattr(radar, "get_conn", lambda: conn)
    monkeypatch.setattr(radar, "table_exists", lambda name: name == "organization_members")
    return conn


def _install_importable_reviewed_catalog(monkeypatch) -> dict[str, Any]:
    """Give SQL-mechanics tests explicit per-source/per-row freshness.

    The production catalog intentionally lacks these fields and is blocked by
    the quality gate.  These tests exercise the statements after that gate, so
    their hermetic fixture must satisfy the same contract explicitly.
    """
    catalog = deepcopy(radar.load_reviewed_catalog())
    checked_at = datetime.now(timezone.utc)
    catalog["checked_at"] = checked_at.isoformat().replace("+00:00", "Z")
    for index, source in enumerate(catalog["sources"]):
        source["source_checked_at"] = (
            checked_at - timedelta(minutes=index + 1)
        ).isoformat().replace("+00:00", "Z")
        source["reviewer_id"] = "staff_7"
        source["evidence_scope"] = "event_source_listing"
        source["value_status"] = "observed"
    for index, opportunity in enumerate(catalog["opportunities"]):
        opportunity["source_checked_at"] = (
            checked_at - timedelta(hours=1, minutes=index + 1)
        ).isoformat().replace("+00:00", "Z")
        opportunity["reviewer_id"] = "staff_7"
        opportunity["evidence_scope"] = "event_official_listing"
        opportunity["value_status"] = "observed"
    monkeypatch.setattr(radar, "load_reviewed_catalog", lambda: deepcopy(catalog))
    return catalog


def test_reviewed_import_bindings_include_workspace_without_db_side_effect_fixture(monkeypatch):
    _install_importable_reviewed_catalog(monkeypatch)
    conn = _ImportConnection()
    monkeypatch.setattr(radar, "get_conn", lambda: conn)
    monkeypatch.setattr(radar, "table_exists", lambda name: name in IMPORT_REQUIRED_TABLES)

    result = radar.import_reviewed_catalog(record_only=False, organization_id=1)

    assert result["inserted"] == 24
    assert result["observations_inserted"] == 24


def test_reviewed_import_persists_scoped_reviewer_evidence_in_jsonb(monkeypatch):
    catalog = _install_importable_reviewed_catalog(monkeypatch)
    conn = _ImportConnection()
    monkeypatch.setattr(radar, "get_conn", lambda: conn)
    monkeypatch.setattr(radar, "table_exists", lambda name: name in IMPORT_REQUIRED_TABLES)

    radar.import_reviewed_catalog(record_only=False, organization_id=1)

    source_params = next(
        params
        for sql, params in conn.statements
        if sql.startswith("INSERT INTO vkpi_event_watch_targets")
    )
    source_metadata = next(
        json.loads(value)
        for value in source_params
        if isinstance(value, str) and value.startswith("{") and "review_evidence" in value
    )
    assert source_metadata["review_evidence"]["reviewer_id"] == "staff_7"
    assert source_metadata["review_evidence"]["evidence_scope"] == "event_source_listing"
    assert source_metadata["review_evidence"]["value_status"] == "observed"
    assert (
        source_metadata["review_evidence"]["checked_at"]
        == catalog["sources"][0]["source_checked_at"]
    )
    assert source_metadata["review_evidence"]["source_url"].startswith("https://")
    assert source_metadata["review_evidence"]["observed_at"] == (
        catalog["sources"][0]["source_checked_at"]
    )
    assert source_metadata["review_evidence"]["review_status"] == (
        "quality_contract_accepted"
    )
    assert source_params[-1] == catalog["sources"][0]["source_checked_at"]

    opportunity_params = next(
        params
        for sql, params in conn.statements
        if sql.startswith("INSERT INTO vkpi_event_opportunities")
    )
    opportunity_metadata = json.loads(str(opportunity_params[-1]))
    assert opportunity_metadata["review_evidence"]["reviewer_id"] == "staff_7"
    assert opportunity_metadata["review_evidence"]["evidence_scope"] == "event_official_listing"
    assert opportunity_metadata["review_evidence"]["value_status"] == "observed"
    assert (
        opportunity_metadata["review_evidence"]["checked_at"]
        == catalog["opportunities"][0]["source_checked_at"]
    )
    assert opportunity_metadata["review_evidence"]["observed_at"] == (
        catalog["opportunities"][0]["source_checked_at"]
    )
    assert opportunity_metadata["review_evidence"]["review_status"] == (
        "quality_contract_accepted"
    )
    assert opportunity_params[-5] == catalog["opportunities"][0]["source_checked_at"]
    assert opportunity_params[-4] == catalog["opportunities"][0]["source_checked_at"]
    assert opportunity_params[-3] == catalog["opportunities"][0]["source_checked_at"]

    observation_params = next(
        params
        for sql, params in conn.statements
        if sql.startswith("INSERT INTO vkpi_event_source_observations")
    )
    assert observation_params[-2] == catalog["opportunities"][0]["source_checked_at"]
    observation_payload = json.loads(str(observation_params[-1]))
    assert observation_payload["source_url"] == catalog["opportunities"][0]["official_url"]
    assert observation_payload["observed_at"] == catalog["opportunities"][0]["source_checked_at"]
    assert observation_payload["review_status"] == "quality_contract_accepted"
    assert observation_params[-3] == radar_import.observation_identity_hash(
        opportunity_content_hash=str(opportunity_params[-2]),
        source_url=catalog["opportunities"][0]["official_url"],
        observed_at=catalog["opportunities"][0]["source_checked_at"],
        review_status="quality_contract_accepted",
        reviewer_id="staff_7",
        evidence_scope="event_official_listing",
        value_status="observed",
        dealer_stable_location_key=catalog["opportunities"][0].get("dealer_stable_location_key"),
    )


def test_reviewed_import_links_dealer_only_by_verified_stable_location_alias(monkeypatch):
    catalog = _install_importable_reviewed_catalog(monkeypatch)
    dealer_opportunity = next(
        item
        for item in catalog["opportunities"]
        if item.get("lane") in {"dealer_event", "local_activity"}
    )
    dealer_opportunity["dealer_stable_location_key"] = "dealer_loc_12345678"
    dealer_opportunity_index = catalog["opportunities"].index(dealer_opportunity) + 1
    conn = _ImportConnection(dealer_by_location={"dealer_loc_12345678": 42})
    monkeypatch.setattr(radar, "get_conn", lambda: conn)
    monkeypatch.setattr(radar, "table_exists", lambda name: name in IMPORT_REQUIRED_TABLES)

    radar.import_reviewed_catalog(record_only=False, organization_id=1)

    sql = [statement for statement, _params in conn.statements]
    assert any("FROM vkpi_dealer_identity_aliases" in statement for statement in sql)
    assert all("FROM vkpi_dealers WHERE name" not in statement for statement in sql)
    reconcile = next(
        params for statement, params in conn.statements
        if statement.startswith("DELETE FROM vkpi_event_opportunity_dealers")
        and "dealer_id<>?" in statement
    )
    assert reconcile == (1, f"opp-import-{dealer_opportunity_index}", 42)
    host_insert = next(
        params for statement, params in conn.statements
        if statement.startswith("INSERT INTO vkpi_event_opportunity_dealers")
    )
    assert host_insert == (1, f"opp-import-{dealer_opportunity_index}", 42)


def test_reviewed_import_rolls_back_before_host_delete_when_exact_key_is_unresolved(monkeypatch):
    catalog = _install_importable_reviewed_catalog(monkeypatch)
    dealer_opportunity = next(
        item for item in catalog["opportunities"]
        if item.get("lane") in {"dealer_event", "local_activity"}
    )
    dealer_opportunity["dealer_stable_location_key"] = "dealer_loc_12345678"
    conn = _ImportConnection()
    monkeypatch.setattr(radar, "get_conn", lambda: conn)
    monkeypatch.setattr(radar, "table_exists", lambda name: name in IMPORT_REQUIRED_TABLES)

    with pytest.raises(ValueError, match="exactly one reviewed Dealer"):
        radar.import_reviewed_catalog(record_only=False, organization_id=1)

    assert conn.commits == 0
    assert conn.rollbacks == 1
    assert "FROM vkpi_dealer_identity_aliases" in conn.statements[-1][0]
    assert not any(
        statement.startswith("DELETE FROM vkpi_event_opportunity_dealers")
        and "dealer_id<>?" in statement
        for statement, _params in conn.statements
    )


def test_dealer_host_key_change_invalidates_approval_and_reconciles_one_host(monkeypatch):
    catalog = _install_importable_reviewed_catalog(monkeypatch)
    dealer_opportunity = next(
        item for item in catalog["opportunities"]
        if item.get("lane") in {"dealer_event", "local_activity"}
    )
    old_item = dict(dealer_opportunity, dealer_stable_location_key="dealer_loc_aaaaaaaa")
    old_item.update(
        content_hash=radar._content_hash(old_item),
        decision_status="approved",
        metadata_json=json.dumps({"dealer_stable_location_key": "dealer_loc_aaaaaaaa"}),
    )
    dealer_opportunity["dealer_stable_location_key"] = "dealer_loc_bbbbbbbb"
    conn = _ImportConnection(
        {str(dealer_opportunity["canonical_key"]): old_item},
        dealer_by_location={"dealer_loc_bbbbbbbb": 84},
    )
    monkeypatch.setattr(radar, "get_conn", lambda: conn)
    monkeypatch.setattr(radar, "table_exists", lambda name: name in IMPORT_REQUIRED_TABLES)

    result = radar.import_reviewed_catalog(record_only=False, organization_id=1)

    assert result["updated"] == 1
    assert result["invalidated_approvals"] == 1
    invalidation = next(
        params for statement, params in conn.statements
        if statement.startswith("INSERT INTO vkpi_event_opportunity_changes")
        and params[3] == "approval_invalidated"
    )
    assert "dealer_stable_location_key" in json.loads(invalidation[6])
    assert any(
        statement.startswith("DELETE FROM vkpi_event_opportunity_dealers")
        and "dealer_id<>?" in statement and params[-1] == 84
        for statement, params in conn.statements
    )


def test_removed_dealer_host_key_clears_old_host_in_same_transaction(monkeypatch):
    catalog = _install_importable_reviewed_catalog(monkeypatch)
    dealer_opportunity = next(
        item for item in catalog["opportunities"]
        if item.get("lane") in {"dealer_event", "local_activity"}
    )
    opportunity_index = catalog["opportunities"].index(dealer_opportunity) + 1
    old_item = dict(dealer_opportunity, dealer_stable_location_key="dealer_loc_aaaaaaaa")
    old_item.update(
        content_hash=radar._content_hash(old_item),
        decision_status="approved",
        metadata_json=json.dumps({"dealer_stable_location_key": "dealer_loc_aaaaaaaa"}),
    )
    conn = _ImportConnection({str(dealer_opportunity["canonical_key"]): old_item})
    monkeypatch.setattr(radar, "get_conn", lambda: conn)
    monkeypatch.setattr(radar, "table_exists", lambda name: name in IMPORT_REQUIRED_TABLES)

    result = radar.import_reviewed_catalog(record_only=False, organization_id=1)

    assert result["updated"] == 1
    assert result["invalidated_approvals"] == 1
    cleared = [
        params for statement, params in conn.statements
        if statement.startswith("DELETE FROM vkpi_event_opportunity_dealers")
        and "dealer_id<>?" not in statement
    ]
    assert (1, f"opp-import-{opportunity_index}") in cleared


def test_reviewed_import_conflicts_are_scoped_to_workspace(monkeypatch):
    _install_importable_reviewed_catalog(monkeypatch)
    conn = _ImportConnection()
    monkeypatch.setattr(radar, "get_conn", lambda: conn)
    monkeypatch.setattr(radar, "table_exists", lambda name: name in IMPORT_REQUIRED_TABLES)

    result = radar.import_reviewed_catalog(record_only=False, organization_id=2)

    assert result["inserted"] == 24
    opportunity_upsert = next(
        sql for sql, _ in conn.statements
        if sql.startswith("INSERT INTO vkpi_event_opportunities")
    )
    assert "ON CONFLICT (organization_id, canonical_key) DO UPDATE" in opportunity_upsert
    assert "organization_id=excluded.organization_id" not in opportunity_upsert
    observation_insert = next(
        sql for sql, _ in conn.statements
        if sql.startswith("INSERT INTO vkpi_event_source_observations")
    )
    assert (
        "ON CONFLICT (organization_id, source_id, external_event_key, content_hash) DO NOTHING"
        in observation_insert
    )
    scoped_inserts = [
        params for sql, params in conn.statements
        if sql.startswith((
            "INSERT INTO vkpi_event_source_runs",
            "INSERT INTO vkpi_event_opportunities",
            "INSERT INTO vkpi_event_source_observations",
            "INSERT INTO vkpi_event_opportunity_changes",
        ))
    ]
    assert scoped_inserts
    assert all(params[0] == 2 for params in scoped_inserts)


def test_reviewed_import_invalidates_approved_opportunity_when_truth_hash_changes(monkeypatch):
    catalog_item = dict(radar.load_reviewed_catalog()["opportunities"][0])
    catalog_item.update(
        {
            "content_hash": "stale-approved-hash",
            "decision_status": "approved",
            "decision_note": "approved against the prior source snapshot",
            "decision_by": 7,
            "decision_at": "2026-07-12T12:00:00Z",
        }
    )
    _install_importable_reviewed_catalog(monkeypatch)
    conn = _ImportConnection({str(catalog_item["canonical_key"]): catalog_item})
    monkeypatch.setattr(radar, "get_conn", lambda: conn)
    monkeypatch.setattr(radar, "table_exists", lambda name: name in IMPORT_REQUIRED_TABLES)

    result = radar.import_reviewed_catalog(record_only=False, organization_id=1)

    assert result["updated"] == 1
    assert result["invalidated_approvals"] == 1
    upsert = next(
        sql for sql, _ in conn.statements
        if sql.startswith("INSERT INTO vkpi_event_opportunities")
    )
    assert "decision_status='approved'" in upsert
    assert "THEN 'needs_review'" in upsert
    assert "THEN '' ELSE vkpi_event_opportunities.decision_note" in upsert
    assert "THEN NULL ELSE vkpi_event_opportunities.decision_by" in upsert
    assert "THEN NULL ELSE vkpi_event_opportunities.decision_at" in upsert
    invalidation = next(
        params for sql, params in conn.statements
        if sql.startswith("INSERT INTO vkpi_event_opportunity_changes")
        and params[3] == "approval_invalidated"
    )
    assert invalidation[4] == "stale-approved-hash"
    assert "decision_status" in json.loads(invalidation[6])


def test_reviewer_only_refresh_creates_observation_without_invalidating_approval(monkeypatch):
    catalog = _install_importable_reviewed_catalog(monkeypatch)
    current = catalog["opportunities"][0]
    old_item = dict(current, reviewer_id="staff_8")
    old_item.setdefault("is_online", False)
    old_item.setdefault("registration_url", "")
    old_item.setdefault("viltrox_presence_status", "unknown")
    old_item.setdefault("viltrox_evidence_url", "")
    old_item.update(
        content_hash=radar._content_hash(old_item),
        decision_status="approved",
        metadata_json=json.dumps({}),
    )
    conn = _ImportConnection({str(current["canonical_key"]): old_item})
    monkeypatch.setattr(radar, "get_conn", lambda: conn)
    monkeypatch.setattr(radar, "table_exists", lambda name: name in IMPORT_REQUIRED_TABLES)

    result = radar.import_reviewed_catalog(record_only=False, organization_id=1)

    assert result["unchanged"] == 1
    assert result["invalidated_approvals"] == 0
    assert not any(
        params[3] in {"approval_invalidated", "source_update"}
        for statement, params in conn.statements
        if statement.startswith("INSERT INTO vkpi_event_opportunity_changes")
    )
    assert any(
        statement.startswith("INSERT INTO vkpi_event_source_observations")
        and params[4] == current["external_event_key"]
        for statement, params in conn.statements
    )


def test_reviewed_import_does_not_reopen_an_already_promoted_opportunity(monkeypatch):
    catalog_item = dict(radar.load_reviewed_catalog()["opportunities"][0])
    catalog_item.update(
        {
            "content_hash": "stale-promoted-hash",
            "decision_status": "promoted",
            "decision_note": "immutable promotion",
            "decision_by": 7,
            "decision_at": "2026-07-12T12:00:00Z",
        }
    )
    _install_importable_reviewed_catalog(monkeypatch)
    conn = _ImportConnection({str(catalog_item["canonical_key"]): catalog_item})
    monkeypatch.setattr(radar, "get_conn", lambda: conn)
    monkeypatch.setattr(radar, "table_exists", lambda name: name in IMPORT_REQUIRED_TABLES)

    result = radar.import_reviewed_catalog(record_only=False, organization_id=1)

    assert result["updated"] == 1
    assert result["invalidated_approvals"] == 0
    change_kinds = [
        params[3] for sql, params in conn.statements
        if sql.startswith("INSERT INTO vkpi_event_opportunity_changes")
    ]
    assert "source_update" in change_kinds
    assert "approval_invalidated" not in change_kinds


def test_content_hash_tracks_execution_truth_but_not_derived_ranking():
    item = dict(radar.load_reviewed_catalog()["opportunities"][0])
    baseline = radar._content_hash(item)

    date_precision_changed = dict(item, date_precision="month_only")
    online_changed = dict(item, is_online=not bool(item.get("is_online")))
    confidence_changed = dict(item, confidence=float(item.get("confidence") or 0) + 0.1)
    relevance_changed = dict(item, relevance_score=99.0, relevance_basis="recomputed")
    reviewer_changed = dict(item, reviewer_id="staff_8", evidence_scope="re_review")
    dealer_changed = dict(item, dealer_stable_location_key="dealer_loc_aaaaaaaa")

    assert radar._content_hash(date_precision_changed) != baseline
    assert radar._content_hash(online_changed) != baseline
    assert radar._content_hash(confidence_changed) == baseline
    assert radar._content_hash(relevance_changed) == baseline
    assert radar._content_hash(reviewer_changed) == baseline
    assert radar._content_hash(dealer_changed) != baseline
    assert "reviewer_id" not in radar._changed_fields(item, reviewer_changed)
    assert "dealer_stable_location_key" in radar._changed_fields(item, dealer_changed)


def test_decision_state_machine_requires_watching_before_approval(monkeypatch):
    conn = _install(monkeypatch, _opportunity())

    with pytest.raises(ValueError, match="new -> approved"):
        radar.decide(
            "opp_gate",
            decision_status="approved",
            staff={"id": 7, "organization_id": 1},
        )

    assert conn.commits == 0
    assert not any(sql.startswith("UPDATE vkpi_event_opportunities") for sql, _ in conn.statements)


def test_decision_state_machine_allows_reviewed_new_watching_approved_path(monkeypatch):
    conn = _install(monkeypatch, _opportunity())
    staff = {"id": 7, "organization_id": 1}

    watching = radar.decide("opp_gate", decision_status="watching", staff=staff)
    approved = radar.decide("opp_gate", decision_status="approved", staff=staff)

    assert watching["item"]["decision_status"] == "watching"
    assert approved["item"]["decision_status"] == "approved"
    assert conn.commits == 2


def test_concurrent_promotion_cannot_be_overwritten_by_stale_decision(monkeypatch):
    conn = _PromoteWinsDecisionRaceConnection(_opportunity(decision_status="approved"))
    monkeypatch.setattr(radar, "get_conn", lambda: conn)

    with pytest.raises(radar.EventRadarStateConflict, match="reload and retry"):
        radar.decide(
            "opp_gate",
            decision_status="watching",
            staff={"id": 7, "organization_id": 1},
        )

    assert conn.opportunity["decision_status"] == "promoted"
    assert conn.commits == 0
    assert conn.rollbacks == 1


def test_promotion_claim_losing_to_concurrent_decision_fails_before_writes(monkeypatch):
    conn = _DecisionWinsPromotionRaceConnection(_opportunity(decision_status="approved"))
    monkeypatch.setattr(radar, "get_conn", lambda: conn)

    with pytest.raises(radar.EventRadarStateConflict, match="promotion claim"):
        radar.promote("opp_gate", staff={"id": 7, "organization_id": 1})

    assert conn.opportunity["decision_status"] == "watching"
    assert conn.rollbacks == 1
    assert not any(sql.startswith("INSERT INTO vkpi_events") for sql, _ in conn.statements)


def test_catalog_refresh_invalidates_stale_decision_before_write(monkeypatch):
    conn = _CatalogRefreshWinsDecisionRaceConnection(_opportunity(decision_status="watching"))
    monkeypatch.setattr(radar, "get_conn", lambda: conn)

    with pytest.raises(radar.EventRadarStateConflict, match="reload and retry"):
        radar.decide(
            "opp_gate",
            decision_status="approved",
            staff={"id": 7, "organization_id": 1},
        )

    assert conn.opportunity["event_status"] == "cancelled"
    assert conn.opportunity["decision_status"] == "watching"
    assert conn.rollbacks == 1


def test_approval_cas_rejects_freshness_anchor_drift(monkeypatch):
    anchor = datetime.now(timezone.utc).isoformat()
    conn = _FreshnessDriftsBeforeApprovalCasConnection(
        _opportunity(decision_status="watching", last_verified_at=anchor)
    )
    monkeypatch.setattr(radar, "get_conn", lambda: conn)
    monkeypatch.setattr(radar, "is_postgres_runtime", lambda: False)

    with pytest.raises(radar.EventRadarStateConflict, match="reload and retry"):
        radar.decide(
            "opp_gate",
            decision_status="approved",
            staff={"id": 7, "organization_id": 1},
        )

    approval_sql, approval_params = next(
        entry for entry in conn.statements
        if entry[0].startswith("UPDATE vkpi_event_opportunities SET decision_status=?")
        and entry[1][0] == "approved"
    )
    assert "COALESCE(last_verified_at, source_checked_at)=?" in approval_sql
    assert approval_params[-1] == anchor
    assert conn.rollbacks == 1


def test_promotion_cas_rejects_freshness_anchor_drift_before_event_write(monkeypatch):
    anchor = datetime.now(timezone.utc).isoformat()
    conn = _FreshnessDriftsBeforePromotionCasConnection(
        _opportunity(decision_status="approved", last_verified_at=anchor)
    )
    monkeypatch.setattr(radar, "get_conn", lambda: conn)
    monkeypatch.setattr(radar, "is_postgres_runtime", lambda: False)

    with pytest.raises(radar.EventRadarStateConflict, match="promotion claim"):
        radar.promote("opp_gate", staff={"id": 7, "organization_id": 1})

    claim_sql, claim_params = next(
        entry for entry in conn.statements
        if entry[0].startswith("UPDATE vkpi_event_opportunities SET decision_status='promoted'")
    )
    assert "COALESCE(last_verified_at, source_checked_at)=?" in claim_sql
    assert claim_params[-1] == anchor
    assert conn.rollbacks == 1
    assert not any(sql.startswith("INSERT INTO vkpi_events") for sql, _ in conn.statements)


def test_postgres_source_rebind_after_lock_fails_closed(monkeypatch):
    conn = _SourceIdentityChangesAfterLockConnection(
        _opportunity(decision_status="approved")
    )
    monkeypatch.setattr(radar, "get_conn", lambda: conn)
    monkeypatch.setattr(radar, "is_postgres_runtime", lambda: True)

    with pytest.raises(radar.EventRadarStateConflict, match="source changed"):
        radar.promote("opp_gate", staff={"id": 7, "organization_id": 1})

    assert conn.rollbacks == 1
    assert not any(sql.startswith("UPDATE vkpi_event_opportunities") for sql, _ in conn.statements)
    assert not any(sql.startswith("INSERT INTO vkpi_events") for sql, _ in conn.statements)


def test_source_disable_invalidates_stale_promotion_before_event_write(monkeypatch):
    conn = _SourceDisableWinsPromotionRaceConnection(_opportunity(decision_status="approved"))
    monkeypatch.setattr(radar, "get_conn", lambda: conn)

    with pytest.raises(radar.EventRadarStateConflict, match="promotion claim"):
        radar.promote("opp_gate", staff={"id": 7, "organization_id": 1})

    assert conn.opportunity["source_status"] == "blocked"
    assert conn.rollbacks == 1
    assert not any(sql.startswith("INSERT INTO vkpi_events") for sql, _ in conn.statements)


def test_promotion_claim_losing_to_same_operation_returns_idempotent_receipt(monkeypatch):
    conn = _PromotionWinsPromotionRaceConnection(_opportunity(decision_status="approved"))
    monkeypatch.setattr(radar, "get_conn", lambda: conn)

    result = radar.promote("opp_gate", staff={"id": 7, "organization_id": 1})

    assert result == {
        "ok": True,
        "idempotent": True,
        "event_id": "evt_other_request",
        "promoted_at": "2026-07-13T00:00:01Z",
    }
    assert conn.commits == 1
    assert conn.rollbacks == 0
    assert not any(sql.startswith("INSERT INTO vkpi_events") for sql, _ in conn.statements)


def test_repeated_promotion_closes_idempotent_transaction(monkeypatch):
    conn = _IdempotentPromotionConnection(_opportunity(decision_status="promoted"))
    monkeypatch.setattr(radar, "get_conn", lambda: conn)

    result = radar.promote("opp_gate", staff={"id": 7, "organization_id": 1})

    assert result == {
        "ok": True,
        "idempotent": True,
        "event_id": "evt_existing",
        "promoted_at": "2026-07-13T00:00:00Z",
    }
    assert conn.commits == 1
    assert conn.rollbacks == 0
    assert not any(sql.startswith("INSERT INTO vkpi_events") for sql, _ in conn.statements)


@pytest.mark.parametrize(
    ("source_status", "source_enabled", "error"),
    [(status, True, "not active") for status in ("hold", "blocked", "retired")]
    + [("active", False, "disabled")],
)
def test_inactionable_source_cannot_be_decided_or_promoted(
    monkeypatch, source_status, source_enabled, error,
):
    conn = _install(monkeypatch, _opportunity(
        source_status=source_status, source_enabled=source_enabled, decision_status="approved",
    ))
    staff = {"id": 7, "organization_id": 1}

    with pytest.raises(ValueError, match=f"source is {error}"):
        radar.decide("opp_gate", decision_status="watching", staff=staff)
    with pytest.raises(ValueError, match=f"source is {error}"):
        radar.promote("opp_gate", staff=staff)

    assert not any(sql.startswith("INSERT INTO vkpi_events") for sql, _ in conn.statements)


def test_stale_verification_cannot_be_approved_or_promoted(monkeypatch):
    stale = (datetime.now(timezone.utc) - timedelta(days=31)).isoformat()
    conn = _install(monkeypatch, _opportunity(decision_status="watching", last_verified_at=stale))
    staff = {"id": 7, "organization_id": 1}

    with pytest.raises(ValueError, match="verification is not current"):
        radar.decide("opp_gate", decision_status="approved", staff=staff)
    conn.opportunity["decision_status"] = "approved"
    with pytest.raises(ValueError, match="verification is not current"):
        radar.promote("opp_gate", staff=staff)


def test_future_verification_timestamp_is_not_treated_as_current(monkeypatch):
    future = (datetime.now(timezone.utc) + timedelta(days=2)).isoformat()
    _install(monkeypatch, _opportunity(decision_status="watching", last_verified_at=future))

    with pytest.raises(ValueError, match="verification is not current"):
        radar.decide(
            "opp_gate",
            decision_status="approved",
            staff={"id": 7, "organization_id": 1},
        )


def test_cross_workspace_opportunity_is_not_visible_to_writer(monkeypatch):
    _install(monkeypatch, _opportunity(organization_id=2), actor_org=1)

    with pytest.raises(LookupError, match="not found"):
        radar.decide(
            "opp_gate",
            decision_status="watching",
            staff={"id": 7, "organization_id": 1},
        )


def test_active_current_promotion_records_workspace_without_perfect_health(monkeypatch):
    conn = _install(monkeypatch, _opportunity(decision_status="approved"))

    result = radar.promote(
        "opp_gate",
        staff={"id": 7, "organization_id": 1},
    )

    assert result["ok"] is True
    event_insert = next((entry for entry in conn.statements if entry[0].startswith("INSERT INTO vkpi_events")), None)
    assert event_insert is not None
    assert event_insert[1][0] == 1
    assert "'planning',NULL" in event_insert[0]
    promotion_insert = next(
        entry for entry in conn.statements if entry[0].startswith("INSERT INTO vkpi_event_opportunity_promotions")
    )
    assert promotion_insert[1][0] == 1


@pytest.mark.parametrize("postgres_runtime", [False, True])
def test_promotion_cas_sql_is_workspace_scoped_and_dialect_safe(monkeypatch, postgres_runtime):
    conn = _TruthGateConnection(_opportunity(decision_status="approved"))
    monkeypatch.setattr(radar, "get_conn", lambda: conn)
    monkeypatch.setattr(radar, "is_postgres_runtime", lambda: postgres_runtime)

    radar.promote("opp_gate", staff={"id": 7, "organization_id": 1})

    statements = [sql for sql, _ in conn.statements]
    claim = next(
        sql for sql in statements
        if sql.startswith("UPDATE vkpi_event_opportunities SET decision_status='promoted'")
    )
    event_insert = next(sql for sql in statements if sql.startswith("INSERT INTO vkpi_events"))
    assert "organization_id=? AND decision_status='approved'" in claim
    assert "content_hash=?" in claim
    assert "source_id=?" in claim
    assert "source.status='active'" in claim
    assert "event_status='scheduled'" in claim
    assert "COALESCE(last_verified_at, source_checked_at)" in claim
    assert "NOT EXISTS" in claim
    if postgres_runtime:
        assert "COALESCE(last_verified_at, source_checked_at)::text=?" in claim
        assert any("verification_anchor_token" in sql for sql in statements)
        source_seed_index = next(
            index for index, sql in enumerate(statements)
            if sql.startswith("SELECT source_id FROM vkpi_event_opportunities")
        )
        source_lock_index = next(
            index for index, sql in enumerate(statements)
                if sql.startswith("SELECT id,status,enabled FROM vkpi_event_watch_targets")
            and "FOR UPDATE" in sql
        )
        opportunity_lock_index = next(
            index for index, sql in enumerate(statements)
            if "FROM vkpi_event_opportunities o" in sql and "FOR UPDATE OF o" in sql
        )
        claim_index = statements.index(claim)
        assert source_seed_index < source_lock_index < opportunity_lock_index < claim_index
        assert "NOW()" in claim
        assert "?::jsonb" in event_insert
    else:
        assert "COALESCE(last_verified_at, source_checked_at)=?" in claim
        assert all("FOR UPDATE" not in sql for sql in statements)
        assert "CURRENT_TIMESTAMP" in claim
        assert "::jsonb" not in event_insert
