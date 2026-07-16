#!/usr/bin/env python3
"""Safe, reproducible, read-only capacity ramp for the local V-KPI stack.

The runner is intentionally narrower than the legacy load-test scripts:

* loopback HTTP(S) targets only;
* a hard-coded GET-only endpoint allowlist;
* no login request and no token in stdout or report files;
* separate static, health, light-DB, heavy-aggregate, and mixed profiles;
* phase-level stop rules for errors and tail latency;
* repeatable duration- and request-bounded closed-loop virtual-user tiers;
* an explicit, versioned read-only staff-journey hypothesis with per-step pacing;
* multiple independent token/session slots loaded only from explicit env or a
  permission-controlled JSON file;
* owner-only raw NDJSON request samples and repeat min/median/max summaries;
* stage resource samples that degrade to explicit "unavailable" evidence;
* local-machine evidence is labelled as local, never as cloud capacity.

The default command is a zero-network dry-run.  Live loopback requests require
the explicit ``--execute-live`` flag.  Auth tokens are read only from
``VKPI_LOAD_TEST_TOKEN``, ``VKPI_LOAD_TEST_TOKENS_JSON``, or an owner-only
``--token-file``; token values are kept in memory and never serialized.  No
login, local database helper, browser, provider, or write endpoint is invoked.

Terminology is deliberate: ramp ``concurrency`` is a cap on in-flight HTTP
requests; generator ``workers`` are asyncio tasks; soak ``virtual users`` are
closed-loop tasks with optional think time; none of these numbers proves a
count of simultaneous human seats or server worker processes.  The optional
staff journey makes the request order more product-like, but remains an
uncalibrated hypothesis until production traces establish role mix and pacing.
"""
from __future__ import annotations

import argparse
import asyncio
import base64
import binascii
import contextlib
import hashlib
import io
import json
import math
import os
import platform
import random
import re
import secrets
import stat
import subprocess
import time
from collections import Counter, defaultdict
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from statistics import mean, median
from typing import Any, Awaitable, Callable, Iterable, Mapping, Sequence, TextIO, TypeVar
from types import MappingProxyType
from urllib.parse import urlparse

try:
    from cryptography.exceptions import InvalidSignature
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
except ImportError:  # pragma: no cover - dependency state is reported fail-closed
    InvalidSignature = Exception  # type: ignore[assignment,misc]
    Ed25519PublicKey = None  # type: ignore[assignment,misc]

try:
    import aiohttp
except Exception as exc:  # pragma: no cover - dependency check is CLI-facing
    raise SystemExit("aiohttp is required; install the repository requirements") from exc


