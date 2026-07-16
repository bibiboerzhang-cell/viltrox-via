from __future__ import annotations

import os
import json
import re
import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def _read(relative: str) -> str:
    return (ROOT / relative).read_text(encoding="utf-8")


def test_cloud_web_unit_uses_bounded_gunicorn_concurrency() -> None:
    unit = _read("scripts/ops/systemd/viltrox-2.0-test.service")

    assert (
        "ExecStart=/usr/bin/env VKPI_SYSTEMD_ADMIN_WEB_CONTRACT=1 "
        "ENVIRONMENT=production RUNTIME_ENV_QUIET=1 "
        "PYTHON_BIN=/opt/viltrox-2.0/.venv/bin/python /bin/bash "
        "/opt/viltrox-2.0/current/scripts/start_admin.sh"
    ) in unit
    assert "WorkingDirectory=/opt/viltrox-2.0/current" in unit
    assert "Environment=PYTHONPATH=/opt/viltrox-2.0/current/backend" in unit
    assert "Environment=WEB_CONCURRENCY=2" in unit
    assert "Environment=ADMIN_DAEMON=0" in unit
    assert "Environment=RUNTIME_ENV_QUIET=1" in unit
    assert "Environment=ENABLE_SCHEDULER=1" in unit
    assert "Environment=ENABLE_UPLOAD_CLEANUP=0" in unit
    assert "Wants=network-online.target" in unit
    assert "After=network-online.target" in unit
    assert "uvicorn app.main:app" not in unit
    assert "--workers 1" not in unit

    assert "User=viltrox" in unit
    assert "Group=viltrox" in unit
    assert "UMask=0027" in unit
    assert "ProtectSystem=strict" in unit
    assert "ProtectHome=true" in unit
    assert "PrivateTmp=true" in unit
    assert "PrivateDevices=true" in unit
    assert "NoNewPrivileges=true" in unit
    assert "CapabilityBoundingSet=\n" in unit
    assert "AmbientCapabilities=\n" in unit
    assert "ReadOnlyPaths=/opt/viltrox-2.0/current" in unit
    assert "ReadOnlyPaths=/opt/viltrox-2.0/.env" in unit
    assert "ReadOnlyPaths=-/opt/viltrox-2.0/.release-controller" in unit
    assert "InaccessiblePaths=/opt/viltrox-2.0/backups" in unit
    assert {
        line.split("=", 1)[1]
        for line in unit.splitlines()
        if line.startswith("ReadWritePaths=")
    } == {
        "/opt/viltrox-2.0/uploads",
        "/opt/viltrox-2.0/runtime",
        "/opt/viltrox-2.0/frames",
        "/opt/viltrox-2.0/creator_profiles",
    }

    start = _read("scripts/start_admin.sh")
    production_contract = start.split('if [[ "$SYSTEMD_ADMIN_WEB_CONTRACT" == "1" ]]; then', 2)[2]
    assert "export POSTGRES_POOL_MIN_SIZE=2" in production_contract
    assert "export POSTGRES_POOL_MAX_SIZE=16" in production_contract
    assert "export POSTGRES_POOL_TIMEOUT_SEC=30" in production_contract


def test_deploy_installs_reviewed_unit_before_restart() -> None:
    deploy = _read("scripts/ops/deploy_local_to_cloud.sh")

    verify_at = deploy.index("sudo systemd-analyze verify")
    install_at = deploy.index("sudo install -o root -g root -m 0644", verify_at)
    reload_at = deploy.index("sudo systemctl daemon-reload", install_at)
    restart_at = deploy.index("sudo systemctl restart", reload_at)
    assert verify_at < install_at < reload_at < restart_at
    assert "^scripts/ops/systemd/[A-Za-z0-9@_.-]+\\.service$" in deploy
    assert "one direct reviewed unit" in deploy
    assert "bound to /opt/viltrox-2.0 and user/group viltrox" in deploy
    assert "Reviewed web service unit is missing" in deploy


