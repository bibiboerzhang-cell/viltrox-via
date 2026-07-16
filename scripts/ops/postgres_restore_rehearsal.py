#!/usr/bin/env python3
"""Prove a V-KPI PostgreSQL dump by restoring it into a disposable local DB.

The command is fail-closed and deliberately narrower than a general restore
tool.  It refuses the production cluster and requires a short-lived,
root-attested PostgreSQL instance under the reviewed isolated-cluster root,
with a non-default Unix socket/port and an exact system identifier.  It
generates its own ``vkpi_restore_rehearsal_*`` database name, inspects the
restored database in a read-only transaction and always drops the temporary
database before a successful receipt is emitted.

No production database URL, hostname, username or password is accepted by the
CLI.  Both ``--execute`` and the exact confirmation environment value are
required before any PostgreSQL connection or process is created.

Until signed backup provenance, a trusted receipt consumer and a crash
scavenger are implemented, a successful rehearsal remains diagnostic-only and
exits non-zero.  It cannot be used by itself to authorize a cloud release.
"""

from __future__ import annotations

import argparse
import getpass
import hashlib
import ipaddress
import json
import os
import pwd
import re
import shutil
import stat
import subprocess
import sys
import tempfile
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Mapping, Protocol, Sequence


_STDOUT_UTILS_DIR = Path(__file__).resolve().parents[1]
if str(_STDOUT_UTILS_DIR) not in sys.path:
    sys.path.insert(1, str(_STDOUT_UTILS_DIR))
from stdout_utils import out as stdout_out  # noqa: E402

if __package__:
    from .postgres_restore_evidence import build_preflight_receipt, write_private_json
else:
    from postgres_restore_evidence import build_preflight_receipt, write_private_json


CONFIRM_ENV = "VKPI_PG_RESTORE_REHEARSAL_CONFIRM"
CONFIRM_VALUE = "CREATE_VERIFY_DROP_LOCAL_REHEARSAL_DB"
DATABASE_PREFIX = "vkpi_restore_rehearsal_"
ISOLATED_CLUSTER_ROOT = Path("/var/lib/vkpi-restore-rehearsal-clusters")
ISOLATED_PORT_MIN = 20000
ISOLATED_PORT_MAX = 60999
DATABASE_RE = re.compile(r"^vkpi_restore_rehearsal_[0-9]{8}t[0-9]{6}z_[0-9a-f]{16}$")
MIGRATION_RE = re.compile(r"^[0-9]{3}_[A-Za-z0-9_.-]+\.sql$")
TABLE_RE = re.compile(r"^[a-z_][a-z0-9_]{0,62}$")
RELEASE_ID_RE = re.compile(r"^[A-Za-z0-9_.-]{1,120}$")
GIT_SHA_RE = re.compile(r"^[0-9a-f]{40}$")
DEFAULT_ANCHORS = (
    "schema_migrations",
    "users",
    "vkpi_projects",
    "vkpi_kol_pool",
    "vkpi_dealers",
    "vkpi_events",
)
PG_ENV_KEYS = (
    "PGHOST",
    "PGHOSTADDR",
    "PGPORT",
    "PGDATABASE",
    "PGUSER",
    "PGPASSWORD",
    "PGPASSFILE",
    "PGSERVICE",
    "PGSERVICEFILE",
    "PGOPTIONS",
    "DATABASE_URL",
    "DATABASE_POOL_URL",
)


@dataclass(frozen=True)
class ClusterBinding:
    data_root: Path
    socket_dir: Path
    port: int
    owner_user: str
    system_identifier: str
    attestation_sha256: str


class RestoreError(RuntimeError):
    """Credential-free restore rehearsal failure."""

    def __init__(self, operation: str, category: str):
        self.operation = operation
        self.category = category
        super().__init__(f"PostgreSQL restore rehearsal failed: operation={operation} category={category}")


class RestoreRunner(Protocol):
    def check_archive(self, dump: Path) -> None: ...

    def restore(self, dump: Path, database: str) -> None: ...


class DatabaseOps(Protocol):
    def assert_local_admin(self) -> None: ...

    def database_exists(self, database: str) -> bool: ...

    def create_database(self, database: str) -> None: ...

    def inspect_database(self, database: str, anchors: Sequence[str]) -> dict[str, Any]: ...

    def drop_database(self, database: str) -> None: ...


