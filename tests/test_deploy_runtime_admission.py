from __future__ import annotations

import hashlib
import subprocess
from argparse import Namespace
from pathlib import Path

import pytest

from scripts.ops.deploy_runtime_admission import (
    SCHEMA,
    load_admission,
    prepare_admission,
    validate_runtime_binding_values,
)
from scripts.ops.freeze_worktree_candidate import freeze_candidate
from scripts.ops.freeze_worktree_candidate import run_deploy_gate
from scripts.ops.freeze_worktree_contract import FreezeError
from tests.freeze_worktree_candidate_fixtures import (
    _attach_test_static_receipt,
    _built_deploy_gate_fixture,
    _commit_fixture,
    _create_test_venv,
    _freeze_args,
    _deploy_gate_args,
    _repo,
    _write,
)


def _admission_fixture(
    tmp_path: Path,
) -> tuple[Path, Path, Path, Namespace, dict[str, object]]:
    source = _repo(tmp_path)
    (source / "backend/untracked.py").unlink()
    venv_python = _create_test_venv(source)
    _write(
        source / ".env",
        "\n".join(
            (
                "LOCAL_DATABASE_URL=postgresql://postgres@127.0.0.1:15432/vkpi",
                "REDIS_URL=redis://127.0.0.1:16379/0",
                "JWT_SECRET=fixture-jwt-secret",
                "JWT_SECRET_PREVIOUS=",
                "ADMIN_PASSWORD=fixture-admin-password",
                "REDIS_NAMESPACE=fixture",
                "ANTHROPIC_API_KEY=must-never-reach-candidate",
                "OPENAI_API_KEY=must-never-reach-candidate",
                "",
            )
        ),
    )
    (source / ".env").chmod(0o600)
    subprocess.run(["git", "add", ".env"], cwd=source, check=True)
    _commit_fixture(source, "reviewed local runtime fixture")
    candidate = tmp_path / "candidate"
    phase = freeze_candidate(_freeze_args(source, candidate))
    _attach_test_static_receipt(source, candidate, phase, venv_python)

    runtime = tmp_path / "runtime"
    runtime.mkdir(mode=0o700)
    health = tmp_path / "health.env"
    health.write_text("OPS_HEALTH_TOKEN=fixture-health-token\n", encoding="utf-8")
    health.chmod(0o600)
    args = Namespace(
        manifest=str(candidate.with_suffix(".manifest.json")),
        snapshot=str(candidate),
        expected_head=str(phase["source"]["head"]),
        expected_branch=str(phase["source"]["branch"]),
        source=str(source),
        runtime_root=str(runtime),
        source_env_file=str(source / ".env"),
        health_env_file=str(health),
        web_port=18103,
        env_out=str(runtime / "controller/candidate-runtime.env"),
        web_profile_out=str(runtime / "controller/candidate-web.sb"),
        verify_profile_out=str(runtime / "controller/candidate-verify.sb"),
        admission_out=str(runtime / "controller/runtime-admission.json"),
    )
    payload = prepare_admission(args)
    return source, candidate, health, args, payload


def test_prepare_admission_filters_provider_secrets_and_pins_phase_a(
    tmp_path: Path,
) -> None:
    source, candidate, health, args, payload = _admission_fixture(tmp_path)
    runtime_env = Path(args.env_out).read_bytes()
    assert payload["schema"] == SCHEMA
    assert payload["provider_credentials_forwarded"] is False
    assert payload["external_network_allowed"] is False
    assert payload["runtime_ports"] == "15432,16379,18103"
    assert payload["runtime_env_sha256"] == hashlib.sha256(runtime_env).hexdigest()
    assert b"fixture-jwt-secret" in runtime_env
    assert b"fixture-health-token" in runtime_env
    assert b"JWT_SECRET_PREVIOUS" not in runtime_env
    assert b"ANTHROPIC" not in runtime_env
    assert b"OPENAI" not in runtime_env
    assert b"must-never-reach-candidate" not in runtime_env
    web_profile = Path(args.web_profile_out).read_text(encoding="utf-8")
    verify_profile = Path(args.verify_profile_out).read_text(encoding="utf-8")
    assert "(deny network*)" in web_profile
    assert "(deny network*)" in verify_profile
    assert str(source / ".env") not in web_profile

    loaded = load_admission(
        Path(args.admission_out),
        runtime_root=Path(args.runtime_root),
        candidate=candidate,
        manifest=Path(args.manifest),
        health_env_file=health,
        health_url="http://127.0.0.1:18103/health",
        base_url="http://127.0.0.1:18103/",
    )
    assert loaded["candidate_sha256"] == payload["candidate_sha256"]
    assert loaded["_verify_profile"] == verify_profile


