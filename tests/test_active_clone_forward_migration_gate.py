from __future__ import annotations

import hashlib
import json
import os
import subprocess
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
    "307_users_token_version.sql",
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


def test_in_place_seal_validates_the_same_forward_migration_evidence() -> None:
    migrations = ",".join(FORWARD_MIGRATIONS)
    metadata = atomic_release_layout._database_release_metadata(
        strategy="in-place",
        source_database="",
        target_database="",
        env_fingerprint_before="",
        pending_migrations=migrations,
        compatibility_declaration=migrations,
    )

    assert metadata["database_strategy"] == "in-place"
    assert [
        row["name"] for row in metadata["forward_compatibility_evidence"]["migrations"]
    ] == list(FORWARD_MIGRATIONS)


def test_in_place_seal_rejects_missing_or_unreviewed_declaration() -> None:
    with pytest.raises(atomic_release_layout.LayoutError, match="exact forward-compatibility"):
        atomic_release_layout._database_release_metadata(
            strategy="in-place",
            source_database="",
            target_database="",
            env_fingerprint_before="",
            pending_migrations="307_users_token_version.sql",
            compatibility_declaration="",
        )
    with pytest.raises(atomic_release_layout.LayoutError, match="not reviewed by policy"):
        atomic_release_layout._database_release_metadata(
            strategy="in-place",
            source_database="",
            target_database="",
            env_fingerprint_before="",
            pending_migrations="308_vkpi_privacy_retention_columns.sql",
            compatibility_declaration="308_vkpi_privacy_retention_columns.sql",
        )


def test_train_fails_before_freeze_when_migration_policy_is_not_proven() -> None:
    source = (OPS / "train.sh").read_text(encoding="utf-8")
    preflight_at = source.index("\nmigration_preflight\n")
    freeze_at = source.index("candidate_manifest_matches_head")
    assert preflight_at < freeze_at
    preflight = source[source.index("migration_preflight()") : preflight_at]
    assert "声明必须与待应用迁移精确一致" in preflight
    assert "_forward_compatibility_evidence" in preflight
    assert "未经审阅或非前向兼容迁移" in preflight
    assert "完整版本集合与本地运行时清单不一致" in preflight
    assert "pending_runtime_migrations" in preflight
    assert "tr -d" not in preflight
    assert "&& . /opt/viltrox-2.0/.env" in preflight
    assert '[ -n "${DATABASE_URL:-}" ]' in preflight
    assert "-v ON_ERROR_STOP=1" in preflight


@pytest.mark.parametrize(
    ("ssh_body", "expected"),
    [
        ("#!/bin/sh\nexit 23\n", "无法连接线上或读取 schema_migrations"),
        ("#!/bin/sh\nexit 0\n", "未返回完整版本集合"),
    ],
)
def test_train_migration_preflight_fails_closed_when_remote_watermark_is_unknown(
    tmp_path: Path,
    ssh_body: str,
    expected: str,
) -> None:
    source = (OPS / "train.sh").read_text(encoding="utf-8")
    start = source.index("migration_preflight() {")
    end = source.index("\nmigration_preflight\n", start)
    function_source = source[start:end]

    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    ssh = bin_dir / "ssh"
    ssh.write_text(ssh_body, encoding="utf-8")
    ssh.chmod(0o755)
    harness = tmp_path / "preflight.sh"
    harness.write_text(
        "#!/usr/bin/env bash\n"
        "set -euo pipefail\n"
        "log() { printf '%s\\n' \"$*\"; }\n"
        "die() { printf 'FATAL: %s\\n' \"$*\" >&2; exit 1; }\n"
        f"ROOT={json.dumps(str(ROOT), ensure_ascii=False)}\n"
        f"PYTHON_BIN={json.dumps(str(ROOT / 'scripts' / 'ops' / 'safe_python.sh'), ensure_ascii=False)}\n"
        f"{function_source}\n"
        "migration_preflight\n",
        encoding="utf-8",
    )
    harness.chmod(0o755)
    env = os.environ.copy()
    env["PATH"] = f"{bin_dir}:/usr/bin:/bin"

    completed = subprocess.run(
        ["bash", str(harness)],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )

    assert completed.returncode != 0
    assert expected in completed.stderr


