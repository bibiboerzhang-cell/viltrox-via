#!/usr/bin/env python3
"""Isolated read-only Git identity bridge for frozen snapshot verification."""

from __future__ import annotations

import os
import shutil
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
    return f"""#!/usr/bin/env python3
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


@contextmanager
def readonly_snapshot_git_environment(
    snapshot: Path,
    source: Path,
) -> Iterator[dict[str, str]]:
    """Install a temporary PATH shim for snapshot-only read access to source Git."""

    raw_real_git = shutil.which("git")
    if not raw_real_git:
        raise GitBridgeError("Git executable is unavailable for snapshot verification")
    real_git = Path(raw_real_git).resolve()
    if not real_git.is_file() or not os.access(real_git, os.X_OK):
        raise GitBridgeError("Git executable is unsafe for snapshot verification")

    bridge_root = Path(tempfile.mkdtemp(prefix="vkpi-freeze-git-bridge."))
    wrapper = bridge_root / "git"
    try:
        wrapper.write_text(
            _readonly_git_wrapper_source(
                real_git=real_git,
                snapshot=snapshot,
                source=source,
            ),
            encoding="utf-8",
        )
        os.chmod(wrapper, 0o500)
        yield {
            "PATH": str(bridge_root)
            + os.pathsep
            + os.environ.get("PATH", os.defpath),
            "VKPI_FREEZE_GIT_BRIDGE": "readonly-path-wrapper",
        }
    finally:
        shutil.rmtree(bridge_root, ignore_errors=True)
