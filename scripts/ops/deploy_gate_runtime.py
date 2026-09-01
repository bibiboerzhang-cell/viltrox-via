#!/usr/bin/env python3
"""Bind the frozen local deploy gate to one reviewed runtime environment."""

from __future__ import annotations

import hashlib
import ipaddress
import json
import logging
import os
import shutil
import stat
import subprocess
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator, Mapping
from urllib.parse import urlsplit


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
    "VKPI_HEALTH_ENV_FILE",
    "VKPI_HEALTH_URL",
    "VKPI_LOCAL_BASE_URL",
    "VKPI_LOCAL_WEB_PGBOUNCER",
    "VKPI_VERIFY_ACCEPTANCE_JSON_OUT",
    "VKPI_VERIFY_JSON_OUT",
    "VKPI_SAFE_PYTHON_CONTROLLER_RUNTIME_ROOT",
    "XDG_CACHE_HOME",
}

# Every Vite-prefixed value is a build input.  A release build must not inherit
# an operator's local proxy, experimental-navigation, browser-assist, or API
# target switches.  The frozen build identity is injected explicitly after the
# environment has been scrubbed.
_SCRUBBED_ENV_PREFIXES = ("APP_GIT_", "VITE_")
_ENV_I_ALLOWLIST = frozenset({"PATH", "TERM", "TZ"})
_SENSITIVE_ENV_MARKERS = (
    "API_KEY", "API_TOKEN", "ACCESS_KEY", "SECRET_KEY", "CREDENTIAL",
    "PROXY", "ANTHROPIC", "APIFY", "AWS_", "AZURE_", "CLOUDFLARE",
    "GEMINI", "GOOGLE_", "OPENAI", "YOUTUBE", "VITE_", "SHOPIFY",
    "GOAFFPRO", "RESEND", "SENTRY", "R2_",
)
_SAFE_BUILD_IDENTITY_NAMES = frozenset(
    {"VITE_APP_BUILD_TIME", "VITE_APP_GIT_BRANCH", "VITE_APP_GIT_SHA"}
)


@dataclass(frozen=True)
class _PathIdentity:
    lexical_kind: int
    lexical_inode: tuple[int, int]
    link_target: str | None
    resolved: str
    resolved_inode: tuple[int, int]
    resolved_mode: int
    resolved_mtime_ns: int
    resolved_ctime_ns: int
    resolved_size: int
    resolved_uid: int
    resolved_gid: int
    resolved_nlink: int
    resolved_sha256: str | None


@dataclass(frozen=True)
class StrictGateBinding:
    runtime_root: Path
    health_env_file: Path
    health_url: str
    base_url: str
    verify_json_out: Path
    acceptance_json_out: Path

    def environment(self) -> dict[str, str]:
        return {
            "VKPI_HEALTH_ENV_FILE": str(self.health_env_file),
            "VKPI_HEALTH_URL": self.health_url,
            "VKPI_LOCAL_BASE_URL": self.base_url,
            "VKPI_SAFE_PYTHON_CONTROLLER_RUNTIME_ROOT": str(self.runtime_root),
            "VKPI_VERIFY_JSON_OUT": str(self.verify_json_out),
            "VKPI_VERIFY_ACCEPTANCE_JSON_OUT": str(self.acceptance_json_out),
        }


_PG_CTL_FALLBACKS = (
    "/opt/homebrew/opt/postgresql@16/bin/pg_ctl",
    "/opt/homebrew/bin/pg_ctl",
    "/usr/local/bin/pg_ctl",
    "/usr/lib/postgresql/16/bin/pg_ctl",
)
_log = logging.getLogger("vkpi.deploy_gate_runtime")


