#!/usr/bin/env python3
"""Hermetic mock benchmark for the V-KPI online net-new-30 contract.

The benchmark uses a deterministic discrete-event clock to model three
concurrent discovery providers, strict fast verification, cross-source
canonical deduplication and refill.  It performs no network or database I/O.
All timing claims are synthetic and all report fields are aggregate-only.
"""
from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime, timezone
import hashlib
import heapq
import itertools
import json
import math
import os
from pathlib import Path
import re
import stat
import sys
import tempfile
import time
from typing import Any, Mapping, NamedTuple, Sequence


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.stdout_utils import out_json  # noqa: E402


SCHEMA_VERSION = "vkpi_kol_online_mock_benchmark_v1"
DEFAULT_OUTPUT = Path("/private/tmp/vkpi-kol-online-v2-synthetic-20260818.json")
PLATFORMS = ("youtube", "instagram", "tiktok")
STRICT_GATES = (
    "account_quality",
    "followers",
    "activity",
    "market",
    "language",
    "profile_type",
    "platform",
    "relevance",
)
FUNNEL_GATE_FIELDS = {
    gate: f"{gate}_pass" for gate in STRICT_GATES
}
FORBIDDEN_REPORT_KEYS = {
    "candidate",
    "candidates",
    "creator",
    "creator_key",
    "canonical",
    "canonical_key",
    "handle",
    "display_name",
    "email",
    "phone",
    "profile_url",
    "content_url",
    "contact",
    "contacts",
    "contact_channels",
    "other_contacts_json",
    "items",
}
EMAIL_RE = re.compile(r"(?i)\b[a-z0-9.!#$%&'*+/=?^_`{|}~-]+@[a-z0-9.-]+\.[a-z]{2,}\b")
CONTACT_ROUTE_RE = re.compile(
    r"(?i)(?:wa\.me/|t\.me/|m\.me/|instagram\.com/direct|twitter\.com/messages|"
    r"x\.com/messages|facebook\.com/messages|discord(?:\.gg|\.com/invite)/)"
)


class MockCandidate(NamedTuple):
    key: str
    platform: str
    failed_gate: str | None
    verify_ms: int


class MockBatch(NamedTuple):
    delay_ms: int
    rows: tuple[MockCandidate, ...]
    error_code: str | None
    terminal_error: bool


class MockScenario(NamedTuple):
    scenario_id: str
    batches: dict[str, tuple[MockBatch, ...]]
    local_keys: frozenset[str]
    max_provider_calls: int
    max_calls_per_platform: int
    max_candidates_admitted: int
    verify_parallelism: int
    expected_status: str
    expected_shortfall_reason: str | None
    expected_online_net_new: int
    expected_degraded_platforms: int


def _percentile(values: Sequence[float], q: float) -> float | None:
    ordered = sorted(float(value) for value in values)
    if not ordered:
        return None
    index = max(0, min(len(ordered) - 1, math.ceil(q * len(ordered)) - 1))
    return round(ordered[index], 3)


def _stats(values: Sequence[float | int | None]) -> dict[str, float | int | None]:
    available = [float(value) for value in values if value is not None]
    return {
        "n": len(available),
        "min": round(min(available), 3) if available else None,
        "p50": _percentile(available, 0.50),
        "p95": _percentile(available, 0.95),
        "max": round(max(available), 3) if available else None,
    }


def _candidate(
    key: str,
    platform: str,
    *,
    failed_gate: str | None = None,
    verify_ms: int = 4,
) -> MockCandidate:
    if platform not in PLATFORMS:
        raise ValueError(f"unsupported_mock_platform:{platform}")
    if failed_gate is not None and failed_gate not in STRICT_GATES:
        raise ValueError(f"unsupported_failed_gate:{failed_gate}")
    if not key or verify_ms < 1:
        raise ValueError("invalid_mock_candidate")
    return MockCandidate(key, platform, failed_gate, int(verify_ms))