ROOT = Path(__file__).resolve().parents[2]
DEFAULT_REPORT_DIR = ROOT / "runtime" / "ops"
DEFAULT_PHASES = (1, 5, 10, 20, 40, 80)
DEFAULT_PROFILES = ("static_frontend", "health", "light_db", "heavy_aggregate", "mixed")
TOKEN_ENV_NAME = "VKPI_LOAD_TEST_TOKEN"
TOKENS_JSON_ENV_NAME = "VKPI_LOAD_TEST_TOKENS_JSON"
MAX_SOAK_SECONDS = 3600.0
MAX_SOAK_VIRTUAL_USERS = 1000
MAX_SOAK_REQUESTS = 1_000_000
MAX_TRIALS = 20
MAX_TOKEN_FILE_BYTES = 256 * 1024
MAX_PREFLIGHT_REQUESTS = 20_000
_RESOURCE_SAMPLE_MIN_SECONDS = 1.0
TELEMETRY_SIDECAR_SCHEMA = "vkpi-load-telemetry-snapshot/v2"
TELEMETRY_ATTESTATION_SCHEMA = "vkpi-load-telemetry-producer-attestation/v1"
MAX_TELEMETRY_FILE_BYTES = 256 * 1024
MAX_TELEMETRY_AGE_SECONDS = 15.0
MAX_TELEMETRY_FUTURE_SKEW_SECONDS = 2.0
IDENTITY_STAFF_PATH = "/api/auth/me"
IDENTITY_TENANT_PATH = "/api/admin/vkpi/agents/tenant/current"
IDENTITY_REQUESTS_PER_CONTEXT = 2
CALIBRATION_TRACE_SCHEMA = "vkpi-anonymous-session-trace/v1"
CALIBRATION_ROLE_RATE_SCHEMA = "vkpi-explicit-role-rate-calibration/v1"
CALIBRATION_MANIFEST_SCHEMA = "vkpi-capacity-calibration-manifest/v1"
CALIBRATION_ATTESTATION_SCHEMA = "vkpi-capacity-producer-attestation/v1"
MIN_CALIBRATION_SESSIONS = 120
MIN_CALIBRATION_SESSIONS_PER_ROLE = 20
MIN_CALIBRATION_WINDOW_SECONDS = 7 * 24 * 60 * 60
MAX_CALIBRATION_AGE_SECONDS = 14 * 24 * 60 * 60
MIN_CAPACITY_TRIALS = 3
MIN_CAPACITY_TRIAL_SECONDS = 60.0
MAX_TRIAL_RPS_RELATIVE_SPREAD = 0.25
MAX_TRIAL_P95_RELATIVE_SPREAD = 0.50
CAPACITY_SAFETY_FACTOR = 0.80
MAX_CALIBRATION_FILE_BYTES = 4 * 1024 * 1024
MAX_CALIBRATION_ATTESTATION_BYTES = 256 * 1024
# Trust is code-reviewed and public-key-only.  The empty default is deliberate:
# no real producer key has been approved in this worktree, so production
# calibration cannot become trusted merely because an operator supplies a key.
TRUSTED_CALIBRATION_ED25519_PUBLIC_KEYS: Mapping[str, str] = MappingProxyType({})
# Independent telemetry producers must be approved here in code review.  An
# empty default deliberately prevents same-user JSON files from qualifying a
# capacity result, even when they are fresh and internally well-formed.
TRUSTED_TELEMETRY_ED25519_PUBLIC_KEYS: Mapping[str, str] = MappingProxyType({})
_CALIBRATION_VERIFICATION_CAPABILITY = object()
_LIVE_PERFORMANCE_EVIDENCE_CAPABILITY = object()
_T = TypeVar("_T")
_SECRET_PATTERNS = (
    re.compile(r"(?i)\bBearer\s+[A-Za-z0-9._~+/=-]{12,}"),
    re.compile(r"(?i)\b(?:token|authorization|password|secret)\b\s*[:=]\s*[^\s,}]+"),
)
_CALIBRATION_FORBIDDEN_KEYS = {
    "account_id",
    "authorization",
    "cookie",
    "device_id",
    "email",
    "full_name",
    "ip",
    "ip_address",
    "name",
    "password",
    "phone",
    "session_id",
    "token",
    "user_id",
}


@dataclass(frozen=True)
class Endpoint:
    name: str
    category: str
    target: str
    path: str
    authenticated: bool
    weight: int = 1
    expected_statuses: tuple[int, ...] = (200,)

    def public_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "category": self.category,
            "target": self.target,
            "path": self.path,
            "authenticated": self.authenticated,
            "weight": self.weight,
            "method": "GET",
            "expected_statuses": list(self.expected_statuses),
        }


ENDPOINTS: tuple[Endpoint, ...] = (
    Endpoint("frontend_shell", "static_frontend", "frontend", "/", False, 1, (200, 304)),
    Endpoint("backend_health", "health", "backend", "/health", False),
    Endpoint("events_list", "light_db", "backend", "/api/admin/vkpi/events?limit=25", True),
    Endpoint("dealers_list", "light_db", "backend", "/api/admin/vkpi/dealers?limit=25", True),
    Endpoint("event_radar_summary", "light_db", "backend", "/api/admin/vkpi/event-radar/summary", True),
    Endpoint(
        "industry_benchmark",
        "heavy_aggregate",
        "backend",
        "/api/admin/vkpi/strategy/industry-benchmark?window_days=90",
        True,
    ),
    Endpoint(
        "category_tracks",
        "heavy_aggregate",
        "backend",
        "/api/admin/vkpi/strategy/category-tracks",
        True,
    ),
)
ENDPOINT_BY_NAME = {item.name: item for item in ENDPOINTS}


@dataclass(frozen=True)
class Thresholds:
    max_error_rate: float
    max_p95_ms: float
    max_p99_ms: float


