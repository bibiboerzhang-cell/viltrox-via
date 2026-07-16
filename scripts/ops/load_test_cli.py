from __future__ import annotations
import sys as _stdout_sys
from pathlib import Path as _StdoutPath

_STDOUT_UTILS_DIR = _StdoutPath(__file__).resolve().parents[1]
if str(_STDOUT_UTILS_DIR) not in _stdout_sys.path:
    _stdout_sys.path.insert(1, str(_STDOUT_UTILS_DIR))
from stdout_utils import out as stdout_out  # noqa: E402

from scripts.ops.load_test_cli_contract import *


def _base_report_v3(
    args: argparse.Namespace,
    *,
    live: bool,
    synthetic_fixture: bool,
    frontend_base: str,
    backend_base: str,
    profiles: Sequence[str],
    auth_meta: Mapping[str, Any],
) -> dict[str, Any]:
    thresholds = Thresholds(args.max_error_rate, args.max_p95_ms, args.max_p99_ms)
    journey_profile = resolve_journey_profile(args.journey_profile)
    calibration_manifest = build_capacity_calibration_manifest(
        args.calibration_trace or args.role_calibration,
        expected_source_sha256=args.calibration_source_sha256,
        as_of=args.calibration_as_of,
        attestation_path=args.calibration_attestation,
        journey_profile=journey_profile or STAFF_READONLY_JOURNEY_V1,
    )
    environment = (
        environment_snapshot(frontend_base, backend_base, args.postgres_port, args.redis_port)
        if live
        else {
            "evidence_scope": "offline deterministic fixture; no HTTP, DB, Redis, provider, or browser access",
            "repo_root": str(ROOT),
            "git_branch": _git(["branch", "--show-current"]),
            "git_head": _git(["rev-parse", "HEAD"]),
        }
    )
    return {
        "schema_version": 4,
        "evidence_type": "live_local_readonly_attempt" if live else "offline_fixture_contract",
        "requested_live": live,
        "network_observed": False,
        "pressure_completed": False,
        "live_run": False,
        "synthetic_fixture": synthetic_fixture,
        "started_at": utc_now(),
        "environment": environment,
        "safety": {
            "loopback_only": True,
            "method_allowlist": ["GET"],
            "business_mutations": False,
            "provider_calls": False,
            "browser_calls": False,
            "token_persisted": False,
            "automatic_stop": True,
            "phase_or_window_error_budget": True,
        },
        "auth": dict(auth_meta),
        "capacity_calibration": calibration_manifest,
        "configuration": {
            "mode": args.mode,
            "profiles": list(profiles),
            "phases": list(parse_positive_ints(args.phases)),
            "tiers": [
                item.public_dict() for item in parse_vu_duration_tiers(args.tiers)
            ]
            if args.mode == "closed-loop-tiers"
            else [],
            "trials_per_stage": args.trials,
            "requests_per_phase": args.requests_per_phase,
            "waves_per_phase": args.waves_per_phase,
            "seed": args.seed,
            "timeout_seconds": args.timeout_seconds,
            "cooldown_seconds": args.cooldown_seconds,
            "max_response_bytes": args.max_response_bytes,
            "thresholds": asdict(thresholds),
            "endpoint_allowlist": [item.public_dict() for item in ENDPOINTS],
            "journey_profile": (
                journey_profile.public_dict(pacing_scale=args.journey_pacing_scale)
                if journey_profile is not None
                else None
            ),
            "journey_endpoint_thresholds": (
                {
                    name: asdict(budget)
                    for name, budget in sorted(STAFF_READONLY_ENDPOINT_THRESHOLDS.items())
                }
                if journey_profile is not None
                else None
            ),
            "session_count": int(auth_meta.get("independent_session_count") or args.session_count),
            "load_model": {
                "ramp": "bounded closed-loop request workers; no fixed arrival rate",
                "closed_loop_tiers": "one response-waiting task per synthetic VU",
                "open_loop_supported": False,
                "human_user_conversion": "fail_closed_after_live_capacity_and_calibration_gates",
            },
            "telemetry": {
                "cpu_rss": "listener PID observation via lsof/ps when permitted",
                "event_loop_lag": "load-generator scheduling drift",
                "db_pool": "strict fresh run-bound service/host/port JSON sidecar",
                "redis": "strict fresh run-bound service/host/port JSON sidecar",
                "schema_version": TELEMETRY_SIDECAR_SCHEMA,
                "run_nonce_configured": bool(args.telemetry_run_nonce),
                "run_nonce_sha256": (
                    hashlib.sha256(str(args.telemetry_run_nonce).encode("utf-8")).hexdigest()
                    if args.telemetry_run_nonce
                    else None
                ),
            },
        },
        "profiles": [],
        "limitations": [
            "synthetic VU and request concurrency are not human users, accounts, or seats",
            "VU-to-seat conversion remains null unless every calibration and live capacity gate passes",
            "closed-loop results do not establish open-loop arrival-rate capacity",
            "local loopback evidence does not establish cloud, WAN, proxy, or autoscaling capacity",
            "read-only endpoints do not measure write queues, providers, Shopify, Apify, or R2",
            "staff journey role mix and pacing are hypotheses until calibrated from production traces",
            "fixture results validate contracts only and are never performance evidence"
            if synthetic_fixture
            else "one workstation and its load generator can bias measured throughput and latency",
        ],
    }


