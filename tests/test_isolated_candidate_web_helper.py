from __future__ import annotations

import json
import os
import shutil
import stat
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
HELPER = ROOT / "scripts" / "ops" / "run_isolated_candidate_web.sh"


def _write(path: Path, value: str, *, mode: int = 0o600) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(value, encoding="utf-8")
    path.chmod(mode)


def test_isolated_candidate_web_scrubs_providers_and_uses_private_cwd(
    tmp_path: Path,
) -> None:
    project = tmp_path / "source"
    candidate = tmp_path / "candidate"
    report = tmp_path / "captured.json"
    runtime_parent = Path(
        tempfile.mkdtemp(prefix="vkpi-candidate-browser-runtime.", dir="/tmp")
    )
    runtime = runtime_parent / "runtime"
    try:
        reviewed_env = tmp_path / "reviewed-local.env"
        _write(
            reviewed_env,
            "\n".join(
                (
                    "LOCAL_DATABASE_URL=postgresql://127.0.0.1:54329/vkpi_test",
                    "REDIS_URL=redis://127.0.0.1:6380/0",
                    "ANTHROPIC_API_KEY=fallback-must-not-load",
                    "",
                )
            ),
        )
        project.mkdir(parents=True)
        (project / ".env").symlink_to(reviewed_env)
        _write(
            project / ".venv" / "bin" / "python",
            f"#!{sys.executable}\n" + """
import json
import os
import sys
from pathlib import Path

if sys.argv[1:4] == ["-I", "-B", "-"]:
    os.execv(sys.executable, [sys.executable, *sys.argv[1:]])

names = (
    "ANTHROPIC_API_KEY", "APIFY_API_TOKEN", "APIFY_TOKEN",
    "GEMINI_API_KEY", "GOOGLE_API_KEY", "OPENAI_API_KEY",
    "R2_ACCESS_KEY_ID", "R2_SECRET_ACCESS_KEY", "RESEND_API_KEY",
    "SENTRY_DSN", "YTDLP_PROXY", "HTTP_PROXY", "HTTPS_PROXY",
    "ALL_PROXY", "http_proxy", "https_proxy", "all_proxy",
)
Path(os.environ["CAPTURE_REPORT"]).write_text(json.dumps({
    "argv": __import__("sys").argv[1:],
    "cwd": os.getcwd(),
    "database_url": os.environ.get("DATABASE_URL"),
    "jwt_secret": os.environ.get("JWT_SECRET"),
    "provider_values": {name: os.environ.get(name) for name in names},
    "no_proxy": os.environ.get("NO_PROXY"),
    "skip_dotenv": os.environ.get("VKPI_SKIP_DOTENV"),
    "async_enabled": os.environ.get("VKPI_ASYNC_ENABLED"),
    "media_storage": os.environ.get("VKPI_MEDIA_CACHE_STORAGE"),
    "fence": os.environ.get("VKPI_RELEASE_VALIDATION_FENCE_PATH"),
    "app_role": os.environ.get("APP_ROLE"),
    "local_env_exported": "LOCAL_ENV_FILE" in os.environ,
}, sort_keys=True), encoding="utf-8")
""",
            mode=0o700,
        )
        _write(
            candidate / "scripts" / "runtime_env.sh",
            """#!/usr/bin/env bash
LOCAL_DATABASE_URL="${LOCAL_DATABASE_URL:-postgresql://127.0.0.1:54329/wrong_default}"
LOCAL_REDIS_URL="${LOCAL_REDIS_URL:-redis://127.0.0.1:6380/9}"
export DATABASE_URL="$LOCAL_DATABASE_URL"
export REDIS_URL="$LOCAL_REDIS_URL"
export JWT_SECRET='local-test-jwt'
export ANTHROPIC_API_KEY='anthropic-secret'
export APIFY_API_TOKEN='apify-api-secret'
export APIFY_TOKEN='apify-secret'
export GEMINI_API_KEY='gemini-secret'
export GOOGLE_API_KEY='google-secret'
export OPENAI_API_KEY='openai-secret'
export R2_ACCESS_KEY_ID='r2-id'
export R2_SECRET_ACCESS_KEY='r2-secret'
export RESEND_API_KEY='resend-secret'
export SENTRY_DSN='https://sentry.invalid/1'
export YTDLP_PROXY='http://proxy.invalid:8080'
export HTTP_PROXY='http://proxy.invalid:8080'
export HTTPS_PROXY='http://proxy.invalid:8080'
export ALL_PROXY='socks5://proxy.invalid:1080'
export http_proxy="$HTTP_PROXY"
export https_proxy="$HTTPS_PROXY"
export all_proxy="$ALL_PROXY"
""",
            mode=0o700,
        )
        _write(candidate / "deploy" / "gunicorn_config.py", "workers = 1\n")

        env = {
            "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
            "HOME": str(runtime_parent / "home"),
            "XDG_CACHE_HOME": str(runtime_parent / "cache"),
            "TMPDIR": str(runtime_parent / "tmp"),
            "PROJECT_ROOT": str(project),
            "CANDIDATE_ROOT": str(candidate),
            "CANDIDATE_RUNTIME": str(runtime),
            "CANDIDATE_LOCAL_ENV_FILE": str(project / ".env"),
            "CANDIDATE_PORT": "18129",
            "APP_GIT_SHA": "a" * 40,
            "APP_GIT_BRANCH": "codex/test",
            "APP_BUILD_TIME": "2026-08-04T00:00:00Z",
            "CAPTURE_REPORT": str(report),
        }
        completed = subprocess.run(
            ["/bin/bash", str(HELPER)],
            cwd=project,
            env=env,
            capture_output=True,
            text=True,
            check=False,
        )
        assert completed.returncode == 0, completed.stderr

        captured = json.loads(report.read_text(encoding="utf-8"))
        assert Path(captured["cwd"]).resolve() == runtime.resolve()
        assert captured["database_url"] == "postgresql://127.0.0.1:54329/vkpi_test"
        assert captured["jwt_secret"] == "local-test-jwt"
        assert set(captured["provider_values"].values()) == {None}
        assert captured["no_proxy"] == "127.0.0.1,localhost,::1"
        assert captured["skip_dotenv"] == "1"
        assert captured["async_enabled"] == "0"
        assert captured["media_storage"] == "local"
        assert captured["app_role"] == "admin-web"
        assert captured["local_env_exported"] is False
        assert not (runtime / "local.env").exists()
        assert captured["argv"][:3] == ["-B", "-m", "gunicorn"]

        fence = Path(captured["fence"])
        assert fence == runtime / "release-validation.fence"
        assert fence.read_text(encoding="utf-8") == "vkpi-release-validation/v1\n"
        assert stat.S_IMODE(fence.stat().st_mode) == 0o444
        assert not (runtime / ".env").exists()
    finally:
        shutil.rmtree(runtime_parent, ignore_errors=True)


