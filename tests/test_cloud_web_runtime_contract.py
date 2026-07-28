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
    # 2026-07-22 多并发地基:池上限缺省仍 16,但可经 VKPI_WEB_POOL_MAX_SIZE(clamp 4..32)
    # 从 systemd argv/第二 EnvironmentFile 覆盖;stale .env 无法影响(只认 VKPI_ 前缀新名)。
    assert "WEB_POOL_MAX_EFFECTIVE=16" in production_contract
    assert 'export POSTGRES_POOL_MAX_SIZE="$WEB_POOL_MAX_EFFECTIVE"' in production_contract
    assert "export POSTGRES_POOL_TIMEOUT_SEC=30" in production_contract
    # 并发同款机制:缺省 2,VKPI_WEB_CONCURRENCY 覆盖(clamp 1..8)。
    assert "WEB_CONCURRENCY_EFFECTIVE=2" in production_contract
    assert 'export WEB_CONCURRENCY="$WEB_CONCURRENCY_EFFECTIVE"' in production_contract


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


def test_deploy_sync_unit_is_reviewed_verified_installed_and_rollback_captured() -> None:
    deploy = _read("scripts/ops/deploy_local_to_cloud.sh")

    assert (
        'REMOTE_SYNC_SERVICE_UNIT_RELATIVE="${REMOTE_SYNC_SERVICE_UNIT_RELATIVE:-'
        'scripts/ops/systemd/vkpi-sync-daily.service}"'
    ) in deploy
    assert (
        '[[ "${REMOTE_SYNC_SERVICE_UNIT_RELATIVE}" =~ '
        "^scripts/ops/systemd/[A-Za-z0-9@_.-]+\\.service$ ]]"
    ) in deploy
    assert (
        '"${REMOTE_SYNC_SERVICE_UNIT_RELATIVE##*/}" != "${SYNC_SERVICE}"'
    ) in deploy
    assert (
        '[ -L "${PROJECT_ROOT}/${REMOTE_SYNC_SERVICE_UNIT_RELATIVE}" ]'
    ) in deploy

    verify_lines = [
        line for line in deploy.splitlines() if "systemd-analyze verify" in line
    ]
    assert len(verify_lines) == 2
    assert all(
        "'${REMOTE_RELEASE_DIR}/${REMOTE_SYNC_SERVICE_UNIT_RELATIVE}'" in line
        for line in verify_lines
    )

    prepare = next(
        line
        for line in deploy.splitlines()
        if "atomic_release_layout.py' prepare" in line
    )
    assert "--unit-name '${SYNC_SERVICE}'" in prepare

    activate_at = deploy.index("atomic_release_layout.py' activate")
    sync_install_at = deploy.index(
        "sync_unit_source='${REMOTE_CURRENT_DIR}/${REMOTE_SYNC_SERVICE_UNIT_RELATIVE}'",
        activate_at,
    )
    reload_at = deploy.index("sudo systemctl daemon-reload", sync_install_at)
    restore_sync_at = deploy.rindex("restore_remote_sync_unit_state\n")
    assert activate_at < sync_install_at < reload_at < restore_sync_at
    install = deploy[sync_install_at:reload_at]
    for required in (
        "sync_unit_target='/etc/systemd/system/${SYNC_SERVICE}'",
        "sudo install -o root -g root -m 0644",
        "sudo mv -f --",
        "sudo stat -c '%u:%g:%a'",
        "'0:0:644'",
        "sudo cmp -s",
        "trap cleanup_sync_unit_tmp EXIT",
    ):
        assert required in install

    rollback = deploy.split("attempt_automatic_rollback()", 1)[1].split(
        "cleanup_post_deploy_evidence()", 1
    )[0]
    restore_at = rollback.index("atomic_release_layout.py' restore")
    sync_state_at = rollback.index("restore_remote_sync_unit_state")
    assert restore_at < sync_state_at


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