def _path_identity(path: Path) -> _PathIdentity:
    lexical = path.lstat()
    resolved = path.resolve(strict=True)
    physical = resolved.stat()
    content_sha256: str | None = None
    if stat.S_ISREG(physical.st_mode):
        digest = hashlib.sha256()
        with resolved.open("rb") as handle:
            opened = os.fstat(handle.fileno())
            if (opened.st_dev, opened.st_ino) != (physical.st_dev, physical.st_ino):
                raise OSError("path identity changed before content hashing")
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
            after = os.fstat(handle.fileno())
        stable_fields = (
            "st_dev", "st_ino", "st_mode", "st_uid", "st_gid", "st_nlink",
            "st_size", "st_mtime_ns", "st_ctime_ns",
        )
        if any(getattr(opened, name) != getattr(after, name) for name in stable_fields):
            raise OSError("path identity changed during content hashing")
        physical = after
        content_sha256 = digest.hexdigest()
    return _PathIdentity(
        lexical_kind=stat.S_IFMT(lexical.st_mode),
        lexical_inode=(lexical.st_dev, lexical.st_ino),
        link_target=os.readlink(path) if path.is_symlink() else None,
        resolved=str(resolved),
        resolved_inode=(physical.st_dev, physical.st_ino),
        resolved_mode=stat.S_IMODE(physical.st_mode),
        resolved_mtime_ns=physical.st_mtime_ns,
        resolved_ctime_ns=physical.st_ctime_ns,
        resolved_size=physical.st_size,
        resolved_uid=physical.st_uid,
        resolved_gid=physical.st_gid,
        resolved_nlink=physical.st_nlink,
        resolved_sha256=content_sha256,
    )


def _absolute_without_resolving(value: str | os.PathLike[str]) -> Path:
    path = Path(value)
    if not path.is_absolute():
        raise DeployGateRuntimeError(
            "deploy gate Python interpreter must be absolute"
        )
    return Path(os.path.abspath(os.fspath(path)))


def validate_runtime_root(value: str | os.PathLike[str]) -> Path:
    root = _absolute_without_resolving(value)
    try:
        lexical = root.lstat()
        resolved = root.resolve(strict=True)
        physical = resolved.stat()
    except (OSError, RuntimeError) as exc:
        raise DeployGateRuntimeError("deploy gate runtime root is unavailable") from exc
    if (
        not stat.S_ISDIR(lexical.st_mode)
        or stat.S_ISLNK(lexical.st_mode)
        or not stat.S_ISDIR(physical.st_mode)
        or lexical.st_uid != os.geteuid()
        or physical.st_uid != os.geteuid()
        or stat.S_IMODE(lexical.st_mode) != 0o700
        or stat.S_IMODE(physical.st_mode) != 0o700
    ):
        raise DeployGateRuntimeError(
            "deploy gate runtime root must be an owned non-symlink 0700 directory"
        )
    return root


def _loopback_origin(raw: str, *, label: str) -> tuple[str, int]:
    try:
        parsed = urlsplit(str(raw))
        port = parsed.port
    except (ValueError, TypeError) as exc:
        raise DeployGateRuntimeError(f"deploy gate {label} is invalid") from exc
    try:
        address = ipaddress.ip_address(parsed.hostname or "")
    except ValueError as exc:
        raise DeployGateRuntimeError(
            f"deploy gate {label} must use a numeric loopback address"
        ) from exc
    if (
        parsed.scheme != "http"
        or parsed.username is not None
        or parsed.password is not None
        or not address.is_loopback
        or port is None
        or port == 8102
        or not 1 <= port <= 65535
        or parsed.query
        or parsed.fragment
    ):
        raise DeployGateRuntimeError(
            f"deploy gate {label} must be loopback http on an explicit non-8102 port"
        )
    return address.compressed, port


