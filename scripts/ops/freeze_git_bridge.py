#!/usr/bin/env python3
"""Isolated read-only Git identity bridge for frozen snapshot verification."""

from __future__ import annotations

import os
import re
import shlex
import shutil
import sys
import tempfile
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator


GIT_REPOSITORY_BINDING_ENV = (
    "GIT_ALTERNATE_OBJECT_DIRECTORIES",
    "GIT_COMMON_DIR",
    "GIT_DIR",
    "GIT_INDEX_FILE",
    "GIT_OBJECT_DIRECTORY",
    "GIT_OPTIONAL_LOCKS",
    "GIT_WORK_TREE",
)


class GitBridgeError(RuntimeError):
    """Fail-closed error raised while preparing the temporary Git bridge."""


def _readonly_git_wrapper_source(
    *,
    real_git: Path,
    snapshot: Path,
    source: Path,
    python_bin: Path,
) -> str:
    """Return a Git shim that exposes source identity without sharing Git env."""

    readonly_commands = (
        "blame",
        "cat-file",
        "check-attr",
        "check-ignore",
        "describe",
        "diff",
        "diff-index",
        "diff-tree",
        "for-each-ref",
        "grep",
        "log",
        "ls-files",
        "ls-tree",
        "merge-base",
        "name-rev",
        "rev-list",
        "rev-parse",
        "shortlog",
        "show",
        "show-ref",
        "status",
    )
    return f"""#!{python_bin}
from __future__ import annotations

import os
import sys
from pathlib import Path

REAL_GIT = {str(real_git)!r}
SNAPSHOT = Path({str(snapshot)!r}).resolve()
SOURCE = Path({str(source)!r}).resolve()
BINDING_ENV = {GIT_REPOSITORY_BINDING_ENV!r}
READONLY_COMMANDS = frozenset({readonly_commands!r})


def _clean_environment() -> dict[str, str]:
    environment = os.environ.copy()
    for name in BINDING_ENV:
        environment.pop(name, None)
    return environment


def _resolve_from(base: Path, value: str) -> Path:
    if not value:
        return base
    candidate = Path(value)
    if not candidate.is_absolute():
        candidate = base / candidate
    return candidate.resolve()


def _parse_invocation(arguments: list[str]) -> tuple[Path, list[str], bool]:
    target = Path.cwd().resolve()
    unsupported_global = False
    index = 0
    while index < len(arguments):
        item = arguments[index]
        if item == "-C":
            if index + 1 >= len(arguments):
                return target, [], True
            target = _resolve_from(target, arguments[index + 1])
            index += 2
            continue
        if item.startswith("-C") and item != "-C":
            target = _resolve_from(target, item[2:])
            index += 1
            continue
        if item == "--no-optional-locks":
            index += 1
            continue
        if item in {{"--help", "--version"}}:
            return target, [], False
        if item == "--":
            index += 1
            break
        if item.startswith("-"):
            unsupported_global = True
            index += 1
            continue
        break
    return target, arguments[index:], unsupported_global


def _inside(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


def _has_own_git_metadata(target: Path) -> bool:
    cursor = target
    while _inside(cursor, SNAPSHOT):
        marker = cursor / ".git"
        if marker.exists() or marker.is_symlink():
            return True
        if cursor == SNAPSHOT:
            break
        cursor = cursor.parent
    return False


def _read_only(arguments: list[str]) -> bool:
    if not arguments:
        return True
    command, *tail = arguments
    if command in READONLY_COMMANDS:
        if any(
            value == "--output" or value.startswith("--output=")
            for value in tail
        ):
            return False
        return True
    if command == "branch":
        return tail == ["--show-current"] or (
            "--list" in tail
            and not {{
                "--copy",
                "--delete",
                "--edit-description",
                "--force",
                "--move",
                "--set-upstream-to",
                "--unset-upstream",
                "-C",
                "-D",
                "-M",
                "-c",
                "-d",
                "-f",
                "-m",
            }}.intersection(tail)
        )
    if command == "worktree":
        return bool(tail) and tail[0] == "list"
    if command == "remote":
        return tail in ([], ["-v"], ["--verbose"])
    if command == "config":
        write_flags = {{
            "--add",
            "--edit",
            "--remove-section",
            "--rename-section",
            "--replace-all",
            "--unset",
            "--unset-all",
            "-e",
        }}
        read_flags = {{
            "--get",
            "--get-all",
            "--get-regexp",
            "--get-urlmatch",
            "--list",
            "-l",
        }}
        return not write_flags.intersection(tail) and bool(read_flags.intersection(tail))
    return False


def main() -> int:
    arguments = sys.argv[1:]
    target, command, unsupported_global = _parse_invocation(arguments)
    environment = _clean_environment()
    bridge = _inside(target, SNAPSHOT) and not _has_own_git_metadata(target)
    if not bridge:
        os.execve(REAL_GIT, [REAL_GIT, *arguments], environment)
    if unsupported_global or not _read_only(command):
        requested = command[0] if command else "<global-option>"
        sys.stderr.write(
            "candidate Git bridge rejected non-read-only snapshot command: "
            + requested
            + "\\n"
        )
        return 126
    os.execve(
        REAL_GIT,
        [REAL_GIT, "--no-optional-locks", "-C", str(SOURCE), *command],
        environment,
    )
    return 126


if __name__ == "__main__":
    raise SystemExit(main())
"""


