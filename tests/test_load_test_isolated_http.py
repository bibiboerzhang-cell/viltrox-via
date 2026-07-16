from __future__ import annotations

import hashlib
import json
import socket
from dataclasses import FrozenInstanceError
from pathlib import Path

import pytest

from scripts.ops import load_test_isolated_http as isolated_http


def tiny_config(**changes: object) -> isolated_http.HttpFixtureConfig:
    values: dict[str, object] = {
        "tiers": (1, 2),
        "trials": 1,
        "duration_seconds": 0.12,
        "seed": 20260714,
        "database_slots": 2,
        "aggregate_slots": 1,
        "shell_service_ms": 0.5,
        "read_service_ms": 2.0,
        "aggregate_service_ms": 4.0,
        "request_timeout_ms": 500.0,
        "contention_threshold_ms": 0.1,
        **changes,
    }
    return isolated_http.HttpFixtureConfig(**values).validated()


def write_historical_chain_fixture(path: Path) -> Path:
    unavailable = {
        "status": "unavailable",
        "rate": None,
        "numerator": None,
        "denominator": 0,
        "sample_count": 42,
        "coverage": 0.0,
    }

    def profile(name: str, cap: int | None, makespans: list[float]) -> dict[str, object]:
        return {
            "definition": {"resource_cap": cap, "claim": name},
            "scenarios": [
                {
                    "lanes": lane,
                    "makespan_minutes": minutes,
                    "throughput": {"attempts_per_hour": round(2520 / minutes, 3)},
                    "observed_attempt_quality": {
                        "error_rate": unavailable,
                        "conflict_rate": unavailable,
                    },
                }
                for lane, minutes in zip((1, 2, 4, 8), makespans)
            ],
            "calculation_sha256": (name * 64)[:64],
        }

    payload = {
        "source_sample": {
            "sample_count": 42,
            "observation_date_local": "2026-07-13",
            "scope": "one 20-survivor discovery chain; identifiers removed",
            "full_chain_wall_seconds": 6386.45,
            "sampled_service_seconds": 6161.539,
            "sampled_to_wall_coverage": 0.964783,
        },
        "capacity_evidence": {
            "profiles": {
                "current_guarded_cap_1": profile(
                    "cap1", 1, [102.692, 99.471, 94.916, 89.415]
                ),
                "two_slot_candidate_cap_2": profile(
                    "cap2", 2, [102.692, 51.585, 48.417, 46.410]
                ),
                "ideal_unbounded_lower_bound": profile(
                    "ideal", None, [102.692, 51.585, 26.047, 14.049]
                ),
            }
        },
    }
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def test_default_tiers_are_bounded_and_config_is_frozen() -> None:
    config = isolated_http.HttpFixtureConfig().validated()
    assert config.tiers == (1, 2, 4, 8, 16, 32)
    assert config.trials == 3
    with pytest.raises(FrozenInstanceError):
        config.trials = 99  # type: ignore[misc]


@pytest.mark.parametrize(
    ("changes", "message"),
    [
        ({"tiers": (1, 33)}, "tiers"),
        ({"tiers": (2, 2)}, "tiers"),
        ({"trials": 6}, "trials"),
        ({"duration_seconds": 5.1}, "duration_seconds"),
        ({"database_slots": 0}, "database_slots"),
        ({"aggregate_slots": 33}, "aggregate_slots"),
        ({"request_timeout_ms": 49.0}, "request_timeout_ms"),
    ],
)
def test_config_hard_bounds_fail_before_execution(
    changes: dict[str, object], message: str
) -> None:
    with pytest.raises(ValueError, match=message):
        tiny_config(**changes)


def test_protected_live_ports_are_explicitly_owned_by_code() -> None:
    assert isolated_http.PROTECTED_LOCAL_PORTS == {5173, 6379, 8102, 54329}


def test_parser_has_no_target_argument_and_rejects_target_input() -> None:
    parser = isolated_http.build_parser()
    destinations = {action.dest for action in parser._actions}
    assert "target" not in destinations
    assert "base_url" not in destinations
    with pytest.raises(SystemExit):
        parser.parse_args(["--target", "http://127.0.0.1:8102"])


