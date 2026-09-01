#!/usr/bin/env python3
"""Build a clean, content-bound candidate from a dirty local worktree.

Phase A only uses the filesystem and temporary Git. It starts no services,
performs no runtime acceptance, and never mutates the source repository. A
retained dirty-source capsule is bridged through a private clean Git identity.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import stat
import subprocess
import sys
import tempfile
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Iterator, Mapping, Sequence

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from scripts.ops.freeze_git_bridge import GitBridgeError  # noqa: E402
from scripts.ops.candidate_physical_tree import assert_candidate_physical_tree_bound, manifest_files_excluding as _capsule_files_without_stamps  # noqa: E402
from scripts.ops.freeze_worktree_candidate import (  # noqa: E402
    _atomic_json,
    _inventory_candidate,
    _sha256_path,
    freeze_candidate,
    verify_deploy_source,
    verify_manifest,
)
from scripts.ops.freeze_worktree_contract import (  # noqa: E402
    FreezeError, cleanup_owned_paths, path_identity, write_owned_file_exclusive,
)


SCHEMA = "vkpi.isolated-clean-content-candidate/v1"
CLASSIFICATION = "clean_content_candidate_not_runtime_acceptance"
TEMPORARY_PARENT = Path("/tmp")
TEMPORARY_PREFIX = "vkpi-isolated-worktree-gate."
GENERATED_BUILD_STAMPS = frozenset({"BUILD_GIT_SHA", "BUILD_GIT_BRANCH", "BUILD_TIME"})


class IsolatedWorktreeGateError(RuntimeError):
    """Fail-closed Phase-A candidate construction error."""


@dataclass(frozen=True)
class SourceState:
    head: str
    branch: str
    status_sha256: str
    status_bytes: int
    worktree_dirty: bool
    content_sha256: str
    content_file_count: int
    index_path: str
    index_sha256: str
    index_bytes: int
    shared_index_path: str | None
    shared_index_sha256: str | None
    shared_index_bytes: int | None

    def payload(self) -> dict[str, object]:
        return {
            "branch": self.branch,
            "head": self.head,
            "content_sha256": self.content_sha256,
            "content_file_count": self.content_file_count,
            "index": {
                "bytes": self.index_bytes,
                "path": self.index_path,
                "sha256": self.index_sha256,
            },
            "shared_index": (
                {
                    "bytes": self.shared_index_bytes,
                    "path": self.shared_index_path,
                    "sha256": self.shared_index_sha256,
                }
                if self.shared_index_path is not None
                else None
            ),
            "status_bytes": self.status_bytes,
            "status_sha256": self.status_sha256,
            "worktree_dirty": self.worktree_dirty,
        }


def _canonical_bytes(value: object) -> bytes:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _clean_git_environment(inherited: Mapping[str, str], *, home: Path) -> dict[str, str]:
    environment = {
        name: value
        for name, value in inherited.items()
        if not name.startswith("GIT_")
        and name not in {"CDPATH", "ENV", "BASH_ENV", "PYTHONPATH", "PYTHONHOME"}
    }
    environment.update(
        {
            "GIT_CONFIG_GLOBAL": os.devnull,
            "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_CONFIG_SYSTEM": os.devnull,
            "GIT_TERMINAL_PROMPT": "0",
            "HOME": str(home),
            "LC_ALL": "C",
            "LANG": "C",
        }
    )
    return environment


def _run_git(
    root: Path,
    *arguments: str,
    environment: Mapping[str, str],
    allowed_returncodes: Sequence[int] = (0,),
) -> subprocess.CompletedProcess[bytes]:
    from scripts.ops.trusted_git import git_env, trusted_git_executable
    completed = subprocess.run(
        [trusted_git_executable(), *arguments],
        cwd=root,
        env=git_env(environment),
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    if completed.returncode not in allowed_returncodes:
        detail = completed.stderr.decode("utf-8", "replace").strip()[:400]
        raise IsolatedWorktreeGateError(
            f"git {' '.join(arguments)} failed with {completed.returncode}: {detail}"
        )
    return completed


def _source_git(root: Path, *arguments: str) -> bytes:
    from scripts.ops.trusted_git import git_env, trusted_git_executable
    completed = subprocess.run(
        [trusted_git_executable(), "--no-optional-locks", "-c", "core.fsmonitor=false", "-c",
         f"core.hooksPath={os.devnull}", *arguments],
        cwd=root,
        env=git_env(),
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    if completed.returncode != 0:
        detail = completed.stderr.decode("utf-8", "replace").strip()[:400]
        raise IsolatedWorktreeGateError(
            f"source git {' '.join(arguments)} failed with "
            f"{completed.returncode}: {detail}"
        )
    return completed.stdout


def _source_git_text(root: Path, *arguments: str) -> str:
    return _source_git(root, *arguments).decode("utf-8", "strict").strip()


def _resolve_git_path(source: Path, raw: str) -> Path:
    candidate = Path(raw)
    if not candidate.is_absolute():
        candidate = source / candidate
    return Path(os.path.abspath(os.fspath(candidate)))


def _regular_file_fingerprint(path: Path) -> tuple[str, int]:
    try:
        before = path.lstat()
    except OSError as exc:
        raise IsolatedWorktreeGateError(f"Git index is unavailable: {path}") from exc
    if path.is_symlink() or not stat.S_ISREG(before.st_mode):
        raise IsolatedWorktreeGateError(f"Git index is not a regular file: {path}")
    data = path.read_bytes()
    after = path.lstat()
    if (
        (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns)
        != (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns)
        or len(data) != before.st_size
    ):
        raise IsolatedWorktreeGateError(f"Git index changed while reading: {path}")
    return _sha256_bytes(data), len(data)


def _index_state(
    source: Path,
) -> tuple[str, str, int, str | None, str | None, int | None]:
    index_path = _resolve_git_path(
        source, _source_git_text(source, "rev-parse", "--git-path", "index")
    )
    index_sha, index_size = _regular_file_fingerprint(index_path)
    shared_raw = _source_git_text(source, "rev-parse", "--shared-index-path")
    if not shared_raw:
        return str(index_path), index_sha, index_size, None, None, None
    shared_path = _resolve_git_path(source, shared_raw)
    shared_sha, shared_size = _regular_file_fingerprint(shared_path)
    return (
        str(index_path),
        index_sha,
        index_size,
        str(shared_path),
        shared_sha,
        shared_size,
    )


def _capture_source_state(source: Path) -> SourceState:
    from scripts.ops.freeze_worktree_candidate import _inventory_digest, _inventory_source
    index_before = _index_state(source)
    content_before = _inventory_source(source)
    head = _source_git_text(source, "rev-parse", "HEAD")
    branch = _source_git_text(source, "branch", "--show-current") or _source_git_text(
        source, "rev-parse", "--abbrev-ref", "HEAD"
    )
    status = _source_git(
        source, "status", "--porcelain=v1", "-z", "--untracked-files=all"
    )
    index_after = _index_state(source)
    content_after = _inventory_source(source)
    if index_after != index_before or content_after != content_before:
        raise IsolatedWorktreeGateError("source Git status inspection changed the index")
    return SourceState(
        head=head,
        branch=branch,
        status_sha256=_sha256_bytes(status),
        status_bytes=len(status),
        worktree_dirty=bool(status),
        content_sha256=_inventory_digest(content_before),
        content_file_count=len(content_before),
        index_path=index_before[0],
        index_sha256=index_before[1],
        index_bytes=index_before[2],
        shared_index_path=index_before[3],
        shared_index_sha256=index_before[4],
        shared_index_bytes=index_before[5],
    )


def _assert_source_unchanged(
    source: Path, expected: SourceState, *, phase: str
) -> SourceState:
    observed = _capture_source_state(source)
    if observed != expected:
        raise IsolatedWorktreeGateError(
            f"source HEAD, branch, index, status, or content bytes changed during {phase}"
        )
    return observed


@contextmanager
def _freeze_source_read_environment() -> Iterator[None]:
    names = {
        name
        for name in os.environ
        if name == "GIT_OPTIONAL_LOCKS"
        or name == "GIT_CONFIG_COUNT"
        or name.startswith("GIT_CONFIG_KEY_")
        or name.startswith("GIT_CONFIG_VALUE_")
    }
    previous = {name: os.environ[name] for name in names}
    for name in names:
        os.environ.pop(name, None)
    os.environ.update(
        {
            "GIT_CONFIG_COUNT": "2",
            "GIT_CONFIG_KEY_0": "core.fsmonitor",
            "GIT_CONFIG_VALUE_0": "false",
            "GIT_CONFIG_KEY_1": "core.hooksPath",
            "GIT_CONFIG_VALUE_1": os.devnull,
            "GIT_OPTIONAL_LOCKS": "0",
        }
    )
    try:
        yield
    finally:
        for name in tuple(os.environ):
            if (
                name == "GIT_OPTIONAL_LOCKS"
                or name == "GIT_CONFIG_COUNT"
                or name.startswith("GIT_CONFIG_KEY_")
                or name.startswith("GIT_CONFIG_VALUE_")
            ):
                os.environ.pop(name, None)
        os.environ.update(previous)


def _freeze_paths(snapshot: Path) -> tuple[Path, ...]:
    return (
        snapshot,
        snapshot.with_suffix(snapshot.suffix + ".tar"),
        snapshot.with_suffix(snapshot.suffix + ".manifest.json"),
        snapshot.with_suffix(snapshot.suffix + ".manifest.json.sha256"),
        snapshot.with_suffix(snapshot.suffix + ".build.log"),
        snapshot.with_suffix(snapshot.suffix + ".verify.log"),
        snapshot.with_suffix(snapshot.suffix + ".static-receipt.json"),
    )

def _planned_artifacts(output: Path, capsule: Path, receipt: Path) -> tuple[Path, ...]:
    paths = (
        *_freeze_paths(output), *_freeze_paths(capsule), receipt,
        receipt.with_suffix(receipt.suffix + ".sha256"),
    )
    if len(set(paths)) != len(paths):
        raise IsolatedWorktreeGateError("candidate artifact paths overlap")
    return paths


def _require_artifact_paths_available(paths: Sequence[Path]) -> None:
    for path in paths:
        if path.exists() or path.is_symlink():
            raise IsolatedWorktreeGateError(f"artifact path already exists: {path}")


def _capture_created_artifacts(
    paths: Sequence[Path], identities: dict[Path, tuple[int, int]],
) -> None:
    for path in paths:
        if (path.exists() or path.is_symlink()) and path not in identities:
            identities[path] = path_identity(path)


def _new_private_root() -> Path:
    raw = tempfile.mkdtemp(prefix=TEMPORARY_PREFIX, dir=TEMPORARY_PARENT)
    root = Path(raw)
    initial = root.lstat(); identity = (initial.st_dev, initial.st_ino)
    try:
        os.chown(root, os.geteuid(), os.getegid())
        root.chmod(0o700)
        info = root.lstat()
    except OSError as exc:
        try:
            current = root.lstat()
            if not root.is_symlink() and (current.st_dev, current.st_ino) == identity:
                shutil.rmtree(root)
        except FileNotFoundError:
            pass
        raise IsolatedWorktreeGateError(
            "temporary candidate root identity is unsafe"
        ) from exc
    if (
        root.parent != TEMPORARY_PARENT
        or not root.name.startswith(TEMPORARY_PREFIX)
        or root.is_symlink()
        or not stat.S_ISDIR(info.st_mode)
        or info.st_uid != os.geteuid()
        or stat.S_IMODE(info.st_mode) != 0o700
    ):
        if (info.st_dev, info.st_ino) == identity and not root.is_symlink(): shutil.rmtree(root)
        raise IsolatedWorktreeGateError("temporary candidate root is unsafe")
    return root


def _cleanup_private_root(root: Path, identity: tuple[int, int]) -> dict[str, object]:
    receipt: dict[str, object] = {"root": str(root)}
    if not root.exists() and not root.is_symlink():
        return {**receipt, "removed": True, "status": "already_absent"}
    try:
        info = root.lstat()
    except OSError as exc:
        raise IsolatedWorktreeGateError(
            "temporary candidate root cannot be inspected for cleanup"
        ) from exc
    if (
        root.parent != TEMPORARY_PARENT
        or not root.name.startswith(TEMPORARY_PREFIX)
        or root.is_symlink()
        or not stat.S_ISDIR(info.st_mode)
        or info.st_uid != os.geteuid()
        or (info.st_dev, info.st_ino) != identity
    ):
        raise IsolatedWorktreeGateError(
            "refusing to clean an unowned or unexpected temporary root"
        )
    shutil.rmtree(root)
    if root.exists() or root.is_symlink():
        raise IsolatedWorktreeGateError("temporary candidate root cleanup failed")
    return {**receipt, "removed": True, "status": "removed"}


def _prepare_clean_mirror(
    *,
    source: Path,
    capsule: Path,
    capsule_payload: Mapping[str, object],
    temporary_root: Path,
) -> tuple[Path, dict[str, object]]:
    mirror = temporary_root / "clean-source"
    candidate_record = capsule_payload.get("candidate")
    expected_physical_files = (
        candidate_record.get("files")
        if isinstance(candidate_record, Mapping)
        else None
    )
    assert_candidate_physical_tree_bound(capsule, expected_physical_files)
    shutil.copytree(
        capsule,
        mirror,
        copy_function=shutil.copy2,
        symlinks=True,
    )
    assert_candidate_physical_tree_bound(mirror, expected_physical_files)
    mirror.chmod(0o700)
    for name in GENERATED_BUILD_STAMPS:
        (mirror / name).unlink(missing_ok=True)

    expected_files = _capsule_files_without_stamps(
        capsule_payload, GENERATED_BUILD_STAMPS
    )
    assert_candidate_physical_tree_bound(mirror, expected_files)
    observed_entries = _inventory_candidate(mirror)
    observed_files = [entry.payload() for entry in observed_entries]
    if observed_files != expected_files:
        raise IsolatedWorktreeGateError(
            "temporary mirror bytes do not match the dirty-source capsule"
        )

    git_home = temporary_root / "git-home"
    hooks = temporary_root / "no-hooks"
    template = temporary_root / "empty-git-template"
    for directory in (git_home, hooks, template):
        directory.mkdir(mode=0o700)
    environment = _clean_git_environment(os.environ, home=git_home)

    source_record = capsule_payload.get("source")
    if not isinstance(candidate_record, Mapping) or not isinstance(source_record, Mapping):
        raise IsolatedWorktreeGateError("dirty-source capsule identity is missing")
    content_sha = str(source_record.get("content_sha256", ""))
    if len(content_sha) != 64:
        raise IsolatedWorktreeGateError("dirty-source capsule content digest is invalid")
    branch = f"codex/isolated-{content_sha[:16]}"
    _run_git(mirror, "init", "-q", f"--template={template}", environment=environment)
    _run_git(mirror, "check-ref-format", "--branch", branch, environment=environment)
    _run_git(mirror, "symbolic-ref", "HEAD", f"refs/heads/{branch}",
             environment=environment)
    for key, value in (
        ("user.name", "V-KPI Isolated Candidate"),
        ("user.email", "isolated-candidate@invalid.local"),
        ("commit.gpgSign", "false"),
        ("tag.gpgSign", "false"),
        ("core.filemode", "true"),
        ("core.hooksPath", str(hooks)),
    ):
        _run_git(mirror, "config", "--local", key, value, environment=environment)
    physical_hooks = mirror / ".git" / "hooks"
    if physical_hooks.exists():
        shutil.rmtree(physical_hooks)
    physical_hooks.mkdir(mode=0o700)
    exclude = mirror / ".git" / "info" / "exclude"
    exclude.parent.mkdir(parents=True, exist_ok=True)
    exclude.write_text(".venv/\nfrontend/node_modules/\n", encoding="utf-8")

    _run_git(mirror, "add", "-f", "--all", environment=environment)
    tracked = _run_git(mirror, "ls-files", "-z", environment=environment).stdout.split(b"\0")
    tracked_paths = sorted(
        item.decode("utf-8", "strict") for item in tracked if item
    )
    expected_paths = sorted(str(item["path"]) for item in expected_files)
    if tracked_paths != expected_paths:
        raise IsolatedWorktreeGateError(
            "temporary Git mirror did not stage the complete capsule inventory"
        )

    commit_epoch = _source_git_text(source, "show", "-s", "--format=%ct", "HEAD")
    if not commit_epoch.isdigit():
        raise IsolatedWorktreeGateError("source commit timestamp is invalid")
    commit_environment = dict(environment)
    commit_environment.update(
        {
            "GIT_AUTHOR_DATE": f"@{commit_epoch} +0000",
            "GIT_AUTHOR_EMAIL": "isolated-candidate@invalid.local",
            "GIT_AUTHOR_NAME": "V-KPI Isolated Candidate",
            "GIT_COMMITTER_DATE": f"@{commit_epoch} +0000",
            "GIT_COMMITTER_EMAIL": "isolated-candidate@invalid.local",
            "GIT_COMMITTER_NAME": "V-KPI Isolated Candidate",
        }
    )
    _run_git(
        mirror,
        "commit",
        "-q",
        "--no-gpg-sign",
        "--no-verify",
        "-m",
        f"V-KPI isolated content {content_sha}",
        environment=commit_environment,
    )

    head = _run_git(mirror, "rev-parse", "HEAD", environment=environment).stdout.decode(
        "ascii"
    ).strip()
    tree = _run_git(mirror, "rev-parse", "HEAD^{tree}", environment=environment).stdout.decode(
        "ascii"
    ).strip()
    status = _run_git(
        mirror,
        "status",
        "--porcelain=v1",
        "-z",
        "--untracked-files=all",
        environment=environment,
    ).stdout
    remotes = _run_git(mirror, "remote", "-v", environment=environment).stdout
    remote_config = _run_git(
        mirror,
        "config",
        "--local",
        "--get-regexp",
        r"^remote\.",
        environment=environment,
        allowed_returncodes=(0, 1),
    )
    configured_hooks = _run_git(
        mirror,
        "config",
        "--local",
        "--get",
        "core.hooksPath",
        environment=environment,
    ).stdout.decode("utf-8", "strict").strip()
    if (
        status
        or remotes
        or remote_config.returncode != 1
        or configured_hooks != str(hooks)
        or any(hooks.iterdir())
        or any(physical_hooks.iterdir())
    ):
        raise IsolatedWorktreeGateError(
            "temporary Git mirror is not clean, hook-free, and remote-free"
        )
    bridge = {
        "branch": branch,
        "capsule_content_bridge_sha256": _sha256_bytes(_canonical_bytes(expected_files)),
        "capsule_file_count_without_generated_stamps": len(expected_files),
        "commit_created_only_in_temporary_repository": True,
        "configured_hooks_path": str(hooks),
        "git_head": head,
        "git_tree": tree,
        "hooks_empty": True,
        "mirror_mode": format(stat.S_IMODE(mirror.stat().st_mode), "04o"),
        "mirror_path": str(mirror),
        "remote_count": 0,
        "status_clean": True,
    }
    return mirror, bridge


@contextmanager
def _borrow_source_dependencies(
    mirror: Path, source: Path, *, required: bool
) -> Iterator[None]:
    links: list[Path] = []
    if required:
        for link, target in (
            (mirror / ".venv", source / ".venv"),
            (mirror / "frontend" / "node_modules", source / "frontend" / "node_modules"),
        ):
            if not target.is_dir():
                raise IsolatedWorktreeGateError(
                    f"required local dependency is missing: {target}"
                )
            link.parent.mkdir(parents=True, exist_ok=True)
            link.symlink_to(target, target_is_directory=True)
            links.append(link)
    try:
        yield
    finally:
        for link in reversed(links):
            link.unlink(missing_ok=True)


def _freeze_arguments(
    *, repo: Path, output: Path, skip_build: bool, skip_verify: bool,
    skip_archive: bool,
) -> argparse.Namespace:
    return argparse.Namespace(
        repo=str(repo),
        output=str(output),
        skip_build=skip_build,
        skip_verify=skip_verify,
        skip_archive=skip_archive,
    )


def _manifest_summary(snapshot: Path, payload: Mapping[str, object]) -> dict[str, object]:
    manifest = snapshot.with_suffix(snapshot.suffix + ".manifest.json")
    candidate = payload.get("candidate")
    source = payload.get("source")
    if not isinstance(candidate, Mapping) or not isinstance(source, Mapping):
        raise IsolatedWorktreeGateError("candidate manifest summary is unavailable")
    return {
        "candidate_content_sha256": candidate.get("content_sha256"),
        "candidate_file_count": candidate.get("file_count"),
        "manifest_path": str(manifest),
        "manifest_sha256": _sha256_path(manifest),
        "snapshot_path": str(snapshot),
        "source_branch": source.get("branch"),
        "source_content_sha256": source.get("content_sha256"),
        "source_head": source.get("head"),
        "source_status_sha256": source.get("status_sha256"),
        "source_worktree_dirty": source.get("worktree_dirty"),
    }


def run_phase_a(args: argparse.Namespace) -> dict[str, object]:
    source = Path(args.source).resolve()
    output = Path(args.output).resolve()
    if not (source / ".git").exists():
        raise IsolatedWorktreeGateError(f"source is not a Git worktree: {source}")
    if output.suffix:
        raise IsolatedWorktreeGateError(
            "candidate output directory name must not contain a suffix"
        )
    if output == source or source in output.parents:
        relative_output = output.relative_to(source)
        if not relative_output.parts or relative_output.parts[0] != "runtime":
            raise IsolatedWorktreeGateError(
                "in-repository candidate output must live under runtime/"
            )
    capsule = output.with_name(output.name + "-source-capsule")
    receipt = Path(args.receipt or output.with_name(output.name + ".provenance.json")).resolve()
    if receipt.parent != output.parent:
        raise IsolatedWorktreeGateError(
            "provenance receipt must be adjacent to the candidate"
        )
    output.parent.mkdir(parents=True, exist_ok=True)
    artifacts = _planned_artifacts(output, capsule, receipt)
    _require_artifact_paths_available(artifacts)

    initial_state = _capture_source_state(source)
    temporary_root: Path | None = None
    temporary_root_identity: tuple[int, int] | None = None
    cleanup: dict[str, object] | None = None
    success = False
    artifact_identities: dict[Path, tuple[int, int]] = {}
    try:
        with _freeze_source_read_environment():
            capsule_payload = freeze_candidate(
                _freeze_arguments(
                    repo=source,
                    output=capsule,
                    skip_build=True,
                    skip_verify=True,
                    skip_archive=True,
                )
            )
            _capture_created_artifacts(_freeze_paths(capsule), artifact_identities)
            capsule_manifest = capsule.with_suffix(capsule.suffix + ".manifest.json")
            verify_manifest(
                argparse.Namespace(
                    manifest=str(capsule_manifest), snapshot=str(capsule)
                )
            )
            _assert_source_unchanged(
                source, initial_state, phase="dirty-source capsule freeze"
            )

            temporary_root = _new_private_root()
            root_info = temporary_root.lstat()
            temporary_root_identity = (root_info.st_dev, root_info.st_ino)
            mirror, bridge = _prepare_clean_mirror(
                source=source,
                capsule=capsule,
                capsule_payload=capsule_payload,
                temporary_root=temporary_root,
            )
            with _borrow_source_dependencies(
                mirror,
                source,
                required=not (args.skip_build and args.skip_verify),
            ):
                candidate_payload = freeze_candidate(
                    _freeze_arguments(
                        repo=mirror,
                        output=output,
                        skip_build=args.skip_build,
                        skip_verify=args.skip_verify,
                        skip_archive=args.skip_archive,
                    )
                )
            _capture_created_artifacts(_freeze_paths(output), artifact_identities)
            candidate_manifest = output.with_suffix(output.suffix + ".manifest.json")
            verify_manifest(
                argparse.Namespace(
                    manifest=str(candidate_manifest), snapshot=str(output)
                )
            )
            verify_deploy_source(
                argparse.Namespace(
                    manifest=str(candidate_manifest),
                    snapshot=str(output),
                    expected_head=str(bridge["git_head"]),
                    expected_branch=str(bridge["branch"]),
                )
            )
            _assert_source_unchanged(
                source, initial_state, phase="clean candidate freeze"
            )

        if temporary_root is None:
            raise IsolatedWorktreeGateError("temporary mirror was not created")
        if temporary_root_identity is None:
            raise IsolatedWorktreeGateError("temporary root identity is unavailable")
        cleanup = _cleanup_private_root(temporary_root, temporary_root_identity)
        final_state = _assert_source_unchanged(
            source, initial_state, phase="temporary mirror cleanup"
        )
        payload: dict[str, object] = {
            "candidate": {
                **_manifest_summary(output, candidate_payload),
                "clean_deploy_source_contract_verified": True,
            },
            "classification": CLASSIFICATION,
            "created_at": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
            "dirty_source_capsule": _manifest_summary(capsule, capsule_payload),
            "provenance_bridge": bridge,
            "runtime_acceptance": {
                "attempted": False,
                "classification": "not_run_phase_a_only",
                "database_started": False,
                "provider_called": False,
                "redis_started": False,
                "web_started": False,
                "worker_started": False,
            },
            "safety": {
                "provider_network_contact": "not_observed_not_os_enforced",
                "external_registry_network": "npm_audit_may_attempt",
                "deployment_performed": False,
                "persistent_business_write_performed": False,
                "source_branch_changed": False,
                "source_commit_created": False,
                "source_stage_performed": False,
                "synthetic_commit_created_in_temporary_repository": True,
            },
            "schema": SCHEMA,
            "source_integrity": {
                "after": final_state.payload(),
                "before": initial_state.payload(),
                "head_branch_index_status_unchanged": final_state == initial_state,
            },
            "temporary_cleanup": cleanup,
        }
        artifact_identities[receipt] = _atomic_json(receipt, payload)
        receipt_sha = _sha256_path(receipt)
        sidecar = receipt.with_suffix(receipt.suffix + ".sha256")
        artifact_identities[sidecar] = write_owned_file_exclusive(
            sidecar, f"{receipt_sha}  {receipt.name}\n".encode()
        )
        success = True
        return payload
    finally:
        cleanup_error: Exception | None = None
        try:
            if temporary_root is not None and (
                temporary_root.exists() or temporary_root.is_symlink()
            ):
                if temporary_root_identity is None:
                    raise IsolatedWorktreeGateError("temporary root identity is unavailable")
                _cleanup_private_root(temporary_root, temporary_root_identity)
        except (IsolatedWorktreeGateError, OSError) as exc:
            cleanup_error = exc
        if not success:
            cleanup_owned_paths(artifact_identities)
        _assert_source_unchanged(source, initial_state, phase="Phase A finalization")
        if cleanup_error is not None:
            raise cleanup_error


def parser() -> argparse.ArgumentParser:
    from scripts.ops.isolated_worktree_gate_cli import parser as build_parser

    return build_parser(default_source=Path(__file__).resolve().parents[2])


def main() -> int:
    from scripts.ops.isolated_worktree_gate_cli import main as cli_main

    return cli_main(run_phase_a=run_phase_a)


if __name__ == "__main__":
    raise SystemExit(main())
