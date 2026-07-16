#!/usr/bin/env python3
"""Run a hermetic HTTP user-load calibration against an owned fixture only.

This harness intentionally does not accept a target URL.  Every trial creates a
fresh in-process ASGI application and drives it through HTTPX's memory-only
ASGI transport.  No listener, DNS lookup, socket connect, or external service
is possible.  The known V-KPI local ports remain explicit deny-list evidence.

The fixture has deterministic synthetic shell, read, and aggregate endpoints
with deliberately constrained in-process resource pools.  Results calibrate
the HTTP load generator and its saturation math; they are not evidence of
V-KPI, database, Redis, Worker, provider, browser, cloud, or real-user capacity.
"""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import math
import platform
import time
from collections import Counter, defaultdict
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

import httpx

import sys as _stdout_sys

_STDOUT_UTILS_DIR = Path(__file__).resolve().parents[1]
if str(_STDOUT_UTILS_DIR) not in _stdout_sys.path:
    _stdout_sys.path.insert(1, str(_STDOUT_UTILS_DIR))
from stdout_utils import out as stdout_out  # noqa: E402

_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in _stdout_sys.path:
    _stdout_sys.path.insert(1, str(_REPO_ROOT))
from scripts.ops.load_test_isolated_http_reporting import (  # noqa: E402
    aggregate_tier,
    analyze_saturation,
    build_two_slot_fixture_evidence,
    load_historical_chain_context,
    render_markdown,
    write_exclusive,
)
from scripts.ops import load_test_isolated_http_reporting as http_reporting  # noqa: E402
from scripts.ops.load_test_isolated_http_app import (  # noqa: E402
    KNOWN_PATHS,
    FixtureState,
    InProcessFixtureASGI,
)
from scripts.ops import load_test_isolated_http_app as http_app  # noqa: E402


SCHEMA_VERSION = "vkpi-isolated-http-user-load/v2"
EVIDENCE_CLASS = "isolated_in_process_asgi_http_fixture"
DEFAULT_TIERS = (1, 2, 4, 8, 16, 32)
MAX_TIER_VU = 32
MAX_TRIALS = 5
MAX_DURATION_SECONDS = 5.0
MAX_TOTAL_VU_SECONDS = 600.0
PROTECTED_LOCAL_PORTS = frozenset({5173, 8102, 54329, 6379})
KNOWN_PATHS = ("/fixture/shell", "/fixture/read", "/fixture/aggregate")
IN_PROCESS_BASE_URL = "http://vkpi-isolated-fixture.invalid"


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


def sha256_json(value: Any) -> str:
    return hashlib.sha256(canonical_json(value)).hexdigest()


def percentile(values: Sequence[float], pct: float) -> float | None:
    """Return a linearly interpolated percentile, or None for no population."""
    if not values:
        return None
    ordered = sorted(float(value) for value in values)
    if len(ordered) == 1:
        return ordered[0]
    position = (len(ordered) - 1) * pct / 100.0
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return ordered[lower]
    weight = position - lower
    return ordered[lower] * (1.0 - weight) + ordered[upper] * weight


def rounded_percentiles(values: Sequence[float]) -> dict[str, float | None]:
    return {
        "p50": _round_optional(percentile(values, 50)),
        "p95": _round_optional(percentile(values, 95)),
        "p99": _round_optional(percentile(values, 99)),
        "max": _round_optional(max(values) if values else None),
    }


def _round_optional(value: float | None, digits: int = 3) -> float | None:
    return round(value, digits) if value is not None else None


def rate_metric(numerator: int, denominator: int) -> dict[str, int | float | None]:
    return {
        "numerator": numerator,
        "denominator": denominator,
        "value": round(numerator / denominator, 8) if denominator else None,
    }


def throughput_metric(
    numerator: int, denominator_seconds: float
) -> dict[str, int | float | None]:
    return {
        "numerator_requests": numerator,
        "denominator_seconds": round(denominator_seconds, 6),
        "requests_per_second": (
            round(numerator / denominator_seconds, 3)
            if denominator_seconds > 0
            else None
        ),
    }


