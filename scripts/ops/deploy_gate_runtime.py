#!/usr/bin/env python3
"""Bind the frozen local deploy gate to one reviewed runtime environment."""

from __future__ import annotations

import json
import os
import stat
import subprocess
import tempfile
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator, Mapping


class DeployGateRuntimeError(RuntimeError):
    """Fail-closed deploy-gate runtime binding error."""


_SCRUBBED_ENV_NAMES = {
    "ADMIN_PASSWORD",
    "APP_ROLE",
    "DATABASE_POOL_URL",
    "DATABASE_URL",
    "DB_RUNTIME_BACKEND",
    "DB_USE_PGBOUNCER",
    "ENVIRONMENT",
    "ENV_FILE",
    "HOME",
    "JWT_SECRET",
    "JWT_SECRET_PREVIOUS",
    "LOCAL_DATABASE_URL",
    "LOCAL_ENV_FILE",
    "LOCAL_REDIS_URL",
    "LOCAL_RUNTIME_FORCE_STACK",
    "OPS_HEALTH_TOKEN",
    "PGDATABASE",
    "PGHOST",
    "PGOPTIONS",
    "PGPASSWORD",
    "PGPORT",
    "PGUSER",
    "POSTGRES_DB",
    "POSTGRES_HOST",
    "POSTGRES_PORT",
    "POSTGRES_USER",
    "PYTHONHOME",
    "PYTHONPATH",
    "PYTHON_BIN",
    "PYTHON_BIN_FALLBACK",
    "REDIS_PASSWORD",
    "REDIS_HOST",
    "REDIS_NAMESPACE",
    "REDIS_PORT",
    "REDIS_URL",
    "RUNTIME_ROOT",
    "RUNTIME_DATA",
    "RUNTIME_ENV_KEEP_DB_URL",
    "RUNTIME_ENV_KEEP_INHERITED_JWT",
    "RUNTIME_ENV_QUIET",
    "RUNTIME_LOGS",
    "RUNTIME_VENDOR",
    "TMPDIR",
    "V2_PRODUCTION_MODE",
    "VIRTUAL_ENV",
    "VKPI_SKIP_DOTENV",
    "VKPI_LOCAL_WEB_PGBOUNCER",
    "XDG_CACHE_HOME",
}

_SCRUBBED_IDENTITY_PREFIXES = ("APP_GIT_", "VITE_APP_")


@dataclass(frozen=True)
class _PathIdentity:
    lexical_kind: int
    lexical_inode: tuple[int, int]
    link_target: str | None
    resolved: str
    resolved_inode: tuple[int, int]
    resolved_mode: int
    resolved_mtime_ns: int
    resolved_size: int
    resolved_uid: int


def _path_identity(path: Path) -> _PathIdentity:
    lexical = path.lstat()
    resolved = path.resolve(strict=True)
    physical = resolved.stat()
    return _PathIdentity(
        lexical_kind=stat.S_IFMT(lexical.st_mode),
        lexical_inode=(lexical.st_dev, lexical.st_ino),
        link_target=os.readlink(path) if path.is_symlink() else None,
        resolved=str(resolved),
        resolved_inode=(physical.st_dev, physical.st_ino),
        resolved_mode=stat.S_IMODE(physical.st_mode),
        resolved_mtime_ns=physical.st_mtime_ns,
        resolved_size=physical.st_size,
        resolved_uid=physical.st_uid,
    )


def _absolute_without_resolving(value: str | os.PathLike[str]) -> Path:
    path = Path(value)
    if not path.is_absolute():
        raise DeployGateRuntimeError(
            "deploy gate Python interpreter must be absolute"
        )
    return Path(os.path.abspath(os.fspath(path)))


