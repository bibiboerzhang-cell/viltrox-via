#!/usr/bin/env python3
"""Trusted resources and environments for the isolated strict runtime gate."""

from __future__ import annotations

import ipaddress
import os
import shutil
import socket
import stat
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping, Sequence
from urllib.parse import urlsplit

from psycopg import Error as PsycopgError
from psycopg.conninfo import conninfo_to_dict

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from scripts.ops.isolated_runtime_attestation import StrictRuntimeGateError
from scripts.ops.trusted_runtime_binary import trusted_runtime_binary


RUNTIME_PREFIX = "vkpi-candidate-browser-runtime."
PROVIDER_ENV_NAMES = frozenset(
    {
        "ANTHROPIC_API_KEY", "APIFY_API_TOKEN", "APIFY_TOKEN",
        "GEMINI_API_KEY", "GEMINI_API_KEYS", "GOOGLE_API_KEY",
        "GOOGLE_CSE_API_KEY", "GOOGLE_GENERATIVE_AI_API_KEY",
        "GOOGLE_SEARCH_API_KEY", "GOOGLE_YOUTUBE_API_KEY",
        "OPENAI_API_KEY", "OPENAI_PROXY", "YOUTUBE_API_KEY",
        "YOUTUBE_DATA_API_KEY", "YTDLP_PROXY", "HTTP_PROXY", "HTTPS_PROXY",
        "ALL_PROXY", "http_proxy", "https_proxy", "all_proxy",
    }
)


@dataclass(frozen=True)
class RuntimePorts:
    web: int
    postgres: int
    redis: int

    def values(self) -> tuple[int, int, int]:
        return self.web, self.postgres, self.redis


@dataclass(frozen=True)
class ManagedProcess:
    process: subprocess.Popen[bytes]
    pid: int
    pgid: int
    session_id: int
    start_identity: str

    def poll(self):
        return self.process.poll()


def private_root(parent: Path = Path("/tmp")) -> Path:
    raw = __import__("tempfile").mkdtemp(prefix=RUNTIME_PREFIX, dir=parent)
    root = Path(raw)
    initial = root.lstat()
    identity = (initial.st_dev, initial.st_ino)
    try:
        os.chown(root, os.geteuid(), os.getegid())
        root.chmod(0o700)
        info = root.lstat()
        if (
            root.is_symlink()
            or not stat.S_ISDIR(info.st_mode)
            or info.st_uid != os.geteuid()
            or stat.S_IMODE(info.st_mode) != 0o700
            or (info.st_dev, info.st_ino) != identity
        ):
            raise StrictRuntimeGateError("strict runtime root is unsafe")
        return root
    except BaseException:
        try:
            current = root.lstat()
            if not root.is_symlink() and (current.st_dev, current.st_ino) == identity:
                shutil.rmtree(root)
        except FileNotFoundError:
            pass
        raise


def unique_loopback_ports(count: int = 3) -> tuple[int, ...]:
    listeners: list[socket.socket] = []
    try:
        for _ in range(count):
            listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            listener.bind(("127.0.0.1", 0))
            listeners.append(listener)
        ports = tuple(int(listener.getsockname()[1]) for listener in listeners)
    finally:
        for listener in listeners:
            listener.close()
    if len(set(ports)) != count or 8102 in ports:
        raise StrictRuntimeGateError("could not reserve unique non-default ports")
    return ports


def binary(name: str) -> str:
    return trusted_runtime_binary(name, error_type=StrictRuntimeGateError)


def command(
    arguments: Sequence[str], *, cwd: Path, env: Mapping[str, str],
    timeout: int = 1200,
) -> subprocess.CompletedProcess[bytes]:
    completed = subprocess.run(
        list(arguments), cwd=cwd, env=dict(env), stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False, timeout=timeout,
    )
    if completed.returncode != 0:
        detail = completed.stderr.decode("utf-8", "replace").strip()[-500:]
        raise StrictRuntimeGateError(
            f"strict runtime command failed ({Path(arguments[0]).name}): {detail}"
        )
    return completed