def test_web_profile_can_read_filtered_env_but_not_project_dotenv(
    tmp_path: Path,
) -> None:
    source, _candidate, _health, args, _payload = _admission_fixture(tmp_path)
    allowed = subprocess.run(
        [
            "/usr/bin/sandbox-exec",
            "-f",
            args.web_profile_out,
            "/bin/cat",
            args.env_out,
        ],
        capture_output=True,
        check=False,
    )
    denied = subprocess.run(
        [
            "/usr/bin/sandbox-exec",
            "-f",
            args.web_profile_out,
            "/bin/cat",
            str(source / ".env"),
        ],
        capture_output=True,
        check=False,
    )
    assert allowed.returncode == 0
    assert denied.returncode != 0
    assert b"must-never-reach-candidate" not in denied.stdout


def test_verifier_profile_allows_bash_heredoc_without_broad_tmp_write(
    tmp_path: Path,
) -> None:
    _source, candidate, _health, args, _payload = _admission_fixture(tmp_path)
    runtime = Path(args.runtime_root)
    heredoc = subprocess.run(
        [
            "/usr/bin/sandbox-exec",
            "-f",
            args.verify_profile_out,
            "/usr/bin/env",
            f"TMPDIR={runtime / 'tmp'}",
            "/bin/bash",
            "-c",
            "value=$(cat <<EOF\nok\nEOF\n); test \"$value\" = ok",
        ],
        cwd=candidate,
        capture_output=True,
        check=False,
    )
    unrelated = tmp_path.parent / "verifier-unrelated-tmp-write"
    denied = subprocess.run(
        [
            "/usr/bin/sandbox-exec",
            "-f",
            args.verify_profile_out,
            "/usr/bin/touch",
            str(unrelated),
        ],
        cwd=candidate,
        capture_output=True,
        check=False,
    )
    assert heredoc.returncode == 0, heredoc.stderr.decode("utf-8", "replace")
    assert denied.returncode != 0
    assert not unrelated.exists()


def test_admission_rejects_profile_tamper_and_noncanonical_binding(
    tmp_path: Path,
) -> None:
    _source, candidate, health, args, _payload = _admission_fixture(tmp_path)
    profile = Path(args.verify_profile_out)
    profile.write_text(profile.read_text(encoding="utf-8") + "\n", encoding="utf-8")
    profile.chmod(0o600)
    with pytest.raises(FreezeError, match="verify_profile_file changed"):
        load_admission(
            Path(args.admission_out),
            runtime_root=Path(args.runtime_root),
            candidate=candidate,
            manifest=Path(args.manifest),
            health_env_file=health,
            health_url="http://127.0.0.1:18103/health",
            base_url="http://127.0.0.1:18103/",
        )
    with pytest.raises(FreezeError, match="not canonical"):
        validate_runtime_binding_values(
            nonce="a" * 64,
            ports="18103,15432,16379",
            health_url="http://127.0.0.1:18103/health",
            base_url="http://127.0.0.1:18103/",
        )
    with pytest.raises(FreezeError, match="nonce"):
        validate_runtime_binding_values(
            nonce="replayable",
            ports="15432,16379,18103",
            health_url="http://127.0.0.1:18103/health",
            base_url="http://127.0.0.1:18103/",
        )


def test_real_deploy_gate_consumes_admission_and_runs_under_profile(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source, candidate, phase, venv_python = _built_deploy_gate_fixture(
        tmp_path, monkeypatch
    )
    source_env = source / ".env"
    source_env.write_text(
        "LOCAL_DATABASE_URL=postgresql://postgres@127.0.0.1:15432/vkpi\n"
        "REDIS_URL=redis://127.0.0.1:16379/0\n"
        "JWT_SECRET=fixture-jwt-secret\n"
        "ADMIN_PASSWORD=fixture-admin-password\n",
        encoding="utf-8",
    )
    source_env.chmod(0o600)
    deploy_args = _deploy_gate_args(source, candidate, phase, venv_python)
    runtime = Path(deploy_args.runtime_root)
    prepare_args = Namespace(
        manifest=deploy_args.manifest,
        snapshot=deploy_args.snapshot,
        expected_head=deploy_args.expected_head,
        expected_branch=deploy_args.expected_branch,
        source=str(source),
        runtime_root=str(runtime),
        source_env_file=str(source_env),
        health_env_file=deploy_args.health_env_file,
        web_port=18103,
        env_out=str(runtime / "controller/candidate-runtime.env"),
        web_profile_out=str(runtime / "controller/candidate-web.sb"),
        verify_profile_out=str(runtime / "controller/candidate-verify.sb"),
        admission_out=str(runtime / "controller/runtime-admission.json"),
    )
    admission = prepare_admission(prepare_args)
    deploy_args.admission_json = prepare_args.admission_out
    monkeypatch.setenv("VKPI_TEST_REBUILD_MODE", "match")

    result = run_deploy_gate(deploy_args)

    assert result["canonical_deploy_gate"] is True
    assert result["runtime_admission"] == {
        "schema": SCHEMA,
        "nonce": admission["nonce"],
        "runtime_ports": "15432,16379,18103",
        "provider_credentials_forwarded": False,
        "external_network_allowed": False,
    }
