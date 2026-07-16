from __future__ import annotations

from scripts.ops.load_test_calibration import *


def redact_secrets(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {
            str(key): (
                "***"
                if str(key).lower() in {"token", "authorization", "password", "secret"}
                else redact_secrets(item)
            )
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [redact_secrets(item) for item in value]
    if isinstance(value, tuple):
        return [redact_secrets(item) for item in value]
    if isinstance(value, str):
        redacted = value
        for pattern in _SECRET_PATTERNS:
            redacted = pattern.sub("***", redacted)
        return redacted
    return value


def report_contains_secret(report: Mapping[str, Any], token: str | None) -> bool:
    encoded = json.dumps(report, ensure_ascii=False)
    if token and token in encoded:
        return True
    return any(pattern.search(encoded) for pattern in _SECRET_PATTERNS)

class RawSampleWriter:
    """Write secret-free request evidence as exclusive, owner-only NDJSON."""

    _FIELDS = (
        "recorded_at",
        "profile",
        "stage",
        "tier_index",
        "trial_index",
        "request_index",
        "virtual_user_id",
        "session_slot",
        "journey_profile",
        "journey_role",
        "journey_step_index",
        "endpoint",
        "category",
        "status",
        "ok",
        "latency_ms",
        "bytes",
        "error_type",
        "error_detail",
    )

    def __init__(self, path: Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        fd = os.open(self.path, flags, 0o600)
        self._stream: TextIO = io.TextIOWrapper(os.fdopen(fd, "wb", closefd=True), encoding="utf-8")
        self._digest = hashlib.sha256()
        self.count = 0
        self.closed = False

    def write(self, sample: Mapping[str, Any]) -> None:
        if self.closed:
            raise RuntimeError("raw sample writer is closed")
        public = {key: sample.get(key) for key in self._FIELDS if key in sample}
        public.setdefault("recorded_at", utc_now())
        clean = redact_secrets(public)
        encoded = json.dumps(clean, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n"
        if report_contains_secret(clean, None):
            raise RuntimeError("refusing to write secret-like raw sample")
        self._stream.write(encoded)
        self._digest.update(encoded.encode("utf-8"))
        self.count += 1

    def close(self) -> dict[str, Any]:
        if not self.closed:
            self._stream.flush()
            os.fsync(self._stream.buffer.fileno())
            self._stream.close()
            self.closed = True
        return {
            "format": "ndjson",
            "path": str(self.path),
            "sample_count": self.count,
            "sha256": self._digest.hexdigest(),
            "contains_headers_or_tokens": False,
            "file_mode": "0600",
        }

    def __enter__(self) -> "RawSampleWriter":
        return self

    def __exit__(self, *_exc: object) -> None:
        self.close()


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def validate_loopback_base(base: str) -> str:
    clean = str(base or "").strip().rstrip("/")
    parsed = urlparse(clean)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ValueError("base URL must be absolute HTTP(S)")
    if parsed.username or parsed.password:
        raise ValueError("credentials in base URL are forbidden")
    host = str(parsed.hostname or "").lower()
    if host not in {"localhost", "127.0.0.1", "::1"}:
        raise ValueError("capacity runner only permits loopback targets")
    if parsed.query or parsed.fragment or parsed.path not in {"", "/"}:
        raise ValueError("base URL must not include a path, query, or fragment")
    return clean


def _tokens_from_json(raw: str, *, source: str) -> list[str]:
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError(f"{source} must contain a JSON token array") from exc
    if isinstance(payload, Mapping):
        payload = payload.get("tokens")
    if not isinstance(payload, list) or not all(isinstance(item, str) for item in payload):
        raise ValueError(f"{source} must contain a JSON token array")
    if len(payload) > MAX_SOAK_VIRTUAL_USERS:
        raise ValueError(
            f"{source} token entry count cannot exceed {MAX_SOAK_VIRTUAL_USERS}; "
            "refusing before deduplication, session creation, or preflight"
        )
    tokens = [item.strip() for item in payload if item.strip()]
    if not tokens:
        raise ValueError(f"{source} contains no non-empty tokens")
    return tokens


def _read_controlled_token_file(path: Path) -> list[str]:
    candidate = Path(path).expanduser()
    encoded = _secure_read_regular_file(
        candidate,
        max_bytes=MAX_TOKEN_FILE_BYTES,
        label="token file",
        require_owner=True,
        require_private=True,
    )
    try:
        decoded = encoded.decode("utf-8")
    except UnicodeError as exc:
        raise ValueError("token file must be UTF-8 JSON") from exc
    return _tokens_from_json(decoded, source="token file")


def resolve_token_pool(token_file: Path | None = None) -> tuple[tuple[str, ...], dict[str, Any]]:
    """Load auth identities only from explicit env/file inputs, never login or DB helpers."""
    tokens: list[str] = []
    sources: list[str] = []
    raw_pool = str(os.environ.get(TOKENS_JSON_ENV_NAME) or "").strip()
    if raw_pool:
        tokens.extend(_tokens_from_json(raw_pool, source=TOKENS_JSON_ENV_NAME))
        sources.append(TOKENS_JSON_ENV_NAME)
    single = str(os.environ.get(TOKEN_ENV_NAME) or "").strip()
    if single:
        tokens.append(single)
        sources.append(TOKEN_ENV_NAME)
    if token_file is not None:
        tokens.extend(_read_controlled_token_file(token_file))
        sources.append("permission_controlled_json_file")
    if len(tokens) > MAX_SOAK_VIRTUAL_USERS:
        raise ValueError(
            f"combined token entry count cannot exceed {MAX_SOAK_VIRTUAL_USERS}; "
            "refusing before deduplication, session creation, or preflight"
        )
    unique = tuple(dict.fromkeys(tokens))
    if len(unique) > MAX_SOAK_VIRTUAL_USERS:
        raise ValueError(
            f"token pool cannot exceed {MAX_SOAK_VIRTUAL_USERS} identities; "
            "refusing before creating sessions or issuing preflight requests"
        )
    return unique, {
        "sources": sources or ["not_provided"],
        "token_count": len(unique),
        "token_emitted": False,
        "token_persisted": False,
        "implicit_login_or_database_lookup": False,
    }


def validate_telemetry_run_nonce(value: str | None) -> str | None:
    raw = str(value or "").strip()
    if not raw:
        return None
    if not re.fullmatch(r"[A-Za-z0-9_-]{16,128}", raw):
        raise ValueError("telemetry run nonce must be 16-128 URL-safe characters")
    return raw


def endpoints_for_profile(profile: str) -> tuple[Endpoint, ...]:
    if profile == "mixed":
        return ENDPOINTS
    return tuple(item for item in ENDPOINTS if item.category == profile)


def planned_preflight_request_count(args: argparse.Namespace, session_count: int) -> int:
    profiles = parse_profiles(args.profiles)
    selected_profiles = profiles if args.mode == "ramp" else (args.soak_profile,)
    endpoint_checks = sum(len(endpoints_for_profile(profile)) for profile in selected_profiles)
    identity_checks = IDENTITY_REQUESTS_PER_CONTEXT if resolve_journey_profile(args.journey_profile) else 0
    return RUNTIME_IDENTITY_PREFLIGHT_REQUESTS + int(session_count) * (
        endpoint_checks + identity_checks
    )


def resolve_journey_profile(name: str) -> JourneyProfile | None:
    clean = str(name or "none").strip()
    if clean == "none":
        return None
    try:
        profile = JOURNEY_PROFILES[clean]
    except KeyError as exc:
        raise ValueError(f"unknown journey profile: {clean}") from exc
    validate_journey_profile(profile, ENDPOINTS)
    return profile


def validate_journey_profile(
    profile: JourneyProfile,
    allowed_endpoints: Sequence[Endpoint],
) -> None:
    allowed = {endpoint.name: endpoint for endpoint in allowed_endpoints}
    if not profile.profile_id or not profile.version or not profile.roles:
        raise ValueError("journey profile id, version, and roles are required")
    role_names: set[str] = set()
    for role in profile.roles:
        if not role.name or role.name in role_names or role.weight <= 0 or not role.steps:
            raise ValueError("journey roles require unique names, positive weights, and steps")
        role_names.add(role.name)
        for step in role.steps:
            endpoint = allowed.get(step.endpoint_name)
            if endpoint is None:
                raise ValueError(f"journey endpoint is outside the hard-coded allowlist: {step.endpoint_name}")
            if step.think_time_ms < 0 or step.think_time_ms > 60_000:
                raise ValueError("journey step think time must be in [0, 60000] ms")


def deterministic_journey_role(
    profile: JourneyProfile,
    *,
    seed: int,
    virtual_user_id: int,
) -> JourneyRole:
    """Assign one stable role without claiming the weights are production-observed."""
    population: list[JourneyRole] = []
    for role in profile.roles:
        population.extend([role] * role.weight)
    digest = hashlib.sha256(
        f"{profile.profile_id}:{profile.version}:{seed}:{virtual_user_id}".encode("utf-8")
    ).digest()
    return population[int.from_bytes(digest[:8], "big") % len(population)]


def deterministic_journey_step(
    profile: JourneyProfile,
    *,
    seed: int,
    virtual_user_id: int,
    request_index: int,
) -> tuple[Endpoint, JourneyRole, int, float]:
    role = deterministic_journey_role(profile, seed=seed, virtual_user_id=virtual_user_id)
    step_index = int(request_index) % len(role.steps)
    step = role.steps[step_index]
    return ENDPOINT_BY_NAME[step.endpoint_name], role, step_index, step.think_time_ms


def weighted_workload(endpoints: Sequence[Endpoint], total: int, *, seed: int) -> list[Endpoint]:
    if not endpoints or total <= 0:
        return []
    population: list[Endpoint] = []
    for endpoint in endpoints:
        population.extend([endpoint] * max(1, int(endpoint.weight)))
    rng = random.Random(seed)
    workload: list[Endpoint] = []
    # Shuffle complete weighted cycles, rather than independently sampling every
    # request.  This keeps the order seed-reproducible while ensuring the
    # requested mix is exact for every complete cycle (and differs by at most
    # one cycle for a partial tail).
    while len(workload) < total:
        cycle = list(population)
        rng.shuffle(cycle)
        workload.extend(cycle[: total - len(workload)])
    return workload


def deterministic_soak_endpoint(
    endpoints: Sequence[Endpoint],
    *,
    seed: int,
    virtual_user_id: int,
    request_index: int,
) -> Endpoint:
    """Return a stable endpoint for one closed-loop virtual user's next request."""
    if not endpoints:
        raise ValueError("soak requires at least one endpoint")
    population: list[Endpoint] = []
    for endpoint in endpoints:
        population.extend([endpoint] * max(1, int(endpoint.weight)))
    digest = hashlib.sha256(f"{seed}:{virtual_user_id}".encode("utf-8")).digest()
    offset = int.from_bytes(digest[:8], "big") % len(population)
    return population[(offset + int(request_index)) % len(population)]


def workload_metadata(workload: Sequence[Endpoint], *, seed: int, algorithm: str) -> dict[str, Any]:
    names = [endpoint.name for endpoint in workload]
    return {
        "algorithm": algorithm,
        "seed": int(seed),
        "planned_requests": len(names),
        "planned_endpoint_counts": dict(sorted(Counter(names).items())),
        "order_sha256": hashlib.sha256("\n".join(names).encode("utf-8")).hexdigest(),
    }


def summarize_requests(
    results: Sequence[Mapping[str, Any]], elapsed_seconds: float, concurrency: int
) -> dict[str, Any]:
    latencies = [float(item.get("latency_ms") or 0.0) for item in results]
    ok_count = sum(1 for item in results if bool(item.get("ok")))
    total = len(results)
    by_endpoint: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
    by_category: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
    for item in results:
        by_endpoint[str(item.get("endpoint") or "unknown")].append(item)
        by_category[str(item.get("category") or "unknown")].append(item)

    def group_summary(entries: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
        group_latencies = [float(item.get("latency_ms") or 0.0) for item in entries]
        group_ok = sum(1 for item in entries if bool(item.get("ok")))
        return {
            "requests": len(entries),
            "success_count": group_ok,
            "failure_count": len(entries) - group_ok,
            "success_rate": round(group_ok / len(entries), 6) if entries else 0.0,
            "error_rate": round((len(entries) - group_ok) / len(entries), 6) if entries else 1.0,
            "p50_ms": round(percentile(group_latencies, 50), 2),
            "p95_ms": round(percentile(group_latencies, 95), 2),
            "p99_ms": round(percentile(group_latencies, 99), 2),
            "avg_ms": round(mean(group_latencies), 2) if group_latencies else 0.0,
            "status_codes": dict(
                sorted(Counter(str(item.get("status") or 0) for item in entries).items())
            ),
            "error_types": dict(
                sorted(
                    Counter(
                        str(item.get("error_type")) for item in entries if item.get("error_type")
                    ).items()
                )
            ),
        }

    return {
        "concurrency": int(concurrency),
        "total_requests": total,
        "success_count": ok_count,
        "failure_count": total - ok_count,
        "success_rate": round(ok_count / total, 6) if total else 0.0,
        "error_rate": round((total - ok_count) / total, 6) if total else 1.0,
        "elapsed_seconds": round(float(elapsed_seconds), 4),
        "requests_per_second": round(total / elapsed_seconds, 2) if elapsed_seconds > 0 else 0.0,
        "response_bytes": int(sum(int(item.get("bytes") or 0) for item in results)),
        "latency_ms": {
            "avg": round(mean(latencies), 2) if latencies else 0.0,
            "p50": round(percentile(latencies, 50), 2),
            "p95": round(percentile(latencies, 95), 2),
            "p99": round(percentile(latencies, 99), 2),
            "max": round(max(latencies), 2) if latencies else 0.0,
        },
        "status_codes": dict(sorted(Counter(str(item.get("status") or 0) for item in results).items())),
        "error_types": dict(
            sorted(Counter(str(item.get("error_type")) for item in results if item.get("error_type")).items())
        ),
        "by_endpoint": {key: group_summary(value) for key, value in sorted(by_endpoint.items())},
        "by_category": {key: group_summary(value) for key, value in sorted(by_category.items())},
    }


def _histogram_percentile(buckets: Mapping[int, int], total: int, pct: float) -> float:
    if total <= 0:
        return 0.0
    target = max(1, math.ceil((float(pct) / 100.0) * total))
    cumulative = 0
    for bucket, count in sorted((int(key), int(value)) for key, value in buckets.items()):
        cumulative += count
        if cumulative >= target:
            return float(bucket) / 10.0
    return float(max(buckets, default=0)) / 10.0


@dataclass
class _MetricAccumulator:
    total: int = 0
    success: int = 0
    response_bytes: int = 0
    latency_sum_ms: float = 0.0
    latency_max_ms: float = 0.0
    latency_buckets_tenths_ms: Counter[int] = field(default_factory=Counter)
    status_codes: Counter[str] = field(default_factory=Counter)
    error_types: Counter[str] = field(default_factory=Counter)

    def add(self, item: Mapping[str, Any]) -> None:
        latency = max(0.0, float(item.get("latency_ms") or 0.0))
        self.total += 1
        self.success += int(bool(item.get("ok")))
        self.response_bytes += int(item.get("bytes") or 0)
        self.latency_sum_ms += latency
        self.latency_max_ms = max(self.latency_max_ms, latency)
        self.latency_buckets_tenths_ms[math.ceil(latency * 10.0)] += 1
        self.status_codes[str(item.get("status") or 0)] += 1
        if item.get("error_type"):
            self.error_types[str(item["error_type"])] += 1

    def group_summary(self) -> dict[str, Any]:
        return {
            "requests": self.total,
            "success_count": self.success,
            "failure_count": self.total - self.success,
            "success_rate": round(self.success / self.total, 6) if self.total else 0.0,
            "error_rate": round((self.total - self.success) / self.total, 6) if self.total else 1.0,
            "p50_ms": round(_histogram_percentile(self.latency_buckets_tenths_ms, self.total, 50), 2),
            "p95_ms": round(_histogram_percentile(self.latency_buckets_tenths_ms, self.total, 95), 2),
            "p99_ms": round(_histogram_percentile(self.latency_buckets_tenths_ms, self.total, 99), 2),
            "avg_ms": round(self.latency_sum_ms / self.total, 2) if self.total else 0.0,
            "status_codes": dict(sorted(self.status_codes.items())),
            "error_types": dict(sorted(self.error_types.items())),
        }


@dataclass
class _StreamingRequestAccumulator:
    overall: _MetricAccumulator = field(default_factory=_MetricAccumulator)
    by_endpoint: dict[str, _MetricAccumulator] = field(default_factory=dict)
    by_category: dict[str, _MetricAccumulator] = field(default_factory=dict)

    def add(self, item: Mapping[str, Any]) -> None:
        endpoint = str(item.get("endpoint") or "unknown")
        category = str(item.get("category") or "unknown")
        self.overall.add(item)
        self.by_endpoint.setdefault(endpoint, _MetricAccumulator()).add(item)
        self.by_category.setdefault(category, _MetricAccumulator()).add(item)

    def summary(self, elapsed_seconds: float, concurrency: int) -> dict[str, Any]:
        metric = self.overall
        return {
            "concurrency": int(concurrency),
            "total_requests": metric.total,
            "success_count": metric.success,
            "failure_count": metric.total - metric.success,
            "success_rate": round(metric.success / metric.total, 6) if metric.total else 0.0,
            "error_rate": round((metric.total - metric.success) / metric.total, 6) if metric.total else 1.0,
            "elapsed_seconds": round(float(elapsed_seconds), 4),
            "requests_per_second": round(metric.total / elapsed_seconds, 2) if elapsed_seconds > 0 else 0.0,
            "response_bytes": metric.response_bytes,
            "latency_ms": {
                "avg": round(metric.latency_sum_ms / metric.total, 2) if metric.total else 0.0,
                "p50": round(_histogram_percentile(metric.latency_buckets_tenths_ms, metric.total, 50), 2),
                "p95": round(_histogram_percentile(metric.latency_buckets_tenths_ms, metric.total, 95), 2),
                "p99": round(_histogram_percentile(metric.latency_buckets_tenths_ms, metric.total, 99), 2),
                "max": round(metric.latency_max_ms, 2),
                "histogram_resolution_ms": 0.1,
            },
            "status_codes": dict(sorted(metric.status_codes.items())),
            "error_types": dict(sorted(metric.error_types.items())),
            "by_endpoint": {
                key: value.group_summary() for key, value in sorted(self.by_endpoint.items())
            },
            "by_category": {
                key: value.group_summary() for key, value in sorted(self.by_category.items())
            },
        }


def _min_median_max(values: Sequence[float]) -> dict[str, float | None]:
    cleaned = [float(value) for value in values]
    if not cleaned:
        return {"min": None, "median": None, "max": None}
    return {
        "min": round(min(cleaned), 6),
        "median": round(float(median(cleaned)), 6),
        "max": round(max(cleaned), 6),
    }


def aggregate_trial_summaries(
    trials: Sequence[Mapping[str, Any]],
    thresholds: Thresholds,
    *,
    concurrency: int,
    load_model: str,
) -> dict[str, Any]:
    """Build conservative gates plus min/median/max repeatability evidence."""
    if not trials:
        raise ValueError("cannot aggregate an empty trial set")
    rps = [float(item.get("requests_per_second") or 0.0) for item in trials]
    errors = [float(item.get("error_rate") or 0.0) for item in trials]
    p50 = [float((item.get("latency_ms") or {}).get("p50") or 0.0) for item in trials]
    p95 = [float((item.get("latency_ms") or {}).get("p95") or 0.0) for item in trials]
    p99 = [float((item.get("latency_ms") or {}).get("p99") or 0.0) for item in trials]
    status_codes: Counter[str] = Counter()
    error_types: Counter[str] = Counter()
    endpoints = sorted(
        {
            str(name)
            for item in trials
            for name in ((item.get("by_endpoint") or {}).keys())
        }
    )
    for trial in trials:
        status_codes.update({str(key): int(value) for key, value in (trial.get("status_codes") or {}).items()})
        error_types.update({str(key): int(value) for key, value in (trial.get("error_types") or {}).items()})

    endpoint_stats: dict[str, Any] = {}
    for endpoint in endpoints:
        groups = [
            item.get("by_endpoint", {}).get(endpoint, {})
            for item in trials
            if endpoint in item.get("by_endpoint", {})
        ]
        endpoint_status: Counter[str] = Counter()
        endpoint_errors: Counter[str] = Counter()
        for group in groups:
            endpoint_status.update(
                {str(key): int(value) for key, value in (group.get("status_codes") or {}).items()}
            )
            endpoint_errors.update(
                {str(key): int(value) for key, value in (group.get("error_types") or {}).items()}
            )
        endpoint_stats[endpoint] = {
            "trial_count": len(groups),
            "requests": _min_median_max([float(group.get("requests") or 0) for group in groups]),
            "error_rate": _min_median_max([float(group.get("error_rate") or 0.0) for group in groups]),
            "p50_ms": _min_median_max([float(group.get("p50_ms") or 0.0) for group in groups]),
            "p95_ms": _min_median_max([float(group.get("p95_ms") or 0.0) for group in groups]),
            "p99_ms": _min_median_max([float(group.get("p99_ms") or 0.0) for group in groups]),
            "status_codes": dict(sorted(endpoint_status.items())),
            "error_types": dict(sorted(endpoint_errors.items())),
        }

    # Threshold decisions use the worst repeat, while repeatability is exposed
    # independently below.  This prevents a median from hiding one bad trial.
    conservative = {
        "concurrency": int(concurrency),
        "total_requests": sum(int(item.get("total_requests") or 0) for item in trials),
        "requests_per_second": round(float(median(rps)), 2),
        "error_rate": round(max(errors), 6),
        "latency_ms": {
            "p50": round(float(median(p50)), 2),
            "p95": round(max(p95), 2),
            "p99": round(max(p99), 2),
        },
        "status_codes": dict(sorted(status_codes.items())),
        "error_types": dict(sorted(error_types.items())),
    }
    reasons = stop_reasons(conservative, thresholds)
    return {
        **conservative,
        "load_model": load_model,
        "trial_count": len(trials),
        "threshold_policy": "worst trial for error_rate/p95/p99; median trial RPS",
        "threshold_pass": not reasons,
        "stop_reasons": reasons,
        "across_trials": {
            "requests_per_second": _min_median_max(rps),
            "error_rate": _min_median_max(errors),
            "latency_ms": {
                "p50": _min_median_max(p50),
                "p95": _min_median_max(p95),
                "p99": _min_median_max(p99),
            },
            "by_endpoint": endpoint_stats,
        },
        "trials": list(trials),
    }


def stop_reasons(summary: Mapping[str, Any], thresholds: Thresholds) -> list[str]:
    reasons: list[str] = []
    latency = summary.get("latency_ms") if isinstance(summary.get("latency_ms"), Mapping) else {}
    if float(summary.get("error_rate") or 0.0) > thresholds.max_error_rate:
        reasons.append("error_rate")
    if float(latency.get("p95") or 0.0) > thresholds.max_p95_ms:
        reasons.append("p95_latency")
    if float(latency.get("p99") or 0.0) > thresholds.max_p99_ms:
        reasons.append("p99_latency")
    status_codes = summary.get("status_codes") if isinstance(summary.get("status_codes"), Mapping) else {}
    if any(int(code) >= 500 and int(count) > 0 for code, count in status_codes.items() if str(code).isdigit()):
        reasons.append("server_5xx")
    return sorted(set(reasons))


def endpoint_stop_reasons(
    summary: Mapping[str, Any],
    endpoint_thresholds: Mapping[str, Thresholds] | None,
) -> list[str]:
    """Apply endpoint budgets so a fast endpoint cannot hide behind a heavy mix."""
    if not endpoint_thresholds:
        return []
    by_endpoint = summary.get("by_endpoint")
    if not isinstance(by_endpoint, Mapping):
        across = summary.get("across_trials")
        by_endpoint = across.get("by_endpoint") if isinstance(across, Mapping) else None
    if not isinstance(by_endpoint, Mapping):
        return []

    def metric_value(group: Mapping[str, Any], key: str) -> float:
        value = group.get(key)
        if isinstance(value, Mapping):
            return float(value.get("max") or 0.0)
        return float(value or 0.0)

    reasons: list[str] = []
    for endpoint_name, budget in endpoint_thresholds.items():
        group = by_endpoint.get(endpoint_name)
        if not isinstance(group, Mapping):
            continue
        if metric_value(group, "error_rate") > budget.max_error_rate:
            reasons.append(f"endpoint:{endpoint_name}:error_rate")
        if metric_value(group, "p95_ms") > budget.max_p95_ms:
            reasons.append(f"endpoint:{endpoint_name}:p95_latency")
        if metric_value(group, "p99_ms") > budget.max_p99_ms:
            reasons.append(f"endpoint:{endpoint_name}:p99_latency")
        statuses = group.get("status_codes")
        if isinstance(statuses, Mapping) and any(
            str(code).isdigit() and int(code) >= 500 and int(count) > 0
            for code, count in statuses.items()
        ):
            reasons.append(f"endpoint:{endpoint_name}:server_5xx")
    return sorted(set(reasons))


def combined_stop_reasons(
    summary: Mapping[str, Any],
    thresholds: Thresholds,
    endpoint_thresholds: Mapping[str, Thresholds] | None = None,
) -> list[str]:
    return sorted(
        set(stop_reasons(summary, thresholds) + endpoint_stop_reasons(summary, endpoint_thresholds))
    )


def capacity_interpretation(phases: Sequence[Mapping[str, Any]], thresholds: Thresholds) -> dict[str, Any]:
    accepted = [phase for phase in phases if not stop_reasons(phase, thresholds)]
    failed = [phase for phase in phases if stop_reasons(phase, thresholds)]
    best = max(accepted, key=lambda item: float(item.get("requests_per_second") or 0.0), default=None)
    max_accepted = max((int(item.get("concurrency") or 0) for item in accepted), default=0)
    first_failed = failed[0] if failed else None

    latency_knee: int | None = None
    if phases:
        base_p95 = max(1.0, float((phases[0].get("latency_ms") or {}).get("p95") or 0.0))
        knee_threshold = max(250.0, base_p95 * 2.5)
        for phase in phases[1:]:
            if float((phase.get("latency_ms") or {}).get("p95") or 0.0) >= knee_threshold:
                latency_knee = int(phase.get("concurrency") or 0)
                break

    best_rps = float(best.get("requests_per_second") or 0.0) if best else 0.0
    return {
        "accepted_max_concurrency": max_accepted,
        "first_failed_concurrency": int(first_failed.get("concurrency") or 0) if first_failed else None,
        "first_failed_reasons": stop_reasons(first_failed, thresholds) if first_failed else [],
        "latency_knee_concurrency": latency_knee,
        "best_accepted_rps": round(best_rps, 2),
        "capacity_statement": (
            f"observed local test point: {max_accepted} concurrent in-flight requests passed thresholds"
            if accepted and not failed
            else f"local breakpoint observed at concurrency {int(first_failed.get('concurrency') or 0)}"
            if first_failed
            else "no phase passed the configured thresholds"
        ),
        "human_user_capacity": None,
        "interpretation_boundary": {
            "virtual_users_are_human_users": False,
            "conversion_performed": False,
            "warning": (
                "request concurrency and synthetic VU are not human users; no seat/user capacity "
                "conversion is valid without a measured production journey and arrival model"
            ),
        },
    }


def journey_capacity_interpretation(
    stages: Sequence[Mapping[str, Any]],
    profile: JourneyProfile | None,
    *,
    pacing_scale: float = 1.0,
) -> dict[str, Any]:
    """Describe measured synthetic sessions while keeping human capacity unknown."""
    passed = [stage for stage in stages if bool(stage.get("threshold_pass"))]
    failed = [stage for stage in stages if not bool(stage.get("threshold_pass"))]
    accepted = max((int(stage.get("virtual_users") or 0) for stage in passed), default=0)
    first_failed = int(failed[0].get("virtual_users") or 0) if failed else None
    return {
        "observed_closed_loop_test_points": [
            {
                "simulated_active_sessions": int(stage.get("virtual_users") or 0),
                "duration_seconds": stage.get("duration_seconds"),
                "threshold_pass": bool(stage.get("threshold_pass")),
                "requests_per_second": stage.get("requests_per_second"),
                "error_rate": stage.get("error_rate"),
                "p95_ms": (stage.get("latency_ms") or {}).get("p95"),
                "p99_ms": (stage.get("latency_ms") or {}).get("p99"),
            }
            for stage in stages
        ],
        "accepted_max_simulated_active_sessions": accepted,
        "first_failed_simulated_active_sessions": first_failed,
        "journey_profile": (
            profile.public_dict(pacing_scale=pacing_scale) if profile is not None else None
        ),
        "human_user_capacity": None,
        "conversion_performed": False,
        "capacity_statement": (
            f"observed test point: {accepted} synthetic active sessions passed configured thresholds"
            if accepted
            else "no synthetic active-session tier passed configured thresholds"
        ),
        "required_before_human_capacity_claim": [
            "production role-mix and think-time traces with a documented observation window",
            "one independent authenticated identity and cookie session per tested active user",
            "representative read/write/provider journey mix rather than read-only GETs",
            "staging or production-like infrastructure with DB pool, Redis, worker, and queue telemetry",
            "repeatable threshold pass plus long-soak and recovery evidence",
        ],
    }

__all__ = [name for name in globals() if not name.startswith("__")]
