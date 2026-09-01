"""Shared immutable contracts for the local worktree freeze workflow."""

from __future__ import annotations

import hashlib
import json
import os
import re
import stat
import ctypes
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path, PurePosixPath


SCHEMA = "vkpi.local-worktree-candidate/v1"
MAX_SOURCE_FILE_BYTES = 256 * 1024 * 1024


def path_identity(path: Path) -> tuple[int, int]:
    info = path.lstat()
    return info.st_dev, info.st_ino


def physical_special_paths(root: Path) -> list[str]:
    """List unsupported physical nodes without following candidate symlinks."""

    special: list[str] = []
    pending = [root]
    while pending:
        directory = pending.pop()
        try:
            entries = sorted(os.scandir(directory), key=lambda entry: entry.name)
        except OSError as exc:
            raise FreezeError(
                f"candidate physical tree cannot be scanned: {directory}"
            ) from exc
        for entry in entries:
            path = Path(entry.path)
            try:
                info = entry.stat(follow_symlinks=False)
            except OSError as exc:
                raise FreezeError(
                    f"candidate physical node cannot be inspected: {path}"
                ) from exc
            if stat.S_ISDIR(info.st_mode):
                pending.append(path)
            elif not (stat.S_ISREG(info.st_mode) or stat.S_ISLNK(info.st_mode)):
                special.append(path.relative_to(root).as_posix())
    return sorted(special)


def remove_owned_path(path: Path, identity: tuple[int, int]) -> None:
    import shutil
    if path_identity(path) != identity or path.is_symlink():
        raise FreezeError(f"refusing cleanup after path identity changed: {path}")
    if path.is_dir():
        shutil.rmtree(path)
    else:
        path.unlink()


def precreate_owned_file(path: Path) -> tuple[int, int]:
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    os.close(descriptor)
    return path_identity(path)


def write_owned_file_exclusive(path: Path, data: bytes) -> tuple[int, int]:
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    opened = os.fstat(descriptor); identity = (opened.st_dev, opened.st_ino)
    try:
        view = memoryview(data)
        while view:
            written = os.write(descriptor, view)
            if written <= 0:
                raise FreezeError(f"exclusive write made no progress: {path}")
            view = view[written:]
        os.fsync(descriptor)
    except BaseException:
        os.close(descriptor)
        try:
            if path_identity(path) == identity and not path.is_symlink(): path.unlink()
        except FileNotFoundError: pass
        raise
    finally:
        try: os.close(descriptor)
        except OSError: pass
    if path_identity(path) != identity: raise FreezeError(f"exclusive write identity changed: {path}")
    return identity


def cleanup_owned_paths(created: dict[Path, tuple[int, int]]) -> None:
    errors = []
    for path, identity in reversed(created.items()):
        if not path.exists() and not path.is_symlink():
            continue
        try:
            remove_owned_path(path, identity)
        except Exception as exc:
            errors.append(str(exc))
    if errors:
        raise FreezeError("candidate cleanup failed closed: " + "; ".join(errors))


def rename_exclusive(source: Path, target: Path) -> None:
    """Publish without replacing a concurrently-created target (macOS)."""
    libc = ctypes.CDLL(None, use_errno=True)
    function = getattr(libc, "renameatx_np", None)
    if function is None:
        raise FreezeError("exclusive candidate publish is unavailable")
    result = function(-2, os.fsencode(source), -2, os.fsencode(target), 0x00000004)
    if result != 0:
        error = ctypes.get_errno()
        raise FreezeError(f"exclusive candidate publish failed: errno={error}")

