from __future__ import annotations

import importlib.util
import json
import subprocess
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
VERIFIER_PATH = ROOT / "scripts" / "verify_browser_console_capture.py"
CAPTURE_PATH = ROOT / "scripts" / "capture_browser_console_cdp.mjs"
PAGE_MANIFEST_PATH = ROOT / "scripts" / "browser_gate_pages.json"
SPEC = importlib.util.spec_from_file_location("vkpi_browser_console_gate", VERIFIER_PATH)
assert SPEC and SPEC.loader
gate = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(gate)


APP_URL = "http://127.0.0.1:8102/"
APP_ORIGIN = "http://127.0.0.1:8102"
GIT_SHA = "a" * 40
APP_ASSET = "app-Test_Candidate-123.js"
APP_ASSET_SHA256 = "b" * 64


def capture(*, kind: str = "live", events: list[dict] | None = None) -> dict:
    page_manifest = [
        {"family": family, "nav_key": contract[0], "heading": contract[1]}
        for family, contract in gate.REQUIRED_PAGE_FAMILIES.items()
    ]
    pages = [
        {
            "family": family,
            "nav_key": contract[0],
            "expected_heading": contract[1],
            "observed_heading": contract[1],
            "navigation_completed": True,
            "page_settled": True,
            "stage_present": True,
            "heading_present": True,
            "heading_matches": True,
            "cockpit_main_present": True,
            "password_form_present": False,
            "lazy_error_present": False,
            "same_origin_api_idle": True,
            "same_origin_api_inflight": 0,
            "ready_state": "complete",
            "final_url": f"{APP_ORIGIN}/?cockpit={contract[0]}",
            "elapsed_ms": 100,
        }
        for family, contract in gate.REQUIRED_PAGE_FAMILIES.items()
    ]
    return {
        "schema_version": gate.CAPTURE_SCHEMA_VERSION,
        "captured_at": "2026-07-14T12:00:00Z",
        "target_url": APP_URL,
        "page_manifest": {
            "schema_version": "vkpi-browser-page-manifest/v1",
            "pages": page_manifest,
        },
        "pages": pages,
        "functional_proof": {
            "ask_find": {
                "attempted": True,
                "trigger_present": True,
                "dialog_present": True,
                "suggestion_applied": True,
                "query_present": True,
                "ask_not_started_before_search": True,
                "ask_clicked": True,
                "completed": True,
                "failure_absent": True,
                "answer_present": True,
                "home_zones_present": True,
                "catalog_suggest_api_error_absent": True,
                "answer_char_count": 42,
                "fact_count": 1,
                "evidence_count": 1,
                "intelligent_api_2xx_count": 1,
                "ui_global_search_api_2xx_count": 1,
                "ui_catalog_suggest_api_2xx_count": 1,
                "same_origin_api_idle": True,
            },
            "global_search": {
                "ui_search_completed": True,
                "ui_usable_state": True,
                "ui_results_rendered": False,
                "ui_trustworthy_empty": True,
                "ui_partial_or_forbidden_absent": True,
                "ui_error_absent": True,
                "ui_result_count": 0,
                "request_completed": True,
                "same_origin": True,
                "http_2xx": True,
                "source_status_present": True,
                "required_sources_present": True,
                "source_status_values_valid": True,
                "all_sources_ready": True,
                "result_counts_valid": True,
                "result_counts_match_arrays": True,
                "optional_sources_valid": True,
                "catalog_probe_completed": True,
                "catalog_http_2xx": True,
                "catalog_items_valid": True,
                "required_source_count": 3,
                "ready_source_count": 3,
                "optional_source_count": 0,
                "result_count_total": 0,
                "result_item_total": 0,
            },
        },
        "policy": {"external_media_403_allowed_origins": []},
        "release_identity": {
            "schema_version": gate.RELEASE_IDENTITY_SCHEMA_VERSION,
            "cache_bypass": {
                "cdp_cache_disabled": True,
                "service_worker_bypassed": True,
                "fetch_cache_mode": "no-store",
                "request_cache_control": "no-cache, no-store, max-age=0",
                "request_pragma": "no-cache",
                "unique_query_parameter": gate.RELEASE_IDENTITY_QUERY_KEY,
                "unique_request_nonces": True,
            },
            "health": {
                "request_completed": True,
                "same_origin": True,
                "http_status": 200,
                "http_2xx": True,
                "status_ok": True,
                "build_git_sha": GIT_SHA,
                "build_client_sha": GIT_SHA,
                "build_client_source": "frontend_dist",
                "server_git_sha": GIT_SHA,
                "client_git_sha": GIT_SHA,
                "sha_aligned": True,
                "response_cache_control": "no-store",
            },
            "frontend": {
                "loaded_app_asset_count": 1,
                "loaded_app_asset": APP_ASSET,
                "index_request_completed": True,
                "index_same_origin": True,
                "index_http_status": 200,
                "index_http_2xx": True,
                "index_content_type_html": True,
                "index_app_asset_count": 1,
                "index_app_asset": APP_ASSET,
                "loaded_matches_index": True,
                "index_response_cache_control": "no-store",
                "asset_request_completed": True,
                "asset_same_origin": True,
                "asset_http_status": 200,
                "asset_http_2xx": True,
                "asset_content_type_javascript": True,
                "asset_bytes": 12345,
                "asset_sha256": APP_ASSET_SHA256,
                "digest_algorithm": "sha256",
                "asset_response_cache_control": "public, max-age=3600",
            },
        },
        "run": {
            "kind": kind,
            "navigation_completed": True,
            "page_settled": True,
            "authenticated_surface": True,
            "auth_probe": {
                "request_completed": True,
                "same_origin": True,
                "token_present": True,
                "http_status": 200,
                "http_2xx": True,
                "status_success": True,
                "user_present": True,
            },
            "surface_probe": {
                "cockpit_main_present": True,
                "password_form_present": False,
            },
            "final_url": APP_URL,
            "ready_state": "complete",
            "settle_ms": 5000,
            "overall_timeout_ms": 600000,
            "overall_elapsed_ms": 120000,
            "overall_deadline_exhausted": False,
        },
        "browser": {
            "engine": "chromium",
            "process_owned": True,
            "profile_mode": "ephemeral",
            "off_the_record": True,
            "credential_persistence": False,
            "credential_isolation": {
                "cross_origin_frame_probed": True,
                "cross_origin_frame_token_absent": True,
                "opaque_origin_observed": True,
                "sandbox_allow_scripts_only": True,
                "csp_bypass_used": False,
                "csp_enforcement_unchanged": True,
            },
            "extensions_disabled": True,
            "launch_args": [
                "--user-data-dir=<ephemeral>",
                "--incognito",
                "--disable-crash-reporter",
                "--disable-extensions",
                "--disable-component-extensions-with-background-pages",
                "--headless=new",
            ],
        },
        "cleanup": {"browser_exited": True, "profile_removed": True},
        "collection": {
            "enabled_domains": ["Page", "Network", "Runtime", "Log"],
            "event_channels": sorted(gate.REQUIRED_CHANNELS),
            "network_event_channels": sorted(gate.REQUIRED_NETWORK_CHANNELS),
            "execution_context_origins": [APP_ORIGIN],
            "events": list(events or []),
            "network_responses": [
                {
                    "channel": "Network.responseReceived",
                    "page_family": "bootstrap",
                    "url": f"{APP_ORIGIN}/api/auth/me",
                    "status": 200,
                    "resource_type": "Fetch",
                },
                {
                    "channel": "Network.responseReceived",
                    "page_family": "bootstrap",
                    "url": f"{APP_ORIGIN}/health",
                    "status": 200,
                    "resource_type": "Fetch",
                    "release_identity_probe": True,
                    "from_disk_cache": False,
                    "from_service_worker": False,
                },
                {
                    "channel": "Network.responseReceived",
                    "page_family": "bootstrap",
                    "url": f"{APP_ORIGIN}/",
                    "status": 200,
                    "resource_type": "Fetch",
                    "release_identity_probe": True,
                    "from_disk_cache": False,
                    "from_service_worker": False,
                },
                {
                    "channel": "Network.responseReceived",
                    "page_family": "bootstrap",
                    "url": f"{APP_ORIGIN}/assets/{APP_ASSET}",
                    "status": 200,
                    "resource_type": "Fetch",
                    "release_identity_probe": True,
                    "from_disk_cache": False,
                    "from_service_worker": False,
                },
                {
                    "channel": "Network.responseReceived",
                    "page_family": "kol-pool",
                    "url": f"{APP_ORIGIN}/api/admin/vkpi/intelligent/query",
                    "status": 200,
                    "resource_type": "Fetch",
                },
                {
                    "channel": "Network.responseReceived",
                    "page_family": "kol-pool",
                    "url": f"{APP_ORIGIN}/api/admin/vkpi/global-search",
                    "status": 200,
                    "resource_type": "Fetch",
                },
                {
                    "channel": "Network.responseReceived",
                    "page_family": "kol-pool",
                    "url": f"{APP_ORIGIN}/api/admin/vkpi/global-search",
                    "status": 200,
                    "resource_type": "Fetch",
                },
                {
                    "channel": "Network.responseReceived",
                    "page_family": "kol-pool",
                    "url": f"{APP_ORIGIN}/api/admin/vkpi/catalog/suggest",
                    "status": 200,
                    "resource_type": "Fetch",
                },
                {
                    "channel": "Network.responseReceived",
                    "page_family": "kol-pool",
                    "url": f"{APP_ORIGIN}/api/admin/vkpi/catalog/suggest",
                    "status": 200,
                    "resource_type": "Fetch",
                },
            ],
            "network_failures": [],
            "network_summary": {
                "response_count_total": 9,
                "request_count_total": 9,
                "response_error_count_total": 0,
                "retained_response_count": 9,
                "loading_failure_count": 0,
                "inflight_same_origin_api_final": 0,
            },
        },
    }


