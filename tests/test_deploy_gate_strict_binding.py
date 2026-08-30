from __future__ import annotations

from pathlib import Path
import os
import tempfile

import pytest

from scripts.ops.deploy_gate_runtime import (
    DeployGateRuntimeError,
    assert_provider_free_environment,
    build_deploy_gate_environment,
    build_provider_free_subprocess_environment,
    cleanup_candidate_browser_runtime,
    validate_strict_gate_binding,
)
from scripts.ops.freeze_worktree_candidate import parser


def _runtime_root(tmp_path: Path, name: str = "runtime") -> Path:
    root = tmp_path / name
    root.mkdir(mode=0o700)
    root.chmod(0o700)
    return root


def _binding(root: Path, **overrides: str):
    values = {
        "runtime_root": str(root),
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
    args = parser().parse_args(
        required
        + [
            "--runtime-root", str(root),
            "--health-url", "http://127.0.0.1:18103/health",
            "--base-url", "http://127.0.0.1:18103/",
            "--verify-json-out", str(root / "verify.json"),
            "--acceptance-json-out", str(root / "acceptance.json"),
        ]
    )
    assert args.runtime_root == str(root)


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