def validate_health_env_file(value: str | os.PathLike[str]) -> Path:
    """Bind the gate to one protected token file without reading its secret."""

    path = Path(value)
    if not path.is_absolute():
        raise DeployGateRuntimeError(
            "deploy gate health environment file must be absolute"
        )
    path = Path(os.path.abspath(os.fspath(path)))
    try:
        lexical = path.lstat()
        resolved = path.resolve(strict=True)
        physical = resolved.stat()
    except (OSError, RuntimeError) as exc:
        raise DeployGateRuntimeError(
            "deploy gate health environment file is unavailable"
        ) from exc
    effective_uid = os.geteuid()
    effective_gid = os.getegid()
    trusted_groups = {effective_gid, *os.getgroups()}
    mode = stat.S_IMODE(physical.st_mode)
    if physical.st_uid == effective_uid:
        access_shape_is_trusted = mode in {0o400, 0o600}
    else:
        access_shape_is_trusted = (
            physical.st_uid == 0
            and mode in {0o440, 0o640}
            and physical.st_gid in trusted_groups
            and bool(mode & stat.S_IRGRP)
        )
    if (
        not stat.S_ISREG(lexical.st_mode)
        or stat.S_ISLNK(lexical.st_mode)
        or not stat.S_ISREG(physical.st_mode)
        or (lexical.st_dev, lexical.st_ino)
        != (physical.st_dev, physical.st_ino)
        or not access_shape_is_trusted
        or physical.st_nlink != 1
        or physical.st_size > 64 * 1024
    ):
        raise DeployGateRuntimeError(
            "deploy gate health environment file must be protected, "
            "single-link, and regular"
        )
    return resolved


def _bound_output_path(root: Path, raw: str | os.PathLike[str], *, label: str) -> Path:
    target = _absolute_without_resolving(raw)
    try:
        target.relative_to(root)
    except ValueError as exc:
        raise DeployGateRuntimeError(
            f"deploy gate {label} must be inside runtime root"
        ) from exc
    if target == root:
        raise DeployGateRuntimeError(f"deploy gate {label} must name a file")
    current = root
    for part in target.relative_to(root).parts[:-1]:
        current /= part
        if current.exists() or current.is_symlink():
            info = current.lstat()
            if (
                not stat.S_ISDIR(info.st_mode)
                or stat.S_ISLNK(info.st_mode)
                or info.st_uid != os.geteuid()
            ):
                raise DeployGateRuntimeError(
                    f"deploy gate {label} parent is unsafe"
                )
        else:
            current.mkdir(mode=0o700)
    if target.exists() or target.is_symlink():
        info = target.lstat()
        if stat.S_ISLNK(info.st_mode) or not stat.S_ISREG(info.st_mode):
            raise DeployGateRuntimeError(f"deploy gate {label} target is unsafe")
        target.unlink()
    return target


def validate_strict_gate_binding(
    *,
    runtime_root: str | os.PathLike[str],
    health_env_file: str | os.PathLike[str],
    health_url: str,
    base_url: str,
    verify_json_out: str | os.PathLike[str],
    acceptance_json_out: str | os.PathLike[str],
) -> StrictGateBinding:
    root = validate_runtime_root(runtime_root)
    protected_health_env = validate_health_env_file(health_env_file)
    if protected_health_env == root or root in protected_health_env.parents:
        raise DeployGateRuntimeError(
            "deploy gate health environment file must be outside runtime root"
        )
    health_origin = _loopback_origin(health_url, label="health URL")
    base_origin = _loopback_origin(base_url, label="base URL")
    health = urlsplit(health_url)
    base = urlsplit(base_url)
    if health_origin != base_origin:
        raise DeployGateRuntimeError("deploy gate health/base URLs must share one origin")
    if health.path != "/health" or base.path not in {"", "/"}:
        raise DeployGateRuntimeError(
            "deploy gate health URL must end at /health and base URL at /"
        )
    verify_out = _bound_output_path(root, verify_json_out, label="verify JSON output")
    acceptance_out = _bound_output_path(
        root, acceptance_json_out, label="acceptance JSON output"
    )
    if verify_out == acceptance_out:
        raise DeployGateRuntimeError("deploy gate output paths must be distinct")
    return StrictGateBinding(
        runtime_root=root,
        health_env_file=protected_health_env,
        health_url=str(health_url),
        base_url=str(base_url),
        verify_json_out=verify_out,
        acceptance_json_out=acceptance_out,
    )


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
    runtime_root: Path, allow_test_hooks: bool = False,
) -> dict[str, str]:
    """Remove caller runtime identity before binding the local source env."""

    # This is intentionally env-i semantics.  A deny-list cannot keep pace with
    # provider SDKs, cloud CLIs, Vite inputs, or operator-specific proxy names.
    env = {name: inherited[name] for name in _ENV_I_ALLOWLIST if name in inherited}
    if allow_test_hooks:
        env.update({name: value for name, value in inherited.items() if name.startswith("VKPI_TEST_")})
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
            "VKPI_LLM_GATEWAY_FORCE_OFFLINE": "1",
            "VKPI_EXTERNAL_AI_DISABLED": "1",
            "VKPI_AUTOMATED_WRITES_DISABLED": "1",
            "PYTHONDONTWRITEBYTECODE": "1",
            "PYTHON_BIN": str(python_bin),
            "PYTHON_BIN_FALLBACK": str(python_bin),
            "RUNTIME_ROOT": str(runtime_root),
            "RUNTIME_ENV_KEEP_DB_URL": "0",
            "RUNTIME_ENV_KEEP_INHERITED_JWT": "0",
            "RUNTIME_ENV_QUIET": "1",
            "TMPDIR": str(runtime_root / "tmp"),
            "XDG_CACHE_HOME": str(runtime_root / "cache"),
        }
    )
    return env