def event(
    *,
    level: str = "error",
    text: str = "boom",
    source_url: str = f"{APP_ORIGIN}/assets/app.js",
    context: str = APP_ORIGIN,
    channel: str = "Runtime.consoleAPICalled",
    stack: list[dict] | None = None,
) -> dict:
    return {
        "channel": channel,
        "level": level,
        "text": text,
        "source_url": source_url,
        "execution_context_origin": context,
        "page_family": "dashboard",
        "stack_trace": list(stack or []),
    }


def set_network(
    payload: dict,
    *,
    responses: list[dict],
    loading_failures: list[dict] | None = None,
) -> None:
    failures = list(loading_failures or [])
    reviewed_functional_responses = [
        {
            "channel": "Network.responseReceived",
            "page_family": "bootstrap",
            "url": f"{APP_ORIGIN}/health",
            "status": 200,
            "resource_type": "Fetch",
            "release_identity_probe": True,
            "from_disk_cache": False,
            "from_service_worker": False,
        },
        {
            "channel": "Network.responseReceived",
            "page_family": "bootstrap",
            "url": f"{APP_ORIGIN}/",
            "status": 200,
            "resource_type": "Fetch",
            "release_identity_probe": True,
            "from_disk_cache": False,
            "from_service_worker": False,
        },
        {
            "channel": "Network.responseReceived",
            "page_family": "bootstrap",
            "url": f"{APP_ORIGIN}/assets/{APP_ASSET}",
            "status": 200,
            "resource_type": "Fetch",
            "release_identity_probe": True,
            "from_disk_cache": False,
            "from_service_worker": False,
        },
        {
            "channel": "Network.responseReceived",
            "page_family": "kol-pool",
            "url": f"{APP_ORIGIN}/api/admin/vkpi/intelligent/query",
            "status": 200,
            "resource_type": "Fetch",
        },
        {
            "channel": "Network.responseReceived",
            "page_family": "kol-pool",
            "url": f"{APP_ORIGIN}/api/admin/vkpi/global-search",
            "status": 200,
            "resource_type": "Fetch",
        },
        {
            "channel": "Network.responseReceived",
            "page_family": "kol-pool",
            "url": f"{APP_ORIGIN}/api/admin/vkpi/global-search",
            "status": 200,
            "resource_type": "Fetch",
        },
    ]
    response_paths: dict[tuple[str, str], int] = {}
    for row in responses:
        key = (str(row.get("page_family") or ""), str(row.get("url") or ""))
        response_paths[key] = response_paths.get(key, 0) + 1
    for row in reviewed_functional_responses:
        key = (row["page_family"], row["url"])
        if response_paths.get(key, 0) == 0:
            responses.append(row)
        else:
            response_paths[key] -= 1
    payload["collection"]["network_responses"] = responses
    payload["collection"]["network_failures"] = failures
    payload["collection"]["network_summary"] = {
        "response_count_total": len(responses),
        "request_count_total": len(responses),
        "response_error_count_total": sum(
            1 for row in responses if int(row.get("status") or 0) >= 400
        ),
        "retained_response_count": len(responses),
        "loading_failure_count": len(failures),
        "inflight_same_origin_api_final": 0,
    }


