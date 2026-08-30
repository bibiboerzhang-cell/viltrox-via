"""Trusted, side-effect-minimized snapshot primitives for health collection."""
from __future__ import annotations

import hashlib
import io
import os
import stat
import subprocess
import tokenize
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Iterator, Sequence


TRUSTED_GIT_CANDIDATES = (Path("/usr/bin/git"), Path("/opt/homebrew/bin/git"), Path("/usr/local/bin/git"))


class SnapshotError(ValueError):
    """Raised when a trusted snapshot primitive cannot complete."""


@dataclass(frozen=True)
class SourceFile:
    path: Path
    relative_path: str
    category: str
    physical_lines: int
    byte_count: int
    sha256: str
    content: bytes


@dataclass(frozen=True)
class SourceSnapshot:
    files: tuple[SourceFile, ...]
    content_sha256: str
    byte_count: int
    physical_lines: int
    symlink_sources: tuple[str, ...]
    read_errors: tuple[str, ...]

    @property
    def complete(self) -> bool:
        return not self.symlink_sources and not self.read_errors

    def identity(self) -> dict[str, object]:
        return {
            "file_count": len(self.files),
            "byte_count": self.byte_count,
            "physical_lines": self.physical_lines,
            "content_sha256": self.content_sha256,
            "symlink_sources": list(self.symlink_sources),
            "read_errors": list(self.read_errors),
        }


def _is_test_source(path: Path, directory_names: set[str], filename_markers: Sequence[str]) -> bool:
    if any(part.lower() in directory_names for part in path.parts):
        return True
    name = path.name.lower()
    stem = path.stem.lower()
    return name.startswith("test_") or stem.endswith("_test") or any(marker in name for marker in filename_markers)


def _category(relative_path: str) -> str:
    if relative_path.startswith("backend/app/"):
        return "backend"
    if relative_path.startswith("frontend/src/"):
        return "style" if relative_path.endswith(".css") else "frontend"
    return "script" if relative_path.startswith("scripts/") else "source"


def iter_source_paths(
    root: Path,
    roots: Sequence[str],
    suffixes: set[str],
    *,
    skip_parts: set[str],
    test_directory_names: set[str],
    test_filename_markers: Sequence[str],
) -> Iterator[Path]:
    seen: set[str] = set()
    for root_text in roots:
        scan_root = root / root_text
        if not scan_root.exists():
            continue
        candidates: Iterable[Path] = (scan_root,) if scan_root.is_file() else scan_root.rglob("*")
        for path in candidates:
            try:
                relative = path.relative_to(root)
            except ValueError:
                continue
            if path.suffix not in suffixes or any(part in skip_parts for part in relative.parts):
                continue
            if _is_test_source(relative, test_directory_names, test_filename_markers):
                continue
            relative_text = relative.as_posix()
            if relative_text in seen:
                continue
            seen.add(relative_text)
            yield path


def source_content_sha256(files: Sequence[SourceFile], symlinks: Sequence[str], errors: Sequence[str]) -> str:
    digest = hashlib.sha256()
    for item in files:
        digest.update(item.relative_path.encode("utf-8"))
        digest.update(b"\0")
        digest.update(str(item.byte_count).encode("ascii"))
        digest.update(b"\0")
        digest.update(item.sha256.encode("ascii"))
        digest.update(b"\n")
    for label, rows in ((b"symlink", symlinks), (b"error", errors)):
        for row in rows:
            digest.update(label + b"\0" + row.encode("utf-8") + b"\n")
    return digest.hexdigest()


def _stable_regular_bytes(path: Path) -> bytes:
    """Read one regular file through a non-following descriptor.

    The descriptor metadata is checked before and after the read, and the path
    must still name the opened inode afterwards.  A racing writer or path swap
    therefore makes the outer snapshot incomplete instead of mixing versions.
    """
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(path, flags)
    try:
        before = os.fstat(descriptor)
        if not stat.S_ISREG(before.st_mode):
            raise SnapshotError("not_regular")
        chunks: list[bytes] = []
        while True:
            chunk = os.read(descriptor, 1024 * 1024)
            if not chunk:
                break
            chunks.append(chunk)
        after = os.fstat(descriptor)
        path_after = os.stat(path, follow_symlinks=False)
    finally:
        os.close(descriptor)
    stable_fields = ("st_dev", "st_ino", "st_mode", "st_size", "st_mtime_ns", "st_ctime_ns")
    if any(getattr(before, field) != getattr(after, field) for field in stable_fields):
        raise SnapshotError("changed_during_read")
    if (path_after.st_dev, path_after.st_ino) != (before.st_dev, before.st_ino):
        raise SnapshotError("path_changed_during_read")
    content = b"".join(chunks)
    if len(content) != before.st_size:
        raise SnapshotError("short_or_growing_read")
    return content


