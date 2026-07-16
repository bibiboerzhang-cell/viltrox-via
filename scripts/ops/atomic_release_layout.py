#!/usr/bin/env python3
"""Build and switch immutable V-KPI application releases.

This helper never mutates systemd or touches the database.  It owns the
on-host filesystem transaction and may read systemd state so the caller can
restore optional units exactly after a failed release.
"""

from __future__ import annotations

import argparse
import grp
import hashlib
import json
import os
import pwd
import re
import shutil
import stat
import subprocess
import sys
import tempfile
from pathlib import Path

if __package__:
    from .atomic_release_integrity import (
        RELEASE_MANIFEST_NAME,
        make_release_immutable as _make_release_immutable,
        payload_fingerprint as _payload_fingerprint,
        verify_sealed_release as _verify_sealed_release,
    )
    from .atomic_release_units import (
        LayoutError,
        inspect_unit_state,
        optional_unit_names as _optional_unit_names,
        parse_optional_unit_states as _parse_optional_unit_states,
        unit_names as _unit_names,
        unit_state_token as _unit_state_token,
        validate_unit_state as _validate_unit_state,
    )
else:
    from atomic_release_integrity import (
        RELEASE_MANIFEST_NAME,
        make_release_immutable as _make_release_immutable,
        payload_fingerprint as _payload_fingerprint,
        verify_sealed_release as _verify_sealed_release,
    )
    from atomic_release_units import (
        LayoutError,
        inspect_unit_state,
        optional_unit_names as _optional_unit_names,
        parse_optional_unit_states as _parse_optional_unit_states,
        unit_names as _unit_names,
        unit_state_token as _unit_state_token,
        validate_unit_state as _validate_unit_state,
    )


RELEASE_ID_RE = re.compile(r"^[A-Za-z0-9_.-]+$")
ENV_FINGERPRINT_RE = re.compile(r"^[0-9a-f]{64}$")
STAGING_CLONE_RE = re.compile(r"^viltrox2_test_release_[0-9a-f]{20}$")
REQUIRED_RELEASE_PATHS = (
    "backend",
    "frontend/dist",
    "migrations",
    "scripts/start_admin.sh",
    "scripts/ops/systemd/viltrox-2.0-test.service",
    "scripts/ops/systemd/vkpi-worker-interactive.service",
    "scripts/ops/systemd/vkpi-worker-bulk@.service",
    "scripts/ops/systemd/vkpi-redis-worker.service",
    "BUILD_GIT_SHA",
    "BUILD_GIT_BRANCH",
    "BUILD_TIME",
)
SHARED_DIRECTORIES = ("runtime", "uploads", "frames", "creator_profiles", "backups")
SHARED_NESTED_DIRECTORIES = ("runtime/job-results",)
WORKER_WRITABLE_DIRECTORIES = ("uploads",)
WORKER_READONLY_DIRECTORIES = ("runtime", "frames", "creator_profiles")
SHARED_REQUIRED = (".env", ".venv")
SHARED_OPTIONAL = (".env.production", "submissions.db")
RELEASE_SHARED_ALIASES = {
    *SHARED_REQUIRED,
    *SHARED_DIRECTORIES,
    *SHARED_OPTIONAL,
}


def _release_clone_name(release_id: str) -> str:
    release_id = _id(release_id)
    return "viltrox2_test_release_" + hashlib.sha256(
        release_id.encode("utf-8")
    ).hexdigest()[:20]


