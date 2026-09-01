from __future__ import annotations

import json
import os
import shutil
import subprocess
from argparse import Namespace
from pathlib import Path

import pytest

from scripts.ops import run_isolated_worktree_gate as isolated_gate
from scripts.ops import isolated_strict_runtime_gate as strict_gate


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _git(root: Path, *arguments: str) -> subprocess.CompletedProcess[bytes]:
    return subprocess.run(
        ["git", *arguments],
        cwd=root,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=True,
    )


def _dirty_repo(tmp_path: Path) -> Path:
    root = tmp_path / "source"
    root.mkdir()
    _git(root, "init", "-q")
    _write(
        root / ".gitignore",
        ".env\nignored.txt\nruntime/\n.venv/\nfrontend/node_modules/\n",
    )
    _write(root / "backend" / "tracked.py", "VALUE = 'tracked'\n")
    _write(root / "backend" / "delete-me.py", "VALUE = 'delete'\n")
    _write(root / "frontend" / "package.json", '{"scripts":{"build":"true"}}\n')
    _write(root / "scripts" / "verify.sh", "#!/usr/bin/env bash\nexit 0\n")
    os.chmod(root / "scripts" / "verify.sh", 0o755)
    _git(root, "add", "--all")
    environment = os.environ.copy()
    environment.update(
        {
            "GIT_AUTHOR_EMAIL": "fixture@example.invalid",
            "GIT_AUTHOR_NAME": "Fixture",
            "GIT_COMMITTER_EMAIL": "fixture@example.invalid",
            "GIT_COMMITTER_NAME": "Fixture",
        }
    )
    subprocess.run(
        ["git", "commit", "-qm", "fixture"], cwd=root, env=environment, check=True
    )
    _git(root, "remote", "add", "origin", "https://example.invalid/source.git")
    _write(root / ".env", "SECRET=must-not-copy\n")
    _write(root / "ignored.txt", "ignored\n")
    _write(root / "runtime" / "state.json", "{}\n")
    _write(root / "backend" / "untracked.py", "VALUE = 'untracked'\n")
    (root / "backend" / "delete-me.py").unlink()
    return root


def _args(source: Path, output: Path) -> Namespace:
    return Namespace(
        source=str(source),
        output=str(output),
        receipt="",
        skip_build=True,
        skip_verify=True,
        skip_archive=True,
    )


def test_phase_a_bridges_dirty_content_without_touching_source_state(
    tmp_path: Path,
) -> None:
    source = _dirty_repo(tmp_path)
    output = tmp_path / "artifacts" / "candidate"
    before = isolated_gate._capture_source_state(source)
    source_log_before = _git(source, "log", "--format=%H").stdout
    source_remote_before = _git(source, "remote", "-v").stdout

    result = isolated_gate.run_phase_a(_args(source, output))

    after = isolated_gate._capture_source_state(source)
    assert after == before
    assert _git(source, "log", "--format=%H").stdout == source_log_before
    assert _git(source, "remote", "-v").stdout == source_remote_before
    assert _git(source, "diff", "--cached", "--quiet").returncode == 0

    capsule = output.with_name("candidate-source-capsule")
    for snapshot in (capsule, output):
        assert (snapshot / "backend" / "tracked.py").is_file()
        assert (snapshot / "backend" / "untracked.py").is_file()
        assert not (snapshot / "backend" / "delete-me.py").exists()
        assert not (snapshot / ".env").exists()
        assert not (snapshot / "ignored.txt").exists()
        assert not (snapshot / "runtime").exists()
        assert not (snapshot / ".git").exists()

    capsule_manifest = json.loads(
        capsule.with_suffix(".manifest.json").read_text(encoding="utf-8")
    )
    candidate_manifest = json.loads(
        output.with_suffix(".manifest.json").read_text(encoding="utf-8")
    )
    assert capsule_manifest["source"]["worktree_dirty"] is True
    assert candidate_manifest["source"]["worktree_dirty"] is False
    assert (
        candidate_manifest["source"]["head"]
        == result["provenance_bridge"]["git_head"]
    )
    assert result["classification"] == (
        "clean_content_candidate_not_runtime_acceptance"
    )
    assert result["candidate"]["clean_deploy_source_contract_verified"] is True
    assert result["runtime_acceptance"] == {
        "attempted": False,
        "classification": "not_run_phase_a_only",
        "database_started": False,
        "provider_called": False,
        "redis_started": False,
        "web_started": False,
        "worker_started": False,
    }
    assert result["provenance_bridge"]["remote_count"] == 0
    assert result["provenance_bridge"]["hooks_empty"] is True
    assert result["provenance_bridge"]["status_clean"] is True
    assert result["provenance_bridge"]["mirror_mode"] == "0700"
    assert result["source_integrity"]["head_branch_index_status_unchanged"] is True
    assert result["safety"]["provider_network_contact"] == (
        "not_observed_not_os_enforced"
    )
    temporary_root = Path(str(result["temporary_cleanup"]["root"]))
    assert result["temporary_cleanup"]["status"] == "removed"
    assert not temporary_root.exists()

    receipt = output.with_name("candidate.provenance.json")
    assert json.loads(receipt.read_text(encoding="utf-8")) == result
    assert receipt.with_suffix(".json.sha256").is_file()


