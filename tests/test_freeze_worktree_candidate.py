from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from argparse import Namespace
from pathlib import Path

import pytest

from scripts.ops.freeze_worktree_candidate import (
    FreezeError,
    _assert_frontend_dist_reproducible,
    _remove_owned_phase_sandbox,
    _regular_tree_inventory,
    freeze_candidate,
    run_deploy_gate,
    verify_deploy_source,
    verify_manifest,
)
from scripts.ops.freeze_phase_runtime import PHASE_A_NESTED_SEATBELT_TEST_COUNT
from scripts.ops.strict_runtime_seatbelt import trusted_user_home


def test_phase_sandbox_cleanup_handles_readonly_tree_without_following_links(
    tmp_path: Path,
) -> None:
    root = Path(
        tempfile.mkdtemp(prefix="vkpi-phase-a-seatbelt.", dir="/tmp")
    ).resolve()
    outside = tmp_path / "outside.txt"
    outside.write_text("keep\n", encoding="utf-8")
    readonly = root / "readonly"
    readonly.mkdir()
    (readonly / "payload").write_text("fixture\n", encoding="utf-8")
    (readonly / "outside-link").symlink_to(outside)
    (readonly / "payload").chmod(0o400)
    readonly.chmod(0o500)

    _remove_owned_phase_sandbox(root)

    assert not root.exists()
    assert outside.read_text(encoding="utf-8") == "keep\n"


def test_frozen_verifier_support_modules_import_under_isolated_python(
    tmp_path: Path,
) -> None:
    bundle = tmp_path / "verifier"
    for relative in (
        "scripts/ops/candidate_physical_tree.py",
        "scripts/ops/controller_static_receipt.py",
        "scripts/ops/deploy_gate_runtime.py",
        "scripts/ops/deploy_runtime_admission.py",
        "scripts/ops/freeze_deploy_gate.py",
        "scripts/ops/freeze_git_bridge.py",
        "scripts/ops/freeze_phase_runtime.py",
        "scripts/ops/freeze_worktree_candidate.py",
        "scripts/ops/freeze_worktree_contract.py",
        "scripts/ops/phase_a_precheck_receipt.py",
        "scripts/ops/strict_runtime_seatbelt.py",
        "scripts/ops/trusted_git.py",
        "scripts/ops/trusted_npm_audit.py",
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
        "scripts/ops/candidate_physical_tree.py",
        "scripts/ops/controller_static_receipt.py",
        "scripts/ops/deploy_gate_runtime.py",
        "scripts/ops/deploy_runtime_admission.py",
        "scripts/ops/freeze_deploy_gate.py",
        "scripts/ops/freeze_git_bridge.py",
        "scripts/ops/freeze_phase_runtime.py",
        "scripts/ops/freeze_worktree_candidate.py",
        "scripts/ops/freeze_worktree_contract.py",
        "scripts/ops/phase_a_precheck_receipt.py",
        "scripts/ops/strict_runtime_seatbelt.py",
        "scripts/ops/trusted_git.py",
        "scripts/ops/trusted_npm_audit.py",
    ]


from tests.freeze_worktree_candidate_fixtures import (
    _attach_test_static_receipt,
    _built_deploy_gate_fixture,
    _create_test_venv,
    _deploy_gate_args,
    _freeze_args,
    _install_fake_frontend_npm,
    _repo,
    _write,
)

def test_freeze_and_offline_verify_excludes_runtime_dependencies_and_env(
    tmp_path: Path,
) -> None:
    root = _repo(tmp_path)
    output = tmp_path / "candidate"
    payload = freeze_candidate(_freeze_args(root, output))

    assert (output / "backend" / "app.py").is_file()
    assert (output / "backend" / "untracked.py").is_file()
    assert (output / ".env.example").read_text(encoding="utf-8") == (
        "JWT_SECRET=\nADMIN_PASSWORD=\n"
    )
    assert not (output / ".env").exists()
    assert not (output / ".env.production").exists()
    assert not (output / "nested" / ".env.example").exists()
    assert not (output / "uppercase" / ".ENV.EXAMPLE").exists()
    assert not (output / "env-dirs" / ".env.production").exists()
    assert not (output / "env-dirs" / ".env.example").exists()
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
    example = next(
        row for row in payload["candidate"]["files"]
        if row["path"] == ".env.example"
    )
    assert example["sha256"] == hashlib.sha256(
        (output / ".env.example").read_bytes()
    ).hexdigest()
    assert payload["exclusion_contract"]["secret_env"] == {
        "default": "exclude .env and .env.* at every depth",
        "included_exact_root": [".env.example"],
        "included_exact_root_case_sensitive": True,
    }

    manifest = output.with_suffix(".manifest.json")
    result = verify_manifest(Namespace(manifest=str(manifest), snapshot=None))
    assert result["pass"] is True
    assert result["content_sha256"] == payload["candidate"]["content_sha256"]
    assert json.loads(manifest.read_text(encoding="utf-8"))["schema"].endswith("/v1")


