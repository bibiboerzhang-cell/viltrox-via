#!/usr/bin/env python3
"""Controller-trusted Git executable and env-i contract."""

from __future__ import annotations

import os
import platform
import stat
import subprocess
from pathlib import Path
from typing import Mapping


GIT = Path("/usr/bin/git")
XCRUN = Path("/usr/bin/xcrun")


def _trusted_executable(path: Path, *, label: str) -> Path:
    try:
        resolved = path.resolve(strict=True)
        info = resolved.stat()
    except OSError as exc:
        raise RuntimeError(f"controller {label} executable is unavailable") from exc
    if (
        not stat.S_ISREG(info.st_mode)
        or info.st_uid not in {0, os.geteuid()}
        or stat.S_IMODE(info.st_mode) & 0o022
        or not os.access(resolved, os.X_OK)
    ):
        raise RuntimeError(f"controller {label} executable is not trusted")
    return resolved


def _darwin_developer_git() -> Path:
    """Resolve the physical developer Git before entering Seatbelt.

    ``/usr/bin/git`` is an Apple developer-tool shim.  Inside a deny-default
    Seatbelt it can fail through ``xcode-select`` even when the installed Git
    itself is healthy.  Resolve the physical executable in the controller,
    then validate and execute that exact file in child environments.
    """

    xcrun = _trusted_executable(XCRUN, label="xcrun")
    try:
        completed = subprocess.run(
            [str(xcrun), "--find", "git"],
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
            env={
                "HOME": "/tmp",
                "LANG": "C",
                "LC_ALL": "C",
                "PATH": "/usr/bin:/bin",
            },
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise RuntimeError("controller developer Git lookup failed") from exc
    raw = completed.stdout.strip()
    if (
        completed.returncode != 0
        or not raw
        or "\n" in raw
        or not Path(raw).is_absolute()
    ):
        raise RuntimeError("controller developer Git lookup failed")
    physical = _trusted_executable(Path(raw), label="Git")
    if physical == GIT:
        raise RuntimeError("controller developer Git resolved to the Apple shim")
    return physical


def trusted_git_executable() -> str:
    candidate = _darwin_developer_git() if platform.system() == "Darwin" else GIT
    return str(_trusted_executable(candidate, label="Git"))


def trusted_python_executable(path: str | os.PathLike[str]) -> str:
    """Return one physical, immutable-enough controller Python executable."""

    return str(_trusted_executable(Path(path), label="Python"))


def git_env(explicit: Mapping[str, str] | None = None) -> dict[str, str]:
    environment = {"HOME": "/tmp", "LANG": "C", "LC_ALL": "C",
                   "PATH": "/usr/bin:/bin", "GIT_OPTIONAL_LOCKS": "0",
                   "GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": "/dev/null"}
    if explicit:
        environment.update({name: value for name, value in explicit.items()
                            if name.startswith("GIT_") and name not in {
                                "GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE",
                                "GIT_OBJECT_DIRECTORY", "GIT_ALTERNATE_OBJECT_DIRECTORIES",
                            }})
    return environment
