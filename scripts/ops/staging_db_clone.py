#!/usr/bin/env python3
"""Manage the release-specific PostgreSQL clone used by viltroxtest.

The helper is intentionally narrow:

* the first source is ``viltrox2_test`` and later sources must be proven active
  release clones, so repeated deployments never reset accumulated test data;
* clone names are deterministic, release-bound, and identifier-safe;
* PostgreSQL administration uses local peer authentication as the postgres OS
  account, so the application DATABASE_URL is never copied into argv or logs;
* the only environment mutation replaces the database path in DATABASE_URL
  and any disabled DATABASE_POOL_URL metadata in one atomic file replacement.

It is invoked remotely by ``deploy_local_to_cloud.sh`` only after the immutable
release and rollback capture have been prepared.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import pwd
import re
import shutil
import stat
import sys
import tempfile
import time
from pathlib import Path
from typing import Any
from urllib.parse import quote, unquote, urlsplit, urlunsplit


SOURCE_DATABASE = "viltrox2_test"
CLONE_PREFIX = "viltrox2_test_release_"
MIN_DISK_HEADROOM_BYTES = 1024**3
SOURCE_CONNECTION_DRAIN_TIMEOUT_SECONDS = 10.0
SOURCE_CONNECTION_DRAIN_POLL_SECONDS = 0.25
RELEASE_ID_RE = re.compile(r"^[A-Za-z0-9_.-]+$")
DATABASE_IDENTIFIER_RE = re.compile(r"^[a-z][a-z0-9_]{0,62}$")
ENV_KEY_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
RELEASE_CONTROLLER_NAME = ".release-controller"
CONTROLLER_DIRECTORY_MODE = 0o700
CONTROLLER_FILE_MODE = 0o600
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


class CloneError(RuntimeError):
    """A fail-closed staging clone contract violation."""


def _secure_directory(path: Path, *, label: str) -> Path:
    try:
        info = path.lstat()
    except FileNotFoundError as exc:
        raise CloneError(f"{label} is missing: {path}") from exc
    if not stat.S_ISDIR(info.st_mode):
        raise CloneError(f"{label} must be a real directory: {path}")
    if info.st_uid != os.geteuid():
        raise CloneError(f"{label} owner is not the release controller: {path}")
    if stat.S_IMODE(info.st_mode) != CONTROLLER_DIRECTORY_MODE:
        raise CloneError(f"{label} mode must be 0700: {path}")
    return path


def _read_secure_controller_file(path: Path, *, label: str) -> bytes:
    try:
        initial = path.lstat()
    except FileNotFoundError as exc:
        raise CloneError(f"{label} is missing: {path}") from exc
    if not stat.S_ISREG(initial.st_mode) or initial.st_nlink != 1:
        raise CloneError(f"{label} must be a regular single-link file: {path}")
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(path, flags)
    try:
        info = os.fstat(descriptor)
        if (
            not stat.S_ISREG(info.st_mode)
            or info.st_nlink != 1
            or (info.st_dev, info.st_ino) != (initial.st_dev, initial.st_ino)
        ):
            raise CloneError(f"{label} must be a stable regular single-link file: {path}")
        if info.st_uid != os.geteuid():
            raise CloneError(f"{label} owner is not the release controller: {path}")
        if stat.S_IMODE(info.st_mode) != CONTROLLER_FILE_MODE:
            raise CloneError(f"{label} mode must be 0600: {path}")
        chunks: list[bytes] = []
        while True:
            chunk = os.read(descriptor, 1024 * 1024)
            if not chunk:
                break
            chunks.append(chunk)
        return b"".join(chunks)
    finally:
        os.close(descriptor)


def _fsync_directory(path: Path) -> None:
    descriptor = os.open(
        path,
        os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_DIRECTORY", 0),
    )
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _rollback_receipt_directory(root: Path, release_id: str) -> Path:
    if not RELEASE_ID_RE.fullmatch(release_id) or release_id in {".", ".."}:
        raise CloneError("release id is not a safe release directory name")
    root = root.absolute()
    try:
        root_info = root.lstat()
    except FileNotFoundError as exc:
        raise CloneError(f"application root is missing: {root}") from exc
    if not stat.S_ISDIR(root_info.st_mode):
        raise CloneError(f"application root must be a real directory: {root}")
    if root_info.st_uid != os.geteuid() or stat.S_IMODE(root_info.st_mode) & 0o022:
        raise CloneError(
            "application root must be controller-owned and not group/world writable"
        )

    controller = _secure_directory(
        root / RELEASE_CONTROLLER_NAME,
        label="release controller directory",
    )
    rollbacks = _secure_directory(
        controller / "rollbacks",
        label="release rollback directory",
    )
    rollback_dir = _secure_directory(
        rollbacks / release_id,
        label="release rollback capture directory",
    )

    digest_payload = _read_secure_controller_file(
        rollback_dir / "metadata.sha256",
        label="rollback metadata digest",
    )
    try:
        expected_digest = digest_payload.decode("ascii").strip()
    except UnicodeDecodeError as exc:
        raise CloneError("rollback metadata digest is invalid") from exc
    if not SHA256_RE.fullmatch(expected_digest):
        raise CloneError("rollback metadata digest is invalid")
    metadata_payload = _read_secure_controller_file(
        rollback_dir / "metadata.json",
        label="rollback metadata",
    )
    if hashlib.sha256(metadata_payload).hexdigest() != expected_digest:
        raise CloneError("rollback metadata hash mismatch")
    try:
        metadata = json.loads(metadata_payload.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise CloneError("rollback metadata payload is invalid") from exc
    if (
        not isinstance(metadata, dict)
        or metadata.get("schema") != 3
        or metadata.get("release_id") != release_id
    ):
        raise CloneError("rollback capture does not belong to the release")
    return rollback_dir


def _write_new_secure_file(path: Path, payload: bytes, *, label: str) -> None:
    flags = (
        os.O_WRONLY
        | os.O_CREAT
        | os.O_EXCL
        | getattr(os, "O_CLOEXEC", 0)
        | getattr(os, "O_NOFOLLOW", 0)
    )
    descriptor = os.open(path, flags, CONTROLLER_FILE_MODE)
    try:
        with os.fdopen(descriptor, "wb", closefd=False) as handle:
            handle.write(payload)
            handle.flush()
            os.fchmod(handle.fileno(), CONTROLLER_FILE_MODE)
            os.fsync(handle.fileno())
    finally:
        os.close(descriptor)
    installed = _read_secure_controller_file(path, label=label)
    if installed != payload:
        raise CloneError(f"{label} write verification failed")
    _fsync_directory(path.parent)


def _replace_secure_file(path: Path, payload: bytes, *, label: str) -> None:
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.tmp-",
        dir=path.parent,
    )
    temporary = Path(temporary_name)
    replaced = False
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(payload)
            handle.flush()
            os.fchmod(handle.fileno(), CONTROLLER_FILE_MODE)
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        replaced = True
        installed = _read_secure_controller_file(path, label=label)
        if installed != payload:
            raise CloneError(f"{label} replacement verification failed")
        _fsync_directory(path.parent)
    finally:
        if not replaced:
            temporary.unlink(missing_ok=True)


def clone_name_for_release(release_id: str) -> str:
    if not RELEASE_ID_RE.fullmatch(release_id) or release_id in {".", ".."}:
        raise CloneError("release id is not a safe release directory name")
    digest = hashlib.sha256(release_id.encode("utf-8")).hexdigest()[:20]
    name = f"{CLONE_PREFIX}{digest}"
    validate_clone_name(name)
    return name


def validate_source_database(name: str) -> str:
    if name == SOURCE_DATABASE:
        return name
    return validate_clone_name(name)


def validate_clone_name(name: str) -> str:
    if not DATABASE_IDENTIFIER_RE.fullmatch(name) or not name.startswith(CLONE_PREFIX):
        raise CloneError("target database is not a safe release-specific clone name")
    return name


def required_free_bytes(source_size_bytes: int) -> int:
    if source_size_bytes < 0:
        raise CloneError("source database size is invalid")
    return source_size_bytes + MIN_DISK_HEADROOM_BYTES


def assert_disk_headroom(*, source_size_bytes: int, free_bytes: int) -> None:
    required = required_free_bytes(source_size_bytes)
    if free_bytes < required:
        raise CloneError(
            "insufficient PostgreSQL disk headroom for clone: "
            f"required={required} available={free_bytes}"
        )


def _split_database_url(value: str) -> Any:
    try:
        parsed = urlsplit(value)
    except ValueError:
        raise CloneError("DATABASE_URL is invalid") from None
    if parsed.scheme not in {"postgres", "postgresql"}:
        raise CloneError("DATABASE_URL must use postgres or postgresql")
    if parsed.fragment:
        raise CloneError("DATABASE_URL fragments are not supported")
    if not parsed.path.startswith("/") or parsed.path == "/":
        raise CloneError("DATABASE_URL must contain one database path")
    database_name = unquote(parsed.path[1:])
    if not database_name or "/" in database_name or "\x00" in database_name:
        raise CloneError("DATABASE_URL must contain one database path")
    return parsed, database_name


def database_name_from_url(value: str) -> str:
    _parsed, database_name = _split_database_url(value)
    return database_name


def replace_database_name(value: str, *, expected: str, target: str) -> str:
    parsed, current = _split_database_url(value)
    if urlunsplit(parsed) != value:
        raise CloneError(
            "DATABASE_URL must be canonical before a database-only path replacement"
        )
    if current != expected:
        raise CloneError(f"DATABASE_URL database identity must be {expected}")
    if target == SOURCE_DATABASE:
        validate_source_database(target)
    else:
        validate_clone_name(target)
    return urlunsplit(
        (parsed.scheme, parsed.netloc, f"/{quote(target, safe='')}", parsed.query, "")
    )


def _database_url_lines(
    env_path: Path,
) -> tuple[list[str], dict[str, tuple[int, str, str, str, str, str]]]:
    if not env_path.is_file() or env_path.is_symlink():
        raise CloneError("environment file must be a regular non-symlink file")
    try:
        lines = env_path.read_bytes().decode("utf-8").splitlines(keepends=True)
    except UnicodeDecodeError:
        raise CloneError("environment file must be valid UTF-8") from None
    matches: dict[str, tuple[int, str, str, str, str, str]] = {}
    pattern = re.compile(
        r"^(\s*(DATABASE_URL|DATABASE_POOL_URL)\s*=\s*)(.*?)(\r?\n)?$"
    )
    for index, line in enumerate(lines):
        if line.lstrip().startswith("#"):
            continue
        match = pattern.match(line)
        if not match:
            continue
        key = match.group(2)
        if key in matches:
            raise CloneError(f"environment file contains duplicate key: {key}")
        body = match.group(3)
        trailing = body[len(body.rstrip()) :]
        raw = body.rstrip()
        quote_char = ""
        if len(raw) >= 2 and raw[0] == raw[-1] and raw[0] in {"'", '"'}:
            quote_char = raw[0]
            raw = raw[1:-1]
        if not raw:
            raise CloneError(f"{key} is empty")
        newline = "\r\n" if line.endswith("\r\n") else "\n" if line.endswith("\n") else ""
        matches[key] = (index, match.group(1), raw, quote_char, trailing, newline)
    if "DATABASE_URL" not in matches:
        raise CloneError("environment file must contain exactly one active DATABASE_URL")
    return lines, matches


def read_database_identity(env_path: Path) -> str:
    _lines, matches = _database_url_lines(env_path)
    _index, _prefix, value, _quote, _trailing, _newline = matches["DATABASE_URL"]
    return database_name_from_url(value)


def env_fingerprint(env_path: Path) -> str:
    if not env_path.is_file() or env_path.is_symlink():
        raise CloneError("environment file must be a regular non-symlink file")
    return hashlib.sha256(env_path.read_bytes()).hexdigest()


def env_state(
    env_path: Path,
    *,
    allow_runtime_pool: bool = False,
) -> dict[str, str]:
    values = _load_environment_without_logging(env_path)
    database_name = read_database_identity(env_path)
    pool_url = values.get("DATABASE_POOL_URL", "").strip()
    pool_flag = values.get(
        "DB_USE_PGBOUNCER",
        "1" if pool_url else "0",
    ).strip().lower()
    if pool_flag not in {"0", "false", "no", "off", "1", "true", "yes", "on"}:
        raise CloneError("DB_USE_PGBOUNCER must be an explicit boolean")
    pool_enabled = pool_flag in {"1", "true", "yes", "on"}
    if pool_enabled and not pool_url:
        raise CloneError("DB_USE_PGBOUNCER requires DATABASE_POOL_URL")
    if pool_url and database_name_from_url(pool_url) != database_name:
        raise CloneError("DATABASE_POOL_URL database identity must match DATABASE_URL")
    if not allow_runtime_pool and pool_enabled:
        raise CloneError(
            "staging clone requires DB_USE_PGBOUNCER to be disabled"
        )
    return {
        "database_name": database_name,
        "env_sha256": env_fingerprint(env_path),
    }


def switch_environment_database(
    env_path: Path,
    *,
    expected_source: str,
    target: str,
) -> dict[str, str]:
    env_state(env_path)
    lines, matches = _database_url_lines(env_path)
    for key in ("DATABASE_URL", "DATABASE_POOL_URL"):
        if key not in matches:
            continue
        index, prefix, value, quote_char, trailing, newline = matches[key]
        replacement = replace_database_name(
            value, expected=expected_source, target=target
        )
        lines[index] = (
            f"{prefix}{quote_char}{replacement}{quote_char}{trailing}{newline}"
        )

    info = env_path.stat()
    temporary: Path | None = None
    try:
        fd, raw_path = tempfile.mkstemp(prefix=".env.db-switch-", dir=env_path.parent)
        temporary = Path(raw_path)
        with os.fdopen(fd, "w", encoding="utf-8", newline="") as handle:
            handle.writelines(lines)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temporary, info.st_mode & 0o7777)
        os.chown(temporary, info.st_uid, info.st_gid)
        os.replace(temporary, env_path)
        temporary = None
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)

    state = env_state(env_path)
    if state["database_name"] != target:
        raise CloneError("environment database switch did not persist the target identity")
    return state


def _psycopg() -> Any:
    try:
        import psycopg
        from psycopg import sql
    except Exception as exc:  # pragma: no cover - exercised only on remote runtime
        raise CloneError("psycopg is required for staging database administration") from exc
    return psycopg, sql


def _admin_connection(*, database: str = "postgres") -> Any:
    psycopg, _sql = _psycopg()
    # No host, username, password, or application URL: this deliberately relies
    # on local peer authentication for the postgres OS account.
    return psycopg.connect(dbname=database, autocommit=True)


def _wait_for_source_connections_to_drain(
    cursor: Any,
    *,
    source: str,
    timeout_seconds: float = SOURCE_CONNECTION_DRAIN_TIMEOUT_SECONDS,
    poll_seconds: float = SOURCE_CONNECTION_DRAIN_POLL_SECONDS,
) -> None:
    """Wait briefly for PostgreSQL client backends to close after service stop.

    ``systemctl stop`` waits for the application processes, but PostgreSQL can
    retain their closing backend for a short interval.  A single instantaneous
    count therefore creates a deployment race.  This remains fail-closed: it
    never terminates sessions and never proceeds while one remains.
    """

    deadline = time.monotonic() + timeout_seconds
    while True:
        cursor.execute(
            "SELECT count(*) FROM pg_stat_activity WHERE datname = %s",
            (source,),
        )
        active_connections = int(cursor.fetchone()[0])
        if active_connections == 0:
            return
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise CloneError(
                "source database still has active connections after service stop "
                f"and {timeout_seconds:g}s drain grace: count={active_connections}"
            )
        time.sleep(min(poll_seconds, remaining))


def create_clone(*, source: str, target: str) -> dict[str, int | str]:
    validate_source_database(source)
    validate_clone_name(target)
    if source == target:
        raise CloneError("source and target database identities must differ")
    _, sql = _psycopg()
    with _admin_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT pg_database_size(datname), pg_get_userbyid(datdba)
                FROM pg_database
                WHERE datname = %s
                """,
                (source,),
            )
            source_row = cur.fetchone()
            if source_row is None:
                raise CloneError("reviewed source database does not exist")
            source_size, source_owner = int(source_row[0]), str(source_row[1])
            cur.execute("SELECT 1 FROM pg_database WHERE datname = %s", (target,))
            if cur.fetchone() is not None:
                raise CloneError("release-specific clone database already exists")
            _wait_for_source_connections_to_drain(cur, source=source)
            cur.execute("SHOW data_directory")
            data_directory = Path(str(cur.fetchone()[0]))
            free_bytes = int(shutil.disk_usage(data_directory).free)
            assert_disk_headroom(
                source_size_bytes=source_size,
                free_bytes=free_bytes,
            )
            cur.execute(
                sql.SQL("CREATE DATABASE {} WITH TEMPLATE {} OWNER {}").format(
                    sql.Identifier(target),
                    sql.Identifier(source),
                    sql.Identifier(source_owner),
                )
            )
    return {
        "source_database": source,
        "target_database": target,
        "source_size_bytes": source_size,
        "free_bytes_before": free_bytes,
        "minimum_headroom_bytes": MIN_DISK_HEADROOM_BYTES,
    }


