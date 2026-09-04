from __future__ import annotations

import json
import subprocess
import sys
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import verify_runtime_health as health  # noqa: E402


HEAD = "a" * 40
MIGRATION = "244_vkpi_event_radar_tenant_scope.sql"
NOW = datetime(2026, 7, 14, 0, 0, 0, tzinfo=timezone.utc)
WORKER_BOOT_SHA256 = "b" * 64
WORKER_NOT_BEFORE = datetime(2026, 7, 13, 23, 58, 0, tzinfo=timezone.utc)


@pytest.fixture
def payload() -> dict:
    return {
        "status": "ok",
        "build": {
            "git_sha": HEAD,
            "client_matches_server": True,
        },
        "trust": {
            "db_startup": {
                "backend": "postgres",
                "state": "completed",
                "schema_migrations": "completed",
            },
            "sha_aligned": True,
            "server_git_sha": HEAD,
            "client_git_sha": HEAD,
            "worker_sha": HEAD,
            "worker_sha_source": "db_heartbeat",
            "worker_heartbeat_source": "db_heartbeat",
            "worker_pid": 4321,
            "worker_boot_nonce_sha256": WORKER_BOOT_SHA256,
            "worker_started_at": "2026-07-13T23:58:30Z",
            "db_migration_max": MIGRATION,
            "db_migration_source": "schema_migrations",
            "db_migration_complete": True,
            "db_migration_exact": True,
            "db_migration_missing_count": 0,
            "db_migration_unexpected_count": 0,
            "worker_online": True,
            "worker_heartbeat": "2026-07-13T23:59:00Z",
            "scheduler_status": {"total": 31, "enabled": 21},
        },
    }


def test_static_runtime_contract_checks_exact_server_and_client_sha(payload: dict) -> None:
    report = health.validate_health(payload, expected_head=HEAD)

    assert report["pass"] is True
    assert report["errors"] == []
    assert report["observed"]["server_git_sha"] == HEAD[:8]


def test_strict_runtime_contract_requires_migration_worker_and_scheduler(payload: dict) -> None:
    report = health.validate_health(
        payload,
        expected_head=HEAD,
        expected_migration=MIGRATION,
        require_worker=True,
        max_worker_age_seconds=180,
        expected_worker_boot_nonce_sha256=WORKER_BOOT_SHA256,
        worker_not_before=WORKER_NOT_BEFORE,
        now=NOW,
    )

    assert report["pass"] is True
    assert report["observed"]["worker_heartbeat_age_seconds"] == 60.0


def test_runtime_contract_verifies_every_worker_in_expected_pool(payload: dict) -> None:
    workers = []
    for index, lane in enumerate(("interactive", "bulk1", "bulk2"), start=1):
        workers.append(
            {
                "worker_name": f"apify-worker-{lane}-host",
                "pid": 4300 + index,
                "worker_sha": HEAD,
                "boot_nonce_sha256": f"{index}" * 64,
                "started_at": "2026-07-13T23:58:30Z",
                "heartbeat": "2026-07-13T23:59:00Z",
                "online": True,
                "lane": "interactive" if lane == "interactive" else "batch",
            }
        )
    payload["trust"]["worker_fleet"] = {
        "online_count": 3,
        "unique_names": True,
        "unique_pids": True,
        "all_worker_sha_aligned": True,
        "lane_coverage": ["batch", "interactive"],
        "workers": workers,
    }

    report = health.validate_health(
        payload,
        expected_head=HEAD,
        expected_migration=MIGRATION,
        require_worker=True,
        expected_worker_count=3,
        now=NOW,
    )

    assert report["pass"] is True
    assert report["observed"]["worker_fleet_online_count"] == 3


def test_runtime_contract_rejects_one_predeployment_worker_in_fresh_fleet(payload: dict) -> None:
    workers = []
    for index, lane in enumerate(("interactive", "bulk1", "bulk2"), start=1):
        workers.append(
            {
                "worker_name": f"apify-worker-{lane}-host",
                "pid": 4400 + index,
                "worker_sha": HEAD,
                "boot_nonce_sha256": f"{index}" * 64,
                "started_at": (
                    "2026-07-13T23:50:00Z" if lane == "bulk2" else "2026-07-13T23:58:30Z"
                ),
                "heartbeat": "2026-07-13T23:59:00Z",
                "online": True,
                "lane": "interactive" if lane == "interactive" else "batch",
            }
        )
    payload["trust"]["worker_fleet"] = {
        "online_count": 3,
        "unique_names": True,
        "unique_pids": True,
        "all_worker_sha_aligned": True,
        "lane_coverage": ["batch", "interactive"],
        "workers": workers,
    }

    report = health.validate_health(
        payload,
        expected_head=HEAD,
        expected_migration=MIGRATION,
        require_worker=True,
        expected_worker_count=3,
        worker_not_before=WORKER_NOT_BEFORE,
        now=NOW,
    )

    assert report["pass"] is False
    assert "worker fleet instance started before this deployment restart" in report["errors"]