def _batch(
    delay_ms: int,
    rows: Sequence[MockCandidate] = (),
    *,
    error_code: str | None = None,
    terminal_error: bool = True,
) -> MockBatch:
    if delay_ms < 1:
        raise ValueError("mock_batch_delay_must_be_positive")
    if error_code and rows:
        raise ValueError("mock_error_batch_cannot_contain_candidates")
    return MockBatch(int(delay_ms), tuple(rows), error_code, bool(terminal_error))


def _valid_range(prefix: str, start: int, end: int, platform: str) -> list[MockCandidate]:
    return [_candidate(f"{prefix}:{index:03d}", platform) for index in range(start, end + 1)]


def _local_rows(local_keys: Sequence[str], platform: str) -> list[MockCandidate]:
    return [_candidate(key, platform) for key in local_keys]


def _default_local_keys() -> frozenset[str]:
    return frozenset(f"local:{index:03d}" for index in range(1, 31))


def _complete_scenario() -> MockScenario:
    local = _default_local_keys()
    gates = list(STRICT_GATES)
    batches = {
        "youtube": (
            _batch(90, [*_local_rows(sorted(local)[:2], "youtube"), *_valid_range("happy", 1, 7, "youtube"), _candidate("reject:a", "youtube", failed_gate=gates[0]), _candidate("reject:b", "youtube", failed_gate=gates[1])]),
            _batch(110, [*_valid_range("happy", 8, 14, "youtube"), _candidate("reject:c", "youtube", failed_gate=gates[2])]),
        ),
        "instagram": (
            _batch(70, [*_local_rows(sorted(local)[2:3], "instagram"), *_valid_range("happy", 5, 12, "instagram"), _candidate("reject:d", "instagram", failed_gate=gates[3]), _candidate("reject:e", "instagram", failed_gate=gates[4])]),
            _batch(100, [*_valid_range("happy", 15, 22, "instagram"), _candidate("reject:f", "instagram", failed_gate=gates[5])]),
        ),
        "tiktok": (
            _batch(105, [*_valid_range("happy", 18, 27, "tiktok"), _candidate("reject:g", "tiktok", failed_gate=gates[6]), _candidate("reject:h", "tiktok", failed_gate=gates[7])]),
            _batch(90, _valid_range("happy", 28, 36, "tiktok")),
        ),
    }
    return MockScenario(
        "happy_path_refill", batches, local, 18, 6, 200, 6,
        "complete", None, 30, 0,
    )


def _duplicate_heavy_scenario() -> MockScenario:
    local = _default_local_keys()
    batches: dict[str, tuple[MockBatch, ...]] = {}
    for platform_index, platform in enumerate(PLATFORMS):
        pages: list[MockBatch] = []
        for page in range(1, 5):
            shared_start = (page - 1) * 5 + 1
            rows = [
                *_local_rows(sorted(local)[platform_index:platform_index + 2], platform),
                *_valid_range("shared", shared_start, shared_start + 4, platform),
                *_valid_range(f"{platform}-unique", (page - 1) * 2 + 1, page * 2, platform),
            ]
            pages.append(_batch(45 + platform_index * 7 + page * 5, rows))
        batches[platform] = tuple(pages)
    return MockScenario(
        "duplicate_heavy_refill", batches, local, 15, 5, 240, 6,
        "complete", None, 30, 0,
    )


def _one_platform_degraded_complete() -> MockScenario:
    local = _default_local_keys()
    return MockScenario(
        "one_platform_degraded_complete",
        {
            "youtube": (
                _batch(55, _valid_range("degraded-fill-y", 1, 10, "youtube")),
                _batch(65, _valid_range("degraded-fill-y", 11, 20, "youtube")),
            ),
            "instagram": (_batch(40, error_code="mock_timeout"),),
            "tiktok": (
                _batch(60, _valid_range("degraded-fill-t", 1, 10, "tiktok")),
                _batch(60, _valid_range("degraded-fill-t", 11, 20, "tiktok")),
            ),
        },
        local, 12, 4, 160, 6, "complete", None, 30, 1,
    )