def test_deploy_uses_atomic_release_and_fail_closed_migration_contract() -> None:
    deploy = _read("scripts/ops/deploy_local_to_cloud.sh")

    sync_at = deploy.index('rsync -az --delete')
    prepare_at = deploy.index("atomic_release_layout.py' prepare")
    activate_at = deploy.index("atomic_release_layout.py' activate")
    install_at = deploy.index("sudo install -o root -g root -m 0644 '${REMOTE_CURRENT_DIR}")
    restart_at = deploy.index("sudo systemctl restart '${SERVICE_NAME}'", install_at)
    assert sync_at < prepare_at < activate_at < install_at < restart_at
    assert '-- "${DEPLOY_CANDIDATE_DIR}/" "${SSH_TARGET}:${REMOTE_RELEASE_DIR}/"' in deploy
    assert './ "${SSH_TARGET}:${REMOTE_ROOT}/"' not in deploy
    assert "REMOTE_CURRENT_DIR=\"${REMOTE_ROOT}/current\"" in deploy
    assert "atomic_release_layout.py' restore" in deploy
    assert "VKPI_FORWARD_COMPATIBLE_MIGRATIONS" in deploy
    assert 'FORWARD_COMPATIBILITY_DECLARATION}" != "${PENDING_MIGRATIONS}' in deploy
    assert "SKIP_BACKUP=1 is forbidden when pending migrations exist" in deploy
    assert "database is never auto-restored" in deploy
    assert "-m pip install" not in deploy
    assert "-m yt_dlp --version" in deploy


def test_deploy_proves_clone_receipt_from_root_owned_release_controller() -> None:
    deploy = _read("scripts/ops/deploy_local_to_cloud.sh")

    assert 'root / ".release-controller"' in deploy
    assert 'rollback_dir / "database-clone.json"' in deploy
    assert 'rollback_dir / "metadata.sha256"' in deploy
    assert 'rollback_dir / "metadata.json"' in deploy
    assert 'getattr(os, "O_NOFOLLOW", 0)' in deploy
    assert "info.st_nlink != 1" in deploy
    assert "stat.S_IMODE(info.st_mode) != 0o600" in deploy


def test_deploy_rsync_excludes_local_cache_artifacts_not_runtime_payload() -> None:
    deploy = _read("scripts/ops/deploy_local_to_cloud.sh")

    sync_at = deploy.index("rsync -az --delete")
    sync_end = deploy.index(
        '-- "${DEPLOY_CANDIDATE_DIR}/" "${SSH_TARGET}:${REMOTE_RELEASE_DIR}/"',
        sync_at,
    )
    sync = deploy[sync_at:sync_end]

    for cache_pattern in (
        ".git",
        ".venv",
        "venv",
        "node_modules",
        "runtime",
        "uploads",
        "frames",
        "backups",
        "creator_profiles",
        "__pycache__",
        "*.pyc",
        "*.pyo",
        ".pytest_cache",
        ".mypy_cache",
        ".ruff_cache",
        ".vite",
        ".claude",
        ".codegraph",
        ".codex-backups",
        ".integration",
        ".state",
        "coverage",
        "artifacts",
        "exports",
        "output",
        "outputs",
        "tmp",
        "reports/generated",
        ".env",
        ".env.*",
        "id_ed25519",
        "id_rsa",
        "submissions.db",
        "submissions.db-shm",
        "submissions.db-wal",
        "*.dump",
        "*.key",
        "*.log",
        "*.p12",
        "*.pem",
        "*.pfx",
        "*.sqlite",
        "*.sqlite3",
        ".DS_Store",
    ):
        assert f"--exclude '{cache_pattern}'" in sync

    # The exclusions must stay narrow: source, the built frontend, and release
    # operations are part of the immutable runtime payload.
    for required_payload_pattern in (
        "*.py",
        "*.js",
        "backend/",
        "frontend/dist/",
        "scripts/",
        "reports/vkpi_*.html",
        "reports/vkpi_*.md",
    ):
        assert f"--exclude '{required_payload_pattern}'" not in sync


