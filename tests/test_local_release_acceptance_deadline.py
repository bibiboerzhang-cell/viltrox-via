from __future__ import annotations

import json
import math
import os
import sys
import time
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from threading import Event, Thread
from typing import Any

import pytest


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import local_release_acceptance as acceptance  # noqa: E402
import local_release_acceptance_transport as isolated_transport  # noqa: E402


NOW = datetime(2026, 8, 4, 12, 0, 0, tzinfo=timezone.utc)


class FakeClock:
    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


class TimedTransport:
    def __init__(self, clock: FakeClock, advances: list[float] | None = None) -> None:
        self.clock = clock
        self.advances = list(advances or [])
        self.calls: list[tuple[str, float]] = []

    def get(
        self,
        path: str,
        *,
        token: str | None,
        timeout_seconds: float,
    ) -> acceptance.HttpResponse:
        self.calls.append((path, timeout_seconds))
        if self.advances:
            self.clock.advance(self.advances.pop(0))
        return acceptance.HttpResponse(
            status=200,
            body=json.dumps({"value": 1}).encode("utf-8"),
            headers={"content-type": "application/json"},
            latency_ms=1.0,
        )

    def post(
        self,
        path: str,
        *,
        json_body: dict[str, Any],
        token: str | None,
        timeout_seconds: float,
    ) -> acceptance.HttpResponse:
        raise AssertionError("deadline fixtures are GET-only")


def _manifest(count: int, *, timeout_seconds: float = 60.0) -> dict[str, Any]:
    return {
        "name": "deadline-fixture",
        "version": 1,
        "board_families": ["fixture"],
        "endpoints": [
            acceptance._ep(
                f"fixture.{index}",
                "fixture",
                f"/fixture/{index}",
                data_paths=["value"],
                state_paths=[],
                timeout_seconds=timeout_seconds,
            )
            for index in range(count)
        ],
    }


def _runner(
    clock: FakeClock,
    transport: TimedTransport,
    *,
    endpoint_count: int = 1,
    overall_timeout_seconds: float = 10.0,
) -> acceptance.AcceptanceRunner:
    return acceptance.AcceptanceRunner(
        base_url="http://127.0.0.1:8102",
        manifest=_manifest(endpoint_count),
        auth=acceptance.AuthContext(
            token="fixture-token-must-never-be-emitted",
            role="admin",
            expires_in_seconds=60,
        ),
        transport=transport,
        local_head="a" * 40,
        latest_migration="fixture.sql",
        overall_timeout_seconds=overall_timeout_seconds,
        now_fn=lambda: NOW,
        monotonic_fn=clock,
    )


def test_each_request_timeout_is_bounded_by_the_remaining_overall_deadline() -> None:
    clock = FakeClock()
    transport = TimedTransport(clock)

    report = _runner(clock, transport).run()

    assert transport.calls == [("/fixture/0", 10.0)]
    assert report["overall"]["pass"] is True
    assert report["overall"]["deadline_exhausted"] is False
    assert report["safety"]["overall_deadline_seconds"] == 10.0
    assert report["safety"]["token_expiry_safety_seconds"] == 30


def test_mid_request_exhaustion_fails_current_and_all_remaining_endpoints() -> None:
    clock = FakeClock()
    transport = TimedTransport(clock, advances=[10.01])

    report = _runner(clock, transport, endpoint_count=3).run()

    assert transport.calls == [("/fixture/0", 10.0)]
    assert report["overall"]["pass"] is False
    assert report["overall"]["required_total"] == 3
    assert report["overall"]["required_passed"] == 0
    assert report["overall"]["failed_endpoint_ids"] == [
        "fixture.0",
        "fixture.1",
        "fixture.2",
    ]
    assert report["overall"]["deadline_exhausted"] is True
    assert report["endpoints"][0]["errors"] == [
        "acceptance overall deadline exhausted during request"
    ]
    assert report["endpoints"][1]["errors"] == [
        "acceptance overall deadline exhausted before request"
    ]
    assert report["endpoints"][2]["errors"] == [
        "acceptance overall deadline exhausted before request"
    ]
    serialized = json.dumps(report)
    assert "fixture-token-must-never-be-emitted" not in serialized
    assert "request_body" not in serialized


def test_remaining_budget_is_forwarded_after_an_earlier_request() -> None:
    clock = FakeClock()
    transport = TimedTransport(clock, advances=[4.0, 0.0])

    report = _runner(clock, transport, endpoint_count=2).run()

    assert transport.calls == [("/fixture/0", 10.0), ("/fixture/1", 6.0)]
    assert report["overall"]["pass"] is True


