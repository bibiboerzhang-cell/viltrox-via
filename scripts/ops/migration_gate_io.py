"""Descriptor-pinned local file I/O for the migration evidence auditor.

Every decision-bearing byte is read through an ``openat`` chain rooted at the
repository.  The implementation deliberately rejects symlinks, hard links,
non-owned files, writable parent directories and files that change while they
are being read.
"""

from __future__ import annotations

from dataclasses import dataclass
import errno
import hashlib
import json
import os
from pathlib import Path
import stat
from typing import Any

from .migration_gate_contract import Checks, loads_strict


class SafeFileError(ValueError):
    """A local artifact did not satisfy the descriptor-pinned file policy."""


@dataclass(frozen=True)
class Artifact:
    path: Path
    data: bytes
    sha256: str
    size: int
    mtime_ns: int
    mode: int
    uid: int
    nlink: int
    dev: int
    ino: int


def lexical_path(root: Path, path: Path) -> tuple[Path, tuple[str, ...]]:
    """Return an absolute lexical path and safe components below ``root``."""

    root_abs = Path(os.path.abspath(root))
    path_abs = Path(os.path.abspath(path if path.is_absolute() else root_abs / path))
    try:
        relative = path_abs.relative_to(root_abs)
    except ValueError as exc:
        raise SafeFileError("path_outside_repository") from exc
    parts = relative.parts
    if not parts or any(part in ("", ".", "..") for part in parts):
        raise SafeFileError("invalid_relative_path")
    return path_abs, parts


def resolve_input(root: Path, value: str | Path) -> Path:
    """Resolve a CLI path lexically without following any artifact symlink."""

    candidate = Path(value)
    absolute, _ = lexical_path(root, candidate)
    return absolute


def _directory_policy(info: os.stat_result) -> None:
    if not stat.S_ISDIR(info.st_mode):
        raise SafeFileError("parent_not_directory")
    if info.st_uid != os.geteuid():
        raise SafeFileError("parent_not_owned_by_current_user")
    if info.st_mode & 0o022:
        raise SafeFileError("parent_is_group_or_world_writable")
    if info.st_nlink < 1:
        raise SafeFileError("invalid_parent_link_count")


def _file_policy(
    info: os.stat_result,
    *,
    max_bytes: int,
    private: bool,
    allow_empty: bool,
) -> None:
    if not stat.S_ISREG(info.st_mode):
        raise SafeFileError("not_regular_file")
    if info.st_uid != os.geteuid():
        raise SafeFileError("file_not_owned_by_current_user")
    if info.st_nlink != 1:
        raise SafeFileError("file_link_count_must_equal_one")
    if info.st_mode & 0o022:
        raise SafeFileError("file_is_group_or_world_writable")
    if private and info.st_mode & 0o077:
        raise SafeFileError("private_permissions_required")
    if info.st_size < 0 or info.st_size > max_bytes:
        raise SafeFileError("file_size_out_of_range")
    if not allow_empty and info.st_size == 0:
        raise SafeFileError("empty_file")


def _same_snapshot(before: os.stat_result, after: os.stat_result) -> bool:
    fields = (
        "st_dev",
        "st_ino",
        "st_mode",
        "st_uid",
        "st_gid",
        "st_nlink",
        "st_size",
        "st_mtime_ns",
        "st_ctime_ns",
    )
    return all(getattr(before, name) == getattr(after, name) for name in fields)


