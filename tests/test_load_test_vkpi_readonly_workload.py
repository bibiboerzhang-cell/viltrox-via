from __future__ import annotations

from tests.vkpi_load_test_support import *

def test_endpoint_allowlist_is_get_only_read_surface() -> None:
    assert ENDPOINTS
    assert {item.category for item in ENDPOINTS} == {
        "static_frontend",
        "health",
        "light_db",
        "heavy_aggregate",
    }
    assert all(item.path.startswith("/") for item in ENDPOINTS)
    assert not any(word in item.path for item in ENDPOINTS for word in ("refresh", "promote", "enqueue"))
    # Keep Dashboard outside the pressure allow-list until repeated requests on
    # the loaded runtime prove that no lineage snapshots are created.
    assert not any("/dashboard" in item.path for item in ENDPOINTS)
    assert {item.name for item in endpoints_for_profile("mixed")} == {item.name for item in ENDPOINTS}


@pytest.mark.parametrize(
    "value",
    ["https://example.com", "http://10.0.0.5:8102", "http://user:pw@127.0.0.1:8102"],
)
def test_non_loopback_or_embedded_credentials_are_rejected(value: str) -> None:
    with pytest.raises(ValueError):
        validate_loopback_base(value)


def test_loopback_and_strict_increasing_phases_are_accepted() -> None:
    assert validate_loopback_base("http://127.0.0.1:8102/") == "http://127.0.0.1:8102"
    assert validate_loopback_base("http://localhost:5173") == "http://localhost:5173"
    assert parse_positive_ints("1,5,10,20,40,80") == (1, 5, 10, 20, 40, 80)
    with pytest.raises(ValueError):
        parse_positive_ints("1,10,5")


def test_weighted_workload_is_deterministic_and_bounded() -> None:
    endpoints = endpoints_for_profile("light_db")
    first = weighted_workload(endpoints, 100, seed=7)
    second = weighted_workload(endpoints, 100, seed=7)
    assert [item.name for item in first] == [item.name for item in second]
    assert len(first) == 100
    assert set(first).issubset(set(endpoints))
    counts = {endpoint.name: [item.name for item in first].count(endpoint.name) for endpoint in endpoints}
    assert max(counts.values()) - min(counts.values()) <= 1


def test_soak_endpoint_schedule_is_deterministic_per_virtual_user() -> None:
    endpoints = endpoints_for_profile("light_db")
    first = [
        deterministic_soak_endpoint(endpoints, seed=11, virtual_user_id=4, request_index=index).name
        for index in range(12)
    ]
    second = [
        deterministic_soak_endpoint(endpoints, seed=11, virtual_user_id=4, request_index=index).name
        for index in range(12)
    ]
    assert first == second
    assert set(first) == {endpoint.name for endpoint in endpoints}


def test_staff_journey_is_allowlisted_versioned_and_explicitly_uncalibrated() -> None:
    profile = resolve_journey_profile("staff-readonly-v1")
    assert profile is STAFF_READONLY_JOURNEY_V1
    validate_journey_profile(profile, ENDPOINTS)
    public = profile.public_dict(pacing_scale=1.0)
    assert public["version"] == "1.0.0"
    assert public["production_trace_calibrated"] is False
    assert public["human_user_capacity_claim_allowed"] is False
    assert {step["endpoint"] for role in public["roles"] for step in role["steps"]} <= {
        endpoint.name for endpoint in ENDPOINTS
    }


def test_staff_journey_assignment_and_step_order_are_seed_reproducible() -> None:
    profile = STAFF_READONLY_JOURNEY_V1
    first_role = deterministic_journey_role(profile, seed=41, virtual_user_id=7)
    second_role = deterministic_journey_role(profile, seed=41, virtual_user_id=7)
    assert first_role == second_role
    first_cycle = [
        deterministic_journey_step(
            profile,
            seed=41,
            virtual_user_id=7,
            request_index=index,
        )
        for index in range(len(first_role.steps) * 2)
    ]
    assert [item[0].name for item in first_cycle[: len(first_role.steps)]] == [
        step.endpoint_name for step in first_role.steps
    ]
    assert [item[0].name for item in first_cycle[: len(first_role.steps)]] == [
        item[0].name for item in first_cycle[len(first_role.steps) :]
    ]


