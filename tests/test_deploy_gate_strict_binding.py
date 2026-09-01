from __future__ import annotations

from pathlib import Path
import os
import shutil
import subprocess
import sys
import tempfile

import pytest

from scripts.ops.deploy_gate_runtime import (
    _path_identity,
    DeployGateRuntimeError,
    assert_provider_free_environment,
    bound_deploy_gate_runtime,
    build_deploy_gate_environment,
    build_provider_free_subprocess_environment,
    cleanup_candidate_browser_runtime,
    validate_health_env_file,
    validate_strict_gate_binding,
)
from scripts.ops.freeze_worktree_candidate import parser


def _runtime_root(tmp_path: Path, name: str = "runtime") -> Path:
    root = tmp_path / name
    root.mkdir(mode=0o700)
    root.chmod(0o700)
    return root


def _binding(root: Path, **overrides: str):
    health_env_file = root.parent / "health.env"
    health_env_file.write_text("OPS_HEALTH_TOKEN=fixture\n", encoding="utf-8")
    health_env_file.chmod(0o600)
    values = {
        "runtime_root": str(root),
        "health_env_file": str(health_env_file),
        "health_url": "http://127.0.0.1:18103/health",
        "base_url": "http://127.0.0.1:18103/",
        "verify_json_out": str(root / "receipts/verify.json"),
        "acceptance_json_out": str(root / "receipts/acceptance.json"),
    }
    values.update(overrides)
    return validate_strict_gate_binding(**values)


def test_strict_gate_binding_rejects_default_or_external_runtime_urls(tmp_path: Path) -> None:
    root = _runtime_root(tmp_path)
    with pytest.raises(DeployGateRuntimeError, match="non-8102"):
        _binding(
            root,
            health_url="http://127.0.0.1:8102/health",
            base_url="http://127.0.0.1:8102/",
        )
    with pytest.raises(DeployGateRuntimeError, match="loopback"):
        _binding(
            root,
            health_url="https://example.com/health",
            base_url="https://example.com/",
        )
    with pytest.raises(DeployGateRuntimeError, match="share one origin"):
        _binding(root, base_url="http://127.0.0.1:18104/")


def test_strict_gate_binding_rejects_output_escape_and_unsafe_root(tmp_path: Path) -> None:
    root = _runtime_root(tmp_path)
    with pytest.raises(DeployGateRuntimeError, match="inside runtime root"):
        _binding(root, verify_json_out=str(tmp_path / "escaped.json"))
    root.chmod(0o755)
    with pytest.raises(DeployGateRuntimeError, match="0700"):
        _binding(root)


def test_health_env_file_binding_rejects_links_and_unsafe_permissions(
    tmp_path: Path,
) -> None:
    health_env_file = tmp_path / "health.env"
    health_env_file.write_text("OPS_HEALTH_TOKEN=fixture\n", encoding="utf-8")
    health_env_file.chmod(0o600)
    assert validate_health_env_file(health_env_file) == health_env_file

    linked = tmp_path / "linked.env"
    linked.symlink_to(health_env_file)
    with pytest.raises(DeployGateRuntimeError, match="protected"):
        validate_health_env_file(linked)

    health_env_file.chmod(0o644)
    with pytest.raises(DeployGateRuntimeError, match="protected"):
        validate_health_env_file(health_env_file)

    inside_runtime = _runtime_root(tmp_path, "bound-runtime") / "health.env"
    inside_runtime.write_text("OPS_HEALTH_TOKEN=fixture\n", encoding="utf-8")
    inside_runtime.chmod(0o600)
    with pytest.raises(DeployGateRuntimeError, match="outside runtime root"):
        _binding(inside_runtime.parent, health_env_file=str(inside_runtime))


def test_exact_runtime_cleanup_does_not_touch_concurrent_directory(tmp_path: Path) -> None:
    arbitrary = _runtime_root(tmp_path, "candidate-runtime.arbitrary")
    identity = (arbitrary.lstat().st_dev, arbitrary.lstat().st_ino)
    with pytest.raises(DeployGateRuntimeError, match="controller-created"):
        cleanup_candidate_browser_runtime(arbitrary, expected_identity=identity)
    first = Path(tempfile.mkdtemp(prefix="vkpi-candidate-browser-runtime.", dir="/tmp"))
    second = Path(tempfile.mkdtemp(prefix="vkpi-candidate-browser-runtime.", dir="/tmp"))
    first.chmod(0o700); second.chmod(0o700)
    (first / "marker").write_text("first\n", encoding="utf-8")
    second_marker = second / "marker"
    second_marker.write_text("second\n", encoding="utf-8")

    identity = (first.lstat().st_dev, first.lstat().st_ino)
    assert cleanup_candidate_browser_runtime(first, expected_identity=identity) == []
    assert not first.exists()
    assert second_marker.read_text(encoding="utf-8") == "second\n"
    for path in (arbitrary, second):
        if path.exists():
            import shutil
            shutil.rmtree(path)


