#!/usr/bin/env python3
"""Verify and single-use consume dual authority for a cloud remediation plan.

This gate never runs a remediation command and never opens a network socket.
It verifies two distinct Ed25519 signatures against an externally provisioned
public-key allowlist.  ``consume`` writes one owner-private, O_EXCL replay
ledger receipt; it still performs no cloud mutation.
"""

from __future__ import annotations

import argparse
import base64
import binascii
import hashlib
import json
import os
import re
import secrets
import stat
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Mapping, Sequence

try:
    from cryptography.exceptions import InvalidSignature
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
except ImportError:  # pragma: no cover - exercised as a fail-closed branch.
    InvalidSignature = ValueError  # type: ignore[assignment,misc]
    Ed25519PublicKey = None  # type: ignore[assignment,misc]

if __package__:
    from .cloud_preflight_remediation_bundle import (
        APPROVAL_ROLES,
        MUTATION_SCOPES,
        RemediationError,
        _canonical_bytes,
        _load_json_object,
        verify_plan,
    )
else:
    from cloud_preflight_remediation_bundle import (  # type: ignore[no-redef]
        APPROVAL_ROLES,
        MUTATION_SCOPES,
        RemediationError,
        _canonical_bytes,
        _load_json_object,
        verify_plan,
    )


APPROVAL_SCHEMA = "vkpi-cloud-remediation-approval/v1"
TRUST_ROOTS_SCHEMA = "vkpi-cloud-remediation-trust-roots/v1"
CONSUMPTION_SCHEMA = "vkpi-cloud-remediation-approval-consumption/v1"
KEY_ID_RE = re.compile(r"^[A-Za-z0-9._-]{3,64}$")
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
NONCE_RE = re.compile(r"^[A-Za-z0-9._:-]{16,128}$")
MAX_JSON_BYTES = 1024 * 1024
MAX_APPROVAL_SECONDS = 3600
MAX_FUTURE_SKEW_SECONDS = 120
SIGNED_FIELDS = (
    "schema_version",
    "algorithm",
    "role",
    "scope",
    "key_id",
    "plan_sha256",
    "preflight_sha256",
    "target",
    "immutable_bindings",
    "nonce_sha256",
    "issued_at",
    "expires_at",
)


class AuthorityError(RuntimeError):
    pass


def _parse_utc(value: object, *, field: str) -> datetime:
    if not isinstance(value, str) or not value.endswith("Z"):
        raise AuthorityError(f"{field} must be an explicit UTC timestamp")
    try:
        parsed = datetime.fromisoformat(value[:-1] + "+00:00")
    except ValueError as exc:
        raise AuthorityError(f"{field} is malformed") from exc
    if parsed.tzinfo is None or parsed.utcoffset() != timedelta(0):
        raise AuthorityError(f"{field} must be UTC")
    return parsed.astimezone(timezone.utc)


def _read_private_json(path: Path) -> tuple[dict[str, Any], bytes]:
    before = path.lstat()
    mode = stat.S_IMODE(before.st_mode)
    if not stat.S_ISREG(before.st_mode) or before.st_nlink != 1 or mode & 0o077:
        raise AuthorityError(f"approval must be an owner-private regular file: {path}")
    return _load_json_object(path, maximum_bytes=MAX_JSON_BYTES)


def _read_trust_roots(path: Path, *, require_root_owner: bool) -> tuple[dict[str, Any], bytes]:
    before = path.lstat()
    mode = stat.S_IMODE(before.st_mode)
    if (
        not stat.S_ISREG(before.st_mode)
        or before.st_nlink != 1
        or mode & 0o022
        or (require_root_owner and before.st_uid != 0)
    ):
        raise AuthorityError("trust roots must be a non-writable root-owned regular file")
    roots, raw = _load_json_object(path, maximum_bytes=MAX_JSON_BYTES)
    if roots.get("schema_version") != TRUST_ROOTS_SCHEMA:
        raise AuthorityError("trust roots schema is unsupported")
    roles = roots.get("roles")
    if not isinstance(roles, dict) or set(roles) != set(APPROVAL_ROLES):
        raise AuthorityError("trust roots must define exactly both authority roles")
    return roots, raw


