"""Credential-free evidence helpers for PostgreSQL restore rehearsal."""

from __future__ import annotations

import hashlib
import json
import os
import stat
import tempfile
from pathlib import Path
from typing import Any, Callable, Mapping


def build_preflight_receipt(
    *,
    checked_at: str,
    release_id: str,
    expected_app_sha: str,
    expected_migration: str,
    bundle: Mapping[str, Any],
    attestation_sha256: str,
    system_identifier: str,
    port: int,
) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "evidence_type": "vkpi_postgres_restore_rehearsal_preflight",
        "status": "ready_for_diagnostic_restore",
        "checked_at": checked_at,
        "release_id": release_id,
        "expected_app_sha": expected_app_sha,
        "requested_expected_migration_max": expected_migration,
        "bundle": dict(bundle),
        "archive_list_verified": True,
        "postgres_contacted": False,
        "database_created": False,
        "isolated_cluster": {
            "attestation_sha256": attestation_sha256,
            "system_identifier_sha256": hashlib.sha256(
                system_identifier.encode("ascii")
            ).hexdigest(),
            "port": port,
            "network_listen_addresses": "",
            "data_root_under_reviewed_prefix": True,
        },
        "credentials_persisted": False,
        "release_gate_eligible": False,
        "release_gate_blockers": [
            "restore_not_executed",
            "restored_migration_and_anchor_checks_not_executed",
            "signed_backup_provenance_not_implemented",
            "trusted_receipt_consumer_and_seal_not_implemented",
            "crash_scavenger_not_implemented",
        ],
    }


def write_private_json(
    path: Path,
    payload: Mapping[str, Any],
    *,
    error_factory: Callable[[str, str], Exception],
) -> None:
    if path.exists() or path.is_symlink():
        raise error_factory("artifact", "refuse_overwrite")
    parent_existed = path.parent.exists()
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    if path.parent.is_symlink() or not path.parent.is_dir():
        raise error_factory("artifact", "unsafe_parent")
    if path.parent.stat().st_mode & stat.S_IWOTH:
        raise error_factory("artifact", "world_writable_parent")
    if not parent_existed:
        os.chmod(path.parent, 0o700)
    try:
        fd, temp_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    except OSError:
        raise error_factory("artifact", "temporary_create_failed") from None
    temporary = Path(temp_name)
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=True, sort_keys=True, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)
