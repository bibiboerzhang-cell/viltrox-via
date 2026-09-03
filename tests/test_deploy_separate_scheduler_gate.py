"""Dedicated scheduler unit install gate in deploy_local_to_cloud.sh (A1 W1).

Default OFF: without VKPI_DEPLOY_SEPARATE_SCHEDULER=1 the deploy must not touch
the remote host for the scheduler unit at all.  With the gate on, exactly one
reviewed ssh transaction installs/enables/restarts the unit.
"""
from __future__ import annotations

import os
import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEPLOY = ROOT / "scripts" / "ops" / "deploy_local_to_cloud.sh"
UNIT = ROOT / "deploy" / "systemd" / "vkpi-scheduler.service"
README = ROOT / "deploy" / "systemd" / "README.md"
START_MARKER = "# ── A1 W1 quick win: dedicated scheduler unit, default OFF"


def _gate_block() -> str:
    lines = DEPLOY.read_text(encoding="utf-8").split("\n")
    start = next(i for i, line in enumerate(lines) if line.startswith(START_MARKER))
    end = next(i for i in range(start, len(lines)) if lines[i] == "fi")
    return "\n".join(lines[start : end + 1])


def _run_block(env_gate: str | None) -> list[str]:
    block = _gate_block()
    harness = f"""
set -u
LOG="$1"
ssh() {{ printf 'SSH %s\\n' "$*" >>"$LOG"; return 0; }}
SSH_TARGET=viltrox-test
SERVICE_NAME=viltrox-2.0-test.service
REMOTE_CURRENT_DIR=/opt/viltrox-2.0/current
{block}
"""
    env = {k: v for k, v in os.environ.items() if k != "VKPI_DEPLOY_SEPARATE_SCHEDULER"}
    if env_gate is not None:
        env["VKPI_DEPLOY_SEPARATE_SCHEDULER"] = env_gate
    log = ROOT / "runtime" / "tmp" / f"scheduler-gate-{os.getpid()}-{env_gate or 'unset'}.log"
    log.parent.mkdir(parents=True, exist_ok=True)
    log.write_text("")
    try:
        completed = subprocess.run(
            ["bash", "-c", harness, "harness", str(log)],
            capture_output=True,
            text=True,
            env=env,
            check=False,
        )
        assert completed.returncode == 0, completed.stderr
        return [line for line in log.read_text().splitlines() if line]
    finally:
        log.unlink(missing_ok=True)


def test_deploy_script_still_parses() -> None:
    completed = subprocess.run(["bash", "-n", str(DEPLOY)], capture_output=True, text=True, check=False)
    assert completed.returncode == 0, completed.stderr


def test_gate_defaults_off_and_never_touches_the_host() -> None:
    assert _run_block(None) == []
    assert _run_block("0") == []
    assert _run_block("true") == []
    assert _run_block("yes") == []


def test_gate_on_runs_exactly_one_reviewed_install_transaction() -> None:
    calls = _run_block("1")
    assert len(calls) == 1
    command = calls[0]
    assert command.startswith("SSH viltrox-test set -eu;")
    assert "/opt/viltrox-2.0/current/deploy/systemd/vkpi-scheduler.service" in command
    assert "/etc/systemd/system/vkpi-scheduler.service" in command
    for fragment in (
        "systemd-analyze verify",
        "sudo install -o root -g root -m 0644",
        "cmp -s",
        "sudo mv -f --",
        "systemctl daemon-reload",
        "systemctl enable 'vkpi-scheduler.service'",
        "systemctl restart 'vkpi-scheduler.service'",
        "systemctl is-active --quiet 'vkpi-scheduler.service'",
    ):
        assert fragment in command, fragment
    assert "viltrox-2.0-scheduler.service" not in command


def test_unit_name_is_not_a_legacy_writer_and_template_is_reviewed() -> None:
    text = DEPLOY.read_text(encoding="utf-8")
    legacy = re.search(r"LEGACY_WRITER_UNITS=\((.*?)\)", text, re.S)
    assert legacy is not None
    assert "viltrox-2.0-scheduler.service" in legacy.group(1)
    assert "vkpi-scheduler.service" not in legacy.group(1)
    assert 'SEPARATE_SCHEDULER_SERVICE="vkpi-scheduler.service"' in text
    assert 'SEPARATE_SCHEDULER_UNIT_RELATIVE="deploy/systemd/vkpi-scheduler.service"' in text
    assert text.count(START_MARKER) == 1
    assert UNIT.is_file() and not UNIT.is_symlink()


def test_unit_template_contract() -> None:
    unit = UNIT.read_text(encoding="utf-8")
    exec_start = next(line for line in unit.splitlines() if line.startswith("ExecStart="))
    for assignment in (
        "APP_ROLE=worker",
        "ENVIRONMENT=production",
        "ENABLE_SCHEDULER=1",
        "ENABLE_LOCAL_ORCHESTRATOR=0",
        "ENABLE_BROWSER=0",
        "HOST=127.0.0.1",
        "PORT=8103",
        "WORKERS=1",
        "DB_USE_PGBOUNCER=0",
        "POSTGRES_POOL_MAX_SIZE=8",
    ):
        assert f" {assignment} " in exec_start, assignment
    assert exec_start.endswith("/opt/viltrox-2.0/current/scripts/start_scheduler.sh")
    assert "%i" not in unit
    assert "User=viltrox" in unit and "Group=viltrox" in unit
    assert "WorkingDirectory=/opt/viltrox-2.0/current" in unit
    assert "EnvironmentFile=/opt/viltrox-2.0/.env" in unit
    assert "ProtectSystem=strict" in unit and "NoNewPrivileges=true" in unit
    assert "InaccessiblePaths=/opt/viltrox-2.0/backups" in unit
    assert "WantedBy=multi-user.target" in unit
    pre = next(line for line in unit.splitlines() if line.startswith("ExecStartPre="))
    assert ".env.production" in pre and "ENABLE_SCHEDULER|PORT|HOST|BIND|WORKERS|APP_ROLE" in pre
    assert "vkpi-lane-overrides.env" not in unit


def test_readme_documents_the_gate_and_the_legacy_name_trap() -> None:
    readme = README.read_text(encoding="utf-8")
    assert "VKPI_DEPLOY_SEPARATE_SCHEDULER=1" in readme
    assert "vkpi-scheduler.service" in readme
    assert "LEGACY_WRITER_UNITS" in readme