def _failure(*reasons: str) -> dict[str, Any]:
    return {
        "schema_version": "vkpi-cloud-remediation-authority-verification/v1",
        "trusted": False,
        "status": "untrusted_or_unapproved",
        "scope": None,
        "plan_sha256": None,
        "nonce_sha256": None,
        "roles_verified": [],
        "key_ids": [],
        "distinct_signers": False,
        "signatures_valid": False,
        "plan_binding_valid": False,
        "time_binding_valid": False,
        "execution_ready_for_authorization": False,
        "signature_persisted": False,
        "raw_nonce_persisted": False,
        "private_key_read": False,
        "remote_writes_performed": 0,
        "network_calls_performed": 0,
        "failure_reasons": sorted(set(reasons or ("authority_not_verified",))),
    }


def _approval_message(payload: Mapping[str, Any]) -> bytes:
    return _canonical_bytes({field: payload.get(field) for field in SIGNED_FIELDS})


def verify_authority(
    *,
    plan: Mapping[str, Any],
    scope: str,
    nonce: str,
    approvals: Sequence[tuple[Mapping[str, Any], bytes]],
    trust_roots: Mapping[str, Any],
    evaluated_at: datetime | None = None,
) -> dict[str, Any]:
    result = _failure("authority_unverified")
    plan_result = verify_plan(plan)
    plan_sha = plan.get("plan_sha256")
    nonce_valid = bool(NONCE_RE.fullmatch(nonce))
    nonce_sha = hashlib.sha256(nonce.encode("utf-8")).hexdigest() if nonce_valid else None
    result.update(
        {
            "scope": scope if scope in MUTATION_SCOPES else None,
            "plan_sha256": plan_sha if isinstance(plan_sha, str) else None,
            "nonce_sha256": nonce_sha,
            "execution_ready_for_authorization": plan.get("execution_ready_for_authorization") is True,
        }
    )
    failures: list[str] = []
    if not plan_result["valid"]:
        failures.append("plan_invalid")
    if plan.get("execution_ready_for_authorization") is not True:
        failures.append("plan_unbound_or_missing_sources")
    if scope not in MUTATION_SCOPES:
        failures.append("scope_invalid")
    if not nonce_valid:
        failures.append("nonce_invalid")
    if len(approvals) != 2:
        failures.append("exactly_two_approvals_required")
    if Ed25519PublicKey is None:
        failures.append("ed25519_verifier_unavailable")
    roles_root = trust_roots.get("roles") if isinstance(trust_roots, Mapping) else None
    if not isinstance(roles_root, Mapping):
        failures.append("trust_roots_invalid")

    now = (evaluated_at or datetime.now(timezone.utc)).astimezone(timezone.utc)
    verified_roles: list[str] = []
    key_ids: list[str] = []
    key_materials: list[str] = []
    approval_hashes: list[str] = []
    all_signatures = len(approvals) == 2 and Ed25519PublicKey is not None
    all_bindings = len(approvals) == 2
    all_times = len(approvals) == 2
    allowed_fields = set(SIGNED_FIELDS) | {"signature_base64"}
    for index, (payload, raw) in enumerate(approvals):
        prefix = f"approval_{index + 1}"
        unknown = set(payload) - allowed_fields
        missing = allowed_fields - set(payload)
        if unknown or missing:
            failures.append(f"{prefix}_fields")
            all_signatures = False
            all_bindings = False
            continue
        role = payload.get("role")
        key_id = payload.get("key_id")
        if role not in APPROVAL_ROLES:
            failures.append(f"{prefix}_role")
            all_signatures = False
            continue
        if not isinstance(key_id, str) or not KEY_ID_RE.fullmatch(key_id):
            failures.append(f"{prefix}_key_id")
            all_signatures = False
            continue
        role_keys = roles_root.get(role) if isinstance(roles_root, Mapping) else None
        registered = role_keys.get(key_id) if isinstance(role_keys, Mapping) else None
        if not isinstance(registered, str):
            failures.append(f"{prefix}_signer_not_allowlisted")
            all_signatures = False
            continue
        binding_valid = bool(
            payload.get("schema_version") == APPROVAL_SCHEMA
            and payload.get("algorithm") == "Ed25519"
            and payload.get("scope") == scope
            and isinstance(plan_sha, str)
            and secrets.compare_digest(str(payload.get("plan_sha256") or ""), plan_sha)
            and secrets.compare_digest(
                str(payload.get("preflight_sha256") or ""),
                str(plan.get("source_preflight", {}).get("sha256") or ""),
            )
            and payload.get("target") == plan.get("target")
            and payload.get("immutable_bindings") == plan.get("immutable_bindings")
            and isinstance(nonce_sha, str)
            and secrets.compare_digest(str(payload.get("nonce_sha256") or ""), nonce_sha)
        )
        if not binding_valid:
            failures.append(f"{prefix}_binding")
            all_bindings = False
        try:
            issued = _parse_utc(payload.get("issued_at"), field="issued_at")
            expires = _parse_utc(payload.get("expires_at"), field="expires_at")
            time_valid = bool(
                timedelta(0) < expires - issued <= timedelta(seconds=MAX_APPROVAL_SECONDS)
                and issued <= now + timedelta(seconds=MAX_FUTURE_SKEW_SECONDS)
                and now <= expires
            )
        except AuthorityError:
            time_valid = False
        if not time_valid:
            failures.append(f"{prefix}_time")
            all_times = False
        signature_valid = False
        try:
            public_bytes = base64.b64decode(registered.encode("ascii"), validate=True)
            signature = base64.b64decode(
                str(payload.get("signature_base64") or "").encode("ascii"), validate=True
            )
            if len(public_bytes) == 32 and len(signature) == 64 and Ed25519PublicKey is not None:
                Ed25519PublicKey.from_public_bytes(public_bytes).verify(
                    signature, _approval_message(payload)
                )
                signature_valid = True
        except (InvalidSignature, ValueError, TypeError, UnicodeError, binascii.Error):
            signature_valid = False
        if not signature_valid:
            failures.append(f"{prefix}_signature")
            all_signatures = False
        if signature_valid and binding_valid and time_valid:
            verified_roles.append(role)
            key_ids.append(key_id)
            key_materials.append(registered)
            approval_hashes.append(hashlib.sha256(raw).hexdigest())

    distinct = bool(
        sorted(verified_roles) == sorted(APPROVAL_ROLES)
        and len(set(key_ids)) == 2
        and len(set(key_materials)) == 2
    )
    if not distinct:
        failures.append("authority_roles_or_signers_not_distinct")
    result.update(
        {
            "roles_verified": sorted(verified_roles),
            "key_ids": sorted(key_ids),
            "approval_file_sha256": sorted(approval_hashes),
            "distinct_signers": distinct,
            "signatures_valid": all_signatures,
            "plan_binding_valid": all_bindings,
            "time_binding_valid": all_times,
        }
    )
    if failures:
        result["failure_reasons"] = sorted(set(failures))
        return result
    result.update(
        {
            "trusted": True,
            "status": "dual_authority_verified_not_consumed",
            "failure_reasons": [],
        }
    )
    return result


