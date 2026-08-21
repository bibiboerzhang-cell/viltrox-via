#!/usr/bin/env python3
"""Run the provider-free Gemini final_v1 structured quality evaluator.

Example:

  python scripts/eval_gemini_final_v1_quality.py \
    --gold evals/fixtures/gemini_final_v1_synthetic_gold.json \
    --predictions evals/fixtures/gemini_final_v1_synthetic_predictions.json \
    --output /tmp/gemini-final-v1-quality.json --pretty

The command reads local JSON only.  It never imports or calls Gemini, another
provider, or a business database.  Reports are always ``descriptive_only``.
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import sys
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = ROOT / "backend" / "app" / "domains" / "kol" / "final_v1_quality_eval.py"


def _load_evaluator():
    spec = importlib.util.spec_from_file_location("vkpi_gemini_final_v1_quality_eval", MODULE_PATH)
    if spec is None or spec.loader is None:
        raise RuntimeError("final_v1_quality_evaluator_unavailable")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


EVALUATOR = _load_evaluator()
FinalV1QualityInputError = EVALUATOR.FinalV1QualityInputError
evaluate_final_v1_quality = EVALUATOR.evaluate_final_v1_quality


def _load(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _render(payload: Any, *, pretty: bool) -> str:
    return json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        indent=2 if pretty else None,
        separators=None if pretty else (",", ":"),
    ) + "\n"


def _emit(payload: Any, *, output: Path | None, pretty: bool) -> None:
    rendered = _render(payload, pretty=pretty)
    if output is None:
        sys.stdout.write(rendered)
        return
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(rendered, encoding="utf-8")


def _blocked(error: str) -> dict[str, Any]:
    return {
        "schema_version": "gemini_final_v1_quality_report_v1",
        "evaluation_status": "not_evaluated",
        "claim_status": "descriptive_only",
        "accuracy_claim": {"allowed": False, "reason": "evaluation_input_invalid"},
        "quality_gate": {
            "metric_status": "blocked",
            "production_acceptance_eligible": False,
            "reason": error,
        },
        "diagnostics": {
            "provider_calls_during_evaluation": False,
            "llm_calls_during_evaluation": False,
            "database_reads_during_evaluation": False,
            "database_writes_during_evaluation": False,
            "title_fields_used_as_evidence": False,
        },
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--gold", type=Path, required=True)
    parser.add_argument("--predictions", type=Path, required=True)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--pretty", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        report = evaluate_final_v1_quality(
            _load(args.gold),
            _load(args.predictions),
        )
    except (FinalV1QualityInputError, json.JSONDecodeError, OSError) as exc:
        code = str(exc) if isinstance(exc, FinalV1QualityInputError) else "evaluation_input_unreadable"
        _emit(_blocked(code), output=args.output, pretty=args.pretty)
        return 2
    _emit(report, output=args.output, pretty=args.pretty)
    return 0 if report["quality_gate"]["metric_status"] == "pass" else 4


if __name__ == "__main__":
    raise SystemExit(main())
