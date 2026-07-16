from __future__ import annotations

import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
UP = (ROOT / "migrations/244_vkpi_event_radar_truth_scope.sql").read_text(encoding="utf-8")
DOWN = (ROOT / "migrations/244_vkpi_event_radar_truth_scope_down.sql").read_text(encoding="utf-8")
RADAR = (ROOT / "backend/app/domains/events/radar.py").read_text(encoding="utf-8")
RADAR_IMPORT = (ROOT / "backend/app/domains/events/radar_import.py").read_text(
    encoding="utf-8"
)


def _sql_shape(value: str) -> str:
    return " ".join(value.split()).lower()


def test_forward_migration_does_not_end_runner_transaction():
    # connection._run_postgres_migrations owns the transaction and its
    # pg_advisory_xact_lock.  A transaction statement inside a forward file
    # would release that lock before schema_migrations is recorded.
    assert re.search(r"(?mi)^\s*(begin|commit)\s*;", UP) is None
    assert "do not add" in UP.lower()
    assert "pg_advisory_xact_lock" in UP


def test_event_radar_natural_and_primary_keys_are_workspace_scoped():
    sql = _sql_shape(UP)
    required = (
        "primary key (organization_id, id)",
        "unique (organization_id, run_key)",
        "unique (organization_id, canonical_key)",
        "unique (organization_id, source_id, external_event_key)",
        "unique (organization_id, source_id, external_event_key, content_hash)",
        "primary key (organization_id, opportunity_id, dealer_id, relation_type)",
        "primary key (organization_id, opportunity_id)",
        "unique (organization_id, event_id)",
    )
    for contract in required:
        assert contract in sql


def test_event_radar_relationships_cannot_cross_workspaces():
    sql = _sql_shape(UP)
    required = (
        "foreign key (organization_id, run_id) references vkpi_event_source_runs(organization_id, id)",
        "foreign key (organization_id, opportunity_id) references vkpi_event_opportunities(organization_id, id)",
        "foreign key (organization_id, observation_id) references vkpi_event_source_observations(organization_id, id)",
        "foreign key (organization_id, event_id) references vkpi_events(organization_id, id)",
    )
    for contract in required:
        assert contract in sql
    assert "on delete set null (run_id)" in sql
    assert "on delete set null (opportunity_id)" in sql
    assert "on delete set null (observation_id)" in sql


def test_dealer_alias_uniqueness_is_per_workspace_and_claim_bounded():
    sql = _sql_shape(UP)
    assert "unique(organization_id, alias_type, alias_normalized, country_code)" in sql
    assert "check (organization_id > 0)" in sql
    assert "stable_org_key like 'dealer_org_%'" in sql
    assert "stable_location_key like 'dealer_loc_%'" in sql
    assert "never proves viltrox authorization" in sql


def test_forward_constraint_rebuild_is_directly_rerunnable():
    # The automatic runner normally prevents a second execution with its ledger.
    # These guards also make a direct operator retry converge after an interrupted
    # test or a missing ledger marker.
    sql = _sql_shape(UP)
    rebuilt = (
        "fk_event_observations_org_run",
        "fk_event_observations_org_opportunity",
        "fk_event_changes_org_opportunity",
        "fk_event_changes_org_observation",
        "fk_event_dealers_org_opportunity",
        "fk_event_promotions_org_opportunity",
        "fk_event_promotions_org_event",
        "uq_event_source_runs_org_run_key",
        "uq_event_source_runs_org_id",
        "uq_event_opportunities_org_canonical",
        "uq_event_opportunities_org_source_external",
        "uq_event_observations_org_id",
        "uq_event_observations_org_source_external_hash",
        "uq_event_promotions_org_event",
        "uq_vkpi_events_org_id",
    )
    for name in rebuilt:
        assert f"drop constraint if exists {name}" in sql
        assert f"add constraint {name}" in sql


def test_import_upserts_match_workspace_unique_contract():
    # The public service is a compatibility facade; scan the focused
    # transaction implementation as well so this migration contract survives
    # responsibility-preserving module splits.
    shape = _sql_shape(f"{RADAR}\n{RADAR_IMPORT}")
    assert "on conflict (organization_id, canonical_key) do update" in shape
    assert "on conflict (organization_id, source_id, external_event_key, content_hash) do nothing" in shape
    assert "organization_id=excluded.organization_id" not in shape


def test_down_migration_is_fail_closed_and_repairs_ledger():
    sql = _sql_shape(DOWN)
    assert sql.startswith("-- roll back migration 244")
    assert "begin;" in sql and sql.endswith("commit;")
    assert "where organization_id <> 1" in sql
    assert "export dealer identity aliases first" in sql
    assert "delete from schema_migrations where version_key = '244_vkpi_event_radar_truth_scope.sql'" in sql
    assert "primary key (id)" in sql
    assert "unique (canonical_key)" in sql
