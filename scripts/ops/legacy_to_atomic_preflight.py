#!/usr/bin/env python3
"""Secret-free, read-only legacy-to-atomic cloud migration preflight.

The public CLI has no mutation mode.  It sends this source over SSH stdin and
runs a collector which is restricted to filesystem reads, ``systemctl show``,
PostgreSQL ``SHOW``/``SELECT``, Redis read commands, one loopback health GET,
and backup verification.  A successful ``go`` decision means that a separate,
future migration implementation may be reviewed; it never authorizes or
executes that migration.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import pwd
import grp
import re
import shutil
import stat
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any, Mapping, Sequence
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener


SCHEMA_VERSION = 1
REPORT_TYPE = "vkpi_legacy_to_atomic_readonly_preflight"
DEFAULT_ROOT = "/opt/viltrox-2.0"
DEFAULT_TARGET = "viltrox"
DEFAULT_HEALTH_URL = "http://127.0.0.1:8001/health"
DEFAULT_APP_USER = "viltrox"
DEFAULT_REMOTE_PYTHON = "/opt/viltrox-2.0/.venv/bin/python"
DEFAULT_REQUIRED_DOMAINS = (
    "viltroxtest.com",
    "www.viltroxtest.com",
    "viltroxvia.com",
    "www.viltroxvia.com",
)
WEB_UNIT = "viltrox-2.0-test.service"
INTERACTIVE_UNIT = "vkpi-worker-interactive.service"
BULK_UNITS = tuple(f"vkpi-worker-bulk@{index}.service" for index in range(1, 7))
REDIS_WORKER_UNIT = "vkpi-redis-worker.service"
CORE_RUNTIME_UNITS = (WEB_UNIT, INTERACTIVE_UNIT, *BULK_UNITS)
OBSERVED_UNITS = (
    *CORE_RUNTIME_UNITS,
    REDIS_WORKER_UNIT,
    "vkpi-sync-daily.service",
    "vkpi-sync-daily.timer",
    "vkpi-health-sentinel.service",
    "vkpi-health-sentinel.timer",
)

SHA40_RE = re.compile(r"^[0-9a-f]{40}$")
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
MIGRATION_RE = re.compile(r"^[0-9]{3}_[A-Za-z0-9_.-]+\.sql$")
IDENTIFIER_RE = re.compile(r"^[A-Za-z0-9_.@-]+$")
SAFE_PATH_RE = re.compile(r"^/[A-Za-z0-9_./@-]+$")
DOMAIN_RE = re.compile(
    r"^(?:[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?\.)+[A-Za-z]{2,63}$"
)
SSH_TARGET_RE = re.compile(r"^[A-Za-z0-9_.@:-]+$")
DATABASE_NAME_RE = re.compile(r"^[A-Za-z0-9_]+$")
SAFE_STATE_RE = re.compile(r"^[A-Za-z0-9_.@:-]+$")
NGINX_ROOT = Path("/etc/nginx")
SYSTEMD_PROPERTIES = frozenset(
    {
        "LoadState",
        "ActiveState",
        "UnitFileState",
        "FragmentPath",
        "User",
        "Group",
        "WorkingDirectory",
        "Environment",
        "ExecStart",
    }
)


class PreflightError(RuntimeError):
    """A public, secret-free preflight failure."""


class _NoRedirectHandler(HTTPRedirectHandler):
    """Keep the health probe on the reviewed loopback endpoint."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: ANN001
        return None


_HEALTH_PROXY_HANDLER = ProxyHandler({})
_HEALTH_OPENER = build_opener(_HEALTH_PROXY_HANDLER, _NoRedirectHandler())

_REMOTE_COLLECT_MODE = len(sys.argv) > 1 and sys.argv[1] == "--remote-collect"
if not _REMOTE_COLLECT_MODE:
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import legacy_to_atomic_preflight_report as _report_helpers
    import legacy_to_atomic_preflight_transport as _transport_helpers


