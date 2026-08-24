#!/usr/bin/env python3
"""Non-destructive cold/warm HTTP audit for reviewed V-KPI read surfaces.

Two evidence levels are deliberately separate:

* ``warm-observe`` may target a deployed service.  It never deletes cache
  entries and only proves that repeated reads converge to observable hits.
* ``strict-local-cold-warm`` is loopback-only.  It requires the first response
  to prove ``miss_builder`` and every following response to prove ``hit``.
  The auditor still never clears a cache; callers must start a disposable
  candidate with an isolated empty cache and a read-only database.

Only reviewed GET routes are callable.  Response bodies are hashed and size
counted but are never emitted.  The script itself performs no provider calls,
business writes, cache deletion, or cache-clear request.
"""
from __future__ import annotations

import argparse
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import http.client
import json
import math
import os
from pathlib import Path
import re
import ssl
import sys
import time
from typing import Any, Callable, Mapping, Protocol
from urllib.parse import parse_qsl, urlsplit


SCHEMA_VERSION = "vkpi.read-performance-audit.v1"
MODE_WARM = "warm-observe"
MODE_STRICT = "strict-local-cold-warm"
MAX_RESPONSE_BYTES = 8 * 1024 * 1024
SAFE_CACHE_OUTCOMES = frozenset(
    {"hit", "miss_builder", "miss_wait_hit", "miss_distributed_hit"}
)
LOOPBACK_HOSTS = frozenset({"127.0.0.1", "localhost", "::1"})
SAFE_TOKEN_RE = re.compile(r"^[A-Za-z0-9._~+/=-]{16,8192}$")
SAFE_HEADER_TOKEN_RE = re.compile(r"^[A-Za-z0-9._:-]{1,80}$")


@dataclass(frozen=True)
class EndpointSpec:
    endpoint_id: str
    path: str
    surface: str
    timing_metric: str


ENDPOINTS: dict[str, EndpointSpec] = {
    "gtm-summary": EndpointSpec(
        endpoint_id="gtm-summary",
        path="/api/admin/vkpi/market-brain/summary",
        surface="gtm",
        timing_metric="gtm-cache",
    ),
    "dashboard-summary": EndpointSpec(
        endpoint_id="dashboard-summary",
        path="/api/admin/vkpi/dashboard?window_days=30&scope=all",
        surface="dashboard",
        timing_metric="dashboard-cache",
    ),
}


@dataclass(frozen=True)
class HttpResult:
    status: int
    headers: Mapping[str, str]
    body: bytes
    latency_ms: float