@dataclass(frozen=True)
class RequestContext:
    """One independent HTTP cookie/connection context and optional auth identity."""

    session: Any
    token: str | None
    slot: int


@dataclass(frozen=True)
class VuDurationTier:
    virtual_users: int
    duration_seconds: float

    def public_dict(self) -> dict[str, Any]:
        return {
            "virtual_users": self.virtual_users,
            "duration_seconds": self.duration_seconds,
            "load_model": "closed_loop",
            "human_users": None,
        }


def parse_positive_ints(raw: str) -> tuple[int, ...]:
    values = tuple(int(part.strip()) for part in str(raw).split(",") if part.strip())
    if not values or any(value <= 0 for value in values):
        raise ValueError("phases must be positive integers")
    if tuple(sorted(set(values))) != values:
        raise ValueError("phases must be unique and strictly increasing")
    return values


def parse_vu_duration_tiers(raw: str) -> tuple[VuDurationTier, ...]:
    """Parse ``VU:seconds`` tiers while keeping VU distinct from human users."""
    tiers: list[VuDurationTier] = []
    for part in (item.strip() for item in str(raw or "").split(",")):
        if not part:
            continue
        pieces = part.split(":", 1)
        if len(pieces) != 2:
            raise ValueError("tiers must use VU:seconds entries")
        try:
            virtual_users = int(pieces[0])
            duration_seconds = float(pieces[1])
        except ValueError as exc:
            raise ValueError("tiers must use numeric VU:seconds entries") from exc
        if not (1 <= virtual_users <= MAX_SOAK_VIRTUAL_USERS):
            raise ValueError(f"tier VU must be in [1, {MAX_SOAK_VIRTUAL_USERS}]")
        if not (0.001 <= duration_seconds <= MAX_SOAK_SECONDS):
            raise ValueError(
                f"tier duration must be in [0.001, {MAX_SOAK_SECONDS:g}] seconds"
            )
        tiers.append(VuDurationTier(virtual_users, duration_seconds))
    if not tiers:
        raise ValueError("at least one VU:seconds tier is required")
    if tuple(sorted({tier.virtual_users for tier in tiers})) != tuple(
        tier.virtual_users for tier in tiers
    ):
        raise ValueError("tier VU values must be unique and strictly increasing")
    return tuple(tiers)


def parse_profiles(raw: str) -> tuple[str, ...]:
    values = tuple(part.strip() for part in str(raw).split(",") if part.strip())
    valid = set(DEFAULT_PROFILES)
    unknown = sorted(set(values) - valid)
    if not values or unknown:
        raise ValueError(f"invalid profiles: {unknown or values}")
    return values


@dataclass(frozen=True)
class JourneyStep:
    endpoint_name: str
    think_time_ms: float

    def public_dict(self) -> dict[str, Any]:
        return {
            "endpoint": self.endpoint_name,
            "think_time_ms": self.think_time_ms,
        }


@dataclass(frozen=True)
class JourneyRole:
    name: str
    weight: int
    steps: tuple[JourneyStep, ...]

    def public_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "weight": self.weight,
            "steps": [step.public_dict() for step in self.steps],
        }


@dataclass(frozen=True)
class JourneyProfile:
    profile_id: str
    version: str
    roles: tuple[JourneyRole, ...]
    calibration_status: str
    calibration_source: str | None

    def public_dict(self, *, pacing_scale: float) -> dict[str, Any]:
        return {
            "profile_id": self.profile_id,
            "version": self.version,
            "roles": [role.public_dict() for role in self.roles],
            "pacing_scale": float(pacing_scale),
            "calibration_status": self.calibration_status,
            "calibration_source": self.calibration_source,
            "production_trace_calibrated": False,
            "human_user_capacity_claim_allowed": False,
            "interpretation": (
                "one VU is one synthetic active read-only session following a role path; "
                "it is not a measured employee, account, or licensed seat"
            ),
        }