def test_summary_tail_latency_error_rate_and_status_codes() -> None:
    results = [
        {"endpoint": "events_list", "category": "light_db", "ok": True, "status": 200, "latency_ms": 10, "bytes": 100},
        {"endpoint": "events_list", "category": "light_db", "ok": True, "status": 200, "latency_ms": 20, "bytes": 100},
        {
            "endpoint": "dealers_list",
            "category": "light_db",
            "ok": False,
            "status": 500,
            "latency_ms": 100,
            "bytes": 20,
            "error_type": "",
        },
    ]
    summary = summarize_requests(results, elapsed_seconds=0.5, concurrency=3)
    assert summary["total_requests"] == 3
    assert summary["success_count"] == 2
    assert summary["requests_per_second"] == 6.0
    assert summary["latency_ms"]["p95"] == 100.0
    assert summary["status_codes"] == {"200": 2, "500": 1}
    assert summary["by_endpoint"]["events_list"]["success_rate"] == 1.0


def test_ramp_models_request_workers_not_virtual_users() -> None:
    async def fake_request(_session, endpoint, **_kwargs):
        await asyncio.sleep(0)
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

    result = asyncio.run(
        run_phase(
            None,
            endpoints_for_profile("light_db"),
            concurrency=3,
            total_requests=12,
            frontend_base="http://127.0.0.1:5173",
            backend_base="http://127.0.0.1:8102",
            token=None,
            max_response_bytes=1024,
            seed=23,
            request_fn=fake_request,
        )
    )
    assert result["total_requests"] == 12
    assert result["load_model"]["generator_async_request_workers"] == 3
    assert result["load_model"]["maximum_in_flight_requests"] == 3
    assert result["load_model"]["virtual_users"] is None
    assert result["workload"]["planned_requests"] == 12
    assert sum(result["workload"]["planned_endpoint_counts"].values()) == 12


def test_stop_rules_and_capacity_interpretation_are_explicit() -> None:
    thresholds = Thresholds(max_error_rate=0.02, max_p95_ms=5000, max_p99_ms=10000)
    passed = {
        "concurrency": 20,
        "error_rate": 0.0,
        "requests_per_second": 40.0,
        "latency_ms": {"p95": 200, "p99": 350},
        "status_codes": {"200": 120},
    }
    failed = {
        "concurrency": 40,
        "error_rate": 0.03,
        "requests_per_second": 41.0,
        "latency_ms": {"p95": 6100, "p99": 9000},
        "status_codes": {"200": 116, "500": 4},
    }
    assert stop_reasons(passed, thresholds) == []
    assert stop_reasons(failed, thresholds) == ["error_rate", "p95_latency", "server_5xx"]
    capacity = capacity_interpretation([passed, failed], thresholds)
    assert capacity["accepted_max_concurrency"] == 20
    assert capacity["first_failed_concurrency"] == 40
    assert capacity["human_user_capacity"] is None
    assert capacity["interpretation_boundary"]["conversion_performed"] is False
    assert capacity["interpretation_boundary"]["virtual_users_are_human_users"] is False


def test_endpoint_budget_catches_fast_path_regression_hidden_by_mixed_summary() -> None:
    summary = {
        "error_rate": 0.0,
        "latency_ms": {"p95": 900.0, "p99": 1200.0},
        "by_endpoint": {
            "events_list": {
                "error_rate": 0.0,
                "p95_ms": 1800.0,
                "p99_ms": 2500.0,
                "status_codes": {"200": 10},
            },
            "industry_benchmark": {
                "error_rate": 0.0,
                "p95_ms": 4500.0,
                "p99_ms": 8000.0,
                "status_codes": {"200": 10},
            },
        },
    }
    reasons = endpoint_stop_reasons(
        summary,
        {
            "events_list": Thresholds(0.02, 1500.0, 3000.0),
            "industry_benchmark": Thresholds(0.02, 5000.0, 10000.0),
        },
    )
    assert reasons == ["endpoint:events_list:p95_latency"]