def consume_authority(
    verified: Mapping[str, Any], *, ledger_dir: Path, consumed_at: datetime | None = None
) -> dict[str, Any]:
    if verified.get("trusted") is not True or verified.get("status") != "dual_authority_verified_not_consumed":
        return {**_failure("authority_not_verified_for_consumption"), **{
            "scope": verified.get("scope"),
            "plan_sha256": verified.get("plan_sha256"),
            "nonce_sha256": verified.get("nonce_sha256"),
        }}
    ledger_dir.mkdir(parents=True, mode=0o700, exist_ok=True)
    info = ledger_dir.lstat()
    if not stat.S_ISDIR(info.st_mode) or stat.S_IMODE(info.st_mode) & 0o077:
        return {**_failure("authority_ledger_not_private"), **{
            "scope": verified.get("scope"),
            "plan_sha256": verified.get("plan_sha256"),
            "nonce_sha256": verified.get("nonce_sha256"),
        }}
    record = {
        "schema_version": CONSUMPTION_SCHEMA,
        "scope": verified["scope"],
        "plan_sha256": verified["plan_sha256"],
        "nonce_sha256": verified["nonce_sha256"],
        "roles_verified": verified["roles_verified"],
        "key_ids": verified["key_ids"],
        "approval_file_sha256": verified.get("approval_file_sha256", []),
        "consumed_at": (consumed_at or datetime.now(timezone.utc)).astimezone(timezone.utc).isoformat().replace("+00:00", "Z"),
        "signature_persisted": False,
        "raw_nonce_persisted": False,
        "private_key_read": False,
        "remote_writes_performed": 0,
        "network_calls_performed": 0,
    }
    filename = f"{record['scope']}-{record['plan_sha256']}-{record['nonce_sha256']}.json"
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(ledger_dir / filename, flags, 0o600)
    except FileExistsError:
        return {**_failure("authority_nonce_already_consumed"), **{
            "scope": verified.get("scope"),
            "plan_sha256": verified.get("plan_sha256"),
            "nonce_sha256": verified.get("nonce_sha256"),
        }}
    try:
        with os.fdopen(descriptor, "wb") as handle:
            encoded = _canonical_bytes(record) + b"\n"
            handle.write(encoded)
            handle.flush()
            os.fsync(handle.fileno())
    except Exception:
        (ledger_dir / filename).unlink(missing_ok=True)
        raise
    result = dict(verified)
    result.update(
        {
            "status": "dual_authority_consumed_once",
            "nonce_consumed": True,
            "consumption_record_sha256": hashlib.sha256(_canonical_bytes(record) + b"\n").hexdigest(),
            "signature_persisted": False,
            "raw_nonce_persisted": False,
            "private_key_read": False,
        }
    )
    return result


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("verify", "consume"))
    parser.add_argument("--plan", required=True)
    parser.add_argument("--scope", required=True, choices=MUTATION_SCOPES)
    parser.add_argument("--nonce", required=True)
    parser.add_argument("--approval", action="append", required=True)
    parser.add_argument("--trust-roots", default="/etc/vkpi/cloud-remediation-trust-roots.json")
    parser.add_argument("--ledger-dir", default="/var/lib/vkpi/cloud-remediation-authority")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        if len(args.approval) != 2:
            raise AuthorityError("exactly two --approval files are required")
        plan, _ = _load_json_object(Path(args.plan).expanduser().resolve(), maximum_bytes=MAX_JSON_BYTES)
        roots, _ = _read_trust_roots(
            Path(args.trust_roots).expanduser().resolve(), require_root_owner=True
        )
        approvals = [
            _read_private_json(Path(path).expanduser().resolve()) for path in args.approval
        ]
        result = verify_authority(
            plan=plan,
            scope=args.scope,
            nonce=args.nonce,
            approvals=approvals,
            trust_roots=roots,
        )
        if args.command == "consume" and result["trusted"]:
            result = consume_authority(result, ledger_dir=Path(args.ledger_dir).expanduser())
        sys.stdout.write(json.dumps(result, sort_keys=True) + "\n")
        return 0 if result.get("trusted") is True and (
            args.command == "verify" or result.get("nonce_consumed") is True
        ) else 3
    except (OSError, RemediationError, AuthorityError, ValueError) as exc:
        sys.stderr.write(f"cloud remediation authority failed: {exc}\n")
        return 3


if __name__ == "__main__":
    raise SystemExit(main())