def test_phase_a_manifest_bytes_preserve_deleted_recorded_source_for_phase_b(
    tmp_path: Path,
) -> None:
    source = _dirty_repo(tmp_path)
    output = tmp_path / "artifacts" / "candidate"
    result = isolated_gate.run_phase_a(_args(source, output))
    manifest = output.with_suffix(output.suffix + ".manifest.json")
    original_bytes = manifest.read_bytes()
    original_payload = json.loads(original_bytes)
    recorded_source = Path(original_payload["source"]["repo"])

    assert recorded_source != source
    assert not recorded_source.exists()
    strict_root = tmp_path / "strict-root"
    strict_root.mkdir(mode=0o700)
    copied, copied_payload = strict_gate._copy_bound_manifest(
        candidate=output,
        root=strict_root,
        expected_sha256=str(result["candidate"]["manifest_sha256"]),
    )

    assert copied.read_bytes() == original_bytes
    assert copied_payload["source"]["repo"] == str(recorded_source)


def test_phase_b_capsule_identity_accepts_a_clean_phase_a_source(
    tmp_path: Path,
) -> None:
    source = _dirty_repo(tmp_path)
    (source / "backend" / "untracked.py").unlink()
    _write(source / "backend" / "delete-me.py", "VALUE = 'delete'\n")
    assert _git(source, "status", "--porcelain=v1").stdout == b""
    output = tmp_path / "artifacts" / "candidate"

    result = isolated_gate.run_phase_a(_args(source, output))
    identity = strict_gate._phase_capsule_identity(result)

    assert result["dirty_source_capsule"]["source_worktree_dirty"] is False
    assert identity["source_worktree_dirty"] is False


@pytest.mark.parametrize("injection", ["regular", "symlink"])
def test_clean_mirror_rejects_excluded_capsule_physical_injection(
    tmp_path: Path,
    injection: str,
) -> None:
    source = _dirty_repo(tmp_path)
    output = tmp_path / "artifacts" / "candidate"
    isolated_gate.run_phase_a(_args(source, output))
    capsule = output.with_name("candidate-source-capsule")
    capsule_manifest = capsule.with_suffix(".manifest.json")
    capsule_payload = json.loads(capsule_manifest.read_text(encoding="utf-8"))
    injected = capsule / "runtime" / "unbound.sh"
    injected.parent.mkdir()
    if injection == "regular":
        _write(injected, "#!/bin/sh\nexit 0\n")
    else:
        outside = tmp_path / "outside.sh"
        _write(outside, "#!/bin/sh\nexit 0\n")
        injected.symlink_to(outside)
    temporary_root = tmp_path / "strict-controller"
    temporary_root.mkdir()

    with pytest.raises(
        isolated_gate.FreezeError,
        match="physical tree",
    ):
        isolated_gate._prepare_clean_mirror(
            source=source,
            capsule=capsule,
            capsule_payload=capsule_payload,
            temporary_root=temporary_root,
        )


