#!/usr/bin/env python3
"""Run reviewed candidate commands in a private, bounded process group.

This controller reaps the session/process group it creates.  It is not a
same-UID adversarial sandbox: a candidate that deliberately calls ``setsid``
and double-forks can leave that group.  Deploy therefore requires reviewed,
clean Git source; hostile-source execution needs a dedicated UID or VM.
"""

from __future__ import annotations

import os
import signal
import subprocess
import time
from pathlib import Path
from typing import IO, Mapping, Sequence


def _group_state(pgid: int, expected_sid: int) -> str:
    """Return absent, owned-live, or unknown without treating EPERM as ownership."""
    observed = subprocess.run(
        ["/bin/ps", "-axo", "pgid="], check=False,
        capture_output=True, text=True, timeout=5,
        env={"HOME": "/tmp", "PATH": "/usr/bin:/bin", "LANG": "C", "LC_ALL": "C"},
    ).stdout.splitlines()
    members = 0
    for line in observed:
        try:
            member_pgid = int(line.strip())
        except ValueError:
            return "unknown"
        if member_pgid == pgid:
            members += 1
    if members:
        # The group was created by start_new_session and remained continuously
        # occupied; an occupied PGID cannot be reassigned to another group.
        return "owned-live"
    try:
        os.killpg(pgid, 0); return "unknown"
    except ProcessLookupError:
        return "absent"
    except PermissionError:
        return "unknown"


def _signal_owned_group(pgid: int, expected_sid: int, sig: int) -> None:
    state = _group_state(pgid, expected_sid)
    if state == "absent":
        return
    if state != "owned-live":
        raise RuntimeError("candidate process group ownership is unknown")
    try:
        os.killpg(pgid, sig)
    except ProcessLookupError:
        pass
    except PermissionError as exc:
        raise RuntimeError("candidate process group signal was denied") from exc


def run_controlled_candidate(
    arguments: Sequence[str], *, cwd: Path, env: Mapping[str, str],
    stdin: IO[bytes] | int | None = subprocess.DEVNULL,
    stdout: IO[bytes] | int | None = None, stderr: IO[bytes] | int | None = None,
    timeout: int = 1200,
    accepted_returncodes: Sequence[int] = (0,),
) -> subprocess.CompletedProcess[bytes]:
    """Run one reviewed command and reap every process that remains in its group."""
    process = subprocess.Popen(
        list(arguments), cwd=cwd, env=dict(env), stdin=stdin,
        stdout=stdout, stderr=stderr, start_new_session=True,
    )
    leaked_group = False
    pending: BaseException | None = None
    try:
        returncode = process.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        leaked_group = True; returncode = 124
    except BaseException as exc:
        pending = exc; leaked_group = True; returncode = 126
    finally:
        if _group_state(process.pid, process.pid) != "absent":
            leaked_group = True
            _signal_owned_group(process.pid, process.pid, signal.SIGTERM)
            for _ in range(20):
                if _group_state(process.pid, process.pid) == "absent": break
                time.sleep(0.05)
            if _group_state(process.pid, process.pid) != "absent":
                _signal_owned_group(process.pid, process.pid, signal.SIGKILL)
            try: process.wait(timeout=5)
            except (subprocess.TimeoutExpired, ChildProcessError): pass
        if _group_state(process.pid, process.pid) != "absent":
            raise RuntimeError("candidate process group could not be reaped")
    if pending is not None:
        raise pending
    if leaked_group and returncode in accepted_returncodes:
        returncode = 125
    return subprocess.CompletedProcess(list(arguments), returncode)
