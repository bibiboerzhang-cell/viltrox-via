import hashlib
import importlib.util
import json
import os
from pathlib import Path
import stat

import pytest


_SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "benchmark_kol_online_mock.py"
_SPEC = importlib.util.spec_from_file_location("vkpi_benchmark_kol_online_mock", _SCRIPT)
assert _SPEC is not None and _SPEC.loader is not None
benchmark = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(benchmark)


def _scenarios():
    return {row.scenario_id: row for row in benchmark.default_scenarios()}


def _run(scenario_id: str):
    return benchmark.simulate_scenario(_scenarios()[scenario_id])


def _walk_keys(value):
    if isinstance(value, dict):
        for key, child in value.items():
            yield str(key).lower()
            yield from _walk_keys(child)
    elif isinstance(value, list):
        for child in value:
            yield from _walk_keys(child)


def _walk_strings(value):
    if isinstance(value, dict):
        for child in value.values():
            yield from _walk_strings(child)
    elif isinstance(value, list):
        for child in value:
            yield from _walk_strings(child)
    elif isinstance(value, str):
        yield value


def test_matrix_covers_required_online_contract_scenarios():
    scenarios = _scenarios()
    assert set(scenarios) == {
        "happy_path_refill",
        "duplicate_heavy_refill",
        "one_platform_degraded_complete",
        "one_platform_degraded_shortfall",
        "provider_call_budget_exhausted",
        "candidate_budget_exhausted",
        "candidate_exhausted",
        "strict_eight_gate_funnel",
    }
    assert all(set(row.batches) == set(benchmark.PLATFORMS) for row in scenarios.values())
    assert all(len(row.local_keys) == 30 for row in scenarios.values())


def test_happy_path_refills_to_net_new_30_and_unique_60():
    result = _run("happy_path_refill")
    assert result["status"] == "complete"
    assert result["shortfall_reason"] is None
    assert result["counts"]["online_net_new"] == 30
    assert result["counts"]["online_shortfall"] == 0
    assert result["counts"]["unique60"] == 60
    assert result["counts"]["duplicate_local"] > 0
    assert result["counts"]["duplicate_online"] > 0
    assert result["provider"]["peak_in_flight"] == 3
    assert result["timings_ms"]["ttfq"] is not None
    assert result["timings_ms"]["ttfq"] <= result["timings_ms"]["t10_online"]
    assert result["timings_ms"]["t10_online"] <= result["timings_ms"]["t30_online"]
    assert result["scenario_pass"] is True


def test_duplicate_heavy_path_does_not_count_duplicates_toward_online_quota():
    result = _run("duplicate_heavy_refill")
    assert result["status"] == "complete"
    assert result["counts"]["online_net_new"] == 30
    assert result["counts"]["unique60"] == 60
    assert result["counts"]["duplicate_online"] >= 20
    assert result["provider"]["mock_calls_started"] > 3
    assert result["funnel"]["accepted_online"] == 30
    assert result["funnel"]["canonical_unique_online"] >= 30


def test_one_platform_can_degrade_while_remaining_platforms_fill_30():
    result = _run("one_platform_degraded_complete")
    assert result["status"] == "complete"
    assert result["counts"]["online_net_new"] == 30
    assert result["counts"]["unique60"] == 60
    assert result["degradation_reasons"] == ["instagram:mock_provider_failure"]
    assert result["provider"]["mock_calls_failed"] == 1
    assert result["provider"]["failure_rate"] == 0.2
    assert result["provider"]["platforms"]["instagram"]["terminal_error"] is True


def test_platform_degradation_with_insufficient_supply_is_honest_shortfall():
    result = _run("one_platform_degraded_shortfall")
    assert result["status"] == "shortfall"
    assert result["shortfall_reason"] == "candidate_exhausted"
    assert result["counts"] == {
        "local_unique": 30,
        "online_net_new": 18,
        "online_shortfall": 12,
        "unique60": 48,
        "strict_qualified_before_target_cap": 18,
        "duplicate_local": 0,
        "duplicate_online": 0,
    }
    assert result["timings_ms"]["t30_online"] is None
    assert result["degradation_reasons"] == ["instagram:mock_provider_failure"]