def _one_platform_degraded_shortfall() -> MockScenario:
    local = _default_local_keys()
    return MockScenario(
        "one_platform_degraded_shortfall",
        {
            "youtube": (_batch(45, [*_valid_range("degraded-short-y", 1, 9, "youtube"), _candidate("degraded-short-reject-y", "youtube", failed_gate="market")]),),
            "instagram": (_batch(35, error_code="mock_provider_error"),),
            "tiktok": (_batch(50, [*_valid_range("degraded-short-t", 1, 9, "tiktok"), _candidate("degraded-short-reject-t", "tiktok", failed_gate="activity")]),),
        },
        local, 9, 3, 100, 4, "shortfall", "candidate_exhausted", 18, 1,
    )


def _provider_budget_scenario() -> MockScenario:
    local = _default_local_keys()
    batches = {
        platform: tuple(
            _batch(35 + platform_index * 5, _valid_range(f"call-budget-{platform}", page * 4 + 1, page * 4 + 4, platform))
            for page in range(4)
        )
        for platform_index, platform in enumerate(PLATFORMS)
    }
    return MockScenario(
        "provider_call_budget_exhausted", batches, local, 5, 4, 180, 4,
        "shortfall", "budget_exhausted", 20, 0,
    )


def _candidate_budget_scenario() -> MockScenario:
    local = _default_local_keys()
    return MockScenario(
        "candidate_budget_exhausted",
        {
            "youtube": (_batch(45, _valid_range("candidate-budget-y", 1, 10, "youtube")),),
            "instagram": (_batch(35, _valid_range("candidate-budget-i", 1, 10, "instagram")),),
            "tiktok": (_batch(55, _valid_range("candidate-budget-t", 1, 10, "tiktok")),),
        },
        local, 9, 3, 20, 4, "shortfall", "budget_exhausted", 20, 0,
    )


def _candidate_exhausted_scenario() -> MockScenario:
    local = _default_local_keys()
    return MockScenario(
        "candidate_exhausted",
        {
            "youtube": (_batch(40, _valid_range("exhaust-y", 1, 5, "youtube")),),
            "instagram": (_batch(45, _valid_range("exhaust-i", 1, 5, "instagram")),),
            "tiktok": (_batch(50, _valid_range("exhaust-t", 1, 5, "tiktok")),),
        },
        local, 9, 3, 100, 4, "shortfall", "candidate_exhausted", 15, 0,
    )


def _gate_funnel_scenario() -> MockScenario:
    local = _default_local_keys()
    rejected = [
        _candidate(f"gate-reject:{index}", PLATFORMS[index % len(PLATFORMS)], failed_gate=gate)
        for index, gate in enumerate(STRICT_GATES)
    ]
    return MockScenario(
        "strict_eight_gate_funnel",
        {
            "youtube": (_batch(55, [*_valid_range("gate-pass-y", 1, 10, "youtube"), *[row for row in rejected if row.platform == "youtube"]]),),
            "instagram": (_batch(50, [*_valid_range("gate-pass-i", 1, 10, "instagram"), *[row for row in rejected if row.platform == "instagram"]]),),
            "tiktok": (_batch(60, [*_valid_range("gate-pass-t", 1, 10, "tiktok"), *[row for row in rejected if row.platform == "tiktok"]]),),
        },
        local, 9, 3, 100, 8, "complete", None, 30, 0,
    )


def default_scenarios() -> list[MockScenario]:
    return [
        _complete_scenario(),
        _duplicate_heavy_scenario(),
        _one_platform_degraded_complete(),
        _one_platform_degraded_shortfall(),
        _provider_budget_scenario(),
        _candidate_budget_scenario(),
        _candidate_exhausted_scenario(),
        _gate_funnel_scenario(),
    ]


