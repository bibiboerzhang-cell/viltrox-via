from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
OPS = ROOT / "scripts" / "ops"
sys.path.insert(0, str(OPS))

import atomic_release_layout  # noqa: E402
import atomic_release_shared  # noqa: E402
import staging_db_clone  # noqa: E402


FORWARD_MIGRATIONS = (
    "305_vkpi_kol_pool_language_inferred.sql",
    "306_vkpi_product_persona_term_performance.sql",
)


def _database_url(database: str) -> str:
    return f"postgresql://app:secret@db.internal:5432/{database}?sslmode=require"


def _write_environment(root: Path, database: str) -> str:
    path = root / ".env"
    path.write_text(f"DATABASE_URL={_database_url(database)}\n", encoding="utf-8")
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _controller_capture(root: Path, release_id: str) -> Path:
    rollback = root / ".release-controller" / "rollbacks" / release_id
    rollback.mkdir(parents=True)
    for directory in (rollback.parent.parent, rollback.parent, rollback):
        directory.chmod(0o700)
    payload = (json.dumps({"schema": 3, "release_id": release_id}, sort_keys=True) + "\n").encode()
    (rollback / "metadata.json").write_bytes(payload)
    (rollback / "metadata.sha256").write_text(
        hashlib.sha256(payload).hexdigest() + "\n", encoding="ascii"
    )
    for path in (rollback / "metadata.json", rollback / "metadata.sha256"):
        path.chmod(0o600)
    return rollback


def _proven_active_clone(root: Path, *, owner: str = "database-owner") -> tuple[str, str]:
    releases = root / "releases"
    release = releases / owner
    release.mkdir(parents=True)
    database = staging_db_clone.clone_name_for_release(owner)
    migration = "295_vkpi_d2_daily_task_seeds.sql"
    (release / ".vkpi-release.json").write_text(
        json.dumps(
            {
                "release_id": owner,
                "database_strategy": "staging-clone",
                "source_database": staging_db_clone.SOURCE_DATABASE,
                "target_database": database,
                "pending_migrations": [migration],
                "forward_compatible_migrations": [],
            }
        ),
        encoding="utf-8",
    )
    current = root / "current"
    current.symlink_to(Path("releases") / owner, target_is_directory=True)
    fingerprint = _write_environment(root, database)
    _controller_capture(root, owner)
    receipt = {
        "root": root,
        "release_id": owner,
        "source_database": staging_db_clone.SOURCE_DATABASE,
        "target_database": database,
        "env_fingerprint_before": "0" * 64,
        "env_fingerprint_clone": fingerprint,
        "migration_version": migration,
    }
    staging_db_clone.write_release_receipt(**receipt, state="migrated-not-activated")
    staging_db_clone.write_release_receipt(**receipt, state="activated")
    return database, owner


def _activate_reuse_release(
    root: Path,
    *,
    database: str,
    owner: str,
    pending: list[str],
    compatible: list[str],
) -> Path:
    release_id = "forward-compatible-reuse"
    release = root / "releases" / release_id
    release.mkdir()
    manifest = release / ".vkpi-release.json"
    manifest.write_text(
        json.dumps(
            {
                "release_id": release_id,
                "database_strategy": "reuse-active-clone",
                "database_owner_release_id": owner,
                "source_database": None,
                "target_database": database,
                "pending_migrations": pending,
                "forward_compatible_migrations": compatible,
            }
        ),
        encoding="utf-8",
    )
    (root / "current").unlink()
    (root / "current").symlink_to(Path("releases") / release_id, target_is_directory=True)
    return manifest