def source_dump_environment(database_url: str, root: Path) -> dict[str, str]:
    try:
        parsed = urlsplit(database_url)
        address = ipaddress.ip_address(parsed.hostname or "")
        port = parsed.port
        conninfo = conninfo_to_dict(database_url)
        final_host = ipaddress.ip_address(str(conninfo.get("host") or ""))
        final_hostaddr_raw = conninfo.get("hostaddr")
        final_hostaddr = (
            ipaddress.ip_address(str(final_hostaddr_raw)) if final_hostaddr_raw else None
        )
        final_port = int(str(conninfo.get("port") or "0"))
    except (ValueError, TypeError, PsycopgError) as exc:
        raise StrictRuntimeGateError("strict source database URL is invalid") from exc
    if (
        parsed.scheme not in {"postgresql", "postgres"}
        or not address.is_loopback
        or port is None
        or parsed.fragment
        or parsed.query
        or not parsed.path.strip("/")
        or address != ipaddress.ip_address("127.0.0.1")
        or any(conninfo.get(name) for name in ("service", "options", "passfile"))
        or not final_host.is_loopback
        or (final_hostaddr is not None and not final_hostaddr.is_loopback)
        or final_host != address
        or (final_hostaddr is not None and final_hostaddr != address)
        or final_port != port
    ):
        raise StrictRuntimeGateError(
            "strict source database URL must resolve to its explicit loopback origin"
        )
    return {
        "HOME": str(root / "home"), "LANG": "C", "LC_ALL": "C",
        "PATH": os.defpath, "PGDATABASE": database_url,
        "PGOPTIONS": "-c default_transaction_read_only=on -c statement_timeout=1200000",
        "TMPDIR": str(root / "tmp"),
    }


def postgres_environment(root: Path, port: int, database: str = "postgres") -> dict[str, str]:
    return {
        "HOME": str(root / "home"), "LANG": "C", "LC_ALL": "C",
        "PATH": os.defpath, "PGDATABASE": database, "PGHOST": "127.0.0.1",
        "PGPORT": str(port), "PGUSER": "postgres", "TMPDIR": str(root / "tmp"),
    }


def minimal_runtime_environment(
    *, root: Path, candidate: Path, source: Path, ports: RuntimePorts,
    git_sha: str, branch: str, fence: Path,
) -> dict[str, str]:
    database_url = f"postgresql://postgres@127.0.0.1:{ports.postgres}/vkpi_gate"
    redis_url = f"redis://127.0.0.1:{ports.redis}/0"
    environment = {
        "APP_GIT_BRANCH": branch, "APP_GIT_SHA": git_sha, "APP_ROLE": "worker",
        "DATABASE_URL": database_url, "DB_RUNTIME_BACKEND": "postgres",
        "DB_USE_PGBOUNCER": "0", "ENABLE_BROWSER": "0",
        "ENABLE_LOCAL_ORCHESTRATOR": "0", "ENABLE_SCHEDULER": "0",
        "ENVIRONMENT": "local", "HOME": str(root / "home"), "LANG": "C",
        "LC_ALL": "C", "LOCAL_DATABASE_URL": database_url,
        "LOCAL_REDIS_URL": redis_url, "LOCAL_RUNTIME_FORCE_STACK": "1",
        "LOG_LEVEL": "warning", "NO_PROXY": "127.0.0.1,localhost,::1",
        "PATH": os.defpath, "PYTHONDONTWRITEBYTECODE": "1",
        "PYTHONPATH": str(candidate / "backend"), "REDIS_URL": redis_url,
        "RUNTIME_ROOT": str(root / "runtime"), "TMPDIR": str(root / "tmp"),
        "VKPI_ASYNC_ENABLED": "0", "VKPI_RELEASE_VALIDATION_FENCE_PATH": str(fence),
        "VKPI_LLM_GATEWAY_FORCE_OFFLINE": "1", "VKPI_EXTERNAL_AI_DISABLED": "1",
        "VKPI_AUTOMATED_WRITES_DISABLED": "1",
        "VKPI_REDIS_WORKER_ALLOW_STALE_BACKLOG": "1",
        "VKPI_REDIS_WORKER_EXPECTED_INSTANCES": "1",
        "VKPI_REDIS_WORKER_HEARTBEAT_NAME": f"redis-worker-isolated-{ports.web}",
        "VKPI_REDIS_WORKER_MAX_CONSUMERS": "1", "WORKER_ASYNC_CONSUMERS": "1",
        "VKPI_SKIP_DOTENV": "1", "no_proxy": "127.0.0.1,localhost,::1",
    }
    for name in PROVIDER_ENV_NAMES:
        environment.pop(name, None)
    return environment


__all__ = [
    "ManagedProcess", "PROVIDER_ENV_NAMES", "RUNTIME_PREFIX", "RuntimePorts",
    "binary", "command", "minimal_runtime_environment", "postgres_environment",
    "private_root", "source_dump_environment", "unique_loopback_ports",
]