def _strict_identity_wrapper_source(
    *, snapshot: Path, head: str, branch: str, python_bin: Path
) -> str:
    """Return a fixed-response Git shim that never opens the controller source."""

    return f"""#!{python_bin}
from __future__ import annotations

import sys
from pathlib import Path

SNAPSHOT = Path({str(snapshot)!r}).resolve()
HEAD = {head!r}
BRANCH = {branch!r}


def _arguments(raw: list[str]) -> list[str] | None:
    arguments = list(raw)
    target = Path.cwd().resolve()
    while arguments and (arguments[0] == "--no-optional-locks" or arguments[0].startswith("-C")):
        item = arguments.pop(0)
        if item == "--no-optional-locks":
            continue
        if item == "-C":
            if not arguments:
                return None
            target = Path(arguments.pop(0)).resolve()
        else:
            target = Path(item[2:]).resolve()
    try:
        target.relative_to(SNAPSHOT)
    except ValueError:
        return None
    return arguments


def main() -> int:
    arguments = _arguments(sys.argv[1:])
    if arguments in (["rev-parse", "HEAD"], ["rev-parse", "--verify", "HEAD"]):
        print(HEAD)
        return 0
    if arguments == ["rev-parse", "--show-toplevel"]:
        print(SNAPSHOT)
        return 0
    if arguments in (["branch", "--show-current"], ["rev-parse", "--abbrev-ref", "HEAD"]):
        print(BRANCH)
        return 0
    if arguments in (
        ["status", "--porcelain=v1", "--untracked-files=all"],
        ["status", "--porcelain=v1", "-z", "--untracked-files=all"],
    ):
        return 0
    requested = arguments[0] if arguments else "<invalid>"
    sys.stderr.write("strict candidate Git identity rejected command: " + requested + "\\n")
    return 126


if __name__ == "__main__":
    raise SystemExit(main())
"""