def test_runtime_contract_rejects_collapsed_or_duplicate_worker_pool(payload: dict) -> None:
    payload["trust"]["worker_fleet"] = {
        "online_count": 1,
        "unique_names": False,
        "unique_pids": False,
        "all_worker_sha_aligned": True,
        "lane_coverage": ["all"],
        "workers": [],
    }

    report = health.validate_health(
        payload,
        expected_head=HEAD,
        require_worker=True,
        expected_worker_count=3,
        now=NOW,
    )

    assert report["pass"] is False
    assert "worker fleet online count does not match expectation" in report["errors"]
    assert "worker fleet names are not unique" in report["errors"]
    assert "worker fleet does not cover interactive and batch lanes" in report["errors"]


def test_strict_runtime_contract_rejects_stale_or_wrong_worker(payload: dict) -> None:
    broken = deepcopy(payload)
    broken["trust"]["worker_sha"] = "b" * 40
    broken["trust"]["worker_online"] = False
    broken["trust"]["worker_heartbeat"] = "2026-07-13T22:00:00Z"
    broken["trust"]["scheduler_status"] = {"total": 0, "enabled": 0}

    report = health.validate_health(
        broken,
        expected_head=HEAD,
        expected_migration=MIGRATION,
        require_worker=True,
        now=NOW,
    )

    assert report["pass"] is False
    assert "worker SHA does not match local HEAD" in report["errors"]
    assert "worker is not online" in report["errors"]
    assert "worker heartbeat is stale" in report["errors"]
    assert "scheduler registration counts are not trustworthy" in report["errors"]


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("worker_sha_source", "assumed_same_repo", "worker SHA is not sourced"),
        ("worker_heartbeat_source", "apify_jobs_activity", "worker liveness is not sourced"),
        ("worker_boot_nonce_sha256", "c" * 64, "worker boot nonce does not match"),
        ("worker_started_at", "2026-07-13T23:00:00Z", "worker started before"),
    ],
)
def test_strict_runtime_contract_rejects_stale_or_unbound_worker_identity(
    payload: dict,
    field: str,
    value: str,
    message: str,
) -> None:
    broken = deepcopy(payload)
    broken["trust"][field] = value

    report = health.validate_health(
        broken,
        expected_head=HEAD,
        expected_migration=MIGRATION,
        require_worker=True,
        expected_worker_boot_nonce_sha256=WORKER_BOOT_SHA256,
        worker_not_before=WORKER_NOT_BEFORE,
        now=NOW,
    )

    assert report["pass"] is False
    assert any(message in item for item in report["errors"])


def test_strict_runtime_contract_rejects_migration_and_sha_mismatch(payload: dict) -> None:
    broken = deepcopy(payload)
    broken["build"]["git_sha"] = "b" * 40
    broken["build"]["client_matches_server"] = False
    broken["trust"]["sha_aligned"] = False
    broken["trust"]["client_git_sha"] = "c" * 40
    broken["trust"]["db_migration_max"] = "243_old.sql"
    broken["trust"]["db_migration_source"] = "code_manifest_fallback"
    broken["trust"]["db_startup"] = {
        "backend": "sqlite",
        "state": "failed",
        "schema_migrations": "failed",
    }

    report = health.validate_health(
        broken,
        expected_head=HEAD,
        expected_migration=MIGRATION,
    )

    assert report["pass"] is False
    assert "server build SHA does not match local HEAD" in report["errors"]
    assert "frontend build does not match server" in report["errors"]
    assert "health trust SHA alignment is not true" in report["errors"]
    assert "trusted client SHA does not match local HEAD" in report["errors"]
    assert "applied migration max does not match local manifest" in report["errors"]
    assert "migration truth is not sourced from schema_migrations" in report["errors"]
    assert "database startup backend is not postgres" in report["errors"]
    assert "database startup did not complete" in report["errors"]
    assert "database migration startup stage did not complete" in report["errors"]


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("db_migration_complete", False, "applied migration set is incomplete"),
        ("db_migration_exact", False, "applied migration set is not exact"),
        ("db_migration_missing_count", 1, "db_migration_missing_count is not zero"),
        ("db_migration_unexpected_count", 1, "db_migration_unexpected_count is not zero"),
    ],
)
def test_release_acceptance_rejects_incomplete_migration_sets(
    payload: dict,
    field: str,
    value: object,
    message: str,
) -> None:
    payload["trust"][field] = value

    report = health.validate_health(
        payload,
        expected_head=HEAD,
        expected_migration=MIGRATION,
        require_migration_set_complete=True,
    )

    assert report["pass"] is False
    assert message in report["errors"]