def test_deploy_rescue_requires_same_sha_frozen_anchor_and_explicit_confirmation() -> None:
    deploy = _read("scripts/ops/deploy_local_to_cloud.sh")

    for required in (
        "VKPI_RESCUE_ROLLBACK_CANDIDATE_DIR",
        "VKPI_RESCUE_ROLLBACK_CANDIDATE_MANIFEST",
        "VKPI_RESCUE_ROLLBACK_CONFIRM",
        "must be supplied together",
        '"RESCUE_ROLLBACK:${PREDEPLOY_APP_SHA}"',
        'ROLLBACK_ANCHOR_RELEASE_ID="rollback-anchor-${RELEASE_ID}-${PREDEPLOY_APP_SHA:0:12}"',
    ):
        assert required in deploy

    bind = deploy.split("bind_rescue_rollback_candidate()", 1)[1].split("}\n", 1)[0]
    assert "source.get(\"head\")" in bind
    assert 'head != sys.argv[2]' in bind
    assert "source.get(\"branch\")" in bind

    verify = deploy.split("verify_rescue_rollback_candidate()", 1)[1].split(
        "}\n", 1
    )[0]
    for required in (
        "freeze_worktree_candidate.py",
        "verify-deploy-source",
        '--manifest "${RESCUE_ROLLBACK_CANDIDATE_MANIFEST}"',
        '--snapshot "${RESCUE_ROLLBACK_CANDIDATE_DIR}"',
        '--expected-head "${PREDEPLOY_APP_SHA}"',
        '--expected-branch "${RESCUE_ROLLBACK_CANDIDATE_BRANCH}"',
    ):
        assert required in verify

    rescue = deploy.split(
        "# Rebuild the running SHA from a separately frozen clean worktree.",
        1,
    )[1].split(
        "# Capture the exact effective env/units",
        1,
    )[0]
    assert "Refusing to reuse an existing rescue rollback destination" in rescue
    assert (
        '-- "${RESCUE_ROLLBACK_CANDIDATE_DIR}/" '
        '"${SSH_TARGET}:${REMOTE_ROLLBACK_ANCHOR_DIR}/"'
    ) in rescue
    assert "--git-sha '${PREDEPLOY_APP_SHA}'" in rescue
    assert "--pending-migrations '' --compatibility-declaration ''" in rescue
    assert "--database-strategy '${ROLLBACK_ANCHOR_DATABASE_STRATEGY}'" in rescue
    assert rescue.count("verify_rescue_rollback_candidate") == 2
    assert "atomic_release_layout.py' verify-seal" in rescue

    prepare = next(
        line
        for line in deploy.splitlines()
        if "atomic_release_layout.py' prepare" in line
    )
    assert "${ROLLBACK_ANCHOR_PREPARE_OPTION}" in prepare
    assert (
        'ROLLBACK_ANCHOR_PREPARE_OPTION="--rollback-anchor-release-id '
        '${ROLLBACK_ANCHOR_RELEASE_ID}"'
    ) in deploy


def test_deploy_acceptance_reverifies_current_and_previous_seals() -> None:
    deploy = _read("scripts/ops/deploy_local_to_cloud.sh")

    restored = deploy.rindex("restore_remote_sync_unit_state\n")
    seal_pair = deploy.index(
        "post-deploy current pointer does not name the accepted release",
        restored,
    )
    accepted = deploy.rindex("DEPLOY_ACCEPTED=1")
    assert restored < seal_pair < accepted
    block = deploy[seal_pair:accepted]
    assert "post-deploy previous pointer escapes releases" in block
    assert "post-deploy previous pointer does not name the rescue rollback anchor" in block
    assert block.count("atomic_release_layout.py' verify-seal") == 2
    assert '--release-id \\"\\${current_id}\\"' in block
    assert '--release-id \\"\\${previous_id}\\"' in block


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
        *(f"vkpi-worker-bulk@{index}.service" for index in range(1, 16)),
    ]
    assert "EXPECTED_WORKER_COUNT=16" in deploy
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
    assert "VKPI_BROWSER_GATE_TOKEN_TTL_SECONDS must be an integer within [60, 900]" in deploy
    assert "BROWSER_GATE_CAPTURE_BUDGET_SECONDS" in deploy
    assert "is below the ${BROWSER_GATE_CAPTURE_BUDGET_SECONDS}s fail-closed browser capture budget" in deploy
    assert 'POST_DEPLOY_BROWSER_TOKEN="$(ssh' in deploy
    assert 'VKPI_BROWSER_GATE_TOKEN="${POST_DEPLOY_BROWSER_TOKEN}" node' in deploy
    assert '--ttl-seconds \'${BROWSER_GATE_TOKEN_TTL_SECONDS}\'' in deploy
    assert 'POST_DEPLOY_BROWSER_TOKEN=""' in deploy[capture_at : capture_at + 800]
    assert "VKPI_BROWSER_GATE_TOKEN and" not in deploy