def utcnow() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def temporary_database_name() -> str:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ").lower()
    name = f"{DATABASE_PREFIX}{stamp}_{uuid.uuid4().hex[:16]}"
    if not DATABASE_RE.fullmatch(name) or len(name) > 63:
        raise RestoreError("configure", "unsafe_generated_database_name")
    return name


def _parse_utc(value: Any) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return None
    return parsed.astimezone(timezone.utc)


def _owned_private_directory(path: Path, *, owner_uid: int) -> Path:
    if not path.is_absolute():
        raise RestoreError("cluster_attestation", "cluster_path_not_absolute")
    try:
        info = path.lstat()
        resolved = path.resolve(strict=True)
        resolved.relative_to(ISOLATED_CLUSTER_ROOT.resolve(strict=True))
    except (OSError, ValueError):
        raise RestoreError("cluster_attestation", "cluster_path_outside_reviewed_root") from None
    if (
        path.is_symlink()
        or not stat.S_ISDIR(info.st_mode)
        or info.st_uid != owner_uid
        or info.st_mode & (stat.S_IWGRP | stat.S_IRWXO)
    ):
        raise RestoreError("cluster_attestation", "cluster_directory_not_private_owned")
    return resolved


def load_cluster_binding(
    path: Path,
    *,
    release_id: str,
    expected_app_sha: str,
    owner_user: str,
    owner_uid: int,
) -> ClusterBinding:
    try:
        info = path.lstat()
        raw = path.read_bytes()
        path.resolve(strict=True).relative_to(ISOLATED_CLUSTER_ROOT.resolve(strict=True))
    except (OSError, ValueError):
        raise RestoreError("cluster_attestation", "attestation_unreadable") from None
    if (
        path.is_symlink()
        or not stat.S_ISREG(info.st_mode)
        or info.st_nlink != 1
        or info.st_uid != 0
        or info.st_mode & (stat.S_IWGRP | stat.S_IWOTH)
        or len(raw) > 16 * 1024
    ):
        raise RestoreError("cluster_attestation", "attestation_not_root_sealed")
    try:
        payload = json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise RestoreError("cluster_attestation", "attestation_invalid_json") from None
    required = {
        "schema_version",
        "purpose",
        "release_id",
        "expected_app_sha",
        "owner_user",
        "owner_uid",
        "data_root",
        "socket_dir",
        "port",
        "system_identifier",
        "created_at",
        "expires_at",
        "listen_addresses",
    }
    if not isinstance(payload, dict) or set(payload) != required:
        raise RestoreError("cluster_attestation", "attestation_schema_mismatch")
    now = datetime.now(timezone.utc)
    created_at = _parse_utc(payload.get("created_at"))
    expires_at = _parse_utc(payload.get("expires_at"))
    port = payload.get("port")
    system_identifier = str(payload.get("system_identifier") or "")
    identity_ok = (
        payload.get("schema_version") == 1
        and payload.get("purpose") == "vkpi_disposable_restore_rehearsal_cluster"
        and payload.get("release_id") == release_id
        and payload.get("expected_app_sha") == expected_app_sha
        and payload.get("owner_user") == owner_user
        and payload.get("owner_uid") == owner_uid
        and payload.get("listen_addresses") == ""
        and isinstance(port, int)
        and not isinstance(port, bool)
        and ISOLATED_PORT_MIN <= port <= ISOLATED_PORT_MAX
        and re.fullmatch(r"[0-9]{10,30}", system_identifier) is not None
        and created_at is not None
        and expires_at is not None
        and created_at <= now
        and now < expires_at <= now.replace(microsecond=0) + timedelta(hours=6)
    )
    if not identity_ok:
        raise RestoreError("cluster_attestation", "attestation_identity_or_freshness_mismatch")
    data_root = _owned_private_directory(Path(str(payload["data_root"])), owner_uid=owner_uid)
    socket_dir = _owned_private_directory(Path(str(payload["socket_dir"])), owner_uid=owner_uid)
    if data_root == socket_dir:
        raise RestoreError("cluster_attestation", "data_and_socket_directories_must_differ")
    return ClusterBinding(
        data_root=data_root,
        socket_dir=socket_dir,
        port=int(port),
        owner_user=owner_user,
        system_identifier=system_identifier,
        attestation_sha256=hashlib.sha256(raw).hexdigest(),
    )


