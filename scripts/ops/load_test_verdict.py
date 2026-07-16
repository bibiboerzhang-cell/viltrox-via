from __future__ import annotations

from scripts.ops.load_test_workload import *

def _relative_spread(summary: Mapping[str, Any] | None) -> float | None:
    if not isinstance(summary, Mapping):
        return None
    minimum = summary.get("min")
    middle = summary.get("median")
    maximum = summary.get("max")
    if not all(_is_number(value) for value in (minimum, middle, maximum)):
        return None
    middle_value = float(middle)
    if middle_value <= 0.0:
        return None
    return max(0.0, (float(maximum) - float(minimum)) / middle_value)


def _measured_threshold_failure(stage: Mapping[str, Any]) -> bool:
    if stage.get("threshold_pass") is not False:
        return False
    trial_count = stage.get("trial_count")
    total_requests = stage.get("total_requests")
    trials = stage.get("trials")
    reasons = stage.get("stop_reasons")
    if not isinstance(trial_count, int) or isinstance(trial_count, bool) or trial_count < 1:
        return False
    if not isinstance(total_requests, int) or isinstance(total_requests, bool) or total_requests <= 0:
        return False
    if not isinstance(trials, Sequence) or isinstance(trials, (str, bytes)) or not trials:
        return False
    if not all(
        isinstance(trial, Mapping)
        and isinstance(trial.get("total_requests"), int)
        and not isinstance(trial.get("total_requests"), bool)
        and int(trial.get("total_requests")) > 0
        for trial in trials
    ):
        return False
    if not isinstance(reasons, Sequence) or isinstance(reasons, (str, bytes)) or not reasons:
        return False
    allowed = {"error_rate", "p95_latency", "p99_latency", "server_5xx"}
    return all(
        isinstance(reason, str)
        and (reason in allowed or reason.startswith("endpoint:"))
        for reason in reasons
    )


def detect_saturation_breakpoint(
    stages: Sequence[Mapping[str, Any]],
) -> tuple[Mapping[str, Any] | None, dict[str, Any] | None]:
    """Return the conservative stage before a measured threshold/knee breakpoint."""
    passing_before: list[Mapping[str, Any]] = []
    for stage in stages:
        if not bool(stage.get("threshold_pass")):
            if not _measured_threshold_failure(stage):
                return None, {
                    "observed": False,
                    "kind": None,
                    "breakpoint_simulated_active_sessions": None,
                    "candidate_simulated_active_sessions": None,
                    "reason": "failed tier lacks measured requests, trials, or explicit threshold evidence",
                }
            candidate = passing_before[-1] if passing_before else None
            return candidate, {
                "observed": True,
                "kind": "threshold_failure",
                "breakpoint_simulated_active_sessions": int(stage.get("virtual_users") or 0),
                "candidate_simulated_active_sessions": (
                    int(candidate.get("virtual_users") or 0) if candidate is not None else None
                ),
                "reasons": list(stage.get("stop_reasons") or []),
            }
        passing_before.append(stage)

    for previous, current in zip(stages, stages[1:]):
        if not all(
            isinstance(stage.get("total_requests"), int)
            and not isinstance(stage.get("total_requests"), bool)
            and int(stage.get("total_requests")) > 0
            and isinstance(stage.get("trials"), Sequence)
            and not isinstance(stage.get("trials"), (str, bytes))
            and bool(stage.get("trials"))
            for stage in (previous, current)
        ):
            continue
        previous_vu = int(previous.get("virtual_users") or 0)
        current_vu = int(current.get("virtual_users") or 0)
        previous_rps = float(previous.get("requests_per_second") or 0.0)
        current_rps = float(current.get("requests_per_second") or 0.0)
        previous_p95 = float((previous.get("latency_ms") or {}).get("p95") or 0.0)
        current_p95 = float((current.get("latency_ms") or {}).get("p95") or 0.0)
        vu_multiplier = (current_vu / previous_vu) if previous_vu > 0 else 0.0
        rps_gain = ((current_rps - previous_rps) / previous_rps) if previous_rps > 0 else 0.0
        latency_multiplier = (current_p95 / previous_p95) if previous_p95 > 0 else 0.0
        if vu_multiplier >= 1.5 and rps_gain <= 0.20 and latency_multiplier >= 1.50:
            return previous, {
                "observed": True,
                "kind": "throughput_latency_knee",
                "breakpoint_simulated_active_sessions": current_vu,
                "candidate_simulated_active_sessions": previous_vu,
                "vu_multiplier": round(vu_multiplier, 6),
                "rps_relative_gain": round(rps_gain, 6),
                "p95_latency_multiplier": round(latency_multiplier, 6),
            }
    return None, {
        "observed": False,
        "kind": None,
        "breakpoint_simulated_active_sessions": None,
        "candidate_simulated_active_sessions": None,
        "reason": "all tested tiers remain below a measured threshold or saturation knee",
    }