def assert_provider_free_environment(environment: Mapping[str, str]) -> None:
    """Tripwire: canonical verification must never inherit provider/cloud lanes."""

    unsafe = sorted(
        name for name in environment
        if name not in _SAFE_BUILD_IDENTITY_NAMES
        and any(marker in name.upper() for marker in _SENSITIVE_ENV_MARKERS)
    )
    if unsafe:
        raise DeployGateRuntimeError(
            "deploy gate provider/cloud environment tripwire fired: "
            + ", ".join(unsafe)
        )


def build_provider_free_subprocess_environment(
    inherited: Mapping[str, str], *, home: Path, tmpdir: Path,
) -> dict[str, str]:
    """Small shared env-i base for Phase A build/test subprocesses."""

    environment = {
        "HOME": str(home), "LANG": "C", "LC_ALL": "C",
        "PATH": "/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin",
        "TMPDIR": str(tmpdir),
        "PYTHONDONTWRITEBYTECODE": "1", "VKPI_SKIP_DOTENV": "1",
        "VKPI_LLM_GATEWAY_FORCE_OFFLINE": "1", "VKPI_EXTERNAL_AI_DISABLED": "1",
        "VKPI_AUTOMATED_WRITES_DISABLED": "1",
    }
    assert_provider_free_environment(environment)
    return environment


