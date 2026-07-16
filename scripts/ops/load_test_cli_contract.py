from __future__ import annotations

import sys

from scripts.ops.load_test_legacy import *


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--execute-live",
        action="store_true",
        help="issue loopback GET requests; without this flag the command is a zero-network dry-run",
    )
    parser.add_argument(
        "--fixture",
        type=Path,
        help="offline deterministic JSON response fixture; mutually exclusive with --execute-live",
    )
    parser.add_argument("--mode", choices=("ramp", "closed-loop-tiers"), default="ramp")
    parser.add_argument("--frontend-base", default="http://127.0.0.1:5173")
    parser.add_argument("--backend-base", default="http://127.0.0.1:8102")
    parser.add_argument("--profiles", default=",".join(DEFAULT_PROFILES))
    parser.add_argument("--phases", default=",".join(str(item) for item in DEFAULT_PHASES))
    parser.add_argument(
        "--tiers",
        default="1:30,5:30,10:30",
        help="strictly increasing closed-loop VU:seconds entries",
    )
    parser.add_argument("--trials", type=int, default=3)
    parser.add_argument("--requests-per-phase", type=int, default=120)
    parser.add_argument("--waves-per-phase", type=int, default=3)
    parser.add_argument("--seed", type=int, default=20260713)
    parser.add_argument("--timeout-seconds", type=float, default=15.0)
    parser.add_argument("--cooldown-seconds", type=float, default=2.0)
    parser.add_argument("--resource-sample-seconds", type=float, default=5.0)
    parser.add_argument("--max-error-rate", type=float, default=0.02)
    parser.add_argument("--max-p95-ms", type=float, default=5000.0)
    parser.add_argument("--max-p99-ms", type=float, default=10000.0)
    parser.add_argument("--max-response-bytes", type=int, default=10 * 1024 * 1024)
    parser.add_argument("--postgres-port", type=int, default=54329)
    parser.add_argument("--redis-port", type=int, default=6379)
    parser.add_argument("--session-count", type=int, default=1)
    parser.add_argument("--token-file", type=Path)
    parser.add_argument(
        "--expected-runtime-release-sha",
        help="exact 40-hex server/frontend release SHA that /health must prove before pressure",
    )
    parser.add_argument(
        "--expected-migration-version",
        help=(
            "exact applied migration version required from /health trust.db_migration_max; "
            "the source must be schema_migrations"
        ),
    )
    parser.add_argument(
        "--expected-worker-release-sha",
        help="exact 40-hex worker release SHA required from the database heartbeat",
    )
    parser.add_argument(
        "--expected-worker-boot-nonce-sha256",
        help="exact worker boot nonce SHA-256 for the approved deployment restart",
    )
    parser.add_argument(
        "--worker-not-before",
        help="timezone-aware deployment restart time; the live worker must start at or after it",
    )
    parser.add_argument(
        "--max-worker-heartbeat-age-seconds",
        type=int,
        default=DEFAULT_MAX_WORKER_HEARTBEAT_AGE_SECONDS,
        help=(
            "maximum approved database-heartbeat age before pressure; code hard-capped at "
            f"{MAX_WORKER_HEARTBEAT_AGE_SECONDS} seconds"
        ),
    )
    parser.add_argument(
        "--execution-approval",
        type=Path,
        help=(
            "owner-only detached Ed25519 approval for the exact canonical live plan; "
            "verified only against the code-reviewed operator public-key allowlist"
        ),
    )
    parser.add_argument(
        "--execution-run-nonce",
        help=(
            "16-128 character run nonce bound into both the immutable plan and operator approval; "
            "the report stores only its SHA-256"
        ),
    )
    parser.add_argument(
        "--execution-nonce-ledger-dir",
        type=Path,
        default=DEFAULT_CAPACITY_EXECUTION_NONCE_LEDGER_DIR,
        help=(
            "owner-only local directory for atomic one-time nonce claims; the resolved "
            "path digest is bound into the approved canonical plan"
        ),
    )
    parser.add_argument("--db-pool-telemetry-file", type=Path)
    parser.add_argument("--redis-telemetry-file", type=Path)
    parser.add_argument(
        "--telemetry-run-nonce",
        help=(
            "16-128 character run nonce that must match every strict DB/Redis sidecar snapshot; "
            "the report stores only its SHA-256"
        ),
    )
    parser.add_argument(
        "--calibration-trace",
        type=Path,
        help=(
            "versioned anonymous session-trace JSON; local caller-provided file only, "
            "never discovered from browser history"
        ),
    )
    parser.add_argument(
        "--role-calibration",
        type=Path,
        help="versioned explicit per-role request-rate/think-time calibration JSON",
    )
    parser.add_argument(
        "--calibration-source-sha256",
        help="expected lowercase SHA-256 of the calibration source; absence/mismatch fails closed",
    )
    parser.add_argument(
        "--calibration-as-of",
        help="explicit ISO-8601 evaluation time for reproducible freshness checks",
    )
    parser.add_argument(
        "--calibration-attestation",
        type=Path,
        help=(
            "detached Ed25519 producer attestation JSON; verification uses only "
            "code-reviewed public keys and never accepts a runtime signing key"
        ),
    )
    parser.add_argument(
        "--calibration-manifest-output",
        type=Path,
        help="optionally write the redacted calibration manifest as a separate owner-only JSON file",
    )
    parser.add_argument("--soak-seconds", type=float, default=0.0)
    parser.add_argument("--soak-profile", choices=DEFAULT_PROFILES, default="mixed")
    parser.add_argument("--soak-virtual-users", type=int, default=10)
    parser.add_argument("--soak-think-time-ms", type=float, default=1000.0)
    parser.add_argument(
        "--journey-profile",
        choices=("none", *tuple(JOURNEY_PROFILES)),
        default="none",
        help=(
            "versioned read-only user-journey hypothesis; only valid with closed-loop-tiers "
            "and mixed profile"
        ),
    )
    parser.add_argument(
        "--journey-pacing-scale",
        type=float,
        default=1.0,
        help="multiply nominal per-step journey think time; 0 is saturation, 1 is hypothesis pacing",
    )
    parser.add_argument("--soak-window-seconds", type=float, default=10.0)
    parser.add_argument("--soak-max-requests", type=int, default=250_000)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--raw-output", type=Path)
    parser.add_argument("--no-raw-samples", action="store_true")
    return parser