def _candidate_resource_evidence(candidate: Mapping[str, Any] | None) -> dict[str, Any]:
    trials = candidate.get("trials") if isinstance(candidate, Mapping) else []
    if not isinstance(trials, Sequence) or isinstance(trials, (str, bytes)):
        trials = []
    trial_checks: list[dict[str, Any]] = []
    for index, trial in enumerate(trials):
        telemetry = trial.get("resource_telemetry") if isinstance(trial, Mapping) else None
        summary = telemetry.get("summary") if isinstance(telemetry, Mapping) else None
        summary = summary if isinstance(summary, Mapping) else {}
        adapters = summary.get("optional_adapters")
        adapters = adapters if isinstance(adapters, Mapping) else {}
        db_pool = adapters.get("db_pool") if isinstance(adapters.get("db_pool"), Mapping) else {}
        redis = adapters.get("redis") if isinstance(adapters.get("redis"), Mapping) else {}
        listener_coverage = summary.get("listener_process_coverage")
        listener_coverage = listener_coverage if isinstance(listener_coverage, Mapping) else {}
        check = {
            "trial_index": int(trial.get("trial_index") or index) if isinstance(trial, Mapping) else index,
            "resource_sample_count": int(summary.get("sample_count") or 0),
            "process_metrics_available": bool(summary.get("process_metrics_available")),
            "all_target_listener_processes_available": listener_coverage.get("pass") is True,
            "db_pool_sidecar_available": bool(db_pool.get("available")),
            "redis_sidecar_available": bool(redis.get("available")),
            "db_pool_all_samples_bound": db_pool.get("all_samples_fresh_bound_and_advancing") is True,
            "redis_all_samples_bound": redis.get("all_samples_fresh_bound_and_advancing") is True,
            "db_pool_independent_producer_attested": (
                db_pool.get("all_samples_trusted_independent_producer") is True
            ),
            "redis_independent_producer_attested": (
                redis.get("all_samples_trusted_independent_producer") is True
            ),
        }
        check["pass"] = (
            check["resource_sample_count"] >= 3
            and check["process_metrics_available"]
            and check["all_target_listener_processes_available"]
            and check["db_pool_sidecar_available"]
            and check["redis_sidecar_available"]
            and check["db_pool_all_samples_bound"]
            and check["redis_all_samples_bound"]
            and check["db_pool_independent_producer_attested"]
            and check["redis_independent_producer_attested"]
        )
        trial_checks.append(check)
    return {
        "pass": len(trial_checks) >= MIN_CAPACITY_TRIALS and all(item["pass"] for item in trial_checks),
        "trials": trial_checks,
        "required": {
            "minimum_trials": MIN_CAPACITY_TRIALS,
            "minimum_resource_samples_per_trial": 3,
            "listener_process_metrics_for_every_target_service_and_sample": True,
            "db_pool_sidecar": True,
            "redis_sidecar": True,
            "code_allowlisted_independent_telemetry_producer_attestation_for_every_sample": True,
        },
    }


