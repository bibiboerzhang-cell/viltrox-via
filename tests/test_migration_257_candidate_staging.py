from __future__ import annotations

import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
UP_PATH = ROOT / "migrations/257_vkpi_dealer_event_candidate_staging.sql"
DOWN_PATH = ROOT / "migrations/257_vkpi_dealer_event_candidate_staging_down.sql"
UP = UP_PATH.read_text(encoding="utf-8")
DOWN = DOWN_PATH.read_text(encoding="utf-8")


def _shape(value: str) -> str:
    return " ".join(value.split()).lower()


def test_migration_257_is_next_after_256_and_has_no_placeholder_trap():
    from app.db import connection

    sequence = connection._POSTGRES_MIGRATION_SEQUENCE
    assert UP_PATH.name in sequence
    assert sequence.index(UP_PATH.name) == sequence.index(
        "256_vkpi_financial_artifact_invalidation.sql"
    ) + 1
    assert "?" not in UP
    assert re.search(r"(?mi)^\s*(begin|commit)\s*;", UP) is None


def test_candidate_schema_is_org_scoped_and_evidence_linked():
    sql = _shape(UP)
    assert "create table if not exists vkpi_dealer_event_candidates" in sql
    assert "create table if not exists vkpi_candidate_field_evidence_links" in sql
    assert "primary key (organization_id, id)" in sql
    assert "foreign key (organization_id, source_passport_id)" in sql
    assert "references vkpi_source_passports(organization_id, id)" in sql
    assert "foreign key (organization_id, field_evidence_id)" in sql
    assert "references vkpi_source_field_evidence(organization_id, id)" in sql
    assert "unique index if not exists uq_candidate_source_identity" in sql
    assert "unique index if not exists uq_candidate_dealer_location" in sql
    assert "stable_location_key ~ '^dealer_loc_[a-z0-9]{8,64}$'" in sql
    assert "stable_org_key ~ '^dealer_org_[a-z0-9]{8,64}$'" in sql


def test_source_registry_passports_are_exactly_linked_not_business_claims():
    sql = _shape(UP)
    assert "add column if not exists registry_source_id text" in sql
    assert "'source_registry'" in sql
    assert "unique index if not exists uq_source_passport_registry_source" in sql
    assert "where entity_type = 'source_registry'" in sql
    assert "claim_status text not null default 'descriptive_only'" in sql
    assert "check (claim_status = 'descriptive_only')" in sql
    assert "authorization_status" not in sql
    assert "inventory_status" not in sql
    assert "insert into vkpi_dealers" not in sql
    assert "insert into vkpi_event_opportunities" not in sql


def test_promotion_gate_is_default_blocked_current_and_human_only():
    sql = _shape(UP)
    assert "promotion_gate_status text not null default 'blocked'" in sql
    assert "eligible_for_manual_promotion" in sql
    assert "manually_promoted" in sql
    assert "automatic_promotion" not in sql
    assert "create or replace function vkpi_validate_candidate_promotion_gate()" in sql
    assert "passport_row.verification_status <> 'verified'" in sql
    assert "passport_row.identity_status <> 'exact'" in sql
    assert "passport_row.freshness_status_at_write <> 'fresh'" in sql
    assert "evidence.verification_status = 'verified'" in sql
    assert "evidence.freshness_status_at_write = 'fresh'" in sql
    assert "evidence.value_status = 'observed'" in sql
    assert "reviewer_staff_id is not null" in sql
    assert "promotion_reviewer_staff_id is not null" in sql
    assert "manual promotion receipt requires an exact existing business target" in sql
    assert "alias.organization_id = new.organization_id" in sql
    assert "alias.stable_location_key = new.stable_location_key" in sql
    assert "opportunity.organization_id = new.organization_id" in sql
    assert "opportunity.official_url = new.source_url" in sql
    assert "before insert or update on vkpi_dealer_event_candidates" in sql
    assert "old.promotion_gate_status = 'manually_promoted'" in sql
    assert "to_jsonb(new) - 'updated_at'" in sql
    assert "to_jsonb(old) - 'updated_at'" in sql
    assert "manual promotion receipt and candidate truth are immutable" in sql


def test_down_migration_is_guarded_and_registered_explicitly():
    down = _shape(DOWN)
    assert "cannot roll back 257 while source_registry passports exist" in down
    assert "drop table if exists vkpi_candidate_field_evidence_links" in down
    assert "drop table if exists vkpi_dealer_event_candidates" in down
    assert "drop column if exists registry_source_id" in down
    assert "where version_key = '257_vkpi_dealer_event_candidate_staging.sql'" in down