def test_train_migration_preflight_detects_a_hole_below_remote_max(tmp_path: Path) -> None:
    source = (OPS / "train.sh").read_text(encoding="utf-8")
    start = source.index("migration_preflight() {")
    end = source.index("\nmigration_preflight\n", start)
    function_source = source[start:end]
    manifest = atomic_release_shared.runtime_migration_manifest(ROOT / "migrations")
    missing = "309_vkpi_dsar_public_intake.sql"
    assert missing in manifest and manifest[-1] == "310_vkpi_kol_search_refresh_scheduler.sql"
    applied = ",".join(name for name in manifest if name != missing)

    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    ssh = bin_dir / "ssh"
    ssh.write_text(f"#!/bin/sh\nprintf '%s\\n' {json.dumps(applied)}\n", encoding="utf-8")
    ssh.chmod(0o755)
    harness = tmp_path / "hole-preflight.sh"
    harness.write_text(
        "#!/usr/bin/env bash\n"
        "set -euo pipefail\n"
        "log() { printf '%s\\n' \"$*\"; }\n"
        "die() { printf 'FATAL: %s\\n' \"$*\" >&2; exit 1; }\n"
        f"ROOT={json.dumps(str(ROOT), ensure_ascii=False)}\n"
        f"PYTHON_BIN={json.dumps(str(ROOT / 'scripts' / 'ops' / 'safe_python.sh'), ensure_ascii=False)}\n"
        "VKPI_FORWARD_COMPATIBLE_MIGRATIONS=\n"
        f"{function_source}\n"
        "migration_preflight\n",
        encoding="utf-8",
    )
    harness.chmod(0o755)
    env = os.environ.copy()
    env["PATH"] = f"{bin_dir}:/usr/bin:/bin"

    completed = subprocess.run(
        ["bash", str(harness)],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )

    assert completed.returncode != 0
    assert missing in completed.stdout
    assert "待应用迁移未声明" in completed.stderr


def test_pending_runtime_migrations_detects_holes_not_just_the_maximum() -> None:
    manifest = atomic_release_shared.runtime_migration_manifest(ROOT / "migrations")
    missing = "309_vkpi_dsar_public_intake.sql"
    applied = tuple(name for name in manifest if name != missing)

    assert max(applied) == "310_vkpi_kol_search_refresh_scheduler.sql"
    assert atomic_release_shared.pending_runtime_migrations(manifest, applied) == (missing,)


def test_pending_runtime_migrations_rejects_whitespace_polluted_version_keys() -> None:
    manifest = atomic_release_shared.runtime_migration_manifest(ROOT / "migrations")
    canonical = "309_vkpi_dsar_public_intake.sql"
    applied = tuple(
        f" {name}" if name == canonical else name
        for name in manifest
    )

    with pytest.raises(atomic_release_layout.LayoutError, match="empty or invalid"):
        atomic_release_shared.pending_runtime_migrations(manifest, applied)


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


@pytest.mark.parametrize("line_break", [b"\r", b"\r\n"])
def test_forward_policy_does_not_hide_destructive_sql_after_line_comment(
    tmp_path: Path,
    line_break: bytes,
) -> None:
    name = FORWARD_MIGRATIONS[2]
    source = (ROOT / "migrations" / name).read_bytes()
    (tmp_path / name).write_bytes(
        source + b"\n-- hidden" + line_break + b"DROP TABLE users;\n"
    )

    with pytest.raises(atomic_release_layout.LayoutError, match="non-additive SQL"):
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
    full_set_at = deploy.index("complete remote schema_migrations set", lineage_at)
    reconcile_at = deploy.index("pending_runtime_migrations", full_set_at)
    pending_at = deploy.index('if [ -n "${PENDING_MIGRATIONS}" ]', lineage_at)
    declaration_at = deploy.index(
        'FORWARD_COMPATIBILITY_DECLARATION}" != "${PENDING_MIGRATIONS}', pending_at
    )
    backup_at = deploy.index("SKIP_BACKUP=1 is forbidden when pending migrations exist", pending_at)
    reuse_at = deploy.index(
        'if [ "${STAGING_SOURCE_KIND}" = "prior-release-clone" ]', backup_at
    )
    assert lineage_at < full_set_at < reconcile_at < pending_at < declaration_at < backup_at < reuse_at
    reuse = deploy[reuse_at : deploy.index("\nfi", reuse_at)]
    assert 'DATABASE_RELEASE_STRATEGY="reuse-active-clone"' in reuse
    assert 'DATABASE_OWNER_RELEASE_ID="${PREDEPLOY_DATABASE_OWNER_RELEASE_ID}"' in reuse
    assert "pending != compatible" in deploy
    assert deploy.count("--require-migration-set-complete") == 1
    remote_set_reader = deploy[full_set_at - 1400 : reconcile_at]
    assert "tr -d" not in remote_set_reader
    assert "&& [ -n" in remote_set_reader
    assert "DATABASE_URL:-" in remote_set_reader
    assert "-v ON_ERROR_STOP=1" in remote_set_reader


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
