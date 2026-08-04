from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
PROBE_KEYS = (
    "ENVIRONMENT",
    "DATABASE_URL",
    "REDIS_URL",
    "JWT_SECRET",
    "ADMIN_PASSWORD",
    "DB_RUNTIME_BACKEND",
)
PROBE_CODE = (
    "import json, os; "
    f"print(json.dumps({{k: os.environ.get(k, '') for k in {PROBE_KEYS!r}}}))"
)


def _run_probe(
    tmp_path: Path,
    env_text: str,
    *,
    shell: bool = False,
    override_env_text: str | None = None,
    **overrides: str,
) -> subprocess.CompletedProcess[str]:
    tmp_path.mkdir(parents=True, exist_ok=True)
    env_file = tmp_path / "runtime.env"
    env_file.write_text(env_text, encoding="utf-8")
    env = {
        "PATH": os.environ.get("PATH", ""),
        "PYTHONPATH": str(ROOT / "scripts"),
        "LOCAL_ENV_FILE": str(env_file),
        "RUNTIME_ROOT": str(tmp_path / "runtime"),
        "RUNTIME_ENV_QUIET": "1",
        **overrides,
    }
    if override_env_text is not None:
        override_file = tmp_path / "runtime.override.env"
        override_file.write_text(override_env_text, encoding="utf-8")
        env["ENV_FILE"] = str(override_file)
    if shell:
        command = [
            "bash",
            "-c",
            (
                'source "$1" || exit $?; '
                "export ENVIRONMENT DATABASE_URL REDIS_URL JWT_SECRET ADMIN_PASSWORD DB_RUNTIME_BACKEND; "
                'exec "$2" -c "$3"'
            ),
            "runtime-env-probe",
            str(ROOT / "scripts/runtime_env.sh"),
            sys.executable,
            PROBE_CODE,
        ]
    else:
        command = [
            sys.executable,
            "-c",
            "from runtime_env import apply_runtime_env; apply_runtime_env(); " + PROBE_CODE,
        ]
    return subprocess.run(
        command,
        cwd=ROOT,
        env=env,
        check=False,
        capture_output=True,
        text=True,
    )


def _probe(
    tmp_path: Path,
    env_text: str,
    *,
    shell: bool = False,
    override_env_text: str | None = None,
    **overrides: str,
) -> dict[str, str]:
    completed = _run_probe(
        tmp_path,
        env_text,
        shell=shell,
        override_env_text=override_env_text,
        **overrides,
    )
    completed.check_returncode()
    return json.loads(completed.stdout)


def _routing_only(observed: dict[str, str]) -> dict[str, str]:
    return {
        key: observed[key]
        for key in ("ENVIRONMENT", "DATABASE_URL", "REDIS_URL")
    }


def test_python_runtime_env_forces_the_same_local_stack_as_shell(tmp_path: Path) -> None:
    observed = _probe(
        tmp_path,
        "DATABASE_URL=postgresql://postgres@127.0.0.1:5432/stale\n"
        "REDIS_URL=redis://127.0.0.1:6379/0\n",
    )

    assert _routing_only(observed) == {
        "ENVIRONMENT": "local",
        "DATABASE_URL": "postgresql://postgres@127.0.0.1:54329/viltrox2",
        "REDIS_URL": "redis://127.0.0.1:6380/0",
    }


def test_dotenv_cannot_promote_an_unset_process_environment_to_production(tmp_path: Path) -> None:
    observed = _probe(
        tmp_path,
        "ENVIRONMENT=production\n"
        "DATABASE_URL=postgresql://app@db.internal:5432/prod\n"
        "REDIS_URL=redis://cache.internal:6379/0\n",
    )

    assert _routing_only(observed) == {
        "ENVIRONMENT": "local",
        "DATABASE_URL": "postgresql://postgres@127.0.0.1:54329/viltrox2",
        "REDIS_URL": "redis://127.0.0.1:6380/0",
    }