def _utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _safe_identifier(value: object) -> str | None:
    text = str(value or "").strip()
    return text if IDENTIFIER_RE.fullmatch(text) else None


def _safe_state(value: object) -> str | None:
    text = str(value or "").strip()
    return text if SAFE_STATE_RE.fullmatch(text) else None


def _safe_path(value: object) -> str | None:
    text = str(value or "").strip()
    if not SAFE_PATH_RE.fullmatch(text) or ".." in PurePosixPath(text).parts:
        return None
    return text


def _safe_sha40(value: object) -> str | None:
    text = str(value or "").strip().lower()
    return text if SHA40_RE.fullmatch(text) else None


def _safe_sha256(value: object) -> str | None:
    text = str(value or "").strip().lower()
    return text if SHA256_RE.fullmatch(text) else None


def _safe_migration(value: object) -> str | None:
    text = str(value or "").strip()
    return text if MIGRATION_RE.fullmatch(text) else None


def _open_regular_fd(path: Path, *, maximum_bytes: int | None = None) -> tuple[int, os.stat_result]:
    """Open one non-symlink, single-link regular file without a final-component race."""

    before = path.lstat()
    if (
        not stat.S_ISREG(before.st_mode)
        or before.st_nlink != 1
        or (maximum_bytes is not None and before.st_size > maximum_bytes)
    ):
        raise PreflightError("unsafe_regular_file")
    flags = os.O_RDONLY
    flags |= getattr(os, "O_CLOEXEC", 0)
    flags |= getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags)
    except OSError:
        raise PreflightError("unsafe_regular_file") from None
    try:
        after = os.fstat(descriptor)
        if (
            not stat.S_ISREG(after.st_mode)
            or after.st_nlink != 1
            or (before.st_dev, before.st_ino) != (after.st_dev, after.st_ino)
            or (maximum_bytes is not None and after.st_size > maximum_bytes)
        ):
            raise PreflightError("unsafe_regular_file")
        return descriptor, after
    except Exception:
        os.close(descriptor)
        raise


def _safe_regular_info(
    path: Path,
    *,
    maximum_bytes: int | None = None,
    require_nonempty: bool = False,
) -> os.stat_result | None:
    try:
        descriptor, info = _open_regular_fd(path, maximum_bytes=maximum_bytes)
        os.close(descriptor)
    except (OSError, PreflightError):
        return None
    if require_nonempty and info.st_size <= 0:
        return None
    return info


def _safe_directory(path: Path) -> bool:
    try:
        info = path.lstat()
    except OSError:
        return False
    return stat.S_ISDIR(info.st_mode)


def _bounded_read(path: Path, maximum_bytes: int = 2 * 1024 * 1024) -> bytes:
    descriptor, _ = _open_regular_fd(path, maximum_bytes=maximum_bytes)
    with os.fdopen(descriptor, "rb", closefd=True) as handle:
        raw = handle.read(maximum_bytes + 1)
    if len(raw) > maximum_bytes:
        raise PreflightError("unsafe_regular_file")
    return raw


def _sha256_file(path: Path, *, maximum_bytes: int | None = None) -> str:
    digest = hashlib.sha256()
    descriptor, _ = _open_regular_fd(path, maximum_bytes=maximum_bytes)
    with os.fdopen(descriptor, "rb", closefd=True) as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _dotenv(path: Path) -> dict[str, str]:
    raw = _bounded_read(path, 1024 * 1024).decode("utf-8")
    values: dict[str, str] = {}
    for raw_line in raw.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", key):
            continue
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
            value = value[1:-1]
        values[key] = value
    return values


