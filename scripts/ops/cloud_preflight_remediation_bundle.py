#!/usr/bin/env python3
"""Build a non-executable, content-addressed cloud remediation runbook.

The input is the secret-free read-only legacy-to-atomic preflight report.  The
output deliberately contains commands as data, never an SSH/mutation runner.
Every mutating phase is bound to the report digest and requires two distinct
Ed25519 approvals (release authority + operator authority) before a separate,
reviewed executor may consume it.

This module has no network imports and exposes no command capable of changing
the target.  ``build`` and ``verify`` only read local files and write a local
0600 JSON artifact.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import stat
import sys
import tempfile
from pathlib import Path, PurePosixPath
from typing import Any, Mapping, Sequence


PLAN_SCHEMA = "vkpi-cloud-remediation-plan/v1"
PREFLIGHT_SCHEMA = 1
PREFLIGHT_REPORT_TYPE = "vkpi_legacy_to_atomic_readonly_preflight"
SUPPORTED_BLOCKERS = (
    "release.atomic_helper_present",
    "systemd.nonroot_app_identity",
    "workers.lane_contract",
    "environment.app_readonly",
    "environment.hardened_permissions",
    "health.release_sha_aligned",
    "redis.aof_enabled",
    "backup.encrypted_environment_snapshot",
    "backup.off_host_receipt",
)
APPROVAL_ROLES = ("release_authority", "operator_authority")
MUTATION_SCOPES = ("apply", "rollback")
SHA40_RE = re.compile(r"^[0-9a-f]{40}$")
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
RELEASE_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{2,127}$")
AGE_RECIPIENT_RE = re.compile(r"^(age1[0-9a-z]{20,100}|ssh-(rsa|ed25519) [A-Za-z0-9+/=]{32,800})$")
RCLONE_REMOTE_RE = re.compile(r"^[A-Za-z0-9_.-]{1,64}:[A-Za-z0-9_./-]{1,512}$")
SAFE_ROOT_RE = re.compile(r"^/[A-Za-z0-9_./-]+$")
SECRET_KEY_RE = re.compile(
    r"(?i)(secret|password|passwd|access[_-]?token|refresh[_-]?token|private[_-]?key|database[_-]?url|redis[_-]?url)"
)
SECRET_VALUE_RE = re.compile(
    r"(?i)(bearer\s+[A-Za-z0-9._~+/=-]{8,}|[a-z][a-z0-9+.-]*://[^\s/:]+:[^\s/@]+@)"
)
MAX_REPORT_BYTES = 8 * 1024 * 1024


class RemediationError(RuntimeError):
    pass


def _canonical_bytes(value: object) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _read_regular(path: Path, *, maximum_bytes: int) -> bytes:
    before = path.lstat()
    if (
        not stat.S_ISREG(before.st_mode)
        or before.st_nlink != 1
        or before.st_size > maximum_bytes
    ):
        raise RemediationError(f"unsafe regular input: {path}")
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(path, flags)
    try:
        after = os.fstat(descriptor)
        if (
            not stat.S_ISREG(after.st_mode)
            or after.st_nlink != 1
            or (before.st_dev, before.st_ino) != (after.st_dev, after.st_ino)
            or after.st_size > maximum_bytes
        ):
            raise RemediationError(f"input changed while reading: {path}")
        raw = b""
        while len(raw) <= maximum_bytes:
            chunk = os.read(descriptor, min(1024 * 1024, maximum_bytes + 1 - len(raw)))
            if not chunk:
                break
            raw += chunk
        if len(raw) > maximum_bytes:
            raise RemediationError(f"input is too large: {path}")
        return raw
    finally:
        os.close(descriptor)


def _load_json_object(path: Path, *, maximum_bytes: int) -> tuple[dict[str, Any], bytes]:
    raw = _read_regular(path, maximum_bytes=maximum_bytes)
    try:
        value = json.loads(raw.decode("utf-8"))
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise RemediationError(f"invalid JSON input: {path}") from exc
    if not isinstance(value, dict):
        raise RemediationError(f"JSON input must be an object: {path}")
    return value, raw


def _contains_secret(value: object, *, key: str = "") -> bool:
    # Boolean/status fields such as ``secret_free`` and ``secrets_read`` are
    # evidence, not credentials.  A sensitive-shaped key is rejected only
    # when it actually carries string/byte material.
    if key and SECRET_KEY_RE.search(key) and isinstance(value, (str, bytes)) and bool(value):
        return True
    if isinstance(value, Mapping):
        return any(_contains_secret(item, key=str(item_key)) for item_key, item in value.items())
    if isinstance(value, list):
        return any(_contains_secret(item) for item in value)
    return isinstance(value, str) and bool(SECRET_VALUE_RE.search(value))


def _safe_root(value: object) -> str:
    text = str(value or "").strip()
    if not SAFE_ROOT_RE.fullmatch(text) or ".." in PurePosixPath(text).parts:
        raise RemediationError("preflight target root is unsafe")
    return text.rstrip("/") or "/"


def _validate_preflight(report: Mapping[str, Any]) -> tuple[list[str], dict[str, Any]]:
    if report.get("schema_version") != PREFLIGHT_SCHEMA:
        raise RemediationError("unsupported preflight schema")
    if report.get("report_type") != PREFLIGHT_REPORT_TYPE:
        raise RemediationError("unexpected preflight report type")
    if report.get("mode") != "remote_read_only_preflight" or report.get("secret_free") is not True:
        raise RemediationError("preflight is not a secret-free remote read-only report")
    if _contains_secret(report):
        raise RemediationError("preflight report contains secret-shaped material")
    checks = report.get("checks")
    if not isinstance(checks, list):
        raise RemediationError("preflight checks are missing")
    check_ids: set[str] = set()
    actual: list[str] = []
    for check in checks:
        if not isinstance(check, dict) or not isinstance(check.get("id"), str):
            raise RemediationError("preflight check is malformed")
        check_id = check["id"]
        if check_id in check_ids:
            raise RemediationError(f"duplicate preflight check: {check_id}")
        check_ids.add(check_id)
        if check.get("blocking") is True and check.get("pass") is False:
            actual.append(check_id)
    declared = report.get("blocking_check_ids")
    if not isinstance(declared, list) or sorted(declared) != sorted(actual):
        raise RemediationError("declared blocker ids disagree with evaluated checks")
    unsupported = sorted(set(actual) - set(SUPPORTED_BLOCKERS))
    if unsupported:
        raise RemediationError("unsupported blocking checks: " + ", ".join(unsupported))
    target = report.get("target")
    if not isinstance(target, dict):
        raise RemediationError("preflight target is missing")
    root = _safe_root(target.get("root"))
    ssh_target = str(target.get("ssh_target") or "").strip()
    if not re.fullmatch(r"[A-Za-z0-9_.@-]{1,128}", ssh_target):
        raise RemediationError("preflight SSH target alias is unsafe")
    return actual, {"root": root, "ssh_target": ssh_target}


def _source_hashes(project_root: Path) -> dict[str, str | None]:
    paths = (
        "scripts/ops/cloud_preflight_remediation_bundle.py",
        "scripts/ops/cloud_remediation_authority.py",
        "scripts/ops/atomic_release_layout.py",
        "scripts/ops/atomic_release_cli.py",
        "scripts/ops/atomic_release_integrity.py",
        "scripts/ops/atomic_release_shared.py",
        "scripts/ops/atomic_release_units.py",
        "scripts/ops/atomic_release_worker_preflight.py",
        "scripts/ops/legacy_to_atomic_preflight.py",
        "scripts/ops/legacy_to_atomic_preflight_report.py",
        "scripts/ops/systemd/viltrox-2.0-test.service",
        "scripts/ops/systemd/vkpi-worker-interactive.service",
        "scripts/ops/systemd/vkpi-worker-bulk@.service",
        "scripts/ops/systemd/vkpi-redis-worker.service",
        "scripts/ops/systemd/vkpi-lane-overrides.env",
    )
    result: dict[str, str | None] = {}
    for relative in paths:
        path = project_root / relative
        if not path.is_file() or path.is_symlink():
            result[relative] = None
            continue
        result[relative] = _sha256_bytes(_read_regular(path, maximum_bytes=4 * 1024 * 1024))
    return result


def _bindings(
    args: argparse.Namespace, report: Mapping[str, Any]
) -> tuple[dict[str, str | None], list[str]]:
    release_id = args.candidate_release_id or None
    staging_database = (
        "viltrox2_test_release_"
        + hashlib.sha256(release_id.encode("utf-8")).hexdigest()[:20]
        if release_id
        else None
    )
    observed = report.get("observed") if isinstance(report.get("observed"), Mapping) else {}
    backup = observed.get("backup") if isinstance(observed.get("backup"), Mapping) else {}
    values: dict[str, str | None] = {
        "candidate_release_id": release_id,
        "candidate_git_sha": (args.candidate_git_sha or "").lower() or None,
        "candidate_bundle_sha256": (args.candidate_bundle_sha256 or "").lower() or None,
        "age_recipient": args.age_recipient or None,
        "offhost_remote": args.offhost_remote or None,
        "backup_set": str(backup.get("latest_name") or "").strip() or None,
        "pending_migrations": args.pending_migrations or None,
        "staging_database": staging_database,
        "env_fingerprint_before": (args.env_fingerprint_before or "").lower() or None,
    }
    validators = {
        "candidate_release_id": RELEASE_ID_RE,
        "candidate_git_sha": SHA40_RE,
        "candidate_bundle_sha256": SHA256_RE,
        "age_recipient": AGE_RECIPIENT_RE,
        "offhost_remote": RCLONE_REMOTE_RE,
        "backup_set": re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{2,127}$"),
        "pending_migrations": re.compile(
            r"^[0-9]{3}_[A-Za-z0-9_]+\.sql(?:,[0-9]{3}_[A-Za-z0-9_]+\.sql)*$"
        ),
        "staging_database": re.compile(r"^viltrox2_test_release_[0-9a-f]{20}$"),
        "env_fingerprint_before": SHA256_RE,
    }
    missing: list[str] = []
    for name, matcher in validators.items():
        value = values[name]
        if value is None:
            missing.append(name)
        elif not matcher.fullmatch(value):
            raise RemediationError(f"invalid immutable binding: {name}")
    return values, missing


def _cmd(*parts: str) -> list[str]:
    return list(parts)


def _step(
    check_id: str,
    *,
    phase: int,
    purpose: str,
    preconditions: Sequence[str],
    apply: Sequence[str],
    rollback: Sequence[str],
    verify: Sequence[str],
    shared_transaction: str | None = None,
) -> dict[str, Any]:
    return {
        "check_id": check_id,
        "phase": phase,
        "purpose": purpose,
        "preconditions": _cmd(*preconditions),
        "apply_commands": _cmd(*apply),
        "rollback_commands": _cmd(*rollback),
        "verification_commands": _cmd(*verify),
        "shared_transaction": shared_transaction,
        "idempotency": "preconditions + exact target state + postcondition; a satisfied postcondition is a no-op",
        "failure_policy": "stop immediately; do not continue to the next phase; run separately dual-approved rollback",
    }


def _remediation_steps(root: str) -> list[dict[str, Any]]:
    qroot = root
    units = "viltrox-2.0-test.service vkpi-worker-interactive.service " + " ".join(
        f"vkpi-worker-bulk@{index}.service" for index in range(1, 7)
    )
    worker_units = "vkpi-worker-interactive.service " + " ".join(
        f"vkpi-worker-bulk@{index}.service" for index in range(1, 7)
    )
    receipt = f"{qroot}/runtime/ops/cloud-remediation/${{VKPI_PLAN_BINDING_ID}}"
    candidate = f"{qroot}/releases/${{VKPI_CANDIDATE_RELEASE_ID}}"
    backup = f"{qroot}/backups/ops/${{VKPI_BACKUP_SET}}"
    redis = "/usr/bin/redis-cli -s /run/redis/redis-server.sock"
    return [
        _step(
            "backup.encrypted_environment_snapshot",
            phase=10,
            purpose="Create and locally verify an encrypted environment recovery artifact before any mutation.",
            preconditions=(
                f"test -f {qroot}/.env && test ! -L {qroot}/.env",
                "command -v age >/dev/null",
                f"test -d {backup} && test ! -L {backup}",
                "test -n \"${VKPI_AGE_RECIPIENT}\"",
            ),
            apply=(
                f"sudo age --recipient \"${{VKPI_AGE_RECIPIENT}}\" --output {backup}/environment.age.tmp {qroot}/.env",
                f"sudo install -o root -g root -m 0600 {backup}/environment.age.tmp {backup}/environment.age",
                f"sudo rm -f {backup}/environment.age.tmp",
            ),
            rollback=(
                "retain the encrypted recovery artifact as audit evidence; never delete it during rollback",
            ),
            verify=(
                f"sudo test -s {backup}/environment.age && sudo test ! -L {backup}/environment.age",
                f"sudo age --decrypt --identity /etc/vkpi/backup-age-identity.txt --output /dev/null {backup}/environment.age",
            ),
        ),
        _step(
            "backup.off_host_receipt",
            phase=20,
            purpose="Copy the reviewed backup set off-host and retain a checksum-bound receipt.",
            preconditions=(
                "command -v rclone >/dev/null",
                "sudo test -f /etc/vkpi/rclone.conf && sudo test ! -L /etc/vkpi/rclone.conf",
                f"sudo sha256sum -c {backup}/prod-db.dump.sha256",
                f"sudo pg_restore --list {backup}/prod-db.dump >/dev/null",
                "test -n \"${VKPI_OFFHOST_REMOTE}\"",
            ),
            apply=(
                f"sudo install -d -o root -g root -m 0700 {receipt}",
                f"sudo rclone --config /etc/vkpi/rclone.conf copy {backup} \"${{VKPI_OFFHOST_REMOTE}}/${{VKPI_BACKUP_SET}}\" --immutable --checksum --exclude /off-host-backup-receipt.json",
                f"sudo rclone --config /etc/vkpi/rclone.conf check {backup} \"${{VKPI_OFFHOST_REMOTE}}/${{VKPI_BACKUP_SET}}\" --one-way --checksum --exclude /off-host-backup-receipt.json --combined {receipt}/offhost-check.txt",
                f"sudo python3 -c 'import hashlib,json,os,pathlib,sys; c=pathlib.Path(sys.argv[1]).read_bytes(); assert all((not line) or line[:1]==b\"=\" for line in c.splitlines()); p={{\"schema_version\":\"vkpi-off-host-backup-receipt/v1\",\"copy_verified\":True,\"plan_sha256\":sys.argv[2],\"offhost_remote_sha256\":hashlib.sha256(sys.argv[3].encode()).hexdigest(),\"rclone_check_sha256\":hashlib.sha256(c).hexdigest()}}; o=pathlib.Path(sys.argv[4]); o.write_text(json.dumps(p,sort_keys=True)+\"\\n\"); os.chmod(o,0o600)' {receipt}/offhost-check.txt \"${{VKPI_PLAN_SHA256}}\" \"${{VKPI_OFFHOST_REMOTE}}\" {receipt}/offhost-receipt.pending.json",
                f"sudo install -o root -g root -m 0600 {receipt}/offhost-receipt.pending.json {backup}/off-host-backup-receipt.json",
            ),
            rollback=(
                "retain off-host evidence and local receipt; destructive remote deletion is outside rollback scope",
            ),
            verify=(
                f"sudo test -s {backup}/off-host-backup-receipt.json && sudo test ! -L {backup}/off-host-backup-receipt.json",
                f"sudo rclone --config /etc/vkpi/rclone.conf check {backup} \"${{VKPI_OFFHOST_REMOTE}}/${{VKPI_BACKUP_SET}}\" --one-way --checksum --exclude /off-host-backup-receipt.json",
            ),
        ),
        _step(
            "release.atomic_helper_present",
            phase=30,
            purpose="Install the reviewed atomic helper and all local dependencies without activating a release.",
            preconditions=(
                f"test -d {candidate} && test ! -L {candidate}",
                f"test -s {candidate}/scripts/ops/atomic_release_layout.py",
                f"test -s {candidate}/scripts/ops/atomic_release_cli.py",
                f"test -s {candidate}/scripts/ops/atomic_release_integrity.py",
                f"test -s {candidate}/scripts/ops/atomic_release_shared.py",
                f"test -s {candidate}/scripts/ops/atomic_release_units.py",
                f"test -s {candidate}/scripts/ops/atomic_release_worker_preflight.py",
                f"test \"$(cat {candidate}/.bundle.sha256)\" = \"${{VKPI_CANDIDATE_BUNDLE_SHA256}}\"",
                f"test ! -e {qroot}/scripts/ops/atomic_release_layout.py",
                f"test ! -e {qroot}/scripts/ops/atomic_release_cli.py",
                f"test ! -e {qroot}/scripts/ops/atomic_release_integrity.py",
                f"test ! -e {qroot}/scripts/ops/atomic_release_shared.py",
                f"test ! -e {qroot}/scripts/ops/atomic_release_units.py",
                f"test ! -e {qroot}/scripts/ops/atomic_release_worker_preflight.py",
            ),
            apply=(
                f"sudo install -d -o root -g root -m 0755 {qroot}/scripts/ops",
                f"sudo install -o root -g root -m 0555 {candidate}/scripts/ops/atomic_release_layout.py {qroot}/scripts/ops/atomic_release_layout.py",
                f"sudo install -o root -g root -m 0444 {candidate}/scripts/ops/atomic_release_cli.py {qroot}/scripts/ops/atomic_release_cli.py",
                f"sudo install -o root -g root -m 0444 {candidate}/scripts/ops/atomic_release_integrity.py {qroot}/scripts/ops/atomic_release_integrity.py",
                f"sudo install -o root -g root -m 0444 {candidate}/scripts/ops/atomic_release_shared.py {qroot}/scripts/ops/atomic_release_shared.py",
                f"sudo install -o root -g root -m 0444 {candidate}/scripts/ops/atomic_release_units.py {qroot}/scripts/ops/atomic_release_units.py",
                f"sudo install -o root -g root -m 0444 {candidate}/scripts/ops/atomic_release_worker_preflight.py {qroot}/scripts/ops/atomic_release_worker_preflight.py",
            ),
            rollback=(
                f"sudo rm -f {qroot}/scripts/ops/atomic_release_layout.py {qroot}/scripts/ops/atomic_release_cli.py {qroot}/scripts/ops/atomic_release_integrity.py {qroot}/scripts/ops/atomic_release_shared.py {qroot}/scripts/ops/atomic_release_units.py {qroot}/scripts/ops/atomic_release_worker_preflight.py",
            ),
            verify=(
                f"sudo python3 {qroot}/scripts/ops/atomic_release_layout.py --help >/dev/null",
                f"sudo cmp -s {candidate}/scripts/ops/atomic_release_layout.py {qroot}/scripts/ops/atomic_release_layout.py",
                f"sudo cmp -s {candidate}/scripts/ops/atomic_release_cli.py {qroot}/scripts/ops/atomic_release_cli.py",
                f"sudo cmp -s {candidate}/scripts/ops/atomic_release_integrity.py {qroot}/scripts/ops/atomic_release_integrity.py",
                f"sudo cmp -s {candidate}/scripts/ops/atomic_release_shared.py {qroot}/scripts/ops/atomic_release_shared.py",
                f"sudo cmp -s {candidate}/scripts/ops/atomic_release_units.py {qroot}/scripts/ops/atomic_release_units.py",
                f"sudo cmp -s {candidate}/scripts/ops/atomic_release_worker_preflight.py {qroot}/scripts/ops/atomic_release_worker_preflight.py",
            ),
        ),
        _step(
            "environment.hardened_permissions",
            phase=40,
            purpose="Move the shared environment file to root ownership while retaining app read access.",
            preconditions=(
                f"sudo test -s {backup}/environment.age",
                f"test \"$(stat -c '%U:%G:%a' {qroot}/.env)\" = 'viltrox:viltrox:600'",
            ),
            apply=(
                f"sudo chown root:viltrox {qroot}/.env",
                f"sudo chmod 0640 {qroot}/.env",
            ),
            rollback=(
                f"sudo chown viltrox:viltrox {qroot}/.env",
                f"sudo chmod 0600 {qroot}/.env",
            ),
            verify=(
                f"test \"$(stat -c '%U:%G:%a' {qroot}/.env)\" = 'root:viltrox:640'",
            ),
            shared_transaction="environment-permissions",
        ),
        _step(
            "environment.app_readonly",
            phase=40,
            purpose="Prove the app account can read but cannot modify the shared environment file.",
            preconditions=(
                f"test \"$(stat -c '%U:%G:%a' {qroot}/.env)\" = 'root:viltrox:640'",
            ),
            apply=(
                "no additional mutation; this check is the behavioral half of environment-permissions",
            ),
            rollback=(
                "covered by environment.hardened_permissions rollback",
            ),
            verify=(
                f"sudo -n -u viltrox -g viltrox test -r {qroot}/.env",
                f"! sudo -n -u viltrox -g viltrox test -w {qroot}/.env",
            ),
            shared_transaction="environment-permissions",
        ),
        _step(
            "redis.aof_enabled",
            phase=50,
            purpose="Enable Redis AOF through the local administrative socket and persist the configuration.",
            preconditions=(
                "sudo test -S /run/redis/redis-server.sock",
                f"test \"$(sudo -u redis {redis} --raw CONFIG GET appendonly | tail -n 1)\" = 'no'",
                f"sudo install -d -o root -g root -m 0700 {receipt}/redis",
                f"sudo cp --preserve=all /etc/redis/redis.conf {receipt}/redis/redis.conf.before",
            ),
            apply=(
                f"sudo -u redis {redis} CONFIG SET appendonly yes >/dev/null",
                f"sudo -u redis {redis} CONFIG REWRITE >/dev/null",
            ),
            rollback=(
                f"sudo -u redis {redis} CONFIG SET appendonly no >/dev/null",
                f"sudo -u redis {redis} CONFIG REWRITE >/dev/null",
                f"sudo install -o redis -g redis -m 0640 {receipt}/redis/redis.conf.before /etc/redis/redis.conf",
                "sudo systemctl restart redis-server.service",
            ),
            verify=(
                f"test \"$(sudo -u redis {redis} --raw CONFIG GET appendonly | tail -n 1)\" = 'yes'",
                f"sudo -u redis {redis} --raw INFO persistence | grep -Eq '^aof_enabled:1$'",
                f"sudo -u redis {redis} --raw INFO persistence | grep -Eq '^aof_last_write_status:ok$'",
            ),
        ),
        _step(
            "systemd.nonroot_app_identity",
            phase=60,
            purpose="Replace the legacy root worker fragments with reviewed viltrox:viltrox units.",
            preconditions=(
                f"sudo install -d -o root -g root -m 0700 {receipt}/units",
                f"for unit in {units}; do sudo systemctl cat \"$unit\" >/dev/null; done",
                f"for unit in {units}; do sudo cp --preserve=all \"$(systemctl show -p FragmentPath --value $unit)\" {receipt}/units/; done",
                f"grep -Fx 'User=viltrox' {candidate}/scripts/ops/systemd/vkpi-worker-interactive.service >/dev/null",
                f"grep -Fx 'User=viltrox' {candidate}/scripts/ops/systemd/vkpi-worker-bulk@.service >/dev/null",
            ),
            apply=(
                f"sudo systemctl stop {worker_units}",
                f"sudo install -o root -g root -m 0644 {candidate}/scripts/ops/systemd/viltrox-2.0-test.service /etc/systemd/system/viltrox-2.0-test.service",
                f"sudo install -o root -g root -m 0644 {candidate}/scripts/ops/systemd/vkpi-worker-interactive.service /etc/systemd/system/vkpi-worker-interactive.service",
                f"sudo install -o root -g root -m 0644 {candidate}/scripts/ops/systemd/vkpi-worker-bulk@.service /etc/systemd/system/vkpi-worker-bulk@.service",
                f"sudo install -d -o root -g root -m 0755 /etc/vkpi && sudo install -o root -g root -m 0644 {candidate}/scripts/ops/systemd/vkpi-lane-overrides.env /etc/vkpi/vkpi-lane-overrides.env",
                "sudo systemctl daemon-reload",
            ),
            rollback=(
                f"sudo systemctl stop {worker_units}",
                f"sudo install -o root -g root -m 0644 {receipt}/units/viltrox-2.0-test.service /etc/systemd/system/viltrox-2.0-test.service",
                f"sudo install -o root -g root -m 0644 {receipt}/units/vkpi-worker-interactive.service /etc/systemd/system/vkpi-worker-interactive.service",
                f"sudo install -o root -g root -m 0644 {receipt}/units/vkpi-worker-bulk@.service /etc/systemd/system/vkpi-worker-bulk@.service",
                "sudo systemctl daemon-reload",
                f"sudo systemctl restart {units}",
            ),
            verify=(
                f"for unit in {units}; do test \"$(systemctl show -p User --value $unit)\" = viltrox; test \"$(systemctl show -p Group --value $unit)\" = viltrox; done",
            ),
            shared_transaction="systemd-runtime",
        ),
        _step(
            "workers.lane_contract",
            phase=60,
            purpose="Pin interactive and bulk worker lanes at argv level so stale .env values cannot widen claims.",
            preconditions=(
                f"grep -F 'APIFY_WORKER_CLAIM_LANE=interactive' {candidate}/scripts/ops/systemd/vkpi-worker-interactive.service >/dev/null",
                f"grep -F 'APIFY_WORKER_CLAIM_LANE=batch' {candidate}/scripts/ops/systemd/vkpi-worker-bulk@.service >/dev/null",
            ),
            apply=(
                f"sudo systemctl restart {worker_units}",
            ),
            rollback=(
                "covered by systemd.nonroot_app_identity rollback",
            ),
            verify=(
                "test \"$(systemctl show -p Environment --value vkpi-worker-interactive.service | tr ' ' '\\n' | grep '^APIFY_WORKER_CLAIM_LANE=' | tail -n1)\" = 'APIFY_WORKER_CLAIM_LANE=interactive'",
                f"for unit in {' '.join(f'vkpi-worker-bulk@{i}.service' for i in range(1, 7))}; do systemctl cat \"$unit\" | grep -F 'APIFY_WORKER_CLAIM_LANE=batch' >/dev/null; done",
            ),
            shared_transaction="systemd-runtime",
        ),
        _step(
            "health.release_sha_aligned",
            phase=70,
            purpose="Activate only the sealed candidate so server, client and workers report the same signed SHA.",
            preconditions=(
                f"sudo python3 {qroot}/scripts/ops/atomic_release_layout.py verify-seal --root {qroot} --release-id \"${{VKPI_CANDIDATE_RELEASE_ID}}\" --expected-owner-uid 0 --expected-owner-gid 0",
                f"test \"$(cat {candidate}/BUILD_GIT_SHA)\" = \"${{VKPI_CANDIDATE_GIT_SHA}}\"",
                f"sudo test -s {backup}/off-host-backup-receipt.json",
            ),
            apply=(
                f"sudo python3 {qroot}/scripts/ops/atomic_release_layout.py prepare --root {qroot} --release-id \"${{VKPI_CANDIDATE_RELEASE_ID}}\" --unit-dir /etc/systemd/system --unit-name viltrox-2.0-test.service --unit-name vkpi-worker-interactive.service --unit-name vkpi-worker-bulk@.service --pending-migrations \"${{VKPI_PENDING_MIGRATIONS}}\" --database-strategy staging-clone --source-database viltrox2_test --target-database \"${{VKPI_STAGING_DATABASE}}\" --env-fingerprint-before \"${{VKPI_ENV_FINGERPRINT_BEFORE}}\"",
                f"sudo python3 {qroot}/scripts/ops/atomic_release_layout.py activate --root {qroot} --release-id \"${{VKPI_CANDIDATE_RELEASE_ID}}\"",
                f"sudo systemctl restart {units}",
            ),
            rollback=(
                f"sudo systemctl stop {units}",
                f"sudo python3 {qroot}/scripts/ops/atomic_release_layout.py restore --root {qroot} --release-id \"${{VKPI_CANDIDATE_RELEASE_ID}}\" --unit-dir /etc/systemd/system",
                "sudo systemctl daemon-reload",
                f"sudo systemctl restart {units}",
            ),
            verify=(
                f"sudo -n -u viltrox -g viltrox {qroot}/.venv/bin/python {qroot}/current/scripts/ops/fetch_runtime_health.py --url http://127.0.0.1:8001/health --env-file {qroot}/.env > {receipt}/health.after.json",
                f"sudo -n -u viltrox -g viltrox {qroot}/.venv/bin/python {qroot}/current/scripts/verify_runtime_health.py --expected-head \"${{VKPI_CANDIDATE_GIT_SHA}}\" < {receipt}/health.after.json",
            ),
            shared_transaction="atomic-activation",
        ),
    ]


def build_plan(report_path: Path, args: argparse.Namespace) -> dict[str, Any]:
    report, raw = _load_json_object(report_path, maximum_bytes=MAX_REPORT_BYTES)
    blockers, target = _validate_preflight(report)
    if tuple(blockers) != tuple(SUPPORTED_BLOCKERS):
        raise RemediationError(
            "this closure bundle requires the exact reviewed nine-blocker snapshot; observed: "
            + ", ".join(blockers)
        )
    bindings, missing = _bindings(args, report)
    project_root = Path(args.project_root).resolve()
    source_hashes = _source_hashes(project_root)
    missing_sources = sorted(path for path, digest in source_hashes.items() if digest is None)
    report_sha = _sha256_bytes(raw)
    binding_id = f"{report_sha[:16]}-{bindings['candidate_release_id'] or 'UNBOUND'}"
    steps = _remediation_steps(target["root"])
    coverage = {step["check_id"] for step in steps}
    if coverage != set(SUPPORTED_BLOCKERS) or len(steps) != len(SUPPORTED_BLOCKERS):
        raise RemediationError("internal remediation coverage is incomplete")
    execution_ready = not missing and not missing_sources
    base: dict[str, Any] = {
        "schema_version": PLAN_SCHEMA,
        "mode": "local_review_only_command_bundle",
        "source_preflight": {
            "path_basename": report_path.name,
            "sha256": report_sha,
            "generated_at": report.get("generated_at"),
            "decision": report.get("decision"),
            "secret_free": True,
        },
        "target": target,
        "blockers": list(SUPPORTED_BLOCKERS),
        "immutable_bindings": bindings,
        "executor_environment_binding_contract": {
            "VKPI_CANDIDATE_RELEASE_ID": "immutable_bindings.candidate_release_id",
            "VKPI_CANDIDATE_GIT_SHA": "immutable_bindings.candidate_git_sha",
            "VKPI_CANDIDATE_BUNDLE_SHA256": "immutable_bindings.candidate_bundle_sha256",
            "VKPI_AGE_RECIPIENT": "immutable_bindings.age_recipient",
            "VKPI_OFFHOST_REMOTE": "immutable_bindings.offhost_remote",
            "VKPI_BACKUP_SET": "immutable_bindings.backup_set",
            "VKPI_PENDING_MIGRATIONS": "immutable_bindings.pending_migrations",
            "VKPI_STAGING_DATABASE": "immutable_bindings.staging_database",
            "VKPI_ENV_FINGERPRINT_BEFORE": "immutable_bindings.env_fingerprint_before",
            "VKPI_PLAN_BINDING_ID": "binding_id",
            "VKPI_PLAN_SHA256": "plan_sha256",
        },
        "binding_id": binding_id,
        "source_artifact_sha256": source_hashes,
        "missing_bindings": missing,
        "missing_source_artifacts": missing_sources,
        "execution_ready_for_authorization": execution_ready,
        "execution_allowed": False,
        "mutation_interface_present": False,
        "command_representation": "data_only_never_executed_by_this_tool",
        "security_contract": {
            "remote_writes_performed": 0,
            "network_calls_performed": 0,
            "secrets_read": 0,
            "secrets_emitted": 0,
            "unsigned_execution_refused": True,
            "unbound_execution_refused": True,
            "two_distinct_approvals_required_per_mutation_scope": True,
            "approval_roles": list(APPROVAL_ROLES),
            "mutation_scopes": list(MUTATION_SCOPES),
            "approval_algorithm": "Ed25519",
            "approval_max_validity_seconds": 3600,
            "approval_must_bind": [
                "plan_sha256",
                "preflight_sha256",
                "target",
                "immutable_bindings",
                "scope",
                "nonce_sha256",
                "issued_at",
                "expires_at",
            ],
            "approval_replay_protection": "atomic O_EXCL nonce ledger required before first mutation",
            "rollback_requires_fresh_separate_dual_approval": True,
            "trust_roots": "externally provisioned public keys; never supplied by the approval artifact",
        },
        "global_preconditions": [
            "rerun the read-only cloud preflight immediately before authorization",
            "require identical preflight SHA-256 and exact nine blocking check ids",
            "assemble and seal the candidate release; do not deploy from the dirty worktree",
            "bind the concrete release id, Git SHA, candidate bundle SHA-256, age recipient and off-host destination before signing",
            "capture the current unit fragments and timer states before the first mutation",
            "create apply and rollback approval pairs before maintenance starts",
        ],
        "transaction_order": [
            "10 encrypted environment backup",
            "20 off-host receipt",
            "30 atomic helper",
            "40 environment ownership/read-only",
            "50 Redis AOF",
            "60 non-root systemd + worker lanes",
            "70 atomic activation + release SHA alignment",
            "80 rerun full read-only preflight; require zero blocking failures",
        ],
        "steps": steps,
        "completion_verification": [
            "run scripts/ops/legacy_to_atomic_preflight.py in read-only mode with the same target and expected migration",
            "require decision=go, blocking_check_ids=[], secret_free=true",
            "require current and previous pointers to remain inside the releases directory",
            "require every core unit active as viltrox:viltrox and bulk lanes=batch",
            "require app .env root:viltrox 0640, readable but not writable by viltrox",
            "require server/client/worker SHA alignment, Redis AOF healthy, encrypted env snapshot and off-host receipt",
        ],
        "authorization_templates": [
            {
                "schema_version": "vkpi-cloud-remediation-approval/v1",
                "algorithm": "Ed25519",
                "role": role,
                "scope": scope,
                "key_id": None,
                "plan_sha256": "filled_after_plan_seal",
                "preflight_sha256": report_sha,
                "target": target,
                "immutable_bindings": bindings,
                "nonce_sha256": None,
                "issued_at": None,
                "expires_at": None,
                "signature_base64": None,
            }
            for scope in MUTATION_SCOPES
            for role in APPROVAL_ROLES
        ],
    }
    if _contains_secret(base):
        raise RemediationError("generated plan contains secret-shaped material")
    plan_sha = _sha256_bytes(_canonical_bytes(base))
    plan = dict(base)
    plan["plan_sha256"] = plan_sha
    for template in plan["authorization_templates"]:
        template["plan_sha256"] = plan_sha
    # Authorization templates are not part of the authority-bearing digest;
    # they merely copy it. Re-seal the stable payload excluding those copies.
    stable = dict(plan)
    stable.pop("plan_sha256", None)
    for template in stable["authorization_templates"]:
        template["plan_sha256"] = "filled_after_plan_seal"
    if _sha256_bytes(_canonical_bytes(stable)) != plan_sha:
        raise RemediationError("plan seal is internally inconsistent")
    return plan


def verify_plan(plan: Mapping[str, Any]) -> dict[str, Any]:
    checks: dict[str, bool] = {}
    checks["schema"] = plan.get("schema_version") == PLAN_SCHEMA
    checks["non_executable"] = (
        plan.get("execution_allowed") is False
        and plan.get("mutation_interface_present") is False
        and plan.get("command_representation") == "data_only_never_executed_by_this_tool"
    )
    checks["secret_free"] = not _contains_secret(plan)
    blockers = plan.get("blockers")
    steps = plan.get("steps")
    checks["exact_blocker_set"] = blockers == list(SUPPORTED_BLOCKERS)
    checks["one_step_per_blocker"] = bool(
        isinstance(steps, list)
        and len(steps) == len(SUPPORTED_BLOCKERS)
        and {step.get("check_id") for step in steps if isinstance(step, dict)}
        == set(SUPPORTED_BLOCKERS)
    )
    checks["commands_complete"] = bool(
        isinstance(steps, list)
        and all(
            isinstance(step, dict)
            and all(
                isinstance(step.get(field), list) and bool(step[field])
                for field in (
                    "preconditions",
                    "apply_commands",
                    "rollback_commands",
                    "verification_commands",
                )
            )
            for step in steps
        )
    )
    contract = plan.get("security_contract")
    checks["dual_approval_contract"] = bool(
        isinstance(contract, dict)
        and contract.get("two_distinct_approvals_required_per_mutation_scope") is True
        and contract.get("approval_roles") == list(APPROVAL_ROLES)
        and contract.get("mutation_scopes") == list(MUTATION_SCOPES)
        and contract.get("rollback_requires_fresh_separate_dual_approval") is True
    )
    templates = plan.get("authorization_templates")
    checks["approval_templates"] = bool(
        isinstance(templates, list)
        and {(item.get("scope"), item.get("role")) for item in templates if isinstance(item, dict)}
        == {(scope, role) for scope in MUTATION_SCOPES for role in APPROVAL_ROLES}
    )
    expected = plan.get("plan_sha256")
    stable = json.loads(json.dumps(plan)) if isinstance(plan, dict) else {}
    stable.pop("plan_sha256", None)
    if isinstance(stable.get("authorization_templates"), list):
        for template in stable["authorization_templates"]:
            if isinstance(template, dict):
                template["plan_sha256"] = "filled_after_plan_seal"
    checks["plan_sha256"] = bool(
        isinstance(expected, str)
        and SHA256_RE.fullmatch(expected)
        and _sha256_bytes(_canonical_bytes(stable)) == expected
    )
    bindings = plan.get("immutable_bindings")
    missing = plan.get("missing_bindings")
    sources = plan.get("source_artifact_sha256")
    missing_sources = plan.get("missing_source_artifacts")
    expected_ready = bool(
        isinstance(bindings, dict)
        and isinstance(missing, list)
        and not missing
        and isinstance(sources, dict)
        and isinstance(missing_sources, list)
        and not missing_sources
        and all(isinstance(value, str) and SHA256_RE.fullmatch(value) for value in sources.values())
    )
    checks["execution_readiness_truthful"] = plan.get("execution_ready_for_authorization") is expected_ready
    return {
        "schema_version": "vkpi-cloud-remediation-plan-verification/v1",
        "valid": all(checks.values()),
        "checks": checks,
        "plan_sha256": expected if isinstance(expected, str) else None,
        "execution_ready_for_authorization": plan.get("execution_ready_for_authorization") is True,
        "execution_allowed": False,
        "remote_writes_performed": 0,
    }


def _atomic_write_json(path: Path, value: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    temporary = Path(temporary_name)
    try:
        os.fchmod(descriptor, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(value, handle, ensure_ascii=False, sort_keys=True, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        os.chmod(path, 0o600)
    finally:
        temporary.unlink(missing_ok=True)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    build = subparsers.add_parser("build")
    build.add_argument("--preflight", required=True)
    build.add_argument("--output", required=True)
    build.add_argument("--project-root", default=str(Path(__file__).resolve().parents[2]))
    build.add_argument("--candidate-release-id")
    build.add_argument("--candidate-git-sha")
    build.add_argument("--candidate-bundle-sha256")
    build.add_argument("--age-recipient")
    build.add_argument("--offhost-remote")
    build.add_argument("--pending-migrations")
    build.add_argument("--env-fingerprint-before")
    verify = subparsers.add_parser("verify")
    verify.add_argument("--plan", required=True)
    verify.add_argument("--output")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        if args.command == "build":
            plan = build_plan(Path(args.preflight).expanduser().resolve(), args)
            verification = verify_plan(plan)
            if not verification["valid"]:
                raise RemediationError("generated plan failed self-verification")
            _atomic_write_json(Path(args.output).expanduser().resolve(), plan)
            sys.stdout.write(json.dumps(verification, sort_keys=True) + "\n")
            return 0
        plan, _raw = _load_json_object(
            Path(args.plan).expanduser().resolve(), maximum_bytes=MAX_REPORT_BYTES
        )
        verification = verify_plan(plan)
        if args.output:
            _atomic_write_json(Path(args.output).expanduser().resolve(), verification)
        sys.stdout.write(json.dumps(verification, sort_keys=True) + "\n")
        return 0 if verification["valid"] else 2
    except (OSError, RemediationError, ValueError) as exc:
        sys.stderr.write(f"cloud remediation bundle failed: {exc}\n")
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