def validate_source_venv_python(
    source: Path, requested: str | os.PathLike[str]
) -> Path:
    """Keep the venv invocation path while validating its physical identity."""

    expected = source / ".venv" / "bin" / "python"
    candidate = _absolute_without_resolving(requested)
    if candidate != expected:
        raise DeployGateRuntimeError(
            "deploy gate Python interpreter must equal source .venv/bin/python"
        )
    try:
        resolved_venv = (source / ".venv").resolve(strict=True)
        resolved_python = candidate.resolve(strict=True)
        python_info = resolved_python.stat()
    except (OSError, RuntimeError) as exc:
        raise DeployGateRuntimeError(
            "deploy gate Python interpreter is unavailable"
        ) from exc
    if (
        not resolved_venv.is_dir()
        or not stat.S_ISREG(python_info.st_mode)
        or not os.access(candidate, os.X_OK)
    ):
        raise DeployGateRuntimeError(
            "deploy gate Python interpreter is unavailable"
        )

    probe_source = (
        "import json,sys;"
        "print(json.dumps({'prefix':sys.prefix,'base_prefix':sys.base_prefix}))"
    )
    try:
        completed = subprocess.run(
            [str(candidate), "-I", "-B", "-c", probe_source],
            check=False,
            capture_output=True,
            text=True,
            timeout=10,
            env={
                "HOME": "/tmp",
                "LC_ALL": "C",
                "PATH": os.defpath,
                "PYTHONDONTWRITEBYTECODE": "1",
            },
        )
        payload = json.loads(completed.stdout)
        prefix = Path(str(payload["prefix"])).resolve(strict=True)
        base_prefix = Path(str(payload["base_prefix"])).resolve(strict=True)
    except (
        OSError, RuntimeError, KeyError, TypeError, ValueError,
        subprocess.SubprocessError,
    ) as exc:
        raise DeployGateRuntimeError(
            "deploy gate Python virtualenv probe failed"
        ) from exc
    if completed.returncode != 0 or prefix == base_prefix or prefix != resolved_venv:
        raise DeployGateRuntimeError(
            "deploy gate Python is not the source virtualenv"
        )
    return candidate


def build_deploy_gate_environment(
    inherited: Mapping[str, str], *, source: Path, python_bin: Path,
    runtime_root: Path,
) -> dict[str, str]:
    """Remove caller runtime identity before binding the local source env."""

    env = {
        name: value
        for name, value in inherited.items()
        if name not in _SCRUBBED_ENV_NAMES
        and not name.startswith(_SCRUBBED_IDENTITY_PREFIXES)
    }
    local_env = source / ".env"
    try:
        resolved_env = local_env.resolve(strict=True)
        env_info = resolved_env.stat()
    except (OSError, RuntimeError) as exc:
        raise DeployGateRuntimeError(
            "deploy gate local environment file is unavailable"
        ) from exc
    if (
        not stat.S_ISREG(env_info.st_mode)
        or env_info.st_uid != os.geteuid()
        or stat.S_IMODE(env_info.st_mode) & 0o077
    ):
        raise DeployGateRuntimeError(
            "deploy gate local environment file permissions are unsafe"
        )
    env.update(
        {
            "DB_RUNTIME_BACKEND": "postgres",
            "DB_USE_PGBOUNCER": "0",
            "ENVIRONMENT": "local",
            "ENV_FILE": "",
            "HOME": str(runtime_root / "home"),
            "LOCAL_ENV_FILE": str(local_env),
            "LOCAL_RUNTIME_FORCE_STACK": "1",
            "PYTHONDONTWRITEBYTECODE": "1",
            "PYTHON_BIN": str(python_bin),
            "PYTHON_BIN_FALLBACK": str(python_bin),
            "RUNTIME_ROOT": str(runtime_root),
            "RUNTIME_ENV_KEEP_DB_URL": "0",
            "RUNTIME_ENV_KEEP_INHERITED_JWT": "0",
            "RUNTIME_ENV_QUIET": "1",
            "TMPDIR": str(runtime_root),
            "XDG_CACHE_HOME": str(runtime_root / "cache"),
        }
    )
    return env


@contextmanager
def bound_deploy_gate_runtime(
    inherited: Mapping[str, str], *, source: Path,
    requested_python: str | os.PathLike[str],
) -> Iterator[tuple[Path, dict[str, str]]]:
    """Provide a clean gate env and prove its private inputs stayed stable."""

    try:
        env_before = _path_identity(source / ".env")
        venv_before = _path_identity(source / ".venv")
        python_before = _path_identity(source / ".venv" / "bin" / "python")
    except (OSError, RuntimeError) as exc:
        raise DeployGateRuntimeError(
            "deploy gate runtime inputs are unavailable"
        ) from exc
    python_bin = validate_source_venv_python(source, requested_python)
    with tempfile.TemporaryDirectory(
        prefix="vkpi-deploy-gate-runtime.", dir="/tmp"
    ) as raw_runtime:
        runtime_root = Path(raw_runtime)
        runtime_root.chmod(0o700)
        (runtime_root / "home").mkdir(mode=0o700)
        (runtime_root / "cache").mkdir(mode=0o700)
        environment = build_deploy_gate_environment(
            inherited,
            source=source,
            python_bin=python_bin,
            runtime_root=runtime_root,
        )
        try:
            yield python_bin, environment
        finally:
            try:
                stable = (
                    _path_identity(source / ".env") == env_before
                    and _path_identity(source / ".venv") == venv_before
                    and _path_identity(source / ".venv" / "bin" / "python")
                    == python_before
                )
            except (OSError, RuntimeError):
                stable = False
            if not stable:
                raise DeployGateRuntimeError(
                    "deploy gate runtime inputs changed during verification"
                )