@contextmanager
def bound_deploy_gate_runtime(
    inherited: Mapping[str, str], *, source: Path,
    requested_python: str | os.PathLike[str],
    runtime_root: str | os.PathLike[str],
    health_env_file: str | os.PathLike[str],
    health_url: str,
    base_url: str,
    verify_json_out: str | os.PathLike[str],
    acceptance_json_out: str | os.PathLike[str],
    allow_test_hooks: bool = False,
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
    binding = validate_strict_gate_binding(
        runtime_root=runtime_root,
        health_env_file=health_env_file,
        health_url=health_url,
        base_url=base_url,
        verify_json_out=verify_json_out,
        acceptance_json_out=acceptance_json_out,
    )
    try:
        health_env_before = _path_identity(binding.health_env_file)
    except (OSError, RuntimeError) as exc:
        raise DeployGateRuntimeError(
            "deploy gate health environment file is unavailable"
        ) from exc
    for name in ("home", "cache", "tmp", "controller"):
        path = binding.runtime_root / name
        path.mkdir(mode=0o700, exist_ok=True)
        info = path.lstat()
        if (
            not stat.S_ISDIR(info.st_mode)
            or stat.S_ISLNK(info.st_mode)
            or info.st_uid != os.geteuid()
            or stat.S_IMODE(info.st_mode) != 0o700
        ):
            raise DeployGateRuntimeError("deploy gate private runtime child is unsafe")
    environment = build_deploy_gate_environment(
        inherited,
        source=source,
        python_bin=python_bin,
        runtime_root=binding.runtime_root,
        allow_test_hooks=allow_test_hooks,
    )
    environment.update(binding.environment())
    try:
        yield python_bin, environment
    finally:
        try:
            stable = (
                _path_identity(source / ".env") == env_before
                and _path_identity(source / ".venv") == venv_before
                and _path_identity(source / ".venv" / "bin" / "python")
                == python_before
                and _path_identity(binding.health_env_file) == health_env_before
            )
        except (OSError, RuntimeError):
            stable = False
        if not stable:
            raise DeployGateRuntimeError(
                "deploy gate runtime or health-token inputs changed during verification"
            )


def _live_postmaster(pid_file: Path) -> int | None:
    try:
        pid = int(pid_file.read_text(encoding="utf-8").splitlines()[0].strip())
        os.kill(pid, 0)
        return pid if pid > 1 else None
    except (OSError, UnicodeDecodeError, IndexError, ValueError):
        return None


def stop_candidate_browser_runtime_postgres(
    runtime_root: str | os.PathLike[str],
) -> list[dict[str, object]]:
    """Stop Postgres only under the one explicitly bound private runtime."""

    root = validate_runtime_root(runtime_root)
    data_dir = root / "runtime" / "data" / "postgres"
    pid_file = data_dir / "postmaster.pid"
    receipt: dict[str, object] = {"root": str(root)}
    if not pid_file.is_file():
        return []
    pid = _live_postmaster(pid_file)
    if pid is None:
        return [{**receipt, "status": "stale_pidfile"}]
    names = [
        Path(os.environ.get("POSTGRES_BIN") or "/nonexistent") / "pg_ctl",
        *map(Path, _PG_CTL_FALLBACKS),
    ]
    found = shutil.which("pg_ctl")
    if found:
        names.append(Path(found))
    pg_ctl = next(
        (candidate for candidate in names if candidate.is_file() and os.access(candidate, os.X_OK)),
        None,
    )
    if pg_ctl is None:
        _log.error(
            "candidate runtime postgres pid=%s live under %s but pg_ctl unavailable",
            pid,
            data_dir,
        )
        return [{**receipt, "status": "pg_ctl_missing", "pid": pid}]
    try:
        done = subprocess.run(
            [str(pg_ctl), "-D", str(data_dir), "stop", "-m", "fast", "-t", "30"],
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            timeout=60,
            check=False,
        )
        returncode = done.returncode
        detail = (done.stderr or done.stdout or "").strip()[:240]
    except (OSError, subprocess.SubprocessError) as exc:
        returncode = -1
        detail = f"{type(exc).__name__}: {exc}"[:240]
    if returncode == 0 and _live_postmaster(pid_file) is None:
        return [{**receipt, "status": "stopped", "pid": pid}]
    _log.error(
        "candidate runtime postgres stop FAILED pid=%s rc=%s data=%s: %s",
        pid,
        returncode,
        data_dir,
        detail,
    )
    return [
        {
            **receipt,
            "status": "stop_failed",
            "pid": pid,
            "returncode": returncode,
            "detail": detail,
        }
    ]


def cleanup_candidate_browser_runtime(
    runtime_root: str | os.PathLike[str], *, expected_identity: tuple[int, int] | None = None,
    fixture_allow_unsafe_root: bool = False,
) -> list[dict[str, object]]:
    """Remove only the exact bound root, after proving its Postgres is gone."""

    root = validate_runtime_root(runtime_root)
    before = root.lstat()
    observed_identity = (before.st_dev, before.st_ino)
    if expected_identity is None or observed_identity != expected_identity:
        raise DeployGateRuntimeError("candidate runtime cleanup identity token mismatch")
    canonical_tmp = Path("/tmp").resolve()
    if not fixture_allow_unsafe_root and (
        root.parent.resolve() != canonical_tmp
        or not root.name.startswith("vkpi-candidate-browser-runtime.")
    ):
        raise DeployGateRuntimeError("candidate runtime cleanup root was not controller-created")
    receipts = stop_candidate_browser_runtime_postgres(root)
    unsafe = [
        item
        for item in receipts
        if item.get("status") in {"pg_ctl_missing", "stop_failed"}
    ]
    if unsafe:
        raise DeployGateRuntimeError(
            "candidate runtime cleanup refused while Postgres may still be live"
        )
    current = root.lstat()
    if (current.st_dev, current.st_ino) != expected_identity or root.is_symlink():
        raise DeployGateRuntimeError("candidate runtime root changed before removal")
    shutil.rmtree(root)
    return receipts
