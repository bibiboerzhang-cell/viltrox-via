from __future__ import annotations

import json
import os
import shutil
import stat
import subprocess
import sys
import sysconfig
import tempfile
from pathlib import Path

import pytest

from scripts.ops.freeze_phase_runtime import (
    _prepare_nested_dependency_mirror,
    phase_a_runtime_environment,
)
from scripts.ops.strict_runtime_seatbelt import (
    SeatbeltError,
    phase_a_static_profile,
    phase_a_writable_parent,
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
    source_example = mirror / ".env.example"
    physical_example = source / ".env.example"
    nested_example = mirror / "nested" / ".env.example"
    uppercase_example = source / "case-variant" / ".ENV.EXAMPLE"
    env_directory_secret = mirror / "env-dirs" / ".env.production" / "token"
    example_directory_secret = mirror / "env-dirs" / ".env.example" / "token"
    runtime_secret = source / "runtime" / "state.json"
    ignored_secret = source / "private" / "service.key"
    keychain = user_home / "Library" / "Keychains" / "login.keychain-db"
    ssh_key = user_home / ".ssh" / "id_ed25519"
    aws_credentials = user_home / ".aws" / "credentials"
    for path, value in (
        (source_file, "original\n"),
        (source_env, "TOKEN=secret\n"),
        (source_example, "ANTHROPIC_API_KEY=replace-me\n"),
        (physical_example, "OPENAI_API_KEY=replace-me\n"),
        (nested_example, "NESTED_SECRET=blocked\n"),
        (uppercase_example, "UPPER_SECRET=blocked\n"),
        (env_directory_secret, "ENV_DIRECTORY_SECRET=blocked\n"),
        (example_directory_secret, "EXAMPLE_DIRECTORY_SECRET=blocked\n"),
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
source_file, source_env, runtime_secret, ignored_secret, source_example, physical_example, nested_example, uppercase_example, env_directory_secret, example_directory_secret = paths[:10]
venv, node_modules, fake_tool, keychain, ssh_key, aws_credentials, allowed = paths[10:]
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
    "nested_example_read_denied": nested_example,
    "uppercase_example_read_denied": uppercase_example,
    "env_directory_read_denied": env_directory_secret,
    "example_directory_read_denied": example_directory_secret,
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
result["manifest_example_env_read_allowed"] = (
    source_example.read_text() == "ANTHROPIC_API_KEY=replace-me\\n"
)
result["physical_example_env_read_allowed"] = (
    physical_example.read_text() == "OPENAI_API_KEY=replace-me\\n"
)

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
    protected_paths = (
        mirror, mirror / ".venv", mirror / "frontend/node_modules",
        Path(sys.executable), fake_tool, Path("/usr/bin/sandbox-exec"), user_home,
    )
    writable_parent = phase_a_writable_parent(protected_paths)
    with tempfile.TemporaryDirectory(
        prefix="vkpi-phase-a-writable.", dir=writable_parent
    ) as writable_raw:
        runtime_environment = phase_a_runtime_environment(Path(writable_raw))
        assert set(runtime_environment) == {
            "RUNTIME_ROOT", "RUNTIME_DATA", "RUNTIME_LOGS", "RUNTIME_VENDOR",
            "VKPI_RUNTIME_DATA_DIR",
        }
        for value in runtime_environment.values():
            runtime_path = Path(value)
            assert runtime_path.is_relative_to(Path(writable_raw))
            assert not runtime_path.is_symlink()
            assert runtime_path.stat().st_uid == os.geteuid()
            assert stat.S_IMODE(runtime_path.stat().st_mode) == 0o700
        source_example.unlink()
        source_example.symlink_to(source_env)
        with pytest.raises(SeatbeltError, match="manifest example"):
            phase_a_static_profile(
                source=mirror, venv=mirror / ".venv",
                node_modules=mirror / "frontend/node_modules",
                tool_paths=(Path(sys.executable), fake_tool),
                writable_root=Path(writable_raw), user_home=user_home,
            )
        source_example.unlink()
        _write(source_example, "ANTHROPIC_API_KEY=replace-me\n")
        hardlink = mirror / "manifest-example-hardlink"
        os.link(source_example, hardlink)
        with pytest.raises(SeatbeltError, match="manifest example"):
            phase_a_static_profile(
                source=mirror, venv=mirror / ".venv",
                node_modules=mirror / "frontend/node_modules",
                tool_paths=(Path(sys.executable), fake_tool),
                writable_root=Path(writable_raw), user_home=user_home,
            )
        hardlink.unlink()
        profile = phase_a_static_profile(
            source=mirror,
            venv=mirror / ".venv",
            node_modules=mirror / "frontend" / "node_modules",
            tool_paths=(Path(sys.executable), fake_tool, Path("/usr/bin/sandbox-exec")),
            writable_root=Path(writable_raw),
            user_home=user_home,
        )
        writable_alias = Path(f"{writable_raw}-alias")
        writable_alias.symlink_to(writable_raw, target_is_directory=True)
        try:
            with pytest.raises(SeatbeltError, match="writable root overlaps"):
                phase_a_static_profile(
                    source=mirror, venv=mirror / ".venv",
                    node_modules=mirror / "frontend/node_modules",
                    tool_paths=(Path(sys.executable), fake_tool),
                    writable_root=writable_alias, user_home=user_home,
                )
        finally:
            writable_alias.unlink()
        completed = subprocess.run(
            sandboxed(
                [
                    sys.executable,
                    "-I",
                    "-B",
                    str(probe),
                    *(str(path) for path in (
                        source_file, source_env, runtime_secret, ignored_secret,
                        source_example, physical_example, nested_example,
                        uppercase_example, env_directory_secret,
                        example_directory_secret,
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

    with pytest.raises(SeatbeltError, match="writable root overlaps"):
        phase_a_static_profile(
            source=mirror,
            venv=mirror / ".venv",
            node_modules=mirror / "frontend/node_modules",
            tool_paths=(Path(sys.executable), fake_tool),
            writable_root=snapshot,
            user_home=user_home,
        )

    assert completed.returncode == 0, completed.stderr
    assert all(json.loads(completed.stdout).values()), completed.stdout
    assert "deny network" not in profile
    assert source_file.read_text(encoding="utf-8") == "original\n"
    assert fake_tool.read_text(encoding="utf-8") == "controller-tool\n"
    assert not (venv / "candidate-write").exists()
    assert not (node_modules / "candidate-write").exists()

    safe_candidate = tmp_path / "safe-python-candidate"
    safe_ops = safe_candidate / "scripts/ops"
    safe_ops.mkdir(parents=True)
    (safe_candidate / "tests").mkdir()
    (safe_candidate / "backend/app").mkdir(parents=True)
    for name in (
        "safe_python.sh", "safe_python_router.py", "freeze_phase_runtime.py",
        "freeze_worktree_contract.py",
    ):
        shutil.copy2(Path("scripts/ops") / name, safe_ops / name)
    (safe_ops / "safe_python.sh").chmod(0o755)
    hostile_venv = tmp_path / "safe-python-venv"
    subprocess.run(
        [sys.executable, "-m", "venv", "--without-pip", str(hostile_venv)],
        check=True,
    )
    hostile_site = next((hostile_venv / "lib").glob("python*/site-packages"))
    dependency_runtime = tmp_path / "reviewed-dependency-runtime"
    dependency_runtime.mkdir()
    reviewed_site, _dependency_inventory, _dependency_proof = (
        _prepare_nested_dependency_mirror(
            Path(sysconfig.get_path("purelib")), dependency_runtime,
        )
    )
    hostile_site.rmdir()
    hostile_site.symlink_to(reviewed_site, target_is_directory=True)
    reviewed_site.chmod(0o700)
    hostile_scripts = hostile_site / "scripts/ops"
    hostile_scripts.mkdir(parents=True)
    (hostile_scripts.parent / "__init__.py").write_text("", encoding="utf-8")
    (hostile_scripts / "__init__.py").write_text("", encoding="utf-8")
    (hostile_scripts / "shadow_probe.py").write_text(
        "VALUE = 'VENV_SHADOW'\n", encoding="utf-8",
    )
    (safe_ops / "shadow_probe.py").write_text(
        "VALUE = 'CANDIDATE_PINNED'\n", encoding="utf-8",
    )
    pth_marker = tmp_path / "outer-pth-loaded"
    sitecustomize_marker = tmp_path / "outer-sitecustomize-loaded"
    target_marker = tmp_path / "safe-python-target-ran"
    (hostile_site / "hostile.pth").write_text(
        f"import pathlib; pathlib.Path({str(pth_marker)!r}).write_text('loaded')\n",
        encoding="utf-8",
    )
    (safe_candidate / "sitecustomize.py").write_text(
        f"from pathlib import Path\nPath({str(sitecustomize_marker)!r}).write_text('loaded')\n",
        encoding="utf-8",
    )
    reviewed_site.chmod(0o500)
    target = safe_candidate / "scripts/safe_target.py"
    target.parent.mkdir(exist_ok=True)
    target.write_text(
        "import sys\nfrom pathlib import Path\n"
        "from scripts.ops.shadow_probe import VALUE\n"
        "Path(sys.argv[1]).write_text(VALUE)\n",
        encoding="utf-8",
    )
    safe_run = subprocess.run(
        [str(safe_ops / "safe_python.sh"), str(target), str(target_marker)],
        cwd=safe_candidate,
        env={
            "HOME": str(tmp_path),
            "PATH": "/usr/bin:/bin",
            "PYTHONPATH": str(safe_candidate),
            "VKPI_SAFE_PYTHON_REAL": str(hostile_venv / "bin/python"),
        },
        capture_output=True,
        text=True,
        check=False,
    )
    assert safe_run.returncode == 0, safe_run.stderr
    assert target_marker.read_text(encoding="utf-8") == "CANDIDATE_PINNED"
    assert not pth_marker.exists()
    assert not sitecustomize_marker.exists()

    linked_target_dir = safe_candidate / "scripts/real-targets"
    linked_target_dir.mkdir()
    linked_target = linked_target_dir / "linked_target.py"
    linked_target.write_text("raise SystemExit('must not run')\n", encoding="utf-8")
    (safe_candidate / "scripts/linked-targets").symlink_to(linked_target_dir)
    symlink_run = subprocess.run(
        [
            str(safe_ops / "safe_python.sh"),
            str(safe_candidate / "scripts/linked-targets/linked_target.py"),
        ],
        cwd=safe_candidate,
        env={
            "HOME": str(tmp_path),
            "PATH": "/usr/bin:/bin",
            "VKPI_SAFE_PYTHON_REAL": str(hostile_venv / "bin/python"),
        },
        capture_output=True,
        text=True,
        check=False,
    )
    assert symlink_run.returncode != 0
    assert "untrusted script path" in symlink_run.stderr

    writable_target_dir = safe_candidate / "scripts/writable-targets"
    writable_target_dir.mkdir(mode=0o777)
    writable_target_dir.chmod(0o777)
    writable_target = writable_target_dir / "writable_target.py"
    writable_target.write_text("raise SystemExit('must not run')\n", encoding="utf-8")
    writable_run = subprocess.run(
        [str(safe_ops / "safe_python.sh"), str(writable_target)],
        cwd=safe_candidate,
        env={
            "HOME": str(tmp_path),
            "PATH": "/usr/bin:/bin",
            "VKPI_SAFE_PYTHON_REAL": str(hostile_venv / "bin/python"),
        },
        capture_output=True,
        text=True,
        check=False,
    )
    assert writable_run.returncode != 0
    assert "untrusted path component" in writable_run.stderr

    pytest_init = reviewed_site / "pytest/__init__.py"
    (reviewed_site / "pytest").chmod(0o700)
    pytest_init.chmod(0o600)
    pytest_init.write_bytes(pytest_init.read_bytes() + b"# ambient poison\n")
    pytest_init.chmod(0o400)
    (reviewed_site / "pytest").chmod(0o500)
    poisoned_marker = tmp_path / "poisoned-safe-python-target-ran"
    poisoned_run = subprocess.run(
        [str(safe_ops / "safe_python.sh"), str(target), str(poisoned_marker)],
        cwd=safe_candidate,
        env={
            "HOME": str(tmp_path),
            "PATH": "/usr/bin:/bin",
            "VKPI_SAFE_PYTHON_REAL": str(hostile_venv / "bin/python"),
        },
        capture_output=True,
        text=True,
        check=False,
    )
    assert poisoned_run.returncode != 0
    assert "differs from reviewed baseline" in poisoned_run.stderr
    assert not poisoned_marker.exists()

    train_source = Path("scripts/ops/train.sh").read_text(encoding="utf-8")
    assert 'PYTHON_BIN="${ROOT}/scripts/ops/safe_python.sh"' in train_source
    assert 'export VKPI_SAFE_PYTHON_REAL="${PHYSICAL_PYTHON_BIN}"' in train_source
    assert '"${PHYSICAL_PYTHON_BIN}" -' not in train_source
