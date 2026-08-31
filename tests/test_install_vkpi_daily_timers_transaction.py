from __future__ import annotations

import os
from pathlib import Path
import subprocess

import pytest


ROOT = Path(__file__).resolve().parents[1]
INSTALLER = ROOT / "scripts/ops/install_vkpi_daily_timers.sh"
DAILY_UNIT = ROOT / "scripts/ops/systemd/vkpi-sync-daily.service"
DEADMAN_UNIT = ROOT / "scripts/ops/systemd/vkpi-sync-deadman.service"


def _installer() -> str:
    return INSTALLER.read_text(encoding="utf-8")


def test_remote_install_verifies_sealed_stage_before_mutation_and_activation() -> None:
    script = _installer()
    remote = script.split("<<'REMOTE_TRANSACTION'", 1)[1].split(
        "REMOTE_TRANSACTION", 1
    )[0]

    verify_at = remote.index('systemd-analyze verify "${verify_paths[@]}"')
    install_at = remote.index(
        'install -o root -g root -m 0644 -- "${stage}/${unit}" "${target}"'
    )
    activate_at = remote.index(
        "systemctl start --no-block vkpi-sync-deadman.timer "
        "vkpi-sync-daily.timer"
    )
    assert verify_at < install_at < activate_at
    assert '[[ "${#stage_entries[@]}" -eq "${#units[@]}" ]]' in remote
    assert '[[ "$(stat -c %h -- "${path}")" = "1" ]]' in remote
    assert '[[ "${stage_hashes_after}" = "${stage_hashes_before}" ]]' in remote


def test_remote_install_captures_and_rolls_back_files_state_and_timers() -> None:
    script = _installer()
    remote = script.split("<<'REMOTE_TRANSACTION'", 1)[1].split(
        "REMOTE_TRANSACTION", 1
    )[0]

    assert "declare -A unit_existed" in remote
    assert 'cp -a -- "${target}" "${backup_dir}/${unit}"' in remote
    assert 'systemctl is-enabled "${unit}"' in remote
    assert 'systemctl is-active "${unit}"' in remote
    assert "trap 'rollback_transaction \"$?\"' EXIT" in remote
    assert 'rm -f -- "${target}"' in remote
    assert 'cp -a -- "${backup_dir}/${unit}" "${target}"' in remote
    assert "systemctl daemon-reload || rollback_failed=1" in remote
    assert 'systemctl enable --runtime "${unit}"' in remote
    assert 'systemctl start "${unit}"' in remote
    trigger_block = remote.split("trigger_services=(", 1)[1].split(")", 1)[0]
    assert "vkpi-sync-daily.service" in trigger_block
    assert "vkpi-sync-deadman.service" in trigger_block
    assert "vkpi-qualified-kol-refresh.service" in trigger_block
    assert 'systemctl stop "${unit}"' in remote
    assert remote.index("systemctl start --no-block vkpi-sync-deadman.timer") < (
        remote.index("transaction_open=0")
    )
    assert 'backup_parent="/var/lib/vkpi/systemd-unit-backups"' in remote
    assert '[[ "$(stat -c %u -- "${env_file}")" = "0" ]]' in remote
    assert '[[ "$(stat -c %h -- "${env_file}")" = "1" ]]' in remote
    assert "V-KPI systemd rollback incomplete" in remote
    assert "exit 70" in remote
    assert "units committed but timer activation failed" in remote
    assert "exit 71" in remote
    assert remote.index("transaction_open=0") < remote.index(
        'for unit in "${activation_timers[@]}"'
    )


def test_log_permission_migration_is_bounded_and_rollbackable() -> None:
    script = _installer()

    assert "install -d -o viltrox -g viltrox -m 0750 /var/log/vkpi" in script
    assert "^sync_daily_[0-9]{8}\\.log$" in script
    assert (
        'find -P "${log_dir}" -mindepth 1 -maxdepth 1 -print0 '
        '> "${log_scan}"'
    ) in script
    assert '[[ "${file_nlink}" = "1" ]]' in script
    assert "log_uids=()" in script and "log_modes=()" in script
    assert 'chown --no-dereference "${log_uids[i]}:${log_gids[i]}"' in script
    assert 'chmod "${log_modes[i]}" "${log_paths[i]}"' in script
    assert "os.O_DIRECTORY | os.O_NOFOLLOW" in script
    assert "os.fchmod(fd, 0)" in script
    assert script.index("freeze_log_directory\nelse") < script.index(
        'find -P "${log_dir}"'
    )
    assert "${log_devs[i]}" in script and "${log_inos[i]}" in script
    assert "chown -R" not in script