def _readonly_command(command: Sequence[str], *, timeout: float = 20.0) -> subprocess.CompletedProcess[str]:
    """Run only an explicitly reviewed, read-only local command on the target."""

    allowed = False
    if len(command) >= 3 and command[0] == "systemctl" and command[1] == "show":
        unit = command[2]
        options = command[3:]
        allowed = unit in OBSERVED_UNITS and all(
            option == "--no-pager"
            or (
                option.startswith("--property=")
                and option.split("=", 1)[1] in SYSTEMD_PROPERTIES
            )
            for option in options
        )
    elif len(command) == 3 and Path(command[0]).name == "pg_restore":
        executable_path = _safe_path(command[0])
        dump_path = _safe_path(command[2])
        allowed = bool(
            executable_path
            and Path(executable_path).is_absolute()
            and command[1] == "--list"
            and dump_path
            and Path(dump_path).is_absolute()
        )
    if not allowed:
        raise PreflightError("remote_command_not_allowlisted")
    return subprocess.run(
        list(command),
        check=False,
        capture_output=True,
        text=True,
        timeout=timeout,
    )


def _path_description(path: Path, *, releases_root: Path | None = None) -> dict[str, Any]:
    try:
        info = path.lstat()
    except FileNotFoundError:
        return {"kind": "absent", "safe_target": False, "target_name": None}
    except OSError:
        return {"kind": "unreadable", "safe_target": False, "target_name": None}
    if stat.S_ISLNK(info.st_mode):
        try:
            resolved = path.resolve(strict=True)
            safe_target = bool(
                releases_root is not None
                and resolved != releases_root
                and releases_root in resolved.parents
                and resolved.is_dir()
            )
            target_name = resolved.name if IDENTIFIER_RE.fullmatch(resolved.name) else None
        except OSError:
            safe_target = False
            target_name = None
        return {"kind": "symlink", "safe_target": safe_target, "target_name": target_name}
    if stat.S_ISDIR(info.st_mode):
        kind = "directory"
    elif stat.S_ISREG(info.st_mode):
        kind = "file"
    else:
        kind = "special"
    return {"kind": kind, "safe_target": False, "target_name": None}


def _release_layout(root: Path) -> dict[str, Any]:
    releases = root / "releases"
    releases_directory = _safe_directory(releases)
    releases_root = releases.resolve(strict=True) if releases_directory else None
    current = _path_description(root / "current", releases_root=releases_root)
    previous = _path_description(root / "previous", releases_root=releases_root)
    markers = {
        "backend": _safe_directory(root / "backend"),
        "frontend_dist": _safe_directory(root / "frontend/dist"),
        "environment": _safe_regular_info(root / ".env") is not None,
        "build_git_sha": _safe_regular_info(root / "BUILD_GIT_SHA", maximum_bytes=256)
        is not None,
    }
    flat = all(markers.values())
    if current["kind"] == "symlink" and current["safe_target"]:
        state = "atomic"
    elif current["kind"] == "absent" and flat:
        state = "legacy_flat"
    else:
        state = "unrecognized"
    build_sha = None
    try:
        build_sha = _safe_sha40(_bounded_read(root / "BUILD_GIT_SHA", 256).decode("ascii"))
    except (OSError, UnicodeError, PreflightError):
        pass
    return {
        "root_exists": root.is_dir() and not root.is_symlink(),
        "state": state,
        "flat_markers": markers,
        "releases_directory": releases_directory,
        "current": current,
        "previous": previous,
        "root_build_git_sha": build_sha,
        "atomic_helper_present": (
            _safe_regular_info(
                root / "scripts/ops/atomic_release_layout.py",
                maximum_bytes=2 * 1024 * 1024,
                require_nonempty=True,
            )
            is not None
        ),
    }


def _account_access(info: os.stat_result, user_name: str) -> tuple[bool, bool]:
    try:
        user = pwd.getpwnam(user_name)
        group_ids = set(os.getgrouplist(user.pw_name, user.pw_gid))
    except (KeyError, OSError):
        return False, False
    mode = stat.S_IMODE(info.st_mode)
    if info.st_uid == user.pw_uid:
        return bool(mode & stat.S_IRUSR), bool(mode & stat.S_IWUSR)
    if info.st_gid in group_ids:
        return bool(mode & stat.S_IRGRP), bool(mode & stat.S_IWGRP)
    return bool(mode & stat.S_IROTH), bool(mode & stat.S_IWOTH)


