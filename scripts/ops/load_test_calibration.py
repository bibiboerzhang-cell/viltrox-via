from __future__ import annotations

import sys

from scripts.ops.load_test_approval import *


def _runtime_trusted_calibration_public_keys() -> Mapping[str, str]:
    """Resolve the compatibility module's public-key allowlist when patched.

    Historically callers imported ``scripts.load_test_vkpi_readonly`` and
    patched this code-owned allowlist there for offline verification tests.
    The implementation now lives in focused modules, so consult that public
    compatibility module without weakening the production default.
    """
    compatibility_module = sys.modules.get("scripts.load_test_vkpi_readonly")
    candidate = getattr(
        compatibility_module,
        "TRUSTED_CALIBRATION_ED25519_PUBLIC_KEYS",
        TRUSTED_CALIBRATION_ED25519_PUBLIC_KEYS,
    )
    return candidate if isinstance(candidate, Mapping) else MappingProxyType({})

def verify_calibration_producer_attestation(
    attestation_path: Path | None,
    *,
    source_sha256: str,
    source_schema_version: str,
    evidence_class: str,
    journey_profile: str,
    source_generated_at: datetime,
    evaluated_at: datetime,
) -> dict[str, Any]:
    """Verify a detached producer attestation using the code-owned public allowlist.

    This verifier never loads a signing key and never accepts caller-provided
    public keys.  Malformed, missing, or untrusted evidence always returns an
    untrusted result instead of raising.
    """
    if attestation_path is None:
        return _attestation_failure("attestation_not_configured")
    try:
        candidate = Path(attestation_path).expanduser()
        result = _attestation_failure("attestation_unverified")
        result["attestation_file_name"] = candidate.name
        encoded = _secure_read_regular_file(
            candidate,
            max_bytes=MAX_CALIBRATION_ATTESTATION_BYTES,
            label="calibration attestation",
            require_owner=False,
            require_private=False,
        )
        result["attestation_sha256"] = hashlib.sha256(encoded).hexdigest()
        payload = json.loads(encoded.decode("utf-8"))
        if not isinstance(payload, Mapping):
            return {**result, "failure_reasons": ["attestation_root_not_object"]}
    except (OSError, TypeError, UnicodeError, json.JSONDecodeError, ValueError):
        return _attestation_failure("attestation_unreadable_or_malformed")

    signed_fields = (
        "schema_version",
        "algorithm",
        "key_id",
        "source_sha256",
        "source_schema_version",
        "evidence_class",
        "journey_profile",
        "issued_at",
    )
    allowed_fields = set(signed_fields) | {"signature_base64"}
    unknown_fields = sorted(set(str(key) for key in payload) - allowed_fields)
    schema_valid = payload.get("schema_version") == CALIBRATION_ATTESTATION_SCHEMA
    algorithm_valid = payload.get("algorithm") == "Ed25519"
    key_id = payload.get("key_id")
    key_id_valid = isinstance(key_id, str) and bool(
        re.fullmatch(r"[A-Za-z0-9._-]{3,64}", key_id)
    )
    result["key_id"] = key_id if key_id_valid else None
    source_binding_valid = (
        payload.get("source_sha256") == source_sha256
        and payload.get("source_schema_version") == source_schema_version
        and payload.get("evidence_class") == evidence_class
        and payload.get("journey_profile") == journey_profile
    )
    result["source_binding_valid"] = source_binding_valid
    try:
        issued_at = _parse_utc_datetime(
            payload.get("issued_at"), field_name="attestation issued_at"
        )
    except ValueError:
        issued_at = None
    time_binding_valid = bool(
        issued_at is not None
        and source_generated_at <= issued_at <= evaluated_at
    )
    result["time_binding_valid"] = time_binding_valid

    registered_public_key = (
        _runtime_trusted_calibration_public_keys().get(key_id)
        if key_id_valid
        else None
    )
    signer_allowlisted = isinstance(registered_public_key, str)
    result["signer_allowlisted"] = signer_allowlisted
    signature_valid = False
    key_material_valid = False
    signature_text = payload.get("signature_base64")
    if (
        Ed25519PublicKey is not None
        and signer_allowlisted
        and isinstance(signature_text, str)
        and schema_valid
        and algorithm_valid
        and not unknown_fields
    ):
        try:
            public_bytes = base64.b64decode(
                registered_public_key.encode("ascii"), validate=True
            )
            signature = base64.b64decode(signature_text.encode("ascii"), validate=True)
            key_material_valid = len(public_bytes) == 32 and len(signature) == 64
            if key_material_valid:
                message = json.dumps(
                    {field: payload.get(field) for field in signed_fields},
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                ).encode("utf-8")
                Ed25519PublicKey.from_public_bytes(public_bytes).verify(signature, message)
                signature_valid = True
        except (InvalidSignature, ValueError, TypeError, binascii.Error, UnicodeError):
            signature_valid = False
    result["signature_valid"] = signature_valid

    failures: list[str] = []
    if unknown_fields:
        failures.append("attestation_unknown_fields")
    if not schema_valid:
        failures.append("attestation_schema")
    if not algorithm_valid:
        failures.append("attestation_algorithm")
    if not key_id_valid:
        failures.append("attestation_key_id")
    if not signer_allowlisted:
        failures.append("attestation_signer_not_allowlisted")
    if signer_allowlisted and not key_material_valid:
        failures.append("attestation_public_key_or_signature_encoding")
    if not source_binding_valid:
        failures.append("attestation_source_binding")
    if not time_binding_valid:
        failures.append("attestation_time_binding")
    if not signature_valid:
        failures.append("attestation_signature")
    trusted = not failures
    result["trusted"] = trusted
    result["status"] = (
        "trusted_producer_attestation" if trusted else "untrusted_or_unattested"
    )
    result["failure_reasons"] = sorted(set(failures))
    return result