def drop_clone(*, target: str) -> None:
    validate_clone_name(target)
    _, sql = _psycopg()
    with _admin_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                sql.SQL("DROP DATABASE IF EXISTS {} WITH (FORCE)").format(
                    sql.Identifier(target)
                )
            )


def verify_migration(*, target: str, expected_version: str) -> None:
    validate_clone_name(target)
    if not re.fullmatch(r"[0-9]{3}_[A-Za-z0-9_.-]+\.sql", expected_version):
        raise CloneError("expected migration filename is invalid")
    with _admin_connection(database=target) as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT to_regclass('public.schema_migrations')")
            if cur.fetchone()[0] is None:
                raise CloneError("clone migration ledger is missing")
            cur.execute(
                "SELECT 1 FROM schema_migrations WHERE version_key = %s",
                (expected_version,),
            )
            if cur.fetchone() is None:
                raise CloneError("clone does not contain the expected migration ledger entry")


def _load_environment_without_logging(env_path: Path) -> dict[str, str]:
    if not env_path.is_file() or env_path.is_symlink():
        raise CloneError("environment file must be a regular non-symlink file")
    values: dict[str, str] = {}
    for line in env_path.read_text(encoding="utf-8").splitlines():
        raw = line.strip()
        if not raw or raw.startswith("#") or "=" not in raw:
            continue
        key, value = raw.split("=", 1)
        key = key.strip()
        value = value.strip()
        if not ENV_KEY_RE.fullmatch(key):
            raise CloneError("environment file contains an invalid key")
        if key in values:
            raise CloneError(f"environment file contains duplicate key: {key}")
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
            value = value[1:-1]
        if "\x00" in value:
            raise CloneError("environment file contains a NUL byte")
        values[key] = value
    return values