def test_scheduled_sync_is_dropped_to_viltrox_and_deadman_does_not_double_notify() -> None:
    daily = DAILY_UNIT.read_text(encoding="utf-8")
    deadman = DEADMAN_UNIT.read_text(encoding="utf-8")
    installer = _installer()

    for directive in ("User=viltrox", "Group=viltrox", "UMask=0077"):
        assert directive in daily
    assert "mkdir -p /var/log/vkpi" not in daily
    assert "OnFailure=vkpi-sync-daily-alert@%n.service" in daily
    assert "OnFailure=" not in deadman
    assert "PID 1 reads EnvironmentFile as root" in installer
    assert "group/world writable" in installer
    assert 'runuser -u viltrox -- test -x "${remote_root}/.venv/bin/python"' in installer
    assert installer.count("EnvironmentFile=${REMOTE_ROOT}/.env") == 4
    assert installer.count("VKPI_SKIP_DOTENV=1") == 4


def test_qualified_timer_remains_default_disabled() -> None:
    script = _installer()

    assert 'ENABLE_QUALIFIED_KOL_TIMER="${ENABLE_QUALIFIED_KOL_TIMER:-0}"' in script
    assert "policy_disable_timers=(vkpi-qualified-kol-refresh.timer)" in script
    assert 'for unit in "${policy_disable_timers[@]}"' in script
    assert "require_timer_disabled vkpi-qualified-kol-refresh.timer" in script
    assert "systemctl start --no-block vkpi-qualified-kol-refresh.timer" in script
    assert "remained enabled despite default-disabled policy" in script


def test_install_fails_closed_while_any_trigger_service_is_running() -> None:
    remote = _installer().split("<<'REMOTE_TRANSACTION'", 1)[1].split(
        "REMOTE_TRANSACTION", 1
    )[0]

    assert "vkpi-sync-daily.service" in remote
    assert "vkpi-qualified-kol-refresh.service" in remote
    assert "unless ${unit} is safely inactive" in remote
    assert "unless ${unit} remains safely inactive" in remote
    assert "unless ${unit} is safely inactive" in remote
    assert remote.count("inactive|failed)") >= 3
    assert 'systemctl stop "${unit}"' in remote


@pytest.mark.parametrize(
    ("override", "expected"),
    [
        ({"REMOTE_TIMER": "ssh.service"}, "REMOTE_TIMER must be exactly"),
        ({"REMOTE_ROOT": "/opt/../etc"}, "REMOTE_ROOT must not contain traversal"),
        ({"SSH_TARGET": "-oProxyCommand=bad"}, "SSH_TARGET contains unsupported"),
        ({"SSH_TARGET": "-Fevil@host"}, "SSH_TARGET contains unsupported"),
        ({"QUALIFIED_KOL_LIMIT": "1;id"}, "must be a non-negative integer"),
    ],
)
def test_untrusted_environment_overrides_fail_before_remote_access(
    override: dict[str, str], expected: str
) -> None:
    env = os.environ.copy()
    env.update(override)
    result = subprocess.run(
        ["bash", str(INSTALLER), "remote"],
        cwd=ROOT,
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 64
    assert expected in result.stderr


def test_primary_staging_passes_real_local_paths_to_scp(
    tmp_path: Path,
) -> None:
    fake_bin = tmp_path / "bin"
    captured = tmp_path / "captured"
    fake_bin.mkdir()
    captured.mkdir()

    ssh = fake_bin / "ssh"
    ssh.write_text(
        """#!/usr/bin/env bash
set -euo pipefail
if [[ "$*" == *"mktemp -d /tmp/vkpi-systemd-install.XXXXXX"* ]]; then
  printf '%s\\n' /tmp/vkpi-systemd-install.TEST123
else
  cat >/dev/null
fi
""",
        encoding="utf-8",
    )
    scp = fake_bin / "scp"
    scp.write_text(
        """#!/usr/bin/env bash
set -euo pipefail
args=("$@")
for ((i=0; i<${#args[@]}-1; i++)); do
  case "${args[i]}" in
    -q|--) continue ;;
  esac
  cp -- "${args[i]}" "${CAPTURE_DIR}/"
done
""",
        encoding="utf-8",
    )
    ssh.chmod(0o755)
    scp.chmod(0o755)

    env = os.environ.copy()
    env["PATH"] = f"{fake_bin}:{env['PATH']}"
    env["CAPTURE_DIR"] = str(captured)
    result = subprocess.run(
        ["bash", str(INSTALLER), "remote"],
        cwd=ROOT,
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert {path.name for path in captured.iterdir()} == {
        "vkpi-sync-daily.service",
        "vkpi-sync-daily.timer",
        "vkpi-sync-daily-alert@.service",
        "vkpi-sync-deadman.service",
        "vkpi-sync-deadman.timer",
    }
    assert all(path.stat().st_size > 0 for path in captured.iterdir())