def test_clean_owned_ephemeral_extension_free_live_capture_passes() -> None:
    result = gate.evaluate_capture(
        capture(events=[event(level="info", text="ready")]),
        expected_git_sha=GIT_SHA,
        expected_app_asset=APP_ASSET,
        expected_app_asset_sha256=APP_ASSET_SHA256,
        require_expected_identity=True,
    )
    assert result["overall"] == {"pass": True, "release_eligible": True, "failures": []}
    assert result["metrics"]["blocking_events"] == 0
    assert result["metrics"]["functional_proof"] == {
        "ask_find_pass": True,
        "global_search_pass": True,
        "pass": True,
        "network_evidence_pass": True,
        "network_counts": {
            "intelligent_api_2xx": 1,
            "global_search_api_2xx": 2,
            "catalog_suggest_api_2xx": 2,
            "catalog_suggest_api_non_2xx": 0,
        },
    }
    assert result["claims"]["live_functional_journey_completed"] is True
    assert result["claims"]["live_extension_free_run_completed"] is True
    assert result["claims"]["frozen_candidate_identity_bound"] is True


def test_live_release_evaluation_requires_all_frozen_candidate_expectations() -> None:
    result = gate.evaluate_capture(
        capture(),
        require_expected_identity=True,
    )
    assert result["overall"]["pass"] is False
    assert result["overall"]["release_eligible"] is False
    assert result["capture"]["release_identity_proof"]["internal_pass"] is True
    assert result["capture"]["release_identity_proof"]["candidate_binding_pass"] is False
    assert any(
        "frozen candidate identity binding failed" in item
        for item in result["overall"]["failures"]
    )