def _safe_file(path: Path, *, maximum_bytes: int) -> os.stat_result:
    try:
        info = path.lstat()
    except OSError:
        raise RestoreError("bundle", "file_unreadable") from None
    if (
        path.is_symlink()
        or not stat.S_ISREG(info.st_mode)
        or info.st_nlink != 1
        or info.st_uid != os.geteuid()
        or info.st_mode & (stat.S_IRWXG | stat.S_IRWXO)
    ):
        raise RestoreError("bundle", "file_not_private_owned_regular")
    if info.st_size <= 0 or info.st_size > maximum_bytes:
        raise RestoreError("bundle", "file_size_invalid")
    return info


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    try:
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
    except OSError:
        raise RestoreError("bundle", "dump_read_failed") from None
    return digest.hexdigest()


def verify_bundle(dump: Path, sidecar: Path) -> dict[str, Any]:
    dump_info = _safe_file(dump, maximum_bytes=1024**4)
    _safe_file(sidecar, maximum_bytes=4096)
    try:
        fields = sidecar.read_text(encoding="ascii").strip().split()
    except (OSError, UnicodeDecodeError):
        raise RestoreError("bundle", "checksum_sidecar_invalid") from None
    if not fields or not re.fullmatch(r"[0-9a-fA-F]{64}", fields[0]):
        raise RestoreError("bundle", "checksum_sidecar_invalid")
    expected = fields[0].lower()
    actual = sha256_file(dump)
    if actual != expected:
        raise RestoreError("bundle", "sha256_mismatch")
    return {
        "dump_name": dump.name,
        "dump_bytes": dump_info.st_size,
        "dump_sha256": actual,
        "checksum_verified": True,
    }


