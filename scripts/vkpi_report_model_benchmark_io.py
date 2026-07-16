"""Strict JSON loading and private file output for the Report benchmark."""
from __future__ import annotations

import json
import os
import stat
from copy import deepcopy
from pathlib import Path
from typing import Any


def _benchmark_module() -> Any:
    from scripts import vkpi_report_model_benchmark

    return vkpi_report_model_benchmark


def _unique_json_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate_json_key:{key}")
        result[key] = value
    return result


def _load_json_object(path: Path, *, label: str) -> dict[str, Any]:
    def reject_constant(value: str) -> None:
        raise ValueError(f"{label} contains non-finite number: {value}")

    payload = json.loads(
        path.read_text(encoding="utf-8"),
        parse_constant=reject_constant,
        object_pairs_hook=_unique_json_object,
    )
    if not isinstance(payload, dict):
        raise ValueError(f"{label} must be a JSON object")
    return payload


def load_fixture(path: str = "") -> dict[str, Any]:
    if not path:
        return deepcopy(_benchmark_module().DEFAULT_FIXTURE)
    return _load_json_object(Path(path), label="benchmark fixture")


def load_signed_evidence_bundle(path: str) -> dict[str, Any]:
    if not path:
        raise ValueError("signed evidence path is required")
    return _load_json_object(Path(path), label="signed evidence bundle")


def _write_private_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists() or path.is_symlink():
        existing = path.lstat()
        if not stat.S_ISREG(existing.st_mode):
            raise ValueError("output path must be a regular file")
        if existing.st_nlink != 1:
            raise ValueError("output path must have exactly one hard link")
        if existing.st_uid != os.getuid():
            raise ValueError("output path must be owned by the current user")
    flags = os.O_WRONLY | os.O_CREAT
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    if hasattr(os, "O_NONBLOCK"):
        flags |= os.O_NONBLOCK
    descriptor = os.open(path, flags, 0o600)
    try:
        opened = os.fstat(descriptor)
        if not stat.S_ISREG(opened.st_mode):
            raise ValueError("opened output must be a regular file")
        if opened.st_nlink != 1:
            raise ValueError("opened output must have exactly one hard link")
        if opened.st_uid != os.getuid():
            raise ValueError("opened output must be owned by the current user")
        os.fchmod(descriptor, 0o600)
        os.ftruncate(descriptor, 0)
        with os.fdopen(descriptor, "w", encoding="utf-8", closefd=False) as handle:
            handle.write(content)
            handle.flush()
    finally:
        os.close(descriptor)


__all__ = [
    "_load_json_object",
    "_unique_json_object",
    "_write_private_text",
    "load_fixture",
    "load_signed_evidence_bundle",
]
