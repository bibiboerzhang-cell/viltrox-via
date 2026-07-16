from __future__ import annotations

from tests.vkpi_load_test_support import *

def test_identity_preflight_rejects_rotated_tokens_for_the_same_principal() -> None:
    contexts = (
        load_test_module.RequestContext(None, "rotated-token-a", 0),
        load_test_module.RequestContext(None, "rotated-token-b", 1),
    )

    async def same_principal(_context, **_kwargs):
        return {
            "ok": True,
            "principal_id": 77,
            "organization_id": 1,
            "request_count": 2,
        }

    result = asyncio.run(
        verify_live_identity_contexts(
            contexts,
            backend_base="http://127.0.0.1:8102",
            max_response_bytes=1024,
            run_salt=b"run-local-test-salt",
            probe_fn=same_principal,
        )
    )
    assert result["pass"] is False
    assert result["verified_principal_count"] == 2
    assert result["distinct_auth_identity_count"] == 1
    assert "principal_id" not in json.dumps(result)
    assert "rotated-token" not in json.dumps(result)


def test_identity_preflight_accepts_unique_principals_in_one_organization() -> None:
    contexts = tuple(
        load_test_module.RequestContext(None, f"token-{index}", index) for index in range(3)
    )

    async def unique_principal(context, **_kwargs):
        return {
            "ok": True,
            "principal_id": context.slot + 1,
            "organization_id": 9,
            "request_count": 2,
        }

    result = asyncio.run(
        verify_live_identity_contexts(
            contexts,
            backend_base="http://127.0.0.1:8102",
            max_response_bytes=1024,
            run_salt=b"run-local-test-salt",
            probe_fn=unique_principal,
        )
    )
    assert result["pass"] is True
    assert result["distinct_auth_identity_count"] == 3
    assert result["organization_count"] == 1
    assert len(result["run_local_principal_bindings_sha256"]) == 3


