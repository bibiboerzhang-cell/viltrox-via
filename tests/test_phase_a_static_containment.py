from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

from scripts.ops.strict_runtime_seatbelt import (
    phase_a_static_profile,
    sandboxed,
)


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def test_phase_a_profile_denies_sources_secrets_dependencies_and_tools(
    tmp_path: Path,
) -> None:
    source = tmp_path / "source"
    mirror = tmp_path / "clean-mirror"
    snapshot = tmp_path / "snapshot"
    user_home = tmp_path / "user-home"
    for path in (source, mirror, snapshot, user_home):
        path.mkdir()
    venv = source / ".venv"
    node_modules = source / "frontend" / "node_modules"
    venv.mkdir()
    node_modules.mkdir(parents=True)
    (mirror / "frontend").mkdir()
    (mirror / ".venv").symlink_to(venv, target_is_directory=True)
    (mirror / "frontend" / "node_modules").symlink_to(
        node_modules, target_is_directory=True
    )

    source_file = source / "backend" / "tracked.py"
    source_env = source / ".env"
    runtime_secret = source / "runtime" / "state.json"
    ignored_secret = source / "private" / "service.key"
    keychain = user_home / "Library" / "Keychains" / "login.keychain-db"
    ssh_key = user_home / ".ssh" / "id_ed25519"
    aws_credentials = user_home / ".aws" / "credentials"
    for path, value in (
        (source_file, "original\n"),
        (source_env, "TOKEN=secret\n"),
        (runtime_secret, "runtime-secret\n"),
        (ignored_secret, "ignored-secret\n"),
        (keychain, "keychain-secret\n"),
        (ssh_key, "ssh-secret\n"),
        (aws_credentials, "aws-secret\n"),
    ):
        _write(path, value)

    fake_tool = tmp_path / "controller-tool"
    _write(fake_tool, "controller-tool\n")
    probe = tmp_path / "probe.py"
    probe.write_text(
        """import json, socket, subprocess, sys
from pathlib import Path

paths = [Path(value) for value in sys.argv[1:]]
source_file, source_env, runtime_secret, ignored_secret = paths[:4]
venv, node_modules, fake_tool, keychain, ssh_key, aws_credentials, allowed = paths[4:]
result = {}

def denied(name, operation):
    try:
        operation()
    except OSError:
        result[name] = True
    else:
        result[name] = False

for name, path in {
    "source_env_read_denied": source_env,
    "runtime_read_denied": runtime_secret,
    "ignored_secret_read_denied": ignored_secret,
    "keychain_read_denied": keychain,
    "ssh_read_denied": ssh_key,
    "aws_read_denied": aws_credentials,
}.items():
    denied(name, path.read_bytes)
denied("source_write_denied", lambda: source_file.write_text("changed"))
denied(
    "source_anchor_rename_denied",
    lambda: source_file.parents[1].rename(
        source_file.parents[1].with_name("renamed-source")
    ),
)
denied("venv_write_denied", lambda: (venv / "candidate-write").write_text("x"))
denied(
    "node_modules_write_denied",
    lambda: (node_modules / "candidate-write").write_text("x"),
)
denied("tool_write_denied", lambda: fake_tool.write_text("changed"))

allowed.write_text("ok")
result["ordinary_fixture_write_allowed"] = allowed.read_text() == "ok"
listener = socket.socket()
listener.bind(("127.0.0.1", 0))
listener.close()
result["loopback_bind_allowed"] = True
nested = subprocess.run(
    [
        "/usr/bin/sandbox-exec",
        "-p",
        "(version 1)\\n(allow default)\\n",
        "/usr/bin/true",
    ],
    check=False,
)
result["nested_seatbelt_segment_required"] = nested.returncode != 0
print(json.dumps(result, sort_keys=True))
""",
        encoding="utf-8",
    )
    profile = phase_a_static_profile(
        source=mirror,
        venv=mirror / ".venv",
        node_modules=mirror / "frontend" / "node_modules",
        tool_paths=(Path(sys.executable), fake_tool, Path("/usr/bin/sandbox-exec")),
        user_home=user_home,
    )
    with tempfile.TemporaryDirectory(
        prefix="vkpi-phase-a-writable.", dir="/private/var/tmp"
    ) as writable_raw:
        completed = subprocess.run(
            sandboxed(
                [
                    sys.executable,
                    "-I",
                    "-B",
                    str(probe),
                    *(str(path) for path in (
                        source_file, source_env, runtime_secret, ignored_secret,
                        venv, node_modules, fake_tool, keychain, ssh_key,
                        aws_credentials, Path(writable_raw) / "allowed",
                    )),
                ],
                profile,
            ),
            capture_output=True,
            text=True,
            check=False,
            timeout=30,
        )

    assert completed.returncode == 0, completed.stderr
    assert all(json.loads(completed.stdout).values()), completed.stdout
    assert "deny network" not in profile
    assert source_file.read_text(encoding="utf-8") == "original\n"
    assert fake_tool.read_text(encoding="utf-8") == "controller-tool\n"
    assert not (venv / "candidate-write").exists()
    assert not (node_modules / "candidate-write").exists()
