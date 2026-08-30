"""Delivery receipt channel + non-finite evidence guard for the health scorer."""
from __future__ import annotations

import copy
import json
import math
from pathlib import Path

import pytest

from scripts import vkpi_engineering_health_score as score
from scripts import vkpi_engineering_health_score_delivery as delivery


ROOT = Path(__file__).resolve().parents[1]
CONTRACT = json.loads(
    (ROOT / "docs/vkpi/engineering-health-score-contract-v1.json").read_text(encoding="utf-8")
)
DELIVERY_RULES = {
    name: rule
    for name, rule in CONTRACT["dimensions"]["delivery"]["metrics"].items()
    if name not in delivery.DELIVERY_CANONICAL_CHANNEL_METRICS
}
NON_FINITE = (float("-inf"), float("inf"), float("nan"))


def _observed(value: float | bool, *, sample_count: int = 500) -> dict[str, object]:
    return {
        "status": "observed",
        "value": value,
        "source": "fixture://authoritative",
        "observed_at": "2026-08-28T00:00:00+00:00",
        "sample_count": sample_count,
    }


def _target_evidence() -> dict[str, object]:
    metrics: dict[str, dict[str, object]] = {}
    for dimension_name, dimension in CONTRACT["dimensions"].items():
        metrics[dimension_name] = {}
        for metric_name, rule in dimension["metrics"].items():
            metrics[dimension_name][metric_name] = _observed(float(rule["target"]))
    return {
        "schema_version": "vkpi_engineering_health_evidence_v1",
        "contract_sha256": score.contract_sha256(CONTRACT),
        "candidate": {
            "head": "abc",
            "branch": "codex/fixture",
            "clean_worktree": True,
            "status_sha256": "1" * 64,
            "source_content_sha256": "2" * 64,
        },
        "metrics": metrics,
        "release_gates": {name: _observed(True) for name in CONTRACT["release_gates"]},
    }


def _delivery_receipt(head: str = "abc") -> dict[str, object]:
    return {
        "schema_version": "vkpi_delivery_receipt_v1",
        "candidate": {"head": head, "worktree_dirty": False},
        "window": {
            "start": "2026-06-01T00:00:00+00:00",
            "end": "2026-08-30T00:00:00+00:00",
            "days": 90,
            "ledger_covered_days": 90,
        },
        "metrics": {
            name: {
                "status": "observed",
                "value": float(rule["target"]),
                "sample_count": 500,
            }
            for name, rule in DELIVERY_RULES.items()
        },
        "sources": {
            "post_deploy_dirs": 12,
            "incidents_lines": 34,
            "verify_receipts": 56,
            "outcome_files": 7,
        },
    }


# --- Task 1: non-finite evidence must never score -------------------------


@pytest.mark.parametrize("bad_value", NON_FINITE, ids=("neg_inf", "pos_inf", "nan"))
def test_non_finite_evidence_value_is_missing_never_scored(bad_value: float) -> None:
    evidence = _target_evidence()
    # "max" direction metric: -inf used to fall into value<=target => 100.0.
    evidence["metrics"]["delivery"]["mttr_p50_minutes"] = _observed(bad_value)
    report = score.score_evidence(CONTRACT, evidence)
    metric = report["dimensions"]["delivery"]["metrics"]["mttr_p50_minutes"]
    assert metric["status"] == "missing_or_insufficient"
    assert metric["score"] is None
    # The lost weight drags the dimension down instead of inflating it to 100.
    assert report["dimensions"]["delivery"]["observed_score"] == 90.0
    assert report["dimensions"]["delivery"]["evidence_coverage"] == 0.9
    assert report["target_achieved"] is False


@pytest.mark.parametrize("bad_value", NON_FINITE, ids=("neg_inf", "pos_inf", "nan"))
def test_non_finite_min_direction_evidence_is_missing(bad_value: float) -> None:
    evidence = _target_evidence()
    evidence["metrics"]["delivery"]["p1_p2_sla_rate"] = _observed(bad_value)
    report = score.score_evidence(CONTRACT, evidence)
    metric = report["dimensions"]["delivery"]["metrics"]["p1_p2_sla_rate"]
    assert metric["status"] == "missing_or_insufficient"
    assert metric["score"] is None


@pytest.mark.parametrize("bad_value", NON_FINITE, ids=("neg_inf", "pos_inf", "nan"))
def test_number_helper_rejects_non_finite(bad_value: float) -> None:
    with pytest.raises(score.ContractError, match="must be finite"):
        score._number(bad_value, label="probe")


def test_non_finite_hard_gate_evidence_fails_closed() -> None:
    evidence = _target_evidence()
    evidence["metrics"]["code"]["max_cc"] = _observed(float("-inf"))
    with pytest.raises(score.ContractError, match="must be finite"):
        score.score_evidence(CONTRACT, evidence)