async def _execute_v3_with_contexts(
    args: argparse.Namespace,
    *,
    contexts: Sequence[RequestContext],
    request_fn: Callable[..., Awaitable[dict[str, Any]]],
    live: bool,
    synthetic_fixture: bool,
    auth_meta: Mapping[str, Any],
    raw_writer: RawSampleWriter | None,
    identity_probe_fn: Callable[..., Awaitable[Mapping[str, Any]]] = probe_live_identity,
) -> dict[str, Any]:
    validate_capacity_execution_hard_bounds(args)
    frontend_base = validate_loopback_base(args.frontend_base)
    backend_base = validate_loopback_base(args.backend_base)
    profiles = parse_profiles(args.profiles)
    thresholds = Thresholds(args.max_error_rate, args.max_p95_ms, args.max_p99_ms)
    journey_profile = resolve_journey_profile(args.journey_profile)
    journey_endpoint_thresholds = (
        STAFF_READONLY_ENDPOINT_THRESHOLDS if journey_profile is not None else None
    )
    report = _base_report_v3(
        args,
        live=live,
        synthetic_fixture=synthetic_fixture,
        frontend_base=frontend_base,
        backend_base=backend_base,
        profiles=profiles,
        auth_meta=auth_meta,
    )
    frontend_port = urlparse(frontend_base).port or (443 if frontend_base.startswith("https") else 80)
    backend_port = urlparse(backend_base).port or (443 if backend_base.startswith("https") else 80)
    resource_ports = (frontend_port, backend_port, args.postgres_port, args.redis_port)
    required_listeners = {
        "frontend": frontend_port,
        "backend": backend_port,
        "postgresql": int(args.postgres_port),
        "redis": int(args.redis_port),
    }
    telemetry_nonce = validate_telemetry_run_nonce(args.telemetry_run_nonce)
    telemetry_readers = {
        "db_pool": TelemetrySidecarReader(
            "db_pool",
            args.db_pool_telemetry_file,
            "127.0.0.1",
            int(args.postgres_port),
            telemetry_nonce,
            max(MAX_TELEMETRY_AGE_SECONDS, float(args.resource_sample_seconds) * 2.5),
        ),
        "redis": TelemetrySidecarReader(
            "redis",
            args.redis_telemetry_file,
            "127.0.0.1",
            int(args.redis_port),
            telemetry_nonce,
            max(MAX_TELEMETRY_AGE_SECONDS, float(args.resource_sample_seconds) * 2.5),
        ),
    }
    sink = raw_writer.write if raw_writer is not None else None

    selected_profiles = profiles if args.mode == "ramp" else (args.soak_profile,)
    expected_stages_per_profile = len(
        parse_positive_ints(args.phases)
        if args.mode == "ramp"
        else parse_vu_duration_tiers(args.tiers)
    )
    report["execution_expectations"] = {
        "selected_profiles": list(selected_profiles),
        "stages_per_profile": expected_stages_per_profile,
        "trials_per_stage": int(args.trials),
        "positive_requests_required_per_trial": True,
    }
    distinct_tokens = int(auth_meta.get("token_count") or 0)
    if len(contexts) > MAX_SOAK_VIRTUAL_USERS:
        raise ValueError(f"session contexts cannot exceed {MAX_SOAK_VIRTUAL_USERS}")
    planned_preflight = planned_preflight_request_count(args, len(contexts))
    if planned_preflight > MAX_PREFLIGHT_REQUESTS:
        raise ValueError(
            f"planned preflight requests {planned_preflight} exceed hard limit "
            f"{MAX_PREFLIGHT_REQUESTS}"
        )
    identity_preflight: dict[str, Any] | None = None
    if live and journey_profile is not None and distinct_tokens:
        identity_preflight = await verify_live_identity_contexts(
            contexts,
            backend_base=backend_base,
            max_response_bytes=args.max_response_bytes,
            run_salt=secrets.token_bytes(32),
            probe_fn=identity_probe_fn,
        )
    elif journey_profile is not None:
        identity_preflight = {
            "pass": False,
            "identity_source": "not_evaluated_without_live_explicit_tokens",
            "probed_session_count": 0,
            "verified_principal_count": 0,
            "distinct_auth_identity_count": 0,
            "organization_count": 0,
            "run_local_principal_bindings_sha256": [],
            "raw_principals_persisted": False,
            "tokens_persisted": False,
            "request_count": 0,
            "failure_counts": {"not_evaluated": 1},
        }
    report["identity_preflight"] = identity_preflight
    report["planned_preflight_request_count"] = planned_preflight
    for profile_index, profile in enumerate(selected_profiles):
        endpoints = endpoints_for_profile(profile)
        result: dict[str, Any] = {
            "profile": profile,
            "endpoints": [endpoint.name for endpoint in endpoints],
            "status": "pending",
            "preflight": [],
            "stages": [],
        }
        if live and any(endpoint.authenticated for endpoint in endpoints) and distinct_tokens == 0:
            result.update(
                {
                    "status": "blocked",
                    "blocked_reason": "authenticated profile requires explicit env or controlled-file tokens",
                }
            )
            report["profiles"].append(result)
            continue
        if live and journey_profile is not None and not bool((identity_preflight or {}).get("pass")):
            result.update(
                {
                    "status": "blocked",
                    "blocked_reason": (
                        "authenticated current-staff/current-tenant identity preflight did not "
                        "prove one unique principal per session"
                    ),
                }
            )
            report["profiles"].append(result)
            continue
        checks = await preflight(
            contexts[0].session,
            endpoints,
            frontend_base=frontend_base,
            backend_base=backend_base,
            token=contexts[0].token,
            max_response_bytes=args.max_response_bytes,
            request_fn=request_fn,
            request_contexts=contexts,
            sample_sink=sink,
            sample_context={
                "profile": profile,
                "stage": "preflight",
                "tier_index": None,
                "trial_index": None,
            },
        )
        result["preflight"] = checks
        if any(not item.get("ok") for item in checks):
            result.update(
                {
                    "status": "blocked",
                    "blocked_reason": "preflight failed; no pressure stage was started",
                }
            )
            report["profiles"].append(result)
            continue

        result["status"] = "running"
        if args.mode == "ramp":
            stages: Sequence[tuple[int, float | None]] = [
                (concurrency, None) for concurrency in parse_positive_ints(args.phases)
            ]
        else:
            stages = [
                (tier.virtual_users, tier.duration_seconds)
                for tier in parse_vu_duration_tiers(args.tiers)
            ]

        for stage_index, (concurrency, duration) in enumerate(stages):
            trials: list[dict[str, Any]] = []
            for trial_index in range(args.trials):
                seed = int(args.seed) + profile_index * 100_000 + stage_index * 1000 + trial_index
                sample_context = {
                    "profile": profile,
                    "stage": args.mode,
                    "tier_index": stage_index,
                    "trial_index": trial_index,
                }
                if args.mode == "ramp":
                    total = max(args.requests_per_phase, concurrency * args.waves_per_phase)
                    operation = run_phase(
                        contexts[0].session,
                        endpoints,
                        concurrency=concurrency,
                        total_requests=total,
                        frontend_base=frontend_base,
                        backend_base=backend_base,
                        token=contexts[0].token,
                        max_response_bytes=args.max_response_bytes,
                        seed=seed,
                        request_fn=request_fn,
                        request_contexts=contexts,
                        sample_sink=sink,
                        sample_context=sample_context,
                    )
                else:
                    operation = run_soak(
                        contexts[0].session,
                        endpoints,
                        virtual_users=concurrency,
                        duration_seconds=float(duration or 0.0),
                        max_requests=args.soak_max_requests,
                        think_time_ms=args.soak_think_time_ms,
                        window_seconds=args.soak_window_seconds,
                        thresholds=thresholds,
                        frontend_base=frontend_base,
                        backend_base=backend_base,
                        token=contexts[0].token,
                        max_response_bytes=args.max_response_bytes,
                        seed=seed,
                        request_fn=request_fn,
                        request_contexts=contexts,
                        sample_sink=sink,
                        sample_context=sample_context,
                        journey_profile=journey_profile,
                        journey_pacing_scale=args.journey_pacing_scale,
                        endpoint_thresholds=journey_endpoint_thresholds,
                    )
                summary, telemetry = await _run_with_configured_telemetry(
                    operation,
                    live=live,
                    ports=resource_ports,
                    args=args,
                    adapter_readers=telemetry_readers,
                    required_listeners=required_listeners,
                )
                summary["trial_index"] = trial_index
                summary["seed"] = seed
                summary["resource_telemetry"] = telemetry
                reasons = combined_stop_reasons(
                    summary,
                    thresholds,
                    journey_endpoint_thresholds,
                )
                summary["threshold_pass"] = not reasons
                summary["stop_reasons"] = reasons
                trials.append(summary)
                if reasons:
                    break
                if trial_index != args.trials - 1 and args.cooldown_seconds > 0:
                    await asyncio.sleep(args.cooldown_seconds)
            aggregate = aggregate_trial_summaries(
                trials,
                thresholds,
                concurrency=concurrency,
                load_model=(
                    "bounded_work_queue_closed_loop_workers"
                    if args.mode == "ramp"
                    else "closed_loop_virtual_users"
                ),
            )
            aggregate["stage_index"] = stage_index
            aggregate["virtual_users"] = concurrency if args.mode == "closed-loop-tiers" else None
            aggregate["duration_seconds"] = duration
            aggregate["human_users"] = None
            aggregate_reasons = combined_stop_reasons(
                aggregate,
                thresholds,
                journey_endpoint_thresholds,
            )
            aggregate["threshold_pass"] = not aggregate_reasons
            aggregate["stop_reasons"] = aggregate_reasons
            result["stages"].append(aggregate)
            if not aggregate["threshold_pass"]:
                result["status"] = "stopped_at_threshold"
                break
            if stage_index != len(stages) - 1 and args.cooldown_seconds > 0:
                await asyncio.sleep(args.cooldown_seconds)
        if result["status"] == "running":
            result["status"] = "completed"
        identity_fidelity: dict[str, Any] | None = None
        if journey_profile is not None:
            max_tested_sessions = max(
                (int(stage.get("virtual_users") or 0) for stage in result["stages"]),
                default=0,
            )
            independent_sessions = int(
                auth_meta.get("independent_session_count") or len(contexts)
            )
            verified_identities = int(
                (identity_preflight or {}).get("distinct_auth_identity_count") or 0
            )
            identity_fidelity = {
                "max_tested_simulated_active_sessions": max_tested_sessions,
                "independent_http_session_count": independent_sessions,
                "distinct_auth_identity_count": verified_identities,
                "verified_principal_count": int(
                    (identity_preflight or {}).get("verified_principal_count") or 0
                ),
                "organization_count": int(
                    (identity_preflight or {}).get("organization_count") or 0
                ),
                "identity_source": (identity_preflight or {}).get("identity_source"),
                "run_local_principal_bindings_sha256": list(
                    (identity_preflight or {}).get("run_local_principal_bindings_sha256") or []
                ),
                "identity_preflight_pass": (identity_preflight or {}).get("pass") is True,
                "raw_principals_persisted": False,
                "tokens_persisted": False,
                "one_independent_http_session_per_tested_vu": (
                    independent_sessions >= max_tested_sessions
                ),
                "one_distinct_auth_identity_per_tested_vu": (
                    verified_identities >= max_tested_sessions
                ),
            }
        if synthetic_fixture:
            result["capacity"] = {
                "status": "not_evaluated_synthetic_fixture",
                "performance_evidence": False,
                "human_user_capacity": None,
                "conversion_performed": False,
            }
            if journey_profile is not None:
                verdict = fail_closed_capacity_verdict(
                    result["stages"],
                    endpoint_thresholds=journey_endpoint_thresholds,
                    calibration_manifest=report.get("capacity_calibration"),
                    identity_fidelity=identity_fidelity,
                    performance_evidence=None,
                )
                result["capacity"]["identity_fidelity"] = identity_fidelity
                result["capacity"]["capacity_verdict"] = verdict
        elif args.mode == "ramp":
            result["capacity"] = capacity_interpretation(result["stages"], thresholds)
        else:
            result["capacity"] = journey_capacity_interpretation(
                result["stages"],
                journey_profile,
                pacing_scale=args.journey_pacing_scale,
            )
            if journey_profile is not None:
                verdict = fail_closed_capacity_verdict(
                    result["stages"],
                    endpoint_thresholds=journey_endpoint_thresholds,
                    calibration_manifest=report.get("capacity_calibration"),
                    identity_fidelity=identity_fidelity,
                    performance_evidence=(
                        _seal_live_stage_bundle(result["stages"])
                        if live and not synthetic_fixture
                        else None
                    ),
                )
                result["capacity"]["identity_fidelity"] = {
                    **dict(identity_fidelity or {}),
                    "human_user_capacity_claim_allowed": bool(
                        verdict.get("capacity_claim_allowed")
                    ),
                }
                result["capacity"]["capacity_verdict"] = verdict
                result["capacity"]["human_user_capacity"] = verdict.get("human_seat_estimate")
                result["capacity"]["conversion_performed"] = bool(
                    verdict.get("human_seat_estimate")
                )
        report["profiles"].append(result)

    report["executed_stage_count"] = sum(len(item.get("stages") or []) for item in report["profiles"])
    preflight_count = sum(len(profile.get("preflight") or []) for profile in report["profiles"])
    identity_preflight_count = int((identity_preflight or {}).get("request_count") or 0)
    stage_request_count = sum(
            int(trial.get("total_requests") or 0)
            for profile in report["profiles"]
            for stage in profile.get("stages") or []
            for trial in stage.get("trials") or []
    )
    report["identity_preflight_request_count"] = identity_preflight_count
    report["preflight_request_count"] = preflight_count + identity_preflight_count
    report["network_requests_issued"] = (
        preflight_count + identity_preflight_count + stage_request_count if live else 0
    )
    report["fixture_requests_simulated"] = (
        preflight_count + stage_request_count
        if synthetic_fixture
        else 0
    )
    report["network_observed"] = bool(live and report["network_requests_issued"] > 0)
    report["pressure_observed"] = bool(live and stage_request_count > 0)
    report["pressure_completed"] = bool(
        live and capacity_report_execution_complete(report)
    )
    # A partial real pressure stage is still a live attempt, but never a
    # completed run and never eligible for exit 0.
    report["live_run"] = report["pressure_observed"]
    report["evidence_type"] = (
        "live_local_readonly_pressure"
        if report["pressure_completed"]
        else "live_local_readonly_pressure_incomplete"
        if report["pressure_observed"]
        else "live_local_readonly_preflight_only"
        if report["network_observed"]
        else "blocked_local_capacity_attempt"
        if live
        else "offline_fixture_contract"
    )
    report["overall_capacity"] = next(
        (
            item.get("capacity")
            for item in report["profiles"]
            if item.get("profile") == ("mixed" if args.mode == "ramp" else args.soak_profile)
        ),
        None,
    )
    report["completed_at"] = utc_now()
    report["report_sha256"] = "computed_after_redaction"
    return report


