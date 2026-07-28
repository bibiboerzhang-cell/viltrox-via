from __future__ import annotations

import ast
import importlib.util
import json
import socket
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
        "policy": {"external_media_403_allowed_origins": []},
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
        },
        "browser": {
            "engine": "chromium",
            "process_owned": True,
            "profile_mode": "ephemeral",
            "off_the_record": True,
            "credential_persistence": False,
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
                }
            ],
            "network_failures": [],
            "network_summary": {
                "response_count_total": 1,
                "request_count_total": 1,
                "response_error_count_total": 0,
                "retained_response_count": 1,
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
    result = gate.evaluate_capture(capture(events=[event(level="info", text="ready")]))
    assert result["overall"] == {"pass": True, "release_eligible": True, "failures": []}
    assert result["metrics"]["blocking_events"] == 0
    assert result["claims"]["live_extension_free_run_completed"] is True


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


def test_capture_script_owns_an_ephemeral_extension_disabled_chromium_and_all_channels() -> None:
    source = CAPTURE_PATH.read_text(encoding="utf-8")
    for required in (
        "mkdtempSync",
        '"--disable-extensions"',
        '"--disable-component-extensions-with-background-pages"',
        '"Runtime.consoleAPICalled"',
        '"Runtime.exceptionThrown"',
        '"Log.entryAdded"',
        '"Runtime.executionContextCreated"',
        'profile_mode: "ephemeral"',
        "rmSync(profileDir",
        "new URL('/api/auth/me', location.href)",
        "body?.status === 'success'",
        "body?.user && typeof body.user === 'object'",
        "const AUTH_PROBE_TIMEOUT_MS = 5000",
        "signal: AbortSignal.timeout(${AUTH_PROBE_TIMEOUT_MS})",
        "async function requireAuthentication(session)",
        'throw new Error("browser_gate_token_expired")',
        "await requireAuthentication(session)",
        "browser_gate_first_page_failed",
        "document.querySelector('.cockpit-shell main')",
        "document.querySelector('input[type=\"password\"]')",
        "authenticated_surface: authenticatedSurface",
        "delete chromeEnv.VKPI_BROWSER_GATE_TOKEN",
        'spawn(chromePath, launchArgs, { stdio: "ignore", env: chromeEnv })',
        '"--incognito"',
        '"--disable-crash-reporter"',
        'await session.send("Network.enable")',
        'await session.send("Network.setCookie", {',
        'name: "via_token"',
        "value: token",
        'url: new URL("/", targetUrl.origin).href',
        'path: "/"',
        'httpOnly: true',
        'secure: targetUrl.protocol === "https:"',
        'sameSite: "Lax"',
        "if (authCookie.success !== true)",
        "Storage.prototype.getItem = function(key)",
        "Storage.prototype.setItem = function(key, value)",
        "Storage.prototype.removeItem = function(key)",
        "serializedCapture.includes(token)",
        "off_the_record: true",
        "credential_persistence: false",
        'enabled_domains: ["Page", "Network", "Runtime", "Log"]',
        '"Network.responseReceived"',
        '"Network.requestWillBeSent"',
        '"Network.loadingFinished"',
        '"Network.loadingFailed"',
        "browser_gate_pages.json",
        "pageManifest.pages",
        "navigateAndProbePage",
        "beginFullDocumentNavigation",
        "const API_IDLE_GRACE_MS = 1000",
        "const apiIdleDeadline = Math.max(deadline, Date.now() + API_IDLE_GRACE_MS)",
        "waitForFinalSameOriginApiIdle",
        "final same-origin API requests did not become idle before timeout",
        "navigation_discarded_prior_api: navigationDiscardedPriorApi",
        "same_origin_api_inflight_diagnostics",
        "navigation_discarded_same_origin_api_total",
        "external_media_403_allowed_origins: allowedExternalMedia403Origins",
        "response_error_count_total: session.networkResponseErrorTotal",
        "inflight_same_origin_api_final: session.inflightSameOriginApi.size",
    ):
        assert required in source
    assert 'authenticated_surface: true' not in source
    assert "FrameDoesNotExistError" not in source
    assert "background.js" not in source
    assert "VKPI_BROWSER_GATE_TOKEN=" not in source
    assert "localStorage.setItem('viltrox_marketing_token_v1'" not in source
    assert "?token=" not in source
    assert "?access_token=" not in source
    assert "--token" not in source
    assert "Network.getResponseBody" not in source

    network_enable = source.index('await session.send("Network.enable")')
    cookie_injection = source.index('await session.send("Network.setCookie", {')
    page_navigation = source.index('await session.send("Page.navigate", { url: args.url })')
    assert network_enable < cookie_injection < page_navigation

    per_page_navigation = source.split(
        "async function navigateAndProbePage(session, baseUrl, page, timeoutMs, settleMs)",
        1,
    )[1].split("async function waitForFinalSameOriginApiIdle", 1)[0]
    discard_at = per_page_navigation.index("session.beginFullDocumentNavigation()")
    auth_at = per_page_navigation.index("await requireAuthentication(session)")
    target_at = per_page_navigation.index("const target = pageUrl", auth_at)
    navigate_at = per_page_navigation.index('await session.send("Page.navigate"', target_at)
    assert auth_at < target_at < discard_at < navigate_at

    page_loop = source.split(
        "for (const [pageIndex, page] of pageManifest.pages.entries())",
        1,
    )[1].split('const pageState = await session.send("Runtime.evaluate"', 1)[0]
    result_at = page_loop.index("const pageResult = await navigateAndProbePage")
    retain_at = page_loop.index("pages.push(pageResult)", result_at)
    fail_fast_at = page_loop.index("pageIndex === 0", retain_at)
    explicit_error_at = page_loop.index("browser_gate_first_page_failed", fail_fast_at)
    assert result_at < retain_at < fail_fast_at < explicit_error_at

    final_state_read = source.index('const pageState = await session.send("Runtime.evaluate", {')
    final_idle = source.index(
        "await waitForFinalSameOriginApiIdle(session, args.pageTimeoutMs)",
        final_state_read,
    )
    final_snapshot = source.index("const finalNetworkSnapshot = {", final_idle)
    capture_assignment = source.index("capture = {", final_snapshot)
    assert final_state_read < final_idle < final_snapshot < capture_assignment
    assert "inflight_same_origin_api_final: finalNetworkSnapshot.inflight_same_origin_api_final" in source
