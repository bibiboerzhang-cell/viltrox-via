from __future__ import annotations

import asyncio
import base64
import copy
import hashlib
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey


pytest.importorskip("aiohttp")

import scripts.load_test_vkpi_readonly as load_test_module
from scripts.load_test_vkpi_readonly import (
    CALIBRATION_ATTESTATION_SCHEMA,
    ENDPOINTS,
    MAX_SOAK_VIRTUAL_USERS,
    TELEMETRY_ATTESTATION_SCHEMA,
    TELEMETRY_SIDECAR_SCHEMA,
    STAFF_READONLY_JOURNEY_V1,
    STAFF_READONLY_ENDPOINT_THRESHOLDS,
    RawSampleWriter,
    Thresholds,
    aggregate_trial_summaries,
    build_capacity_calibration_manifest,
    build_parser,
    build_dry_run_report,
    capacity_interpretation,
    deterministic_soak_endpoint,
    deterministic_journey_role,
    deterministic_journey_step,
    detect_saturation_breakpoint,
    endpoint_stop_reasons,
    endpoints_for_profile,
    fail_closed_capacity_verdict,
    main,
    optional_json_telemetry_adapter,
    parse_positive_ints,
    parse_vu_duration_tiers,
    redact_secrets,
    report_contains_secret,
    resolve_token_pool,
    resolve_journey_profile,
    run_phase,
    run_soak,
    run_with_resource_telemetry,
    stop_reasons,
    summarize_resource_telemetry,
    summarize_requests,
    verify_live_identity_contexts,
    validate_loopback_base,
    validate_execution_args,
    validate_journey_profile,
    weighted_workload,
    write_report,
)


ROLE_CALIBRATION_FIXTURE = (
    Path(__file__).parent / "fixtures" / "vkpi_capacity_role_calibration_v1.json"
)
ROLE_CALIBRATION_SHA256 = "bebb87017de7c732c0dc2cb715c944f44f80e2774c976cc133ca255434cca59c"



def _telemetry_payload(
    name: str,
    *,
    port: int,
    nonce: str,
    sequence: int = 1,
    observed_at: str | None = None,
) -> dict:
    metrics = {
        "db_pool": {
            "active": 4,
            "idle": 2,
            "checked_out": 4,
            "max_size": 20,
            "waiting": 0,
            "overflow": 0,
            "checkout_wait_ms": 1.5,
        },
        "redis": {
            "connected_clients": 8,
            "blocked_clients": 0,
            "used_memory_bytes": 1_000_000,
            "ops_per_sec": 40,
            "keyspace_hits": 1000,
            "keyspace_misses": 5,
            "evicted_keys": 0,
        },
    }[name]
    return {
        "schema_version": TELEMETRY_SIDECAR_SCHEMA,
        "service": name,
        "host": "127.0.0.1",
        "port": port,
        "run_nonce": nonce,
        "observed_at": observed_at or datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "sequence": sequence,
        "metrics": metrics,
        "producer_attestation": {
            "schema_version": TELEMETRY_ATTESTATION_SCHEMA,
            "algorithm": "Ed25519",
            "key_id": "untrusted-test-producer",
            "signature_base64": base64.b64encode(bytes(64)).decode("ascii"),
        },
    }