@contextmanager
def readonly_snapshot_git_environment(
    snapshot: Path,
    source: Path,
    *, bridge_parent: Path | None = None,
    python_bin: Path | None = None,
) -> Iterator[dict[str, str]]:
    """Install a temporary PATH shim for snapshot-only read access to source Git."""

    from scripts.ops.trusted_git import (
        trusted_git_executable,
        trusted_python_executable,
    )

    try:
        real_git = Path(trusted_git_executable())
        physical_python = Path(
            trusted_python_executable(python_bin or Path(sys.executable))
        )
    except RuntimeError as exc:
        raise GitBridgeError(str(exc)) from exc

    if bridge_parent is not None:
        bridge_parent.mkdir(parents=True, exist_ok=True)
    bridge_root = Path(tempfile.mkdtemp(prefix="vkpi-freeze-git-bridge.", dir=bridge_parent))
    wrapper = bridge_root / "git"
    try:
        from scripts.ops.trusted_npm_audit import _trusted_node, _trusted_npm, _trusted_npx
        node, npm_cli = _trusted_node(), _trusted_npm()
        npx_cli = _trusted_npx(npm_cli)
        wrapper.write_text(
            _readonly_git_wrapper_source(
                real_git=real_git,
                snapshot=snapshot,
                source=source,
                python_bin=physical_python,
            ),
            encoding="utf-8",
        )
        os.chmod(wrapper, 0o500)
        for name, cli in (("npm", npm_cli), ("npx", npx_cli)):
            tool = bridge_root / name
            tool.write_text(f'#!/bin/sh\nexec {node} {cli} "$@"\n', encoding="utf-8")
            os.chmod(tool, 0o500)
        node_tool = bridge_root / "node"
        node_tool.write_text(f'#!/bin/sh\nexec {node} "$@"\n', encoding="utf-8")
        os.chmod(node_tool, 0o500)
        for name in ("python", "python3"):
            tool = bridge_root / name
            tool.write_text(
                "#!/bin/sh\nexec "
                + shlex.quote(str(physical_python))
                + ' "$@"\n',
                encoding="utf-8",
            )
            os.chmod(tool, 0o500)
        yield {
            "PATH": str(bridge_root) + os.pathsep + "/usr/bin:/bin",
            "VKPI_FREEZE_GIT_BRIDGE": "readonly-path-wrapper",
            "VKPI_FREEZE_GIT_WRAPPER": str(wrapper),
            "VKPI_FREEZE_REAL_GIT": str(real_git),
            "VKPI_FREEZE_REAL_PYTHON": str(physical_python),
        }
    finally:
        shutil.rmtree(bridge_root, ignore_errors=True)


@contextmanager
def strict_snapshot_identity_environment(
    snapshot: Path,
    *,
    expected_head: str,
    expected_branch: str,
    bridge_parent: Path,
    python_bin: Path,
) -> Iterator[dict[str, str]]:
    """Expose only the immutable identity queries needed by Phase B."""

    from scripts.ops.trusted_git import trusted_python_executable
    from scripts.ops.trusted_npm_audit import _trusted_node, _trusted_npm, _trusted_npx

    if not re.fullmatch(r"[0-9a-f]{40}", expected_head):
        raise GitBridgeError("strict snapshot Git head is invalid")
    if not expected_branch or any(character in expected_branch for character in "\r\n\0"):
        raise GitBridgeError("strict snapshot Git branch is invalid")
    try:
        physical_python = Path(trusted_python_executable(python_bin))
    except RuntimeError as exc:
        raise GitBridgeError(str(exc)) from exc
    bridge_parent.mkdir(parents=True, exist_ok=True)
    bridge_root = Path(
        tempfile.mkdtemp(prefix="vkpi-strict-git-identity.", dir=bridge_parent)
    )
    wrapper = bridge_root / "git"
    try:
        node, npm_cli = _trusted_node(), _trusted_npm()
        npx_cli = _trusted_npx(npm_cli)
        wrapper.write_text(
            _strict_identity_wrapper_source(
                snapshot=snapshot,
                head=expected_head,
                branch=expected_branch,
                python_bin=physical_python,
            ),
            encoding="utf-8",
        )
        os.chmod(wrapper, 0o500)
        for name, cli in (("npm", npm_cli), ("npx", npx_cli)):
            tool = bridge_root / name
            tool.write_text(
                f"#!/bin/sh\nexec {shlex.quote(str(node))} "
                f"{shlex.quote(str(cli))} \"$@\"\n",
                encoding="utf-8",
            )
            os.chmod(tool, 0o500)
        node_tool = bridge_root / "node"
        node_tool.write_text(
            f"#!/bin/sh\nexec {shlex.quote(str(node))} \"$@\"\n",
            encoding="utf-8",
        )
        os.chmod(node_tool, 0o500)
        for name in ("python", "python3"):
            tool = bridge_root / name
            tool.write_text(
                "#!/bin/sh\nexec "
                + shlex.quote(str(physical_python))
                + ' "$@"\n',
                encoding="utf-8",
            )
            os.chmod(tool, 0o500)
        yield {
            "PATH": str(bridge_root) + os.pathsep + "/usr/bin:/bin",
            "VKPI_FREEZE_GIT_BRIDGE": "strict-fixed-identity-wrapper",
            "VKPI_FREEZE_GIT_WRAPPER": str(wrapper),
            "VKPI_FREEZE_REAL_PYTHON": str(physical_python),
        }
    finally:
        shutil.rmtree(bridge_root, ignore_errors=True)
