from __future__ import annotations

import inspect
from pathlib import Path

import pytest

from app.db import connection
from scripts.ops import rehearse_apify_migrations_245_247 as rehearsal


ROOT = Path(__file__).resolve().parents[1]


def test_admin_database_guard_rejects_real_or_arbitrary_database():
    with pytest.raises(RuntimeError, match="refusing to connect to viltrox2"):
        rehearsal._validate_admin_database_name("viltrox2")
    with pytest.raises(RuntimeError, match="postgres or template1"):
        rehearsal._validate_admin_database_name("vkpi_round12_ephemeral_fake")
    assert rehearsal._validate_admin_database_name("postgres") == "postgres"
    assert rehearsal._validate_admin_database_name("template1") == "template1"
    assert rehearsal._validate_admin_server("postgres", None) == "postgres"
    with pytest.raises(RuntimeError, match="Unix-domain socket"):
        rehearsal._validate_admin_server("postgres", "127.0.0.1")


def test_target_name_is_ephemeral_only(monkeypatch):
    monkeypatch.setattr(rehearsal.secrets, "token_hex", lambda _size: "a1b2c3d4e5f60708")
    assert rehearsal._target_database_name() == "vkpi_round12_ephemeral_a1b2c3d4e5f60708"


def test_current_forward_chain_is_discovered_through_244():
    files = rehearsal._discover_forward_migrations(
        ROOT, through="244_vkpi_event_radar_truth_scope.sql"
    )
    assert files[-1].name == "244_vkpi_event_radar_truth_scope.sql"
    assert len(files) == 240
    assert files[0].name == "003_postgres_baseline.sql"
    assert not any(path.name.endswith("_down.sql") for path in files)
    assert "001_verification.sql" not in {path.name for path in files}
    assert "245_vkpi_staff_organization_membership_backfill.sql" not in {
        path.name for path in files
    }


def test_rehearsal_discovery_exactly_matches_production_manifest_through_244():
    assert rehearsal.EXCLUDED_FORWARD == connection._MIGRATION_EXCLUDE
    assert (
        rehearsal.RUNNER_OWNED_TRANSACTION_MIN_VERSION
        == connection._RUNNER_OWNED_TRANSACTION_MIN_VERSION
    )
    assert (
        rehearsal.FORWARD_TRANSACTION_CONTROL_RE.pattern
        == connection._FORWARD_TRANSACTION_CONTROL_RE.pattern
    )
    files = rehearsal._discover_forward_migrations(
        ROOT, through="244_vkpi_event_radar_truth_scope.sql"
    )
    production = list(connection._POSTGRES_MIGRATION_SEQUENCE)
    boundary = production.index("244_vkpi_event_radar_truth_scope.sql") + 1
    assert [path.name for path in files] == production[:boundary]


def test_rehearsal_discovery_fails_closed_on_missing_boundary_or_transaction_control(
    tmp_path: Path,
):
    migrations = tmp_path / "migrations"
    migrations.mkdir()
    (migrations / "003_postgres_baseline.sql").write_text(
        "CREATE TABLE baseline(id BIGINT);\n", encoding="utf-8"
    )
    with pytest.raises(RuntimeError, match="forward migration boundary missing"):
        rehearsal._discover_forward_migrations(tmp_path, through="244_missing.sql")

    (migrations / "234_bad.sql").write_text(
        "CREATE TABLE bad(id BIGINT);\nCOMMIT;\n", encoding="utf-8"
    )
    with pytest.raises(RuntimeError, match="transaction control owned"):
        rehearsal._discover_forward_migrations(tmp_path, through="234_bad.sql")


def test_schema_migrations_manifest_must_match_exactly():
    class Result:
        def __init__(self, rows):
            self.rows = rows

        def fetchall(self):
            return self.rows

    class Connection:
        def __init__(self, rows):
            self.rows = rows

        def execute(self, statement):
            assert "FROM schema_migrations" in statement
            return Result(self.rows)

    expected = ["003_postgres_baseline.sql", "005_via_control_stack.sql"]
    state = rehearsal._assert_schema_migration_manifest(
        Connection([(expected[0],), (expected[1],)]), expected
    )
    assert state == {
        "applied_count": 2,
        "first": "003_postgres_baseline.sql",
        "last": "005_via_control_stack.sql",
        "exact_manifest_match": True,
    }
    with pytest.raises(AssertionError, match="schema_migrations manifest mismatch"):
        rehearsal._assert_schema_migration_manifest(
            Connection([("003_postgres_baseline.sql",), ("999_unexpected.sql",)]),
            expected,
        )


def test_rehearsal_targets_exact_migrations_and_247_rollback():
    assert rehearsal.MIGRATION_245 == "245_vkpi_staff_organization_membership_backfill.sql"
    assert rehearsal.MIGRATION_246 == "246_vkpi_worker_runtime_identity.sql"
    assert rehearsal.MIGRATION_247 == "247_apify_jobs_active_idempotency.sql"
    assert rehearsal.MIGRATION_247_DOWN == "247_apify_jobs_active_idempotency_down.sql"
    for name in (
        rehearsal.MIGRATION_245,
        rehearsal.MIGRATION_246,
        rehearsal.MIGRATION_247,
        rehearsal.MIGRATION_247_DOWN,
    ):
        assert (ROOT / "migrations" / name).is_file()


def test_concurrent_insert_sql_matches_partial_unique_predicate():
    migration = (ROOT / "migrations" / rehearsal.MIGRATION_247).read_text(
        encoding="utf-8"
    )
    for clause in (
        "ON CONFLICT (idempotency_key)",
        "idempotency_key IS NOT NULL",
        "idempotency_key <> ''",
        "status IN ('queued', 'running')",
    ):
        assert clause in rehearsal.ACTIVE_CONFLICT_SQL
        if not clause.startswith("ON CONFLICT"):
            assert clause in migration


def test_runner_never_uses_ambient_database_url_and_always_drops_target():
    source = inspect.getsource(rehearsal)
    assert 'os.environ.get("DATABASE_URL")' not in source
    assert "DROP DATABASE IF EXISTS" in source
    assert "pg_terminate_backend" in source
    assert "refusing to connect to viltrox2" in source


def test_connection_failure_returns_redacted_failed_evidence(monkeypatch):
    def fail_connect(*_args, **_kwargs):
        raise RuntimeError("isolated socket unavailable")

    monkeypatch.setattr(rehearsal.psycopg, "connect", fail_connect)
    payload = rehearsal.run_rehearsal("dbname=postgres host=/private/tmp/unavailable")
    assert payload["status"] == "failed"
    assert payload["error_type"] == "RuntimeError"
    assert payload["error"] == "isolated socket unavailable"
    assert "completed_at" in payload
    assert "dbname=postgres" not in str(payload)


def test_245_247_down_contracts_are_narrow():
    down_245 = (
        ROOT / "migrations/245_vkpi_staff_organization_membership_backfill_down.sql"
    ).read_text(encoding="utf-8")
    down_247 = (
        ROOT / "migrations/247_apify_jobs_active_idempotency_down.sql"
    ).read_text(encoding="utf-8")
    assert "DELETE" not in down_245.upper()
    assert down_247.strip() == "DROP INDEX IF EXISTS uq_apify_jobs_active_idempotency;"