async def execute(
    args: argparse.Namespace,
    *,
    raw_writer: RawSampleWriter | None = None,
    execution_plan: ImmutableCapacityExecutionPlan | None = None,
    operator_approval: Mapping[str, Any] | None = None,
    runtime_identity_probe_fn: Callable[..., Awaitable[Mapping[str, Any]]] | None = None,
) -> dict[str, Any]:
    """Execute either an explicit live loopback run or an offline fixture contract."""
    if args.fixture is not None:
        fixture = OfflineFixture.from_path(args.fixture)
        contexts = tuple(RequestContext(None, None, index) for index in range(args.session_count))
        auth_meta = {
            "sources": ["offline_fixture_no_auth"],
            "token_count": 0,
            "independent_session_count": len(contexts),
            "token_emitted": False,
            "token_persisted": False,
        }
        return await _execute_v3_with_contexts(
            args,
            contexts=contexts,
            request_fn=fixture.request,
            live=False,
            synthetic_fixture=True,
            auth_meta=auth_meta,
            raw_writer=raw_writer,
        )

    execution_plan = execution_plan or build_capacity_execution_plan(args)
    try:
        args = freeze_capacity_execution_args(args, execution_plan)
    except ValueError:
        approval_failure = _execution_approval_failure(
            "capacity_execution_args_changed_after_plan"
        )
        approval_failure["plan_sha256"] = execution_plan.plan_sha256
        return blocked_operator_approval_report(
            args,
            plan=execution_plan,
            approval=approval_failure,
            reason="execution configuration no longer matches the approved plan",
        )
    approval = operator_approval or verify_capacity_execution_approval(
        args.execution_approval,
        plan=execution_plan,
        run_nonce=args.execution_run_nonce,
    )
    expected_nonce = validate_execution_run_nonce(args.execution_run_nonce)
    expected_nonce_sha256 = (
        hashlib.sha256(expected_nonce.encode("utf-8")).hexdigest()
        if expected_nonce
        else None
    )
    approval_matches_prepared_plan = bool(
        isinstance(approval.get("plan_sha256"), str)
        and secrets.compare_digest(approval["plan_sha256"], execution_plan.plan_sha256)
        and isinstance(approval.get("run_nonce_sha256"), str)
        and isinstance(expected_nonce_sha256, str)
        and secrets.compare_digest(approval["run_nonce_sha256"], expected_nonce_sha256)
    )
    if not is_verified_capacity_execution_approval(approval) or not approval_matches_prepared_plan:
        if is_verified_capacity_execution_approval(approval):
            approval = {
                **public_capacity_execution_approval(approval),
                "status": "untrusted_or_unapproved",
                "trusted": False,
                "failure_reasons": ["prepared_approval_binding_mismatch"],
            }
        return blocked_operator_approval_report(
            args,
            plan=execution_plan,
            approval=approval,
        )

    if not is_consumed_capacity_execution_approval(approval):
        approval = consume_capacity_execution_approval(
            approval,
            plan=execution_plan,
            ledger_dir=args.execution_nonce_ledger_dir,
        )
    if not is_consumed_capacity_execution_approval(approval):
        return blocked_operator_approval_report(
            args,
            plan=execution_plan,
            approval=approval,
            reason="operator approval nonce could not be consumed exactly once",
        )
    if not redeem_consumed_capacity_execution_approval(approval):
        replayed_approval = public_capacity_execution_approval(approval)
        replayed_approval.update(
            {
                "status": "approval_nonce_consumption_failed",
                "trusted": False,
                "failure_reasons": ["execution_approval_consumed_capability_reused"],
            }
        )
        return blocked_operator_approval_report(
            args,
            plan=execution_plan,
            approval=replayed_approval,
            reason="consumed operator approval capability was already redeemed",
        )

    # Atomic nonce consumption/redeeming occurs before this first token source
    # read.  The approved plan fixes the maximum independent session count, so
    # a larger token pool cannot silently expand the live run.
    tokens, auth_meta = resolve_token_pool(args.token_file)
    if len(tokens) > int(args.session_count):
        blocked_approval = public_capacity_execution_approval(approval)
        blocked_approval.update(
            {
                "status": "blocked_after_approval",
                "trusted": False,
                "failure_reasons": ["token_count_exceeds_approved_session_count"],
            }
        )
        return blocked_operator_approval_report(
            args,
            plan=execution_plan,
            approval=blocked_approval,
            reason="resolved token count exceeds the operator-approved session bound",
        )
    session_count = max(int(args.session_count), 1)
    if session_count > MAX_SOAK_VIRTUAL_USERS:
        raise ValueError(
            f"resolved session count cannot exceed {MAX_SOAK_VIRTUAL_USERS}; "
            "refusing before creating HTTP sessions"
        )
    preflight_requests = planned_preflight_request_count(args, session_count)
    if preflight_requests > MAX_PREFLIGHT_REQUESTS:
        raise ValueError(
            f"planned preflight requests {preflight_requests} exceed hard limit "
            f"{MAX_PREFLIGHT_REQUESTS}; refusing before creating HTTP sessions"
        )
    auth_meta = {
        **auth_meta,
        "independent_session_count": session_count,
        "distinct_auth_identity_count": len(tokens),
        "session_identity_assignment": "round_robin_without_serializing_identity_values",
    }
    timeout = aiohttp.ClientTimeout(total=float(args.timeout_seconds))
    async with contextlib.AsyncExitStack() as stack:
        contexts: list[RequestContext] = []
        for index in range(session_count):
            connector = aiohttp.TCPConnector(
                limit=0,
                limit_per_host=0,
                ssl=False,
                enable_cleanup_closed=True,
            )
            session = await stack.enter_async_context(
                aiohttp.ClientSession(timeout=timeout, connector=connector)
            )
            contexts.append(
                RequestContext(
                    session=session,
                    token=tokens[index % len(tokens)] if tokens else None,
                    slot=index,
                )
            )
        runtime_identity = await verify_target_runtime_identity(
            contexts[0],
            backend_base=args.backend_base,
            max_response_bytes=args.max_response_bytes,
            execution_plan=execution_plan,
            probe_fn=runtime_identity_probe_fn or probe_target_runtime_health,
        )
        if runtime_identity.get("pass") is not True:
            return blocked_target_runtime_identity_report(
                args,
                plan=execution_plan,
                approval=approval,
                auth_meta=auth_meta,
                preflight=runtime_identity,
            )
        report = await _execute_v3_with_contexts(
            args,
            contexts=contexts,
            request_fn=fire_one,
            live=True,
            synthetic_fixture=False,
            auth_meta=auth_meta,
            raw_writer=raw_writer,
        )
        runtime_request_count = int(runtime_identity.get("request_count") or 0)
        report["runtime_identity_preflight"] = runtime_identity
        report["runtime_identity_preflight_request_count"] = runtime_request_count
        report["preflight_request_count"] = int(report.get("preflight_request_count") or 0) + runtime_request_count
        report["network_requests_issued"] = int(report.get("network_requests_issued") or 0) + runtime_request_count
        report["network_observed"] = bool(report.get("network_requests_issued"))
        report["capacity_execution_plan"] = execution_plan.public_dict()
        report["operator_preflight"] = public_capacity_execution_approval(approval)
        return report