def test_deploy_requires_and_reverifies_one_head_bound_frozen_candidate() -> None:
    deploy = _read("scripts/ops/deploy_local_to_cloud.sh")

    assert "VKPI_DEPLOY_CANDIDATE_DIR and VKPI_DEPLOY_CANDIDATE_MANIFEST are mandatory" in deploy
    verify_command = deploy.split("verify_deploy_candidate()", 1)[1].split("}\n", 1)[0]
    for required in (
        "freeze_worktree_candidate.py",
        "verify-deploy-source",
        '--manifest "${DEPLOY_CANDIDATE_MANIFEST}"',
        '--snapshot "${DEPLOY_CANDIDATE_DIR}"',
        '--expected-head "${LOCAL_GIT_SHA}"',
        '--expected-branch "${LOCAL_GIT_BRANCH}"',
    ):
        assert required in verify_command

    calls = [
        match.start()
        for match in re.finditer(r"^verify_deploy_candidate$", deploy, re.MULTILINE)
    ]
    assert len(calls) == 3
    gate = deploy.index("gate: strict code + runtime trust verification")
    remote_create = deploy.index('ssh "${SSH_TARGET}" "sudo install -d')
    rsync = deploy.index("rsync -az --delete", remote_create)
    remote_stamp_check = deploy.index("Uploaded candidate build SHA mismatch", rsync)
    assert calls[0] < gate < calls[1] < remote_create < rsync < calls[2] < remote_stamp_check
    assert '-- "${DEPLOY_CANDIDATE_DIR}/" "${SSH_TARGET}:${REMOTE_RELEASE_DIR}/"' in deploy
    assert "printf '%s\\n' '${LOCAL_GIT_SHA}' > BUILD_GIT_SHA" not in deploy
    assert (
        'LOCAL_ASSET="$(grep -o \'app-[A-Za-z0-9_-]*\\.js\' '
        '"${DEPLOY_CANDIDATE_DIR}/frontend/dist/index.html" | head -1)"'
        in deploy
    )
    assert (
        'LOCAL_ASSET="$(grep -o \'app-[A-Za-z0-9_-]*\\.js\' '
        'frontend/dist/index.html | head -1)"'
        not in deploy
    )


def test_deploy_remote_python_cannot_write_bytecode_into_release() -> None:
    deploy = _read("scripts/ops/deploy_local_to_cloud.sh")

    # Local checks use PROJECT_ROOT/.venv.  Every system-python or REMOTE_ROOT
    # interpreter in this script therefore executes on the remote host, where a
    # late import from release/current would otherwise invalidate the seal.
    markers = ("python3", "'${REMOTE_ROOT}/.venv/bin/python'")
    for marker in markers:
        matches = list(re.finditer(re.escape(marker), deploy))
        assert matches, f"missing reviewed remote Python marker: {marker}"
        for match in matches:
            tail = deploy[match.end() : match.end() + 16]
            assert re.match(r"\s+-B(?:\s|$)", tail), (
                f"remote Python invocation is missing -B near offset {match.start()}"
            )

            prefix = deploy[: match.start()]
            boundaries = (
                (prefix.rfind("\n"), 1),
                (prefix.rfind("&&"), 2),
                (prefix.rfind("||"), 2),
                (prefix.rfind(";"), 1),
                (prefix.rfind("|"), 1),
            )
            boundary, width = max(boundaries, key=lambda item: item[0])
            command_prefix = prefix[boundary + width :]
            assert "PYTHONDONTWRITEBYTECODE=1" in command_prefix, (
                "remote Python invocation is missing the bytecode environment "
                f"guard near offset {match.start()}"
            )

    assert "sudo python3 " not in deploy
    assert "'${REMOTE_ROOT}/.venv/bin/python' scripts/" not in deploy
    assert "'${REMOTE_ROOT}/.venv/bin/python' '${REMOTE_RELEASE_DIR}" not in deploy


def test_reviewed_worker_units_execute_only_from_atomic_current() -> None:
    for relative in (
        "scripts/ops/systemd/vkpi-worker-interactive.service",
        "scripts/ops/systemd/vkpi-worker-bulk@.service",
    ):
        unit = _read(relative)
        assert "WorkingDirectory=/opt/viltrox-2.0/current" in unit
        assert "Environment=PYTHONPATH=/opt/viltrox-2.0/current/backend" in unit


