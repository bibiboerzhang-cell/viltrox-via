"""Exact, forward-only release plan bindings; never a live drain authorization."""
from __future__ import annotations

import hashlib
import json
import os
import re
import stat
from pathlib import Path
from typing import Any

if __package__:
    from .atomic_release_units import LayoutError
else:
    from atomic_release_units import LayoutError


DEFAULT_POLICY = "rollback-compatible"
FORWARD_ONLY_POLICY = "forward-only-307-310-v1"
EVIDENCE_SCHEMA = "vkpi.forward-only-release-plan/v1"
MIGRATION_SHA256 = {
    "307_users_token_version.sql": "5ca5b5809828ffab06afa6e6dcff918945d07e6a1bbb3865ad8b5abc5c6a0d7f",
    "308_vkpi_privacy_retention_columns.sql": "1c7fa59da67fa73a572b6017201a20d9cb07bcec718952db47652f36a24b1fe8",
    "309_vkpi_dsar_public_intake.sql": "39c722f00919c689243b468c77936e8e2c19fc79ab92533984c2c3cf101b4570",
    "310_vkpi_kol_search_refresh_scheduler.sql": "6199f8470d3fe3b0ce406678318be81b3abd6a74033f3d6b15bac2fd085d237b",
}
_SHA256_FIELDS = ("env_sha256", "backup_receipt_sha256", "unknown_manifest_sha256")
_EVIDENCE_FIELDS = {
    "schema", "release_id", "source_git_sha", "target_git_sha",
    "database_identity", "pending_migrations", *_SHA256_FIELDS,
}
_POLICY_FLAGS = {
    "recovery_mode": "forward-only",
    "app_rollback_allowed": False,
    "database_rollback_allowed": False,
    "preserve_unknown_budget": True,
    "live_drain_authorized": False,
    "claim_status": "seal_plan_binding_only",
}


def _hex(value: Any, size: int, label: str) -> str:
    if not isinstance(value, str) or re.fullmatch(rf"[0-9a-f]{{{size}}}", value) is None:
        raise LayoutError(f"forward-only plan has invalid {label}")
    return value


def migration_names(declaration: str) -> tuple[str, ...]:
    if not isinstance(declaration, str):
        raise LayoutError("forward-only migrations must be an exact ordered CSV")
    names = tuple(declaration.split(","))
    if not names or names != tuple(name for name in MIGRATION_SHA256 if name in names):
        raise LayoutError("forward-only migrations must be a nonempty ordered unique reviewed subset")
    return names


def _database_identity(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != {"system_identifier", "oid", "name"}:
        raise LayoutError("forward-only plan database identity is incomplete")
    system_id, oid, name = value["system_identifier"], value["oid"], value["name"]
    if (not isinstance(system_id, str) or len(system_id) > 20 or not system_id.isascii() or not system_id.isdecimal()
            or not 0 < int(system_id) < 2**64 or str(int(system_id)) != system_id):
        raise LayoutError("forward-only plan database system identifier is invalid")
    if type(oid) is not int or not 0 < oid < 2**32:
        raise LayoutError("forward-only plan database OID is invalid")
    if not isinstance(name, str) or re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]{0,62}", name) is None:
        raise LayoutError("forward-only plan database name is invalid")
    return dict(value)


def validate_plan(evidence: Any, names: tuple[str, ...]) -> dict[str, Any]:
    """Check plan syntax/bindings, without claiming its referenced evidence is true."""
    if not isinstance(evidence, dict) or set(evidence) != _EVIDENCE_FIELDS:
        raise LayoutError("forward-only plan evidence fields are incomplete or unreviewed")
    if evidence["schema"] != EVIDENCE_SCHEMA:
        raise LayoutError("forward-only plan evidence schema is invalid")
    release_id = evidence["release_id"]
    if (not isinstance(release_id, str) or release_id in {".", ".."}
            or re.fullmatch(r"[A-Za-z0-9_.-]+", release_id) is None):
        raise LayoutError("forward-only plan release id is invalid")
    for field in ("source_git_sha", "target_git_sha"):
        _hex(evidence[field], 40, field)
    for field in _SHA256_FIELDS:
        _hex(evidence[field], 64, field)
    if not isinstance(evidence["pending_migrations"], list) or evidence["pending_migrations"] != list(names):
        raise LayoutError("forward-only plan pending migrations do not match")
    return {**evidence, "database_identity": _database_identity(evidence["database_identity"]),
            "pending_migrations": list(names)}


def _migration_proof(names: tuple[str, ...], directory: Path) -> list[dict[str, str]]:
    proof = []
    for name in names:
        path = directory / name
        info = path.lstat()
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
            raise LayoutError("forward-only migration must be a regular single-link file")
        descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
        with os.fdopen(descriptor, "rb") as handle:
            opened = os.fstat(handle.fileno())
            if (not stat.S_ISREG(opened.st_mode) or opened.st_nlink != 1
                    or (opened.st_dev, opened.st_ino) != (info.st_dev, info.st_ino) or opened.st_size > 65536):
                raise LayoutError("forward-only migration changed or exceeds the reviewed size")
            payload = handle.read(65537)
        after = path.lstat()
        if (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns) != (
                after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns):
            raise LayoutError("forward-only migration changed during validation")
        digest = hashlib.sha256(payload).hexdigest()
        if digest != MIGRATION_SHA256[name]:
            raise LayoutError(f"forward-only migration bytes are not reviewed: {name}")
        proof.append({"name": name, "sha256": digest})
    return proof


