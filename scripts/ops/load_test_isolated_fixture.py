#!/usr/bin/env python3
"""Hermetic capacity-harness calibration with no sockets or external services.

This target exists to validate the load generator, percentile aggregation,
repeat handling, identity isolation, resource-saturation telemetry, and
tamper-evident artifact flow.  It calls an in-process coroutine directly.  It
does not import an HTTP client, open a listener, connect to PostgreSQL/Redis,
touch a Worker, or read business data.

The service timings and slot counts below are an explicit synthetic model.
Consequently, every artifact is labelled ``isolated_fixture`` and is forbidden
from supporting claims about production capacity, real users, cloud capacity,
or application SLOs.
"""
from __future__ import annotations
import sys as _stdout_sys
from pathlib import Path as _StdoutPath

_STDOUT_UTILS_DIR = _StdoutPath(__file__).resolve().parents[1]
if str(_STDOUT_UTILS_DIR) not in _stdout_sys.path:
    _stdout_sys.path.insert(1, str(_STDOUT_UTILS_DIR))
from stdout_utils import out as stdout_out  # noqa: E402

import argparse
import asyncio
import base64
import hashlib
import hmac
import json
import os
import platform
import stat
import time
from collections import Counter, defaultdict
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from statistics import median
from typing import Any, Iterable, Mapping, Sequence

SCHEMA_VERSION = "vkpi-isolated-capacity-fixture/v1"
ATTESTATION_SCHEMA = "vkpi-isolated-fixture-self-attestation/v1"
EVIDENCE_CLASS = "isolated_fixture"
DEFAULT_TIERS = (1, 5, 10, 20, 40, 80)
MAX_TIER_VU = 80
MAX_TRIALS = 5
MAX_DURATION_SECONDS = 10.0
MAX_TOTAL_WALL_SECONDS = 120.0
MAX_TOTAL_VU_SECONDS = 5_000.0
SIGNING_DOMAIN = "vkpi-isolated-fixture-hmac-sha256-v1"


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


