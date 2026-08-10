from __future__ import annotations

import importlib.util
from pathlib import Path
from urllib.parse import urlsplit

import pytest


ROOT = Path(__file__).resolve().parents[1]
FIXTURE_PATH = ROOT / "tests" / "test_browser_console_release_gate.py"
SPEC = importlib.util.spec_from_file_location("vkpi_browser_gate_fixtures", FIXTURE_PATH)
assert SPEC and SPEC.loader
fixtures = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(fixtures)


def append_network_response(payload: dict, response: dict) -> None:
    payload["collection"]["network_responses"].append(response)
    summary = payload["collection"]["network_summary"]
    for key in ("response_count_total", "request_count_total", "retained_response_count"):
        summary[key] += 1
    if int(response.get("status") or 0) >= 400:
        summary["response_error_count_total"] += 1


def test_attributed_fixture_rows_default_to_false() -> None:
    result = fixtures.gate.evaluate_capture(fixtures.capture())
    assert result["overall"]["pass"] is True
    assert all(row["unattributed"] is False for row in result["network"]["responses"])


@pytest.mark.parametrize(
    ("response_path", "network_metric"),
    [
        ("/api/auth/me", "auth_me_2xx_observed"),
        ("/health", "release_identity_probe"),
    ],
)
def test_unattributed_response_cannot_supply_auth_or_release_identity_proof(
    response_path: str,
    network_metric: str,
) -> None:
    payload = fixtures.capture()
    response = next(
        row for row in payload["collection"]["network_responses"]
        if urlsplit(row["url"]).path == response_path
    )
    response.update({"page_family": "unattributed", "unattributed": True})
    result = fixtures.gate.evaluate_capture(payload)
    assert result["overall"]["pass"] is False
    retained = next(
        row for row in result["network"]["responses"]
        if urlsplit(row["url"]).path == response_path
    )
    assert retained["unattributed"] is True
    assert retained["provenance"] == "unattributed"
    network = result["metrics"]["network"]
    if network_metric == "auth_me_2xx_observed":
        assert network[network_metric] is False
    else:
        assert network[network_metric]["health_uncached_2xx"] == 0


@pytest.mark.parametrize(
    "response_path",
    ["/api/admin/vkpi/intelligent/query", "/api/admin/vkpi/global-search"],
)
def test_unattributed_response_cannot_supply_ask_or_search_network_proof(
    response_path: str,
) -> None:
    payload = fixtures.capture()
    for response in payload["collection"]["network_responses"]:
        if urlsplit(response["url"]).path == response_path:
            response.update({"page_family": "unattributed", "unattributed": True})
    result = fixtures.gate.evaluate_capture(payload)
    assert result["overall"]["pass"] is False
    assert result["metrics"]["functional_proof"]["network_evidence_pass"] is False


def test_unattributed_http_error_remains_blocking() -> None:
    payload = fixtures.capture()
    append_network_response(payload, {
        "channel": "Network.responseReceived",
        "page_family": "unattributed",
        "unattributed": True,
        "url": f"{fixtures.APP_ORIGIN}/api/unknown",
        "status": 500,
        "resource_type": "Fetch",
    })
    result = fixtures.gate.evaluate_capture(payload)
    assert result["overall"]["pass"] is False
    assert result["metrics"]["network"]["blocking_http_errors"] == 1


@pytest.mark.parametrize(
    "redirect_target",
    [
        f"{fixtures.APP_ORIGIN}/assets/login.js",
        "https://redirect.example.test/login",
    ],
)
def test_retained_api_redirect_to_unreviewed_success_fails_closed(
    redirect_target: str,
) -> None:
    payload = fixtures.capture()
    append_network_response(payload, {
        "channel": "Network.responseReceived",
        "page_family": "kol-pool",
        "unattributed": False,
        "url": redirect_target,
        "status": 200,
        "resource_type": "Fetch",
    })
    result = fixtures.gate.evaluate_capture(payload)
    assert result["overall"]["pass"] is False
    assert any(
        "retained an unreviewed successful resource" in failure
        for failure in result["overall"]["failures"]
    )