class Requester(Protocol):
    def __call__(self, path: str) -> HttpResult: ...


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _nearest_rank(values: list[float], quantile: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    index = max(0, math.ceil(len(ordered) * quantile) - 1)
    return ordered[index]


def _latency_summary(values: list[float]) -> dict[str, float]:
    return {
        "min_ms": round(min(values), 3) if values else 0.0,
        "median_ms": round(_nearest_rank(values, 0.50), 3),
        "p95_ms": round(_nearest_rank(values, 0.95), 3),
        "max_ms": round(max(values), 3) if values else 0.0,
    }


def _normalize_headers(headers: Mapping[str, str]) -> dict[str, str]:
    return {str(key).strip().lower(): str(value).strip() for key, value in headers.items()}


def _safe_base_url(value: str, *, mode: str) -> tuple[str, str, int | None]:
    parsed = urlsplit(str(value or "").strip())
    if (
        parsed.scheme not in {"http", "https"}
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
        or parsed.path not in {"", "/"}
    ):
        raise ValueError("base_url_must_be_origin_only_http_or_https")
    host = parsed.hostname.lower()
    if host not in LOOPBACK_HOSTS and parsed.scheme != "https":
        raise ValueError("non_loopback_requires_https")
    if mode == MODE_STRICT and host not in LOOPBACK_HOSTS:
        raise ValueError("strict_cold_warm_is_loopback_only")
    return parsed.scheme, host, parsed.port


def _validate_endpoint(spec: EndpointSpec) -> None:
    parsed = urlsplit(spec.path)
    if parsed.scheme or parsed.netloc or parsed.fragment:
        raise ValueError(f"unsafe_endpoint:{spec.endpoint_id}")
    if parsed.path == "/api/admin/vkpi/market-brain/summary":
        if (
            parsed.query
            or spec.endpoint_id != "gtm-summary"
            or spec.surface != "gtm"
            or spec.timing_metric != "gtm-cache"
        ):
            raise ValueError(f"unsafe_endpoint_query:{spec.endpoint_id}")
        return
    if parsed.path == "/api/admin/vkpi/dashboard":
        query = parse_qsl(parsed.query, keep_blank_values=True, strict_parsing=True)
        if (
            query != [("window_days", "30"), ("scope", "all")]
            or spec.endpoint_id != "dashboard-summary"
            or spec.surface != "dashboard"
            or spec.timing_metric != "dashboard-cache"
        ):
            raise ValueError(f"unsafe_endpoint_query:{spec.endpoint_id}")
        return
    raise ValueError(f"endpoint_not_on_reviewed_read_allowlist:{spec.endpoint_id}")


def _load_token(*, token_file: Path | None, allow_unauthenticated: bool, host: str) -> str:
    token = ""
    if token_file is not None:
        if token_file.is_symlink() or not token_file.is_file():
            raise ValueError("token_file_is_unsafe")
        token = token_file.read_text(encoding="utf-8").strip()
    else:
        token = str(os.environ.get("VKPI_PERF_AUDIT_TOKEN") or "").strip()
    if token:
        if not SAFE_TOKEN_RE.fullmatch(token):
            raise ValueError("token_format_invalid")
        return token
    if allow_unauthenticated and host in LOOPBACK_HOSTS:
        return ""
    raise ValueError("authentication_token_required")


class HttpRequester:
    """Small keep-alive transport; only response metadata leaves this object."""

    def __init__(
        self,
        *,
        base_url: str,
        token: str,
        timeout_seconds: float,
    ) -> None:
        scheme, host, port = _safe_base_url(base_url, mode=MODE_WARM)
        self._scheme = scheme
        self._host = host
        self._port = port
        self._token = token
        self._timeout = timeout_seconds
        self._conn: http.client.HTTPConnection | None = None

    def _connection(self) -> http.client.HTTPConnection:
        if self._conn is None:
            if self._scheme == "https":
                self._conn = http.client.HTTPSConnection(
                    self._host,
                    self._port,
                    timeout=self._timeout,
                    context=ssl.create_default_context(),
                )
            else:
                self._conn = http.client.HTTPConnection(
                    self._host,
                    self._port,
                    timeout=self._timeout,
                )
        return self._conn

    def __call__(self, path: str) -> HttpResult:
        headers = {
            "Accept": "application/json",
            "Cache-Control": "no-cache",
            "User-Agent": "vkpi-read-performance-audit/1",
        }
        if self._token:
            headers["Authorization"] = f"Bearer {self._token}"
        started = time.perf_counter()
        connection = self._connection()
        try:
            connection.request("GET", path, headers=headers)
            response = connection.getresponse()
            body = response.read(MAX_RESPONSE_BYTES + 1)
        except Exception:
            if self._conn is not None:
                self._conn.close()
            self._conn = None
            raise
        latency_ms = (time.perf_counter() - started) * 1000
        if len(body) > MAX_RESPONSE_BYTES:
            raise ValueError("response_exceeds_size_limit")
        response_headers = {str(key): str(value) for key, value in response.getheaders()}
        return HttpResult(
            status=int(response.status),
            headers=response_headers,
            body=body,
            latency_ms=latency_ms,
        )

    def close(self) -> None:
        if self._conn is not None:
            self._conn.close()
            self._conn = None


def _server_timing_metrics(value: str) -> frozenset[str]:
    metrics: set[str] = set()
    for member in str(value or "").split(","):
        name = member.split(";", 1)[0].strip().lower()
        if SAFE_HEADER_TOKEN_RE.fullmatch(name):
            metrics.add(name)
    return frozenset(metrics)


def _sample(
    result: HttpResult,
    *,
    index: int,
    timing_metric: str,
) -> tuple[dict[str, Any], list[str]]:
    headers = _normalize_headers(result.headers)
    errors: list[str] = []
    content_type = headers.get("content-type", "").lower()
    cache_outcome = headers.get("x-vkpi-cache", "").lower()
    cache_builder = headers.get("x-vkpi-cache-builder", "")
    key_version = headers.get("x-vkpi-cache-key-version", "")
    server_timing = _server_timing_metrics(headers.get("server-timing", ""))
    builder_timing_metric = timing_metric.removesuffix("-cache") + "-builder"
    if result.status != 200:
        errors.append(f"sample_{index}:http_status_{result.status}")
    if "application/json" not in content_type:
        errors.append(f"sample_{index}:content_type_not_json")
    try:
        json.loads(result.body)
    except (UnicodeDecodeError, json.JSONDecodeError):
        errors.append(f"sample_{index}:invalid_json")
    if cache_outcome not in SAFE_CACHE_OUTCOMES:
        errors.append(f"sample_{index}:cache_outcome_missing_or_invalid")
    if cache_builder not in {"0", "1"}:
        errors.append(f"sample_{index}:cache_builder_missing_or_invalid")
    if not SAFE_HEADER_TOKEN_RE.fullmatch(key_version):
        errors.append(f"sample_{index}:cache_key_version_missing_or_invalid")
    if timing_metric not in server_timing:
        errors.append(f"sample_{index}:cache_server_timing_missing")
    if cache_outcome == "miss_builder" and cache_builder != "1":
        errors.append(f"sample_{index}:miss_builder_flag_inconsistent")
    if cache_outcome in SAFE_CACHE_OUTCOMES - {"miss_builder"} and cache_builder != "0":
        errors.append(f"sample_{index}:non_builder_flag_inconsistent")
    if cache_builder == "1" and builder_timing_metric not in server_timing:
        errors.append(f"sample_{index}:builder_timing_missing")
    if cache_builder == "0" and builder_timing_metric in server_timing:
        errors.append(f"sample_{index}:builder_timing_unexpected")
    return (
        {
            "index": index,
            "http_status": int(result.status),
            "latency_ms": round(float(result.latency_ms), 3),
            "response_bytes": len(result.body),
            "response_sha256": hashlib.sha256(result.body).hexdigest(),
            "cache_outcome": cache_outcome or "missing",
            "cache_builder": cache_builder or "missing",
            "cache_key_version": key_version or "missing",
            "server_timing_cache_present": timing_metric in server_timing,
            "server_timing_builder_present": builder_timing_metric in server_timing,
        },
        errors,
    )


def audit_endpoint(
    spec: EndpointSpec,
    *,
    requester: Requester,
    mode: str,
    warm_samples: int,
    cold_max_ms: float,
    warm_p95_max_ms: float,
    min_speedup: float,
) -> dict[str, Any]:
    _validate_endpoint(spec)
    samples: list[dict[str, Any]] = []
    errors: list[str] = []
    for index in range(warm_samples + 1):
        try:
            sample, sample_errors = _sample(
                requester(spec.path),
                index=index,
                timing_metric=spec.timing_metric,
            )
        except Exception as exc:
            errors.append(f"sample_{index}:transport_{type(exc).__name__}")
            break
        samples.append(sample)
        errors.extend(sample_errors)

    if len(samples) == warm_samples + 1:
        first, warm = samples[0], samples[1:]
        if mode == MODE_STRICT:
            if first["cache_outcome"] != "miss_builder" or first["cache_builder"] != "1":
                errors.append("strict_cold_miss_builder_not_observed")
            if not first["server_timing_builder_present"]:
                errors.append("strict_cold_builder_timing_not_observed")
        elif first["cache_outcome"] not in SAFE_CACHE_OUTCOMES:
            errors.append("warm_observation_first_outcome_invalid")
        for sample in warm:
            if sample["cache_outcome"] != "hit" or sample["cache_builder"] != "0":
                errors.append(f"sample_{sample['index']}:warm_hit_not_observed")
            if sample["server_timing_builder_present"]:
                errors.append(f"sample_{sample['index']}:warm_builder_timing_present")
        digests = {sample["response_sha256"] for sample in samples}
        if len(digests) != 1:
            errors.append("response_changed_between_cold_and_warm_reads")
        warm_latency = _latency_summary([float(sample["latency_ms"]) for sample in warm])
        if warm_latency["p95_ms"] > warm_p95_max_ms:
            errors.append("warm_p95_budget_exceeded")
        cold_ms = float(first["latency_ms"])
        if mode == MODE_STRICT and cold_ms > cold_max_ms:
            errors.append("cold_budget_exceeded")
        speedup = cold_ms / max(float(warm_latency["p95_ms"]), 0.001)
        if mode == MODE_STRICT and speedup < min_speedup:
            errors.append("cold_to_warm_speedup_below_minimum")
    else:
        first = samples[0] if samples else None
        warm = samples[1:] if len(samples) > 1 else []
        warm_latency = _latency_summary(
            [float(sample["latency_ms"]) for sample in warm]
        )
        speedup = 0.0

    return {
        "id": spec.endpoint_id,
        "surface": spec.surface,
        "path": spec.path,
        "passed": not errors,
        "claim": (
            "strict_local_cold_warm"
            if mode == MODE_STRICT
            else "non_destructive_warm_observation"
        ),
        "cold": (
            {
                "observed": bool(first and first["cache_outcome"] == "miss_builder"),
                "latency_ms": first["latency_ms"] if first else None,
                "max_ms": cold_max_ms,
            }
            if mode == MODE_STRICT
            else {"observed": False, "reason": "not_claimed_in_warm_observe_mode"}
        ),
        "warm": {
            "samples": len(warm),
            "latency": warm_latency,
            "p95_max_ms": warm_p95_max_ms,
        },
        "cold_to_warm_p95_speedup": (
            round(speedup, 3) if mode == MODE_STRICT else None
        ),
        "errors": sorted(set(errors)),
        "samples": samples,
    }


def run_audit(
    *,
    base_url: str,
    endpoint_ids: list[str],
    mode: str,
    warm_samples: int,
    cold_max_ms: float,
    warm_p95_max_ms: float,
    min_speedup: float,
    requester: Requester,
    strict_runtime_confirmed: bool = False,
) -> dict[str, Any]:
    if mode not in {MODE_WARM, MODE_STRICT}:
        raise ValueError("invalid_mode")
    _safe_base_url(base_url, mode=mode)
    if mode == MODE_STRICT and not strict_runtime_confirmed:
        raise ValueError("strict_runtime_preconditions_not_confirmed")
    if not 1 <= warm_samples <= 100:
        raise ValueError("warm_samples_must_be_within_1_and_100")
    if min(cold_max_ms, warm_p95_max_ms, min_speedup) <= 0:
        raise ValueError("performance_budgets_must_be_positive")
    if not endpoint_ids:
        raise ValueError("at_least_one_endpoint_required")
    unknown = [item for item in endpoint_ids if item not in ENDPOINTS]
    if unknown:
        raise ValueError("unknown_endpoint:" + ",".join(sorted(set(unknown))))
    if len(endpoint_ids) != len(set(endpoint_ids)):
        raise ValueError("duplicate_endpoint_not_allowed")
    endpoints = [
        audit_endpoint(
            ENDPOINTS[endpoint_id],
            requester=requester,
            mode=mode,
            warm_samples=warm_samples,
            cold_max_ms=cold_max_ms,
            warm_p95_max_ms=warm_p95_max_ms,
            min_speedup=min_speedup,
        )
        for endpoint_id in endpoint_ids
    ]
    return {
        "schema_version": SCHEMA_VERSION,
        "generated_at": _utcnow(),
        "mode": mode,
        "passed": all(item["passed"] for item in endpoints),
        "safety": {
            "http_methods": ["GET"],
            "reviewed_endpoint_allowlist": True,
            "business_data_writes_requested": 0,
            "provider_calls_requested_by_auditor": 0,
            "cache_clear_requested": False,
            "cache_delete_requested": False,
            "response_bodies_emitted": False,
            "authorization_emitted": False,
            "application_provider_calls_instrumented": False,
            "strict_runtime_preconditions_operator_confirmed": (
                strict_runtime_confirmed if mode == MODE_STRICT else None
            ),
        },
        "budgets": {
            "cold_max_ms": cold_max_ms,
            "warm_p95_max_ms": warm_p95_max_ms,
            "min_cold_to_warm_p95_speedup": min_speedup,
            "warm_samples_per_endpoint": warm_samples,
        },
        "endpoints": endpoints,
    }


def _write_json(path: Path, report: dict[str, Any]) -> None:
    if path.is_symlink() or (path.exists() and not path.is_file()):
        raise ValueError("json_output_path_is_unsafe")
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp-{os.getpid()}")
    temporary.write_text(
        json.dumps(report, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Audit reviewed GTM/Dashboard read latency without clearing caches."
    )
    parser.add_argument("--base-url", default="http://127.0.0.1:8001")
    parser.add_argument(
        "--mode",
        choices=(MODE_WARM, MODE_STRICT),
        default=MODE_WARM,
    )
    parser.add_argument(
        "--endpoint",
        action="append",
        choices=tuple(sorted(ENDPOINTS)),
        dest="endpoints",
        help="Repeat to select surfaces; defaults to both reviewed endpoints.",
    )
    parser.add_argument("--warm-samples", type=int, default=5)
    parser.add_argument("--cold-max-ms", type=float, default=2000.0)
    parser.add_argument("--warm-p95-max-ms", type=float, default=500.0)
    parser.add_argument("--min-speedup", type=float, default=1.0)
    parser.add_argument("--timeout", type=float, default=15.0)
    parser.add_argument("--token-file", type=Path)
    parser.add_argument("--allow-unauthenticated-loopback", action="store_true")
    parser.add_argument(
        "--confirm-isolated-readonly-runtime",
        action="store_true",
        help=(
            "Required for strict mode: attest that the loopback candidate is "
            "disposable, DB-read-only, provider-disabled, and uses an isolated "
            "initially empty application cache."
        ),
    )
    parser.add_argument("--json-out", type=Path)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    requester: HttpRequester | None = None
    try:
        _scheme, host, _port = _safe_base_url(args.base_url, mode=args.mode)
        if args.mode == MODE_STRICT and not args.confirm_isolated_readonly_runtime:
            raise ValueError("strict_runtime_preconditions_not_confirmed")
        if not 0.1 <= args.timeout <= 120:
            raise ValueError("timeout_must_be_within_0.1_and_120_seconds")
        token = _load_token(
            token_file=args.token_file,
            allow_unauthenticated=args.allow_unauthenticated_loopback,
            host=host,
        )
        requester = HttpRequester(
            base_url=args.base_url,
            token=token,
            timeout_seconds=args.timeout,
        )
        report = run_audit(
            base_url=args.base_url,
            endpoint_ids=args.endpoints or list(ENDPOINTS),
            mode=args.mode,
            warm_samples=args.warm_samples,
            cold_max_ms=args.cold_max_ms,
            warm_p95_max_ms=args.warm_p95_max_ms,
            min_speedup=args.min_speedup,
            requester=requester,
            strict_runtime_confirmed=args.confirm_isolated_readonly_runtime,
        )
        if args.json_out:
            _write_json(args.json_out, report)
        sys.stdout.write(json.dumps(report, ensure_ascii=False, sort_keys=True, indent=2) + "\n")
        return 0 if report["passed"] else 1
    except (OSError, ValueError) as exc:
        sys.stderr.write(
            json.dumps(
                {
                    "schema_version": SCHEMA_VERSION,
                    "passed": False,
                    "error": str(exc)[:160],
                },
                sort_keys=True,
            )
            + "\n"
        )
        return 2
    finally:
        if requester is not None:
            requester.close()


if __name__ == "__main__":
    raise SystemExit(main())
