#!/usr/bin/env python3
"""Fetch private runtime health without exposing the ops token.

The token is inherited or read from an explicitly supplied protected dotenv.
The helper accepts only the loopback ``/health`` endpoint and emits the
response JSON body, never the token itself.
"""
from __future__ import annotations
import sys as _stdout_sys
from pathlib import Path as _StdoutPath

_STDOUT_UTILS_DIR = _StdoutPath(__file__).resolve().parents[1]
if str(_STDOUT_UTILS_DIR) not in _stdout_sys.path:
    _stdout_sys.path.insert(1, str(_STDOUT_UTILS_DIR))
try:
    from stdout_utils import out as stdout_out  # noqa: E402
except ModuleNotFoundError:
    # The first atomic deployment can execute this reviewed file over stdin on
    # a legacy release that predates ``scripts/stdout_utils.py``. Keep that
    # bootstrap probe self-contained while preserving the shared helper's
    # bounded output contract.
    def stdout_out(
        *values: object,
        sep: str | None = " ",
        end: str | None = "\n",
        file: object | None = None,
        flush: bool = False,
    ) -> None:
        separator = " " if sep is None else sep
        terminator = "\n" if end is None else end
        if not isinstance(separator, str) or not isinstance(terminator, str):
            raise TypeError("sep and end must be strings or None")
        stream = _stdout_sys.stdout if file is None else file
        stream.write(separator.join(str(value) for value in values) + terminator)
        if flush:
            stream.flush()

import argparse
from io import StringIO
import json
import os
from pathlib import Path
import stat
import sys
from typing import Any
from urllib.parse import urlsplit
from urllib.request import Request, urlopen

from dotenv import dotenv_values


_LOOPBACK_HOSTS = {"127.0.0.1", "::1", "localhost"}
_SAFE_ENV_MODES = {0o400, 0o440, 0o600, 0o640}


def _validated_health_url(value: str) -> str:
    parsed = urlsplit(str(value or "").strip())
    if (
        parsed.scheme not in {"http", "https"}
        or parsed.hostname not in _LOOPBACK_HOSTS
        or parsed.username is not None
        or parsed.password is not None
        or parsed.path != "/health"
        or parsed.fragment
    ):
        raise ValueError("health URL must be an authenticated loopback /health endpoint")
    return parsed.geturl()


def _read_ops_token(env_file: Path | None) -> str:
    inherited = str(os.getenv("OPS_HEALTH_TOKEN") or "").strip()
    if inherited:
        return inherited
    if env_file is None:
        raise ValueError(
            "OPS_HEALTH_TOKEN must be inherited or supplied by an explicit --env-file"
        )
    path = Path(env_file).expanduser()
    flags = os.O_RDONLY
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    if hasattr(os, "O_CLOEXEC"):
        flags |= os.O_CLOEXEC
    descriptor = os.open(path, flags)
    try:
        metadata = os.fstat(descriptor)
        effective_uid = os.geteuid() if hasattr(os, "geteuid") else os.getuid()
        effective_gid = os.getegid() if hasattr(os, "getegid") else os.getgid()
        trusted_groups = {effective_gid, *getattr(os, "getgroups", lambda: [])()}
        mode = stat.S_IMODE(metadata.st_mode)
        owner_is_trusted = metadata.st_uid in {0, effective_uid}
        trusted_group_read = bool(mode & stat.S_IRGRP) and metadata.st_gid in trusted_groups
        # Local development uses <current-user>:<group> 0600. Production is
        # deliberately hardened to root:viltrox 0640 and the probe executes as
        # viltrox. Accept only those two trust shapes (plus their read-only
        # variants); never accept group write/execute or any world access.
        if metadata.st_uid == effective_uid:
            access_shape_is_trusted = mode in {0o400, 0o600}
        else:
            access_shape_is_trusted = (
                owner_is_trusted
                and mode in {0o440, 0o640}
                and trusted_group_read
            )
        if (
            not stat.S_ISREG(metadata.st_mode)
            or mode not in _SAFE_ENV_MODES
            or not access_shape_is_trusted
            or metadata.st_nlink != 1
            or metadata.st_size > 64 * 1024
        ):
            raise ValueError(
                "health token source must be trusted-owner/private-group, "
                "single-link, and regular"
            )
        chunks: list[bytes] = []
        remaining = 64 * 1024 + 1
        while remaining > 0:
            chunk = os.read(descriptor, min(8192, remaining))
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
        encoded = b"".join(chunks)
        if len(encoded) > 64 * 1024:
            raise ValueError("health token source exceeds the 64 KiB safety limit")
        text = encoded.decode("utf-8")
    finally:
        os.close(descriptor)
    token = str(dotenv_values(stream=StringIO(text)).get("OPS_HEALTH_TOKEN") or "").strip()
    if not token:
        raise ValueError("OPS_HEALTH_TOKEN is not configured on the target host")
    return token


def fetch_runtime_health(
    *,
    url: str,
    env_file: Path | None = None,
    timeout_seconds: float = 3.0,
) -> dict[str, Any]:
    safe_url = _validated_health_url(url)
    token = _read_ops_token(env_file)
    request = Request(
        safe_url,
        method="GET",
        headers={"x-ops-token": token, "accept": "application/json"},
    )
    with urlopen(request, timeout=max(0.1, min(float(timeout_seconds), 30.0))) as response:
        body = response.read(4 * 1024 * 1024 + 1)
    if len(body) > 4 * 1024 * 1024:
        raise ValueError("health response exceeds the 4 MiB safety limit")
    payload = json.loads(body.decode("utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("health response must be a JSON object")
    return payload


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Fetch authenticated loopback runtime health JSON.")
    parser.add_argument("--url", required=True)
    parser.add_argument(
        "--env-file",
        type=Path,
        help=(
            "protected dotenv containing OPS_HEALTH_TOKEN (0600 owner file or "
            "root:trusted-group 0640); optional only when OPS_HEALTH_TOKEN is inherited"
        ),
    )
    parser.add_argument("--timeout-seconds", type=float, default=3.0)
    args = parser.parse_args(argv)
    try:
        payload = fetch_runtime_health(
            url=args.url,
            env_file=args.env_file,
            timeout_seconds=args.timeout_seconds,
        )
    except Exception as exc:  # noqa: BLE001 - CLI must fail closed with a bounded message.
        stdout_out(f"runtime health fetch failed: {exc.__class__.__name__}: {str(exc)[:200]}", file=sys.stderr)
        return 2
    stdout_out(json.dumps(payload, ensure_ascii=False, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