def test_python_runtime_env_honours_explicit_local_stack_urls(tmp_path: Path) -> None:
    observed = _probe(
        tmp_path,
        "REDIS_URL=redis://127.0.0.1:6379/0\n",
        ENVIRONMENT="local",
        LOCAL_DATABASE_URL="postgresql://postgres@127.0.0.1:55432/isolated",
        LOCAL_REDIS_URL="redis://127.0.0.1:6480/3",
    )

    assert observed["DATABASE_URL"] == "postgresql://postgres@127.0.0.1:55432/isolated"
    assert observed["REDIS_URL"] == "redis://127.0.0.1:6480/3"


def test_python_runtime_env_preserves_reviewed_non_local_urls(tmp_path: Path) -> None:
    observed = _probe(
        tmp_path,
        "DATABASE_URL=postgresql://app@db.internal:5432/prod\n"
        "REDIS_URL=redis://cache.internal:6379/0\n"
        "JWT_SECRET=reviewed-production-jwt-secret\n"
        "ADMIN_PASSWORD=reviewed-production-admin-password\n",
        ENVIRONMENT="production",
    )

    assert observed["DATABASE_URL"] == "postgresql://app@db.internal:5432/prod"
    assert observed["REDIS_URL"] == "redis://cache.internal:6379/0"


def test_python_runtime_env_allows_explicit_local_keep_override(tmp_path: Path) -> None:
    observed = _probe(
        tmp_path,
        "DATABASE_URL=postgresql://postgres@127.0.0.1:5432/legacy\n"
        "REDIS_URL=redis://127.0.0.1:6379/0\n",
        ENVIRONMENT="local",
        RUNTIME_ENV_KEEP_DB_URL="1",
    )

    assert observed["DATABASE_URL"] == "postgresql://postgres@127.0.0.1:5432/legacy"
    assert observed["REDIS_URL"] == "redis://127.0.0.1:6379/0"


@pytest.mark.parametrize(
    ("env_text", "overrides"),
    [
        (
            "ENVIRONMENT=production\n"
            "DATABASE_URL=postgresql://app@db.internal:5432/stale\n"
            "REDIS_URL=redis://cache.internal:6379/0\n"
            "LOCAL_DATABASE_URL=postgresql://app@db.internal:5432/stale-local\n"
            "LOCAL_REDIS_URL=redis://cache.internal:6380/0\n"
            'JWT_SECRET="synthetic-local-secret"\n',
            {},
        ),
        (
            'DATABASE_URL="postgresql://app@db.internal:5432/prod"\n'
            "REDIS_URL='redis://cache.internal:6379/0'\n"
            'JWT_SECRET="synthetic-production-secret"\n'
            'ADMIN_PASSWORD="synthetic-production-password"\n',
            {"ENVIRONMENT": "production"},
        ),
        (
            "DATABASE_URL=postgresql://postgres@127.0.0.1:5432/legacy\n"
            "REDIS_URL=redis://127.0.0.1:6379/0\n"
            "RUNTIME_ENV_KEEP_DB_URL=1\n",
            {"ENVIRONMENT": "local"},
        ),
        (
            "DATABASE_URL=postgresql://app@db.internal:5432/base\n"
            "REDIS_URL=redis://cache.internal:6379/0\n"
            "JWT_SECRET=base-secret\n"
            "ADMIN_PASSWORD=base-admin-password\n",
            {
                "ENVIRONMENT": "production",
                "DATABASE_URL": "",
                "REDIS_URL": "",
                "LOCAL_DATABASE_URL": "",
                "LOCAL_REDIS_URL": "",
                "JWT_SECRET": "",
                "DB_RUNTIME_BACKEND": "",
            },
        ),
    ],
)
def test_python_and_shell_runtime_env_match_for_dirty_and_explicit_modes(
    tmp_path: Path,
    env_text: str,
    overrides: dict[str, str],
) -> None:
    python_observed = _probe(tmp_path / "python", env_text, **overrides)
    shell_observed = _probe(tmp_path / "shell", env_text, shell=True, **overrides)

    assert python_observed == shell_observed