def _build_capacity_calibration_manifest(
    source_path: Path | None,
    *,
    expected_source_sha256: str | None,
    as_of: str | None = None,
    attestation_path: Path | None = None,
    journey_profile: JourneyProfile = STAFF_READONLY_JOURNEY_V1,
) -> dict[str, Any]:
    """Build a privacy-bounded, hash-pinned VU-to-seat calibration contract.

    The source is caller-provided JSON only.  This function never opens a browser,
    HTTP connection, database, token store, or user-history location.  Raw session
    rows and anonymous row order are never copied into the returned manifest.
    """
    as_of_pinned = as_of is not None
    evaluated_at = (
        _parse_utc_datetime(as_of, field_name="calibration as-of")
        if as_of_pinned
        else datetime(1970, 1, 1, tzinfo=timezone.utc)
    )
    base: dict[str, Any] = {
        "schema_version": CALIBRATION_MANIFEST_SCHEMA,
        "evaluated_at": _iso_utc(evaluated_at) if as_of_pinned else None,
        "journey_profile": journey_profile.profile_id,
        "journey_profile_version": journey_profile.version,
        "status": "not_configured",
        "consistency_status": "source_not_configured",
        "trust_status": "untrusted_or_unattested",
        "eligible": False,
        "human_seat_conversion_allowed": False,
        "source": None,
        "producer_attestation": _attestation_failure("attestation_not_configured"),
        "requirements": {
            "minimum_total_sessions": MIN_CALIBRATION_SESSIONS,
            "minimum_sessions_per_required_role": MIN_CALIBRATION_SESSIONS_PER_ROLE,
            "minimum_observation_window_seconds": MIN_CALIBRATION_WINDOW_SECONDS,
            "maximum_source_age_seconds": MAX_CALIBRATION_AGE_SECONDS,
            "required_roles": [role.name for role in journey_profile.roles],
            "minimum_confidence_level": 0.95,
            "expected_source_sha256_required": True,
            "explicit_as_of_required": True,
            "measured_evidence_class_required": True,
            "trusted_ed25519_producer_attestation_required": True,
            "trusted_public_keys_are_code_allowlisted": True,
        },
        "gates": {},
        "role_metrics": [],
        "aggregate_request_rate_per_active_minute": None,
        "failure_reasons": ["calibration_source_not_configured"],
        "privacy": {
            "anonymous_aggregate_only": True,
            "raw_session_rows_persisted_in_manifest": False,
            "browser_history_read": False,
            "token_or_cookie_read": False,
        },
    }
    if source_path is None:
        return base

    candidate = Path(source_path).expanduser()
    source_bytes = _secure_read_regular_file(
        candidate,
        max_bytes=MAX_CALIBRATION_FILE_BYTES,
        label="calibration source",
        require_owner=False,
        require_private=False,
    )
    source_sha256 = hashlib.sha256(source_bytes).hexdigest()
    payload = json.loads(source_bytes.decode("utf-8"))
    if not isinstance(payload, Mapping):
        raise ValueError("calibration source root must be an object")

    expected_hash = str(expected_source_sha256 or "").strip().lower()
    hash_format_ok = bool(re.fullmatch(r"[0-9a-f]{64}", expected_hash))
    hash_verified = hash_format_ok and expected_hash == source_sha256
    schema = str(payload.get("schema_version") or "")
    source_kind = (
        "anonymous_session_trace"
        if schema == CALIBRATION_TRACE_SCHEMA
        else "explicit_role_rates"
        if schema == CALIBRATION_ROLE_RATE_SCHEMA
        else "unsupported"
    )
    required_roles = [role.name for role in journey_profile.roles]
    required_role_set = set(required_roles)
    privacy_findings = _forbidden_calibration_keys(payload)
    structural_findings: list[str] = []
    common_allowed = {
        "schema_version",
        "evidence_class",
        "generated_at",
        "window_start",
        "window_end",
        "journey_profile",
    }
    if schema == CALIBRATION_TRACE_SCHEMA:
        structural_findings.extend(
            _unknown_keys(payload, common_allowed | {"sessions"}, path="$")
        )
    elif schema == CALIBRATION_ROLE_RATE_SCHEMA:
        structural_findings.extend(
            _unknown_keys(payload, common_allowed | {"confidence_level", "roles"}, path="$")
        )

    timestamp_errors: list[str] = []
    try:
        generated_at = _parse_utc_datetime(payload.get("generated_at"), field_name="generated_at")
        window_start = _parse_utc_datetime(payload.get("window_start"), field_name="window_start")
        window_end = _parse_utc_datetime(payload.get("window_end"), field_name="window_end")
    except ValueError as exc:
        timestamp_errors.append(str(exc))
        generated_at = evaluated_at
        window_start = evaluated_at
        window_end = evaluated_at
    if not as_of_pinned and not timestamp_errors:
        # Keep an unqualified manifest byte-reproducible without pretending that
        # source generation time proves current freshness.
        evaluated_at = generated_at
        base["evaluated_at"] = _iso_utc(evaluated_at)
    window_seconds = max(0.0, (window_end - window_start).total_seconds())
    age_seconds = (evaluated_at - generated_at).total_seconds()
    end_age_seconds = (evaluated_at - window_end).total_seconds()
    timestamps_ordered = window_start < window_end <= generated_at <= evaluated_at

    role_samples: dict[str, int] = {name: 0 for name in required_roles}
    role_rates: dict[str, list[float]] = {name: [] for name in required_roles}
    role_think_times: dict[str, list[float]] = {name: [] for name in required_roles}
    explicit_role_bounds: dict[str, tuple[float, float, float]] = {}
    explicit_think: dict[str, tuple[float, float]] = {}
    invalid_row_count = 0
    duplicate_row_count = 0
    confidence_level = 0.95 if schema == CALIBRATION_TRACE_SCHEMA else 0.0

    if schema == CALIBRATION_TRACE_SCHEMA:
        sessions = payload.get("sessions")
        if not isinstance(sessions, list):
            structural_findings.append("$.sessions must be an array")
            sessions = []
        allowed_session_keys = {"role", "started_at", "ended_at", "request_count", "think_time_ms"}
        trace_fingerprints: set[str] = set()
        for index, raw in enumerate(sessions):
            if not isinstance(raw, Mapping):
                invalid_row_count += 1
                continue
            structural_findings.extend(_unknown_keys(raw, allowed_session_keys, path=f"$.sessions[{index}]"))
            role = str(raw.get("role") or "")
            try:
                started = _parse_utc_datetime(raw.get("started_at"), field_name=f"sessions[{index}].started_at")
                ended = _parse_utc_datetime(raw.get("ended_at"), field_name=f"sessions[{index}].ended_at")
            except ValueError:
                invalid_row_count += 1
                continue
            request_count = raw.get("request_count")
            think_time_ms = raw.get("think_time_ms")
            duration_seconds = (ended - started).total_seconds()
            row_valid = (
                role in required_role_set
                and _is_number(request_count)
                and float(request_count).is_integer()
                and int(request_count) >= 2
                and _is_number(think_time_ms)
                and 0.0 <= float(think_time_ms) <= 60_000.0
                and duration_seconds > 0.0
                and window_start <= started < ended <= window_end
            )
            if not row_valid:
                invalid_row_count += 1
                continue
            fingerprint = json.dumps(
                {key: raw.get(key) for key in sorted(allowed_session_keys)},
                sort_keys=True,
                separators=(",", ":"),
            )
            if fingerprint in trace_fingerprints:
                duplicate_row_count += 1
                continue
            trace_fingerprints.add(fingerprint)
            role_samples[role] += 1
            role_rates[role].append(float(request_count) / (duration_seconds / 60.0))
            role_think_times[role].append(float(think_time_ms))
    elif schema == CALIBRATION_ROLE_RATE_SCHEMA:
        confidence_raw = payload.get("confidence_level")
        confidence_level = float(confidence_raw) if _is_number(confidence_raw) else 0.0
        roles = payload.get("roles")
        if not isinstance(roles, list):
            structural_findings.append("$.roles must be an array")
            roles = []
        allowed_role_keys = {
            "role",
            "sample_sessions",
            "request_rate_per_active_minute",
            "think_time_ms",
        }
        seen_roles: set[str] = set()
        for index, raw in enumerate(roles):
            if not isinstance(raw, Mapping):
                invalid_row_count += 1
                continue
            structural_findings.extend(_unknown_keys(raw, allowed_role_keys, path=f"$.roles[{index}]"))
            role = str(raw.get("role") or "")
            rate = raw.get("request_rate_per_active_minute")
            think = raw.get("think_time_ms")
            sample_sessions = raw.get("sample_sessions")
            if role in seen_roles:
                structural_findings.append(f"duplicate role: {role}")
            seen_roles.add(role)
            row_valid = (
                role in required_role_set
                and _is_number(sample_sessions)
                and float(sample_sessions).is_integer()
                and int(sample_sessions) > 0
                and isinstance(rate, Mapping)
                and isinstance(think, Mapping)
                and all(_is_number(rate.get(key)) for key in ("lower", "point", "upper"))
                and all(_is_number(think.get(key)) for key in ("p50", "p95"))
            )
            if not row_valid:
                invalid_row_count += 1
                continue
            lower, point, upper = (float(rate[key]) for key in ("lower", "point", "upper"))
            think_p50, think_p95 = (float(think[key]) for key in ("p50", "p95"))
            if not (
                0.0 < lower <= point <= upper
                and 0.0 <= think_p50 <= think_p95 <= 60_000.0
            ):
                invalid_row_count += 1
                continue
            role_samples[role] = int(sample_sessions)
            explicit_role_bounds[role] = (lower, point, upper)
            explicit_think[role] = (think_p50, think_p95)

    seed = int(source_sha256[:16], 16)
    role_metrics: list[dict[str, Any]] = []
    total_sessions = sum(role_samples.values())
    aggregate_bounds = [0.0, 0.0, 0.0]
    for index, role in enumerate(required_roles):
        count = role_samples[role]
        weight = (count / total_sessions) if total_sessions else 0.0
        if schema == CALIBRATION_TRACE_SCHEMA:
            lower, point, upper = _bootstrap_mean_interval(
                role_rates[role], seed=seed + index * 1009
            )
            think_p50 = percentile(role_think_times[role], 50) if role_think_times[role] else 0.0
            think_p95 = percentile(role_think_times[role], 95) if role_think_times[role] else 0.0
            method = "deterministic_bootstrap_95pct_mean_ci"
        else:
            lower, point, upper = explicit_role_bounds.get(role, (0.0, 0.0, 0.0))
            think_p50, think_p95 = explicit_think.get(role, (0.0, 0.0))
            method = "caller_supplied_interval_with_hash_pin"
        bounds = (lower, point, upper)
        for bound_index, value in enumerate(bounds):
            aggregate_bounds[bound_index] += weight * float(value)
        role_metrics.append(
            {
                "role": role,
                "sample_sessions": count,
                "observed_role_weight": round(weight, 6),
                "request_rate_per_active_minute": {
                    "lower": round(lower, 6),
                    "point": round(point, 6),
                    "upper": round(upper, 6),
                    "confidence_level": confidence_level,
                    "method": method,
                },
                "think_time_ms": {
                    "p50": round(think_p50, 3),
                    "p95": round(think_p95, 3),
                },
            }
        )

    profile_match = str(payload.get("journey_profile") or "") == journey_profile.profile_id
    evidence_class = str(payload.get("evidence_class") or "")
    required_evidence_class = (
        "measured_anonymous_operational_trace"
        if source_kind == "anonymous_session_trace"
        else "operator_supplied_measured_aggregate"
        if source_kind == "explicit_role_rates"
        else None
    )
    measured_evidence_class = bool(
        required_evidence_class and evidence_class == required_evidence_class
    )
    role_coverage_ok = (
        set(role for role, count in role_samples.items() if count > 0) == required_role_set
        and all(count >= MIN_CALIBRATION_SESSIONS_PER_ROLE for count in role_samples.values())
    )
    rate_valid = (
        invalid_row_count == 0
        and duplicate_row_count == 0
        and not structural_findings
        and all(0.0 < item["request_rate_per_active_minute"]["lower"] for item in role_metrics)
        and aggregate_bounds[0] > 0.0
    )
    freshness_ok = (
        timestamps_ordered
        and 0.0 <= age_seconds <= MAX_CALIBRATION_AGE_SECONDS
        and 0.0 <= end_age_seconds <= MAX_CALIBRATION_AGE_SECONDS
    )
    producer_attestation = verify_calibration_producer_attestation(
        attestation_path,
        source_sha256=source_sha256,
        source_schema_version=schema,
        evidence_class=evidence_class,
        journey_profile=journey_profile.profile_id,
        source_generated_at=generated_at,
        evaluated_at=evaluated_at,
    )
    gates = {
        "schema_supported": _calibration_gate(
            source_kind != "unsupported",
            schema,
            [CALIBRATION_TRACE_SCHEMA, CALIBRATION_ROLE_RATE_SCHEMA],
        ),
        "privacy_safe_shape": _calibration_gate(
            not privacy_findings and not structural_findings,
            {
                "forbidden_key_count": len(privacy_findings),
                "unknown_field_count": len(structural_findings),
            },
            {"forbidden_key_count": 0, "unknown_field_count": 0},
        ),
        "source_hash_verified": _calibration_gate(
            hash_verified,
            {"expected_sha256_present": bool(expected_hash), "matches": hash_verified},
            {"expected_sha256_present": True, "matches": True},
        ),
        "evaluation_time_pinned": _calibration_gate(
            as_of_pinned,
            as_of_pinned,
            True,
        ),
        "journey_profile_match": _calibration_gate(
            profile_match,
            payload.get("journey_profile"),
            journey_profile.profile_id,
        ),
        "measured_evidence_class": _calibration_gate(
            measured_evidence_class,
            evidence_class or None,
            required_evidence_class,
        ),
        "sample_size": _calibration_gate(
            total_sessions >= MIN_CALIBRATION_SESSIONS,
            total_sessions,
            MIN_CALIBRATION_SESSIONS,
        ),
        "role_coverage": _calibration_gate(
            role_coverage_ok,
            role_samples,
            {role: MIN_CALIBRATION_SESSIONS_PER_ROLE for role in required_roles},
        ),
        "observation_window": _calibration_gate(
            timestamps_ordered and window_seconds >= MIN_CALIBRATION_WINDOW_SECONDS,
            {"seconds": round(window_seconds, 3), "timestamps_ordered": timestamps_ordered},
            {"minimum_seconds": MIN_CALIBRATION_WINDOW_SECONDS, "timestamps_ordered": True},
        ),
        "freshness": _calibration_gate(
            freshness_ok,
            {
                "source_age_seconds": round(age_seconds, 3),
                "window_end_age_seconds": round(end_age_seconds, 3),
            },
            {"minimum_seconds": 0, "maximum_seconds": MAX_CALIBRATION_AGE_SECONDS},
        ),
        "rate_and_think_time_valid": _calibration_gate(
            rate_valid,
            {
                "invalid_row_count": invalid_row_count,
                "duplicate_row_count": duplicate_row_count,
            },
            {
                "invalid_row_count": 0,
                "duplicate_row_count": 0,
                "positive_ordered_rate_bounds": True,
            },
        ),
        "confidence_boundary": _calibration_gate(
            confidence_level >= 0.95,
            confidence_level,
            0.95,
        ),
        "trusted_producer_attestation": _calibration_gate(
            bool(producer_attestation.get("trusted")),
            {
                "status": producer_attestation.get("status"),
                "key_id": producer_attestation.get("key_id"),
                "signer_allowlisted": producer_attestation.get("signer_allowlisted"),
                "signature_valid": producer_attestation.get("signature_valid"),
                "source_binding_valid": producer_attestation.get("source_binding_valid"),
                "time_binding_valid": producer_attestation.get("time_binding_valid"),
            },
            {
                "status": "trusted_producer_attestation",
                "public_key_allowlisted": True,
                "signature_valid": True,
                "source_binding_valid": True,
                "time_binding_valid": True,
            },
        ),
    }
    failure_reasons = sorted(name for name, gate in gates.items() if not gate["pass"])
    consistency_failures = [
        name
        for name in failure_reasons
        if name not in {"measured_evidence_class", "trusted_producer_attestation"}
    ]
    internally_consistent = not consistency_failures
    producer_attested = bool(producer_attestation.get("trusted"))
    trusted_measured = producer_attested and measured_evidence_class
    eligible = internally_consistent and trusted_measured
    return _seal_verified_calibration_manifest({
        **base,
        "status": "qualified" if eligible else "unqualified",
        "consistency_status": (
            "internally_consistent" if internally_consistent else "inconsistent"
        ),
        "trust_status": (
            "trusted_measured_evidence"
            if trusted_measured
            else "untrusted_or_unattested"
        ),
        "eligible": eligible,
        "human_seat_conversion_allowed": eligible,
        "source": {
            "kind": source_kind,
            "schema_version": schema,
            "evidence_class": evidence_class or None,
            "file_name": candidate.name,
            "sha256": source_sha256,
            "expected_sha256": expected_hash or None,
            "hash_verified": hash_verified,
            "generated_at": _iso_utc(generated_at) if not timestamp_errors else None,
            "window_start": _iso_utc(window_start) if not timestamp_errors else None,
            "window_end": _iso_utc(window_end) if not timestamp_errors else None,
            "authenticity": (
                "trusted_producer_attested"
                if producer_attested
                else "self_asserted_or_unattested"
            ),
        },
        "producer_attestation": producer_attestation,
        "gates": gates,
        "role_metrics": role_metrics,
        "aggregate_request_rate_per_active_minute": (
            {
                "lower": round(aggregate_bounds[0], 6),
                "point": round(aggregate_bounds[1], 6),
                "upper": round(aggregate_bounds[2], 6),
                "confidence_level": confidence_level,
                "role_mix_basis": "observed_calibration_session_share",
            }
            if aggregate_bounds[0] > 0.0
            else None
        ),
        "failure_reasons": failure_reasons,
        "diagnostics": {
            "timestamp_errors": timestamp_errors,
            "invalid_row_count": invalid_row_count,
            "duplicate_row_count": duplicate_row_count,
            "privacy_forbidden_fields": privacy_findings,
            "structural_findings": structural_findings,
        },
    })