def _environment_state(root: Path, app_user: str) -> tuple[dict[str, Any], dict[str, str]]:
    path = root / ".env"
    result: dict[str, Any] = {
        "regular_file": False,
        "app_user_nonroot": False,
        "owner": None,
        "group": None,
        "mode": None,
        "app_readable": False,
        "app_writable": False,
        "database_configured": False,
        "redis_configured": False,
        "parse_ok": False,
    }
    values: dict[str, str] = {}
    try:
        result["app_user_nonroot"] = pwd.getpwnam(app_user).pw_uid != 0
    except KeyError:
        pass
    try:
        info = path.lstat()
        if not stat.S_ISREG(info.st_mode) or path.is_symlink() or info.st_nlink != 1:
            return result, values
        readable, writable = _account_access(info, app_user)
        try:
            owner = pwd.getpwuid(info.st_uid).pw_name
        except KeyError:
            owner = str(info.st_uid)
        try:
            group_name = grp.getgrgid(info.st_gid).gr_name
        except KeyError:
            group_name = str(info.st_gid)
        result.update(
            {
                "regular_file": True,
                "owner": _safe_identifier(owner),
                "group": _safe_identifier(group_name),
                "mode": f"{stat.S_IMODE(info.st_mode):04o}",
                "app_readable": readable,
                "app_writable": writable,
            }
        )
        values = _dotenv(path)
        result.update(
            {
                "database_configured": bool(values.get("DATABASE_URL")),
                "redis_configured": bool(values.get("REDIS_URL")),
                "parse_ok": True,
            }
        )
    except (OSError, UnicodeError, PreflightError):
        pass
    return result, values


def _systemd_unit(name: str) -> dict[str, Any]:
    command = [
        "systemctl",
        "show",
        name,
        "--no-pager",
        "--property=LoadState",
        "--property=ActiveState",
        "--property=UnitFileState",
        "--property=FragmentPath",
        "--property=User",
        "--property=Group",
        "--property=WorkingDirectory",
        "--property=Environment",
        "--property=ExecStart",
    ]
    try:
        completed = _readonly_command(command)
    except (OSError, subprocess.TimeoutExpired, PreflightError):
        return {"name": name, "observable": False}
    fields: dict[str, str] = {}
    for line in completed.stdout.splitlines():
        if "=" not in line:
            continue
        key, value = line.split("=", 1)
        fields[key] = value
    searchable = " ".join((fields.get("Environment", ""), fields.get("ExecStart", "")))

    def selected(key: str) -> str | None:
        match = re.search(rf"(?:^|[ ;\[\]]){re.escape(key)}=([A-Za-z0-9_.@:-]+)", searchable)
        return _safe_state(match.group(1)) if match else None

    fragment_path = _safe_path(fields.get("FragmentPath"))
    fragment_sha = None
    fragment_readable = False
    if fragment_path:
        try:
            fragment = Path(fragment_path)
            _bounded_read(fragment)
            fragment_sha = _sha256_file(fragment, maximum_bytes=2 * 1024 * 1024)
            fragment_readable = True
        except (OSError, PreflightError):
            pass
    return {
        "name": name,
        "observable": completed.returncode == 0,
        "load_state": _safe_state(fields.get("LoadState")),
        "active_state": _safe_state(fields.get("ActiveState")),
        "unit_file_state": _safe_state(fields.get("UnitFileState")),
        "fragment_path": fragment_path,
        "fragment_sha256": _safe_sha256(fragment_sha),
        "fragment_readable": fragment_readable,
        "user": _safe_identifier(fields.get("User") or "root"),
        "group": _safe_identifier(fields.get("Group") or "root"),
        "working_directory": _safe_path(fields.get("WorkingDirectory")),
        "app_role": selected("APP_ROLE"),
        "environment_mode": selected("ENVIRONMENT"),
        "claim_lane": selected("APIFY_WORKER_CLAIM_LANE"),
        "heartbeat_name": selected("APIFY_WORKER_HEARTBEAT_NAME"),
    }