def test_python_and_shell_honor_the_same_explicit_override_file(tmp_path: Path) -> None:
    base = (
        "DATABASE_URL=postgresql://app@db.internal:5432/base\n"
        "REDIS_URL=redis://cache.internal:6379/0\n"
        "JWT_SECRET=base-secret\n"
        "ADMIN_PASSWORD=base-admin-password\n"
    )
    override = (
        'DATABASE_URL="postgresql://reviewed@db.internal:5432/prod"\n'
        "REDIS_URL='redis://reviewed-cache.internal:6379/1'\n"
        "JWT_SECRET=reviewed-secret\n"
        "ADMIN_PASSWORD=reviewed-admin-password\n"
    )
    options = {
        "ENVIRONMENT": "production",
        "override_env_text": override,
    }

    python_observed = _probe(tmp_path / "python", base, **options)
    shell_observed = _probe(tmp_path / "shell", base, shell=True, **options)

    assert python_observed == shell_observed
    assert python_observed["DATABASE_URL"] == "postgresql://reviewed@db.internal:5432/prod"
    assert python_observed["REDIS_URL"] == "redis://reviewed-cache.internal:6379/1"


@pytest.mark.parametrize("shell", [False, True])
def test_dirty_base_env_cannot_replace_the_default_local_routes(
    tmp_path: Path,
    shell: bool,
) -> None:
    observed = _probe(
        tmp_path,
        "ENVIRONMENT=production\n"
        "DATABASE_URL=postgresql://app@db.internal:5432/stale\n"
        "REDIS_URL=redis://cache.internal:6379/0\n"
        "LOCAL_DATABASE_URL=postgresql://app@db.internal:5432/stale-local\n"
        "LOCAL_REDIS_URL=redis://cache.internal:6380/0\n",
        shell=shell,
    )

    assert _routing_only(observed) == {
        "ENVIRONMENT": "local",
        "DATABASE_URL": "postgresql://postgres@127.0.0.1:54329/viltrox2",
        "REDIS_URL": "redis://127.0.0.1:6380/0",
    }


@pytest.mark.parametrize("shell", [False, True])
def test_base_env_cannot_inject_an_explicit_override_file(
    tmp_path: Path,
    shell: bool,
) -> None:
    injected = tmp_path / "injected.env"
    injected.write_text(
        "DATABASE_URL=postgresql://injected@db.internal:5432/prod\n"
        "REDIS_URL=redis://injected-cache.internal:6379/9\n",
        encoding="utf-8",
    )
    observed = _probe(
        tmp_path / ("shell" if shell else "python"),
        f"ENV_FILE={injected}\n"
        "DATABASE_URL=postgresql://reviewed@db.internal:5432/base\n"
        "REDIS_URL=redis://reviewed-cache.internal:6379/1\n"
        "JWT_SECRET=reviewed-production-jwt-secret\n"
        "ADMIN_PASSWORD=reviewed-production-admin-password\n",
        shell=shell,
        ENVIRONMENT="production",
    )

    assert observed["DATABASE_URL"] == "postgresql://reviewed@db.internal:5432/base"
    assert observed["REDIS_URL"] == "redis://reviewed-cache.internal:6379/1"


@pytest.mark.parametrize("shell", [False, True])
@pytest.mark.parametrize(
    ("environment", "env_text"),
    [
        ("production", ""),
        (
            "prod",
            "JWT_SECRET=viltrox2-local-dev-secret-change-me\n"
            "ADMIN_PASSWORD=reviewed-production-admin-password\n",
        ),
        (
            "staging",
            "JWT_SECRET=reviewed-production-jwt-secret\n"
            "ADMIN_PASSWORD=AdminPass123!\n",
        ),
        (
            " Stage ",
            "JWT_SECRET=reviewed-production-jwt-secret\n",
        ),
    ],
)
def test_production_like_runtime_fails_closed_without_reviewed_auth_secrets(
    tmp_path: Path,
    shell: bool,
    environment: str,
    env_text: str,
) -> None:
    completed = _run_probe(
        tmp_path / ("shell" if shell else "python"),
        env_text,
        shell=shell,
        ENVIRONMENT=environment,
    )

    assert completed.returncode != 0
    assert completed.stdout == ""
    assert "viltrox2-local-dev-secret-change-me" not in completed.stderr
    assert "AdminPass123!" not in completed.stderr
    assert "reviewed-production" not in completed.stderr
