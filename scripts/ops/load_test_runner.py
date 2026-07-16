from __future__ import annotations

from scripts.ops.load_test_runtime_identity import *

def resolve_short_lived_token() -> tuple[str | None, dict[str, Any]]:
    tokens, metadata = resolve_token_pool()
    return (tokens[0] if tokens else None), metadata


async def fire_one(
    session: aiohttp.ClientSession,
    endpoint: Endpoint,
    *,
    frontend_base: str,
    backend_base: str,
    token: str | None,
    semaphore: asyncio.Semaphore,
    max_response_bytes: int,
) -> dict[str, Any]:
    base = frontend_base if endpoint.target == "frontend" else backend_base
    headers = {
        "Accept": "text/html,*/*" if endpoint.category == "static_frontend" else "application/json",
        "Cache-Control": "no-cache",
        "User-Agent": "vkpi-readonly-capacity/1",
        "X-Requested-With": "XMLHttpRequest",
    }
    if endpoint.authenticated and token:
        headers["Authorization"] = f"Bearer {token}"
    status = 0
    size = 0
    error_type = ""
    error_detail = ""
    truncated = False
    async with semaphore:
        # Measure server-facing request latency, not the load-generator queue
        # wait.  Phase elapsed time separately captures total throughput.
        started = time.perf_counter()
        try:
            async with session.get(f"{base}{endpoint.path}", headers=headers, allow_redirects=False) as response:
                status = int(response.status)
                while True:
                    chunk = await response.content.read(min(65536, max_response_bytes + 1 - size))
                    if not chunk:
                        break
                    size += len(chunk)
                    if size > max_response_bytes:
                        truncated = True
                        break
        except Exception as exc:  # noqa: BLE001 - summarized without secrets
            error_type = type(exc).__name__
            error_detail = str(exc)[:160]
        latency_ms = (time.perf_counter() - started) * 1000.0
    ok = not error_type and not truncated and status in endpoint.expected_statuses
    return {
        "endpoint": endpoint.name,
        "category": endpoint.category,
        "status": status,
        "ok": ok,
        "latency_ms": round(latency_ms, 4),
        "bytes": size,
        "error_type": "response_too_large" if truncated else error_type,
        "error_detail": "response exceeded safety cap" if truncated else error_detail,
    }


