from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import stat
import sys

import pytest


ROOT = Path(__file__).resolve().parents[1]
OPS = ROOT / "scripts" / "ops"
sys.path.insert(0, str(OPS))

import verify_legacy_bootstrap_anchor as anchor  # noqa: E402


SERVER_SHA = "a" * 40
CLIENT_SHA = "b" * 40
OLD_MIGRATION = "233_legacy.sql"
TARGET_MIGRATION = "264_target.sql"
PENDING = "234_first.sql,264_target.sql"
RELEASE_ID = "20260716T010000Z-candidate"
GIT_SHA = "c" * 40


def _write_json(path: Path, payload: object, mode: int = 0o600) -> Path:
    path.write_text(json.dumps(payload, sort_keys=True), encoding="utf-8")
    path.chmod(mode)
    return path


def _units() -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    for index, name in enumerate(anchor.CORE_UNITS):
        rows.append(
            {
                "name": name,
                "observable": True,
                "load_state": "loaded",
                "active_state": "active",
                "unit_file_state": "enabled",
                "fragment_path": f"/etc/systemd/system/{name.split('@', 1)[0]}@.service"
                if "@" in name
                else f"/etc/systemd/system/{name}",
                "fragment_sha256": f"{index + 1:064x}",
                "fragment_readable": True,
                "user": "viltrox" if index == 0 else "root",
                "group": "viltrox" if index == 0 else "root",
                "working_directory": "/opt/viltrox-2.0",
                "app_role": None,
                "environment_mode": None,
                "claim_lane": "interactive" if index == 1 else ("all" if index else None),
                "heartbeat_name": None if index == 0 else f"legacy-worker-{index}",
            }
        )
    return rows


def _fixture_payloads() -> tuple[dict[str, object], dict[str, object], dict[str, object]]:
    preflight: dict[str, object] = {
        "schema_version": 1,
        "report_type": anchor.PREFLIGHT_REPORT_TYPE,
        "mode": "remote_read_only_preflight",
        "decision": "no-go",
        "secret_free": True,
        "target": {"ssh_target": "viltrox", "root": "/opt/viltrox-2.0"},
        "candidate": {"expected_migration": TARGET_MIGRATION},
        "blocking_check_ids": list(anchor.ALLOWED_BLOCKERS),
        "checks": [
            {"id": check_id, "blocking": True, "pass": False}
            for check_id in anchor.ALLOWED_BLOCKERS
        ],
        "observed": {
            "release_layout": {
                "state": "legacy_flat",
                "current": {"kind": "absent"},
                "previous": {"kind": "absent"},
                "root_build_git_sha": CLIENT_SHA,
            },
            "environment": {
                "owner": "viltrox",
                "group": "viltrox",
                "mode": "0600",
            },
            "systemd_units": _units(),
            "database": {
                "database_name": "viltrox2_test",
                "migration_max": OLD_MIGRATION,
            },
            "redis": {
                "reachable": True,
                "aof_enabled": True,
                "rdb_last_bgsave_status": "ok",
                "aof_last_write_status": "ok",
                "error_code": None,
            },
            "health": {
                "server_git_sha": SERVER_SHA,
                "client_git_sha": CLIENT_SHA,
                "db_migration_max": OLD_MIGRATION,
            },
            "backup": {
                "latest_name": "20260716T001100Z-basic-precloud",
                "checksum_verified": True,
                "catalog_verified": True,
                "encrypted_environment_snapshot_present": True,
                "off_host_receipt_present": True,
            },
        },
    }
    health = {
        "status": "ok",
        "trust": {
            "server_git_sha": SERVER_SHA,
            "client_git_sha": CLIENT_SHA,
            "db_migration_max": OLD_MIGRATION,
        },
    }
    anchor_payload = {
        "schema_version": anchor.ANCHOR_SCHEMA,
        "root": "/opt/viltrox-2.0",
        "root_build_git_sha": CLIENT_SHA,
        "environment": {
            "sha256": "d" * 64,
            "uid": 1001,
            "gid": 1001,
            "mode": "0600",
            "database_name": "viltrox2_test",
        },
        "current": {"kind": "absent"},
        "previous": {"kind": "absent"},
        "success_marker": {"kind": "absent"},
        "recovery": {
            "backup_stamp": "20260716T001100Z-basic-precloud",
            "dump_sha256": "e" * 64,
            "dump_size_bytes": 10,
            "environment_cipher_sha256": "f" * 64,
            "environment_cipher_size_bytes": 10,
            "offhost_receipt_sha256": "1" * 64,
            "offhost_receipt_size_bytes": 10,
        },
        "secret_free": True,
    }
    return preflight, health, anchor_payload


