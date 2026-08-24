from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import sys

import pytest


_SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "ops" / "audit_vkpi_read_performance.py"
_SPEC = importlib.util.spec_from_file_location("audit_vkpi_read_performance", _SCRIPT)
assert _SPEC is not None and _SPEC.loader is not None
audit = importlib.util.module_from_spec(_SPEC)
sys.modules[_SPEC.name] = audit
_SPEC.loader.exec_module(audit)


_BODY = b'{"status":"ok","items":[1]}'


def _result(
    *,
    surface: str,
    outcome: str,
    builder: bool,
    latency_ms: float,
    body: bytes = _BODY,
    include_headers: bool = True,
) -> audit.HttpResult:
    headers = {"Content-Type": "application/json"}
    if include_headers:
        timings = [f'{surface}-cache;desc="{outcome}";dur=1.000']
        if builder:
            timings.append(f"{surface}-builder;dur={latency_ms:.3f}")
        headers.update(
            {
                "X-VKPI-Cache": outcome,
                "X-VKPI-Cache-Builder": "1" if builder else "0",
                "X-VKPI-Cache-Key-Version": "v-test",
                "Server-Timing": ", ".join(timings),
            }
        )
    return audit.HttpResult(
        status=200,
        headers=headers,
        body=body,
        latency_ms=latency_ms,
    )


class _Requester:
    def __init__(self, responses: list[audit.HttpResult]) -> None:
        self.responses = list(responses)
        self.paths: list[str] = []

    def __call__(self, path: str) -> audit.HttpResult:
        self.paths.append(path)
        return self.responses.pop(0)


def _run(
    requester: _Requester,
    *,
    mode: str = audit.MODE_STRICT,
    base_url: str = "http://127.0.0.1:8001",
    strict_runtime_confirmed: bool = True,
) -> dict:
    return audit.run_audit(
        base_url=base_url,
        endpoint_ids=["gtm-summary"],
        mode=mode,
        warm_samples=2,
        cold_max_ms=2_000,
        warm_p95_max_ms=500,
        min_speedup=2,
        requester=requester,
        strict_runtime_confirmed=strict_runtime_confirmed,
    )


def test_strict_local_cold_warm_passes_only_proven_header_sequence() -> None:
    requester = _Requester(
        [
            _result(
                surface="gtm",
                outcome="miss_builder",
                builder=True,
                latency_ms=1_200,
            ),
            _result(surface="gtm", outcome="hit", builder=False, latency_ms=80),
            _result(surface="gtm", outcome="hit", builder=False, latency_ms=90),
        ]
    )

    report = _run(requester)

    endpoint = report["endpoints"][0]
    assert report["passed"] is True
    assert report["safety"]["strict_runtime_preconditions_operator_confirmed"] is True
    assert endpoint["cold"] == {
        "observed": True,
        "latency_ms": 1_200.0,
        "max_ms": 2_000,
    }
    assert endpoint["warm"]["latency"]["p95_ms"] == 90.0
    assert endpoint["cold_to_warm_p95_speedup"] == pytest.approx(13.333)
    assert requester.paths == [audit.ENDPOINTS["gtm-summary"].path] * 3


def test_strict_mode_refuses_to_claim_cold_when_first_read_is_already_hot() -> None:
    requester = _Requester(
        [
            _result(surface="gtm", outcome="hit", builder=False, latency_ms=90),
            _result(surface="gtm", outcome="hit", builder=False, latency_ms=70),
            _result(surface="gtm", outcome="hit", builder=False, latency_ms=80),
        ]
    )

    endpoint = _run(requester)["endpoints"][0]

    assert endpoint["passed"] is False
    assert endpoint["cold"]["observed"] is False
    assert "strict_cold_miss_builder_not_observed" in endpoint["errors"]
    assert "strict_cold_builder_timing_not_observed" in endpoint["errors"]


def test_warm_observe_never_reports_a_cold_speedup_claim() -> None:
    requester = _Requester(
        [
            _result(
                surface="gtm",
                outcome="miss_builder",
                builder=True,
                latency_ms=700,
            ),
            _result(surface="gtm", outcome="hit", builder=False, latency_ms=70),
            _result(surface="gtm", outcome="hit", builder=False, latency_ms=75),
        ]
    )

    report = _run(
        requester,
        mode=audit.MODE_WARM,
        strict_runtime_confirmed=False,
    )

    endpoint = report["endpoints"][0]
    assert report["passed"] is True
    assert endpoint["claim"] == "non_destructive_warm_observation"
    assert endpoint["cold"] == {
        "observed": False,
        "reason": "not_claimed_in_warm_observe_mode",
    }
    assert endpoint["cold_to_warm_p95_speedup"] is None