def test_reviewed_worker_units_run_nonroot_with_minimal_mutable_surface() -> None:
    expected_writes = {"/opt/viltrox-2.0/uploads"}
    expected_readonly = {
        "/opt/viltrox-2.0/current",
        "/opt/viltrox-2.0/.env",
        "-/opt/viltrox-2.0/.env.production",
        "/opt/viltrox-2.0/runtime",
        "/opt/viltrox-2.0/frames",
        "/opt/viltrox-2.0/creator_profiles",
    }
    for relative in (
        "scripts/ops/systemd/vkpi-worker-interactive.service",
        "scripts/ops/systemd/vkpi-worker-bulk@.service",
    ):
        unit = _read(relative)
        assert "User=viltrox" in unit
        assert "Group=viltrox" in unit
        assert "UMask=0027" in unit
        assert "ProtectSystem=strict" in unit
        assert "ProtectHome=true" in unit
        assert "PrivateTmp=true" in unit
        assert "PrivateDevices=true" in unit
        assert "NoNewPrivileges=true" in unit
        assert "CapabilityBoundingSet=\n" in unit
        assert "AmbientCapabilities=\n" in unit
        assert "RestrictAddressFamilies=AF_UNIX AF_INET AF_INET6" in unit
        assert "ReadOnlyPaths=/opt/viltrox-2.0/.env" in unit
        assert "InaccessiblePaths=/opt/viltrox-2.0/backups" in unit
        assert "ENABLE_BROWSER=0" in unit.split("ExecStart=", 1)[1]
        assert "PYTHONDONTWRITEBYTECODE=1" in unit
        writes = {
            line.split("=", 1)[1]
            for line in unit.splitlines()
            if line.startswith("ReadWritePaths=")
        }
        assert writes == expected_writes
        readonly = {
            line.split("=", 1)[1]
            for line in unit.splitlines()
            if line.startswith("ReadOnlyPaths=")
        }
        assert readonly == expected_readonly
        assert "/opt/viltrox-2.0/backups" not in writes
        assert "/opt/viltrox-2.0/.env" not in writes
        preflight = next(
            line for line in unit.splitlines() if line.startswith("ExecStartPre=")
        )
        assert "worker-runtime-preflight" in preflight
        assert "--app-user viltrox --app-group viltrox" in preflight
        assert "--require-sandbox-readonly" in preflight


def test_deploy_restarts_and_validates_exact_seven_service_worker_fleet() -> None:
    deploy = _read("scripts/ops/deploy_local_to_cloud.sh")

    expected_units = [
        "vkpi-worker-interactive.service",
        *(f"vkpi-worker-bulk@{index}.service" for index in range(1, 7)),
    ]
    assert "EXPECTED_WORKER_COUNT=7" in deploy
    fleet_block = deploy.split("WORKER_SYSTEMD_UNITS=(", 1)[1].split(")", 1)[0]
    configured_units = [line.strip() for line in fleet_block.splitlines() if line.strip()]
    assert configured_units == expected_units
    assert "sudo systemctl restart ${WORKER_SYSTEMD_UNIT_ARGS}" in deploy
    assert "--expected-worker-count \"${EXPECTED_WORKER_COUNT}\"" in deploy
    assert "fleet.get('all_worker_sha_aligned') is True" in deploy
    assert "{'interactive', 'batch'}.issubset" in deploy
    assert "start_worker.sh" not in deploy
    assert "VKPI_WORKER_BOOT_NONCE=" not in deploy

    interactive = _read("scripts/ops/systemd/vkpi-worker-interactive.service")
    bulk = _read("scripts/ops/systemd/vkpi-worker-bulk@.service")
    assert "ExecStart=/usr/bin/env APP_ROLE=worker ENVIRONMENT=production" in interactive
    assert "APIFY_WORKER_CLAIM_LANE=interactive" in interactive.split("ExecStart=", 1)[1]
    assert "APIFY_WORKER_HEARTBEAT_NAME=apify-worker-interactive" in interactive.split(
        "ExecStart=", 1
    )[1]
    assert "ExecStart=/usr/bin/env APP_ROLE=worker ENVIRONMENT=production" in bulk
    assert "APIFY_WORKER_CLAIM_LANE=batch" in bulk.split("ExecStart=", 1)[1]
    assert "APIFY_WORKER_HEARTBEAT_NAME=apify-worker-bulk-%i" in bulk.split("ExecStart=", 1)[1]
    assert "'/etc/systemd/system/vkpi-worker-interactive.service'" in deploy
    assert "'/etc/systemd/system/vkpi-worker-bulk@.service'" in deploy