def _database_release_metadata(
    *,
    strategy: str,
    source_database: str,
    target_database: str,
    env_fingerprint_before: str,
    pending_migrations: str,
    compatibility_declaration: str,
    database_owner_release_id: str = "",
) -> dict[str, str | None]:
    if strategy == "in-place":
        if (
            source_database
            or target_database
            or env_fingerprint_before
            or database_owner_release_id
        ):
            raise LayoutError("in-place releases must not declare staging clone metadata")
        return {
            "database_strategy": "in-place",
            "source_database": None,
            "target_database": None,
            "env_fingerprint_before": None,
            "database_owner_release_id": None,
        }
    if strategy == "reuse-active-clone":
        if source_database:
            raise LayoutError("clone-reuse releases must not declare a new source database")
        if not database_owner_release_id:
            raise LayoutError("clone-reuse releases require the database owner release id")
        if not STAGING_CLONE_RE.fullmatch(target_database):
            raise LayoutError("clone-reuse target database is not release-specific")
        if _release_clone_name(database_owner_release_id) != target_database:
            raise LayoutError("clone-reuse owner release does not own the target database")
        if not ENV_FINGERPRINT_RE.fullmatch(env_fingerprint_before):
            raise LayoutError("clone-reuse env fingerprint must be a SHA-256 digest")
        if pending_migrations:
            raise LayoutError("clone-reuse strategy is app-only and forbids migrations")
        if compatibility_declaration:
            raise LayoutError("clone-reuse strategy must not claim forward compatibility")
        return {
            "database_strategy": "reuse-active-clone",
            "source_database": None,
            "target_database": target_database,
            "env_fingerprint_before": env_fingerprint_before,
            "database_owner_release_id": database_owner_release_id,
        }
    if strategy != "staging-clone":
        raise LayoutError(f"unsupported database release strategy: {strategy!r}")
    if database_owner_release_id:
        raise LayoutError("new staging clones must not inherit another database owner")
    if source_database != "viltrox2_test" and not STAGING_CLONE_RE.fullmatch(
        source_database
    ):
        raise LayoutError(
            "staging clone source must be the legacy base or a proven prior release clone"
        )
    if not STAGING_CLONE_RE.fullmatch(target_database):
        raise LayoutError("staging clone target database is not release-specific")
    if not ENV_FINGERPRINT_RE.fullmatch(env_fingerprint_before):
        raise LayoutError("staging clone env fingerprint must be a SHA-256 digest")
    if not pending_migrations:
        raise LayoutError("staging clone strategy requires pending migrations")
    if compatibility_declaration:
        raise LayoutError(
            "staging clone strategy must not claim in-place forward compatibility"
        )
    return {
        "database_strategy": "staging-clone",
        "source_database": source_database,
        "target_database": target_database,
        "env_fingerprint_before": env_fingerprint_before,
        "database_owner_release_id": None,
    }


def _id(value: str) -> str:
    if not RELEASE_ID_RE.fullmatch(value) or value in {".", ".."}:
        raise LayoutError(f"invalid release id: {value!r}")
    return value


def _inside_releases(root: Path, target: Path) -> Path:
    releases = (root / "releases").resolve()
    resolved = target.resolve(strict=True)
    if resolved == releases or releases not in resolved.parents:
        raise LayoutError(f"release target escapes {releases}: {resolved}")
    if not resolved.is_dir():
        raise LayoutError(f"release target is not a directory: {resolved}")
    return resolved


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


def _account(user_name: str, group_name: str) -> tuple[pwd.struct_passwd, grp.struct_group]:
    try:
        user = pwd.getpwnam(user_name)
    except KeyError as exc:
        raise LayoutError(f"worker account does not exist: {user_name}") from exc
    try:
        group = grp.getgrnam(group_name)
    except KeyError as exc:
        raise LayoutError(f"worker group does not exist: {group_name}") from exc
    try:
        groups = set(os.getgrouplist(user.pw_name, user.pw_gid))
    except OSError:
        groups = {user.pw_gid}
    if group.gr_gid not in groups:
        raise LayoutError(
            f"worker account {user_name} is not a member of required group {group_name}"
        )
    return user, group


def _identity_groups(user: pwd.struct_passwd, group: grp.struct_group) -> set[int]:
    try:
        groups = set(os.getgrouplist(user.pw_name, user.pw_gid))
    except OSError:
        groups = {user.pw_gid}
    groups.add(group.gr_gid)
    return groups


def _mode_allows(path: Path, *, uid: int, gids: set[int], mask: int) -> bool:
    info = path.stat()
    mode = stat.S_IMODE(info.st_mode)
    if info.st_uid == uid:
        effective = (mode >> 6) & 0b111
    elif info.st_gid in gids:
        effective = (mode >> 3) & 0b111
    else:
        effective = mode & 0b111
    return effective & mask == mask


def _require_identity_access(
    path: Path,
    *,
    user: pwd.struct_passwd,
    group: grp.struct_group,
    mask: int,
    label: str,
) -> None:
    groups = _identity_groups(user, group)
    if not _mode_allows(path, uid=user.pw_uid, gids=groups, mask=mask):
        raise LayoutError(f"{label} is not accessible to {user.pw_name}: {path}")