@dataclass(frozen=True)
class HttpFixtureConfig:
    tiers: tuple[int, ...] = DEFAULT_TIERS
    trials: int = 3
    duration_seconds: float = 1.0
    seed: int = 20260714
    database_slots: int = 4
    aggregate_slots: int = 2
    shell_service_ms: float = 1.0
    read_service_ms: float = 12.0
    aggregate_service_ms: float = 36.0
    request_timeout_ms: float = 2_000.0
    contention_threshold_ms: float = 0.5

    def validated(self) -> "HttpFixtureConfig":
        if not self.tiers or tuple(sorted(set(self.tiers))) != self.tiers:
            raise ValueError("tiers must be non-empty, unique, and strictly increasing")
        if self.tiers[0] < 1 or self.tiers[-1] > MAX_TIER_VU:
            raise ValueError(f"tiers must stay within [1, {MAX_TIER_VU}]")
        if not 1 <= self.trials <= MAX_TRIALS:
            raise ValueError(f"trials must stay within [1, {MAX_TRIALS}]")
        if not 0.05 <= self.duration_seconds <= MAX_DURATION_SECONDS:
            raise ValueError(
                f"duration_seconds must stay within [0.05, {MAX_DURATION_SECONDS:g}]"
            )
        vu_seconds = self.trials * self.duration_seconds * sum(self.tiers)
        if vu_seconds > MAX_TOTAL_VU_SECONDS:
            raise ValueError("planned VU-seconds exceed the code-owned hard limit")
        if not 1 <= self.database_slots <= 32:
            raise ValueError("database_slots must stay within [1, 32]")
        if not 1 <= self.aggregate_slots <= 32:
            raise ValueError("aggregate_slots must stay within [1, 32]")
        for name, value in (
            ("shell_service_ms", self.shell_service_ms),
            ("read_service_ms", self.read_service_ms),
            ("aggregate_service_ms", self.aggregate_service_ms),
        ):
            if not 0.1 <= value <= 500.0:
                raise ValueError(f"{name} must stay within [0.1, 500]")
        if not 50.0 <= self.request_timeout_ms <= 10_000.0:
            raise ValueError("request_timeout_ms must stay within [50, 10000]")
        if not 0.01 <= self.contention_threshold_ms <= 10.0:
            raise ValueError("contention_threshold_ms must stay within [0.01, 10]")
        return self

    def public_dict(self) -> dict[str, Any]:
        return {
            **asdict(self),
            "tiers": list(self.tiers),
            "load_model": "closed_loop_one_asgi_http_task_per_synthetic_vu",
            "journey": list(KNOWN_PATHS),
            "human_users": None,
        }


@dataclass(frozen=True)
class ClientMeasurement:
    vu_id: int
    request_index: int
    endpoint: str
    offered_offset_ms: float
    completed_offset_ms: float
    latency_ms: float
    status: int
    outcome: str
    response_bytes: int

    def public_dict(self) -> dict[str, Any]:
        return {
            "vu_id": self.vu_id,
            "request_index": self.request_index,
            "endpoint": self.endpoint,
            "offered_offset_ms": round(self.offered_offset_ms, 3),
            "completed_offset_ms": round(self.completed_offset_ms, 3),
            "latency_ms": round(self.latency_ms, 3),
            "status": self.status,
            "outcome": self.outcome,
            "response_bytes": self.response_bytes,
        }


def synthetic_identity(seed: int, trial_index: int, tier: int, vu_id: int) -> str:
    return hashlib.sha256(
        f"vkpi-http-fixture:v1:{seed}:{trial_index}:{tier}:{vu_id}".encode(
            "utf-8"
        )
    ).hexdigest()


def endpoint_for_request(vu_id: int, request_index: int) -> str:
    return KNOWN_PATHS[(vu_id + request_index) % len(KNOWN_PATHS)]