STAFF_READONLY_JOURNEY_V1 = JourneyProfile(
    profile_id="staff-readonly-v1",
    version="1.0.0",
    calibration_status="workflow_hypothesis_unvalidated",
    calibration_source=None,
    roles=(
        JourneyRole(
            "event_planner",
            4,
            (
                JourneyStep("frontend_shell", 2_000.0),
                JourneyStep("event_radar_summary", 4_000.0),
                JourneyStep("events_list", 5_000.0),
                JourneyStep("dealers_list", 6_000.0),
            ),
        ),
        JourneyRole(
            "market_strategist",
            3,
            (
                JourneyStep("frontend_shell", 2_000.0),
                JourneyStep("industry_benchmark", 6_000.0),
                JourneyStep("category_tracks", 6_000.0),
                JourneyStep("event_radar_summary", 4_000.0),
            ),
        ),
        JourneyRole(
            "dealer_researcher",
            3,
            (
                JourneyStep("frontend_shell", 2_000.0),
                JourneyStep("dealers_list", 6_000.0),
                JourneyStep("event_radar_summary", 5_000.0),
                JourneyStep("events_list", 5_000.0),
            ),
        ),
    ),
)
JOURNEY_PROFILES: Mapping[str, JourneyProfile] = {
    STAFF_READONLY_JOURNEY_V1.profile_id: STAFF_READONLY_JOURNEY_V1,
}
STAFF_READONLY_ENDPOINT_THRESHOLDS: Mapping[str, Thresholds] = {
    "frontend_shell": Thresholds(0.02, 1_500.0, 3_000.0),
    "events_list": Thresholds(0.02, 1_500.0, 3_000.0),
    "dealers_list": Thresholds(0.02, 1_500.0, 3_000.0),
    "event_radar_summary": Thresholds(0.02, 1_500.0, 3_000.0),
    "industry_benchmark": Thresholds(0.02, 5_000.0, 10_000.0),
    "category_tracks": Thresholds(0.02, 5_000.0, 10_000.0),
}


def _parse_utc_datetime(value: Any, *, field_name: str) -> datetime:
    raw = str(value or "").strip()
    if not raw:
        raise ValueError(f"{field_name} is required")
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError(f"{field_name} must be an ISO-8601 timestamp") from exc
    if parsed.tzinfo is None:
        raise ValueError(f"{field_name} must include an explicit timezone")
    return parsed.astimezone(timezone.utc)


def _iso_utc(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _is_number(value: Any) -> bool:
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        return False
    try:
        return math.isfinite(float(value))
    except (OverflowError, TypeError, ValueError):
        return False


def _secure_read_regular_file(
    path: Path,
    *,
    max_bytes: int,
    label: str,
    require_owner: bool,
    require_private: bool,
) -> bytes:
    """Open once, validate the opened inode, and read through that same FD."""
    candidate = Path(path).expanduser()
    flags = os.O_RDONLY
    if hasattr(os, "O_CLOEXEC"):
        flags |= os.O_CLOEXEC
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        fd = os.open(candidate, flags)
    except OSError as exc:
        raise ValueError(f"{label} must be an accessible non-symlink file") from exc
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode):
            raise ValueError(f"{label} must be a regular file")
        if int(info.st_nlink) != 1:
            raise ValueError(f"{label} must have exactly one hard link")
        if require_owner and hasattr(os, "getuid") and info.st_uid != os.getuid():
            raise ValueError(f"{label} must be owned by the current user")
        if require_private and info.st_mode & 0o077:
            raise ValueError(f"{label} permissions must deny group and other access")
        if info.st_size > max_bytes:
            raise ValueError(f"{label} exceeds {max_bytes} bytes")
        chunks: list[bytes] = []
        observed = 0
        while observed <= max_bytes:
            chunk = os.read(fd, min(65_536, max_bytes + 1 - observed))
            if not chunk:
                break
            chunks.append(chunk)
            observed += len(chunk)
        if observed > max_bytes:
            raise ValueError(f"{label} exceeds {max_bytes} bytes")
        return b"".join(chunks)
    finally:
        os.close(fd)


def _forbidden_calibration_keys(value: Any, *, path: str = "$") -> list[str]:
    findings: list[str] = []
    if isinstance(value, Mapping):
        for key, item in value.items():
            clean = str(key).strip().lower()
            child = f"{path}.{key}"
            if clean in _CALIBRATION_FORBIDDEN_KEYS or clean.endswith("_token"):
                findings.append(child)
            findings.extend(_forbidden_calibration_keys(item, path=child))
    elif isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        for index, item in enumerate(value):
            findings.extend(_forbidden_calibration_keys(item, path=f"{path}[{index}]"))
    return findings


