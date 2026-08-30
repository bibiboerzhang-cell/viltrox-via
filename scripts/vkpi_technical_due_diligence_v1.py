#!/usr/bin/env python3
"""Deterministic implementation of the user-provided technical DD engine V1.

The comparable score accepts explicit assumptions for delivery metrics that are
not observed.  Those assumptions are surfaced in the receipt and force
``claim_status=descriptive_only``; they are never presented as DORA evidence.
"""
from __future__ import annotations

import argparse
from dataclasses import asdict, dataclass
import json
from pathlib import Path
import sys
from typing import Any


def lower_is_better(value: float, excellent: float, qualified: float, redline: float) -> float:
    if value <= excellent:
        return 100.0
    if value <= qualified:
        return 100.0 - 40.0 * ((value - excellent) / (qualified - excellent))
    if value < redline:
        return 60.0 - 60.0 * ((value - qualified) / (redline - qualified))
    return 0.0


def higher_is_better(value: float, excellent: float, qualified: float, redline: float) -> float:
    if value >= excellent:
        return 100.0
    if value >= qualified:
        return 60.0 + 40.0 * ((value - qualified) / (excellent - qualified))
    if value > redline:
        return 60.0 * ((value - redline) / (qualified - redline))
    return 0.0


def weighted_average(scores: dict[str, float], weights: dict[str, float]) -> float:
    return sum(scores[key] * weight for key, weight in weights.items()) / sum(weights.values())


@dataclass(frozen=True)
class SystemMetrics:
    cc_le_10_ratio: float
    max_cyclomatic_complexity: int
    cognitive_le_15_ratio: float
    max_nesting_depth: int
    duplication_rate: float
    branch_coverage: float
    cross_module_cycles: int
    package_local_cycles: int
    avg_main_sequence_distance: float
    max_class_loc: int
    max_fanout: int
    hotspot_avg_cc: float
    unhealthy_hotspot_ratio: float
    temporal_coupling_rate: float
    max_modules_changed_together: int
    core_bus_factor: int
    build_test_minutes: float
    critical_fix_rate: float | None
    quality_gate_enabled: bool | None
    change_failure_rate: float | None

    @classmethod
    def from_mapping(cls, values: dict[str, Any]) -> "SystemMetrics":
        missing = sorted(field for field in cls.__dataclass_fields__ if field not in values)
        if missing:
            raise ValueError(f"missing V1 metrics: {', '.join(missing)}")
        return cls(**{field: values[field] for field in cls.__dataclass_fields__})


@dataclass(frozen=True)
class ComparableAssumptions:
    critical_fix_rate_metric_score: float | None = None
    change_failure_rate_metric_score: float | None = None
    quality_gate_enabled: bool | None = None

    @classmethod
    def from_mapping(cls, values: dict[str, Any] | None) -> "ComparableAssumptions":
        return cls(**(values or {}))


def _bounded_score(value: Any, name: str) -> float:
    number = float(value)
    if not 0.0 <= number <= 100.0:
        raise ValueError(f"{name} must be between 0 and 100")
    return number