def write_report(report: Mapping[str, Any], output: Path, token: str | None = None) -> Path:
    output.parent.mkdir(parents=True, exist_ok=True)
    clean = redact_secrets(report)
    if report_contains_secret(clean, token):
        raise RuntimeError("refusing to write report because a secret-like value remains")
    clean_without_hash = dict(clean)
    clean_without_hash.pop("report_sha256", None)
    canonical = json.dumps(clean_without_hash, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    clean["report_sha256"] = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    encoded = json.dumps(clean, ensure_ascii=False, indent=2) + "\n"
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    fd = os.open(output, flags, 0o600)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            fd = -1
            stream.write(encoded)
            stream.flush()
            os.fsync(stream.fileno())
    finally:
        if fd >= 0:
            os.close(fd)
    return output


def main(argv: Iterable[str] | None = None) -> int:
    args = build_parser().parse_args(list(argv) if argv is not None else None)
    try:
        validate_execution_args(args)
    except ValueError as exc:
        raise SystemExit(str(exc)) from exc
    started = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
    default_name = (
        f"vkpi-readonly-pressure-{started}.json"
        if args.execute_live
        else f"vkpi-readonly-fixture-{started}.json"
        if args.fixture is not None
        else f"vkpi-readonly-pressure-plan-{started}.json"
    )
    output = args.output or DEFAULT_REPORT_DIR / default_name
    raw_meta: dict[str, Any] | None = None
    if not args.execute_live and args.fixture is None:
        report = build_dry_run_report(args)
    elif args.execute_live:
        # Verify and atomically consume operator authority before constructing
        # RawSampleWriter.  Any missing, invalid, dirty-tree, expired, or
        # replayed approval must leave no empty raw-evidence artifact.
        prepared_plan = build_capacity_execution_plan(args)
        try:
            prepared_args = freeze_capacity_execution_args(args, prepared_plan)
        except ValueError:
            prepared_args = None
        if prepared_args is None:
            approval_failure = _execution_approval_failure(
                "capacity_execution_args_changed_after_plan"
            )
            approval_failure["plan_sha256"] = prepared_plan.plan_sha256
            report = blocked_operator_approval_report(
                args,
                plan=prepared_plan,
                approval=approval_failure,
                reason="execution configuration no longer matches the approved plan",
            )
        else:
            prepared_approval = verify_capacity_execution_approval(
                prepared_args.execution_approval,
                plan=prepared_plan,
                run_nonce=prepared_args.execution_run_nonce,
            )
            if not is_verified_capacity_execution_approval(prepared_approval):
                report = blocked_operator_approval_report(
                    prepared_args,
                    plan=prepared_plan,
                    approval=prepared_approval,
                )
            else:
                prepared_approval = consume_capacity_execution_approval(
                    prepared_approval,
                    plan=prepared_plan,
                    ledger_dir=prepared_args.execution_nonce_ledger_dir,
                )
                if not is_consumed_capacity_execution_approval(prepared_approval):
                    report = blocked_operator_approval_report(
                        prepared_args,
                        plan=prepared_plan,
                        approval=prepared_approval,
                        reason="operator approval nonce could not be consumed exactly once",
                    )
                elif prepared_args.no_raw_samples:
                    report = asyncio.run(
                        execute(
                            prepared_args,
                            execution_plan=prepared_plan,
                            operator_approval=prepared_approval,
                        )
                    )
                else:
                    raw_output = prepared_args.raw_output or output.with_name(
                        f"{output.stem}.samples.ndjson"
                    )
                    writer = RawSampleWriter(raw_output)
                    try:
                        report = asyncio.run(
                            execute(
                                prepared_args,
                                raw_writer=writer,
                                execution_plan=prepared_plan,
                                operator_approval=prepared_approval,
                            )
                        )
                    finally:
                        raw_meta = writer.close()
                    report["raw_evidence"] = raw_meta
    elif args.no_raw_samples:
        report = asyncio.run(execute(args))
    else:
        raw_output = args.raw_output or output.with_name(f"{output.stem}.samples.ndjson")
        writer = RawSampleWriter(raw_output)
        try:
            report = asyncio.run(execute(args, raw_writer=writer))
        finally:
            raw_meta = writer.close()
        report["raw_evidence"] = raw_meta
    path = write_report(report, output)
    calibration_manifest_path: Path | None = None
    if args.calibration_manifest_output is not None:
        calibration_output = args.calibration_manifest_output.expanduser()
        if calibration_output.resolve() == output.expanduser().resolve():
            raise ValueError("--calibration-manifest-output must differ from --output")
        calibration = report.get("capacity_calibration")
        if not isinstance(calibration, Mapping):
            raise RuntimeError("capacity calibration manifest is missing from report")
        calibration_manifest_path = write_report(calibration, calibration_output)
    summary_status = capacity_cli_summary_status(
        report,
        execute_live=bool(args.execute_live),
        fixture_configured=args.fixture is not None,
    )
    summary = {
        "status": summary_status,
        "report_path": str(path),
        "requested_live": report.get("requested_live", False),
        "live_run": report.get("live_run"),
        "network_observed": report.get("network_observed", False),
        "pressure_observed": report.get("pressure_observed", False),
        "pressure_completed": report.get("pressure_completed", False),
        "network_requests_issued": report.get("network_requests_issued", 0),
        "raw_evidence": raw_meta,
        "calibration_manifest_path": (
            str(calibration_manifest_path) if calibration_manifest_path is not None else None
        ),
        "operator_preflight": report.get("operator_preflight"),
        "runtime_identity_preflight": report.get("runtime_identity_preflight"),
        "overall_capacity": report.get("overall_capacity"),
        "blocked_profiles": [
            item.get("profile") for item in report.get("profiles", []) if item.get("status") == "blocked"
        ],
        "incomplete_profiles": [
            item.get("profile")
            for item in report.get("profiles", [])
            if item.get("status") not in {"completed", "blocked"}
        ],
        "token_emitted": False,
    }
    stdout_out(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0 if not args.execute_live or summary_status == "complete" else 2

__all__ = [name for name in globals() if not name.startswith("__")]
