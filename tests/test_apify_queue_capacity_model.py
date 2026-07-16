from __future__ import annotations

import ast
import json
import socket
from pathlib import Path

import pytest

from scripts.ops import apify_queue_capacity_model as model


def jobs(count: int, duration: float = 10.0, resource: str = "apify") -> list[model.JobSample]:
    return [model.JobSample(duration, resource, "fixture") for _ in range(count)]


def test_parallel_tiers_are_deterministic_ideal_lower_bounds() -> None:
    report = model.build_report(jobs(8), lanes=(1, 2, 4, 8))
    assert [row["makespan_seconds"] for row in report["scenarios"]] == [80.0, 40.0, 20.0, 10.0]
    assert [row["speedup_vs_smallest_tier"] for row in report["scenarios"]] == [1.0, 2.0, 4.0, 8.0]
    assert [row["throughput"]["attempts_per_hour"] for row in report["scenarios"]] == [360.0, 720.0, 1440.0, 2880.0]
    assert report["input"]["duration_seconds"] == {"p50": 10.0, "p95": 10.0, "p99": 10.0, "max": 10.0}
    assert report["input"]["observed_attempt_quality"]["error_rate"]["rate"] is None
    assert report["input"]["observed_attempt_quality"]["conflict_rate"]["rate"] is None
    assert all(row["modeled_resource_gate"]["contention_rate"] == 0.0 for row in report["scenarios"])
    assert report["claims"]["production_load_test"] is False
    assert report["claims"]["maximum_real_users"] is None
    assert set(report["safety"].values()) == {0}
    assert len(report["reproducibility"]["calculation_sha256"]) == 64


def test_provider_cap_prevents_fake_linear_speedup() -> None:
    scenario = model.simulate_fcfs(jobs(8), lanes=8, provider_caps={"apify": 2})
    assert scenario["makespan_seconds"] == 40.0
    assert scenario["provider_caps"] == {"apify": 2}
    assert scenario["modeled_resource_gate"]["delayed_attempts"] == 6
    assert scenario["modeled_resource_gate"]["contention_rate"] == 0.75
    assert scenario["throughput"]["successful_completions_per_hour"] is None
    assert model.parse_provider_cap("apify=2") == ("apify", 2)
    with pytest.raises(Exception, match="provider cap"):
        model.parse_provider_cap("apify=0")


def test_mixed_resources_can_overlap_but_stay_bounded() -> None:
    samples = [
        model.JobSample(10, "apify", "crawl"),
        model.JobSample(10, "gemini", "analysis"),
        model.JobSample(10, "apify", "crawl"),
        model.JobSample(10, "gemini", "analysis"),
    ]
    scenario = model.simulate_fcfs(
        samples,
        lanes=4,
        provider_caps={"apify": 1, "gemini": 1},
    )
    assert scenario["makespan_seconds"] == 20.0


def test_explicit_error_and_conflict_rates_use_labelled_denominators() -> None:
    samples = [
        model.JobSample(10, "apify", "crawl", errored=False, conflicted=False),
        model.JobSample(10, "apify", "crawl", errored=True, conflicted=False),
        model.JobSample(10, "apify", "crawl", errored=False, conflicted=True),
        model.JobSample(10, "apify", "crawl", errored=False, conflicted=False),
    ]
    scenario = model.simulate_fcfs(samples, lanes=2)
    quality = scenario["observed_attempt_quality"]
    assert quality["error_rate"] == {
        "status": "complete",
        "rate": 0.25,
        "numerator": 1,
        "denominator": 4,
        "sample_count": 4,
        "coverage": 1.0,
    }
    assert quality["conflict_rate"]["rate"] == 0.25
    assert scenario["throughput"]["successful_completions_per_hour"] == 540.0


def test_partial_quality_labels_are_not_promoted_to_full_window_sla() -> None:
    samples = [
        model.JobSample(10, "apify", "crawl", errored=False, conflicted=None),
        model.JobSample(10, "apify", "crawl", errored=None, conflicted=None),
    ]
    scenario = model.simulate_fcfs(samples, lanes=1)
    assert scenario["observed_attempt_quality"]["error_rate"]["status"] == "partial"
    assert scenario["observed_attempt_quality"]["error_rate"]["coverage"] == 0.5
    assert scenario["observed_attempt_quality"]["conflict_rate"]["status"] == "unavailable"
    assert scenario["throughput"]["successful_completions_per_hour"] is None