# --- Task 2: delivery receipt channel -------------------------------------


def test_valid_delivery_receipt_merges_and_scores(tmp_path: Path) -> None:
    evidence = _target_evidence()
    for name in DELIVERY_RULES:
        evidence["metrics"]["delivery"][name] = {
            "status": "missing", "value": None, "source": "", "observed_at": "",
        }
    receipt = _delivery_receipt()
    receipt_path = tmp_path / "delivery.json"
    score.merge_delivery_receipt(CONTRACT, evidence, receipt_path, receipt)
    for name in DELIVERY_RULES:
        merged = evidence["metrics"]["delivery"][name]
        assert merged["status"] == "observed"
        assert merged["source"] == f"receipt://{receipt_path.resolve()}"
        assert merged["observed_at"] == "2026-08-30T00:00:00+00:00"
    report = score.score_evidence(CONTRACT, evidence)
    assert report["status"] == "formal"
    assert report["formal_score"] == 100.0
    assert report["dimensions"]["delivery"]["observed_score"] == 100.0


def test_delivery_receipt_head_mismatch_rejected(tmp_path: Path) -> None:
    evidence = _target_evidence()
    with pytest.raises(score.ContractError, match="head mismatch"):
        score.merge_delivery_receipt(
            CONTRACT, evidence, tmp_path / "delivery.json", _delivery_receipt(head="def")
        )
    unbound = _target_evidence()
    unbound["candidate"]["head"] = ""
    with pytest.raises(score.ContractError, match="candidate head is required"):
        score.merge_delivery_receipt(
            CONTRACT, unbound, tmp_path / "delivery.json", _delivery_receipt()
        )


def test_delivery_receipt_low_sample_count_downgrades_fail_closed(tmp_path: Path) -> None:
    evidence = _target_evidence()
    receipt = _delivery_receipt()
    minimum = int(DELIVERY_RULES["change_failure_rate"]["minimum_samples"])
    receipt["metrics"]["change_failure_rate"]["sample_count"] = minimum - 1
    score.merge_delivery_receipt(CONTRACT, evidence, tmp_path / "delivery.json", receipt)
    merged = evidence["metrics"]["delivery"]["change_failure_rate"]
    assert merged["status"] == "missing_or_insufficient"
    assert merged["reason"] == delivery.INSUFFICIENT_SAMPLES_REASON
    report = score.score_evidence(CONTRACT, evidence)
    metric = report["dimensions"]["delivery"]["metrics"]["change_failure_rate"]
    assert metric["status"] == "missing_or_insufficient"
    assert metric["score"] is None
    assert report["formal_score"] is None


@pytest.mark.parametrize("bad_value", NON_FINITE, ids=("neg_inf", "pos_inf", "nan"))
def test_delivery_receipt_non_finite_value_rejected(tmp_path: Path, bad_value: float) -> None:
    evidence = _target_evidence()
    receipt = _delivery_receipt()
    receipt["metrics"]["rollback_p95_minutes"]["value"] = bad_value
    with pytest.raises(score.ContractError, match="must be finite"):
        score.merge_delivery_receipt(CONTRACT, evidence, tmp_path / "delivery.json", receipt)

    missing_carrier = _delivery_receipt()
    missing_carrier["metrics"]["rollback_p95_minutes"] = {
        "status": "missing_or_insufficient", "value": bad_value,
        "sample_count": 0, "reason": "ledger_gap",
    }
    with pytest.raises(score.ContractError, match="must be finite"):
        score.merge_delivery_receipt(
            CONTRACT, evidence, tmp_path / "delivery.json", missing_carrier
        )


def test_delivery_receipt_missing_entry_merges_without_score(tmp_path: Path) -> None:
    evidence = _target_evidence()
    receipt = _delivery_receipt()
    receipt["metrics"]["lead_time_p50_hours"] = {
        "status": "missing_or_insufficient", "value": None,
        "sample_count": 3, "reason": "ledger_gap",
    }
    score.merge_delivery_receipt(CONTRACT, evidence, tmp_path / "delivery.json", receipt)
    merged = evidence["metrics"]["delivery"]["lead_time_p50_hours"]
    assert merged["status"] == "missing_or_insufficient"
    assert merged["value"] is None
    assert merged["reason"] == "ledger_gap"
    report = score.score_evidence(CONTRACT, evidence)
    assert report["dimensions"]["delivery"]["metrics"]["lead_time_p50_hours"]["score"] is None


