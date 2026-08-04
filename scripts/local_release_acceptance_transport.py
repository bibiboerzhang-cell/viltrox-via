#!/usr/bin/env python3
"""Isolated HTTP transport with a parent-enforced wall-clock deadline.

The bearer is sent to a short-lived worker over stdin.  It is never placed in
argv, the environment, stdout, stderr, or an exception message.  The parent
owns the deadline and kills/reaps the worker process group when it expires, so
a peer that sends bytes forever cannot turn a socket inactivity timeout into
an unbounded acceptance run.
"""

from __future__ import annotations

import base64
import binascii
import ipaddress
import json
import math
import os
import signal
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener


PROTOCOL_VERSION = 1
MAX_REQUEST_ENVELOPE_BYTES = 256 * 1024
_WORKER_PATH = Path(__file__).resolve()
_KNOWN_FAILURES = {
    "connection_error",
    "error_response_too_large",
    "request_encoding_error",
    "response_too_large",
    "timeout",
    "worker_failure",
    "worker_protocol_error",
    "worker_start_error",
}


@dataclass(frozen=True)
class IsolatedHttpResponse:
    status: int
    body: bytes
    headers: Mapping[str, str]
    latency_ms: float


class IsolatedRequestFailure(RuntimeError):
    def __init__(self, kind: str, latency_ms: float) -> None:
        safe_kind = kind if kind in _KNOWN_FAILURES else "worker_failure"
        super().__init__(safe_kind)
        self.kind = safe_kind
        self.latency_ms = latency_ms


def _elapsed_ms(started: float) -> float:
    return (time.perf_counter() - started) * 1000


def _worker_environment() -> dict[str, str]:
    """Use no inherited credentials, proxy settings, or Python startup hooks."""

    clean: dict[str, str] = {}
    for name in ("LANG", "LC_ALL", "TMPDIR"):
        value = os.environ.get(name)
        if value:
            clean[name] = value
    return clean


def _kill_and_reap(process: subprocess.Popen[bytes]) -> None:
    """Kill the isolated process group and synchronously reap its leader."""

    if process.poll() is None:
        try:
            if os.name == "posix":
                os.killpg(process.pid, signal.SIGKILL)
            else:  # pragma: no cover - release controller is macOS/Linux
                process.kill()
        except ProcessLookupError:
            pass
        except OSError:  # retain a direct-child fallback if group signalling fails
            process.kill()
    try:
        process.communicate(timeout=1.0)
    except subprocess.TimeoutExpired:  # defensive: SIGKILL normally reaps at once
        process.kill()
        process.wait(timeout=1.0)