def test_run_deploy_gate_cli_requires_all_strict_runtime_bindings(tmp_path: Path) -> None:
    required = [
        "run-deploy-gate",
        "--manifest", "manifest.json",
        "--snapshot", "snapshot",
        "--expected-head", "a" * 40,
        "--expected-branch", "main",
        "--source", str(tmp_path),
        "--python", str(tmp_path / ".venv/bin/python"),
    ]
    with pytest.raises(SystemExit):
        parser().parse_args(required)
    root = _runtime_root(tmp_path)
    health_env_file = tmp_path / "health.env"
    health_env_file.write_text("OPS_HEALTH_TOKEN=fixture\n", encoding="utf-8")
    health_env_file.chmod(0o600)
    admission = root / "controller" / "runtime-admission.json"
    args = parser().parse_args(
        required
        + [
            "--runtime-root", str(root),
            "--health-env-file", str(health_env_file),
            "--health-url", "http://127.0.0.1:18103/health",
            "--base-url", "http://127.0.0.1:18103/",
            "--verify-json-out", str(root / "verify.json"),
            "--acceptance-json-out", str(root / "acceptance.json"),
            "--admission-json", str(admission),
        ]
    )
    assert args.runtime_root == str(root)
    assert args.health_env_file == str(health_env_file)
    assert args.admission_json == str(admission)


def test_bound_gate_injects_only_health_file_path_and_detects_drift(
    tmp_path: Path,
) -> None:
    source = tmp_path / "source"
    source.mkdir()
    (source / ".venv").symlink_to(Path(".venv").resolve(), target_is_directory=True)
    source_env = source / ".env"
    source_env.write_text("JWT_SECRET=fixture\n", encoding="utf-8")
    source_env.chmod(0o600)
    health_env_file = tmp_path / "health.env"
    health_env_file.write_text("OPS_HEALTH_TOKEN=fixture\n", encoding="utf-8")
    health_env_file.chmod(0o600)
    runtime_root = _runtime_root(tmp_path)

    with pytest.raises(DeployGateRuntimeError, match="health-token inputs changed"):
        with bound_deploy_gate_runtime(
            {"PATH": os.defpath, "OPS_HEALTH_TOKEN": "ambient-secret"},
            source=source,
            requested_python=source / ".venv/bin/python",
            runtime_root=runtime_root,
            health_env_file=health_env_file,
            health_url="http://127.0.0.1:18103/health",
            base_url="http://127.0.0.1:18103/",
            verify_json_out=runtime_root / "controller/verify.json",
            acceptance_json_out=runtime_root / "controller/acceptance.json",
        ) as (_python_bin, environment):
            assert environment["VKPI_HEALTH_ENV_FILE"] == str(health_env_file)
            assert "OPS_HEALTH_TOKEN" not in environment
            health_env_file.write_text(
                "OPS_HEALTH_TOKEN=changed-fixture\n", encoding="utf-8"
            )


def test_health_env_identity_captures_content_and_single_link_drift(
    tmp_path: Path,
) -> None:
    health_env_file = tmp_path / "health.env"
    health_env_file.write_text("OPS_HEALTH_TOKEN=fixture\n", encoding="utf-8")
    health_env_file.chmod(0o600)
    before = _path_identity(health_env_file)

    assert before.resolved_gid == health_env_file.stat().st_gid
    assert before.resolved_nlink == 1
    assert before.resolved_ctime_ns == health_env_file.stat().st_ctime_ns
    assert isinstance(before.resolved_sha256, str)
    assert len(before.resolved_sha256) == 64

    original_stat = health_env_file.stat()
    health_env_file.write_text("OPS_HEALTH_TOKEN=changed\n", encoding="utf-8")
    os.utime(
        health_env_file,
        ns=(original_stat.st_atime_ns, before.resolved_mtime_ns),
    )
    content_changed = _path_identity(health_env_file)
    assert content_changed.resolved_size == before.resolved_size
    assert content_changed.resolved_mtime_ns == before.resolved_mtime_ns
    assert content_changed.resolved_sha256 != before.resolved_sha256

    linked = tmp_path / "health-hardlink.env"
    os.link(health_env_file, linked)
    after = _path_identity(health_env_file)
    assert after.resolved_nlink == 2
    assert after != before