@pytest.mark.darwin_controller
def test_build_and_static_verify_share_exact_snapshot_identity(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _repo(tmp_path)
    (root / "backend" / "untracked.py").unlink()
    _create_test_venv(root)
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
    assert build_info["gitSha"] == identity["git_sha"]
    assert build_info["gitShortSha"] == identity["git_sha"][:8]
    assert build_info["gitBranch"] == identity["git_branch"]
    assert build_info["builtAt"] == identity["build_time"]
    assert build_info["ambientVite"] == []
    receipt = payload["verification"]["static_receipt"]["payload"]
    assert receipt["nested_seatbelt_tests"]["status"] == "passed"
    assert (
        receipt["nested_seatbelt_tests"]["expected_count"]
        == PHASE_A_NESTED_SEATBELT_TEST_COUNT
    )
    verify_env = receipt["canonical_receipt"]["candidate"]["fixture"]
    assert verify_env["VKPI_FREEZE_GIT_BRIDGE"] == "readonly-path-wrapper"
    assert verify_env["git_head"] == identity["git_sha"]
    assert verify_env["nested_fixture_commit_ok"] is True
    assert verify_env["nested_fixture_config_isolated"] is True
    assert verify_env["blocked_snapshot_mutation_rc"] == 126
    assert verify_env["blocked_symbolic_ref_rc"] == 126
    physical_cwd = Path(verify_env["physical_cwd"])
    assert physical_cwd.is_relative_to(trusted_user_home())
    assert not physical_cwd.is_relative_to(root)
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


@pytest.mark.darwin_controller
def test_static_verify_uses_external_mirror_when_output_is_under_runtime(
    tmp_path: Path,
) -> None:
    root = _repo(tmp_path)
    (root / "backend" / "untracked.py").unlink()
    _create_test_venv(root)
    output = root / "runtime" / "ops" / "candidate"
    args = _freeze_args(root, output)
    args.skip_archive = True
    args.skip_build = True
    args.skip_verify = False

    payload = freeze_candidate(args)

    assert payload["verification"]["static_receipt"]["payload"]["passed"] is True
    assert payload["verification"]["static_receipt"]["payload"][
        "verification_mirror"
    ]["status"] == "passed"
    assert (output / "scripts" / "verify.sh").is_file()


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


@pytest.mark.darwin_controller
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


@pytest.mark.darwin_controller
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
        "scripts.ops.freeze_deploy_gate.verify_deploy_source",
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


@pytest.mark.darwin_controller
def test_freeze_rechecks_source_after_static_verify(tmp_path: Path) -> None:
    root = _repo(tmp_path)
    (root / "backend" / "untracked.py").unlink()
    _create_test_venv(root)
    verify = root / "scripts" / "verify.sh"
    _write(
        verify,
        """#!/usr/bin/env bash
printf 'mutating source fixture\n'
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
    source_before = (root / "backend" / "app.py").read_bytes()

    with pytest.raises(FreezeError, match="command failed"):
        freeze_candidate(args)
    assert (root / "backend" / "app.py").read_bytes() == source_before
    assert not output.exists()
    assert output.with_suffix(".verify.log").is_file()


def test_offline_verify_detects_candidate_tamper(tmp_path: Path) -> None:
    root = _repo(tmp_path)
    output = tmp_path / "candidate"
    freeze_candidate(_freeze_args(root, output))
    _write(output / "backend" / "app.py", "VALUE = 999\n")

    with pytest.raises(FreezeError, match="digest mismatch"):
        verify_manifest(
            Namespace(manifest=str(output.with_suffix(".manifest.json")), snapshot=None)
        )


@pytest.mark.parametrize("injection", ["regular", "symlink"])
def test_offline_verify_rejects_excluded_physical_tree_injection(
    tmp_path: Path,
    injection: str,
) -> None:
    root = _repo(tmp_path)
    output = tmp_path / "candidate"
    freeze_candidate(_freeze_args(root, output))
    injected = output / "runtime" / "unbound.sh"
    injected.parent.mkdir()
    if injection == "regular":
        _write(injected, "#!/bin/sh\nexit 0\n")
    else:
        outside = tmp_path / "outside.sh"
        _write(outside, "#!/bin/sh\nexit 0\n")
        injected.symlink_to(outside)

    with pytest.raises(FreezeError, match="physical tree"):
        verify_manifest(
            Namespace(
                manifest=str(output.with_suffix(".manifest.json")),
                snapshot=str(output),
            )
        )


def test_offline_verify_rejects_manifested_file_hardlink_injection(
    tmp_path: Path,
) -> None:
    root = _repo(tmp_path)
    output = tmp_path / "candidate"
    freeze_candidate(_freeze_args(root, output))
    target = output / "backend" / "app.py"
    outside = tmp_path / "outside-app.py"
    outside.write_bytes(target.read_bytes())
    outside.chmod(target.stat().st_mode & 0o777)
    target.unlink()
    os.link(outside, target)

    with pytest.raises(FreezeError, match="hard-linked file: backend/app.py"):
        verify_manifest(
            Namespace(
                manifest=str(output.with_suffix(".manifest.json")),
                snapshot=str(output),
            )
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


@pytest.mark.darwin_controller
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
    _attach_test_static_receipt(root, output, payload, venv_python)
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
            run_deploy_gate(_deploy_gate_args(root, output, payload, venv_python))
    finally:
        mutator.join(timeout=5)
    assert not (output / ".venv").exists()
    assert not (output / "frontend" / "node_modules").exists()
    assert not (output / "runtime").exists()


@pytest.mark.darwin_controller
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
    "health_url": os.environ.get("VKPI_HEALTH_URL"),
    "health_env_file": os.environ.get("VKPI_HEALTH_ENV_FILE"),
    "base_url": os.environ.get("VKPI_LOCAL_BASE_URL"),
    "verify_json_out": os.environ.get("VKPI_VERIFY_JSON_OUT"),
    "acceptance_json_out": os.environ.get("VKPI_VERIFY_ACCEPTANCE_JSON_OUT"),
}, sort_keys=True), encoding="utf-8")
runtime_root.joinpath("probe").write_text("isolated\\n")
step_names = [
    "controller-bound canonical static receipt",
    "frontend isolated production build + chunk graph/bundle budget guards",
    "runtime trust (required)",
    "local release acceptance (all required GETs)",
    "browser console live extension-free release gate (not requested)",
    "post-restart runtime log leak canary (not requested)",
]
Path(os.environ["VKPI_VERIFY_JSON_OUT"]).write_text(json.dumps({
    "schema_version": "vkpi_canonical_gate_receipt_v1",
    "passed": True,
    "failed_steps": [],
    "candidate": {
        "release_head": os.environ["APP_GIT_SHA"],
        "git_head": os.environ["APP_GIT_SHA"],
        "branch": os.environ["APP_GIT_BRANCH"],
        "clean_worktree": True,
        "dirty_path_count": 0,
    },
    "verification": {
        "runtime": "verified",
        "acceptance": "verified",
        "browser_console": "not_requested",
        "runtime_log_canary": "not_requested",
    },
    "steps": [
        {"index": index, "name": name, "status": "passed", "exit_code": 0}
        for index, name in enumerate(step_names, 1)
    ],
    "strict_runtime_binding": {
        "nonce": os.environ.get("VKPI_STRICT_RUN_NONCE", ""),
        "ports": os.environ.get("VKPI_STRICT_RUNTIME_PORTS", ""),
        "candidate_sha256": os.environ["VKPI_STRICT_CANDIDATE_SHA256"],
        "static_receipt_sha256": os.environ["VKPI_STRICT_STATIC_RECEIPT_SHA256"],
        "manifest_sha256": os.environ["VKPI_STRICT_MANIFEST_SHA256"],
    },
}, sort_keys=True), encoding="utf-8")
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
    _attach_test_static_receipt(root, output, payload, venv_python)
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
    monkeypatch.setenv("VKPI_HEALTH_ENV_FILE", "/tmp/hostile-health.env")
    monkeypatch.setenv("DB_RUNTIME_BACKEND", "sqlite")
    monkeypatch.setenv("DB_USE_PGBOUNCER", "1")
    monkeypatch.setenv("LOCAL_RUNTIME_FORCE_STACK", "0")
    monkeypatch.setenv("TMPDIR", str(output / "runtime"))
    monkeypatch.setenv("APP_GIT_SHA", "f" * 40)
    monkeypatch.setenv("VITE_APP_GIT_SHA", "e" * 40)
    monkeypatch.setenv("VITE_API_BASE", "https://hostile.invalid/api")
    monkeypatch.setenv("VITE_BROWSER_ASSIST", "1")
    monkeypatch.setenv("VITE_EXPERIMENTAL_NAV", "1")
    monkeypatch.setenv("VKPI_HEALTH_URL", "http://127.0.0.1:8102/health")
    monkeypatch.setenv("VKPI_LOCAL_BASE_URL", "http://127.0.0.1:8102/")
    monkeypatch.setenv("VKPI_VERIFY_JSON_OUT", "/tmp/ambient-verify.json")
    monkeypatch.setenv("VKPI_VERIFY_ACCEPTANCE_JSON_OUT", "/tmp/ambient-acceptance.json")

    result = run_deploy_gate(_deploy_gate_args(root, output, payload, venv_python))

    assert result["canonical_deploy_gate"] is True
    observed = json.loads(report.read_text(encoding="utf-8"))
    expected_head = str(payload["source"]["head"])
    assert observed == {
        "app_git_sha": expected_head,
        "acceptance_json_out": str(Path(observed["runtime_root"]) / "receipts/acceptance.json"),
        "base_url": "http://127.0.0.1:18103/",
        "database_pool_url": None,
        "database_url": None,
        "db_runtime_backend": "postgres",
        "db_use_pgbouncer": "0",
        "env_file": "",
        "environment": "local",
        "effective_gid": os.getegid(),
        "effective_uid": os.geteuid(),
        "home": str(Path(observed["runtime_root"]) / "home"),
        "health_url": "http://127.0.0.1:18103/health",
        "health_env_file": str(output.parent / f".{output.name}-health.env"),
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
        "tmpdir": str(Path(observed["runtime_root"]) / "tmp"),
        "vite_api_base": None,
        "vite_app_git_sha": expected_head,
        "vite_browser_assist": None,
        "vite_experimental_nav": None,
        "verify_json_out": str(Path(observed["runtime_root"]) / "receipts/verify.json"),
        "xdg_cache_home": str(Path(observed["runtime_root"]) / "cache"),
        }
    isolated_runtime = Path(observed["runtime_root"])
    assert isolated_runtime.exists()
    assert result["candidate_browser_runtime_postgres"] == [{
        "root": str(isolated_runtime),
        "status": "controller_registry_cleanup_required",
        "destructive_cleanup_performed": False,
    }]
    shutil.rmtree(isolated_runtime)
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
    deploy_args = _deploy_gate_args(root, output, payload, root / ".venv/bin/python")
    deploy_args.python = "python3"

    with pytest.raises(FreezeError, match="must be absolute"):
        run_deploy_gate(deploy_args)


def test_deploy_gate_rejects_absolute_non_source_venv_python(
    tmp_path: Path,
) -> None:
    root = _repo(tmp_path)
    (root / "backend" / "untracked.py").unlink()
    _create_test_venv(root)
    output = tmp_path / "candidate"
    payload = freeze_candidate(_freeze_args(root, output))
    deploy_args = _deploy_gate_args(root, output, payload, root / ".venv/bin/python")
    deploy_args.python = str(Path(sys.executable).resolve())

    with pytest.raises(FreezeError, match="must equal source"):
        run_deploy_gate(deploy_args)


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