def run_isolated_http_request(
    *,
    base_url: str,
    method: str,
    path: str,
    token: str | None,
    timeout_seconds: float,
    max_response_bytes: int,
    json_body: Mapping[str, Any] | None = None,
) -> IsolatedHttpResponse:
    """Run one request in a disposable process bounded by elapsed wall time."""

    started = time.perf_counter()
    timeout = float(timeout_seconds)
    if not math.isfinite(timeout) or timeout <= 0:
        raise IsolatedRequestFailure("timeout", _elapsed_ms(started))
    deadline = started + timeout
    envelope = {
        "version": PROTOCOL_VERSION,
        "base_url": base_url,
        "method": method,
        "path": path,
        "token": token,
        "timeout_seconds": timeout,
        "max_response_bytes": int(max_response_bytes),
        "json_body": json_body,
    }
    try:
        wire = json.dumps(
            envelope,
            ensure_ascii=False,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError, OverflowError):
        raise IsolatedRequestFailure("request_encoding_error", _elapsed_ms(started)) from None
    if len(wire) > MAX_REQUEST_ENVELOPE_BYTES:
        raise IsolatedRequestFailure("request_encoding_error", _elapsed_ms(started))

    try:
        process = subprocess.Popen(
            [sys.executable, "-I", "-B", str(_WORKER_PATH), "--worker"],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            close_fds=True,
            start_new_session=os.name == "posix",
            env=_worker_environment(),
        )
    except OSError:
        raise IsolatedRequestFailure("worker_start_error", _elapsed_ms(started)) from None

    remaining = deadline - time.perf_counter()
    if remaining <= 0:
        _kill_and_reap(process)
        raise IsolatedRequestFailure("timeout", _elapsed_ms(started))
    try:
        stdout, _ = process.communicate(input=wire, timeout=remaining)
    except subprocess.TimeoutExpired:
        _kill_and_reap(process)
        raise IsolatedRequestFailure("timeout", _elapsed_ms(started)) from None

    try:
        message = json.loads(stdout.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise IsolatedRequestFailure("worker_protocol_error", _elapsed_ms(started)) from None
    if not isinstance(message, dict) or message.get("version") != PROTOCOL_VERSION:
        raise IsolatedRequestFailure("worker_protocol_error", _elapsed_ms(started))
    if message.get("ok") is not True:
        kind = str(message.get("error_code") or "worker_failure")
        raise IsolatedRequestFailure(kind, _elapsed_ms(started))
    if process.returncode != 0 or time.perf_counter() > deadline:
        raise IsolatedRequestFailure(
            "timeout" if time.perf_counter() > deadline else "worker_failure",
            _elapsed_ms(started),
        )

    status = message.get("status")
    headers = message.get("headers")
    encoded_body = message.get("body_b64")
    if (
        isinstance(status, bool)
        or not isinstance(status, int)
        or not isinstance(headers, dict)
        or any(not isinstance(key, str) or not isinstance(value, str) for key, value in headers.items())
        or not isinstance(encoded_body, str)
    ):
        raise IsolatedRequestFailure("worker_protocol_error", _elapsed_ms(started))
    try:
        body = base64.b64decode(encoded_body, validate=True)
    except (ValueError, binascii.Error):
        raise IsolatedRequestFailure("worker_protocol_error", _elapsed_ms(started)) from None
    if len(body) > max_response_bytes:
        raise IsolatedRequestFailure("worker_protocol_error", _elapsed_ms(started))
    if time.perf_counter() > deadline:
        raise IsolatedRequestFailure("timeout", _elapsed_ms(started))
    return IsolatedHttpResponse(status, body, headers, _elapsed_ms(started))


class _NoRedirectHandler(HTTPRedirectHandler):
    def redirect_request(self, req: Request, fp: Any, code: int, msg: str, headers: Any, newurl: str) -> None:
        return None


def _is_loopback_base_url(base_url: str) -> bool:
    try:
        parsed = urlsplit(base_url)
        host = str(parsed.hostname or "").lower()
        loopback = host == "localhost" or ipaddress.ip_address(host).is_loopback
        parsed.port  # force malformed-port validation
    except ValueError:
        return False
    return bool(
        parsed.scheme in {"http", "https"}
        and parsed.netloc
        and loopback
        and not parsed.username
        and not parsed.password
        and not parsed.query
        and not parsed.fragment
    )


def _worker_request(envelope: Mapping[str, Any]) -> dict[str, Any]:
    if envelope.get("version") != PROTOCOL_VERSION:
        return {"version": PROTOCOL_VERSION, "ok": False, "error_code": "worker_protocol_error"}
    base_url = envelope.get("base_url")
    method = envelope.get("method")
    path = envelope.get("path")
    token = envelope.get("token")
    timeout = envelope.get("timeout_seconds")
    max_bytes = envelope.get("max_response_bytes")
    if (
        not isinstance(base_url, str)
        or not _is_loopback_base_url(base_url)
        or method not in {"GET", "POST"}
        or not isinstance(path, str)
        or not path.startswith("/")
        or path.startswith("//")
        or urlsplit(path).scheme
        or (token is not None and not isinstance(token, str))
        or isinstance(timeout, bool)
        or not isinstance(timeout, (int, float))
        or not math.isfinite(float(timeout))
        or float(timeout) <= 0
        or isinstance(max_bytes, bool)
        or not isinstance(max_bytes, int)
        or not 1024 <= max_bytes <= 64 * 1024 * 1024
    ):
        return {"version": PROTOCOL_VERSION, "ok": False, "error_code": "worker_protocol_error"}

    headers = {
        "Accept": "application/json",
        "Cache-Control": "no-cache",
        "User-Agent": "vkpi-local-release-acceptance/1",
        "X-Requested-With": "XMLHttpRequest",
    }
    if token:
        headers["Authorization"] = f"Bearer {token}"
    body: bytes | None = None
    if method == "POST":
        try:
            body = json.dumps(
                envelope.get("json_body"),
                ensure_ascii=False,
                separators=(",", ":"),
                allow_nan=False,
            ).encode("utf-8")
        except (TypeError, ValueError, OverflowError):
            return {"version": PROTOCOL_VERSION, "ok": False, "error_code": "request_encoding_error"}
        headers["Content-Type"] = "application/json; charset=utf-8"
    elif envelope.get("json_body") is not None:
        return {"version": PROTOCOL_VERSION, "ok": False, "error_code": "worker_protocol_error"}

    request = Request(f"{base_url}{path}", data=body, headers=headers, method=method)
    # Do not consult inherited or system proxy configuration for a bearer-bearing
    # loopback request. Redirects are separately disabled below.
    opener = build_opener(ProxyHandler({}), _NoRedirectHandler())
    try:
        with opener.open(request, timeout=float(timeout)) as response:
            response_body = response.read(max_bytes + 1)
            if len(response_body) > max_bytes:
                return {"version": PROTOCOL_VERSION, "ok": False, "error_code": "response_too_large"}
            status = int(response.status)
            response_headers = {str(key).lower(): str(value) for key, value in response.headers.items()}
    except HTTPError as exc:
        response_body = exc.read(max_bytes + 1)
        if len(response_body) > max_bytes:
            return {"version": PROTOCOL_VERSION, "ok": False, "error_code": "error_response_too_large"}
        status = int(exc.code)
        response_headers = {str(key).lower(): str(value) for key, value in (exc.headers or {}).items()}
    except (TimeoutError, URLError, OSError) as exc:
        reason = getattr(exc, "reason", None)
        code = "timeout" if isinstance(exc, TimeoutError) or isinstance(reason, TimeoutError) else "connection_error"
        return {"version": PROTOCOL_VERSION, "ok": False, "error_code": code}
    return {
        "version": PROTOCOL_VERSION,
        "ok": True,
        "status": status,
        "headers": response_headers,
        "body_b64": base64.b64encode(response_body).decode("ascii"),
    }


def _worker_main() -> int:
    try:
        raw = sys.stdin.buffer.read(MAX_REQUEST_ENVELOPE_BYTES + 1)
        if len(raw) > MAX_REQUEST_ENVELOPE_BYTES:
            response = {"version": PROTOCOL_VERSION, "ok": False, "error_code": "worker_protocol_error"}
        else:
            envelope = json.loads(raw.decode("utf-8"))
            response = _worker_request(envelope) if isinstance(envelope, dict) else {
                "version": PROTOCOL_VERSION,
                "ok": False,
                "error_code": "worker_protocol_error",
            }
    except Exception:
        response = {"version": PROTOCOL_VERSION, "ok": False, "error_code": "worker_failure"}
    sys.stdout.buffer.write(json.dumps(response, separators=(",", ":")).encode("utf-8"))
    sys.stdout.buffer.flush()
    return 0


if __name__ == "__main__":
    raise SystemExit(_worker_main() if sys.argv[1:] == ["--worker"] else 2)