def _database_state(values: Mapping[str, str]) -> dict[str, Any]:
    result: dict[str, Any] = {
        "reachable": False,
        "read_only_session": False,
        "database_name": None,
        "migration_max": None,
        "migration_count": None,
        "error_code": None,
    }
    database_url = str(values.get("DATABASE_URL") or "")
    if not database_url:
        result["error_code"] = "database_not_configured"
        return result
    try:
        import psycopg

        with psycopg.connect(
            database_url,
            connect_timeout=5,
            options="-c default_transaction_read_only=on",
        ) as connection:
            with connection.cursor() as cursor:
                cursor.execute("SHOW default_transaction_read_only")
                read_only = str(cursor.fetchone()[0]).lower() == "on"
                cursor.execute("SELECT current_database()")
                database_name = str(cursor.fetchone()[0])
                cursor.execute("SELECT version_key FROM schema_migrations")
                migrations = [str(row[0]) for row in cursor.fetchall() if row and row[0]]
        result.update(
            {
                "reachable": True,
                "read_only_session": read_only,
                "database_name": (
                    database_name if DATABASE_NAME_RE.fullmatch(database_name) else None
                ),
                "migration_max": _safe_migration(max(migrations) if migrations else None),
                "migration_count": len(migrations),
            }
        )
    except Exception:
        result["error_code"] = "database_read_failed"
    return result


def _redis_state(values: Mapping[str, str]) -> dict[str, Any]:
    result: dict[str, Any] = {
        "reachable": False,
        "aof_enabled": None,
        "rdb_last_bgsave_status": None,
        "aof_last_write_status": None,
        "error_code": None,
    }
    redis_url = str(values.get("REDIS_URL") or "")
    if not redis_url:
        result["error_code"] = "redis_not_configured"
        return result
    try:
        import redis

        client = redis.Redis.from_url(
            redis_url,
            socket_connect_timeout=2,
            socket_timeout=3,
            decode_responses=True,
        )
        try:
            reachable = bool(client.ping())
            persistence = client.info("persistence")
            try:
                config = client.config_get("appendonly")
            except Exception:
                config = {}
            raw_aof = config.get("appendonly")
            if raw_aof is None:
                raw_aof = persistence.get("aof_enabled")
            aof_enabled = str(raw_aof).lower() in {"1", "yes", "true", "on"}
            result.update(
                {
                    "reachable": reachable,
                    "aof_enabled": aof_enabled,
                    "rdb_last_bgsave_status": _safe_state(
                        persistence.get("rdb_last_bgsave_status")
                    ),
                    "aof_last_write_status": _safe_state(
                        persistence.get("aof_last_write_status")
                    ),
                }
            )
        finally:
            close = getattr(client, "close", None)
            if callable(close):
                close()
    except Exception:
        result["error_code"] = "redis_read_failed"
    return result


def _nginx_state() -> dict[str, Any]:
    enabled = NGINX_ROOT / "sites-enabled"
    domains: set[str] = set()
    readable_files = 0
    try:
        paths = sorted(enabled.iterdir())
    except OSError:
        return {"config_readable": False, "readable_file_count": 0, "domains": []}
    for path in paths:
        try:
            root = NGINX_ROOT.resolve(strict=True)
            resolved = path.resolve(strict=True)
            if resolved == root or root not in resolved.parents:
                continue
            raw = _bounded_read(resolved, 2 * 1024 * 1024).decode("utf-8")
        except (OSError, UnicodeError, PreflightError):
            continue
        readable_files += 1
        without_comments = "\n".join(line.split("#", 1)[0] for line in raw.splitlines())
        for match in re.finditer(r"\bserver_name\s+([^;]+);", without_comments):
            for candidate in match.group(1).split():
                candidate = candidate.strip().lower()
                if DOMAIN_RE.fullmatch(candidate):
                    domains.add(candidate)
    return {
        "config_readable": readable_files > 0,
        "readable_file_count": readable_files,
        "domains": sorted(domains),
    }