def test_deploy_proves_nonroot_worker_permissions_before_switching_current() -> None:
    deploy = _read("scripts/ops/deploy_local_to_cloud.sh")

    layout_at = deploy.index("worker-layout-preflight")
    seal_at = deploy.index("atomic_release_layout.py' seal", layout_at)
    verify_seal_at = deploy.index("atomic_release_layout.py' verify-seal", seal_at)
    runtime_at = deploy.index("worker-runtime-preflight", verify_seal_at)
    verify_at = deploy.index("systemd-analyze verify", runtime_at)
    prepare_at = deploy.index("atomic_release_layout.py' prepare", verify_at)
    activate_at = deploy.index("atomic_release_layout.py' activate", prepare_at)
    assert layout_at < seal_at < verify_seal_at < runtime_at < verify_at < prepare_at < activate_at
    assert "--provision-missing" in deploy[layout_at:seal_at]
    assert "--owner-uid 0 --owner-gid 0" in deploy[seal_at:verify_seal_at]
    assert "--expected-owner-uid 0 --expected-owner-gid 0" in deploy[
        verify_seal_at:runtime_at
    ]
    assert "sudo -u '${REMOTE_APP_USER}' -g '${REMOTE_APP_GROUP}'" in deploy
    assert "HOME=/tmp/vkpi-worker-home" in deploy[runtime_at - 300 : runtime_at]
    assert "REMOTE_APP_USER=\"${REMOTE_APP_USER}\"" in deploy
    assert "REMOTE_APP_GROUP=\"${REMOTE_APP_GROUP}\"" in deploy


def test_deploy_stages_writable_release_under_root_owned_nonreusable_parent() -> None:
    deploy = _read("scripts/ops/deploy_local_to_cloud.sh")

    parent_at = deploy.index(
        "sudo install -d -o root -g root -m 0755 '${REMOTE_RELEASES_DIR}'"
    )
    refuse_at = deploy.index("Refusing to reuse an existing release destination", parent_at)
    staging_at = deploy.index(
        "sudo install -d -o '${REMOTE_APP_USER}' -g '${REMOTE_APP_GROUP}' -m 0755 "
        "'${REMOTE_RELEASE_DIR}'",
        refuse_at,
    )
    rsync_at = deploy.index("rsync -az --delete", staging_at)
    seal_at = deploy.index("atomic_release_layout.py' seal", rsync_at)
    verify_at = deploy.index("atomic_release_layout.py' verify-seal", seal_at)
    prepare_at = deploy.index("atomic_release_layout.py' prepare", verify_at)
    assert parent_at < refuse_at < staging_at < rsync_at < seal_at < verify_at < prepare_at


def test_first_atomic_bootstrap_provisions_only_safe_job_results_before_seal() -> None:
    deploy = _read("scripts/ops/deploy_local_to_cloud.sh")
    bootstrap = deploy.split(
        "# it.  Provision only the absent non-secret job-results directory needed by",
        1,
    )[1].split("\nelse\n", 1)[0]

    parent_guard = bootstrap.index("bootstrap shared runtime parent is unsafe")
    provision = bootstrap.index(
        "sudo install -d -o '${REMOTE_APP_USER}' -g '${REMOTE_APP_GROUP}' -m 0750"
    )
    child_guard = bootstrap.index("bootstrap job-results directory is unsafe")
    seal = bootstrap.index("atomic_release_layout.py' seal")
    assert parent_guard < provision < child_guard < seal
    assert "[ ! -d \\\"\\${runtime_dir}\\\" ] || [ -L \\\"\\${runtime_dir}\\\" ]" in bootstrap
    assert "[ ! -e \\\"\\${job_results_dir}\\\" ] && [ ! -L \\\"\\${job_results_dir}\\\" ]" in bootstrap
    assert "${REMOTE_APP_USER}:${REMOTE_APP_GROUP}:755" in bootstrap
    assert "${REMOTE_APP_USER}:${REMOTE_APP_GROUP}:750" in bootstrap