def test_bound_gate_rejects_health_file_hardlink_added_during_verification(
    tmp_path: Path,
) -> None:
    source = tmp_path / "source"
    source.mkdir()
    (source / ".venv").symlink_to(Path(".venv").resolve(), target_is_directory=True)
    source_env = source / ".env"
    source_env.write_text("JWT_SECRET=fixture\n", encoding="utf-8")
    source_env.chmod(0o600)
    health_env_file = tmp_path / "health.env"
    health_env_file.write_text("OPS_HEALTH_TOKEN=fixture\n", encoding="utf-8")
    health_env_file.chmod(0o600)
    runtime_root = _runtime_root(tmp_path)

    with pytest.raises(DeployGateRuntimeError, match="health-token inputs changed"):
        with bound_deploy_gate_runtime(
            {"PATH": os.defpath},
            source=source,
            requested_python=source / ".venv/bin/python",
            runtime_root=runtime_root,
            health_env_file=health_env_file,
            health_url="http://127.0.0.1:18103/health",
            base_url="http://127.0.0.1:18103/",
            verify_json_out=runtime_root / "controller/verify.json",
            acceptance_json_out=runtime_root / "controller/acceptance.json",
        ):
            os.link(health_env_file, tmp_path / "health-copy.env")


def test_deploy_wires_canonical_gate_to_live_private_runtime_before_ssh() -> None:
    deploy = Path("scripts/ops/deploy_local_to_cloud.sh").read_text(encoding="utf-8")
    canonical_gate = deploy.split("run_predeploy_canonical_gate() {", 1)[1].split(
        "\n}\n\nrun_predeploy_final_runtime_gate() {", 1
    )[0]
    browser_gate = deploy.split("run_predeploy_embedded_browser_gate() {", 1)[1].split(
        "\n}\n\ncapture_remote_sync_unit_state() {", 1
    )[0]

    assert deploy.count('"${TRUSTED_CANDIDATE_VERIFIER}" run-deploy-gate') == 1
    assert 'local runtime_root="${LOCAL_CANDIDATE_WEB_RUNTIME}"' in canonical_gate
    assert 'local health_url="${PREDEPLOY_BROWSER_URL}health"' in canonical_gate
    assert 'local base_url="${PREDEPLOY_BROWSER_URL}"' in canonical_gate
    for required_binding in (
        '--runtime-root "${runtime_root}"',
        '--health-env-file "${LOCAL_HEALTH_ENV_FILE}"',
        '--health-url "${health_url}"',
        '--base-url "${base_url}"',
        '--verify-json-out "${verify_receipt}"',
        '--acceptance-json-out "${acceptance_receipt}"',
        '--admission-json "${LOCAL_CANDIDATE_ADMISSION}"',
    ):
        assert required_binding in canonical_gate
    assert '"${runtime_root}/controller/canonical-verify.json"' in canonical_gate
    assert '"${runtime_root}/controller/canonical-acceptance.json"' in canonical_gate
    assert "OPS_HEALTH_TOKEN" not in canonical_gate
    assert "ssh " not in canonical_gate
    assert browser_gate.index("start_local_candidate_browser_runtime") < browser_gate.index(
        "run_predeploy_canonical_gate"
    ) < browser_gate.index("run_predeploy_final_runtime_gate") < browser_gate.index(
        "cleanup_local_candidate_browser_runtime"
    )
    assert "run_predeploy_embedded_browser_gate\nsetup_deploy_ssh_transport" in deploy