def test_remote_release_acceptance_uses_the_production_nonroot_runtime_contract() -> None:
    deploy = _read("scripts/ops/deploy_local_to_cloud.sh")
    acceptance = deploy.split(
        "# Repeat the complete manifest-driven read-only API acceptance",
        1,
    )[1].split("# A caller may supply an explicit reviewed token", 1)[0]

    assert "sudo -n -u '${REMOTE_APP_USER}' -g '${REMOTE_APP_GROUP}' env -i" in acceptance
    for required in (
        "ENVIRONMENT=production",
        "V2_PRODUCTION_MODE=1",
        "APP_ROLE=admin-web",
        "DB_RUNTIME_BACKEND=postgres",
        "LOCAL_RUNTIME_FORCE_STACK=0",
        "LOCAL_ENV_FILE='${REMOTE_ROOT}/.env'",
        "RUNTIME_ROOT='${REMOTE_ROOT}/runtime'",
        "PYTHONDONTWRITEBYTECODE=1",
    ):
        assert required in acceptance
    assert "scripts/local_release_acceptance.py" in acceptance


def test_database_identity_probe_allows_pool_metadata_for_app_only_releases() -> None:
    deploy = _read("scripts/ops/deploy_local_to_cloud.sh")

    assert "'${REMOTE_ROOT}/.env' '${STAGING_DB_CLONE_MODE}'" in deploy
    assert "staging_clone_mode = sys.argv[2]" in deploy
    assert 'if staging_clone_mode not in {"0", "1"}:' in deploy
    assert (
        'if staging_clone_mode == "1" and '
        'runtime_values.get("DATABASE_POOL_URL", "").strip():'
    ) in deploy
    assert (
        'staging_clone_mode == "1"\n'
        '    and runtime_values.get("DB_USE_PGBOUNCER", "").strip().lower()'
    ) in deploy
    assert 'DATABASE_ENV_ASSERT_RUNTIME_POOL_FLAG=""' in deploy
    assert (
        'DATABASE_RELEASE_STRATEGY="reuse-active-clone"\n'
        '  DATABASE_ENV_ASSERT_RUNTIME_POOL_FLAG="--allow-runtime-pool"'
    ) in deploy
    assert deploy.count("${DATABASE_ENV_ASSERT_RUNTIME_POOL_FLAG}") == 3
    assert (
        "prove-active-source --root '${REMOTE_ROOT}' "
        "--expected-db '${STAGING_CLONE_DATABASE}' "
        "${DATABASE_ENV_ASSERT_RUNTIME_POOL_FLAG}"
    ) in deploy
    clone_helper = _read("scripts/ops/staging_db_clone.py")
    assert "assert_env.add_argument(\"--allow-runtime-pool\"" in clone_helper
    assert "prove.add_argument(\"--allow-runtime-pool\"" in clone_helper


def test_deploy_retains_failed_remote_acceptance_report_before_rollback() -> None:
    deploy = _read("scripts/ops/deploy_local_to_cloud.sh")
    acceptance = deploy.split(
        "# Repeat the complete manifest-driven read-only API acceptance",
        1,
    )[1].split("# A caller may supply an explicit reviewed token", 1)[0]

    run_at = acceptance.index("REMOTE_ACCEPTANCE_RC=0")
    copy_at = acceptance.index(
        'cat -- \'${REMOTE_ACCEPTANCE_REPORT}\'\" >\"${LOCAL_ACCEPTANCE_REPORT_TMP}\"'
    )
    publish_at = acceptance.index(
        'mv -- "${LOCAL_ACCEPTANCE_REPORT_TMP}" "${LOCAL_ACCEPTANCE_REPORT}"'
    )
    fail_at = acceptance.index('if [ "${REMOTE_ACCEPTANCE_RC}" -ne 0 ]; then')

    assert run_at < copy_at < publish_at < fail_at
    assert "rm -f -- '${REMOTE_ACCEPTANCE_REPORT}' && cd" in acceptance
    assert '|| REMOTE_ACCEPTANCE_RC=$?' in acceptance
    assert 'mktemp "${POST_DEPLOY_EVIDENCE_DIR}/.release-acceptance.XXXXXX"' in acceptance
    assert 'chmod 600 "${LOCAL_ACCEPTANCE_REPORT_TMP}"' in acceptance
    assert "report retained at ${LOCAL_ACCEPTANCE_REPORT}" in acceptance

    cleanup = deploy.split("cleanup_post_deploy_evidence()", 1)[1].split(
        "trap cleanup_post_deploy_evidence EXIT", 1
    )[0]
    assert 'rm -f -- "${LOCAL_ACCEPTANCE_REPORT_TMP}"' in cleanup


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
    assert 'BROWSER_GATE_TOKEN_TTL_SECONDS="${VKPI_BROWSER_GATE_TOKEN_TTL_SECONDS:-900}"' in deploy
    assert '--page-settle-ms "${BROWSER_GATE_PAGE_SETTLE_MS}"' in deploy
    assert '--page-timeout-ms "${BROWSER_GATE_PAGE_TIMEOUT_MS}"' in deploy
    assert "VKPI_BROWSER_GATE_EXTERNAL_MEDIA_403_ORIGINS must contain only exact external HTTPS origins" in deploy