def pin_verified_bundle(dump: Path, sidecar: Path) -> tuple[Path, Path, dict[str, Any]]:
    """Copy one opened inode to a private path and bind restore to those bytes."""

    dump_info = _safe_file(dump, maximum_bytes=1024**4)
    _safe_file(sidecar, maximum_bytes=4096)
    try:
        fields = sidecar.read_text(encoding="ascii").strip().split()
    except (OSError, UnicodeDecodeError):
        raise RestoreError("bundle", "checksum_sidecar_invalid") from None
    if not fields or not re.fullmatch(r"[0-9a-fA-F]{64}", fields[0]):
        raise RestoreError("bundle", "checksum_sidecar_invalid")
    expected = fields[0].lower()

    temporary_root = Path(tempfile.mkdtemp(prefix="vkpi-pinned-restore-"))
    os.chmod(temporary_root, 0o700)
    pinned = temporary_root / "prod-db.pinned.dump"
    source_fd = -1
    target_fd = -1
    digest = hashlib.sha256()
    try:
        flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
        source_fd = os.open(dump, flags)
        opened = os.fstat(source_fd)
        if (
            opened.st_dev != dump_info.st_dev
            or opened.st_ino != dump_info.st_ino
            or opened.st_size != dump_info.st_size
            or opened.st_uid != os.geteuid()
            or opened.st_mode & (stat.S_IRWXG | stat.S_IRWXO)
        ):
            raise RestoreError("bundle", "dump_changed_before_pin")
        target_fd = os.open(pinned, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        while True:
            chunk = os.read(source_fd, 1024 * 1024)
            if not chunk:
                break
            digest.update(chunk)
            view = memoryview(chunk)
            while view:
                written = os.write(target_fd, view)
                view = view[written:]
        os.fsync(target_fd)
        after = os.fstat(source_fd)
        if (after.st_dev, after.st_ino, after.st_size) != (
            opened.st_dev,
            opened.st_ino,
            opened.st_size,
        ):
            raise RestoreError("bundle", "dump_changed_during_pin")
        actual = digest.hexdigest()
        if actual != expected:
            raise RestoreError("bundle", "sha256_mismatch")
        return (
            temporary_root,
            pinned,
            {
                "dump_name": dump.name,
                "dump_bytes": opened.st_size,
                "dump_sha256": actual,
                "checksum_verified": True,
                "restore_input_pinned": True,
            },
        )
    except Exception:
        shutil.rmtree(temporary_root, ignore_errors=True)
        raise
    finally:
        if source_fd >= 0:
            os.close(source_fd)
        if target_fd >= 0:
            os.close(target_fd)


def _local_peer_environment(*, socket_dir: Path, port: int, user: str) -> dict[str, str]:
    # A clean HOME prevents ~/.pg_service.conf and ~/.pgpass from silently
    # redirecting the rehearsal.  Ambient PG* routing/credential values are
    # discarded; only the fixed local socket and postgres role are supplied.
    env = {
        "PATH": os.environ.get("PATH", "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"),
        "HOME": "/nonexistent/vkpi-restore-rehearsal",
        "LANG": os.environ.get("LANG", "C.UTF-8"),
        "LC_ALL": os.environ.get("LC_ALL", "C.UTF-8"),
        "PGHOST": str(socket_dir),
        "PGPORT": str(port),
        "PGUSER": user,
    }
    return env


class PgRestoreSubprocess:
    def __init__(self, *, socket_dir: Path, port: int, user: str) -> None:
        executable = shutil.which("pg_restore")
        if not executable:
            raise RestoreError("configure", "pg_restore_missing")
        self.executable = executable
        self.socket_dir = socket_dir
        self.port = port
        self.user = user

    def _run(self, argv: list[str], operation: str) -> None:
        # Raw stderr is never captured into a receipt or surfaced to stdout.
        # libpq routing variables are stripped by _local_peer_environment().
        try:
            completed = subprocess.run(
                argv,
                env=_local_peer_environment(
                    socket_dir=self.socket_dir,
                    port=self.port,
                    user=self.user,
                ),
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=1800,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired):
            raise RestoreError(operation, "process_failed_or_timed_out") from None
        if completed.returncode != 0:
            raise RestoreError(operation, "nonzero_exit")

    def check_archive(self, dump: Path) -> None:
        self._run([self.executable, "--list", str(dump)], "archive_list")

    def restore(self, dump: Path, database: str) -> None:
        if not DATABASE_RE.fullmatch(database):
            raise RestoreError("restore", "unsafe_database_name")
        self._run(
            [
                self.executable,
                "--exit-on-error",
                "--no-owner",
                "--no-privileges",
                f"--dbname={database}",
                str(dump),
            ],
            "pg_restore",
        )


def _is_local_server_address(value: Any) -> bool:
    if value in (None, ""):
        return True  # Unix-domain socket connection.
    try:
        return ipaddress.ip_address(str(value)).is_loopback
    except ValueError:
        return False


class PsycopgLocalPeerOps:
    def __init__(
        self,
        *,
        socket_dir: Path,
        port: int,
        user: str,
        expected_data_root: Path,
        expected_system_identifier: str,
    ) -> None:
        try:
            import psycopg
            from psycopg import sql
        except Exception:
            raise RestoreError("configure", "psycopg_missing") from None
        self.psycopg = psycopg
        self.sql = sql
        self.socket_dir = socket_dir
        self.port = port
        self.user = user
        self.expected_data_root = expected_data_root
        self.expected_system_identifier = expected_system_identifier

    def _connect(self, database: str, *, autocommit: bool) -> Any:
        # Deliberately no TCP hostname, password or connection URL.  The
        # reviewed non-default socket/port belongs to a disposable cluster,
        # and assert_local_admin verifies its data root + system identifier.
        try:
            return self.psycopg.connect(
                dbname=database,
                host=str(self.socket_dir),
                port=self.port,
                user=self.user,
                autocommit=autocommit,
            )
        except Exception:
            raise RestoreError("connect", "local_peer_connection_failed") from None

    @staticmethod
    def _verify_identity(row: Any, expected_database: str) -> None:
        if not row or str(row[0]) != expected_database:
            raise RestoreError("identity", "database_name_mismatch")
        if not _is_local_server_address(row[1]):
            raise RestoreError("identity", "non_local_postgres_server")

    def assert_local_admin(self) -> None:
        with self._connect("postgres", autocommit=True) as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT current_database(), inet_server_addr()::text")
                self._verify_identity(cur.fetchone(), "postgres")
                cur.execute("SHOW data_directory")
                data_directory = Path(str(cur.fetchone()[0])).resolve(strict=True)
                cur.execute("SHOW port")
                actual_port = int(cur.fetchone()[0])
                cur.execute("SHOW listen_addresses")
                listen_addresses = str(cur.fetchone()[0] or "").strip()
                cur.execute("SELECT system_identifier::text FROM pg_control_system()")
                system_identifier = str(cur.fetchone()[0])
                if data_directory != self.expected_data_root:
                    raise RestoreError("identity", "isolated_data_directory_mismatch")
                if actual_port != self.port or listen_addresses:
                    raise RestoreError("identity", "isolated_network_policy_mismatch")
                if system_identifier != self.expected_system_identifier:
                    raise RestoreError("identity", "isolated_system_identifier_mismatch")

    def database_exists(self, database: str) -> bool:
        if not DATABASE_RE.fullmatch(database):
            raise RestoreError("database", "unsafe_database_name")
        with self._connect("postgres", autocommit=True) as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT 1 FROM pg_database WHERE datname = %s", (database,))
                return cur.fetchone() is not None

    def create_database(self, database: str) -> None:
        if not DATABASE_RE.fullmatch(database):
            raise RestoreError("create", "unsafe_database_name")
        with self._connect("postgres", autocommit=True) as conn:
            with conn.cursor() as cur:
                try:
                    cur.execute(
                        self.sql.SQL("CREATE DATABASE {} WITH TEMPLATE template0").format(
                            self.sql.Identifier(database)
                        )
                    )
                except Exception:
                    raise RestoreError("create", "create_database_failed") from None

    def _primary_key_columns(self, cur: Any, table: str) -> list[str]:
        cur.execute(
            """
            SELECT a.attname
            FROM pg_index AS i
            JOIN LATERAL unnest(i.indkey) WITH ORDINALITY AS k(attnum, ord) ON TRUE
            JOIN pg_attribute AS a
              ON a.attrelid = i.indrelid AND a.attnum = k.attnum
            WHERE i.indrelid = %s::regclass AND i.indisprimary
            ORDER BY k.ord
            """,
            (f"public.{table}",),
        )
        return [str(row[0]) for row in cur.fetchall()]

    def _anchor(self, cur: Any, table: str) -> dict[str, Any]:
        cur.execute("SELECT to_regclass(%s)", (f"public.{table}",))
        if cur.fetchone()[0] is None:
            raise RestoreError("anchors", f"missing_table_{table}")
        cur.execute(self.sql.SQL("SELECT count(*) FROM {}").format(self.sql.Identifier(table)))
        count = int(cur.fetchone()[0])
        primary_key = self._primary_key_columns(cur, table)
        sample_sha256 = ""
        if primary_key:
            columns = self.sql.SQL(", ").join(self.sql.Identifier(name) for name in primary_key)
            cur.execute(
                self.sql.SQL("SELECT {} FROM {} ORDER BY {} LIMIT 256").format(
                    columns,
                    self.sql.Identifier(table),
                    columns,
                )
            )
            sample = json.dumps(cur.fetchall(), default=str, ensure_ascii=True, separators=(",", ":"))
            sample_sha256 = hashlib.sha256(sample.encode("utf-8")).hexdigest()
        return {
            "row_count": count,
            "primary_key_columns": primary_key,
            "primary_key_sample_limit": 256,
            "primary_key_sample_sha256": sample_sha256,
        }

    def inspect_database(self, database: str, anchors: Sequence[str]) -> dict[str, Any]:
        if not DATABASE_RE.fullmatch(database):
            raise RestoreError("inspect", "unsafe_database_name")
        with self._connect(database, autocommit=False) as conn:
            with conn.cursor() as cur:
                try:
                    cur.execute("SET TRANSACTION READ ONLY")
                    cur.execute("SET LOCAL statement_timeout = '60s'")
                    cur.execute("SELECT current_database(), inet_server_addr()::text")
                    self._verify_identity(cur.fetchone(), database)
                    cur.execute("SELECT MAX(version_key) FROM schema_migrations")
                    migration_max = str(cur.fetchone()[0] or "")
                    anchor_payload = {table: self._anchor(cur, table) for table in anchors}
                except RestoreError:
                    raise
                except Exception:
                    raise RestoreError("inspect", "read_only_anchor_query_failed") from None
        return {
            "transaction_mode": "read_only",
            "migration_max": migration_max,
            "anchors": anchor_payload,
        }

    def drop_database(self, database: str) -> None:
        if not DATABASE_RE.fullmatch(database):
            raise RestoreError("drop", "unsafe_database_name")
        with self._connect("postgres", autocommit=True) as conn:
            with conn.cursor() as cur:
                try:
                    cur.execute(
                        self.sql.SQL("DROP DATABASE IF EXISTS {} WITH (FORCE)").format(
                            self.sql.Identifier(database)
                        )
                    )
                except Exception:
                    raise RestoreError("drop", "drop_database_failed") from None


def run_rehearsal(
    *,
    dump: Path,
    sidecar: Path,
    expected_migration: str,
    anchors: Sequence[str],
    database_ops: DatabaseOps,
    restore_runner: RestoreRunner,
    database: str,
    release_id: str,
    expected_app_sha: str,
) -> dict[str, Any]:
    if not MIGRATION_RE.fullmatch(expected_migration):
        raise RestoreError("configure", "invalid_expected_migration")
    if not anchors or len(set(anchors)) != len(anchors) or any(not TABLE_RE.fullmatch(item) for item in anchors):
        raise RestoreError("configure", "invalid_anchor_table_set")
    if "schema_migrations" not in anchors:
        raise RestoreError("configure", "schema_migrations_anchor_required")
    if not DATABASE_RE.fullmatch(database):
        raise RestoreError("configure", "unsafe_generated_database_name")
    if (
        not RELEASE_ID_RE.fullmatch(release_id)
        or release_id in {".", ".."}
        or not GIT_SHA_RE.fullmatch(expected_app_sha)
    ):
        raise RestoreError("configure", "invalid_release_binding")

    pinned_root, pinned_dump, bundle = pin_verified_bundle(dump, sidecar)
    operations = {
        "dump_pinned": True,
        "archive_list": False,
        "local_admin_identity": False,
        "database_created": False,
        "pg_restore": False,
        "post_restore_dump_reverified": False,
        "read_only_anchors": False,
        "database_dropped": False,
        "absence_confirmed": False,
        "pinned_dump_removed": False,
    }
    failure: RestoreError | None = None
    inspection: dict[str, Any] = {}
    cleanup_candidate = False
    try:
        restore_runner.check_archive(pinned_dump)
        operations["archive_list"] = True
        database_ops.assert_local_admin()
        operations["local_admin_identity"] = True
        if database_ops.database_exists(database):
            raise RestoreError("create", "generated_database_already_exists")
        # From this point a CREATE may commit even if the client loses its
        # response, so finally must look for and remove the unique database.
        cleanup_candidate = True
        database_ops.create_database(database)
        operations["database_created"] = True
        restore_runner.restore(pinned_dump, database)
        operations["pg_restore"] = True
        if sha256_file(pinned_dump) != bundle["dump_sha256"]:
            raise RestoreError("bundle", "pinned_dump_changed_during_restore")
        operations["post_restore_dump_reverified"] = True
        inspection = database_ops.inspect_database(database, anchors)
        if inspection.get("transaction_mode") != "read_only":
            raise RestoreError("inspect", "transaction_not_read_only")
        if inspection.get("migration_max") != expected_migration:
            raise RestoreError("inspect", "migration_max_mismatch")
        anchor_payload = inspection.get("anchors")
        if not isinstance(anchor_payload, dict) or set(anchor_payload) != set(anchors):
            raise RestoreError("inspect", "anchor_set_mismatch")
        business_rows = sum(
            int((anchor_payload.get(table) or {}).get("row_count") or 0)
            for table in anchors
            if table != "schema_migrations"
        )
        if business_rows <= 0:
            raise RestoreError("inspect", "all_business_anchors_empty")
        operations["read_only_anchors"] = True
    except RestoreError as exc:
        failure = exc
    except Exception:
        failure = RestoreError("internal", "unexpected")
    finally:
        if cleanup_candidate:
            try:
                if database_ops.database_exists(database):
                    database_ops.drop_database(database)
                operations["database_dropped"] = True
                operations["absence_confirmed"] = not database_ops.database_exists(database)
            except RestoreError as exc:
                if failure is None:
                    failure = exc
            except Exception:
                if failure is None:
                    failure = RestoreError("drop", "unexpected_cleanup_failure")
            if not operations["absence_confirmed"] and failure is None:
                failure = RestoreError("drop", "absence_not_confirmed")
        try:
            shutil.rmtree(pinned_root)
            operations["pinned_dump_removed"] = not pinned_root.exists()
        except OSError:
            if failure is None:
                failure = RestoreError("bundle_cleanup", "pinned_dump_removal_failed")
        if not operations["pinned_dump_removed"] and failure is None:
            failure = RestoreError("bundle_cleanup", "pinned_dump_removal_not_confirmed")

    if failure is not None:
        failure.operations = operations  # type: ignore[attr-defined]
        failure.bundle = bundle  # type: ignore[attr-defined]
        failure.inspection = inspection  # type: ignore[attr-defined]
        raise failure
    return {
        "schema_version": 1,
        "evidence_type": "vkpi_postgres_restore_rehearsal",
        "status": "passed",
        "checked_at": utcnow(),
        "release_id": release_id,
        "expected_app_sha": expected_app_sha,
        "connection_policy": "root_attested_disposable_cluster_unix_socket_only",
        "temporary_database": database,
        "expected_migration_max": expected_migration,
        "bundle": bundle,
        "inspection": inspection,
        "operations": operations,
        "credentials_persisted": False,
        "release_gate_eligible": False,
        "release_gate_blockers": [
            "signed_backup_provenance_not_implemented",
            "trusted_receipt_consumer_and_seal_not_implemented",
            "crash_scavenger_not_implemented",
        ],
    }


def preflight_rehearsal(
    *,
    dump: Path,
    sidecar: Path,
    expected_migration: str,
    binding: ClusterBinding,
    restore_runner: RestoreRunner,
    release_id: str,
    expected_app_sha: str,
) -> dict[str, Any]:
    """Verify sealed inputs without connecting to or creating a database."""

    if not MIGRATION_RE.fullmatch(expected_migration):
        raise RestoreError("configure", "invalid_expected_migration")
    if (
        not RELEASE_ID_RE.fullmatch(release_id)
        or release_id in {".", ".."}
        or not GIT_SHA_RE.fullmatch(expected_app_sha)
    ):
        raise RestoreError("configure", "invalid_release_binding")
    bundle = verify_bundle(dump, sidecar)
    restore_runner.check_archive(dump)
    return build_preflight_receipt(
        checked_at=utcnow(),
        release_id=release_id,
        expected_app_sha=expected_app_sha,
        expected_migration=expected_migration,
        bundle=bundle,
        attestation_sha256=binding.attestation_sha256,
        system_identifier=binding.system_identifier,
        port=binding.port,
    )


def _write_private_json(path: Path, payload: Mapping[str, Any]) -> None:
    write_private_json(path, payload, error_factory=RestoreError)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Strict opt-in local-peer PostgreSQL restore rehearsal")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--execute", action="store_true", help="Create, verify and drop the isolated DB")
    mode.add_argument(
        "--preflight",
        action="store_true",
        help="Verify archive and root-sealed isolated-cluster binding without contacting PostgreSQL",
    )
    parser.add_argument("--dump", required=True, help="Custom-format pg_dump archive")
    parser.add_argument("--sha256-file", required=True, help="SHA-256 sidecar for --dump")
    parser.add_argument("--expected-migration-max", required=True, help="Exact schema_migrations max from backup")
    parser.add_argument("--anchor-table", action="append", default=[], help="Required table; defaults to V-KPI anchors")
    parser.add_argument("--artifact", default="", help="Required non-existing private receipt in execute mode")
    parser.add_argument("--release-id", default="", help="Release identifier bound into the receipt")
    parser.add_argument("--expected-app-sha", default="", help="Exact 40-char application SHA")
    parser.add_argument(
        "--isolated-cluster-attestation",
        default="",
        help="Root-sealed, release-bound disposable PostgreSQL cluster attestation",
    )
    return parser.parse_args(argv)


