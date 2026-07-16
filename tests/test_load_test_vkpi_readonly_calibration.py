from __future__ import annotations

from tests.vkpi_load_test_support import *

def test_self_authored_hash_pinned_role_aggregate_is_consistent_but_untrusted(
    tmp_path: Path,
) -> None:
    payload = json.loads(ROLE_CALIBRATION_FIXTURE.read_text(encoding="utf-8"))
    payload["evidence_class"] = "operator_supplied_measured_aggregate"
    source = tmp_path / "measured-role-aggregate.json"
    source_hash = _write_json_with_hash(source, payload)
    manifest = build_capacity_calibration_manifest(
        source,
        expected_source_sha256=source_hash,
        as_of="2026-07-13T22:00:00Z",
    )
    assert manifest["status"] == "unqualified"
    assert manifest["consistency_status"] == "internally_consistent"
    assert manifest["trust_status"] == "untrusted_or_unattested"
    assert manifest["eligible"] is False
    assert manifest["human_seat_conversion_allowed"] is False
    assert manifest["source"]["hash_verified"] is True
    assert manifest["source"]["sha256"] == source_hash
    assert manifest["source"]["evidence_class"] == "operator_supplied_measured_aggregate"
    assert manifest["gates"]["sample_size"]["observed"] == 140
    assert manifest["gates"]["role_coverage"]["pass"] is True
    assert manifest["gates"]["observation_window"]["pass"] is True
    assert manifest["gates"]["freshness"]["pass"] is True
    assert manifest["gates"]["trusted_producer_attestation"]["pass"] is False
    assert manifest["source"]["authenticity"] == "self_asserted_or_unattested"
    assert manifest["aggregate_request_rate_per_active_minute"] == {
        "lower": 4.821429,
        "point": 5.453571,
        "upper": 6.221429,
        "confidence_level": 0.95,
        "role_mix_basis": "observed_calibration_session_share",
    }
    encoded = json.dumps(manifest)
    assert "authorization" not in encoded.lower()
    assert "password" not in encoded.lower()