def test_deploy_mints_browser_token_only_after_remote_identity_and_never_persists_it() -> None:
    deploy = _read("scripts/ops/deploy_local_to_cloud.sh")

    identity_at = deploy.index("Post-restart remote runtime trust validation failed")
    acceptance_at = deploy.index("post-restart remote acceptance passed", identity_at)
    mint_at = deploy.index("scripts/ops/mint_browser_gate_token.py", acceptance_at)
    capture_at = deploy.index("scripts/capture_browser_console_cdp.mjs", mint_at)
    assert identity_at < acceptance_at < mint_at < capture_at
    assert 'if [ -z "${POST_DEPLOY_BROWSER_TOKEN}" ]; then' in deploy[mint_at - 1200 : mint_at]
    assert "sudo -n -u '${REMOTE_APP_USER}' -g '${REMOTE_APP_GROUP}' env -i" in deploy
    assert "ENVIRONMENT=production V2_PRODUCTION_MODE=1 APP_ROLE=admin-web" in deploy
    assert "VKPI_BROWSER_GATE_TOKEN_TTL_SECONDS must be an integer within [60, 300]" in deploy
    assert 'POST_DEPLOY_BROWSER_TOKEN="$(ssh' in deploy
    assert 'VKPI_BROWSER_GATE_TOKEN="${POST_DEPLOY_BROWSER_TOKEN}" node' in deploy
    assert '--ttl-seconds \'${BROWSER_GATE_TOKEN_TTL_SECONDS}\'' in deploy
    assert 'POST_DEPLOY_BROWSER_TOKEN=""' in deploy[capture_at : capture_at + 800]
    assert "VKPI_BROWSER_GATE_TOKEN and" not in deploy


def test_deploy_browser_gate_runs_reviewed_21_page_and_network_contract() -> None:
    deploy = _read("scripts/ops/deploy_local_to_cloud.sh")
    capture = _read("scripts/capture_browser_console_cdp.mjs")
    manifest = json.loads(_read("scripts/browser_gate_pages.json"))

    assert manifest["schema_version"] == "vkpi-browser-page-manifest/v1"
    assert len(manifest["pages"]) == 21
    assert len({row["family"] for row in manifest["pages"]}) == 21
    assert "browser_gate_pages.json" in capture
    assert '"Network.responseReceived"' in capture
    assert '"Network.loadingFailed"' in capture
    assert "navigateAndProbePage" in capture
    assert 'BROWSER_GATE_TOKEN_TTL_SECONDS="${VKPI_BROWSER_GATE_TOKEN_TTL_SECONDS:-300}"' in deploy
    assert '--page-settle-ms "${BROWSER_GATE_PAGE_SETTLE_MS}"' in deploy
    assert '--page-timeout-ms "${BROWSER_GATE_PAGE_TIMEOUT_MS}"' in deploy
    assert "VKPI_BROWSER_GATE_EXTERNAL_MEDIA_403_ORIGINS must contain only exact external HTTPS origins" in deploy


def test_backup_owns_its_separate_write_surface_and_never_chowns_history() -> None:
    backup = _read("scripts/ops/backup_prod_vkpi.sh")

    assert "sudo -n -u '${REMOTE_APP_USER}' -g '${REMOTE_APP_GROUP}'" in backup
    assert backup.index("sudo -n -u '${REMOTE_APP_USER}'") < backup.index("<<'REMOTE'")
    assert 'if [ "$(id -un)" != "${REMOTE_APP_USER}" ]' in backup
    assert "backup root ownership mismatch" in backup
    assert "refusing recursive chown" in backup
    assert "mktemp backups/.vkpi-backup-preflight.XXXXXX" in backup
    assert "rm -f -- \"${backup_canary}\"" in backup
    executable_lines = "\n".join(
        line for line in backup.splitlines() if not line.lstrip().startswith("#")
    )
    assert not re.search(r"(^|\s)chown(\s|$)", executable_lines)


def test_health_lane_classifier_matches_systemd_heartbeat_names() -> None:
    from app.main import _worker_lane_from_name

    assert _worker_lane_from_name("apify-worker-interactive") == "interactive"
    assert _worker_lane_from_name("apify-worker-interactive-host") == "interactive"
    assert _worker_lane_from_name("apify-worker-bulk-1") == "batch"
    assert _worker_lane_from_name("legacy-singleton") == "all"