def test_token_pool_rejects_more_than_session_hard_limit_before_execution(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(
        "VKPI_LOAD_TEST_TOKENS_JSON",
        json.dumps([f"token-{index}" for index in range(MAX_SOAK_VIRTUAL_USERS + 1)]),
    )
    monkeypatch.delenv("VKPI_LOAD_TEST_TOKEN", raising=False)
    with pytest.raises(ValueError, match="cannot exceed"):
        resolve_token_pool(None)


def test_token_pool_hard_limit_applies_before_duplicate_tokens_are_collapsed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(
        "VKPI_LOAD_TEST_TOKENS_JSON",
        json.dumps(["same-rotated-token"] * (MAX_SOAK_VIRTUAL_USERS + 1)),
    )
    monkeypatch.delenv("VKPI_LOAD_TEST_TOKEN", raising=False)
    with pytest.raises(ValueError, match="before deduplication"):
        resolve_token_pool(None)


@pytest.mark.parametrize(
    "mutator",
    [
        lambda payload: payload.update({"service": "redis"}),
        lambda payload: payload.update(
            {"observed_at": "2026-01-01T00:00:00Z"}
        ),
        lambda payload: payload.update({"metrics": {"active": 1}}),
        lambda payload: payload["metrics"].update({"active": float("nan")}),
        lambda payload: payload.update({"port": 54330}),
        lambda payload: payload.update({"run_nonce": "another-valid-nonce-0002"}),
    ],
)
def test_strict_sidecar_rejects_wrong_stale_partial_nonfinite_or_unbound_payload(
    tmp_path: Path,
    mutator,
) -> None:
    nonce = "round7-test-nonce-0001"
    payload = _telemetry_payload("db_pool", port=54329, nonce=nonce)
    mutator(payload)
    path = tmp_path / "db-sidecar.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    path.chmod(0o600)
    result = optional_json_telemetry_adapter(
        "db_pool",
        path,
        expected_port=54329,
        run_nonce=nonce,
    )
    assert result["available"] is False


@pytest.mark.parametrize(
    ("mutation", "reason"),
    [
        (
            lambda encoded: encoded.replace(
                '"service": "db_pool"',
                '"service": "db_pool", "service": "db_pool"',
                1,
            ),
            "duplicate JSON key",
        ),
        (
            lambda encoded: encoded.replace('"active": 4', '"active": NaN', 1),
            "non-finite JSON constant",
        ),
    ],
)
def test_strict_sidecar_json_rejects_duplicate_keys_and_nonfinite_constants(
    tmp_path: Path,
    mutation,
    reason: str,
) -> None:
    nonce = "round7-test-nonce-0001"
    encoded = json.dumps(_telemetry_payload("db_pool", port=54329, nonce=nonce))
    path = tmp_path / "malformed-db-sidecar.json"
    path.write_text(mutation(encoded), encoding="utf-8")
    path.chmod(0o600)

    result = optional_json_telemetry_adapter(
        "db_pool", path, expected_port=54329, run_nonce=nonce
    )

    assert result["available"] is False
    assert reason in result["reason"]


def test_strict_sidecar_rejects_static_snapshot_that_does_not_advance(tmp_path: Path) -> None:
    nonce = "round7-test-nonce-0001"
    payload = _telemetry_payload("db_pool", port=54329, nonce=nonce)
    path = tmp_path / "db-sidecar.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    path.chmod(0o600)
    reader = load_test_module.TelemetrySidecarReader(
        "db_pool", path, "127.0.0.1", 54329, nonce
    )
    assert reader.read()["available"] is True
    second = reader.read()
    assert second["available"] is False
    assert "did not advance" in second["reason"]


def test_allowlisted_independent_telemetry_producer_signature_is_verified(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    nonce = "round7-test-nonce-0001"
    signer = Ed25519PrivateKey.generate()
    key_id = "telemetry-producer-test-v1"
    monkeypatch.setattr(
        load_test_module,
        "TRUSTED_TELEMETRY_ED25519_PUBLIC_KEYS",
        {key_id: _public_key_base64(signer)},
    )
    payload = _telemetry_payload("db_pool", port=54329, nonce=nonce)
    _sign_telemetry_payload(payload, signer, key_id=key_id)
    path = tmp_path / "signed-db-sidecar.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    path.chmod(0o600)

    result = optional_json_telemetry_adapter(
        "db_pool", path, expected_port=54329, run_nonce=nonce
    )

    assert result["available"] is True
    assert result["external_input_trust"] == "trusted_independent_telemetry_producer"
    assert result["producer_attestation"]["trusted"] is True
    assert result["producer_attestation"]["signature_valid"] is True


def test_telemetry_sidecar_rejects_hardlinked_inode(tmp_path: Path) -> None:
    nonce = "round7-test-nonce-0001"
    path = tmp_path / "db-sidecar.json"
    path.write_text(
        json.dumps(_telemetry_payload("db_pool", port=54329, nonce=nonce)),
        encoding="utf-8",
    )
    path.chmod(0o600)
    alias = tmp_path / "db-sidecar-alias.json"
    alias.hardlink_to(path)

    result = optional_json_telemetry_adapter(
        "db_pool", path, expected_port=54329, run_nonce=nonce
    )

    assert result["available"] is False
    assert "hard link" in result["reason"]


def test_advancing_self_signed_sidecars_cannot_qualify_capacity(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    nonce = "round7-test-nonce-0001"
    attacker = Ed25519PrivateKey.generate()
    path = tmp_path / "forged-db-sidecar.json"
    observed = datetime.now(timezone.utc)
    reader = load_test_module.TelemetrySidecarReader(
        "db_pool", path, "127.0.0.1", 54329, nonce
    )
    results = []
    for sequence in (1, 2):
        payload = _telemetry_payload(
            "db_pool",
            port=54329,
            nonce=nonce,
            sequence=sequence,
            observed_at=(observed + timedelta(milliseconds=sequence)).isoformat().replace(
                "+00:00", "Z"
            ),
        )
        _sign_telemetry_payload(
            payload,
            attacker,
            key_id="self-signed-telemetry-attacker",
        )
        path.write_text(json.dumps(payload), encoding="utf-8")
        path.chmod(0o600)
        results.append(reader.read())

    assert all(item["available"] is True for item in results)
    assert all(item["producer_attestation"]["trusted"] is False for item in results)
    stages = _capacity_stage_fixture()
    forged_trust = all(
        item["producer_attestation"]["trusted"] is True for item in results
    )
    for trial in stages[0]["trials"]:
        adapters = trial["resource_telemetry"]["summary"]["optional_adapters"]
        adapters["db_pool"]["all_samples_trusted_independent_producer"] = forged_trust
        adapters["redis"]["all_samples_trusted_independent_producer"] = forged_trust
    verdict = fail_closed_capacity_verdict(
        stages,
        endpoint_thresholds=STAFF_READONLY_ENDPOINT_THRESHOLDS,
        calibration_manifest=_verified_role_calibration_manifest(tmp_path, monkeypatch),
        identity_fidelity=_capacity_identity(),
        performance_evidence=load_test_module._seal_live_stage_bundle(stages),
    )
    assert verdict["status"] == "unqualified"
    assert verdict["gates"]["resource_sidecars"]["pass"] is False
    assert "resource_sidecars" in verdict["failure_reasons"]


def test_listener_process_coverage_requires_every_target_service_on_every_sample() -> None:
    samples = [
        {
            "snapshot": {
                "listeners": {"8102": [{"pid": 10, "command": "backend"}], "54329": []},
                "processes": [{"pid": 10, "cpu_percent": 1.0, "rss_kib": 100}],
                "optional_adapters": {},
            }
        }
        for _ in range(3)
    ]
    summary = summarize_resource_telemetry(
        samples,
        required_listeners={"backend": 8102, "postgresql": 54329},
    )
    assert summary["listener_process_coverage"]["pass"] is False
    assert summary["listener_process_coverage"]["required_services"]["backend"][
        "samples_with_process_metrics"
    ] == 3
    assert summary["listener_process_coverage"]["required_services"]["postgresql"][
        "samples_with_listener"
    ] == 0


@pytest.mark.parametrize(
    "mutator",
    [
        lambda stages: stages[0].update({"virtual_users": "oops"}),
        lambda stages: next(iter(stages[0]["across_trials"]["by_endpoint"].values())).update(
            {"trial_count": "oops"}
        ),
        lambda stages: stages[0].update({"duration_seconds": "oops"}),
        lambda stages: stages[0].update({"requests_per_second": float("nan")}),
    ],
)
def test_capacity_verdict_malformed_stage_never_raises(mutator) -> None:
    stages = _capacity_stage_fixture()
    mutator(stages)
    verdict = fail_closed_capacity_verdict(
        stages,
        endpoint_thresholds=STAFF_READONLY_ENDPOINT_THRESHOLDS,
        calibration_manifest=_qualified_role_calibration_manifest(),
        identity_fidelity=_capacity_identity(),
        performance_evidence=True,
    )
    assert verdict["status"] == "unqualified"
    assert verdict["input_status"] == "invalid_input"
    assert verdict["failure_reasons"] == ["invalid_input"]


def test_endpoint_gate_rejects_zero_request_or_missing_metric_evidence(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    stages = _capacity_stage_fixture()
    for group in stages[0]["across_trials"]["by_endpoint"].values():
        group.clear()
        group["trial_count"] = 3
    verdict = fail_closed_capacity_verdict(
        stages,
        endpoint_thresholds=STAFF_READONLY_ENDPOINT_THRESHOLDS,
        calibration_manifest=_verified_role_calibration_manifest(tmp_path, monkeypatch),
        identity_fidelity=_capacity_identity(),
        performance_evidence=load_test_module._seal_live_stage_bundle(stages),
    )
    assert verdict["status"] == "unqualified"
    assert "endpoint_thresholds" in verdict["failure_reasons"]


def test_empty_failed_tier_is_not_a_measured_saturation_breakpoint(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    stages = _capacity_stage_fixture()
    stages[1] = {
        "virtual_users": 20,
        "threshold_pass": False,
        "trial_count": 0,
        "total_requests": 0,
        "trials": [],
        "stop_reasons": [],
    }
    verdict = fail_closed_capacity_verdict(
        stages,
        endpoint_thresholds=STAFF_READONLY_ENDPOINT_THRESHOLDS,
        calibration_manifest=_verified_role_calibration_manifest(tmp_path, monkeypatch),
        identity_fidelity=_capacity_identity(),
        performance_evidence=load_test_module._seal_live_stage_bundle(stages),
    )
    assert verdict["status"] == "unqualified"
    assert "saturation_breakpoint" in verdict["failure_reasons"]


def test_live_stage_capability_is_invalid_after_stage_mutation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    stages = _capacity_stage_fixture()
    evidence = load_test_module._seal_live_stage_bundle(stages)
    stages[0]["requests_per_second"] = 999.0
    verdict = fail_closed_capacity_verdict(
        stages,
        endpoint_thresholds=STAFF_READONLY_ENDPOINT_THRESHOLDS,
        calibration_manifest=_verified_role_calibration_manifest(tmp_path, monkeypatch),
        identity_fidelity=_capacity_identity(),
        performance_evidence=evidence,
    )
    assert verdict["status"] == "unqualified"
    assert "live_performance_evidence" in verdict["failure_reasons"]
    with pytest.raises(TypeError):
        json.dumps(evidence)


def test_legacy_v2_execution_hook_is_retired_fail_closed() -> None:
    args = build_parser().parse_args([])
    with pytest.raises(RuntimeError, match="legacy v2 load execution is retired"):
        asyncio.run(load_test_module._execute_legacy_v2(args))


def test_blocked_live_attempt_is_not_labeled_pressure_or_complete(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(load_test_module, "environment_snapshot", lambda *_args: {"listeners": {}})
    args = build_parser().parse_args(
        [
            "--execute-live",
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
            "--no-raw-samples",
        ]
    )

    async def never_called(*_args, **_kwargs):
        raise AssertionError("no request should be issued without an explicit token")

    report = asyncio.run(
        load_test_module._execute_v3_with_contexts(
            args,
            contexts=(load_test_module.RequestContext(None, None, 0),),
            request_fn=never_called,
            live=True,
            synthetic_fixture=False,
            auth_meta={"token_count": 0, "independent_session_count": 1},
            raw_writer=None,
        )
    )
    assert report["requested_live"] is True
    assert report["network_observed"] is False
    assert report["pressure_completed"] is False
    assert report["live_run"] is False
    assert report["evidence_type"] == "blocked_local_capacity_attempt"
    assert report["profiles"][0]["status"] == "blocked"


def test_identity_preflight_network_is_not_mislabeled_as_pressure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    args = build_parser().parse_args(
        [
            "--execute-live",
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
        ]
    )
    monkeypatch.setattr(load_test_module, "environment_snapshot", lambda *_args: {"listeners": {}})

    async def failed_identity_probe(_context, **_kwargs):
        return {"ok": False, "reason": "forbidden", "request_count": 2}

    async def unused_request(*_args, **_kwargs):
        raise AssertionError("pressure/preflight request must not run after identity failure")

    report = asyncio.run(
        load_test_module._execute_v3_with_contexts(
            args,
            contexts=(load_test_module.RequestContext(None, "token", 0),),
            request_fn=unused_request,
            live=True,
            synthetic_fixture=False,
            auth_meta={"token_count": 1, "independent_session_count": 1},
            raw_writer=None,
            identity_probe_fn=failed_identity_probe,
        )
    )

    assert report["requested_live"] is True
    assert report["network_observed"] is True
    assert report["network_requests_issued"] == 2
    assert report["pressure_completed"] is False
    assert report["live_run"] is False
    assert report["evidence_type"] == "live_local_readonly_preflight_only"


def test_zero_request_stage_is_not_pressure_completed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    args = build_parser().parse_args(
        [
            "--execute-live",
            "--profiles",
            "health",
            "--phases",
            "1",
            "--trials",
            "1",
            "--requests-per-phase",
            "20",
        ]
    )
    monkeypatch.setattr(load_test_module, "environment_snapshot", lambda *_args: {"listeners": {}})

    async def successful_preflight(_session, endpoint, **_kwargs):
        return {
            "endpoint": endpoint.name,
            "category": endpoint.category,
            "ok": True,
            "status": 200,
            "latency_ms": 1.0,
            "bytes": 1,
            "error_type": "",
        }

    async def zero_request_telemetry(operation, **_kwargs):
        operation.close()
        summary = summarize_requests([], elapsed_seconds=1.0, concurrency=1)
        return summary, load_test_module._offline_telemetry()

    monkeypatch.setattr(load_test_module, "_run_with_configured_telemetry", zero_request_telemetry)
    report = asyncio.run(
        load_test_module._execute_v3_with_contexts(
            args,
            contexts=(load_test_module.RequestContext(None, None, 0),),
            request_fn=successful_preflight,
            live=True,
            synthetic_fixture=False,
            auth_meta={"token_count": 0, "independent_session_count": 1},
            raw_writer=None,
        )
    )

    assert report["executed_stage_count"] == 1
    assert report["network_requests_issued"] == 1
    assert report["network_observed"] is True
    assert report["pressure_completed"] is False
    assert report["live_run"] is False


def test_completed_health_plus_blocked_selected_profile_is_not_complete(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    args = build_parser().parse_args(
        [
            "--execute-live",
            "--profiles",
            "health,light_db",
            "--phases",
            "1",
            "--trials",
            "1",
            "--requests-per-phase",
            "20",
            "--cooldown-seconds",
            "0",
        ]
    )
    monkeypatch.setattr(load_test_module, "environment_snapshot", lambda *_args: {"listeners": {}})

    async def fake_telemetry(operation, **_kwargs):
        return await operation, load_test_module._offline_telemetry()

    async def successful_request(_session, endpoint, **_kwargs):
        return {
            "endpoint": endpoint.name,
            "category": endpoint.category,
            "status": 200,
            "ok": True,
            "latency_ms": 1.0,
            "bytes": 1,
            "error_type": "",
            "error_detail": "",
        }

    monkeypatch.setattr(load_test_module, "_run_with_configured_telemetry", fake_telemetry)
    report = asyncio.run(
        load_test_module._execute_v3_with_contexts(
            args,
            contexts=(load_test_module.RequestContext(None, None, 0),),
            request_fn=successful_request,
            live=True,
            synthetic_fixture=False,
            auth_meta={"token_count": 0, "independent_session_count": 1},
            raw_writer=None,
        )
    )

    assert [item["status"] for item in report["profiles"]] == ["completed", "blocked"]
    assert report["pressure_observed"] is True
    assert report["pressure_completed"] is False
    assert report["evidence_type"] == "live_local_readonly_pressure_incomplete"
    report["operator_preflight"] = {"trusted": True}
    assert load_test_module.capacity_cli_summary_status(
        report,
        execute_live=True,
        fixture_configured=False,
    ) == "blocked"


def test_normal_live_fixture_wires_verified_identity_and_bound_stage_capability(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(load_test_module, "environment_snapshot", lambda *_args: {"listeners": {}})

    async def fake_telemetry(operation, **_kwargs):
        return await operation, load_test_module._offline_telemetry()

    monkeypatch.setattr(load_test_module, "_run_with_configured_telemetry", fake_telemetry)
    args = build_parser().parse_args(
        [
            "--execute-live",
            "--mode",
            "closed-loop-tiers",
            "--profiles",
            "mixed",
            "--soak-profile",
            "mixed",
            "--tiers",
            "1:0.02,2:0.02",
            "--session-count",
            "2",
            "--trials",
            "1",
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
            "--no-raw-samples",
        ]
    )
    contexts = tuple(
        load_test_module.RequestContext(None, f"token-{index}", index) for index in range(2)
    )

    async def fake_request(_session, endpoint, **_kwargs):
        return {
            "endpoint": endpoint.name,
            "category": endpoint.category,
            "status": 200,
            "ok": True,
            "latency_ms": 1.0,
            "bytes": 10,
            "error_type": "",
            "error_detail": "",
        }

    async def unique_identity(context, **_kwargs):
        return {
            "ok": True,
            "principal_id": context.slot + 100,
            "organization_id": 1,
            "request_count": 2,
        }

    report = asyncio.run(
        load_test_module._execute_v3_with_contexts(
            args,
            contexts=contexts,
            request_fn=fake_request,
            live=True,
            synthetic_fixture=False,
            auth_meta={
                "token_count": 2,
                "independent_session_count": 2,
                "token_emitted": False,
                "token_persisted": False,
            },
            raw_writer=None,
            identity_probe_fn=unique_identity,
        )
    )
    assert report["identity_preflight"]["pass"] is True
    assert report["identity_preflight"]["distinct_auth_identity_count"] == 2
    assert report["network_observed"] is True
    assert report["pressure_completed"] is True
    assert report["evidence_type"] == "live_local_readonly_pressure"
    report["operator_preflight"] = {"trusted": True}
    assert load_test_module.capacity_cli_summary_status(
        report,
        execute_live=True,
        fixture_configured=False,
    ) == "complete"
    assert report["profiles"][0]["capacity"]["capacity_verdict"]["gates"][
        "live_performance_evidence"
    ]["pass"] is True
    encoded = json.dumps(report)
    assert "token-0" not in encoded and "token-1" not in encoded
    assert "principal_id" not in encoded


def test_preflight_hard_limit_is_enforced_before_execution(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    args = build_parser().parse_args(["--profiles", "mixed", "--session-count", "1"])
    monkeypatch.setattr(load_test_module, "MAX_PREFLIGHT_REQUESTS", 1)
    with pytest.raises(ValueError, match="preflight requests"):
        validate_execution_args(args)


@pytest.mark.parametrize(
    ("extra_args", "expected_message"),
    [
        (["--phases", "1001"], "concurrency"),
        (["--phases", ",".join(str(index) for index in range(1, 34))], "stages"),
        (["--requests-per-phase", "250001"], "requests per phase"),
        (["--waves-per-phase", "1001"], "waves per phase"),
        (
            ["--phases", "1000", "--requests-per-phase", "20", "--waves-per-phase", "251"],
            "requests per stage",
        ),
        (
            [
                "--profiles",
                "static_frontend,health,light_db,heavy_aggregate,mixed",
                "--phases",
                "1,2",
                "--trials",
                "20",
                "--requests-per-phase",
                "10000",
            ],
            "planned ramp requests",
        ),
    ],
)
def test_ramp_code_owned_hard_limits_reject_oversized_plans(
    extra_args: list[str],
    expected_message: str,
) -> None:
    args = build_parser().parse_args(
        [
            "--profiles",
            "health",
            "--phases",
            "1",
            "--trials",
            "1",
            "--requests-per-phase",
            "20",
            *extra_args,
        ]
    )

    with pytest.raises(ValueError, match=expected_message):
        validate_execution_args(args)
    with pytest.raises(ValueError, match=expected_message):
        load_test_module.build_capacity_execution_plan(args)


def test_ramp_plan_records_non_signable_code_owned_hard_limits() -> None:
    args = build_parser().parse_args(
        ["--profiles", "health", "--phases", "1", "--trials", "1"]
    )
    plan = load_test_module.build_capacity_execution_plan(args)
    hard_limits = plan["workload"]["hard_limits"]

    assert hard_limits["maximum_ramp_concurrency"] == load_test_module.MAX_RAMP_CONCURRENCY
    assert hard_limits["maximum_ramp_stages"] == load_test_module.MAX_RAMP_STAGES
    assert hard_limits["maximum_ramp_requests_per_phase"] == (
        load_test_module.MAX_RAMP_REQUESTS_PER_PHASE
    )
    assert hard_limits["maximum_ramp_waves_per_phase"] == (
        load_test_module.MAX_RAMP_WAVES_PER_PHASE
    )
    assert hard_limits["maximum_ramp_total_requests"] == (
        load_test_module.MAX_RAMP_TOTAL_REQUESTS
    )


@pytest.mark.parametrize(
    ("extra_args", "expected_message"),
    [
        (
            ["--tiers", ",".join(f"{value}:1" for value in range(1, 34))],
            "closed-loop tiers",
        ),
        (
            ["--tiers", ",".join(f"{value}:3600" for value in range(1, 26))],
            "total duration",
        ),
        (
            ["--tiers", "999:3600,1000:3600"],
            "total VU-seconds",
        ),
        (
            [
                "--tiers",
                "1:1,2:1",
                "--trials",
                "20",
                "--soak-max-requests",
                "250001",
            ],
            "total requests",
        ),
    ],
)
def test_closed_loop_code_owned_aggregate_limits_reject_oversized_plans(
    extra_args: list[str],
    expected_message: str,
) -> None:
    args = build_parser().parse_args(
        [
            "--mode",
            "closed-loop-tiers",
            "--profiles",
            "mixed",
            "--soak-profile",
            "mixed",
            "--tiers",
            "1:1",
            "--trials",
            "1",
            *extra_args,
        ]
    )

    with pytest.raises(ValueError, match=expected_message):
        validate_execution_args(args)
    with pytest.raises(ValueError, match=expected_message):
        load_test_module.build_capacity_execution_plan(args)


def test_closed_loop_aggregate_limit_blocks_before_privileged_resources(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def forbidden(*_args, **_kwargs):
        raise AssertionError(
            "aggregate hard limits must fail before token/session/network/raw evidence"
        )

    output = tmp_path / "oversized-closed-loop.json"
    monkeypatch.setattr(load_test_module, "resolve_token_pool", forbidden)
    monkeypatch.setattr(load_test_module, "RawSampleWriter", forbidden)
    monkeypatch.setattr(load_test_module.aiohttp, "ClientSession", forbidden)

    with pytest.raises(SystemExit, match="total requests"):
        main(
            [
                "--execute-live",
                "--mode",
                "closed-loop-tiers",
                "--profiles",
                "mixed",
                "--soak-profile",
                "mixed",
                "--tiers",
                "1:1,2:1",
                "--trials",
                "20",
                "--soak-max-requests",
                "250001",
                "--output",
                str(output),
            ]
        )

    assert not output.exists()
    assert not output.with_name("oversized-closed-loop.samples.ndjson").exists()


def test_closed_loop_plan_records_code_owned_aggregate_limits() -> None:
    args = build_parser().parse_args(
        [
            "--mode",
            "closed-loop-tiers",
            "--profiles",
            "mixed",
            "--soak-profile",
            "mixed",
            "--tiers",
            "1:60,5:60",
            "--trials",
            "3",
            "--soak-max-requests",
            "100",
        ]
    )
    plan = load_test_module.build_capacity_execution_plan(args)
    hard_limits = plan["workload"]["hard_limits"]

    assert hard_limits["maximum_closed_loop_tiers"] == (
        load_test_module.MAX_CLOSED_LOOP_TIERS
    )
    assert hard_limits["maximum_closed_loop_total_duration_seconds"] == (
        load_test_module.MAX_CLOSED_LOOP_TOTAL_DURATION_SECONDS
    )
    assert hard_limits["maximum_closed_loop_total_requests"] == (
        load_test_module.MAX_CLOSED_LOOP_TOTAL_REQUESTS
    )
    assert hard_limits["planned_closed_loop_tiers"] == 2
    assert hard_limits["planned_closed_loop_total_duration_seconds"] == 360
    assert hard_limits["planned_closed_loop_total_vu_seconds"] == 1080
    assert hard_limits["planned_pressure_requests"] == 600
    assert plan["request_bounds"]["maximum_pressure_requests"] == 600


def test_cli_blocked_live_attempt_summary_is_not_complete(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr(load_test_module, "environment_snapshot", lambda *_args: {"listeners": {}})
    monkeypatch.delenv("VKPI_LOAD_TEST_TOKEN", raising=False)
    monkeypatch.delenv("VKPI_LOAD_TEST_TOKENS_JSON", raising=False)
    output = tmp_path / "blocked-live.json"
    assert main(
        [
            "--execute-live",
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
            "--no-raw-samples",
            "--output",
            str(output),
        ]
    ) == 2
    summary = json.loads(capsys.readouterr().out)
    assert summary["status"] == "blocked"
    assert summary["requested_live"] is True
    assert summary["network_observed"] is False
    assert summary["pressure_completed"] is False