def validate_execution_args(args: argparse.Namespace) -> None:
    if args.execute_live and args.fixture is not None:
        raise ValueError("--execute-live and --fixture are mutually exclusive")
    calibration_sources = [args.calibration_trace, args.role_calibration]
    if sum(item is not None for item in calibration_sources) > 1:
        raise ValueError("--calibration-trace and --role-calibration are mutually exclusive")
    calibration_source = args.calibration_trace or args.role_calibration
    if any(
        (
            args.calibration_source_sha256,
            args.calibration_as_of,
            args.calibration_attestation,
            args.calibration_manifest_output,
        )
    ) and calibration_source is None:
        raise ValueError("calibration hash/as-of/attestation/output requires a calibration source")
    validate_loopback_base(args.frontend_base)
    validate_loopback_base(args.backend_base)
    build_target_runtime_identity_contract(args)
    validate_execution_run_nonce(args.execution_run_nonce)
    telemetry_nonce = validate_telemetry_run_nonce(args.telemetry_run_nonce)
    if (args.db_pool_telemetry_file is not None or args.redis_telemetry_file is not None) and not telemetry_nonce:
        raise ValueError("strict telemetry sidecars require --telemetry-run-nonce")
    parse_profiles(args.profiles)
    parse_positive_ints(args.phases)
    validate_capacity_execution_hard_bounds(args)
    journey_profile = resolve_journey_profile(args.journey_profile)
    parsed_tiers: tuple[VuDurationTier, ...] = ()
    if args.mode == "closed-loop-tiers":
        parsed_tiers = parse_vu_duration_tiers(args.tiers)
        if args.soak_profile not in parse_profiles(args.profiles):
            raise ValueError("--soak-profile must also be selected in --profiles")
        if args.soak_max_requests < max(tier.virtual_users for tier in parsed_tiers):
            raise ValueError("--soak-max-requests must be at least the largest configured tier VU")
    if not (0.0 <= args.journey_pacing_scale <= 10.0):
        raise ValueError("--journey-pacing-scale must be in [0, 10]")
    if journey_profile is not None:
        if args.mode != "closed-loop-tiers":
            raise ValueError("--journey-profile requires --mode closed-loop-tiers")
        if args.soak_profile != "mixed":
            raise ValueError("--journey-profile requires --soak-profile mixed")
        largest_tier = max(tier.virtual_users for tier in parsed_tiers)
        if args.session_count < largest_tier:
            raise ValueError(
                "--journey-profile requires --session-count at least the largest tier VU "
                "so each simulated active session has an independent cookie/connection context"
            )
    elif calibration_source is not None:
        raise ValueError("capacity calibration requires --journey-profile staff-readonly-v1")
    if not (1 <= args.trials <= MAX_TRIALS):
        raise ValueError(f"--trials must be in [1, {MAX_TRIALS}]")
    if not (1 <= args.session_count <= MAX_SOAK_VIRTUAL_USERS):
        raise ValueError(f"--session-count must be in [1, {MAX_SOAK_VIRTUAL_USERS}]")
    planned_preflight = planned_preflight_request_count(args, args.session_count)
    compatibility_module = sys.modules.get("scripts.load_test_vkpi_readonly")
    preflight_limit = int(
        getattr(compatibility_module, "MAX_PREFLIGHT_REQUESTS", MAX_PREFLIGHT_REQUESTS)
    )
    if planned_preflight > preflight_limit:
        raise ValueError(
            f"planned preflight requests {planned_preflight} exceed hard limit {preflight_limit}"
        )
    if args.no_raw_samples and args.raw_output is not None:
        raise ValueError("--raw-output cannot be combined with --no-raw-samples")
    if args.requests_per_phase < 20:
        raise ValueError("--requests-per-phase must be at least 20")
    if args.waves_per_phase < 1:
        raise ValueError("--waves-per-phase must be positive")
    if not (0.0 <= args.max_error_rate < 1.0):
        raise ValueError("--max-error-rate must be in [0, 1)")
    if args.timeout_seconds <= 0:
        raise ValueError("--timeout-seconds must be positive")
    if args.resource_sample_seconds < _RESOURCE_SAMPLE_MIN_SECONDS:
        raise ValueError(f"--resource-sample-seconds must be at least {_RESOURCE_SAMPLE_MIN_SECONDS:g}")
    if not (0.0 <= args.soak_seconds <= MAX_SOAK_SECONDS):
        raise ValueError(f"--soak-seconds must be in [0, {MAX_SOAK_SECONDS:g}]")
    if args.soak_seconds > 0 or args.mode == "closed-loop-tiers":
        if not (1 <= args.soak_virtual_users <= MAX_SOAK_VIRTUAL_USERS):
            raise ValueError(f"--soak-virtual-users must be in [1, {MAX_SOAK_VIRTUAL_USERS}]")
        if not (args.soak_virtual_users <= args.soak_max_requests <= MAX_SOAK_REQUESTS):
            raise ValueError(
                f"--soak-max-requests must be between soak virtual users and {MAX_SOAK_REQUESTS}"
            )
        if not (0.0 <= args.soak_think_time_ms <= 60_000.0):
            raise ValueError("--soak-think-time-ms must be in [0, 60000]")
        if not (0.01 <= args.soak_window_seconds <= 300.0):
            raise ValueError("--soak-window-seconds must be in [0.01, 300]")
        selected_profiles = parse_profiles(args.profiles)
        if args.soak_profile not in selected_profiles:
            raise ValueError("--soak-profile must also be selected in --profiles")


__all__ = [name for name in globals() if not name.startswith("__")]
