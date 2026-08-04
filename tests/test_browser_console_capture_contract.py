from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import subprocess


ROOT = Path(__file__).resolve().parents[1]
CAPTURE_PATH = ROOT / "scripts" / "capture_browser_console_cdp.mjs"
RUNTIME_PATH = ROOT / "scripts" / "browser_console_capture_runtime.mjs"
ISOLATION_PATH = ROOT / "scripts" / "browser_console_token_isolation.mjs"
PIPE_PATH = ROOT / "scripts" / "browser_console_cdp_pipe.mjs"
SECRET_SCAN_PATH = ROOT / "scripts" / "browser_capture_secret_scan.mjs"


def test_capture_script_owns_an_ephemeral_extension_disabled_chromium_and_all_channels() -> None:
    capture_source = CAPTURE_PATH.read_text(encoding="utf-8")
    runtime_source = RUNTIME_PATH.read_text(encoding="utf-8")
    isolation_source = ISOLATION_PATH.read_text(encoding="utf-8")
    pipe_source = PIPE_PATH.read_text(encoding="utf-8")
    secret_scan_source = SECRET_SCAN_PATH.read_text(encoding="utf-8")
    combined_source = (
        f"{capture_source}\n{runtime_source}\n{isolation_source}\n"
        f"{pipe_source}\n{secret_scan_source}"
    )
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
        "signal: AbortSignal.timeout(${fetchTimeoutMs})",
        "async function requireAuthentication(session)",
        'throw new Error("browser_gate_token_expired")',
        "await requireAuthentication(session)",
        "browser_gate_first_page_failed",
        "document.querySelector('.cockpit-shell main')",
        "document.querySelector('input[type=\"password\"]')",
        "authenticated_surface: authenticatedSurface",
        '"--remote-debugging-pipe"',
        "new CdpPipeConnection(browser, overallDeadline)",
        "attachFirstPageTarget(connection, overallDeadline)",
        'connection.send("Target.setDiscoverTargets", { discover: true })',
        'connection.send("Target.getTargets",',
        'connection.send("Target.attachToTarget", {',
        "flatten: true",
        "chromeChildEnvironment(process.env)",
        'stdio: ["ignore", "ignore", "ignore", "pipe", "pipe"]',
        "const writer = child?.stdio?.[3]",
        "const reader = child?.stdio?.[4]",
        "this.buffer.indexOf(0)",
        "if (sessionId) command.sessionId = sessionId",
        '"--incognito"',
        '"--disable-crash-reporter"',
        'await session.send("Network.enable")',
        'await session.send("Network.setCacheDisabled", { cacheDisabled: true })',
        'await session.send("Network.setBypassServiceWorker", { bypass: true })',
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
        "const targetOrigin = ${JSON.stringify(targetUrl.origin)}",
        "if (location.origin !== targetOrigin) return",
        "proveOpaqueOriginTokenIsolation",
        "iframe.setAttribute('sandbox', 'allow-scripts')",
        "iframe.srcdoc =",
        "location.origin !== 'null'",
        "event.origin === 'null'",
        "parent.postMessage({ token_present: tokenPresent }, '*')",
        "sandbox_allow_scripts_only",
        "csp_bypass_used: false",
        "csp_enforcement_unchanged: true",
        "cross_origin_frame_token_absent",
        "assertBrowserCaptureCredentialFree(serializedCapture, [token])",
        '"database_dsn"',
        '"provider_key"',
        '"credential_assignment"',
        '"provider_credential_assignment"',
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
        "const apiIdleDeadline = Math.min(",
        "session.overallDeadline.expiresAt",
        "Math.max(deadline, Date.now() + API_IDLE_GRACE_MS)",
        "waitForFinalSameOriginApiIdle",
        "final same-origin API requests did not become idle before timeout",
        "navigation_discarded_prior_api: navigationDiscardedPriorApi",
        "same_origin_api_inflight_diagnostics",
        "navigation_discarded_same_origin_api_total",
        "external_media_403_allowed_origins: allowedExternalMedia403Origins",
        "response_error_count_total: session.networkResponseErrorTotal",
        "inflight_same_origin_api_final: session.inflightSameOriginApi.size",
        'else if (item === "--overall-timeout-ms")',
        "class OverallDeadline",
        "this.overallDeadline.boundedTimeoutMs(requestedTimeoutMs)",
        "overall_timeout_ms: args.overallTimeoutMs",
        "overall_deadline_exhausted: false",
        "runKolPoolFunctionalJourney",
        "probeGlobalSearchSourceTruth",
        "functional_proof: functionalProof",
        "document.querySelector('.vkpi-ask-trigger')",
        "document.querySelector('.vkpi-ask-dialog__suggestions button')",
        "Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value')?.set",
        "document.querySelector('.vkpi-ask-dialog__state.is-empty')",
        "document.querySelector('.vkpi-ask-dialog__state.is-warning')",
        "document.querySelector('.vkpi-ask-dialog__state.is-error')",
        "document.querySelector('.vkpi-ask-dialog__ask')",
        "document.querySelector('.vkpi-ask-dialog__answer.is-ready')",
        "document.querySelectorAll('.vkpi-ask-dialog__facts article')",
        "document.querySelectorAll('.vkpi-ask-dialog__evidence article')",
        '"/api/admin/vkpi/intelligent/query"',
        '"/api/admin/vkpi/global-search"',
        "const requiredSources = ['kols', 'projects', 'events']",
        "async function probeReleaseIdentity(session)",
        "_vkpi_release_probe",
        "cache: 'no-store'",
        "'Cache-Control': 'no-cache, no-store, max-age=0'",
        "new URL('/health', location.origin)",
        "new URL('/', location.origin)",
        "indexDocument.querySelectorAll('script[src]')",
        "crypto.subtle.digest('SHA-256', assetBody)",
        "loaded_matches_index",
        "release_identity: releaseIdentity",
        "release_identity_probe: releaseIdentityProbe",
    ):
        assert required in combined_source
    assert 'authenticated_surface: true' not in combined_source
    assert "FrameDoesNotExistError" not in combined_source
    assert "background.js" not in combined_source
    assert "VKPI_BROWSER_GATE_TOKEN=" not in combined_source
    assert "localStorage.setItem('viltrox_marketing_token_v1'" not in combined_source
    assert "?token=" not in combined_source
    assert "?access_token=" not in combined_source
    assert "--token" not in combined_source
    assert "--remote-debugging-port" not in combined_source
    assert "freeLoopbackPort" not in combined_source
    assert "/json/list" not in combined_source
    assert "webSocketDebuggerUrl" not in combined_source
    assert "new WebSocket(" not in combined_source
    assert 'from "node:net"' not in combined_source
    assert "...process.env" not in combined_source
    assert "Network.getResponseBody" not in combined_source
    assert "Page.setBypassCSP" not in combined_source
    assert isolation_source.count("setAttribute('sandbox'") == 1
    assert "!iframe.sandbox.contains('allow-same-origin')" in isolation_source
    assert "createServer" not in isolation_source
    assert "127.0.0.1" not in isolation_source
    assert "http://" not in isolation_source
    message_payload = isolation_source.split("parent.postMessage(", 1)[1].split(", '*'", 1)[0]
    assert message_payload.strip() == "{ token_present: tokenPresent }"
    assert "目前 KOL 数量是多少" not in combined_source
    assert "How many KOLs are in the pool" not in combined_source
    assert "suggestion.click()" not in combined_source
    assert combined_source.count("await sleep(") == 1
    assert 'from "./browser_console_capture_runtime.mjs"' in capture_source
    assert 'from "./browser_capture_secret_scan.mjs"' in capture_source
    assert "serializedCapture.includes(token)" not in capture_source

    injection = capture_source.split('source: `(() => {', 1)[1].split('})();`,', 1)[0]
    origin_at = injection.index("const targetOrigin =")
    reject_at = injection.index("if (location.origin !== targetOrigin) return", origin_at)
    token_at = injection.index("const gateToken =", reject_at)
    storage_at = injection.index("Storage.prototype.getItem", token_at)
    assert origin_at < reject_at < token_at < storage_at

    network_enable = capture_source.index('await session.send("Network.enable")')
    cache_disabled = capture_source.index('await session.send("Network.setCacheDisabled"', network_enable)
    service_worker_bypassed = capture_source.index(
        'await session.send("Network.setBypassServiceWorker"', cache_disabled
    )
    cookie_injection = capture_source.index('await session.send("Network.setCookie", {')
    page_navigation = capture_source.index('await session.send("Page.navigate", { url: args.url })')
    assert network_enable < cache_disabled < service_worker_bypassed < cookie_injection < page_navigation

    bootstrap_auth = capture_source.index("const authProof = await requireAuthentication(session)")
    identity_probe = capture_source.index(
        "const releaseIdentity = await probeReleaseIdentity(session)", bootstrap_auth
    )
    page_loop_start = capture_source.index(
        "for (const [pageIndex, page] of pageManifest.pages.entries())",
        identity_probe,
    )
    assert bootstrap_auth < identity_probe < page_loop_start

    per_page_navigation = capture_source.split(
        "async function navigateAndProbePage(session, baseUrl, page, timeoutMs, settleMs)",
        1,
    )[1].split("async function waitForFinalSameOriginApiIdle", 1)[0]
    discard_at = per_page_navigation.index("session.beginFullDocumentNavigation()")
    auth_at = per_page_navigation.index("await requireAuthentication(session)")
    target_at = per_page_navigation.index("const target = pageUrl", auth_at)
    navigate_at = per_page_navigation.index('await session.send("Page.navigate"', target_at)
    assert auth_at < target_at < discard_at < navigate_at

    page_loop = capture_source.split(
        "for (const [pageIndex, page] of pageManifest.pages.entries())",
        1,
    )[1].split('const pageState = await session.send("Runtime.evaluate"', 1)[0]
    result_at = page_loop.index("const pageResult = await navigateAndProbePage")
    retain_at = page_loop.index("pages.push(pageResult)", result_at)
    fail_fast_at = page_loop.index("pageIndex === 0", retain_at)
    explicit_error_at = page_loop.index("browser_gate_first_page_failed", fail_fast_at)
    assert result_at < retain_at < fail_fast_at < explicit_error_at

    functional_journey = runtime_source.split(
        "export async function runKolPoolFunctionalJourney(session, timeoutMs)",
        1,
    )[1]
    apply_at = functional_journey.index("setter.call(input, value)")
    ui_search_at = functional_journey.index("globalSearchDomProof(session)", apply_at)
    source_truth_at = functional_journey.index("probeGlobalSearchSourceTruth(session)", ui_search_at)
    ask_at = functional_journey.index("askButton.click()", source_truth_at)
    answer_at = functional_journey.index("askFindDomProof(session)", ask_at)
    assert apply_at < ui_search_at < source_truth_at < ask_at < answer_at

    final_state_read = capture_source.index('const pageState = await session.send("Runtime.evaluate", {')
    final_idle = capture_source.index(
        "await waitForFinalSameOriginApiIdle(session, args.pageTimeoutMs)",
        final_state_read,
    )
    final_snapshot = capture_source.index("const finalNetworkSnapshot = {", final_idle)
    capture_assignment = capture_source.index("capture = {", final_snapshot)
    assert final_state_read < final_idle < final_snapshot < capture_assignment
    assert (
        "inflight_same_origin_api_final: finalNetworkSnapshot.inflight_same_origin_api_final"
        in capture_source
    )

    serialized_at = capture_source.index("const serializedCapture =")
    secret_scan_at = capture_source.index(
        "assertBrowserCaptureCredentialFree(serializedCapture, [token])",
        serialized_at,
    )
    output_directory_at = capture_source.index(
        "mkdirSync(path.dirname(path.resolve(args.output))",
        secret_scan_at,
    )
    output_write_at = capture_source.index("writeFileSync(args.output", output_directory_at)
    assert serialized_at < secret_scan_at < output_directory_at < output_write_at