def build_capacity_calibration_manifest(
    source_path: Path | None,
    *,
    expected_source_sha256: str | None,
    as_of: str | None = None,
    attestation_path: Path | None = None,
    journey_profile: JourneyProfile = STAFF_READONLY_JOURNEY_V1,
) -> dict[str, Any]:
    """Return a fail-closed manifest for every input, including malformed JSON."""
    try:
        return _build_capacity_calibration_manifest(
            source_path,
            expected_source_sha256=expected_source_sha256,
            as_of=as_of,
            attestation_path=attestation_path,
            journey_profile=journey_profile,
        )
    except Exception as exc:  # noqa: BLE001 - malformed calibration is data, not a crash
        evaluated_at: str | None = None
        try:
            evaluated_at = (
                _iso_utc(_parse_utc_datetime(as_of, field_name="calibration as-of"))
                if as_of is not None
                else None
            )
        except Exception:  # noqa: BLE001 - preserve fail-closed output
            evaluated_at = None
        try:
            file_name = Path(source_path).name if source_path is not None else None
        except Exception:  # noqa: BLE001 - unusual PathLike must not escape fallback
            file_name = None
        return {
            "schema_version": CALIBRATION_MANIFEST_SCHEMA,
            "evaluated_at": evaluated_at,
            "journey_profile": getattr(journey_profile, "profile_id", None),
            "journey_profile_version": getattr(journey_profile, "version", None),
            "status": "unqualified",
            "consistency_status": "invalid_input",
            "trust_status": "untrusted_or_unattested",
            "eligible": False,
            "human_seat_conversion_allowed": False,
            "source": {
                "file_name": file_name,
                "authenticity": "unverified_malformed_input",
            }
            if file_name
            else None,
            "producer_attestation": _attestation_failure(
                "source_input_malformed_before_attestation"
            ),
            "requirements": {
                "expected_source_sha256_required": True,
                "explicit_as_of_required": True,
                "trusted_ed25519_producer_attestation_required": True,
                "trusted_public_keys_are_code_allowlisted": True,
            },
            "gates": {
                "input_parseable": _calibration_gate(
                    False,
                    {"error_type": type(exc).__name__},
                    True,
                )
            },
            "role_metrics": [],
            "aggregate_request_rate_per_active_minute": None,
            "failure_reasons": ["calibration_input_malformed"],
            "privacy": {
                "anonymous_aggregate_only": True,
                "raw_session_rows_persisted_in_manifest": False,
                "browser_history_read": False,
                "token_or_cookie_read": False,
            },
            "diagnostics": {
                "input_error_type": type(exc).__name__,
                "error_message_persisted": False,
            },
        }

__all__ = [name for name in globals() if not name.startswith("__")]