async def run_phase(
    session: aiohttp.ClientSession,
    endpoints: Sequence[Endpoint],
    *,
    concurrency: int,
    total_requests: int,
    frontend_base: str,
    backend_base: str,
    token: str | None,
    max_response_bytes: int,
    seed: int,
    request_fn: Callable[..., Awaitable[dict[str, Any]]] = fire_one,
    request_contexts: Sequence[RequestContext] | None = None,
    sample_sink: Callable[[Mapping[str, Any]], None] | None = None,
    sample_context: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    workload = weighted_workload(endpoints, total_requests, seed=seed)
    queue: asyncio.Queue[tuple[int, Endpoint]] = asyncio.Queue()
    for index, endpoint in enumerate(workload):
        queue.put_nowait((index, endpoint))
    semaphore = asyncio.Semaphore(concurrency)
    ordered_results: list[dict[str, Any] | None] = [None] * len(workload)

    contexts = tuple(request_contexts or (RequestContext(session, token, 0),))
    if not contexts:
        raise ValueError("at least one request context is required")

    async def request_worker(worker_id: int) -> None:
        context = contexts[worker_id % len(contexts)]
        while True:
            try:
                index, endpoint = queue.get_nowait()
            except asyncio.QueueEmpty:
                return
            result = await request_fn(
                context.session,
                endpoint,
                frontend_base=frontend_base,
                backend_base=backend_base,
                token=context.token,
                semaphore=semaphore,
                max_response_bytes=max_response_bytes,
            )
            result["session_slot"] = context.slot
            ordered_results[index] = result
            if sample_sink is not None:
                sample_sink({**dict(sample_context or {}), "request_index": index, **result})
            queue.task_done()

    started = time.perf_counter()
    worker_count = min(int(concurrency), len(workload))
    await asyncio.gather(*(request_worker(worker_id) for worker_id in range(worker_count)))
    results = [item for item in ordered_results if item is not None]
    summary = summarize_requests(results, time.perf_counter() - started, concurrency)
    summary["workload"] = workload_metadata(
        workload,
        seed=seed,
        algorithm="seeded shuffled complete weighted cycles",
    )
    summary["load_model"] = {
        "generator_processes": 1,
        "generator_async_request_workers": worker_count,
        "configured_request_concurrency": int(concurrency),
        "maximum_in_flight_requests": worker_count,
        "virtual_users": None,
        "model": "bounded_work_queue",
        "arrival_model": (
            "closed-loop request workers: each worker waits for its response before dequeuing another; "
            "this is not an open-loop arrival-rate generator"
        ),
        "open_loop": False,
        "closed_loop_virtual_users": False,
        "independent_http_sessions": len(contexts),
        "warning": "request worker count and in-flight request cap are not simultaneous logged-in users",
    }
    return summary


async def run_soak(
    session: aiohttp.ClientSession,
    endpoints: Sequence[Endpoint],
    *,
    virtual_users: int,
    duration_seconds: float,
    max_requests: int,
    think_time_ms: float,
    window_seconds: float,
    thresholds: Thresholds,
    frontend_base: str,
    backend_base: str,
    token: str | None,
    max_response_bytes: int,
    seed: int,
    request_fn: Callable[..., Awaitable[dict[str, Any]]] = fire_one,
    request_contexts: Sequence[RequestContext] | None = None,
    sample_sink: Callable[[Mapping[str, Any]], None] | None = None,
    sample_context: Mapping[str, Any] | None = None,
    journey_profile: JourneyProfile | None = None,
    journey_pacing_scale: float = 1.0,
    endpoint_thresholds: Mapping[str, Thresholds] | None = None,
) -> dict[str, Any]:
    """Run bounded closed-loop virtual users with deterministic per-user mixes."""
    if not endpoints:
        raise ValueError("soak requires at least one endpoint")
    if virtual_users <= 0 or duration_seconds <= 0 or max_requests <= 0:
        raise ValueError("soak virtual users, duration, and request cap must be positive")
    if duration_seconds > MAX_SOAK_SECONDS:
        raise ValueError(f"soak duration cannot exceed {MAX_SOAK_SECONDS:g} seconds")
    if virtual_users > MAX_SOAK_VIRTUAL_USERS:
        raise ValueError(f"soak virtual users cannot exceed {MAX_SOAK_VIRTUAL_USERS}")
    if max_requests > MAX_SOAK_REQUESTS:
        raise ValueError(f"soak request cap cannot exceed {MAX_SOAK_REQUESTS}")
    if think_time_ms < 0 or window_seconds <= 0:
        raise ValueError("soak think time cannot be negative and window must be positive")
    if not (0.0 <= journey_pacing_scale <= 10.0):
        raise ValueError("journey pacing scale must be in [0, 10]")
    if journey_profile is not None:
        validate_journey_profile(journey_profile, endpoints)

    started = time.perf_counter()
    deadline = started + float(duration_seconds)
    semaphore = asyncio.Semaphore(int(virtual_users))
    stop_event = asyncio.Event()
    window_results: list[dict[str, Any]] = []
    accumulated = _StreamingRequestAccumulator()
    windows: list[dict[str, Any]] = []
    issued_count = 0
    journey_role_requests: Counter[str] = Counter()
    termination_reason = "duration_elapsed"
    threshold_reasons: list[str] = []
    contexts = tuple(request_contexts or (RequestContext(session, token, 0),))
    if not contexts:
        raise ValueError("at least one request context is required")

    async def virtual_user(virtual_user_id: int) -> None:
        nonlocal issued_count, termination_reason
        request_index = 0
        context = contexts[virtual_user_id % len(contexts)]
        while not stop_event.is_set() and time.perf_counter() < deadline:
            # This check-and-increment contains no await, so it is atomic within
            # the single asyncio event loop and cannot overrun max_requests.
            if issued_count >= max_requests:
                termination_reason = "max_requests"
                stop_event.set()
                return
            issued_count += 1
            role: JourneyRole | None = None
            journey_step_index: int | None = None
            step_think_time_ms = float(think_time_ms)
            if journey_profile is not None:
                endpoint, role, journey_step_index, nominal_think_time_ms = deterministic_journey_step(
                    journey_profile,
                    seed=seed,
                    virtual_user_id=virtual_user_id,
                    request_index=request_index,
                )
                step_think_time_ms = nominal_think_time_ms * float(journey_pacing_scale)
            else:
                endpoint = deterministic_soak_endpoint(
                    endpoints,
                    seed=seed,
                    virtual_user_id=virtual_user_id,
                    request_index=request_index,
                )
            request_index += 1
            result = await request_fn(
                context.session,
                endpoint,
                frontend_base=frontend_base,
                backend_base=backend_base,
                token=context.token,
                semaphore=semaphore,
                max_response_bytes=max_response_bytes,
            )
            result["virtual_user_id"] = virtual_user_id
            result["session_slot"] = context.slot
            if journey_profile is not None and role is not None:
                result["journey_profile"] = journey_profile.profile_id
                result["journey_role"] = role.name
                result["journey_step_index"] = journey_step_index
                journey_role_requests[role.name] += 1
            window_results.append(result)
            accumulated.add(result)
            if sample_sink is not None:
                sample_sink(
                    {
                        **dict(sample_context or {}),
                        "request_index": request_index - 1,
                        **result,
                    }
                )
            if step_think_time_ms > 0 and not stop_event.is_set():
                remaining = deadline - time.perf_counter()
                if remaining <= 0:
                    return
                try:
                    await asyncio.wait_for(
                        stop_event.wait(),
                        timeout=min(remaining, step_think_time_ms / 1000.0),
                    )
                except asyncio.TimeoutError:
                    pass

    async def window_monitor() -> None:
        nonlocal termination_reason, threshold_reasons
        window_started = time.perf_counter()
        while not stop_event.is_set():
            remaining = deadline - time.perf_counter()
            if remaining <= 0:
                termination_reason = "duration_elapsed"
                stop_event.set()
                break
            try:
                await asyncio.wait_for(stop_event.wait(), timeout=min(float(window_seconds), remaining))
            except asyncio.TimeoutError:
                pass
            now = time.perf_counter()
            entries = list(window_results)
            window_results.clear()
            if entries:
                window_summary = summarize_requests(entries, max(0.000001, now - window_started), virtual_users)
                reasons = combined_stop_reasons(
                    window_summary,
                    thresholds,
                    endpoint_thresholds,
                )
                window_summary.update(
                    {
                        "window_index": len(windows),
                        "start_offset_seconds": round(window_started - started, 4),
                        "end_offset_seconds": round(now - started, 4),
                        "threshold_pass": not reasons,
                        "stop_reasons": reasons,
                    }
                )
                windows.append(window_summary)
                if reasons:
                    termination_reason = "threshold"
                    threshold_reasons = reasons
                    stop_event.set()
            window_started = now

    workers = [asyncio.create_task(virtual_user(index)) for index in range(int(virtual_users))]
    monitor = asyncio.create_task(window_monitor())
    await asyncio.gather(*workers)
    if not stop_event.is_set():
        termination_reason = "duration_elapsed"
        stop_event.set()
    await monitor
    elapsed = time.perf_counter() - started

    # The monitor may be woken by the final request-cap/duration event before it
    # emits the last partial interval.
    if window_results:
        last_window_end = windows[-1]["end_offset_seconds"] if windows else 0.0
        partial = summarize_requests(
            window_results,
            max(0.000001, elapsed - last_window_end),
            virtual_users,
        )
        reasons = combined_stop_reasons(partial, thresholds, endpoint_thresholds)
        partial.update(
            {
                "window_index": len(windows),
                "start_offset_seconds": windows[-1]["end_offset_seconds"] if windows else 0.0,
                "end_offset_seconds": round(elapsed, 4),
                "threshold_pass": not reasons,
                "stop_reasons": reasons,
                "partial_window": True,
            }
        )
        windows.append(partial)
        if reasons:
            termination_reason = "threshold"
            threshold_reasons = sorted(set(threshold_reasons + reasons))

    summary = accumulated.summary(elapsed, virtual_users)
    overall_reasons = combined_stop_reasons(summary, thresholds, endpoint_thresholds)
    if overall_reasons:
        termination_reason = "threshold"
        threshold_reasons = sorted(set(threshold_reasons + overall_reasons))
    summary.update(
        {
            "mode": "closed_loop_virtual_user_soak",
            "planned_duration_seconds": float(duration_seconds),
            "actual_duration_seconds": round(elapsed, 4),
            "request_cap": int(max_requests),
            "issued_requests": int(issued_count),
            "termination_reason": termination_reason,
            "threshold_pass": not threshold_reasons,
            "stop_reasons": threshold_reasons,
            "windows": windows,
            "workload": {
                "algorithm": (
                    "versioned role journey with deterministic role assignment"
                    if journey_profile is not None
                    else "sha256-seeded per-virtual-user weighted cycle"
                ),
                "seed": int(seed),
                "actual_endpoint_counts": dict(
                    sorted(
                        (endpoint, metric.total)
                        for endpoint, metric in accumulated.by_endpoint.items()
                    )
                ),
                "journey_role_request_counts": dict(sorted(journey_role_requests.items())),
            },
            "load_model": {
                "generator_processes": 1,
                "generator_async_worker_tasks": int(virtual_users),
                "virtual_users": int(virtual_users),
                "maximum_in_flight_requests": int(virtual_users),
                "server_worker_processes": (
                    "observed only through resource telemetry; not configured by this runner"
                ),
                "closed_loop": True,
                "open_loop": False,
                "independent_http_sessions": len(contexts),
                "think_time_ms": float(think_time_ms),
                "definition": (
                    "one asyncio task per virtual user; each user waits for its response "
                    "and think time before the next GET"
                ),
                "warning": "virtual users are synthetic HTTP actors, not authenticated human seats",
                "simulated_active_sessions": int(virtual_users) if journey_profile is not None else None,
                "human_users": None,
                "journey": (
                    journey_profile.public_dict(pacing_scale=journey_pacing_scale)
                    if journey_profile is not None
                    else None
                ),
                "endpoint_thresholds": (
                    {
                        name: asdict(budget)
                        for name, budget in sorted(endpoint_thresholds.items())
                    }
                    if endpoint_thresholds
                    else None
                ),
            },
        }
    )
    return summary


async def preflight(
    session: aiohttp.ClientSession,
    endpoints: Sequence[Endpoint],
    *,
    frontend_base: str,
    backend_base: str,
    token: str | None,
    max_response_bytes: int,
    request_fn: Callable[..., Awaitable[dict[str, Any]]] = fire_one,
    request_contexts: Sequence[RequestContext] | None = None,
    sample_sink: Callable[[Mapping[str, Any]], None] | None = None,
    sample_context: Mapping[str, Any] | None = None,
) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    semaphore = asyncio.Semaphore(1)
    contexts = tuple(request_contexts or (RequestContext(session, token, 0),))
    for context in contexts:
        for endpoint in endpoints:
            result = await request_fn(
                context.session,
                endpoint,
                frontend_base=frontend_base,
                backend_base=backend_base,
                token=context.token,
                semaphore=semaphore,
                max_response_bytes=max_response_bytes,
            )
            result["session_slot"] = context.slot
            results.append(result)
            if sample_sink is not None:
                sample_sink(
                    {
                        **dict(sample_context or {}),
                        "request_index": len(results) - 1,
                        **result,
                    }
                )
    return results




async def probe_live_identity(
    context: RequestContext,
    *,
    backend_base: str,
    max_response_bytes: int,
) -> dict[str, Any]:
    """Resolve one principal and tenant through authenticated GET endpoints."""
    request_count = 0
    if not context.token:
        return {"ok": False, "reason": "missing_token", "request_count": request_count}
    headers = {
        "Accept": "application/json",
        "User-Agent": "vkpi-readonly-load-test/identity-v1",
        "Authorization": f"Bearer {context.token}",
    }
    try:
        async with context.session.get(
            f"{backend_base}{IDENTITY_STAFF_PATH}", headers=headers, allow_redirects=False
        ) as response:
            request_count += 1
            if int(response.status) != 200:
                raise ValueError("current-staff endpoint rejected the token")
            staff_payload = await _read_bounded_json_response(response, max_response_bytes)
        user = staff_payload.get("user")
        if staff_payload.get("status") != "success" or not isinstance(user, Mapping):
            raise ValueError("current-staff endpoint did not return an authenticated principal")
        principal = user.get("staff_id") or user.get("id")
        if not isinstance(principal, int) or isinstance(principal, bool) or principal <= 0:
            raise ValueError("current-staff endpoint omitted a stable positive principal id")

        async with context.session.get(
            f"{backend_base}{IDENTITY_TENANT_PATH}", headers=headers, allow_redirects=False
        ) as response:
            request_count += 1
            if int(response.status) != 200:
                raise ValueError("current-tenant endpoint rejected the token")
            tenant_payload = await _read_bounded_json_response(response, max_response_bytes)
        organization = tenant_payload.get("organization_id")
        if not isinstance(organization, int) or isinstance(organization, bool) or organization <= 0:
            raise ValueError("current-tenant endpoint omitted a stable positive organization id")
        return {
            "ok": True,
            "principal_id": principal,
            "organization_id": organization,
            "request_count": request_count,
        }
    except Exception as exc:  # noqa: BLE001 - identity must fail closed without leaking payloads
        return {
            "ok": False,
            "reason": type(exc).__name__,
            "request_count": request_count,
        }


async def verify_live_identity_contexts(
    contexts: Sequence[RequestContext],
    *,
    backend_base: str,
    max_response_bytes: int,
    run_salt: bytes,
    probe_fn: Callable[..., Awaitable[Mapping[str, Any]]] = probe_live_identity,
) -> dict[str, Any]:
    """Return only counts and salted run-local bindings, never raw identities."""
    bindings: list[str] = []
    organization_bindings: set[str] = set()
    failures: Counter[str] = Counter()
    request_count = 0
    for context in contexts:
        try:
            result = await probe_fn(
                context,
                backend_base=backend_base,
                max_response_bytes=max_response_bytes,
            )
        except Exception as exc:  # noqa: BLE001 - injected probe failures are evidence failures
            result = {"ok": False, "reason": type(exc).__name__, "request_count": 0}
        request_count += int(result.get("request_count") or 0)
        principal = result.get("principal_id")
        organization = result.get("organization_id")
        valid = (
            result.get("ok") is True
            and isinstance(principal, int)
            and not isinstance(principal, bool)
            and principal > 0
            and isinstance(organization, int)
            and not isinstance(organization, bool)
            and organization > 0
        )
        if not valid:
            failures[str(result.get("reason") or "invalid_identity_response")] += 1
            continue
        canonical = f"org:{organization}\x00principal:{principal}".encode("utf-8")
        bindings.append(hashlib.sha256(run_salt + b"\x00" + canonical).hexdigest())
        organization_bindings.add(
            hashlib.sha256(run_salt + b"\x00org:" + str(organization).encode("ascii")).hexdigest()
        )
    distinct_bindings = sorted(set(bindings))
    complete = len(bindings) == len(contexts)
    unique = len(distinct_bindings) == len(contexts)
    single_org = len(organization_bindings) == 1
    return {
        "pass": bool(contexts) and complete and unique and single_org,
        "identity_source": "authenticated_current_staff_plus_current_tenant_get",
        "probed_session_count": len(contexts),
        "verified_principal_count": len(bindings),
        "distinct_auth_identity_count": len(distinct_bindings),
        "organization_count": len(organization_bindings),
        "run_local_principal_bindings_sha256": distinct_bindings,
        "raw_principals_persisted": False,
        "tokens_persisted": False,
        "request_count": request_count,
        "failure_counts": dict(sorted(failures.items())),
    }




class OfflineFixture:
    """Deterministic request callable for CLI contract tests; it never opens HTTP."""

    def __init__(self, payload: Mapping[str, Any]):
        responses = payload.get("responses")
        if not isinstance(responses, Mapping):
            raise ValueError("fixture must contain a responses object")
        unknown = sorted(set(str(key) for key in responses) - set(ENDPOINT_BY_NAME))
        if unknown:
            raise ValueError(f"fixture contains endpoints outside allowlist: {unknown}")
        self._responses: dict[str, tuple[Mapping[str, Any], ...]] = {}
        self._indexes: Counter[str] = Counter()
        for name, value in responses.items():
            entries = value if isinstance(value, list) else [value]
            if not entries or not all(isinstance(item, Mapping) for item in entries):
                raise ValueError(f"fixture response for {name} must be an object or non-empty object array")
            self._responses[str(name)] = tuple(entries)

    @classmethod
    def from_path(cls, path: Path) -> "OfflineFixture":
        candidate = Path(path).expanduser()
        encoded = _secure_read_regular_file(
            candidate,
            max_bytes=1024 * 1024,
            label="fixture",
            require_owner=False,
            require_private=False,
        )
        payload = json.loads(encoded.decode("utf-8"))
        if not isinstance(payload, Mapping):
            raise ValueError("fixture root must be an object")
        return cls(payload)

    async def request(self, _session: Any, endpoint: Endpoint, **_kwargs: Any) -> dict[str, Any]:
        entries = self._responses.get(endpoint.name)
        if not entries:
            raise ValueError(f"fixture has no response for selected endpoint: {endpoint.name}")
        index = self._indexes[endpoint.name]
        self._indexes[endpoint.name] += 1
        item = entries[index % len(entries)]
        status = int(item.get("status", 200))
        error_type = str(item.get("error_type") or "")
        latency_ms = max(0.0, float(item.get("latency_ms", 1.0)))
        size = max(0, int(item.get("bytes", 0)))
        return {
            "endpoint": endpoint.name,
            "category": endpoint.category,
            "status": status,
            "ok": not error_type and status in endpoint.expected_statuses,
            "latency_ms": round(latency_ms, 4),
            "bytes": size,
            "error_type": error_type,
            "error_detail": str(item.get("error_detail") or "")[:160],
        }


def build_capacity_execution_plan(args: argparse.Namespace) -> ImmutableCapacityExecutionPlan:
    """Build the immutable, secret-free plan an operator must approve.

    This function reads only CLI configuration, local Git state, and the
    capacity harness sources.  It hashes configured file *paths* without
    opening token, calibration, telemetry, approval, or nonce-ledger files.
    Dirty worktrees may produce a dry-run plan, but that plan is deliberately
    ineligible for live approval.
    """
    frontend_base = validate_loopback_base(args.frontend_base)
    backend_base = validate_loopback_base(args.backend_base)
    profiles = parse_profiles(args.profiles)
    phases = parse_positive_ints(args.phases)
    tiers = parse_vu_duration_tiers(args.tiers) if args.mode == "closed-loop-tiers" else ()
    journey_profile = resolve_journey_profile(args.journey_profile)
    execution_nonce = validate_execution_run_nonce(args.execution_run_nonce)
    telemetry_nonce = validate_telemetry_run_nonce(args.telemetry_run_nonce)
    frontend_url = urlparse(frontend_base)
    backend_url = urlparse(backend_base)
    frontend_port = frontend_url.port or (443 if frontend_url.scheme == "https" else 80)
    backend_port = backend_url.port or (443 if backend_url.scheme == "https" else 80)
    worktree_state = current_capacity_worktree_state()
    nonce_ledger_dir = Path(
        getattr(
            args,
            "execution_nonce_ledger_dir",
            DEFAULT_CAPACITY_EXECUTION_NONCE_LEDGER_DIR,
        )
    )
    hard_bounds = validate_capacity_execution_hard_bounds(args)
    preflight_requests = planned_preflight_request_count(args, int(args.session_count))
    pressure_requests = int(hard_bounds["planned_pressure_requests"])
    if args.mode == "ramp":
        selected_profiles = list(profiles)
    else:
        selected_profiles = [str(args.soak_profile)]
    endpoint_contract = [item.public_dict() for item in ENDPOINTS]
    payload = {
        "schema_version": CAPACITY_EXECUTION_PLAN_SCHEMA,
        "approval_scope": CAPACITY_EXECUTION_APPROVAL_SCOPE,
        "code": {
            "git_head": current_capacity_code_head(),
            "worktree_clean": worktree_state["worktree_clean"],
            "worktree_status_sha256": worktree_state["worktree_status_sha256"],
            "runner_entrypoint": "scripts/load_test_vkpi_readonly.py",
            "runtime_source_files": list(CAPACITY_RUNNER_SOURCE_FILES),
            "runtime_source_bundle_sha256": current_capacity_runner_source_bundle_sha256(),
        },
        "run_binding": {
            "sealed_execution_args_sha256": capacity_execution_bound_args_sha256(args),
            "execution_run_nonce_sha256": (
                hashlib.sha256(execution_nonce.encode("utf-8")).hexdigest()
                if execution_nonce
                else None
            ),
            "telemetry_run_nonce_sha256": (
                hashlib.sha256(telemetry_nonce.encode("utf-8")).hexdigest()
                if telemetry_nonce
                else None
            ),
        },
        "target_runtime_identity": build_target_runtime_identity_contract(args),
        "approval_consumption": {
            "schema_version": CAPACITY_EXECUTION_NONCE_CONSUMPTION_SCHEMA,
            "single_use_required": True,
            "atomic_create_required": True,
            "ledger_record_mode": "owner_read_write_only",
            "ledger_dir_path_sha256": capacity_path_binding_sha256(nonce_ledger_dir),
            "raw_nonce_persisted": False,
            "signature_persisted": False,
        },
        "safety": {
            "loopback_only": True,
            "http_methods": ["GET"],
            "business_mutations": False,
            "provider_calls": False,
            "browser_calls": False,
            "automatic_stop": True,
            "runtime_signing_surface": False,
        },
        "targets": {
            "frontend": {
                "base": frontend_base,
                "host": frontend_url.hostname,
                "port": frontend_port,
            },
            "backend": {
                "base": backend_base,
                "host": backend_url.hostname,
                "port": backend_port,
            },
            "postgresql_telemetry": {"host": "127.0.0.1", "port": int(args.postgres_port)},
            "redis_telemetry": {"host": "127.0.0.1", "port": int(args.redis_port)},
            "endpoint_allowlist": endpoint_contract,
            "endpoint_allowlist_sha256": _canonical_json_sha256(endpoint_contract),
        },
        "workload": {
            "mode": str(args.mode),
            "profiles": selected_profiles,
            "phases": list(phases),
            "tiers": [tier.public_dict() for tier in tiers],
            "trials_per_stage": int(args.trials),
            "requests_per_phase": int(args.requests_per_phase),
            "waves_per_phase": int(args.waves_per_phase),
            "seed": int(args.seed),
            "timeout_seconds": float(args.timeout_seconds),
            "cooldown_seconds": float(args.cooldown_seconds),
            "max_response_bytes": int(args.max_response_bytes),
            "soak_profile": str(args.soak_profile),
            "soak_virtual_users": int(args.soak_virtual_users),
            "soak_think_time_ms": float(args.soak_think_time_ms),
            "soak_window_seconds": float(args.soak_window_seconds),
            "soak_max_requests_per_trial": int(args.soak_max_requests),
            "journey_profile": journey_profile.profile_id if journey_profile else None,
            "journey_profile_version": journey_profile.version if journey_profile else None,
            "journey_pacing_scale": float(args.journey_pacing_scale),
            "thresholds": {
                "max_error_rate": float(args.max_error_rate),
                "max_p95_ms": float(args.max_p95_ms),
                "max_p99_ms": float(args.max_p99_ms),
            },
            "hard_limits": hard_bounds,
        },
        "identity_preflight": {
            "enabled": journey_profile is not None,
            "current_staff_path": IDENTITY_STAFF_PATH,
            "current_tenant_path": IDENTITY_TENANT_PATH,
            "requests_per_context": IDENTITY_REQUESTS_PER_CONTEXT,
            "configured_session_count": int(args.session_count),
            "unique_principal_per_session_required": journey_profile is not None,
            "single_organization_required": journey_profile is not None,
            "token_count_must_not_exceed_approved_session_count": True,
            "token_file_path_sha256": capacity_path_binding_sha256(args.token_file),
        },
        "telemetry": {
            "sample_interval_seconds": float(args.resource_sample_seconds),
            "sidecar_schema": TELEMETRY_SIDECAR_SCHEMA,
            "db_pool_sidecar_path_sha256": capacity_path_binding_sha256(
                args.db_pool_telemetry_file
            ),
            "redis_sidecar_path_sha256": capacity_path_binding_sha256(
                args.redis_telemetry_file
            ),
            "independent_producer_attestation_required_for_qualification": True,
        },
        "calibration": {
            "source_path_sha256": capacity_path_binding_sha256(
                args.calibration_trace or args.role_calibration
            ),
            "source_content_sha256": str(args.calibration_source_sha256 or "") or None,
            "attestation_path_sha256": capacity_path_binding_sha256(
                args.calibration_attestation
            ),
            "independent_producer_attestation_required_for_qualification": True,
        },
        "request_bounds": {
            "planned_preflight_requests": int(preflight_requests),
            "maximum_pressure_requests": int(pressure_requests),
            "maximum_total_network_requests": int(preflight_requests + pressure_requests),
        },
    }
    return build_immutable_capacity_execution_plan(payload)


def freeze_capacity_execution_args(
    args: Any,
    plan: ImmutableCapacityExecutionPlan,
) -> FrozenCapacityExecutionArgs:
    """Snapshot args, then require that snapshot to rebuild the exact plan."""
    if isinstance(args, FrozenCapacityExecutionArgs):
        if (
            args._capability is _FROZEN_CAPACITY_ARGS_CAPABILITY
            and secrets.compare_digest(args._plan_sha256, plan.plan_sha256)
        ):
            return args
        raise ValueError("frozen capacity execution args do not match the approved plan")
    try:
        values = dict(vars(args))
    except TypeError as exc:
        raise ValueError("capacity execution args cannot be snapshotted") from exc
    for value in values.values():
        if value is not None and not isinstance(value, (bool, int, float, str, Path)):
            raise ValueError("capacity execution args contain a mutable or unsupported value")
    candidate = FrozenCapacityExecutionArgs(
        values,
        plan_sha256="",
        capability=_FROZEN_CAPACITY_ARGS_CAPABILITY,
    )
    rebuilt = build_capacity_execution_plan(candidate)
    if not secrets.compare_digest(rebuilt.plan_sha256, plan.plan_sha256):
        raise ValueError("capacity execution args changed after canonical plan creation")
    return FrozenCapacityExecutionArgs(
        values,
        plan_sha256=plan.plan_sha256,
        capability=_FROZEN_CAPACITY_ARGS_CAPABILITY,
    )


def build_dry_run_report(args: argparse.Namespace) -> dict[str, Any]:
    frontend_base = validate_loopback_base(args.frontend_base)
    backend_base = validate_loopback_base(args.backend_base)
    profiles = parse_profiles(args.profiles)
    tiers = parse_vu_duration_tiers(args.tiers) if args.mode == "closed-loop-tiers" else ()
    journey_profile = resolve_journey_profile(args.journey_profile)
    execution_plan = build_capacity_execution_plan(args)
    calibration_manifest = build_capacity_calibration_manifest(
        args.calibration_trace or args.role_calibration,
        expected_source_sha256=args.calibration_source_sha256,
        as_of=args.calibration_as_of,
        attestation_path=args.calibration_attestation,
        journey_profile=journey_profile or STAFF_READONLY_JOURNEY_V1,
    )
    return {
        "schema_version": 4,
        "evidence_type": "readonly_pressure_plan",
        "requested_live": False,
        "network_observed": False,
        "pressure_completed": False,
        "live_run": False,
        "synthetic_fixture": False,
        "network_requests_issued": 0,
        "business_mutations": False,
        "capacity_execution_plan": execution_plan.public_dict(),
        "operator_preflight": {
            "status": "approval_request_not_executed",
            "trusted": False,
            "plan_sha256": execution_plan.plan_sha256,
            "run_nonce_sha256": execution_plan["run_binding"][
                "execution_run_nonce_sha256"
            ],
            "approval_file_read": False,
            "token_file_read": False,
            "required": {
                "schema_version": CAPACITY_EXECUTION_APPROVAL_SCHEMA,
                "approval_scope": CAPACITY_EXECUTION_APPROVAL_SCOPE,
                "code_reviewed_operator_public_key": True,
                "exact_plan_binding": True,
                "run_nonce_binding": True,
                "single_use_nonce_consumption": True,
                "clean_worktree_required_for_live": True,
                "complete_target_runtime_identity_plan_binding": True,
                "maximum_validity_seconds": MAX_CAPACITY_EXECUTION_APPROVAL_SECONDS,
            },
        },
        "capacity_calibration": calibration_manifest,
        "configuration": {
            "mode": args.mode,
            "profiles": list(profiles),
            "phases": list(parse_positive_ints(args.phases)),
            "tiers": [tier.public_dict() for tier in tiers],
            "trials_per_stage": args.trials,
            "frontend_base": frontend_base,
            "backend_base": backend_base,
            "session_count": args.session_count,
            "token_sources": [
                TOKEN_ENV_NAME,
                TOKENS_JSON_ENV_NAME,
                "permission-controlled --token-file",
            ],
            "tokens_read_during_dry_run": False,
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
            "load_model": {
                "ramp": "bounded closed-loop request workers; not an open-loop arrival-rate model",
                "closed_loop_tiers": "one response-waiting task per synthetic VU",
                "open_loop_supported": False,
                "human_user_conversion": False,
            },
        },
        "limitations": [
            "dry-run validates configuration only and is not capacity evidence",
            (
                "live pressure remains blocked until the plan binds exact release, applied "
                "migration, and worker heartbeat identity"
            ),
            "synthetic VU and request concurrency are not human users or licensed seats",
            "staff journey role mix and pacing are hypotheses until calibrated from production traces",
        ],
    }

__all__ = [name for name in globals() if not name.startswith("__")]