def test_complete_migration_flag_is_enforced_without_expected_max(payload: dict) -> None:
    payload["trust"]["db_migration_missing_count"] = 1

    report = health.validate_health(
        payload,
        expected_head=HEAD,
        require_migration_set_complete=True,
    )

    assert report["pass"] is False
    assert "db_migration_missing_count is not zero" in report["errors"]


def test_complete_migration_cli_flag_does_not_require_expected_max(payload: dict) -> None:
    payload["trust"]["db_migration_complete"] = False
    proc = subprocess.run(
        [
            sys.executable,
            str(SCRIPTS / "verify_runtime_health.py"),
            "--expected-head",
            HEAD,
            "--require-migration-set-complete",
        ],
        input=json.dumps(payload).encode("utf-8"),
        capture_output=True,
        check=False,
        timeout=5,
    )

    report = json.loads(proc.stdout)
    assert proc.returncode == 1
    assert "applied migration set is incomplete" in report["errors"]


def test_strict_deploy_implicitly_requires_complete_migration_set(payload: dict) -> None:
    payload["trust"]["db_migration_exact"] = False
    proc = subprocess.run(
        [
            sys.executable,
            str(SCRIPTS / "verify_runtime_health.py"),
            "--strict-deploy",
            "--expected-head",
            HEAD,
            "--expected-migration",
            MIGRATION,
            "--require-worker",
            "--max-worker-age-seconds",
            "180",
            "--expected-worker-boot-nonce-sha256",
            WORKER_BOOT_SHA256,
            "--worker-not-before",
            "2026-07-13T23:58:00Z",
            "--now",
            "2026-07-14T00:00:00Z",
        ],
        input=json.dumps(payload).encode("utf-8"),
        capture_output=True,
        check=False,
        timeout=5,
    )

    report = json.loads(proc.stdout)
    assert proc.returncode == 1
    assert "applied migration set is not exact" in report["errors"]


@pytest.mark.parametrize(
    "raw",
    [
        b'{"status":"ok","status":"ok"}',
        b'{"status":NaN}',
        b'[]',
        b'',
    ],
)
def test_strict_json_rejects_ambiguous_or_invalid_health(raw: bytes) -> None:
    if raw == b"[]":
        assert health.validate_health(health.strict_json_loads(raw), expected_head=HEAD)["pass"] is False
    else:
        with pytest.raises(ValueError):
            health.strict_json_loads(raw)


def test_cli_emits_bounded_json_and_nonzero_for_failure(payload: dict) -> None:
    broken = deepcopy(payload)
    broken["trust"]["worker_heartbeat"] = "2026-07-13T20:00:00Z"
    proc = subprocess.run(
        [
            sys.executable,
            str(SCRIPTS / "verify_runtime_health.py"),
            "--expected-head",
            HEAD,
            "--expected-migration",
            MIGRATION,
            "--require-worker",
            "--max-worker-age-seconds",
            "180",
            "--expected-worker-boot-nonce-sha256",
            WORKER_BOOT_SHA256,
            "--worker-not-before",
            "2026-07-13T23:58:00Z",
            "--strict-deploy",
            "--now",
            "2026-07-14T00:00:00Z",
        ],
        input=json.dumps(broken).encode("utf-8"),
        capture_output=True,
        check=False,
        timeout=5,
    )

    report = json.loads(proc.stdout)
    assert proc.returncode == 1
    assert report["pass"] is False
    assert "worker heartbeat is stale" in report["errors"]
    assert "a" * 40 not in proc.stdout.decode("utf-8")


def test_strict_fleet_cli_uses_exact_count_without_shared_boot_nonce(payload: dict) -> None:
    workers = []
    for index, lane in enumerate(("interactive", "bulk1", "bulk2"), start=1):
        workers.append(
            {
                "worker_name": f"apify-worker-{lane}-host",
                "pid": 4500 + index,
                "worker_sha": HEAD,
                "boot_nonce_sha256": f"{index}" * 64,
                "started_at": "2026-07-13T23:58:30Z",
                "heartbeat": "2026-07-13T23:59:00Z",
                "online": True,
                "lane": "interactive" if lane == "interactive" else "batch",
            }
        )
    payload["trust"]["worker_fleet"] = {
        "online_count": 3,
        "unique_names": True,
        "unique_pids": True,
        "all_worker_sha_aligned": True,
        "lane_coverage": ["batch", "interactive"],
        "workers": workers,
    }
    proc = subprocess.run(
        [
            sys.executable,
            str(SCRIPTS / "verify_runtime_health.py"),
            "--strict-deploy",
            "--expected-head",
            HEAD,
            "--expected-migration",
            MIGRATION,
            "--require-worker",
            "--max-worker-age-seconds",
            "180",
            "--worker-not-before",
            "2026-07-13T23:58:00Z",
            "--expected-worker-count",
            "3",
            "--now",
            "2026-07-14T00:00:00Z",
        ],
        input=json.dumps(payload).encode("utf-8"),
        capture_output=True,
        check=False,
        timeout=5,
    )

    report = json.loads(proc.stdout)
    assert proc.returncode == 0, proc.stderr.decode("utf-8")
    assert report["pass"] is True
    assert report["observed"]["worker_fleet_online_count"] == 3


