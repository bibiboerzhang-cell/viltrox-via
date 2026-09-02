from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
from argparse import Namespace
from pathlib import Path

import pytest

from scripts.ops.controller_static_receipt import OUTER_STATIC_PARTIAL_COVERAGE
from scripts.ops.freeze_worktree_candidate import (
    CANONICAL_STATIC_STEP_PLAN,
    _controller_static_receipt_payload,
    _trusted_file_identity,
    freeze_candidate,
)
from scripts.ops.freeze_worktree_contract import BuildIdentity
from scripts.ops.freeze_phase_runtime import (
    PHASE_A_DEPENDENCY_BASELINE,
    PHASE_A_NESTED_SEATBELT_TEST_COUNT,
    PHASE_A_NESTED_SEATBELT_TEST_FILES,
    PHASE_A_NESTED_SEATBELT_TESTS,
    PHASE_A_PYTEST_BOOTSTRAP,
)

def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _repo(tmp_path: Path) -> Path:
    root = tmp_path / "repo"
    root.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=root, check=True)
    _write(root / ".gitignore", "runtime/\nfrontend/node_modules/\n")
    _write(root / ".env.example", "JWT_SECRET=\nADMIN_PASSWORD=\n")
    _write(root / ".env.production", "TOKEN=excluded\n")
    _write(root / "nested" / ".env.example", "TOKEN=excluded\n")
    _write(root / "uppercase" / ".ENV.EXAMPLE", "TOKEN=excluded\n")
    _write(root / "env-dirs" / ".env.production" / "token.txt", "excluded\n")
    _write(root / "env-dirs" / ".env.example" / "token.txt", "excluded\n")
    _write(root / "backend" / "app.py", "VALUE = 1\n")
    _write(root / "frontend" / "package.json", '{"scripts":{"build":"true"}}\n')
    for relative, count in PHASE_A_NESTED_SEATBELT_TESTS:
        _write(
            root / relative,
            "\n\n".join(
                f"def test_phase_a_fixture_{index}():\n    assert True"
                for index in range(count)
            ) + "\n",
        )
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
evidence = {
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
    "physical_cwd": str(Path.cwd().resolve()),
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
}
step_names = [
    "release candidate worktree (required for deploy)",
    "frontend contracts are checked in and current",
    "frontend i18n dictionary + missing-English ratchet",
    "frontend production dependency security audit (moderate+)",
    "silent exception baseline",
    "repo hardening + reviewed warning ratchet",
    "alembic heads",
    "Python compile (in-memory; no bytecode writes)",
    "backend pytest",
    "frontend vitest",
    "frontend tsc --noEmit",
    "frontend isolated production build + chunk graph/bundle budget guards",
    "redline grep (viltrox_fit_score write)",
    "line guard >1000 (zero allowlist)",
    "runtime trust (not requested static-gate mode)",
    "local release acceptance (skipped in static-gate mode)",
    "browser console live extension-free release gate (not requested)",
    "post-restart runtime log leak canary (not requested)",
]
Path(os.environ["VKPI_VERIFY_JSON_OUT"]).write_text(json.dumps({
    "schema_version": "vkpi_canonical_gate_receipt_v1",
    "generated_at": "2026-09-02T01:52:23+00:00",
    "duration_seconds": 42,
    "passed": False,
    "static_coverage": {
        "status": "outer_static_partial_requires_nested_proof",
        "complete": False,
    },
    "failed_steps": [],
    "candidate": {
        "release_head": os.environ["APP_GIT_SHA"],
        "git_head": evidence["git_head"],
        "branch": os.environ["APP_GIT_BRANCH"],
        "clean_worktree": True,
        "dirty_path_count": 0,
        "fixture": evidence,
    },
    "verification": {
        "runtime": "not_requested",
        "acceptance": "not_requested",
        "browser_console": "not_requested",
        "runtime_log_canary": "not_requested",
    },
    "steps": [
        {"index": index, "name": name, "status": "passed", "exit_code": 0}
        for index, name in enumerate(step_names, 1)
    ],
}, sort_keys=True), encoding="utf-8")
PY
exit 78
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
    site_packages = next((root / ".venv" / "lib").glob("python*/site-packages"))
    _write(
        site_packages / "fixture-controller-deps.pth",
        str(Path(pytest.__file__).resolve().parents[1]) + "\n",
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
    fake_npm_root = tmp_path / "lib/node_modules/npm"
    fake_npm = fake_npm_root / "bin/npm-cli.js"
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
    _write(fake_npm.with_name("npx-cli.js"), "#!/bin/sh\nexit 0\n")
    os.chmod(fake_npm.with_name("npx-cli.js"), 0o755)
    _write(fake_npm_root / "lib/cli.js", "// reviewed fixture npm closure\n")
    _write(fake_npm_root / "package.json", '{"name":"npm"}\n')
    fake_bin.mkdir(parents=True, exist_ok=True)
    (fake_bin / "npm").symlink_to(fake_npm)
    fake_node = fake_bin / "node"
    _write(fake_node, "#!/bin/sh\nexec \"$@\"\n")
    os.chmod(fake_node, 0o755)
    monkeypatch.setenv("PATH", str(fake_bin) + os.pathsep + os.environ["PATH"])
    from scripts.ops import trusted_npm_audit
    monkeypatch.setattr(trusted_npm_audit, "TRUSTED_NPM_CANDIDATES", (fake_npm,))
    monkeypatch.setattr(trusted_npm_audit, "TRUSTED_NODE_CANDIDATES", (fake_node,))


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


def _attach_test_static_receipt(
    root: Path,
    output: Path,
    payload: dict[str, object],
    venv_python: Path,
) -> None:
    """Create an exact controller receipt for deploy-only fixture scripts."""

    from scripts.ops.trusted_git import (
        trusted_git_executable,
        trusted_python_executable,
    )
    from scripts.ops.trusted_npm_audit import (
        _trusted_node,
        _trusted_npm,
        _trusted_npx,
    )

    source = payload["source"]
    candidate = payload["candidate"]
    build = payload["build"]
    verification = payload["verification"]
    assert isinstance(source, dict)
    assert isinstance(candidate, dict)
    assert isinstance(build, dict)
    assert isinstance(verification, dict)
    raw_identity = build["identity"]
    assert isinstance(raw_identity, dict)
    identity = BuildIdentity(
        git_sha=str(raw_identity["git_sha"]),
        git_branch=str(raw_identity["git_branch"]),
        build_time=str(raw_identity["build_time"]),
    )
    canonical = {
        "schema_version": "vkpi_canonical_gate_receipt_v1",
        "passed": False,
        "static_coverage": OUTER_STATIC_PARTIAL_COVERAGE,
        "failed_steps": [],
        "candidate": {
            "release_head": identity.git_sha,
            "git_head": identity.git_sha,
            "branch": identity.git_branch,
            "clean_worktree": True,
            "dirty_path_count": 0,
        },
        "verification": {
            "runtime": "not_requested",
            "acceptance": "not_requested",
            "browser_console": "not_requested",
            "runtime_log_canary": "not_requested",
        },
        "steps": [
            {
                "index": index,
                "name": name,
                "status": "passed",
                "exit_code": 0,
            }
            for index, name in enumerate(CANONICAL_STATIC_STEP_PLAN, 1)
        ],
    }
    npm = _trusted_npm()
    verify_log = output.with_suffix(output.suffix + ".verify.log")
    verify_log.write_text("fixture canonical static gate passed\n", encoding="utf-8")
    verify_log.chmod(0o600)
    receipt_payload = _controller_static_receipt_payload(
        output=output,
        snapshot=output,
        candidate_digest=str(candidate["content_sha256"]),
        candidate_file_count=int(candidate["file_count"]),
        source_digest=str(source["content_sha256"]),
        source_file_count=int(source["file_count"]),
        source_status_sha256=str(source["status_sha256"]),
        source_dirty=bool(source["worktree_dirty"]),
        identity=identity,
        verify_log=verify_log,
        static_gate_run={
            "canonical_receipt": canonical,
            "verification_mirror": {
                "status": "passed",
                "copy_method": "independent_physical_files",
                "file_count": len(candidate["files"]),
                "candidate_digest_before": candidate["content_sha256"],
                "mirror_digest_before": candidate["content_sha256"],
                "candidate_digest_after": candidate["content_sha256"],
                "mirror_digest_after": candidate["content_sha256"],
            },
            "nested_seatbelt_tests": {
                "status": "passed",
                "execution_boundary": {
                    "candidate_source": "reviewed_clean_git_required_for_deploy",
                    "outer_seatbelt": False,
                    "same_uid_adversarial_source_resistance": False,
                    "reason": "darwin_nested_sandbox_incompatible",
                },
                "test_files": list(PHASE_A_NESTED_SEATBELT_TEST_FILES),
                "test_file_sha256": {
                    relative: hashlib.sha256((output / relative).read_bytes()).hexdigest()
                    for relative in PHASE_A_NESTED_SEATBELT_TEST_FILES
                },
                "file_counts": dict(PHASE_A_NESTED_SEATBELT_TESTS),
                "command": [
                    str(venv_python), "-I", "-S", "-B", "-c",
                    "<controller-bootstrap>", "<controller-dependency-mirror>",
                    "<verification-snapshot>",
                    "-c", "/dev/null", "--rootdir", "<verification-snapshot>",
                    "-o", "junit_family=xunit1", "--import-mode=importlib",
                    "--noconftest",
                    "--disable-plugin-autoload", "-p", "no:cacheprovider",
                    "-q", "--junitxml", "<controller-bound-junit>",
                    *PHASE_A_NESTED_SEATBELT_TEST_FILES,
                ],
                "exit_code": 0,
                "collected_count": PHASE_A_NESTED_SEATBELT_TEST_COUNT,
                "passed_count": PHASE_A_NESTED_SEATBELT_TEST_COUNT,
                "expected_count": PHASE_A_NESTED_SEATBELT_TEST_COUNT,
                "bootstrap_sha256": hashlib.sha256(
                    PHASE_A_PYTEST_BOOTSTRAP.encode("utf-8")
                ).hexdigest(),
                "junit_xml_sha256": "a" * 64,
                "junit_testcase_count": PHASE_A_NESTED_SEATBELT_TEST_COUNT,
                "junit_failures": 0,
                "junit_errors": 0,
                "junit_skipped": 0,
                "run_log_sha256": "b" * 64,
                "dependency_mirror": {
                    **PHASE_A_DEPENDENCY_BASELINE,
                    "identity_sha256_before": "d" * 64,
                    "identity_sha256_after": "d" * 64,
                },
                "candidate_identity_sha256_before": "e" * 64,
                "candidate_identity_sha256_after": "e" * 64,
                "candidate_digest_before": candidate["content_sha256"],
                "candidate_digest_after": candidate["content_sha256"],
                "source_digest_before": source["content_sha256"],
                "source_digest_after": source["content_sha256"],
            },
            "toolchain": {
                "git": _trusted_file_identity(Path(trusted_git_executable())),
                "node": _trusted_file_identity(_trusted_node()),
                "npm": _trusted_file_identity(npm),
                "npx": _trusted_file_identity(_trusted_npx(npm)),
                "python": _trusted_file_identity(
                    Path(trusted_python_executable(venv_python))
                ),
            },
        },
    )
    receipt_path = output.with_suffix(output.suffix + ".static-receipt.json")
    receipt_path.write_text(
        json.dumps(receipt_payload, ensure_ascii=False, indent=2, sort_keys=True)
        + "\n",
        encoding="utf-8",
    )
    receipt_path.chmod(0o600)
    verification.update(
        {
            "executed": True,
            "log_path": str(verify_log),
            "log_sha256": hashlib.sha256(verify_log.read_bytes()).hexdigest(),
            "static_receipt": {
                "path": str(receipt_path),
                "sha256": hashlib.sha256(receipt_path.read_bytes()).hexdigest(),
                "payload": receipt_payload,
            },
        }
    )
    manifest = output.with_suffix(output.suffix + ".manifest.json")
    manifest.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    manifest.chmod(0o600)
    manifest_sha256 = hashlib.sha256(manifest.read_bytes()).hexdigest()
    manifest.with_suffix(manifest.suffix + ".sha256").write_text(
        f"{manifest_sha256}  {manifest.name}\n",
        encoding="utf-8",
    )


def _built_deploy_gate_fixture(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> tuple[Path, Path, dict[str, object], Path]:
    root = _repo(tmp_path)
    (root / "backend" / "untracked.py").unlink()
    _create_test_venv(root)
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
import json
import os
import sys
from pathlib import Path

mode = sys.argv[1]
destination = Path(sys.argv[2])
shutil.copytree(Path("frontend/dist"), destination)
if mode == "drift":
    with destination.joinpath("index.html").open("a", encoding="utf-8") as handle:
        handle.write("<!-- drift -->\\n")
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
    _commit_fixture(root, "reproducible deploy gate fixture")
    venv_python = root / ".venv/bin/python"
    _install_fake_frontend_npm(tmp_path, monkeypatch)

    output = tmp_path / "candidate"
    args = _freeze_args(root, output)
    args.skip_archive = True
    args.skip_build = False
    payload = freeze_candidate(args)
    _attach_test_static_receipt(root, output, payload, venv_python)
    assert payload["build"]["executed"] is True
    return root, output, payload, venv_python


def _deploy_gate_args(
    root: Path, output: Path, payload: dict[str, object], venv_python: Path
) -> Namespace:
    source = payload["source"]
    assert isinstance(source, dict)
    runtime_root = output.parent / f".{output.name}-strict-runtime"
    runtime_root.mkdir(mode=0o700)
    os.chown(runtime_root, os.geteuid(), os.getegid())
    health_env_file = output.parent / f".{output.name}-health.env"
    health_env_file.write_text("OPS_HEALTH_TOKEN=fixture\n", encoding="utf-8")
    health_env_file.chmod(0o600)
    manifest = output.with_suffix(".manifest.json")
    static_receipt = output.with_suffix(output.suffix + ".static-receipt.json")
    return Namespace(
        manifest=str(manifest),
        snapshot=str(output),
        expected_head=str(source["head"]),
        expected_branch=str(source["branch"]),
        source=str(root),
        python=str(venv_python),
        runtime_root=str(runtime_root),
        health_env_file=str(health_env_file),
        health_url="http://127.0.0.1:18103/health",
        base_url="http://127.0.0.1:18103/",
        verify_json_out=str(runtime_root / "receipts" / "verify.json"),
        acceptance_json_out=str(runtime_root / "receipts" / "acceptance.json"),
        runtime_nonce="a" * 64,
        runtime_ports="15432,16379,18103",
        expected_manifest_sha256=hashlib.sha256(manifest.read_bytes()).hexdigest(),
        expected_static_receipt_sha256=(
            hashlib.sha256(static_receipt.read_bytes()).hexdigest()
            if static_receipt.is_file()
            else "0" * 64
        ),
        fixture_allow_test_hooks=True,
    )