@pytest.mark.parametrize(
    "unsafe_database_url",
    (
        "postgresql://127.0.0.1:54329/vkpi_test?host=example.com",
        "postgresql://127.0.0.1:54329/vkpi_test?host=/tmp/other&port=6543",
        "postgresql://127.0.0.1:54329/vkpi_test?dbname=wrong",
        "postgresql://u@198.51.100.9,\t@127.0.0.1:54329/vkpi_test",
        "postgresql://u@198.51.100.9,@127.0.0.1:54329/vkpi_test",
    ),
)
def test_isolated_candidate_web_rejects_query_identity_overrides(
    tmp_path: Path,
    unsafe_database_url: str,
) -> None:
    project = tmp_path / "source"
    candidate = tmp_path / "candidate"
    runtime_parent = Path(
        tempfile.mkdtemp(prefix="vkpi-candidate-browser-runtime.", dir="/tmp")
    )
    runtime = runtime_parent / "runtime"
    try:
        reviewed_env = tmp_path / "reviewed-local.env"
        _write(
            reviewed_env,
            f"LOCAL_DATABASE_URL={unsafe_database_url}\n"
            "REDIS_URL=redis://127.0.0.1:6380/0\n",
        )
        project.mkdir(parents=True)
        (project / ".env").symlink_to(reviewed_env)
        _write(
            project / ".venv" / "bin" / "python",
            f"#!/bin/sh\nexec {shutil.which('python') or sys.executable!s} \"$@\"\n",
            mode=0o700,
        )
        _write(candidate / "scripts" / "runtime_env.sh", "exit 99\n", mode=0o700)
        _write(candidate / "deploy" / "gunicorn_config.py", "workers = 1\n")

        completed = subprocess.run(
            ["/bin/bash", str(HELPER)],
            cwd=project,
            env={
                "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
                "HOME": str(runtime_parent / "home"),
                "XDG_CACHE_HOME": str(runtime_parent / "cache"),
                "TMPDIR": str(runtime_parent / "tmp"),
                "PROJECT_ROOT": str(project),
                "CANDIDATE_ROOT": str(candidate),
                "CANDIDATE_RUNTIME": str(runtime),
                "CANDIDATE_LOCAL_ENV_FILE": str(project / ".env"),
                "CANDIDATE_PORT": "18129",
                "APP_GIT_SHA": "a" * 40,
                "APP_GIT_BRANCH": "codex/test",
                "APP_BUILD_TIME": "2026-08-04T00:00:00Z",
            },
            capture_output=True,
            text=True,
            check=False,
        )
        assert completed.returncode != 0
        assert (
            "candidate local database identity is unsafe" in completed.stderr
            or "candidate local environment has invalid connection identity"
            in completed.stderr
            or "candidate local database identity disagrees with libpq"
            in completed.stderr
        )
        assert unsafe_database_url not in completed.stderr
        assert not (runtime / "local.env").exists()
        assert not (runtime / "local-identity.env").exists()
    finally:
        shutil.rmtree(runtime_parent, ignore_errors=True)


