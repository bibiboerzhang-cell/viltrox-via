#!/usr/bin/env python3
"""Evaluate human-reviewed KOL analysis labels from JSON or JSONL."""
from __future__ import annotations

import argparse
import importlib.util
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = ROOT / "backend" / "app" / "domains" / "kol" / "analysis_precision_eval.py"
SPEC = importlib.util.spec_from_file_location("vkpi_kol_analysis_precision_eval", MODULE_PATH)
if SPEC is None or SPEC.loader is None:
    raise RuntimeError(f"cannot load evaluator: {MODULE_PATH}")
EVALUATOR = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = EVALUATOR
SPEC.loader.exec_module(EVALUATOR)
EvaluationPolicy = EVALUATOR.EvaluationPolicy
evaluate_analysis_precision = EVALUATOR.evaluate_analysis_precision


def _load(path: Path) -> list[dict]:
    text = path.read_text(encoding="utf-8").strip()
    if not text:
        return []
    if path.suffix.lower() == ".jsonl":
        return [json.loads(line) for line in text.splitlines() if line.strip()]
    payload = json.loads(text)
    if isinstance(payload, dict):
        payload = payload.get("items") or payload.get("records") or []
    if not isinstance(payload, list):
        raise ValueError("input must be a JSON array, {items:[...]}, or JSONL")
    return payload


def main() -> int:
    parser = argparse.ArgumentParser(description="Label-backed KOL analysis precision evaluation")
    parser.add_argument("input", type=Path)
    parser.add_argument("--minimum-total", type=int, default=180)
    parser.add_argument("--minimum-per-task", type=int, default=180)
    parser.add_argument("--minimum-positive", type=int, default=30)
    parser.add_argument("--minimum-negative", type=int, default=30)
    parser.add_argument("--minimum-per-platform", type=int, default=60)
    parser.add_argument("--required-platform", action="append", default=[])
    args = parser.parse_args()
    required_platforms = args.required_platform or ["youtube", "instagram", "tiktok"]
    policy = EvaluationPolicy(
        minimum_total=max(1, args.minimum_total),
        minimum_per_task=max(1, args.minimum_per_task),
        minimum_positive=max(1, args.minimum_positive),
        minimum_negative=max(1, args.minimum_negative),
        minimum_per_platform=max(1, args.minimum_per_platform),
        required_platforms=tuple(sorted(set(required_platforms))),
    )
    report = evaluate_analysis_precision(_load(args.input), policy=policy)
    sys.stdout.write(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
