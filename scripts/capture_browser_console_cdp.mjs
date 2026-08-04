#!/usr/bin/env node
/**
 * Capture the authenticated V-KPI console in an owned, ephemeral Chromium
 * profile with extensions disabled. This script only produces the raw capture;
 * scripts/verify_browser_console_capture.py owns the fail-closed release verdict.
 */
import { spawn } from "node:child_process";
import { mkdirSync, mkdtempSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import os from "node:os";
import path from "node:path";
import { fileURLToPath } from "node:url";
import {
  CdpPipeConnection,
  attachFirstPageTarget,
  chromeChildEnvironment,
} from "./browser_console_cdp_pipe.mjs";
import {
  DEFAULT_OVERALL_TIMEOUT_MS,
  MAX_OVERALL_TIMEOUT_MS,
  MIN_OVERALL_TIMEOUT_MS,
  OverallDeadline,
  emptyFunctionalProof,
  functionalProofPassed,
  runKolPoolFunctionalJourney,
} from "./browser_console_capture_runtime.mjs";
import { proveOpaqueOriginTokenIsolation } from "./browser_console_token_isolation.mjs";
import { assertBrowserCaptureCredentialFree } from "./browser_capture_secret_scan.mjs";

const CAPTURE_SCHEMA_VERSION = "vkpi-browser-console-capture/v1";
const PAGE_MANIFEST_SCHEMA_VERSION = "vkpi-browser-page-manifest/v1";
const EVENT_CHANNELS = [
  "Runtime.consoleAPICalled",
  "Runtime.exceptionThrown",
  "Log.entryAdded",
];
const NETWORK_EVENT_CHANNELS = [
  "Network.requestWillBeSent",
  "Network.responseReceived",
  "Network.loadingFinished",
  "Network.loadingFailed",
];
const AUTH_PROBE_TIMEOUT_MS = 5000;
const RELEASE_IDENTITY_PROBE_TIMEOUT_MS = 10000;
const RELEASE_IDENTITY_SCHEMA_VERSION = "vkpi-browser-release-identity/v1";
const RELEASE_IDENTITY_QUERY_KEY = "_vkpi_release_probe";
const API_IDLE_GRACE_MS = 1000;
const DEFAULT_PAGE_MANIFEST = path.join(
  path.dirname(fileURLToPath(import.meta.url)),
  "browser_gate_pages.json",
);

function parseArgs(argv) {
  const result = {
    settleMs: 5000,
    pageSettleMs: 1000,
    pageTimeoutMs: 30000,
    overallTimeoutMs: DEFAULT_OVERALL_TIMEOUT_MS,
    manifest: DEFAULT_PAGE_MANIFEST,
    allowedExternalMedia403Origins: [],
  };
  for (let index = 0; index < argv.length; index += 1) {
    const item = argv[index];
    if (item === "--url") result.url = argv[++index];
    else if (item === "--output") result.output = argv[++index];
    else if (item === "--settle-ms") result.settleMs = Number(argv[++index]);
    else if (item === "--page-settle-ms") result.pageSettleMs = Number(argv[++index]);
    else if (item === "--page-timeout-ms") result.pageTimeoutMs = Number(argv[++index]);
    else if (item === "--overall-timeout-ms") result.overallTimeoutMs = Number(argv[++index]);
    else if (item === "--manifest") result.manifest = argv[++index];
    else if (item === "--allow-external-media-403-origin") {
      result.allowedExternalMedia403Origins.push(argv[++index]);
    }
    else if (item === "--chrome") result.chrome = argv[++index];
    else throw new Error(`unknown argument: ${item}`);
  }
  if (!result.url || !result.output) {
    throw new Error("usage: capture_browser_console_cdp.mjs --url <http(s)://...> --output <capture.json> [--manifest <pages.json>] [--overall-timeout-ms <ms>]");
  }
  const target = new URL(result.url);
  if (!["http:", "https:"].includes(target.protocol) || target.username || target.password) {
    throw new Error("--url must be credential-free absolute HTTP(S)");
  }
  if (!Number.isFinite(result.settleMs) || result.settleMs < 1000 || result.settleMs > 60000) {
    throw new Error("--settle-ms must be within [1000, 60000]");
  }
  if (!Number.isFinite(result.pageSettleMs) || result.pageSettleMs < 250 || result.pageSettleMs > 10000) {
    throw new Error("--page-settle-ms must be within [250, 10000]");
  }
  if (!Number.isFinite(result.pageTimeoutMs) || result.pageTimeoutMs < 5000 || result.pageTimeoutMs > 60000) {
    throw new Error("--page-timeout-ms must be within [5000, 60000]");
  }
  if (
    !Number.isInteger(result.overallTimeoutMs)
    || result.overallTimeoutMs < MIN_OVERALL_TIMEOUT_MS
    || result.overallTimeoutMs > MAX_OVERALL_TIMEOUT_MS
  ) {
    throw new Error(`--overall-timeout-ms must be an integer within [${MIN_OVERALL_TIMEOUT_MS}, ${MAX_OVERALL_TIMEOUT_MS}]`);
  }
  const envOrigins = String(process.env.VKPI_BROWSER_GATE_EXTERNAL_MEDIA_403_ORIGINS || "")
    .split(",")
    .map((value) => value.trim())
    .filter(Boolean);
  result.allowedExternalMedia403Origins.push(...envOrigins);
  return result;
}

function exactHttpsOrigin(value, applicationOrigin) {
  let parsed;
  try {
    parsed = new URL(String(value || ""));
  } catch {
    throw new Error(`external media 403 allowlist entry is not an absolute URL: ${value}`);
  }
  if (
    parsed.protocol !== "https:"
    || parsed.username
    || parsed.password
    || parsed.hostname.includes("*")
    || parsed.pathname !== "/"
    || parsed.search
    || parsed.hash
    || parsed.origin === applicationOrigin
    || String(value) !== parsed.origin
  ) {
    throw new Error(`external media 403 allowlist entry must be one exact external HTTPS origin: ${value}`);
  }
  return parsed.origin;
}

function loadPageManifest(filename) {
  let payload;
  try {
    payload = JSON.parse(readFileSync(path.resolve(filename), "utf8"));
  } catch {
    throw new Error("browser page manifest is not readable JSON");
  }
  if (payload?.schema_version !== PAGE_MANIFEST_SCHEMA_VERSION || !Array.isArray(payload?.pages)) {
    throw new Error(`browser page manifest must use ${PAGE_MANIFEST_SCHEMA_VERSION}`);
  }
  const pages = payload.pages.map((raw, index) => {
    const family = String(raw?.family || "").trim();
    const navKey = String(raw?.nav_key || "").trim();
    const heading = String(raw?.heading || "").trim();
    if (!family || !navKey || !heading || !/^[A-Za-z0-9-]+$/.test(family) || !/^[A-Za-z0-9-]+$/.test(navKey)) {
      throw new Error(`browser page manifest entry ${index} is invalid`);
    }
    return { family, nav_key: navKey, heading };
  });
  if (!pages.length || new Set(pages.map((item) => item.family)).size !== pages.length) {
    throw new Error("browser page manifest families must be non-empty and unique");
  }
  if (new Set(pages.map((item) => item.nav_key)).size !== pages.length) {
    throw new Error("browser page manifest nav keys must be unique");
  }
  return { schema_version: PAGE_MANIFEST_SCHEMA_VERSION, pages };
}

function captureUrl(value) {
  try {
    const parsed = new URL(String(value || ""));
    return `${parsed.origin}${parsed.pathname}`;
  } catch {
    return "";
  }
}

function callFrames(stackTrace) {
  const frames = [];
  let current = stackTrace;
  while (current && frames.length < 50) {
    for (const frame of current.callFrames || []) {
      frames.push({
        function_name: frame.functionName || "",
        url: frame.url || "",
        line_number: Number(frame.lineNumber || 0),
        column_number: Number(frame.columnNumber || 0),
      });
      if (frames.length >= 50) break;
    }
    current = current.parent;
  }
  return frames;
}

class CdpSession {
  constructor(connection, sessionId, applicationOrigin, overallDeadline) {
    this.connection = connection;
    this.sessionId = sessionId;
    this.applicationOrigin = applicationOrigin;
    this.overallDeadline = overallDeadline;
    this.events = [];
    this.networkResponses = [];
    this.networkFailures = [];
    this.networkRequests = new Map();
    this.inflightSameOriginApi = new Map();
    this.networkLastActivityAt = new Map();
    this.networkRequestTotal = 0;
    this.networkResponseTotal = 0;
    this.networkResponseErrorTotal = 0;
    this.navigationDiscardedSameOriginApiTotal = 0;
    this.currentPageFamily = "bootstrap";
    this.contexts = new Map();
    this.loadCompleted = false;
    this.removeEventListener = connection.onEvent((payload) => {
      if (payload.sessionId === this.sessionId) this.onMessage(payload);
    });
  }

  onMessage(payload) {
    if (payload.method === "Page.loadEventFired") this.loadCompleted = true;
    if (payload.method === "Runtime.executionContextCreated") {
      const context = payload.params?.context || {};
      this.contexts.set(context.id, {
        origin: context.origin || "",
        name: context.name || "",
        type: context.auxData?.type || "",
        is_default: context.auxData?.isDefault === true,
      });
      return;
    }
    if (payload.method === "Runtime.consoleAPICalled") {
      const params = payload.params || {};
      const stack = callFrames(params.stackTrace);
      const context = this.contexts.get(params.executionContextId) || {};
      this.events.push({
        channel: payload.method,
        level: params.type || "unknown",
        text: (params.args || []).map((arg) => arg.value ?? arg.description ?? "").join(" "),
        source_url: stack[0]?.url || "",
        execution_context_origin: context.origin || "",
        page_family: this.currentPageFamily,
        stack_trace: stack,
      });
      return;
    }
    if (payload.method === "Runtime.exceptionThrown") {
      const details = payload.params?.exceptionDetails || {};
      const stack = callFrames(details.stackTrace);
      const context = this.contexts.get(details.executionContextId) || {};
      this.events.push({
        channel: payload.method,
        level: "exception",
        text: details.exception?.description || details.text || "Runtime exception",
        source_url: details.url || stack[0]?.url || "",
        execution_context_origin: context.origin || "",
        page_family: this.currentPageFamily,
        stack_trace: stack,
      });
      return;
    }
    if (payload.method === "Log.entryAdded") {
      const entry = payload.params?.entry || {};
      const stack = callFrames(entry.stackTrace);
      this.events.push({
        channel: payload.method,
        level: entry.level || "unknown",
        text: entry.text || "",
        source_url: entry.url || stack[0]?.url || "",
        execution_context_origin: "",
        page_family: this.currentPageFamily,
        stack_trace: stack,
      });
      return;
    }
    if (payload.method === "Network.requestWillBeSent") {
      const params = payload.params || {};
      const request = params.request || {};
      const requestId = String(params.requestId || "");
      const url = String(request.url || "");
      const resourceType = String(params.type || "Other");
      this.networkRequestTotal += 1;
      let sameOriginApi = false;
      let releaseIdentityProbe = false;
      try {
        const parsed = new URL(url);
        sameOriginApi = parsed.origin === this.applicationOrigin
          && (parsed.pathname === "/health" || parsed.pathname.startsWith("/api/"));
        releaseIdentityProbe = parsed.origin === this.applicationOrigin
          && parsed.searchParams.has(RELEASE_IDENTITY_QUERY_KEY);
      } catch {
        // Invalid request URL remains non-API and cannot satisfy idle proof.
      }
      this.networkRequests.set(requestId, {
        family: this.currentPageFamily,
        same_origin_api: sameOriginApi,
        release_identity_probe: releaseIdentityProbe,
        long_lived: resourceType === "EventSource" || resourceType === "WebSocket",
        method: String(request.method || "GET").toUpperCase().slice(0, 16),
        resource_type: resourceType.slice(0, 32),
        url: captureUrl(url),
      });
      if (sameOriginApi && resourceType !== "EventSource" && resourceType !== "WebSocket") {
        this.inflightSameOriginApi.set(requestId, this.currentPageFamily);
      }
      this.networkLastActivityAt.set(this.currentPageFamily, Date.now());
      return;
    }
    if (payload.method === "Network.responseReceived") {
      const params = payload.params || {};
      const response = params.response || {};
      const request = this.networkRequests.get(String(params.requestId || ""));
      const pageFamily = request?.family || this.currentPageFamily;
      const url = captureUrl(response.url);
      const status = Number(response.status);
      this.networkResponseTotal += 1;
      this.networkLastActivityAt.set(pageFamily, Date.now());
      if (Number.isFinite(status) && status >= 400) this.networkResponseErrorTotal += 1;
      let sameOriginApi = false;
      try {
        const parsed = new URL(String(response.url || ""));
        sameOriginApi = parsed.origin === this.applicationOrigin
          && (parsed.pathname === "/health" || parsed.pathname.startsWith("/api/"));
      } catch {
        // Malformed response URLs are retained only when they are errors below.
      }
      const releaseIdentityProbe = request?.release_identity_probe === true;
      // Retain every same-origin API response as collection proof, plus every
      // cache-busted release-identity response and every HTTP error from any
      // origin. Successful third-party media is noise.
      if (sameOriginApi || releaseIdentityProbe || (Number.isFinite(status) && status >= 400)) {
        this.networkResponses.push({
          channel: payload.method,
          page_family: pageFamily,
          url,
          status: Number.isFinite(status) ? status : null,
          resource_type: String(params.type || "Other"),
          mime_type: String(response.mimeType || "").slice(0, 160),
          release_identity_probe: releaseIdentityProbe,
          from_disk_cache: response.fromDiskCache === true,
          from_service_worker: response.fromServiceWorker === true,
        });
      }
      return;
    }
    if (payload.method === "Network.loadingFinished") {
      const requestId = String(payload.params?.requestId || "");
      const request = this.networkRequests.get(requestId);
      this.inflightSameOriginApi.delete(requestId);
      this.networkRequests.delete(requestId);
      this.networkLastActivityAt.set(request?.family || this.currentPageFamily, Date.now());
      return;
    }
    if (payload.method === "Network.loadingFailed") {
      const params = payload.params || {};
      const requestId = String(params.requestId || "");
      const request = this.networkRequests.get(requestId);
      this.inflightSameOriginApi.delete(requestId);
      this.networkRequests.delete(requestId);
      this.networkLastActivityAt.set(request?.family || this.currentPageFamily, Date.now());
      this.networkFailures.push({
        channel: payload.method,
        page_family: request?.family || this.currentPageFamily,
        resource_type: String(params.type || "Other"),
        canceled: params.canceled === true,
        blocked_reason: String(params.blockedReason || "").slice(0, 120),
        error_text: String(params.errorText || "network loading failed").slice(0, 240),
      });
    }
  }

  send(method, params = {}) {
    return this.connection.send(method, params, this.sessionId);
  }

  close() {
    this.removeEventListener();
  }

  inflightApiForFamily(family) {
    return [...this.inflightSameOriginApi.values()].filter((value) => value === family).length;
  }

  inflightApiDiagnostics(family = "") {
    return [...this.inflightSameOriginApi.keys()]
      .map((requestId) => {
        const request = this.networkRequests.get(requestId) || {};
        return {
          family: String(request.family || ""),
          method: String(request.method || ""),
          resource_type: String(request.resource_type || ""),
          url: String(request.url || ""),
        };
      })
      .filter((request) => !family || request.family === family)
      .sort((left, right) => JSON.stringify(left).localeCompare(JSON.stringify(right)))
      .slice(0, 20);
  }

  beginFullDocumentNavigation() {
    const staleRequestIds = [...this.inflightSameOriginApi.keys()];
    for (const requestId of staleRequestIds) {
      this.inflightSameOriginApi.delete(requestId);
      this.networkRequests.delete(requestId);
    }
    this.navigationDiscardedSameOriginApiTotal += staleRequestIds.length;
    return staleRequestIds.length;
  }
}

async function waitForExit(child, timeoutMs) {
  if (child.exitCode !== null) return true;
  return new Promise((resolve) => {
    const timer = setTimeout(() => resolve(false), timeoutMs);
    child.once("exit", () => {
      clearTimeout(timer);
      resolve(true);
    });
  });
}

function pageUrl(baseUrl, navKey) {
  const url = new URL(baseUrl);
  url.searchParams.set("cockpit", navKey);
  // The public Dashboard is canonical at `/`; the inner cockpit page is
  // selected by the reviewed query contract and does not need #cockpit.
  if (url.hash === "#cockpit") url.hash = "";
  return url.href;
}

async function pageDomProof(session, page) {
  const result = await session.send("Runtime.evaluate", {
    expression: `(() => {
      const navKey = ${JSON.stringify(page.nav_key)};
      const expectedHeading = ${JSON.stringify(page.heading)};
      const stage = document.querySelector('.vkpi-page-stage--' + navKey);
      const heading = document.querySelector('.cockpit-shell main header h1');
      const observedHeading = String(heading?.textContent || '').replace(/\\s+/g, ' ').trim();
      const lazyError = Boolean(stage?.querySelector('[role="alert"][aria-label$="加载失败"]'));
      return {
        stage_present: Boolean(stage),
        heading_present: Boolean(heading),
        heading_matches: observedHeading === expectedHeading,
        observed_heading: observedHeading,
        password_form_present: Boolean(document.querySelector('input[type="password"]')),
        lazy_error_present: lazyError,
        cockpit_main_present: Boolean(document.querySelector('.cockpit-shell main')),
        ready_state: document.readyState,
        final_url: location.href,
      };
    })()`,
    returnByValue: true,
  });
  return result.result?.value || {};
}

async function probeAuthentication(session) {
  const fetchTimeoutMs = session.overallDeadline.boundedTimeoutMs(AUTH_PROBE_TIMEOUT_MS);
  const authState = await session.send("Runtime.evaluate", {
    expression: `(async () => {
      const proof = {
        request_completed: false,
        same_origin: false,
        token_present: false,
        http_status: null,
        http_2xx: false,
        status_success: false,
        user_present: false,
      };
      try {
        const token = localStorage.getItem('viltrox_marketing_token_v1') || '';
        const endpoint = new URL('/api/auth/me', location.href);
        proof.same_origin = endpoint.origin === location.origin;
        proof.token_present = token.length > 0;
        const response = await fetch(endpoint.pathname, {
          method: 'GET',
          credentials: 'same-origin',
          cache: 'no-store',
          headers: { Authorization: 'Bearer ' + token },
          signal: AbortSignal.timeout(${fetchTimeoutMs}),
        });
        proof.request_completed = true;
        proof.http_status = response.status;
        proof.http_2xx = response.status >= 200 && response.status < 300;
        const body = await response.json().catch(() => null);
        proof.status_success = body?.status === 'success';
        proof.user_present = Boolean(body?.user && typeof body.user === 'object');
      } catch {}
      return proof;
    })()`,
    awaitPromise: true,
    returnByValue: true,
  });
  return authState.result?.value || {};
}

function authenticationPassed(proof) {
  return (
    proof.request_completed === true
    && proof.same_origin === true
    && proof.token_present === true
    && proof.http_2xx === true
    && proof.status_success === true
    && proof.user_present === true
  );
}

async function requireAuthentication(session) {
  const proof = await probeAuthentication(session);
  if (!authenticationPassed(proof)) {
    throw new Error("browser_gate_token_expired");
  }
  return proof;
}

async function probeReleaseIdentity(session) {
  const fetchTimeoutMs = session.overallDeadline.boundedTimeoutMs(
    RELEASE_IDENTITY_PROBE_TIMEOUT_MS,
  );
  const state = await session.send("Runtime.evaluate", {
    expression: `(async () => {
      const sha40 = (value) => {
        const text = String(value || '').trim().toLowerCase();
        return /^[0-9a-f]{40}$/.test(text) ? text : '';
      };
      const appAsset = (value) => {
        const text = String(value || '').trim();
        return /^app-[A-Za-z0-9_-]+\\.js$/.test(text) ? text : '';
      };
      const basename = (pathname) => String(pathname || '').split('/').filter(Boolean).at(-1) || '';
      const nonce = () => {
        const bytes = new Uint8Array(16);
        crypto.getRandomValues(bytes);
        return [...bytes].map((value) => value.toString(16).padStart(2, '0')).join('');
      };
      const healthNonce = nonce();
      const indexNonce = nonce();
      const assetNonce = nonce();
      const requestHeaders = {
        Authorization: 'Bearer ' + (localStorage.getItem('viltrox_marketing_token_v1') || ''),
        'Cache-Control': 'no-cache, no-store, max-age=0',
        Pragma: 'no-cache',
        'X-Requested-With': 'XMLHttpRequest',
      };
      const fetchOptions = {
        method: 'GET',
        credentials: 'same-origin',
        cache: 'no-store',
        headers: requestHeaders,
      };
      const proof = {
        schema_version: ${JSON.stringify(RELEASE_IDENTITY_SCHEMA_VERSION)},
        cache_bypass: {
          cdp_cache_disabled: true,
          service_worker_bypassed: true,
          fetch_cache_mode: 'no-store',
          request_cache_control: 'no-cache, no-store, max-age=0',
          request_pragma: 'no-cache',
          unique_query_parameter: ${JSON.stringify(RELEASE_IDENTITY_QUERY_KEY)},
          unique_request_nonces: new Set([healthNonce, indexNonce, assetNonce]).size === 3,
        },
        health: {
          request_completed: false,
          same_origin: false,
          http_status: null,
          http_2xx: false,
          status_ok: false,
          build_git_sha: '',
          build_client_sha: '',
          build_client_source: '',
          server_git_sha: '',
          client_git_sha: '',
          sha_aligned: false,
          response_cache_control: '',
        },
        frontend: {
          loaded_app_asset_count: 0,
          loaded_app_asset: '',
          index_request_completed: false,
          index_same_origin: false,
          index_http_status: null,
          index_http_2xx: false,
          index_content_type_html: false,
          index_app_asset_count: 0,
          index_app_asset: '',
          loaded_matches_index: false,
          index_response_cache_control: '',
          asset_request_completed: false,
          asset_same_origin: false,
          asset_http_status: null,
          asset_http_2xx: false,
          asset_content_type_javascript: false,
          asset_bytes: 0,
          asset_sha256: '',
          digest_algorithm: 'sha256',
          asset_response_cache_control: '',
        },
      };
      try {
        const loaded = [...document.scripts]
          .map((script) => {
            try { return new URL(script.src, location.href); } catch { return null; }
          })
          .filter((url) => url && url.origin === location.origin)
          .map((url) => appAsset(basename(url.pathname)))
          .filter(Boolean);
        proof.frontend.loaded_app_asset_count = loaded.length;
        proof.frontend.loaded_app_asset = loaded.length === 1 ? loaded[0] : '';

        const healthEndpoint = new URL('/health', location.origin);
        healthEndpoint.searchParams.set(${JSON.stringify(RELEASE_IDENTITY_QUERY_KEY)}, healthNonce);
        proof.health.same_origin = healthEndpoint.origin === location.origin;
        const healthResponse = await fetch(healthEndpoint.href, {
          ...fetchOptions,
          signal: AbortSignal.timeout(${fetchTimeoutMs}),
        });
        proof.health.request_completed = true;
        proof.health.http_status = healthResponse.status;
        proof.health.http_2xx = healthResponse.status >= 200 && healthResponse.status < 300;
        proof.health.response_cache_control = String(healthResponse.headers.get('cache-control') || '').slice(0, 160);
        const healthBody = await healthResponse.json().catch(() => null);
        const build = healthBody && typeof healthBody.build === 'object' ? healthBody.build : {};
        const trust = healthBody && typeof healthBody.trust === 'object' ? healthBody.trust : {};
        proof.health.status_ok = healthBody?.status === 'ok';
        proof.health.build_git_sha = sha40(build.git_sha);
        proof.health.build_client_sha = sha40(build.client_build);
        proof.health.build_client_source = String(build.client_build_source || '').slice(0, 40);
        proof.health.server_git_sha = sha40(trust.server_git_sha);
        proof.health.client_git_sha = sha40(trust.client_git_sha);
        proof.health.sha_aligned = trust.sha_aligned === true;

        const indexEndpoint = new URL('/', location.origin);
        indexEndpoint.searchParams.set(${JSON.stringify(RELEASE_IDENTITY_QUERY_KEY)}, indexNonce);
        proof.frontend.index_same_origin = indexEndpoint.origin === location.origin;
        const indexResponse = await fetch(indexEndpoint.href, {
          ...fetchOptions,
          signal: AbortSignal.timeout(${fetchTimeoutMs}),
        });
        proof.frontend.index_request_completed = true;
        proof.frontend.index_http_status = indexResponse.status;
        proof.frontend.index_http_2xx = indexResponse.status >= 200 && indexResponse.status < 300;
        proof.frontend.index_content_type_html = String(indexResponse.headers.get('content-type') || '')
          .toLowerCase().includes('text/html');
        proof.frontend.index_response_cache_control = String(indexResponse.headers.get('cache-control') || '').slice(0, 160);
        const indexHtml = await indexResponse.text();
        const indexDocument = new DOMParser().parseFromString(indexHtml, 'text/html');
        const indexAssets = [...indexDocument.querySelectorAll('script[src]')]
          .map((script) => {
            try { return new URL(script.getAttribute('src') || '', indexEndpoint); } catch { return null; }
          })
          .filter((url) => url && url.origin === location.origin)
          .map((url) => ({ url, name: appAsset(basename(url.pathname)) }))
          .filter((item) => item.name);
        proof.frontend.index_app_asset_count = indexAssets.length;
        proof.frontend.index_app_asset = indexAssets.length === 1 ? indexAssets[0].name : '';
        proof.frontend.loaded_matches_index = loaded.length === 1
          && indexAssets.length === 1
          && loaded[0] === indexAssets[0].name;

        if (indexAssets.length === 1) {
          const assetEndpoint = new URL(indexAssets[0].url.href);
          assetEndpoint.searchParams.set(${JSON.stringify(RELEASE_IDENTITY_QUERY_KEY)}, assetNonce);
          proof.frontend.asset_same_origin = assetEndpoint.origin === location.origin;
          const assetResponse = await fetch(assetEndpoint.href, {
            ...fetchOptions,
            signal: AbortSignal.timeout(${fetchTimeoutMs}),
          });
          proof.frontend.asset_request_completed = true;
          proof.frontend.asset_http_status = assetResponse.status;
          proof.frontend.asset_http_2xx = assetResponse.status >= 200 && assetResponse.status < 300;
          proof.frontend.asset_content_type_javascript = String(assetResponse.headers.get('content-type') || '')
            .toLowerCase().includes('javascript');
          proof.frontend.asset_response_cache_control = String(assetResponse.headers.get('cache-control') || '').slice(0, 160);
          const assetBody = await assetResponse.arrayBuffer();
          proof.frontend.asset_bytes = assetBody.byteLength;
          const digest = await crypto.subtle.digest('SHA-256', assetBody);
          proof.frontend.asset_sha256 = [...new Uint8Array(digest)]
            .map((value) => value.toString(16).padStart(2, '0')).join('');
        }
      } catch {}
      return proof;
    })()`,
    awaitPromise: true,
    returnByValue: true,
  });
  return state.result?.value || {};
}

async function navigateAndProbePage(session, baseUrl, page, timeoutMs, settleMs) {
  // Re-prove the bearer immediately before every navigation.  HTTP 200 alone
  // is insufficient because /api/auth/me may encode auth failure in its JSON
  // body; require the complete body-aware success contract and stop before a
  // stale token can create a cascade of unrelated page/API failures. Keep the
  // current family unchanged during this probe so any timer fired by the old
  // document cannot be attributed to the page that has not navigated yet.
  await requireAuthentication(session);
  const target = pageUrl(baseUrl, page.nav_key);
  // Every reviewed page probe uses a top-level Page.navigate. Requests from the
  // document being replaced are no longer part of the next page's readiness
  // contract, and Chromium does not guarantee a loadingFailed event for every
  // fetch canceled during that replacement. Clear the in-flight tracker only
  // after authentication and immediately before Page.navigate; response/error
  // evidence already collected for the prior page remains intact.
  const navigationDiscardedPriorApi = session.beginFullDocumentNavigation();
  session.currentPageFamily = page.family;
  session.loadCompleted = false;
  const startedAt = Date.now();
  const responseStart = session.networkResponses.length;
  const failureStart = session.networkFailures.length;
  await session.send("Page.navigate", { url: target });
  const deadline = session.overallDeadline.localDeadline(timeoutMs);
  let proof = {};
  while (Date.now() < deadline) {
    proof = await pageDomProof(session, page);
    if (
      session.loadCompleted
      && proof.stage_present === true
      && proof.heading_matches === true
      && proof.cockpit_main_present === true
      && proof.password_form_present === false
      && proof.lazy_error_present === false
    ) {
      break;
    }
    await session.overallDeadline.wait(100);
  }
  session.overallDeadline.assertAvailable();
  await session.overallDeadline.wait(settleMs);
  let apiIdle = false;
  // A request may begin immediately before the primary page deadline. Give
  // Chromium one short, explicitly budgeted terminal-event window; completion
  // still requires Network.loadingFinished/loadingFailed and is never inferred
  // from headers, response bytes, or a second probe.
  const apiIdleDeadline = Math.min(
    session.overallDeadline.expiresAt,
    Math.max(deadline, Date.now() + API_IDLE_GRACE_MS),
  );
  while (Date.now() < apiIdleDeadline) {
    const inflight = session.inflightApiForFamily(page.family);
    const lastActivity = session.networkLastActivityAt.get(page.family) || startedAt;
    if (inflight === 0 && Date.now() - lastActivity >= 500) {
      apiIdle = true;
      break;
    }
    await session.overallDeadline.wait(100);
  }
  session.overallDeadline.assertAvailable();
  proof = await pageDomProof(session, page);
  const passed = (
    session.loadCompleted
    && proof.stage_present === true
    && proof.heading_matches === true
    && proof.cockpit_main_present === true
    && proof.password_form_present === false
    && proof.lazy_error_present === false
    && apiIdle
  );
  return {
    family: page.family,
    nav_key: page.nav_key,
    expected_heading: page.heading,
    observed_heading: String(proof.observed_heading || ""),
    navigation_completed: session.loadCompleted === true,
    page_settled: passed,
    stage_present: proof.stage_present === true,
    heading_present: proof.heading_present === true,
    heading_matches: proof.heading_matches === true,
    cockpit_main_present: proof.cockpit_main_present === true,
    password_form_present: proof.password_form_present === true,
    lazy_error_present: proof.lazy_error_present === true,
    same_origin_api_idle: apiIdle,
    same_origin_api_inflight: session.inflightApiForFamily(page.family),
    same_origin_api_inflight_diagnostics: apiIdle
      ? []
      : session.inflightApiDiagnostics(page.family),
    ready_state: String(proof.ready_state || ""),
    final_url: String(proof.final_url || ""),
    elapsed_ms: Date.now() - startedAt,
    observed_network_responses: session.networkResponses.length - responseStart,
    observed_network_failures: session.networkFailures.length - failureStart,
    navigation_discarded_prior_api: navigationDiscardedPriorApi,
  };
}

async function waitForFinalSameOriginApiIdle(session, timeoutMs) {
  const deadline = session.overallDeadline.localDeadline(timeoutMs);
  while (Date.now() < deadline) {
    const lastActivity = Math.max(0, ...session.networkLastActivityAt.values());
    if (
      session.inflightSameOriginApi.size === 0
      && Date.now() - lastActivity >= 500
    ) {
      return;
    }
    await session.overallDeadline.wait(100);
  }
  session.overallDeadline.assertAvailable();
  throw new Error(
    `final same-origin API requests did not become idle before timeout: ${JSON.stringify(session.inflightApiDiagnostics())}`,
  );
}

async function main() {
  const args = parseArgs(process.argv.slice(2));
  const overallDeadline = new OverallDeadline(args.overallTimeoutMs);
  const targetUrl = new URL(args.url);
  const pageManifest = loadPageManifest(args.manifest);
  const allowedExternalMedia403Origins = [...new Set(
    args.allowedExternalMedia403Origins.map((value) => exactHttpsOrigin(value, targetUrl.origin)),
  )].sort();
  const chromePath = args.chrome
    || process.env.VKPI_CHROME_PATH
    || "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome";
  const token = process.env.VKPI_BROWSER_GATE_TOKEN || "";
  if (!token) throw new Error("VKPI_BROWSER_GATE_TOKEN is required for authenticated private-surface capture");

  const profileDir = mkdtempSync(path.join(os.tmpdir(), "vkpi-console-gate-"));
  const launchArgs = [
    "--remote-debugging-pipe",
    `--user-data-dir=${profileDir}`,
    "--headless=new",
    "--incognito",
    "--disable-crash-reporter",
    "--disable-breakpad",
    "--disable-extensions",
    "--disable-component-extensions-with-background-pages",
    "--disable-default-apps",
    "--disable-sync",
    "--no-first-run",
    "--no-default-browser-check",
    "--window-size=1440,1000",
    "about:blank",
  ];
  // The short-lived bearer and every unrelated parent secret stay in this
  // controller. Chrome receives only the small non-secret OS environment it
  // needs plus two private anonymous CDP pipes on child fd3/fd4.
  const chromeEnv = chromeChildEnvironment(process.env);
  const browser = spawn(chromePath, launchArgs, {
    stdio: ["ignore", "ignore", "ignore", "pipe", "pipe"],
    env: chromeEnv,
  });
  let connection;
  let session;
  let capture;
  let cleanup = { browser_exited: false, profile_removed: false };
  try {
    connection = new CdpPipeConnection(browser, overallDeadline);
    const target = await attachFirstPageTarget(connection, overallDeadline);
    session = new CdpSession(connection, target.sessionId, targetUrl.origin, overallDeadline);
    await session.send("Page.enable");
    await session.send("Network.enable");
    await session.send("Network.setCacheDisabled", { cacheDisabled: true });
    await session.send("Network.setBypassServiceWorker", { bypass: true });
    await session.send("Runtime.enable");
    await session.send("Log.enable");
    // Native media elements cannot attach the SPA's Authorization header.
    // Mirror the real login cookie only inside Chromium's off-the-record
    // context.  With --incognito it is a memory-only session credential, not a
    // persistent cookie database entry.
    const authCookie = await session.send("Network.setCookie", {
      name: "via_token",
      value: token,
      url: new URL("/", targetUrl.origin).href,
      path: "/",
      httpOnly: true,
      secure: targetUrl.protocol === "https:",
      sameSite: "Lax",
    });
    if (authCookie.success !== true) {
      throw new Error("owned Chromium auth cookie injection failed");
    }
    // AuthProvider and the two direct API helpers read this key from
    // localStorage. Virtualize that one key in renderer memory instead of
    // calling Storage.setItem, so the bearer is never persisted in the profile.
    await session.send("Page.addScriptToEvaluateOnNewDocument", {
      source: `(() => {
        const targetOrigin = ${JSON.stringify(targetUrl.origin)};
        if (location.origin !== targetOrigin) return;
        const gateKey = 'viltrox_marketing_token_v1';
        const gateToken = ${JSON.stringify(token)};
        const originalGetItem = Storage.prototype.getItem;
        const originalSetItem = Storage.prototype.setItem;
        const originalRemoveItem = Storage.prototype.removeItem;
        Storage.prototype.getItem = function(key) {
          if (this === window.localStorage && key === gateKey) return gateToken;
          return originalGetItem.call(this, key);
        };
        Storage.prototype.setItem = function(key, value) {
          if (this === window.localStorage && key === gateKey) return;
          return originalSetItem.call(this, key, value);
        };
        Storage.prototype.removeItem = function(key) {
          if (this === window.localStorage && key === gateKey) return;
          return originalRemoveItem.call(this, key);
        };
      })();`,
    });
    await session.send("Page.navigate", { url: args.url });
    const loadDeadline = overallDeadline.localDeadline(30000);
    while (!session.loadCompleted && Date.now() < loadDeadline) await overallDeadline.wait(100);
    overallDeadline.assertAvailable();
    await overallDeadline.wait(args.settleMs);
    const bootstrapNavigationCompleted = session.loadCompleted === true;

    // Prove authentication from inside the final same-origin page. Merely
    // writing a token to localStorage is not evidence that the token is valid:
    // /api/auth/me must return 2xx + status=success + a user object. Keep only
    // booleans/status in the capture; never persist the token or user payload.
    const authProof = await requireAuthentication(session);

    // A successful auth API probe is still insufficient if the browser ended
    // on a login/reset screen. Require the actual cockpit shell and reject any
    // password input. Again, persist only the two booleans.
    const surfaceState = await session.send("Runtime.evaluate", {
      expression: `({
        cockpit_main_present: Boolean(document.querySelector('.cockpit-shell main')),
        password_form_present: Boolean(document.querySelector('input[type="password"]')),
      })`,
      returnByValue: true,
    });
    const surfaceProof = surfaceState.result?.value || {};
    // Bind the browser evidence to public bytes and runtime identity, not just
    // to a successful UI journey. The probe compares the app script loaded by
    // this document with a separately cache-busted index and hashes the exact
    // cache-busted app bytes. The verifier supplies the frozen candidate's
    // expected SHA, asset name and digest independently.
    const releaseIdentity = await probeReleaseIdentity(session);
    // Keep CSP enforcement untouched. A sandboxed srcdoc frame with scripts
    // enabled but no same-origin privilege becomes an opaque `null` origin,
    // proving the renderer-only bearer cannot cross that origin boundary.
    const credentialIsolationProof = await proveOpaqueOriginTokenIsolation(session);

    // Exercise every reviewed cockpit family in the same clean browser. Each
    // family receives an independent full navigation using the public
    // ?cockpit=<nav-key> contract, so production-hidden operational pages are
    // still release-tested without changing the deployed navigation menu.
    const pages = [];
    let functionalProof = emptyFunctionalProof();
    for (const [pageIndex, page] of pageManifest.pages.entries()) {
      const pageResult = await navigateAndProbePage(
        session,
        args.url,
        page,
        args.pageTimeoutMs,
        args.pageSettleMs,
      );
      pages.push(pageResult);
      if (page.family === "kol-pool") {
        functionalProof = await runKolPoolFunctionalJourney(session, args.pageTimeoutMs);
      }
      if (pageIndex === 0 && pageResult.page_settled !== true) {
        throw new Error(
          `browser_gate_first_page_failed:${page.family}:`
          + `${JSON.stringify({ page: pageResult, inflight: session.inflightApiDiagnostics(page.family) })}`,
        );
      }
    }
    const pageState = await session.send("Runtime.evaluate", {
      expression: "({url: location.href, readyState: document.readyState, title: document.title})",
      returnByValue: true,
    });
    const finalState = pageState.result?.value || {};
    // The final DOM read above yields to the page and can race with polling
    // requests that start after the last per-page idle proof. Require one
    // fleet-wide quiet window and then synchronously freeze the evidence. A
    // timeout aborts the capture instead of rewriting a non-zero final count.
    await waitForFinalSameOriginApiIdle(session, args.pageTimeoutMs);
    overallDeadline.assertAvailable();
    const finalNetworkSnapshot = {
      events: [...session.events],
      responses: [...session.networkResponses],
      failures: [...session.networkFailures],
      response_count_total: session.networkResponseTotal,
      request_count_total: session.networkRequestTotal,
      response_error_count_total: session.networkResponseErrorTotal,
      retained_response_count: session.networkResponses.length,
      loading_failure_count: session.networkFailures.length,
      inflight_same_origin_api_final: session.inflightSameOriginApi.size,
      navigation_discarded_same_origin_api_total: session.navigationDiscardedSameOriginApiTotal,
    };
    const origins = [...new Set([...session.contexts.values()].map((item) => item.origin).filter(Boolean))].sort();
    const authenticatedSurface = (
      authProof.request_completed === true
      && authProof.same_origin === true
      && authProof.token_present === true
      && authProof.http_2xx === true
      && authProof.status_success === true
      && authProof.user_present === true
      && surfaceProof.cockpit_main_present === true
      && surfaceProof.password_form_present === false
    );
    capture = {
      schema_version: CAPTURE_SCHEMA_VERSION,
      captured_at: new Date().toISOString(),
      target_url: args.url,
      page_manifest: pageManifest,
      pages,
      functional_proof: functionalProof,
      release_identity: releaseIdentity,
      policy: {
        external_media_403_allowed_origins: allowedExternalMedia403Origins,
      },
      run: {
        kind: "live",
        navigation_completed: bootstrapNavigationCompleted && pages.every((page) => page.navigation_completed === true),
        page_settled: pages.every((page) => page.page_settled === true),
        authenticated_surface: authenticatedSurface,
        auth_probe: {
          request_completed: authProof.request_completed === true,
          same_origin: authProof.same_origin === true,
          token_present: authProof.token_present === true,
          http_status: Number.isInteger(authProof.http_status) ? authProof.http_status : null,
          http_2xx: authProof.http_2xx === true,
          status_success: authProof.status_success === true,
          user_present: authProof.user_present === true,
        },
        surface_probe: {
          cockpit_main_present: surfaceProof.cockpit_main_present === true,
          password_form_present: surfaceProof.password_form_present === true,
        },
        final_url: finalState.url || "",
        ready_state: finalState.readyState || "",
        title: finalState.title || "",
        settle_ms: args.settleMs,
        page_settle_ms: args.pageSettleMs,
        page_timeout_ms: args.pageTimeoutMs,
        overall_timeout_ms: args.overallTimeoutMs,
        overall_elapsed_ms: overallDeadline.elapsedMs(),
        overall_deadline_exhausted: false,
      },
      browser: {
        engine: "chromium",
        debug_transport: "remote-debugging-pipe",
        process_owned: true,
        profile_mode: "ephemeral",
        off_the_record: true,
        credential_persistence: false,
        credential_isolation: credentialIsolationProof,
        extensions_disabled: true,
        launch_args: launchArgs.map((item) => item.startsWith("--user-data-dir=") ? "--user-data-dir=<ephemeral>" : item),
      },
      collection: {
        enabled_domains: ["Page", "Network", "Runtime", "Log"],
        event_channels: EVENT_CHANNELS,
        network_event_channels: NETWORK_EVENT_CHANNELS,
        execution_context_origins: origins,
        events: finalNetworkSnapshot.events,
        network_responses: finalNetworkSnapshot.responses,
        network_failures: finalNetworkSnapshot.failures,
        network_summary: {
          response_count_total: finalNetworkSnapshot.response_count_total,
          request_count_total: finalNetworkSnapshot.request_count_total,
          response_error_count_total: finalNetworkSnapshot.response_error_count_total,
          retained_response_count: finalNetworkSnapshot.retained_response_count,
          loading_failure_count: finalNetworkSnapshot.loading_failure_count,
          inflight_same_origin_api_final: finalNetworkSnapshot.inflight_same_origin_api_final,
          navigation_discarded_same_origin_api_total:
            finalNetworkSnapshot.navigation_discarded_same_origin_api_total,
        },
      },
    };
  } finally {
    if (session) session.close();
    if (connection) connection.close();
    browser.kill("SIGTERM");
    cleanup.browser_exited = await waitForExit(browser, 3000);
    if (!cleanup.browser_exited) {
      browser.kill("SIGKILL");
      cleanup.browser_exited = await waitForExit(browser, 1000);
    }
    rmSync(profileDir, { recursive: true, force: true });
    cleanup.profile_removed = true;
  }
  capture.cleanup = cleanup;
  const serializedCapture = `${JSON.stringify(capture, null, 2)}\n`;
  assertBrowserCaptureCredentialFree(serializedCapture, [token]);
  mkdirSync(path.dirname(path.resolve(args.output)), { recursive: true });
  writeFileSync(args.output, serializedCapture, { encoding: "utf8", mode: 0o600 });
  console.log(JSON.stringify({
    output: path.resolve(args.output),
    captured_at: capture.captured_at,
    events: capture.collection.events.length,
    pages: capture.pages.length,
    network_errors: capture.collection.network_summary.response_error_count_total,
    functional_proof_passed: functionalProofPassed(capture.functional_proof),
    extension_contexts: capture.collection.execution_context_origins.filter((origin) => /^(chrome|moz)-extension:\/\//.test(origin)).length,
    cleanup,
  }));
}

main().catch((error) => {
  console.error(`browser console capture failed: ${error.message}`);
  process.exitCode = 2;
});
