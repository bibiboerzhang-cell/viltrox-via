from __future__ import annotations

import ast
import json
from pathlib import Path
import socket
import sys

import pytest


TESTS_DIR = Path(__file__).resolve().parent
if str(TESTS_DIR) not in sys.path:
    sys.path.insert(0, str(TESTS_DIR))
from test_browser_console_release_gate import (  # noqa: E402
    APP_ASSET,
    APP_ASSET_SHA256,
    APP_ORIGIN,
    GIT_SHA,
    PAGE_MANIFEST_PATH,
    VERIFIER_PATH,
    capture,
    event,
    gate,
    set_network,
)


def test_same_origin_api_http_error_blocks_release() -> None:
    payload = capture()
    set_network(
        payload,
        responses=[
            {
                "channel": "Network.responseReceived",
                "page_family": "bootstrap",
                "url": f"{APP_ORIGIN}/api/auth/me",
                "status": 200,
                "resource_type": "Fetch",
            },
            {
                "channel": "Network.responseReceived",
                "page_family": "dealers",
                "url": f"{APP_ORIGIN}/api/admin/vkpi/dealers",
                "status": 503,
                "resource_type": "Fetch",
            },
        ],
    )
    result = gate.evaluate_capture(payload)
    assert result["overall"]["pass"] is False
    error = result["network"]["responses"][1]
    assert error["provenance"] == "same_origin_api"
    assert error["blocking"] is True
    assert result["metrics"]["network"]["blocking_http_errors"] == 1


def test_external_media_403_needs_exact_origin_and_media_type() -> None:
    payload = capture(
        events=[
            event(
                channel="Log.entryAdded",
                text="Failed to load resource: the server responded with a status of 403",
                source_url="https://media.example/thumb.jpg?temporary=redacted",
                context="",
            )
        ]
    )
    payload["policy"]["external_media_403_allowed_origins"] = [
        "https://media.example"
    ]
    set_network(
        payload,
        responses=[
            {
                "channel": "Network.responseReceived",
                "page_family": "bootstrap",
                "url": f"{APP_ORIGIN}/api/auth/me",
                "status": 200,
                "resource_type": "Fetch",
            },
            {
                "channel": "Network.responseReceived",
                "page_family": "kol-pool",
                "url": "https://media.example/thumb.jpg",
                "status": 403,
                "resource_type": "Image",
            },
        ],
    )
    result = gate.evaluate_capture(payload)
    assert result["overall"]["pass"] is True
    evidence = result["network"]["responses"][1]
    assert evidence["tolerated_external_media_403"] is True
    assert evidence["blocking"] is False
    assert result["metrics"]["network"]["tolerated_external_media_403"] == 1
    assert result["events"][0]["tolerated_external_media_403"] is True
    assert result["events"][0]["blocking"] is False
    assert result["metrics"]["tolerated_external_media_403_console_events"] == 1

    payload["collection"]["network_responses"][1]["resource_type"] = "Fetch"
    result = gate.evaluate_capture(payload)
    assert result["overall"]["pass"] is False
    assert result["network"]["responses"][1]["blocking"] is True


@pytest.mark.parametrize(
    "origin",
    [
        "https://*.example.com",
        "https://media.example/path",
        APP_ORIGIN,
        "http://media.example",
    ],
)
def test_external_media_allowlist_rejects_non_exact_or_unsafe_origins(origin: str) -> None:
    payload = capture()
    payload["policy"]["external_media_403_allowed_origins"] = [origin]
    result = gate.evaluate_capture(payload)
    assert result["overall"]["pass"] is False
    assert any("exact external HTTPS origins" in item for item in result["overall"]["failures"])


def test_non_cancelled_loading_failure_blocks_but_navigation_cancel_is_diagnostic() -> None:
    payload = capture()
    response = payload["collection"]["network_responses"]
    set_network(
        payload,
        responses=response,
        loading_failures=[
            {
                "channel": "Network.loadingFailed",
                "page_family": "events",
                "resource_type": "Fetch",
                "canceled": True,
                "error_text": "net::ERR_ABORTED",
            }
        ],
    )
    assert gate.evaluate_capture(payload)["overall"]["pass"] is True

    payload["collection"]["network_failures"][0]["canceled"] = False
    result = gate.evaluate_capture(payload)
    assert result["overall"]["pass"] is False
    assert result["metrics"]["network"]["blocking_loading_failures"] == 1