def test_default_browser_token_ttl_covers_the_complete_fail_closed_capture_budget() -> None:
    deploy = _read("scripts/ops/deploy_local_to_cloud.sh")
    manifest = json.loads(_read("scripts/browser_gate_pages.json"))
    page_count = len(manifest["pages"])
    budget_ms = (
        15_000
        + 30_000
        + 5_000
        + 5_000 * (page_count + 1)
        + page_count * (30_000 + 1_000)
        + 30_000
        + 30_000
    )

    assert page_count == 21
    assert budget_ms == 871_000
    assert 900 >= (budget_ms + 999) // 1000
    assert "+  5000 * (BROWSER_GATE_PAGE_COUNT + 1)" in deploy
    assert "+  BROWSER_GATE_PAGE_COUNT * (BROWSER_GATE_PAGE_TIMEOUT_MS + BROWSER_GATE_PAGE_SETTLE_MS)" in deploy


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


def test_systemd_contract_honours_vkpi_web_concurrency_override(tmp_path: Path) -> None:
    """VKPI_WEB_CONCURRENCY/VKPI_WEB_POOL_MAX_SIZE 是评审过的提并发通道(2026-07-22)。

    stale .env 的 WEB_CONCURRENCY 依旧被合同压回;只有 VKPI_ 前缀新名生效,且有 clamp。
    """
    stale_env = tmp_path / "stale-production.env"
    stale_env.write_text("WEB_CONCURRENCY=1\nENVIRONMENT=local\n", encoding="utf-8")
    fake_python = tmp_path / "capture-web-concurrency.sh"
    fake_python.write_text(
        "#!/bin/sh\n"
        "printf '%s\\n' \"$WORKERS|$POSTGRES_POOL_MAX_SIZE\"\n",
        encoding="utf-8",
    )
    fake_python.chmod(0o700)
    env = os.environ.copy()
    env.update(
        {
            "VKPI_SYSTEMD_ADMIN_WEB_CONTRACT": "1",
            "PYTHON_BIN": str(fake_python),
            "ENV_FILE": str(stale_env),
            "DATABASE_URL": "postgresql://cloud_user:db-secret@db.internal:5432/vkpi",
            "REDIS_URL": "redis://:redis-secret@redis.internal:6379/0",
            "RUNTIME_ENV_QUIET": "1",
            "VKPI_WEB_CONCURRENCY": "6",
            "VKPI_WEB_POOL_MAX_SIZE": "12",
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
    assert result.stdout.strip() == "6|12"

    # clamp:99 进程 → 8;池 999 → 32
    env["VKPI_WEB_CONCURRENCY"] = "99"
    env["VKPI_WEB_POOL_MAX_SIZE"] = "999"
    result = subprocess.run(
        ["bash", str(ROOT / "scripts/start_admin.sh")],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "8|32"

    # 非数字/缺省 → 评审缺省 2|16
    env["VKPI_WEB_CONCURRENCY"] = "bogus"
    env.pop("VKPI_WEB_POOL_MAX_SIZE")
    result = subprocess.run(
        ["bash", str(ROOT / "scripts/start_admin.sh")],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "2|16"
