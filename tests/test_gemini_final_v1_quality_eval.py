from __future__ import annotations

import ast
import copy
import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = ROOT / "backend/app/domains/kol/final_v1_quality_eval.py"
GOLD_PATH = ROOT / "evals/fixtures/gemini_final_v1_synthetic_gold.json"
PREDICTIONS_PATH = ROOT / "evals/fixtures/gemini_final_v1_synthetic_predictions.json"
SCHEMA_PATH = ROOT / "evals/schemas/gemini_final_v1_quality_gold.schema.json"
CLI_PATH = ROOT / "scripts/eval_gemini_final_v1_quality.py"


def _load_module():
    spec = importlib.util.spec_from_file_location("test_final_v1_quality_eval", MODULE_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


EVALUATOR = _load_module()


def _json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _fixtures() -> tuple[dict, dict]:
    return _json(GOLD_PATH), _json(PREDICTIONS_PATH)


def _record(predictions: dict, case_id: str) -> dict:
    return next(item for item in predictions["predictions"] if item["case_id"] == case_id)


def _block(record: dict) -> dict:
    return record["output"]["layer1_visual_content"]["brand_product_evidence"]


def _check(report: dict, name: str) -> dict:
    return next(item for item in report["quality_gate"]["checks"] if item["metric"] == name)


def test_evaluator_import_graph_is_stdlib_only() -> None:
    tree = ast.parse(MODULE_PATH.read_text(encoding="utf-8"))
    imported_roots = {
        node.module.split(".", 1)[0]
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) and node.module
    }
    imported_roots.update(
        alias.name.split(".", 1)[0]
        for node in ast.walk(tree)
        if isinstance(node, ast.Import)
        for alias in node.names
    )
    assert imported_roots <= {
        "__future__",
        "hashlib",
        "json",
        "math",
        "re",
        "typing",
        "unicodedata",
    }


def test_synthetic_fixture_is_complete_but_never_claims_model_accuracy() -> None:
    gold, predictions = _fixtures()
    report = EVALUATOR.evaluate_final_v1_quality(gold, predictions)

    assert report["evaluation_status"] == "evaluated"
    assert report["claim_status"] == "descriptive_only"
    assert report["accuracy_claim"] == {
        "allowed": False,
        "reason": "synthetic_gold_and_no_verified_gemini_execution",
        "declared_model_invoked": False,
    }
    assert report["quality_gate"]["metric_status"] == "pass"
    assert report["quality_gate"]["production_acceptance_eligible"] is False
    assert report["metrics"]["brand_status"]["unknown_as_absent_count"] == 0
    assert report["metrics"]["non_title_evidence"]["recall"] == 1.0
    assert report["metrics"]["products"]["precision"] == 1.0
    assert report["metrics"]["products"]["recall"] == 1.0
    assert report["metrics"]["products"]["hallucination_count"] == 0
    assert report["metrics"]["competitors"]["f1"] == 1.0
    assert report["metrics"]["evidence_support"]["modality_support_rate"] == 1.0
    assert report["metrics"]["evidence_support"]["timestamp_support_rate"] == 1.0
    assert report["metrics"]["schema_coverage"]["coverage"] == 1.0
    assert report["diagnostics"] == {
        "provider_calls_during_evaluation": False,
        "llm_calls_during_evaluation": False,
        "database_reads_during_evaluation": False,
        "database_writes_during_evaluation": False,
        "title_fields_used_as_evidence": False,
    }


def test_gold_schema_and_cases_pin_media_model_prompt_and_structured_evidence() -> None:
    gold, _ = _fixtures()
    schema = _json(SCHEMA_PATH)

    assert schema["$schema"].endswith("2020-12/schema")
    assert schema["properties"]["claim_status"]["const"] == "descriptive_only"
    assert schema["$defs"]["case"]["properties"]["media_sha256"]["pattern"]
    assert EVALUATOR.validate_gold(gold) is gold
    for case in gold["cases"]:
        assert len(case["media_sha256"]) == 64
        assert case["model"]
        assert case["prompt_version"]
        for evidence in case["expected"]["evidence"]:
            assert evidence["modality"] in {"visual", "subtitle", "audio"}
            assert isinstance(evidence["timestamp_seconds"], (int, float))
            assert evidence["in_title"] is False


def test_title_and_free_text_never_substitute_for_structured_evidence() -> None:
    gold, predictions = _fixtures()
    present = _record(predictions, "synthetic-present-001")
    present["output"]["title"] = "Viltrox AF 35mm F1.2 LAB FE versus Sigma"
    present["output"]["layer1_visual_content"]["content_summary"] = (
        "The title says Viltrox and Sigma at every expected timestamp."
    )
    block = _block(present)
    block["viltrox_evidence"] = ["title says Viltrox", "summary says Viltrox"]
    block["viltrox_products"][0]["evidence"] = ["product appears in title"]
    block["competitors"][0]["evidence"] = ["Sigma appears in title"]

    report = EVALUATOR.evaluate_final_v1_quality(gold, predictions)

    assert report["metrics"]["non_title_evidence"]["matched_count"] == 0
    assert report["metrics"]["non_title_evidence"]["recall"] == 0.0
    assert report["metrics"]["non_title_evidence"]["title_fields_read_as_evidence"] == 0
    assert report["metrics"]["evidence_support"]["modality_support_rate"] == 0.0
    assert report["metrics"]["evidence_support"]["timestamp_support_rate"] == 0.0
    assert report["metrics"]["evidence_support"]["malformed_structured_evidence_count"] == 4
    assert report["quality_gate"]["metric_status"] == "fail"