def _effective_username() -> str:
    try:
        return pwd.getpwuid(os.geteuid()).pw_name
    except Exception:
        return getpass.getuser()


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    if not args.execute and not args.preflight:
        stdout_out(
            json.dumps(
                {
                    "status": "not_executed",
                    "postgres_contacted": False,
                    "required_flag": "--preflight or --execute",
                    "required_confirmation_env": CONFIRM_ENV,
                    "connection_policy": "root_attested_disposable_cluster_unix_socket_only",
                    "temporary_database_prefix": DATABASE_PREFIX,
                },
                sort_keys=True,
            )
        )
        return 2
    if args.execute and os.environ.get(CONFIRM_ENV) != CONFIRM_VALUE:
        stdout_out(
            "Restore rehearsal confirmation is absent or invalid; PostgreSQL was not contacted.",
            file=sys.stderr,
        )
        return 2
    effective_user = _effective_username()
    effective_uid = os.geteuid()
    if effective_uid == 0:
        stdout_out(
            "Restore rehearsal must run as the non-root owner of a disposable cluster.",
            file=sys.stderr,
        )
        return 2
    if not args.artifact:
        stdout_out("--artifact is required in preflight and execute modes.", file=sys.stderr)
        return 2
    if (
        not RELEASE_ID_RE.fullmatch(args.release_id)
        or args.release_id in {".", ".."}
        or not GIT_SHA_RE.fullmatch(args.expected_app_sha)
    ):
        stdout_out(
            "A safe --release-id and exact --expected-app-sha are required.",
            file=sys.stderr,
        )
        return 2
    if any(str(os.environ.get(key) or "").strip() for key in PG_ENV_KEYS):
        stdout_out(
            "Ambient PostgreSQL routing/credential variables are forbidden.",
            file=sys.stderr,
        )
        return 2
    if not args.isolated_cluster_attestation:
        stdout_out(
            "--isolated-cluster-attestation is required; production-cluster rehearsal is forbidden.",
            file=sys.stderr,
        )
        return 2

    anchors = tuple(args.anchor_table or DEFAULT_ANCHORS)
    database = temporary_database_name() if args.execute else ""
    try:
        binding = load_cluster_binding(
            Path(args.isolated_cluster_attestation),
            release_id=args.release_id,
            expected_app_sha=args.expected_app_sha,
            owner_user=effective_user,
            owner_uid=effective_uid,
        )
        if args.preflight:
            result = preflight_rehearsal(
                dump=Path(args.dump),
                sidecar=Path(args.sha256_file),
                expected_migration=args.expected_migration_max,
                binding=binding,
                restore_runner=PgRestoreSubprocess(
                    socket_dir=binding.socket_dir,
                    port=binding.port,
                    user=binding.owner_user,
                ),
                release_id=args.release_id,
                expected_app_sha=args.expected_app_sha,
            )
            _write_private_json(Path(args.artifact), result)
            stdout_out(
                json.dumps(
                    {
                        "status": result["status"],
                        "artifact": args.artifact,
                        "postgres_contacted": False,
                        "release_gate_eligible": False,
                    },
                    sort_keys=True,
                )
            )
            return 0
        result = run_rehearsal(
            dump=Path(args.dump),
            sidecar=Path(args.sha256_file),
            expected_migration=args.expected_migration_max,
            anchors=anchors,
            database_ops=PsycopgLocalPeerOps(
                socket_dir=binding.socket_dir,
                port=binding.port,
                user=binding.owner_user,
                expected_data_root=binding.data_root,
                expected_system_identifier=binding.system_identifier,
            ),
            restore_runner=PgRestoreSubprocess(
                socket_dir=binding.socket_dir,
                port=binding.port,
                user=binding.owner_user,
            ),
            database=database,
            release_id=args.release_id,
            expected_app_sha=args.expected_app_sha,
        )
        result["isolated_cluster"] = {
            "attestation_sha256": binding.attestation_sha256,
            "system_identifier_sha256": hashlib.sha256(
                binding.system_identifier.encode("ascii")
            ).hexdigest(),
            "port": binding.port,
            "network_listen_addresses": "",
            "data_root_under_reviewed_prefix": True,
        }
        _write_private_json(Path(args.artifact), result)
    except RestoreError as exc:
        failure = {
            "schema_version": 1,
            "evidence_type": (
                "vkpi_postgres_restore_rehearsal_preflight"
                if args.preflight
                else "vkpi_postgres_restore_rehearsal"
            ),
            "status": "failed",
            "checked_at": utcnow(),
            "release_id": args.release_id,
            "expected_app_sha": args.expected_app_sha,
            "connection_policy": "root_attested_disposable_cluster_unix_socket_only",
            "temporary_database": database or None,
            "postgres_contacted": False if args.preflight else None,
            "failure": {"operation": exc.operation, "category": exc.category},
            "bundle": getattr(exc, "bundle", {}),
            "inspection": getattr(exc, "inspection", {}),
            "operations": getattr(exc, "operations", {}),
            "credentials_persisted": False,
            "release_gate_eligible": False,
        }
        try:
            _write_private_json(Path(args.artifact), failure)
        except RestoreError:
            pass
        stdout_out(str(exc), file=sys.stderr)
        return 1
    except Exception:
        stdout_out(
            "PostgreSQL restore rehearsal failed: operation=internal category=unexpected",
            file=sys.stderr,
        )
        return 1
    stdout_out(
        json.dumps(
            {
                "status": "diagnostic_passed_not_release_eligible",
                "artifact": args.artifact,
            },
            sort_keys=True,
        )
    )
    return 3


if __name__ == "__main__":
    raise SystemExit(main())