def test_proven_active_clone_accepts_only_exact_forward_migration_manifest(
    tmp_path: Path,
) -> None:
    root = tmp_path / "app"
    database, owner = _proven_active_clone(root)
    pending = [
        "296_vkpi_budget_scope_registry.sql",
        "297_vkpi_marketing_advisor_budget.sql",
    ]
    manifest = _activate_reuse_release(
        root,
        database=database,
        owner=owner,
        pending=pending,
        compatible=list(pending),
    )

    proof = staging_db_clone.prove_active_source(root=root, expected_database=database)
    assert proof["source_kind"] == "prior-release-clone"
    assert proof["database_owner_release_id"] == owner
    assert proof["active_release_id"] == "forward-compatible-reuse"

    payload = json.loads(manifest.read_text(encoding="utf-8"))
    payload["forward_compatible_migrations"] = pending[:1]
    manifest.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(staging_db_clone.CloneError, match="exact declaration"):
        staging_db_clone.prove_active_source(root=root, expected_database=database)


@pytest.mark.parametrize(
    ("pending", "declaration"),
    [
        ("296.sql", ""),
        ("", "296.sql"),
        ("296.sql,297.sql", "296.sql"),
    ],
)
def test_clone_reuse_seal_rejects_missing_or_mismatched_declaration(
    pending: str,
    declaration: str,
) -> None:
    database = staging_db_clone.clone_name_for_release("database-owner")
    with pytest.raises(atomic_release_layout.LayoutError, match="exact forward-compatibility"):
        atomic_release_layout._database_release_metadata(
            strategy="reuse-active-clone",
            source_database="",
            target_database=database,
            env_fingerprint_before="a" * 64,
            pending_migrations=pending,
            compatibility_declaration=declaration,
            database_owner_release_id="database-owner",
        )


def test_clone_reuse_seal_accepts_exact_declaration_and_preserves_owner() -> None:
    database = staging_db_clone.clone_name_for_release("database-owner")
    migrations = ",".join(FORWARD_MIGRATIONS)
    metadata = atomic_release_layout._database_release_metadata(
        strategy="reuse-active-clone",
        source_database="",
        target_database=database,
        env_fingerprint_before="a" * 64,
        pending_migrations=migrations,
        compatibility_declaration=migrations,
        database_owner_release_id="database-owner",
    )
    evidence = metadata.pop("forward_compatibility_evidence")
    assert metadata == {
        "database_strategy": "reuse-active-clone",
        "source_database": None,
        "target_database": database,
        "env_fingerprint_before": "a" * 64,
        "database_owner_release_id": "database-owner",
    }
    assert evidence["policy_id"] == "vkpi-additive-nullable-defaultless-v1"
    assert evidence["guarantees"] == [
        "additive_columns_only",
        "nullable_columns",
        "defaultless_columns",
        "no_row_writes",
    ]
    assert [row["name"] for row in evidence["migrations"]] == list(
        FORWARD_MIGRATIONS
    )
    assert all(len(row["sha256"]) == 64 for row in evidence["migrations"])


@pytest.mark.parametrize(
    ("old", "new"),
    [
        ("JSONB NULL", "JSONB NOT NULL"),
        ("JSONB NULL", "JSONB NULL DEFAULT '{}'::jsonb"),
        (
            "COMMENT ON COLUMN vkpi_product_persona.term_performance_json",
            "UPDATE vkpi_product_persona SET term_performance_json='{}'::jsonb;\n"
            "COMMENT ON COLUMN vkpi_product_persona.term_performance_json",
        ),
    ],
)
def test_forward_policy_rejects_non_nullable_defaulted_or_row_writing_drift(
    tmp_path: Path,
    old: str,
    new: str,
) -> None:
    name = FORWARD_MIGRATIONS[1]
    source = (ROOT / "migrations" / name).read_text(encoding="utf-8")
    assert old in source
    (tmp_path / name).write_text(source.replace(old, new, 1), encoding="utf-8")

    with pytest.raises(atomic_release_layout.LayoutError, match="forward-compatible"):
        atomic_release_shared._forward_compatibility_evidence(
            name,
            migrations_dir=tmp_path,
        )


def test_forward_policy_fails_closed_for_unreviewed_migration() -> None:
    with pytest.raises(atomic_release_layout.LayoutError, match="not reviewed by policy"):
        atomic_release_shared._forward_compatibility_evidence("307_unreviewed.sql")