def _database_metadata(strategy: str, source_database: str, target_database: str,
                       env_fingerprint_before: str, owner: str, plan: dict[str, Any]) -> dict[str, Any]:
    if strategy == "in-place":
        if source_database or target_database or env_fingerprint_before or owner:
            raise LayoutError("in-place forward-only release cannot declare clone metadata")
        return {"database_strategy": strategy, "source_database": None, "target_database": None,
                "env_fingerprint_before": None, "database_owner_release_id": None}
    if strategy != "reuse-active-clone":
        raise LayoutError("forward-only policy requires the existing database, never a new clone")
    if not isinstance(owner, str) or owner in {"", ".", ".."} or re.fullmatch(r"[A-Za-z0-9_.-]+", owner) is None:
        raise LayoutError("forward-only clone reuse requires the original database owner")
    expected = "viltrox2_test_release_" + hashlib.sha256(owner.encode()).hexdigest()[:20]
    if source_database or target_database != expected or target_database != plan["database_identity"]["name"]:
        raise LayoutError("forward-only clone reuse database identity does not match")
    if _hex(env_fingerprint_before, 64, "environment fingerprint") != plan["env_sha256"]:
        raise LayoutError("forward-only clone reuse environment does not match")
    return {"database_strategy": strategy, "source_database": None, "target_database": target_database,
            "env_fingerprint_before": env_fingerprint_before, "database_owner_release_id": owner}


def forward_only_metadata(*, release_policy: str, strategy: str, source_database: str,
                          target_database: str, env_fingerprint_before: str,
                          database_owner_release_id: str, pending_migrations: str,
                          compatibility_declaration: str, evidence: Any,
                          migrations_dir: Path) -> dict[str, Any]:
    if release_policy != FORWARD_ONLY_POLICY or compatibility_declaration:
        raise LayoutError("forward-only policy cannot claim application rollback compatibility")
    names = migration_names(pending_migrations)
    plan = validate_plan(evidence, names)
    metadata = _database_metadata(strategy, source_database, target_database,
                                  env_fingerprint_before, database_owner_release_id, plan)
    return {**metadata, "release_policy": FORWARD_ONLY_POLICY, **_POLICY_FLAGS,
            "database_rollback": "forbidden-forward-only", "schema_retained_on_app_rollback": False,
            "forward_only_evidence": plan,
            "forward_only_evidence_sha256": hashlib.sha256(
                json.dumps(plan, sort_keys=True, separators=(",", ":")).encode()).hexdigest(),
            "forward_only_migrations": _migration_proof(names, migrations_dir)}


def inspect_policy(metadata: dict[str, Any]) -> dict[str, Any]:
    """Inspect an already integrity-checked seal or capture, not arbitrary live state."""
    policy = metadata.get("release_policy", DEFAULT_POLICY)
    if policy == DEFAULT_POLICY:
        if any(key in metadata for key in (*_POLICY_FLAGS, "forward_only_evidence", "forward_only_evidence_sha256", "forward_only_migrations")):
            raise LayoutError("default release has contradictory forward-only metadata")
        return {"release_policy": DEFAULT_POLICY, "forward_only": False}
    if policy != FORWARD_ONLY_POLICY:
        raise LayoutError("sealed release policy is unknown")
    if any(type(metadata.get(key)) is not type(value) or metadata[key] != value for key, value in _POLICY_FLAGS.items()):
        raise LayoutError("sealed forward-only recovery contract is incomplete")
    if metadata.get("database_rollback") != "forbidden-forward-only" or metadata.get("schema_retained_on_app_rollback") is not False:
        raise LayoutError("sealed forward-only release cannot restore an earlier database or application")
    pending = metadata.get("pending_migrations")
    if not isinstance(pending, list) or any(not isinstance(name, str) for name in pending):
        raise LayoutError("sealed forward-only migration list is invalid")
    names = migration_names(",".join(pending))
    plan = validate_plan(metadata.get("forward_only_evidence"), names)
    if metadata.get("forward_compatible_migrations") != []:
        raise LayoutError("sealed forward-only policy contains an application rollback claim")
    if metadata.get("forward_only_migrations") != [{"name": name, "sha256": MIGRATION_SHA256[name]} for name in names]:
        raise LayoutError("sealed forward-only migration proof does not match policy")
    digest = hashlib.sha256(json.dumps(plan, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    if metadata.get("forward_only_evidence_sha256") != digest or metadata.get("release_id") != plan["release_id"]:
        raise LayoutError("sealed forward-only plan binding does not match")
    if "git_sha" in metadata and metadata["git_sha"] != plan["target_git_sha"]:
        raise LayoutError("sealed forward-only target SHA does not match")
    _database_metadata(metadata.get("database_strategy"), metadata.get("source_database") or "",
                       metadata.get("target_database") or "", metadata.get("env_fingerprint_before") or "",
                       metadata.get("database_owner_release_id") or "", plan)
    return {"release_policy": policy, "forward_only": True, **_POLICY_FLAGS,
            "source_git_sha": plan["source_git_sha"], "target_git_sha": plan["target_git_sha"],
            "database_identity": plan["database_identity"], "pending_migrations": list(names)}


def assert_restore_allowed(*metadata: dict[str, Any]) -> None:
    if any(inspect_policy(item)["forward_only"] for item in metadata):
        raise LayoutError("forward-only release forbids app/database restore; keep the fence and recover forward")


def assert_activation_allowed(metadata: dict[str, Any]) -> None:
    if inspect_policy(metadata)["forward_only"]:
        raise LayoutError("forward-only seal plan is not live drain authorization; activation is forbidden")


def validate_sealed_policy(metadata: dict[str, Any], release: Path) -> dict[str, Any]:
    result = inspect_policy(metadata)
    if result["forward_only"]:
        _migration_proof(tuple(result["pending_migrations"]), release / "migrations")
    return result