def worker_layout_preflight(args: argparse.Namespace) -> None:
    """Provision only absent shared roots, then prove the reviewed Unix ownership.

    Existing data is never recursively chowned or chmodded.  Historical files
    created by root therefore remain an explicit operator migration instead of
    being silently reinterpreted during a release.
    """

    root = Path(args.root).resolve()
    release = _inside_releases(root, root / "releases" / _id(args.release_id))
    user, group = _account(args.app_user, args.app_group)
    if not root.is_dir() or root.is_symlink():
        raise LayoutError(f"application root must be a real directory: {root}")

    for name in SHARED_DIRECTORIES:
        path = root / name
        if not path.exists() and args.provision_missing:
            path.mkdir(mode=0o750)
            os.chown(path, user.pw_uid, group.gr_gid)
        if not path.is_dir() or path.is_symlink():
            raise LayoutError(f"shared runtime path must be a real directory: {path}")
        info = path.stat()
        if (info.st_uid, info.st_gid) != (user.pw_uid, group.gr_gid):
            raise LayoutError(
                f"shared runtime ownership mismatch: {path}; expected "
                f"{args.app_user}:{args.app_group}; refusing recursive chown"
            )
        _require_identity_access(
            path,
            user=user,
            group=group,
            mask=0b111,
            label="shared runtime directory",
        )

    for name in SHARED_NESTED_DIRECTORIES:
        path = root / name
        if not path.exists() and args.provision_missing:
            path.mkdir(mode=0o750, parents=True)
            os.chown(path, user.pw_uid, group.gr_gid)
        if not path.is_dir() or path.is_symlink():
            raise LayoutError(f"shared runtime path must be a real directory: {path}")
        info = path.stat()
        if (info.st_uid, info.st_gid) != (user.pw_uid, group.gr_gid):
            raise LayoutError(
                f"shared runtime ownership mismatch: {path}; expected "
                f"{args.app_user}:{args.app_group}; refusing recursive chown"
            )
        _require_identity_access(
            path,
            user=user,
            group=group,
            mask=0b111,
            label="shared runtime directory",
        )

    env_paths = [root / ".env"]
    if (root / ".env.production").exists():
        env_paths.append(root / ".env.production")
    for path in env_paths:
        if not path.is_file() or path.is_symlink():
            raise LayoutError(f"shared environment path must be a regular file: {path}")
        _require_identity_access(
            path,
            user=user,
            group=group,
            mask=0b100,
            label="shared environment file",
        )
        if _mode_allows(
            path,
            uid=user.pw_uid,
            gids=_identity_groups(user, group),
            mask=0b010,
        ):
            raise LayoutError(
                f"shared environment file must not be writable by {args.app_user}: {path}"
            )

    python_bin = root / ".venv" / "bin" / "python"
    if not python_bin.exists():
        raise LayoutError(f"shared Python runtime is missing: {python_bin}")
    _require_identity_access(
        python_bin,
        user=user,
        group=group,
        mask=0b101,
        label="shared Python runtime",
    )
    for required in REQUIRED_RELEASE_PATHS:
        path = release / required
        if not path.exists():
            raise LayoutError(f"release payload is incomplete: {required}")


def _write_canary(directory: Path, *, label: str) -> None:
    canary: Path | None = None
    try:
        fd, raw_path = tempfile.mkstemp(prefix=".vkpi-worker-preflight-", dir=directory)
        canary = Path(raw_path)
        with os.fdopen(fd, "wb") as handle:
            handle.write(b"vkpi-worker-permission-canary\n")
            handle.flush()
            os.fsync(handle.fileno())
        if canary.read_bytes() != b"vkpi-worker-permission-canary\n":
            raise LayoutError(f"{label} canary readback mismatch: {directory}")
    except OSError as exc:
        raise LayoutError(f"{label} is not writable by the worker: {directory}: {exc}") from exc
    finally:
        if canary is not None:
            canary.unlink(missing_ok=True)


def _require_write_blocked(directory: Path, *, label: str) -> None:
    canary: Path | None = None
    try:
        fd, raw_path = tempfile.mkstemp(prefix=".vkpi-worker-forbidden-", dir=directory)
        os.close(fd)
        canary = Path(raw_path)
    except OSError:
        return
    finally:
        if canary is not None:
            canary.unlink(missing_ok=True)
    raise LayoutError(f"{label} unexpectedly remains writable inside the worker sandbox")