def _health_state(url: str) -> dict[str, Any]:
    result: dict[str, Any] = {
        "reachable": False,
        "status": None,
        "server_git_sha": None,
        "client_git_sha": None,
        "sha_aligned": None,
        "db_migration_max": None,
        "worker_online": None,
        "worker_fleet_present": False,
        "error_code": None,
    }
    try:
        request = Request(url, method="GET", headers={"Accept": "application/json"})
        with _HEALTH_OPENER.open(request, timeout=5) as response:
            if response.geturl() != url:
                raise PreflightError("health_redirect_refused")
            raw = response.read(1024 * 1024 + 1)
        if len(raw) > 1024 * 1024:
            raise PreflightError("health_response_too_large")
        payload = json.loads(raw.decode("utf-8"))
        trust = payload.get("trust") if isinstance(payload, dict) else None
        trust = trust if isinstance(trust, dict) else {}
        result.update(
            {
                "reachable": True,
                "status": _safe_state(payload.get("status")),
                "server_git_sha": _safe_sha40(trust.get("server_git_sha")),
                "client_git_sha": _safe_sha40(trust.get("client_git_sha")),
                "sha_aligned": trust.get("sha_aligned") if isinstance(trust.get("sha_aligned"), bool) else None,
                "db_migration_max": _safe_migration(trust.get("db_migration_max")),
                "worker_online": trust.get("worker_online") if isinstance(trust.get("worker_online"), bool) else None,
                "worker_fleet_present": isinstance(trust.get("worker_fleet"), dict),
            }
        )
    except Exception:
        result["error_code"] = "health_read_failed"
    return result


def _backup_state(root: Path, max_age_hours: float) -> dict[str, Any]:
    base = root / "backups/ops"
    result: dict[str, Any] = {
        "directory_readable": False,
        "candidate_count": 0,
        "latest_name": None,
        "latest_age_hours": None,
        "fresh": False,
        "dump_size_bytes": None,
        "checksum_present": False,
        "checksum_verified": False,
        "catalog_verified": False,
        "runtime_state_present": False,
        "media_manifest_present": False,
        "encrypted_environment_snapshot_present": False,
        "off_host_receipt_present": False,
    }
    try:
        candidates: list[tuple[Path, os.stat_result]] = []
        for path in base.iterdir():
            if not _safe_directory(path):
                continue
            dump_info = _safe_regular_info(path / "prod-db.dump", require_nonempty=True)
            if dump_info is not None:
                candidates.append((path, dump_info))
    except OSError:
        return result
    result["directory_readable"] = True
    result["candidate_count"] = len(candidates)
    if not candidates:
        return result
    latest, _ = max(candidates, key=lambda item: item[1].st_mtime_ns)
    dump = latest / "prod-db.dump"
    sidecar = latest / "prod-db.dump.sha256"
    try:
        dump_info = _safe_regular_info(dump, require_nonempty=True)
        if dump_info is None:
            raise PreflightError("unsafe_backup_dump")
        age_seconds = datetime.now(timezone.utc).timestamp() - dump_info.st_mtime
        age_hours = max(0.0, age_seconds / 3600)
        latest_name = latest.name if IDENTIFIER_RE.fullmatch(latest.name) else None
        result.update(
            {
                "latest_name": latest_name,
                "latest_age_hours": round(age_hours, 3),
                "fresh": -300 <= age_seconds <= max_age_hours * 3600,
                "dump_size_bytes": dump_info.st_size,
                "runtime_state_present": _safe_regular_info(
                    latest / "runtime-state.txt", require_nonempty=True
                )
                is not None,
                "media_manifest_present": _safe_regular_info(
                    latest / "media-cache-manifest.tsv"
                )
                is not None,
            }
        )
        if _safe_regular_info(sidecar, maximum_bytes=4096, require_nonempty=True):
            result["checksum_present"] = True
            line = _bounded_read(sidecar, 4096).decode("ascii").strip()
            match = re.fullmatch(r"([0-9a-f]{64})\s+\*?([A-Za-z0-9_./-]+)", line)
            sidecar_name = PurePosixPath(match.group(2)) if match else None
            if (
                match
                and sidecar_name is not None
                and ".." not in sidecar_name.parts
                and sidecar_name.name == dump.name
            ):
                result["checksum_verified"] = _sha256_file(dump) == match.group(1)
        pg_restore = shutil.which("pg_restore")
        if pg_restore:
            completed = _readonly_command([pg_restore, "--list", str(dump)], timeout=90)
            result["catalog_verified"] = completed.returncode == 0 and bool(completed.stdout.strip())
        result["encrypted_environment_snapshot_present"] = any(
            _safe_regular_info(path, maximum_bytes=16 * 1024 * 1024, require_nonempty=True)
            is not None
            and path.suffix.lower() in {".age", ".gpg", ".enc"}
            and "env" in path.name.lower()
            for path in latest.iterdir()
        )
        result["off_host_receipt_present"] = (
            _safe_regular_info(
                latest / "off-host-backup-receipt.json",
                maximum_bytes=1024 * 1024,
                require_nonempty=True,
            )
            is not None
        )
        final_dump_info = _safe_regular_info(dump, require_nonempty=True)
        initial_identity = (
            dump_info.st_dev,
            dump_info.st_ino,
            dump_info.st_size,
            dump_info.st_mtime_ns,
        )
        final_identity = (
            (
                final_dump_info.st_dev,
                final_dump_info.st_ino,
                final_dump_info.st_size,
                final_dump_info.st_mtime_ns,
            )
            if final_dump_info is not None
            else None
        )
        if final_identity != initial_identity:
            result["checksum_verified"] = False
            result["catalog_verified"] = False
    except (OSError, UnicodeError, PreflightError, subprocess.TimeoutExpired):
        pass
    return result


