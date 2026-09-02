from __future__ import annotations

import hashlib
import os
import shutil
import socket
import stat
import subprocess
import sys
from argparse import Namespace
from pathlib import Path

import pytest

from scripts.ops.deploy_runtime_admission import (
    SCHEMA,
    _database_url_with_gss_disabled,
    _profile_payloads,
    load_admission,
    prepare_admission,
    validate_runtime_binding_values,
)
from scripts.ops.freeze_deploy_gate import (
    _run_controlled_candidate_with_private_output,
)
from scripts.ops.freeze_worktree_candidate import freeze_candidate
from scripts.ops.freeze_worktree_candidate import run_deploy_gate
from scripts.ops.freeze_worktree_contract import FreezeError
from scripts.ops.trusted_npm_audit import (
    _trusted_node,
    _trusted_npm,
    _trusted_npm_package_root,
)
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


def _unused_loopback_ports() -> tuple[int, int, int]:
    reservations: list[socket.socket] = []
    try:
        while len(reservations) < 3:
            reservation = socket.socket()
            reservation.bind(("127.0.0.1", 0))
            if reservation.getsockname()[1] == 8102:
                reservation.close()
                continue
            reservations.append(reservation)
        return tuple(reservation.getsockname()[1] for reservation in reservations)
    finally:
        for reservation in reservations:
            reservation.close()


