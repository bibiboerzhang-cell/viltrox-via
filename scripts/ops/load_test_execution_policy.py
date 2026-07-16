from __future__ import annotations

from scripts.ops.load_test_contracts import *


MAX_RAMP_CONCURRENCY = 1_000
MAX_RAMP_STAGES = 32
MAX_RAMP_REQUESTS_PER_PHASE = 250_000
MAX_RAMP_WAVES_PER_PHASE = 1_000
MAX_RAMP_REQUESTS_PER_STAGE = 250_000
MAX_RAMP_TOTAL_REQUESTS = 1_000_000
MAX_CLOSED_LOOP_TIERS = 32
MAX_CLOSED_LOOP_TOTAL_DURATION_SECONDS = 86_400.0
MAX_CLOSED_LOOP_TOTAL_VU_SECONDS = 3_600_000.0
MAX_CLOSED_LOOP_TOTAL_REQUESTS = 10_000_000
DEFAULT_CAPACITY_EXECUTION_NONCE_LEDGER_DIR = (
    DEFAULT_REPORT_DIR / "capacity-approval-nonce-ledger"
)
TARGET_RUNTIME_IDENTITY_SCHEMA = "vkpi-load-target-runtime-identity/v1"
TARGET_RUNTIME_HEALTH_PATH = "/health"
DEFAULT_MAX_WORKER_HEARTBEAT_AGE_SECONDS = 180
MAX_WORKER_HEARTBEAT_AGE_SECONDS = 600
RUNTIME_IDENTITY_PREFLIGHT_REQUESTS = 1
_RUNTIME_RELEASE_SHA_RE = re.compile(r"^[0-9a-f]{40}$")
_RUNTIME_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_RUNTIME_MIGRATION_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,254}$")


def capacity_path_binding_sha256(path: Path | None) -> str | None:
    """Bind a configured path without opening the referenced file."""
    if path is None:
        return None
    normalized = str(Path(path).expanduser().resolve(strict=False))
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


class FrozenCapacityExecutionArgs:
    """Immutable snapshot accepted only after a canonical-plan hash match."""

    __slots__ = ("_values", "_plan_sha256", "_capability")

    def __init__(
        self,
        values: Mapping[str, Any],
        *,
        plan_sha256: str,
        capability: object,
    ) -> None:
        if capability is not _FROZEN_CAPACITY_ARGS_CAPABILITY:
            raise TypeError("capacity execution args require the canonical freezer")
        object.__setattr__(self, "_values", MappingProxyType(dict(values)))
        object.__setattr__(self, "_plan_sha256", str(plan_sha256))
        object.__setattr__(self, "_capability", capability)

    def __getattr__(self, name: str) -> Any:
        try:
            return self._values[name]
        except KeyError as exc:
            raise AttributeError(name) from exc

    def __setattr__(self, _name: str, _value: Any) -> None:
        raise AttributeError("capacity execution args are frozen")


_FROZEN_CAPACITY_ARGS_CAPABILITY = object()

CAPACITY_EXECUTION_BOUND_ARG_FIELDS = (
    "mode",
    "frontend_base",
    "backend_base",
    "profiles",
    "phases",
    "tiers",
    "trials",
    "requests_per_phase",
    "waves_per_phase",
    "seed",
    "timeout_seconds",
    "cooldown_seconds",
    "resource_sample_seconds",
    "max_error_rate",
    "max_p95_ms",
    "max_p99_ms",
    "max_response_bytes",
    "postgres_port",
    "redis_port",
    "session_count",
    "token_file",
    "expected_runtime_release_sha",
    "expected_migration_version",
    "expected_worker_release_sha",
    "expected_worker_boot_nonce_sha256",
    "worker_not_before",
    "max_worker_heartbeat_age_seconds",
    "execution_run_nonce",
    "execution_nonce_ledger_dir",
    "db_pool_telemetry_file",
    "redis_telemetry_file",
    "telemetry_run_nonce",
    "calibration_trace",
    "role_calibration",
    "calibration_source_sha256",
    "calibration_as_of",
    "calibration_attestation",
    "soak_seconds",
    "soak_profile",
    "soak_virtual_users",
    "soak_think_time_ms",
    "journey_profile",
    "journey_pacing_scale",
    "soak_window_seconds",
    "soak_max_requests",
)