def test_allowlisted_offline_ed25519_attestation_qualifies_measured_aggregate(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    payload = json.loads(ROLE_CALIBRATION_FIXTURE.read_text(encoding="utf-8"))
    payload["evidence_class"] = "operator_supplied_measured_aggregate"
    source = tmp_path / "measured-role-aggregate.json"
    source_hash = _write_json_with_hash(source, payload)
    signer = Ed25519PrivateKey.generate()
    key_id = "capacity-producer-test-v1"
    monkeypatch.setattr(
        load_test_module,
        "TRUSTED_CALIBRATION_ED25519_PUBLIC_KEYS",
        {key_id: _public_key_base64(signer)},
    )
    attestation = tmp_path / "measured-role-aggregate.attestation.json"
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
    assert manifest["consistency_status"] == "internally_consistent"
    assert manifest["trust_status"] == "trusted_measured_evidence"
    assert manifest["eligible"] is True
    assert manifest["human_seat_conversion_allowed"] is True
    assert manifest["source"]["authenticity"] == "trusted_producer_attested"
    attestation_result = manifest["producer_attestation"]
    assert attestation_result["status"] == "trusted_producer_attestation"
    assert attestation_result["trusted"] is True
    assert attestation_result["signature_valid"] is True
    assert attestation_result["signer_allowlisted"] is True
    assert attestation_result["source_binding_valid"] is True
    assert attestation_result["time_binding_valid"] is True
    assert attestation_result["key_id"] == key_id
    encoded = json.dumps(manifest)
    assert "private_key" not in encoded
    assert "signature_base64" not in encoded

    args = build_parser().parse_args(
        [
            "--mode",
            "closed-loop-tiers",
            "--profiles",
            "mixed",
            "--soak-profile",
            "mixed",
            "--tiers",
            "1:60",
            "--session-count",
            "1",
            "--journey-profile",
            "staff-readonly-v1",
            "--role-calibration",
            str(source),
            "--calibration-source-sha256",
            source_hash,
            "--calibration-as-of",
            "2026-07-13T22:00:00Z",
            "--calibration-attestation",
            str(attestation),
        ]
    )
    validate_execution_args(args)
    plan = build_dry_run_report(args)
    assert plan["network_requests_issued"] == 0
    assert plan["capacity_calibration"]["status"] == "qualified"


def test_wrong_ed25519_signer_cannot_authorize_human_seat_conversion(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    payload = json.loads(ROLE_CALIBRATION_FIXTURE.read_text(encoding="utf-8"))
    payload["evidence_class"] = "operator_supplied_measured_aggregate"
    source = tmp_path / "measured-role-aggregate.json"
    source_hash = _write_json_with_hash(source, payload)
    trusted_signer = Ed25519PrivateKey.generate()
    wrong_signer = Ed25519PrivateKey.generate()
    key_id = "capacity-producer-test-v1"
    monkeypatch.setattr(
        load_test_module,
        "TRUSTED_CALIBRATION_ED25519_PUBLIC_KEYS",
        {key_id: _public_key_base64(trusted_signer)},
    )
    attestation = tmp_path / "wrong-signer.attestation.json"
    _write_signed_calibration_attestation(
        attestation,
        source_path=source,
        private_key=wrong_signer,
        key_id=key_id,
    )

    manifest = build_capacity_calibration_manifest(
        source,
        expected_source_sha256=source_hash,
        as_of="2026-07-13T22:00:00Z",
        attestation_path=attestation,
    )

    assert manifest["consistency_status"] == "internally_consistent"
    assert manifest["trust_status"] == "untrusted_or_unattested"
    assert manifest["human_seat_conversion_allowed"] is False
    assert manifest["producer_attestation"]["signer_allowlisted"] is True
    assert manifest["producer_attestation"]["signature_valid"] is False
    assert "attestation_signature" in manifest["producer_attestation"]["failure_reasons"]


def test_self_signed_unallowlisted_attestation_is_not_trusted(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    payload = json.loads(ROLE_CALIBRATION_FIXTURE.read_text(encoding="utf-8"))
    payload["evidence_class"] = "operator_supplied_measured_aggregate"
    source = tmp_path / "measured-role-aggregate.json"
    source_hash = _write_json_with_hash(source, payload)
    attacker_key = Ed25519PrivateKey.generate()
    monkeypatch.setattr(
        load_test_module,
        "TRUSTED_CALIBRATION_ED25519_PUBLIC_KEYS",
        {},
    )
    attestation = tmp_path / "self-signed.attestation.json"
    _write_signed_calibration_attestation(
        attestation,
        source_path=source,
        private_key=attacker_key,
        key_id="unapproved-self-signer",
    )

    manifest = build_capacity_calibration_manifest(
        source,
        expected_source_sha256=source_hash,
        as_of="2026-07-13T22:00:00Z",
        attestation_path=attestation,
    )

    assert manifest["consistency_status"] == "internally_consistent"
    assert manifest["trust_status"] == "untrusted_or_unattested"
    assert manifest["human_seat_conversion_allowed"] is False
    assert manifest["producer_attestation"]["signer_allowlisted"] is False
    assert "attestation_signer_not_allowlisted" in manifest["producer_attestation"][
        "failure_reasons"
    ]


def test_calibration_hash_missing_or_mismatched_fails_closed() -> None:
    missing = build_capacity_calibration_manifest(
        ROLE_CALIBRATION_FIXTURE,
        expected_source_sha256=None,
        as_of="2026-07-13T22:00:00Z",
    )
    mismatch = build_capacity_calibration_manifest(
        ROLE_CALIBRATION_FIXTURE,
        expected_source_sha256="0" * 64,
        as_of="2026-07-13T22:00:00Z",
    )
    for manifest in (missing, mismatch):
        assert manifest["status"] == "unqualified"
        assert manifest["human_seat_conversion_allowed"] is False
        assert manifest["gates"]["source_hash_verified"]["pass"] is False
        assert "source_hash_verified" in manifest["failure_reasons"]


def test_calibration_without_explicit_as_of_is_reproducible_but_unqualified() -> None:
    first = build_capacity_calibration_manifest(
        ROLE_CALIBRATION_FIXTURE,
        expected_source_sha256=ROLE_CALIBRATION_SHA256,
        as_of=None,
    )
    second = build_capacity_calibration_manifest(
        ROLE_CALIBRATION_FIXTURE,
        expected_source_sha256=ROLE_CALIBRATION_SHA256,
        as_of=None,
    )
    assert first == second
    assert first["eligible"] is False
    assert first["evaluated_at"] == "2026-07-13T21:00:00Z"
    assert first["gates"]["evaluation_time_pinned"]["pass"] is False
    assert "evaluation_time_pinned" in first["failure_reasons"]


def test_anonymous_trace_calibration_is_reproducible_and_does_not_persist_rows(
    tmp_path: Path,
) -> None:
    window_start = datetime(2026, 7, 6, 20, tzinfo=timezone.utc)
    roles = ["event_planner", "market_strategist", "dealer_researcher"]
    sessions = []
    for index in range(120):
        started = window_start + timedelta(minutes=index * 30)
        ended = started + timedelta(minutes=5 + (index % 3))
        sessions.append(
            {
                "role": roles[index % len(roles)],
                "started_at": started.isoformat().replace("+00:00", "Z"),
                "ended_at": ended.isoformat().replace("+00:00", "Z"),
                "request_count": 4 + (index % 4),
                "think_time_ms": 3000.0 + index,
            }
        )
    trace = tmp_path / "trace.json"
    source_hash = _write_json_with_hash(
        trace,
        {
            "schema_version": "vkpi-anonymous-session-trace/v1",
            "evidence_class": "measured_anonymous_operational_trace",
            "generated_at": "2026-07-13T21:00:00Z",
            "window_start": "2026-07-06T20:00:00Z",
            "window_end": "2026-07-13T20:00:00Z",
            "journey_profile": "staff-readonly-v1",
            "sessions": sessions,
        },
    )
    first = build_capacity_calibration_manifest(
        trace,
        expected_source_sha256=source_hash,
        as_of="2026-07-13T22:00:00Z",
    )
    second = build_capacity_calibration_manifest(
        trace,
        expected_source_sha256=source_hash,
        as_of="2026-07-13T22:00:00Z",
    )
    assert first == second
    assert first["status"] == "unqualified"
    assert first["consistency_status"] == "internally_consistent"
    assert first["trust_status"] == "untrusted_or_unattested"
    assert first["eligible"] is False
    assert first["human_seat_conversion_allowed"] is False
    assert first["source"]["authenticity"] == "self_asserted_or_unattested"
    assert first["failure_reasons"] == ["trusted_producer_attestation"]
    assert first["source"]["kind"] == "anonymous_session_trace"
    assert sum(item["sample_sessions"] for item in first["role_metrics"]) == 120
    assert "sessions" not in first
    assert all("started_at" not in item for item in first["role_metrics"])


def test_stale_incomplete_or_pii_shaped_calibration_remains_unqualified(tmp_path: Path) -> None:
    payload = json.loads(ROLE_CALIBRATION_FIXTURE.read_text(encoding="utf-8"))
    payload["generated_at"] = "2026-06-20T21:00:00Z"
    payload["window_start"] = "2026-06-13T20:00:00Z"
    payload["window_end"] = "2026-06-20T20:00:00Z"
    payload["roles"][0]["sample_sessions"] = 5
    payload["roles"][0]["user_id"] = "forbidden-even-if-anonymous"
    source = tmp_path / "unsafe.json"
    source_hash = _write_json_with_hash(source, payload)
    manifest = build_capacity_calibration_manifest(
        source,
        expected_source_sha256=source_hash,
        as_of="2026-07-13T22:00:00Z",
    )
    assert manifest["eligible"] is False
    assert manifest["human_seat_conversion_allowed"] is False
    assert {"freshness", "privacy_safe_shape", "role_coverage"} <= set(
        manifest["failure_reasons"]
    )
    assert manifest["diagnostics"]["privacy_forbidden_fields"] == ["$.roles[0].user_id"]