def _remote_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--root", required=True)
    parser.add_argument("--app-user", required=True)
    parser.add_argument("--health-url", required=True)
    parser.add_argument("--max-backup-age-hours", required=True, type=float)
    return parser


def _remote_collect(argv: Sequence[str]) -> int:
    args = _remote_parser().parse_args(argv)
    root_value = _validate_root(args.root)
    _validate_app_user(args.app_user)
    health_url = _validate_health_url(args.health_url)
    if not (0 < args.max_backup_age_hours <= 168):
        raise PreflightError("invalid_backup_age")
    root = Path(root_value)
    environment, values = _environment_state(root, args.app_user)
    snapshot = {
        "schema_version": SCHEMA_VERSION,
        "collected_at": _utc_now(),
        "release_layout": _release_layout(root),
        "environment": environment,
        "systemd_units": [_systemd_unit(name) for name in OBSERVED_UNITS],
        "database": _database_state(values),
        "redis": _redis_state(values),
        "nginx": _nginx_state(),
        "health": _health_state(health_url),
        "backup": _backup_state(root, args.max_backup_age_hours),
    }
    sys.stdout.write(json.dumps(snapshot, sort_keys=True, separators=(",", ":")) + "\n")
    return 0


def _valid_absolute_path(value: str, label: str) -> str:
    if not SAFE_PATH_RE.fullmatch(value) or ".." in PurePosixPath(value).parts:
        raise PreflightError(f"invalid_{label}")
    return value


def _validate_root(value: str) -> str:
    root = _valid_absolute_path(value, "root")
    if PurePosixPath(root) != PurePosixPath(DEFAULT_ROOT):
        raise PreflightError("root_outside_reviewed_installation")
    return root


def _validate_target(value: str) -> str:
    return _transport_helpers.validate_target(value, SSH_TARGET_RE, PreflightError)


def _validate_health_url(value: str) -> str:
    try:
        parsed = urlsplit(value)
    except ValueError:
        raise PreflightError("invalid_health_url") from None
    if (
        parsed.scheme != "http"
        or parsed.hostname != "127.0.0.1"
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
        or parsed.path != "/health"
    ):
        raise PreflightError("health_url_must_be_loopback_get")
    return value


