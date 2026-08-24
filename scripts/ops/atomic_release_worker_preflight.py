"""Worker identity, filesystem, sandbox, and tool preflights for releases."""

from __future__ import annotations

import argparse
import grp
import os
import pwd
import shutil
import stat
import subprocess
import tempfile
from pathlib import Path

if __package__:
    from .atomic_release_shared import (
        REQUIRED_RELEASE_PATHS,
        SHARED_DIRECTORIES,
        SHARED_NESTED_DIRECTORIES,
        WORKER_READONLY_DIRECTORIES,
        WORKER_WRITABLE_DIRECTORIES,
        LayoutError,
        _id,
        _inside_releases,
    )
else:
    from atomic_release_shared import (
        REQUIRED_RELEASE_PATHS,
        SHARED_DIRECTORIES,
        SHARED_NESTED_DIRECTORIES,
        WORKER_READONLY_DIRECTORIES,
        WORKER_WRITABLE_DIRECTORIES,
        LayoutError,
        _id,
        _inside_releases,
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


def _tool_path(_root: Path, name: str) -> str:
    resolved = shutil.which(name)
    if not resolved or not os.access(resolved, os.X_OK):
        raise LayoutError(f"required worker tool is missing or not executable: {name}")
    return resolved


def _venv_tool_path(root: Path, name: str) -> str:
    candidate = root / ".venv" / "bin" / name
    if not candidate.is_file() or not os.access(candidate, os.R_OK | os.X_OK):
        raise LayoutError(f"required venv worker tool is missing or not executable: {name}")
    return str(candidate)


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
        ("yt_dlp module", (str(python_bin), "-m", "yt_dlp", "--version")),
        ("yt-dlp console", (_venv_tool_path(root, "yt-dlp"), "--version")),
        ("ffmpeg", (_tool_path(root, "ffmpeg"), "-version")),
        ("ffprobe", (_tool_path(root, "ffprobe"), "-version")),
    )
    for label, command in commands:
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
            raise LayoutError(f"worker tool execution preflight failed: {label}") from exc
