#!/usr/bin/env python3
"""Resolve controller runtime tools without ambient PATH or tool overrides."""

from __future__ import annotations

import hashlib
import os
import stat
from pathlib import Path
from typing import Type


TRUSTED_RUNTIME_ROOTS = (
    Path("/opt/homebrew/opt/postgresql@16/bin"), Path("/opt/homebrew/bin"),
    Path("/usr/local/bin"), Path("/usr/lib/postgresql/16/bin"),
)


def trusted_runtime_binary(name: str, *, error_type: Type[Exception] = RuntimeError) -> str:
    for candidate in (root / name for root in TRUSTED_RUNTIME_ROOTS):
        try:
            resolved = candidate.resolve(strict=True)
            before = resolved.stat()
            digest = hashlib.sha256(resolved.read_bytes()).digest()
            after = resolved.stat()
        except (FileNotFoundError, OSError):
            continue
        stable = (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns) == (
            after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns
        )
        if (
            stable and digest == hashlib.sha256(resolved.read_bytes()).digest()
            and stat.S_ISREG(after.st_mode) and os.access(resolved, os.X_OK)
            and after.st_uid in {0, os.geteuid()}
            and not stat.S_IMODE(after.st_mode) & 0o022
        ):
            return str(resolved)
    raise error_type(f"required trusted runtime binary is missing: {name}")