def test_report_writer_never_persists_token(tmp_path: Path) -> None:
    token = "eyJvery-secret-token-value.abc.def"
    report = {
        "authorization": f"Bearer {token}",
        "auth": {"token": token, "token_emitted": False},
        "profiles": [],
    }
    clean = redact_secrets(report)
    assert not report_contains_secret(clean, token)
    path = write_report(report, tmp_path / "report.json", token=token)
    encoded = path.read_text(encoding="utf-8")
    assert token not in encoded
    assert "Bearer" not in encoded
    assert json.loads(encoded)["report_sha256"]
    assert path.stat().st_mode & 0o077 == 0
    with pytest.raises(FileExistsError):
        write_report(report, path, token=token)


def test_resource_telemetry_samples_before_during_and_after_stage() -> None:
    calls = 0

    def fake_snapshot(_ports):
        nonlocal calls
        calls += 1
        return {
            "load_average_1m_5m_15m": [float(calls), 0.5, 0.25],
            "listeners": {"8102": [{"pid": 321, "command": "gunicorn"}]},
            "processes": [
                {
                    "pid": 321,
                    "cpu_percent": float(calls),
                    "memory_percent": 1.0,
                    "rss_kib": 1000 + calls,
                }
            ],
            "process_metrics_unavailable_reason": None,
        }

    async def operation() -> str:
        await asyncio.sleep(1.02)
        return "done"

    result, telemetry = asyncio.run(
        run_with_resource_telemetry(
            operation(),
            ports=(8102,),
            sample_interval_seconds=1.0,
            snapshotter=fake_snapshot,
        )
    )
    assert result == "done"
    assert telemetry["summary"]["sample_count"] >= 3
    assert telemetry["summary"]["process_metrics_available"] is True
    assert telemetry["summary"]["observed_listener_process_pids"] == [321]
    assert telemetry["summary"]["peak_combined_process_rss_kib"] >= 1003


def test_resource_telemetry_absence_is_explicit_not_fatal() -> None:
    samples = [
        {
            "snapshot": {
                "load_average_1m_5m_15m": [],
                "processes": [],
                "process_metrics_unavailable_reason": "ps unavailable",
            }
        }
    ]
    summary = summarize_resource_telemetry(samples)
    assert summary["process_metrics_available"] is False
    assert summary["peak_combined_process_rss_kib"] is None
    assert summary["unavailable_reasons"] == ["ps unavailable"]


def test_closed_loop_soak_is_bounded_and_labels_virtual_users() -> None:
    async def fake_request(_session, endpoint, **_kwargs):
        await asyncio.sleep(0.001)
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

    result = asyncio.run(
        run_soak(
            None,
            endpoints_for_profile("light_db"),
            virtual_users=3,
            duration_seconds=0.04,
            max_requests=60,
            think_time_ms=0,
            window_seconds=0.01,
            thresholds=Thresholds(0.02, 5000, 10000),
            frontend_base="http://127.0.0.1:5173",
            backend_base="http://127.0.0.1:8102",
            token=None,
            max_response_bytes=1024,
            seed=17,
            request_fn=fake_request,
        )
    )
    assert 0 < result["issued_requests"] <= 60
    assert result["total_requests"] == result["issued_requests"]
    assert result["termination_reason"] in {"duration_elapsed", "max_requests"}
    assert result["load_model"]["virtual_users"] == 3
    assert result["load_model"]["maximum_in_flight_requests"] == 3
    assert result["load_model"]["generator_processes"] == 1
    assert result["load_model"]["server_worker_processes"].startswith("observed only")
    assert result["threshold_pass"] is True
    assert result["windows"]