def test_provider_call_budget_exhaustion_never_exceeds_cap():
    result = _run("provider_call_budget_exhausted")
    assert result["status"] == "shortfall"
    assert result["shortfall_reason"] == "budget_exhausted"
    assert result["counts"]["online_net_new"] == 20
    assert result["provider"]["mock_calls_started"] == result["limits"]["max_provider_calls"] == 5
    assert result["limits"]["provider_call_budget_blocked"] is True
    assert result["limits"]["provider_call_limit_violations"] == 0


def test_candidate_budget_exhaustion_never_admits_more_than_cap():
    result = _run("candidate_budget_exhausted")
    assert result["status"] == "shortfall"
    assert result["shortfall_reason"] == "budget_exhausted"
    assert result["counts"]["online_net_new"] == 20
    assert result["funnel"]["provider_candidates_admitted"] == 20
    assert result["limits"]["max_candidates_admitted"] == 20
    assert result["limits"]["candidate_budget_blocked"] is True
    assert result["limits"]["candidate_limit_violations"] == 0


def test_normal_candidate_exhaustion_is_not_mislabelled_as_budget_failure():
    result = _run("candidate_exhausted")
    assert result["status"] == "shortfall"
    assert result["shortfall_reason"] == "candidate_exhausted"
    assert result["counts"]["online_net_new"] == 15
    assert result["counts"]["unique60"] == 45
    assert result["limits"]["provider_call_budget_blocked"] is False
    assert result["limits"]["candidate_budget_blocked"] is False


def test_all_eight_strict_gates_have_independent_rejection_and_monotonic_funnel():
    result = _run("strict_eight_gate_funnel")
    assert result["rejected_by_gate"] == {gate: 1 for gate in benchmark.STRICT_GATES}
    assert result["verification"]["strict_gate_names"] == list(benchmark.STRICT_GATES)
    assert result["verification"]["canonical_unique_verified"] == 38
    assert result["funnel_monotonic"] is True
    funnel = result["funnel"]
    ordered = [
        funnel["provider_candidates_admitted"],
        funnel["not_local_duplicate"],
        funnel["canonical_unique_online"],
        *[funnel[benchmark.FUNNEL_GATE_FIELDS[gate]] for gate in benchmark.STRICT_GATES],
        funnel["strict_qualified"],
        funnel["accepted_online"],
    ]
    assert all(left >= right for left, right in zip(ordered, ordered[1:]))


def test_discrete_event_outcome_is_deterministic_but_wall_clock_is_separate():
    scenario = _scenarios()["happy_path_refill"]
    first = benchmark.simulate_scenario(scenario)
    second = benchmark.simulate_scenario(scenario)
    assert benchmark._deterministic_projection(first) == benchmark._deterministic_projection(second)
    summary = benchmark.summarize_scenario(scenario, [first, second])
    assert summary["deterministic_outcome_stable"] is True
    assert summary["timings_ms"]["ttfq"] == {
        "n": 2,
        "min": 74.0,
        "p50": 74.0,
        "p95": 74.0,
        "max": 74.0,
    }
    assert summary["timings_ms"]["harness_wall_clock"]["n"] == 2


