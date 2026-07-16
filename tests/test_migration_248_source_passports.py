from __future__ import annotations

import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
UP_PATH = ROOT / "migrations/248_vkpi_dealer_event_source_passports.sql"
DOWN_PATH = ROOT / "migrations/248_vkpi_dealer_event_source_passports_down.sql"
UP = UP_PATH.read_text(encoding="utf-8")
DOWN = DOWN_PATH.read_text(encoding="utf-8")


def _shape(value: str) -> str:
    return " ".join(value.split()).lower()


def test_migration_248_is_registered_after_247_and_has_no_placeholder_trap():
    from app.db import connection

    sequence = connection._POSTGRES_MIGRATION_SEQUENCE
    assert UP_PATH.name in sequence
    assert sequence.index(UP_PATH.name) == sequence.index(
        "247_apify_jobs_active_idempotency.sql"
    ) + 1
    assert "?" not in UP


def test_forward_migration_leaves_transaction_control_to_runner():
    assert re.search(r"(?mi)^\s*(begin|commit)\s*;", UP) is None
    assert "migration runner owns the surrounding transaction" in UP.lower()


def test_passport_schema_is_workspace_scoped_and_exact_linked():
    sql = _shape(UP)
    for table in (
        "vkpi_source_passports",
        "vkpi_source_field_evidence",
        "vkpi_source_passport_revisions",
    ):
        assert f"create table if not exists {table}" in sql
    assert "primary key (organization_id, id)" in sql
    assert "unique index if not exists uq_source_passport_entity" in sql
    assert "on vkpi_source_passports(organization_id, entity_type, entity_key)" in sql
    assert "foreign key (organization_id, event_opportunity_id)" in sql
    assert "references vkpi_event_opportunities(organization_id, id)" in sql
    assert "foreign key (organization_id, passport_id)" in sql
    assert "references vkpi_source_passports(organization_id, id)" in sql
    assert sql.count("references staff(id) on delete restrict") == 3
    assert sql.count(
        "references vkpi_source_passports(organization_id, id) on delete restrict"
    ) == 2
    assert "exact_location_key ~ '^dealer_loc_[a-z0-9]{8,64}$'" in sql
    assert "stable_org_key ~ '^dealer_org_[a-z0-9]{8,64}$'" in sql


def test_truth_defaults_fail_closed_and_claims_cannot_be_promoted():
    sql = _shape(UP)
    assert "publisher_tier text not null default 'unknown'" in sql
    assert "identity_status text not null default 'unknown'" in sql
    assert "verification_status text not null default 'unknown'" in sql
    assert "freshness_status_at_write text not null default 'unavailable'" in sql
    assert sql.count("claim_status text not null default 'descriptive_only'") == 2
    assert sql.count("check (claim_status = 'descriptive_only')") == 2
    assert "global_coverage" not in sql
    assert "authorization_status" not in sql
    assert "inventory_status" not in sql
    assert "roi" in sql  # only the explicit no-claim comments


def test_verified_rows_require_current_review_and_known_publisher():
    sql = _shape(UP)
    assert "verification_status <> 'verified'" in sql
    assert "publisher_tier <> 'unknown'" in sql
    assert "identity_status <> 'exact'" in sql
    assert "canonical_url like 'https://%'" in sql
    assert "source_url like 'https://%'" in sql
    assert "reviewer_staff_id is not null" in sql
    assert "freshness_status_at_write = 'fresh'" in sql
    assert "value_status <> 'observed' or value_sha256 <> ''" in sql


def test_revision_history_is_append_only_and_down_is_explicit():
    sql = _shape(UP)
    assert "unique (organization_id, passport_id, revision_no)" in sql
    assert "snapshot_sha256" in sql
    assert "changed_fields jsonb" in sql
    down = _shape(DOWN)
    assert down.startswith("-- roll back migration 248")
    assert "drop table if exists vkpi_source_passport_revisions" in down
    assert "drop table if exists vkpi_source_field_evidence" in down
    assert "drop table if exists vkpi_source_passports" in down
    assert "where version_key = '248_vkpi_dealer_event_source_passports.sql'" in down