def test_scenario_bundle_cross_checks_cap_1_cap_2_and_ideal() -> None:
    samples = [
        model.JobSample(10, "apify", "crawl"),
        model.JobSample(20, "gemini", "analysis"),
        model.JobSample(30, "apify", "crawl"),
        model.JobSample(40, "gemini", "analysis"),
        model.JobSample(50, "apify", "crawl"),
        model.JobSample(60, "gemini", "analysis"),
        model.JobSample(70, "apify", "crawl"),
        model.JobSample(80, "gemini", "analysis"),
    ]
    first = model.build_scenario_bundle(samples, lanes=(1, 2, 4, 8), input_sha256="a" * 64)
    second = model.build_scenario_bundle(samples, lanes=(1, 2, 4, 8), input_sha256="a" * 64)

    assert first["validation"]["pass"] is True
    assert first["lane_tiers"] == [1, 2, 4, 8]
    assert first["reproducibility"]["calculation_sha256"] == second["reproducibility"]["calculation_sha256"]
    assert first["claims"]["real_sla"] is False
    assert set(first["safety"].values()) == {0}

    current = first["profiles"]["current_guarded_cap_1"]["scenarios"]
    two_slot = first["profiles"]["two_slot_candidate_cap_2"]["scenarios"]
    ideal = first["profiles"]["ideal_unbounded_lower_bound"]["scenarios"]
    assert current[0]["makespan_seconds"] == two_slot[0]["makespan_seconds"] == ideal[0]["makespan_seconds"]
    for index in range(4):
        assert current[index]["makespan_seconds"] >= two_slot[index]["makespan_seconds"] >= ideal[index]["makespan_seconds"]
    assert all(set(row["provider_caps"].values()) == {1} for row in current)
    assert [set(row["provider_caps"].values()) for row in two_slot] == [{1}, {2}, {2}, {2}]
    assert all(row["provider_caps"] == {} for row in ideal)


def test_scenario_bundle_does_not_invent_a_cap_for_unclassified_work() -> None:
    samples = [
        model.JobSample(10, "comments_pipeline", "comments"),
        model.JobSample(1, "unclassified", "bookkeeping"),
        model.JobSample(1, "unclassified", "bookkeeping"),
    ]
    report = model.build_scenario_bundle(samples, lanes=(1, 2))
    current = report["profiles"]["current_guarded_cap_1"]
    assert current["definition"]["provider_caps"] == {"comments_pipeline": 1}
    assert all(row["provider_caps"] == {"comments_pipeline": 1} for row in current["scenarios"])
    assert report["validation"]["pass"] is True
    assert report["limitations"][0].startswith("the 3 durations")


@pytest.mark.parametrize("duration", [0.0, -1.0, float("nan"), float("inf"), 86_401])
def test_invalid_service_times_fail_closed(duration: float) -> None:
    with pytest.raises(ValueError, match="duration_seconds"):
        model.JobSample.from_mapping({"duration_seconds": duration})


def test_input_schema_and_exclusive_output(tmp_path: Path) -> None:
    input_path = tmp_path / "samples.json"
    input_path.write_text(
        json.dumps(
            {
                "schema_version": model.INPUT_SCHEMA_VERSION,
                "metadata": {"source": "aggregated_fixture"},
                "provider_caps": {"apify": 2},
                "samples": [
                    {"duration_seconds": 10, "resource_group": "apify", "job_type": "crawl"},
                    {"duration_seconds": 20, "resource_group": "apify", "job_type": "crawl"},
                ],
            }
        ),
        encoding="utf-8",
    )
    loaded, metadata, caps = model.load_samples(input_path)
    report = model.build_report(loaded, lanes=(1, 2), provider_caps=caps, input_metadata=metadata)
    output_path = tmp_path / "report.json"
    model.write_exclusive(output_path, report)
    assert json.loads(output_path.read_text(encoding="utf-8"))["schema_version"] == model.SCHEMA_VERSION
    with pytest.raises(FileExistsError):
        model.write_exclusive(output_path, report)


def test_invalid_quality_labels_fail_closed() -> None:
    with pytest.raises(ValueError, match="errored"):
        model.JobSample.from_mapping({"duration_seconds": 10, "errored": 0})
    with pytest.raises(ValueError, match="conflicted"):
        model.JobSample.from_mapping({"duration_seconds": 10, "conflicted": "false"})


def test_model_imports_no_network_or_runtime_clients_and_opens_no_socket(monkeypatch: pytest.MonkeyPatch) -> None:
    source = Path(model.__file__).read_text(encoding="utf-8")
    tree = ast.parse(source)
    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name.split(".", 1)[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module.split(".", 1)[0])
    assert imported.isdisjoint({"aiohttp", "httpx", "requests", "urllib", "psycopg", "redis"})

    def forbidden(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("socket attempted")

    monkeypatch.setattr(socket.socket, "connect", forbidden)
    report = model.build_report(jobs(4), lanes=(1, 2))
    assert report["safety"]["network_calls"] == 0