def test_staff_journey_soak_records_role_path_but_never_claims_humans() -> None:
    samples: list[dict] = []

    async def fake_request(_session, endpoint, **_kwargs):
        await asyncio.sleep(0)
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

    result = asyncio.run(
        run_soak(
            None,
            endpoints_for_profile("mixed"),
            virtual_users=3,
            duration_seconds=0.2,
            max_requests=12,
            think_time_ms=999,
            window_seconds=1.0,
            thresholds=Thresholds(0.02, 5000, 10000),
            frontend_base="http://127.0.0.1:5173",
            backend_base="http://127.0.0.1:8102",
            token=None,
            max_response_bytes=1024,
            seed=43,
            request_fn=fake_request,
            sample_sink=samples.append,
            journey_profile=STAFF_READONLY_JOURNEY_V1,
            journey_pacing_scale=0.0,
        )
    )
    assert result["issued_requests"] == 12
    assert result["load_model"]["simulated_active_sessions"] == 3
    assert result["load_model"]["human_users"] is None
    assert result["load_model"]["journey"]["production_trace_calibrated"] is False
    assert result["workload"]["journey_role_request_counts"]
    assert all(sample.get("journey_role") for sample in samples)
    assert all(sample.get("journey_profile") == "staff-readonly-v1" for sample in samples)


def test_soak_stops_early_on_window_threshold() -> None:
    async def failing_request(_session, endpoint, **_kwargs):
        await asyncio.sleep(0.001)
        return {
            "endpoint": endpoint.name,
            "category": endpoint.category,
            "status": 500,
            "ok": False,
            "latency_ms": 2.0,
            "bytes": 10,
            "error_type": "",
            "error_detail": "",
        }

    result = asyncio.run(
        run_soak(
            None,
            endpoints_for_profile("health"),
            virtual_users=2,
            duration_seconds=1.0,
            max_requests=500,
            think_time_ms=0,
            window_seconds=0.01,
            thresholds=Thresholds(0.02, 5000, 10000),
            frontend_base="http://127.0.0.1:5173",
            backend_base="http://127.0.0.1:8102",
            token=None,
            max_response_bytes=1024,
            seed=19,
            request_fn=failing_request,
        )
    )
    assert result["termination_reason"] == "threshold"
    assert result["issued_requests"] < 500
    assert result["threshold_pass"] is False
    assert set(result["stop_reasons"]) == {"error_rate", "server_5xx"}


def test_soak_cli_bounds_and_profile_selection_are_validated() -> None:
    parser = build_parser()
    valid = parser.parse_args(
        [
            "--profiles",
            "health,mixed",
            "--soak-seconds",
            "30",
            "--soak-profile",
            "mixed",
            "--soak-virtual-users",
            "5",
        ]
    )
    validate_execution_args(valid)

    too_long = parser.parse_args(["--soak-seconds", "3601"])
    with pytest.raises(ValueError, match="soak-seconds"):
        validate_execution_args(too_long)

    missing_profile = parser.parse_args(
        ["--profiles", "health", "--soak-seconds", "30", "--soak-profile", "mixed"]
    )
    with pytest.raises(ValueError, match="soak-profile"):
        validate_execution_args(missing_profile)

    journey = parser.parse_args(
        [
            "--mode",
            "closed-loop-tiers",
            "--profiles",
            "mixed",
            "--soak-profile",
            "mixed",
            "--tiers",
            "1:10,5:10",
            "--session-count",
            "5",
            "--journey-profile",
            "staff-readonly-v1",
        ]
    )
    validate_execution_args(journey)
    journey.session_count = 4
    with pytest.raises(ValueError, match="independent cookie/connection"):
        validate_execution_args(journey)

    wrong_mode = parser.parse_args(["--journey-profile", "staff-readonly-v1"])
    with pytest.raises(ValueError, match="closed-loop-tiers"):
        validate_execution_args(wrong_mode)


