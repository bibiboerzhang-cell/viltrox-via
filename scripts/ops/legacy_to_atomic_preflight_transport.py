"""Local SSH transport for the self-contained read-only preflight collector."""

from __future__ import annotations

import sys

sys.dont_write_bytecode = True

import argparse
import json
import shlex
import subprocess
from pathlib import PurePosixPath
from typing import Any, Callable, Mapping, Pattern, Sequence


def validate_target(
    value: str, target_pattern: Pattern[str], error_type: type[Exception]
) -> str:
    if not value or value.startswith("-") or not target_pattern.fullmatch(value):
        raise error_type("invalid_ssh_target")
    return value


def validate_remote_python(
    value: str,
    root: str,
    *,
    valid_absolute_path: Callable[[str, str], str],
    validate_root: Callable[[str], str],
    error_type: type[Exception],
) -> str:
    python_path = valid_absolute_path(value, "remote_python")
    root_path = PurePosixPath(validate_root(root))
    candidate = PurePosixPath(python_path)
    if (
        candidate.parent != root_path / ".venv" / "bin"
        or candidate.name not in {"python", "python3"}
    ):
        raise error_type("remote_python_outside_reviewed_venv")
    return python_path


def ssh_command(
    args: argparse.Namespace,
    *,
    validate_target_value: Callable[[str], str],
    validate_root: Callable[[str], str],
    validate_remote_python_value: Callable[[str, str], str],
    validate_health_url: Callable[[str], str],
) -> list[str]:
    target = validate_target_value(args.ssh_target)
    root = validate_root(args.root)
    python_path = validate_remote_python_value(args.remote_python, root)
    health_url = validate_health_url(args.health_url)
    remote_arguments = [
        "env",
        "PYTHONDONTWRITEBYTECODE=1",
        python_path,
        "-B",
        "-",
        "--remote-collect",
        "--root",
        root,
        "--app-user",
        args.app_user,
        "--health-url",
        health_url,
        "--max-backup-age-hours",
        str(args.max_backup_age_hours),
    ]
    remote_command = " ".join(shlex.quote(value) for value in remote_arguments)
    return [
        "ssh",
        "-o",
        "BatchMode=yes",
        "-o",
        f"ConnectTimeout={args.connect_timeout}",
        "-o",
        "ClearAllForwardings=yes",
        "-o",
        "RequestTTY=no",
        "--",
        target,
        remote_command,
    ]


def collect_via_ssh(
    args: argparse.Namespace,
    *,
    source: str,
    command_builder: Callable[[argparse.Namespace], Sequence[str]],
    schema_version: int,
    error_type: type[Exception],
) -> Mapping[str, Any]:
    try:
        completed = subprocess.run(
            list(command_builder(args)),
            input=source,
            check=False,
            capture_output=True,
            text=True,
            timeout=args.timeout,
        )
    except (OSError, subprocess.TimeoutExpired):
        raise error_type("ssh_collection_failed") from None
    if completed.returncode != 0:
        raise error_type("ssh_collection_failed")
    try:
        payload = json.loads(completed.stdout)
    except (json.JSONDecodeError, TypeError):
        raise error_type("invalid_remote_snapshot") from None
    if not isinstance(payload, dict) or payload.get("schema_version") != schema_version:
        raise error_type("invalid_remote_snapshot")
    return payload
