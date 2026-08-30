from __future__ import annotations

import pytest

from scripts.vkpi_technical_due_diligence_v1 import (
    ComparableAssumptions,
    SystemMetrics,
    TechnicalDueDiligenceEngineV1,
)


def _current_metrics() -> SystemMetrics:
    return SystemMetrics(
        cc_le_10_ratio=0.82916025,
        max_cyclomatic_complexity=67,
        cognitive_le_15_ratio=0.88995891,
        max_nesting_depth=6,
        duplication_rate=0.02966635,
        branch_coverage=0.55583863,
        cross_module_cycles=1,
        package_local_cycles=77,
        avg_main_sequence_distance=0.41776,
        max_class_loc=782,
        max_fanout=185,
        hotspot_avg_cc=4.4129,
        unhealthy_hotspot_ratio=0.1,
        temporal_coupling_rate=0.33333333,
        max_modules_changed_together=4,
        core_bus_factor=1,
        build_test_minutes=4.885,
        critical_fix_rate=None,
        quality_gate_enabled=None,
        change_failure_rate=None,
    )


def _comparable_assumptions() -> ComparableAssumptions:
    return ComparableAssumptions(
        critical_fix_rate_metric_score=50,
        change_failure_rate_metric_score=50,
        quality_gate_enabled=True,
    )


def test_reproduces_reviewed_43_7584_baseline_without_claiming_dora() -> None:
    receipt = TechnicalDueDiligenceEngineV1().assess(
        _current_metrics(), assumptions=_comparable_assumptions()
    )

    assert receipt["total_score"] == 43.7584
    assert receipt["dimension_scores"] == {
        "code_health": 75.0214,
        "architecture": 47.612,
        "evolution": 47.4,
        "delivery": 65.0,
    }
    assert receipt["claim_status"] == "descriptive_only"
    assert {item["metric"] for item in receipt["assumption_ledger"]} == {
        "critical_fix_rate",
        "change_failure_rate",
        "quality_gate_enabled",
    }


def test_frontend_cross_cycle_removal_is_a_nine_point_comparable_gain() -> None:
    values = _current_metrics().__dict__ | {"cross_module_cycles": 0}
    receipt = TechnicalDueDiligenceEngineV1().assess(
        SystemMetrics(**values), assumptions=_comparable_assumptions()
    )

    assert receipt["total_score"] == 52.7584
    assert not any(item["reason"] == "cross_module_cycles" for item in receipt["hard_penalties"])


def test_missing_delivery_evidence_fails_closed_without_explicit_assumption() -> None:
    with pytest.raises(ValueError, match="critical_fix_rate is unobserved"):
        TechnicalDueDiligenceEngineV1().assess(_current_metrics())


def test_target_gate_is_monotonic_and_requires_all_hard_penalties_cleared() -> None:
    values = _current_metrics().__dict__ | {
        "cc_le_10_ratio": 0.85,
        "max_cyclomatic_complexity": 50,
        "cognitive_le_15_ratio": 0.90,
        "branch_coverage": 0.70,
        "cross_module_cycles": 0,
        "package_local_cycles": 3,
        "avg_main_sequence_distance": 0.30,
        "max_fanout": 25,
        "temporal_coupling_rate": 0.25,
        "core_bus_factor": 3,
        "critical_fix_rate": 0.90,
        "quality_gate_enabled": True,
        "change_failure_rate": 0.15,
    }
    receipt = TechnicalDueDiligenceEngineV1().assess(SystemMetrics(**values))

    assert receipt["total_score"] >= 80
    assert receipt["target_pass"] is True
    assert receipt["hard_penalties"] == []
    assert receipt["claim_status"] == "observed_metrics_only"