async def run_vu(
    *,
    vu_id: int,
    tier: int,
    trial_index: int,
    config: HttpFixtureConfig,
    nonce: str,
    start: asyncio.Event,
    clock: Mapping[str, float],
    client: httpx.AsyncClient,
) -> list[ClientMeasurement]:
    identity = synthetic_identity(config.seed, trial_index, tier, vu_id)
    records: list[ClientMeasurement] = []
    request_index = 0
    await start.wait()
    started = clock["started"]
    deadline = clock["deadline"]
    while time.perf_counter() < deadline:
        endpoint = endpoint_for_request(vu_id, request_index)
        offered = time.perf_counter()
        status = 0
        outcome = "client_error"
        response_bytes = 0
        try:
            response = await asyncio.wait_for(
                client.get(
                    endpoint,
                    params={"nonce": nonce},
                    headers={
                        "Accept": "application/json",
                        "X-VKPI-Fixture-Identity": identity,
                        "X-VKPI-Fixture-VU": str(vu_id),
                        "X-VKPI-Fixture-Request-Index": str(request_index),
                    },
                ),
                timeout=config.request_timeout_ms / 1_000.0,
            )
            status = response.status_code
            response_bytes = len(response.content)
            outcome = "success" if 200 <= status < 300 else "http_error"
        except asyncio.TimeoutError:
            outcome = "timeout"
        except (httpx.HTTPError, ValueError):
            outcome = "client_error"
        completed = time.perf_counter()
        records.append(
            ClientMeasurement(
                vu_id=vu_id,
                request_index=request_index,
                endpoint=endpoint,
                offered_offset_ms=(offered - started) * 1_000.0,
                completed_offset_ms=(completed - started) * 1_000.0,
                latency_ms=(completed - offered) * 1_000.0,
                status=status,
                outcome=outcome,
                response_bytes=response_bytes,
            )
        )
        request_index += 1
    return records


def summarize_measurements(
    records: Sequence[ClientMeasurement],
    *,
    offer_window_seconds: float,
    completion_window_seconds: float,
) -> dict[str, Any]:
    outcomes = Counter(record.outcome for record in records)
    statuses = Counter(str(record.status) for record in records)
    offered = len(records)
    completed = sum(outcomes.values())
    successes = outcomes["success"]
    errors = completed - successes
    timeouts = outcomes["timeout"]
    latencies = [record.latency_ms for record in records]
    success_latencies = [
        record.latency_ms for record in records if record.outcome == "success"
    ]
    return {
        "offered_requests": offered,
        "completed_outcomes": completed,
        "successful_responses": successes,
        "error_outcomes": errors,
        "timeout_outcomes": timeouts,
        "completion_rate": rate_metric(completed, offered),
        "success_rate": rate_metric(successes, offered),
        "error_rate": rate_metric(errors, offered),
        "timeout_rate": rate_metric(timeouts, offered),
        "offered_throughput": throughput_metric(offered, offer_window_seconds),
        "completed_throughput": throughput_metric(
            completed, completion_window_seconds
        ),
        "successful_throughput": throughput_metric(
            successes, completion_window_seconds
        ),
        "status_codes": dict(sorted(statuses.items())),
        "outcomes": dict(sorted(outcomes.items())),
        "latency_ms_all_outcomes": {
            "sample_count": len(latencies),
            **rounded_percentiles(latencies),
        },
        "latency_ms_success": {
            "sample_count": len(success_latencies),
            **rounded_percentiles(success_latencies),
        },
        "response_bytes": sum(record.response_bytes for record in records),
    }


def summarize_by_endpoint(
    records: Sequence[ClientMeasurement],
) -> dict[str, Any]:
    grouped: dict[str, list[ClientMeasurement]] = defaultdict(list)
    for record in records:
        grouped[record.endpoint].append(record)
    result: dict[str, Any] = {}
    for endpoint in KNOWN_PATHS:
        entries = grouped.get(endpoint, [])
        outcomes = Counter(item.outcome for item in entries)
        errors = len(entries) - outcomes["success"]
        latencies = [item.latency_ms for item in entries]
        result[endpoint] = {
            "offered_requests": len(entries),
            "successful_responses": outcomes["success"],
            "error_rate": rate_metric(errors, len(entries)),
            "timeout_rate": rate_metric(outcomes["timeout"], len(entries)),
            "latency_ms_all_outcomes": {
                "sample_count": len(latencies),
                **rounded_percentiles(latencies),
            },
        }
    return result