def read_artifact(
    root: Path,
    path: Path,
    *,
    max_bytes: int,
    private: bool = False,
    allow_empty: bool = False,
    retain_bytes: bool = True,
) -> Artifact:
    """Read one immutable snapshot using only pinned directory/file FDs."""

    absolute, parts = lexical_path(root, path)
    nofollow = getattr(os, "O_NOFOLLOW", 0)
    directory = getattr(os, "O_DIRECTORY", 0)
    cloexec = getattr(os, "O_CLOEXEC", 0)
    directory_fds: list[int] = []
    file_fd: int | None = None
    try:
        current_fd = os.open(
            Path(os.path.abspath(root)), os.O_RDONLY | directory | nofollow | cloexec
        )
        directory_fds.append(current_fd)
        root_before = os.fstat(current_fd)
        _directory_policy(root_before)
        pinned_directories: list[
            tuple[int, os.stat_result, int | None, str | None]
        ] = [
            (current_fd, root_before, None, None)
        ]

        for component in parts[:-1]:
            next_fd = os.open(
                component,
                os.O_RDONLY | directory | nofollow | cloexec,
                dir_fd=current_fd,
            )
            directory_fds.append(next_fd)
            info = os.fstat(next_fd)
            _directory_policy(info)
            pinned_directories.append((next_fd, info, current_fd, component))
            current_fd = next_fd

        file_fd = os.open(
            parts[-1], os.O_RDONLY | nofollow | cloexec, dir_fd=current_fd
        )
        before = os.fstat(file_fd)
        _file_policy(
            before,
            max_bytes=max_bytes,
            private=private,
            allow_empty=allow_empty,
        )
        chunks: list[bytes] = []
        digest = hashlib.sha256()
        total = 0
        while True:
            block = os.read(file_fd, min(1024 * 1024, max_bytes + 1 - total))
            if not block:
                break
            digest.update(block)
            if retain_bytes:
                chunks.append(block)
            total += len(block)
            if total > max_bytes:
                raise SafeFileError("file_size_out_of_range")
        data = b"".join(chunks)
        after = os.fstat(file_fd)
        if not _same_snapshot(before, after) or total != before.st_size:
            raise SafeFileError("file_changed_while_reading")

        path_after = os.stat(parts[-1], dir_fd=current_fd, follow_symlinks=False)
        if not _same_snapshot(before, path_after):
            raise SafeFileError("path_rebound_while_reading")
        root_path_after = os.stat(
            Path(os.path.abspath(root)), follow_symlinks=False
        )
        if not _same_snapshot(root_before, root_path_after):
            raise SafeFileError("repository_root_rebound_while_reading")
        for fd, initial, parent_fd, component in pinned_directories:
            if not _same_snapshot(initial, os.fstat(fd)):
                raise SafeFileError("parent_changed_while_reading")
            if parent_fd is not None and component is not None:
                directory_path_after = os.stat(
                    component, dir_fd=parent_fd, follow_symlinks=False
                )
                if not _same_snapshot(initial, directory_path_after):
                    raise SafeFileError("parent_path_rebound_while_reading")

        return Artifact(
            path=absolute,
            data=data,
            sha256=digest.hexdigest(),
            size=total,
            mtime_ns=before.st_mtime_ns,
            mode=stat.S_IMODE(before.st_mode),
            uid=before.st_uid,
            nlink=before.st_nlink,
            dev=before.st_dev,
            ino=before.st_ino,
        )
    except OSError as exc:
        reason = "filesystem_error"
        if exc.errno in (errno.ELOOP,):
            reason = "symlink_forbidden"
        elif exc.errno in (errno.ENOENT, errno.ENOTDIR):
            reason = "file_not_found"
        raise SafeFileError(reason) from exc
    finally:
        if file_fd is not None:
            os.close(file_fd)
        for fd in reversed(directory_fds):
            os.close(fd)


def load_json_artifact(
    root: Path,
    path: Path,
    *,
    max_bytes: int,
    private: bool,
    prefix: str,
    checks: Checks,
) -> tuple[dict[str, Any] | None, Artifact | None]:
    """Securely read and strictly decode one JSON object without echoing data."""

    try:
        artifact = read_artifact(
            root, path, max_bytes=max_bytes, private=private, allow_empty=False
        )
    except (OSError, SafeFileError) as exc:
        checks.add(prefix + ".file", False, str(exc))
        return None, None
    checks.add(prefix + ".file", True, "descriptor-pinned regular file")
    checks.add(prefix + ".owner", artifact.uid == os.geteuid(), "current owner")
    checks.add(prefix + ".nlink", artifact.nlink == 1, "exact nlink=1")
    checks.add(
        prefix + ".private_permissions",
        not private or artifact.mode & 0o077 == 0,
        "private artifact mode required",
    )
    checks.add(prefix + ".size", artifact.size <= max_bytes, f"bytes={artifact.size}")
    try:
        decoded = loads_strict(artifact.data)
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
        checks.add(prefix + ".strict_json", False, type(exc).__name__)
        return None, artifact
    checks.add(prefix + ".strict_json", True, "duplicate/non-finite values rejected")
    if not isinstance(decoded, dict):
        checks.add(prefix + ".json_object", False, "top-level object required")
        return None, artifact
    checks.add(prefix + ".json_object", True, "top-level object")
    return decoded, artifact


def write_report(path: Path, payload: dict[str, Any]) -> None:
    """Create/replace a private report without following an existing symlink."""

    parent = path.parent
    parent.mkdir(parents=True, exist_ok=True)
    if path.exists() and path.is_symlink():
        raise SafeFileError("output_symlink_forbidden")
    temporary = parent / f".{path.name}.tmp.{os.getpid()}"
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
    fd = os.open(temporary, flags, 0o600)
    try:
        data = json.dumps(
            payload,
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
            allow_nan=False,
        ).encode("utf-8") + b"\n"
        offset = 0
        while offset < len(data):
            offset += os.write(fd, data[offset:])
        os.fsync(fd)
    finally:
        os.close(fd)
    try:
        os.replace(temporary, path)
    finally:
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass
