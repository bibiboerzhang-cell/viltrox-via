from __future__ import annotations

import hashlib
import os
import subprocess
import sys
from pathlib import Path

import pytest

from scripts.ops.candidate_physical_tree import candidate_verification_mirror
from scripts.ops.freeze_phase_runtime import (
    PHASE_A_NESTED_SEATBELT_TESTS,
    PHASE_A_NESTED_SEATBELT_TEST_FILES,
    _prepare_nested_dependency_mirror,
    run_nested_seatbelt_tests,
)
from scripts.ops.phase_a_precheck_receipt import (
    validate_delegated_receipt,
    write_receipt,
)
from scripts.ops.freeze_worktree_contract import FreezeError, path_identity, precreate_owned_file
from scripts.ops.strict_runtime_seatbelt import trusted_user_home


def _fixture(root: Path) -> tuple[Path, list[dict[str, object]]]:
    candidate = root / "candidate"
    candidate.mkdir()
    payload = candidate / "payload.txt"
    payload.write_text("bound\n", encoding="utf-8")
    payload.chmod(0o640)
    data = payload.read_bytes()
    return candidate, [
        {
            "mode": "0640",
            "path": "payload.txt",
            "sha256": hashlib.sha256(data).hexdigest(),
            "size_bytes": len(data),
        }
    ]


def test_verification_mirror_is_independent_and_binds_all_digests(
    tmp_path: Path,
) -> None:
    source, files = _fixture(tmp_path)

    with candidate_verification_mirror(source, files) as (mirror, proof):
        assert mirror.is_relative_to(trusted_user_home())
        assert (mirror / "payload.txt").read_bytes() == b"bound\n"
        assert os.stat(source / "payload.txt").st_ino != os.stat(
            mirror / "payload.txt"
        ).st_ino
        assert os.stat(mirror / "payload.txt").st_nlink == 1

    digest = proof["candidate_digest_before"]
    assert proof == {
        "status": "passed",
        "copy_method": "independent_physical_files",
        "file_count": 1,
        "candidate_digest_before": digest,
        "mirror_digest_before": digest,
        "candidate_digest_after": digest,
        "mirror_digest_after": digest,
    }


def test_verification_mirror_revalidates_bytes_on_gate_failure(
    tmp_path: Path,
) -> None:
    source, files = _fixture(tmp_path)

    with pytest.raises(FreezeError, match="mirror source changed"):
        with candidate_verification_mirror(source, files) as (mirror, _proof):
            (mirror / "payload.txt").write_text("tampered\n", encoding="utf-8")
            raise RuntimeError("gate failed")

    assert (source / "payload.txt").read_bytes() == b"bound\n"


def test_nested_failure_is_copied_to_bound_controller_log(tmp_path: Path) -> None:
    snapshot = tmp_path / "snapshot"
    for relative in PHASE_A_NESTED_SEATBELT_TEST_FILES:
        path = snapshot / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("# present\n", encoding="utf-8")
    fake_python = tmp_path / "python"
    fake_python.write_text(
        "#!/bin/sh\necho nested-collect-failure\nexit 9\n", encoding="utf-8"
    )
    fake_python.chmod(0o700)
    runtime = tmp_path / "runtime"
    runtime.mkdir()
    failure_log = runtime / "candidate-verify.log"
    identity = precreate_owned_file(failure_log)

    with pytest.raises(FreezeError, match="command failed with exit 9"):
        run_nested_seatbelt_tests(
            snapshot=snapshot,
            python_bin=fake_python,
            env={"HOME": str(runtime), "PATH": "/usr/bin:/bin"},
            runtime_root=runtime,
            error_log_path=tmp_path / "published.verify.log",
            failure_log_path=failure_log,
            failure_log_identity=identity,
            expected_test_file_sha256={
                relative: hashlib.sha256((snapshot / relative).read_bytes()).hexdigest()
                for relative in PHASE_A_NESTED_SEATBELT_TEST_FILES
            },
        )

    assert path_identity(failure_log) == identity
    assert failure_log.read_text(encoding="utf-8") == "nested-collect-failure\n"

    protected = tmp_path / "protected-snapshot"
    marker = tmp_path / "sitecustomize-loaded"
    for index, (relative, count) in enumerate(PHASE_A_NESTED_SEATBELT_TESTS):
        prelude = ""
        if index == 0:
            prelude = (
                "import atexit, sys\nfrom pathlib import Path\n"
                "atexit.register(lambda: Path(sys.argv[sys.argv.index('--junitxml') + 1]).write_text('forged'))\n\n"
            )
        body = "\n\n".join(
            f"def test_protected_{item}():\n    assert True"
            for item in range(count)
        )
        target = protected / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(prelude + body + "\n", encoding="utf-8")
    (protected / "pytest.py").write_text("raise RuntimeError('shadowed')\n")
    (protected / "sitecustomize.py").write_text(
        f"from pathlib import Path\nPath({str(marker)!r}).write_text('loaded')\n"
    )
    protected_runtime = tmp_path / "protected-runtime"
    protected_runtime.mkdir()
    protected_log = protected_runtime / "failure.log"
    protected_identity = precreate_owned_file(protected_log)
    hostile_venv = tmp_path / "hostile-venv"
    subprocess.run(
        [sys.executable, "-m", "venv", "--without-pip", str(hostile_venv)],
        check=True,
    )
    hostile_site = next((hostile_venv / "lib").glob("python*/site-packages"))
    pth_marker = tmp_path / "pth-loaded"
    hostile_site_marker = tmp_path / "hostile-sitecustomize-loaded"
    attack_module = hostile_site / "vkpi_pytest_attack.py"
    attack_module.write_text(
        "from pathlib import Path\n"
        "import pytest\n"
        f"Path({str(pth_marker)!r}).write_text('loaded')\n"
        "pytest.main = lambda _args: 0\n",
        encoding="utf-8",
    )
    (hostile_site / "hostile.pth").write_text(
        f"{str(Path(pytest.__file__).resolve().parents[1])}\n"
        "import vkpi_pytest_attack\n",
        encoding="utf-8",
    )
    (hostile_site / "sitecustomize.py").write_text(
        f"from pathlib import Path\nPath({str(hostile_site_marker)!r}).write_text('loaded')\n",
        encoding="utf-8",
    )
    expected_hashes = {
        relative: hashlib.sha256((protected / relative).read_bytes()).hexdigest()
        for relative in PHASE_A_NESTED_SEATBELT_TEST_FILES
    }
    proof = run_nested_seatbelt_tests(
        snapshot=protected, python_bin=hostile_venv / "bin/python",
        env=os.environ.copy(),
        runtime_root=protected_runtime, error_log_path=protected_log,
        failure_log_path=protected_log, failure_log_identity=protected_identity,
        expected_test_file_sha256=expected_hashes,
    )
    assert proof["passed_count"] == proof["expected_count"]
    assert proof["dependency_mirror"]["identity_sha256_before"] == (
        proof["dependency_mirror"]["identity_sha256_after"]
    )
    assert proof["candidate_identity_sha256_before"] == (
        proof["candidate_identity_sha256_after"]
    )
    assert "--import-mode=importlib" in proof["command"]
    assert not marker.exists()
    assert not pth_marker.exists()
    assert not hostile_site_marker.exists()
    poisoned_dependencies = protected_runtime / "controller-pytest-dependencies"
    poisoned_pytest = poisoned_dependencies / "pytest/__init__.py"
    poisoned_pytest.chmod(0o600)
    poisoned_pytest.write_bytes(poisoned_pytest.read_bytes() + b"# ambient drift\n")
    poisoned_pytest.chmod(0o400)
    rejected_runtime = tmp_path / "rejected-dependency-runtime"
    rejected_runtime.mkdir()
    with pytest.raises(FreezeError, match="differs from reviewed baseline"):
        _prepare_nested_dependency_mirror(
            poisoned_dependencies, rejected_runtime,
        )
    proof.update(
        {
            "candidate_digest_before": "a" * 64,
            "candidate_digest_after": "a" * 64,
            "source_digest_before": "b" * 64,
            "source_digest_after": "b" * 64,
        }
    )
    delegated_root = tmp_path / "controller-immutable"
    delegated_root.mkdir(mode=0o700)
    receipt = write_receipt(
        delegated_root / "nested-seatbelt-precheck.json", proof
    )
    accepted = validate_delegated_receipt(
        path=Path(receipt["path"]), expected_sha256=receipt["sha256"],
        candidate_root=protected,
    )
    assert accepted["nested_seatbelt_tests"]["passed_count"] == 78
    first_test = protected / PHASE_A_NESTED_SEATBELT_TEST_FILES[0]
    first_test.write_text(first_test.read_text() + "# drift\n", encoding="utf-8")
    with pytest.raises(FreezeError, match="does not match candidate tests"):
        validate_delegated_receipt(
            path=Path(receipt["path"]), expected_sha256=receipt["sha256"],
            candidate_root=protected,
        )
