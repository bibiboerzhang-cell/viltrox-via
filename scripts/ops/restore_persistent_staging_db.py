#!/usr/bin/env python3
"""Restore a verified pg_dump archive into one release-bound staging DB.

This is deliberately not a general PostgreSQL restore command.  It accepts no
DSN, host, password or arbitrary target name; it uses local peer auth, derives
the target from the release id, verifies and pins the archive, and writes a
private credential-free receipt only after read-only post-restore checks pass.
"""

from __future__ import annotations

import argparse
import ipaddress
import json
import os
import pwd
import re
import shutil
import stat
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Protocol, Sequence

if __package__:
    from . import postgres_restore_rehearsal as restore_support
    from . import staging_db_clone
else:
    import postgres_restore_rehearsal as restore_support
    import staging_db_clone


CONFIRM_ENV = "VKPI_PERSISTENT_STAGING_RESTORE_CONFIRM"
CONFIRM_VALUE = "CREATE_RELEASE_BOUND_PERSISTENT_STAGING_DB"
SOURCE_DATABASE = "viltrox2_test"
DEFAULT_ANCHORS = (
    "schema_migrations",
    "users",
    "vkpi_projects",
    "vkpi_kol_pool",
    "vkpi_dealers",
    "vkpi_events",
)
MIGRATION_RE = re.compile(r"^[0-9]{3}_[A-Za-z0-9_.-]+\.sql$")
ROLE_RE = re.compile(r"^[a-z_][a-z0-9_-]{0,62}$")
TABLE_RE = re.compile(r"^[a-z_][a-z0-9_]{0,62}$")
MIN_DISK_HEADROOM_BYTES = 1024**3
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


class StagingRestoreError(RuntimeError):
    """Credential-free, stable failure contract."""

    def __init__(self, operation: str, category: str):
        self.operation = operation
        self.category = category
        super().__init__(
            f"persistent staging restore failed: operation={operation} category={category}"
        )


class RestoreRunner(Protocol):
    def check_archive(self, dump: Path) -> None: ...

    def restore(self, dump: Path, database: str, owner: str) -> None: ...


class DatabaseOps(Protocol):
    def assert_local_admin(self) -> None: ...

    def source_state(self, source: str, expected_owner: str) -> dict[str, Any]: ...

    def database_exists(self, database: str) -> bool: ...

    def create_database(self, database: str, owner: str) -> None: ...

    def inspect_database(
        self, database: str, expected_owner: str, anchors: Sequence[str]
    ) -> dict[str, Any]: ...

    def drop_database_force(self, database: str) -> None: ...


def utcnow() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def target_database_for_release(release_id: str) -> str:
    try:
        return staging_db_clone.clone_name_for_release(release_id)
    except staging_db_clone.CloneError:
        raise StagingRestoreError("configure", "invalid_release_id") from None


def _validate_inputs(
    *,
    release_id: str,
    source_database: str,
    expected_owner: str,
    expected_migration: str,
    anchors: Sequence[str],
) -> str:
    target = target_database_for_release(release_id)
    if source_database != SOURCE_DATABASE:
        raise StagingRestoreError("configure", "unreviewed_source_database")
    if not ROLE_RE.fullmatch(expected_owner):
        raise StagingRestoreError("configure", "invalid_database_owner")
    if not MIGRATION_RE.fullmatch(expected_migration):
        raise StagingRestoreError("configure", "invalid_expected_migration")
    if (
        not anchors
        or "schema_migrations" not in anchors
        or len(set(anchors)) != len(anchors)
        or any(not TABLE_RE.fullmatch(table) for table in anchors)
    ):
        raise StagingRestoreError("configure", "invalid_anchor_table_set")
    return target


def required_free_bytes(source_size_bytes: int) -> int:
    if source_size_bytes <= 0:
        raise StagingRestoreError("capacity", "source_size_invalid")
    return source_size_bytes + MIN_DISK_HEADROOM_BYTES


def _validate_sidecar_name(dump: Path, sidecar: Path) -> None:
    try:
        info = sidecar.lstat()
        fields = sidecar.read_text(encoding="ascii").strip().split()
    except (OSError, UnicodeDecodeError):
        raise StagingRestoreError("bundle", "checksum_sidecar_invalid") from None
    if (
        sidecar.is_symlink()
        or not stat.S_ISREG(info.st_mode)
        or info.st_nlink != 1
        or len(fields) != 2
        or not re.fullmatch(r"[0-9a-fA-F]{64}", fields[0])
        or fields[1] != dump.name
    ):
        raise StagingRestoreError("bundle", "checksum_sidecar_invalid")