def _new_platform_stats() -> dict[str, Any]:
    return {
        "calls_started": 0,
        "calls_succeeded": 0,
        "calls_failed": 0,
        "mock_candidates_returned_raw": 0,
        "candidates_admitted": 0,
        "candidates_ignored_after_target": 0,
        "candidates_ignored_by_limit": 0,
        "terminal_error": False,
        "call_budget_blocked": False,
    }


def _funnel_is_monotonic(funnel: Mapping[str, int]) -> bool:
    keys = (
        "provider_candidates_admitted",
        "not_local_duplicate",
        "canonical_unique_online",
        *[FUNNEL_GATE_FIELDS[gate] for gate in STRICT_GATES],
        "strict_qualified",
        "accepted_online",
    )
    values = [int(funnel.get(key) or 0) for key in keys]
    return all(left >= right for left, right in zip(values, values[1:]))


def simulate_scenario(scenario: MockScenario) -> dict[str, Any]:
    wall_started = time.perf_counter()
    sequence = itertools.count()
    events: list[tuple[int, int, str, Any]] = []
    provider_state = {
        platform: {"next_page": 0, "active": False, "closed": False, **_new_platform_stats()}
        for platform in PLATFORMS
    }
    verifier_available = [0 for _ in range(scenario.verify_parallelism)]
    heapq.heapify(verifier_available)
    local_keys = set(scenario.local_keys)
    seen_online: set[str] = set()
    accepted_keys: set[str] = set()
    rejected_by_gate: Counter[str] = Counter()
    funnel: Counter[str] = Counter()
    duplicate_local = 0
    duplicate_online = 0
    provider_calls = 0
    provider_active = 0
    provider_peak_in_flight = 0
    candidates_admitted = 0
    candidate_limit_blocked = False
    provider_limit_blocked = False
    strict_qualified = 0
    accepted = 0
    ttfq_ms: int | None = None
    t10_ms: int | None = None
    t30_ms: int | None = None

    def start_next(platform: str, started_at_ms: int) -> None:
        nonlocal provider_calls, provider_active, provider_peak_in_flight
        nonlocal provider_limit_blocked, candidate_limit_blocked
        state = provider_state[platform]
        pages = scenario.batches.get(platform) or ()
        if state["closed"] or state["active"] or state["next_page"] >= len(pages):
            if state["next_page"] >= len(pages):
                state["closed"] = True
            return
        if accepted >= 30:
            state["closed"] = True
            return
        if candidates_admitted >= scenario.max_candidates_admitted:
            candidate_limit_blocked = True
            state["closed"] = True
            return
        if (
            provider_calls >= scenario.max_provider_calls
            or state["calls_started"] >= scenario.max_calls_per_platform
        ):
            provider_limit_blocked = True
            state["call_budget_blocked"] = True
            state["closed"] = True
            return
        page_index = int(state["next_page"])
        batch = pages[page_index]
        state["next_page"] = page_index + 1
        state["active"] = True
        state["calls_started"] += 1
        provider_calls += 1
        provider_active += 1
        provider_peak_in_flight = max(provider_peak_in_flight, provider_active)
        heapq.heappush(
            events,
            (started_at_ms + batch.delay_ms, next(sequence), "provider", (platform, batch)),
        )

    for platform in PLATFORMS:
        start_next(platform, 0)

    while events:
        event_time, _order, event_type, payload = heapq.heappop(events)
        if event_type == "provider":
            platform, batch = payload
            state = provider_state[platform]
            state["active"] = False
            provider_active -= 1
            if batch.error_code:
                state["calls_failed"] += 1
                state["terminal_error"] = bool(batch.terminal_error)
                if batch.terminal_error:
                    state["closed"] = True
                else:
                    start_next(platform, event_time)
                continue
            state["calls_succeeded"] += 1
            state["mock_candidates_returned_raw"] += len(batch.rows)
            if accepted >= 30:
                state["candidates_ignored_after_target"] += len(batch.rows)
                state["closed"] = True
                continue
            remaining = max(0, scenario.max_candidates_admitted - candidates_admitted)
            admitted_rows = batch.rows[:remaining]
            ignored = len(batch.rows) - len(admitted_rows)
            if ignored:
                candidate_limit_blocked = True
                state["candidates_ignored_by_limit"] += ignored
            state["candidates_admitted"] += len(admitted_rows)
            for row in admitted_rows:
                candidates_admitted += 1
                funnel["provider_candidates_admitted"] += 1
                if row.key in local_keys:
                    duplicate_local += 1
                    continue
                funnel["not_local_duplicate"] += 1
                if row.key in seen_online:
                    duplicate_online += 1
                    continue
                seen_online.add(row.key)
                funnel["canonical_unique_online"] += 1
                worker_ready = heapq.heappop(verifier_available)
                verified_at = max(event_time, worker_ready) + row.verify_ms
                heapq.heappush(verifier_available, verified_at)
                heapq.heappush(
                    events,
                    (verified_at, next(sequence), "verify", row),
                )
            if ignored:
                state["closed"] = True
            else:
                start_next(platform, event_time)
            continue

        row: MockCandidate = payload
        failed = False
        for gate in STRICT_GATES:
            if row.failed_gate == gate:
                rejected_by_gate[gate] += 1
                failed = True
                break
            funnel[FUNNEL_GATE_FIELDS[gate]] += 1
        if failed:
            continue
        strict_qualified += 1
        funnel["strict_qualified"] += 1
        if accepted >= 30:
            continue
        accepted_keys.add(row.key)
        accepted += 1
        funnel["accepted_online"] += 1
        if accepted == 1:
            ttfq_ms = event_time
        if accepted == 10:
            t10_ms = event_time
        if accepted == 30:
            t30_ms = event_time

    degraded = sorted(
        platform for platform, state in provider_state.items() if state["calls_failed"] > 0
    )
    if accepted >= 30:
        status = "complete"
        shortfall_reason = None
    else:
        status = "shortfall"
        shortfall_reason = (
            "budget_exhausted"
            if provider_limit_blocked or candidate_limit_blocked
            else "candidate_exhausted"
        )
    platform_report: dict[str, Any] = {}
    for platform in PLATFORMS:
        state = provider_state[platform]
        calls = int(state["calls_started"])
        platform_report[platform] = {
            "calls_started": calls,
            "calls_succeeded": int(state["calls_succeeded"]),
            "calls_failed": int(state["calls_failed"]),
            "failure_rate": round(int(state["calls_failed"]) / calls, 4) if calls else 0.0,
            "mock_candidates_returned_raw": int(state["mock_candidates_returned_raw"]),
            "candidates_admitted": int(state["candidates_admitted"]),
            "candidates_ignored_after_target": int(state["candidates_ignored_after_target"]),
            "candidates_ignored_by_limit": int(state["candidates_ignored_by_limit"]),
            "terminal_error": bool(state["terminal_error"]),
            "call_budget_blocked": bool(state["call_budget_blocked"]),
        }
    call_limit_violations = int(provider_calls > scenario.max_provider_calls) + sum(
        int(report["calls_started"] > scenario.max_calls_per_platform)
        for report in platform_report.values()
    )
    candidate_limit_violations = int(candidates_admitted > scenario.max_candidates_admitted)
    result = {
        "scenario_id": scenario.scenario_id,
        "status": status,
        "shortfall_reason": shortfall_reason,
        "degradation_reasons": [f"{platform}:mock_provider_failure" for platform in degraded],
        "counts": {
            "local_unique": len(local_keys),
            "online_net_new": accepted,
            "online_shortfall": max(0, 30 - accepted),
            "unique60": len(local_keys | accepted_keys),
            "strict_qualified_before_target_cap": strict_qualified,
            "duplicate_local": duplicate_local,
            "duplicate_online": duplicate_online,
        },
        "timings_ms": {
            "basis": "deterministic_concurrent_discrete_event_clock",
            "ttfq": ttfq_ms,
            "t10_online": t10_ms,
            "t30_online": t30_ms,
            "harness_wall_clock": round((time.perf_counter() - wall_started) * 1000.0, 3),
        },
        "funnel": {key: int(value) for key, value in sorted(funnel.items())},
        "funnel_monotonic": _funnel_is_monotonic(funnel),
        "rejected_by_gate": {
            gate: int(rejected_by_gate.get(gate) or 0) for gate in STRICT_GATES
        },
        "provider": {
            "mock_calls_started": provider_calls,
            "mock_calls_succeeded": sum(row["calls_succeeded"] for row in platform_report.values()),
            "mock_calls_failed": sum(row["calls_failed"] for row in platform_report.values()),
            "failure_rate": round(
                sum(row["calls_failed"] for row in platform_report.values()) / provider_calls,
                4,
            ) if provider_calls else 0.0,
            "peak_in_flight": provider_peak_in_flight,
            "platforms": platform_report,
        },
        "limits": {
            "max_provider_calls": scenario.max_provider_calls,
            "max_calls_per_platform": scenario.max_calls_per_platform,
            "max_candidates_admitted": scenario.max_candidates_admitted,
            "provider_call_budget_blocked": provider_limit_blocked,
            "candidate_budget_blocked": candidate_limit_blocked,
            "provider_call_limit_violations": call_limit_violations,
            "candidate_limit_violations": candidate_limit_violations,
        },
        "verification": {
            "mode": "strict_fast_verify_mock",
            "parallelism": scenario.verify_parallelism,
            "strict_gate_names": list(STRICT_GATES),
            "canonical_unique_verified": int(funnel.get("canonical_unique_online") or 0),
        },
    }
    result["expected"] = {
        "status": scenario.expected_status,
        "shortfall_reason": scenario.expected_shortfall_reason,
        "online_net_new": scenario.expected_online_net_new,
        "degraded_platform_count": scenario.expected_degraded_platforms,
    }
    result["scenario_pass"] = bool(
        status == scenario.expected_status
        and shortfall_reason == scenario.expected_shortfall_reason
        and accepted == scenario.expected_online_net_new
        and len(degraded) == scenario.expected_degraded_platforms
        and call_limit_violations == 0
        and candidate_limit_violations == 0
        and result["funnel_monotonic"]
        and result["counts"]["unique60"] == 30 + accepted
    )
    return result