@pytest.mark.parametrize(
    "forbidden_line",
    (
        "ENV_FILE=/tmp/override.env",
        "PGHOSTADDR=198.51.100.10",
        "PGSERVICE=unreviewed",
    ),
)
def test_isolated_candidate_web_rejects_ambient_connection_controls(
    tmp_path: Path,
    forbidden_line: str,
) -> None:
    project = tmp_path / "source"
    candidate = tmp_path / "candidate"
    runtime_parent = Path(
        tempfile.mkdtemp(prefix="vkpi-candidate-browser-runtime.", dir="/tmp")
    )
    runtime = runtime_parent / "runtime"
    try:
        reviewed_env = tmp_path / "reviewed-local.env"
        _write(
            reviewed_env,
            "LOCAL_DATABASE_URL=postgresql://127.0.0.1:54329/vkpi_test\n"
            "REDIS_URL=redis://127.0.0.1:6380/0\n"
            f"{forbidden_line}\n",
        )
        project.mkdir(parents=True)
        (project / ".env").symlink_to(reviewed_env)
        _write(
            project / ".venv" / "bin" / "python",
            f"#!/bin/sh\nexec {shutil.which('python') or sys.executable!s} \"$@\"\n",
            mode=0o700,
        )
        _write(candidate / "scripts" / "runtime_env.sh", "exit 99\n", mode=0o700)
        _write(candidate / "deploy" / "gunicorn_config.py", "workers = 1\n")
        completed = subprocess.run(
            ["/bin/bash", str(HELPER)],
            cwd=project,
            env={
                "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
                "HOME": str(runtime_parent / "home"),
                "XDG_CACHE_HOME": str(runtime_parent / "cache"),
                "TMPDIR": str(runtime_parent / "tmp"),
                "PROJECT_ROOT": str(project),
                "CANDIDATE_ROOT": str(candidate),
                "CANDIDATE_RUNTIME": str(runtime),
                "CANDIDATE_LOCAL_ENV_FILE": str(project / ".env"),
                "CANDIDATE_PORT": "18129",
                "APP_GIT_SHA": "a" * 40,
                "APP_GIT_BRANCH": "codex/test",
                "APP_BUILD_TIME": "2026-08-04T00:00:00Z",
            },
            capture_output=True,
            text=True,
            check=False,
        )
        assert completed.returncode != 0
        assert "forbidden connection controls" in completed.stderr
        assert forbidden_line not in completed.stderr
        assert not (runtime / "local.env").exists()
        assert not (runtime / "local-identity.env").exists()
    finally:
        shutil.rmtree(runtime_parent, ignore_errors=True)


@pytest.mark.parametrize(
    "override_path",
    (Path(".env.local"), Path("runtime/local_operator_env.sh")),
)
def test_isolated_candidate_web_rejects_candidate_side_environment_overrides(
    tmp_path: Path,
    override_path: Path,
) -> None:
    project = tmp_path / "source"
    candidate = tmp_path / "candidate"
    runtime_parent = Path(
        tempfile.mkdtemp(prefix="vkpi-candidate-browser-runtime.", dir="/tmp")
    )
    runtime = runtime_parent / "runtime"
    try:
        reviewed_env = tmp_path / "reviewed-local.env"
        _write(
            reviewed_env,
            "LOCAL_DATABASE_URL=postgresql://127.0.0.1:54329/vkpi_test\n"
            "REDIS_URL=redis://127.0.0.1:6380/0\n",
        )
        project.mkdir(parents=True)
        (project / ".env").symlink_to(reviewed_env)
        _write(
            project / ".venv" / "bin" / "python",
            f"#!/bin/sh\nexec {shutil.which('python') or sys.executable!s} \"$@\"\n",
            mode=0o700,
        )
        _write(
            candidate / "scripts" / "runtime_env.sh",
            "exit 99\n",
            mode=0o700,
        )
        _write(candidate / "deploy" / "gunicorn_config.py", "workers = 1\n")
        _write(candidate / override_path, "export PGHOST=example.com\n")
        completed = subprocess.run(
            ["/bin/bash", str(HELPER)],
            cwd=project,
            env={
                "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
                "HOME": str(runtime_parent / "home"),
                "XDG_CACHE_HOME": str(runtime_parent / "cache"),
                "TMPDIR": str(runtime_parent / "tmp"),
                "PROJECT_ROOT": str(project),
                "CANDIDATE_ROOT": str(candidate),
                "CANDIDATE_RUNTIME": str(runtime),
                "CANDIDATE_LOCAL_ENV_FILE": str(project / ".env"),
                "CANDIDATE_PORT": "18129",
                "APP_GIT_SHA": "a" * 40,
                "APP_GIT_BRANCH": "codex/test",
                "APP_BUILD_TIME": "2026-08-04T00:00:00Z",
            },
            capture_output=True,
            text=True,
            check=False,
        )
        assert completed.returncode != 0
        assert "unreviewed runtime environment override" in completed.stderr
        assert not (runtime / "local.env").exists()
        assert not (runtime / "local-identity.env").exists()
    finally:
        shutil.rmtree(runtime_parent, ignore_errors=True)