def _tool_path(root: Path, name: str) -> str:
    if name == "yt-dlp":
        sibling = root / ".venv" / "bin" / "yt-dlp"
        if sibling.is_file() and os.access(sibling, os.X_OK):
            return str(sibling)
    resolved = shutil.which(name)
    if not resolved or not os.access(resolved, os.X_OK):
        raise LayoutError(f"required worker tool is missing or not executable: {name}")
    return resolved


def worker_runtime_preflight(args: argparse.Namespace) -> None:
    """Run as the service user and exercise the exact read/execute/write surface."""

    root = Path(args.root).resolve()
    release = _inside_releases(root, Path(args.release_path))
    user, group = _account(args.app_user, args.app_group)
    if os.geteuid() != user.pw_uid or os.getegid() != group.gr_gid:
        raise LayoutError(
            "worker runtime preflight must run as the exact reviewed account/group: "
            f"{args.app_user}:{args.app_group}"
        )

    for env_name in (".env", ".env.production"):
        path = root / env_name
        if not path.exists() and env_name == ".env.production":
            continue
        if not path.is_file() or not os.access(path, os.R_OK):
            raise LayoutError(f"worker cannot read required environment file: {path}")
        if os.access(path, os.W_OK):
            raise LayoutError(f"worker must not be able to write environment file: {path}")

    python_bin = root / ".venv" / "bin" / "python"
    if not os.access(python_bin, os.R_OK | os.X_OK):
        raise LayoutError(f"worker cannot execute shared Python runtime: {python_bin}")
    worker_module = release / "backend" / "app" / "workers" / "apify_jobs_worker.py"
    if not worker_module.is_file() or not os.access(worker_module, os.R_OK):
        raise LayoutError(f"worker source is not readable: {worker_module}")

    for name in WORKER_WRITABLE_DIRECTORIES:
        shared = root / name
        alias = release / name
        if not alias.is_symlink() or alias.resolve() != shared.resolve():
            raise LayoutError(f"release shared path is not bound to the reviewed root: {alias}")
        _write_canary(shared, label=name)

    for name in WORKER_READONLY_DIRECTORIES:
        shared = root / name
        alias = release / name
        if not alias.is_symlink() or alias.resolve() != shared.resolve():
            raise LayoutError(f"release shared path is not bound to the reviewed root: {alias}")
        if not os.access(shared, os.R_OK | os.X_OK):
            raise LayoutError(f"worker cannot read required shared path: {shared}")
        if args.require_sandbox_readonly:
            _require_write_blocked(shared, label=name)

    if args.job_results_dir:
        configured = Path(args.job_results_dir)
        expected = root / SHARED_NESTED_DIRECTORIES[0]
        env_value = os.environ.get("VKPI_JOB_RESULTS_DIR", "").strip()
        if not configured.is_absolute() or configured.resolve() != expected.resolve():
            raise LayoutError(
                f"worker result directory is not the reviewed shared path: {configured}"
            )
        env_path = Path(env_value) if env_value else None
        if (
            env_path is None
            or not env_path.is_absolute()
            or env_path.resolve() != expected.resolve()
        ):
            raise LayoutError(
                "VKPI_JOB_RESULTS_DIR is not bound to the reviewed shared path"
            )
        release_alias = release / SHARED_NESTED_DIRECTORIES[0]
        if release_alias.resolve() != expected.resolve():
            raise LayoutError(
                f"release result path is not bound to the reviewed root: {release_alias}"
            )
        _write_canary(expected, label="job results")

    temp_root = Path(os.environ.get("TMPDIR") or "/tmp")
    temp_root.mkdir(parents=True, exist_ok=True)
    _write_canary(temp_root, label="private temporary directory")
    for variable in ("HOME", "XDG_CACHE_HOME"):
        raw = os.environ.get(variable, "").strip()
        if not raw:
            raise LayoutError(f"worker runtime must set {variable}")
        cache_dir = Path(raw)
        cache_dir.mkdir(parents=True, exist_ok=True)
        _write_canary(cache_dir, label=variable)

    commands = (
        (_tool_path(root, "yt-dlp"), "--version"),
        (_tool_path(root, "ffmpeg"), "-version"),
        (_tool_path(root, "ffprobe"), "-version"),
    )
    for command in commands:
        try:
            subprocess.run(
                command,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=15,
                check=True,
            )
        except (OSError, subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
            raise LayoutError(f"worker tool execution preflight failed: {command[0]}") from exc


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
    unit_dir = Path(args.unit_dir).resolve()
    units = _unit_names(args.unit_name)
    optional_units = _optional_unit_names(args.optional_unit_name)
    overlap = sorted(set(units).intersection(optional_units))
    if overlap:
        raise LayoutError(
            "systemd units cannot be both required and optional: " + ", ".join(overlap)
        )
    env_file = root / ".env"
    if not env_file.is_file():
        raise LayoutError(f"shared environment file is missing: {env_file}")
    missing_units = [name for name in units if not (unit_dir / name).is_file()]
    if missing_units:
        raise LayoutError("reviewed installed units are missing: " + ", ".join(missing_units))
    optional_unit_states = _parse_optional_unit_states(
        optional_units,
        args.optional_unit_state,
        unit_dir,
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

    prior_previous = _existing_link_target(root, "previous")
    active = _existing_link_target(root, "current")
    if active is None:
        active = _snapshot_legacy(root, release_id)

    rollback_dir = root / "runtime" / "ops" / "deploy-rollbacks" / release_id
    if rollback_dir.exists():
        raise LayoutError(f"rollback capture already exists: {rollback_dir}")
    (rollback_dir / "units").mkdir(parents=True)
    shutil.copy2(env_file, rollback_dir / ".env")
    for name in units:
        shutil.copy2(unit_dir / name, rollback_dir / "units" / name)
    for name, state in optional_unit_states.items():
        if state["present"] and not state["masked"]:
            shutil.copy2(unit_dir / name, rollback_dir / "units" / name)
    metadata = {
        "schema": 2,
        "release_id": release_id,
        "new_release": str(new_release),
        "active_release": str(active),
        "prior_previous_release": str(prior_previous) if prior_previous else None,
        "unit_names": units,
        "optional_unit_states": optional_unit_states,
        "database_rollback": {
            "staging-clone": "restore-captured-env-to-original-database",
            "reuse-active-clone": "restore-captured-env-on-reused-database",
        }.get(args.database_strategy, "never-automatic"),
        "pending_migrations": args.pending_migrations.split(",") if args.pending_migrations else [],
        "forward_compatible_migrations": (
            args.compatibility_declaration.split(",") if args.compatibility_declaration else []
        ),
        **database_metadata,
    }
    (rollback_dir / "metadata.json").write_text(
        json.dumps(metadata, sort_keys=True) + "\n", encoding="utf-8"
    )
    _atomic_link(root, "previous", active)


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
    metadata_path = (
        root
        / "runtime"
        / "ops"
        / "deploy-rollbacks"
        / release_id
        / "metadata.json"
    )
    if not metadata_path.is_file():
        raise LayoutError(f"rollback metadata is missing: {metadata_path}")
    metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
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
    rollback_dir = root / "runtime" / "ops" / "deploy-rollbacks" / release_id
    metadata_path = rollback_dir / "metadata.json"
    if not metadata_path.is_file():
        raise LayoutError(f"rollback metadata is missing: {metadata_path}")
    metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    if metadata.get("release_id") != release_id:
        raise LayoutError("rollback metadata release id mismatch")
    active = _inside_releases(root, Path(metadata["active_release"]))
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
    backups = [rollback_dir / "units" / name for name in units]
    optional_backups = {
        name: rollback_dir / "units" / name
        for name, state in optional_unit_states.items()
        if state["present"] and not state["masked"]
    }
    if (
        not (rollback_dir / ".env").is_file()
        or any(not path.is_file() for path in backups)
        or any(not path.is_file() for path in optional_backups.values())
    ):
        raise LayoutError("rollback capture is incomplete")
    for name, state in optional_unit_states.items():
        installed = unit_dir / name
        if (not state["present"] or state["masked"]) and installed.exists() and not (
            installed.is_file() or installed.is_symlink()
        ):
            raise LayoutError(f"refusing to remove unsafe optional unit path: {installed}")

    _atomic_link(root, "current", active)
    prior_previous = metadata.get("prior_previous_release")
    if prior_previous:
        _atomic_link(root, "previous", Path(prior_previous))
    else:
        _remove_pointer(root, "previous")
    shutil.copy2(rollback_dir / ".env", root / ".env")
    for name, backup in zip(units, backups, strict=True):
        shutil.copy2(backup, unit_dir / name)
    for name, state in optional_unit_states.items():
        installed = unit_dir / name
        if state["present"] and not state["masked"]:
            shutil.copy2(optional_backups[name], installed)
        elif installed.exists() or installed.is_symlink():
            installed.unlink()


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser()
    subparsers = result.add_subparsers(dest="command", required=True)
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--root", required=True)
    common.add_argument("--release-id", required=True)

    seal_parser = subparsers.add_parser("seal", parents=[common])
    seal_parser.add_argument("--git-sha", required=True)
    seal_parser.add_argument("--pending-migrations", default="")
    seal_parser.add_argument("--compatibility-declaration", default="")
    seal_parser.add_argument("--database-strategy", default="in-place")
    seal_parser.add_argument("--source-database", default="")
    seal_parser.add_argument("--target-database", default="")
    seal_parser.add_argument("--env-fingerprint-before", default="")
    seal_parser.add_argument("--database-owner-release-id", default="")
    seal_parser.add_argument("--owner-uid", type=int)
    seal_parser.add_argument("--owner-gid", type=int)
    seal_parser.set_defaults(action=seal)

    verify_seal_parser = subparsers.add_parser("verify-seal", parents=[common])
    verify_seal_parser.add_argument("--expected-owner-uid", type=int)
    verify_seal_parser.add_argument("--expected-owner-gid", type=int)
    verify_seal_parser.set_defaults(action=verify_seal)

    layout_parser = subparsers.add_parser("worker-layout-preflight", parents=[common])
    layout_parser.add_argument("--app-user", required=True)
    layout_parser.add_argument("--app-group", required=True)
    layout_parser.add_argument("--provision-missing", action="store_true")
    layout_parser.set_defaults(action=worker_layout_preflight)

    runtime_parser = subparsers.add_parser("worker-runtime-preflight")
    runtime_parser.add_argument("--root", required=True)
    runtime_parser.add_argument("--release-path", required=True)
    runtime_parser.add_argument("--app-user", required=True)
    runtime_parser.add_argument("--app-group", required=True)
    runtime_parser.add_argument("--job-results-dir", default="")
    runtime_parser.add_argument("--require-sandbox-readonly", action="store_true")
    runtime_parser.set_defaults(action=worker_runtime_preflight)

    prepare_parser = subparsers.add_parser("prepare", parents=[common])
    prepare_parser.add_argument("--unit-dir", required=True)
    prepare_parser.add_argument("--unit-name", action="append", default=[])
    prepare_parser.add_argument("--optional-unit-name", action="append", default=[])
    prepare_parser.add_argument("--optional-unit-state", action="append", default=[])
    prepare_parser.add_argument("--pending-migrations", default="")
    prepare_parser.add_argument("--compatibility-declaration", default="")
    prepare_parser.add_argument("--database-strategy", default="in-place")
    prepare_parser.add_argument("--source-database", default="")
    prepare_parser.add_argument("--target-database", default="")
    prepare_parser.add_argument("--env-fingerprint-before", default="")
    prepare_parser.add_argument("--database-owner-release-id", default="")
    prepare_parser.set_defaults(action=prepare)

    activate_parser = subparsers.add_parser("activate", parents=[common])
    activate_parser.set_defaults(action=activate)

    state_parser = subparsers.add_parser("rollback-unit-state", parents=[common])
    state_parser.add_argument("--unit-name", required=True)
    state_parser.set_defaults(action=rollback_unit_state)

    inspect_parser = subparsers.add_parser("inspect-unit-state")
    inspect_parser.add_argument("--unit-dir", required=True)
    inspect_parser.add_argument("--unit-name", required=True)
    inspect_parser.add_argument("--systemctl-bin", default="/usr/bin/systemctl")
    inspect_parser.set_defaults(action=inspect_unit_state)

    restore_parser = subparsers.add_parser("restore", parents=[common])
    restore_parser.add_argument("--unit-dir", required=True)
    restore_parser.set_defaults(action=restore)
    return result


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
