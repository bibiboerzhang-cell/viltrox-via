from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from scripts.ops.controller_static_receipt import (
    _validate_nested_seatbelt_tests,
    _validate_verification_mirror,
)
from scripts.ops.freeze_phase_runtime import (
    PHASE_A_NESTED_SEATBELT_TEST_COUNT,
    PHASE_A_NESTED_SEATBELT_TEST_FILES,
    PHASE_A_NESTED_SEATBELT_TESTS,
)
from scripts.ops.freeze_worktree_candidate import (
    FreezeError,
    _validate_controller_static_receipt,
    freeze_candidate,
    run_deploy_gate,
)
from tests.freeze_worktree_candidate_fixtures import (
    _attach_test_static_receipt,
    _built_deploy_gate_fixture,
    _commit_fixture,
    _create_test_venv,
    _deploy_gate_args,
    _freeze_args,
    _repo,
    _write,
)


def test_nested_seatbelt_missing_proof_is_test_fixture_only(
    tmp_path: Path,
) -> None:
    verify = (Path(__file__).resolve().parents[1] / "scripts/verify.sh").read_text(
        encoding="utf-8"
    )
    assert "Legacy Phase A precheck count bypass is forbidden" in verify
    assert "VKPI_PHASE_A_NESTED_SEATBELT_RECEIPT_SHA256" in verify
    assert 'STATIC_COVERAGE_STATE="outer_static_partial_requires_nested_proof"' in verify
    assert '"passed": final_pass == "1" and static_coverage_state == "complete"' in verify
    assert "CONTROLLER PARTIAL" in verify
    assert "exit 78" in verify
    for relative in PHASE_A_NESTED_SEATBELT_TEST_FILES:
        assert verify.count(f'--ignore="$ROOT/{relative}"') == 1
    proof = {
        "status": "not_present_fixture",
        "test_files": list(PHASE_A_NESTED_SEATBELT_TEST_FILES),
        "file_counts": dict(PHASE_A_NESTED_SEATBELT_TESTS),
        "expected_count": PHASE_A_NESTED_SEATBELT_TEST_COUNT,
    }
    with pytest.raises(FreezeError, match="were not executed"):
        _validate_nested_seatbelt_tests(proof, snapshot=tmp_path)
    _validate_nested_seatbelt_tests(
        proof, snapshot=tmp_path, allow_not_present_fixture=True
    )
    fixed_test = tmp_path / PHASE_A_NESTED_SEATBELT_TEST_FILES[0]
    fixed_test.parent.mkdir(parents=True)
    fixed_test.write_text("fixture\n", encoding="utf-8")

    with pytest.raises(FreezeError, match="were not executed"):
        _validate_nested_seatbelt_tests(proof, snapshot=tmp_path)
    fixed_test.unlink()


def test_phase_a_mirror_proof_must_bind_all_four_digests() -> None:
    digest = "a" * 64
    proof = {
        "status": "passed",
        "copy_method": "independent_physical_files",
        "file_count": 1,
        "candidate_digest_before": digest,
        "mirror_digest_before": digest,
        "candidate_digest_after": digest,
        "mirror_digest_after": digest,
    }
    _validate_verification_mirror(
        proof, candidate_digest=digest, candidate_file_count=1,
    )
    proof["mirror_digest_after"] = "b" * 64
    with pytest.raises(FreezeError, match="mirror proof is invalid"):
        _validate_verification_mirror(
            proof, candidate_digest=digest, candidate_file_count=1,
        )


def _clean_fixture(tmp_path: Path) -> tuple[Path, Path, dict[str, object], Path]:
    root = _repo(tmp_path)
    (root / "backend" / "untracked.py").unlink()
    venv_python = _create_test_venv(root)
    output = tmp_path / "candidate"
    payload = freeze_candidate(_freeze_args(root, output))
    return root, output, payload, venv_python


def _rewrite_manifest(output: Path, payload: dict[str, object]) -> str:
    manifest = output.with_suffix(output.suffix + ".manifest.json")
    manifest.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    manifest.chmod(0o600)
    digest = hashlib.sha256(manifest.read_bytes()).hexdigest()
    manifest.with_suffix(manifest.suffix + ".sha256").write_text(
        f"{digest}  {manifest.name}\n", encoding="utf-8"
    )
    return digest


