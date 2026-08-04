from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import threading
import time
from argparse import Namespace
from pathlib import Path

import pytest

from scripts.ops.freeze_worktree_candidate import (
    FreezeError,
    _assert_frontend_dist_reproducible,
    _regular_tree_inventory,
    freeze_candidate,
    run_deploy_gate,
    verify_deploy_source,
    verify_manifest,
)


def test_frozen_verifier_support_modules_import_under_isolated_python(
    tmp_path: Path,
) -> None:
    bundle = tmp_path / "verifier"
    for relative in (
        "scripts/ops/deploy_gate_runtime.py",
        "scripts/ops/freeze_git_bridge.py",
        "scripts/ops/freeze_worktree_candidate.py",
        "scripts/ops/freeze_worktree_contract.py",
    ):
        target = bundle / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(Path(relative), target)

    result = subprocess.run(
        [
            sys.executable,
            "-I",
            "-B",
            str(bundle / "scripts/ops/freeze_worktree_candidate.py"),
            "--help",
        ],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert "verify-deploy-source" in result.stdout
    assert [row[1] for row in _regular_tree_inventory(bundle)] == [
        "scripts",
        "scripts/ops",
        "scripts/ops/deploy_gate_runtime.py",
        "scripts/ops/freeze_git_bridge.py",
        "scripts/ops/freeze_worktree_candidate.py",
        "scripts/ops/freeze_worktree_contract.py",
    ]


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _repo(tmp_path: Path) -> Path:
    root = tmp_path / "repo"
    root.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=root, check=True)
    _write(root / ".gitignore", "runtime/\nfrontend/node_modules/\n")
    _write(root / "backend" / "app.py", "VALUE = 1\n")
    _write(root / "frontend" / "package.json", '{"scripts":{"build":"true"}}\n')
    _write(
        root / "scripts" / "verify.sh",
        """#!/usr/bin/env bash
python3 - <<'PY'
import json
import os
import subprocess
import tempfile
from pathlib import Path
with tempfile.TemporaryDirectory() as raw:
    fixture = Path(raw)
    subprocess.run(["git", "init", "-q"], cwd=fixture, check=True)
    subprocess.run(
        ["git", "config", "user.email", "nested@example.invalid"],
        cwd=fixture,
        check=True,
    )
    subprocess.run(
        ["git", "config", "user.name", "Nested Fixture"],
        cwd=fixture,
        check=True,
    )
    subprocess.run(
        ["git", "-C", str(fixture), "config", "test.bridge", "isolated"],
        cwd=Path.cwd(),
        check=True,
    )
    (fixture / "README.md").write_text("nested\\n", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=fixture, check=True)
    subprocess.run(["git", "commit", "-qm", "nested"], cwd=fixture, check=True)
    nested_head = subprocess.check_output(
        ["git", "rev-parse", "HEAD"], cwd=fixture, text=True
    ).strip()
    nested_toplevel = subprocess.check_output(
        ["git", "rev-parse", "--show-toplevel"], cwd=fixture, text=True
    ).strip()
    nested_config = subprocess.check_output(
        ["git", "-C", str(fixture), "config", "--get", "test.bridge"],
        cwd=Path.cwd(),
        text=True,
    ).strip()
blocked_mutation = subprocess.run(
    ["git", "config", "core.worktree", "/tmp/forbidden"],
    cwd=Path.cwd(),
    capture_output=True,
    text=True,
    check=False,
)
blocked_symbolic_ref = subprocess.run(
    ["git", "symbolic-ref", "HEAD", "refs/heads/forbidden"],
    cwd=Path.cwd(),
    capture_output=True,
    text=True,
    check=False,
)
Path("verify-env.json").write_text(json.dumps({
    "GIT_DIR": os.environ.get("GIT_DIR"),
    "GIT_OPTIONAL_LOCKS": os.environ.get("GIT_OPTIONAL_LOCKS"),
    "GIT_WORK_TREE": os.environ.get("GIT_WORK_TREE"),
    "VKPI_FREEZE_GIT_BRIDGE": os.environ.get("VKPI_FREEZE_GIT_BRIDGE"),
    "VITE_APP_GIT_SHA": os.environ.get("VITE_APP_GIT_SHA"),
    "VITE_APP_GIT_BRANCH": os.environ.get("VITE_APP_GIT_BRANCH"),
    "VITE_APP_BUILD_TIME": os.environ.get("VITE_APP_BUILD_TIME"),
    "VITE_API_BASE": os.environ.get("VITE_API_BASE"),
    "VITE_BROWSER_ASSIST": os.environ.get("VITE_BROWSER_ASSIST"),
    "VITE_EXPERIMENTAL_NAV": os.environ.get("VITE_EXPERIMENTAL_NAV"),
    "VKPI_VERIFY_FRONTEND_OUT_DIR": os.environ.get(
        "VKPI_VERIFY_FRONTEND_OUT_DIR"
    ),
    "blocked_snapshot_mutation_rc": blocked_mutation.returncode,
    "blocked_symbolic_ref_rc": blocked_symbolic_ref.returncode,
    "git_head": subprocess.check_output(
        ["git", "rev-parse", "HEAD"], text=True
    ).strip(),
    "git_status": subprocess.check_output(
        ["git", "status", "--porcelain=v1", "--untracked-files=all"],
        text=True,
    ).strip(),
    "git_toplevel": subprocess.check_output(
        ["git", "rev-parse", "--show-toplevel"], text=True
    ).strip(),
    "nested_fixture_commit_ok": len(nested_head) == 40,
    "nested_fixture_config_isolated": nested_config == "isolated",
    "nested_fixture_toplevel_ok": (
        Path(nested_toplevel).resolve() == fixture.resolve()
    ),
}, sort_keys=True), encoding="utf-8")
PY
exit 0
""",
    )
    os.chmod(root / "scripts" / "verify.sh", 0o755)
    _write(root / ".env", "TOKEN=must-not-copy\n")
    os.chmod(root / ".env", 0o600)
    _write(root / "runtime" / "state.json", "{}\n")
    _write(root / "frontend" / "node_modules" / "ignored", "ignored\n")
    subprocess.run(["git", "add", "."], cwd=root, check=True)
    env = os.environ.copy()
    env.update(
        {
            "GIT_AUTHOR_EMAIL": "freeze@example.invalid",
            "GIT_AUTHOR_NAME": "Freeze Test",
            "GIT_COMMITTER_EMAIL": "freeze@example.invalid",
            "GIT_COMMITTER_NAME": "Freeze Test",
        }
    )
    subprocess.run(["git", "commit", "-qm", "fixture"], cwd=root, env=env, check=True)
    (root / ".venv").mkdir()
    _write(root / "backend" / "untracked.py", "VALUE = 2\n")
    return root


