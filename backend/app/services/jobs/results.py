"""
services/jobs/results.py — 后台任务结果落盘
"""
from __future__ import annotations

import json
import os
import stat
from pathlib import Path
from typing import Any


BASE_DIR = Path(__file__).resolve().parents[3]
DEFAULT_JOB_RESULTS_DIR = BASE_DIR / "data" / "job_results"
_RESULT_LOAD_ERROR = "job result unavailable"


def job_results_dir() -> Path:
    """Return the configured release-stable result store.

    Local development keeps the historical backend-local default. Production
    workers bind ``VKPI_JOB_RESULTS_DIR`` to a shared path outside the immutable
    release so a result remains readable after the ``current`` symlink moves.
    Relative overrides are rejected because their meaning would change with a
    worker's WorkingDirectory/release.
    """

    configured = os.environ.get("VKPI_JOB_RESULTS_DIR", "").strip()
    if not configured:
        return DEFAULT_JOB_RESULTS_DIR
    path = Path(configured)
    if not path.is_absolute():
        raise RuntimeError("VKPI_JOB_RESULTS_DIR must be an absolute path")
    return path


def persist_job_result(task_id: str, payload: Any) -> str:
    directory = job_results_dir()
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{task_id}.json"
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    return str(path)


def _relative_result_path(root: Path, path: str) -> Path:
    candidate = Path(path)
    relative = candidate.relative_to(root) if candidate.is_absolute() else candidate
    if (
        not relative.parts
        or any(part in {"", ".", ".."} for part in relative.parts)
        or relative.suffix.lower() != ".json"
    ):
        raise ValueError(_RESULT_LOAD_ERROR)
    return relative


def _load_json_without_symlinks(root: Path, relative: Path) -> Any:
    nofollow = getattr(os, "O_NOFOLLOW", 0)
    directory = getattr(os, "O_DIRECTORY", 0)
    cloexec = getattr(os, "O_CLOEXEC", 0)
    if not nofollow or not directory:
        raise RuntimeError(_RESULT_LOAD_ERROR)

    directory_flags = os.O_RDONLY | directory | nofollow | cloexec
    file_flags = os.O_RDONLY | nofollow | cloexec
    directory_fds: list[int] = []
    file_fd: int | None = None
    try:
        directory_fds.append(os.open(root, directory_flags))
        for part in relative.parts[:-1]:
            directory_fds.append(
                os.open(part, directory_flags, dir_fd=directory_fds[-1])
            )
        file_fd = os.open(relative.name, file_flags, dir_fd=directory_fds[-1])
        if not stat.S_ISREG(os.fstat(file_fd).st_mode):
            raise ValueError(_RESULT_LOAD_ERROR)
        handle = os.fdopen(file_fd, "r", encoding="utf-8")
        file_fd = None
        with handle:
            return json.load(handle)
    finally:
        if file_fd is not None:
            os.close(file_fd)
        for directory_fd in reversed(directory_fds):
            os.close(directory_fd)


def load_job_result(path: str) -> Any:
    try:
        root = job_results_dir()
        relative = _relative_result_path(root, path)
        return _load_json_without_symlinks(root, relative)
    except Exception:
        raise RuntimeError(_RESULT_LOAD_ERROR) from None