def test_deploy_rechecks_exact_worker_and_redis_fleets_after_browser_gate() -> None:
    deploy = Path("scripts/ops/deploy_local_to_cloud.sh").read_text(encoding="utf-8")
    final_gate = deploy.split("run_predeploy_final_runtime_gate() {", 1)[1].split(
        "\n}\n\nrun_predeploy_embedded_browser_gate() {", 1
    )[0]
    browser_gate = deploy.split("run_predeploy_embedded_browser_gate() {", 1)[1].split(
        "\n}\n\ncapture_remote_sync_unit_state() {", 1
    )[0]

    assert "env -i" in final_gate
    assert "OPS_HEALTH_TOKEN" not in final_gate
    assert 'scripts/ops/fetch_runtime_health.py"' in final_gate
    assert '--env-file "${LOCAL_HEALTH_ENV_FILE}"' in final_gate
    assert 'scripts/verify_runtime_health.py"' in final_gate
    assert '--expected-worker-count "${EXPECTED_WORKER_COUNT}"' in final_gate
    assert "--max-worker-age-seconds 180" in final_gate
    assert 'scripts/verify_redis_worker_health.py"' in final_gate
    assert "--expected-count 1" in final_gate
    assert "--max-age-seconds 180" in final_gate
    assert browser_gate.index('chmod 600 "${capture_path}" "${report_path}"') < (
        browser_gate.index("run_predeploy_final_runtime_gate")
    ) < browser_gate.index("cleanup_local_candidate_browser_runtime")


def test_sealed_verifier_contains_the_canonical_gate_import_closure(
    tmp_path: Path,
) -> None:
    deploy = Path("scripts/ops/deploy_local_to_cloud.sh").read_text(encoding="utf-8")
    relative_paths = (
        "scripts/ops/candidate_physical_tree.py",
        "scripts/ops/controller_static_receipt.py",
        "scripts/ops/controlled_candidate_process.py",
        "scripts/ops/deploy_gate_runtime.py",
        "scripts/ops/deploy_runtime_admission.py",
        "scripts/ops/freeze_git_bridge.py",
        "scripts/ops/freeze_deploy_gate.py",
        "scripts/ops/freeze_phase_runtime.py",
        "scripts/ops/freeze_worktree_candidate.py",
        "scripts/ops/freeze_worktree_contract.py",
        "scripts/ops/strict_runtime_seatbelt.py",
        "scripts/ops/trusted_git.py",
        "scripts/ops/trusted_npm_audit.py",
    )
    bundle = tmp_path / "bundle"
    for relative in relative_paths:
        source = Path(relative)
        target = bundle / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, target)
        target.chmod(
            0o500
            if relative.endswith(
                ("freeze_worktree_candidate.py", "deploy_runtime_admission.py")
            )
            else 0o400
        )
        assert f'"${{DEPLOY_VERIFIER_BUNDLE_DIR}}/{relative}"' in deploy
        assert f"    {relative} \\" in deploy
        assert deploy.count(f'Path("{relative}")') == 2

    completed = subprocess.run(
        [
            sys.executable,
            "-I",
            "-B",
            str(bundle / "scripts/ops/freeze_worktree_candidate.py"),
            "run-deploy-gate",
            "--help",
        ],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr


def test_canonical_gate_receipts_are_bound_by_reviewed_environment_names() -> None:
    verify = Path("scripts/verify.sh").read_text(encoding="utf-8")
    assert 'VKPI_VERIFY_JSON_OUT' in verify
    assert 'VKPI_VERIFY_ACCEPTANCE_JSON_OUT' in verify


def test_phase_a_and_canonical_env_i_drop_all_ambient_service_credentials(
    tmp_path: Path,
) -> None:
    hostile = {
        "GEMINI_API_KEY": "secret", "APIFY_TOKEN": "secret",
        "HTTP_PROXY": "http://evil", "DATABASE_URL": "postgres://evil/db",
        "REDIS_URL": "redis://evil", "AWS_SECRET_ACCESS_KEY": "secret",
        "SHOPIFY_ACCESS_TOKEN": "secret", "GOAFFPRO_API_KEY": "secret",
        "R2_SECRET_ACCESS_KEY": "secret", "RESEND_API_KEY": "secret",
        "SENTRY_DSN": "https://evil", "PATH": "/usr/bin:/bin",
    }
    phase_a = build_provider_free_subprocess_environment(
        hostile, home=tmp_path, tmpdir=tmp_path,
    )
    source = tmp_path / "source"
    (source / ".venv/bin").mkdir(parents=True)
    local_env = source / ".env"
    local_env.write_text("JWT_SECRET=fixture\n", encoding="utf-8")
    local_env.chmod(0o600)
    canonical = build_deploy_gate_environment(
        hostile, source=source, python_bin=source / ".venv/bin/python",
        runtime_root=tmp_path,
    )
    for environment in (phase_a, canonical):
        assert (set(hostile) - {"PATH"}).isdisjoint(environment)
        assert environment["VKPI_LLM_GATEWAY_FORCE_OFFLINE"] == "1"
        assert_provider_free_environment(environment)