@pytest.mark.parametrize(
    "omitted",
    [
        "--expected-head",
        "--expected-migration",
        "--require-worker",
        "--max-worker-age-seconds",
        "--expected-worker-boot-nonce-sha256",
        "--worker-not-before",
    ],
)
def test_strict_deploy_cli_fails_closed_when_key_expectation_is_missing(
    payload: dict, omitted: str
) -> None:
    option_values = {
        "--expected-head": HEAD,
        "--expected-migration": MIGRATION,
        "--max-worker-age-seconds": "180",
        "--expected-worker-boot-nonce-sha256": WORKER_BOOT_SHA256,
        "--worker-not-before": "2026-07-13T23:58:00Z",
    }
    argv = ["--strict-deploy", "--require-worker"]
    for option, value in option_values.items():
        if option != omitted:
            argv.extend([option, value])
    if omitted == "--require-worker":
        argv.remove("--require-worker")

    proc = subprocess.run(
        [sys.executable, str(SCRIPTS / "verify_runtime_health.py"), *argv],
        input=json.dumps(payload).encode("utf-8"),
        capture_output=True,
        check=False,
        timeout=5,
    )

    report = json.loads(proc.stdout)
    assert proc.returncode == 2
    assert report["pass"] is False
    assert "runtime health validation setup failed" in report["errors"][0]


def test_cloud_deploy_requires_clean_source_and_post_restart_strict_verification() -> None:
    deploy = (SCRIPTS / "ops" / "deploy_local_to_cloud.sh").read_text(encoding="utf-8")

    assert '"${TRUSTED_CANDIDATE_VERIFIER}" run-deploy-gate' in deploy
    assert '--snapshot "${DEPLOY_CANDIDATE_DIR}"' in deploy
    assert '--health-env-file "${LOCAL_HEALTH_ENV_FILE}"' in deploy
    assert '--health-url "${health_url}"' in deploy
    assert '--base-url "${base_url}"' in deploy
    assert "ALLOW_DIRTY_DEPLOY" not in deploy
    assert "git status --porcelain=v1 --untracked-files=all" in deploy
    assert deploy.count("assert_deploy_source_unchanged") >= 3
    assert deploy.count("scripts/ops/fetch_runtime_health.py") >= 3
    assert "curl -fsS '${HEALTH_URL}'" not in deploy
    assert "--env-file '${REMOTE_ROOT}/.env'" in deploy
    assert "fetch_predeploy_runtime_health" in deploy
    assert "sudo -n -u viltrox -g viltrox" in deploy
    assert '< "${DEPLOY_CANDIDATE_DIR}/scripts/ops/fetch_runtime_health.py"' in deploy
    predeploy_read = deploy.index('REMOTE_PREDEPLOY_HEALTH_JSON="$(fetch_predeploy_runtime_health)"')
    first_release_sync = deploy.index("rsync -az --delete")
    assert predeploy_read < first_release_sync

    cutover_install = deploy.index("sudo install -o root -g root -m 0644 '${REMOTE_CURRENT_DIR}")
    service_restart = deploy.index("sudo systemctl restart '${SERVICE_NAME}'", cutover_install)
    worker_restart = deploy.index(
        "sudo systemctl restart ${WORKER_SYSTEMD_UNIT_ARGS}", service_restart
    )
    remote_fetch = deploy.index('REMOTE_HEALTH_JSON="$(ssh', worker_restart)
    strict_validator = deploy.index(
        '"${DEPLOY_CANDIDATE_DIR}/scripts/verify_runtime_health.py"', remote_fetch
    )
    process_binding = deploy.index(
        "scripts/ops/verify_apify_worker_process_binding.py", strict_validator
    )
    asset_check = deploy.index('LOCAL_ASSET="${BROWSER_EXPECTED_APP_ASSET}"')
    assert (
        service_restart
        < worker_restart
        < remote_fetch
        < strict_validator
        < process_binding
        < asset_check
    )

    strict_call = deploy[strict_validator:asset_check]
    for required in (
        "--strict-deploy",
        "--expected-head",
        "--expected-migration",
        "--require-migration-set-complete",
        "--require-worker",
        "--max-worker-age-seconds",
        "--expected-worker-boot-nonce-sha256",
        "--worker-not-before",
        "--expected-worker-count",
    ):
        assert required in strict_call
    assert "Post-restart remote runtime trust validation failed" in strict_call
    assert "exit 1" in strict_call
