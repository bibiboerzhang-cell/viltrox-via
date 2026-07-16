from __future__ import annotations

import hashlib
import json
import os
import pwd
import subprocess
import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
OPS = ROOT / "scripts" / "ops"
sys.path.insert(0, str(OPS))

import atomic_release_layout  # noqa: E402
import staging_db_clone  # noqa: E402


SECRET = "never-print-this-password"


def _url(database: str) -> str:
    return (
        f"postgresql://app:{SECRET}@db.internal:5432/{database}"
        "?sslmode=require&application_name=vkpi"
    )


def _write_env(path: Path, database: str, *, app_sha: str = "old") -> None:
    path.write_text(
        f"DATABASE_URL='{_url(database)}'\nAPP_GIT_SHA={app_sha}\nSECRET={SECRET}\n",
        encoding="utf-8",
    )


def _activated_release(
    root: Path,
    *,
    release_id: str,
    source_database: str,
) -> tuple[str, Path]:
    target_database = staging_db_clone.clone_name_for_release(release_id)
    release = root / "releases" / release_id
    release.mkdir(parents=True)
    (release / ".vkpi-release.json").write_text(
        json.dumps(
            {
                "schema": 1,
                "release_id": release_id,
                "database_strategy": "staging-clone",
                "source_database": source_database,
                "target_database": target_database,
                "pending_migrations": ["252_vkpi_advisor_turn_claims.sql"],
                "forward_compatible_migrations": [],
            }
        )
        + "\n",
        encoding="utf-8",
    )
    current = root / "current"
    if current.exists() or current.is_symlink():
        current.unlink()
    current.symlink_to(Path("releases") / release_id, target_is_directory=True)
    _write_env(root / ".env", target_database, app_sha=release_id)
    fingerprint = hashlib.sha256((root / ".env").read_bytes()).hexdigest()
    receipt = (
        root
        / "runtime"
        / "ops"
        / "deploy-rollbacks"
        / release_id
        / "database-clone.json"
    )
    receipt.parent.mkdir(parents=True)
    receipt.write_text(
        json.dumps(
            {
                "schema": 1,
                "release_id": release_id,
                "database_strategy": "staging-clone",
                "source_database": source_database,
                "target_database": target_database,
                "env_fingerprint_before": "0" * 64,
                "env_fingerprint_clone": fingerprint,
                "migration_version": "252_vkpi_advisor_turn_claims.sql",
                "state": "activated",
                "rollback_env_fingerprint": None,
                "secrets_included": False,
            }
        )
        + "\n",
        encoding="utf-8",
    )
    return target_database, release


def _reuse_release(
    root: Path,
    *,
    release_id: str,
    database_owner_release_id: str,
    target_database: str,
) -> Path:
    release = root / "releases" / release_id
    release.mkdir(parents=True)
    (release / ".vkpi-release.json").write_text(
        json.dumps(
            {
                "schema": 1,
                "release_id": release_id,
                "database_strategy": "reuse-active-clone",
                "database_owner_release_id": database_owner_release_id,
                "source_database": None,
                "target_database": target_database,
                "pending_migrations": [],
                "forward_compatible_migrations": [],
            }
        )
        + "\n",
        encoding="utf-8",
    )
    current = root / "current"
    if current.exists() or current.is_symlink():
        current.unlink()
    current.symlink_to(Path("releases") / release_id, target_is_directory=True)
    _write_env(root / ".env", target_database, app_sha=release_id)
    return release


def test_clone_name_is_deterministic_release_bound_and_identifier_safe() -> None:
    first = staging_db_clone.clone_name_for_release("20260714T220000Z-a1b2c3d4")
    second = staging_db_clone.clone_name_for_release("20260714T220100Z-a1b2c3d4")

    assert first != second
    assert first.startswith(staging_db_clone.CLONE_PREFIX)
    assert len(first) <= 63
    assert staging_db_clone.DATABASE_IDENTIFIER_RE.fullmatch(first)
    with pytest.raises(staging_db_clone.CloneError):
        staging_db_clone.clone_name_for_release("../escape")


def test_database_url_switch_changes_only_path_and_never_returns_secret() -> None:
    source = staging_db_clone.clone_name_for_release("release-one")
    target = staging_db_clone.clone_name_for_release("release-two")
    original = _url(source)

    changed = staging_db_clone.replace_database_name(
        original,
        expected=source,
        target=target,
    )

    assert staging_db_clone.database_name_from_url(changed) == target
    assert changed.replace(f"/{target}", f"/{source}") == original
    with pytest.raises(staging_db_clone.CloneError):
        staging_db_clone.replace_database_name(
            original,
            expected=staging_db_clone.SOURCE_DATABASE,
            target=target,
        )