def _validate_app_user(value: str) -> str:
    if not IDENTIFIER_RE.fullmatch(value) or value.lower() == "root":
        raise PreflightError("invalid_app_user")
    return value


def _validate_remote_python(value: str, root: str) -> str:
    return _transport_helpers.validate_remote_python(
        value,
        root,
        valid_absolute_path=_valid_absolute_path,
        validate_root=_validate_root,
        error_type=PreflightError,
    )


def _ssh_command(args: argparse.Namespace) -> list[str]:
    return _transport_helpers.ssh_command(
        args,
        validate_target_value=_validate_target,
        validate_root=_validate_root,
        validate_remote_python_value=_validate_remote_python,
        validate_health_url=_validate_health_url,
    )


def _collect_via_ssh(args: argparse.Namespace) -> Mapping[str, Any]:
    return _transport_helpers.collect_via_ssh(
        args,
        source=Path(__file__).read_text(encoding="utf-8"),
        command_builder=_ssh_command,
        schema_version=SCHEMA_VERSION,
        error_type=PreflightError,
    )


def _contains_secret(value: object, *, key: str = "") -> bool:
    return _report_helpers.contains_secret(value, key=key)


def _build_report(snapshot: Mapping[str, Any], args: argparse.Namespace) -> dict[str, Any]:
    return _report_helpers.build_report(
        snapshot,
        args,
        schema_version=SCHEMA_VERSION,
        report_type=REPORT_TYPE,
        generated_at=_utc_now(),
        observed_units=OBSERVED_UNITS,
        core_runtime_units=CORE_RUNTIME_UNITS,
        interactive_unit=INTERACTIVE_UNIT,
        bulk_units=BULK_UNITS,
        error_type=PreflightError,
    )


def _latest_local_migration() -> str:
    return _report_helpers.latest_local_migration(Path(__file__), PreflightError)


def build_parser() -> argparse.ArgumentParser:
    return _report_helpers.build_parser(
        __doc__,
        {
            "ssh_target": DEFAULT_TARGET,
            "root": DEFAULT_ROOT,
            "app_user": DEFAULT_APP_USER,
            "remote_python": DEFAULT_REMOTE_PYTHON,
            "health_url": DEFAULT_HEALTH_URL,
        },
    )


def _validate_public_args(args: argparse.Namespace) -> None:
    _report_helpers.validate_public_args(
        args,
        validate_target=_validate_target,
        validate_root=_validate_root,
        validate_remote_python=_validate_remote_python,
        validate_health_url=_validate_health_url,
        validate_app_user=_validate_app_user,
        error_type=PreflightError,
    )


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        args.expected_migration = args.expected_migration or _latest_local_migration()
        args.required_domain = args.required_domain or list(DEFAULT_REQUIRED_DOMAINS)
        _validate_public_args(args)
        snapshot = _collect_via_ssh(args)
        report = _build_report(snapshot, args)
    except PreflightError as exc:
        report = {
            "schema_version": SCHEMA_VERSION,
            "report_type": REPORT_TYPE,
            "generated_at": _utc_now(),
            "mode": "remote_read_only_preflight",
            "decision": "no-go",
            "secret_free": True,
            "safety_contract": {
                "remote_write_operations": [],
                "mutation_interface_present": False,
                "execution_allowed": False,
                "future_mutation_requires_distinct_explicit_approvals": 2,
                "future_mutation_authorization_implemented": False,
            },
            "error_code": str(exc),
        }
        sys.stdout.write(json.dumps(report, sort_keys=True, indent=2 if args.pretty else None) + "\n")
        return 3
    sys.stdout.write(json.dumps(report, sort_keys=True, indent=2 if args.pretty else None) + "\n")
    return 0 if report["decision"] == "go" else 2


if __name__ == "__main__":
    if _REMOTE_COLLECT_MODE:
        raise SystemExit(_remote_collect(sys.argv[2:]))
    raise SystemExit(main())
