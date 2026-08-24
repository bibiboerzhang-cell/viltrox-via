#!/usr/bin/env python3
"""Build and switch immutable V-KPI application releases.

This helper never mutates systemd or touches the database.  It owns the
on-host filesystem transaction and may read systemd state so the caller can
restore optional units exactly after a failed release.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import stat
import subprocess
import sys
import tempfile
from pathlib import Path

if __package__:
    from .atomic_release_cli import build_parser as _build_parser
    from .atomic_release_integrity import (
        RELEASE_MANIFEST_NAME,
        make_release_immutable as _make_release_immutable,
        payload_fingerprint as _payload_fingerprint,
        verify_sealed_release as _verify_sealed_release,
    )
    from .atomic_release_units import (
        LayoutError,
        inspect_unit_state,
        is_exact_dev_null_symlink as _is_exact_dev_null_symlink,
        optional_unit_names as _optional_unit_names,
        parse_optional_unit_states as _parse_optional_unit_states,
        unit_names as _unit_names,
        unit_state_token as _unit_state_token,
        validate_unit_state as _validate_unit_state,
    )
    from .atomic_release_shared import (
        CONTROLLER_DIRECTORY_MODE,
        ENV_FINGERPRINT_RE,
        RELEASE_ID_RE,
        RELEASE_CONTROLLER_NAME,
        RELEASE_SHARED_ALIASES,
        REQUIRED_RELEASE_PATHS,
        ROLLBACK_METADATA_DIGEST_NAME,
        ROLLBACK_METADATA_SCHEMA,
        SHARED_DIRECTORIES,
        SHARED_NESTED_DIRECTORIES,
        SHARED_OPTIONAL,
        SHARED_REQUIRED,
        STAGING_CLONE_RE,
        WORKER_READONLY_DIRECTORIES,
        WORKER_WRITABLE_DIRECTORIES,
        _capture_metadata,
        _capture_rollback_file_sources,
        _create_secure_directory,
        _database_release_metadata,
        _fsync_directory,
        _id,
        _inside_releases,
        _read_regular_single_link,
        _read_secure_controller_file,
        _release_clone_name,
        _restore_file_atomically,
        _restore_rollback_files,
        _secure_directory,
        _validate_rollback_file_targets,
        _validated_capture_restore,
        _validated_rollback_file_restores,
        _write_new_capture,
        _write_rollback_file_captures,
    )
    from .atomic_release_worker_preflight import (
        _account,
        _identity_groups,
        _mode_allows,
        _require_identity_access,
        _require_write_blocked,
        _tool_path,
        _write_canary,
        worker_layout_preflight,
        worker_runtime_preflight,
    )
else:
    from atomic_release_cli import build_parser as _build_parser
    from atomic_release_integrity import (
        RELEASE_MANIFEST_NAME,
        make_release_immutable as _make_release_immutable,
        payload_fingerprint as _payload_fingerprint,
        verify_sealed_release as _verify_sealed_release,
    )
    from atomic_release_units import (
        LayoutError,
        inspect_unit_state,
        is_exact_dev_null_symlink as _is_exact_dev_null_symlink,
        optional_unit_names as _optional_unit_names,
        parse_optional_unit_states as _parse_optional_unit_states,
        unit_names as _unit_names,
        unit_state_token as _unit_state_token,
        validate_unit_state as _validate_unit_state,
    )
    from atomic_release_shared import (
        CONTROLLER_DIRECTORY_MODE,
        ENV_FINGERPRINT_RE,
        RELEASE_ID_RE,
        RELEASE_CONTROLLER_NAME,
        RELEASE_SHARED_ALIASES,
        REQUIRED_RELEASE_PATHS,
        ROLLBACK_METADATA_DIGEST_NAME,
        ROLLBACK_METADATA_SCHEMA,
        SHARED_DIRECTORIES,
        SHARED_NESTED_DIRECTORIES,
        SHARED_OPTIONAL,
        SHARED_REQUIRED,
        STAGING_CLONE_RE,
        WORKER_READONLY_DIRECTORIES,
        WORKER_WRITABLE_DIRECTORIES,
        _capture_metadata,
        _capture_rollback_file_sources,
        _create_secure_directory,
        _database_release_metadata,
        _fsync_directory,
        _id,
        _inside_releases,
        _read_regular_single_link,
        _read_secure_controller_file,
        _release_clone_name,
        _restore_file_atomically,
        _restore_rollback_files,
        _secure_directory,
        _validate_rollback_file_targets,
        _validated_capture_restore,
        _validated_rollback_file_restores,
        _write_new_capture,
        _write_rollback_file_captures,
    )
    from atomic_release_worker_preflight import (
        _account,
        _identity_groups,
        _mode_allows,
        _require_identity_access,
        _require_write_blocked,
        _tool_path,
        _write_canary,
        worker_layout_preflight,
        worker_runtime_preflight,
    )


def _existing_link_target(root: Path, name: str) -> Path | None:
    link = root / name
    if not link.exists() and not link.is_symlink():
        return None
    if not link.is_symlink():
        raise LayoutError(f"refusing non-symlink release pointer: {link}")
    return _inside_releases(root, link)


def _atomic_link(root: Path, name: str, target: Path) -> None:
    target = _inside_releases(root, target)
    link = root / name
    if (link.exists() or link.is_symlink()) and not link.is_symlink():
        raise LayoutError(f"refusing non-symlink release pointer: {link}")
    temporary = root / f".{name}.tmp-{os.getpid()}"
    if temporary.exists() or temporary.is_symlink():
        temporary.unlink()
    relative = os.path.relpath(target, root)
    temporary.symlink_to(relative, target_is_directory=True)
    os.replace(temporary, link)


def _remove_pointer(root: Path, name: str) -> None:
    link = root / name
    if not link.exists() and not link.is_symlink():
        return
    if not link.is_symlink():
        raise LayoutError(f"refusing non-symlink release pointer: {link}")
    link.unlink()


def _controller_rollbacks_root(root: Path, *, create: bool) -> Path:
    root_info = root.lstat()
    if not stat.S_ISDIR(root_info.st_mode):
        raise LayoutError(f"application root must be a real directory: {root}")
    if root_info.st_uid != os.geteuid() or stat.S_IMODE(root_info.st_mode) & 0o022:
        raise LayoutError(
            f"application root must be controller-owned and not group/world writable: {root}"
        )
    controller = root / RELEASE_CONTROLLER_NAME
    if create and not controller.exists() and not controller.is_symlink():
        _create_secure_directory(controller, label="release controller directory")
    _secure_directory(controller, label="release controller directory")
    rollbacks = controller / "rollbacks"
    if create and not rollbacks.exists() and not rollbacks.is_symlink():
        _create_secure_directory(rollbacks, label="release rollback directory")
    return _secure_directory(rollbacks, label="release rollback directory")


def _rollback_capture_dir(root: Path, release_id: str, *, create: bool) -> Path:
    rollbacks = _controller_rollbacks_root(root, create=create)
    rollback_dir = rollbacks / _id(release_id)
    if create:
        return _create_secure_directory(
            rollback_dir,
            label="release rollback capture directory",
        )
    return _secure_directory(
        rollback_dir,
        label="release rollback capture directory",
    )


def _load_rollback_metadata(root: Path, release_id: str) -> tuple[Path, dict[str, object]]:
    rollback_dir = _rollback_capture_dir(root, release_id, create=False)
    digest_payload = _read_secure_controller_file(
        rollback_dir / ROLLBACK_METADATA_DIGEST_NAME,
        label="rollback metadata digest",
    )
    try:
        expected_digest = digest_payload.decode("ascii").strip()
    except UnicodeDecodeError as exc:
        raise LayoutError("rollback metadata digest is invalid") from exc
    if not ENV_FINGERPRINT_RE.fullmatch(expected_digest):
        raise LayoutError("rollback metadata digest is invalid")
    metadata_payload = _read_secure_controller_file(
        rollback_dir / "metadata.json",
        label="rollback metadata",
    )
    if hashlib.sha256(metadata_payload).hexdigest() != expected_digest:
        raise LayoutError("rollback metadata hash mismatch")
    try:
        metadata = json.loads(metadata_payload.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise LayoutError("rollback metadata payload is invalid") from exc
    if not isinstance(metadata, dict):
        raise LayoutError("rollback metadata payload is invalid")
    return rollback_dir, metadata


def _ensure_shared_root(root: Path) -> None:
    for name in SHARED_REQUIRED:
        if not (root / name).exists():
            raise LayoutError(f"required shared runtime path is missing: {root / name}")
    for name in SHARED_DIRECTORIES:
        path = root / name
        if not path.is_dir() or path.is_symlink():
            raise LayoutError(
                f"required shared runtime directory is missing or unsafe: {path}; "
                "run worker-layout-preflight before seal"
            )
    for name in SHARED_NESTED_DIRECTORIES:
        path = root / name
        if not path.is_dir() or path.is_symlink():
            raise LayoutError(
                f"required shared runtime directory is missing or unsafe: {path}; "
                "run worker-layout-preflight before seal"
            )


def _shared_links(root: Path, release: Path) -> None:
    _ensure_shared_root(root)
    for name in (*SHARED_REQUIRED, *SHARED_DIRECTORIES, *SHARED_OPTIONAL):
        source = root / name
        destination = release / name
        if not source.exists() and not source.is_symlink():
            continue
        if destination.is_symlink():
            if destination.resolve() != source.resolve():
                raise LayoutError(f"shared link points to the wrong target: {destination}")
            continue
        if destination.exists():
            raise LayoutError(f"release contains mutable shared data: {destination}")
        destination.symlink_to(os.path.relpath(source, release), target_is_directory=source.is_dir())


def _manifest(
    root: Path,
    release: Path,
    *,
    release_id: str,
    git_sha: str,
    pending_migrations: str,
    compatibility_declaration: str,
    database_strategy: str = "in-place",
    source_database: str = "",
    target_database: str = "",
    env_fingerprint_before: str = "",
    database_owner_release_id: str = "",
    immutable_owner_uid: int,
    immutable_owner_gid: int,
) -> None:
    database_metadata = _database_release_metadata(
        strategy=database_strategy,
        source_database=source_database,
        target_database=target_database,
        env_fingerprint_before=env_fingerprint_before,
        pending_migrations=pending_migrations,
        compatibility_declaration=compatibility_declaration,
        database_owner_release_id=database_owner_release_id,
    )
    payload_sha256, payload_entry_count = _payload_fingerprint(
        root,
        release,
        shared_aliases=RELEASE_SHARED_ALIASES,
    )
    payload = {
        "schema": 2,
        "release_id": release_id,
        "git_sha": git_sha,
        "payload_sha256": payload_sha256,
        "payload_entry_count": payload_entry_count,
        "immutable_owner_uid": immutable_owner_uid,
        "immutable_owner_gid": immutable_owner_gid,
        "pending_migrations": [value for value in pending_migrations.split(",") if value],
        "forward_compatible_migrations": [
            value for value in compatibility_declaration.split(",") if value
        ],
        **database_metadata,
    }
    manifest_path = release / RELEASE_MANIFEST_NAME
    if manifest_path.exists() or manifest_path.is_symlink():
        raise LayoutError("refusing to reseal an existing release manifest")
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=".vkpi-release.", suffix=".tmp", dir=release
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(json.dumps(payload, sort_keys=True) + "\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, manifest_path)
    finally:
        temporary.unlink(missing_ok=True)


def seal(args: argparse.Namespace) -> None:
    root = Path(args.root).resolve()
    release_id = _id(args.release_id)
    release = root / "releases" / release_id
    _inside_releases(root, release)
    missing = [name for name in REQUIRED_RELEASE_PATHS if not (release / name).exists()]
    if missing:
        raise LayoutError("release payload is incomplete: " + ", ".join(missing))
    build_sha = (release / "BUILD_GIT_SHA").read_text(encoding="utf-8").strip()
    if build_sha != args.git_sha:
        raise LayoutError("BUILD_GIT_SHA does not match the sealed release")
    _database_release_metadata(
        strategy=args.database_strategy,
        source_database=args.source_database,
        target_database=args.target_database,
        env_fingerprint_before=args.env_fingerprint_before,
        database_owner_release_id=args.database_owner_release_id,
        pending_migrations=args.pending_migrations,
        compatibility_declaration=args.compatibility_declaration,
    )
    _shared_links(root, release)
    owner_uid = os.geteuid() if args.owner_uid is None else args.owner_uid
    owner_gid = os.getegid() if args.owner_gid is None else args.owner_gid
    _manifest(
        root,
        release,
        release_id=release_id,
        git_sha=args.git_sha,
        pending_migrations=args.pending_migrations,
        compatibility_declaration=args.compatibility_declaration,
        database_strategy=args.database_strategy,
        source_database=args.source_database,
        target_database=args.target_database,
        env_fingerprint_before=args.env_fingerprint_before,
        database_owner_release_id=args.database_owner_release_id,
        immutable_owner_uid=owner_uid,
        immutable_owner_gid=owner_gid,
    )
    _make_release_immutable(release, owner_uid=owner_uid, owner_gid=owner_gid)
    _verify_sealed_release(
        root,
        release,
        shared_aliases=RELEASE_SHARED_ALIASES,
        expected_owner_uid=owner_uid,
        expected_owner_gid=owner_gid,
    )


def verify_seal(args: argparse.Namespace) -> None:
    root = Path(args.root).resolve()
    release = _inside_releases(root, root / "releases" / _id(args.release_id))
    _verify_sealed_release(
        root,
        release,
        shared_aliases=RELEASE_SHARED_ALIASES,
        expected_owner_uid=args.expected_owner_uid,
        expected_owner_gid=args.expected_owner_gid,
    )


def _release_build_sha(release: Path, *, label: str) -> str:
    payload, _info = _read_regular_single_link(
        release / "BUILD_GIT_SHA",
        label=f"{label} BUILD_GIT_SHA",
    )
    try:
        value = payload.decode("ascii").strip()
    except UnicodeDecodeError as exc:
        raise LayoutError(f"{label} BUILD_GIT_SHA is invalid") from exc
    if len(value) != 40 or any(
        character not in "0123456789abcdef" for character in value
    ):
        raise LayoutError(f"{label} BUILD_GIT_SHA is invalid")
    return value


def _rollback_activation_anchor(
    root: Path,
    *,
    new_release: Path,
    observed_current: Path | None,
    rollback_anchor_release_id: str,
) -> Path | None:
    """Return the sealed release that automatic restore may activate.

    The normal path proves the observed current release is still immutable.
    Rescue mode deliberately leaves a contaminated current tree untouched and
    instead binds rollback to a separately sealed release with the exact same
    Git identity as the running pre-deploy release.
    """

    if observed_current is None:
        if rollback_anchor_release_id:
            raise LayoutError(
                "rollback anchor requires an observed atomic current release"
            )
        return None

    if not rollback_anchor_release_id:
        _verify_sealed_release(
            root,
            observed_current,
            shared_aliases=RELEASE_SHARED_ALIASES,
        )
        return observed_current

    rollback_anchor = _inside_releases(
        root,
        root / "releases" / _id(rollback_anchor_release_id),
    )
    if rollback_anchor == new_release:
        raise LayoutError("rollback anchor must not be the release being prepared")
    anchor_manifest = _verify_sealed_release(
        root,
        rollback_anchor,
        shared_aliases=RELEASE_SHARED_ALIASES,
    )
    observed_sha = _release_build_sha(
        observed_current,
        label="observed current release",
    )
    if anchor_manifest.get("git_sha") != observed_sha:
        raise LayoutError(
            "rollback anchor Git SHA does not match observed current BUILD_GIT_SHA"
        )
    return rollback_anchor


def _snapshot_legacy(root: Path, release_id: str) -> Path:
    legacy = root / "releases" / f"legacy-before-{release_id}"
    if legacy.exists():
        raise LayoutError(f"legacy snapshot already exists: {legacy}")
    legacy.mkdir(parents=True)
    command = [
        "rsync",
        "-a",
        "--delete",
        "--exclude", "releases/",
        "--exclude", "current",
        "--exclude", "previous",
        "--exclude", f"{RELEASE_CONTROLLER_NAME}/",
        "--exclude", ".env",
        "--exclude", ".env.*",
        "--exclude", ".venv/",
        "--exclude", "runtime/",
        "--exclude", "uploads/",
        "--exclude", "frames/",
        "--exclude", "creator_profiles/",
        "--exclude", "backups/",
        "--exclude", "submissions.db",
        "--exclude", RELEASE_MANIFEST_NAME,
        "--exclude", "node_modules/",
        # These are reviewed legacy host/developer workspaces, not application
        # release state.  Keep all other unexpected special files fail-closed
        # so the immutable payload verifier can still reject them.
        "--exclude", ".codegraph/",
        "--exclude", "video-production-platform/",
        f"{root}/",
        f"{legacy}/",
    ]
    subprocess.run(command, check=True)
    _shared_links(root, legacy)
    _manifest(
        root,
        legacy,
        release_id=legacy.name,
        git_sha=(legacy / "BUILD_GIT_SHA").read_text(encoding="utf-8").strip()
        if (legacy / "BUILD_GIT_SHA").exists()
        else "legacy-unknown",
        pending_migrations="",
        compatibility_declaration="",
        immutable_owner_uid=os.geteuid(),
        immutable_owner_gid=os.getegid(),
    )
    _make_release_immutable(legacy, owner_uid=os.geteuid(), owner_gid=os.getegid())
    _verify_sealed_release(root, legacy, shared_aliases=RELEASE_SHARED_ALIASES)
    return _inside_releases(root, legacy)


def prepare(args: argparse.Namespace) -> None:
    root = Path(args.root).resolve()
    release_id = _id(args.release_id)
    new_release = _inside_releases(root, root / "releases" / release_id)
    _verify_sealed_release(root, new_release, shared_aliases=RELEASE_SHARED_ALIASES)
    original_previous = _existing_link_target(root, "previous")
    observed_current = _existing_link_target(root, "current")
    activation_anchor = _rollback_activation_anchor(
        root,
        new_release=new_release,
        observed_current=observed_current,
        rollback_anchor_release_id=args.rollback_anchor_release_id,
    )
    unit_dir = Path(args.unit_dir).resolve()
    units = _unit_names(args.unit_name)
    optional_units = _optional_unit_names(args.optional_unit_name)
    overlap = sorted(set(units).intersection(optional_units))
    if overlap:
        raise LayoutError(
            "systemd units cannot be both required and optional: " + ", ".join(overlap)
        )
    env_file = root / ".env"
    env_payload, env_info = _read_regular_single_link(
        env_file,
        label="shared environment file",
    )
    rollback_file_sources = _capture_rollback_file_sources(args.rollback_file)
    optional_unit_states = _parse_optional_unit_states(
        optional_units,
        args.optional_unit_state,
        unit_dir,
    )
    unit_source_captures: dict[str, tuple[bytes, os.stat_result]] = {}
    for name in units:
        unit_source_captures[name] = _read_regular_single_link(
            unit_dir / name,
            label=f"required installed unit {name}",
        )
    for name, state in optional_unit_states.items():
        if state["present"] and not state["masked"]:
            unit_source_captures[name] = _read_regular_single_link(
                unit_dir / name,
                label=f"optional installed unit {name}",
            )
    database_metadata = _database_release_metadata(
        strategy=args.database_strategy,
        source_database=args.source_database,
        target_database=args.target_database,
        env_fingerprint_before=args.env_fingerprint_before,
        database_owner_release_id=args.database_owner_release_id,
        pending_migrations=args.pending_migrations,
        compatibility_declaration=args.compatibility_declaration,
    )

    rollbacks_root = _controller_rollbacks_root(root, create=True)
    rollback_candidate = rollbacks_root / release_id
    if rollback_candidate.exists() or rollback_candidate.is_symlink():
        raise LayoutError(f"rollback capture already exists: {rollback_candidate}")

    legacy_snapshot: Path | None = None
    if activation_anchor is None:
        legacy_snapshot = _snapshot_legacy(root, release_id)
        activation_anchor = legacy_snapshot

    rollback_dir = _rollback_capture_dir(root, release_id, create=True)
    units_capture_dir = _create_secure_directory(
        rollback_dir / "units",
        label="release unit capture directory",
    )
    _write_new_capture(rollback_dir / ".env", env_payload)
    rollback_files = _write_rollback_file_captures(
        rollback_dir,
        rollback_file_sources,
    )
    unit_files: dict[str, dict[str, int | str]] = {}
    for name, (payload, info) in unit_source_captures.items():
        _write_new_capture(units_capture_dir / name, payload)
        unit_files[name] = _capture_metadata(payload, info)
    metadata = {
        "schema": ROLLBACK_METADATA_SCHEMA,
        "release_id": release_id,
        "new_release": str(new_release),
        # active_release/prior_previous_release remain as audit aliases for
        # readers that do not perform restore.  Schema 3 restore exclusively
        # uses the explicit original pointer fields below.
        "active_release": str(activation_anchor),
        "prior_previous_release": (
            str(original_previous) if original_previous else None
        ),
        "original_current_release": (
            str(activation_anchor) if observed_current else None
        ),
        "original_previous_release": (
            str(original_previous) if original_previous else None
        ),
        "observed_predeploy_current_release": (
            str(observed_current) if observed_current else None
        ),
        "legacy_snapshot_release": str(legacy_snapshot) if legacy_snapshot else None,
        "environment_file": _capture_metadata(env_payload, env_info),
        "rollback_files_required": bool(rollback_file_sources),
        "rollback_files": rollback_files,
        "unit_names": units,
        "unit_files": unit_files,
        "optional_unit_states": optional_unit_states,
        "database_rollback": {
            "staging-clone": "restore-captured-env-to-original-database",
            "reuse-active-clone": "restore-captured-env-on-reused-database",
        }.get(args.database_strategy, "never-automatic"),
        "schema_retained_on_app_rollback": bool(args.database_strategy == "reuse-active-clone" and args.pending_migrations),
        "pending_migrations": args.pending_migrations.split(",") if args.pending_migrations else [],
        "forward_compatible_migrations": args.compatibility_declaration.split(",") if args.compatibility_declaration else [],
        **database_metadata,
    }
    metadata_payload = (json.dumps(metadata, sort_keys=True) + "\n").encode("utf-8")
    _write_new_capture(rollback_dir / "metadata.json", metadata_payload)
    _write_new_capture(
        rollback_dir / ROLLBACK_METADATA_DIGEST_NAME,
        (hashlib.sha256(metadata_payload).hexdigest() + "\n").encode("ascii"),
    )
    _fsync_directory(units_capture_dir)
    _fsync_directory(rollback_dir)
    _fsync_directory(rollbacks_root)
    _atomic_link(root, "previous", activation_anchor)


def activate(args: argparse.Namespace) -> None:
    root = Path(args.root).resolve()
    release = _inside_releases(root, root / "releases" / _id(args.release_id))
    _verify_sealed_release(root, release, shared_aliases=RELEASE_SHARED_ALIASES)
    if _existing_link_target(root, "previous") is None:
        raise LayoutError("previous release pointer is required before activation")
    _atomic_link(root, "current", release)


def rollback_unit_state(args: argparse.Namespace) -> None:
    root = Path(args.root).resolve()
    release_id = _id(args.release_id)
    unit_name = _optional_unit_names([args.unit_name])[0]
    _rollback_dir, metadata = _load_rollback_metadata(root, release_id)
    if metadata.get("release_id") != release_id:
        raise LayoutError("rollback metadata release id mismatch")
    states = metadata.get("optional_unit_states")
    state = states.get(unit_name) if isinstance(states, dict) else None
    if not isinstance(state, dict):
        raise LayoutError("optional systemd unit was not captured for rollback")
    sys.stdout.write(_unit_state_token(_validate_unit_state(unit_name, state)) + "\n")


def restore(args: argparse.Namespace) -> None:
    root = Path(args.root).resolve()
    release_id = _id(args.release_id)
    rollback_dir, metadata = _load_rollback_metadata(root, release_id)
    if metadata.get("release_id") != release_id:
        raise LayoutError("rollback metadata release id mismatch")
    schema = metadata.get("schema")
    if schema == 2:
        raise LayoutError(
            "rollback metadata schema 2 cannot prove original pointer presence or "
            "environment ownership; automatic restore is refused"
        )
    if schema != ROLLBACK_METADATA_SCHEMA:
        raise LayoutError(f"unsupported rollback metadata schema: {schema!r}")

    def captured_pointer(name: str) -> Path | None:
        if name not in metadata:
            raise LayoutError(f"rollback pointer metadata is missing: {name}")
        raw = metadata[name]
        if raw is None:
            return None
        if not isinstance(raw, str) or not raw:
            raise LayoutError(f"rollback pointer metadata is invalid: {name}")
        return _inside_releases(root, Path(raw))

    original_current = captured_pointer("original_current_release")
    original_previous = captured_pointer("original_previous_release")
    legacy_snapshot = captured_pointer("legacy_snapshot_release")
    if original_current is None and legacy_snapshot is None:
        raise LayoutError("legacy rollback metadata is missing its sealed audit snapshot")
    if original_current is not None and legacy_snapshot is not None:
        raise LayoutError("rollback metadata declares an unexpected legacy audit snapshot")
    units = _unit_names(list(metadata.get("unit_names") or []))
    optional_states_raw = metadata.get("optional_unit_states") or {}
    if not isinstance(optional_states_raw, dict):
        raise LayoutError("optional systemd unit rollback state is invalid")
    optional_units = _optional_unit_names(list(optional_states_raw))
    optional_unit_states: dict[str, dict[str, bool]] = {}
    for name in optional_units:
        state = optional_states_raw.get(name)
        if not isinstance(state, dict):
            raise LayoutError(f"optional systemd unit rollback state is invalid: {name}")
        optional_unit_states[name] = _validate_unit_state(name, state)
    if set(units).intersection(optional_units):
        raise LayoutError("required and optional rollback unit sets overlap")
    unit_dir = Path(args.unit_dir).resolve()
    units_capture_dir = _secure_directory(
        rollback_dir / "units",
        label="release unit capture directory",
    )
    expected_unit_captures = set(units) | {
        name
        for name, state in optional_unit_states.items()
        if state["present"] and not state["masked"]
    }
    unit_files_raw = metadata.get("unit_files")
    if not isinstance(unit_files_raw, dict) or set(unit_files_raw) != expected_unit_captures:
        raise LayoutError("rollback unit capture metadata is incomplete or inconsistent")
    unit_restores: dict[str, tuple[bytes, int, int, int]] = {}
    for name in sorted(expected_unit_captures):
        unit_restores[name] = _validated_capture_restore(
            units_capture_dir / name,
            unit_files_raw.get(name),
            label=f"rollback unit capture {name}",
        )
    env_restore = _validated_capture_restore(
        rollback_dir / ".env",
        metadata.get("environment_file"),
        label="rollback environment capture",
    )
    rollback_file_restores = _validated_rollback_file_restores(
        rollback_dir,
        metadata,
    )
    env_target = root / ".env"
    if env_target.exists() or env_target.is_symlink():
        if not stat.S_ISREG(env_target.lstat().st_mode):
            raise LayoutError(
                f"shared environment target must be a regular file: {env_target}"
            )
    # Validate the presently installed pointers before any rollback mutation.
    _existing_link_target(root, "current")
    _existing_link_target(root, "previous")
    for name in (*units, *optional_units):
        installed = unit_dir / name
        if installed.is_symlink():
            state = optional_unit_states.get(name)
            if state and state["masked"] and _is_exact_dev_null_symlink(installed):
                continue
            raise LayoutError(f"refusing symlink installed unit target: {installed}")
        if installed.exists() and not stat.S_ISREG(installed.lstat().st_mode):
            raise LayoutError(f"installed unit target must be a regular file: {installed}")
    _validate_rollback_file_targets(rollback_file_restores)

    if original_current is not None:
        _atomic_link(root, "current", original_current)
    else:
        _remove_pointer(root, "current")
    if original_previous is not None:
        _atomic_link(root, "previous", original_previous)
    else:
        _remove_pointer(root, "previous")
    payload, uid, gid, mode = env_restore
    _restore_file_atomically(
        env_target,
        payload=payload,
        uid=uid,
        gid=gid,
        mode=mode,
        label="shared environment",
    )
    _restore_rollback_files(rollback_file_restores)
    for name in units:
        payload, uid, gid, mode = unit_restores[name]
        _restore_file_atomically(
            unit_dir / name,
            payload=payload,
            uid=uid,
            gid=gid,
            mode=mode,
            label=f"required installed unit {name}",
        )
    for name, state in optional_unit_states.items():
        installed = unit_dir / name
        if state["present"] and not state["masked"]:
            payload, uid, gid, mode = unit_restores[name]
            _restore_file_atomically(
                installed,
                payload=payload,
                uid=uid,
                gid=gid,
                mode=mode,
                label=f"optional installed unit {name}",
            )
        elif installed.exists() or installed.is_symlink():
            installed.unlink()
    _fsync_directory(unit_dir)


def parser() -> argparse.ArgumentParser:
    return _build_parser(
        {
            "seal": seal,
            "verify_seal": verify_seal,
            "worker_layout_preflight": worker_layout_preflight,
            "worker_runtime_preflight": worker_runtime_preflight,
            "prepare": prepare,
            "activate": activate,
            "rollback_unit_state": rollback_unit_state,
            "inspect_unit_state": inspect_unit_state,
            "restore": restore,
        }
    )


def main() -> int:
    try:
        args = parser().parse_args()
        args.action(args)
    except (LayoutError, OSError, subprocess.CalledProcessError, ValueError, KeyError) as exc:
        sys.stderr.write(f"atomic release layout failed: {exc}\n")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