def identify_bottleneck(server: Mapping[str, Any]) -> dict[str, Any]:
    resources = server["resources"]
    candidates = {
        name: float(resource["wait_ms"]["p95"] or 0.0)
        for name, resource in resources.items()
    }
    candidate, score = max(candidates.items(), key=lambda item: item[1])
    if score < 0.5:
        candidate = "no_material_fixture_queue"
    return {
        "candidate": candidate,
        "basis": "largest measured fixture resource p95 wait",
        "p95_wait_ms_by_resource": {
            name: round(value, 3) for name, value in candidates.items()
        },
        "production_bottleneck_claim_allowed": False,
    }


async def sample_event_loop_lag(
    *, stop: asyncio.Event, samples_ms: list[float], interval_seconds: float = 0.01
) -> None:
    loop = asyncio.get_running_loop()
    expected = loop.time() + interval_seconds
    while not stop.is_set():
        await asyncio.sleep(max(0.0, expected - loop.time()))
        observed = loop.time()
        samples_ms.append(max(0.0, (observed - expected) * 1_000.0))
        expected += interval_seconds


async def run_trial(
    config: HttpFixtureConfig, *, tier: int, trial_index: int
) -> dict[str, Any]:
    trial_seed = config.seed + trial_index * 10_000 + tier
    nonce = hashlib.sha256(
        f"vkpi-fixture-nonce:{trial_seed}".encode("utf-8")
    ).hexdigest()[:32]
    state = FixtureState(config, nonce=nonce, trial_seed=trial_seed)
    app = InProcessFixtureASGI(state)
    transport = httpx.ASGITransport(app=app)
    start = asyncio.Event()
    clock: dict[str, float] = {}
    loop_lag_ms: list[float] = []
    stop_lag = asyncio.Event()
    cpu_started = time.process_time()
    async with httpx.AsyncClient(
        transport=transport,
        base_url=IN_PROCESS_BASE_URL,
        follow_redirects=False,
    ) as client:
        lag_task = asyncio.create_task(
            sample_event_loop_lag(stop=stop_lag, samples_ms=loop_lag_ms)
        )
        workers = [
            asyncio.create_task(
                run_vu(
                        vu_id=vu_id,
                        tier=tier,
                        trial_index=trial_index,
                        config=config,
                        nonce=nonce,
                        start=start,
                        clock=clock,
                        client=client,
                )
            )
            for vu_id in range(tier)
        ]
        started = time.perf_counter()
        clock["started"] = started
        clock["deadline"] = started + config.duration_seconds
        start.set()
        per_vu = await asyncio.gather(*workers)
        completed = time.perf_counter()
        stop_lag.set()
        await lag_task
    cpu_seconds = time.process_time() - cpu_started

    records = [record for group in per_vu for record in group]
    records.sort(key=lambda item: (item.offered_offset_ms, item.vu_id))
    server_snapshot = state.snapshot()
    summary = summarize_measurements(
        records,
        offer_window_seconds=config.duration_seconds,
        completion_window_seconds=completed - started,
    )
    identity_values = {
        synthetic_identity(config.seed, trial_index, tier, vu_id)
        for vu_id in range(tier)
    }
    public_records = [record.public_dict() for record in records]
    return {
        "tier_vu": tier,
        "trial_index": trial_index,
        "duration_target_seconds": config.duration_seconds,
        "completion_window_seconds": round(completed - started, 6),
        "summary": summary,
        "by_endpoint": summarize_by_endpoint(records),
        "server": server_snapshot,
        "bottleneck": identify_bottleneck(server_snapshot),
        "load_generator": {
            "process_cpu_seconds": round(cpu_seconds, 6),
            "process_cpu_cores_equivalent": round(
                cpu_seconds / max(completed - started, 0.000001), 4
            ),
            "configured_async_vu_tasks": tier,
            "event_loop_lag_ms": {
                "sample_count": len(loop_lag_ms),
                **rounded_percentiles(loop_lag_ms),
            },
            "socket_connections": 0,
        },
        "identities": {
            "synthetic_vu": tier,
            "unique_synthetic_identities": len(identity_values),
            "collision_count": tier - len(identity_values),
            "real_accounts_used": 0,
            "identity_values_persisted": False,
            "identity_set_sha256": hashlib.sha256(
                "\n".join(sorted(identity_values)).encode("ascii")
            ).hexdigest(),
        },
        "transport_safety": {
            "transport": "httpx_asgi_in_process_memory_only",
            "listener_created": False,
            "dns_lookups": 0,
            "socket_connections": 0,
            "protected_ports_contacted": 0,
            "non_loopback_connections": 0,
            "external_network_connections": 0,
        },
        "measurements": public_records,
        "measurements_sha256": sha256_json(public_records),
    }