def test_full_report_separates_synthetic_semantics_from_real_world_claims():
    report = benchmark.run_benchmark(runs_per_scenario=2)
    assert report["claim_status"] == "synthetic_mock_only"
    assert report["scope"]["real_provider_calls"] == 0
    assert report["scope"]["external_network_accessed"] is False
    assert report["scope"]["business_database_accessed"] is False
    assert report["scope"]["business_database_writes"] == 0
    assert report["scope"]["production_backend_adapter_tested"] is False
    assert report["scope"]["real_world_supply_tested"] is False
    assert report["scope"]["human_precision_at_30"] == "not_evaluated_synthetic_fixture"
    assert report["aggregate"]["scenario_pass_count"] == 8
    assert report["aggregate"]["scenario_count"] == 8
    assert report["aggregate"]["net_new_30_scenario_count"] == 4
    assert report["aggregate"]["unique60_scenario_count"] == 4
    assert report["aggregate"]["provider_call_limit_violation_count"] == 0
    assert report["aggregate"]["candidate_limit_violation_count"] == 0
    assert report["aggregate"]["timings_ms"]["t30_online_complete_only"]["n"] == 8
    assert report["source_state"]["benchmark_script_sha256"] == hashlib.sha256(
        _SCRIPT.read_bytes()
    ).hexdigest()


def test_aggregate_weighted_provider_failure_rate_uses_calls_as_denominator():
    report = benchmark.run_benchmark(runs_per_scenario=1)
    calls = report["aggregate"]["total_mock_provider_calls_across_runs"]
    failures = report["aggregate"]["total_mock_provider_failures_across_runs"]
    assert calls == 39
    assert failures == 2
    assert report["aggregate"]["weighted_mock_provider_failure_rate"] == round(failures / calls, 4)


def test_report_contains_no_creator_or_contact_identifiers():
    report = benchmark.run_benchmark(runs_per_scenario=1)
    assert not (set(_walk_keys(report)) & benchmark.FORBIDDEN_REPORT_KEYS)
    for value in _walk_strings(report):
        assert benchmark.EMAIL_RE.search(value) is None
        assert benchmark.CONTACT_ROUTE_RE.search(value) is None
        assert "local:001" not in value
        assert "happy:001" not in value
    benchmark._assert_private_report(report)


def test_report_digest_is_recomputable():
    report = benchmark.run_benchmark(runs_per_scenario=1)
    expected = report.pop("report_sha256_without_digest")
    actual = hashlib.sha256(
        json.dumps(report, ensure_ascii=False, sort_keys=True).encode("utf-8")
    ).hexdigest()
    assert actual == expected


def test_write_report_is_atomic_private_and_json_valid(tmp_path: Path):
    report = benchmark.run_benchmark(runs_per_scenario=1)
    output = tmp_path / "online-mock.json"
    benchmark.write_report(output, report)
    assert stat.S_IMODE(output.stat().st_mode) == 0o600
    assert json.loads(output.read_text(encoding="utf-8"))["schema_version"] == benchmark.SCHEMA_VERSION
    assert not list(tmp_path.glob(".online-mock.json.*"))


def test_output_symlink_is_rejected_before_write(tmp_path: Path):
    target = tmp_path / "target.json"
    target.write_text("{}", encoding="utf-8")
    link = tmp_path / "report.json"
    link.symlink_to(target)
    with pytest.raises(ValueError, match="output_symlink_forbidden"):
        benchmark.write_report(link, benchmark.run_benchmark(runs_per_scenario=1))
    assert target.read_text(encoding="utf-8") == "{}"


@pytest.mark.parametrize("runs", [0, 21])
def test_runs_per_scenario_is_bounded(runs: int):
    with pytest.raises(ValueError, match="runs_per_scenario_must_be_between_1_and_20"):
        benchmark.run_benchmark(runs_per_scenario=runs)


def test_percentiles_use_nearest_rank_and_ignore_unreached_t30():
    assert benchmark._percentile([1, 2, 3, 4, 5], 0.50) == 3.0
    assert benchmark._percentile([1, 2, 3, 4, 5], 0.95) == 5.0
    assert benchmark._stats([None, 10, None, 20]) == {
        "n": 2,
        "min": 10.0,
        "p50": 10.0,
        "p95": 20.0,
        "max": 20.0,
    }
