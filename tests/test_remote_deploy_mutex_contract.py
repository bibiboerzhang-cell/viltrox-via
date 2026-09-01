from __future__ import annotations

import os
import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DEPLOY = ROOT / "scripts" / "ops" / "deploy_local_to_cloud.sh"
TIMER_INSTALLER = ROOT / "scripts" / "ops" / "install_vkpi_daily_timers.sh"


def _deploy() -> str:
    return DEPLOY.read_text(encoding="utf-8")


def _function(deploy: str, name: str, next_name: str) -> str:
    return deploy.split(f"{name}() {{", 1)[1].split(f"\n}}\n\n{next_name}() {{", 1)[0]


def test_remote_mutex_is_acquired_before_the_first_remote_state_read() -> None:
    deploy = _deploy()

    main = deploy.index("\nrun_predeploy_embedded_browser_gate\n")
    transport = deploy.index("\nsetup_deploy_ssh_transport\n", main)
    mutex = deploy.index("\nacquire_remote_deploy_lock\n", transport)
    first_remote_state_read = deploy.index("\ncapture_remote_sync_unit_state\n", mutex)

    assert main < transport < mutex < first_remote_state_read
    cleanup = deploy.split("cleanup_post_deploy_evidence() {", 1)[1].split(
        "\n}\ntrap cleanup_post_deploy_evidence EXIT", 1
    )[0]
    remote_evidence_cleanup = cleanup.index('ssh "${SSH_TARGET}" "rm -f --')
    mutex_release = cleanup.index("release_remote_deploy_lock")
    transport_release = cleanup.index("cleanup_deploy_ssh_transport")
    assert remote_evidence_cleanup < mutex_release < transport_release


def test_remote_mutex_uses_a_root_only_persistent_inode_and_nonblocking_flock() -> None:
    deploy = _deploy()
    acquire = _function(deploy, "acquire_remote_deploy_lock", "release_remote_deploy_lock")
    remote_program = acquire.replace('\\"', '"')

    assert 'REMOTE_DEPLOY_LOCK_DIR="/run/lock/vkpi-deploy"' in deploy
    assert (
        'REMOTE_DEPLOY_LOCK_FILE="${REMOTE_DEPLOY_LOCK_DIR}/deploy.lock"'
        in deploy
    )
    assert "sudo -n /bin/bash -c" in acquire
    assert "/usr/bin/mkdir -m 0700 -- /run/lock/vkpi-deploy" in acquire
    assert '[ ! -L /run/lock/vkpi-deploy ]' in acquire
    assert '"0:0:700"' in remote_program
    assert "umask 077" in acquire
    assert '[ ! -L /run/lock/vkpi-deploy/deploy.lock ]' in acquire
    assert '"0:0:600:1"' in remote_program

    descriptor = acquire.index("exec 9>>/run/lock/vkpi-deploy/deploy.lock")
    flock = acquire.index("/usr/bin/flock -n 9", descriptor)
    acknowledgement = acquire.index("vkpi-deploy-lock/v1 acquired", flock)
    hold_until_eof = acquire.index("IFS= read -r _ || true", acknowledgement)
    assert descriptor < flock < acknowledgement < hold_until_eof

    # Unlocking is done by closing the descriptor/session.  Removing the stable
    # remote inode could unlock a different deployment and is forbidden.
    assert "rm -f -- /run/lock/vkpi-deploy" not in deploy
    assert "rm -rf -- /run/lock/vkpi-deploy" not in deploy