class TechnicalDueDiligenceEngineV1:
    def assess(
        self,
        metrics: SystemMetrics,
        *,
        assumptions: ComparableAssumptions | None = None,
        target: float = 80.0,
    ) -> dict[str, Any]:
        assumptions = assumptions or ComparableAssumptions()
        metric_scores: dict[str, float] = {}
        red_flags: list[str] = []
        recommendations: list[str] = []
        assumption_ledger: list[dict[str, Any]] = []

        metric_scores["cyclomatic_complexity"] = higher_is_better(
            metrics.cc_le_10_ratio, 0.90, 0.80, 0.50
        )
        metric_scores["cognitive_complexity"] = higher_is_better(
            metrics.cognitive_le_15_ratio, 0.90, 0.80, 0.50
        )
        metric_scores["duplication"] = lower_is_better(
            metrics.duplication_rate, 0.03, 0.05, 0.10
        )
        metric_scores["coverage"] = higher_is_better(
            metrics.branch_coverage, 0.80, 0.60, 0.30
        )
        code_health = weighted_average(
            {
                "cc": metric_scores["cyclomatic_complexity"],
                "cognitive": metric_scores["cognitive_complexity"],
                "duplication": metric_scores["duplication"],
                "coverage": metric_scores["coverage"],
            },
            {"cc": 0.25, "cognitive": 0.20, "duplication": 0.20, "coverage": 0.35},
        )

        cycle_score = 100.0 if metrics.cross_module_cycles == 0 else 40.0 if metrics.cross_module_cycles <= 2 else 0.0
        if metrics.cross_module_cycles == 0 and metrics.package_local_cycles > 3:
            cycle_score = 80.0
        metric_scores["cycles"] = cycle_score
        metric_scores["main_sequence_distance"] = lower_is_better(
            metrics.avg_main_sequence_distance, 0.20, 0.40, 0.70
        )
        class_loc_score = lower_is_better(metrics.max_class_loc, 800, 1500, 3000)
        fanout_score = lower_is_better(metrics.max_fanout, 15, 25, 35)
        metric_scores["god_class"] = 0.5 * class_loc_score + 0.5 * fanout_score
        architecture = weighted_average(
            {
                "cycles": metric_scores["cycles"],
                "distance": metric_scores["main_sequence_distance"],
                "godclass": metric_scores["god_class"],
            },
            {"cycles": 0.40, "distance": 0.25, "godclass": 0.35},
        )

        hotspot_cc_score = lower_is_better(metrics.hotspot_avg_cc, 10, 15, 30)
        hotspot_ratio_score = lower_is_better(metrics.unhealthy_hotspot_ratio, 0.00, 0.05, 0.25)
        metric_scores["hotspots"] = 0.7 * hotspot_cc_score + 0.3 * hotspot_ratio_score
        metric_scores["temporal_coupling"] = lower_is_better(
            metrics.temporal_coupling_rate, 0.10, 0.25, 0.50
        )
        metric_scores["bus_factor"] = higher_is_better(metrics.core_bus_factor, 3, 2, 1)
        evolution = weighted_average(
            {
                "hotspots": metric_scores["hotspots"],
                "temporal": metric_scores["temporal_coupling"],
                "bus": metric_scores["bus_factor"],
            },
            {"hotspots": 0.40, "temporal": 0.35, "bus": 0.25},
        )

        metric_scores["build_time"] = lower_is_better(metrics.build_test_minutes, 10, 25, 45)
        quality_gate_enabled = metrics.quality_gate_enabled
        if quality_gate_enabled is None:
            quality_gate_enabled = assumptions.quality_gate_enabled
            if quality_gate_enabled is not None:
                assumption_ledger.append(
                    {"metric": "quality_gate_enabled", "assumed_value": quality_gate_enabled}
                )
        if quality_gate_enabled is False:
            quality_score = 0.0
        elif metrics.critical_fix_rate is not None:
            quality_score = higher_is_better(metrics.critical_fix_rate, 1.00, 0.85, 0.50)
        elif assumptions.critical_fix_rate_metric_score is not None:
            quality_score = _bounded_score(
                assumptions.critical_fix_rate_metric_score, "critical_fix_rate_metric_score"
            )
            assumption_ledger.append(
                {"metric": "critical_fix_rate", "assumed_metric_score": quality_score}
            )
        else:
            raise ValueError("critical_fix_rate is unobserved and no explicit score assumption was supplied")
        metric_scores["quality_gate"] = quality_score

        if metrics.change_failure_rate is not None:
            cfr_score = lower_is_better(metrics.change_failure_rate, 0.05, 0.15, 0.25)
        elif assumptions.change_failure_rate_metric_score is not None:
            cfr_score = _bounded_score(
                assumptions.change_failure_rate_metric_score, "change_failure_rate_metric_score"
            )
            assumption_ledger.append(
                {"metric": "change_failure_rate", "assumed_metric_score": cfr_score}
            )
        else:
            raise ValueError("change_failure_rate is unobserved and no explicit score assumption was supplied")
        metric_scores["change_failure_rate"] = cfr_score
        delivery = weighted_average(
            {
                "build": metric_scores["build_time"],
                "quality": metric_scores["quality_gate"],
                "cfr": metric_scores["change_failure_rate"],
            },
            {"build": 0.30, "quality": 0.30, "cfr": 0.40},
        )

        penalties: list[dict[str, Any]] = []
        if metrics.cross_module_cycles > 0:
            penalties.append({"reason": "cross_module_cycles", "points": 5.0})
        if metrics.max_cyclomatic_complexity > 50:
            penalties.append({"reason": "max_cyclomatic_complexity_gt_50", "points": 5.0})
        if metrics.core_bus_factor == 1:
            penalties.append({"reason": "core_bus_factor_equals_1", "points": 5.0})
        if quality_gate_enabled is not True:
            penalties.append({"reason": "quality_gate_not_enabled", "points": 5.0})

        if metrics.max_cyclomatic_complexity > 30:
            red_flags.append(f"max cyclomatic complexity is {metrics.max_cyclomatic_complexity}")
        if metrics.max_nesting_depth > 5:
            red_flags.append(f"max nesting depth is {metrics.max_nesting_depth}")
        if metrics.cross_module_cycles > 0:
            red_flags.append(f"cross-module cycles remain: {metrics.cross_module_cycles}")
        if metrics.max_modules_changed_together >= 4:
            red_flags.append("a change commonly touches four or more modules")
        if metrics.core_bus_factor <= 1:
            red_flags.append("people-normalized core Bus Factor is 1")

        if metric_scores["cycles"] < 60:
            recommendations.append("P0: remove cross-module cycles")
        if metric_scores["hotspots"] < 60:
            recommendations.append("P0: refactor high-churn high-complexity hotspots")
        if metric_scores["coverage"] < 60:
            recommendations.append("P1: raise critical branch coverage")
        if metric_scores["bus_factor"] < 60:
            recommendations.append("P1: rotate real maintainers across core domains")

        dimensions = {
            "code_health": code_health,
            "architecture": architecture,
            "evolution": evolution,
            "delivery": delivery,
        }
        subtotal = weighted_average(dimensions, {name: 0.25 for name in dimensions})
        total = max(0.0, subtotal - sum(item["points"] for item in penalties))
        grade = "A" if total >= 85 else "B" if total >= 70 else "C" if total >= 55 else "D"
        return {
            "schema_version": "vkpi_technical_due_diligence_v1",
            "claim_status": "descriptive_only" if assumption_ledger else "observed_metrics_only",
            "target": round(float(target), 4),
            "target_pass": total >= target,
            "total_score": round(total, 4),
            "grade": grade,
            "dimension_scores": {key: round(value, 4) for key, value in dimensions.items()},
            "metric_scores": {key: round(value, 4) for key, value in metric_scores.items()},
            "hard_penalties": penalties,
            "assumption_ledger": assumption_ledger,
            "red_flags": red_flags,
            "recommendations": recommendations,
            "input_metrics": asdict(metrics),
        }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", required=True, help="JSON containing metrics and optional assumptions")
    parser.add_argument("--json-out", help="Optional output receipt path")
    parser.add_argument("--target", type=float, default=80.0)
    parser.add_argument("--require-target", action="store_true")
    return parser


def main() -> int:
    args = _parser().parse_args()
    payload = json.loads(Path(args.input).read_text(encoding="utf-8"))
    receipt = TechnicalDueDiligenceEngineV1().assess(
        SystemMetrics.from_mapping(payload.get("metrics") or {}),
        assumptions=ComparableAssumptions.from_mapping(payload.get("assumptions")),
        target=args.target,
    )
    rendered = json.dumps(receipt, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    if args.json_out:
        Path(args.json_out).write_text(rendered, encoding="utf-8")
    sys.stdout.write(rendered)
    return 1 if args.require_target and not receipt["target_pass"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