def test_real_transport_hard_stops_a_trickle_response_and_reaps_worker(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Socket activity must not extend the request's wall-clock budget."""

    secret = "deadline-secret-bearer-must-never-be-emitted"
    handler_started = Event()
    handler_finished = Event()
    stop_server = Event()
    observed_authorization: list[str | None] = []

    class TrickleHandler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            observed_authorization.append(self.headers.get("Authorization"))
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", "200")
            self.end_headers()
            handler_started.set()
            try:
                for _ in range(200):  # ten seconds without an external deadline
                    if stop_server.is_set():
                        break
                    self.wfile.write(b"x")
                    self.wfile.flush()
                    time.sleep(0.05)
            except (BrokenPipeError, ConnectionResetError):
                pass
            finally:
                handler_finished.set()

        def log_message(self, format: str, *args: Any) -> None:
            return

    server = HTTPServer(("127.0.0.1", 0), TrickleHandler)
    server_thread = Thread(target=server.serve_forever, daemon=True)
    server_thread.start()
    workers: list[Any] = []
    real_popen = isolated_transport.subprocess.Popen

    def tracked_popen(*args: Any, **kwargs: Any) -> Any:
        process = real_popen(*args, **kwargs)
        workers.append(process)
        return process

    monkeypatch.setattr(isolated_transport.subprocess, "Popen", tracked_popen)
    started = time.perf_counter()
    failure: acceptance.TransportFailure | None = None
    try:
        transport = acceptance.UrlLibTransport(
            f"http://127.0.0.1:{server.server_port}"
        )
        with pytest.raises(acceptance.TransportFailure) as raised:
            transport.get("/trickle", token=secret, timeout_seconds=0.5)
        failure = raised.value
        elapsed = time.perf_counter() - started

        assert handler_started.wait(1.0)
        assert observed_authorization == [f"Bearer {secret}"]
        assert failure.kind == "timeout"
        assert 0.35 <= elapsed < 2.0
        assert workers and all(process.poll() is not None for process in workers)
        assert all(secret not in str(argument) for argument in workers[0].args)
        if os.name == "posix":
            with pytest.raises(ChildProcessError):
                os.waitpid(workers[0].pid, os.WNOHANG)
    finally:
        stop_server.set()
        assert handler_finished.wait(1.0)
        server.shutdown()
        server.server_close()
        server_thread.join(timeout=2.0)

    assert not server_thread.is_alive()
    captured = capsys.readouterr()
    assert secret not in captured.out
    assert secret not in captured.err
    assert failure is not None and secret not in str(failure)


@pytest.mark.parametrize(
    ("token_ttl", "overall_timeout"),
    [(300, 270), (1200, 1170), (60, 30), (300, 1.5)],
)
def test_timing_contract_accepts_explicit_safe_pairs(
    token_ttl: int, overall_timeout: float
) -> None:
    assert acceptance.validate_acceptance_timing(token_ttl, overall_timeout) == (
        token_ttl,
        float(overall_timeout),
    )


@pytest.mark.parametrize(
    ("token_ttl", "overall_timeout"),
    [
        (59, 29),
        (1201, 1170),
        (True, 1),
        (300.0, 270),
        (300, 0),
        (300, -1),
        (300, math.inf),
        (300, math.nan),
        (300, 271),
        (60, 31),
    ],
)
def test_timing_contract_rejects_unsafe_or_ambiguous_pairs(
    token_ttl: Any, overall_timeout: Any
) -> None:
    with pytest.raises(ValueError):
        acceptance.validate_acceptance_timing(token_ttl, overall_timeout)


def test_cli_exposes_matching_token_and_overall_deadline_parameters() -> None:
    args = acceptance.parse_args(
        ["--token-ttl", "1200", "--overall-timeout", "1170"]
    )

    assert args.token_ttl == 1200
    assert args.overall_timeout == 1170.0


def test_unsafe_cli_pair_fails_before_authentication_or_http(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    def unexpected_auth(_: int) -> acceptance.AuthContext:
        raise AssertionError("authentication must not run for an unsafe timing pair")

    monkeypatch.setattr(acceptance, "create_local_auth_context", unexpected_auth)

    exit_code = acceptance.main(
        ["--token-ttl", "300", "--overall-timeout", "271"]
    )

    output = capsys.readouterr()
    report = json.loads(output.out)
    assert exit_code == 2
    assert report["overall"]["pass"] is False
    assert report["overall"]["failed_endpoint_ids"] == ["setup"]
    assert report["safety"]["token_emitted"] is False


def test_cloud_acceptance_invocation_uses_the_reviewed_maximum_pair() -> None:
    deploy = (ROOT / "scripts" / "ops" / "deploy_local_to_cloud.sh").read_text(
        encoding="utf-8"
    )
    acceptance_at = deploy.index("scripts/local_release_acceptance.py")
    browser_at = deploy.index("scripts/capture_browser_console_cdp.mjs", acceptance_at)

    assert "--token-ttl 1200 --overall-timeout 1170" in deploy[
        acceptance_at:browser_at
    ]


def test_cloud_pair_covers_the_complete_serial_manifest_budget() -> None:
    manifest = acceptance.load_manifest()
    serial_budget = sum(
        float(spec.get("timeout_seconds") or acceptance.DEFAULT_TIMEOUT_SECONDS)
        for spec in manifest["endpoints"]
    )

    assert len(manifest["endpoints"]) == 53
    assert serial_budget == 1095.0
    assert serial_budget < 1170
    assert 1170 + acceptance.TOKEN_EXPIRY_SAFETY_SECONDS == 1200
