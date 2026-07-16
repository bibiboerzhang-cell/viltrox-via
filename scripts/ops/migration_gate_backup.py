"""Approved-source and migration-243 backup evidence validation."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import os
from pathlib import Path
import re
import stat
from typing import Any, Mapping

from . import migration_gate_contract as contract
from .migration_gate_contract import Checks
from .migration_gate_io import SafeFileError, load_json_artifact, read_artifact
from .migration_gate_receipts import validate_receipts


METADATA_NAME_RE = re.compile(r"^vkpi-([0-9]{8}T[0-9]{6}Z)\.meta\.json$")


def read_repository_head(root: Path, checks: Checks) -> str | None:
    try:
        head_artifact = read_artifact(
            root, root / ".git/HEAD", max_bytes=4096, private=False
        )
    except SafeFileError as exc:
        checks.add("source.repository_head.file", False, str(exc))
        return None
    try:
        head_text = head_artifact.data.decode("ascii").strip()
    except UnicodeDecodeError:
        checks.add("source.repository_head.encoding", False, "ASCII required")
        return None
    if contract.GIT_SHA_RE.fullmatch(head_text):
        checks.add("source.repository_head.format", True, "detached HEAD")
        return head_text
    if not head_text.startswith("ref: "):
        checks.add("source.repository_head.format", False, "HEAD sha/ref required")
        return None
    reference = head_text[5:]
    if not reference.startswith("refs/") or ".." in Path(reference).parts:
        checks.add("source.repository_head.ref", False, "safe refs path required")
        return None
    try:
        ref_artifact = read_artifact(
            root, root / ".git" / reference, max_bytes=4096, private=False
        )
        sha = ref_artifact.data.decode("ascii").strip()
    except (SafeFileError, UnicodeDecodeError) as exc:
        checks.add("source.repository_head.ref", False, type(exc).__name__)
        return None
    valid = contract.GIT_SHA_RE.fullmatch(sha) is not None
    checks.add("source.repository_head.ref", valid, "40-char lowercase commit sha")
    return sha if valid else None


def validate_source_manifest(
    *,
    root: Path,
    manifest_path: Path,
    now: datetime,
    max_age: timedelta,
    producer_keys: Mapping[str, str],
    checks: Checks,
) -> dict[str, Any]:
    head = read_repository_head(root, checks)
    payload, artifact = load_json_artifact(
        root,
        manifest_path,
        max_bytes=contract.MAX_SOURCE_MANIFEST_BYTES,
        private=True,
        prefix="source.manifest",
        checks=checks,
    )
    result: dict[str, Any] = {
        "path": str(manifest_path),
        "sha256": artifact.sha256 if artifact else None,
        "repository_head": head,
        "trusted_attestation": False,
        "migration_hashes": {},
    }
    if payload is None or artifact is None:
        return result
    mapping = contract.strict_object(
        payload,
        required=(
            "schema_version",
            "manifest_type",
            "status",
            "approval_id",
            "approved_at",
            "repository_head",
            "files",
            "attestation",
        ),
        prefix="source.manifest.schema",
        checks=checks,
    )
    if mapping is None:
        return result
    schema_ok = contract.schema_version_exact(mapping, prefix="source.manifest", checks=checks)
    approved_at = contract.parse_time(mapping.get("approved_at"))
    approval_ok = checks.add(
        "source.manifest.approval",
        mapping.get("manifest_type") == contract.SOURCE_MANIFEST_TYPE
        and mapping.get("status") == "approved"
        and isinstance(mapping.get("approval_id"), str)
        and contract.BUNDLE_ID_RE.fullmatch(mapping["approval_id"]) is not None
        and approved_at is not None
        and approved_at <= now + contract.FUTURE_TOLERANCE
        and now - approved_at <= max_age,
        "fresh explicit approved source manifest required",
    )
    head_ok = checks.add(
        "source.manifest.head",
        head is not None and mapping.get("repository_head") == head,
        "manifest must bind pinned repository HEAD",
    )
    expected_paths = (contract.PRE_MIGRATION, contract.UP_MIGRATION, contract.DOWN_MIGRATION)
    files = mapping.get("files")
    files_shape_ok = isinstance(files, dict) and set(files) == set(expected_paths)
    checks.add(
        "source.manifest.files",
        files_shape_ok,
        "exact 243/up/down migration source set required",
    )
    migration_hashes: dict[str, str] = {}
    migrations_ok = files_shape_ok
    for relative in expected_paths:
        artifact_prefix = f"source.migration.{Path(relative).name}"
        try:
            migration = read_artifact(
                root,
                root / relative,
                max_bytes=contract.MAX_MIGRATION_BYTES,
                private=False,
            )
        except SafeFileError as exc:
            checks.add(artifact_prefix + ".file", False, str(exc))
            migrations_ok = False
            continue
        checks.add(artifact_prefix + ".file", True, "descriptor-pinned source")
        expected_sha = files.get(relative) if isinstance(files, dict) else None
        sha_ok = checks.add(
            artifact_prefix + ".sha256",
            contract.is_sha256(expected_sha) and expected_sha == migration.sha256,
            "manifest hash must match pinned bytes",
        )
        migrations_ok = migrations_ok and sha_ok
        migration_hashes[relative] = migration.sha256
    secret_ok = contract.check_no_secrets(payload, prefix="source.manifest", checks=checks)
    mtime = datetime.fromtimestamp(artifact.mtime_ns / 1_000_000_000, timezone.utc)
    attested = contract.verify_producer_attestation(
        payload,
        prefix="source.manifest",
        now=now,
        not_before=approved_at,
        finalized_at=mtime,
        max_age=max_age,
        public_keys=producer_keys,
        checks=checks,
    )
    trusted = all((schema_ok, approval_ok, head_ok, migrations_ok, secret_ok, attested))
    result.update(
        {
            "sha256": artifact.sha256,
            "approved_at": contract.iso_z(approved_at) if approved_at else None,
            "finalized_at": contract.iso_z(mtime),
            "repository_head": head,
            "migration_hashes": migration_hashes,
            "trusted_attestation": trusted,
        }
    )
    return result


def candidate_inventory(
    root: Path, backup_dir: Path
) -> tuple[list[dict[str, Any]], list[Path]]:
    """Inventory metadata names; actual selection is re-opened through safe FDs."""

    inventory: list[dict[str, Any]] = []
    exact: list[Path] = []
    try:
        names = sorted(
            name
            for name in os.listdir(backup_dir)
            if METADATA_NAME_RE.fullmatch(name)
        )
    except OSError:
        return inventory, exact
    for name in names:
        path = backup_dir / name
        item: dict[str, Any] = {"metadata": name, "usable_json": False}
        try:
            info = os.stat(path, follow_symlinks=False)
        except OSError:
            item["rejected_reason"] = "stat_failed"
            inventory.append(item)
            continue
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
            item["rejected_reason"] = "not_regular_file"
            inventory.append(item)
            continue
        local_checks = Checks()
        payload, _ = load_json_artifact(
            root,
            path,
            max_bytes=contract.MAX_METADATA_BYTES,
            private=True,
            prefix="candidate",
            checks=local_checks,
        )
        if payload is None:
            item["rejected_reason"] = "unsafe_or_invalid_json"
            inventory.append(item)
            continue
        item["usable_json"] = True
        stamp_match = METADATA_NAME_RE.fullmatch(name)
        expected_dump = f"vkpi-{stamp_match.group(1)}.dump" if stamp_match else ""
        if (
            payload.get("stamp") == (stamp_match.group(1) if stamp_match else None)
            and payload.get("migration_max") == contract.EXPECTED_PRE_MIGRATION
            and payload.get("dump") == expected_dump
        ):
            exact.append(path)
        inventory.append(item)
    return inventory, exact


def compatibility_candidate_inventory(backup_dir: Path) -> tuple[list[dict[str, Any]], list[Path]]:
    root = backup_dir.parents[1]
    return candidate_inventory(root, backup_dir)


def _parse_sidecar(data: bytes, dump_name: str) -> tuple[str, str] | None:
    try:
        line = data.decode("ascii").strip()
    except UnicodeDecodeError:
        return None
    match = re.fullmatch(r"([0-9a-f]{64})  ([A-Za-z0-9._-]+)", line)
    if not match or match.group(2) != dump_name:
        return None
    return match.group(1), match.group(2)


def validate_backup(
    *,
    root: Path,
    backup_dir: Path,
    source_manifest: Mapping[str, Any],
    now: datetime,
    max_age: timedelta,
    producer_keys: Mapping[str, str],
    runner_keys: Mapping[str, str],
    checks: Checks,
) -> dict[str, Any]:
    inventory, exact = candidate_inventory(root, backup_dir)
    checks.add(
        "backup.exact_migration_243_candidate",
        len(exact) == 1,
        f"exact_candidates={len(exact)}",
    )
    result: dict[str, Any] = {"inventory": inventory, "selected": None}
    if len(exact) != 1:
        return result
    metadata_path = exact[0]
    payload, artifact = load_json_artifact(
        root,
        metadata_path,
        max_bytes=contract.MAX_METADATA_BYTES,
        private=True,
        prefix="backup.metadata",
        checks=checks,
    )
    if payload is None or artifact is None:
        return result
    mapping = contract.strict_object(
        payload,
        required=(
            "schema_version",
            "bundle_id",
            "stamp",
            "migration_max",
            "migration_max_source",
            "dump",
            "dump_bytes",
            "dump_sha256",
            "archive_verified",
            "repository_head",
            "source_manifest_sha256",
            "migration_state",
            "archive_list_receipt",
            "attestation",
        ),
        prefix="backup.metadata.schema",
        checks=checks,
    )
    if mapping is None:
        return result
    schema_ok = contract.schema_version_exact(mapping, prefix="backup.metadata", checks=checks)
    bundle_id = mapping.get("bundle_id")
    stamp_time = contract.parse_stamp(mapping.get("stamp"))
    mtime = datetime.fromtimestamp(artifact.mtime_ns / 1_000_000_000, timezone.utc)
    filename_match = METADATA_NAME_RE.fullmatch(metadata_path.name)
    filename_stamp = filename_match.group(1) if filename_match else None
    identity_ok = checks.add(
        "backup.identity",
        isinstance(bundle_id, str)
        and contract.BUNDLE_ID_RE.fullmatch(bundle_id) is not None
        and mapping.get("stamp") == filename_stamp
        and mapping.get("dump") == f"vkpi-{filename_stamp}.dump"
        and mapping.get("migration_max") == contract.EXPECTED_PRE_MIGRATION
        and mapping.get("migration_max_source") == "schema_migrations"
        and mapping.get("archive_verified") is True,
        "migration 243 schema marker and archive verification required",
    )
    fresh_ok = checks.add(
        "backup.stamp_fresh",
        stamp_time is not None
        and stamp_time <= now + contract.FUTURE_TOLERANCE
        and now - stamp_time <= max_age
        and stamp_time <= mtime + contract.FINALIZATION_TOLERANCE
        and mtime <= now + contract.FUTURE_TOLERANCE,
        "fresh backup stamp required",
    )
    source_ok = checks.add(
        "backup.source_binding",
        mapping.get("repository_head") == source_manifest.get("repository_head")
        and mapping.get("source_manifest_sha256") == source_manifest.get("sha256"),
        "backup must bind approved source and repository HEAD",
    )
    migration_state = contract.validate_migration_state(
        mapping.get("migration_state"), prefix="backup.migration_state", checks=checks
    )
    state_ok = checks.add(
        "backup.migration_state.pre_marker",
        migration_state is not None
        and migration_state["version_keys"][-1] == contract.EXPECTED_PRE_MIGRATION
        and contract.EXPECTED_POST_MIGRATION not in migration_state["version_keys"],
        "exact pre-244 migration key-set required",
    )

    dump_name = mapping.get("dump") if isinstance(mapping.get("dump"), str) else ""
    dump_path = backup_dir / dump_name
    try:
        dump = read_artifact(
            root,
            dump_path,
            max_bytes=contract.MAX_DUMP_BYTES,
            private=True,
            retain_bytes=False,
        )
    except SafeFileError as exc:
        checks.add("backup.dump_file", False, str(exc))
        dump = None
    else:
        checks.add("backup.dump_file", True, "descriptor-pinned archive")
    dump_ok = checks.add(
        "backup.dump_binding",
        dump is not None
        and contract.is_exact_int(
            mapping.get("dump_bytes"), maximum=contract.MAX_DUMP_BYTES
        )
        and mapping.get("dump_bytes") == dump.size
        and mapping.get("dump_sha256") == dump.sha256
        and contract.is_sha256(mapping.get("dump_sha256")),
        "exact non-boolean size and digest required",
    )
    try:
        sidecar = read_artifact(
            root,
            Path(str(dump_path) + ".sha256"),
            max_bytes=256,
            private=True,
        )
    except SafeFileError as exc:
        checks.add("backup.sidecar_file", False, str(exc))
        sidecar_value = None
    else:
        checks.add("backup.sidecar_file", True, "descriptor-pinned sidecar")
        sidecar_value = _parse_sidecar(sidecar.data, dump_name)
    sidecar_ok = checks.add(
        "backup.sidecar_binding",
        dump is not None
        and sidecar_value == (dump.sha256, dump_name),
        "strict sha256 sidecar required",
    )
    secret_ok = contract.check_no_secrets(payload, prefix="backup.metadata", checks=checks)
    attested = contract.verify_producer_attestation(
        payload,
        prefix="backup.metadata",
        now=now,
        not_before=stamp_time,
        finalized_at=mtime,
        max_age=max_age,
        public_keys=producer_keys,
        checks=checks,
    )
    state_sha = contract.json_sha256(migration_state) if migration_state else ""
    receipts = validate_receipts(
        root=root,
        evidence_path=metadata_path,
        descriptors=[mapping.get("archive_list_receipt")],
        required_labels=contract.REQUIRED_BACKUP_RECEIPTS,
        prefix="backup.receipt",
        bundle_id=bundle_id if isinstance(bundle_id, str) else "",
        repository_head=source_manifest.get("repository_head") or "",
        manifest_sha256=source_manifest.get("sha256") or "",
        dump_name=dump_name,
        dump_sha256=dump.sha256 if dump else "",
        migration_hashes=source_manifest.get("migration_hashes") or {},
        state_hashes={"restore": state_sha},
        anchor_hashes={},
        check_hashes={},
        earliest=stamp_time,
        now=now,
        max_age=max_age,
        producer_keys=producer_keys,
        runner_keys=runner_keys,
        checks=checks,
    )
    receipts_ok = len(receipts) == 1 and all(
        item.get("trusted_attestation") is True for item in receipts
    )
    trusted = all(
        (
            schema_ok,
            identity_ok,
            fresh_ok,
            source_ok,
            state_ok,
            dump_ok,
            sidecar_ok,
            secret_ok,
            attested,
            receipts_ok,
        )
    )
    selected = {
        "metadata": str(metadata_path.relative_to(root)),
        "metadata_sha256": artifact.sha256,
        "bundle_id": bundle_id,
        "stamp": mapping.get("stamp"),
        "finalized_at": contract.iso_z(mtime),
        "dump_name": dump_name,
        "dump_sha256": dump.sha256 if dump else None,
        "dump_bytes": dump.size if dump else None,
        "migration_state": migration_state,
        "migration_state_sha256": state_sha or None,
        "trusted_attestation": trusted,
        "archive_list_receipts": receipts,
    }
    result["selected"] = selected
    return result
