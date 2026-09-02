from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

import pytest

from scripts.verify_static_gate_helpers import (
    LINE_GUARD_ALLOWLIST,
    check_release_line_guard,
    validate_npm_audit_receipt,
)
from scripts.ops.freeze_worktree_candidate import freeze_candidate
from tests.freeze_worktree_candidate_fixtures import (
    _attach_test_static_receipt,
    _commit_fixture,
    _create_test_venv,
    _freeze_args,
    _repo,
)


def test_trusted_npm_audit_receipt_is_bound_to_lock_bytes(tmp_path: Path) -> None:
    lock = tmp_path / "package-lock.json"
    lock.write_text('{"lockfileVersion":3}\n', encoding="utf-8")
    receipt = tmp_path / "receipt.json"
    receipt.write_text(
        json.dumps(
            {
                "schema": "vkpi.controller-npm-audit/v1",
                "passed": True,
                "returncode": 0,
                "package_lock_sha256": hashlib.sha256(lock.read_bytes()).hexdigest(),
            }
        ),
        encoding="utf-8",
    )

    validate_npm_audit_receipt(receipt, lock)
    lock.write_text('{"lockfileVersion":2}\n', encoding="utf-8")
    with pytest.raises(SystemExit, match="receipt mismatch"):
        validate_npm_audit_receipt(receipt, lock)


def test_release_line_guard_has_zero_allowlist_and_rejects_debt(
    tmp_path: Path,
) -> None:
    assert LINE_GUARD_ALLOWLIST == frozenset()
    violation = tmp_path / "backend/app/new_debt.py"
    violation.parent.mkdir(parents=True, exist_ok=True)
    violation.write_text("x\n" * 1001, encoding="utf-8")
    with pytest.raises(SystemExit) as raised:
        check_release_line_guard(tmp_path)
    assert raised.value.code == 1


@pytest.mark.darwin_controller  # 可信控制器 npm 只在 macOS 三个绝对路径;Linux CI 跳过
def test_controller_static_helper_accepts_exact_partial_proof_and_rejects_tamper(
    tmp_path: Path,
) -> None:
    source = _repo(tmp_path)
    (source / "backend/untracked.py").unlink()
    venv_python = _create_test_venv(source)
    project = Path(__file__).resolve().parents[1]
    helper_closure = (
        "scripts/verify_static_gate_helpers.py",
        "scripts/stdout_utils.py",
        "scripts/ops/controller_static_receipt.py",
        "scripts/ops/freeze_phase_runtime.py",
        "scripts/ops/freeze_worktree_contract.py",
    )
    for relative in helper_closure:
        target = source / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(project / relative, target)
    subprocess.run(["git", "add", *helper_closure], cwd=source, check=True)
    _commit_fixture(source, "add isolated static receipt helper")
    candidate = tmp_path / "candidate"
    manifest = freeze_candidate(_freeze_args(source, candidate))
    _attach_test_static_receipt(source, candidate, manifest, venv_python)

    source_receipt = candidate.with_suffix(candidate.suffix + ".static-receipt.json")
    original = json.loads(source_receipt.read_text(encoding="utf-8"))
    runtime_root = tmp_path / "strict-runtime"
    controller = runtime_root / "controller"
    runtime_root.mkdir(mode=0o700)
    controller.mkdir(mode=0o700)
    private_receipt = controller / "static-receipt.json"
    candidate_record = manifest["candidate"]
    source_record = manifest["source"]
    assert isinstance(candidate_record, dict)
    assert isinstance(source_record, dict)

    helper = candidate / "scripts/verify_static_gate_helpers.py"

    def validate(payload: dict[str, object]) -> subprocess.CompletedProcess[str]:
        data = (
            json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
        ).encode("utf-8")
        private_receipt.write_bytes(data)
        private_receipt.chmod(0o600)
        return subprocess.run(
            [
                sys.executable,
                "-I",
                "-B",
                str(helper),
                "static-receipt",
                str(private_receipt),
                str(candidate),
                str(runtime_root),
                str(candidate_record["content_sha256"]),
                str(source_record["head"]),
                str(source_record["branch"]),
                hashlib.sha256(data).hexdigest(),
                "a" * 64,
                "8001",
                "http://127.0.0.1:8001/health",
                "http://127.0.0.1:8001",
            ],
            cwd=candidate,
            env={
                "HOME": str(tmp_path),
                "PATH": os.defpath,
                "PYTHONDONTWRITEBYTECODE": "1",
            },
            capture_output=True,
            text=True,
            check=False,
        )

    accepted = validate(original)
    assert accepted.returncode == 0, accepted.stderr
    assert "controller-bound canonical static receipt passed" in accepted.stdout

    partial_tamper = json.loads(json.dumps(original))
    partial_tamper["canonical_receipt"]["static_coverage"]["complete"] = True
    partial_tamper["canonical_receipt_sha256"] = hashlib.sha256(
        json.dumps(
            partial_tamper["canonical_receipt"],
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()
    rejected_partial = validate(partial_tamper)
    assert rejected_partial.returncode != 0
    assert "binding mismatch" in rejected_partial.stderr

    nested_tamper = json.loads(json.dumps(original))
    nested_tamper["nested_seatbelt_tests"]["passed_count"] -= 1
    rejected_nested = validate(nested_tamper)
    assert rejected_nested.returncode != 0
    assert "binding mismatch" in rejected_nested.stderr
