from __future__ import annotations

import copy
import json
import subprocess
import sys
from pathlib import Path

from scripts import vkpi_engineering_health_score as score


ROOT = Path(__file__).resolve().parents[1]
CONTRACT = json.loads((ROOT / "docs/vkpi/engineering-health-score-contract-v1.json").read_text(encoding="utf-8"))


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


def test_contract_weights_are_complete() -> None:
    score.validate_contract(CONTRACT)


def test_all_targets_produce_formal_release_eligible_score() -> None:
    report = score.score_evidence(CONTRACT, _target_evidence())
    assert report["status"] == "formal"
    assert report["formal_score"] == 100.0
    assert report["grade_cap"] == 100.0
    assert report["release_eligible"] is True
    assert report["target_achieved"] is True


def test_missing_evidence_never_becomes_neutral_score() -> None:
    evidence = _target_evidence()
    evidence["metrics"]["code"]["branch_coverage"] = {
        "status": "missing",
        "value": None,
        "source": "",
        "observed_at": "",
    }
    report = score.score_evidence(CONTRACT, evidence)
    assert report["status"] == "provisional"
    assert report["formal_score"] is None
    assert report["dimensions"]["code"]["metrics"]["branch_coverage"]["score"] is None


def test_missing_or_mismatched_contract_binding_cannot_be_formal() -> None:
    missing = _target_evidence()
    del missing["contract_sha256"]
    assert score.score_evidence(CONTRACT, missing)["formal_score"] is None

    mismatched = _target_evidence()
    mismatched["contract_sha256"] = "0" * 64
    try:
        score.score_evidence(CONTRACT, mismatched)
    except score.ContractError as exc:
        assert "contract_sha256 mismatch" in str(exc)
    else:
        raise AssertionError("mismatched contract binding must fail closed")


def test_missing_low_weight_metrics_stay_in_original_score_denominator() -> None:
    evidence = _target_evidence()
    for dimension_name, metric_name in (
        ("code", "duplication_rate"),
        ("code", "core_mutation_score"),
        ("delivery", "deployment_frequency_per_week"),
        ("delivery", "lead_time_p50_hours"),
    ):
        del evidence["metrics"][dimension_name][metric_name]

    report = score.score_evidence(CONTRACT, evidence)

    assert report["status"] == "formal"
    assert report["evidence_coverage"] == 0.95
    assert report["dimensions"]["code"]["evidence_coverage"] == 0.9
    assert report["dimensions"]["delivery"]["evidence_coverage"] == 0.9
    assert report["dimensions"]["code"]["scoring_denominator"] == 1.0
    assert report["dimensions"]["delivery"]["scoring_denominator"] == 1.0
    assert report["dimensions"]["code"]["observed_score"] == 90.0
    assert report["dimensions"]["delivery"]["observed_score"] == 90.0
    assert report["formal_score"] == 95.0
    assert report["target_achieved"] is False


def test_insufficient_sample_count_is_missing_evidence() -> None:
    evidence = _target_evidence()
    evidence["metrics"]["delivery"]["change_failure_rate"] = _observed(0.0, sample_count=19)
    report = score.score_evidence(CONTRACT, evidence)
    metric = report["dimensions"]["delivery"]["metrics"]["change_failure_rate"]
    assert metric["status"] == "missing_or_insufficient"
    assert report["formal_score"] is None


def test_hard_gate_failure_caps_score_without_double_subtraction() -> None:
    evidence = _target_evidence()
    evidence["metrics"]["code"]["max_cc"] = _observed(51.0)
    report = score.score_evidence(CONTRACT, evidence)
    assert report["raw_score"] > 90.0
    assert report["grade_cap"] == 79.9
    assert report["formal_score"] == 79.9
    assert report["release_eligible"] is False
    assert report["target_achieved"] is False


def test_release_gate_failure_does_not_rewrite_technical_score() -> None:
    evidence = _target_evidence()
    evidence["release_gates"]["functional_gate_pass"] = _observed(False)
    report = score.score_evidence(CONTRACT, evidence)
    assert report["formal_score"] == 100.0
    assert report["release_eligible"] is False
    assert report["target_achieved"] is False


def test_dimension_floor_prevents_average_from_hiding_weakness() -> None:
    evidence = _target_evidence()
    weak = copy.deepcopy(evidence)
    for metric_name, rule in CONTRACT["dimensions"]["evolution"]["metrics"].items():
        bound = "floor" if rule["direction"] == "min" else "ceiling"
        weak["metrics"]["evolution"][metric_name] = _observed(float(rule[bound]))
    report = score.score_evidence(CONTRACT, weak)
    assert report["formal_score"] is not None
    assert report["target_achieved"] is False


def test_three_same_head_canonical_receipts_populate_delivery_and_provenance(tmp_path: Path) -> None:
    evidence = _target_evidence()
    receipts = []
    for index in range(3):
        path = tmp_path / f"gate-{index}.json"
        receipts.append(
            (
                path,
                {
                    "schema_version": "vkpi_canonical_gate_receipt_v1",
                    "generated_at": f"2026-08-28T00:00:0{index}+00:00",
                    "passed": True,
                    "candidate": {
                        "git_head": "abc",
                        "release_head": "abc",
                        "branch": "codex/fixture",
                        "clean_worktree": True,
                        "status_sha256": "1" * 64,
                        "source_content_sha256": "2" * 64,
                    },
                },
            )
        )
    score.merge_canonical_receipts(evidence, receipts)
    metric = evidence["metrics"]["delivery"]["canonical_gate_pass_rate"]
    assert metric["value"] == 1.0
    assert metric["sample_count"] == 3
    assert evidence["release_gates"]["canonical_gate_pass"]["value"] is True
    assert evidence["release_gates"]["artifact_provenance_pass"]["value"] is True