@pytest.mark.darwin_controller  # 可信控制器 npm 只在 macOS 三个绝对路径;Linux CI 跳过
def test_deploy_gate_rejects_static_receipt_artifact_tamper_before_execution(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root, output, payload, venv_python = _clean_fixture(tmp_path)
    _attach_test_static_receipt(root, output, payload, venv_python)
    receipt = output.with_suffix(output.suffix + ".static-receipt.json")
    with receipt.open("ab") as handle:
        handle.write(b" \n")

    def unexpected_execution(*_args: object, **_kwargs: object) -> object:
        raise AssertionError("candidate process started before receipt admission")

    monkeypatch.setattr(
        "scripts.ops.controlled_candidate_process.run_controlled_candidate",
        unexpected_execution,
    )
    with pytest.raises(FreezeError, match="artifact hash mismatch"):
        run_deploy_gate(_deploy_gate_args(root, output, payload, venv_python))


@pytest.mark.darwin_controller  # 可信控制器 npm 只在 macOS 三个绝对路径;Linux CI 跳过
def test_deploy_gate_rejects_cross_candidate_static_receipt_replay(
    tmp_path: Path,
) -> None:
    root, first, first_payload, venv_python = _clean_fixture(tmp_path)
    _attach_test_static_receipt(root, first, first_payload, venv_python)
    first_receipt = first.with_suffix(first.suffix + ".static-receipt.json")
    first_receipt_bytes = first_receipt.read_bytes()
    first_receipt_payload = json.loads(first_receipt_bytes)

    _write(root / "scripts" / "verify.sh", "#!/usr/bin/env bash\nexit 0\n")
    _commit_fixture(root, "second candidate")
    second = tmp_path / "candidate-second"
    second_payload = freeze_candidate(_freeze_args(root, second))
    _attach_test_static_receipt(root, second, second_payload, venv_python)
    second_receipt = second.with_suffix(second.suffix + ".static-receipt.json")
    second_receipt.write_bytes(first_receipt_bytes)
    second_receipt.chmod(0o600)
    verification = second_payload["verification"]
    assert isinstance(verification, dict)
    verification["static_receipt"] = {
        "path": str(second_receipt),
        "sha256": hashlib.sha256(first_receipt_bytes).hexdigest(),
        "payload": first_receipt_payload,
    }
    _rewrite_manifest(second, second_payload)

    with pytest.raises(FreezeError, match="full-source binding mismatch"):
        run_deploy_gate(
            _deploy_gate_args(root, second, second_payload, venv_python)
        )


def test_ambient_receipt_environment_cannot_bypass_manifest_binding(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root, output, payload, venv_python = _clean_fixture(tmp_path)
    ambient = tmp_path / "ambient-receipt.json"
    ambient.write_text('{"passed":true}\n', encoding="utf-8")
    ambient.chmod(0o600)
    monkeypatch.setenv("VKPI_CONTROLLER_STATIC_GATE_RECEIPT", str(ambient))

    with pytest.raises(FreezeError, match="requires a controller static gate receipt"):
        run_deploy_gate(_deploy_gate_args(root, output, payload, venv_python))


@pytest.mark.darwin_controller  # 可信控制器 npm 只在 macOS 三个绝对路径;Linux CI 跳过
def test_controller_receipt_validation_uses_bound_reads_not_path_read_bytes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root, output, payload, venv_python = _clean_fixture(tmp_path)
    _attach_test_static_receipt(root, output, payload, venv_python)

    def reject_read_bytes(_path: Path) -> bytes:
        raise AssertionError("receipt validation reopened a path with read_bytes")

    monkeypatch.setattr(Path, "read_bytes", reject_read_bytes)
    admitted, admitted_bytes = _validate_controller_static_receipt(
        manifest=payload, snapshot=output
    )

    assert admitted["passed"] is True
    assert hashlib.sha256(admitted_bytes).hexdigest() == payload["verification"][
        "static_receipt"
    ]["sha256"]


@pytest.mark.darwin_controller
def test_deploy_gate_preserves_recorded_phase_a_source_and_uses_controller_source(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root, output, payload, venv_python = _built_deploy_gate_fixture(
        tmp_path, monkeypatch
    )
    recorded_source = tmp_path / "deleted-phase-a" / "clean-source"
    assert not recorded_source.exists()
    source = payload["source"]
    assert isinstance(source, dict)
    source["repo"] = str(recorded_source)
    manifest_sha256 = _rewrite_manifest(output, payload)
    receipt = output.with_suffix(output.suffix + ".static-receipt.json")
    deploy_args = _deploy_gate_args(root, output, payload, venv_python)
    deploy_args.expected_recorded_source = str(recorded_source)
    deploy_args.controller_source = str(root)
    deploy_args.expected_manifest_sha256 = manifest_sha256
    deploy_args.expected_static_receipt_sha256 = hashlib.sha256(
        receipt.read_bytes()
    ).hexdigest()
    monkeypatch.setenv("VKPI_TEST_REBUILD_MODE", "match")

    result = run_deploy_gate(deploy_args)

    assert result["canonical_deploy_gate"] is True
    assert result["candidate_manifest_sha256"] == manifest_sha256