@pytest.mark.parametrize(
    ("expected_overrides", "failed_proof"),
    [
        ({"expected_git_sha": "c" * 40}, "health_server_git_sha_matches"),
        ({"expected_app_asset": "app-Other_Candidate.js"}, "loaded_app_asset_matches"),
        ({"expected_app_asset_sha256": "d" * 64}, "asset_sha256_matches"),
    ],
)
def test_frozen_candidate_sha_asset_or_digest_mismatch_fails_closed(
    expected_overrides: dict[str, str],
    failed_proof: str,
) -> None:
    expected = {
        "expected_git_sha": GIT_SHA,
        "expected_app_asset": APP_ASSET,
        "expected_app_asset_sha256": APP_ASSET_SHA256,
    }
    expected.update(expected_overrides)
    result = gate.evaluate_capture(
        capture(),
        require_expected_identity=True,
        **expected,
    )
    proof = result["capture"]["release_identity_proof"]
    assert result["overall"]["pass"] is False
    assert result["overall"]["release_eligible"] is False
    assert proof["internal_pass"] is True
    assert proof["candidate_proof"][failed_proof] is False


def test_coherent_old_public_version_cannot_pass_new_frozen_candidate() -> None:
    payload = capture()
    old_sha = "c" * 40
    old_asset = "app-Old_Cached.js"
    old_digest = "d" * 64
    payload["release_identity"]["health"].update(
        {
            "build_git_sha": old_sha,
            "build_client_sha": old_sha,
            "server_git_sha": old_sha,
            "client_git_sha": old_sha,
        }
    )
    payload["release_identity"]["frontend"].update(
        {
            "loaded_app_asset": old_asset,
            "index_app_asset": old_asset,
            "asset_sha256": old_digest,
        }
    )
    asset_response = next(
        row
        for row in payload["collection"]["network_responses"]
        if str(row["url"]).startswith(f"{APP_ORIGIN}/assets/")
    )
    asset_response["url"] = f"{APP_ORIGIN}/assets/{old_asset}"

    result = gate.evaluate_capture(
        payload,
        expected_git_sha=GIT_SHA,
        expected_app_asset=APP_ASSET,
        expected_app_asset_sha256=APP_ASSET_SHA256,
        require_expected_identity=True,
    )
    proof = result["capture"]["release_identity_proof"]
    assert proof["internal_pass"] is True
    assert proof["candidate_binding_pass"] is False
    assert result["overall"]["release_eligible"] is False