def _sign_telemetry_payload(
    payload: dict,
    private_key: Ed25519PrivateKey,
    *,
    key_id: str,
) -> None:
    signed_snapshot = {
        field: payload.get(field)
        for field in (
            "schema_version",
            "service",
            "host",
            "port",
            "run_nonce",
            "observed_at",
            "sequence",
            "metrics",
        )
    }
    message = json.dumps(
        signed_snapshot,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    payload["producer_attestation"] = {
        "schema_version": TELEMETRY_ATTESTATION_SCHEMA,
        "algorithm": "Ed25519",
        "key_id": key_id,
        "signature_base64": base64.b64encode(private_key.sign(message)).decode("ascii"),
    }



def _write_json_with_hash(path: Path, payload: dict) -> str:
    encoded = json.dumps(payload, ensure_ascii=False, indent=2) + "\n"
    path.write_text(encoded, encoding="utf-8")
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _write_signed_calibration_attestation(
    path: Path,
    *,
    source_path: Path,
    private_key: Ed25519PrivateKey,
    key_id: str,
    issued_at: str = "2026-07-13T21:30:00Z",
) -> str:
    source_payload = json.loads(source_path.read_text(encoding="utf-8"))
    signed = {
        "schema_version": CALIBRATION_ATTESTATION_SCHEMA,
        "algorithm": "Ed25519",
        "key_id": key_id,
        "source_sha256": hashlib.sha256(source_path.read_bytes()).hexdigest(),
        "source_schema_version": source_payload["schema_version"],
        "evidence_class": source_payload["evidence_class"],
        "journey_profile": source_payload["journey_profile"],
        "issued_at": issued_at,
    }
    message = json.dumps(
        signed,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    payload = {
        **signed,
        "signature_base64": base64.b64encode(private_key.sign(message)).decode("ascii"),
    }
    return _write_json_with_hash(path, payload)


def _public_key_base64(private_key: Ed25519PrivateKey) -> str:
    encoded = private_key.public_key().public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    )
    return base64.b64encode(encoded).decode("ascii")


def _qualified_role_calibration_manifest() -> dict:
    """A fully forged JSON-shaped contract used only by bypass tests."""
    return {
        "status": "qualified",
        "consistency_status": "internally_consistent",
        "trust_status": "trusted_measured_evidence",
        "eligible": True,
        "human_seat_conversion_allowed": True,
        "source": {"authenticity": "trusted_producer_attested"},
        "producer_attestation": {
            "status": "trusted_producer_attestation",
            "trusted": True,
            "signature_valid": True,
            "signer_allowlisted": True,
            "source_binding_valid": True,
            "time_binding_valid": True,
        },
        "aggregate_request_rate_per_active_minute": {
            "lower": 4.821429,
            "point": 5.453571,
            "upper": 6.221429,
            "confidence_level": 0.95,
            "role_mix_basis": "test_only_qualified_contract",
        },
    }


def _verified_role_calibration_manifest(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> dict:
    payload = json.loads(ROLE_CALIBRATION_FIXTURE.read_text(encoding="utf-8"))
    payload["evidence_class"] = "operator_supplied_measured_aggregate"
    source = tmp_path / "verdict-role-calibration.json"
    source_hash = _write_json_with_hash(source, payload)
    signer = Ed25519PrivateKey.generate()
    key_id = "capacity-verdict-producer-test-v1"
    monkeypatch.setattr(
        load_test_module,
        "TRUSTED_CALIBRATION_ED25519_PUBLIC_KEYS",
        {key_id: _public_key_base64(signer)},
    )
    attestation = tmp_path / "verdict-role-calibration.attestation.json"
    _write_signed_calibration_attestation(
        attestation,
        source_path=source,
        private_key=signer,
        key_id=key_id,
    )
    manifest = build_capacity_calibration_manifest(
        source,
        expected_source_sha256=source_hash,
        as_of="2026-07-13T22:00:00Z",
        attestation_path=attestation,
    )
    assert manifest["status"] == "qualified"
    return manifest



def _capacity_stage_fixture() -> list[dict]:
    endpoint_groups = {
        name: {
            "trial_count": 3,
            "requests": {"min": 10.0, "median": 10.0, "max": 10.0},
            "error_rate": {"min": 0.0, "median": 0.0, "max": 0.0},
            "p50_ms": {"min": 40.0, "median": 45.0, "max": 50.0},
            "p95_ms": {"min": 90.0, "median": 100.0, "max": 110.0},
            "p99_ms": {"min": 120.0, "median": 130.0, "max": 140.0},
            "status_codes": {"200": 30},
            "error_types": {},
        }
        for name in STAFF_READONLY_ENDPOINT_THRESHOLDS
    }
    trials = []
    for index, rps in enumerate((100.0, 105.0, 110.0)):
        trial_endpoint_groups = {
            name: {
                "requests": 10,
                "error_rate": 0.0,
                "p50_ms": 45.0,
                "p95_ms": 100.0,
                "p99_ms": 130.0,
                "status_codes": {"200": 10},
                "error_types": {},
            }
            for name in STAFF_READONLY_ENDPOINT_THRESHOLDS
        }
        trials.append(
            {
                "trial_index": index,
                "threshold_pass": True,
                "termination_reason": "duration_elapsed",
                "elapsed_seconds": 60.0,
                "total_requests": 60,
                "requests_per_second": rps,
                "by_endpoint": trial_endpoint_groups,
                "resource_telemetry": {
                    "summary": {
                        "sample_count": 3,
                        "process_metrics_available": True,
                        "listener_process_coverage": {"pass": True},
                        "optional_adapters": {
                            "db_pool": {
                                "available": True,
                                "all_samples_fresh_bound_and_advancing": True,
                                "all_samples_trusted_independent_producer": True,
                            },
                            "redis": {
                                "available": True,
                                "all_samples_fresh_bound_and_advancing": True,
                                "all_samples_trusted_independent_producer": True,
                            },
                        },
                    }
                },
            }
        )
    return [
        {
            "virtual_users": 10,
            "duration_seconds": 60.0,
            "trial_count": 3,
            "threshold_pass": True,
            "stop_reasons": [],
            "total_requests": 180,
            "requests_per_second": 105.0,
            "error_rate": 0.0,
            "latency_ms": {"p50": 45.0, "p95": 110.0, "p99": 140.0},
            "status_codes": {"200": 180},
            "across_trials": {
                "requests_per_second": {"min": 100.0, "median": 105.0, "max": 110.0},
                "latency_ms": {
                    "p50": {"min": 40.0, "median": 45.0, "max": 50.0},
                    "p95": {"min": 90.0, "median": 100.0, "max": 110.0},
                    "p99": {"min": 120.0, "median": 130.0, "max": 140.0},
                },
                "by_endpoint": endpoint_groups,
            },
            "trials": trials,
        },
        {
            "virtual_users": 20,
            "duration_seconds": 60.0,
            "trial_count": 1,
            "threshold_pass": False,
            "stop_reasons": ["endpoint:events_list:p95_latency"],
            "total_requests": 60,
            "trials": [
                {
                    "trial_index": 0,
                    "total_requests": 60,
                    "threshold_pass": False,
                    "stop_reasons": ["endpoint:events_list:p95_latency"],
                }
            ],
            "requests_per_second": 106.0,
            "error_rate": 0.0,
            "latency_ms": {"p50": 100.0, "p95": 1800.0, "p99": 2500.0},
        },
    ]


def _capacity_identity() -> dict:
    return {
        "max_tested_simulated_active_sessions": 20,
        "one_independent_http_session_per_tested_vu": True,
        "one_distinct_auth_identity_per_tested_vu": True,
        "independent_http_session_count": 20,
        "distinct_auth_identity_count": 20,
        "verified_principal_count": 20,
        "organization_count": 1,
        "identity_preflight_pass": True,
        "raw_principals_persisted": False,
        "tokens_persisted": False,
        "run_local_principal_bindings_sha256": [f"binding-{index}" for index in range(20)],
    }





__all__ = [name for name in globals() if not name.startswith("__")]