def _unknown_keys(value: Mapping[str, Any], allowed: set[str], *, path: str) -> list[str]:
    return [f"{path}.{key}" for key in sorted(set(str(key) for key in value) - allowed)]


def percentile(values: Sequence[float], pct: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(float(value) for value in values)
    index = min(
        len(ordered) - 1,
        max(0, math.ceil((pct / 100.0) * len(ordered)) - 1),
    )
    return ordered[index]


def _bootstrap_mean_interval(
    values: Sequence[float],
    *,
    seed: int,
    iterations: int = 1000,
) -> tuple[float, float, float]:
    cleaned = [float(value) for value in values if _is_number(value) and float(value) > 0]
    if not cleaned:
        return 0.0, 0.0, 0.0
    point = float(mean(cleaned))
    if len(cleaned) == 1:
        return point, point, point
    randomizer = random.Random(seed)
    sample_size = len(cleaned)
    boot = [
        float(mean(cleaned[randomizer.randrange(sample_size)] for _ in range(sample_size)))
        for _ in range(max(200, int(iterations)))
    ]
    return percentile(boot, 2.5), point, percentile(boot, 97.5)


def _calibration_gate(passed: bool, observed: Any, required: Any) -> dict[str, Any]:
    return {"pass": bool(passed), "observed": observed, "required": required}


class _VerifiedCalibrationManifest(dict[str, Any]):
    """In-process proof wrapper; JSON round-trips intentionally discard trust."""


class _VerifiedLiveStageBundle:
    """Run-local proof bound to the exact canonical stage bundle.

    The object is intentionally not JSON serializable.  It carries no stage
    payload itself, so reports cannot accidentally persist an authority token.
    """

    __slots__ = ("_verification_capability", "_stage_bundle_sha256")

    def __init__(self, stage_bundle_sha256: str):
        self._verification_capability = _LIVE_PERFORMANCE_EVIDENCE_CAPABILITY
        self._stage_bundle_sha256 = stage_bundle_sha256


def _canonical_json_sha256(value: Any) -> str:
    canonical = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _seal_live_stage_bundle(stages: Sequence[Mapping[str, Any]]) -> _VerifiedLiveStageBundle:
    return _VerifiedLiveStageBundle(_canonical_json_sha256(list(stages)))


def _is_verified_live_stage_bundle(value: Any, stages: Sequence[Mapping[str, Any]]) -> bool:
    if not isinstance(value, _VerifiedLiveStageBundle):
        return False
    if value._verification_capability is not _LIVE_PERFORMANCE_EVIDENCE_CAPABILITY:
        return False
    try:
        observed = _canonical_json_sha256(list(stages))
    except (TypeError, ValueError, OverflowError, RecursionError):
        return False
    return secrets.compare_digest(observed, value._stage_bundle_sha256)


def _seal_verified_calibration_manifest(
    payload: Mapping[str, Any],
) -> _VerifiedCalibrationManifest:
    manifest = _VerifiedCalibrationManifest(payload)
    canonical_hash = _canonical_json_sha256(dict(manifest))
    manifest._verification_capability = _CALIBRATION_VERIFICATION_CAPABILITY
    manifest._verified_content_sha256 = canonical_hash
    return manifest


def _is_in_process_verified_calibration(value: Any) -> bool:
    if not isinstance(value, _VerifiedCalibrationManifest):
        return False
    if (
        getattr(value, "_verification_capability", None)
        is not _CALIBRATION_VERIFICATION_CAPABILITY
    ):
        return False
    try:
        observed = _canonical_json_sha256(dict(value))
    except (TypeError, ValueError, OverflowError, RecursionError):
        return False
    return observed == getattr(value, "_verified_content_sha256", None)


def _attestation_failure(*reasons: str) -> dict[str, Any]:
    return {
        "status": "untrusted_or_unattested",
        "trusted": False,
        "signature_valid": False,
        "signer_allowlisted": False,
        "source_binding_valid": False,
        "time_binding_valid": False,
        "key_id": None,
        "algorithm": "Ed25519",
        "attestation_file_name": None,
        "attestation_sha256": None,
        "verifier_key_material": "public_only",
        "failure_reasons": sorted(set(reasons or ("attestation_not_configured",))),
    }

__all__ = [name for name in globals() if not name.startswith("__")]
