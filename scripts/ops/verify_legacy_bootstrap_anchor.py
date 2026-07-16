#!/usr/bin/env python3
"""Create and verify the one-time legacy-to-atomic deployment anchor.

The plan is deliberately secret-free.  It binds the exact legacy runtime,
filesystem, systemd, Redis, and recovery evidence that an automatic rollback
must reproduce.  The normal atomic deployment path does not use this helper.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import stat
import sys
from typing import Any, Mapping, Sequence
from urllib.parse import unquote, urlsplit


PLAN_SCHEMA = "vkpi-first-atomic-bootstrap-plan/v1"
ANCHOR_SCHEMA = "vkpi-legacy-bootstrap-live-anchor/v1"
PREFLIGHT_REPORT_TYPE = "vkpi_legacy_to_atomic_readonly_preflight"
SUCCESS_MARKER_SCHEMA = "vkpi-first-atomic-bootstrap-success/v1"
ALLOWED_BLOCKERS = (
    "release.atomic_helper_present",
    "systemd.nonroot_app_identity",
    "workers.lane_contract",
    "environment.app_readonly",
    "environment.hardened_permissions",
    "health.release_sha_aligned",
)
CORE_UNITS = (
    "viltrox-2.0-test.service",
    "vkpi-worker-interactive.service",
    *(f"vkpi-worker-bulk@{index}.service" for index in range(1, 7)),
)
SHA40_RE = re.compile(r"^[0-9a-f]{40}$")
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
MIGRATION_RE = re.compile(r"^[0-9]{3}_[A-Za-z0-9_.-]+\.sql$")
IDENTIFIER_RE = re.compile(r"^[A-Za-z0-9_.@-]+$")
SERVICE_RE = re.compile(r"^[A-Za-z0-9@_.-]+\.service$")
SSH_TARGET_RE = re.compile(r"^[A-Za-z0-9_.@:-]+$")
SAFE_PATH_RE = re.compile(r"^/[A-Za-z0-9_./@-]+$")
SECRET_KEY_RE = re.compile(
    r"(?i)(?:^|_)(?:authorization|password|passwd|secret|token|api_key|"
    r"access_key|secret_key|database_url|redis_url|cookie|credential)$"
)
URL_CREDENTIAL_RE = re.compile(r"(?i)\b[a-z][a-z0-9+.-]*://[^\s/@:]+:[^\s/@]+@")
TOKEN_VALUE_RE = re.compile(
    r"(?i)(?:bearer\s+[A-Za-z0-9._~-]{8,}|sk-[A-Za-z0-9_-]{8,}|"
    r"AKIA[0-9A-Z]{16})"
)


class AnchorError(RuntimeError):
    """A fail-closed, secret-free contract error."""


def _canonical(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _digest(value: object) -> str:
    return hashlib.sha256(_canonical(value)).hexdigest()


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _expect(condition: bool, code: str) -> None:
    if not condition:
        raise AnchorError(code)


def _contains_secret(value: object, *, key: str = "") -> bool:
    if key and SECRET_KEY_RE.search(key):
        return True
    if isinstance(value, Mapping):
        return any(_contains_secret(item, key=str(item_key)) for item_key, item in value.items())
    if isinstance(value, list):
        return any(_contains_secret(item) for item in value)
    if isinstance(value, str):
        return bool(URL_CREDENTIAL_RE.search(value) or TOKEN_VALUE_RE.search(value))
    return False


def _safe_identifier(value: str, *, code: str) -> str:
    _expect(bool(IDENTIFIER_RE.fullmatch(value)) and value not in {".", ".."}, code)
    return value


def _safe_absolute_path(value: str, *, code: str) -> Path:
    _expect(
        bool(SAFE_PATH_RE.fullmatch(value)) and ".." not in PurePosixPath(value).parts,
        code,
    )
    return Path(value)


def _read_regular(path: Path, *, maximum_bytes: int | None = None) -> tuple[bytes, os.stat_result]:
    try:
        before = path.lstat()
    except OSError as exc:
        raise AnchorError("unsafe_or_missing_file") from exc
    _expect(stat.S_ISREG(before.st_mode) and before.st_nlink == 1, "unsafe_or_missing_file")
    if maximum_bytes is not None:
        _expect(before.st_size <= maximum_bytes, "file_too_large")
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags)
    except OSError as exc:
        raise AnchorError("unsafe_or_missing_file") from exc
    try:
        after = os.fstat(descriptor)
        _expect(
            stat.S_ISREG(after.st_mode)
            and after.st_nlink == 1
            and (before.st_dev, before.st_ino) == (after.st_dev, after.st_ino),
            "file_changed_during_read",
        )
        chunks: list[bytes] = []
        total = 0
        while True:
            chunk = os.read(descriptor, 1024 * 1024)
            if not chunk:
                break
            total += len(chunk)
            if maximum_bytes is not None:
                _expect(total <= maximum_bytes, "file_too_large")
            chunks.append(chunk)
    finally:
        os.close(descriptor)
    return b"".join(chunks), after


def _sha256_regular(path: Path, *, maximum_bytes: int | None = None) -> tuple[str, os.stat_result]:
    try:
        before = path.lstat()
    except OSError as exc:
        raise AnchorError("unsafe_or_missing_file") from exc
    _expect(stat.S_ISREG(before.st_mode) and before.st_nlink == 1, "unsafe_or_missing_file")
    if maximum_bytes is not None:
        _expect(before.st_size <= maximum_bytes, "file_too_large")
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags)
    except OSError as exc:
        raise AnchorError("unsafe_or_missing_file") from exc
    digest = hashlib.sha256()
    total = 0
    try:
        info = os.fstat(descriptor)
        _expect(
            stat.S_ISREG(info.st_mode)
            and info.st_nlink == 1
            and (before.st_dev, before.st_ino) == (info.st_dev, info.st_ino),
            "file_changed_during_read",
        )
        while True:
            chunk = os.read(descriptor, 1024 * 1024)
            if not chunk:
                break
            total += len(chunk)
            if maximum_bytes is not None:
                _expect(total <= maximum_bytes, "file_too_large")
            digest.update(chunk)
        after = os.fstat(descriptor)
        _expect(
            (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns)
            == (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns),
            "file_changed_during_read",
        )
    finally:
        os.close(descriptor)
    return digest.hexdigest(), after


def _load_json(path: Path, *, protected: bool = False) -> dict[str, Any]:
    raw, info = _read_regular(path, maximum_bytes=2 * 1024 * 1024)
    if protected:
        _expect(info.st_uid == os.geteuid(), "plan_owner_mismatch")
        _expect(stat.S_IMODE(info.st_mode) == 0o600, "plan_mode_must_be_0600")
    try:
        payload = json.loads(raw)
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise AnchorError("invalid_json") from exc
    _expect(isinstance(payload, dict), "json_root_must_be_object")
    return payload


def _write_exclusive_json(path: Path, payload: Mapping[str, Any], *, mode: int) -> None:
    _expect(path.is_absolute(), "output_path_must_be_absolute")
    _expect(not path.exists() and not path.is_symlink(), "refusing_existing_output")
    path.parent.mkdir(parents=True, exist_ok=True)
    encoded = _canonical(payload) + b"\n"
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_CLOEXEC", 0)
    flags |= getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(path, flags, mode)
    try:
        os.fchmod(descriptor, mode)
        with os.fdopen(descriptor, "wb", closefd=False) as handle:
            handle.write(encoded)
            handle.flush()
            os.fsync(handle.fileno())
    finally:
        os.close(descriptor)


def _pointer(path: Path) -> dict[str, object]:
    try:
        info = path.lstat()
    except FileNotFoundError:
        return {"kind": "absent"}
    except OSError:
        return {"kind": "unreadable"}
    if stat.S_ISLNK(info.st_mode):
        return {"kind": "symlink"}
    if stat.S_ISDIR(info.st_mode):
        return {"kind": "directory"}
    if stat.S_ISREG(info.st_mode):
        return {"kind": "file"}
    return {"kind": "special"}


def _database_name_from_env(raw: bytes) -> str:
    try:
        lines = raw.decode("utf-8").splitlines()
    except UnicodeError as exc:
        raise AnchorError("environment_not_utf8") from exc
    values: list[str] = []
    for raw_line in lines:
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        if key.strip() != "DATABASE_URL":
            continue
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
            value = value[1:-1]
        values.append(value)
    _expect(len(values) == 1, "environment_database_identity_ambiguous")
    try:
        parsed = urlsplit(values[0])
    except ValueError as exc:
        raise AnchorError("environment_database_url_invalid") from exc
    database = unquote(parsed.path[1:]) if parsed.path.startswith("/") else ""
    _expect(
        parsed.scheme in {"postgres", "postgresql"}
        and bool(re.fullmatch(r"[A-Za-z0-9_]+", database)),
        "environment_database_url_invalid",
    )
    return database


def _collect_anchor(args: argparse.Namespace) -> dict[str, Any]:
    root = _safe_absolute_path(args.root, code="invalid_root").resolve(strict=True)
    _expect(root.is_dir() and not root.is_symlink(), "root_must_be_real_directory")
    stamp = _safe_identifier(args.backup_stamp, code="invalid_backup_stamp")
    env_raw, env_info = _read_regular(root / ".env", maximum_bytes=1024 * 1024)
    build_raw, _ = _read_regular(root / "BUILD_GIT_SHA", maximum_bytes=256)
    try:
        root_build_sha = build_raw.decode("ascii").strip().lower()
    except UnicodeError as exc:
        raise AnchorError("root_build_sha_invalid") from exc
    _expect(bool(SHA40_RE.fullmatch(root_build_sha)), "root_build_sha_invalid")

    backup_root = root / "backups" / "ops"
    backup_dir = backup_root / stamp
    for directory, code in (
        (root / "backups", "backup_parent_invalid"),
        (backup_root, "backup_ops_parent_invalid"),
        (backup_dir, "backup_set_invalid"),
    ):
        try:
            info = directory.lstat()
        except OSError as exc:
            raise AnchorError(code) from exc
        _expect(stat.S_ISDIR(info.st_mode) and not directory.is_symlink(), code)
    _expect(backup_dir.parent.resolve() == backup_root.resolve(), "backup_set_escaped")

    dump_sha, dump_info = _sha256_regular(backup_dir / "prod-db.dump")
    env_cipher_sha, env_cipher_info = _sha256_regular(
        backup_dir / "environment.gpg", maximum_bytes=16 * 1024 * 1024
    )
    receipt_raw, receipt_info = _read_regular(
        backup_dir / "off-host-backup-receipt.json", maximum_bytes=1024 * 1024
    )
    receipt_sha = hashlib.sha256(receipt_raw).hexdigest()
    try:
        receipt = json.loads(receipt_raw)
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise AnchorError("offhost_receipt_invalid") from exc
    required_receipt = {
        "schema_version": "vkpi-off-host-backup-receipt/v1",
        "method": "ssh_pull_verified_mac",
        "stamp": stamp,
        "db_artifact": "prod-db.dump",
        "db_sha256": dump_sha,
        "environment_ciphertext_artifact": "environment.gpg",
        "environment_ciphertext_sha256": env_cipher_sha,
        "pg_restore_list_passed": True,
        "environment_decryption_verified": True,
        "local_copy_verified": True,
        "plaintext_environment_persisted": False,
    }
    _expect(
        isinstance(receipt, dict)
        and all(receipt.get(key) == value for key, value in required_receipt.items()),
        "offhost_receipt_binding_invalid",
    )

    marker = _safe_absolute_path(args.success_marker, code="invalid_success_marker")
    payload = {
        "schema_version": ANCHOR_SCHEMA,
        "root": str(root),
        "root_build_git_sha": root_build_sha,
        "environment": {
            "sha256": hashlib.sha256(env_raw).hexdigest(),
            "uid": env_info.st_uid,
            "gid": env_info.st_gid,
            "mode": f"{stat.S_IMODE(env_info.st_mode):04o}",
            "database_name": _database_name_from_env(env_raw),
        },
        "current": _pointer(root / "current"),
        "previous": _pointer(root / "previous"),
        "success_marker": _pointer(marker),
        "recovery": {
            "backup_stamp": stamp,
            "dump_sha256": dump_sha,
            "dump_size_bytes": dump_info.st_size,
            "environment_cipher_sha256": env_cipher_sha,
            "environment_cipher_size_bytes": env_cipher_info.st_size,
            "offhost_receipt_sha256": receipt_sha,
            "offhost_receipt_size_bytes": receipt_info.st_size,
        },
        "secret_free": True,
    }
    _expect(not _contains_secret(payload), "secret_like_anchor_output_refused")
    return payload


def _unit_summary(preflight: Mapping[str, Any]) -> list[dict[str, Any]]:
    observed = preflight.get("observed")
    units = observed.get("systemd_units") if isinstance(observed, dict) else None
    _expect(isinstance(units, list), "preflight_units_missing")
    by_name = {
        str(unit.get("name")): unit
        for unit in units
        if isinstance(unit, dict) and str(unit.get("name")) in CORE_UNITS
    }
    _expect(set(by_name) == set(CORE_UNITS), "preflight_core_unit_set_mismatch")
    fields = (
        "name",
        "observable",
        "load_state",
        "active_state",
        "unit_file_state",
        "fragment_path",
        "fragment_sha256",
        "fragment_readable",
        "user",
        "group",
        "working_directory",
        "app_role",
        "environment_mode",
        "claim_lane",
        "heartbeat_name",
    )
    return [{key: by_name[name].get(key) for key in fields} for name in CORE_UNITS]


def _redis_summary(preflight: Mapping[str, Any]) -> dict[str, Any]:
    observed = preflight.get("observed")
    redis_state = observed.get("redis") if isinstance(observed, dict) else None
    _expect(isinstance(redis_state, dict), "preflight_redis_missing")
    return {
        key: redis_state.get(key)
        for key in (
            "reachable",
            "aof_enabled",
            "rdb_last_bgsave_status",
            "aof_last_write_status",
            "error_code",
        )
    }


def _health_identity(payload: Mapping[str, Any]) -> dict[str, str]:
    trust = payload.get("trust")
    _expect(isinstance(trust, dict), "health_trust_missing")
    result = {
        "server_git_sha": str(trust.get("server_git_sha") or "").lower(),
        "client_git_sha": str(trust.get("client_git_sha") or "").lower(),
        "db_migration": str(trust.get("db_migration_max") or ""),
    }
    _expect(bool(SHA40_RE.fullmatch(result["server_git_sha"])), "health_server_sha_invalid")
    _expect(bool(SHA40_RE.fullmatch(result["client_git_sha"])), "health_client_sha_invalid")
    _expect(bool(MIGRATION_RE.fullmatch(result["db_migration"])), "health_migration_invalid")
    _expect(str(payload.get("status") or "") == "ok", "health_status_not_ok")
    return result


def _validate_preflight(preflight: Mapping[str, Any]) -> Mapping[str, Any]:
    _expect(preflight.get("report_type") == PREFLIGHT_REPORT_TYPE, "preflight_type_mismatch")
    _expect(preflight.get("mode") == "remote_read_only_preflight", "preflight_mode_mismatch")
    _expect(preflight.get("secret_free") is True, "preflight_not_secret_free")
    _expect(not _contains_secret(preflight), "secret_like_preflight_refused")
    blockers = preflight.get("blocking_check_ids")
    _expect(blockers == list(ALLOWED_BLOCKERS), "preflight_blocker_set_mismatch")
    _expect(preflight.get("decision") == "no-go", "preflight_decision_mismatch")
    checks = preflight.get("checks")
    _expect(isinstance(checks, list), "preflight_checks_missing")
    failed_blocking = [
        str(row.get("id"))
        for row in checks
        if isinstance(row, dict) and row.get("blocking") is True and row.get("pass") is not True
    ]
    _expect(failed_blocking == list(ALLOWED_BLOCKERS), "preflight_failed_check_set_mismatch")
    observed = preflight.get("observed")
    _expect(isinstance(observed, dict), "preflight_observed_missing")
    release = observed.get("release_layout")
    _expect(isinstance(release, dict), "preflight_release_missing")
    _expect(release.get("state") == "legacy_flat", "preflight_not_legacy_flat")
    _expect(
        isinstance(release.get("current"), dict)
        and release["current"].get("kind") == "absent"
        and isinstance(release.get("previous"), dict)
        and release["previous"].get("kind") == "absent",
        "preflight_pointer_not_absent",
    )
    redis_state = _redis_summary(preflight)
    _expect(
        redis_state == {
            "reachable": True,
            "aof_enabled": True,
            "rdb_last_bgsave_status": "ok",
            "aof_last_write_status": "ok",
            "error_code": None,
        },
        "preflight_redis_not_durable",
    )
    return observed


def _candidate_from_args(args: argparse.Namespace) -> dict[str, Any]:
    _safe_identifier(args.release_id, code="invalid_release_id")
    _expect(bool(SHA40_RE.fullmatch(args.git_sha)), "invalid_candidate_git_sha")
    _expect(bool(MIGRATION_RE.fullmatch(args.target_migration)), "invalid_target_migration")
    pending = [value for value in args.pending_migrations.split(",") if value]
    _expect(pending and all(MIGRATION_RE.fullmatch(value) for value in pending), "invalid_pending_migrations")
    _expect(pending[-1] == args.target_migration, "pending_migrations_do_not_reach_target")
    return {
        "release_id": args.release_id,
        "git_sha": args.git_sha,
        "target_migration": args.target_migration,
        "pending_migrations": pending,
        "database_strategy": "staging-clone",
    }


def _target_from_args(args: argparse.Namespace) -> dict[str, str]:
    _expect(bool(SSH_TARGET_RE.fullmatch(args.ssh_target)), "invalid_ssh_target")
    root = str(_safe_absolute_path(args.root, code="invalid_root"))
    _expect(bool(SERVICE_RE.fullmatch(args.service)), "invalid_service")
    parsed = urlsplit(args.health_url)
    _expect(
        parsed.scheme == "http"
        and parsed.hostname in {"127.0.0.1", "localhost"}
        and parsed.path == "/health"
        and not parsed.username
        and not parsed.password
        and not parsed.query
        and not parsed.fragment,
        "invalid_health_url",
    )
    return {
        "ssh_target": args.ssh_target,
        "root": root,
        "service": args.service,
        "health_url": args.health_url,
    }


def _build_plan(
    args: argparse.Namespace,
    preflight: Mapping[str, Any],
    health: Mapping[str, Any],
    anchor: Mapping[str, Any],
) -> dict[str, Any]:
    observed = _validate_preflight(preflight)
    _expect(anchor.get("schema_version") == ANCHOR_SCHEMA, "anchor_schema_mismatch")
    _expect(anchor.get("secret_free") is True and not _contains_secret(anchor), "anchor_not_secret_free")
    target = _target_from_args(args)
    candidate = _candidate_from_args(args)
    health_identity = _health_identity(health)

    preflight_target = preflight.get("target")
    _expect(
        isinstance(preflight_target, dict)
        and preflight_target.get("ssh_target") == target["ssh_target"]
        and preflight_target.get("root") == target["root"],
        "preflight_target_drift",
    )
    _expect(anchor.get("root") == target["root"], "anchor_root_drift")
    preflight_candidate = preflight.get("candidate")
    _expect(
        isinstance(preflight_candidate, dict)
        and preflight_candidate.get("expected_migration") == candidate["target_migration"],
        "preflight_candidate_drift",
    )

    release = observed.get("release_layout")
    database = observed.get("database")
    preflight_health = observed.get("health")
    environment = observed.get("environment")
    backup = observed.get("backup")
    _expect(
        all(isinstance(value, dict) for value in (release, database, preflight_health, environment, backup)),
        "preflight_observed_shape_invalid",
    )
    assert isinstance(release, dict)
    assert isinstance(database, dict)
    assert isinstance(preflight_health, dict)
    assert isinstance(environment, dict)
    assert isinstance(backup, dict)
    anchor_env = anchor.get("environment")
    recovery = anchor.get("recovery")
    _expect(isinstance(anchor_env, dict) and isinstance(recovery, dict), "anchor_shape_invalid")
    assert isinstance(anchor_env, dict)
    assert isinstance(recovery, dict)

    _expect(
        health_identity["server_git_sha"] == preflight_health.get("server_git_sha")
        and health_identity["client_git_sha"] == preflight_health.get("client_git_sha")
        and health_identity["db_migration"] == preflight_health.get("db_migration_max"),
        "health_preflight_drift",
    )
    _expect(health_identity["db_migration"] == database.get("migration_max"), "database_health_drift")
    _expect(anchor_env.get("database_name") == database.get("database_name"), "environment_database_drift")
    _expect(anchor.get("root_build_git_sha") == release.get("root_build_git_sha"), "root_build_sha_drift")
    _expect(health_identity["client_git_sha"] == anchor.get("root_build_git_sha"), "client_root_build_drift")
    _expect(anchor.get("current") == {"kind": "absent"}, "anchor_current_not_absent")
    _expect(anchor.get("previous") == {"kind": "absent"}, "anchor_previous_not_absent")
    _expect(anchor.get("success_marker") == {"kind": "absent"}, "bootstrap_already_marked")
    _expect(
        backup.get("latest_name") == recovery.get("backup_stamp")
        and backup.get("checksum_verified") is True
        and backup.get("catalog_verified") is True
        and backup.get("encrypted_environment_snapshot_present") is True
        and backup.get("off_host_receipt_present") is True,
        "preflight_backup_drift",
    )
    _expect(
        environment.get("mode") == anchor_env.get("mode")
        and environment.get("owner") == "viltrox"
        and environment.get("group") == "viltrox",
        "environment_metadata_drift",
    )
    for key in (
        "sha256",
        "uid",
        "gid",
        "mode",
        "database_name",
    ):
        _expect(anchor_env.get(key) is not None, "anchor_environment_field_missing")
    for key in (
        "backup_stamp",
        "dump_sha256",
        "environment_cipher_sha256",
        "offhost_receipt_sha256",
    ):
        _expect(recovery.get(key) is not None, "anchor_recovery_field_missing")

    plan: dict[str, Any] = {
        "schema_version": PLAN_SCHEMA,
        "created_at": _now(),
        "secret_free": True,
        "allowed_preflight_blockers": list(ALLOWED_BLOCKERS),
        "target": target,
        "candidate": candidate,
        "legacy_anchor": {
            "server_git_sha": health_identity["server_git_sha"],
            "client_git_sha": health_identity["client_git_sha"],
            "root_build_git_sha": anchor["root_build_git_sha"],
            "db_migration": health_identity["db_migration"],
            "database_name": anchor_env["database_name"],
            "environment": {
                key: anchor_env[key] for key in ("sha256", "uid", "gid", "mode")
            },
            "current": {"kind": "absent"},
            "previous": {"kind": "absent"},
            "systemd_summary_sha256": _digest(_unit_summary(preflight)),
            "redis_precondition_sha256": _digest(_redis_summary(preflight)),
        },
        "recovery": {
            key: recovery[key]
            for key in (
                "backup_stamp",
                "dump_sha256",
                "environment_cipher_sha256",
                "offhost_receipt_sha256",
            )
        },
    }
    _expect(not _contains_secret(plan), "secret_like_plan_refused")
    plan["plan_sha256"] = _digest(plan)
    return plan


def _validate_plan_digest(plan: Mapping[str, Any], confirm: str) -> str:
    _expect(plan.get("schema_version") == PLAN_SCHEMA, "plan_schema_mismatch")
    _expect(plan.get("secret_free") is True and not _contains_secret(plan), "plan_not_secret_free")
    expected = str(plan.get("plan_sha256") or "").lower()
    _expect(bool(SHA256_RE.fullmatch(expected)), "plan_sha256_invalid")
    unsigned = dict(plan)
    unsigned.pop("plan_sha256", None)
    _expect(_digest(unsigned) == expected, "plan_content_digest_mismatch")
    _expect(confirm == expected, "bootstrap_confirmation_mismatch")
    _expect(plan.get("allowed_preflight_blockers") == list(ALLOWED_BLOCKERS), "plan_blockers_mismatch")
    return expected


def _compare_live_to_plan(
    args: argparse.Namespace,
    plan: Mapping[str, Any],
    preflight: Mapping[str, Any],
    health: Mapping[str, Any],
    anchor: Mapping[str, Any],
) -> dict[str, Any]:
    generated = _build_plan(args, preflight, health, anchor)
    for key in (
        "secret_free",
        "allowed_preflight_blockers",
        "target",
        "candidate",
        "legacy_anchor",
        "recovery",
    ):
        _expect(plan.get(key) == generated.get(key), f"live_plan_drift_{key}")
    legacy = plan["legacy_anchor"]
    return {
        "plan_sha256": plan["plan_sha256"],
        "server_git_sha": legacy["server_git_sha"],
        "client_git_sha": legacy["client_git_sha"],
        "root_build_git_sha": legacy["root_build_git_sha"],
        "db_migration": legacy["db_migration"],
        "database_name": legacy["database_name"],
        "environment_sha256": legacy["environment"]["sha256"],
        "backup_stamp": plan["recovery"]["backup_stamp"],
        "verified": True,
    }


def _common_live_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--preflight", required=True, type=Path)
    parser.add_argument("--health", required=True, type=Path)
    parser.add_argument("--anchor", required=True, type=Path)
    parser.add_argument("--ssh-target", required=True)
    parser.add_argument("--root", required=True)
    parser.add_argument("--service", required=True)
    parser.add_argument("--health-url", required=True)
    parser.add_argument("--release-id", required=True)
    parser.add_argument("--git-sha", required=True)
    parser.add_argument("--target-migration", required=True)
    parser.add_argument("--pending-migrations", required=True)


def _create_plan(args: argparse.Namespace) -> None:
    preflight = _load_json(args.preflight)
    health = _load_json(args.health)
    anchor = _load_json(args.anchor)
    plan = _build_plan(args, preflight, health, anchor)
    _write_exclusive_json(args.output, plan, mode=0o600)
    sys.stdout.write(f"{plan['plan_sha256']}\n")


def _verify_plan(args: argparse.Namespace) -> None:
    plan = _load_json(args.plan, protected=True)
    _validate_plan_digest(plan, args.confirm)
    summary = _compare_live_to_plan(
        args,
        plan,
        _load_json(args.preflight),
        _load_json(args.health),
        _load_json(args.anchor),
    )
    sys.stdout.write(json.dumps(summary, sort_keys=True, separators=(",", ":")) + "\n")


def _plan_field(args: argparse.Namespace) -> None:
    plan = _load_json(args.plan, protected=True)
    _validate_plan_digest(plan, args.confirm)
    allowed = {
        "recovery.backup_stamp": ("recovery", "backup_stamp"),
        "legacy_anchor.environment.sha256": ("legacy_anchor", "environment", "sha256"),
        "legacy_anchor.server_git_sha": ("legacy_anchor", "server_git_sha"),
        "legacy_anchor.client_git_sha": ("legacy_anchor", "client_git_sha"),
        "plan_sha256": ("plan_sha256",),
    }
    _expect(args.field in allowed, "plan_field_not_allowlisted")
    value: object = plan
    for key in allowed[args.field]:
        _expect(isinstance(value, Mapping) and key in value, "plan_field_missing")
        value = value[key]
    _expect(isinstance(value, str), "plan_field_not_string")
    sys.stdout.write(value + "\n")


def _verify_candidate(args: argparse.Namespace) -> None:
    plan = _load_json(args.plan, protected=True)
    _validate_plan_digest(plan, args.confirm)
    manifest = _load_json(args.manifest)
    candidate = plan.get("candidate")
    legacy = plan.get("legacy_anchor")
    _expect(isinstance(candidate, dict) and isinstance(legacy, dict), "plan_shape_invalid")
    expected = {
        "schema": 2,
        "release_id": candidate["release_id"],
        "git_sha": candidate["git_sha"],
        "pending_migrations": candidate["pending_migrations"],
        "forward_compatible_migrations": [],
        "database_strategy": "staging-clone",
        "source_database": legacy["database_name"],
        "target_database": args.target_database,
        "env_fingerprint_before": legacy["environment"]["sha256"],
        "database_owner_release_id": None,
        "immutable_owner_uid": 0,
        "immutable_owner_gid": 0,
    }
    _expect(all(manifest.get(key) == value for key, value in expected.items()), "sealed_candidate_identity_mismatch")
    _expect(bool(SHA256_RE.fullmatch(str(manifest.get("payload_sha256") or ""))), "sealed_candidate_payload_sha_invalid")
    count = manifest.get("payload_entry_count")
    _expect(isinstance(count, int) and not isinstance(count, bool) and count > 0, "sealed_candidate_entry_count_invalid")
    sys.stdout.write(
        json.dumps(
            {"candidate_seal_verified": True, "payload_sha256": manifest["payload_sha256"]},
            sort_keys=True,
        )
        + "\n"
    )


def _verify_rollback(args: argparse.Namespace) -> None:
    plan = _load_json(args.plan, protected=True)
    _validate_plan_digest(plan, args.confirm)
    summary = _compare_live_to_plan(
        args,
        plan,
        _load_json(args.preflight),
        _load_json(args.health),
        _load_json(args.anchor),
    )
    sys.stdout.write(
        json.dumps({**summary, "rollback_exact": True}, sort_keys=True, separators=(",", ":"))
        + "\n"
    )


def _write_success_marker(args: argparse.Namespace) -> None:
    _expect(bool(SHA256_RE.fullmatch(args.plan_sha256)), "plan_sha256_invalid")
    _safe_identifier(args.release_id, code="invalid_release_id")
    _expect(bool(SHA40_RE.fullmatch(args.git_sha)), "invalid_candidate_git_sha")
    marker = _safe_absolute_path(args.marker, code="invalid_success_marker")
    try:
        parent_info = marker.parent.lstat()
    except OSError as exc:
        raise AnchorError("success_marker_parent_missing") from exc
    _expect(
        stat.S_ISDIR(parent_info.st_mode)
        and not marker.parent.is_symlink()
        and parent_info.st_uid == os.geteuid()
        and not stat.S_IMODE(parent_info.st_mode) & 0o022,
        "success_marker_parent_unsafe",
    )
    payload = {
        "schema_version": SUCCESS_MARKER_SCHEMA,
        "accepted_at": _now(),
        "plan_sha256": args.plan_sha256,
        "release_id": args.release_id,
        "git_sha": args.git_sha,
        "secret_free": True,
    }
    _write_exclusive_json(marker, payload, mode=0o444)
    _expect(stat.S_IMODE(marker.lstat().st_mode) == 0o444, "success_marker_mode_mismatch")
    sys.stdout.write(json.dumps(payload, sort_keys=True, separators=(",", ":")) + "\n")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)

    collect = subparsers.add_parser("collect-anchor")
    collect.add_argument("--root", required=True)
    collect.add_argument("--backup-stamp", required=True)
    collect.add_argument("--success-marker", required=True)

    create = subparsers.add_parser("create-plan")
    _common_live_args(create)
    create.add_argument("--output", required=True, type=Path)

    verify = subparsers.add_parser("verify-plan")
    _common_live_args(verify)
    verify.add_argument("--plan", required=True, type=Path)
    verify.add_argument("--confirm", required=True)

    field = subparsers.add_parser("plan-field")
    field.add_argument("--plan", required=True, type=Path)
    field.add_argument("--confirm", required=True)
    field.add_argument("--field", required=True)

    candidate = subparsers.add_parser("verify-candidate")
    candidate.add_argument("--plan", required=True, type=Path)
    candidate.add_argument("--confirm", required=True)
    candidate.add_argument("--manifest", required=True, type=Path)
    candidate.add_argument("--target-database", required=True)

    rollback = subparsers.add_parser("verify-rollback")
    _common_live_args(rollback)
    rollback.add_argument("--plan", required=True, type=Path)
    rollback.add_argument("--confirm", required=True)

    marker = subparsers.add_parser("write-success-marker")
    marker.add_argument("--marker", required=True)
    marker.add_argument("--plan-sha256", required=True)
    marker.add_argument("--release-id", required=True)
    marker.add_argument("--git-sha", required=True)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.command == "collect-anchor":
            sys.stdout.write(
                json.dumps(_collect_anchor(args), sort_keys=True, separators=(",", ":"))
                + "\n"
            )
        elif args.command == "create-plan":
            _create_plan(args)
        elif args.command == "verify-plan":
            _verify_plan(args)
        elif args.command == "plan-field":
            _plan_field(args)
        elif args.command == "verify-candidate":
            _verify_candidate(args)
        elif args.command == "verify-rollback":
            _verify_rollback(args)
        elif args.command == "write-success-marker":
            _write_success_marker(args)
        else:  # pragma: no cover
            raise AnchorError("unsupported_command")
    except AnchorError as exc:
        sys.stderr.write(f"legacy_bootstrap_anchor_error:{exc}\n")
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