def test_switch_env_is_atomic_preserves_non_database_values_and_cli_redacts_secret(
    tmp_path: Path,
) -> None:
    env_path = tmp_path / ".env"
    _write_env(env_path, staging_db_clone.SOURCE_DATABASE)
    before_mode = env_path.stat().st_mode & 0o777
    target = staging_db_clone.clone_name_for_release("release-one")

    result = subprocess.run(
        [
            sys.executable,
            str(OPS / "staging_db_clone.py"),
            "switch-env",
            "--env-file",
            str(env_path),
            "--expected-source-db",
            staging_db_clone.SOURCE_DATABASE,
            "--target-db",
            target,
        ],
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert SECRET not in result.stdout
    assert SECRET not in result.stderr
    assert staging_db_clone.read_database_identity(env_path) == target
    assert f"SECRET={SECRET}" in env_path.read_text(encoding="utf-8")
    assert env_path.stat().st_mode & 0o777 == before_mode
    payload = json.loads(result.stdout)
    assert payload["database_name"] == target
    assert payload["env_sha256"] == hashlib.sha256(env_path.read_bytes()).hexdigest()
    assert not list(tmp_path.glob(".env.db-switch-*"))


def test_disk_gate_requires_source_size_plus_one_gibibyte() -> None:
    source_size = 7 * 1024**3
    required = source_size + 1024**3

    staging_db_clone.assert_disk_headroom(
        source_size_bytes=source_size,
        free_bytes=required,
    )
    with pytest.raises(staging_db_clone.CloneError, match="insufficient"):
        staging_db_clone.assert_disk_headroom(
            source_size_bytes=source_size,
            free_bytes=required - 1,
        )


def test_clone_mode_rejects_pool_url_that_could_bypass_database_switch(
    tmp_path: Path,
) -> None:
    env_path = tmp_path / ".env"
    _write_env(env_path, staging_db_clone.SOURCE_DATABASE)
    with env_path.open("a", encoding="utf-8") as handle:
        handle.write(f"DATABASE_POOL_URL={_url(staging_db_clone.SOURCE_DATABASE)}\n")

    with pytest.raises(staging_db_clone.CloneError, match="DATABASE_POOL_URL"):
        staging_db_clone.env_state(env_path)


def test_second_release_uses_proven_active_clone_and_never_legacy_base(
    tmp_path: Path,
) -> None:
    root = tmp_path / "app"
    (root / "releases").mkdir(parents=True)
    _write_env(root / ".env", staging_db_clone.SOURCE_DATABASE)

    first_database, _first_release = _activated_release(
        root,
        release_id="release-one",
        source_database=staging_db_clone.SOURCE_DATABASE,
    )
    first_proof = staging_db_clone.prove_active_source(
        root=root,
        expected_database=first_database,
    )
    assert first_proof["source_kind"] == "prior-release-clone"
    assert first_proof["source_release_id"] == "release-one"

    second_database = staging_db_clone.clone_name_for_release("release-two")
    assert staging_db_clone.validate_source_database(first_proof["database_name"]) == first_database
    assert second_database != first_database
    assert first_proof["database_name"] != staging_db_clone.SOURCE_DATABASE
    switched = staging_db_clone.switch_environment_database(
        root / ".env",
        expected_source=first_proof["database_name"],
        target=second_database,
    )
    assert switched["database_name"] == second_database
    assert staging_db_clone.read_database_identity(root / ".env") != staging_db_clone.SOURCE_DATABASE

    with pytest.raises(staging_db_clone.CloneError, match="identity mismatch"):
        staging_db_clone.prove_active_source(
            root=root,
            expected_database=staging_db_clone.SOURCE_DATABASE,
        )


def test_clone_a_app_only_b_migration_clone_c_preserves_database_lineage(
    tmp_path: Path,
) -> None:
    root = tmp_path / "app"
    (root / "releases").mkdir(parents=True)
    _write_env(root / ".env", staging_db_clone.SOURCE_DATABASE)

    database_a, _release_a = _activated_release(
        root,
        release_id="release-a",
        source_database=staging_db_clone.SOURCE_DATABASE,
    )
    release_b = _reuse_release(
        root,
        release_id="release-b",
        database_owner_release_id="release-a",
        target_database=database_a,
    )
    proof_b = staging_db_clone.prove_active_source(
        root=root,
        expected_database=database_a,
    )

    assert proof_b["database_name"] == database_a
    assert proof_b["database_owner_release_id"] == "release-a"
    assert proof_b["source_release_id"] == "release-a"
    assert proof_b["active_release_id"] == "release-b"
    assert not (
        root
        / "runtime/ops/deploy-rollbacks/release-b/database-clone.json"
    ).exists()
    manifest_b = json.loads(
        (release_b / ".vkpi-release.json").read_text(encoding="utf-8")
    )
    assert manifest_b["source_database"] is None
    assert manifest_b["target_database"] == database_a

    reuse_metadata = atomic_release_layout._database_release_metadata(
        strategy="reuse-active-clone",
        source_database="",
        target_database=database_a,
        env_fingerprint_before=proof_b["env_sha256"],
        pending_migrations="",
        compatibility_declaration="",
        database_owner_release_id="release-a",
    )
    assert reuse_metadata["database_owner_release_id"] == "release-a"

    database_c = staging_db_clone.clone_name_for_release("release-c")
    clone_c_metadata = atomic_release_layout._database_release_metadata(
        strategy="staging-clone",
        source_database=proof_b["database_name"],
        target_database=database_c,
        env_fingerprint_before=proof_b["env_sha256"],
        pending_migrations="253_next.sql",
        compatibility_declaration="",
    )
    assert clone_c_metadata["source_database"] == database_a
    assert clone_c_metadata["target_database"] == database_c
    assert database_a != staging_db_clone.SOURCE_DATABASE


def test_active_clone_cannot_silently_fall_back_to_legacy_database(tmp_path: Path) -> None:
    root = tmp_path / "app"
    (root / "releases").mkdir(parents=True)
    _active_database, _release = _activated_release(
        root,
        release_id="release-one",
        source_database=staging_db_clone.SOURCE_DATABASE,
    )
    _write_env(root / ".env", staging_db_clone.SOURCE_DATABASE)

    with pytest.raises(staging_db_clone.CloneError, match="refusing to fall back"):
        staging_db_clone.prove_active_source(
            root=root,
            expected_database=staging_db_clone.SOURCE_DATABASE,
        )


def test_active_clone_requires_matching_activated_receipt(tmp_path: Path) -> None:
    root = tmp_path / "app"
    (root / "releases").mkdir(parents=True)
    database, _release = _activated_release(
        root,
        release_id="release-one",
        source_database=staging_db_clone.SOURCE_DATABASE,
    )
    receipt_path = (
        root
        / "runtime"
        / "ops"
        / "deploy-rollbacks"
        / "release-one"
        / "database-clone.json"
    )
    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    receipt["state"] = "rollback-restored"
    receipt_path.write_text(json.dumps(receipt), encoding="utf-8")

    with pytest.raises(staging_db_clone.CloneError, match="receipt"):
        staging_db_clone.prove_active_source(root=root, expected_database=database)


def test_clone_receipt_state_machine_cannot_reactivate_a_rolled_back_release(
    tmp_path: Path,
) -> None:
    root = tmp_path / "app"
    release_id = "release-receipt"
    target = staging_db_clone.clone_name_for_release(release_id)
    (root / "runtime/ops/deploy-rollbacks" / release_id).mkdir(parents=True)
    kwargs = {
        "root": root,
        "release_id": release_id,
        "source_database": staging_db_clone.SOURCE_DATABASE,
        "target_database": target,
        "env_fingerprint_before": "a" * 64,
        "env_fingerprint_clone": "b" * 64,
        "migration_version": "252_vkpi_advisor_turn_claims.sql",
    }

    staging_db_clone.write_release_receipt(
        **kwargs,
        state="migrated-not-activated",
    )
    staging_db_clone.write_release_receipt(**kwargs, state="activated")
    with pytest.raises(staging_db_clone.CloneError, match="state transition"):
        staging_db_clone.write_release_receipt(
            **kwargs,
            state="migrated-not-activated",
        )
    staging_db_clone.write_release_receipt(
        **kwargs,
        state="rollback-restored",
        rollback_env_fingerprint="a" * 64,
    )
    with pytest.raises(staging_db_clone.CloneError, match="state transition"):
        staging_db_clone.write_release_receipt(**kwargs, state="activated")


def test_atomic_clone_metadata_rejects_fake_forward_compatibility() -> None:
    source = staging_db_clone.clone_name_for_release("release-one")
    target = staging_db_clone.clone_name_for_release("release-two")
    metadata = atomic_release_layout._database_release_metadata(
        strategy="staging-clone",
        source_database=source,
        target_database=target,
        env_fingerprint_before="a" * 64,
        pending_migrations="240.sql,241.sql,252.sql",
        compatibility_declaration="",
    )

    assert metadata == {
        "database_strategy": "staging-clone",
        "source_database": source,
        "target_database": target,
        "env_fingerprint_before": "a" * 64,
        "database_owner_release_id": None,
    }
    with pytest.raises(atomic_release_layout.LayoutError, match="must not claim"):
        atomic_release_layout._database_release_metadata(
            strategy="staging-clone",
            source_database=source,
            target_database=target,
            env_fingerprint_before="a" * 64,
            pending_migrations="240.sql,241.sql,252.sql",
            compatibility_declaration="240.sql,241.sql,252.sql",
        )


def test_deploy_clone_path_is_tightly_scoped_ordered_and_rollback_bound() -> None:
    deploy = (OPS / "deploy_local_to_cloud.sh").read_text(encoding="utf-8")

    for required in (
        'STAGING_DB_CLONE_MODE="${VKPI_STAGING_DB_CLONE:-0}"',
        'VILTROXTEST_RELEASE_SCOPE=1',
        'parsed.hostname == "viltroxtest.com"',
        'DATABASE_RELEASE_STRATEGY="reuse-active-clone"',
        "--database-owner-release-id '${DATABASE_OWNER_RELEASE_ID}'",
        'STAGING_REDIS_WORKER_SERVICE="vkpi-redis-worker.service"',
        "--optional-unit-name '${STAGING_REDIS_WORKER_SERVICE}'",
        "--optional-unit-state '${STAGING_REDIS_WORKER_SERVICE}=${STAGING_REDIS_WORKER_CAPTURED_STATE}'",
        "inspect-unit-state --unit-dir /etc/systemd/system",
        "scripts/verify_redis_worker_health.py",
        "source_size_bytes\"]) + 1024**3",
        "run-migrations-only",
        "write-receipt",
        "prove-active-source",
        "rollback environment fingerprint or database identity mismatch",
    ):
        assert required in deploy
    assert "redis.service" not in deploy

    prepare_at = deploy.index("atomic_release_layout.py' prepare")
    unit_state_at = deploy.index("STAGING_REDIS_WORKER_UNIT_STATE=", prepare_at)
    stop_at = deploy.index("quiesce_remote_release_consumers\n", prepare_at)
    create_at = deploy.index("staging_db_clone.py' create", stop_at)
    switch_at = deploy.index("staging_db_clone.py' switch-env", create_at)
    migrate_at = deploy.index("run-migrations-only", switch_at)
    verify_at = deploy.index("verify-migration", migrate_at)
    activate_at = deploy.index("atomic_release_layout.py' activate", verify_at)
    install_at = deploy.index(
        "scripts/ops/systemd/${STAGING_REDIS_WORKER_SERVICE}' '/etc/systemd/system/${STAGING_REDIS_WORKER_SERVICE}'",
        activate_at,
    )
    redis_start_at = deploy.index(
        "sudo systemctl enable --now '${STAGING_REDIS_WORKER_SERVICE}'", install_at
    )
    web_start_at = deploy.index("sudo systemctl restart '${SERVICE_NAME}'", redis_start_at)
    assert prepare_at < unit_state_at < stop_at < create_at < switch_at < migrate_at < verify_at
    assert verify_at < activate_at < install_at < redis_start_at < web_start_at

    rollback = deploy.split("attempt_automatic_rollback()", 1)[1].split(
        "cleanup_post_deploy_evidence()", 1
    )[0]
    stop_all_at = rollback.index("sudo systemctl stop '${SERVICE_NAME}'")
    disable_at = rollback.index("sudo systemctl disable --now", stop_all_at)
    restore_at = rollback.index("atomic_release_layout.py' restore")
    identity_at = rollback.index("staging_db_clone.py' assert-env", restore_at)
    drop_at = rollback.index("staging_db_clone.py' drop", identity_at)
    rollback_start_at = rollback.index("restored_redis_unit_state=", drop_at)
    assert stop_all_at < disable_at < restore_at < identity_at < drop_at < rollback_start_at
    assert "STAGING_DB_CLONE_ACTIVATED" in rollback
    assert "rollback did not restore the exact Redis worker unit state" in rollback


def test_migrations_only_runner_contract_is_single_process_and_provider_free() -> None:
    helper = (OPS / "staging_db_clone.py").read_text(encoding="utf-8")
    deploy = (OPS / "deploy_local_to_cloud.sh").read_text(encoding="utf-8")

    assert '"APP_ROLE": "migration-runner"' in helper
    assert '"VKPI_DB_STARTUP_MODE": "migrations-only"' in helper
    assert '"ENABLE_SCHEDULER": "0"' in helper
    assert '"ENABLE_BROWSER": "0"' in helper
    assert '"VKPI_SKIP_DOTENV": "1"' in helper
    assert "asyncio.run(init_db_runtime())" in helper
    assert "gunicorn" not in helper
    assert 'CREATE DATABASE {} WITH TEMPLATE {} OWNER {}' in helper
    assert "sql.Identifier(source)" in helper
    assert "create --source-db '${STAGING_SOURCE_DATABASE}'" in deploy
    migration_command = deploy.split("run-migrations-only", 1)[0].rsplit("ssh", 1)[1]
    assert "env -i" in migration_command
    assert "APIFY_TOKEN" not in migration_command
    assert "GEMINI_API_KEY" not in migration_command
    assert "OPENAI_API_KEY" not in migration_command


def test_migrations_only_runner_sets_one_shot_role_and_writes_nothing_to_release(
    tmp_path: Path,
) -> None:
    target = staging_db_clone.clone_name_for_release("readonly-migration-runner")
    release = tmp_path / "release"
    connection_module = release / "backend" / "app" / "db" / "connection.py"
    connection_module.parent.mkdir(parents=True)
    (release / "backend" / "app" / "__init__.py").write_text("", encoding="utf-8")
    (release / "backend" / "app" / "db" / "__init__.py").write_text("", encoding="utf-8")
    (release / "migrations").mkdir()
    capture = tmp_path / "runner-capture.json"
    connection_module.write_text(
        """
import json
import os
from pathlib import Path

async def init_db_runtime():
    Path(os.environ["CAPTURE_PATH"]).write_text(json.dumps({
        "app_role": os.environ.get("APP_ROLE"),
        "startup_mode": os.environ.get("VKPI_DB_STARTUP_MODE"),
        "orchestrator": os.environ.get("ENABLE_LOCAL_ORCHESTRATOR"),
        "browser": os.environ.get("ENABLE_BROWSER"),
        "scheduler": os.environ.get("ENABLE_SCHEDULER"),
        "cleanup": os.environ.get("ENABLE_UPLOAD_CLEANUP"),
    }), encoding="utf-8")

def close_db_runtime_sync():
    return None
""",
        encoding="utf-8",
    )
    env_path = tmp_path / ".env"
    env_path.write_text(
        "\n".join(
            [
                f"DATABASE_URL={_url(target)}",
                f"CAPTURE_PATH={capture}",
                "ENABLE_LOCAL_ORCHESTRATOR=1",
                "ENABLE_BROWSER=1",
                "ENABLE_SCHEDULER=1",
                "ENABLE_UPLOAD_CLEANUP=1",
                "GEMINI_API_KEY=must-not-be-used",
                "OPENAI_API_KEY=must-not-be-used",
                "APIFY_TOKEN=must-not-be-used",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    app_user = pwd.getpwuid(os.geteuid()).pw_name
    for path in sorted(release.rglob("*"), reverse=True):
        path.chmod(0o555 if path.is_dir() else 0o444)
    release.chmod(0o555)
    try:
        result = subprocess.run(
            [
                sys.executable,
                str(OPS / "staging_db_clone.py"),
                "run-migrations-only",
                "--env-file",
                str(env_path),
                "--release-path",
                str(release),
                "--expected-db",
                target,
                "--app-user",
                app_user,
            ],
            cwd=tmp_path,
            env={
                "HOME": os.environ.get("HOME", "/tmp"),
                "PATH": os.environ.get("PATH", ""),
                "PYTHONDONTWRITEBYTECODE": "1",
            },
            capture_output=True,
            text=True,
            check=False,
        )
    finally:
        release.chmod(0o755)
        for path in release.rglob("*"):
            path.chmod(0o755 if path.is_dir() else 0o644)

    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == {
        "database_name": target,
        "startup_mode": "migrations-only",
    }
    assert json.loads(capture.read_text(encoding="utf-8")) == {
        "app_role": "migration-runner",
        "startup_mode": "migrations-only",
        "orchestrator": "0",
        "browser": "0",
        "scheduler": "0",
        "cleanup": "0",
    }
    assert not (release / "uploads").exists()
    assert not (release / "frames").exists()
    assert not (release / "creator_profiles").exists()
