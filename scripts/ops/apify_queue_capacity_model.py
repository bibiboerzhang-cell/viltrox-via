#!/usr/bin/env python3
"""Hermetic FCFS capacity model for the legacy ``apify_jobs`` worker.

The model consumes *aggregated service-time samples* from a local JSON file and
simulates bounded worker/provider slots.  It never imports a network client,
opens a socket, connects to PostgreSQL/Redis, starts a Worker, or calls a
provider.  Results are planning evidence only; they are not production load or
real-user capacity evidence.
"""
from __future__ import annotations

import argparse
import hashlib
import heapq
import json
import math
import os
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from statistics import median
from typing import Any, Iterable, Mapping, Sequence

SCHEMA_VERSION = "vkpi-apify-queue-capacity-model/v1"
BUNDLE_SCHEMA_VERSION = "vkpi-apify-queue-capacity-evidence/v1"
INPUT_SCHEMA_VERSION = "vkpi-apify-service-time-samples/v1"
EVIDENCE_CLASS = "hermetic_queue_model"
DEFAULT_LANES = (1, 2, 4, 8)
MAX_JOBS = 10_000
MAX_LANES = 64
MAX_DURATION_SECONDS = 86_400.0


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


def percentile(values: Sequence[float], pct: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(float(value) for value in values)
    if len(ordered) == 1:
        return ordered[0]
    position = (len(ordered) - 1) * pct / 100.0
    lower = int(position)
    upper = min(lower + 1, len(ordered) - 1)
    fraction = position - lower
    return ordered[lower] * (1.0 - fraction) + ordered[upper] * fraction


def distribution(values: Sequence[float]) -> dict[str, float]:
    if not values:
        return {"p50": 0.0, "p95": 0.0, "p99": 0.0, "max": 0.0}
    return {
        "p50": round(percentile(values, 50), 3),
        "p95": round(percentile(values, 95), 3),
        "p99": round(percentile(values, 99), 3),
        "max": round(max(values), 3),
    }


def observed_rate(values: Sequence[bool | None]) -> dict[str, Any]:
    """Describe an observed binary rate without treating missing labels as zero."""
    known = [value for value in values if value is not None]
    numerator = sum(value is True for value in known)
    denominator = len(known)
    sample_count = len(values)
    if denominator == 0:
        status = "unavailable"
        rate: float | None = None
    else:
        status = "complete" if denominator == sample_count else "partial"
        rate = round(numerator / denominator, 6)
    return {
        "status": status,
        "rate": rate,
        "numerator": numerator if denominator else None,
        "denominator": denominator,
        "sample_count": sample_count,
        "coverage": round(denominator / sample_count, 6) if sample_count else 0.0,
    }


def _optional_bool(raw: Mapping[str, Any], name: str) -> bool | None:
    if name not in raw or raw.get(name) is None:
        return None
    value = raw.get(name)
    if not isinstance(value, bool):
        raise ValueError(f"{name} must be boolean or null")
    return value


@dataclass(frozen=True)
class JobSample:
    duration_seconds: float
    resource_group: str = "unclassified"
    job_type: str = "unknown"
    errored: bool | None = None
    conflicted: bool | None = None

    @classmethod
    def from_mapping(cls, raw: Mapping[str, Any]) -> "JobSample":
        duration = float(raw.get("duration_seconds") or 0.0)
        if not math.isfinite(duration) or not 0.0 < duration <= MAX_DURATION_SECONDS:
            raise ValueError("duration_seconds must be finite and within (0, 86400]")
        resource = str(raw.get("resource_group") or "unclassified").strip().lower()
        job_type = str(raw.get("job_type") or "unknown").strip().lower()
        if not resource or len(resource) > 80 or not job_type or len(job_type) > 160:
            raise ValueError("resource_group/job_type is empty or oversized")
        return cls(
            duration,
            resource,
            job_type,
            _optional_bool(raw, "errored"),
            _optional_bool(raw, "conflicted"),
        )


def observed_attempt_quality(jobs: Sequence[JobSample]) -> dict[str, Any]:
    return {
        "error_rate": observed_rate([job.errored for job in jobs]),
        "conflict_rate": observed_rate([job.conflicted for job in jobs]),
        "interpretation": (
            "rates describe only explicitly labelled attempts; null means the source "
            "did not capture the denominator and must not be read as zero"
        ),
    }


def _validated_caps(raw: Mapping[str, Any] | None, lanes: int) -> dict[str, int]:
    caps: dict[str, int] = {}
    for name, value in (raw or {}).items():
        key = str(name).strip().lower()
        cap = int(value)
        if not key or len(key) > 80 or not 1 <= cap <= MAX_LANES:
            raise ValueError("provider caps must be named integers within [1, 64]")
        caps[key] = min(cap, lanes)
    return caps


def simulate_fcfs(
    jobs: Sequence[JobSample],
    *,
    lanes: int,
    provider_caps: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Schedule jobs in input order on bounded worker and provider slots."""
    if not 1 <= int(lanes) <= MAX_LANES:
        raise ValueError(f"lanes must stay within [1, {MAX_LANES}]")
    if not jobs or len(jobs) > MAX_JOBS:
        raise ValueError(f"jobs must contain between 1 and {MAX_JOBS} samples")
    lane_heap = [(0.0, index) for index in range(int(lanes))]
    heapq.heapify(lane_heap)
    caps = _validated_caps(provider_caps, int(lanes))
    resource_heaps: dict[str, list[tuple[float, int]]] = {}
    waits: list[float] = []
    sojourns: list[float] = []
    finishes: list[float] = []
    resource_gate_waits: list[float] = []

    for job in jobs:
        lane_ready, lane_id = heapq.heappop(lane_heap)
        resource_heap = resource_heaps.get(job.resource_group)
        resource_slot: int | None = None
        resource_ready = 0.0
        if job.resource_group in caps:
            if resource_heap is None:
                resource_heap = [(0.0, index) for index in range(caps[job.resource_group])]
                heapq.heapify(resource_heap)
                resource_heaps[job.resource_group] = resource_heap
            resource_ready, resource_slot = heapq.heappop(resource_heap)
        started = max(lane_ready, resource_ready)
        finished = started + job.duration_seconds
        heapq.heappush(lane_heap, (finished, lane_id))
        if resource_heap is not None and resource_slot is not None:
            heapq.heappush(resource_heap, (finished, resource_slot))
        waits.append(started)
        sojourns.append(finished)
        finishes.append(finished)
        resource_gate_waits.append(max(0.0, resource_ready - lane_ready))

    makespan = max(finishes)
    busy_seconds = sum(job.duration_seconds for job in jobs)
    quality = observed_attempt_quality(jobs)
    errors = quality["error_rate"]
    successful_per_hour: float | None = None
    if errors["status"] == "complete":
        successful = len(jobs) - int(errors["numerator"] or 0)
        successful_per_hour = round(successful * 3600.0 / makespan, 3)
    attempts_per_hour = len(jobs) * 3600.0 / makespan
    delayed_attempts = sum(wait > 0.0 for wait in resource_gate_waits)
    return {
        "lanes": int(lanes),
        "provider_caps": caps,
        "jobs": len(jobs),
        "attempts": len(jobs),
        "makespan_seconds": round(makespan, 3),
        "makespan_minutes": round(makespan / 60.0, 3),
        "jobs_per_hour": round(attempts_per_hour, 3),
        "throughput": {
            "attempts_per_second": round(len(jobs) / makespan, 6),
            "attempts_per_minute": round(len(jobs) * 60.0 / makespan, 3),
            "attempts_per_hour": round(attempts_per_hour, 3),
            "successful_completions_per_hour": successful_per_hour,
        },
        "observed_attempt_quality": quality,
        "worker_slot_utilization_upper_bound": round(
            busy_seconds / (makespan * int(lanes)), 4
        ),
        "queue_wait_seconds": distribution(waits),
        "completion_from_queue_start_seconds": distribution(sojourns),
        "modeled_resource_gate": {
            "delayed_attempts": delayed_attempts,
            "contention_rate": round(delayed_attempts / len(jobs), 6),
            "wait_seconds": distribution(resource_gate_waits),
            "interpretation": (
                "modelled delay caused by a configured resource cap; this is not a "
                "measured database/provider conflict rate"
            ),
        },
    }


def build_report(
    jobs: Sequence[JobSample],
    *,
    lanes: Sequence[int] = DEFAULT_LANES,
    provider_caps: Mapping[str, Any] | None = None,
    input_metadata: Mapping[str, Any] | None = None,
    input_sha256: str | None = None,
) -> dict[str, Any]:
    lane_values = tuple(sorted(set(int(value) for value in lanes)))
    if not lane_values or lane_values[0] < 1 or lane_values[-1] > MAX_LANES:
        raise ValueError(f"lane tiers must stay within [1, {MAX_LANES}]")
    scenarios = [
        simulate_fcfs(jobs, lanes=value, provider_caps=provider_caps)
        for value in lane_values
    ]
    baseline = scenarios[0]["makespan_seconds"]
    for scenario in scenarios:
        scenario["speedup_vs_smallest_tier"] = round(
            baseline / scenario["makespan_seconds"], 4
        )
    report: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "generated_at": utc_now(),
        "evidence_class": EVIDENCE_CLASS,
        "input": {
            "sample_count": len(jobs),
            "duration_seconds": {
                "p50": round(median([job.duration_seconds for job in jobs]), 3),
                "p95": round(percentile([job.duration_seconds for job in jobs], 95), 3),
                "p99": round(percentile([job.duration_seconds for job in jobs], 99), 3),
                "max": round(max(job.duration_seconds for job in jobs), 3),
            },
            "resource_groups": sorted({job.resource_group for job in jobs}),
            "observed_attempt_quality": observed_attempt_quality(jobs),
            "metadata": dict(input_metadata or {}),
        },
        "scenarios": scenarios,
        "safety": {
            "network_calls": 0,
            "database_connections": 0,
            "redis_connections": 0,
            "provider_calls": 0,
            "worker_jobs_claimed": 0,
            "business_rows_read": 0,
            "business_rows_written": 0,
        },
        "claims": {
            "production_load_test": False,
            "real_user_capacity_evidence": False,
            "safe_live_concurrency": None,
            "maximum_real_users": None,
            "error_or_conflict_sla": None,
            "interpretation": "deterministic planning model; validate each tier in isolated staging",
        },
        "limitations": [
            "service times are replayed samples and do not change under contention",
            "provider rate limits, 429/5xx, retries, DB/R2 contention and cost are only represented when encoded as caps or samples",
            "missing error/conflict labels remain null and are never inferred to be zero",
            "results are not permission to start additional live workers",
        ],
    }
    calculation = {
        "schema_version": report["schema_version"],
        "input": report["input"],
        "scenarios": report["scenarios"],
        "claims": report["claims"],
        "limitations": report["limitations"],
    }
    report["reproducibility"] = {
        "input_sha256": str(input_sha256 or "") or None,
        "calculation_sha256": hashlib.sha256(canonical_json(calculation)).hexdigest(),
        "deterministic_fields": ["input", "scenarios", "claims", "limitations"],
    }
    report["artifact_sha256"] = hashlib.sha256(canonical_json(report)).hexdigest()
    return report


def _ordered_percentiles(payload: Mapping[str, Any]) -> bool:
    return all(
        float(payload.get(left, 0.0)) <= float(payload.get(right, 0.0))
        for left, right in (("p50", "p95"), ("p95", "p99"), ("p99", "max"))
    )


def build_scenario_bundle(
    jobs: Sequence[JobSample],
    *,
    lanes: Sequence[int] = DEFAULT_LANES,
    bounded_resources: Sequence[str] | None = None,
    input_metadata: Mapping[str, Any] | None = None,
    input_sha256: str | None = None,
) -> dict[str, Any]:
    """Build and cross-check current/cap=2/ideal deterministic scenarios."""
    resources = sorted({job.resource_group for job in jobs})
    if bounded_resources is None:
        # ``unclassified`` means that no shared provider/resource gate has been
        # established for the sample.  Capping it would silently serialize
        # lightweight work (for example dossier bookkeeping) and materially
        # understate the benefit of adding workers to a mixed queue.
        bounded = [resource for resource in resources if resource != "unclassified"]
    else:
        bounded = sorted(
            {
                str(resource).strip().lower()
                for resource in bounded_resources
                if str(resource).strip().lower() in resources
            }
        )
    definitions: dict[str, dict[str, Any]] = {
        "current_guarded_cap_1": {
            "resource_cap": 1,
            "provider_caps": {resource: 1 for resource in bounded},
            "claim": "current guarded planning case; not observed concurrent capacity",
        },
        "two_slot_candidate_cap_2": {
            "resource_cap": 2,
            "provider_caps": {resource: 2 for resource in bounded},
            "claim": "candidate cap=2 planning case; requires isolated staging validation",
        },
        "ideal_unbounded_lower_bound": {
            "resource_cap": None,
            "provider_caps": {},
            "claim": "idealized lower bound with no provider/resource contention",
        },
    }
    profiles: dict[str, Any] = {}
    for name, definition in definitions.items():
        report = build_report(
            jobs,
            lanes=lanes,
            provider_caps=definition["provider_caps"],
            input_metadata=input_metadata,
            input_sha256=input_sha256,
        )
        profiles[name] = {
            "definition": definition,
            "scenarios": report["scenarios"],
            "calculation_sha256": report["reproducibility"]["calculation_sha256"],
        }

    checks: list[dict[str, Any]] = []

    def add_check(check_id: str, passed: bool, evidence: Any) -> None:
        checks.append({"id": check_id, "pass": bool(passed), "evidence": evidence})

    lane_values = tuple(sorted(set(int(value) for value in lanes)))
    for name, profile in profiles.items():
        scenarios = profile["scenarios"]
        add_check(
            f"{name}.lane_tiers",
            [row["lanes"] for row in scenarios] == list(lane_values),
            [row["lanes"] for row in scenarios],
        )
        add_check(
            f"{name}.makespan_monotonic",
            all(
                scenarios[index]["makespan_seconds"]
                >= scenarios[index + 1]["makespan_seconds"]
                for index in range(len(scenarios) - 1)
            ),
            [row["makespan_seconds"] for row in scenarios],
        )
        add_check(
            f"{name}.throughput_monotonic",
            all(
                scenarios[index]["throughput"]["attempts_per_hour"]
                <= scenarios[index + 1]["throughput"]["attempts_per_hour"]
                for index in range(len(scenarios) - 1)
            ),
            [row["throughput"]["attempts_per_hour"] for row in scenarios],
        )
        percentile_payloads = [
            row[key]
            for row in scenarios
            for key in ("queue_wait_seconds", "completion_from_queue_start_seconds")
        ] + [row["modeled_resource_gate"]["wait_seconds"] for row in scenarios]
        add_check(
            f"{name}.percentiles_ordered",
            all(_ordered_percentiles(payload) for payload in percentile_payloads),
            "p50 <= p95 <= p99 <= max",
        )

    current = profiles["current_guarded_cap_1"]["scenarios"]
    two_slot = profiles["two_slot_candidate_cap_2"]["scenarios"]
    ideal = profiles["ideal_unbounded_lower_bound"]["scenarios"]
    add_check(
        "one_lane_profiles_equivalent",
        len({row[0]["makespan_seconds"] for row in (current, two_slot, ideal)}) == 1,
        [row[0]["makespan_seconds"] for row in (current, two_slot, ideal)],
    )
    add_check(
        "resource_caps_exact",
        all(
            set(row["provider_caps"]) == set(bounded)
            and set(row["provider_caps"].values()) <= {1}
            for row in current
        )
        and all(
            set(row["provider_caps"]) == set(bounded)
            and set(row["provider_caps"].values()) <= {1, 2}
            for row in two_slot
        )
        and all(not row["provider_caps"] for row in ideal),
        {
            "current": [row["provider_caps"] for row in current],
            "two_slot": [row["provider_caps"] for row in two_slot],
            "ideal": [row["provider_caps"] for row in ideal],
        },
    )
    ordering = [
        {
            "lanes": lane_values[index],
            "current_seconds": current[index]["makespan_seconds"],
            "two_slot_seconds": two_slot[index]["makespan_seconds"],
            "ideal_seconds": ideal[index]["makespan_seconds"],
        }
        for index in range(len(lane_values))
    ]
    add_check(
        "bounded_makespan_order",
        all(
            row["current_seconds"] >= row["two_slot_seconds"] >= row["ideal_seconds"]
            for row in ordering
        ),
        ordering,
    )
    quality = observed_attempt_quality(jobs)
    add_check(
        "missing_quality_not_zero_filled",
        all(
            metric["rate"] is not None or metric["status"] == "unavailable"
            for metric in (quality["error_rate"], quality["conflict_rate"])
        ),
        quality,
    )

    report: dict[str, Any] = {
        "schema_version": BUNDLE_SCHEMA_VERSION,
        "generated_at": utc_now(),
        "evidence_class": "hermetic_capacity_scenario_bundle",
        "source": {
            "input_sha256": str(input_sha256 or "") or None,
            "sample_count": len(jobs),
            "metadata": dict(input_metadata or {}),
        },
        "metric_definitions": {
            "p50_p95_p99": "linear-interpolated percentiles over the named finite sample",
            "throughput": "scheduled attempts divided by modeled makespan; not real requests/users",
            "error_rate": "explicitly labelled errored attempts / labelled attempts",
            "conflict_rate": "explicitly labelled conflicted attempts / labelled attempts",
            "resource_contention_rate": "attempts delayed by a configured model cap / attempts",
        },
        "observed_sample": {
            "duration_seconds": distribution([job.duration_seconds for job in jobs]),
            "attempt_quality": quality,
        },
        "lane_tiers": list(lane_values),
        "profiles": profiles,
        "validation": {
            "pass": all(check["pass"] for check in checks),
            "checks": checks,
        },
        "safety": {
            "network_calls": 0,
            "database_connections": 0,
            "redis_connections": 0,
            "provider_calls": 0,
            "worker_processes_started": 0,
            "business_rows_read": 0,
            "business_rows_written": 0,
        },
        "claims": {
            "model_only": True,
            "production_load_test": False,
            "real_sla": False,
            "safe_live_concurrency": None,
            "maximum_real_users": None,
        },
        "limitations": [
            (
                f"the {len(jobs)} durations are a bounded historical sample "
                "rather than an independently sampled workload distribution"
            ),
            "replay assumes service times do not inflate under concurrency",
            "unlabelled errors/conflicts remain unavailable rather than zero",
            "provider, DB, Redis, R2, CPU, memory, network and cost saturation were not exercised",
        ],
    }
    deterministic = {
        key: report[key]
        for key in (
            "schema_version",
            "source",
            "metric_definitions",
            "observed_sample",
            "lane_tiers",
            "profiles",
            "validation",
            "safety",
            "claims",
            "limitations",
        )
    }
    report["reproducibility"] = {
        "calculation_sha256": hashlib.sha256(canonical_json(deterministic)).hexdigest(),
        "rerun": (
            "PYTHONPATH=. .venv/bin/python scripts/ops/apify_queue_capacity_model.py "
            "--scenario-bundle --input <service-time-samples.json>"
        ),
    }
    report["artifact_sha256"] = hashlib.sha256(canonical_json(report)).hexdigest()
    return report


def load_samples(path: Path) -> tuple[list[JobSample], dict[str, Any], dict[str, int]]:
    raw = json.loads(path.read_text(encoding="utf-8"))
    if raw.get("schema_version") != INPUT_SCHEMA_VERSION:
        raise ValueError(f"input schema_version must be {INPUT_SCHEMA_VERSION}")
    rows = raw.get("samples")
    if not isinstance(rows, list):
        raise ValueError("input samples must be a list")
    jobs = [JobSample.from_mapping(row) for row in rows if isinstance(row, Mapping)]
    if len(jobs) != len(rows):
        raise ValueError("every sample must be an object")
    metadata = raw.get("metadata") if isinstance(raw.get("metadata"), Mapping) else {}
    caps = raw.get("provider_caps") if isinstance(raw.get("provider_caps"), Mapping) else {}
    return jobs, dict(metadata), {str(k): int(v) for k, v in caps.items()}


def parse_lanes(value: str) -> tuple[int, ...]:
    try:
        lanes = tuple(sorted(set(int(part.strip()) for part in value.split(",") if part.strip())))
    except ValueError as exc:
        raise argparse.ArgumentTypeError("lanes must be comma-separated integers") from exc
    if not lanes or lanes[0] < 1 or lanes[-1] > MAX_LANES:
        raise argparse.ArgumentTypeError(f"lanes must stay within [1, {MAX_LANES}]")
    return lanes


def parse_provider_cap(value: str) -> tuple[str, int]:
    try:
        name, raw_cap = value.split("=", 1)
        key = name.strip().lower()
        cap = int(raw_cap.strip())
    except (ValueError, TypeError) as exc:
        raise argparse.ArgumentTypeError("provider cap must look like resource_group=2") from exc
    if not key or len(key) > 80 or not 1 <= cap <= MAX_LANES:
        raise argparse.ArgumentTypeError(f"provider cap must stay within [1, {MAX_LANES}]")
    return key, cap


def write_exclusive(path: Path, report: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False) + "\n"
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o400)
    try:
        os.write(fd, payload.encode("utf-8"))
    finally:
        os.close(fd)


def main(argv: Iterable[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--lanes", type=parse_lanes, default=DEFAULT_LANES)
    parser.add_argument(
        "--provider-cap",
        action="append",
        type=parse_provider_cap,
        default=[],
        help="override/add a bounded resource cap, e.g. comments_pipeline=2",
    )
    parser.add_argument("--output", type=Path)
    parser.add_argument(
        "--scenario-bundle",
        action="store_true",
        help="emit current cap=1, candidate cap=2 and ideal scenario cross-checks",
    )
    args = parser.parse_args(list(argv) if argv is not None else None)
    jobs, metadata, provider_caps = load_samples(args.input)
    input_sha256 = hashlib.sha256(args.input.read_bytes()).hexdigest()
    provider_caps.update(dict(args.provider_cap))
    if args.scenario_bundle:
        if args.provider_cap:
            parser.error("--provider-cap cannot be combined with --scenario-bundle")
        report = build_scenario_bundle(
            jobs,
            lanes=args.lanes,
            bounded_resources=tuple(provider_caps) if provider_caps else None,
            input_metadata=metadata,
            input_sha256=input_sha256,
        )
    else:
        report = build_report(
            jobs,
            lanes=args.lanes,
            provider_caps=provider_caps,
            input_metadata=metadata,
            input_sha256=input_sha256,
        )
    if args.output:
        write_exclusive(args.output, report)
    sys.stdout.write(json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