def percentile(values: Sequence[float], pct: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(float(value) for value in values)
    if len(ordered) == 1:
        return ordered[0]
    position = (len(ordered) - 1) * (pct / 100.0)
    lower = int(position)
    upper = min(lower + 1, len(ordered) - 1)
    fraction = position - lower
    return ordered[lower] * (1.0 - fraction) + ordered[upper] * fraction


def metric_triplet(values: Sequence[float], *, digits: int = 3) -> dict[str, float]:
    if not values:
        return {"min": 0.0, "median": 0.0, "max": 0.0}
    return {
        "min": round(min(values), digits),
        "median": round(median(values), digits),
        "max": round(max(values), digits),
    }


def stable_unit(seed: int, *parts: object) -> float:
    digest = hashlib.sha256(
        ":".join([str(seed), *(str(part) for part in parts)]).encode("utf-8")
    ).digest()
    return int.from_bytes(digest[:8], "big") / float(2**64 - 1)


@dataclass(frozen=True)
class EndpointModel:
    name: str
    category: str
    non_resource_ms: float
    database_ms: float
    aggregate_compute_ms: float
    response_bytes: int

    def public_dict(self) -> dict[str, Any]:
        return asdict(self)


ENDPOINTS: tuple[EndpointModel, ...] = (
    EndpointModel("frontend_shell", "static", 2.5, 0.0, 0.0, 24_000),
    EndpointModel("backend_health", "health", 1.0, 0.0, 0.0, 320),
    EndpointModel("events_list", "light_read", 0.5, 10.0, 0.0, 12_000),
    EndpointModel("dealers_list", "light_read", 0.5, 12.0, 0.0, 14_000),
    EndpointModel("event_radar_summary", "light_read", 0.5, 14.0, 0.0, 18_000),
    EndpointModel("industry_benchmark", "aggregate", 0.5, 8.0, 36.0, 28_000),
    EndpointModel("category_tracks", "aggregate", 0.5, 8.0, 42.0, 26_000),
)


@dataclass(frozen=True)
class IsolatedFixtureConfig:
    tiers: tuple[int, ...] = DEFAULT_TIERS
    trials: int = 3
    duration_seconds: float = 1.2
    seed: int = 20260713
    database_slots: int = 16
    aggregate_compute_slots: int = 8
    request_timeout_ms: float = 1_500.0
    event_loop_sample_ms: float = 10.0

    def validated(self) -> "IsolatedFixtureConfig":
        if not self.tiers or tuple(sorted(set(self.tiers))) != self.tiers:
            raise ValueError("tiers must be non-empty, unique, and strictly increasing")
        if self.tiers[0] < 1 or self.tiers[-1] > MAX_TIER_VU:
            raise ValueError(f"tiers must stay within [1, {MAX_TIER_VU}] VU")
        if not 1 <= self.trials <= MAX_TRIALS:
            raise ValueError(f"trials must stay within [1, {MAX_TRIALS}]")
        if not 0.05 <= self.duration_seconds <= MAX_DURATION_SECONDS:
            raise ValueError(
                f"duration_seconds must stay within [0.05, {MAX_DURATION_SECONDS:g}]"
            )
        if self.trials * len(self.tiers) * self.duration_seconds > MAX_TOTAL_WALL_SECONDS:
            raise ValueError("planned fixture wall time exceeds the code-owned hard limit")
        vu_seconds = self.trials * self.duration_seconds * sum(self.tiers)
        if vu_seconds > MAX_TOTAL_VU_SECONDS:
            raise ValueError("planned fixture VU-seconds exceed the code-owned hard limit")
        if not 1 <= self.database_slots <= 128:
            raise ValueError("database_slots must stay within [1, 128]")
        if not 1 <= self.aggregate_compute_slots <= 128:
            raise ValueError("aggregate_compute_slots must stay within [1, 128]")
        if not 50.0 <= self.request_timeout_ms <= 10_000.0:
            raise ValueError("request_timeout_ms must stay within [50, 10000]")
        if not 1.0 <= self.event_loop_sample_ms <= 100.0:
            raise ValueError("event_loop_sample_ms must stay within [1, 100]")
        return self

    def public_dict(self) -> dict[str, Any]:
        return {
            **asdict(self),
            "tiers": list(self.tiers),
            "load_model": "closed_loop_one_direct_coroutine_per_synthetic_vu",
            "human_users": None,
            "endpoint_sequence": [endpoint.name for endpoint in ENDPOINTS],
        }


@dataclass(frozen=True)
class RequestMetric:
    endpoint: str
    category: str
    status: int
    ok: bool
    latency_ms: float
    database_wait_ms: float
    aggregate_compute_wait_ms: float
    response_bytes: int
    error_type: str = ""


@dataclass(frozen=True)
class FrozenSignedArtifact:
    report: Mapping[str, Any]
    encoded: bytes
    signed_payload_sha256: str
    integrity_tag_base64: str


class SlotPool:
    """A deterministic-size in-process resource pool with wait telemetry."""

    def __init__(self, name: str, slots: int):
        self.name = name
        self.slots = slots
        self._semaphore = asyncio.Semaphore(slots)
        self.active = 0
        self.max_active = 0
        self.acquisitions = 0
        self.wait_samples_ms: list[float] = []

    async def hold(self, duration_ms: float) -> float:
        queued_at = time.perf_counter_ns()
        await self._semaphore.acquire()
        acquired_at = time.perf_counter_ns()
        wait_ms = (acquired_at - queued_at) / 1_000_000.0
        self.wait_samples_ms.append(wait_ms)
        self.acquisitions += 1
        self.active += 1
        self.max_active = max(self.max_active, self.active)
        try:
            await asyncio.sleep(duration_ms / 1_000.0)
        finally:
            self.active -= 1
            self._semaphore.release()
        return wait_ms

    def public_dict(self) -> dict[str, Any]:
        waits = self.wait_samples_ms
        return {
            "resource": self.name,
            "slots": self.slots,
            "acquisitions": self.acquisitions,
            "max_active": self.max_active,
            "max_slot_utilization": round(self.max_active / self.slots, 4),
            "wait_ms": {
                "p50": round(percentile(waits, 50), 3),
                "p95": round(percentile(waits, 95), 3),
                "p99": round(percentile(waits, 99), 3),
                "max": round(max(waits), 3) if waits else 0.0,
            },
        }


class IsolatedFixtureService:
    """Synthetic direct-call service model; it owns no transport or state store."""

    def __init__(self, config: IsolatedFixtureConfig, trial_seed: int):
        self.config = config
        self.trial_seed = trial_seed
        self.database = SlotPool("synthetic_database_slots", config.database_slots)
        self.aggregate_compute = SlotPool(
            "synthetic_aggregate_compute_slots", config.aggregate_compute_slots
        )

    async def handle(
        self,
        endpoint: EndpointModel,
        *,
        identity_digest: str,
        request_index: int,
    ) -> RequestMetric:
        if len(identity_digest) != 64:
            raise ValueError("a full synthetic identity digest is required")
        started = time.perf_counter_ns()
        jitter = 0.9 + 0.2 * stable_unit(
            self.trial_seed, identity_digest, request_index, endpoint.name
        )
        database_wait_ms = 0.0
        aggregate_wait_ms = 0.0
        if endpoint.non_resource_ms:
            await asyncio.sleep(endpoint.non_resource_ms * jitter / 1_000.0)
        if endpoint.database_ms:
            database_wait_ms = await self.database.hold(endpoint.database_ms * jitter)
        if endpoint.aggregate_compute_ms:
            aggregate_wait_ms = await self.aggregate_compute.hold(
                endpoint.aggregate_compute_ms * jitter
            )
        latency_ms = (time.perf_counter_ns() - started) / 1_000_000.0
        return RequestMetric(
            endpoint=endpoint.name,
            category=endpoint.category,
            status=200,
            ok=True,
            latency_ms=latency_ms,
            database_wait_ms=database_wait_ms,
            aggregate_compute_wait_ms=aggregate_wait_ms,
            response_bytes=endpoint.response_bytes,
        )


def synthetic_identity_digest(seed: int, trial_index: int, tier: int, vu_id: int) -> str:
    return hashlib.sha256(
        f"vkpi-isolated-identity:v1:{seed}:{trial_index}:{tier}:{vu_id}".encode("utf-8")
    ).hexdigest()


def endpoint_for_request(
    *, identity_digest: str, request_index: int
) -> EndpointModel:
    offset = int(identity_digest[:8], 16) % len(ENDPOINTS)
    return ENDPOINTS[(offset + request_index) % len(ENDPOINTS)]


async def sample_event_loop_lag(
    *, interval_ms: float, stop: asyncio.Event, samples: list[float]
) -> None:
    loop = asyncio.get_running_loop()
    interval = interval_ms / 1_000.0
    expected = loop.time() + interval
    while not stop.is_set():
        await asyncio.sleep(max(0.0, expected - loop.time()))
        observed = loop.time()
        samples.append(max(0.0, (observed - expected) * 1_000.0))
        expected += interval


async def run_vu(
    *,
    vu_id: int,
    identity_digest: str,
    service: IsolatedFixtureService,
    start: asyncio.Event,
    deadline: float,
    timeout_ms: float,
    metrics: list[RequestMetric],
) -> None:
    await start.wait()
    request_index = 0
    while time.perf_counter() < deadline:
        endpoint = endpoint_for_request(
            identity_digest=identity_digest,
            request_index=request_index,
        )
        try:
            metric = await asyncio.wait_for(
                service.handle(
                    endpoint,
                    identity_digest=identity_digest,
                    request_index=request_index,
                ),
                timeout=timeout_ms / 1_000.0,
            )
        except asyncio.TimeoutError:
            metric = RequestMetric(
                endpoint=endpoint.name,
                category=endpoint.category,
                status=0,
                ok=False,
                latency_ms=timeout_ms,
                database_wait_ms=0.0,
                aggregate_compute_wait_ms=0.0,
                response_bytes=0,
                error_type="fixture_timeout",
            )
        metrics.append(metric)
        request_index += 1


def summarize_endpoint_metrics(metrics: Sequence[RequestMetric]) -> dict[str, Any]:
    groups: dict[str, list[RequestMetric]] = defaultdict(list)
    for metric in metrics:
        groups[metric.endpoint].append(metric)
    result: dict[str, Any] = {}
    for endpoint, entries in sorted(groups.items()):
        latencies = [entry.latency_ms for entry in entries]
        errors = sum(1 for entry in entries if not entry.ok)
        result[endpoint] = {
            "requests": len(entries),
            "error_rate": round(errors / len(entries), 6),
            "latency_ms": {
                "p50": round(percentile(latencies, 50), 3),
                "p95": round(percentile(latencies, 95), 3),
                "p99": round(percentile(latencies, 99), 3),
            },
        }
    return result


def identify_trial_bottleneck(
    database: Mapping[str, Any],
    aggregate_compute: Mapping[str, Any],
    event_loop_lag: Mapping[str, Any],
) -> dict[str, Any]:
    candidates = {
        "synthetic_database_slots": float(database["wait_ms"]["p95"]),
        "synthetic_aggregate_compute_slots": float(
            aggregate_compute["wait_ms"]["p95"]
        ),
        "load_generator_event_loop": float(event_loop_lag["p95"]),
    }
    resource, score = max(candidates.items(), key=lambda item: item[1])
    if score < 1.0:
        resource = "no_material_queue_observed"
    return {
        "candidate": resource,
        "basis": "largest measured p95 wait or scheduler lag in the synthetic model",
        "p95_ms_by_candidate": {key: round(value, 3) for key, value in candidates.items()},
        "production_bottleneck_claim_allowed": False,
    }


async def run_trial(
    config: IsolatedFixtureConfig, *, tier: int, trial_index: int
) -> dict[str, Any]:
    trial_seed = config.seed + trial_index * 10_000 + tier
    service = IsolatedFixtureService(config, trial_seed)
    metrics: list[RequestMetric] = []
    lag_samples: list[float] = []
    stop_lag = asyncio.Event()
    start = asyncio.Event()
    identity_digests = [
        synthetic_identity_digest(config.seed, trial_index, tier, vu_id)
        for vu_id in range(tier)
    ]
    if len(set(identity_digests)) != tier:
        raise RuntimeError("synthetic identity collision")

    began = time.perf_counter()
    deadline = began + config.duration_seconds
    lag_task = asyncio.create_task(
        sample_event_loop_lag(
            interval_ms=config.event_loop_sample_ms,
            stop=stop_lag,
            samples=lag_samples,
        )
    )
    workers = [
        asyncio.create_task(
            run_vu(
                vu_id=vu_id,
                identity_digest=identity_digests[vu_id],
                service=service,
                start=start,
                deadline=deadline,
                timeout_ms=config.request_timeout_ms,
                metrics=metrics,
            )
        )
        for vu_id in range(tier)
    ]
    start.set()
    await asyncio.gather(*workers)
    elapsed = time.perf_counter() - began
    stop_lag.set()
    await lag_task

    latencies = [metric.latency_ms for metric in metrics]
    errors = sum(1 for metric in metrics if not metric.ok)
    statuses = Counter(str(metric.status) for metric in metrics)
    database = service.database.public_dict()
    aggregate_compute = service.aggregate_compute.public_dict()
    event_loop_lag = {
        "sample_count": len(lag_samples),
        "p50": round(percentile(lag_samples, 50), 3),
        "p95": round(percentile(lag_samples, 95), 3),
        "p99": round(percentile(lag_samples, 99), 3),
        "max": round(max(lag_samples), 3) if lag_samples else 0.0,
    }
    return {
        "tier_vu": tier,
        "trial_index": trial_index,
        "duration_target_seconds": config.duration_seconds,
        "elapsed_seconds": round(elapsed, 6),
        "total_requests": len(metrics),
        "requests_per_second": round(len(metrics) / elapsed, 3) if elapsed else 0.0,
        "error_rate": round(errors / len(metrics), 6) if metrics else 1.0,
        "status_codes": dict(sorted(statuses.items())),
        "latency_ms": {
            "p50": round(percentile(latencies, 50), 3),
            "p95": round(percentile(latencies, 95), 3),
            "p99": round(percentile(latencies, 99), 3),
            "max": round(max(latencies), 3) if latencies else 0.0,
        },
        "identities": {
            "requested": tier,
            "unique": len(set(identity_digests)),
            "collision_count": tier - len(set(identity_digests)),
            "assignment": "one_unique_synthetic_digest_per_vu_per_trial",
            "identity_values_persisted": False,
            "identity_set_sha256": hashlib.sha256(
                "\n".join(sorted(identity_digests)).encode("ascii")
            ).hexdigest(),
        },
        "resources": {
            "database": database,
            "aggregate_compute": aggregate_compute,
            "event_loop_lag_ms": event_loop_lag,
        },
        "by_endpoint": summarize_endpoint_metrics(metrics),
        "bottleneck": identify_trial_bottleneck(
            database, aggregate_compute, event_loop_lag
        ),
        "external_io": {
            "http_requests": 0,
            "socket_connections": 0,
            "database_connections": 0,
            "redis_connections": 0,
            "worker_jobs": 0,
            "business_rows_written": 0,
        },
    }


def aggregate_tier(tier: int, trials: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    rps = [float(item["requests_per_second"]) for item in trials]
    errors = [float(item["error_rate"]) for item in trials]
    p50 = [float(item["latency_ms"]["p50"]) for item in trials]
    p95 = [float(item["latency_ms"]["p95"]) for item in trials]
    p99 = [float(item["latency_ms"]["p99"]) for item in trials]
    requests = [float(item["total_requests"]) for item in trials]
    bottlenecks = Counter(str(item["bottleneck"]["candidate"]) for item in trials)
    return {
        "tier_vu": tier,
        "trial_count": len(trials),
        "trials": list(trials),
        "across_trials": {
            "total_requests": metric_triplet(requests),
            "requests_per_second": metric_triplet(rps),
            "error_rate": metric_triplet(errors, digits=6),
            "latency_ms": {
                "p50": metric_triplet(p50),
                "p95": metric_triplet(p95),
                "p99": metric_triplet(p99),
            },
        },
        "representative": {
            "requests_per_second": round(median(rps), 3),
            "error_rate": round(max(errors), 6),
            "p50_ms": round(median(p50), 3),
            "p95_ms": round(max(p95), 3),
            "p99_ms": round(max(p99), 3),
        },
        "fixture_gate": {
            "pass": max(errors) <= 0.01 and max(p99) <= 1_000.0,
            "policy": "worst repeat error <= 1% and p99 <= 1000ms; fixture-only",
            "production_slo_gate": False,
        },
        "bottleneck_candidates": dict(sorted(bottlenecks.items())),
    }


def analyze_fixture(tiers: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    knee: int | None = None
    previous_rps: float | None = None
    previous_tier: int | None = None
    for item in tiers:
        tier = int(item["tier_vu"])
        rps = float(item["representative"]["requests_per_second"])
        if previous_rps and previous_tier:
            vu_growth = tier / previous_tier
            rps_growth = rps / previous_rps
            if vu_growth >= 1.8 and rps_growth < 1.35:
                knee = tier
                break
        previous_rps = rps
        previous_tier = tier
    highest = tiers[-1]
    candidates = Counter()
    for trial in highest["trials"]:
        candidates[str(trial["bottleneck"]["candidate"])] += 1
    bottleneck = candidates.most_common(1)[0][0] if candidates else "not_observed"
    return {
        "fixture_throughput_knee_vu": knee,
        "highest_fixture_tier_exercised": int(highest["tier_vu"]),
        "dominant_highest_tier_fixture_bottleneck": bottleneck,
        "interpretation": (
            "This identifies the deliberately constrained synthetic model, not V-KPI."
        ),
        "production_capacity": None,
        "maximum_real_users": None,
    }


async def build_report(config: IsolatedFixtureConfig) -> dict[str, Any]:
    config = config.validated()
    started_at = utc_now()
    tier_trials: dict[int, list[dict[str, Any]]] = {tier: [] for tier in config.tiers}
    began = time.perf_counter()
    # Trial-major order makes repeat drift visible instead of running all repeats
    # for one tier back-to-back.
    for trial_index in range(config.trials):
        for tier in config.tiers:
            tier_trials[tier].append(
                await run_trial(config, tier=tier, trial_index=trial_index)
            )
    tier_results = [aggregate_tier(tier, tier_trials[tier]) for tier in config.tiers]
    config_public = config.public_dict()
    script_path = Path(__file__).resolve()
    return {
        "schema_version": SCHEMA_VERSION,
        "evidence_class": EVIDENCE_CLASS,
        "status": "completed_isolated_fixture",
        "started_at": started_at,
        "completed_at": utc_now(),
        "elapsed_seconds": round(time.perf_counter() - began, 6),
        "run_id": hashlib.sha256(
            f"{started_at}:{sha256_json(config_public)}".encode("utf-8")
        ).hexdigest()[:24],
        "configuration": config_public,
        "configuration_sha256": sha256_json(config_public),
        "fixture_model": {
            "version": "vkpi-synthetic-read-model/v1",
            "endpoints": [endpoint.public_dict() for endpoint in ENDPOINTS],
            "timings_are_synthetic": True,
            "resource_slots_are_synthetic": True,
            "application_code_exercised": False,
        },
        "safety": {
            "transport": "direct_in_process_coroutine_call",
            "network_stack_used": False,
            "socket_listeners_opened": False,
            "http_requests_issued": 0,
            "database_connections_opened": 0,
            "redis_connections_opened": 0,
            "worker_jobs_enqueued": 0,
            "browser_used": False,
            "provider_calls": 0,
            "business_rows_read": 0,
            "business_rows_written": 0,
        },
        "identity_contract": {
            "kind": "deterministic_synthetic_digest",
            "one_unique_identity_per_vu_per_trial": True,
            "authentication_performed": False,
            "real_account_or_token_used": False,
            "identity_values_persisted": False,
        },
        "tier_results": tier_results,
        "analysis": analyze_fixture(tier_results),
        "claims": {
            "harness_calibration_evidence": True,
            "production_performance_evidence": False,
            "application_slo_evidence": False,
            "real_user_capacity_evidence": False,
            "cloud_capacity_evidence": False,
            "human_user_conversion_allowed": False,
            "maximum_real_users": None,
        },
        "limitations": [
            "service latency and resource contention are synthetic by construction",
            "the application, proxy, database, cache, Worker, providers, and browser are not exercised",
            "local scheduler noise can change measured coroutine timing",
            "VU means a closed-loop synthetic coroutine, never a person or licensed seat",
            "the self-signature is tamper evidence only and is not an independent producer attestation",
        ],
        "generator": {
            "python": platform.python_version(),
            "platform": platform.platform(),
            "script_name": script_path.name,
            "script_sha256": hashlib.sha256(script_path.read_bytes()).hexdigest(),
        },
    }


def sign_frozen_report(report: Mapping[str, Any], *, seed: int) -> FrozenSignedArtifact:
    if "attestation" in report:
        raise ValueError("unsigned report must not already contain an attestation")
    unsigned = json.loads(canonical_json(report))
    payload = canonical_json(unsigned)
    fixture_key = hashlib.sha256(f"{SIGNING_DOMAIN}:{seed}".encode("utf-8")).digest()
    integrity_tag = hmac.digest(fixture_key, payload, "sha256")
    signed_payload_sha256 = hashlib.sha256(payload).hexdigest()
    attestation = {
        "schema_version": ATTESTATION_SCHEMA,
        "algorithm": "HMAC-SHA256",
        "attestation_class": "deterministic_self_integrity_isolated_fixture",
        "integrity_tag_base64": base64.b64encode(integrity_tag).decode("ascii"),
        "signed_payload_sha256": signed_payload_sha256,
        "integrity_verified_at_write": True,
        "independent_producer": False,
        "production_trust": False,
        "secret_key_persisted": False,
        "key_derivation": "public_fixture_domain_and_configuration_seed",
    }
    final_report = {**unsigned, "attestation": attestation}
    encoded = json.dumps(final_report, ensure_ascii=False, indent=2) + "\n"
    artifact = FrozenSignedArtifact(
        report=final_report,
        encoded=encoded.encode("utf-8"),
        signed_payload_sha256=signed_payload_sha256,
        integrity_tag_base64=attestation["integrity_tag_base64"],
    )
    if not verify_signed_report(artifact.report):
        raise RuntimeError("isolated fixture signature failed immediate verification")
    return artifact


def verify_signed_report(report: Mapping[str, Any]) -> bool:
    attestation = report.get("attestation")
    if not isinstance(attestation, Mapping):
        return False
    unsigned = {key: value for key, value in report.items() if key != "attestation"}
    payload = canonical_json(unsigned)
    try:
        integrity_tag = base64.b64decode(
            str(attestation["integrity_tag_base64"]).encode("ascii"), validate=True
        )
        if hashlib.sha256(payload).hexdigest() != attestation["signed_payload_sha256"]:
            return False
        seed = int(unsigned["configuration"]["seed"])
        fixture_key = hashlib.sha256(f"{SIGNING_DOMAIN}:{seed}".encode("utf-8")).digest()
        expected_tag = hmac.digest(fixture_key, payload, "sha256")
        if not hmac.compare_digest(integrity_tag, expected_tag):
            return False
    except (KeyError, TypeError, ValueError):
        return False
    return True


def write_frozen_artifact(artifact: FrozenSignedArtifact, output: Path) -> Path:
    output = Path(output).expanduser()
    output.parent.mkdir(parents=True, exist_ok=True)
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    fd = os.open(output, flags, 0o600)
    try:
        with os.fdopen(fd, "wb") as stream:
            fd = -1
            stream.write(artifact.encoded)
            stream.flush()
            os.fsync(stream.fileno())
    finally:
        if fd >= 0:
            os.close(fd)
    output.chmod(stat.S_IRUSR)
    return output


def parse_tiers(raw: str) -> tuple[int, ...]:
    try:
        tiers = tuple(int(item.strip()) for item in raw.split(",") if item.strip())
    except ValueError as exc:
        raise argparse.ArgumentTypeError("tiers must be comma-separated integers") from exc
    try:
        IsolatedFixtureConfig(tiers=tiers).validated()
    except ValueError as exc:
        raise argparse.ArgumentTypeError(str(exc)) from exc
    return tiers


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tiers", type=parse_tiers, default=DEFAULT_TIERS)
    parser.add_argument("--trials", type=int, default=3)
    parser.add_argument("--duration-seconds", type=float, default=1.2)
    parser.add_argument("--seed", type=int, default=20260713)
    parser.add_argument("--database-slots", type=int, default=16)
    parser.add_argument("--aggregate-compute-slots", type=int, default=8)
    parser.add_argument("--request-timeout-ms", type=float, default=1_500.0)
    parser.add_argument("--event-loop-sample-ms", type=float, default=10.0)
    parser.add_argument("--output", type=Path)
    return parser


def main(argv: Iterable[str] | None = None) -> int:
    args = build_parser().parse_args(list(argv) if argv is not None else None)
    config = IsolatedFixtureConfig(
        tiers=tuple(args.tiers),
        trials=args.trials,
        duration_seconds=args.duration_seconds,
        seed=args.seed,
        database_slots=args.database_slots,
        aggregate_compute_slots=args.aggregate_compute_slots,
        request_timeout_ms=args.request_timeout_ms,
        event_loop_sample_ms=args.event_loop_sample_ms,
    ).validated()
    report = asyncio.run(build_report(config))
    artifact = sign_frozen_report(report, seed=config.seed)
    output = args.output or Path(
        f"/tmp/vkpi-isolated-capacity-{time.strftime('%Y%m%dT%H%M%SZ', time.gmtime())}.json"
    )
    write_frozen_artifact(artifact, output)
    summary = {
        "status": report["status"],
        "evidence_class": EVIDENCE_CLASS,
        "output": str(output),
        "signature_verified": verify_signed_report(artifact.report),
        "production_capacity": None,
        "tiers": [
            {
                "vu": item["tier_vu"],
                **item["representative"],
            }
            for item in report["tier_results"]
        ],
        "fixture_bottleneck": report["analysis"][
            "dominant_highest_tier_fixture_bottleneck"
        ],
    }
    stdout_out(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