@pytest.mark.parametrize("cache_field", ["from_disk_cache", "from_service_worker"])
def test_cached_or_service_worker_identity_asset_fails_closed(cache_field: str) -> None:
    payload = capture()
    asset_response = next(
        row
        for row in payload["collection"]["network_responses"]
        if str(row["url"]).startswith(f"{APP_ORIGIN}/assets/")
    )
    asset_response[cache_field] = True
    result = gate.evaluate_capture(
        payload,
        expected_git_sha=GIT_SHA,
        expected_app_asset=APP_ASSET,
        expected_app_asset_sha256=APP_ASSET_SHA256,
        require_expected_identity=True,
    )
    proof = result["capture"]["release_identity_proof"]
    assert result["overall"]["pass"] is False
    assert proof["internal_proof"]["network_app_asset_probe_uncached"] is False


def test_loaded_and_fresh_index_app_assets_must_match() -> None:
    payload = capture()
    payload["release_identity"]["frontend"]["index_app_asset"] = "app-Fresh.js"
    payload["release_identity"]["frontend"]["loaded_matches_index"] = True
    result = gate.evaluate_capture(payload)
    assert result["overall"]["pass"] is False
    proof = result["capture"]["release_identity_proof"]["internal_proof"]
    assert proof["loaded_matches_index"] is False


def test_missing_functional_proof_fails_closed() -> None:
    payload = capture()
    del payload["functional_proof"]
    result = gate.evaluate_capture(payload)
    assert result["overall"]["pass"] is False
    assert result["claims"]["live_functional_journey_completed"] is False
    assert any("functional_proof" in item for item in result["overall"]["failures"])


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("completed", False),
        ("ask_not_started_before_search", False),
        ("ask_clicked", False),
        ("answer_present", False),
        ("answer_char_count", 0),
        ("home_zones_present", False),
        ("catalog_suggest_api_error_absent", False),
        ("intelligent_api_2xx_count", 0),
        ("ui_global_search_api_2xx_count", 0),
    ],
)
def test_incomplete_ask_find_journey_fails_closed(field: str, value: object) -> None:
    payload = capture()
    payload["functional_proof"]["ask_find"][field] = value
    result = gate.evaluate_capture(payload)
    assert result["overall"]["pass"] is False
    assert result["metrics"]["functional_proof"]["ask_find_pass"] is False
    assert any("functional Ask & Find proof failed" in item for item in result["overall"]["failures"])


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("all_sources_ready", False),
        ("ready_source_count", 2),
        ("required_source_count", 4),
        ("result_counts_match_arrays", False),
        ("required_sources_present", False),
        ("optional_sources_valid", False),
        ("catalog_probe_completed", False),
        ("catalog_http_2xx", False),
        ("catalog_items_valid", False),
        ("ui_search_completed", False),
        ("ui_usable_state", False),
        ("ui_partial_or_forbidden_absent", False),
        ("ui_error_absent", False),
    ],
)
def test_untrustworthy_global_search_source_proof_fails_closed(
    field: str,
    value: object,
) -> None:
    payload = capture()
    payload["functional_proof"]["global_search"][field] = value
    result = gate.evaluate_capture(payload)
    assert result["overall"]["pass"] is False
    assert result["metrics"]["functional_proof"]["global_search_pass"] is False
    assert any(
        "global search source-truth proof failed" in item
        for item in result["overall"]["failures"]
    )


@pytest.mark.parametrize(
    ("results_rendered", "trustworthy_empty", "result_count"),
    [
        (True, True, 1),
        (False, False, 0),
        (True, False, 0),
        (False, True, 1),
    ],
)
def test_ui_search_requires_exactly_one_consistent_rendered_outcome(
    results_rendered: bool,
    trustworthy_empty: bool,
    result_count: int,
) -> None:
    payload = capture()
    search = payload["functional_proof"]["global_search"]
    search["ui_results_rendered"] = results_rendered
    search["ui_trustworthy_empty"] = trustworthy_empty
    search["ui_result_count"] = result_count
    result = gate.evaluate_capture(payload)
    assert result["overall"]["pass"] is False
    assert result["metrics"]["functional_proof"]["global_search_pass"] is False


