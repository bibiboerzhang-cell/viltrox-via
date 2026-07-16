from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
UP = ROOT / "migrations/261_vkpi_dealer_activity_candidate_sync.sql"
DOWN = ROOT / "migrations/261_vkpi_dealer_activity_candidate_sync_down.sql"


def _shape(path: Path) -> str:
    return " ".join(path.read_text(encoding="utf-8").lower().split())


def test_migration_261_registers_default_off_candidate_only_scheduler_task() -> None:
    sql = _shape(UP)

    assert "insert into scheduler_tasks" in sql
    assert "'vkpi_dealer_activity_candidate_sync'" in sql
    assert "false,48,0" in sql
    assert "on conflict (task_key) do nothing" in sql
    assert "insert into vkpi_dealer_event_candidates" not in sql
    assert "insert into vkpi_events" not in sql
    assert "insert into vkpi_event_opportunities" not in sql
    assert "activity_sync_claim_token" in sql
    assert "activity_sync_claim_organization_id" in sql
    assert "activity_sync_claim_expires_at" in sql
    assert "update vkpi_event_watch_targets" not in sql
    assert "begin;" not in sql
    assert "commit;" not in sql


def test_migration_261_down_removes_only_task_and_schema_receipt() -> None:
    sql = _shape(DOWN)

    assert "delete from scheduler_tasks where task_key = 'vkpi_dealer_activity_candidate_sync'" in sql
    assert "where version_key = '261_vkpi_dealer_activity_candidate_sync.sql'" in sql
    assert "drop table" not in sql
    assert "drop column if exists activity_sync_claim_token" in sql
    assert "drop index if exists idx_event_watch_activity_sync_claim" in sql
    assert "delete from vkpi_dealer_event_candidates" not in sql