def run_migrations_only(
    *,
    env_path: Path,
    release_path: Path,
    expected_database: str,
    app_user: str,
) -> None:
    validate_clone_name(expected_database)
    if pwd.getpwuid(os.geteuid()).pw_name != app_user:
        raise CloneError(f"migrations-only process must run as {app_user}")
    release_path = release_path.resolve()
    backend_path = release_path / "backend"
    migrations_path = release_path / "migrations"
    if not backend_path.is_dir() or not migrations_path.is_dir():
        raise CloneError("migrations-only release payload is incomplete")

    values = _load_environment_without_logging(env_path)
    database_url = values.get("DATABASE_URL", "")
    if database_name_from_url(database_url) != expected_database:
        raise CloneError("migrations-only process database identity mismatch")
    os.environ.update(values)
    os.environ.update(
        {
            "APP_ROLE": "migration-runner",
            "DB_RUNTIME_BACKEND": "postgres",
            "ENABLE_BROWSER": "0",
            "ENABLE_LOCAL_ORCHESTRATOR": "0",
            "ENABLE_SCHEDULER": "0",
            "ENABLE_UPLOAD_CLEANUP": "0",
            "ENVIRONMENT": "production",
            "LOG_LEVEL": "CRITICAL",
            "PYTHONDONTWRITEBYTECODE": "1",
            "RUNTIME_ENV_QUIET": "1",
            "VKPI_DB_STARTUP_MODE": "migrations-only",
            "VKPI_SKIP_DOTENV": "1",
        }
    )
    sys.path.insert(0, str(backend_path))
    os.chdir(release_path)

    try:
        import asyncio

        from app.db.connection import close_db_runtime_sync, init_db_runtime

        asyncio.run(init_db_runtime())
        close_db_runtime_sync()
    except Exception as exc:
        raise CloneError(
            f"migrations-only runner failed: {type(exc).__name__}"
        ) from None