def test_phase_a_failure_cleans_only_owned_outputs_and_private_root(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = _dirty_repo(tmp_path)
    output = tmp_path / "artifacts" / "candidate"
    before = isolated_gate._capture_source_state(source)
    private_root = Path(
        isolated_gate.tempfile.mkdtemp(
            prefix=isolated_gate.TEMPORARY_PREFIX,
            dir=isolated_gate.TEMPORARY_PARENT,
        )
    )
    private_root.chmod(0o700)
    unrelated = tmp_path / "unrelated.txt"
    _write(unrelated, "keep\n")
    real_freeze = isolated_gate.freeze_candidate
    calls = 0

    def fail_second_freeze(arguments: Namespace) -> dict[str, object]:
        nonlocal calls
        calls += 1
        if calls == 2:
            raise isolated_gate.FreezeError("forced final freeze failure")
        return real_freeze(arguments)

    monkeypatch.setattr(isolated_gate, "_new_private_root", lambda: private_root)
    monkeypatch.setattr(isolated_gate, "freeze_candidate", fail_second_freeze)

    with pytest.raises(isolated_gate.FreezeError, match="forced final freeze failure"):
        isolated_gate.run_phase_a(_args(source, output))

    assert isolated_gate._capture_source_state(source) == before
    assert not private_root.exists()
    assert unrelated.read_text(encoding="utf-8") == "keep\n"
    assert not output.exists()
    assert not output.with_name("candidate-source-capsule").exists()
    assert not output.with_name("candidate.provenance.json").exists()


def test_phase_a_post_freeze_failure_cleans_controller_static_receipt(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = _dirty_repo(tmp_path)
    output = tmp_path / "artifacts" / "candidate"
    receipt = output.with_suffix(output.suffix + ".static-receipt.json")
    real_freeze = isolated_gate.freeze_candidate
    calls = 0

    def freeze_with_static_receipt(arguments: Namespace) -> dict[str, object]:
        nonlocal calls
        calls += 1
        result = real_freeze(arguments)
        if calls == 2:
            _write(receipt, '{"fixture": true}\n')
        return result

    def fail_after_freeze(_arguments: Namespace) -> dict[str, object]:
        raise isolated_gate.FreezeError("forced post-freeze verification failure")

    monkeypatch.setattr(isolated_gate, "freeze_candidate", freeze_with_static_receipt)
    monkeypatch.setattr(isolated_gate, "verify_deploy_source", fail_after_freeze)

    with pytest.raises(
        isolated_gate.FreezeError, match="forced post-freeze verification failure"
    ):
        isolated_gate.run_phase_a(_args(source, output))

    assert not receipt.exists()
    assert not output.exists()
    assert not output.with_name("candidate-source-capsule").exists()


def test_cleanup_refuses_unexpected_root(tmp_path: Path) -> None:
    unsafe = tmp_path / f"{isolated_gate.TEMPORARY_PREFIX}outside"
    unsafe.mkdir()
    _write(unsafe / "sentinel", "keep\n")

    with pytest.raises(
        isolated_gate.IsolatedWorktreeGateError,
        match="refusing to clean",
    ):
        isolated_gate._cleanup_private_root(unsafe, (unsafe.stat().st_dev, unsafe.stat().st_ino))

    assert (unsafe / "sentinel").read_text(encoding="utf-8") == "keep\n"


def test_cleanup_refuses_private_root_path_swap() -> None:
    root = isolated_gate._new_private_root()
    identity = (root.stat().st_dev, root.stat().st_ino)
    original = root.with_name(root.name + ".original")
    root.rename(original); root.mkdir(mode=0o700)
    _write(root / "replacement", "keep\n")
    try:
        with pytest.raises(isolated_gate.IsolatedWorktreeGateError, match="refusing to clean"):
            isolated_gate._cleanup_private_root(root, identity)
        assert (root / "replacement").read_text(encoding="utf-8") == "keep\n"
    finally:
        shutil.rmtree(root); shutil.rmtree(original)


def test_synthetic_identity_is_reproducible_for_the_same_dirty_content(
    tmp_path: Path,
) -> None:
    source = _dirty_repo(tmp_path)

    first = isolated_gate.run_phase_a(
        _args(source, tmp_path / "first" / "candidate")
    )
    second = isolated_gate.run_phase_a(
        _args(source, tmp_path / "second" / "candidate")
    )

    assert (
        first["provenance_bridge"]["git_head"]
        == second["provenance_bridge"]["git_head"]
    )
    assert (
        first["provenance_bridge"]["git_tree"]
        == second["provenance_bridge"]["git_tree"]
    )
    assert (
        first["provenance_bridge"]["capsule_content_bridge_sha256"]
        == second["provenance_bridge"]["capsule_content_bridge_sha256"]
    )


def test_cli_help_labels_phase_a_as_non_runtime() -> None:
    result = subprocess.run(
        [
            os.fspath(Path(isolated_gate.sys.executable)),
            "-I",
            "-B",
            os.fspath(Path(isolated_gate.__file__)),
            "--help",
        ],
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert (
        "output classification=clean_content_candidate_not_runtime_acceptance"
        in result.stdout
    )
    assert "runtime acceptance" in result.stdout
