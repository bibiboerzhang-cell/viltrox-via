from __future__ import annotations

import hashlib
import json
import os
import stat
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Sequence

import pytest

from scripts.ops import restore_persistent_staging_db as restore


OWNER = "viltrox"
MIGRATION = "263_scheduler_fleet_recovery.sql"
RELEASE_ID = "20260715T230000Z-fe3871c438ff"


def _bundle(tmp_path: Path) -> tuple[Path, Path]:
    dump = tmp_path / "prod-db.dump"
    dump.write_bytes(b"offline custom archive fixture")
    dump.chmod(0o600)
    sidecar = tmp_path / "prod-db.dump.sha256"
    sidecar.write_text(f"{hashlib.sha256(dump.read_bytes()).hexdigest()}  {dump.name}\n")
    sidecar.chmod(0o600)
    return dump, sidecar


class FakeRunner:
    def __init__(self, *, fail_restore: bool = False) -> None:
        self.calls: list[tuple[Any, ...]] = []
        self.fail_restore = fail_restore

    def check_archive(self, dump: Path) -> None:
        self.calls.append(("list", dump))

    def restore(self, dump: Path, database: str, owner: str) -> None:
        self.calls.append(("restore", dump, database, owner))
        if self.fail_restore:
            raise restore.StagingRestoreError("pg_restore", "nonzero_exit")


class FakeDatabaseOps:
    def __init__(
        self,
        *,
        exists: bool = False,
        source_owner: str = OWNER,
        free_bytes: int = 4 * 1024**3,
        migration: str = MIGRATION,
    ) -> None:
        self.exists = exists
        self.source_owner = source_owner
        self.free_bytes = free_bytes
        self.migration = migration
        self.calls: list[str] = []

    def assert_local_admin(self) -> None:
        self.calls.append("admin")

    def source_state(self, source: str, expected_owner: str) -> dict[str, Any]:
        self.calls.append("source")
        return {
            "database": source,
            "owner": self.source_owner,
            "size_bytes": 1024**2,
            "free_bytes": self.free_bytes,
        }

    def database_exists(self, database: str) -> bool:
        self.calls.append("exists")
        return self.exists

    def create_database(self, database: str, owner: str) -> None:
        self.calls.append("create_template0")
        self.exists = True

    def inspect_database(
        self, database: str, expected_owner: str, anchors: Sequence[str]
    ) -> dict[str, Any]:
        self.calls.append("inspect")
        return {
            "transaction_mode": "read_only",
            "database_owner": expected_owner,
            "migration_max": self.migration,
            "table_counts": {
                table: (263 if table == "schema_migrations" else 0)
                for table in anchors
            },
            "table_owners": {table: expected_owner for table in anchors},
        }

    def drop_database_force(self, database: str) -> None:
        self.calls.append("force_drop")
        self.exists = False


def _run(
    tmp_path: Path,
    *,
    ops: FakeDatabaseOps | None = None,
    runner: FakeRunner | None = None,
) -> dict[str, Any]:
    dump, sidecar = _bundle(tmp_path)
    return restore.restore_persistent_staging(
        dump=dump,
        sidecar=sidecar,
        release_id=RELEASE_ID,
        source_database=restore.SOURCE_DATABASE,
        expected_owner=OWNER,
        expected_migration=MIGRATION,
        anchors=restore.DEFAULT_ANCHORS,
        receipt_path=tmp_path / "receipt.json",
        database_ops=ops or FakeDatabaseOps(),
        restore_runner=runner or FakeRunner(),
    )