def write_release_receipt(
    *,
    root: Path,
    release_id: str,
    source_database: str,
    target_database: str,
    env_fingerprint_before: str,
    env_fingerprint_clone: str,
    migration_version: str,
    state: str,
    rollback_env_fingerprint: str = "",
) -> Path:
    validate_source_database(source_database)
    validate_clone_name(target_database)
    if clone_name_for_release(release_id) != target_database:
        raise CloneError("receipt target database does not match the release id")
    for fingerprint in (env_fingerprint_before, env_fingerprint_clone):
        if not re.fullmatch(r"[0-9a-f]{64}", fingerprint):
            raise CloneError("receipt env fingerprint must be a SHA-256 digest")
    if rollback_env_fingerprint and not re.fullmatch(
        r"[0-9a-f]{64}", rollback_env_fingerprint
    ):
        raise CloneError("rollback env fingerprint must be a SHA-256 digest")
    if not re.fullmatch(r"[0-9]{3}_[A-Za-z0-9_.-]+\.sql", migration_version):
        raise CloneError("receipt migration filename is invalid")
    if state not in {"migrated-not-activated", "activated", "rollback-restored"}:
        raise CloneError("receipt state is invalid")
    if state == "rollback-restored" and not rollback_env_fingerprint:
        raise CloneError("rollback-restored receipt requires the restored env fingerprint")
    if state != "rollback-restored" and rollback_env_fingerprint:
        raise CloneError("non-rollback receipt must not declare a rollback env fingerprint")

    rollback_dir = _rollback_receipt_directory(root, release_id)
    receipt_path = rollback_dir / "database-clone.json"
    payload = {
        "schema": 1,
        "release_id": release_id,
        "database_strategy": "staging-clone",
        "source_database": source_database,
        "target_database": target_database,
        "env_fingerprint_before": env_fingerprint_before,
        "env_fingerprint_clone": env_fingerprint_clone,
        "migration_version": migration_version,
        "state": state,
        "rollback_env_fingerprint": rollback_env_fingerprint or None,
        "secrets_included": False,
    }
    payload_bytes = (json.dumps(payload, sort_keys=True) + "\n").encode("utf-8")
    if receipt_path.exists() or receipt_path.is_symlink():
        try:
            existing_payload = _read_secure_controller_file(
                receipt_path,
                label="database clone receipt",
            )
            existing = json.loads(existing_payload.decode("utf-8"))
        except (OSError, UnicodeDecodeError, ValueError, TypeError):
            raise CloneError("existing clone receipt is invalid") from None
        immutable_keys = {
            "release_id",
            "database_strategy",
            "source_database",
            "target_database",
            "env_fingerprint_before",
            "env_fingerprint_clone",
            "migration_version",
            "secrets_included",
        }
        if any(existing.get(key) != payload.get(key) for key in immutable_keys):
            raise CloneError("existing clone receipt immutable identity mismatch")
        allowed_transitions = {
            "migrated-not-activated": {"activated", "rollback-restored"},
            "activated": {"rollback-restored"},
            "rollback-restored": set(),
        }
        old_state = str(existing.get("state") or "")
        if state == old_state and existing != payload:
            raise CloneError("existing clone receipt idempotent state mismatch")
        if state == old_state:
            return receipt_path
        if state != old_state and state not in allowed_transitions.get(old_state, set()):
            raise CloneError(f"invalid clone receipt state transition: {old_state}")
        _replace_secure_file(
            receipt_path,
            payload_bytes,
            label="database clone receipt",
        )
    else:
        _write_new_secure_file(
            receipt_path,
            payload_bytes,
            label="database clone receipt",
        )
    return receipt_path