def test_unknown_to_absent_is_visible_and_zero_tolerance_fails() -> None:
    gold, predictions = _fixtures()
    unknown = _record(predictions, "synthetic-unknown-001")
    block = _block(unknown)
    block["viltrox_status"] = "absent"
    block["inspection_complete"] = True
    block["checked_modalities"] = ["visual", "audio"]

    report = EVALUATOR.evaluate_final_v1_quality(gold, predictions)

    matrix = report["metrics"]["brand_status"]["confusion_matrix"]
    assert matrix["unknown"]["absent"] == 1
    assert report["metrics"]["brand_status"]["unknown_as_absent_count"] == 1
    assert _check(report, "unknown_as_absent_max")["passed"] is False
    assert report["quality_gate"]["metric_status"] == "fail"


def test_timestamp_and_modality_without_observation_are_not_support() -> None:
    gold, predictions = _fixtures()
    present = _record(predictions, "synthetic-present-001")
    product_evidence = _block(present)["viltrox_products"][0]["evidence"][0]
    product_evidence.pop("observation")

    report = EVALUATOR.evaluate_final_v1_quality(gold, predictions)

    assert report["metrics"]["non_title_evidence"]["matched_count"] == 3
    support = report["metrics"]["evidence_support"]
    assert support["malformed_structured_evidence_count"] == 1
    assert support["modality_support_rate"] == pytest.approx(2 / 3, abs=1e-6)
    assert support["timestamp_support_rate"] == pytest.approx(2 / 3, abs=1e-6)
    assert report["quality_gate"]["metric_status"] == "fail"


def test_product_hallucination_and_competitor_set_errors_are_counted() -> None:
    gold, predictions = _fixtures()
    present = _record(predictions, "synthetic-present-001")
    block = _block(present)
    block["viltrox_products"].append(
        {
            "name": "Imaginary 99mm F0.7",
            "sku": "FAKE-99",
            "evidence": [
                {"modality": "visual", "timestamp": "00:20", "observation": "synthetic"}
            ],
        }
    )
    block["competitors"].append(
        {
            "brand": "Imaginary Rival",
            "evidence": [
                {"modality": "audio", "timestamp": "00:21", "observation": "synthetic"}
            ],
        }
    )

    report = EVALUATOR.evaluate_final_v1_quality(gold, predictions)

    products = report["metrics"]["products"]
    assert products["true_positive"] == 1
    assert products["false_positive"] == 1
    assert products["hallucination_count"] == 1
    assert products["precision"] == 0.5
    assert products["recall"] == 1.0
    competitors = report["metrics"]["competitors"]
    assert competitors["false_positive"] == 1
    assert competitors["precision"] == 0.5
    assert competitors["recall"] == 1.0
    assert competitors["f1"] == pytest.approx(2 / 3, abs=1e-6)


def test_prediction_provenance_drift_is_not_scored_as_matching_media() -> None:
    gold, predictions = _fixtures()
    present = _record(predictions, "synthetic-present-001")
    present["media_sha256"] = "d" * 64

    report = EVALUATOR.evaluate_final_v1_quality(gold, predictions)

    assert report["input_integrity"]["missing_or_drifted"] == [
        "synthetic-present-001:prediction_media_sha256_mismatch"
    ]
    case = next(item for item in report["cases"] if item["case_id"] == "synthetic-present-001")
    assert case["provenance_valid"] is False
    assert case["brand_predicted"] == "invalid"
    assert case["schema_fields_present"] == 0
    assert report["quality_gate"]["metric_status"] == "fail"


@pytest.mark.parametrize(
    ("field", "value", "expected_error"),
    [
        ("timestamp_seconds", "00:08", "gold_evidence_timestamp_invalid"),
        ("in_title", True, "gold_title_cannot_be_evidence"),
        ("entity_key", "orphan-product", "gold_evidence_entity_orphan"),
    ],
)
def test_invalid_gold_evidence_is_rejected_before_metrics(
    field: str,
    value: object,
    expected_error: str,
) -> None:
    gold, _ = _fixtures()
    gold = copy.deepcopy(gold)
    gold["cases"][0]["expected"]["evidence"][0][field] = value

    with pytest.raises(EVALUATOR.FinalV1QualityInputError) as raised:
        EVALUATOR.validate_gold(gold)
    assert str(raised.value) == expected_error


def test_cli_writes_machine_readable_provider_free_report(tmp_path: Path) -> None:
    output = tmp_path / "report.json"
    completed = subprocess.run(
        [
            sys.executable,
            str(CLI_PATH),
            "--gold",
            str(GOLD_PATH),
            "--predictions",
            str(PREDICTIONS_PATH),
            "--output",
            str(output),
        ],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
    )

    assert completed.returncode == 0, completed.stderr
    assert completed.stdout == ""
    report = _json(output)
    assert report["schema_version"] == "gemini_final_v1_quality_report_v1"
    assert report["claim_status"] == "descriptive_only"
    assert report["quality_gate"]["metric_status"] == "pass"
    assert report["diagnostics"]["provider_calls_during_evaluation"] is False
    assert report["diagnostics"]["database_reads_during_evaluation"] is False
