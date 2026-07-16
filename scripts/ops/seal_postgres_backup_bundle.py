#!/usr/bin/env python3
"""Seal one existing private PostgreSQL archive with verified sidecars.

This helper is for a locally created archive that predates the atomic backup
script.  It cannot overwrite evidence, contact PostgreSQL, attest a restore,
or make a release eligible.  ``pg_restore --list`` must accept the archive
before a checksum sidecar and metadata receipt are published.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import stat
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

if __package__:
    from . import postgres_restore_rehearsal as restore_gate
else:
    import postgres_restore_rehearsal as restore_gate


MIGRATION_RE = re.compile(r"^[0-9]{3}_[A-Za-z0-9_.-]+\.sql$")
RELEASE_RE = re.compile(r"^[A-Za-z0-9_.-]{1,120}$")
SHA40_RE = re.compile(r"^[0-9a-f]{40}$")


class SealError(RuntimeError):
    """Non-secret fail-closed bundle sealing error."""


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _safe_parent(path: Path) -> Path:
    try:
        requested_parent = path.parent
        info = requested_parent.lstat()
        parent = requested_parent.resolve(strict=True)
    except OSError:
        raise SealError("bundle parent is unavailable") from None
    if (
        requested_parent.is_symlink()
        or not stat.S_ISDIR(info.st_mode)
        or info.st_uid != os.geteuid()
        or info.st_mode & (stat.S_IWGRP | stat.S_IWOTH)
    ):
        raise SealError("bundle parent is not private-owned")
    return parent


def _write_exclusive(path: Path, content: bytes) -> None:
    if path.exists() or path.is_symlink():
        raise SealError("refusing to overwrite bundle evidence")
    parent = _safe_parent(path)
    descriptor = -1
    created = False
    try:
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        created = True
        with os.fdopen(descriptor, "wb") as handle:
            descriptor = -1
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        directory_fd = os.open(parent, os.O_RDONLY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    except FileExistsError:
        raise SealError("refusing to overwrite bundle evidence") from None
    except OSError:
        if created:
            path.unlink(missing_ok=True)
        raise SealError("unable to publish bundle evidence") from None
    finally:
        if descriptor >= 0:
            os.close(descriptor)


def check_archive(dump: Path) -> None:
    executable = shutil.which("pg_restore")
    if not executable:
        raise SealError("pg_restore is unavailable")
    try:
        result = subprocess.run(
            [executable, "--list", str(dump)],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=1800,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        raise SealError("pg_restore archive verification failed") from None
    if result.returncode != 0:
        raise SealError("pg_restore archive verification failed")


def read_archive_migration_max(dump: Path) -> str:
    """Read the restored migration watermark without contacting PostgreSQL."""

    executable = shutil.which("pg_restore")
    if not executable:
        raise SealError("pg_restore is unavailable")
    try:
        result = subprocess.run(
            [
                executable,
                "--data-only",
                "--table=schema_migrations",
                "--file=-",
                str(dump),
            ],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            timeout=1800,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        raise SealError("archive migration verification failed") from None
    if result.returncode != 0 or len(result.stdout) > 4 * 1024 * 1024:
        raise SealError("archive migration verification failed")
    try:
        lines = result.stdout.decode("utf-8").splitlines()
    except UnicodeDecodeError:
        raise SealError("archive migration verification failed") from None
    migration_keys = {
        line.split("\t", 1)[0].strip()
        for line in lines
        if "\t" in line and MIGRATION_RE.fullmatch(line.split("\t", 1)[0].strip())
    }
    if not migration_keys:
        raise SealError("archive migration watermark is missing")
    return max(migration_keys)


def seal_bundle(
    *,
    dump: Path,
    sidecar: Path,
    metadata: Path,
    expected_migration: str,
    release_id: str,
    expected_app_sha: str,
    archive_checker: Callable[[Path], None] = check_archive,
    migration_reader: Callable[[Path], str] = read_archive_migration_max,
) -> dict[str, Any]:
    if not MIGRATION_RE.fullmatch(expected_migration):
        raise SealError("expected migration is invalid")
    if not RELEASE_RE.fullmatch(release_id) or release_id in {".", ".."}:
        raise SealError("release identifier is invalid")
    if not SHA40_RE.fullmatch(expected_app_sha):
        raise SealError("expected application SHA is invalid")
    if sidecar.parent.resolve() != dump.parent.resolve() or metadata.parent.resolve() != dump.parent.resolve():
        raise SealError("bundle evidence must share the dump directory")
    if sidecar == metadata or sidecar == dump or metadata == dump:
        raise SealError("bundle paths must be distinct")
    restore_gate._safe_file(dump, maximum_bytes=1024**4)
    _safe_parent(dump)
    if sidecar.exists() or sidecar.is_symlink() or metadata.exists() or metadata.is_symlink():
        raise SealError("refusing to overwrite bundle evidence")

    archive_checker(dump)
    archive_migration_max = migration_reader(dump)
    if archive_migration_max != expected_migration:
        raise SealError("archive migration does not match expected migration")
    digest = restore_gate.sha256_file(dump)
    sidecar_created = False
    metadata_created = False
    try:
        _write_exclusive(sidecar, f"{digest}  {dump.name}\n".encode("ascii"))
        sidecar_created = True
        bundle = restore_gate.verify_bundle(dump, sidecar)
        receipt: dict[str, Any] = {
            "schema_version": 1,
            "evidence_type": "vkpi_postgres_backup_bundle_seal",
            "status": "sealed_diagnostic_only",
            "sealed_at": _utcnow(),
            "release_id": release_id,
            "expected_app_sha": expected_app_sha,
            "expected_migration_max": expected_migration,
            "archive_migration_max": archive_migration_max,
            "archive_migration_verified": True,
            "bundle": bundle,
            "archive_list_verified": True,
            "database_contacted": False,
            "credentials_persisted": False,
            "release_gate_eligible": False,
            "release_gate_blockers": [
                "restore_rehearsal_not_attested",
                "signed_backup_provenance_not_present",
                "off_host_receipt_not_present",
            ],
        }
        _write_exclusive(
            metadata,
            (json.dumps(receipt, sort_keys=True, indent=2) + "\n").encode("utf-8"),
        )
        metadata_created = True
        return receipt
    except Exception:
        if metadata_created:
            metadata.unlink(missing_ok=True)
        if sidecar_created:
            sidecar.unlink(missing_ok=True)
        raise


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description="Seal one private PostgreSQL backup bundle")
    result.add_argument("--dump", required=True)
    result.add_argument("--sha256-file", default="")
    result.add_argument("--metadata", default="")
    result.add_argument("--expected-migration-max", required=True)
    result.add_argument("--release-id", required=True)
    result.add_argument("--expected-app-sha", required=True)
    return result


def main(argv: Sequence[str] | None = None) -> int:
    args = parser().parse_args(argv)
    dump = Path(args.dump)
    sidecar = Path(args.sha256_file) if args.sha256_file else dump.with_name(f"{dump.name}.sha256")
    metadata = Path(args.metadata) if args.metadata else dump.with_name(f"{dump.stem}.meta.json")
    try:
        result = seal_bundle(
            dump=dump,
            sidecar=sidecar,
            metadata=metadata,
            expected_migration=args.expected_migration_max,
            release_id=args.release_id,
            expected_app_sha=args.expected_app_sha,
        )
    except (SealError, restore_gate.RestoreError, OSError) as exc:
        sys.stderr.write(f"backup bundle seal failed: {exc}\n")
        return 1
    sys.stdout.write(
        json.dumps(
            {
                "status": result["status"],
                "metadata": str(metadata),
                "release_gate_eligible": False,
            },
            sort_keys=True,
        )
        + "\n"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
