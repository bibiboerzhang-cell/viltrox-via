#!/usr/bin/env python3
"""Classify an apify worker PID marker without touching runtime state.

The legacy worker scripts stored only a numeric PID.  A numeric PID is not an
identity: after a crash and PID reuse, blindly trusting it can make a start
script report a false "already running" or make a stop script signal an
unrelated process.

This guard is intentionally fail-closed.  A process is a verified V-KPI apify
worker only when all three independently observable facts match:

* command line contains ``app.workers.apify_jobs_worker``;
* process cwd is the expected repository root;
* stdout or stderr is the expected lane log file.

If permissions or platform tooling prevent that proof, the result is
``indeterminate``.  Callers must retain the marker and must not signal a
process in that state.
"""
from __future__ import annotations
from stdout_utils import out as stdout_out

import argparse
import json
import os
import subprocess
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Callable, Sequence


EXPECTED_MODULE = "app.workers.apify_jobs_worker"

EXIT_VERIFIED = 0
EXIT_MISSING = 10
EXIT_STALE_ABSENT = 11
EXIT_STALE_FOREIGN = 12
EXIT_INVALID = 13
EXIT_INDETERMINATE = 20

_EXIT_BY_STATUS = {
    "verified_worker": EXIT_VERIFIED,
    "missing_marker": EXIT_MISSING,
    "stale_absent": EXIT_STALE_ABSENT,
    "stale_foreign": EXIT_STALE_FOREIGN,
    "invalid_marker": EXIT_INVALID,
    "indeterminate": EXIT_INDETERMINATE,
}


@dataclass(frozen=True)
class ProcessEvidence:
    exists: bool | None
    command: str | None = None
    cwd: str | None = None
    log_fds: tuple[str, ...] = ()
    errors: tuple[str, ...] = ()


@dataclass(frozen=True)
class MarkerClassification:
    status: str
    pidfile: str
    pid: int | None
    safe_to_remove_marker: bool
    safe_to_signal: bool
    reason: str
    observed: dict[str, object] = field(default_factory=dict)


