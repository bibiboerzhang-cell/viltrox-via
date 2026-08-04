"""Pure page and public-release identity checks for the browser release gate."""
from __future__ import annotations

import re
from typing import Any, Callable, Mapping
from urllib.parse import parse_qs, urlsplit


RELEASE_IDENTITY_SCHEMA_VERSION = "vkpi-browser-release-identity/v1"
RELEASE_IDENTITY_QUERY_KEY = "_vkpi_release_probe"
MAX_APP_ASSET_BYTES = 50 * 1024 * 1024
_SHA40 = re.compile(r"[0-9a-f]{40}")
_SHA256 = re.compile(r"[0-9a-f]{64}")
_APP_ASSET = re.compile(r"app-[A-Za-z0-9_-]+\.js")


def evaluate_pages(
    payload: Mapping[str, Any],
    *,
    application_origin: str,
    required_page_families: Mapping[str, tuple[str, str]],
    normalized_origin: Callable[[Any], str | None],
    redact_text: Callable[..., str],
    sanitize_url: Callable[[Any], str],
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
    if manifest_map != required_page_families:
        missing = sorted(set(required_page_families) - set(manifest_map))
        extra = sorted(set(manifest_map) - set(required_page_families))
        changed = sorted(
            family
            for family in set(manifest_map) & set(required_page_families)
            if manifest_map[family] != required_page_families[family]
        )
        failures.append(
            "page manifest must exactly cover the reviewed 21 families"
            f" (missing={missing}, extra={extra}, changed={changed})"
        )

    raw_pages = payload.get("pages")
    if not isinstance(raw_pages, list):
        failures.append("pages must be a list")
        raw_pages = []
    if len(raw_pages) != len(required_page_families):
        failures.append(
            f"captured pages must contain exactly {len(required_page_families)} entries"
        )
    rows: list[dict[str, Any]] = []
    seen: set[str] = set()
    passed = 0
    for index, raw in enumerate(raw_pages[: len(required_page_families) + 10]):
        if not isinstance(raw, Mapping):
            failures.append(f"page[{index}] must be an object")
            continue
        family = str(raw.get("family") or "")
        expected = required_page_families.get(family)
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
    missing_captures = sorted(set(required_page_families) - seen)
    if missing_captures:
        failures.append("missing captured page families: " + ", ".join(missing_captures))
    return rows, {
        "required": len(required_page_families),
        "captured": len(rows),
        "passed": passed,
        "missing": missing_captures,
    }


def _normalized_hex(value: Any, pattern: re.Pattern[str]) -> str:
    text = str(value or "").strip().lower()
    return text if pattern.fullmatch(text) else ""


def _normalized_app_asset(value: Any) -> str:
    text = str(value or "").strip()
    return text if _APP_ASSET.fullmatch(text) else ""


def evaluate_release_identity(
    payload: Mapping[str, Any],
    *,
    network_metrics: Mapping[str, Any],
    expected_git_sha: str | None,
    expected_app_asset: str | None,
    expected_app_asset_sha256: str | None,
    require_expected_identity: bool,
    redact_text: Callable[..., str],
    failures: list[str],
) -> dict[str, Any]:
    raw_identity = payload.get("release_identity")
    if not isinstance(raw_identity, Mapping):
        failures.append("release_identity must be an object")
        raw_identity = {}
    cache = (
        raw_identity.get("cache_bypass")
        if isinstance(raw_identity.get("cache_bypass"), Mapping)
        else {}
    )
    health = (
        raw_identity.get("health")
        if isinstance(raw_identity.get("health"), Mapping)
        else {}
    )
    frontend = (
        raw_identity.get("frontend")
        if isinstance(raw_identity.get("frontend"), Mapping)
        else {}
    )
    network_probe = (
        network_metrics.get("release_identity_probe")
        if isinstance(network_metrics.get("release_identity_probe"), Mapping)
        else {}
    )

    build_git_sha = _normalized_hex(health.get("build_git_sha"), _SHA40)
    build_client_sha = _normalized_hex(health.get("build_client_sha"), _SHA40)
    server_git_sha = _normalized_hex(health.get("server_git_sha"), _SHA40)
    client_git_sha = _normalized_hex(health.get("client_git_sha"), _SHA40)
    loaded_app_asset = _normalized_app_asset(frontend.get("loaded_app_asset"))
    index_app_asset = _normalized_app_asset(frontend.get("index_app_asset"))
    asset_sha256 = _normalized_hex(frontend.get("asset_sha256"), _SHA256)
    network_app_assets = sorted(
        {
            item
            for item in network_probe.get("app_assets", [])
            if isinstance(item, str) and _APP_ASSET.fullmatch(item)
        }
    ) if isinstance(network_probe.get("app_assets"), list) else []

    health_status = health.get("http_status")
    index_status = frontend.get("index_http_status")
    asset_status = frontend.get("asset_http_status")
    asset_bytes = frontend.get("asset_bytes")
    internal_proof = {
        "schema_version": raw_identity.get("schema_version")
        == RELEASE_IDENTITY_SCHEMA_VERSION,
        "cdp_cache_disabled": cache.get("cdp_cache_disabled") is True,
        "service_worker_bypassed": cache.get("service_worker_bypassed") is True,
        "fetch_cache_mode_no_store": cache.get("fetch_cache_mode") == "no-store",
        "request_cache_control_no_store": cache.get("request_cache_control")
        == "no-cache, no-store, max-age=0",
        "request_pragma_no_cache": cache.get("request_pragma") == "no-cache",
        "unique_query_parameter": cache.get("unique_query_parameter")
        == RELEASE_IDENTITY_QUERY_KEY,
        "unique_request_nonces": cache.get("unique_request_nonces") is True,
        "health_request_completed": health.get("request_completed") is True,
        "health_same_origin": health.get("same_origin") is True,
        "health_http_2xx": (
            isinstance(health_status, int)
            and not isinstance(health_status, bool)
            and 200 <= health_status < 300
            and health.get("http_2xx") is True
        ),
        "health_status_ok": health.get("status_ok") is True,
        "health_build_git_sha_valid": bool(build_git_sha),
        "health_build_client_sha_valid": bool(build_client_sha),
        "health_server_git_sha_valid": bool(server_git_sha),
        "health_client_git_sha_valid": bool(client_git_sha),
        "health_client_source_frontend_dist": health.get("build_client_source")
        == "frontend_dist",
        "health_sha_aligned": health.get("sha_aligned") is True,
        "health_sha_self_consistent": bool(
            build_git_sha
            and build_git_sha
            == build_client_sha
            == server_git_sha
            == client_git_sha
        ),
        "loaded_app_asset_count_one": frontend.get("loaded_app_asset_count") == 1,
        "loaded_app_asset_valid": bool(loaded_app_asset),
        "index_request_completed": frontend.get("index_request_completed") is True,
        "index_same_origin": frontend.get("index_same_origin") is True,
        "index_http_2xx": (
            isinstance(index_status, int)
            and not isinstance(index_status, bool)
            and 200 <= index_status < 300
            and frontend.get("index_http_2xx") is True
        ),
        "index_content_type_html": frontend.get("index_content_type_html") is True,
        "index_app_asset_count_one": frontend.get("index_app_asset_count") == 1,
        "index_app_asset_valid": bool(index_app_asset),
        "loaded_matches_index": bool(
            frontend.get("loaded_matches_index") is True
            and loaded_app_asset
            and loaded_app_asset == index_app_asset
        ),
        "asset_request_completed": frontend.get("asset_request_completed") is True,
        "asset_same_origin": frontend.get("asset_same_origin") is True,
        "asset_http_2xx": (
            isinstance(asset_status, int)
            and not isinstance(asset_status, bool)
            and 200 <= asset_status < 300
            and frontend.get("asset_http_2xx") is True
        ),
        "asset_content_type_javascript": frontend.get("asset_content_type_javascript")
        is True,
        "asset_size_valid": (
            isinstance(asset_bytes, int)
            and not isinstance(asset_bytes, bool)
            and 0 < asset_bytes <= MAX_APP_ASSET_BYTES
        ),
        "asset_digest_algorithm_sha256": frontend.get("digest_algorithm") == "sha256",
        "asset_sha256_valid": bool(asset_sha256),
        "network_health_probe_uncached": (
            isinstance(network_probe.get("health_uncached_2xx"), int)
            and not isinstance(network_probe.get("health_uncached_2xx"), bool)
            and network_probe.get("health_uncached_2xx") >= 1
        ),
        "network_index_probe_uncached": (
            isinstance(network_probe.get("index_uncached_2xx"), int)
            and not isinstance(network_probe.get("index_uncached_2xx"), bool)
            and network_probe.get("index_uncached_2xx") >= 1
        ),
        "network_app_asset_probe_uncached": (
            isinstance(network_probe.get("app_asset_uncached_2xx"), int)
            and not isinstance(network_probe.get("app_asset_uncached_2xx"), bool)
            and network_probe.get("app_asset_uncached_2xx") >= 1
        ),
        "network_app_asset_matches_observed": bool(
            loaded_app_asset and loaded_app_asset in network_app_assets
        ),
    }
    failed_internal = sorted(
        name for name, passed in internal_proof.items() if not passed
    )
    if failed_internal:
        failures.append(
            "public release identity proof failed: " + ", ".join(failed_internal)
        )

    expected_git = _normalized_hex(expected_git_sha, _SHA40)
    expected_asset = _normalized_app_asset(expected_app_asset)
    expected_digest = _normalized_hex(expected_app_asset_sha256, _SHA256)
    any_expectation = any(
        value is not None
        for value in (expected_git_sha, expected_app_asset, expected_app_asset_sha256)
    )
    binding_required = bool(require_expected_identity or any_expectation)
    expectations_complete = bool(expected_git and expected_asset and expected_digest)
    candidate_proof = {
        "expectations_complete_and_valid": expectations_complete,
        "health_build_git_sha_matches": bool(expected_git and build_git_sha == expected_git),
        "health_build_client_sha_matches": bool(expected_git and build_client_sha == expected_git),
        "health_server_git_sha_matches": bool(expected_git and server_git_sha == expected_git),
        "health_client_git_sha_matches": bool(expected_git and client_git_sha == expected_git),
        "loaded_app_asset_matches": bool(expected_asset and loaded_app_asset == expected_asset),
        "index_app_asset_matches": bool(expected_asset and index_app_asset == expected_asset),
        "network_app_asset_matches": bool(expected_asset and expected_asset in network_app_assets),
        "asset_sha256_matches": bool(expected_digest and asset_sha256 == expected_digest),
    }
    candidate_binding_pass = expectations_complete and all(candidate_proof.values())
    if binding_required and not candidate_binding_pass:
        failed_candidate = sorted(
            name for name, passed in candidate_proof.items() if not passed
        )
        failures.append(
            "frozen candidate identity binding failed: " + ", ".join(failed_candidate)
        )

    return {
        "schema_version": raw_identity.get("schema_version"),
        "binding_required": binding_required,
        "expected": {
            "git_sha": expected_git or None,
            "app_asset": expected_asset or None,
            "app_asset_sha256": expected_digest or None,
        },
        "observed": {
            "build_git_sha": build_git_sha or None,
            "build_client_sha": build_client_sha or None,
            "server_git_sha": server_git_sha or None,
            "client_git_sha": client_git_sha or None,
            "loaded_app_asset": loaded_app_asset or None,
            "index_app_asset": index_app_asset or None,
            "asset_sha256": asset_sha256 or None,
            "asset_bytes": asset_bytes
            if isinstance(asset_bytes, int) and not isinstance(asset_bytes, bool)
            else None,
            "network_app_assets": network_app_assets,
            "health_response_cache_control": redact_text(
                health.get("response_cache_control"), limit=160
            ),
            "index_response_cache_control": redact_text(
                frontend.get("index_response_cache_control"), limit=160
            ),
            "asset_response_cache_control": redact_text(
                frontend.get("asset_response_cache_control"), limit=160
            ),
        },
        "internal_proof": internal_proof,
        "candidate_proof": candidate_proof,
        "internal_pass": not failed_internal,
        "candidate_binding_pass": candidate_binding_pass,
        "pass": not failed_internal
        and (not binding_required or candidate_binding_pass),
    }