def test_success_restores_persistent_release_database_and_private_receipt(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("PGPASSWORD", "must-not-appear")
    ops = FakeDatabaseOps()
    runner = FakeRunner()
    result = _run(tmp_path, ops=ops, runner=runner)
    target = restore.target_database_for_release(RELEASE_ID)
    artifact = tmp_path / "receipt.json"
    persisted = json.loads(artifact.read_text())
    assert target.startswith("viltrox2_test_release_")
    assert result["target_database"] == target == persisted["target_database"]
    assert result["persistent_database_retained"] is True and ops.exists is True
    assert persisted["operations"]["receipt_written"] is True
    assert persisted["operations"]["database_created_from_template0"] is True
    assert persisted["operations"]["pg_restore_single_transaction"] is True
    assert "must-not-appear" not in artifact.read_text()
    assert stat.S_IMODE(artifact.stat().st_mode) == 0o600
    assert runner.calls[0][0] == "list" and runner.calls[1][0] == "restore"


def test_restore_failure_force_drops_created_target(tmp_path: Path) -> None:
    ops = FakeDatabaseOps()
    with pytest.raises(restore.StagingRestoreError, match="pg_restore"):
        _run(tmp_path, ops=ops, runner=FakeRunner(fail_restore=True))
    assert ops.exists is False
    assert "force_drop" in ops.calls
    assert not (tmp_path / "receipt.json").exists()


def test_existing_target_is_refused_and_never_dropped(tmp_path: Path) -> None:
    ops = FakeDatabaseOps(exists=True)
    with pytest.raises(restore.StagingRestoreError, match="already_exists"):
        _run(tmp_path, ops=ops)
    assert ops.exists is True
    assert "create_template0" not in ops.calls
    assert "force_drop" not in ops.calls


@pytest.mark.parametrize(
    ("ops", "category"),
    [
        (FakeDatabaseOps(source_owner="wrong_owner"), "source_identity_mismatch"),
        (FakeDatabaseOps(free_bytes=1), "insufficient_disk_headroom"),
        (FakeDatabaseOps(migration="262_previous.sql"), "migration_max_mismatch"),
    ],
)
def test_source_owner_capacity_and_migration_fail_closed(
    tmp_path: Path, ops: FakeDatabaseOps, category: str
) -> None:
    with pytest.raises(restore.StagingRestoreError, match=category):
        _run(tmp_path, ops=ops)
    assert not (tmp_path / "receipt.json").exists()
    if "create_template0" in ops.calls:
        assert "force_drop" in ops.calls and ops.exists is False


def test_checksum_mismatch_stops_before_archive_or_database(tmp_path: Path) -> None:
    dump, sidecar = _bundle(tmp_path)
    sidecar.write_text(f"{'0' * 64}  {dump.name}\n")
    sidecar.chmod(0o600)
    ops = FakeDatabaseOps()
    runner = FakeRunner()
    with pytest.raises(restore.StagingRestoreError, match="sha256_mismatch"):
        restore.restore_persistent_staging(
            dump=dump,
            sidecar=sidecar,
            release_id=RELEASE_ID,
            source_database=restore.SOURCE_DATABASE,
            expected_owner=OWNER,
            expected_migration=MIGRATION,
            anchors=restore.DEFAULT_ANCHORS,
            receipt_path=tmp_path / "receipt.json",
            database_ops=ops,
            restore_runner=runner,
        )
    assert runner.calls == [] and ops.calls == []


def test_pg_restore_command_has_required_atomic_flags_and_clean_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[list[str], dict[str, str]]] = []
    monkeypatch.setattr(restore.shutil, "which", lambda name: "/usr/bin/pg_restore")

    def fake_run(argv: list[str], **kwargs: Any) -> SimpleNamespace:
        calls.append((argv, kwargs["env"]))
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(restore.subprocess, "run", fake_run)
    runner = restore.PgRestoreSubprocess(admin_user="postgres")
    target = restore.target_database_for_release(RELEASE_ID)
    runner.check_archive(Path("/private/pinned.dump"))
    runner.restore(Path("/private/pinned.dump"), target, OWNER)
    assert calls[0][0] == ["/usr/bin/pg_restore", "--list", "/private/pinned.dump"]
    restore_argv, clean_env = calls[1]
    for flag in (
        "--no-owner",
        "--no-acl",
        "--exit-on-error",
        "--single-transaction",
        f"--role={OWNER}",
        f"--dbname={target}",
    ):
        assert flag in restore_argv
    assert clean_env["PGUSER"] == "postgres"
    assert not any(key in clean_env for key in ("PGPASSWORD", "DATABASE_URL", "PGSERVICE"))