def _canonical_runtime_timestamp(value: Any, *, field_name: str) -> str | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError(f"{field_name} must be a timezone-aware ISO timestamp") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError(f"{field_name} must be a timezone-aware ISO timestamp")
    return parsed.astimezone(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def build_target_runtime_identity_contract(args: Any) -> dict[str, Any]:
    """Normalize the exact runtime identity an operator is asked to approve.

    Missing values remain explicit for dry-run planning, but such a contract is
    not eligible for live approval.  Any supplied value is validated before it
    can enter the canonical plan hash.
    """

    runtime_sha = str(getattr(args, "expected_runtime_release_sha", None) or "").strip().lower()
    migration = str(getattr(args, "expected_migration_version", None) or "").strip()
    worker_sha = str(getattr(args, "expected_worker_release_sha", None) or "").strip().lower()
    boot_nonce = str(
        getattr(args, "expected_worker_boot_nonce_sha256", None) or ""
    ).strip().lower()
    worker_not_before = _canonical_runtime_timestamp(
        getattr(args, "worker_not_before", None),
        field_name="worker not-before",
    )
    max_age = int(
        getattr(
            args,
            "max_worker_heartbeat_age_seconds",
            DEFAULT_MAX_WORKER_HEARTBEAT_AGE_SECONDS,
        )
    )
    if runtime_sha and not _RUNTIME_RELEASE_SHA_RE.fullmatch(runtime_sha):
        raise ValueError("expected runtime release SHA must be 40 lowercase hex characters")
    if migration and not _RUNTIME_MIGRATION_RE.fullmatch(migration):
        raise ValueError("expected migration version contains unsupported characters")
    if worker_sha and not _RUNTIME_RELEASE_SHA_RE.fullmatch(worker_sha):
        raise ValueError("expected worker release SHA must be 40 lowercase hex characters")
    if boot_nonce and not _RUNTIME_SHA256_RE.fullmatch(boot_nonce):
        raise ValueError("expected worker boot nonce must be 64 lowercase hex characters")
    if not (1 <= max_age <= MAX_WORKER_HEARTBEAT_AGE_SECONDS):
        raise ValueError(
            "max worker heartbeat age must be in "
            f"[1, {MAX_WORKER_HEARTBEAT_AGE_SECONDS}] seconds"
        )
    complete = all((runtime_sha, migration, worker_sha, boot_nonce, worker_not_before))
    return {
        "schema_version": TARGET_RUNTIME_IDENTITY_SCHEMA,
        "complete": bool(complete),
        "health_path": TARGET_RUNTIME_HEALTH_PATH,
        "release": {
            "expected_server_git_sha": runtime_sha or None,
            "expected_client_git_sha": runtime_sha or None,
            "client_server_alignment_required": True,
        },
        "migration": {
            "orchestrator_contract": "alembic_bridge_over_schema_migrations",
            "expected_applied_version": migration or None,
            "required_runtime_field": "trust.db_migration_max",
            "required_source": "schema_migrations",
            "postgres_startup_and_migration_completion_required": True,
        },
        "worker": {
            "expected_release_sha": worker_sha or None,
            "expected_boot_nonce_sha256": boot_nonce or None,
            "not_before": worker_not_before,
            "maximum_heartbeat_age_seconds": max_age,
            "required_sha_source": "db_heartbeat",
            "required_heartbeat_source": "db_heartbeat",
            "online_required": True,
        },
        "preflight": {
            "requests": RUNTIME_IDENTITY_PREFLIGHT_REQUESTS,
            "must_pass_before_pressure": True,
            "raw_health_payload_persisted": False,
        },
    }


def target_runtime_identity_contract_valid(value: Any) -> bool:
    if not isinstance(value, Mapping):
        return False
    try:
        release = value["release"]
        migration = value["migration"]
        worker = value["worker"]
        preflight = value["preflight"]
        not_before = _canonical_runtime_timestamp(
            worker.get("not_before"), field_name="worker not-before"
        )
        max_age = worker.get("maximum_heartbeat_age_seconds")
        return bool(
            value.get("schema_version") == TARGET_RUNTIME_IDENTITY_SCHEMA
            and value.get("complete") is True
            and value.get("health_path") == TARGET_RUNTIME_HEALTH_PATH
            and isinstance(release, Mapping)
            and _RUNTIME_RELEASE_SHA_RE.fullmatch(
                str(release.get("expected_server_git_sha") or "")
            )
            and release.get("expected_client_git_sha")
            == release.get("expected_server_git_sha")
            and release.get("client_server_alignment_required") is True
            and isinstance(migration, Mapping)
            and migration.get("orchestrator_contract")
            == "alembic_bridge_over_schema_migrations"
            and _RUNTIME_MIGRATION_RE.fullmatch(
                str(migration.get("expected_applied_version") or "")
            )
            and migration.get("required_runtime_field") == "trust.db_migration_max"
            and migration.get("required_source") == "schema_migrations"
            and migration.get("postgres_startup_and_migration_completion_required") is True
            and isinstance(worker, Mapping)
            and _RUNTIME_RELEASE_SHA_RE.fullmatch(
                str(worker.get("expected_release_sha") or "")
            )
            and _RUNTIME_SHA256_RE.fullmatch(
                str(worker.get("expected_boot_nonce_sha256") or "")
            )
            and not_before is not None
            and isinstance(max_age, int)
            and not isinstance(max_age, bool)
            and 1 <= max_age <= MAX_WORKER_HEARTBEAT_AGE_SECONDS
            and worker.get("required_sha_source") == "db_heartbeat"
            and worker.get("required_heartbeat_source") == "db_heartbeat"
            and worker.get("online_required") is True
            and isinstance(preflight, Mapping)
            and preflight.get("requests") == RUNTIME_IDENTITY_PREFLIGHT_REQUESTS
            and preflight.get("must_pass_before_pressure") is True
            and preflight.get("raw_health_payload_persisted") is False
        )
    except (KeyError, TypeError, ValueError):
        return False


def capacity_execution_bound_args_sha256(args: Any) -> str:
    """Hash every load/evidence input without serializing raw paths or nonces."""
    nonce_fields = {"execution_run_nonce", "telemetry_run_nonce"}
    projection: dict[str, Any] = {}
    for field_name in CAPACITY_EXECUTION_BOUND_ARG_FIELDS:
        value = getattr(args, field_name)
        if field_name in nonce_fields:
            projection[field_name] = (
                hashlib.sha256(str(value).encode("utf-8")).hexdigest()
                if value is not None
                else None
            )
        elif isinstance(value, Path):
            projection[field_name] = capacity_path_binding_sha256(value)
        else:
            projection[field_name] = value
    return _canonical_json_sha256(projection)


def validate_capacity_execution_hard_bounds(args: Any) -> dict[str, int | float]:
    """Enforce code-owned aggregate caps that no signed plan can enlarge."""
    mode = str(args.mode)
    if mode == "closed-loop-tiers":
        tiers = parse_vu_duration_tiers(args.tiers)
        trials = int(args.trials)
        requests_per_trial = int(args.soak_max_requests)
        if len(tiers) > MAX_CLOSED_LOOP_TIERS:
            raise ValueError(
                "closed-loop tiers cannot exceed hard limit "
                f"{MAX_CLOSED_LOOP_TIERS}"
            )
        if not (1 <= trials <= MAX_TRIALS):
            raise ValueError(f"closed-loop trials must be in [1, {MAX_TRIALS}]")
        total_duration_seconds = trials * sum(
            float(tier.duration_seconds) for tier in tiers
        )
        if total_duration_seconds > MAX_CLOSED_LOOP_TOTAL_DURATION_SECONDS:
            raise ValueError(
                "planned closed-loop total duration "
                f"{total_duration_seconds:g}s exceeds hard limit "
                f"{MAX_CLOSED_LOOP_TOTAL_DURATION_SECONDS:g}s"
            )
        total_vu_seconds = trials * sum(
            int(tier.virtual_users) * float(tier.duration_seconds)
            for tier in tiers
        )
        if total_vu_seconds > MAX_CLOSED_LOOP_TOTAL_VU_SECONDS:
            raise ValueError(
                "planned closed-loop total VU-seconds "
                f"{total_vu_seconds:g} exceeds hard limit "
                f"{MAX_CLOSED_LOOP_TOTAL_VU_SECONDS:g}"
            )
        total_requests = trials * len(tiers) * requests_per_trial
        if total_requests > MAX_CLOSED_LOOP_TOTAL_REQUESTS:
            raise ValueError(
                f"planned closed-loop total requests {total_requests} exceed hard limit "
                f"{MAX_CLOSED_LOOP_TOTAL_REQUESTS}"
            )
        return {
            "maximum_closed_loop_tiers": MAX_CLOSED_LOOP_TIERS,
            "maximum_closed_loop_total_duration_seconds": (
                MAX_CLOSED_LOOP_TOTAL_DURATION_SECONDS
            ),
            "maximum_closed_loop_total_vu_seconds": MAX_CLOSED_LOOP_TOTAL_VU_SECONDS,
            "maximum_closed_loop_total_requests": MAX_CLOSED_LOOP_TOTAL_REQUESTS,
            "planned_closed_loop_tiers": len(tiers),
            "planned_closed_loop_total_duration_seconds": total_duration_seconds,
            "planned_closed_loop_total_vu_seconds": total_vu_seconds,
            "planned_pressure_requests": total_requests,
        }
    if mode != "ramp":
        raise ValueError(f"unsupported capacity mode for hard bounds: {mode}")
    phases = parse_positive_ints(args.phases)
    profiles = parse_profiles(args.profiles)
    trials = int(args.trials)
    requests_per_phase = int(args.requests_per_phase)
    waves_per_phase = int(args.waves_per_phase)
    if len(phases) > MAX_RAMP_STAGES:
        raise ValueError(f"ramp stages cannot exceed hard limit {MAX_RAMP_STAGES}")
    if max(phases, default=0) > MAX_RAMP_CONCURRENCY:
        raise ValueError(
            f"ramp concurrency cannot exceed hard limit {MAX_RAMP_CONCURRENCY}"
        )
    if not (1 <= requests_per_phase <= MAX_RAMP_REQUESTS_PER_PHASE):
        raise ValueError(
            "ramp requests per phase must be in "
            f"[1, {MAX_RAMP_REQUESTS_PER_PHASE}]"
        )
    if not (1 <= waves_per_phase <= MAX_RAMP_WAVES_PER_PHASE):
        raise ValueError(
            "ramp waves per phase must be in "
            f"[1, {MAX_RAMP_WAVES_PER_PHASE}]"
        )
    if not (1 <= trials <= MAX_TRIALS):
        raise ValueError(f"ramp trials must be in [1, {MAX_TRIALS}]")
    requests_by_stage = tuple(
        max(requests_per_phase, int(concurrency) * waves_per_phase)
        for concurrency in phases
    )
    if max(requests_by_stage, default=0) > MAX_RAMP_REQUESTS_PER_STAGE:
        raise ValueError(
            "ramp requests per stage cannot exceed hard limit "
            f"{MAX_RAMP_REQUESTS_PER_STAGE}"
        )
    total_requests = len(profiles) * trials * sum(requests_by_stage)
    if total_requests > MAX_RAMP_TOTAL_REQUESTS:
        raise ValueError(
            f"planned ramp requests {total_requests} exceed hard limit "
            f"{MAX_RAMP_TOTAL_REQUESTS}"
        )
    return {
        "maximum_ramp_concurrency": MAX_RAMP_CONCURRENCY,
        "maximum_ramp_stages": MAX_RAMP_STAGES,
        "maximum_ramp_requests_per_phase": MAX_RAMP_REQUESTS_PER_PHASE,
        "maximum_ramp_waves_per_phase": MAX_RAMP_WAVES_PER_PHASE,
        "maximum_ramp_requests_per_stage": MAX_RAMP_REQUESTS_PER_STAGE,
        "maximum_ramp_total_requests": MAX_RAMP_TOTAL_REQUESTS,
        "planned_pressure_requests": total_requests,
    }


def capacity_report_execution_complete(report: Mapping[str, Any]) -> bool:
    """Defensively prove every selected profile/stage/trial emitted requests."""
    expectations = report.get("execution_expectations")
    profiles = report.get("profiles")
    if not isinstance(expectations, Mapping) or not isinstance(profiles, list):
        return False
    selected_profiles = expectations.get("selected_profiles")
    expected_stages = expectations.get("stages_per_profile")
    expected_trials = expectations.get("trials_per_stage")
    if (
        not isinstance(selected_profiles, list)
        or not selected_profiles
        or not all(isinstance(item, str) and item for item in selected_profiles)
        or not isinstance(expected_stages, int)
        or isinstance(expected_stages, bool)
        or expected_stages < 1
        or not isinstance(expected_trials, int)
        or isinstance(expected_trials, bool)
        or expected_trials < 1
        or len(profiles) != len(selected_profiles)
    ):
        return False
    if [item.get("profile") if isinstance(item, Mapping) else None for item in profiles] != (
        selected_profiles
    ):
        return False
    for profile in profiles:
        if not isinstance(profile, Mapping) or profile.get("status") != "completed":
            return False
        stages = profile.get("stages")
        if not isinstance(stages, list) or len(stages) != expected_stages:
            return False
        for stage_index, stage in enumerate(stages):
            if (
                not isinstance(stage, Mapping)
                or stage.get("stage_index") != stage_index
                or stage.get("threshold_pass") is not True
            ):
                return False
            trials = stage.get("trials")
            if not isinstance(trials, list) or len(trials) != expected_trials:
                return False
            for trial_index, trial in enumerate(trials):
                if (
                    not isinstance(trial, Mapping)
                    or trial.get("trial_index") != trial_index
                    or trial.get("threshold_pass") is not True
                    or not isinstance(trial.get("total_requests"), int)
                    or isinstance(trial.get("total_requests"), bool)
                    or int(trial["total_requests"]) < 1
                ):
                    return False
    network_requests_issued = report.get("network_requests_issued")
    return bool(
        isinstance(network_requests_issued, int)
        and not isinstance(network_requests_issued, bool)
        and network_requests_issued > 0
    )


def capacity_cli_summary_status(
    report: Mapping[str, Any],
    *,
    execute_live: bool,
    fixture_configured: bool,
) -> str:
    if not execute_live:
        return "fixture_complete" if fixture_configured else "dry_run_complete"
    if not bool((report.get("operator_preflight") or {}).get("trusted")):
        return "blocked"
    if any(item.get("status") == "blocked" for item in report.get("profiles", [])):
        return "blocked"
    if (
        report.get("pressure_completed") is True
        and capacity_report_execution_complete(report)
    ):
        return "complete"
    return "incomplete"


__all__ = [name for name in globals() if not name.startswith("__")]
