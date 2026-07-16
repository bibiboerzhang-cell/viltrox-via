from __future__ import annotations

from tests.vkpi_load_test_support import *

def test_capacity_verdict_requires_breakpoint_and_emits_bounded_human_seat_estimate(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    stages = _capacity_stage_fixture()
    candidate, breakpoint = detect_saturation_breakpoint(stages)
    assert candidate is stages[0]
    assert breakpoint["kind"] == "threshold_failure"
    verdict = fail_closed_capacity_verdict(
        stages,
        endpoint_thresholds=STAFF_READONLY_ENDPOINT_THRESHOLDS,
        calibration_manifest=_verified_role_calibration_manifest(tmp_path, monkeypatch),
        identity_fidelity=_capacity_identity(),
        performance_evidence=load_test_module._seal_live_stage_bundle(stages),
    )
    assert verdict["status"] == "qualified"
    assert verdict["failure_reasons"] == []
    assert verdict["capacity_claim_allowed"] is True
    estimate = verdict["human_seat_estimate"]
    assert estimate["metric"] == "active_human_seat_load_equivalent"
    assert 0 < estimate["lower"] <= estimate["point"] <= estimate["upper"]
    assert estimate["confidence_level"] == 0.95
    assert estimate["capacity_safety_factor"] == 0.8


def test_capacity_verdict_rejects_complete_forged_trust_contract() -> None:
    forged = _qualified_role_calibration_manifest()

    verdict = fail_closed_capacity_verdict(
        _capacity_stage_fixture(),
        endpoint_thresholds=STAFF_READONLY_ENDPOINT_THRESHOLDS,
        calibration_manifest=forged,
        identity_fidelity=_capacity_identity(),
        performance_evidence=True,
    )

    assert forged["status"] == "qualified"
    assert forged["eligible"] is True
    assert forged["human_seat_conversion_allowed"] is True
    assert verdict["status"] == "unqualified"
    assert verdict["human_seat_estimate"] is None
    assert "calibration_manifest" in verdict["failure_reasons"]
    assert verdict["gates"]["calibration_manifest"]["observed"][
        "in_process_verified_and_unmodified"
    ] is False


def test_signed_manifest_loses_authority_after_json_round_trip(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    verified = _verified_role_calibration_manifest(tmp_path, monkeypatch)
    replayed = json.loads(json.dumps(verified))
    verdict = fail_closed_capacity_verdict(
        _capacity_stage_fixture(),
        endpoint_thresholds=STAFF_READONLY_ENDPOINT_THRESHOLDS,
        calibration_manifest=replayed,
        identity_fidelity=_capacity_identity(),
        performance_evidence=True,
    )
    assert verdict["status"] == "unqualified"
    assert verdict["human_seat_estimate"] is None
    assert "calibration_manifest" in verdict["failure_reasons"]


def test_plain_boolean_cannot_self_assert_live_performance_evidence(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    verdict = fail_closed_capacity_verdict(
        _capacity_stage_fixture(),
        endpoint_thresholds=STAFF_READONLY_ENDPOINT_THRESHOLDS,
        calibration_manifest=_verified_role_calibration_manifest(tmp_path, monkeypatch),
        identity_fidelity=_capacity_identity(),
        performance_evidence=True,
    )
    assert verdict["status"] == "unqualified"
    assert verdict["human_seat_estimate"] is None
    assert "live_performance_evidence" in verdict["failure_reasons"]


@pytest.mark.parametrize(
    ("mutator", "failed_gate"),
    [
        (
            lambda stages: stages[0]["trials"][0]["resource_telemetry"]["summary"][
                "optional_adapters"
            ]["db_pool"].update({"available": False}),
            "resource_sidecars",
        ),
        (
            lambda stages: stages[0]["across_trials"]["requests_per_second"].update(
                {"max": 160.0}
            ),
            "three_trial_consistency",
        ),
        (
            lambda stages: stages[0]["across_trials"]["by_endpoint"].pop("events_list"),
            "endpoint_thresholds",
        ),
        (
            lambda stages: stages[0]["trials"][0].update(
                {"termination_reason": "max_requests"}
            ),
            "trial_duration",
        ),
    ],
)
def test_capacity_verdict_fails_closed_on_missing_or_inconsistent_evidence(
    mutator,
    failed_gate: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    stages = copy.deepcopy(_capacity_stage_fixture())
    mutator(stages)
    verdict = fail_closed_capacity_verdict(
        stages,
        endpoint_thresholds=STAFF_READONLY_ENDPOINT_THRESHOLDS,
        calibration_manifest=_verified_role_calibration_manifest(tmp_path, monkeypatch),
        identity_fidelity=_capacity_identity(),
        performance_evidence=load_test_module._seal_live_stage_bundle(stages),
    )
    assert verdict["status"] == "unqualified"
    assert verdict["human_seat_estimate"] is None
    assert failed_gate in verdict["failure_reasons"]


def test_capacity_verdict_does_not_treat_highest_passing_tier_as_capacity(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    stages = _capacity_stage_fixture()[:1]
    verdict = fail_closed_capacity_verdict(
        stages,
        endpoint_thresholds=STAFF_READONLY_ENDPOINT_THRESHOLDS,
        calibration_manifest=_verified_role_calibration_manifest(tmp_path, monkeypatch),
        identity_fidelity=_capacity_identity(),
        performance_evidence=load_test_module._seal_live_stage_bundle(stages),
    )
    assert verdict["status"] == "unqualified"
    assert verdict["human_seat_estimate"] is None
    assert "saturation_breakpoint" in verdict["failure_reasons"]


def test_capacity_verdict_rejects_self_asserted_identity_flags_with_low_counts(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    identity = _capacity_identity()
    identity["independent_http_session_count"] = 1
    identity["distinct_auth_identity_count"] = 1

    stages = _capacity_stage_fixture()
    verdict = fail_closed_capacity_verdict(
        stages,
        endpoint_thresholds=STAFF_READONLY_ENDPOINT_THRESHOLDS,
        calibration_manifest=_verified_role_calibration_manifest(tmp_path, monkeypatch),
        identity_fidelity=identity,
        performance_evidence=load_test_module._seal_live_stage_bundle(stages),
    )

    assert verdict["status"] == "unqualified"
    assert verdict["human_seat_estimate"] is None
    assert "identity_fidelity" in verdict["failure_reasons"]


def test_capacity_verdict_rejects_non_increasing_tiers(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    stages = _capacity_stage_fixture()
    stages[1]["virtual_users"] = 5
    identity = _capacity_identity()
    identity["max_tested_simulated_active_sessions"] = 10

    verdict = fail_closed_capacity_verdict(
        stages,
        endpoint_thresholds=STAFF_READONLY_ENDPOINT_THRESHOLDS,
        calibration_manifest=_verified_role_calibration_manifest(tmp_path, monkeypatch),
        identity_fidelity=identity,
        performance_evidence=load_test_module._seal_live_stage_bundle(stages),
    )

    assert verdict["status"] == "unqualified"
    assert verdict["human_seat_estimate"] is None
    assert "tier_order" in verdict["failure_reasons"]


def test_calibration_rejects_numeric_overflow_without_raising(tmp_path: Path) -> None:
    payload = json.loads(ROLE_CALIBRATION_FIXTURE.read_text(encoding="utf-8"))
    payload["roles"][0]["sample_sessions"] = 10**1000
    source = tmp_path / "overflow.json"
    source_hash = _write_json_with_hash(source, payload)

    manifest = build_capacity_calibration_manifest(
        source,
        expected_source_sha256=source_hash,
        as_of="2026-07-13T22:00:00Z",
    )

    assert manifest["eligible"] is False
    assert "rate_and_think_time_valid" in manifest["failure_reasons"]


def test_calibration_rejects_boolean_counts_and_rates_without_raising(tmp_path: Path) -> None:
    payload = json.loads(ROLE_CALIBRATION_FIXTURE.read_text(encoding="utf-8"))
    payload["evidence_class"] = "operator_supplied_measured_aggregate"
    payload["confidence_level"] = True
    payload["roles"][0]["sample_sessions"] = True
    payload["roles"][1]["request_rate_per_active_minute"]["lower"] = False
    source = tmp_path / "boolean-counts.json"
    source_hash = _write_json_with_hash(source, payload)

    manifest = build_capacity_calibration_manifest(
        source,
        expected_source_sha256=source_hash,
        as_of="2026-07-13T22:00:00Z",
    )

    assert manifest["status"] == "unqualified"
    assert manifest["consistency_status"] == "inconsistent"
    assert manifest["human_seat_conversion_allowed"] is False
    assert manifest["diagnostics"]["invalid_row_count"] == 2
    assert {"confidence_boundary", "rate_and_think_time_valid", "role_coverage"} <= set(
        manifest["failure_reasons"]
    )


@pytest.mark.parametrize(
    ("source_text", "as_of"),
    [
        ("{not-json", "2026-07-13T22:00:00Z"),
        ("true", "2026-07-13T22:00:00Z"),
        (json.dumps({"schema_version": "bad"}), "not-an-iso-time"),
    ],
)
def test_malformed_calibration_input_returns_fail_closed_manifest(
    tmp_path: Path,
    source_text: str,
    as_of: str,
) -> None:
    source = tmp_path / "malformed.json"
    source.write_text(source_text, encoding="utf-8")

    manifest = build_capacity_calibration_manifest(
        source,
        expected_source_sha256=hashlib.sha256(source.read_bytes()).hexdigest(),
        as_of=as_of,
        attestation_path=tmp_path / "missing-attestation.json",
    )

    assert manifest["status"] == "unqualified"
    assert manifest["consistency_status"] == "invalid_input"
    assert manifest["trust_status"] == "untrusted_or_unattested"
    assert manifest["human_seat_conversion_allowed"] is False
    assert manifest["gates"]["input_parseable"]["pass"] is False
    assert manifest["diagnostics"]["error_message_persisted"] is False


def test_malformed_attestation_booleans_fail_closed_without_raising(tmp_path: Path) -> None:
    payload = json.loads(ROLE_CALIBRATION_FIXTURE.read_text(encoding="utf-8"))
    payload["evidence_class"] = "operator_supplied_measured_aggregate"
    source = tmp_path / "measured-role-aggregate.json"
    source_hash = _write_json_with_hash(source, payload)
    attestation = tmp_path / "malformed-attestation.json"
    _write_json_with_hash(
        attestation,
        {
            "schema_version": CALIBRATION_ATTESTATION_SCHEMA,
            "algorithm": "Ed25519",
            "key_id": True,
            "source_sha256": source_hash,
            "source_schema_version": payload["schema_version"],
            "evidence_class": payload["evidence_class"],
            "journey_profile": payload["journey_profile"],
            "issued_at": False,
            "signature_base64": True,
        },
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
    assert manifest["producer_attestation"]["trusted"] is False
    assert {"attestation_key_id", "attestation_time_binding"} <= set(
        manifest["producer_attestation"]["failure_reasons"]
    )


def test_calibration_cli_plan_and_manifest_are_zero_network(tmp_path: Path) -> None:
    output = tmp_path / "plan.json"
    manifest_output = tmp_path / "calibration.json"
    assert (
        main(
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
                str(ROLE_CALIBRATION_FIXTURE),
                "--calibration-source-sha256",
                ROLE_CALIBRATION_SHA256,
                "--calibration-as-of",
                "2026-07-13T22:00:00Z",
                "--output",
                str(output),
                "--calibration-manifest-output",
                str(manifest_output),
            ]
        )
        == 0
    )
    plan = json.loads(output.read_text(encoding="utf-8"))
    manifest = json.loads(manifest_output.read_text(encoding="utf-8"))
    assert plan["live_run"] is False
    assert plan["network_requests_issued"] == 0
    assert plan["capacity_calibration"]["status"] == "unqualified"
    assert plan["capacity_calibration"]["consistency_status"] == "internally_consistent"
    assert plan["capacity_calibration"]["trust_status"] == "untrusted_or_unattested"
    assert plan["capacity_calibration"]["human_seat_conversion_allowed"] is False
    assert plan["capacity_calibration"]["failure_reasons"] == [
        "measured_evidence_class",
        "trusted_producer_attestation",
    ]
    assert manifest["status"] == "unqualified"
    assert manifest["report_sha256"]
    assert output.stat().st_mode & 0o077 == 0
    assert manifest_output.stat().st_mode & 0o077 == 0


def test_cli_malformed_calibration_time_stays_zero_network_and_unqualified(
    tmp_path: Path,
) -> None:
    output = tmp_path / "malformed-time-plan.json"

    assert (
        main(
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
                str(ROLE_CALIBRATION_FIXTURE),
                "--calibration-source-sha256",
                ROLE_CALIBRATION_SHA256,
                "--calibration-as-of",
                "malformed-time",
                "--output",
                str(output),
            ]
        )
        == 0
    )

    plan = json.loads(output.read_text(encoding="utf-8"))
    assert plan["live_run"] is False
    assert plan["network_requests_issued"] == 0
    assert plan["capacity_calibration"]["consistency_status"] == "invalid_input"
    assert plan["capacity_calibration"]["human_seat_conversion_allowed"] is False


def test_offline_journey_fixture_is_wired_to_fail_closed_capacity_verdict(
    tmp_path: Path,
) -> None:
    response_fixture = tmp_path / "responses.json"
    _write_json_with_hash(
        response_fixture,
        {
            "responses": {
                endpoint.name: {"status": 200, "latency_ms": 1.0, "bytes": 10}
                for endpoint in ENDPOINTS
            }
        },
    )
    output = tmp_path / "offline-journey.json"
    assert (
        main(
            [
                "--fixture",
                str(response_fixture),
                "--mode",
                "closed-loop-tiers",
                "--profiles",
                "mixed",
                "--soak-profile",
                "mixed",
                "--tiers",
                "1:0.05,2:0.05",
                "--session-count",
                "2",
                "--trials",
                "3",
                "--cooldown-seconds",
                "0",
                "--soak-window-seconds",
                "0.01",
                "--soak-max-requests",
                "100",
                "--journey-profile",
                "staff-readonly-v1",
                "--journey-pacing-scale",
                "0",
                "--role-calibration",
                str(ROLE_CALIBRATION_FIXTURE),
                "--calibration-source-sha256",
                ROLE_CALIBRATION_SHA256,
                "--calibration-as-of",
                "2026-07-13T22:00:00Z",
                "--no-raw-samples",
                "--output",
                str(output),
            ]
        )
        == 0
    )
    report = json.loads(output.read_text(encoding="utf-8"))
    assert report["network_requests_issued"] == 0
    capacity = report["profiles"][0]["capacity"]
    assert capacity["status"] == "not_evaluated_synthetic_fixture"
    verdict = capacity["capacity_verdict"]
    assert verdict["status"] == "unqualified"
    assert verdict["human_seat_estimate"] is None
    assert "live_performance_evidence" in verdict["failure_reasons"]
    assert "calibration_manifest" in verdict["failure_reasons"]
    assert "resource_sidecars" in verdict["failure_reasons"]