def _finite_bounds(value: Any) -> tuple[float, float, float] | None:
    if not isinstance(value, Mapping):
        return None
    values = (value.get("min"), value.get("median"), value.get("max"))
    if not all(_is_number(item) for item in values):
        return None
    numbers = tuple(float(item) for item in values)
    if not numbers[0] <= numbers[1] <= numbers[2]:
        return None
    return numbers


def _status_evidence_valid(statuses: Any, *, minimum_requests: float) -> bool:
    if not isinstance(statuses, Mapping) or not statuses:
        return False
    total = 0
    for code, count in statuses.items():
        if not str(code).isdigit() or not (100 <= int(code) <= 599):
            return False
        if not isinstance(count, int) or isinstance(count, bool) or count < 0:
            return False
        total += count
    return total >= max(1, math.ceil(minimum_requests))


def _trial_endpoint_evidence_valid(group: Any, budget: Thresholds) -> bool:
    if not isinstance(group, Mapping):
        return False
    requests = group.get("requests")
    error_rate = group.get("error_rate")
    p95 = group.get("p95_ms")
    p99 = group.get("p99_ms")
    if not all(_is_number(value) for value in (requests, error_rate, p95, p99)):
        return False
    request_count = float(requests)
    error_value = float(error_rate)
    p95_value = float(p95)
    p99_value = float(p99)
    if not (
        request_count > 0.0
        and 0.0 <= error_value <= budget.max_error_rate
        and 0.0 <= p95_value <= p99_value
        and p95_value <= budget.max_p95_ms
        and p99_value <= budget.max_p99_ms
    ):
        return False
    statuses = group.get("status_codes")
    if not _status_evidence_valid(statuses, minimum_requests=request_count):
        return False
    return not any(int(code) >= 500 and int(count) > 0 for code, count in statuses.items())


def _aggregate_endpoint_evidence_valid(group: Any, budget: Thresholds) -> bool:
    if not isinstance(group, Mapping):
        return False
    trial_count = group.get("trial_count")
    if not isinstance(trial_count, int) or isinstance(trial_count, bool):
        return False
    if trial_count < MIN_CAPACITY_TRIALS:
        return False
    requests = _finite_bounds(group.get("requests"))
    errors = _finite_bounds(group.get("error_rate"))
    p95 = _finite_bounds(group.get("p95_ms"))
    p99 = _finite_bounds(group.get("p99_ms"))
    if None in (requests, errors, p95, p99):
        return False
    assert requests is not None and errors is not None and p95 is not None and p99 is not None
    if not (
        requests[0] > 0.0
        and 0.0 <= errors[0] <= errors[2] <= budget.max_error_rate
        and 0.0 <= p95[0] <= p95[2] <= budget.max_p95_ms
        and 0.0 <= p99[0] <= p99[2] <= budget.max_p99_ms
        and all(left <= right for left, right in zip(p95, p99))
    ):
        return False
    statuses = group.get("status_codes")
    minimum_total = requests[0] * trial_count
    if not _status_evidence_valid(statuses, minimum_requests=minimum_total):
        return False
    return not any(int(code) >= 500 and int(count) > 0 for code, count in statuses.items())