@pytest.mark.parametrize(
    ("base_url", "confirmed", "error"),
    [
        (
            "https://viltroxtest.com",
            True,
            "strict_cold_warm_is_loopback_only",
        ),
        (
            "http://127.0.0.1:8001",
            False,
            "strict_runtime_preconditions_not_confirmed",
        ),
    ],
)
def test_strict_mode_fails_closed_before_any_request(
    base_url: str,
    confirmed: bool,
    error: str,
) -> None:
    requester = _Requester([])

    with pytest.raises(ValueError, match=error):
        _run(
            requester,
            base_url=base_url,
            strict_runtime_confirmed=confirmed,
        )

    assert requester.paths == []


def test_surface_specific_headers_are_required() -> None:
    requester = _Requester(
        [
            _result(
                surface="dashboard",
                outcome="miss_builder",
                builder=True,
                latency_ms=800,
            ),
            _result(surface="dashboard", outcome="hit", builder=False, latency_ms=80),
            _result(surface="dashboard", outcome="hit", builder=False, latency_ms=90),
        ]
    )

    endpoint = _run(requester)["endpoints"][0]

    assert endpoint["passed"] is False
    assert "sample_0:cache_server_timing_missing" in endpoint["errors"]
    assert "strict_cold_builder_timing_not_observed" in endpoint["errors"]


def test_missing_cache_headers_fail_and_response_body_is_never_emitted() -> None:
    secret_body = b'{"status":"ok","private":"do-not-print-this"}'
    requester = _Requester(
        [
            _result(
                surface="gtm",
                outcome="miss_builder",
                builder=True,
                latency_ms=800,
                body=secret_body,
                include_headers=False,
            ),
            _result(
                surface="gtm",
                outcome="hit",
                builder=False,
                latency_ms=80,
                body=secret_body,
                include_headers=False,
            ),
            _result(
                surface="gtm",
                outcome="hit",
                builder=False,
                latency_ms=90,
                body=secret_body,
                include_headers=False,
            ),
        ]
    )

    report = _run(requester)
    serialized = json.dumps(report)

    assert report["passed"] is False
    assert "do-not-print-this" not in serialized
    assert report["safety"]["response_bodies_emitted"] is False
    assert report["safety"]["provider_calls_requested_by_auditor"] == 0
    assert report["safety"]["business_data_writes_requested"] == 0
    assert report["safety"]["cache_clear_requested"] is False
    assert report["safety"]["cache_delete_requested"] is False


def test_only_reviewed_get_routes_can_be_audited() -> None:
    unsafe = audit.EndpointSpec(
        endpoint_id="dashboard-refresh",
        path="/api/admin/vkpi/dashboard/refresh",
        surface="dashboard",
        timing_metric="dashboard-cache",
    )

    with pytest.raises(ValueError, match="endpoint_not_on_reviewed_read_allowlist"):
        audit.audit_endpoint(
            unsafe,
            requester=_Requester([]),
            mode=audit.MODE_WARM,
            warm_samples=2,
            cold_max_ms=2_000,
            warm_p95_max_ms=500,
            min_speedup=2,
        )


def test_duplicate_endpoint_is_rejected_before_requests() -> None:
    requester = _Requester([])

    with pytest.raises(ValueError, match="duplicate_endpoint_not_allowed"):
        audit.run_audit(
            base_url="http://127.0.0.1:8001",
            endpoint_ids=["gtm-summary", "gtm-summary"],
            mode=audit.MODE_WARM,
            warm_samples=2,
            cold_max_ms=2_000,
            warm_p95_max_ms=500,
            min_speedup=2,
            requester=requester,
        )

    assert requester.paths == []


def test_json_artifact_contains_only_report_metadata(tmp_path: Path) -> None:
    destination = tmp_path / "audit.json"
    report = {"passed": True, "safety": {"authorization_emitted": False}}

    audit._write_json(destination, report)

    assert json.loads(destination.read_text(encoding="utf-8")) == report
