from __future__ import annotations

import json
import os
import subprocess
from argparse import Namespace
from pathlib import Path

import pytest

from scripts.ops.freeze_worktree_candidate import (
    FreezeError,
    freeze_candidate,
    verify_deploy_source,
    verify_manifest,
)


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


def _freeze_args(root: Path, output: Path) -> Namespace:
    return Namespace(
        repo=str(root),
        output=str(output),
        skip_archive=False,
        skip_build=True,
        skip_verify=True,
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
}
(out / "index.html").write_text("<html></html>\\n", encoding="utf-8")
(out / "build-info.json").write_text(json.dumps(payload, sort_keys=True), encoding="utf-8")
""",
    )
    os.chmod(fake_npm, 0o755)
    monkeypatch.setenv("PATH", str(fake_bin) + os.pathsep + os.environ["PATH"])

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
    assert verify_env == {
        "GIT_DIR": None,
        "GIT_OPTIONAL_LOCKS": None,
        "GIT_WORK_TREE": None,
        "VKPI_FREEZE_GIT_BRIDGE": "readonly-path-wrapper",
        "VITE_APP_BUILD_TIME": identity["build_time"],
        "VITE_APP_GIT_BRANCH": identity["git_branch"],
        "VITE_APP_GIT_SHA": identity["git_sha"],
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