def test_small_run_has_recomputable_denominators_and_no_external_io(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def forbidden(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("in-process ASGI harness attempted socket I/O")

    monkeypatch.setattr(socket, "create_connection", forbidden)
    monkeypatch.setattr(socket.socket, "connect", forbidden)
    monkeypatch.setattr(socket, "getaddrinfo", forbidden)
    report = isolated_http.build_report(tiny_config())

    assert report["evidence_class"] == "isolated_in_process_asgi_http_fixture"
    assert report["status"] == "completed_isolated_http_fixture"
    assert report["claims"]["fixture_capacity_evidence"] is True
    assert report["claims"]["production_performance_evidence"] is False
    assert report["claims"]["real_user_capacity_evidence"] is False
    assert report["claims"]["maximum_real_users"] is None
    assert report["analysis"]["production_capacity"] is None
    assert report["safety"]["protected_ports_contacted"] == 0
    assert report["safety"]["non_loopback_connections"] == 0
    assert report["safety"]["socket_connections"] == 0
    assert report["safety"]["dns_lookups"] == 0
    assert report["safety"]["database_connections"] == 0
    assert report["safety"]["redis_connections"] == 0
    assert report["safety"]["business_rows_written"] == 0
    helper_path = Path(isolated_http.http_reporting.__file__)
    assert report["generator"]["reporting_helper_sha256"] == hashlib.sha256(
        helper_path.read_bytes()
    ).hexdigest()
    app_path = Path(isolated_http.http_app.__file__)
    assert report["generator"]["app_helper_sha256"] == hashlib.sha256(
        app_path.read_bytes()
    ).hexdigest()

    for tier in report["tier_results"]:
        assert tier["trial_count"] == 1
        trial = tier["trials"][0]
        measurements = trial["measurements"]
        assert measurements
        assert trial["measurements_sha256"] == isolated_http.sha256_json(
            measurements
        )
        assert trial["transport_safety"]["protected_ports_contacted"] == 0
        assert trial["transport_safety"]["non_loopback_connections"] == 0
        assert trial["transport_safety"]["socket_connections"] == 0
        assert trial["identities"]["unique_synthetic_identities"] == tier["tier_vu"]
        assert trial["identities"]["collision_count"] == 0

        denominator = tier["denominators"]
        rates = tier["rates"]
        assert denominator["offered_requests"] == len(measurements)
        assert denominator["completed_outcomes"] == len(measurements)
        assert denominator["latency_samples"] == len(measurements)
        assert rates["completion_rate"] == {
            "numerator": len(measurements),
            "denominator": len(measurements),
            "value": 1.0,
        }
        assert rates["error_rate"]["denominator"] == len(measurements)
        assert rates["timeout_rate"]["denominator"] == len(measurements)
        assert tier["latency_ms_all_outcomes"]["sample_count"] == len(
            measurements
        )
        assert tier["latency_ms_all_outcomes"]["p50"] <= tier[
            "latency_ms_all_outcomes"
        ]["p95"]
        assert tier["latency_ms_all_outcomes"]["p95"] <= tier[
            "latency_ms_all_outcomes"
        ]["p99"]


def test_historical_chain_is_separate_and_keeps_unknown_rates_null(
    tmp_path: Path,
) -> None:
    evidence = write_historical_chain_fixture(tmp_path / "round12.json")
    report = isolated_http.build_report(
        tiny_config(
            aggregate_slots=2,
            aggregate_service_ms=30.0,
            duration_seconds=0.18,
        ),
        historical_chain_evidence=evidence,
    )
    history = report["historical_20_survivor_chain"]
    assert history["task_count"] == 42
    assert history["full_chain_wall_seconds"] == 6386.45
    assert history["claim_boundary"]["cap_2_runtime_verified"] is False
    assert history["claim_boundary"]["error_rate"] is None
    assert history["claim_boundary"]["conflict_rate"] is None
    candidate = history["profiles"]["two_slot_candidate_cap_2"]["scenarios"][1]
    assert candidate["lanes"] == 2
    assert candidate["makespan_minutes"] == 51.585
    assert candidate["error_rate"]["rate"] is None
    assert candidate["error_rate"]["denominator"] == 0
    fixture = report["two_slot_fixture_mechanics"]
    assert fixture["status"] == "verified_in_process_fixture_only"
    assert fixture["aggregate_max_active"] == 2
    assert fixture["real_worker_lane_validation"] is False
    assert fixture["idempotency_validation"] is False
    markdown = isolated_http.render_markdown(report)
    assert "HTTP fixture 虚拟用户" in markdown
    assert "真实 20-survivor 抓取链" in markdown
    assert "51.585 分钟" in markdown
    assert "14.049 分钟" in markdown
    assert "幂等和真实 conflict rate 仍未验证" in markdown


def test_summary_counts_timeouts_and_errors_against_offered_denominator() -> None:
    records = [
        isolated_http.ClientMeasurement(0, 0, "/fixture/read", 0, 1, 1, 200, "success", 10),
        isolated_http.ClientMeasurement(0, 1, "/fixture/read", 1, 3, 2, 500, "http_error", 10),
        isolated_http.ClientMeasurement(0, 2, "/fixture/read", 3, 8, 5, 0, "timeout", 0),
        isolated_http.ClientMeasurement(0, 3, "/fixture/read", 8, 9, 1, 0, "client_error", 0),
    ]
    result = isolated_http.summarize_measurements(
        records, offer_window_seconds=1.0, completion_window_seconds=1.1
    )
    assert result["completion_rate"] == {
        "numerator": 4,
        "denominator": 4,
        "value": 1.0,
    }
    assert result["error_rate"] == {
        "numerator": 3,
        "denominator": 4,
        "value": 0.75,
    }
    assert result["timeout_rate"] == {
        "numerator": 1,
        "denominator": 4,
        "value": 0.25,
    }
    assert result["offered_throughput"]["requests_per_second"] == 4.0
    assert result["completed_throughput"]["requests_per_second"] == 3.636


def test_saturation_rule_is_explicit_and_does_not_claim_real_capacity() -> None:
    def tier(vu: int, rps: float, p95: float, wait: float) -> dict[str, object]:
        return {
            "tier_vu": vu,
            "throughput": {"completed": {"requests_per_second": rps}},
            "latency_ms_all_outcomes": {"p95": p95},
            "resource_saturation": {
                "aggregate": {"p95_ms_max_across_trials": wait}
            },
        }

    result = isolated_http.analyze_saturation(
        [tier(1, 10, 10, 0), tier(2, 19, 11, 0), tier(4, 21, 30, 20)]
    )
    assert result["fixture_saturation_breakpoint_vu"] == 4
    assert result["highest_pre_saturation_fixture_vu"] == 2
    assert result["production_capacity"] is None
    assert result["maximum_real_users"] is None


def test_markdown_repeats_fixture_only_caveat_and_json_round_trips() -> None:
    report = isolated_http.build_report(tiny_config(tiers=(1,)))
    markdown = isolated_http.render_markdown(report)
    assert "不能证明 V-KPI、生产、云端或真实用户容量" in markdown
    assert "production_capacity" in markdown
    assert "5173、8102、54329、6379" in markdown
    assert json.loads(json.dumps(report, allow_nan=False))["run_id"] == report["run_id"]


def test_write_exclusive_refuses_overwrite(tmp_path: Path) -> None:
    output = tmp_path / "report.json"
    isolated_http.write_exclusive(output, b"{}\n")
    assert output.read_bytes() == b"{}\n"
    with pytest.raises(FileExistsError):
        isolated_http.write_exclusive(output, b"changed\n")


def test_percentile_empty_population_is_not_false_zero() -> None:
    assert isolated_http.percentile([], 95) is None
    assert isolated_http.rounded_percentiles([]) == {
        "p50": None,
        "p95": None,
        "p99": None,
        "max": None,
    }


def test_round14_modules_stay_below_canonical_line_guard() -> None:
    paths = (
        Path(isolated_http.__file__),
        Path(isolated_http.http_app.__file__),
        Path(isolated_http.http_reporting.__file__),
        Path(__file__),
    )
    for path in paths:
        assert len(path.read_text(encoding="utf-8").splitlines()) <= 800, path