def snapshot_sources(
    root: Path,
    roots: Sequence[str],
    suffixes: set[str],
    *,
    skip_parts: set[str],
    test_directory_names: set[str],
    test_filename_markers: Sequence[str],
) -> SourceSnapshot:
    files: list[SourceFile] = []
    symlinks: list[str] = []
    errors: list[str] = []
    for path in iter_source_paths(
        root,
        roots,
        suffixes,
        skip_parts=skip_parts,
        test_directory_names=test_directory_names,
        test_filename_markers=test_filename_markers,
    ):
        relative = path.relative_to(root).as_posix()
        try:
            preliminary = path.lstat()
            if stat.S_ISLNK(preliminary.st_mode):
                symlinks.append(relative)
                continue
            if not stat.S_ISREG(preliminary.st_mode):
                continue
            content = _stable_regular_bytes(path)
        except (OSError, SnapshotError) as exc:
            errors.append(f"{relative}:{type(exc).__name__}")
            continue
        files.append(
            SourceFile(
                path=path,
                relative_path=relative,
                category=_category(relative),
                physical_lines=len(content.splitlines()),
                byte_count=len(content),
                sha256=hashlib.sha256(content).hexdigest(),
                content=content,
            )
        )
    ordered = tuple(sorted(files, key=lambda item: item.relative_path))
    ordered_symlinks = tuple(sorted(symlinks))
    ordered_errors = tuple(sorted(errors))
    return SourceSnapshot(
        files=ordered,
        content_sha256=source_content_sha256(ordered, ordered_symlinks, ordered_errors),
        byte_count=sum(item.byte_count for item in ordered),
        physical_lines=sum(item.physical_lines for item in ordered),
        symlink_sources=ordered_symlinks,
        read_errors=ordered_errors,
    )


def decode_python(item: SourceFile) -> str:
    try:
        encoding, _ = tokenize.detect_encoding(io.BytesIO(item.content).readline)
        return item.content.decode(encoding)
    except (SyntaxError, UnicodeError) as exc:
        raise SnapshotError(f"cannot decode Python snapshot: {item.relative_path}") from exc


def _trusted_git_binary(explicit: Path | None = None) -> Path:
    candidates = (explicit,) if explicit is not None else TRUSTED_GIT_CANDIDATES
    for candidate in candidates:
        if candidate is None or not candidate.is_absolute():
            continue
        try:
            resolved = candidate.resolve(strict=True)
            metadata = candidate.lstat()
        except OSError:
            continue
        if resolved != candidate or not stat.S_ISREG(metadata.st_mode) or metadata.st_uid != 0:
            continue
        if not os.access(candidate, os.X_OK):
            continue
        return candidate
    raise SnapshotError("trusted root-owned absolute git binary unavailable")


def _git_env() -> dict[str, str]:
    return {
        "LANG": "C",
        "LC_ALL": "C",
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_CONFIG_GLOBAL": "/dev/null",
        "GIT_OPTIONAL_LOCKS": "0",
        "GIT_TERMINAL_PROMPT": "0",
    }


def _git(root: Path, git_binary: Path, *args: str) -> str:
    command = [
        str(git_binary),
        "-c", "core.fsmonitor=false",
        "-c", "core.untrackedCache=false",
        *args,
    ]
    try:
        completed = subprocess.run(
            command,
            cwd=root,
            env=_git_env(),
            stdin=subprocess.DEVNULL,
            capture_output=True,
            check=False,
            timeout=30,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise SnapshotError(f"trusted git command failed: {args[0] if args else 'unknown'}") from exc
    if completed.returncode != 0:
        raise SnapshotError(f"trusted git command rejected: {args[0] if args else 'unknown'}")
    try:
        return completed.stdout.decode("utf-8", "strict").strip()
    except UnicodeDecodeError as exc:
        raise SnapshotError("trusted git output is not UTF-8") from exc


def trusted_git_state(root: Path, *, git_binary: Path | None = None) -> dict[str, object]:
    binary = _trusted_git_binary(git_binary)
    status = _git(root, binary, "status", "--porcelain=v1", "--untracked-files=all", "--no-renames")
    lines = [line for line in status.splitlines() if line]
    return {
        "branch": _git(root, binary, "branch", "--show-current"),
        "head": _git(root, binary, "rev-parse", "HEAD"),
        "clean_worktree": not lines,
        "tracked_change_count": sum(not line.startswith("??") for line in lines),
        "untracked_change_count": sum(line.startswith("??") for line in lines),
        "status_sha256": hashlib.sha256(status.encode("utf-8")).hexdigest(),
        "git_binary": str(binary),
        "git_binary_sha256": hashlib.sha256(binary.read_bytes()).hexdigest(),
    }