def test_raw_capture_secret_scan_is_generic_and_never_echoes_matches() -> None:
    node = shutil.which("node")
    assert node is not None
    program = f"""
import {{
  assertBrowserCaptureCredentialFree,
  browserCaptureSecretCategories,
}} from {json.dumps(SECRET_SCAN_PATH.as_uri())};
const knownSecret = "known-browser-gate-secret-value";
const samples = {{
  authorization: "Authorization: Bearer bearer-token-value-1234567890",
  database: "postgresql://release_user:database-password@127.0.0.1/vkpi",
  provider: "sk-ant-api03-provider-secret-value-1234567890",
  google_provider: "AIzaProviderSecretValue123456789012345",
  assignment: "client_secret = client-secret-value-1234567890",
  provider_assignment: "APIFY_TOKEN=provider-token-value-1234567890",
  query: "https://example.invalid/path?access_token=query-secret-value-1234567890",
  jwt: "eyJabcdefghijk.eyJabcdefghijk.signature123456",
  private_key: "-----BEGIN " + "PRIVATE KEY-----",
  known: knownSecret,
}};
const categories = Object.fromEntries(
  Object.entries(samples).map(([name, value]) => [
    name,
    browserCaptureSecretCategories(
      JSON.stringify({{ channel: "console", text: value }}),
      name === "known" ? [knownSecret] : [],
    ),
  ]),
);
const safe = browserCaptureSecretCategories(JSON.stringify({{
  token_present: true,
  credential_persistence: false,
  credential_isolation: {{ cross_origin_frame_token_absent: true }},
  page_manifest: {{ pages: [{{ nav_key: "dashboard", family: "overview" }}] }},
  text: "token missing",
}}));
let blockedMessage = "";
try {{
  assertBrowserCaptureCredentialFree(samples.assignment);
}} catch (error) {{
  blockedMessage = error.message;
}}
process.stdout.write(JSON.stringify({{ categories, safe, blockedMessage }}));
"""
    result = subprocess.run(
        [node, "--input-type=module", "--eval", program],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    observed = json.loads(result.stdout)

    assert "authorization" in observed["categories"]["authorization"]
    assert "database_dsn" in observed["categories"]["database"]
    assert "provider_key" in observed["categories"]["provider"]
    assert "provider_key" in observed["categories"]["google_provider"]
    assert "credential_assignment" in observed["categories"]["assignment"]
    assert "provider_credential_assignment" in observed["categories"]["provider_assignment"]
    assert "credential_query" in observed["categories"]["query"]
    assert "jwt" in observed["categories"]["jwt"]
    assert "private_key" in observed["categories"]["private_key"]
    assert observed["categories"]["known"] == ["known_credential"]
    assert observed["safe"] == []
    assert observed["blockedMessage"] == "browser_capture_secret_scan_failed"
    assert "client-secret-value" not in result.stdout
    assert result.stderr == ""


def test_chromium_child_environment_is_minimal_and_drops_parent_secrets() -> None:
    node = shutil.which("node")
    assert node is not None
    environment = os.environ.copy()
    environment.update(
        {
            "HOME": "/tmp/browser-home",
            "PATH": "/usr/bin:/bin",
            "LANG": "en_US.UTF-8",
            "LC_TEST": "allowed-locale",
            "VKPI_BROWSER_GATE_TOKEN": "gate-token-must-not-cross-exec",
            "VKPI_PARENT_SENTINEL_SECRET": "sentinel-must-not-cross-exec",
            "DATABASE_URL": "postgresql://secret",
        }
    )
    program = (
        "import { chromeChildEnvironment } from "
        f"{json.dumps(PIPE_PATH.as_uri())};"
        "process.stdout.write(JSON.stringify(chromeChildEnvironment(process.env)));"
    )
    result = subprocess.run(
        [node, "--input-type=module", "--eval", program],
        cwd=ROOT,
        env=environment,
        check=True,
        capture_output=True,
        text=True,
    )
    child_environment = json.loads(result.stdout)

    assert child_environment["HOME"] == "/tmp/browser-home"
    assert child_environment["PATH"] == "/usr/bin:/bin"
    assert child_environment["LANG"] == "en_US.UTF-8"
    assert child_environment["LC_TEST"] == "allowed-locale"
    assert "VKPI_BROWSER_GATE_TOKEN" not in child_environment
    assert "VKPI_PARENT_SENTINEL_SECRET" not in child_environment
    assert "DATABASE_URL" not in child_environment
    allowed_names = {
        "HOME",
        "USER",
        "LOGNAME",
        "TMPDIR",
        "TMP",
        "TEMP",
        "LANG",
        "LANGUAGE",
        "PATH",
        "__CF_USER_TEXT_ENCODING",
    }
    assert all(name in allowed_names or name.startswith("LC_") for name in child_environment)


def test_cdp_pipe_uses_nul_frames_and_flattened_session_ids() -> None:
    node = shutil.which("node")
    assert node is not None
    program = f"""
import {{ EventEmitter }} from "node:events";
import {{ PassThrough }} from "node:stream";
import {{ CdpPipeConnection }} from {json.dumps(PIPE_PATH.as_uri())};
const child = new EventEmitter();
child.stdio = [null, null, null, new PassThrough(), new PassThrough()];
const deadline = {{
  assertAvailable() {{}},
  boundedTimeoutMs(value) {{ return value; }},
}};
let observedFrame;
child.stdio[3].once("data", (frame) => {{
  observedFrame = frame;
  const command = JSON.parse(frame.subarray(0, -1).toString("utf8"));
  child.stdio[4].write(Buffer.from(JSON.stringify({{
    id: command.id,
    sessionId: command.sessionId,
    result: {{ accepted: true }},
  }}) + "\\0", "utf8"));
}});
const connection = new CdpPipeConnection(child, deadline);
const result = await connection.send("Runtime.enable", {{}}, "flattened-session");
const command = JSON.parse(observedFrame.subarray(0, -1).toString("utf8"));
connection.close();
process.stdout.write(JSON.stringify({{
  nul_terminated: observedFrame.at(-1) === 0,
  method: command.method,
  session_id: command.sessionId,
  accepted: result.accepted,
}}));
"""
    result = subprocess.run(
        [node, "--input-type=module", "--eval", program],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    )

    assert json.loads(result.stdout) == {
        "nul_terminated": True,
        "method": "Runtime.enable",
        "session_id": "flattened-session",
        "accepted": True,
    }