def _candidate_endpoint_evidence(
    candidate: Mapping[str, Any] | None,
    endpoint_thresholds: Mapping[str, Thresholds] | None,
) -> dict[str, Any]:
    required = sorted(endpoint_thresholds or {})
    across = candidate.get("across_trials") if isinstance(candidate, Mapping) else None
    across = across if isinstance(across, Mapping) else {}
    aggregate_groups = across.get("by_endpoint")
    aggregate_groups = aggregate_groups if isinstance(aggregate_groups, Mapping) else {}
    trials = candidate.get("trials") if isinstance(candidate, Mapping) else None
    trials = trials if isinstance(trials, Sequence) and not isinstance(trials, (str, bytes)) else []
    aggregate_failures: list[str] = []
    trial_failures: list[dict[str, Any]] = []
    for endpoint_name in required:
        budget = endpoint_thresholds[endpoint_name]  # type: ignore[index]
        if not _aggregate_endpoint_evidence_valid(aggregate_groups.get(endpoint_name), budget):
            aggregate_failures.append(endpoint_name)
        for trial_index, trial in enumerate(trials):
            groups = trial.get("by_endpoint") if isinstance(trial, Mapping) else None
            groups = groups if isinstance(groups, Mapping) else {}
            if not _trial_endpoint_evidence_valid(groups.get(endpoint_name), budget):
                trial_failures.append({"trial_index": trial_index, "endpoint": endpoint_name})
    return {
        "pass": bool(required)
        and len(trials) >= MIN_CAPACITY_TRIALS
        and not aggregate_failures
        and not trial_failures,
        "required_endpoints": required,
        "aggregate_failures": aggregate_failures,
        "trial_failures": trial_failures,
        "minimum_positive_requests_per_endpoint_per_trial": 1,
        "finite_error_p95_p99_and_status_evidence_required": True,
    }