def _deterministic_projection(result: Mapping[str, Any]) -> dict[str, Any]:
    projected = json.loads(json.dumps(result))
    projected["timings_ms"].pop("harness_wall_clock", None)
    return projected


def summarize_scenario(scenario: MockScenario, runs: Sequence[dict[str, Any]]) -> dict[str, Any]:
    if not runs:
        raise ValueError("scenario_runs_required")
    first = runs[0]
    deterministic_stable = all(
        _deterministic_projection(run) == _deterministic_projection(first)
        for run in runs[1:]
    )
    summary = _deterministic_projection(first)
    summary["runs"] = len(runs)
    summary["deterministic_outcome_stable"] = deterministic_stable
    summary["timings_ms"] = {
        "basis": "deterministic_concurrent_discrete_event_clock",
        "ttfq": _stats([run["timings_ms"]["ttfq"] for run in runs]),
        "t10_online": _stats([run["timings_ms"]["t10_online"] for run in runs]),
        "t30_online": _stats([run["timings_ms"]["t30_online"] for run in runs]),
        "harness_wall_clock": _stats(
            [run["timings_ms"]["harness_wall_clock"] for run in runs]
        ),
    }
    summary["scenario_pass"] = bool(
        deterministic_stable and all(run["scenario_pass"] for run in runs)
    )
    return summary