def _run(command: Sequence[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        list(command),
        check=False,
        capture_output=True,
        text=True,
        timeout=3,
        # Preserve the user's UTF-8 locale.  For repositories whose real path
        # contains non-ASCII characters, forcing LC_ALL=C makes macOS lsof
        # escape bytes (``\\xe2...``), which would look like a false mismatch.
        env=os.environ.copy(),
    )


def _parse_lsof_fields(output: str) -> tuple[str | None, tuple[str, ...]]:
    current_fd: str | None = None
    cwd: str | None = None
    logs: list[str] = []
    for raw_line in output.splitlines():
        if not raw_line:
            continue
        tag, value = raw_line[0], raw_line[1:]
        if tag == "f":
            current_fd = value
        elif tag == "n" and current_fd == "cwd":
            cwd = value
        elif tag == "n" and current_fd in {"1", "2"}:
            logs.append(value)
    return cwd, tuple(logs)


def inspect_process(pid: int) -> ProcessEvidence:
    """Collect process evidence; never sends a signal other than signal 0."""

    errors: list[str] = []
    exists: bool | None
    try:
        os.kill(pid, 0)
        exists = True
    except ProcessLookupError:
        return ProcessEvidence(exists=False)
    except PermissionError:
        # The process exists, but identity still needs independent proof.
        exists = True
        errors.append("signal0_permission_denied")
    except OSError as exc:
        exists = None
        errors.append(f"signal0_{type(exc).__name__}")

    command: str | None = None
    try:
        ps = _run(("ps", "-ww", "-p", str(pid), "-o", "command="))
        if ps.returncode == 0 and ps.stdout.strip():
            command = ps.stdout.strip()
        else:
            errors.append(f"ps_unavailable_rc_{ps.returncode}")
    except (OSError, subprocess.SubprocessError) as exc:
        errors.append(f"ps_{type(exc).__name__}")

    cwd: str | None = None
    log_fds: tuple[str, ...] = ()
    try:
        lsof = _run(("lsof", "-nP", "-a", "-p", str(pid), "-d", "cwd,1,2", "-F", "fn"))
        if lsof.returncode == 0 and lsof.stdout.strip():
            cwd, log_fds = _parse_lsof_fields(lsof.stdout)
            exists = True
        else:
            errors.append(f"lsof_unavailable_rc_{lsof.returncode}")
    except (OSError, subprocess.SubprocessError) as exc:
        errors.append(f"lsof_{type(exc).__name__}")

    return ProcessEvidence(
        exists=exists,
        command=command,
        cwd=cwd,
        log_fds=log_fds,
        errors=tuple(errors),
    )


def _realpath(value: str | Path) -> str:
    raw = os.fspath(value)
    # Linux lsof may annotate an unlinked-but-still-open log as ``(deleted)``.
    # It is still the worker's exact lane log identity for stop-safety purposes.
    if raw.endswith(" (deleted)"):
        raw = raw[: -len(" (deleted)")]
    return os.path.realpath(raw)


def _read_pid(pidfile: Path) -> tuple[int | None, str | None]:
    try:
        raw = pidfile.read_text(encoding="utf-8").strip()
    except FileNotFoundError:
        return None, "missing"
    except OSError:
        return None, "unreadable"
    if not raw:
        return None, "empty"
    try:
        pid = int(raw)
    except ValueError:
        return None, "not_an_integer"
    if pid <= 1:
        return None, "unsafe_pid"
    return pid, None


def classify_pidfile(
    pidfile: str | Path,
    *,
    expected_root: str | Path,
    expected_log: str | Path,
    inspector: Callable[[int], ProcessEvidence] = inspect_process,
) -> MarkerClassification:
    path = Path(pidfile)
    pid, marker_error = _read_pid(path)
    if marker_error == "missing":
        return MarkerClassification(
            status="missing_marker",
            pidfile=str(path),
            pid=None,
            safe_to_remove_marker=False,
            safe_to_signal=False,
            reason="pid marker does not exist",
        )
    if marker_error is not None:
        return MarkerClassification(
            status="invalid_marker",
            pidfile=str(path),
            pid=None,
            safe_to_remove_marker=True,
            safe_to_signal=False,
            reason=f"invalid pid marker: {marker_error}",
        )

    assert pid is not None
    evidence = inspector(pid)
    observed: dict[str, object] = {
        "process_exists": evidence.exists,
        "command_matches": bool(evidence.command and EXPECTED_MODULE in evidence.command),
        "cwd": evidence.cwd,
        "log_fds": list(evidence.log_fds),
        "inspection_errors": list(evidence.errors),
    }
    if evidence.exists is False:
        return MarkerClassification(
            status="stale_absent",
            pidfile=str(path),
            pid=pid,
            safe_to_remove_marker=True,
            safe_to_signal=False,
            reason="the recorded pid no longer exists",
            observed=observed,
        )
    if evidence.exists is None:
        return MarkerClassification(
            status="indeterminate",
            pidfile=str(path),
            pid=pid,
            safe_to_remove_marker=False,
            safe_to_signal=False,
            reason="process existence could not be established",
            observed=observed,
        )

    expected_root_real = _realpath(expected_root)
    expected_log_real = _realpath(expected_log)
    command_known = evidence.command is not None
    cwd_known = evidence.cwd is not None
    logs_known = bool(evidence.log_fds)
    command_matches = bool(evidence.command and EXPECTED_MODULE in evidence.command)
    cwd_matches = bool(evidence.cwd and _realpath(evidence.cwd) == expected_root_real)
    log_matches = any(_realpath(item) == expected_log_real for item in evidence.log_fds)
    observed.update(
        {
            "cwd_matches": cwd_matches,
            "expected_root": expected_root_real,
            "log_matches": log_matches,
            "expected_log": expected_log_real,
        }
    )

    # A positive mismatch proves this marker belongs to a foreign/reused PID.
    if (
        (command_known and not command_matches)
        or (cwd_known and not cwd_matches)
        or (command_matches and cwd_matches and logs_known and not log_matches)
    ):
        return MarkerClassification(
            status="stale_foreign",
            pidfile=str(path),
            pid=pid,
            safe_to_remove_marker=True,
            safe_to_signal=False,
            reason="recorded pid exists but does not match the expected worker identity",
            observed=observed,
        )

    if command_matches and cwd_matches and log_matches:
        return MarkerClassification(
            status="verified_worker",
            pidfile=str(path),
            pid=pid,
            safe_to_remove_marker=False,
            safe_to_signal=True,
            reason="module, repository cwd, and lane log all match",
            observed=observed,
        )

    return MarkerClassification(
        status="indeterminate",
        pidfile=str(path),
        pid=pid,
        safe_to_remove_marker=False,
        safe_to_signal=False,
        reason="process exists but the three-part worker identity could not be proven",
        observed=observed,
    )


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pidfile", required=True)
    parser.add_argument("--root", required=True)
    parser.add_argument("--logfile", required=True)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    result = classify_pidfile(
        args.pidfile,
        expected_root=args.root,
        expected_log=args.logfile,
    )
    stdout_out(json.dumps(asdict(result), ensure_ascii=False, sort_keys=True))
    return _EXIT_BY_STATUS[result.status]


if __name__ == "__main__":
    raise SystemExit(main())