FORBIDDEN_COMPONENTS = {
    ".git",
    ".venv",
    "venv",
    "node_modules",
    "runtime",
    "uploads",
    "frames",
    "backups",
    "creator_profiles",
    "__pycache__",
    ".pytest_cache",
    ".mypy_cache",
    ".ruff_cache",
    ".vite",
    ".claude",
    ".codegraph",
    ".codex-backups",
    ".state",
    "coverage",
}
GENERATED_ROOT_COMPONENTS = {
    "artifacts",
    "exports",
    "output",
    "outputs",
    "tmp",
}
FORBIDDEN_NAMES = {
    # Finder may rewrite this file while a candidate is being frozen.
    ".ds_store",
    ".env",
    "id_ed25519",
    "id_rsa",
    "submissions.db",
    "submissions.db-shm",
    "submissions.db-wal",
}
FORBIDDEN_SUFFIXES = (
    ".dump",
    ".key",
    ".log",
    ".p12",
    ".pem",
    ".pfx",
    ".pyc",
    ".pyo",
    ".sqlite",
    ".sqlite3",
)
HIGH_CONFIDENCE_SECRET_PATTERNS = (
    re.compile(rb"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
    re.compile(rb"(?:AKIA|ASIA)[A-Z0-9]{16}"),
    re.compile(rb"xox[baprs]-[A-Za-z0-9-]{20,}"),
    re.compile(rb"sk_live_[A-Za-z0-9]{16,}"),
)


def safe_relative_path(raw: str) -> str:
    path = PurePosixPath(raw)
    if path.is_absolute() or not path.parts:
        raise FreezeError(f"unsafe source path: {raw!r}")
    if any(part in {"", ".", ".."} for part in path.parts):
        raise FreezeError(f"unsafe source path: {raw!r}")
    return path.as_posix()


def is_excluded(path: str, *, source_phase: bool) -> bool:
    pure = PurePosixPath(safe_relative_path(path))
    lower_parts = tuple(part.lower() for part in pure.parts)
    lower_name = pure.name.lower()
    if pure.parts == (".env.example",):
        return False
    if any(part == ".env" or part.startswith(".env.") for part in lower_parts):
        return True
    if set(lower_parts) & FORBIDDEN_COMPONENTS:
        return True
    if lower_parts and lower_parts[0] in GENERATED_ROOT_COMPONENTS:
        return True
    if lower_parts[:2] == ("reports", "generated"):
        return True
    if lower_parts[:2] == ("frontend", "dist") and source_phase:
        return True
    if lower_name in FORBIDDEN_NAMES:
        return True
    return lower_name.endswith(FORBIDDEN_SUFFIXES)


class FreezeError(RuntimeError):
    """Stable fail-closed error for the local candidate workflow."""


@dataclass(frozen=True)
class FileEntry:
    path: str
    size_bytes: int
    mode: int
    sha256: str

    def payload(self) -> dict[str, object]:
        return {
            "mode": format(self.mode, "04o"),
            "path": self.path,
            "sha256": self.sha256,
            "size_bytes": self.size_bytes,
        }


@dataclass(frozen=True)
class BuildIdentity:
    git_sha: str
    git_branch: str
    build_time: str

    def __post_init__(self) -> None:
        if not re.fullmatch(r"[0-9a-f]{40}", self.git_sha):
            raise FreezeError("build identity Git SHA must be a 40-character lowercase digest")
        if not self.git_branch or any(character in self.git_branch for character in "\r\n\0"):
            raise FreezeError("build identity Git branch is invalid")
        try:
            parsed = datetime.fromisoformat(self.build_time.replace("Z", "+00:00"))
        except ValueError as exc:
            raise FreezeError("build identity time is invalid") from exc
        if parsed.tzinfo is None or parsed.utcoffset() is None:
            raise FreezeError("build identity time must include UTC")

    def vite_environment(self) -> dict[str, str]:
        return {
            "VITE_APP_BUILD_TIME": self.build_time,
            "VITE_APP_GIT_BRANCH": self.git_branch,
            "VITE_APP_GIT_SHA": self.git_sha,
        }

    def payload(self) -> dict[str, str]:
        return {
            "build_time": self.build_time,
            "git_branch": self.git_branch,
            "git_sha": self.git_sha,
        }


def _regular_tree_inventory(
    root: Path, *, max_file_bytes: int = MAX_SOURCE_FILE_BYTES
) -> list[tuple[str, str, int, str]]:
    """Inventory every node below a build directory without exclusions."""

    try:
        root_info = root.lstat()
    except OSError as exc:
        raise FreezeError("frontend reproducibility output is unavailable") from exc
    if not stat.S_ISDIR(root_info.st_mode) or root.is_symlink():
        raise FreezeError("frontend reproducibility output is unsafe")

    inventory: list[tuple[str, str, int, str]] = []
    for directory, names, files in os.walk(root, topdown=True, followlinks=False):
        base = Path(directory)
        relative_base = base.relative_to(root)
        names[:] = sorted(names)
        for name in names:
            path = base / name
            relative = (relative_base / name).as_posix()
            info = path.lstat()
            if not stat.S_ISDIR(info.st_mode) or path.is_symlink():
                raise FreezeError(
                    f"frontend reproducibility tree contains unsafe directory: {relative}"
                )
            inventory.append(("directory", relative, 0, ""))
        for name in sorted(files):
            path = base / name
            relative = (relative_base / name).as_posix()
            info = path.lstat()
            if not stat.S_ISREG(info.st_mode) or path.is_symlink():
                raise FreezeError(
                    f"frontend reproducibility tree contains unsafe file: {relative}"
                )
            if info.st_size > max_file_bytes:
                raise FreezeError(
                    f"frontend reproducibility file exceeds limit: {relative}"
                )
            payload = path.read_bytes()
            after = path.lstat()
            if (
                (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns)
                != (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns)
                or len(payload) != info.st_size
            ):
                raise FreezeError(
                    f"frontend reproducibility file changed while reading: {relative}"
                )
            inventory.append(
                ("file", relative, len(payload), hashlib.sha256(payload).hexdigest())
            )
    return sorted(inventory)


def assert_frontend_dist_reproducible(candidate: Path, rebuilt: Path) -> None:
    expected = _regular_tree_inventory(
        candidate, max_file_bytes=MAX_SOURCE_FILE_BYTES
    )
    observed = _regular_tree_inventory(
        rebuilt, max_file_bytes=MAX_SOURCE_FILE_BYTES
    )
    if observed == expected:
        return

    expected_map = {(row[0], row[1]): row for row in expected}
    observed_map = {(row[0], row[1]): row for row in observed}
    missing = sorted(set(expected_map) - set(observed_map))
    extra = sorted(set(observed_map) - set(expected_map))
    changed = sorted(
        key
        for key in set(expected_map) & set(observed_map)
        if expected_map[key] != observed_map[key]
    )
    detail = {
        "missing": [f"{kind}:{path}" for kind, path in missing[:10]],
        "extra": [f"{kind}:{path}" for kind, path in extra[:10]],
        "changed": [f"{kind}:{path}" for kind, path in changed[:10]],
    }
    raise FreezeError(
        "frontend reproducibility mismatch: "
        + json.dumps(detail, sort_keys=True, separators=(",", ":"))
    )
