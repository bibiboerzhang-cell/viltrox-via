#!/usr/bin/env python3
"""Fail-closed verifier for V-KPI browser console release captures.

The verifier is hermetic: it reads one JSON capture, classifies events from
explicit URL/context provenance, and never opens a browser, socket, database,
Redis connection, or provider client.  Text such as ``background.js`` or
``FrameDoesNotExistError`` is deliberately *not* an exemption signal: an event
is extension-owned only when its source/context uses an extension URL scheme.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence
from urllib.parse import parse_qs, urlsplit, urlunsplit


CAPTURE_SCHEMA_VERSION = "vkpi-browser-console-capture/v1"
GATE_SCHEMA_VERSION = "vkpi-browser-console-gate/v1"
MAX_CAPTURE_BYTES = 10 * 1024 * 1024
MAX_EVENTS = 10_000
MAX_EVENT_TEXT = 32_000
MAX_NETWORK_RECORDS = 20_000
EXTENSION_SCHEMES = {"chrome-extension", "moz-extension", "safari-web-extension"}
BROWSER_INTERNAL_SCHEMES = {"chrome", "devtools", "edge", "about"}
BLOCKING_LEVELS = {
    "warning",
    "error",
    "exception",
    "assert",
    "pageerror",
    "unhandledrejection",
}
REQUIRED_DOMAINS = {"Log", "Network", "Page", "Runtime"}
REQUIRED_CHANNELS = {
    "Log.entryAdded",
    "Runtime.consoleAPICalled",
    "Runtime.exceptionThrown",
}
REQUIRED_NETWORK_CHANNELS = {
    "Network.loadingFinished",
    "Network.loadingFailed",
    "Network.requestWillBeSent",
    "Network.responseReceived",
}
REQUIRED_PAGE_FAMILIES: dict[str, tuple[str, str]] = {
    "dashboard": ("dashboard", "Dashboard"),
    "kol-pool": ("kol-pool", "KOL Pool"),
    "my-kol": ("my-kol", "MY KOL"),
    "projects": ("projects", "Projects"),
    "events": ("events", "Events"),
    "shopify": ("shopify", "Shopify"),
    "dealers": ("dealers", "Dealers"),
    "triage": ("triage", "运维 Triage"),
    "dataQuery": ("dataQuery", "问数"),
    "marketTrends": ("marketTrends", "市场趋势"),
    "skillStudio": ("skillStudio", "Skill Studio"),
    "intelligent": ("intelligent", "Intelligent 问答"),
    "replyQueue": ("replyQueue", "回复队列"),
    "sku360": ("sku360", "SKU 360°"),
    "kolProfile": ("kolProfile", "KOL 档案"),
    "launchpad": ("launchpad", "发射台"),
    "autonomy": ("autonomy", "自治驾照"),
    "marketVoice": ("marketVoice", "市场之声"),
    "creativeLibrary": ("creativeLibrary", "创意资产库"),
    "strategyBoard": ("strategyBoard", "战略台"),
    "gtmCommand": ("gtmCommand", "GTM Command"),
}

_BEARER = re.compile(r"(?i)\bBearer\s+[A-Za-z0-9._~+/=-]{12,}")
_JWT = re.compile(r"\beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\b")
_QUERY_SECRET = re.compile(r"(?i)(access_token|api[_-]?key|token|authorization)=([^&\s]+)")
_KNOWN_PROVIDER_SECRET = re.compile(
    r"\b(?:sk-[A-Za-z0-9_-]{16,}|AIza[0-9A-Za-z_-]{20,}|apify_api_[A-Za-z0-9]{16,})\b"
)


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def canonical_json(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def redact_text(value: Any, *, limit: int = 320) -> str:
    text = str(value or "").replace("\x00", " ")
    text = _BEARER.sub("Bearer [REDACTED]", text)
    text = _JWT.sub("[REDACTED_JWT]", text)
    text = _QUERY_SECRET.sub(lambda match: f"{match.group(1)}=[REDACTED]", text)
    text = _KNOWN_PROVIDER_SECRET.sub("[REDACTED_PROVIDER_SECRET]", text)
    compact = re.sub(r"\s+", " ", text).strip()
    return compact[:limit]


def sanitize_url(value: Any) -> str:
    raw = str(value or "").strip()
    if not raw:
        return ""
    try:
        parsed = urlsplit(raw)
    except ValueError:
        return redact_text(raw, limit=240)
    if not parsed.scheme:
        return redact_text(raw, limit=240)
    host = parsed.hostname or ""
    netloc = host
    try:
        port = parsed.port
    except ValueError:
        return redact_text(raw, limit=240)
    if port is not None:
        netloc = f"{host}:{port}"
    return urlunsplit((parsed.scheme.lower(), netloc, parsed.path, "", ""))[:512]


def normalized_origin(value: Any) -> str | None:
    raw = str(value or "").strip()
    if not raw:
        return None
    try:
        parsed = urlsplit(raw)
    except ValueError:
        return None
    scheme = parsed.scheme.lower()
    if scheme in EXTENSION_SCHEMES | BROWSER_INTERNAL_SCHEMES:
        return f"{scheme}://{parsed.netloc}" if parsed.netloc else f"{scheme}:"
    if scheme not in {"http", "https"} or not parsed.hostname:
        return None
    try:
        port = parsed.port
    except ValueError:
        return None
    default = 80 if scheme == "http" else 443
    authority = parsed.hostname.lower()
    if port is not None and port != default:
        authority = f"{authority}:{port}"
    return f"{scheme}://{authority}"


def normalize_level(value: Any) -> str:
    level = str(value or "").strip().lower()
    aliases = {
        "warn": "warning",
        "critical": "error",
        "fatal": "error",
        "exceptionthrown": "exception",
        "unhandled_rejection": "unhandledrejection",
        "unhandled-rejection": "unhandledrejection",
    }
    return aliases.get(level, level or "unknown")


def _event_urls(event: Mapping[str, Any]) -> list[str]:
    values: list[str] = []
    for key in ("source_url", "url", "execution_context_origin"):
        value = str(event.get(key) or "").strip()
        if value:
            values.append(value)
    stack = event.get("stack_trace")
    if isinstance(stack, Sequence) and not isinstance(stack, (str, bytes)):
        for frame in stack:
            if isinstance(frame, Mapping):
                value = str(frame.get("url") or "").strip()
                if value:
                    values.append(value)
    return values


def classify_event(event: Mapping[str, Any], *, application_origin: str) -> str:
    """Classify by explicit provenance, with application ownership winning."""
    urls = _event_urls(event)
    schemes: list[str] = []
    origins: list[str] = []
    for value in urls:
        try:
            scheme = urlsplit(value).scheme.lower()
        except ValueError:
            scheme = ""
        if scheme:
            schemes.append(scheme)
        origin = normalized_origin(value)
        if origin:
            origins.append(origin)

    # Never let an extension frame hide an application-owned top frame/context.
    if application_origin in origins:
        return "application"
    if any(scheme in {"http", "https"} for scheme in schemes):
        return "third_party"
    if any(scheme in EXTENSION_SCHEMES for scheme in schemes):
        return "extension_noise"
    if any(scheme in BROWSER_INTERNAL_SCHEMES for scheme in schemes):
        return "browser_internal"
    return "unattributed"


def _validate_string_list(value: Any, label: str, failures: list[str]) -> list[str]:
    if not isinstance(value, list) or any(not isinstance(item, str) for item in value):
        failures.append(f"{label} must be a string list")
        return []
    return [str(item) for item in value]


def _validate_event(raw: Any, index: int, failures: list[str]) -> dict[str, Any] | None:
    if not isinstance(raw, Mapping):
        failures.append(f"event[{index}] must be an object")
        return None
    channel = str(raw.get("channel") or "").strip()
    level = normalize_level(raw.get("level"))
    text = str(raw.get("text") or "")
    if channel not in REQUIRED_CHANNELS:
        failures.append(f"event[{index}] has unsupported channel")
    if not level or level == "unknown":
        failures.append(f"event[{index}] has no recognized level")
    if len(text) > MAX_EVENT_TEXT:
        failures.append(f"event[{index}] text exceeds {MAX_EVENT_TEXT} characters")
    stack = raw.get("stack_trace", [])
    if not isinstance(stack, list) or any(not isinstance(frame, Mapping) for frame in stack):
        failures.append(f"event[{index}] stack_trace must be an object list")
        stack = []
    return {
        "channel": channel,
        "level": level,
        "text": text,
        "source_url": str(raw.get("source_url") or ""),
        "execution_context_origin": str(raw.get("execution_context_origin") or ""),
        "page_family": str(raw.get("page_family") or ""),
        "stack_trace": [dict(frame) for frame in stack[:50]],
    }


def _validate_exact_external_media_origins(
    value: Any,
    *,
    application_origin: str,
    failures: list[str],
) -> set[str]:
    origins = _validate_string_list(
        value,
        "policy.external_media_403_allowed_origins",
        failures,
    )
    if len(origins) != len(set(origins)):
        failures.append("external media 403 allowlist contains duplicates")
    accepted: set[str] = set()
    for origin in origins:
        normalized = normalized_origin(origin)
        if (
            normalized is None
            or not normalized.startswith("https://")
            or normalized == application_origin
            or origin != normalized
            or "*" in origin
        ):
            failures.append(
                "external media 403 allowlist entries must be exact external HTTPS origins"
            )
            continue
        accepted.add(origin)
    return accepted


def _evaluate_pages(
    payload: Mapping[str, Any],
    *,
    application_origin: str,
    failures: list[str],
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    manifest = (
        payload.get("page_manifest")
        if isinstance(payload.get("page_manifest"), Mapping)
        else {}
    )
    if manifest.get("schema_version") != "vkpi-browser-page-manifest/v1":
        failures.append("page manifest schema is missing or unsupported")
    manifest_rows = manifest.get("pages")
    if not isinstance(manifest_rows, list):
        failures.append("page_manifest.pages must be a list")
        manifest_rows = []
    manifest_map: dict[str, tuple[str, str]] = {}
    for index, raw in enumerate(manifest_rows):
        if not isinstance(raw, Mapping):
            failures.append(f"page manifest entry[{index}] must be an object")
            continue
        family = str(raw.get("family") or "")
        nav_key = str(raw.get("nav_key") or "")
        heading = str(raw.get("heading") or "")
        if not family or family in manifest_map:
            failures.append(f"page manifest entry[{index}] family is empty or duplicated")
            continue
        manifest_map[family] = (nav_key, heading)
    if manifest_map != REQUIRED_PAGE_FAMILIES:
        missing = sorted(set(REQUIRED_PAGE_FAMILIES) - set(manifest_map))
        extra = sorted(set(manifest_map) - set(REQUIRED_PAGE_FAMILIES))
        changed = sorted(
            family
            for family in set(manifest_map) & set(REQUIRED_PAGE_FAMILIES)
            if manifest_map[family] != REQUIRED_PAGE_FAMILIES[family]
        )
        failures.append(
            "page manifest must exactly cover the reviewed 21 families"
            f" (missing={missing}, extra={extra}, changed={changed})"
        )

    raw_pages = payload.get("pages")
    if not isinstance(raw_pages, list):
        failures.append("pages must be a list")
        raw_pages = []
    if len(raw_pages) != len(REQUIRED_PAGE_FAMILIES):
        failures.append(
            f"captured pages must contain exactly {len(REQUIRED_PAGE_FAMILIES)} entries"
        )
    rows: list[dict[str, Any]] = []
    seen: set[str] = set()
    passed = 0
    for index, raw in enumerate(raw_pages[: len(REQUIRED_PAGE_FAMILIES) + 10]):
        if not isinstance(raw, Mapping):
            failures.append(f"page[{index}] must be an object")
            continue
        family = str(raw.get("family") or "")
        expected = REQUIRED_PAGE_FAMILIES.get(family)
        nav_key = str(raw.get("nav_key") or "")
        expected_heading = str(raw.get("expected_heading") or "")
        observed_heading = str(raw.get("observed_heading") or "")
        final_url = str(raw.get("final_url") or "").strip()
        try:
            final_url_cockpit_values = parse_qs(
                urlsplit(final_url).query,
                keep_blank_values=True,
            ).get("cockpit", [])
        except ValueError:
            final_url_cockpit_values = []
        if not expected or family in seen:
            failures.append(f"page[{index}] family is unknown or duplicated")
        else:
            seen.add(family)
        proof = {
            "known_family": expected is not None,
            "nav_key_matches": expected is not None and nav_key == expected[0],
            "expected_heading_matches": expected is not None and expected_heading == expected[1],
            "observed_heading_matches": expected is not None and observed_heading == expected[1],
            "navigation_completed": raw.get("navigation_completed") is True,
            "page_settled": raw.get("page_settled") is True,
            "stage_present": raw.get("stage_present") is True,
            "heading_present": raw.get("heading_present") is True,
            "heading_matches": raw.get("heading_matches") is True,
            "cockpit_main_present": raw.get("cockpit_main_present") is True,
            "password_form_absent": raw.get("password_form_present") is False,
            "lazy_error_absent": raw.get("lazy_error_present") is False,
            "same_origin_api_idle": raw.get("same_origin_api_idle") is True,
            "same_origin_api_inflight_zero": (
                isinstance(raw.get("same_origin_api_inflight"), int)
                and not isinstance(raw.get("same_origin_api_inflight"), bool)
                and raw.get("same_origin_api_inflight") == 0
            ),
            "ready_state_complete": str(raw.get("ready_state") or "") == "complete",
            "same_origin_final_url": normalized_origin(raw.get("final_url"))
            == application_origin,
            "cockpit_query_matches_nav_key": (
                expected is not None
                and final_url_cockpit_values == [expected[0]]
            ),
        }
        page_pass = all(proof.values())
        if page_pass:
            passed += 1
        else:
            failed_proofs = sorted(key for key, value in proof.items() if not value)
            failures.append(
                f"page family {family or index} failed browser proof: {', '.join(failed_proofs)}"
            )
        rows.append(
            {
                "family": family,
                "nav_key": nav_key,
                "expected_heading": expected_heading,
                "observed_heading": redact_text(observed_heading, limit=120),
                "final_url": sanitize_url(raw.get("final_url")),
                "proof": proof,
                "pass": page_pass,
                "elapsed_ms": raw.get("elapsed_ms")
                if isinstance(raw.get("elapsed_ms"), int)
                and not isinstance(raw.get("elapsed_ms"), bool)
                and raw.get("elapsed_ms") >= 0
                else None,
            }
        )
    missing_captures = sorted(set(REQUIRED_PAGE_FAMILIES) - seen)
    if missing_captures:
        failures.append("missing captured page families: " + ", ".join(missing_captures))
    return rows, {
        "required": len(REQUIRED_PAGE_FAMILIES),
        "captured": len(rows),
        "passed": passed,
        "missing": missing_captures,
    }


def _evaluate_network(
    collection: Mapping[str, Any],
    *,
    application_origin: str,
    allowed_external_media_403_origins: set[str],
    failures: list[str],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, Any]]:
    raw_responses = collection.get("network_responses")
    raw_failures = collection.get("network_failures")
    if not isinstance(raw_responses, list):
        failures.append("collection.network_responses must be a list")
        raw_responses = []
    if not isinstance(raw_failures, list):
        failures.append("collection.network_failures must be a list")
        raw_failures = []
    if len(raw_responses) + len(raw_failures) > MAX_NETWORK_RECORDS:
        failures.append(f"network records exceed {MAX_NETWORK_RECORDS}")
        raw_responses = raw_responses[:MAX_NETWORK_RECORDS]
        raw_failures = raw_failures[: max(0, MAX_NETWORK_RECORDS - len(raw_responses))]

    responses: list[dict[str, Any]] = []
    blocking_responses = 0
    tolerated_external_media_403 = 0
    auth_me_2xx = False
    retained_http_errors = 0
    for index, raw in enumerate(raw_responses):
        if not isinstance(raw, Mapping):
            failures.append(f"network_response[{index}] must be an object")
            continue
        channel = str(raw.get("channel") or "")
        url = str(raw.get("url") or "")
        origin = normalized_origin(url)
        status = raw.get("status")
        resource_type = str(raw.get("resource_type") or "Other")
        page_family = str(raw.get("page_family") or "")
        if channel != "Network.responseReceived":
            failures.append(f"network_response[{index}] has unsupported channel")
        if (
            not isinstance(status, int)
            or isinstance(status, bool)
            or status < 100
            or status > 599
        ):
            failures.append(f"network_response[{index}] has invalid HTTP status")
            continue
        if origin is None or not origin.startswith(("http://", "https://")):
            failures.append(f"network_response[{index}] has invalid URL")
        if page_family not in set(REQUIRED_PAGE_FAMILIES) | {"bootstrap"}:
            failures.append(f"network_response[{index}] has unknown page family")
        try:
            path = urlsplit(url).path
        except ValueError:
            path = ""
        same_origin = origin == application_origin
        same_origin_api = same_origin and (path == "/health" or path.startswith("/api/"))
        is_error = status >= 400
        if is_error:
            retained_http_errors += 1
        # Successful retained responses are allowed only as same-origin API
        # collection proof. This prevents a producer from dropping an HTTP
        # error while padding the artifact with arbitrary successful assets.
        if not is_error and not same_origin_api:
            failures.append(
                f"network_response[{index}] retained an unreviewed successful resource"
            )
        tolerated = (
            status == 403
            and not same_origin
            and origin in allowed_external_media_403_origins
            and resource_type.lower() in {"image", "media"}
        )
        blocking = is_error and not tolerated
        if tolerated:
            tolerated_external_media_403 += 1
        if blocking:
            blocking_responses += 1
        if same_origin_api and path == "/api/auth/me" and 200 <= status < 300:
            auth_me_2xx = True
        responses.append(
            {
                "index": index,
                "page_family": page_family,
                "status": status,
                "resource_type": resource_type[:80],
                "provenance": (
                    "same_origin_api"
                    if same_origin_api
                    else "same_origin_asset"
                    if same_origin
                    else "external_media"
                    if resource_type.lower() in {"image", "media"}
                    else "external"
                ),
                "tolerated_external_media_403": tolerated,
                "blocking": blocking,
                "url": sanitize_url(url),
            }
        )
    if not auth_me_2xx:
        failures.append("network capture has no same-origin 2xx /api/auth/me response")
    if blocking_responses:
        failures.append(f"{blocking_responses} blocking HTTP response error(s) observed")

    loading_rows: list[dict[str, Any]] = []
    blocking_loading_failures = 0
    for index, raw in enumerate(raw_failures):
        if not isinstance(raw, Mapping):
            failures.append(f"network_failure[{index}] must be an object")
            continue
        channel = str(raw.get("channel") or "")
        page_family = str(raw.get("page_family") or "")
        canceled = raw.get("canceled") is True
        if channel != "Network.loadingFailed":
            failures.append(f"network_failure[{index}] has unsupported channel")
        if page_family not in set(REQUIRED_PAGE_FAMILIES) | {"bootstrap"}:
            failures.append(f"network_failure[{index}] has unknown page family")
        if not canceled:
            blocking_loading_failures += 1
        loading_rows.append(
            {
                "index": index,
                "page_family": page_family,
                "resource_type": str(raw.get("resource_type") or "Other")[:80],
                "canceled": canceled,
                "blocking": not canceled,
                "blocked_reason": redact_text(raw.get("blocked_reason"), limit=120),
                "error_text": redact_text(raw.get("error_text"), limit=240),
            }
        )
    if blocking_loading_failures:
        failures.append(
            f"{blocking_loading_failures} non-cancelled network loading failure(s) observed"
        )

    summary = collection.get("network_summary")
    if not isinstance(summary, Mapping):
        failures.append("collection.network_summary must be an object")
        summary = {}
    expected_counts = {
        "response_error_count_total": retained_http_errors,
        "retained_response_count": len(raw_responses),
        "loading_failure_count": len(raw_failures),
        "inflight_same_origin_api_final": 0,
    }
    for key, expected in expected_counts.items():
        value = summary.get(key)
        if (
            not isinstance(value, int)
            or isinstance(value, bool)
            or value != expected
        ):
            failures.append(f"network summary {key} does not match retained evidence")
    response_count_total = summary.get("response_count_total")
    if (
        not isinstance(response_count_total, int)
        or isinstance(response_count_total, bool)
        or response_count_total < len(raw_responses)
    ):
        failures.append("network summary response_count_total is invalid")
        response_count_total = 0
    request_count_total = summary.get("request_count_total")
    if (
        not isinstance(request_count_total, int)
        or isinstance(request_count_total, bool)
        or request_count_total < response_count_total
    ):
        failures.append("network summary request_count_total is invalid")
        request_count_total = 0
    return responses, loading_rows, {
        "request_count_total": request_count_total,
        "response_count_total": response_count_total,
        "retained_responses": len(responses),
        "retained_http_errors": retained_http_errors,
        "blocking_http_errors": blocking_responses,
        "tolerated_external_media_403": tolerated_external_media_403,
        "loading_failures": len(loading_rows),
        "blocking_loading_failures": blocking_loading_failures,
        "auth_me_2xx_observed": auth_me_2xx,
    }


def evaluate_capture(payload: Mapping[str, Any], *, require_live: bool = True) -> dict[str, Any]:
    failures: list[str] = []
    if payload.get("schema_version") != CAPTURE_SCHEMA_VERSION:
        failures.append(f"schema_version must be {CAPTURE_SCHEMA_VERSION}")

    target_url = str(payload.get("target_url") or "").strip()
    application_origin = normalized_origin(target_url)
    if application_origin is None or not application_origin.startswith(("http://", "https://")):
        failures.append("target_url must be an absolute HTTP(S) URL")
        application_origin = "invalid://target"

    run = payload.get("run") if isinstance(payload.get("run"), Mapping) else {}
    kind = str(run.get("kind") or "").strip().lower()
    if kind not in {"fixture", "live"}:
        failures.append("run.kind must be fixture or live")
    if require_live and kind != "live":
        failures.append("release evaluation requires run.kind=live")
    if run.get("navigation_completed") is not True:
        failures.append("navigation did not complete")
    if run.get("page_settled") is not True:
        failures.append("page did not reach the configured settle point")

    # Do not trust a producer's authenticated_surface assertion on its own.
    # Release proof requires a same-origin /api/auth/me result and the final
    # cockpit DOM; an invalid token or a login/reset page must fail closed.
    auth_probe = run.get("auth_probe") if isinstance(run.get("auth_probe"), Mapping) else {}
    surface_probe = (
        run.get("surface_probe")
        if isinstance(run.get("surface_probe"), Mapping)
        else {}
    )
    auth_http_status = auth_probe.get("http_status")
    auth_proof = {
        "producer_authenticated_surface": run.get("authenticated_surface") is True,
        "request_completed": auth_probe.get("request_completed") is True,
        "same_origin": auth_probe.get("same_origin") is True,
        "token_present": auth_probe.get("token_present") is True,
        "http_status_2xx": (
            isinstance(auth_http_status, int)
            and not isinstance(auth_http_status, bool)
            and 200 <= auth_http_status < 300
            and auth_probe.get("http_2xx") is True
        ),
        "status_success": auth_probe.get("status_success") is True,
        "user_present": auth_probe.get("user_present") is True,
        "cockpit_main_present": surface_probe.get("cockpit_main_present") is True,
        "password_form_absent": surface_probe.get("password_form_present") is False,
    }
    for proof, passed in auth_proof.items():
        if not passed:
            failures.append(f"authenticated private surface proof missing: {proof}")
    final_origin = normalized_origin(run.get("final_url"))
    if final_origin != application_origin:
        failures.append("final_url origin does not match target_url origin")

    page_rows, page_metrics = _evaluate_pages(
        payload,
        application_origin=application_origin,
        failures=failures,
    )

    policy = payload.get("policy") if isinstance(payload.get("policy"), Mapping) else {}
    allowed_external_media_403_origins = _validate_exact_external_media_origins(
        policy.get("external_media_403_allowed_origins", []),
        application_origin=application_origin,
        failures=failures,
    )

    browser = payload.get("browser") if isinstance(payload.get("browser"), Mapping) else {}
    launch_args = _validate_string_list(browser.get("launch_args"), "browser.launch_args", failures)
    cleanup = payload.get("cleanup") if isinstance(payload.get("cleanup"), Mapping) else {}
    extension_free_proof = {
        "engine_chromium": browser.get("engine") == "chromium",
        "process_owned": browser.get("process_owned") is True,
        "profile_mode_ephemeral": browser.get("profile_mode") == "ephemeral",
        "off_the_record": browser.get("off_the_record") is True,
        "credential_persistence_disabled": browser.get("credential_persistence") is False,
        "ephemeral_user_data_dir_flag": any(
            item.startswith("--user-data-dir=") for item in launch_args
        ),
        "incognito_flag": "--incognito" in launch_args,
        "crash_reporter_disabled": "--disable-crash-reporter" in launch_args,
        "extensions_disabled": browser.get("extensions_disabled") is True,
        "disable_extensions_flag": "--disable-extensions" in launch_args,
        "component_background_extensions_disabled": (
            "--disable-component-extensions-with-background-pages" in launch_args
        ),
        "owned_browser_exited": cleanup.get("browser_exited") is True,
        "ephemeral_profile_removed": cleanup.get("profile_removed") is True,
    }
    for proof, passed in extension_free_proof.items():
        if not passed:
            failures.append(f"extension-free proof missing: {proof}")

    collection = payload.get("collection") if isinstance(payload.get("collection"), Mapping) else {}
    enabled_domains = set(_validate_string_list(collection.get("enabled_domains"), "collection.enabled_domains", failures))
    channels = set(_validate_string_list(collection.get("event_channels"), "collection.event_channels", failures))
    network_channels = set(
        _validate_string_list(
            collection.get("network_event_channels"),
            "collection.network_event_channels",
            failures,
        )
    )
    missing_domains = sorted(REQUIRED_DOMAINS - enabled_domains)
    missing_channels = sorted(REQUIRED_CHANNELS - channels)
    missing_network_channels = sorted(REQUIRED_NETWORK_CHANNELS - network_channels)
    if missing_domains:
        failures.append("missing CDP domains: " + ", ".join(missing_domains))
    if missing_channels:
        failures.append("missing event channels: " + ", ".join(missing_channels))
    if missing_network_channels:
        failures.append(
            "missing network event channels: " + ", ".join(missing_network_channels)
        )

    raw_events = collection.get("events")
    if not isinstance(raw_events, list):
        failures.append("collection.events must be a list")
        raw_events = []
    if len(raw_events) > MAX_EVENTS:
        failures.append(f"collection.events exceeds {MAX_EVENTS}")
        raw_events = raw_events[:MAX_EVENTS]

    network_responses, network_failures, network_metrics = _evaluate_network(
        collection,
        application_origin=application_origin,
        allowed_external_media_403_origins=allowed_external_media_403_origins,
        failures=failures,
    )
    tolerated_media_response_urls = {
        str(row.get("url") or "")
        for row in network_responses
        if row.get("tolerated_external_media_403") is True
    }

    context_origins = _validate_string_list(
        collection.get("execution_context_origins", []),
        "collection.execution_context_origins",
        failures,
    )
    extension_contexts = [
        origin
        for origin in context_origins
        if (urlsplit(origin).scheme.lower() if origin else "") in EXTENSION_SCHEMES
    ]

    sanitized_events: list[dict[str, Any]] = []
    severity_counts: Counter[str] = Counter()
    provenance_counts: Counter[str] = Counter()
    blocking_count = 0
    extension_event_count = 0
    tolerated_media_console_count = 0
    for index, raw in enumerate(raw_events):
        event = _validate_event(raw, index, failures)
        if event is None:
            continue
        provenance = classify_event(event, application_origin=application_origin)
        level = normalize_level(event["level"])
        page_family = event["page_family"]
        if page_family not in set(REQUIRED_PAGE_FAMILIES) | {"bootstrap"}:
            failures.append(f"event[{index}] has unknown page family")
        sanitized_source_url = sanitize_url(event["source_url"])
        tolerated_media_console = (
            provenance == "third_party"
            and sanitized_source_url in tolerated_media_response_urls
            and normalized_origin(event["source_url"])
            in allowed_external_media_403_origins
        )
        blocking = (
            level in BLOCKING_LEVELS
            and provenance != "extension_noise"
            and not tolerated_media_console
        )
        if blocking:
            blocking_count += 1
        if tolerated_media_console:
            tolerated_media_console_count += 1
        if provenance == "extension_noise":
            extension_event_count += 1
        severity_counts[level] += 1
        provenance_counts[provenance] += 1
        text_bytes = event["text"].encode("utf-8", errors="replace")
        sanitized_events.append(
            {
                "index": index,
                "channel": event["channel"],
                "level": level,
                "page_family": page_family,
                "provenance": provenance,
                "blocking": blocking,
                "tolerated_external_media_403": tolerated_media_console,
                "text_preview": redact_text(event["text"]),
                "text_sha256": hashlib.sha256(text_bytes).hexdigest(),
                "source_url": sanitized_source_url,
                "execution_context_origin": sanitize_url(event["execution_context_origin"]),
                "stack_urls": [
                    sanitize_url(frame.get("url"))
                    for frame in event["stack_trace"]
                    if frame.get("url")
                ][:10],
            }
        )

    # A claimed extension-free run containing an extension context/event is a
    # provenance contradiction, not a reason to silently ignore the event.
    if extension_contexts:
        failures.append("extension execution context observed in extension-free capture")
    if extension_event_count:
        failures.append("extension event observed in extension-free capture")
    if blocking_count:
        failures.append(f"{blocking_count} blocking console event(s) observed")

    result: dict[str, Any] = {
        "schema_version": GATE_SCHEMA_VERSION,
        "evaluated_at": utc_now(),
        "capture": {
            "schema_version": payload.get("schema_version"),
            "captured_at": payload.get("captured_at"),
            "run_kind": kind,
            "target_origin": application_origin,
            "final_origin": final_origin,
            "extension_free_proof": extension_free_proof,
            "required_live": bool(require_live),
            "authenticated_surface_proof": auth_proof,
            "page_manifest_families": sorted(REQUIRED_PAGE_FAMILIES),
            "external_media_403_allowed_origins": sorted(
                allowed_external_media_403_origins
            ),
        },
        "overall": {
            "pass": not failures,
            "release_eligible": kind == "live" and not failures,
            "failures": failures,
        },
        "metrics": {
            "total_events": len(sanitized_events),
            "blocking_events": blocking_count,
            "extension_noise_events": extension_event_count,
            "extension_contexts": len(extension_contexts),
            "severity_counts": dict(sorted(severity_counts.items())),
            "provenance_counts": dict(sorted(provenance_counts.items())),
            "tolerated_external_media_403_console_events": tolerated_media_console_count,
            "pages": page_metrics,
            "network": network_metrics,
        },
        "pages": page_rows,
        "events": sanitized_events,
        "network": {
            "responses": network_responses,
            "loading_failures": network_failures,
        },
        "claims": {
            "text_only_extension_exemptions": False,
            "application_warning_or_error_allowlist": False,
            "third_party_error_allowlist": False,
            "unattributed_error_allowlist": False,
            "extension_noise_diagnostic_only": True,
            "reviewed_page_families_required": len(REQUIRED_PAGE_FAMILIES),
            "same_origin_http_errors_fail_closed": True,
            "external_http_errors_fail_closed": True,
            "external_media_403_requires_exact_origin_allowlist": True,
            "external_media_403_console_requires_network_match": True,
            "live_extension_free_run_completed": kind == "live" and not failures,
        },
    }
    deterministic = {
        key: result[key]
        for key in (
            "capture",
            "overall",
            "metrics",
            "pages",
            "events",
            "network",
            "claims",
        )
    }
    result["calculation_sha256"] = hashlib.sha256(canonical_json(deterministic)).hexdigest()
    return result


def load_capture(path: Path) -> dict[str, Any]:
    if not path.is_file():
        raise ValueError("capture path is not a file")
    size = path.stat().st_size
    if size <= 0 or size > MAX_CAPTURE_BYTES:
        raise ValueError(f"capture file must be within (0, {MAX_CAPTURE_BYTES}] bytes")
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError("capture is not readable UTF-8 JSON") from exc
    if not isinstance(payload, dict):
        raise ValueError("capture root must be an object")
    return payload


def write_json(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True, allow_nan=False) + "\n",
        encoding="utf-8",
    )


def parse_args(argv: Iterable[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, required=True, help="CDP capture JSON")
    parser.add_argument("--json-out", type=Path, help="optional machine-readable gate report")
    parser.add_argument(
        "--allow-fixture",
        action="store_true",
        help="contract-test mode only; release usage must omit this flag",
    )
    return parser.parse_args(list(argv) if argv is not None else None)


def main(argv: Iterable[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        capture = load_capture(args.input)
        report = evaluate_capture(capture, require_live=not args.allow_fixture)
        exit_code = 0 if report["overall"]["pass"] else 1
    except ValueError as exc:
        report = {
            "schema_version": GATE_SCHEMA_VERSION,
            "evaluated_at": utc_now(),
            "overall": {"pass": False, "release_eligible": False, "failures": [str(exc)]},
            "claims": {"live_extension_free_run_completed": False},
        }
        exit_code = 2
    output = json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True, allow_nan=False)
    sys.stdout.write(output + "\n")
    if args.json_out:
        write_json(args.json_out, report)
    status = (
        "PASS browser console release gate"
        if exit_code == 0
        else "FAIL browser console release gate"
    )
    sys.stderr.write(status + "\n")
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