def test_admin_startup_banner_never_prints_connection_strings() -> None:
    script = _read("scripts/start_admin.sh")

    assert "DATABASE_URL         = $DATABASE_URL\n" not in script
    assert "REDIS_URL            = $REDIS_URL\n" not in script
    assert "DATABASE_URL         = $DATABASE_URL_STATE" in script
    assert "REDIS_URL            = $REDIS_URL_STATE" in script
    assert "configured but differs from LOCAL_DATABASE_URL" in script

    result = subprocess.run(
        ["bash", "-n", str(ROOT / "scripts/start_admin.sh")],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr


def test_systemd_contract_survives_stale_env_and_emits_no_runtime_urls(tmp_path: Path) -> None:
    database_url = "postgresql://cloud_user:db-secret@db.internal:5432/vkpi"
    redis_url = "redis://:redis-secret@redis.internal:6379/0"
    stale_env = tmp_path / "stale-production.env"
    stale_env.write_text(
        "\n".join(
            (
                "APP_ROLE=all",
                "ENVIRONMENT=local",
                "DB_RUNTIME_BACKEND=sqlite",
                "LOCAL_RUNTIME_FORCE_STACK=1",
                "HOST=0.0.0.0",
                "PORT=9999",
                "WEB_CONCURRENCY=1",
                "ADMIN_DAEMON=1",
                "ENABLE_SCHEDULER=0",
                "ENABLE_BROWSER=1",
                "ENABLE_UPLOAD_CLEANUP=1",
                "RUNTIME_ENV_QUIET=0",
                "DATABASE_URL=postgresql://stale:stale@stale-db.invalid/stale",
                "REDIS_URL=redis://:stale@stale-redis.invalid/0",
            )
        )
        + "\n",
        encoding="utf-8",
    )
    fake_python = tmp_path / "capture-runtime-contract.sh"
    fake_python.write_text(
        "#!/bin/sh\n"
        "printf '%s\\n' \"$APP_ROLE|$ENVIRONMENT|$DB_RUNTIME_BACKEND|$BIND|$WORKERS|"
        "$ADMIN_DAEMON|$ENABLE_SCHEDULER|$ENABLE_BROWSER|$ENABLE_UPLOAD_CLEANUP\"\n",
        encoding="utf-8",
    )
    fake_python.chmod(0o700)
    env = os.environ.copy()
    env.update(
        {
            "VKPI_SYSTEMD_ADMIN_WEB_CONTRACT": "1",
            "PYTHON_BIN": str(fake_python),
            "ENV_FILE": str(stale_env),
            "DATABASE_URL": database_url,
            "REDIS_URL": redis_url,
            # Model stale values that can arrive from a legacy production env.
            "APP_ROLE": "all",
            "ENVIRONMENT": "local",
            "DB_RUNTIME_BACKEND": "sqlite",
            "LOCAL_RUNTIME_FORCE_STACK": "1",
            "HOST": "0.0.0.0",
            "PORT": "9999",
            "WEB_CONCURRENCY": "1",
            "ADMIN_DAEMON": "1",
            "ENABLE_SCHEDULER": "0",
            "ENABLE_BROWSER": "1",
            "ENABLE_UPLOAD_CLEANUP": "1",
            "RUNTIME_ENV_QUIET": "1",
        }
    )

    result = subprocess.run(
        ["bash", str(ROOT / "scripts/start_admin.sh")],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert "ENVIRONMENT          = production" in result.stderr
    assert "APP_ROLE             = admin-web" in result.stderr
    assert "BIND                 = 127.0.0.1:8001" in result.stderr
    assert "WORKERS              = 2" in result.stderr
    assert "DATABASE_URL         = configured" in result.stderr
    assert "REDIS_URL            = configured" in result.stderr
    assert result.stdout.strip() == "admin-web|production|postgres|127.0.0.1:8001|2|0|1|0|0"
    for sensitive in (
        database_url,
        redis_url,
        "db-secret",
        "redis-secret",
        "db.internal",
        "redis.internal",
        "postgresql://",
        "redis://",
    ):
        assert sensitive not in result.stderr