@pytest.mark.parametrize(
    ("field", "value", "failure"),
    [
        ("overall_timeout_ms", 59_999, "overall timeout"),
        ("overall_elapsed_ms", -1, "overall elapsed"),
        ("overall_deadline_exhausted", True, "deadline was exhausted"),
        ("overall_elapsed_ms", 600_001, "exceeds its overall deadline"),
    ],
)
def test_single_overall_deadline_proof_is_mandatory(
    field: str,
    value: object,
    failure: str,
) -> None:
    payload = capture()
    payload["run"][field] = value
    result = gate.evaluate_capture(payload)
    assert result["overall"]["pass"] is False
    assert any(failure in item for item in result["overall"]["failures"])


def test_capture_cli_rejects_an_unbounded_or_too_short_overall_deadline(
    tmp_path: Path,
) -> None:
    completed = subprocess.run(
        [
            "node",
            str(CAPTURE_PATH),
            "--url",
            APP_URL,
            "--output",
            str(tmp_path / "capture.json"),
            "--overall-timeout-ms",
            "59999",
        ],
        check=False,
        capture_output=True,
        text=True,
    )
    assert completed.returncode == 2
    assert (
        "browser console capture failed stage=argument_validation "
        "code=invalid_overall_timeout"
    ) in completed.stderr
    assert "--overall-timeout-ms must be an integer within" not in completed.stderr
    assert not (tmp_path / "capture.json").exists()


def test_functional_proof_accepts_only_boolean_counts_and_never_emits_answer_text() -> None:
    payload = capture()
    payload["functional_proof"]["ask_find"]["answer_text"] = "private answer body"
    payload["functional_proof"]["ask_find"]["fact_count"] = "1"
    result = gate.evaluate_capture(payload)
    assert result["overall"]["pass"] is False
    assert "private answer body" not in json.dumps(result)
    assert "answer_text" not in result["functional_proof"]["ask_find"]
    assert any(
        "reviewed boolean/count fields" in item
        for item in result["overall"]["failures"]
    )


def test_functional_counts_must_match_retained_live_network_evidence() -> None:
    payload = capture()
    payload["collection"]["network_responses"] = [
        row
        for row in payload["collection"]["network_responses"]
        if not row["url"].endswith("/api/admin/vkpi/intelligent/query")
    ]
    payload["collection"]["network_summary"].update(
        {
            "response_count_total": 3,
            "request_count_total": 3,
            "retained_response_count": 3,
        }
    )
    result = gate.evaluate_capture(payload)
    assert result["overall"]["pass"] is False
    assert result["metrics"]["functional_proof"]["network_evidence_pass"] is False
    assert any(
        "functional journey network evidence" in item
        for item in result["overall"]["failures"]
    )


def test_invalid_token_cannot_be_promoted_by_authenticated_surface_claim() -> None:
    payload = capture()
    payload["run"]["authenticated_surface"] = True
    payload["run"]["auth_probe"].update(
        {
            "http_status": 200,
            "http_2xx": True,
            "status_success": False,
            "user_present": False,
        }
    )
    result = gate.evaluate_capture(payload)
    assert result["overall"]["pass"] is False
    assert result["overall"]["release_eligible"] is False
    assert any("status_success" in item for item in result["overall"]["failures"])
    assert any("user_present" in item for item in result["overall"]["failures"])


def test_login_page_cannot_pass_even_when_auth_probe_succeeds() -> None:
    payload = capture()
    payload["run"]["authenticated_surface"] = True
    payload["run"]["surface_probe"] = {
        "cockpit_main_present": False,
        "password_form_present": True,
    }
    result = gate.evaluate_capture(payload)
    assert result["overall"]["pass"] is False
    assert result["overall"]["release_eligible"] is False
    assert any("cockpit_main_present" in item for item in result["overall"]["failures"])
    assert any("password_form_absent" in item for item in result["overall"]["failures"])


@pytest.mark.parametrize("level", ["warning", "error", "exception", "assert", "pageerror", "unhandledrejection"])
def test_every_application_warning_or_error_level_blocks(level: str) -> None:
    result = gate.evaluate_capture(capture(events=[event(level=level)]))
    assert result["overall"]["pass"] is False
    assert result["metrics"]["blocking_events"] == 1
    assert result["events"][0]["provenance"] == "application"
    assert result["events"][0]["blocking"] is True


def test_background_js_text_is_not_an_extension_exemption() -> None:
    application = event(
        text="FrameDoesNotExistError at background.js:1",
        source_url=f"{APP_ORIGIN}/assets/background.js",
    )
    result = gate.evaluate_capture(capture(events=[application]))
    assert result["overall"]["pass"] is False
    assert result["events"][0]["provenance"] == "application"
    assert result["metrics"]["blocking_events"] == 1