def test_one_canonical_receipt_cannot_satisfy_three_run_release_gate(tmp_path: Path) -> None:
    evidence = _target_evidence()
    receipt = {
        "schema_version": "vkpi_canonical_gate_receipt_v1",
        "generated_at": "2026-08-28T00:00:00+00:00",
        "passed": True,
        "candidate": {
            "git_head": "abc",
            "release_head": "abc",
            "branch": "codex/fixture",
            "clean_worktree": True,
            "status_sha256": "1" * 64,
            "source_content_sha256": "2" * 64,
        },
    }

    score.merge_canonical_receipts(evidence, [(tmp_path / "gate.json", receipt)])

    assert evidence["metrics"]["delivery"]["canonical_gate_pass_rate"]["sample_count"] == 1
    assert evidence["release_gates"]["canonical_gate_pass"]["value"] is False
    assert evidence["release_gates"]["artifact_provenance_pass"]["value"] is False


def test_duplicate_canonical_receipt_source_fails_closed(tmp_path: Path) -> None:
    evidence = _target_evidence()
    path = tmp_path / "gate.json"
    receipt = {
        "schema_version": "vkpi_canonical_gate_receipt_v1",
        "generated_at": "2026-08-28T00:00:00+00:00",
        "passed": True,
        "candidate": {
            "git_head": "abc",
            "release_head": "abc",
            "branch": "codex/fixture",
            "clean_worktree": True,
            "status_sha256": "1" * 64,
            "source_content_sha256": "2" * 64,
        },
    }

    try:
        score.merge_canonical_receipts(evidence, [(path, receipt), (path, receipt)])
    except score.ContractError as exc:
        assert "duplicate canonical receipt source" in str(exc)
    else:
        raise AssertionError("duplicate canonical receipt source must fail closed")


def test_canonical_receipt_head_mismatch_fails_closed(tmp_path: Path) -> None:
    evidence = _target_evidence()
    receipt = {
        "schema_version": "vkpi_canonical_gate_receipt_v1",
        "generated_at": "2026-08-28T00:00:00+00:00",
        "passed": True,
        "candidate": {
            "git_head": "different",
            "release_head": "different",
            "branch": "codex/fixture",
            "clean_worktree": True,
            "status_sha256": "1" * 64,
            "source_content_sha256": "2" * 64,
        },
    }
    try:
        score.merge_canonical_receipts(evidence, [(tmp_path / "gate.json", receipt)])
    except score.ContractError as exc:
        assert "head mismatch" in str(exc)
    else:
        raise AssertionError("head mismatch must fail closed")


def test_canonical_receipt_content_or_status_mismatch_fails_closed(tmp_path: Path) -> None:
    for field, value, message in (
        ("status_sha256", "3" * 64, "worktree status mismatch"),
        ("source_content_sha256", "4" * 64, "source content mismatch"),
    ):
        evidence = _target_evidence()
        receipt = {
            "schema_version": "vkpi_canonical_gate_receipt_v1",
            "generated_at": "2026-08-28T00:00:00+00:00",
            "passed": True,
            "candidate": {
                "git_head": "abc",
                "release_head": "abc",
                "branch": "codex/fixture",
                "clean_worktree": True,
                "status_sha256": "1" * 64,
                "source_content_sha256": "2" * 64,
                field: value,
            },
        }
        try:
            score.merge_canonical_receipts(
                evidence,
                [(tmp_path / f"gate-{field}.json", receipt)],
            )
        except score.ContractError as exc:
            assert message in str(exc)
        else:
            raise AssertionError("mismatched canonical receipt must fail closed")


def test_static_capture_uses_reviewed_line_guard_for_module_loc() -> None:
    evidence = _target_evidence()
    score.capture_static_metrics(evidence, root=ROOT)
    metric = evidence["metrics"]["architecture"]["module_loc_max"]
    assert metric["status"] == "observed"
    assert metric["value"] >= 800
    assert metric["details"]["production_source_file_count"] > 100
    assert metric["source"].startswith("command://scripts/check_line_guard.py")


def test_line_guard_no_tests_excludes_co_located_frontend_tests(tmp_path: Path) -> None:
    source_root = tmp_path / "frontend" / "src" / "components"
    source_root.mkdir(parents=True)
    (source_root / "Panel.tsx").write_text("export const Panel = 1;\n", encoding="utf-8")
    (source_root / "Panel.test.tsx").write_text("test('panel', () => {});\n", encoding="utf-8")
    (source_root / "Panel.spec.ts").write_text("test('panel', () => {});\n", encoding="utf-8")
    (source_root / "helper_test.py").write_text("def test_helper(): pass\n", encoding="utf-8")

    completed = subprocess.run(
        [
            sys.executable,
            str(ROOT / "scripts" / "check_line_guard.py"),
            str(tmp_path / "frontend" / "src"),
            "--limit",
            "0",
            "--no-tests",
            "--json",
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=True,
    )
    payload = json.loads(completed.stdout)

    assert [row["path"] for row in payload["violations"]] == [str(source_root / "Panel.tsx")]
