#!/usr/bin/env python3
"""Freeze a dirty V-KPI worktree into a content-addressed local candidate.

This command is deliberately local-only.  It never stages, commits, pushes,
uploads, edits systemd, contacts a provider, or deploys.  The source inventory
comes from Git's tracked plus non-ignored untracked paths, while the bytes come
from the working tree.  A before/copy/after digest check fails closed when the
source changes during the freeze.

The default workflow rebuilds ``frontend/dist`` inside the snapshot, runs the
canonical static gate against the snapshot, creates a deterministic tar, and
writes an adjacent secret-free manifest.  Dependencies are borrowed through
temporary symlinks and are never copied into the candidate.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import stat
import subprocess
import sys
import tarfile
import tempfile
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath
from typing import Iterator, Sequence


SCHEMA = "vkpi.local-worktree-candidate/v1"
MAX_SOURCE_FILE_BYTES = 256 * 1024 * 1024

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
    # 2026-07-20:macOS Finder 浏览目录即写 .DS_Store,冻结后仍会改写文件内容,
    # 连续搅黄五班车(候选指纹漂移);快照/盘点双侧统一排除,Finder 从此无关。
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


def _canonical_bytes(value: object) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _sha256_path(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _run_git_bytes(root: Path, *args: str) -> bytes:
    try:
        return subprocess.check_output(
            ["git", *args], cwd=root, stderr=subprocess.PIPE
        )
    except (OSError, subprocess.CalledProcessError) as exc:
        raise FreezeError(f"git command failed: {' '.join(args)}") from exc


def _run_git_text(root: Path, *args: str) -> str:
    return _run_git_bytes(root, *args).decode("utf-8", "strict").strip()


def _safe_relative(raw: str) -> str:
    path = PurePosixPath(raw)
    if path.is_absolute() or not path.parts:
        raise FreezeError(f"unsafe source path: {raw!r}")
    if any(part in {"", ".", ".."} for part in path.parts):
        raise FreezeError(f"unsafe source path: {raw!r}")
    return path.as_posix()


def is_excluded(path: str, *, source_phase: bool) -> bool:
    pure = PurePosixPath(_safe_relative(path))
    lower_parts = tuple(part.lower() for part in pure.parts)
    lower_name = pure.name.lower()
    if set(lower_parts) & FORBIDDEN_COMPONENTS:
        return True
    if lower_parts and lower_parts[0] in GENERATED_ROOT_COMPONENTS:
        return True
    if lower_parts[:2] == ("reports", "generated"):
        return True
    if lower_parts[:2] == ("frontend", "dist") and source_phase:
        return True
    if lower_name in FORBIDDEN_NAMES or lower_name.startswith(".env"):
        return True
    if lower_name.endswith(FORBIDDEN_SUFFIXES):
        return True
    return False


def _git_worktree_paths(root: Path) -> list[str]:
    raw = _run_git_bytes(
        root,
        "ls-files",
        "-z",
        "--cached",
        "--others",
        "--exclude-standard",
    )
    paths: list[str] = []
    seen: set[str] = set()
    for item in raw.split(b"\0"):
        if not item:
            continue
        try:
            path = _safe_relative(item.decode("utf-8", "strict"))
        except UnicodeDecodeError as exc:
            raise FreezeError("non-UTF-8 Git path is not supported") from exc
        if path in seen:
            raise FreezeError(f"duplicate Git source path: {path}")
        seen.add(path)
        absolute = root / path
        if not absolute.exists() and not absolute.is_symlink():
            # A worktree deletion is represented by absence plus the status
            # digest in the manifest; there are no bytes to copy.
            continue
        if is_excluded(path, source_phase=True):
            continue
        paths.append(path)
    return sorted(paths)


def _check_secret(path: str, data: bytes) -> None:
    for pattern in HIGH_CONFIDENCE_SECRET_PATTERNS:
        if pattern.search(data):
            raise FreezeError(f"high-confidence secret detected: {path}")


def _read_entry(root: Path, path: str) -> tuple[FileEntry, bytes]:
    absolute = root / path
    before = absolute.lstat()
    if stat.S_ISLNK(before.st_mode):
        raise FreezeError(f"symlink source requires separate review: {path}")
    if not stat.S_ISREG(before.st_mode):
        raise FreezeError(f"non-regular source requires separate review: {path}")
    if before.st_size > MAX_SOURCE_FILE_BYTES:
        raise FreezeError(f"source file exceeds {MAX_SOURCE_FILE_BYTES} bytes: {path}")
    data = absolute.read_bytes()
    after = absolute.lstat()
    identity_before = (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns)
    identity_after = (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns)
    if identity_before != identity_after or len(data) != before.st_size:
        raise FreezeError(f"source changed while reading: {path}")
    _check_secret(path, data)
    entry = FileEntry(
        path=path,
        size_bytes=len(data),
        mode=stat.S_IMODE(before.st_mode),
        sha256=hashlib.sha256(data).hexdigest(),
    )
    return entry, data


def _inventory_source(root: Path) -> list[FileEntry]:
    return [_read_entry(root, path)[0] for path in _git_worktree_paths(root)]


def _inventory_digest(entries: Sequence[FileEntry]) -> str:
    return hashlib.sha256(
        _canonical_bytes([entry.payload() for entry in entries])
    ).hexdigest()


def _copy_inventory(root: Path, destination: Path, entries: Sequence[FileEntry]) -> None:
    for expected in entries:
        current, data = _read_entry(root, expected.path)
        if current != expected:
            raise FreezeError(f"source drift before copy: {expected.path}")
        target = destination / expected.path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
        os.chmod(target, expected.mode)


def _candidate_paths(root: Path) -> list[str]:
    result: list[str] = []
    for directory, names, files in os.walk(root, topdown=True, followlinks=False):
        base = Path(directory)
        relative_base = base.relative_to(root)
        kept_names: list[str] = []
        for name in sorted(names):
            relative = (relative_base / name).as_posix()
            if relative == ".":
                relative = name
            if is_excluded(relative, source_phase=False):
                continue
            if (base / name).is_symlink():
                raise FreezeError(f"candidate dependency symlink was not removed: {relative}")
            kept_names.append(name)
        names[:] = kept_names
        for name in sorted(files):
            relative = (relative_base / name).as_posix()
            if relative.startswith("./"):
                relative = relative[2:]
            if is_excluded(relative, source_phase=False):
                continue
            absolute = root / relative
            if absolute.is_symlink() or not absolute.is_file():
                raise FreezeError(f"candidate contains non-regular file: {relative}")
            result.append(relative)
    return sorted(result)


def _inventory_candidate(root: Path) -> list[FileEntry]:
    entries: list[FileEntry] = []
    for path in _candidate_paths(root):
        entries.append(_read_entry(root, path)[0])
    return entries


def _physical_special_paths(root: Path) -> list[str]:
    """List unsupported physical nodes without following candidate symlinks."""

    special: list[str] = []
    pending = [root]
    while pending:
        directory = pending.pop()
        try:
            entries = sorted(os.scandir(directory), key=lambda entry: entry.name)
        except OSError as exc:
            raise FreezeError(f"candidate physical tree cannot be scanned: {directory}") from exc
        for entry in entries:
            path = Path(entry.path)
            try:
                info = entry.stat(follow_symlinks=False)
            except OSError as exc:
                raise FreezeError(f"candidate physical node cannot be inspected: {path}") from exc
            if stat.S_ISDIR(info.st_mode):
                pending.append(path)
            elif not (stat.S_ISREG(info.st_mode) or stat.S_ISLNK(info.st_mode)):
                special.append(path.relative_to(root).as_posix())
    return sorted(special)


@contextmanager
def _borrow_dependencies(snapshot: Path, source: Path) -> Iterator[None]:
    links = (
        (snapshot / ".venv", source / ".venv"),
        (snapshot / "frontend" / "node_modules", source / "frontend" / "node_modules"),
    )
    created: list[Path] = []
    try:
        for link, target in links:
            if not target.is_dir():
                raise FreezeError(f"required local dependency is missing: {target}")
            if link.exists() or link.is_symlink():
                raise FreezeError(f"snapshot unexpectedly contains dependency path: {link}")
            link.parent.mkdir(parents=True, exist_ok=True)
            link.symlink_to(target, target_is_directory=True)
            created.append(link)
        yield
    finally:
        for link in reversed(created):
            link.unlink(missing_ok=True)


def _run_logged(
    command: Sequence[str],
    *,
    cwd: Path,
    env: dict[str, str],
    log_path: Path,
) -> None:
    with log_path.open("wb") as log:
        proc = subprocess.run(
            list(command),
            cwd=cwd,
            env=env,
            stdin=subprocess.DEVNULL,
            stdout=log,
            stderr=subprocess.STDOUT,
            check=False,
        )
    if proc.returncode != 0:
        raise FreezeError(
            f"command failed with exit {proc.returncode}; inspect {log_path}"
        )


def _write_build_stamps(snapshot: Path, identity: BuildIdentity) -> None:
    stamps = {
        "BUILD_GIT_SHA": identity.git_sha,
        "BUILD_GIT_BRANCH": identity.git_branch,
        "BUILD_TIME": identity.build_time,
    }
    for name, value in stamps.items():
        path = snapshot / name
        path.write_text(value + "\n", encoding="utf-8")
        os.chmod(path, 0o644)


def _validate_frontend_build_info(
    snapshot: Path, identity: BuildIdentity
) -> dict[str, object]:
    path = snapshot / "frontend" / "dist" / "build-info.json"
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise FreezeError("frontend build-info.json is missing or invalid") from exc
    if not isinstance(payload, dict):
        raise FreezeError("frontend build-info.json must be an object")
    expected = {
        "builtAt": identity.build_time,
        "gitBranch": identity.git_branch,
        "gitSha": identity.git_sha,
        "gitShortSha": identity.git_sha[:8],
    }
    mismatches = {
        key: {"expected": value, "observed": payload.get(key)}
        for key, value in expected.items()
        if payload.get(key) != value
    }
    if mismatches:
        raise FreezeError(
            "frontend build-info identity mismatch: "
            + json.dumps(mismatches, sort_keys=True)
        )
    return payload


def _build_frontend(
    snapshot: Path,
    source: Path,
    log_path: Path,
    identity: BuildIdentity,
) -> dict[str, object]:
    env = os.environ.copy()
    env.update({"CI": "1", "NODE_ENV": "production"})
    env.update(identity.vite_environment())
    with _borrow_dependencies(snapshot, source):
        dist = snapshot / "frontend" / "dist"
        if dist.exists():
            shutil.rmtree(dist)
        _run_logged(
            ["npm", "run", "build", "--", "--outDir", str(dist), "--emptyOutDir"],
            cwd=snapshot / "frontend",
            env=env,
            log_path=log_path,
        )
    if not (snapshot / "frontend" / "dist" / "index.html").is_file():
        raise FreezeError("frontend build did not create dist/index.html")
    return _validate_frontend_build_info(snapshot, identity)


def _run_static_verify(
    snapshot: Path,
    source: Path,
    log_path: Path,
    identity: BuildIdentity,
) -> None:
    env = os.environ.copy()
    env.update(
        {
            "APP_BUILD_TIME": identity.build_time,
            "APP_GIT_BRANCH": identity.git_branch,
            "APP_GIT_SHA": identity.git_sha,
            "PYTHON_BIN": str(source / ".venv" / "bin" / "python"),
            "PYTHON_BIN_FALLBACK": sys.executable,
            "VKPI_HEALTH_URL": "http://127.0.0.1:9/health",
            "VKPI_VERIFY_REQUIRE_BROWSER_CONSOLE": "0",
            "VKPI_VERIFY_REQUIRE_CLEAN_WORKTREE": "0",
            "VKPI_VERIFY_REQUIRE_RUNTIME": "0",
            "VKPI_VERIFY_REQUIRE_RUNTIME_LOG_CANARY": "0",
        }
    )
    env.update(identity.vite_environment())
    with _borrow_dependencies(snapshot, source):
        _run_logged(
            ["bash", "scripts/verify.sh"],
            cwd=snapshot,
            env=env,
            log_path=log_path,
        )


def _deterministic_tar(
    snapshot: Path, archive: Path, entries: Sequence[FileEntry]
) -> None:
    directories: set[PurePosixPath] = set()
    for entry in entries:
        parent = PurePosixPath(entry.path).parent
        while parent.parts:
            directories.add(parent)
            parent = parent.parent
    with tarfile.open(archive, mode="w", format=tarfile.PAX_FORMAT) as bundle:
        for directory in sorted(directories, key=lambda item: (len(item.parts), item.as_posix())):
            info = tarfile.TarInfo(directory.as_posix())
            info.type = tarfile.DIRTYPE
            info.mode = 0o755
            info.uid = 0
            info.gid = 0
            info.uname = "root"
            info.gname = "root"
            info.mtime = 0
            bundle.addfile(info)
        by_path = {entry.path: entry for entry in entries}
        for path in sorted(by_path):
            entry = by_path[path]
            info = tarfile.TarInfo(path)
            info.size = entry.size_bytes
            info.mode = entry.mode
            info.uid = 0
            info.gid = 0
            info.uname = "root"
            info.gname = "root"
            info.mtime = 0
            with (snapshot / path).open("rb") as handle:
                bundle.addfile(info, handle)


def _atomic_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def freeze_candidate(args: argparse.Namespace) -> dict[str, object]:
    source = Path(args.repo).resolve()
    if not (source / ".git").exists():
        raise FreezeError(f"not a Git worktree: {source}")
    output = Path(args.output).resolve()
    if output == source or source in output.parents:
        # Output inside runtime/ is supported and intentionally excluded from
        # Git inventory.  Any other in-repo destination could recurse or leak.
        try:
            relative_output = output.relative_to(source)
        except ValueError:
            relative_output = None
        if relative_output is None or not relative_output.parts or relative_output.parts[0] != "runtime":
            raise FreezeError("in-repo output must live under runtime/")
    if output.exists() or output.is_symlink():
        raise FreezeError(f"output already exists: {output}")
    output.parent.mkdir(parents=True, exist_ok=True)

    head = _run_git_text(source, "rev-parse", "HEAD")
    branch = _run_git_text(source, "branch", "--show-current") or _run_git_text(
        source, "rev-parse", "--abbrev-ref", "HEAD"
    )
    build_time = (
        datetime.now(UTC)
        .replace(microsecond=0)
        .isoformat()
        .replace("+00:00", "Z")
    )
    identity = BuildIdentity(
        git_sha=head,
        git_branch=branch,
        build_time=build_time,
    )
    status_before = _run_git_bytes(
        source, "status", "--porcelain=v1", "-z", "--untracked-files=all"
    )
    entries_before = _inventory_source(source)
    source_digest = _inventory_digest(entries_before)
    temporary = Path(
        tempfile.mkdtemp(prefix=f".{output.name}.", suffix=".freeze", dir=output.parent)
    )
    build_log = output.with_suffix(output.suffix + ".build.log")
    verify_log = output.with_suffix(output.suffix + ".verify.log")
    archive = output.with_suffix(output.suffix + ".tar")
    manifest_path = output.with_suffix(output.suffix + ".manifest.json")
    try:
        _copy_inventory(source, temporary, entries_before)
        entries_after = _inventory_source(source)
        status_after = _run_git_bytes(
            source, "status", "--porcelain=v1", "-z", "--untracked-files=all"
        )
        if entries_after != entries_before or status_after != status_before:
            raise FreezeError("worktree drifted during candidate copy")

        # The source may contain stale deployment stamps from an earlier build.
        # Replace them only inside the frozen snapshot, before any Vite or
        # backend imports occur.  This is the authoritative identity for the
        # no-.git candidate.
        _write_build_stamps(temporary, identity)

        if not args.skip_build:
            frontend_build_info = _build_frontend(
                temporary, source, build_log, identity
            )
        else:
            frontend_build_info = None
            copied_dist = temporary / "frontend" / "dist"
            if copied_dist.exists():
                shutil.rmtree(copied_dist)

        if not args.skip_verify:
            _run_static_verify(temporary, source, verify_log, identity)
            if frontend_build_info is not None:
                # The static gate builds into an isolated output directory and
                # must not rewrite the candidate dist identity.
                _validate_frontend_build_info(temporary, identity)

        candidate_entries = _inventory_candidate(temporary)
        if not args.skip_build and not any(
            entry.path == "frontend/dist/index.html" for entry in candidate_entries
        ):
            raise FreezeError("rebuilt frontend dist is missing from candidate inventory")
        candidate_digest = _inventory_digest(candidate_entries)
        if not args.skip_archive:
            _deterministic_tar(temporary, archive, candidate_entries)
            archive_payload: dict[str, object] | None = {
                "bytes": archive.stat().st_size,
                "path": str(archive),
                "sha256": _sha256_path(archive),
            }
        else:
            archive_payload = None

        os.replace(temporary, output)
        payload: dict[str, object] = {
            "archive": archive_payload,
            "build": {
                "build_info": frontend_build_info,
                "build_info_path": (
                    "frontend/dist/build-info.json" if frontend_build_info is not None else None
                ),
                "build_info_sha256": (
                    _sha256_path(output / "frontend" / "dist" / "build-info.json")
                    if frontend_build_info is not None
                    else None
                ),
                "executed": not args.skip_build,
                "identity": identity.payload(),
                "log_path": str(build_log) if not args.skip_build else None,
                "log_sha256": _sha256_path(build_log) if build_log.exists() else None,
                "output": "frontend/dist",
            },
            "candidate": {
                "content_sha256": candidate_digest,
                "file_count": len(candidate_entries),
                "files": [entry.payload() for entry in candidate_entries],
                "snapshot_path": str(output),
            },
            "created_at": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
            "exclusion_contract": {
                "components": sorted(FORBIDDEN_COMPONENTS),
                "generated_roots": sorted(GENERATED_ROOT_COMPONENTS),
                "secret_env_prefix": ".env*",
                "source_frontend_dist": "excluded_and_rebuilt",
                "suffixes": list(FORBIDDEN_SUFFIXES),
            },
            "safety": {
                "cloud_contacted": False,
                "commit_created": False,
                "deployment_performed": False,
                "push_performed": False,
                "stage_performed": False,
            },
            "schema": SCHEMA,
            "source": {
                "branch": branch,
                "content_sha256": source_digest,
                "file_count": len(entries_before),
                "head": head,
                "repo": str(source),
                "status_sha256": hashlib.sha256(status_before).hexdigest(),
                "worktree_dirty": bool(status_before),
            },
            "verification": {
                "classification": "static_snapshot_gate_not_runtime_acceptance",
                "executed": not args.skip_verify,
                "log_path": str(verify_log) if not args.skip_verify else None,
                "log_sha256": _sha256_path(verify_log) if verify_log.exists() else None,
                "runtime_intentionally_unreachable": not args.skip_verify,
            },
        }
        _atomic_json(manifest_path, payload)
        manifest_sha = _sha256_path(manifest_path)
        manifest_path.with_suffix(manifest_path.suffix + ".sha256").write_text(
            f"{manifest_sha}  {manifest_path.name}\n", encoding="utf-8"
        )
        return payload
    except Exception:
        shutil.rmtree(temporary, ignore_errors=True)
        if output.exists():
            shutil.rmtree(output, ignore_errors=True)
        if archive.exists():
            archive.unlink()
        raise


def verify_manifest(args: argparse.Namespace) -> dict[str, object]:
    manifest_path = Path(args.manifest).resolve()
    try:
        payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise FreezeError(f"invalid manifest: {manifest_path}") from exc
    if not isinstance(payload, dict) or payload.get("schema") != SCHEMA:
        raise FreezeError("manifest schema mismatch")
    candidate = payload.get("candidate")
    if not isinstance(candidate, dict):
        raise FreezeError("candidate section missing")
    snapshot = Path(args.snapshot or str(candidate.get("snapshot_path", ""))).resolve()
    entries = _inventory_candidate(snapshot)
    digest = _inventory_digest(entries)
    if digest != candidate.get("content_sha256"):
        raise FreezeError("candidate content digest mismatch")
    expected_files = candidate.get("files")
    if [entry.payload() for entry in entries] != expected_files:
        raise FreezeError("candidate file manifest mismatch")
    archive = payload.get("archive")
    if isinstance(archive, dict) and archive.get("path"):
        archive_path = Path(str(archive["path"]))
        if not archive_path.is_file():
            raise FreezeError("candidate archive is missing")
        if _sha256_path(archive_path) != archive.get("sha256"):
            raise FreezeError("candidate archive digest mismatch")
    return {
        "content_sha256": digest,
        "file_count": len(entries),
        "manifest": str(manifest_path),
        "pass": True,
        "snapshot": str(snapshot),
    }


def verify_deploy_source(args: argparse.Namespace) -> dict[str, object]:
    """Bind one verified snapshot to the exact Git identity being deployed."""

    expected_head = str(args.expected_head).strip()
    if not re.fullmatch(r"[0-9a-f]{40}", expected_head):
        raise FreezeError("expected deploy Git SHA must be a lowercase 40-character digest")
    expected_branch = str(args.expected_branch)
    if not expected_branch or any(character in expected_branch for character in "\r\n\0"):
        raise FreezeError("expected deploy Git branch is invalid")

    manifest_path = Path(args.manifest).resolve()
    try:
        payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise FreezeError(f"invalid manifest: {manifest_path}") from exc
    if not isinstance(payload, dict) or payload.get("schema") != SCHEMA:
        raise FreezeError("manifest schema mismatch")

    candidate = payload.get("candidate")
    source = payload.get("source")
    build = payload.get("build")
    identity = build.get("identity") if isinstance(build, dict) else None
    if not isinstance(candidate, dict) or not isinstance(source, dict):
        raise FreezeError("deploy candidate source binding is missing")
    if not isinstance(identity, dict):
        raise FreezeError("deploy candidate build identity is missing")

    raw_snapshot = Path(args.snapshot)
    if raw_snapshot.is_symlink() or not raw_snapshot.is_dir():
        raise FreezeError("deploy candidate snapshot is missing or unsafe")
    snapshot = raw_snapshot.resolve()
    recorded_snapshot_raw = candidate.get("snapshot_path")
    if not isinstance(recorded_snapshot_raw, str) or not recorded_snapshot_raw:
        raise FreezeError("deploy candidate snapshot path is missing")
    recorded_snapshot = Path(recorded_snapshot_raw).resolve()
    if recorded_snapshot != snapshot:
        raise FreezeError("deploy candidate snapshot canonical path mismatch")
    if source.get("worktree_dirty") is not False:
        raise FreezeError("deploy candidate was frozen from a dirty worktree")
    if source.get("head") != expected_head:
        raise FreezeError("deploy candidate source HEAD mismatch")
    if identity.get("git_sha") != expected_head:
        raise FreezeError("deploy candidate build identity Git SHA mismatch")
    if source.get("branch") != expected_branch:
        raise FreezeError("deploy candidate source branch mismatch")
    if identity.get("git_branch") != expected_branch:
        raise FreezeError("deploy candidate build identity Git branch mismatch")

    special_paths = _physical_special_paths(snapshot)
    if special_paths:
        raise FreezeError(
            "deploy candidate contains unsupported special file: "
            + ", ".join(special_paths[:10])
        )

    build_identity = BuildIdentity(
        git_sha=str(identity.get("git_sha", "")),
        git_branch=str(identity.get("git_branch", "")),
        build_time=str(identity.get("build_time", "")),
    )
    expected_stamps = {
        "BUILD_GIT_SHA": build_identity.git_sha,
        "BUILD_GIT_BRANCH": build_identity.git_branch,
        "BUILD_TIME": build_identity.build_time,
    }
    for name, expected in expected_stamps.items():
        path = snapshot / name
        if path.is_symlink() or not path.is_file():
            raise FreezeError(f"deploy candidate build stamp is missing or unsafe: {name}")
        try:
            observed = path.read_text(encoding="utf-8").strip()
        except (OSError, UnicodeDecodeError) as exc:
            raise FreezeError(f"deploy candidate build stamp is unreadable: {name}") from exc
        if observed != expected:
            raise FreezeError(f"deploy candidate build stamp mismatch: {name}")

    result = verify_manifest(
        argparse.Namespace(manifest=str(manifest_path), snapshot=str(snapshot))
    )
    result.update(
        {
            "build_git_sha": identity.get("git_sha"),
            "source_git_sha": source.get("head"),
        }
    )
    return result


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description=__doc__)
    subparsers = result.add_subparsers(dest="command", required=True)
    freeze = subparsers.add_parser("freeze")
    freeze.add_argument("--repo", default=str(Path(__file__).resolve().parents[2]))
    freeze.add_argument("--output", required=True)
    freeze.add_argument("--skip-build", action="store_true")
    freeze.add_argument("--skip-verify", action="store_true")
    freeze.add_argument("--skip-archive", action="store_true")
    freeze.set_defaults(action=freeze_candidate)
    verify = subparsers.add_parser("verify-manifest")
    verify.add_argument("--manifest", required=True)
    verify.add_argument("--snapshot")
    verify.set_defaults(action=verify_manifest)
    deploy_source = subparsers.add_parser("verify-deploy-source")
    deploy_source.add_argument("--manifest", required=True)
    deploy_source.add_argument("--snapshot", required=True)
    deploy_source.add_argument("--expected-head", required=True)
    deploy_source.add_argument("--expected-branch", required=True)
    deploy_source.set_defaults(action=verify_deploy_source)
    return result


def main() -> int:
    args = parser().parse_args()
    try:
        payload = args.action(args)
    except (FreezeError, OSError, subprocess.SubprocessError) as exc:
        sys.stderr.write(f"candidate freeze failed: {exc}\n")
        return 1
    sys.stdout.write(json.dumps(payload, ensure_ascii=False, sort_keys=True) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