async def build_report_async(
    config: HttpFixtureConfig,
    *,
    historical_chain_evidence: Path | None = None,
) -> dict[str, Any]:
    config = config.validated()
    started_at = utc_now()
    began = time.perf_counter()
    by_tier: dict[int, list[dict[str, Any]]] = {
        tier: [] for tier in config.tiers
    }
    # Trial-major ordering exposes time drift instead of hiding it inside tiers.
    for trial_index in range(config.trials):
        for tier in config.tiers:
            by_tier[tier].append(
                await run_trial(config, tier=tier, trial_index=trial_index)
            )
    tier_results = [
        aggregate_tier(tier, by_tier[tier]) for tier in config.tiers
    ]
    historical_context = (
        load_historical_chain_context(historical_chain_evidence)
        if historical_chain_evidence is not None
        else None
    )
    configuration = config.public_dict()
    script_path = Path(__file__).resolve()
    reporting_path = Path(http_reporting.__file__).resolve()
    app_path = Path(http_app.__file__).resolve()
    report = {
        "schema_version": SCHEMA_VERSION,
        "evidence_class": EVIDENCE_CLASS,
        "status": "completed_isolated_http_fixture",
        "started_at": started_at,
        "completed_at": utc_now(),
        "elapsed_seconds": round(time.perf_counter() - began, 6),
        "run_id": hashlib.sha256(
            f"{started_at}:{sha256_json(configuration)}".encode("utf-8")
        ).hexdigest()[:24],
        "configuration": configuration,
        "configuration_sha256": sha256_json(configuration),
        "fixture_model": {
            "endpoints": list(KNOWN_PATHS),
            "timings_are_synthetic": True,
            "resource_slots_are_synthetic": True,
            "http_stack_exercised": True,
            "http_transport": "httpx.ASGITransport",
            "v_kpi_application_code_exercised": False,
        },
        "safety": {
            "target_argument_supported": False,
            "listener": None,
            "transport": "per-trial in-process ASGI application",
            "network_stack_used": False,
            "socket_connections": 0,
            "dns_lookups": 0,
            "protected_local_ports": sorted(PROTECTED_LOCAL_PORTS),
            "protected_ports_contacted": 0,
            "non_loopback_connections": 0,
            "external_network_connections": 0,
            "database_connections": 0,
            "redis_connections": 0,
            "worker_jobs": 0,
            "provider_calls": 0,
            "browser_sessions": 0,
            "business_rows_read": 0,
            "business_rows_written": 0,
            "local_historical_evidence_files_read": (
                1 if historical_context is not None else 0
            ),
        },
        "metric_definitions": {
            "offered_request": "client began one fixture HTTP attempt before tier deadline",
            "completed_outcome": "client observed success, HTTP error, timeout, or client error",
            "offered_throughput": "offered requests divided by sum of configured offer windows",
            "completed_throughput": "completed outcomes divided by sum of measured completion windows",
            "error_rate": "non-success completed outcomes divided by offered requests",
            "timeout_rate": "timeout outcomes divided by offered requests",
            "latency": "client wall time from offer to completed outcome",
            "vu": "one closed-loop synthetic async task, not a person or seat",
        },
        "tier_results": tier_results,
        "analysis": analyze_saturation(tier_results),
        "two_slot_fixture_mechanics": build_two_slot_fixture_evidence(
            tier_results, config
        ),
        "historical_20_survivor_chain": historical_context,
        "claims": {
            "http_harness_calibration_evidence": True,
            "fixture_capacity_evidence": True,
            "production_performance_evidence": False,
            "application_slo_evidence": False,
            "real_user_capacity_evidence": False,
            "cloud_capacity_evidence": False,
            "human_user_conversion_allowed": False,
            "maximum_real_users": None,
        },
        "limitations": [
            "all endpoint timings and resource limits are synthetic by construction",
            "V-KPI frontend/backend, PostgreSQL, Redis, Worker, providers, and business data are not exercised",
            "client and fixture share one local Python event loop, so scheduler and interpreter noise affect measurements",
            "the closed-loop workload does not model open-loop arrival bursts or think time",
            "fixture VU cannot be converted to concurrent production users",
            "a real staging capacity claim still requires an isolated deployment, production-like data shape, telemetry, and authorization",
        ],
        "generator": {
            "python": platform.python_version(),
            "platform": platform.platform(),
            "script_name": script_path.name,
            "script_sha256": hashlib.sha256(script_path.read_bytes()).hexdigest(),
            "reporting_helper_name": reporting_path.name,
            "reporting_helper_sha256": hashlib.sha256(
                reporting_path.read_bytes()
            ).hexdigest(),
            "app_helper_name": app_path.name,
            "app_helper_sha256": hashlib.sha256(app_path.read_bytes()).hexdigest(),
        },
    }
    report["report_calculation_sha256"] = sha256_json(
        {
            "configuration": report["configuration"],
            "tier_calculations": [
                item["calculation_sha256"] for item in tier_results
            ],
            "analysis": report["analysis"],
            "two_slot_fixture_mechanics": report["two_slot_fixture_mechanics"],
            "historical_20_survivor_chain": report[
                "historical_20_survivor_chain"
            ],
        }
    )
    return report


