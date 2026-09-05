from __future__ import annotations

import json
import os
import socket
import stat
import subprocess
import tempfile
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
DEPLOY = ROOT / "scripts" / "ops" / "deploy_local_to_cloud.sh"


def _deploy() -> str:
    return DEPLOY.read_text(encoding="utf-8")


def test_deploy_bootstraps_one_private_transport_before_any_remote_read() -> None:
    deploy = _deploy()

    browser_gate = deploy.index("\nrun_predeploy_embedded_browser_gate\n")
    setup = deploy.index("\nsetup_deploy_ssh_transport\n", browser_gate)
    first_remote_read = deploy.index("\ncapture_remote_sync_unit_state\n", setup)
    assert browser_gate < setup < first_remote_read

    setup_body = deploy.split("setup_deploy_ssh_transport()", 1)[1].split(
        "cleanup_deploy_ssh_transport()", 1
    )[0]
    assert 'SSH_TRANSPORT_DIR="$(mktemp -d ' in setup_body
    assert 'chmod 700 "${SSH_TRANSPORT_DIR}"' in setup_body
    assert 'SSH_WRAPPER_SNAPSHOT="${SSH_TRANSPORT_DIR}/transport-wrapper"' in setup_body
    assert 'SSH_CONTROL_PATH="${SSH_TRANSPORT_DIR}/master.sock"' in setup_body
    assert "install -m 0500" in setup_body
    assert 'ln "${SSH_WRAPPER_SNAPSHOT}" "${SSH_TRANSPORT_DIR}/ssh"' in setup_body
    assert 'ln "${SSH_WRAPPER_SNAPSHOT}" "${SSH_TRANSPORT_DIR}/scp"' in setup_body
    assert "ln -s " not in setup_body
    assert '[ ! -f "${wrapper_path}" ] || [ -L "${wrapper_path}" ]' in setup_body
    assert '[ "${wrapper_mode}" != "500" ]' in setup_body
    assert '[ ! "${SSH_WRAPPER_SNAPSHOT}" -ef "${SSH_TRANSPORT_DIR}/ssh" ]' in setup_body
    assert '[ ! "${SSH_WRAPPER_SNAPSHOT}" -ef "${SSH_TRANSPORT_DIR}/scp" ]' in setup_body
    assert (
        'cmp -s "${PROJECT_ROOT}/scripts/ops/deploy_local_to_cloud.sh" '
        '"${SSH_WRAPPER_SNAPSHOT}"'
    ) in setup_body
    assert '[ "${SSH_INITIAL_CONNECT_ATTEMPTS}" -gt 3 ]' in setup_body
    assert "-o ControlMaster=yes" in setup_body
    assert "-o ConnectionAttempts=1" in setup_body
    assert '"${bootstrap_options[@]}" -N -f "${SSH_TARGET}"' in setup_body
    assert "-M -N" not in setup_body
    assert 'effective_control_master}" != "true"' in setup_body
    assert '"${SSH_TARGET}" true' in setup_body
    assert "-o ControlMaster=no" in setup_body
    assert '-o "ProxyCommand=${SSH_FAIL_CLOSED_PROXY}"' in setup_body
    assert "Every later command executes exactly" in setup_body
    assert "once through this same fail-closed transport" in setup_body
    bootstrap_exit = setup_body.index('-O exit "${SSH_TARGET}"')
    bootstrap_recheck = setup_body.index(
        '-O check "${SSH_TARGET}"', bootstrap_exit
    )
    bootstrap_unlink = setup_body.index(
        'rm -f -- "${SSH_CONTROL_PATH}"', bootstrap_recheck
    )
    assert bootstrap_exit < bootstrap_recheck < bootstrap_unlink
    assert "failed SSH bootstrap left a live ControlMaster" in setup_body
    assert 'PATH="${SSH_TRANSPORT_DIR}:${PATH}"' in setup_body
    assert 'RSYNC_RSH="${SSH_TRANSPORT_DIR}/ssh"' in setup_body
    assert "export VKPI_DEPLOY_SSH_WRAPPER_MODE=1" in setup_body


def test_bootstrap_effective_control_master_is_true_not_ask() -> None:
    effective = subprocess.run(
        [
            "/usr/bin/ssh",
            "-G",
            "-o",
            "ControlMaster=yes",
            "-N",
            "-f",
            "example.invalid",
        ],
        check=True,
        text=True,
        capture_output=True,
        timeout=10,
    ).stdout
    doubled = subprocess.run(
        [
            "/usr/bin/ssh",
            "-G",
            "-o",
            "ControlMaster=yes",
            "-M",
            "-N",
            "-f",
            "example.invalid",
        ],
        check=True,
        text=True,
        capture_output=True,
        timeout=10,
    ).stdout

    assert "controlmaster true\n" in effective
    assert "controlmaster ask\n" not in effective
    assert "controlmaster ask\n" in doubled