def _fail_closed_capacity_verdict_validated(
    stages: Sequence[Mapping[str, Any]],
    *,
    endpoint_thresholds: Mapping[str, Thresholds] | None,
    calibration_manifest: Mapping[str, Any] | None,
    identity_fidelity: Mapping[str, Any] | None,
    performance_evidence: Any,
) -> dict[str, Any]:
    """Qualify a human-seat load estimate only when every evidence gate passes."""
    candidate, breakpoint = detect_saturation_breakpoint(stages)
    across = candidate.get("across_trials") if isinstance(candidate, Mapping) else None
    across = across if isinstance(across, Mapping) else {}
    endpoint_groups = across.get("by_endpoint")
    endpoint_groups = endpoint_groups if isinstance(endpoint_groups, Mapping) else {}
    required_endpoint_names = sorted(endpoint_thresholds or {})
    missing_endpoints = [name for name in required_endpoint_names if name not in endpoint_groups]
    endpoint_reasons = endpoint_stop_reasons(candidate or {}, endpoint_thresholds)
    strict_endpoint_evidence = _candidate_endpoint_evidence(candidate, endpoint_thresholds)
    endpoint_trials_complete = all(
        isinstance(endpoint_groups.get(name), Mapping)
        and int(endpoint_groups[name].get("trial_count") or 0) >= MIN_CAPACITY_TRIALS
        for name in required_endpoint_names
    )

    trials = candidate.get("trials") if isinstance(candidate, Mapping) else []
    trials = trials if isinstance(trials, Sequence) and not isinstance(trials, (str, bytes)) else []
    trial_count = int(candidate.get("trial_count") or 0) if isinstance(candidate, Mapping) else 0
    trial_rows_valid = bool(trials) and all(isinstance(trial, Mapping) for trial in trials)
    trial_thresholds_pass = (
        len(trials) >= MIN_CAPACITY_TRIALS
        and trial_rows_valid
        and all(bool(trial.get("threshold_pass")) for trial in trials)
    )
    rps_spread = _relative_spread(across.get("requests_per_second") if isinstance(across, Mapping) else None)
    latency = across.get("latency_ms") if isinstance(across.get("latency_ms"), Mapping) else {}
    p95_spread = _relative_spread(latency.get("p95") if isinstance(latency, Mapping) else None)
    repeatable = (
        trial_count >= MIN_CAPACITY_TRIALS
        and len(trials) >= MIN_CAPACITY_TRIALS
        and trial_thresholds_pass
        and rps_spread is not None
        and p95_spread is not None
        and rps_spread <= MAX_TRIAL_RPS_RELATIVE_SPREAD
        and p95_spread <= MAX_TRIAL_P95_RELATIVE_SPREAD
    )
    trial_duration_ok = (
        bool(trials)
        and float(candidate.get("duration_seconds") or 0.0) >= MIN_CAPACITY_TRIAL_SECONDS
        and all(
            str(trial.get("termination_reason") or "") == "duration_elapsed"
            and float(trial.get("elapsed_seconds") or 0.0) >= MIN_CAPACITY_TRIAL_SECONDS * 0.90
            for trial in trials
            if isinstance(trial, Mapping)
        )
    )
    resource_evidence = _candidate_resource_evidence(candidate)
    identity = identity_fidelity if isinstance(identity_fidelity, Mapping) else {}
    stage_vus = [
        int(stage.get("virtual_users") or 0)
        for stage in stages
        if isinstance(stage, Mapping)
    ]
    stage_order_ok = (
        len(stage_vus) == len(stages)
        and bool(stage_vus)
        and all(value > 0 for value in stage_vus)
        and all(current > previous for previous, current in zip(stage_vus, stage_vus[1:]))
    )
    candidate_vu = int(candidate.get("virtual_users") or 0) if candidate is not None else 0
    breakpoint_vu = int((breakpoint or {}).get("breakpoint_simulated_active_sessions") or 0)
    breakpoint_order_ok = bool(
        candidate_vu > 0 and breakpoint_vu > candidate_vu
    )
    max_tested_vu = max(stage_vus, default=0)
    session_count = identity.get("independent_http_session_count")
    identity_count = identity.get("distinct_auth_identity_count")
    reported_max_vu = identity.get("max_tested_simulated_active_sessions")
    counts_valid = all(
        isinstance(value, int) and not isinstance(value, bool) and value >= 0
        for value in (session_count, identity_count, reported_max_vu)
    )
    identity_ok = (
        identity.get("identity_preflight_pass") is True
        and bool(identity.get("one_independent_http_session_per_tested_vu"))
        and bool(identity.get("one_distinct_auth_identity_per_tested_vu"))
        and identity.get("organization_count") == 1
        and identity.get("raw_principals_persisted") is False
        and identity.get("tokens_persisted") is False
        and counts_valid
        and reported_max_vu == max_tested_vu
        and session_count >= max_tested_vu
        and identity_count >= max_tested_vu
        and max_tested_vu > 0
    )
    calibration = calibration_manifest if isinstance(calibration_manifest, Mapping) else {}
    producer_attestation = calibration.get("producer_attestation")
    producer_attestation = (
        producer_attestation if isinstance(producer_attestation, Mapping) else {}
    )
    source_contract = calibration.get("source")
    source_contract = source_contract if isinstance(source_contract, Mapping) else {}
    calibration_in_process_verified = _is_in_process_verified_calibration(calibration)
    calibration_ok = bool(
        calibration_in_process_verified
        and calibration.get("status") == "qualified"
        and calibration.get("consistency_status") == "internally_consistent"
        and calibration.get("trust_status") == "trusted_measured_evidence"
        and calibration.get("eligible") is True
        and calibration.get("human_seat_conversion_allowed") is True
        and source_contract.get("authenticity") == "trusted_producer_attested"
        and producer_attestation.get("status") == "trusted_producer_attestation"
        and producer_attestation.get("trusted") is True
        and producer_attestation.get("signature_valid") is True
        and producer_attestation.get("signer_allowlisted") is True
        and producer_attestation.get("source_binding_valid") is True
        and producer_attestation.get("time_binding_valid") is True
    )

    live_performance_verified_in_process = _is_verified_live_stage_bundle(
        performance_evidence,
        stages,
    )
    gates = {
        "live_performance_evidence": {
            "pass": live_performance_verified_in_process,
            "observed": {
                "in_process_live_execution_capability": live_performance_verified_in_process,
            },
            "required": (
                "unserialized in-process capability bound to the unmodified canonical live stage bundle"
            ),
        },
        "calibration_manifest": {
            "pass": calibration_ok,
            "observed": {
                "status": calibration.get("status") or "not_configured",
                "consistency_status": calibration.get("consistency_status"),
                "trust_status": calibration.get("trust_status"),
                "source_authenticity": source_contract.get("authenticity"),
                "producer_attestation_status": producer_attestation.get("status"),
                "in_process_verified_and_unmodified": calibration_in_process_verified,
            },
            "required": (
                "qualified internally-consistent measured evidence with an allowlisted "
                "Ed25519 producer attestation, derived and consumed unmodified in-process"
            ),
        },
        "saturation_breakpoint": {
            "pass": bool(
                candidate is not None
                and breakpoint
                and breakpoint.get("observed")
                and stage_order_ok
                and breakpoint_order_ok
            ),
            "observed": breakpoint,
            "required": "threshold failure or throughput/latency knee after a passing tier",
        },
        "tier_order": {
            "pass": stage_order_ok and breakpoint_order_ok,
            "observed": {
                "simulated_active_session_tiers": stage_vus,
                "candidate": candidate_vu or None,
                "breakpoint": breakpoint_vu or None,
            },
            "required": "strictly increasing positive tiers and a breakpoint above the candidate tier",
        },
        "endpoint_thresholds": {
            "pass": bool(
                candidate is not None
                and required_endpoint_names
                and not missing_endpoints
                and endpoint_trials_complete
                and not endpoint_reasons
                and strict_endpoint_evidence.get("pass") is True
                and bool(candidate.get("threshold_pass"))
            ),
            "observed": {
                "required_endpoints": required_endpoint_names,
                "missing_endpoints": missing_endpoints,
                "failed_reasons": endpoint_reasons,
                "three_trial_endpoint_coverage": endpoint_trials_complete,
                "strict_evidence": strict_endpoint_evidence,
            },
            "required": "every required endpoint present in >=3 trials and within its own budget",
        },
        "three_trial_consistency": {
            "pass": repeatable,
            "observed": {
                "trial_count": trial_count,
                "all_trial_thresholds_pass": trial_thresholds_pass,
                "all_trial_rows_structured": trial_rows_valid,
                "rps_relative_spread": round(rps_spread, 6) if rps_spread is not None else None,
                "p95_relative_spread": round(p95_spread, 6) if p95_spread is not None else None,
            },
            "required": {
                "minimum_trials": MIN_CAPACITY_TRIALS,
                "maximum_rps_relative_spread": MAX_TRIAL_RPS_RELATIVE_SPREAD,
                "maximum_p95_relative_spread": MAX_TRIAL_P95_RELATIVE_SPREAD,
            },
        },
        "trial_duration": {
            "pass": trial_duration_ok,
            "observed": {
                "configured_duration_seconds": candidate.get("duration_seconds") if isinstance(candidate, Mapping) else None,
                "trial_elapsed_seconds": [
                    trial.get("elapsed_seconds") for trial in trials if isinstance(trial, Mapping)
                ],
                "termination_reasons": [
                    trial.get("termination_reason") for trial in trials if isinstance(trial, Mapping)
                ],
            },
            "required": {
                "minimum_seconds_per_trial": MIN_CAPACITY_TRIAL_SECONDS,
                "termination_reason": "duration_elapsed",
            },
        },
        "resource_sidecars": resource_evidence,
        "identity_fidelity": {
            "pass": identity_ok,
            "observed": dict(identity),
            "required": {
                "one_independent_http_session_per_tested_vu": True,
                "one_distinct_auth_identity_per_tested_vu": True,
                "session_and_identity_counts_at_least_max_tested_vu": max_tested_vu,
            },
        },
    }
    failure_reasons = sorted(name for name, gate in gates.items() if not bool(gate.get("pass")))
    qualified = not failure_reasons
    human_estimate: dict[str, Any] | None = None
    if qualified and candidate is not None:
        rate = calibration.get("aggregate_request_rate_per_active_minute")
        rate = rate if isinstance(rate, Mapping) else {}
        lower_rate = float(rate.get("lower") or 0.0)
        point_rate = float(rate.get("point") or 0.0)
        upper_rate = float(rate.get("upper") or 0.0)
        rps_bounds = across.get("requests_per_second")
        rps_bounds = rps_bounds if isinstance(rps_bounds, Mapping) else {}
        sustainable_rps = float(rps_bounds.get("min") or 0.0) * CAPACITY_SAFETY_FACTOR
        if sustainable_rps > 0.0 and 0.0 < lower_rate <= point_rate <= upper_rate:
            lower_seats = math.floor(sustainable_rps * 60.0 / upper_rate)
            point_seats = math.floor(sustainable_rps * 60.0 / point_rate)
            upper_seats = math.floor(sustainable_rps * 60.0 / lower_rate)
            human_estimate = {
                "metric": "active_human_seat_load_equivalent",
                "lower": lower_seats,
                "point": point_seats,
                "upper": upper_seats,
                "confidence_level": rate.get("confidence_level"),
                "capacity_safety_factor": CAPACITY_SAFETY_FACTOR,
                "minimum_repeat_rps_before_safety_factor": rps_bounds.get("min"),
                "sustainable_rps_basis": round(sustainable_rps, 6),
                "calibrated_requests_per_active_minute": dict(rate),
                "accepted_simulated_active_sessions": int(candidate.get("virtual_users") or 0),
                "boundary": (
                    "load-equivalent active seats for this calibrated read-only journey only; "
                    "not total employees, licensed accounts, write/provider capacity, or cloud capacity"
                ),
            }
        else:
            failure_reasons.append("human_seat_formula_inputs")
            qualified = False

    return {
        "status": "qualified" if qualified else "unqualified",
        "claim_status": "bounded_load_equivalent" if qualified else "descriptive_only",
        "capacity_claim_allowed": qualified,
        "human_seat_estimate": human_estimate if qualified else None,
        "candidate_simulated_active_sessions": (
            int(candidate.get("virtual_users") or 0) if candidate is not None else None
        ),
        "saturation_breakpoint": breakpoint,
        "gates": gates,
        "failure_reasons": sorted(set(failure_reasons)),
        "fail_closed": True,
    }