def test_deploy_exception_is_bounded_by_lineage_declaration_and_backup() -> None:
    deploy = (OPS / "deploy_local_to_cloud.sh").read_text(encoding="utf-8")
    assert "Refusing VKPI_STAGING_DB_CLONE=1 before remote mutation" in deploy
    assert "Refusing to mutate an active release clone in place" not in deploy

    lineage_at = deploy.index('source_kind = "prior-release-clone"')
    pending_at = deploy.index('if [ -n "${PENDING_MIGRATIONS}" ]', lineage_at)
    declaration_at = deploy.index(
        'FORWARD_COMPATIBILITY_DECLARATION}" != "${PENDING_MIGRATIONS}', pending_at
    )
    backup_at = deploy.index("SKIP_BACKUP=1 is forbidden when pending migrations exist", pending_at)
    reuse_at = deploy.index(
        'if [ "${STAGING_SOURCE_KIND}" = "prior-release-clone" ]', backup_at
    )
    assert lineage_at < pending_at < declaration_at < backup_at < reuse_at
    reuse = deploy[reuse_at : deploy.index("\nfi", reuse_at)]
    assert 'DATABASE_RELEASE_STRATEGY="reuse-active-clone"' in reuse
    assert 'DATABASE_OWNER_RELEASE_ID="${PREDEPLOY_DATABASE_OWNER_RELEASE_ID}"' in reuse
    assert "pending != compatible" in deploy


def test_prior_clone_migration_backup_is_release_bound_and_reverified() -> None:
    deploy = (OPS / "deploy_local_to_cloud.sh").read_text(encoding="utf-8")
    boundary_at = deploy.index(
        'if ! PRIOR_CLONE_BOUNDARY_BEFORE="$(read_prior_clone_backup_boundary)"'
    )
    backup_at = deploy.index(
        'PRIOR_CLONE_BACKUP_STAMP="${RELEASE_ID}-pre-migration"', boundary_at
    )
    receipt_at = deploy.index(
        '"schema_version": "vkpi-release-migration-backup/v1"', backup_at
    )
    migration_at = deploy.index("run-migrations-only", receipt_at)
    backup = deploy[boundary_at:migration_at]
    assert backup.count("read_prior_clone_backup_boundary") == 2
    for required in (
        'STAMP="${PRIOR_CLONE_BACKUP_STAMP}"',
        'LOCAL_DIR="${PRIOR_CLONE_BACKUP_DIR}"',
        'pg_restore --list "${PRIOR_CLONE_BACKUP_DIR}/prod-db.dump"',
        'sidecar = directory / "prod-db.dump.sha256"',
        'runtime_state = directory / "runtime-state.txt"',
        '"release_id": release_id',
        '"active_release_id": active_release_id',
        '"predeploy_git_sha": predeploy_sha',
        '"predeploy_migration": predeploy_migration',
        '"database_owner_release_id": database_owner_release_id',
        '"predeploy_env_sha256": predeploy_env_sha256',
        '"active_manifest_sha256": active_manifest_sha256',
        '"pending_migrations": pending',
        '"forward_compatible_migrations": pending',
        '"db_sha256": parts[0]',
        '"runtime_state_sha256": hashlib.sha256(runtime_state.read_bytes()).hexdigest()',
        'os.O_WRONLY | os.O_CREAT | os.O_EXCL',
        "os.fsync(handle.fileno())",
        "os.fsync(parent)",
        'PRIOR_CLONE_ENV_SHA_BEFORE}" != "${PREDEPLOY_ENV_SHA256}',
        'PRIOR_CLONE_ENV_SHA_AFTER}" != "${PREDEPLOY_ENV_SHA256}',
        'PRIOR_CLONE_MANIFEST_SHA_AFTER}" != "${PRIOR_CLONE_ACTIVE_MANIFEST_SHA256}',
    ):
        assert required in backup

    boundary_reader = deploy[deploy.index("read_prior_clone_backup_boundary()") : backup_at]
    assert "sudo -n env -i" in boundary_reader
    assert "/usr/bin/python3 -B -" in boundary_reader
    assert "prior_clone_backup_boundary.py" in boundary_reader