def test_unattributed_frame_error_fails_closed_instead_of_matching_text() -> None:
    unknown = event(
        text="FrameDoesNotExistError at background.js:1",
        source_url="",
        context="",
    )
    result = gate.evaluate_capture(capture(events=[unknown]))
    assert result["events"][0]["provenance"] == "unattributed"
    assert result["events"][0]["blocking"] is True


def test_explicit_extension_provenance_is_diagnostic_but_invalidates_extension_free_proof() -> None:
    extension = event(
        text="FrameDoesNotExistError",
        source_url="chrome-extension://abcdefghijklmnop/background.js",
        context="chrome-extension://abcdefghijklmnop",
    )
    payload = capture(events=[extension])
    payload["collection"]["execution_context_origins"].append(
        "chrome-extension://abcdefghijklmnop"
    )
    result = gate.evaluate_capture(payload)
    assert result["events"][0]["provenance"] == "extension_noise"
    assert result["events"][0]["blocking"] is False
    assert result["metrics"]["extension_noise_events"] == 1
    assert result["overall"]["pass"] is False
    assert "extension event observed in extension-free capture" in result["overall"]["failures"]
    assert "extension execution context observed in extension-free capture" in result["overall"]["failures"]


def test_application_provenance_wins_when_extension_frame_is_also_present() -> None:
    mixed = event(
        source_url=f"{APP_ORIGIN}/assets/app.js",
        stack=[{"url": "chrome-extension://abcdefghijklmnop/content.js"}],
    )
    assert gate.classify_event(mixed, application_origin=APP_ORIGIN) == "application"


def test_third_party_and_browser_internal_errors_are_not_allowlisted() -> None:
    third_party = event(source_url="https://cdn.example/media.js", context="")
    internal = event(source_url="devtools://devtools/bundled/shell.js", context="")
    result = gate.evaluate_capture(capture(events=[third_party, internal]))
    assert [row["provenance"] for row in result["events"]] == [
        "third_party",
        "browser_internal",
    ]
    assert result["metrics"]["blocking_events"] == 2
    assert result["overall"]["pass"] is False


@pytest.mark.parametrize(
    ("field", "value", "failure"),
    [
        ("process_owned", False, "process_owned"),
        ("profile_mode", "persistent", "profile_mode_ephemeral"),
        ("off_the_record", False, "off_the_record"),
        ("credential_persistence", True, "credential_persistence_disabled"),
        ("extensions_disabled", False, "extensions_disabled"),
    ],
)
def test_extension_free_proof_is_mandatory(field: str, value: object, failure: str) -> None:
    payload = capture()
    payload["browser"][field] = value
    result = gate.evaluate_capture(payload)
    assert result["overall"]["pass"] is False
    assert any(failure in item for item in result["overall"]["failures"])


@pytest.mark.parametrize(
    ("field", "value", "failure"),
    [
        ("cross_origin_frame_probed", False, "cross_origin_frame_probed"),
        ("cross_origin_frame_token_absent", False, "cross_origin_frame_token_absent"),
        ("opaque_origin_observed", False, "opaque_origin_observed"),
        ("sandbox_allow_scripts_only", False, "sandbox_allow_scripts_only"),
        ("csp_bypass_used", True, "csp_bypass_unused"),
        ("csp_enforcement_unchanged", False, "csp_enforcement_unchanged"),
    ],
)
def test_cross_origin_credential_isolation_proof_is_mandatory(
    field: str, value: bool, failure: str
) -> None:
    payload = capture()
    payload["browser"]["credential_isolation"][field] = value
    result = gate.evaluate_capture(payload)
    assert result["overall"]["pass"] is False
    assert any(failure in item for item in result["overall"]["failures"])


def test_cross_origin_isolation_and_same_origin_ask_are_jointly_required() -> None:
    result = gate.evaluate_capture(capture())
    assert result["capture"]["extension_free_proof"]["cross_origin_frame_token_absent"] is True
    assert result["capture"]["extension_free_proof"]["opaque_origin_observed"] is True
    assert result["capture"]["extension_free_proof"]["csp_bypass_unused"] is True
    assert result["capture"]["authenticated_surface_proof"]["token_present"] is True
    assert result["metrics"]["functional_proof"]["ask_find_pass"] is True
    assert result["overall"]["pass"] is True


