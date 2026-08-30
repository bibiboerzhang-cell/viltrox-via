#!/usr/bin/env python3
"""Freeze a dirty V-KPI worktree into a content-addressed local candidate.

This command is deliberately local-only.  It never stages, commits, pushes,
uploads, edits systemd, contacts a provider, or deploys.  The source inventory
comes from Git's tracked plus non-ignored untracked paths, while the bytes come
from the working tree.  A before/copy/after digest check fails closed when the
source changes during the freeze.

The default rebuilds ``frontend/dist``, runs the static gate, and emits a
deterministic tar plus manifest without copying borrowed dependencies.
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
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath
from typing import Iterator, Sequence

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from scripts.ops.freeze_git_bridge import (  # noqa: E402
    GIT_REPOSITORY_BINDING_ENV,
    GitBridgeError,
    readonly_snapshot_git_environment as _readonly_snapshot_git_environment,
)
from scripts.ops.deploy_gate_runtime import (  # noqa: E402
    DeployGateRuntimeError,
    bound_deploy_gate_runtime,
)
from scripts.ops.freeze_phase_runtime import (  # noqa: E402
    physical_special_paths as _physical_special_paths,
    publish_owned_log as _publish_owned_log,
    remove_owned_phase_sandbox as _remove_owned_phase_sandbox,
    run_logged as _run_logged,
)
from scripts.ops.freeze_worktree_contract import (  # noqa: E402
    FORBIDDEN_COMPONENTS,
    FORBIDDEN_NAMES,
    FORBIDDEN_SUFFIXES,
    GENERATED_ROOT_COMPONENTS,
    HIGH_CONFIDENCE_SECRET_PATTERNS,
    MAX_SOURCE_FILE_BYTES,
    SCHEMA,
    BuildIdentity,
    FileEntry,
    FreezeError,
    cleanup_owned_paths,
    path_identity,
    precreate_owned_file,
    rename_exclusive,
    write_owned_file_exclusive,
    assert_frontend_dist_reproducible as _check_frontend_dist_reproducible,
    _regular_tree_inventory as _contract_regular_tree_inventory,
)


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
    from scripts.ops.trusted_git import git_env, trusted_git_executable
    try:
        return subprocess.check_output(
            [trusted_git_executable(), *args], cwd=root, stderr=subprocess.PIPE,
            env=git_env(),
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
    log_identity: tuple[int, int],
    identity: BuildIdentity,
) -> dict[str, object]:
    from scripts.ops.deploy_gate_runtime import assert_provider_free_environment, build_provider_free_subprocess_environment
    env = build_provider_free_subprocess_environment(
        os.environ, home=log_path.parent, tmpdir=log_path.parent,
    )
    env.update({"CI": "1", "NODE_ENV": "production"})
    env.update(identity.vite_environment())
    assert_provider_free_environment(env)
    from scripts.ops.strict_runtime_seatbelt import candidate_profile, sandboxed
    from scripts.ops.trusted_npm_audit import _trusted_node, _trusted_npm
    npm, node = _trusted_npm(), _trusted_node()
    sandbox_root = Path(tempfile.mkdtemp(prefix="vkpi-phase-a-seatbelt.", dir="/tmp"))
    sandbox_root.chmod(0o700)
    for child in ("home", "tmp", "cache"):
        (sandbox_root / child).mkdir(mode=0o700)
    env.update(
        {
            "HOME": str(sandbox_root / "home"),
            "TMPDIR": str(sandbox_root / "tmp"),
            "XDG_CACHE_HOME": str(sandbox_root / "cache"),
        }
    )
    assert_provider_free_environment(env)
    sandbox_log = sandbox_root / "candidate-build.log"
    try:
        dist = snapshot / "frontend" / "dist"
        profile = candidate_profile(candidate=snapshot, clean_source=snapshot,
            venv=source / ".venv", node_modules=source / "frontend/node_modules",
            runtime_root=sandbox_root, allowed_ports=(), writable_paths=(dist,),
            protect_clean_source=False,
            executable_paths=(npm, node),
            # Some dependency tools resolve their owning package.json from
            # the physical node_modules path.  Permit that one immutable
            # package descriptor, not the source frontend tree.
            readable_paths=(npm.parent.parent, source / "frontend/package.json"))
        with _borrow_dependencies(snapshot, source):
            if dist.exists():
                shutil.rmtree(dist)
            _run_logged(
                sandboxed([str(node), str(npm), "run", "build", "--", "--outDir", str(dist),
                           "--emptyOutDir"], profile), cwd=snapshot / "frontend",
                env=env, log_path=sandbox_log, error_log_path=log_path,
            )
    finally:
        try:
            if sandbox_log.is_file() and not sandbox_log.is_symlink():
                _publish_owned_log(sandbox_log, log_path, log_identity)
        finally:
            _remove_owned_phase_sandbox(sandbox_root)
    if not (snapshot / "frontend" / "dist" / "index.html").is_file():
        raise FreezeError("frontend build did not create dist/index.html")
    return _validate_frontend_build_info(snapshot, identity)
def _run_static_verify(
    snapshot: Path,
    source: Path,
    log_path: Path,
    log_identity: tuple[int, int],
    identity: BuildIdentity,
) -> None:
    source_top = Path(_run_git_text(source, "rev-parse", "--show-toplevel")).resolve()
    if source_top != source:
        raise FreezeError("source Git worktree binding does not match freeze root")

    from scripts.ops.deploy_gate_runtime import assert_provider_free_environment, build_provider_free_subprocess_environment
    env = build_provider_free_subprocess_environment(
        os.environ, home=log_path.parent, tmpdir=log_path.parent,
    )
    # This output path is reserved for the later canonical deploy gate.  A
    # caller must not be able to redirect an ordinary freeze-time verifier to
    # an arbitrary path or make the frozen snapshot appear reproducible by
    # pre-seeding output outside the private gate runtime.
    env.pop("VKPI_VERIFY_FRONTEND_OUT_DIR", None)
    for name in GIT_REPOSITORY_BINDING_ENV:
        env.pop(name, None)
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
    assert_provider_free_environment(env)
    from scripts.ops.strict_runtime_seatbelt import candidate_profile, sandboxed
    from scripts.ops.trusted_npm_audit import _trusted_node, _trusted_npm, _trusted_npx, run_trusted_npm_audit
    npm, node = _trusted_npm(), _trusted_node()
    npx = _trusted_npx(npm)
    sandbox_root = Path(tempfile.mkdtemp(prefix="vkpi-phase-a-seatbelt.", dir="/tmp"))
    sandbox_root.chmod(0o700)
    for child in ("home", "tmp", "cache"):
        (sandbox_root / child).mkdir(mode=0o700)
    env.update(
        {
            "HOME": str(sandbox_root / "home"),
            "PYTEST_ADDOPTS": "-p no:cacheprovider",
            "TMPDIR": str(sandbox_root / "tmp"),
            "VKPI_RUNTIME_DATA_DIR": str(sandbox_root / "runtime-data"),
            "XDG_CACHE_HOME": str(sandbox_root / "cache"),
        }
    )
    (sandbox_root / "runtime-data").mkdir(mode=0o700)
    assert_provider_free_environment(env)
    audit_receipt = sandbox_root / "npm-audit.json"
    bridge_parent = sandbox_root / "controller"
    bridge_parent.mkdir(mode=0o700)
    if (snapshot / "frontend/package-lock.json").is_file():
        run_trusted_npm_audit(snapshot / "frontend", audit_receipt)
        env["VKPI_TRUSTED_NPM_AUDIT_RECEIPT"] = str(audit_receipt)
    profile = candidate_profile(candidate=snapshot, clean_source=snapshot,
        venv=source / ".venv", node_modules=source / "frontend/node_modules",
        runtime_root=sandbox_root, allowed_ports=(), protect_clean_source=False,
        executable_paths=(node, npm, npx, Path("/bin/bash")), executable_dirs=(bridge_parent,),
        readable_paths=(npm.parent.parent, source / "frontend/package.json"))
    sandbox_log = sandbox_root / "candidate-verify.log"
    try:
        with _readonly_snapshot_git_environment(
            snapshot, source, bridge_parent=bridge_parent,
        ) as git_environment:
            env.update(git_environment)
            with _borrow_dependencies(snapshot, source):
                _run_logged(sandboxed(["/bin/bash", "scripts/verify.sh"], profile),
                            cwd=snapshot, env=env, log_path=sandbox_log,
                            error_log_path=log_path)
    finally:
        try:
            if sandbox_log.is_file() and not sandbox_log.is_symlink():
                _publish_owned_log(sandbox_log, log_path, log_identity)
        finally:
            _remove_owned_phase_sandbox(sandbox_root)
def _regular_tree_inventory(root: Path) -> list[tuple[str, str, int, str]]:
    """Backward-compatible wrapper around the shared strict tree inventory."""

    return _contract_regular_tree_inventory(root)


def _assert_frontend_dist_reproducible(candidate: Path, rebuilt: Path) -> None:
    _check_frontend_dist_reproducible(candidate, rebuilt)


def _assert_source_state_unchanged(
    source: Path,
    *,
    entries: Sequence[FileEntry],
    status: bytes,
    head: str,
    branch: str,
    phase: str,
) -> None:
    observed_head = _run_git_text(source, "rev-parse", "HEAD")
    observed_branch = _run_git_text(
        source, "branch", "--show-current"
    ) or _run_git_text(source, "rev-parse", "--abbrev-ref", "HEAD")
    observed_status = _run_git_bytes(
        source, "status", "--porcelain=v1", "-z", "--untracked-files=all"
    )
    observed_entries = _inventory_source(source)
    if (
        observed_head != head
        or observed_branch != branch
        or observed_status != status
        or observed_entries != entries
    ):
        raise FreezeError(f"worktree drifted during {phase}")


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


def _atomic_json(path: Path, payload: object) -> tuple[int, int]:
    path.parent.mkdir(parents=True, exist_ok=True)
    data = (json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode()
    return write_owned_file_exclusive(path, data)


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
    created: dict[Path, tuple[int, int]] = {temporary: path_identity(temporary)}
    try:
        for log_path, enabled in ((build_log, not args.skip_build),
                                  (verify_log, not args.skip_verify)):
            if enabled:
                created[log_path] = precreate_owned_file(log_path)
        _copy_inventory(source, temporary, entries_before)
        _assert_source_state_unchanged(
            source,
            entries=entries_before,
            status=status_before,
            head=head,
            branch=branch,
            phase="candidate copy",
        )

        # The source may contain stale deployment stamps from an earlier build.
        # Replace them only inside the frozen snapshot, before any Vite or
        # backend imports occur.  This is the authoritative identity for the
        # no-.git candidate.
        _write_build_stamps(temporary, identity)

        if not args.skip_build:
            frontend_build_info = _build_frontend(
                temporary, source, build_log, created[build_log], identity
            )
        else:
            frontend_build_info = None
            copied_dist = temporary / "frontend" / "dist"
            if copied_dist.exists():
                shutil.rmtree(copied_dist)

        if not args.skip_verify:
            _run_static_verify(
                temporary, source, verify_log, created[verify_log], identity
            )
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
            created[archive] = path_identity(archive)
            archive_payload: dict[str, object] | None = {
                "bytes": archive.stat().st_size,
                "path": str(archive),
                "sha256": _sha256_path(archive),
            }
        else:
            archive_payload = None

        # Build and verification borrow dependency directories from the source,
        # and verification receives a read-only Git identity binding. Recheck
        # bytes, status, HEAD, and branch before making the candidate visible.
        _assert_source_state_unchanged(
            source,
            entries=entries_before,
            status=status_before,
            head=head,
            branch=branch,
            phase="candidate build and verification",
        )

        rename_exclusive(temporary, output)
        created.pop(temporary); created[output] = path_identity(output)
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
                "provider_network_contacted": False,
                "external_registry_network": "npm_audit_may_attempt",
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
        created[manifest_path] = _atomic_json(manifest_path, payload)
        manifest_sha = _sha256_path(manifest_path)
        sidecar = manifest_path.with_suffix(manifest_path.suffix + ".sha256")
        created[sidecar] = write_owned_file_exclusive(
            sidecar, f"{manifest_sha}  {manifest_path.name}\n".encode()
        )
        return payload
    except Exception:
        # `_run_logged` deliberately points callers at these files.  Keep a
        # non-empty log only when it is still the exact inode pre-created by
        # this process; otherwise the generic owned-path cleanup remains
        # fail-closed and will never delete a replacement path.
        for failure_log in (build_log, verify_log):
            expected_identity = created.get(failure_log)
            if expected_identity is None:
                continue
            try:
                if (
                    not failure_log.is_symlink()
                    and failure_log.is_file()
                    and path_identity(failure_log) == expected_identity
                    and failure_log.stat().st_size > 0
                ):
                    created.pop(failure_log)
            except OSError:
                pass
        cleanup_owned_paths(created)
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


def run_deploy_gate(args: argparse.Namespace) -> dict[str, object]:
    """Run the canonical gate from candidate bytes, then reverify the candidate."""

    try:
        runtime_root = str(args.runtime_root)
        health_url = str(args.health_url)
        base_url = str(args.base_url)
        verify_json_out = str(args.verify_json_out)
        acceptance_json_out = str(args.acceptance_json_out)
    except AttributeError as exc:
        raise FreezeError("deploy gate strict runtime bindings are required") from exc
    before = verify_deploy_source(args)
    snapshot = Path(str(before["snapshot"])).resolve()
    source = Path(args.source).resolve()
    manifest_path = Path(args.manifest).resolve()
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise FreezeError("deploy gate manifest is invalid") from exc
    source_record = manifest.get("source") if isinstance(manifest, dict) else None
    recorded_repo = source_record.get("repo") if isinstance(source_record, dict) else None
    if not isinstance(recorded_repo, str) or Path(recorded_repo).resolve() != source:
        raise FreezeError("deploy gate source repository binding mismatch")
    build_record = manifest.get("build") if isinstance(manifest, dict) else None
    require_reproducible_frontend = bool(
        isinstance(build_record, dict) and build_record.get("executed") is True
    )
    reproducible_frontend_verified = False
    completed: subprocess.CompletedProcess[bytes] | None = None
    try:
        try:
            with bound_deploy_gate_runtime(
                os.environ,
                source=source,
                requested_python=args.python,
                runtime_root=runtime_root,
                health_url=health_url,
                base_url=base_url,
                verify_json_out=verify_json_out,
                acceptance_json_out=acceptance_json_out,
                allow_test_hooks=bool(getattr(args, "fixture_allow_test_hooks", False)),
            ) as (python_bin, environment):
                for name in GIT_REPOSITORY_BINDING_ENV:
                    environment.pop(name, None)
                build_time = str(manifest["build"]["identity"]["build_time"])
                rebuilt_frontend = (
                        Path(environment["RUNTIME_ROOT"]) / "controller/frontend-dist-rebuild"
                )
                environment.update(
                    {
                        "APP_BUILD_TIME": build_time,
                        "APP_GIT_BRANCH": str(args.expected_branch),
                        "APP_GIT_SHA": str(args.expected_head),
                        "PYTHON_BIN": str(python_bin),
                        "PYTHON_BIN_FALLBACK": str(python_bin),
                        "VITE_APP_BUILD_TIME": build_time,
                        "VITE_APP_GIT_BRANCH": str(args.expected_branch),
                        "VITE_APP_GIT_SHA": str(args.expected_head),
                        "VKPI_VERIFY_FRONTEND_OUT_DIR": str(rebuilt_frontend),
                        "VKPI_VERIFY_REQUIRE_BROWSER_CONSOLE": "0",
                        "VKPI_VERIFY_REQUIRE_CLEAN_WORKTREE": "1",
                        "VKPI_VERIFY_REQUIRE_RUNTIME": "1",
                        "VKPI_VERIFY_REQUIRE_RUNTIME_LOG_CANARY": "0",
                        "VKPI_STRICT_RUN_NONCE": str(getattr(args, "runtime_nonce", "")),
                        "VKPI_STRICT_RUNTIME_PORTS": str(getattr(args, "runtime_ports", "")),
                        "VKPI_STRICT_CANDIDATE_SHA256": str(getattr(args, "candidate_digest", "")),
                    }
                )
                from scripts.ops.trusted_npm_audit import run_trusted_npm_audit
                strict_audit = Path(runtime_root) / "controller/npm-audit.json"
                strict_audit.parent.mkdir(parents=True, exist_ok=True)
                if (snapshot / "frontend/package-lock.json").is_file():
                    run_trusted_npm_audit(snapshot / "frontend", strict_audit)
                    environment["VKPI_TRUSTED_NPM_AUDIT_RECEIPT"] = str(strict_audit)
                from scripts.ops.deploy_gate_runtime import assert_provider_free_environment
                assert_provider_free_environment(environment)
                with _readonly_snapshot_git_environment(
                    snapshot, source, bridge_parent=Path(runtime_root) / "controller",
                ) as git_environment:
                    environment.update(git_environment)
                    with _borrow_dependencies(snapshot, source):
                        from scripts.ops.controlled_candidate_process import run_controlled_candidate
                        completed = run_controlled_candidate(
                            (["/usr/bin/sandbox-exec", "-p", str(args.seatbelt_profile)]
                             if getattr(args, "seatbelt_profile", None) else [])
                            + ["/bin/bash", "scripts/verify.sh"],
                            cwd=snapshot,
                            env=environment,
                            stdin=subprocess.DEVNULL,
                            timeout=1800,
                        )
                if completed.returncode == 0 and require_reproducible_frontend:
                    _assert_frontend_dist_reproducible(
                        snapshot / "frontend" / "dist",
                        rebuilt_frontend,
                    )
                    reproducible_frontend_verified = True
        except (DeployGateRuntimeError, KeyError, TypeError) as exc:
            raise FreezeError(str(exc)) from exc
    finally:
        candidate_postgres_receipts = [{
            "root": runtime_root,
            "status": "controller_registry_cleanup_required",
            "destructive_cleanup_performed": False,
        }]
        after = verify_deploy_source(args)
    if completed is None or completed.returncode != 0:
        code = completed.returncode if completed is not None else "unavailable"
        raise FreezeError(f"candidate canonical deploy gate failed: {code}")
    after["canonical_deploy_gate"] = True
    after["candidate_browser_runtime_postgres"] = candidate_postgres_receipts
    after["frontend_reproducible"] = (
        reproducible_frontend_verified
        if require_reproducible_frontend
        else "not_required_for_unbuilt_fixture"
    )
    return after


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
    deploy_gate = subparsers.add_parser("run-deploy-gate")
    deploy_gate.add_argument("--manifest", required=True)
    deploy_gate.add_argument("--snapshot", required=True)
    deploy_gate.add_argument("--expected-head", required=True)
    deploy_gate.add_argument("--expected-branch", required=True)
    deploy_gate.add_argument("--source", required=True)
    deploy_gate.add_argument("--python", required=True)
    deploy_gate.add_argument("--runtime-root", required=True)
    deploy_gate.add_argument("--health-url", required=True)
    deploy_gate.add_argument("--base-url", required=True)
    deploy_gate.add_argument("--verify-json-out", required=True)
    deploy_gate.add_argument("--acceptance-json-out", required=True)
    deploy_gate.set_defaults(action=run_deploy_gate)
    return result


def main() -> int:
    args = parser().parse_args()
    try:
        payload = args.action(args)
    except (FreezeError, GitBridgeError, OSError, subprocess.SubprocessError) as exc:
        sys.stderr.write(f"candidate freeze failed: {exc}\n")
        return 1
    sys.stdout.write(json.dumps(payload, ensure_ascii=False, sort_keys=True) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
