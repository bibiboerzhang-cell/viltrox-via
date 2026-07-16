from __future__ import annotations

from pathlib import Path

from app.db import connection


ROOT = Path(__file__).resolve().parents[1]
UP = ROOT / "migrations/259_vkpi_dealer_reviewed_evidence.sql"
DOWN = ROOT / "migrations/259_vkpi_dealer_reviewed_evidence_down.sql"


def test_migration_259_is_discovered_after_llm_budget_reservations() -> None:
    sequence = connection._discover_postgres_migrations()
    reservation = "258_vkpi_llm_budget_reservations.sql"
    reviewed_dealer = "259_vkpi_dealer_reviewed_evidence.sql"
    assert reservation in sequence
    assert reviewed_dealer in sequence
    assert sequence.index(reservation) < sequence.index(reviewed_dealer)


def test_migration_259_is_fail_closed_and_rollback_is_guarded() -> None:
    up = UP.read_text(encoding="utf-8").lower()
    down = DOWN.read_text(encoding="utf-8").lower()
    assert "begin;" not in up
    assert "commit;" not in up
    for column in (
        "source_id",
        "stable_org_key",
        "stable_location_key",
        "reviewer_id",
        "reviewed_at",
        "evidence_json",
        "review_contract_version",
    ):
        assert f"add column if not exists {column}" in up
    assert "review_contract_version in (0, 1)" in " ".join(up.split())
    assert "evidence_json ->> 'claim_status' = 'descriptive_only'" in up
    assert "evidence_json #>> '{source,source_id}' = source_id" in up
    assert "evidence_json #>> '{product,source_url}' = brand_listing_url" in up
    assert "where review_contract_version = 1" in " ".join(up.split())
    assert "cannot roll back 259 while reviewed dealer evidence receipts exist" in down
    assert (
        "where version_key = '259_vkpi_dealer_reviewed_evidence.sql'"
        in " ".join(down.split())
    )