def test_disable_extension_flags_are_mandatory() -> None:
    payload = capture()
    payload["browser"]["launch_args"] = ["--headless=new"]
    result = gate.evaluate_capture(payload)
    assert result["overall"]["pass"] is False
    assert any("disable_extensions_flag" in item for item in result["overall"]["failures"])
    assert any("component_background_extensions_disabled" in item for item in result["overall"]["failures"])


def test_owned_browser_cleanup_and_ephemeral_profile_flag_are_mandatory() -> None:
    payload = capture()
    payload["browser"]["engine"] = "unknown"
    payload["browser"]["launch_args"] = [
        item for item in payload["browser"]["launch_args"] if not item.startswith("--user-data-dir=")
    ]
    payload["cleanup"] = {"browser_exited": False, "profile_removed": False}
    result = gate.evaluate_capture(payload)
    assert result["overall"]["pass"] is False
    for proof in (
        "engine_chromium",
        "ephemeral_user_data_dir_flag",
        "owned_browser_exited",
        "ephemeral_profile_removed",
    ):
        assert any(proof in item for item in result["overall"]["failures"])


def test_fixture_can_test_contract_but_cannot_pass_release_evaluation() -> None:
    payload = capture(kind="fixture")
    contract_result = gate.evaluate_capture(payload, require_live=False)
    release_result = gate.evaluate_capture(payload, require_live=True)
    assert contract_result["overall"]["pass"] is True
    assert contract_result["overall"]["release_eligible"] is False
    assert release_result["overall"]["pass"] is False
    assert "release evaluation requires run.kind=live" in release_result["overall"]["failures"]


def test_missing_console_channel_fails_closed() -> None:
    payload = capture()
    payload["collection"]["event_channels"].remove("Log.entryAdded")
    result = gate.evaluate_capture(payload)
    assert result["overall"]["pass"] is False
    assert any("missing event channels" in item for item in result["overall"]["failures"])


def test_reviewed_page_manifest_and_capture_require_all_21_families() -> None:
    payload = capture()
    payload["pages"] = payload["pages"][:-1]
    result = gate.evaluate_capture(payload)
    assert result["overall"]["pass"] is False
    assert result["metrics"]["pages"]["required"] == 21
    assert "gtmCommand" in result["metrics"]["pages"]["missing"]
    assert any("exactly 21" in item for item in result["overall"]["failures"])


def test_wrong_page_heading_or_lazy_error_fails_closed() -> None:
    payload = capture()
    events_page = next(row for row in payload["pages"] if row["family"] == "events")
    events_page["observed_heading"] = "Dashboard"
    events_page["lazy_error_present"] = True
    result = gate.evaluate_capture(payload)
    assert result["overall"]["pass"] is False
    page = next(row for row in result["pages"] if row["family"] == "events")
    assert page["pass"] is False
    assert page["proof"]["observed_heading_matches"] is False
    assert page["proof"]["lazy_error_absent"] is False


@pytest.mark.parametrize(
    "final_url",
    [
        f"{APP_ORIGIN}/#cockpit",
        f"{APP_ORIGIN}/?cockpit=dashboard#cockpit",
        f"{APP_ORIGIN}/?cockpit=events&cockpit=dealers#cockpit",
        f"{APP_ORIGIN}/#cockpit?cockpit=events",
    ],
)
def test_page_without_exact_cockpit_query_fails_closed(final_url: str) -> None:
    payload = capture()
    page = next(row for row in payload["pages"] if row["family"] == "events")
    page["final_url"] = final_url
    result = gate.evaluate_capture(payload)
    assert result["overall"]["pass"] is False
    evidence = next(row for row in result["pages"] if row["family"] == "events")
    assert evidence["proof"]["cockpit_query_matches_nav_key"] is False
    assert "?" not in evidence["final_url"]


def test_page_with_inflight_same_origin_api_cannot_pass() -> None:
    payload = capture()
    page = next(row for row in payload["pages"] if row["family"] == "gtmCommand")
    page["same_origin_api_idle"] = False
    page["same_origin_api_inflight"] = 1
    result = gate.evaluate_capture(payload)
    assert result["overall"]["pass"] is False
    evidence = next(row for row in result["pages"] if row["family"] == "gtmCommand")
    assert evidence["proof"]["same_origin_api_idle"] is False
    assert evidence["proof"]["same_origin_api_inflight_zero"] is False