def _admission_fixture(
    tmp_path: Path,
) -> tuple[Path, Path, Path, Namespace, dict[str, object]]:
    database_port, redis_port, web_port = _unused_loopback_ports()
    source = _repo(tmp_path)
    (source / "backend/untracked.py").unlink()
    venv_python = _create_test_venv(source)
    _write(
        source / ".env",
        "\n".join(
            (
                f"LOCAL_DATABASE_URL=postgresql://postgres@127.0.0.1:{database_port}/vkpi",
                f"REDIS_URL=redis://127.0.0.1:{redis_port}/0",
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
        web_port=web_port,
        env_out=str(runtime / "controller/candidate-runtime.env"),
        web_profile_out=str(runtime / "controller/candidate-web.sb"),
        verify_profile_out=str(runtime / "controller/candidate-verify.sb"),
        admission_out=str(runtime / "controller/runtime-admission.json"),
    )
    payload = prepare_admission(args)
    return source, candidate, health, args, payload


@pytest.mark.darwin_controller  # 可信控制器 npm 只在 macOS 三个绝对路径;Linux CI 跳过
def test_prepare_admission_filters_provider_secrets_and_pins_phase_a(
    tmp_path: Path,
) -> None:
    source, candidate, health, args, payload = _admission_fixture(tmp_path)
    runtime_env = Path(args.env_out).read_bytes()
    assert payload["schema"] == SCHEMA
    assert payload["provider_credentials_forwarded"] is False
    assert payload["external_network_allowed"] is False
    expected_ports = ",".join(
        str(port)
        for port in sorted(
            (payload["database_port"], payload["redis_port"], payload["web_port"])
        )
    )
    assert payload["runtime_ports"] == expected_ports
    assert payload["runtime_env_sha256"] == hashlib.sha256(runtime_env).hexdigest()
    assert b"fixture-jwt-secret" in runtime_env
    assert b"fixture-health-token" in runtime_env
    expected_database_url = (
        "LOCAL_DATABASE_URL=postgresql://postgres@127.0.0.1:"
        f"{payload['database_port']}/vkpi?gssencmode=disable\n"
    ).encode()
    assert expected_database_url in runtime_env
    assert b"JWT_SECRET_PREVIOUS" not in runtime_env
    assert b"ANTHROPIC" not in runtime_env
    assert b"OPENAI" not in runtime_env
    assert b"must-never-reach-candidate" not in runtime_env
    web_profile = Path(args.web_profile_out).read_text(encoding="utf-8")
    verify_profile = Path(args.verify_profile_out).read_text(encoding="utf-8")
    assert "(deny network*)" in web_profile
    assert "(deny network*)" in verify_profile
    assert (
        f'(allow network-outbound (remote ip "localhost:{args.web_port}"))'
        in verify_profile
    )
    assert (
        f'(allow network-outbound (remote ip "localhost:{payload["database_port"]}"))'
        in verify_profile
    )
    assert (
        f'(allow network-outbound (remote ip "localhost:{payload["redis_port"]}"))'
        not in verify_profile
    )
    assert web_profile.count("(allow network-inbound") == 1
    assert (
        f'(allow network-inbound (local ip "localhost:{args.web_port}"))'
        in web_profile
    )
    assert "(allow network-inbound" not in verify_profile
    assert str(source / ".env") not in web_profile

    loaded = load_admission(
        Path(args.admission_out),
        runtime_root=Path(args.runtime_root),
        candidate=candidate,
        manifest=Path(args.manifest),
        health_env_file=health,
        health_url=f"http://127.0.0.1:{args.web_port}/health",
        base_url=f"http://127.0.0.1:{args.web_port}/",
    )
    assert loaded["candidate_sha256"] == payload["candidate_sha256"]
    assert loaded["_verify_profile"] == verify_profile


def test_candidate_database_url_requires_gss_disabled() -> None:
    assert _database_url_with_gss_disabled(
        "postgresql://127.0.0.1:54329/vkpi?application_name=candidate"
    ) == (
        "postgresql://127.0.0.1:54329/vkpi?"
        "application_name=candidate&gssencmode=disable"
    )
    assert _database_url_with_gss_disabled(
        "postgresql://127.0.0.1:54329/vkpi?gssencmode=DISABLE"
    ).endswith("gssencmode=DISABLE")
    assert _database_url_with_gss_disabled(
        "postgresql://127.0.0.1:54329/vkpi?"
    ).endswith("?gssencmode=disable")
    encoded = (
        "postgresql://127.0.0.1:54329/vkpi?"
        "application_name=hello%20world&options=-c%20search_path%3Dfixture"
    )
    assert _database_url_with_gss_disabled(encoded) == (
        encoded + "&gssencmode=disable"
    )
    with pytest.raises(FreezeError, match="must disable GSS"):
        _database_url_with_gss_disabled(
            "postgresql://127.0.0.1:54329/vkpi?gssencmode=prefer"
        )


@pytest.mark.darwin_controller
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


@pytest.mark.darwin_controller
@pytest.mark.parametrize("profile_attribute", ("web_profile_out", "verify_profile_out"))
def test_candidate_profiles_allow_bash_heredoc_without_broad_tmp_write(
    tmp_path: Path,
    profile_attribute: str,
) -> None:
    _source, candidate, _health, args, _payload = _admission_fixture(tmp_path)
    runtime = Path(args.runtime_root)
    profile = getattr(args, profile_attribute)
    heredoc = subprocess.run(
        [
            "/usr/bin/sandbox-exec",
            "-f",
            profile,
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
            profile,
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


@pytest.mark.darwin_controller
def test_verifier_profile_runs_controller_bound_safe_python_and_npm(
    tmp_path: Path,
    capfd: pytest.CaptureFixture[str],
) -> None:
    reviewed_source = Path.cwd().resolve(strict=True)
    dependency_source = Path(sys.prefix).resolve(strict=True).parent
    candidate = tmp_path / "safe-python-candidate"
    safe_ops = candidate / "scripts/ops"
    safe_ops.mkdir(parents=True)
    (candidate / "tests").mkdir()
    (candidate / "backend/app").mkdir(parents=True)
    for name in (
        "safe_python.sh",
        "safe_python_router.py",
        "freeze_phase_runtime.py",
        "freeze_worktree_contract.py",
    ):
        shutil.copy2(reviewed_source / "scripts/ops" / name, safe_ops / name)
    (safe_ops / "safe_python.sh").chmod(0o755)

    runtime = Path(subprocess.check_output(
        [
            "/usr/bin/mktemp",
            "-d",
            "/private/tmp/vkpi-candidate-browser-runtime.XXXXXX",
        ],
        text=True,
    ).strip()).resolve(strict=True)
    runtime.chmod(0o700)
    for child in ("home", "tmp", "cache", "controller", "receipts"):
        (runtime / child).mkdir(mode=0o700)
    health = tmp_path / "health.env"
    health.write_text("OPS_HEALTH_TOKEN=fixture\n", encoding="utf-8")
    health.chmod(0o600)
    database_port, redis_port, web_port = _unused_loopback_ports()
    try:
        _web_profile, verifier_profile, _ports = _profile_payloads(
            candidate=candidate,
            source=dependency_source,
            runtime=runtime,
            health_env=health,
            web_port=web_port,
            database_port=database_port,
            redis_port=redis_port,
        )
        marker = runtime / "controller/router-marker"
        environment = {
            "HOME": str(runtime / "home"),
            "LANG": "C.UTF-8",
            "PATH": "/usr/local/bin:/opt/homebrew/bin:/usr/bin:/bin",
            "PYTHONDONTWRITEBYTECODE": "1",
            "TMPDIR": str(runtime / "tmp"),
            "VKPI_SAFE_PYTHON_CONTROLLER_RUNTIME_ROOT": str(runtime),
            "VKPI_SAFE_PYTHON_REAL": str(dependency_source / ".venv/bin/python"),
            "XDG_CACHE_HOME": str(runtime / "cache"),
        }
        node_run = _run_controlled_candidate_with_private_output(
            [
                "/usr/bin/sandbox-exec",
                "-p",
                verifier_profile,
                str(_trusted_node()),
                "--version",
            ],
            cwd=candidate,
            env=environment,
            runtime_root=runtime,
            run_nonce="a" * 64,
            timeout=30,
        )
        captured = capfd.readouterr()
        output = runtime / "controller" / f"canonical-gate-output.{'a' * 64}.log"
        output_info = output.lstat()
        assert node_run.returncode == 0
        assert captured.out.startswith("v")
        assert output.read_text(encoding="utf-8") == captured.out
        assert stat.S_ISREG(output_info.st_mode)
        assert not output.is_symlink()
        assert output_info.st_uid == os.geteuid()
        assert output_info.st_nlink == 1
        assert stat.S_IMODE(output_info.st_mode) == 0o600
        assert f'(allow file-read* (subpath "{tmp_path}"))' not in verifier_profile

        safe_run = subprocess.run(
            [
                "/usr/bin/sandbox-exec", "-p", verifier_profile,
                str(safe_ops / "safe_python.sh"), "-", str(marker),
            ],
            input=(
                "import sys\nfrom pathlib import Path\n"
                "Path(sys.argv[1]).write_text('router-ok', encoding='utf-8')\n"
            ),
            cwd=candidate,
            env=environment,
            capture_output=True,
            text=True,
            check=False,
            timeout=120,
        )
        assert safe_run.returncode == 0, safe_run.stderr
        assert marker.read_text(encoding="utf-8") == "router-ok"
        assert not list((runtime / "tmp").glob("vkpi-phase-a-seatbelt.*"))

        # The verifier profile deliberately admits the reviewed wrapper from
        # the frozen candidate, not an identically named live-worktree entry.
        # The deploy token mint must therefore stay bound to candidate bytes.
        live_marker = runtime / "controller/live-router-marker"
        live_wrapper_run = subprocess.run(
            [
                "/usr/bin/sandbox-exec", "-p", verifier_profile,
                str(reviewed_source / "scripts/ops/safe_python.sh"),
                "-", str(live_marker),
            ],
            input=(
                "import sys\nfrom pathlib import Path\n"
                "Path(sys.argv[1]).write_text('must-not-run', encoding='utf-8')\n"
            ),
            cwd=candidate,
            env=environment,
            capture_output=True,
            text=True,
            check=False,
            timeout=30,
        )
        assert live_wrapper_run.returncode != 0
        assert not live_marker.exists()

        npm_run = subprocess.run(
            [
                "/usr/bin/sandbox-exec", "-p", verifier_profile,
                str(_trusted_npm()), "--version",
            ],
            cwd=candidate,
            env=environment,
            capture_output=True,
            text=True,
            check=False,
            timeout=30,
        )
        assert npm_run.returncode == 0, npm_run.stderr
        assert npm_run.stdout.strip()
        assert str(_trusted_npm_package_root()) in verifier_profile
        assert '(subpath "/")' not in verifier_profile
        assert '(subpath "/usr/local")' not in verifier_profile

        direct_npm = tmp_path / "direct-npm"
        direct_npm.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
        direct_npm.chmod(0o755)
        with pytest.raises(RuntimeError, match="noncanonical package layout"):
            _trusted_npm_package_root(direct_npm)

        rejected_marker = runtime / "controller/rejected-marker"
        rejected = subprocess.run(
            [
                "/usr/bin/sandbox-exec", "-p", verifier_profile,
                str(safe_ops / "safe_python.sh"), "-", str(rejected_marker),
            ],
            input=(
                "import sys\nfrom pathlib import Path\n"
                "Path(sys.argv[1]).write_text('must-not-run', encoding='utf-8')\n"
            ),
            cwd=candidate,
            env={
                **environment,
                "VKPI_SAFE_PYTHON_CONTROLLER_RUNTIME_ROOT": str(tmp_path),
            },
            capture_output=True,
            text=True,
            check=False,
            timeout=30,
        )
        assert rejected.returncode != 0
        assert not rejected_marker.exists()
    finally:
        shutil.rmtree(runtime)


@pytest.mark.darwin_controller
def test_web_profile_allows_exact_loopback_listener(tmp_path: Path) -> None:
    source, candidate, _health, args, payload = _admission_fixture(tmp_path)
    listener = subprocess.run(
        [
            "/usr/bin/sandbox-exec",
            "-f",
            args.web_profile_out,
            str(source / ".venv/bin/python"),
            "-I",
            "-S",
            "-B",
            "-c",
            (
                "import socket; "
                "listener = socket.socket(); "
                f"listener.bind(('127.0.0.1', {args.web_port})); "
                "listener.listen(1); "
                f"client = socket.create_connection(('127.0.0.1', {args.web_port})); "
                "accepted, _ = listener.accept(); "
                "accepted.close(); client.close()"
            ),
        ],
        cwd=candidate,
        capture_output=True,
        check=False,
    )
    assert listener.returncode == 0, listener.stderr.decode("utf-8", "replace")
    for port in (payload["database_port"],):
        with socket.socket() as endpoint:
            endpoint.bind(("127.0.0.1", port))
            endpoint.listen(1)
            allowed = subprocess.run(
                [
                    "/usr/bin/sandbox-exec",
                    "-f",
                    args.verify_profile_out,
                    str(source / ".venv/bin/python"),
                    "-I",
                    "-S",
                    "-B",
                    "-c",
                    (
                        "import socket,sys; "
                        "connection=socket.create_connection(('127.0.0.1', int(sys.argv[1]))); "
                        "connection.close()"
                    ),
                    str(port),
                ],
                cwd=candidate,
                capture_output=True,
                check=False,
            )
            assert allowed.returncode == 0, allowed.stderr.decode("utf-8", "replace")

    with socket.socket() as redis_endpoint:
        redis_endpoint.bind(("127.0.0.1", payload["redis_port"]))
        redis_endpoint.listen(1)
        redis_denied = subprocess.run(
            [
                "/usr/bin/sandbox-exec",
                "-f",
                args.verify_profile_out,
                str(source / ".venv/bin/python"),
                "-I",
                "-S",
                "-B",
                "-c",
                (
                    "import socket,sys; "
                    "socket.create_connection(('127.0.0.1', int(sys.argv[1])))"
                ),
                str(payload["redis_port"]),
            ],
            cwd=candidate,
            capture_output=True,
            check=False,
        )
        assert redis_denied.returncode != 0
        assert b"Operation not permitted" in redis_denied.stderr
    for profile, port in (
        (args.web_profile_out, payload["database_port"]),
        (args.web_profile_out, payload["redis_port"]),
        (args.verify_profile_out, args.web_port),
    ):
        denied = subprocess.run(
            [
                "/usr/bin/sandbox-exec",
                "-f",
                profile,
                str(source / ".venv/bin/python"),
                "-I",
                "-S",
                "-B",
                "-c",
                (
                    "import socket; "
                    "listener = socket.socket(); "
                    f"listener.bind(('127.0.0.1', {port})); "
                    "listener.listen(1)"
                ),
            ],
            cwd=candidate,
            capture_output=True,
            check=False,
        )
        assert denied.returncode != 0
        assert b"Operation not permitted" in denied.stderr


@pytest.mark.darwin_controller  # 可信控制器 npm 只在 macOS 三个绝对路径;Linux CI 跳过
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
            health_url=f"http://127.0.0.1:{args.web_port}/health",
            base_url=f"http://127.0.0.1:{args.web_port}/",
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


@pytest.mark.darwin_controller
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