def _common_args(tmp_path: Path) -> tuple[list[str], Path, Path, Path]:
    preflight, health, live_anchor = _fixture_payloads()
    preflight_path = _write_json(tmp_path / "preflight.json", preflight)
    health_path = _write_json(tmp_path / "health.json", health)
    anchor_path = _write_json(tmp_path / "anchor.json", live_anchor)
    args = [
        "--preflight",
        str(preflight_path),
        "--health",
        str(health_path),
        "--anchor",
        str(anchor_path),
        "--ssh-target",
        "viltrox",
        "--root",
        "/opt/viltrox-2.0",
        "--service",
        "viltrox-2.0-test.service",
        "--health-url",
        "http://127.0.0.1:8001/health",
        "--release-id",
        RELEASE_ID,
        "--git-sha",
        GIT_SHA,
        "--target-migration",
        TARGET_MIGRATION,
        "--pending-migrations",
        PENDING,
    ]
    return args, preflight_path, health_path, anchor_path


def _create_plan(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> tuple[Path, str, list[str]]:
    common, _, _, _ = _common_args(tmp_path)
    plan = tmp_path / "bootstrap-plan.json"
    assert anchor.main(["create-plan", *common, "--output", str(plan)]) == 0
    confirm = capsys.readouterr().out.strip()
    assert stat.S_IMODE(plan.stat().st_mode) == 0o600
    assert len(confirm) == 64
    return plan, confirm, common


def test_plan_is_0600_hash_confirmed_and_live_bound(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    plan, confirm, common = _create_plan(tmp_path, capsys)
    assert anchor.main(
        ["verify-plan", *common, "--plan", str(plan), "--confirm", confirm]
    ) == 0
    summary = json.loads(capsys.readouterr().out)
    assert summary["verified"] is True
    assert summary["server_git_sha"] == SERVER_SHA
    assert summary["client_git_sha"] == CLIENT_SHA
    assert summary["server_git_sha"] != summary["client_git_sha"]


def test_plan_refuses_wrong_confirm_mode_and_live_drift(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    plan, confirm, common = _create_plan(tmp_path, capsys)
    assert anchor.main(
        ["verify-plan", *common, "--plan", str(plan), "--confirm", "0" * 64]
    ) == 2
    capsys.readouterr()

    plan.chmod(0o644)
    assert anchor.main(
        ["verify-plan", *common, "--plan", str(plan), "--confirm", confirm]
    ) == 2
    capsys.readouterr()
    plan.chmod(0o600)

    health_path = Path(common[common.index("--health") + 1])
    health = json.loads(health_path.read_text(encoding="utf-8"))
    health["trust"]["server_git_sha"] = "9" * 40
    _write_json(health_path, health)
    assert anchor.main(
        ["verify-plan", *common, "--plan", str(plan), "--confirm", confirm]
    ) == 2
    assert "health_preflight_drift" in capsys.readouterr().err


def test_preflight_allows_only_the_exact_six_blockers(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    common, preflight_path, _, _ = _common_args(tmp_path)
    preflight = json.loads(preflight_path.read_text(encoding="utf-8"))
    preflight["blocking_check_ids"].append("redis.aof_enabled")
    preflight["checks"].append(
        {"id": "redis.aof_enabled", "blocking": True, "pass": False}
    )
    _write_json(preflight_path, preflight)
    output = tmp_path / "rejected-plan.json"
    assert anchor.main(["create-plan", *common, "--output", str(output)]) == 2
    assert not output.exists()
    assert "preflight_blocker_set_mismatch" in capsys.readouterr().err


def test_candidate_seal_is_bound_to_plan(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    plan, confirm, _ = _create_plan(tmp_path, capsys)
    manifest = {
        "schema": 2,
        "release_id": RELEASE_ID,
        "git_sha": GIT_SHA,
        "payload_sha256": "2" * 64,
        "payload_entry_count": 42,
        "immutable_owner_uid": 0,
        "immutable_owner_gid": 0,
        "pending_migrations": PENDING.split(","),
        "forward_compatible_migrations": [],
        "database_strategy": "staging-clone",
        "source_database": "viltrox2_test",
        "target_database": "viltrox2_test_release_0123456789abcdef0123",
        "env_fingerprint_before": "d" * 64,
        "database_owner_release_id": None,
    }
    manifest_path = _write_json(tmp_path / "manifest.json", manifest)
    assert anchor.main(
        [
            "verify-candidate",
            "--plan",
            str(plan),
            "--confirm",
            confirm,
            "--manifest",
            str(manifest_path),
            "--target-database",
            manifest["target_database"],
        ]
    ) == 0
    capsys.readouterr()
    manifest["git_sha"] = "3" * 40
    _write_json(manifest_path, manifest)
    assert anchor.main(
        [
            "verify-candidate",
            "--plan",
            str(plan),
            "--confirm",
            confirm,
            "--manifest",
            str(manifest_path),
            "--target-database",
            manifest["target_database"],
        ]
    ) == 2


def test_collect_anchor_hashes_exact_recovery_artifacts(tmp_path: Path) -> None:
    root = tmp_path / "legacy-root"
    backup = root / "backups" / "ops" / "backup-1"
    backup.mkdir(parents=True)
    (root / ".env").write_text(
        "DATABASE_URL=postgresql://app:private@127.0.0.1/viltrox2_test\n",
        encoding="utf-8",
    )
    (root / "BUILD_GIT_SHA").write_text(f"{CLIENT_SHA}\n", encoding="ascii")
    dump = b"pg-dump"
    ciphertext = b"encrypted-environment"
    (backup / "prod-db.dump").write_bytes(dump)
    (backup / "environment.gpg").write_bytes(ciphertext)
    receipt = {
        "schema_version": "vkpi-off-host-backup-receipt/v1",
        "method": "ssh_pull_verified_mac",
        "stamp": "backup-1",
        "db_artifact": "prod-db.dump",
        "db_sha256": hashlib.sha256(dump).hexdigest(),
        "environment_ciphertext_artifact": "environment.gpg",
        "environment_ciphertext_sha256": hashlib.sha256(ciphertext).hexdigest(),
        "pg_restore_list_passed": True,
        "environment_decryption_verified": True,
        "local_copy_verified": True,
        "plaintext_environment_persisted": False,
    }
    (backup / "off-host-backup-receipt.json").write_text(
        json.dumps(receipt), encoding="utf-8"
    )
    payload = anchor._collect_anchor(
        argparse_namespace(
            root=str(root),
            backup_stamp="backup-1",
            success_marker=str(tmp_path / "marker.json"),
        )
    )
    assert payload["environment"]["database_name"] == "viltrox2_test"
    assert payload["recovery"]["dump_sha256"] == hashlib.sha256(dump).hexdigest()
    assert "app:private@" not in json.dumps(payload)


def argparse_namespace(**values: object):
    class Namespace:
        pass

    namespace = Namespace()
    for key, value in values.items():
        setattr(namespace, key, value)
    return namespace


def test_deploy_bootstrap_is_explicit_and_normal_strict_path_remains() -> None:
    deploy = (OPS / "deploy_local_to_cloud.sh").read_text(encoding="utf-8")
    assert 'FIRST_ATOMIC_BOOTSTRAP_PLAN="${VKPI_FIRST_ATOMIC_BOOTSTRAP_PLAN:-}"' in deploy
    assert "VKPI_FIRST_ATOMIC_BOOTSTRAP_CONFIRM" in deploy
    assert "preflight_blocker_set_mismatch" not in deploy
    assert "verify_legacy_bootstrap_anchor.py\" verify-plan" in deploy
    assert "verify_legacy_bootstrap_anchor.py\" verify-rollback" in deploy
    assert "write-success-marker" in deploy
    assert "SKIP_BACKUP=1 is forbidden for the first atomic bootstrap" in deploy
    assert 'if [ "${FIRST_ATOMIC_BOOTSTRAP_MODE}" != "1" ]; then' in deploy
    assert "--strict-deploy" in deploy
    assert "harden_first_atomic_root()" in deploy
    assert "application root does not match the reviewed legacy or hardened shape" in deploy
    assert "os.chown(root, 0, app_gid, follow_symlinks=False)" in deploy
    assert "rollback never downgrades the root to app-writable ownership" in deploy
    assert deploy.index("harden_first_atomic_root\n", deploy.index("plan verified")) < deploy.index(
        "# Freeze timer-triggered writers"
    )