def build_report(
    config: HttpFixtureConfig,
    *,
    historical_chain_evidence: Path | None = None,
) -> dict[str, Any]:
    """Synchronous CLI/test entrypoint around the in-process async harness."""
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(
            build_report_async(
                config,
                historical_chain_evidence=historical_chain_evidence,
            )
        )
    raise RuntimeError("build_report cannot run inside an active event loop; await build_report_async")


def parse_tiers(raw: str) -> tuple[int, ...]:
    try:
        tiers = tuple(int(item.strip()) for item in raw.split(",") if item.strip())
    except ValueError as exc:
        raise argparse.ArgumentTypeError("tiers must be comma-separated integers") from exc
    try:
        HttpFixtureConfig(tiers=tiers).validated()
    except ValueError as exc:
        raise argparse.ArgumentTypeError(str(exc)) from exc
    return tiers


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tiers", type=parse_tiers, default=DEFAULT_TIERS)
    parser.add_argument("--trials", type=int, default=3)
    parser.add_argument("--duration-seconds", type=float, default=1.0)
    parser.add_argument("--seed", type=int, default=20260714)
    parser.add_argument("--database-slots", type=int, default=4)
    parser.add_argument("--aggregate-slots", type=int, default=2)
    parser.add_argument("--request-timeout-ms", type=float, default=2_000.0)
    parser.add_argument(
        "--historical-chain-evidence",
        type=Path,
        help="optional local Round 12 evidence to present separately from fixture metrics",
    )
    parser.add_argument("--json-output", type=Path)
    parser.add_argument("--markdown-output", type=Path)
    return parser


def main(argv: Iterable[str] | None = None) -> int:
    args = build_parser().parse_args(list(argv) if argv is not None else None)
    config = HttpFixtureConfig(
        tiers=tuple(args.tiers),
        trials=args.trials,
        duration_seconds=args.duration_seconds,
        seed=args.seed,
        database_slots=args.database_slots,
        aggregate_slots=args.aggregate_slots,
        request_timeout_ms=args.request_timeout_ms,
    ).validated()
    report = build_report(
        config,
        historical_chain_evidence=args.historical_chain_evidence,
    )
    encoded_json = json.dumps(report, ensure_ascii=False, indent=2).encode("utf-8") + b"\n"
    markdown = render_markdown(report).encode("utf-8")
    if args.json_output:
        write_exclusive(args.json_output, encoded_json)
    if args.markdown_output:
        write_exclusive(args.markdown_output, markdown)
    if not args.json_output and not args.markdown_output:
        stdout_out(encoded_json.decode("utf-8"), end="")
    else:
        stdout_out(
            json.dumps(
                {
                    "status": report["status"],
                    "run_id": report["run_id"],
                    "json_output": str(args.json_output) if args.json_output else None,
                    "markdown_output": (
                        str(args.markdown_output) if args.markdown_output else None
                    ),
                },
                ensure_ascii=False,
            )
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
