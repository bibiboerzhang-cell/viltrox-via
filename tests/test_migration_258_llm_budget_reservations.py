from __future__ import annotations

from pathlib import Path

from app.db import connection


ROOT = Path(__file__).resolve().parents[1]
UP = ROOT / "migrations/258_vkpi_llm_budget_reservations.sql"
DOWN = ROOT / "migrations/258_vkpi_llm_budget_reservations_down.sql"


def test_migration_258_is_discovered_after_candidate_staging() -> None:
    sequence = connection._discover_postgres_migrations()
    candidate = "257_vkpi_dealer_event_candidate_staging.sql"
    reservation = "258_vkpi_llm_budget_reservations.sql"
    assert candidate in sequence
    assert reservation in sequence
    assert sequence.index(candidate) < sequence.index(reservation)


def test_migration_258_has_atomic_reservation_contract_and_rollback() -> None:
    up = UP.read_text(encoding="utf-8").lower()
    down = DOWN.read_text(encoding="utf-8").lower()
    assert "begin;" not in up
    assert "commit;" not in up
    assert "create table if not exists vkpi_llm_budget_reservations" in up
    for column in (
        "reservation_key",
        "request_hash",
        "provider_scope",
        "cost_scope",
        "cumulative_scopes_json",
        "estimated_cost_usd",
        "actual_cost_usd",
        "provider_started_at",
        "settled_at",
    ):
        assert column in up
    for state in ("reserved", "provider_started", "unknown", "settled", "released", "blocked"):
        assert f"'{state}'" in up
    assert "drop table if exists vkpi_llm_budget_reservations" in down
    assert (
        "where version_key = '258_vkpi_llm_budget_reservations.sql'"
        in " ".join(down.split())
    )