def test_immutable_transport_wrapper_survives_later_source_replacement(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("VKPI_SAFE_PYTHON_PROFILE", "github-actions-static-v1")
    mutable_source = tmp_path / "mutable-deploy.sh"
    mutable_source.write_bytes(DEPLOY.read_bytes())
    mutable_source.chmod(0o700)
    transport = tmp_path / "transport"
    transport.mkdir(mode=0o700)
    snapshot = transport / "transport-wrapper"
    ssh_wrapper = transport / "ssh"
    scp_wrapper = transport / "scp"

    subprocess.run(
        ["install", "-m", "0500", str(mutable_source), str(snapshot)],
        check=True,
        timeout=10,
    )
    os.link(snapshot, ssh_wrapper)
    os.link(snapshot, scp_wrapper)

    wrappers = (snapshot, ssh_wrapper, scp_wrapper)
    assert all(path.is_file() and not path.is_symlink() for path in wrappers)
    assert all(stat.S_IMODE(path.stat().st_mode) == 0o500 for path in wrappers)
    assert len({path.stat().st_ino for path in wrappers}) == 1
    frozen_bytes = snapshot.read_bytes()
    assert frozen_bytes == mutable_source.read_bytes()

    # Simulate rsync replacing the deploy worktree after transport setup.
    mutable_source.write_text("#!/bin/sh\nexit 97\n", encoding="utf-8")
    mutable_source.chmod(0o700)
    assert mutable_source.read_bytes() != frozen_bytes
    assert ssh_wrapper.read_bytes() == frozen_bytes

    capture = tmp_path / "immutable-wrapper.json"
    fake_client = tmp_path / "real-ssh"
    fake_client.write_text(
        "#!/usr/bin/env python3\n"
        "import json, os, sys\n"
        "from pathlib import Path\n"
        "Path(os.environ['VKPI_TEST_CAPTURE']).write_text("
        "json.dumps(sys.argv[1:]), encoding='utf-8')\n",
        encoding="utf-8",
    )
    fake_client.chmod(0o700)
    control_path = transport / "master.sock"
    env = {
        **os.environ,
        "VKPI_DEPLOY_SSH_WRAPPER_MODE": "1",
        "VKPI_DEPLOY_REAL_SSH": str(fake_client),
        "VKPI_DEPLOY_REAL_SCP": str(fake_client),
        "VKPI_DEPLOY_SSH_CONTROL_PATH": str(control_path),
        "VKPI_DEPLOY_SSH_CONNECT_TIMEOUT_SECONDS": "10",
        "VKPI_DEPLOY_SSH_CONTROL_PERSIST_SECONDS": "3600",
        "VKPI_DEPLOY_SSH_FAIL_CLOSED_PROXY": "/usr/bin/false",
        "VKPI_TEST_CAPTURE": str(capture),
    }
    # Exercise transport semantics without inheriting the unrelated CI-only guard.
    env.pop("VKPI_SAFE_PYTHON_PROFILE", None)

    completed = subprocess.run(
        [str(ssh_wrapper), "viltrox", "printf rollback-safe"],
        check=False,
        text=True,
        capture_output=True,
        env=env,
        timeout=10,
    )

    assert completed.returncode == 0, completed.stderr
    assert json.loads(capture.read_text(encoding="utf-8"))[-2:] == [
        "viltrox",
        "printf rollback-safe",
    ]


def test_transport_wrapper_covers_ssh_scp_python_children_and_rsync_without_retries() -> None:
    deploy = _deploy()
    wrapper = deploy.split("run_deploy_ssh_transport_wrapper()", 1)[1].split(
        'if [ "${VKPI_DEPLOY_SSH_WRAPPER_MODE:-0}" = "1" ]', 1
    )[0]

    for option in (
        "-o BatchMode=yes",
        "-o ControlMaster=no",
        '-o "ControlPersist=${control_persist}"',
        '-o "ControlPath=${control_path}"',
        '-o "ProxyCommand=${fail_closed_proxy}"',
        "-o ConnectionAttempts=1",
        '-o "ConnectTimeout=${connect_timeout}"',
        "-o ServerAliveInterval=15",
        "-o ServerAliveCountMax=3",
    ):
        assert option in wrapper
    assert 'exec "${real_binary}"' in wrapper
    assert "while " not in wrapper
    assert "for " not in wrapper

    # PATH reaches direct shell calls and Python subprocess(["ssh", ...]);
    # RSYNC_RSH reaches rsync's independently spawned remote shell.
    assert 'export PATH\n  RSYNC_RSH="${SSH_TRANSPORT_DIR}/ssh"' in deploy
    assert 'export RSYNC_RSH' in deploy


@pytest.mark.parametrize(
    ("tool", "tail"),
    (
        ("ssh", ["viltrox", "printf safe"]),
        ("scp", ["source.txt", "viltrox:/tmp/destination.txt"]),
    ),
)
def test_transport_wrapper_executes_the_requested_client_once(
    tmp_path: Path,
    tool: str,
    tail: list[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("VKPI_SAFE_PYTHON_PROFILE", "github-actions-static-v1")
    capture = tmp_path / f"{tool}.json"
    fake_client = tmp_path / f"real-{tool}"
    fake_client.write_text(
        "#!/usr/bin/env python3\n"
        "import json, os, sys\n"
        "from pathlib import Path\n"
        "Path(os.environ['VKPI_TEST_CAPTURE']).write_text("
        "json.dumps(sys.argv[1:]), encoding='utf-8')\n",
        encoding="utf-8",
    )
    fake_client.chmod(0o700)
    wrapper = tmp_path / tool
    wrapper.symlink_to(DEPLOY)
    control_path = tmp_path / "master.sock"
    env = {
        **os.environ,
        "VKPI_DEPLOY_SSH_WRAPPER_MODE": "1",
        "VKPI_DEPLOY_REAL_SSH": str(fake_client),
        "VKPI_DEPLOY_REAL_SCP": str(fake_client),
        "VKPI_DEPLOY_SSH_CONTROL_PATH": str(control_path),
        "VKPI_DEPLOY_SSH_CONNECT_TIMEOUT_SECONDS": "10",
        "VKPI_DEPLOY_SSH_CONTROL_PERSIST_SECONDS": "3600",
        "VKPI_DEPLOY_SSH_FAIL_CLOSED_PROXY": "/usr/bin/false",
        "VKPI_TEST_CAPTURE": str(capture),
    }
    env.pop("VKPI_SAFE_PYTHON_PROFILE", None)

    completed = subprocess.run(
        [str(wrapper), *tail],
        check=False,
        text=True,
        capture_output=True,
        env=env,
        timeout=10,
    )

    assert completed.returncode == 0, completed.stderr
    argv = json.loads(capture.read_text(encoding="utf-8"))
    assert argv == [
        "-o",
        "BatchMode=yes",
        "-o",
        "ControlMaster=no",
        "-o",
        "ControlPersist=3600",
        "-o",
        f"ControlPath={control_path}",
        "-o",
        "ProxyCommand=/usr/bin/false",
        "-o",
        "ConnectionAttempts=1",
        "-o",
        "ConnectTimeout=10",
        "-o",
        "ServerAliveInterval=15",
        "-o",
        "ServerAliveCountMax=3",
        *tail,
    ]


def test_transport_wrapper_fails_closed_before_client_on_invalid_socket(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("VKPI_SAFE_PYTHON_PROFILE", "github-actions-static-v1")
    capture = tmp_path / "unexpected-client-run"
    fake_client = tmp_path / "real-ssh"
    fake_client.write_text(
        "#!/bin/sh\n"
        'touch "$VKPI_TEST_CAPTURE"\n',
        encoding="utf-8",
    )
    fake_client.chmod(0o700)
    wrapper = tmp_path / "ssh"
    wrapper.symlink_to(DEPLOY)
    env = {
        **os.environ,
        "VKPI_DEPLOY_SSH_WRAPPER_MODE": "1",
        "VKPI_DEPLOY_REAL_SSH": str(fake_client),
        "VKPI_DEPLOY_REAL_SCP": str(fake_client),
        "VKPI_DEPLOY_SSH_CONTROL_PATH": "relative/socket",
        "VKPI_DEPLOY_SSH_CONNECT_TIMEOUT_SECONDS": "10",
        "VKPI_DEPLOY_SSH_CONTROL_PERSIST_SECONDS": "3600",
        "VKPI_TEST_CAPTURE": str(capture),
    }
    env.pop("VKPI_SAFE_PYTHON_PROFILE", None)

    completed = subprocess.run(
        [str(wrapper), "viltrox", "true"],
        check=False,
        text=True,
        capture_output=True,
        env=env,
        timeout=10,
    )

    assert completed.returncode == 64
    assert "configuration is invalid" in completed.stderr
    assert not capture.exists()


def test_transport_cleanup_closes_master_after_remote_failure_cleanup() -> None:
    deploy = _deploy()
    cleanup = deploy.split("cleanup_post_deploy_evidence()", 1)[1].split(
        "trap cleanup_post_deploy_evidence EXIT", 1
    )[0]

    remote_cleanup = cleanup.index(
        'ssh "${SSH_TARGET}" "rm -f -- \'${REMOTE_LOG_BASELINE}\''
    )
    transport_cleanup = cleanup.index("cleanup_deploy_ssh_transport")
    assert remote_cleanup < transport_cleanup

    transport = deploy.split("cleanup_deploy_ssh_transport()", 1)[1].split(
        'REMOTE_ROOT="${REMOTE_ROOT:-', 1
    )[0]
    close = transport.index('-O exit "${SSH_TARGET}"')
    check = transport.index('-O check "${SSH_TARGET}"')
    preserve = transport.index("preserve_transport=1")
    remove_socket = transport.index('"${SSH_TRANSPORT_DIR}/ssh"')
    remove_directory = transport.index('rmdir -- "${SSH_TRANSPORT_DIR}"')
    assert close < check < preserve < remove_socket < remove_directory
    assert (
        "SSH ControlMaster is still alive; preserving mode-0700 transport directory"
        in transport
    )


@pytest.mark.parametrize(
    ("check_rc", "expect_preserved"),
    (
        (0, True),
        (1, False),
    ),
)
def test_transport_cleanup_checks_after_failed_exit_before_removing_socket(
    tmp_path: Path,
    check_rc: int,
    expect_preserved: bool,
) -> None:
    deploy = _deploy()
    cleanup_function = (
        "cleanup_deploy_ssh_transport() {"
        + deploy.split("cleanup_deploy_ssh_transport() {", 1)[1].split(
            '\n}\n\nREMOTE_ROOT="${REMOTE_ROOT:-', 1
        )[0]
        + "\n}\n"
    )
    harness = tmp_path / "cleanup-harness.sh"
    harness.write_text(
        "#!/usr/bin/env bash\n"
        "set -u\n"
        f"{cleanup_function}\n"
        'SSH_TRANSPORT_READY=1\n'
        'SSH_TRANSPORT_DIR="${VKPI_TEST_TRANSPORT_DIR}"\n'
        'SSH_WRAPPER_SNAPSHOT="${SSH_TRANSPORT_DIR}/transport-wrapper"\n'
        'SSH_CONTROL_PATH="${SSH_TRANSPORT_DIR}/master.sock"\n'
        'SSH_REAL_BIN="${VKPI_TEST_REAL_SSH}"\n'
        'SSH_TARGET="viltrox"\n'
        'SSH_ORIGINAL_PATH="${PATH}"\n'
        'SSH_ORIGINAL_RSYNC_RSH_SET=0\n'
        'SSH_ORIGINAL_RSYNC_RSH=""\n'
        "cleanup_deploy_ssh_transport\n",
        encoding="utf-8",
    )
    harness.chmod(0o700)

    fake_ssh = tmp_path / "fake-ssh"
    fake_ssh.write_text(
        "#!/bin/sh\n"
        'printf "%s\\n" "$*" >> "$VKPI_TEST_SSH_LOG"\n'
        'case " $* " in\n'
        '  *" -O exit "*) exit 1 ;;\n'
        '  *" -O check "*) exit "$VKPI_TEST_CHECK_RC" ;;\n'
        "esac\n"
        "exit 2\n",
        encoding="utf-8",
    )
    fake_ssh.chmod(0o700)

    # macOS limits AF_UNIX paths to 104 bytes; use a deliberately short root.
    with tempfile.TemporaryDirectory(prefix="vkpi-ssh-", dir="/tmp") as short_root:
        transport = Path(short_root) / "t"
        transport.mkdir(mode=0o755)
        transport.chmod(0o755)
        snapshot = transport / "transport-wrapper"
        snapshot.write_text("#!/bin/sh\n", encoding="utf-8")
        snapshot.chmod(0o500)
        os.link(snapshot, transport / "ssh")
        os.link(snapshot, transport / "scp")
        control_path = transport / "master.sock"
        ssh_log = tmp_path / "ssh-calls.log"
        control_socket = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        control_socket.bind(str(control_path))
        env = {
            **os.environ,
            "VKPI_TEST_TRANSPORT_DIR": str(transport),
            "VKPI_TEST_REAL_SSH": str(fake_ssh),
            "VKPI_TEST_SSH_LOG": str(ssh_log),
            "VKPI_TEST_CHECK_RC": str(check_rc),
        }
        try:
            completed = subprocess.run(
                [str(harness)],
                check=False,
                text=True,
                capture_output=True,
                env=env,
                timeout=10,
            )
        finally:
            control_socket.close()

        assert completed.returncode == 0, completed.stderr
        calls = ssh_log.read_text(encoding="utf-8").splitlines()
        assert len(calls) == 2
        assert " -O exit " in f" {calls[0]} "
        assert " -O check " in f" {calls[1]} "
        assert "exit request failed" in completed.stderr
        if expect_preserved:
            assert transport.is_dir()
            assert stat.S_IMODE(transport.stat().st_mode) == 0o700
            assert control_path.exists()
            assert str(transport) in completed.stderr
            assert "preserving mode-0700 transport directory" in completed.stderr
        else:
            assert not transport.exists()
            assert not control_path.exists()