def test_timer_installer_uses_the_same_remote_mutex_without_a_reverse_wait() -> None:
    deploy = _deploy()
    installer = TIMER_INSTALLER.read_text(encoding="utf-8")
    remote = installer.split("<<'REMOTE_TRANSACTION'", 1)[1].split(
        "REMOTE_TRANSACTION", 1
    )[0]

    shared_lock = "/run/lock/vkpi-deploy/deploy.lock"
    assert shared_lock in deploy
    assert shared_lock in remote
    assert "exec 9>>/run/lock/vkpi-deploy/deploy.lock" in deploy
    assert "exec 9>>/run/lock/vkpi-deploy/deploy.lock" in remote
    assert "/usr/bin/flock -n 9" in deploy
    assert "/usr/bin/flock -n 9" in remote

    shared_at = remote.index("exec 9>>/run/lock/vkpi-deploy/deploy.lock")
    private_at = remote.index(
        "exec 8>/run/lock/vkpi-systemd-install.lock", shared_at
    )
    assert shared_at < private_at
    assert "/run/lock/vkpi-systemd-install.lock" not in deploy


def test_mutex_cleanup_closes_the_control_fd_before_transport_cleanup() -> None:
    deploy = _deploy()
    release = deploy.split("release_remote_deploy_lock() {", 1)[1].split(
        "\n}\n\nEXPECTED_WORKER_COUNT=", 1
    )[0]

    close_control_fd = release.index("exec 9>&-")
    wait_for_holder = release.index('wait "${REMOTE_DEPLOY_LOCK_HOLDER_PID}"')
    clear_guard = release.index("unset VKPI_DEPLOY_REMOTE_LOCK_REQUIRED")
    local_cleanup = release.index("rm -f --")
    assert close_control_fd < wait_for_holder < clear_guard < local_cleanup
    assert "REMOTE_DEPLOY_LOCK_HELD=0" in release


def test_transport_wrapper_fails_closed_if_mutex_holder_has_ended(
    tmp_path: Path,
) -> None:
    unexpected_client = tmp_path / "unexpected-client-run"
    fake_client = tmp_path / "real-ssh"
    fake_client.write_text(
        "#!/bin/sh\n"
        'touch "$VKPI_TEST_CAPTURE"\n',
        encoding="utf-8",
    )
    fake_client.chmod(0o700)
    wrapper = tmp_path / "ssh"
    wrapper.symlink_to(DEPLOY)
    status_file = tmp_path / "deploy-lock.status"
    status_file.write_text("75\n", encoding="utf-8")
    env = {
        **os.environ,
        "VKPI_DEPLOY_SSH_WRAPPER_MODE": "1",
        "VKPI_DEPLOY_REAL_SSH": str(fake_client),
        "VKPI_DEPLOY_REAL_SCP": str(fake_client),
        "VKPI_DEPLOY_SSH_CONTROL_PATH": str(tmp_path / "master.sock"),
        "VKPI_DEPLOY_SSH_CONNECT_TIMEOUT_SECONDS": "10",
        "VKPI_DEPLOY_SSH_CONTROL_PERSIST_SECONDS": "3600",
        "VKPI_DEPLOY_SSH_FAIL_CLOSED_PROXY": "/usr/bin/false",
        "VKPI_DEPLOY_REMOTE_LOCK_REQUIRED": "1",
        "VKPI_DEPLOY_REMOTE_LOCK_HOLDER_PID": str(os.getpid()),
        "VKPI_DEPLOY_REMOTE_LOCK_STATUS_FILE": str(status_file),
        "VKPI_TEST_CAPTURE": str(unexpected_client),
    }

    completed = subprocess.run(
        [str(wrapper), "viltrox", "true"],
        check=False,
        text=True,
        capture_output=True,
        env=env,
        timeout=10,
    )

    assert completed.returncode == 75
    assert "mutex holder is no longer alive" in completed.stderr
    assert not unexpected_client.exists()


def test_mutex_contention_has_a_stable_fail_closed_result() -> None:
    deploy = _deploy()
    acquire = _function(deploy, "acquire_remote_deploy_lock", "release_remote_deploy_lock")

    assert "/usr/bin/flock -n 9 || exit 75" in acquire
    assert '[ "${holder_rc:-}" = "75" ]' in acquire
    assert (
        "Refusing deploy because another deployment already holds the production mutex."
        in acquire
    )
    assert "return 1" in acquire