def _invalid_capacity_verdict(error_type: str) -> dict[str, Any]:
    return {
        "status": "unqualified",
        "input_status": "invalid_input",
        "claim_status": "descriptive_only",
        "capacity_claim_allowed": False,
        "human_seat_estimate": None,
        "candidate_simulated_active_sessions": None,
        "saturation_breakpoint": None,
        "gates": {
            "input_contract": {
                "pass": False,
                "observed": {"error_type": error_type},
                "required": "finite, structurally valid canonical stage evidence",
            }
        },
        "failure_reasons": ["invalid_input"],
        "fail_closed": True,
    }


def fail_closed_capacity_verdict(
    stages: Sequence[Mapping[str, Any]],
    *,
    endpoint_thresholds: Mapping[str, Thresholds] | None,
    calibration_manifest: Mapping[str, Any] | None,
    identity_fidelity: Mapping[str, Any] | None,
    performance_evidence: Any,
) -> dict[str, Any]:
    """Never raise on evidence input; malformed data is an unqualified verdict."""
    try:
        if not isinstance(stages, Sequence) or isinstance(stages, (str, bytes)):
            raise TypeError("stages must be a sequence")
        if not all(isinstance(stage, Mapping) for stage in stages):
            raise TypeError("every stage must be an object")
        _canonical_json_sha256(list(stages))
        return _fail_closed_capacity_verdict_validated(
            stages,
            endpoint_thresholds=endpoint_thresholds,
            calibration_manifest=calibration_manifest,
            identity_fidelity=identity_fidelity,
            performance_evidence=performance_evidence,
        )
    except Exception as exc:  # noqa: BLE001 - fail-closed API boundary
        return _invalid_capacity_verdict(type(exc).__name__)

__all__ = [name for name in globals() if not name.startswith("__")]
