#!/usr/bin/env python3
"""Controller-trusted Git executable and env-i contract."""

from __future__ import annotations

import os
import stat
from pathlib import Path
from typing import Mapping


GIT = Path("/usr/bin/git")


def trusted_git_executable() -> str:
    info = GIT.stat()
    if (not stat.S_ISREG(info.st_mode) or info.st_uid != 0
            or stat.S_IMODE(info.st_mode) & 0o022 or not os.access(GIT, os.X_OK)):
        raise RuntimeError("controller Git executable is not trusted")
    return str(GIT)


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