def test_default_cli_plan_is_zero_network_and_never_reads_tokens(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("VKPI_LOAD_TEST_TOKEN", "must-not-be-read-during-plan")
    args = build_parser().parse_args(["--profiles", "health", "--phases", "1", "--trials", "1"])
    validate_execution_args(args)
    report = build_dry_run_report(args)
    assert args.execute_live is False
    assert report["live_run"] is False
    assert report["network_requests_issued"] == 0
    assert report["configuration"]["tokens_read_during_dry_run"] is False
    assert report["configuration"]["load_model"]["human_user_conversion"] is False


def test_runtime_contains_only_public_key_verification_surface() -> None:
    scripts_root = Path(__file__).parents[1] / "scripts"
    source = "\n".join(
        path.read_text(encoding="utf-8")
        for path in (
            scripts_root / "load_test_vkpi_readonly.py",
            *sorted((scripts_root / "ops").glob("load_test_*.py")),
        )
    )
    assert "Ed25519PublicKey" in source
    assert "Ed25519PrivateKey" not in source
    assert "--private-key" not in source
    assert "TRUSTED_CALIBRATION_ED25519_PUBLIC_KEYS" in source
    assert "TRUSTED_TELEMETRY_ED25519_PUBLIC_KEYS" in source


def test_tier_parser_keeps_vu_duration_explicit_and_rejects_duplicates() -> None:
    tiers = parse_vu_duration_tiers("1:10,5:30,20:60")
    assert [(tier.virtual_users, tier.duration_seconds) for tier in tiers] == [
        (1, 10.0),
        (5, 30.0),
        (20, 60.0),
    ]
    assert all(tier.public_dict()["human_users"] is None for tier in tiers)
    with pytest.raises(ValueError, match="unique and strictly increasing"):
        parse_vu_duration_tiers("5:10,5:20")


def test_token_pool_accepts_env_and_owner_only_json_file_without_metadata_values(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    token_file = tmp_path / "tokens.json"
    token_file.write_text(json.dumps(["file-token-a", "file-token-b"]), encoding="utf-8")
    token_file.chmod(0o600)
    monkeypatch.setenv("VKPI_LOAD_TEST_TOKENS_JSON", json.dumps(["env-token-a"]))
    monkeypatch.delenv("VKPI_LOAD_TEST_TOKEN", raising=False)
    tokens, metadata = resolve_token_pool(token_file)
    assert tokens == ("env-token-a", "file-token-a", "file-token-b")
    encoded = json.dumps(metadata)
    assert "env-token-a" not in encoded
    assert "file-token-a" not in encoded
    assert metadata["token_count"] == 3
    assert metadata["implicit_login_or_database_lookup"] is False

    token_file.chmod(0o644)
    with pytest.raises(ValueError, match="permissions"):
        resolve_token_pool(token_file)


def test_raw_ndjson_is_owner_only_whitelisted_and_hashed(tmp_path: Path) -> None:
    path = tmp_path / "samples.ndjson"
    secret = "very-secret-token-value"
    writer = RawSampleWriter(path)
    writer.write(
        {
            "profile": "health",
            "endpoint": "backend_health",
            "status": 200,
            "ok": True,
            "latency_ms": 3.25,
            "authorization": f"Bearer {secret}",
            "error_detail": f"token={secret}",
        }
    )
    metadata = writer.close()
    encoded = path.read_text(encoding="utf-8")
    assert secret not in encoded
    assert "authorization" not in encoded
    assert metadata["sample_count"] == 1
    assert metadata["sha256"]
    assert path.stat().st_mode & 0o077 == 0


def test_trial_aggregate_exposes_repeatability_and_endpoint_status() -> None:
    trials = [
        {
            "total_requests": 10,
            "requests_per_second": rps,
            "error_rate": error,
            "latency_ms": {"p50": p50, "p95": p95, "p99": p99},
            "status_codes": statuses,
            "error_types": {},
            "by_endpoint": {
                "backend_health": {
                    "requests": 10,
                    "error_rate": error,
                    "p50_ms": p50,
                    "p95_ms": p95,
                    "p99_ms": p99,
                    "status_codes": statuses,
                    "error_types": {},
                }
            },
        }
        for rps, error, p50, p95, p99, statuses in (
            (100.0, 0.0, 10.0, 20.0, 30.0, {"200": 10}),
            (90.0, 0.1, 12.0, 25.0, 40.0, {"200": 9, "500": 1}),
            (110.0, 0.0, 8.0, 18.0, 28.0, {"200": 10}),
        )
    ]
    result = aggregate_trial_summaries(
        trials,
        Thresholds(0.05, 1000, 2000),
        concurrency=5,
        load_model="closed_loop_virtual_users",
    )
    assert result["across_trials"]["requests_per_second"] == {
        "min": 90.0,
        "median": 100.0,
        "max": 110.0,
    }
    assert result["threshold_pass"] is False
    endpoint = result["across_trials"]["by_endpoint"]["backend_health"]
    assert endpoint["status_codes"] == {"200": 29, "500": 1}


def test_optional_telemetry_adapter_requires_strict_fresh_run_bound_contract(
    tmp_path: Path,
) -> None:
    assert optional_json_telemetry_adapter("db_pool", None) == {
        "available": False,
        "value": None,
        "reason": "not_configured",
    }
    path = tmp_path / "db-pool.json"
    nonce = "round7-test-nonce-0001"
    path.write_text(
        json.dumps(_telemetry_payload("db_pool", port=54329, nonce=nonce)),
        encoding="utf-8",
    )
    path.chmod(0o600)
    result = optional_json_telemetry_adapter(
        "db_pool",
        path,
        expected_port=54329,
        run_nonce=nonce,
    )
    assert result["available"] is True
    assert result["value"]["active"] == 4
    assert result["schema_version"] == TELEMETRY_SIDECAR_SCHEMA
    assert result["external_input_trust"] == "untrusted_external_input"
    assert result["producer_attestation"]["trusted"] is False
    assert nonce not in json.dumps(result)
    path.chmod(0o644)
    assert optional_json_telemetry_adapter(
        "db_pool", path, expected_port=54329, run_nonce=nonce
    )["available"] is False


def test_offline_fixture_cli_writes_auditable_samples_without_network(tmp_path: Path) -> None:
    fixture = tmp_path / "fixture.json"
    fixture.write_text(
        json.dumps(
            {
                "responses": {
                    "backend_health": [
                        {"status": 200, "latency_ms": 3.0, "bytes": 20},
                        {"status": 200, "latency_ms": 5.0, "bytes": 20},
                    ]
                }
            }
        ),
        encoding="utf-8",
    )
    output = tmp_path / "report.json"
    raw = tmp_path / "samples.ndjson"
    assert (
        main(
            [
                "--fixture",
                str(fixture),
                "--profiles",
                "health",
                "--phases",
                "1",
                "--trials",
                "2",
                "--requests-per-phase",
                "20",
                "--cooldown-seconds",
                "0",
                "--output",
                str(output),
                "--raw-output",
                str(raw),
            ]
        )
        == 0
    )
    report = json.loads(output.read_text(encoding="utf-8"))
    assert report["live_run"] is False
    assert report["network_requests_issued"] == 0
    assert report["fixture_requests_simulated"] == 41
    assert report["overall_capacity"] is None  # no mixed profile was selected
    assert report["profiles"][0]["capacity"]["status"] == "not_evaluated_synthetic_fixture"
    assert report["profiles"][0]["capacity"]["performance_evidence"] is False
    assert report["profiles"][0]["stages"][0]["trial_count"] == 2
    assert report["raw_evidence"]["sample_count"] == 41
    assert len(raw.read_text(encoding="utf-8").splitlines()) == 41