def test_delivery_receipt_metric_names_must_match_contract(tmp_path: Path) -> None:
    evidence = _target_evidence()
    dropped = _delivery_receipt()
    del dropped["metrics"]["mttr_p90_minutes"]
    with pytest.raises(score.ContractError, match="match the contract delivery block"):
        score.merge_delivery_receipt(CONTRACT, evidence, tmp_path / "delivery.json", dropped)

    canonical_smuggler = _delivery_receipt()
    canonical_smuggler["metrics"]["canonical_gate_pass_rate"] = {
        "status": "observed", "value": 1.0, "sample_count": 3,
    }
    with pytest.raises(score.ContractError, match="match the contract delivery block"):
        score.merge_delivery_receipt(
            CONTRACT, evidence, tmp_path / "delivery.json", canonical_smuggler
        )

    unknown = _delivery_receipt()
    unknown["metrics"]["made_up_metric"] = {"status": "observed", "value": 1.0, "sample_count": 9}
    with pytest.raises(score.ContractError, match="match the contract delivery block"):
        score.merge_delivery_receipt(CONTRACT, evidence, tmp_path / "delivery.json", unknown)


def test_delivery_receipt_schema_window_and_sources_validated(tmp_path: Path) -> None:
    evidence = _target_evidence()
    cases = (
        ({"schema_version": "vkpi_delivery_receipt_v2"}, "unsupported delivery receipt schema"),
        ({"window": {"start": "a", "end": "b", "days": 60, "ledger_covered_days": 60}}, "90-day window"),
        ({"window": {"start": "", "end": "b", "days": 90, "ledger_covered_days": 90}}, "window start is required"),
        ({"window": {"start": "a", "end": "b", "days": 90, "ledger_covered_days": 120}}, "out of range"),
        ({"sources": {"post_deploy_dirs": 1}}, "four canonical source kinds"),
        ({"candidate": {"head": "abc", "worktree_dirty": "no"}}, "worktree_dirty must be boolean"),
    )
    for override, message in cases:
        receipt = _delivery_receipt()
        receipt.update(copy.deepcopy(override))
        with pytest.raises(score.ContractError, match=message):
            score.merge_delivery_receipt(
                CONTRACT, evidence, tmp_path / "delivery.json", receipt
            )


def test_delivery_receipt_observed_requires_positive_integer_samples(tmp_path: Path) -> None:
    evidence = _target_evidence()
    for bad_samples in (0, -1, 2.5, True, None, "20"):
        receipt = _delivery_receipt()
        receipt["metrics"]["build_test_p95_minutes"]["sample_count"] = bad_samples
        with pytest.raises(score.ContractError, match="positive integer"):
            score.merge_delivery_receipt(
                CONTRACT, evidence, tmp_path / "delivery.json", receipt
            )


def test_cli_delivery_receipt_flag_merges_into_report(tmp_path: Path) -> None:
    evidence = _target_evidence()
    for name in DELIVERY_RULES:
        evidence["metrics"]["delivery"][name] = {
            "status": "missing", "value": None, "source": "", "observed_at": "",
        }
    evidence_path = tmp_path / "evidence.json"
    evidence_path.write_text(json.dumps(evidence), encoding="utf-8")
    receipt_path = tmp_path / "delivery.json"
    receipt_path.write_text(json.dumps(_delivery_receipt()), encoding="utf-8")
    report_path = tmp_path / "report.json"

    exit_code = score.main([
        "--contract", str(ROOT / "docs/vkpi/engineering-health-score-contract-v1.json"),
        "--evidence", str(evidence_path),
        "--delivery-receipt", str(receipt_path),
        "--json", "--json-out", str(report_path),
        "--require-formal",
    ])

    assert exit_code == 0
    report = json.loads(report_path.read_text(encoding="utf-8"))
    assert report["formal_score"] == 100.0
    for name in DELIVERY_RULES:
        assert report["dimensions"]["delivery"]["metrics"][name]["status"] == "observed"


def test_cli_template_mode_rejects_delivery_receipt(tmp_path: Path) -> None:
    receipt_path = tmp_path / "delivery.json"
    receipt_path.write_text(json.dumps(_delivery_receipt()), encoding="utf-8")
    with pytest.raises(score.ContractError, match="requires collected --evidence"):
        score.main([
            "--contract", str(ROOT / "docs/vkpi/engineering-health-score-contract-v1.json"),
            "--template", "--delivery-receipt", str(receipt_path),
        ])


def test_validator_never_recomputes_values() -> None:
    """The channel passes collector values through untouched (binding only)."""
    evidence = _target_evidence()
    receipt = _delivery_receipt()
    receipt["metrics"]["deployment_frequency_per_week"]["value"] = 3.75
    validated = delivery.validate_delivery_receipt(CONTRACT, evidence, receipt)
    assert validated["deployment_frequency_per_week"]["value"] == 3.75
    assert math.isfinite(validated["deployment_frequency_per_week"]["value"])