def _pin_verified_bundle(
    dump: Path, sidecar: Path
) -> tuple[Path, Path, dict[str, Any]]:
    _validate_sidecar_name(dump, sidecar)
    try:
        return restore_support.pin_verified_bundle(dump, sidecar)
    except restore_support.RestoreError as exc:
        raise StagingRestoreError("bundle", exc.category) from None


def _write_private_receipt(path: Path, payload: Mapping[str, Any]) -> None:
    try:
        parent_info = path.parent.lstat()
        parent = path.parent.resolve(strict=True)
    except OSError:
        raise StagingRestoreError("receipt", "receipt_parent_unavailable") from None
    if (
        path.exists()
        or path.is_symlink()
        or not stat.S_ISDIR(parent_info.st_mode)
        or parent_info.st_uid != os.geteuid()
        or parent_info.st_mode & (stat.S_IWGRP | stat.S_IWOTH)
    ):
        raise StagingRestoreError("receipt", "receipt_path_not_private_or_exists")
    temporary = parent / f".{path.name}.tmp-{os.getpid()}"
    descriptor = -1
    created = False
    try:
        descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        created = True
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            descriptor = -1
            json.dump(payload, handle, sort_keys=True, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        directory_fd = os.open(parent, os.O_RDONLY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
        created = False
    except (OSError, TypeError, ValueError):
        raise StagingRestoreError("receipt", "receipt_write_failed") from None
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        if created:
            temporary.unlink(missing_ok=True)


def _peer_environment(admin_user: str) -> dict[str, str]:
    return {
        "PATH": os.environ.get(
            "PATH", "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"
        ),
        "HOME": "/nonexistent/vkpi-persistent-staging-restore",
        "LANG": os.environ.get("LANG", "C.UTF-8"),
        "LC_ALL": os.environ.get("LC_ALL", "C.UTF-8"),
        "PGUSER": admin_user,
    }


class PgRestoreSubprocess:
    def __init__(self, *, admin_user: str) -> None:
        executable = shutil.which("pg_restore")
        if not executable:
            raise StagingRestoreError("configure", "pg_restore_missing")
        self.executable = executable
        self.admin_user = admin_user

    def _run(self, argv: list[str], operation: str) -> None:
        try:
            completed = subprocess.run(
                argv,
                env=_peer_environment(self.admin_user),
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=3600,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired):
            raise StagingRestoreError(operation, "process_failed_or_timed_out") from None
        if completed.returncode != 0:
            raise StagingRestoreError(operation, "nonzero_exit")

    def check_archive(self, dump: Path) -> None:
        self._run([self.executable, "--list", str(dump)], "archive_list")

    def restore(self, dump: Path, database: str, owner: str) -> None:
        staging_db_clone.validate_clone_name(database)
        if not ROLE_RE.fullmatch(owner):
            raise StagingRestoreError("pg_restore", "invalid_database_owner")
        self._run(
            [
                self.executable,
                "--no-owner",
                "--no-acl",
                "--exit-on-error",
                "--single-transaction",
                f"--role={owner}",
                f"--dbname={database}",
                str(dump),
            ],
            "pg_restore",
        )


def _is_local_server(value: Any) -> bool:
    if value in (None, ""):
        return True
    try:
        return ipaddress.ip_address(str(value)).is_loopback
    except ValueError:
        return False


class LocalPeerDatabaseOps:
    def __init__(self, *, admin_user: str) -> None:
        try:
            import psycopg
            from psycopg import sql
        except Exception:
            raise StagingRestoreError("configure", "psycopg_missing") from None
        self.psycopg = psycopg
        self.sql = sql
        self.admin_user = admin_user

    def _connect(self, database: str, *, autocommit: bool) -> Any:
        try:
            return self.psycopg.connect(dbname=database, autocommit=autocommit)
        except Exception:
            raise StagingRestoreError("connect", "local_peer_connection_failed") from None

    def assert_local_admin(self) -> None:
        with self._connect("postgres", autocommit=True) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT current_database(), current_user, inet_server_addr()::text"
                )
                row = cur.fetchone()
                if (
                    not row
                    or str(row[0]) != "postgres"
                    or str(row[1]) != self.admin_user
                    or not _is_local_server(row[2])
                ):
                    raise StagingRestoreError("identity", "local_peer_admin_mismatch")

    def source_state(self, source: str, expected_owner: str) -> dict[str, Any]:
        with self._connect("postgres", autocommit=True) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT pg_database_size(datname), pg_get_userbyid(datdba)
                    FROM pg_database WHERE datname = %s
                    """,
                    (source,),
                )
                row = cur.fetchone()
                if row is None:
                    raise StagingRestoreError("source", "source_database_missing")
                source_size = int(row[0])
                source_owner = str(row[1])
                if source_owner != expected_owner:
                    raise StagingRestoreError("source", "source_owner_mismatch")
                cur.execute("SHOW data_directory")
                data_directory = Path(str(cur.fetchone()[0])).resolve(strict=True)
        return {
            "database": source,
            "owner": source_owner,
            "size_bytes": source_size,
            "free_bytes": int(shutil.disk_usage(data_directory).free),
        }

    def database_exists(self, database: str) -> bool:
        staging_db_clone.validate_clone_name(database)
        with self._connect("postgres", autocommit=True) as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT 1 FROM pg_database WHERE datname = %s", (database,))
                return cur.fetchone() is not None

    def create_database(self, database: str, owner: str) -> None:
        staging_db_clone.validate_clone_name(database)
        if not ROLE_RE.fullmatch(owner):
            raise StagingRestoreError("create", "invalid_database_owner")
        with self._connect("postgres", autocommit=True) as conn:
            with conn.cursor() as cur:
                try:
                    cur.execute(
                        self.sql.SQL(
                            "CREATE DATABASE {} WITH TEMPLATE template0 OWNER {}"
                        ).format(
                            self.sql.Identifier(database),
                            self.sql.Identifier(owner),
                        )
                    )
                except Exception:
                    raise StagingRestoreError("create", "create_database_failed") from None

    def inspect_database(
        self, database: str, expected_owner: str, anchors: Sequence[str]
    ) -> dict[str, Any]:
        staging_db_clone.validate_clone_name(database)
        with self._connect(database, autocommit=False) as conn:
            with conn.cursor() as cur:
                try:
                    cur.execute("SET TRANSACTION READ ONLY")
                    cur.execute("SET LOCAL statement_timeout = '60s'")
                    cur.execute("SELECT current_database(), inet_server_addr()::text")
                    identity = cur.fetchone()
                    if (
                        not identity
                        or str(identity[0]) != database
                        or not _is_local_server(identity[1])
                    ):
                        raise StagingRestoreError("inspect", "target_identity_mismatch")
                    cur.execute(
                        "SELECT pg_get_userbyid(datdba) FROM pg_database WHERE datname = %s",
                        (database,),
                    )
                    owner_row = cur.fetchone()
                    target_owner = str(owner_row[0]) if owner_row else ""
                    cur.execute("SELECT MAX(version_key) FROM schema_migrations")
                    migration_max = str(cur.fetchone()[0] or "")
                    counts: dict[str, int] = {}
                    table_owners: dict[str, str] = {}
                    for table in anchors:
                        cur.execute("SELECT to_regclass(%s)", (f"public.{table}",))
                        if cur.fetchone()[0] is None:
                            raise StagingRestoreError(
                                "inspect", f"missing_table_{table}"
                            )
                        cur.execute(
                            """
                            SELECT pg_get_userbyid(c.relowner)
                            FROM pg_class AS c
                            JOIN pg_namespace AS n ON n.oid = c.relnamespace
                            WHERE n.nspname = 'public' AND c.relname = %s
                            """,
                            (table,),
                        )
                        table_owner_row = cur.fetchone()
                        table_owner = str(table_owner_row[0]) if table_owner_row else ""
                        if table_owner != expected_owner:
                            raise StagingRestoreError(
                                "inspect", f"table_owner_mismatch_{table}"
                            )
                        table_owners[table] = table_owner
                        cur.execute(
                            self.sql.SQL("SELECT count(*) FROM {}").format(
                                self.sql.Identifier(table)
                            )
                        )
                        counts[table] = int(cur.fetchone()[0])
                except StagingRestoreError:
                    raise
                except Exception:
                    raise StagingRestoreError(
                        "inspect", "read_only_verification_failed"
                    ) from None
        return {
            "transaction_mode": "read_only",
            "database_owner": target_owner,
            "migration_max": migration_max,
            "table_counts": counts,
            "table_owners": table_owners,
        }

    def drop_database_force(self, database: str) -> None:
        staging_db_clone.validate_clone_name(database)
        with self._connect("postgres", autocommit=True) as conn:
            with conn.cursor() as cur:
                try:
                    cur.execute(
                        self.sql.SQL("DROP DATABASE IF EXISTS {} WITH (FORCE)").format(
                            self.sql.Identifier(database)
                        )
                    )
                except Exception:
                    raise StagingRestoreError("cleanup", "force_drop_failed") from None


def restore_persistent_staging(
    *,
    dump: Path,
    sidecar: Path,
    release_id: str,
    source_database: str,
    expected_owner: str,
    expected_migration: str,
    anchors: Sequence[str],
    receipt_path: Path,
    database_ops: DatabaseOps,
    restore_runner: RestoreRunner,
) -> dict[str, Any]:
    target = _validate_inputs(
        release_id=release_id,
        source_database=source_database,
        expected_owner=expected_owner,
        expected_migration=expected_migration,
        anchors=anchors,
    )
    pinned_root, pinned_dump, bundle = _pin_verified_bundle(dump, sidecar)
    operations = {
        "dump_pinned": True,
        "archive_list": False,
        "local_admin_identity": False,
        "source_and_owner_verified": False,
        "disk_headroom_verified": False,
        "target_absent": False,
        "database_created_from_template0": False,
        "pg_restore_single_transaction": False,
        "post_restore_dump_reverified": False,
        "read_only_verification": False,
        "pinned_dump_removed": False,
        "receipt_written": False,
        "failure_force_drop": False,
    }
    source_state: dict[str, Any] = {}
    inspection: dict[str, Any] = {}
    failure: StagingRestoreError | None = None
    receipt: dict[str, Any] | None = None
    cleanup_candidate = False
    success = False
    try:
        restore_runner.check_archive(pinned_dump)
        operations["archive_list"] = True
        database_ops.assert_local_admin()
        operations["local_admin_identity"] = True
        source_state = database_ops.source_state(source_database, expected_owner)
        if (
            source_state.get("database") != source_database
            or source_state.get("owner") != expected_owner
        ):
            raise StagingRestoreError("source", "source_identity_mismatch")
        operations["source_and_owner_verified"] = True
        required = required_free_bytes(int(source_state.get("size_bytes") or 0))
        if int(source_state.get("free_bytes") or 0) < required:
            raise StagingRestoreError("capacity", "insufficient_disk_headroom")
        operations["disk_headroom_verified"] = True
        if database_ops.database_exists(target):
            raise StagingRestoreError("create", "target_database_already_exists")
        operations["target_absent"] = True
        # CREATE may commit even if a client loses the response.  From this
        # point every failure path checks and force-drops the release target.
        cleanup_candidate = True
        database_ops.create_database(target, expected_owner)
        operations["database_created_from_template0"] = True
        restore_runner.restore(pinned_dump, target, expected_owner)
        operations["pg_restore_single_transaction"] = True
        if restore_support.sha256_file(pinned_dump) != bundle["dump_sha256"]:
            raise StagingRestoreError("bundle", "pinned_dump_changed_during_restore")
        operations["post_restore_dump_reverified"] = True
        inspection = database_ops.inspect_database(target, expected_owner, anchors)
        if inspection.get("transaction_mode") != "read_only":
            raise StagingRestoreError("inspect", "transaction_not_read_only")
        if inspection.get("database_owner") != expected_owner:
            raise StagingRestoreError("inspect", "target_owner_mismatch")
        if inspection.get("migration_max") != expected_migration:
            raise StagingRestoreError("inspect", "migration_max_mismatch")
        counts = inspection.get("table_counts")
        if not isinstance(counts, dict) or set(counts) != set(anchors):
            raise StagingRestoreError("inspect", "anchor_table_set_mismatch")
        table_owners = inspection.get("table_owners")
        if (
            not isinstance(table_owners, dict)
            or set(table_owners) != set(anchors)
            or any(owner != expected_owner for owner in table_owners.values())
        ):
            raise StagingRestoreError("inspect", "anchor_table_owner_mismatch")
        if any(not isinstance(value, int) or isinstance(value, bool) or value < 0 for value in counts.values()):
            raise StagingRestoreError("inspect", "invalid_anchor_table_count")
        if int(counts.get("schema_migrations") or 0) <= 0:
            raise StagingRestoreError("inspect", "migration_ledger_empty")
        operations["read_only_verification"] = True
        try:
            shutil.rmtree(pinned_root)
        except OSError:
            raise StagingRestoreError("bundle_cleanup", "pinned_dump_removal_failed") from None
        operations["pinned_dump_removed"] = not pinned_root.exists()
        if not operations["pinned_dump_removed"]:
            raise StagingRestoreError("bundle_cleanup", "pinned_dump_removal_not_confirmed")
        operations["receipt_written"] = True
        receipt = {
            "schema_version": 1,
            "evidence_type": "vkpi_persistent_staging_restore",
            "status": "passed",
            "checked_at": utcnow(),
            "release_id": release_id,
            "database_strategy": "persistent-staging-restore",
            "source_database": source_database,
            "source_owner": expected_owner,
            "source_size_bytes": int(source_state["size_bytes"]),
            "free_bytes_before": int(source_state["free_bytes"]),
            "target_database": target,
            "expected_migration_max": expected_migration,
            "required_free_bytes": required,
            "bundle": bundle,
            "inspection": inspection,
            "operations": operations,
            "connection_policy": "local_peer_no_dsn_no_password",
            "persistent_database_retained": True,
            "credentials_persisted": False,
            "secrets_included": False,
        }
        _write_private_receipt(receipt_path, receipt)
        success = True
    except StagingRestoreError as exc:
        failure = exc
    except restore_support.RestoreError as exc:
        failure = StagingRestoreError("bundle", exc.category)
    except Exception:
        failure = StagingRestoreError("internal", "unexpected")
    finally:
        if pinned_root.exists():
            shutil.rmtree(pinned_root, ignore_errors=True)
            operations["pinned_dump_removed"] = not pinned_root.exists()
        if not success and cleanup_candidate:
            # A signal can arrive immediately after the atomic receipt rename.
            # Never leave a passing receipt behind for a database we then drop.
            receipt_path.unlink(missing_ok=True)
            try:
                if database_ops.database_exists(target):
                    database_ops.drop_database_force(target)
                operations["failure_force_drop"] = True
                if database_ops.database_exists(target):
                    failure = StagingRestoreError(
                        "cleanup", "target_database_still_exists"
                    )
            except Exception:
                failure = StagingRestoreError("cleanup", "force_drop_failed")
    if failure is not None:
        failure.operations = operations  # type: ignore[attr-defined]
        raise failure
    assert receipt is not None
    return receipt


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Restore a verified dump into a release-bound persistent staging DB"
    )
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--dump", required=True)
    parser.add_argument("--sha256-file", required=True)
    parser.add_argument("--release-id", required=True)
    parser.add_argument("--source-db", default=SOURCE_DATABASE)
    parser.add_argument("--expected-owner", required=True)
    parser.add_argument("--expected-migration-max", required=True)
    parser.add_argument("--anchor-table", action="append", default=[])
    parser.add_argument("--receipt", required=True)
    parser.add_argument("--admin-os-user", default="postgres")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if not args.execute or os.environ.get(CONFIRM_ENV) != CONFIRM_VALUE:
        sys.stderr.write(
            "Persistent staging restore was not executed; explicit flag and confirmation are required.\n"
        )
        return 2
    if any(str(os.environ.get(key) or "").strip() for key in PG_ENV_KEYS):
        sys.stderr.write("Ambient PostgreSQL routing or credential variables are forbidden.\n")
        return 2
    try:
        effective_user = pwd.getpwuid(os.geteuid()).pw_name
    except Exception:
        effective_user = ""
    if effective_user != args.admin_os_user or os.geteuid() == 0:
        sys.stderr.write("Persistent staging restore must run as the reviewed non-root PostgreSQL OS user.\n")
        return 2
    anchors = tuple(args.anchor_table or DEFAULT_ANCHORS)
    try:
        receipt = restore_persistent_staging(
            dump=Path(args.dump),
            sidecar=Path(args.sha256_file),
            release_id=args.release_id,
            source_database=args.source_db,
            expected_owner=args.expected_owner,
            expected_migration=args.expected_migration_max,
            anchors=anchors,
            receipt_path=Path(args.receipt),
            database_ops=LocalPeerDatabaseOps(admin_user=args.admin_os_user),
            restore_runner=PgRestoreSubprocess(admin_user=args.admin_os_user),
        )
    except StagingRestoreError as exc:
        sys.stderr.write(f"{exc}\n")
        return 1
    sys.stdout.write(
        json.dumps(
            {
                "status": receipt["status"],
                "target_database": receipt["target_database"],
                "receipt": args.receipt,
                "receipt_written": True,
                "credentials_persisted": False,
            },
            sort_keys=True,
        )
        + "\n"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
