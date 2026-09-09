"""Atomic seal writer and file-bound forward-only plan adapters."""
from __future__ import annotations

import hashlib
import json
import os
import stat
import tempfile
from pathlib import Path
from typing import Any

if __package__:
    from .atomic_release_integrity import RELEASE_MANIFEST_NAME, payload_fingerprint
    from .atomic_release_shared import RELEASE_SHARED_ALIASES, _database_release_metadata, _read_regular_single_link
    from .atomic_release_units import LayoutError
    from .release_forward_policy import DEFAULT_POLICY, FORWARD_ONLY_POLICY, inspect_policy, migration_names, validate_plan
else:
    from atomic_release_integrity import RELEASE_MANIFEST_NAME, payload_fingerprint
    from atomic_release_shared import RELEASE_SHARED_ALIASES, _database_release_metadata, _read_regular_single_link
    from atomic_release_units import LayoutError
    from release_forward_policy import DEFAULT_POLICY, FORWARD_ONLY_POLICY, inspect_policy, migration_names, validate_plan


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result = {}
    for key, value in pairs:
        if key in result:
            raise LayoutError("forward-only plan contains duplicate JSON keys")
        result[key] = value
    return result


def policy_arguments(args: Any, *, git_sha: str) -> dict[str, Any]:
    policy = getattr(args, "release_policy", DEFAULT_POLICY)
    evidence_file = getattr(args, "forward_only_evidence_file", "")
    if policy == DEFAULT_POLICY:
        if evidence_file:
            raise LayoutError("default release cannot supply forward-only plan evidence")
        return {}
    if policy != FORWARD_ONLY_POLICY or not evidence_file:
        raise LayoutError("explicit forward-only policy requires a protected plan evidence file")
    payload, info = _read_regular_single_link(Path(evidence_file), label="forward-only plan", max_bytes=16384)
    if info.st_uid != os.geteuid() or stat.S_IMODE(info.st_mode) not in {0o400, 0o600}:
        raise LayoutError("forward-only plan must be owned by its controller with mode 0400 or 0600")
    try:
        evidence = json.loads(payload.decode("utf-8"), object_pairs_hook=_unique_object)
    except (ValueError, UnicodeDecodeError) as exc:
        raise LayoutError("forward-only plan JSON is invalid") from exc
    plan = validate_plan(evidence, migration_names(args.pending_migrations))
    if plan["release_id"] != args.release_id or plan["target_git_sha"] != git_sha:
        raise LayoutError("forward-only plan is bound to another release or target SHA")
    env_payload, _info = _read_regular_single_link(Path(args.root) / ".env", label="shared environment file")
    if plan["env_sha256"] != hashlib.sha256(env_payload).hexdigest():
        raise LayoutError("forward-only plan shared environment hash does not match")
    return {"release_policy": policy, "forward_only_evidence": plan}


def validate_prepare_policy(manifest: dict[str, Any], metadata: dict[str, Any], source: Path) -> None:
    sealed_policy = inspect_policy(manifest)
    if not sealed_policy["forward_only"] and metadata.get("release_policy", DEFAULT_POLICY) == DEFAULT_POLICY:
        return
    if not sealed_policy["forward_only"] or any(manifest.get(key) != value for key, value in metadata.items()):
        raise LayoutError("prepare policy or evidence differs from the sealed release")
    payload, _info = _read_regular_single_link(source / "BUILD_GIT_SHA", label="predeploy BUILD_GIT_SHA")
    if payload.decode("ascii").strip() != sealed_policy["source_git_sha"]:
        raise LayoutError("forward-only plan source SHA differs from the observed predeploy release")


def read_restore_policy(release: Path) -> dict[str, Any]:
    """Read protected policy fields without requiring the failed payload to be healthy."""
    payload, info = _read_regular_single_link(release / RELEASE_MANIFEST_NAME,
                                            label="sealed release policy", max_bytes=1024 * 1024)
    for path in (release.parent, release):
        directory = path.lstat()
        if (not stat.S_ISDIR(directory.st_mode) or directory.st_uid != os.geteuid()
                or stat.S_IMODE(directory.st_mode) & 0o022):
            raise LayoutError("sealed policy parent must remain controller-owned and protected")
    if info.st_uid != os.geteuid() or stat.S_IMODE(info.st_mode) not in {0o444, 0o555}:
        raise LayoutError("sealed policy file must remain controller-owned and immutable")
    try:
        manifest = json.loads(payload.decode("utf-8"), object_pairs_hook=_unique_object)
    except (ValueError, UnicodeDecodeError) as exc:
        raise LayoutError("sealed release policy JSON is invalid") from exc
    if (not isinstance(manifest, dict) or manifest.get("schema") != 2
            or manifest.get("release_id") != release.name
            or type(manifest.get("immutable_owner_uid")) is not int
            or type(manifest.get("immutable_owner_gid")) is not int
            or (manifest["immutable_owner_uid"], manifest["immutable_owner_gid"]) != (info.st_uid, info.st_gid)):
        raise LayoutError("sealed release policy identity is invalid")
    inspect_policy(manifest)
    return manifest


def write_manifest(
    root: Path, release: Path, *, release_id: str, git_sha: str,
    pending_migrations: str, compatibility_declaration: str,
    database_strategy: str = "in-place", source_database: str = "", target_database: str = "",
    env_fingerprint_before: str = "", database_owner_release_id: str = "",
    immutable_owner_uid: int, immutable_owner_gid: int,
    release_policy: str = DEFAULT_POLICY, forward_only_evidence: Any = None,
) -> None:
    database_metadata = _database_release_metadata(
        strategy=database_strategy, source_database=source_database, target_database=target_database,
        env_fingerprint_before=env_fingerprint_before, pending_migrations=pending_migrations,
        compatibility_declaration=compatibility_declaration, database_owner_release_id=database_owner_release_id,
        release_policy=release_policy, forward_only_evidence=forward_only_evidence,
        migrations_dir=release / "migrations",
    )
    payload_sha256, payload_entry_count = payload_fingerprint(root, release, shared_aliases=RELEASE_SHARED_ALIASES)
    payload = {
        "schema": 2, "release_id": release_id, "git_sha": git_sha,
        "payload_sha256": payload_sha256, "payload_entry_count": payload_entry_count,
        "immutable_owner_uid": immutable_owner_uid, "immutable_owner_gid": immutable_owner_gid,
        "pending_migrations": [value for value in pending_migrations.split(",") if value],
        "forward_compatible_migrations": [value for value in compatibility_declaration.split(",") if value],
        **database_metadata,
    }
    manifest_path = release / RELEASE_MANIFEST_NAME
    if manifest_path.exists() or manifest_path.is_symlink():
        raise LayoutError("refusing to reseal an existing release manifest")
    descriptor, temporary_name = tempfile.mkstemp(prefix=".vkpi-release.", suffix=".tmp", dir=release)
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(json.dumps(payload, sort_keys=True) + "\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, manifest_path)
    finally:
        temporary.unlink(missing_ok=True)