def test_page_manifest_file_matches_independent_verifier_contract() -> None:
    payload = json.loads(PAGE_MANIFEST_PATH.read_text(encoding="utf-8"))
    assert payload["schema_version"] == "vkpi-browser-page-manifest/v1"
    actual = {
        row["family"]: (row["nav_key"], row["heading"])
        for row in payload["pages"]
    }
    assert actual == gate.REQUIRED_PAGE_FAMILIES
    assert len(actual) == 21


def test_cli_allows_fixture_only_when_explicit_and_writes_machine_report(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    input_path = tmp_path / "capture.json"
    output_path = tmp_path / "gate.json"
    input_path.write_text(json.dumps(capture(kind="fixture")), encoding="utf-8")
    assert gate.main(
        ["--input", str(input_path), "--json-out", str(output_path), "--allow-fixture"]
    ) == 0
    written = json.loads(output_path.read_text(encoding="utf-8"))
    assert written["overall"]["pass"] is True
    assert written["overall"]["release_eligible"] is False
    assert "PASS browser console release gate" in capsys.readouterr().err


def test_cli_default_rejects_fixture_as_release_evidence(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    input_path = tmp_path / "capture.json"
    input_path.write_text(json.dumps(capture(kind="fixture")), encoding="utf-8")
    assert gate.main(["--input", str(input_path)]) == 1
    assert "FAIL browser console release gate" in capsys.readouterr().err


def test_cli_live_release_fails_without_frozen_candidate_identity(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    input_path = tmp_path / "capture.json"
    input_path.write_text(json.dumps(capture()), encoding="utf-8")
    assert gate.main(["--input", str(input_path)]) == 1
    report = json.loads(capsys.readouterr().out)
    assert report["overall"]["release_eligible"] is False
    assert report["claims"]["frozen_candidate_identity_bound"] is False


def test_cli_live_release_binds_public_evidence_to_frozen_candidate(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    input_path = tmp_path / "capture.json"
    input_path.write_text(json.dumps(capture()), encoding="utf-8")
    assert gate.main(
        [
            "--input",
            str(input_path),
            "--expected-git-sha",
            GIT_SHA,
            "--expected-app-asset",
            APP_ASSET,
            "--expected-app-asset-sha256",
            APP_ASSET_SHA256,
        ]
    ) == 0
    report = json.loads(capsys.readouterr().out)
    assert report["overall"]["release_eligible"] is True
    assert report["claims"]["frozen_candidate_identity_bound"] is True


def test_report_redacts_secrets_and_removes_url_query() -> None:
    secret = event(
        text="Bearer abcdefghijklmnopqrstuvwxyz access_token=topsecret sk-abcdefghijklmnopqrstuv",
        source_url=f"{APP_ORIGIN}/assets/app.js?access_token=topsecret#fragment",
    )
    result = gate.evaluate_capture(capture(events=[secret]))
    serialized = json.dumps(result)
    assert "topsecret" not in serialized
    assert "abcdefghijklmnopqrstuv" not in serialized
    assert "?access_token" not in result["events"][0]["source_url"]
    events_page = next(row for row in result["pages"] if row["family"] == "events")
    assert events_page["final_url"] == f"{APP_ORIGIN}/"
    assert "?" not in events_page["final_url"]
    assert "#" not in events_page["final_url"]
    assert "[REDACTED]" in result["events"][0]["text_preview"]


def test_verifier_imports_no_browser_network_or_runtime_clients_and_opens_no_socket(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tree = ast.parse(VERIFIER_PATH.read_text(encoding="utf-8"))
    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name.split(".", 1)[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module.split(".", 1)[0])
    assert imported.isdisjoint(
        {"aiohttp", "httpx", "playwright", "psycopg", "puppeteer", "redis", "requests", "selenium"}
    )

    def forbidden(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("socket attempted")

    monkeypatch.setattr(socket.socket, "connect", forbidden)
    assert gate.evaluate_capture(capture(), require_live=False)["overall"]["pass"] is True