def _assert_private_report(value: Any, *, path: str = "$") -> None:
    if isinstance(value, dict):
        for key, child in value.items():
            if str(key).lower() in FORBIDDEN_REPORT_KEYS:
                raise ValueError(f"identity_or_contact_key_forbidden:{path}.{key}")
            _assert_private_report(child, path=f"{path}.{key}")
        return
    if isinstance(value, list):
        for index, child in enumerate(value):
            _assert_private_report(child, path=f"{path}[{index}]")
        return
    if isinstance(value, str):
        if EMAIL_RE.search(value):
            raise ValueError(f"email_value_forbidden:{path}")
        if CONTACT_ROUTE_RE.search(value):
            raise ValueError(f"contact_route_forbidden:{path}")


def run_benchmark(*, runs_per_scenario: int = 3) -> dict[str, Any]:
    if runs_per_scenario < 1 or runs_per_scenario > 20:
        raise ValueError("runs_per_scenario_must_be_between_1_and_20")
    started = time.perf_counter()
    scenarios = default_scenarios()
    summaries: list[dict[str, Any]] = []
    raw_runs: list[dict[str, Any]] = []
    for scenario in scenarios:
        runs = [simulate_scenario(scenario) for _run in range(runs_per_scenario)]
        raw_runs.extend(runs)
        summaries.append(summarize_scenario(scenario, runs))
    total_calls = sum(int(run["provider"]["mock_calls_started"]) for run in raw_runs)
    failed_calls = sum(int(run["provider"]["mock_calls_failed"]) for run in raw_runs)
    complete_runs = [run for run in raw_runs if run["status"] == "complete"]
    report: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "claim_status": "synthetic_mock_only",
        "source_state": {
            "benchmark_script": "scripts/benchmark_kol_online_mock.py",
            "benchmark_script_sha256": hashlib.sha256(
                Path(__file__).resolve().read_bytes()
            ).hexdigest(),
            "fixture_version": "online_mock_matrix_v1",
        },
        "scope": {
            "three_platform_concurrent_discovery_simulated": True,
            "timing_basis": "deterministic_concurrent_discrete_event_clock",
            "real_provider_calls": 0,
            "mock_provider_calls_only": True,
            "external_network_accessed": False,
            "business_database_accessed": False,
            "business_database_writes": 0,
            "production_http_tested": False,
            "production_backend_adapter_tested": False,
            "production_worker_tested": False,
            "production_ui_tested": False,
            "real_world_supply_tested": False,
            "human_precision_at_30": "not_evaluated_synthetic_fixture",
            "creator_identity_values_emitted": False,
            "contact_values_emitted": False,
        },
        "configuration": {
            "scenario_count": len(scenarios),
            "runs_per_scenario": runs_per_scenario,
            "scenario_run_count": len(raw_runs),
            "platforms": list(PLATFORMS),
            "local_unique_target": 30,
            "online_net_new_target": 30,
            "global_unique_target": 60,
            "strict_gate_names": list(STRICT_GATES),
            "strict_gate_count": len(STRICT_GATES),
        },
        "scenarios": summaries,
        "aggregate": {
            "scenario_count": len(summaries),
            "scenario_pass_count": sum(bool(row["scenario_pass"]) for row in summaries),
            "complete_scenario_count": sum(row["status"] == "complete" for row in summaries),
            "shortfall_scenario_count": sum(row["status"] == "shortfall" for row in summaries),
            "net_new_30_scenario_count": sum(int(row["counts"]["online_net_new"]) == 30 for row in summaries),
            "unique60_scenario_count": sum(int(row["counts"]["unique60"]) == 60 for row in summaries),
            "provider_call_limit_violation_count": sum(int(run["limits"]["provider_call_limit_violations"]) for run in raw_runs),
            "candidate_limit_violation_count": sum(int(run["limits"]["candidate_limit_violations"]) for run in raw_runs),
            "total_mock_provider_calls_across_runs": total_calls,
            "total_mock_provider_failures_across_runs": failed_calls,
            "weighted_mock_provider_failure_rate": round(failed_calls / total_calls, 4) if total_calls else 0.0,
            "timings_ms": {
                "basis": "deterministic_concurrent_discrete_event_clock",
                "ttfq": _stats([run["timings_ms"]["ttfq"] for run in raw_runs]),
                "t10_online": _stats([run["timings_ms"]["t10_online"] for run in raw_runs]),
                "t30_online_complete_only": _stats(
                    [run["timings_ms"]["t30_online"] for run in complete_runs]
                ),
                "harness_wall_clock_per_scenario": _stats(
                    [run["timings_ms"]["harness_wall_clock"] for run in raw_runs]
                ),
            },
            "benchmark_wall_clock_ms": round((time.perf_counter() - started) * 1000.0, 3),
        },
        "privacy_receipt": {
            "aggregate_only_report": True,
            "creator_identity_values_serialized": False,
            "contact_values_serialized": False,
            "forbidden_key_scan_passed": True,
            "email_pattern_hits": 0,
            "contact_route_pattern_hits": 0,
        },
        "required_caveats": [
            "All provider responses, candidates, failures and latencies are deterministic synthetic mocks.",
            "TTFQ, T10 and T30-online use a simulated completion clock, not observed internet or provider latency.",
            "No real provider, network, business database, production worker, HTTP middleware or UI was exercised.",
            "Net-new 30 and unique 60 prove orchestration semantics only; they do not prove real-world KOL supply or accuracy.",
            "Human relevance precision@30 and contact availability are not evaluated by this fixture.",
        ],
    }
    _assert_private_report(report)
    report["report_sha256_without_digest"] = hashlib.sha256(
        json.dumps(report, ensure_ascii=False, sort_keys=True).encode("utf-8")
    ).hexdigest()
    return report