def _create_test_venv(root: Path) -> Path:
    subprocess.run(
        [sys.executable, "-m", "venv", "--without-pip", str(root / ".venv")],
        check=True,
    )
    _write(root / ".git" / "info" / "exclude", ".venv/\n")
    return root / ".venv" / "bin" / "python"


def _freeze_args(root: Path, output: Path) -> Namespace:
    return Namespace(
        repo=str(root),
        output=str(output),
        skip_archive=False,
        skip_build=True,
        skip_verify=True,
    )


def _install_fake_frontend_npm(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake_bin = tmp_path / "bin"
    fake_npm = fake_bin / "npm"
    _write(
        fake_npm,
        """#!/usr/bin/env python3
import json
import os
import sys
from pathlib import Path
out = Path(sys.argv[sys.argv.index("--outDir") + 1])
out.mkdir(parents=True, exist_ok=True)
sha = os.environ["VITE_APP_GIT_SHA"]
payload = {
    "version": "0.0.0-test",
    "gitSha": sha,
    "gitShortSha": sha[:8],
    "gitBranch": os.environ["VITE_APP_GIT_BRANCH"],
    "builtAt": os.environ["VITE_APP_BUILD_TIME"],
    "ambientVite": sorted(
        name for name in os.environ
        if name.startswith("VITE_") and name not in {
            "VITE_APP_BUILD_TIME", "VITE_APP_GIT_BRANCH", "VITE_APP_GIT_SHA"
        }
    ),
}
(out / "index.html").write_text("<html></html>\\n", encoding="utf-8")
(out / "build-info.json").write_text(json.dumps(payload, sort_keys=True), encoding="utf-8")
""",
    )
    os.chmod(fake_npm, 0o755)
    monkeypatch.setenv("PATH", str(fake_bin) + os.pathsep + os.environ["PATH"])


def _commit_fixture(root: Path, message: str) -> None:
    commit_env = os.environ.copy()
    commit_env.update(
        {
            "GIT_AUTHOR_EMAIL": "freeze@example.invalid",
            "GIT_AUTHOR_NAME": "Freeze Test",
            "GIT_COMMITTER_EMAIL": "freeze@example.invalid",
            "GIT_COMMITTER_NAME": "Freeze Test",
        }
    )
    subprocess.run(["git", "add", "scripts/verify.sh"], cwd=root, check=True)
    subprocess.run(
        ["git", "commit", "-qm", message],
        cwd=root,
        env=commit_env,
        check=True,
    )


def _built_deploy_gate_fixture(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> tuple[Path, Path, dict[str, object], Path]:
    root = _repo(tmp_path)
    (root / "backend" / "untracked.py").unlink()
    _write(
        root / "scripts" / "verify.sh",
        """#!/usr/bin/env bash
set -euo pipefail
mode="${VKPI_TEST_REBUILD_MODE:-match}"
if [[ "${mode}" == "missing" ]]; then
  exit 0
fi
"${PYTHON_BIN:?}" - "${mode}" "${VKPI_VERIFY_FRONTEND_OUT_DIR:?}" <<'PY'
import shutil
import sys
from pathlib import Path

mode = sys.argv[1]
destination = Path(sys.argv[2])
shutil.copytree(Path("frontend/dist"), destination)
if mode == "drift":
    with destination.joinpath("index.html").open("a", encoding="utf-8") as handle:
        handle.write("<!-- drift -->\\n")
PY
""",
    )
    os.chmod(root / "scripts" / "verify.sh", 0o755)
    _commit_fixture(root, "reproducible deploy gate fixture")
    venv_python = _create_test_venv(root)
    _install_fake_frontend_npm(tmp_path, monkeypatch)

    output = tmp_path / "candidate"
    args = _freeze_args(root, output)
    args.skip_archive = True
    args.skip_build = False
    payload = freeze_candidate(args)
    assert payload["build"]["executed"] is True
    return root, output, payload, venv_python


def _deploy_gate_args(
    root: Path, output: Path, payload: dict[str, object], venv_python: Path
) -> Namespace:
    source = payload["source"]
    assert isinstance(source, dict)
    return Namespace(
        manifest=str(output.with_suffix(".manifest.json")),
        snapshot=str(output),
        expected_head=str(source["head"]),
        expected_branch=str(source["branch"]),
        source=str(root),
        python=str(venv_python),
    )


def test_freeze_and_offline_verify_excludes_runtime_dependencies_and_env(
    tmp_path: Path,
) -> None:
    root = _repo(tmp_path)
    output = tmp_path / "candidate"
    payload = freeze_candidate(_freeze_args(root, output))

    assert (output / "backend" / "app.py").is_file()
    assert (output / "backend" / "untracked.py").is_file()
    assert not (output / ".env").exists()
    assert not (output / "runtime").exists()
    assert not (output / "frontend" / "node_modules").exists()
    assert not (output / "frontend" / "dist").exists()
    assert not (output / ".git").exists()
    identity = payload["build"]["identity"]
    assert (output / "BUILD_GIT_SHA").read_text(encoding="utf-8") == identity["git_sha"] + "\n"
    assert (output / "BUILD_GIT_BRANCH").read_text(encoding="utf-8") == identity["git_branch"] + "\n"
    assert (output / "BUILD_TIME").read_text(encoding="utf-8") == identity["build_time"] + "\n"
    assert identity["git_sha"] == payload["source"]["head"]
    assert identity["git_branch"] == payload["source"]["branch"]
    assert payload["source"]["worktree_dirty"] is True
    assert payload["safety"]["deployment_performed"] is False

    manifest = output.with_suffix(".manifest.json")
    result = verify_manifest(Namespace(manifest=str(manifest), snapshot=None))
    assert result["pass"] is True
    assert result["content_sha256"] == payload["candidate"]["content_sha256"]
    assert json.loads(manifest.read_text(encoding="utf-8"))["schema"].endswith("/v1")


def test_build_and_static_verify_share_exact_snapshot_identity(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _repo(tmp_path)
    (root / "backend" / "untracked.py").unlink()
    common_worktree_before = subprocess.run(
        ["git", "config", "--local", "--get", "core.worktree"],
        cwd=root,
        capture_output=True,
        text=True,
        check=False,
    )
    assert common_worktree_before.returncode == 1
    _install_fake_frontend_npm(tmp_path, monkeypatch)
    monkeypatch.setenv("VITE_API_BASE", "https://hostile.invalid/api")
    monkeypatch.setenv("VITE_BROWSER_ASSIST", "1")
    monkeypatch.setenv("VITE_EXPERIMENTAL_NAV", "1")
    monkeypatch.setenv(
        "VKPI_VERIFY_FRONTEND_OUT_DIR",
        str(tmp_path / "hostile-frontend-output"),
    )

    output = tmp_path / "candidate"
    args = _freeze_args(root, output)
    args.skip_archive = True
    args.skip_build = False
    args.skip_verify = False
    payload = freeze_candidate(args)
    identity = payload["build"]["identity"]

    build_info = json.loads(
        (output / "frontend" / "dist" / "build-info.json").read_text(
            encoding="utf-8"
        )
    )
    verify_env = json.loads(
        (output / "verify-env.json").read_text(encoding="utf-8")
    )
    assert build_info["gitSha"] == identity["git_sha"]
    assert build_info["gitShortSha"] == identity["git_sha"][:8]
    assert build_info["gitBranch"] == identity["git_branch"]
    assert build_info["builtAt"] == identity["build_time"]
    assert build_info["ambientVite"] == []
    assert verify_env == {
        "GIT_DIR": None,
        "GIT_OPTIONAL_LOCKS": None,
        "GIT_WORK_TREE": None,
        "VKPI_FREEZE_GIT_BRIDGE": "readonly-path-wrapper",
        "VITE_APP_BUILD_TIME": identity["build_time"],
        "VITE_APP_GIT_BRANCH": identity["git_branch"],
        "VITE_APP_GIT_SHA": identity["git_sha"],
        "VITE_API_BASE": None,
        "VITE_BROWSER_ASSIST": None,
        "VITE_EXPERIMENTAL_NAV": None,
        "VKPI_VERIFY_FRONTEND_OUT_DIR": None,
        "blocked_snapshot_mutation_rc": 126,
        "blocked_symbolic_ref_rc": 126,
        "git_head": identity["git_sha"],
        "git_status": "",
        "git_toplevel": str(root),
        "nested_fixture_commit_ok": True,
        "nested_fixture_config_isolated": True,
        "nested_fixture_toplevel_ok": True,
    }
    assert not (output / ".git").exists()
    assert not (output / ".git").is_symlink()
    common_worktree_after = subprocess.run(
        ["git", "config", "--local", "--get", "core.worktree"],
        cwd=root,
        capture_output=True,
        text=True,
        check=False,
    )
    assert common_worktree_after.returncode == 1
    assert payload["build"]["build_info"] == build_info
    assert payload["build"]["build_info_sha256"]


def test_frontend_reproducibility_inventory_rejects_any_drift(
    tmp_path: Path,
) -> None:
    candidate = tmp_path / "candidate-dist"
    rebuilt = tmp_path / "rebuilt-dist"
    for root in (candidate, rebuilt):
        _write(root / "index.html", '<script src="/assets/app-fixed.js"></script>\n')
        _write(root / "assets" / "app-fixed.js", "const build = 'fixed';\n")
        os.chmod(root / "index.html", 0o644)
        os.chmod(root / "assets" / "app-fixed.js", 0o644)

    _assert_frontend_dist_reproducible(candidate, rebuilt)

    _write(rebuilt / "assets" / "app-fixed.js", "const build = 'changed';\n")
    with pytest.raises(FreezeError, match="frontend reproducibility mismatch"):
        _assert_frontend_dist_reproducible(candidate, rebuilt)

    _write(rebuilt / "assets" / "app-fixed.js", "const build = 'fixed';\n")
    _write(rebuilt / "assets" / "extra.js", "extra\n")
    with pytest.raises(FreezeError, match="frontend reproducibility mismatch"):
        _assert_frontend_dist_reproducible(candidate, rebuilt)

    (rebuilt / "assets" / "extra.js").unlink()
    (rebuilt / "assets" / "unsafe.js").symlink_to("app-fixed.js")
    with pytest.raises(FreezeError, match="unsafe file"):
        _assert_frontend_dist_reproducible(candidate, rebuilt)


def test_built_deploy_gate_accepts_exact_frontend_rebuild(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    root, output, payload, venv_python = _built_deploy_gate_fixture(
        tmp_path, monkeypatch
    )
    monkeypatch.setenv("VKPI_TEST_REBUILD_MODE", "match")

    result = run_deploy_gate(
        _deploy_gate_args(root, output, payload, venv_python)
    )

    assert result["canonical_deploy_gate"] is True
    assert result["frontend_reproducible"] is True
    assert result["content_sha256"] == payload["candidate"]["content_sha256"]
    assert verify_manifest(
        Namespace(
            manifest=str(output.with_suffix(".manifest.json")),
            snapshot=str(output),
        )
    )["pass"] is True


@pytest.mark.parametrize(
    ("rebuild_mode", "error_pattern"),
    [
        ("drift", "frontend reproducibility mismatch"),
        ("missing", "frontend reproducibility output is unavailable"),
    ],
)
def test_built_deploy_gate_fails_closed_and_reverifies_candidate(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    rebuild_mode: str,
    error_pattern: str,
) -> None:
    root, output, payload, venv_python = _built_deploy_gate_fixture(
        tmp_path, monkeypatch
    )
    monkeypatch.setenv("VKPI_TEST_REBUILD_MODE", rebuild_mode)
    deploy_args = _deploy_gate_args(root, output, payload, venv_python)
    revalidation_calls: list[str] = []
    real_verify_deploy_source = verify_deploy_source

    def tracked_verify_deploy_source(args: Namespace) -> dict[str, object]:
        revalidation_calls.append(str(args.snapshot))
        return real_verify_deploy_source(args)

    monkeypatch.setattr(
        "scripts.ops.freeze_worktree_candidate.verify_deploy_source",
        tracked_verify_deploy_source,
    )

    with pytest.raises(FreezeError, match=error_pattern):
        run_deploy_gate(deploy_args)

    assert revalidation_calls == [str(output), str(output)]
    assert verify_manifest(
        Namespace(
            manifest=str(output.with_suffix(".manifest.json")),
            snapshot=str(output),
        )
    )["pass"] is True


def test_freeze_rechecks_source_after_static_verify(tmp_path: Path) -> None:
    root = _repo(tmp_path)
    (root / "backend" / "untracked.py").unlink()
    verify = root / "scripts" / "verify.sh"
    _write(
        verify,
        """#!/usr/bin/env bash
python3 - <<'PY'
import subprocess
from pathlib import Path
source = Path(
    subprocess.check_output(
        ["git", "rev-parse", "--show-toplevel"], text=True
    ).strip()
)
(source / "backend" / "app.py").write_text("VALUE = 999\\n", encoding="utf-8")
PY
""",
    )
    os.chmod(verify, 0o755)
    subprocess.run(["git", "add", "scripts/verify.sh"], cwd=root, check=True)
    commit_env = os.environ.copy()
    commit_env.update(
        {
            "GIT_AUTHOR_EMAIL": "freeze@example.invalid",
            "GIT_AUTHOR_NAME": "Freeze Test",
            "GIT_COMMITTER_EMAIL": "freeze@example.invalid",
            "GIT_COMMITTER_NAME": "Freeze Test",
        }
    )
    subprocess.run(
        ["git", "commit", "-qm", "mutating verifier fixture"],
        cwd=root,
        env=commit_env,
        check=True,
    )

    output = tmp_path / "candidate"
    args = _freeze_args(root, output)
    args.skip_archive = True
    args.skip_verify = False

    with pytest.raises(
        FreezeError, match="worktree drifted during candidate build and verification"
    ):
        freeze_candidate(args)
    assert not output.exists()


def test_offline_verify_detects_candidate_tamper(tmp_path: Path) -> None:
    root = _repo(tmp_path)
    output = tmp_path / "candidate"
    freeze_candidate(_freeze_args(root, output))
    _write(output / "backend" / "app.py", "VALUE = 999\n")

    with pytest.raises(FreezeError, match="digest mismatch"):
        verify_manifest(
            Namespace(manifest=str(output.with_suffix(".manifest.json")), snapshot=None)
        )


def test_deploy_source_binds_snapshot_and_both_git_identities(tmp_path: Path) -> None:
    root = _repo(tmp_path)
    (root / "backend" / "untracked.py").unlink()
    output = tmp_path / "candidate"
    payload = freeze_candidate(_freeze_args(root, output))
    manifest = output.with_suffix(".manifest.json")
    expected_head = str(payload["source"]["head"])
    expected_branch = str(payload["source"]["branch"])

    result = verify_deploy_source(
        Namespace(
            manifest=str(manifest),
            snapshot=str(output),
            expected_head=expected_head,
            expected_branch=expected_branch,
        )
    )
    assert result["pass"] is True
    assert result["source_git_sha"] == expected_head
    assert result["build_git_sha"] == expected_head

    with pytest.raises(FreezeError, match="source HEAD mismatch"):
        verify_deploy_source(
            Namespace(
                manifest=str(manifest),
                snapshot=str(output),
                expected_head="f" * 40,
                expected_branch=expected_branch,
            )
        )

    other_snapshot = tmp_path / "other-candidate"
    other_snapshot.mkdir()
    with pytest.raises(FreezeError, match="canonical path mismatch"):
        verify_deploy_source(
            Namespace(
                manifest=str(manifest),
                snapshot=str(other_snapshot),
                expected_head=expected_head,
                expected_branch=expected_branch,
            )
        )


def test_deploy_gate_detects_candidate_mutation_while_gate_is_running(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = _repo(tmp_path)
    (root / "backend" / "untracked.py").unlink()
    started = tmp_path / "gate-started"
    _write(
        root / "scripts" / "verify.sh",
        """#!/usr/bin/env bash
set -euo pipefail
printf 'started\n' >"${VKPI_TEST_GATE_STARTED:?}"
sleep 0.5
""",
    )
    os.chmod(root / "scripts" / "verify.sh", 0o755)
    commit_env = os.environ.copy()
    commit_env.update(
        {
            "GIT_AUTHOR_EMAIL": "freeze@example.invalid",
            "GIT_AUTHOR_NAME": "Freeze Test",
            "GIT_COMMITTER_EMAIL": "freeze@example.invalid",
            "GIT_COMMITTER_NAME": "Freeze Test",
        }
    )
    subprocess.run(["git", "add", "scripts/verify.sh"], cwd=root, check=True)
    subprocess.run(
        ["git", "commit", "-qm", "gate fixture"],
        cwd=root,
        env=commit_env,
        check=True,
    )
    venv_python = _create_test_venv(root)
    output = tmp_path / "candidate"
    payload = freeze_candidate(_freeze_args(root, output))
    monkeypatch.setenv("VKPI_TEST_GATE_STARTED", str(started))

    def mutate_candidate() -> None:
        deadline = time.monotonic() + 5
        while not started.exists() and time.monotonic() < deadline:
            time.sleep(0.01)
        assert started.exists()
        _write(output / "backend" / "app.py", "VALUE = 999\n")

    mutator = threading.Thread(target=mutate_candidate)
    mutator.start()
    try:
        with pytest.raises(FreezeError, match="digest mismatch"):
            run_deploy_gate(
                Namespace(
                    manifest=str(output.with_suffix(".manifest.json")),
                    snapshot=str(output),
                    expected_head=str(payload["source"]["head"]),
                    expected_branch=str(payload["source"]["branch"]),
                    source=str(root),
                    python=str(venv_python),
                )
            )
    finally:
        mutator.join(timeout=5)
    assert not (output / ".venv").exists()
    assert not (output / "frontend" / "node_modules").exists()
    assert not (output / "runtime").exists()


def test_deploy_gate_preserves_venv_invocation_and_isolates_runtime(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = _repo(tmp_path)
    (root / "backend" / "untracked.py").unlink()
    report = tmp_path / "deploy-gate-env.json"
    _write(
        root / "scripts" / "verify.sh",
        """#!/usr/bin/env bash
set -euo pipefail
"${PYTHON_BIN:?}" - <<'PY'
import json
import os
from pathlib import Path
runtime_root = Path(os.environ["RUNTIME_ROOT"])
Path(os.environ["VKPI_TEST_GATE_REPORT"]).write_text(json.dumps({
    "python_bin": os.environ.get("PYTHON_BIN"),
    "python_fallback": os.environ.get("PYTHON_BIN_FALLBACK"),
    "environment": os.environ.get("ENVIRONMENT"),
    "env_file": os.environ.get("ENV_FILE"),
    "local_env_file": os.environ.get("LOCAL_ENV_FILE"),
    "runtime_root": os.environ.get("RUNTIME_ROOT"),
    "runtime_mode": oct(runtime_root.stat().st_mode & 0o777),
    "runtime_uid": runtime_root.stat().st_uid,
    "runtime_gid": runtime_root.stat().st_gid,
    "effective_uid": os.geteuid(),
    "effective_gid": os.getegid(),
    "home": os.environ.get("HOME"),
    "tmpdir": os.environ.get("TMPDIR"),
    "xdg_cache_home": os.environ.get("XDG_CACHE_HOME"),
    "database_url": os.environ.get("DATABASE_URL"),
    "database_pool_url": os.environ.get("DATABASE_POOL_URL"),
    "redis_url": os.environ.get("REDIS_URL"),
    "jwt_secret": os.environ.get("JWT_SECRET"),
    "ops_health_token": os.environ.get("OPS_HEALTH_TOKEN"),
    "db_runtime_backend": os.environ.get("DB_RUNTIME_BACKEND"),
    "db_use_pgbouncer": os.environ.get("DB_USE_PGBOUNCER"),
    "local_runtime_force_stack": os.environ.get("LOCAL_RUNTIME_FORCE_STACK"),
    "keep_db_url": os.environ.get("RUNTIME_ENV_KEEP_DB_URL"),
    "keep_inherited_jwt": os.environ.get("RUNTIME_ENV_KEEP_INHERITED_JWT"),
    "runtime_quiet": os.environ.get("RUNTIME_ENV_QUIET"),
    "app_git_sha": os.environ.get("APP_GIT_SHA"),
    "vite_app_git_sha": os.environ.get("VITE_APP_GIT_SHA"),
    "vite_api_base": os.environ.get("VITE_API_BASE"),
    "vite_browser_assist": os.environ.get("VITE_BROWSER_ASSIST"),
    "vite_experimental_nav": os.environ.get("VITE_EXPERIMENTAL_NAV"),
}, sort_keys=True), encoding="utf-8")
runtime_root.joinpath("probe").write_text("isolated\\n")
PY
""",
    )
    os.chmod(root / "scripts" / "verify.sh", 0o755)
    commit_env = os.environ.copy()
    commit_env.update(
        {
            "GIT_AUTHOR_EMAIL": "freeze@example.invalid",
            "GIT_AUTHOR_NAME": "Freeze Test",
            "GIT_COMMITTER_EMAIL": "freeze@example.invalid",
            "GIT_COMMITTER_NAME": "Freeze Test",
        }
    )
    subprocess.run(["git", "add", "scripts/verify.sh"], cwd=root, check=True)
    subprocess.run(
        ["git", "commit", "-qm", "deploy gate env fixture"],
        cwd=root,
        env=commit_env,
        check=True,
    )
    venv_python = _create_test_venv(root)
    runtime_sentinel = root / "runtime" / "source-sentinel"
    _write(runtime_sentinel, "unchanged\n")

    output = tmp_path / "candidate"
    payload = freeze_candidate(_freeze_args(root, output))
    monkeypatch.setenv("VKPI_TEST_GATE_REPORT", str(report))
    monkeypatch.setenv("ENVIRONMENT", "production")
    monkeypatch.setenv("ENV_FILE", "/tmp/untrusted.env")
    monkeypatch.setenv("LOCAL_ENV_FILE", "/tmp/untrusted-local.env")
    monkeypatch.setenv("RUNTIME_ROOT", "/tmp/untrusted-runtime")
    monkeypatch.setenv("RUNTIME_ENV_KEEP_DB_URL", "1")
    monkeypatch.setenv("RUNTIME_ENV_KEEP_INHERITED_JWT", "1")
    monkeypatch.setenv("RUNTIME_ENV_QUIET", "0")
    monkeypatch.setenv("DATABASE_URL", "postgresql://hostile.invalid/wrong")
    monkeypatch.setenv("DATABASE_POOL_URL", "postgresql://hostile.invalid/pool")
    monkeypatch.setenv("REDIS_URL", "redis://hostile.invalid/0")
    monkeypatch.setenv("JWT_SECRET", "hostile-secret")
    monkeypatch.setenv("OPS_HEALTH_TOKEN", "hostile-health")
    monkeypatch.setenv("DB_RUNTIME_BACKEND", "sqlite")
    monkeypatch.setenv("DB_USE_PGBOUNCER", "1")
    monkeypatch.setenv("LOCAL_RUNTIME_FORCE_STACK", "0")
    monkeypatch.setenv("TMPDIR", str(output / "runtime"))
    monkeypatch.setenv("APP_GIT_SHA", "f" * 40)
    monkeypatch.setenv("VITE_APP_GIT_SHA", "e" * 40)
    monkeypatch.setenv("VITE_API_BASE", "https://hostile.invalid/api")
    monkeypatch.setenv("VITE_BROWSER_ASSIST", "1")
    monkeypatch.setenv("VITE_EXPERIMENTAL_NAV", "1")

    result = run_deploy_gate(
        Namespace(
            manifest=str(output.with_suffix(".manifest.json")),
            snapshot=str(output),
            expected_head=str(payload["source"]["head"]),
            expected_branch=str(payload["source"]["branch"]),
            source=str(root),
            python=str(venv_python),
        )
    )

    assert result["canonical_deploy_gate"] is True
    observed = json.loads(report.read_text(encoding="utf-8"))
    expected_head = str(payload["source"]["head"])
    assert observed == {
        "app_git_sha": expected_head,
        "database_pool_url": None,
        "database_url": None,
        "db_runtime_backend": "postgres",
        "db_use_pgbouncer": "0",
        "env_file": "",
        "environment": "local",
        "effective_gid": os.getegid(),
        "effective_uid": os.geteuid(),
        "home": str(Path(observed["runtime_root"]) / "home"),
        "jwt_secret": None,
        "keep_db_url": "0",
        "keep_inherited_jwt": "0",
        "local_runtime_force_stack": "1",
        "local_env_file": str(root / ".env"),
        "ops_health_token": None,
        "python_bin": str(venv_python),
        "python_fallback": str(venv_python),
        "redis_url": None,
        "runtime_quiet": "1",
        "runtime_mode": "0o700",
        "runtime_gid": os.getegid(),
        "runtime_root": observed["runtime_root"],
        "runtime_uid": os.geteuid(),
        "tmpdir": observed["runtime_root"],
        "vite_api_base": None,
        "vite_app_git_sha": expected_head,
        "vite_browser_assist": None,
        "vite_experimental_nav": None,
        "xdg_cache_home": str(Path(observed["runtime_root"]) / "cache"),
    }
    isolated_runtime = Path(observed["runtime_root"])
    assert not isolated_runtime.exists()
    assert root not in isolated_runtime.parents
    assert output not in isolated_runtime.parents
    assert runtime_sentinel.read_text(encoding="utf-8") == "unchanged\n"
    assert not (output / ".venv").exists()
    assert not (output / "frontend" / "node_modules").exists()
    assert not (output / "runtime").exists()


def test_deploy_gate_rejects_relative_python_path(tmp_path: Path) -> None:
    root = _repo(tmp_path)
    (root / "backend" / "untracked.py").unlink()
    _create_test_venv(root)
    output = tmp_path / "candidate"
    payload = freeze_candidate(_freeze_args(root, output))

    with pytest.raises(FreezeError, match="must be absolute"):
        run_deploy_gate(
            Namespace(
                manifest=str(output.with_suffix(".manifest.json")),
                snapshot=str(output),
                expected_head=str(payload["source"]["head"]),
                expected_branch=str(payload["source"]["branch"]),
                source=str(root),
                python="python3",
            )
        )


def test_deploy_gate_rejects_absolute_non_source_venv_python(
    tmp_path: Path,
) -> None:
    root = _repo(tmp_path)
    (root / "backend" / "untracked.py").unlink()
    _create_test_venv(root)
    output = tmp_path / "candidate"
    payload = freeze_candidate(_freeze_args(root, output))

    with pytest.raises(FreezeError, match="must equal source"):
        run_deploy_gate(
            Namespace(
                manifest=str(output.with_suffix(".manifest.json")),
                snapshot=str(output),
                expected_head=str(payload["source"]["head"]),
                expected_branch=str(payload["source"]["branch"]),
                source=str(root),
                python=str(Path(sys.executable).resolve()),
            )
        )


def test_deploy_source_fails_closed_on_special_file_inside_excluded_cache(
    tmp_path: Path,
) -> None:
    root = _repo(tmp_path)
    (root / "backend" / "untracked.py").unlink()
    output = tmp_path / "candidate"
    payload = freeze_candidate(_freeze_args(root, output))
    special = output / ".codegraph" / "daemon.sock"
    special.parent.mkdir()
    os.mkfifo(special)

    with pytest.raises(FreezeError, match="contains unsupported special file"):
        verify_deploy_source(
            Namespace(
                manifest=str(output.with_suffix(".manifest.json")),
                snapshot=str(output),
                expected_head=str(payload["source"]["head"]),
                expected_branch=str(payload["source"]["branch"]),
            )
        )


def test_deploy_source_rejects_candidate_frozen_from_dirty_worktree(
    tmp_path: Path,
) -> None:
    root = _repo(tmp_path)
    output = tmp_path / "candidate"
    payload = freeze_candidate(_freeze_args(root, output))
    assert payload["source"]["worktree_dirty"] is True

    with pytest.raises(FreezeError, match="frozen from a dirty worktree"):
        verify_deploy_source(
            Namespace(
                manifest=str(output.with_suffix(".manifest.json")),
                snapshot=str(output),
                expected_head=str(payload["source"]["head"]),
                expected_branch=str(payload["source"]["branch"]),
            )
        )


def test_high_confidence_secret_fails_closed(tmp_path: Path) -> None:
    root = _repo(tmp_path)
    private_key_marker = "-----BEGIN " + "OPENSSH PRIVATE KEY-----"
    _write(
        root / "backend" / "leaked.txt",
        private_key_marker + "\nnot-a-real-key\n",
    )
    with pytest.raises(FreezeError, match="secret detected"):
        freeze_candidate(_freeze_args(root, tmp_path / "candidate"))