def prove_active_source(
    *,
    root: Path,
    expected_database: str,
    allow_runtime_pool: bool = False,
) -> dict[str, str]:
    validate_source_database(expected_database)
    state = env_state(root / ".env", allow_runtime_pool=allow_runtime_pool)
    if state["database_name"] != expected_database:
        raise CloneError("active environment database identity mismatch")
    if expected_database == SOURCE_DATABASE:
        current = root / "current"
        if current.is_symlink():
            try:
                active_manifest_path = current.resolve(strict=True) / ".vkpi-release.json"
                active_manifest = (
                    json.loads(active_manifest_path.read_text(encoding="utf-8"))
                    if active_manifest_path.is_file()
                    else {}
                )
            except (OSError, ValueError, TypeError):
                raise CloneError("active release manifest is invalid") from None
            if active_manifest.get("database_strategy") in {
                "staging-clone",
                "reuse-active-clone",
            }:
                raise CloneError(
                    "refusing to fall back from an active release clone to the legacy base"
                )
        return {
            **state,
            "source_kind": "legacy-base",
            "source_release_id": "",
        }

    root = root.resolve()
    releases = (root / "releases").resolve()
    current = root / "current"
    if not current.is_symlink():
        raise CloneError("active clone source requires the atomic current pointer")
    try:
        active_release = current.resolve(strict=True)
    except OSError:
        raise CloneError("atomic current pointer is broken") from None
    if releases not in active_release.parents or not active_release.is_dir():
        raise CloneError("atomic current pointer escapes the releases directory")
    manifest_path = active_release / ".vkpi-release.json"
    if not manifest_path.is_file():
        raise CloneError("active clone source release manifest is missing")
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        raise CloneError("active clone source release manifest is invalid") from None
    active_release_id = str(manifest.get("release_id") or "")
    strategy = manifest.get("database_strategy")
    if strategy == "staging-clone":
        database_owner_release_id = active_release_id
    elif strategy == "reuse-active-clone":
        database_owner_release_id = str(
            manifest.get("database_owner_release_id") or ""
        )
        if manifest.get("pending_migrations") not in ([], None) or manifest.get(
            "forward_compatible_migrations"
        ) not in ([], None):
            raise CloneError("active clone-reuse manifest is not app-only")
    else:
        raise CloneError("active release manifest lost the clone database lineage")
    if (
        manifest.get("target_database") != expected_database
        or clone_name_for_release(database_owner_release_id) != expected_database
    ):
        raise CloneError("active release manifest does not prove the clone source")

    try:
        owner_release = (releases / database_owner_release_id).resolve(strict=True)
    except OSError:
        raise CloneError("database owner release is missing") from None
    if releases not in owner_release.parents or not owner_release.is_dir():
        raise CloneError("database owner release escapes the releases directory")
    owner_manifest_path = owner_release / ".vkpi-release.json"
    if not owner_manifest_path.is_file():
        raise CloneError("database owner release manifest is missing")
    try:
        owner_manifest = json.loads(owner_manifest_path.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        raise CloneError("database owner release manifest is invalid") from None
    if (
        owner_manifest.get("release_id") != database_owner_release_id
        or owner_manifest.get("database_strategy") != "staging-clone"
        or owner_manifest.get("target_database") != expected_database
    ):
        raise CloneError("database owner release manifest does not prove the clone")

    rollback_dir = _rollback_receipt_directory(root, database_owner_release_id)
    receipt_path = rollback_dir / "database-clone.json"
    try:
        receipt_payload = _read_secure_controller_file(
            receipt_path,
            label="database clone receipt",
        )
        receipt = json.loads(receipt_payload.decode("utf-8"))
    except (OSError, UnicodeDecodeError, ValueError, TypeError):
        raise CloneError("active clone source receipt is invalid") from None
    if (
        receipt.get("release_id") != database_owner_release_id
        or receipt.get("database_strategy") != "staging-clone"
        or receipt.get("source_database") != owner_manifest.get("source_database")
        or receipt.get("target_database") != expected_database
        or receipt.get("state") != "activated"
        or receipt.get("secrets_included") is not False
    ):
        raise CloneError("active receipt does not prove the current clone source")
    return {
        **state,
        "source_kind": "prior-release-clone",
        "source_release_id": database_owner_release_id,
        "database_owner_release_id": database_owner_release_id,
        "active_release_id": active_release_id,
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest="command", required=True)

    name = subparsers.add_parser("name")
    name.add_argument("--release-id", required=True)

    state = subparsers.add_parser("env-state")
    state.add_argument("--env-file", required=True)

    assert_env = subparsers.add_parser("assert-env")
    assert_env.add_argument("--env-file", required=True)
    assert_env.add_argument("--expected-db", required=True)
    assert_env.add_argument("--allow-runtime-pool", action="store_true")

    switch = subparsers.add_parser("switch-env")
    switch.add_argument("--env-file", required=True)
    switch.add_argument("--expected-source-db", required=True)
    switch.add_argument("--target-db", required=True)

    create = subparsers.add_parser("create")
    create.add_argument("--source-db", required=True)
    create.add_argument("--target-db", required=True)

    drop = subparsers.add_parser("drop")
    drop.add_argument("--target-db", required=True)

    verify = subparsers.add_parser("verify-migration")
    verify.add_argument("--target-db", required=True)
    verify.add_argument("--expected-version", required=True)

    migrate = subparsers.add_parser("run-migrations-only")
    migrate.add_argument("--env-file", required=True)
    migrate.add_argument("--release-path", required=True)
    migrate.add_argument("--expected-db", required=True)
    migrate.add_argument("--app-user", required=True)

    receipt = subparsers.add_parser("write-receipt")
    receipt.add_argument("--root", required=True)
    receipt.add_argument("--release-id", required=True)
    receipt.add_argument("--source-db", required=True)
    receipt.add_argument("--target-db", required=True)
    receipt.add_argument("--env-fingerprint-before", required=True)
    receipt.add_argument("--env-fingerprint-clone", required=True)
    receipt.add_argument("--migration-version", required=True)
    receipt.add_argument("--state", required=True)
    receipt.add_argument("--rollback-env-fingerprint", default="")

    prove = subparsers.add_parser("prove-active-source")
    prove.add_argument("--root", required=True)
    prove.add_argument("--expected-db", required=True)
    prove.add_argument("--allow-runtime-pool", action="store_true")
    return parser


def _write_stdout_line(value: object) -> None:
    sys.stdout.write(f"{value}\n")


def main() -> int:
    try:
        args = _parser().parse_args()
        if args.command == "name":
            _write_stdout_line(clone_name_for_release(args.release_id))
        elif args.command == "env-state":
            _write_stdout_line(json.dumps(env_state(Path(args.env_file)), sort_keys=True))
        elif args.command == "assert-env":
            state = env_state(
                Path(args.env_file),
                allow_runtime_pool=args.allow_runtime_pool,
            )
            if state["database_name"] != args.expected_db:
                raise CloneError(
                    f"environment database identity must be {args.expected_db}"
                )
            _write_stdout_line(json.dumps(state, sort_keys=True))
        elif args.command == "switch-env":
            state = switch_environment_database(
                Path(args.env_file),
                expected_source=args.expected_source_db,
                target=args.target_db,
            )
            _write_stdout_line(json.dumps(state, sort_keys=True))
        elif args.command == "create":
            _write_stdout_line(
                json.dumps(
                    create_clone(source=args.source_db, target=args.target_db),
                    sort_keys=True,
                )
            )
        elif args.command == "drop":
            drop_clone(target=args.target_db)
            _write_stdout_line(json.dumps({"dropped_database": args.target_db}, sort_keys=True))
        elif args.command == "verify-migration":
            verify_migration(
                target=args.target_db,
                expected_version=args.expected_version,
            )
            _write_stdout_line(
                json.dumps(
                    {
                        "database_name": args.target_db,
                        "migration_version": args.expected_version,
                    },
                    sort_keys=True,
                )
            )
        elif args.command == "run-migrations-only":
            run_migrations_only(
                env_path=Path(args.env_file),
                release_path=Path(args.release_path),
                expected_database=args.expected_db,
                app_user=args.app_user,
            )
            _write_stdout_line(
                json.dumps(
                    {
                        "database_name": args.expected_db,
                        "startup_mode": "migrations-only",
                    },
                    sort_keys=True,
                )
            )
        elif args.command == "write-receipt":
            path = write_release_receipt(
                root=Path(args.root),
                release_id=args.release_id,
                source_database=args.source_db,
                target_database=args.target_db,
                env_fingerprint_before=args.env_fingerprint_before,
                env_fingerprint_clone=args.env_fingerprint_clone,
                migration_version=args.migration_version,
                state=args.state,
                rollback_env_fingerprint=args.rollback_env_fingerprint,
            )
            _write_stdout_line(
                json.dumps(
                    {"receipt": str(path), "state": args.state},
                    sort_keys=True,
                )
            )
        elif args.command == "prove-active-source":
            _write_stdout_line(
                json.dumps(
                    prove_active_source(
                        root=Path(args.root),
                        expected_database=args.expected_db,
                        allow_runtime_pool=args.allow_runtime_pool,
                    ),
                    sort_keys=True,
                )
            )
        else:  # pragma: no cover - argparse requires a known command
            raise CloneError("unsupported staging clone command")
    except (CloneError, OSError, ValueError) as exc:
        sys.stderr.write(f"staging database clone failed: {exc}\n")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