def _validate_output_path(path: Path) -> Path:
    absolute = Path(os.path.abspath(os.fspath(path)))
    try:
        mode = os.lstat(absolute).st_mode
    except FileNotFoundError:
        return absolute
    if stat.S_ISLNK(mode):
        raise ValueError("output_symlink_forbidden")
    if not stat.S_ISREG(mode):
        raise ValueError("output_must_be_regular_file")
    return absolute


def write_report(path: Path, report: Mapping[str, Any]) -> None:
    _assert_private_report(report)
    destination = _validate_output_path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{destination.name}.", dir=str(destination.parent)
    )
    temporary = Path(temporary_name)
    try:
        os.fchmod(descriptor, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(report, handle, ensure_ascii=False, indent=2, sort_keys=True)
            handle.write("\n")
        os.replace(temporary, destination)
        os.chmod(destination, 0o600)
    finally:
        temporary.unlink(missing_ok=True)


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--runs", type=int, default=3)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args(argv)
    if args.runs < 1 or args.runs > 20:
        parser.error("--runs must be between 1 and 20")
    return args


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    output = _validate_output_path(args.output)
    report = run_benchmark(runs_per_scenario=args.runs)
    write_report(output, report)
    out_json({
        "status": "ok" if report["aggregate"]["scenario_pass_count"] == report["aggregate"]["scenario_count"] else "failed",
        "output": str(output),
        "claim_status": report["claim_status"],
        "scenario_pass_count": report["aggregate"]["scenario_pass_count"],
        "scenario_count": report["aggregate"]["scenario_count"],
        "real_provider_calls": 0,
        "business_database_writes": 0,
    }, ensure_ascii=False, sort_keys=True)
    return 0 if report["aggregate"]["scenario_pass_count"] == report["aggregate"]["scenario_count"] else 2


if __name__ == "__main__":
    sys.exit(main())
