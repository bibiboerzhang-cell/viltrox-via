from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
UP = ROOT / "migrations/262_vkpi_dealer_activity_claim_invariant.sql"
DOWN = ROOT / "migrations/262_vkpi_dealer_activity_claim_invariant_down.sql"


def _shape(path: Path) -> str:
    return " ".join(path.read_text(encoding="utf-8").lower().split())


def test_migration_262_makes_active_claim_branch_null_safe_without_activation() -> None:
    sql = _shape(UP)
    assert "drop constraint if exists chk_event_watch_activity_sync_claim" in sql
    assert "activity_sync_claim_token <> ''" in sql
    assert "activity_sync_claim_organization_id is not null" in sql
    assert "activity_sync_claimed_at is not null" in sql
    assert "activity_sync_claim_expires_at is not null" in sql
    assert "activity_sync_claim_expires_at > activity_sync_claimed_at" in sql
    assert "update vkpi_event_watch_targets" not in sql
    assert "insert into scheduler_tasks" not in sql
    assert "begin;" not in sql and "commit;" not in sql


def test_migration_262_down_restores_261_shape_and_only_deletes_receipt() -> None:
    sql = _shape(DOWN)
    assert "add constraint chk_event_watch_activity_sync_claim" in sql
    assert "activity_sync_claim_token <> ''" not in sql
    assert "where version_key = '262_vkpi_dealer_activity_claim_invariant.sql'" in sql
    assert "drop table" not in sql
    assert "delete from vkpi_dealer_event_candidates" not in sql
