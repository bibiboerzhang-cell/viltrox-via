"""Truth-gated rollups for prediction evaluations.

Raw metrics remain useful diagnostics, but claimable metrics require one
immutable, human-verified evaluation per distinct business outcome.  Binary
outreach probabilities additionally require the due-run coverage gate.
"""
from __future__ import annotations

import math
from typing import Any

MIN_BINARY_CLAIMABLE_EVALS = 50


def _number(value: Any) -> float | None:
    if isinstance(value, bool) or value in (None, ""):
        return None
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    return parsed if math.isfinite(parsed) else None


def _boolean(value: Any) -> bool | None:
    if isinstance(value, bool):
        return value
    if isinstance(value, int) and value in (0, 1):
        return bool(value)
    text = str(value or "").strip().lower()
    if text in {"true", "t", "1", "yes"}:
        return True
    if text in {"false", "f", "0", "no"}:
        return False
    return None


def binary_brier_rollup(
    rows: list[dict[str, Any]], *, outreach_coverage: dict[str, Any] | None,
) -> dict[str, Any]:
    """Return Brier score for one verified Bernoulli actual per outcome."""
    binary_rows = [
        row for row in rows
        if str(row.get("task_type") or "") == "kol_outreach_reply_probability"
    ]
    groups: dict[str, list[dict[str, Any]]] = {}
    run_counts: dict[str, int] = {}
    invalid_n = 0
    for row in binary_rows:
        run_id = str(row.get("run_id") or "").strip()
        outcome_id = str(row.get("outcome_id") or "").strip()
        if not run_id or not outcome_id:
            invalid_n += 1
            continue
        groups.setdefault(outcome_id, []).append(row)
        run_counts[run_id] = run_counts.get(run_id, 0) + 1
    squared_errors: list[float] = []
    for grouped in groups.values():
        if len(grouped) != 1:
            invalid_n += len(grouped)
            continue
        row = grouped[0]
        if run_counts.get(str(row.get("run_id") or "").strip()) != 1:
            invalid_n += 1
            continue
        probability = _number(row.get("p50"))
        actual = _number(row.get("actual_value"))
        if (
            probability is None or not 0.0 <= probability <= 1.0
            or actual not in {0.0, 1.0}
            or _boolean(row.get("verified_actual")) is not True
        ):
            invalid_n += 1
            continue
        squared_errors.append((probability - actual) ** 2)
    coverage = outreach_coverage or {}
    coverage_claimable = bool(coverage.get("claimable"))
    n = len(squared_errors)
    score = round(sum(squared_errors) / n, 6) if n else None
    claimable = n >= MIN_BINARY_CLAIMABLE_EVALS and coverage_claimable
    return {
        "task_type": "kol_outreach_reply_probability",
        "metric": "brier_score",
        "brier_score": score,
        "n": n,
        "invalid_n": invalid_n,
        "coverage": coverage,
        "coverage_claimable": coverage_claimable,
        "minimum_verified_outcomes": MIN_BINARY_CLAIMABLE_EVALS,
        "claimable": claimable,
        "claim_level": "validated" if claimable else "descriptive_only",
    }


def verified_nonbinary_rollup(
    rows: list[dict[str, Any]], *, minimum: int,
) -> dict[str, Any]:
    """Aggregate one verified row per distinct outcome; reject duplicates."""
    groups: dict[str, list[dict[str, Any]]] = {}
    invalid_n = 0
    for row in rows:
        if str(row.get("task_type") or "") == "kol_outreach_reply_probability":
            continue
        outcome_id = str(row.get("outcome_id") or "").strip()
        if not outcome_id or _boolean(row.get("verified_actual")) is not True:
            invalid_n += 1
            continue
        groups.setdefault(outcome_id, []).append(row)

    verified: list[dict[str, Any]] = []
    for grouped in groups.values():
        if len(grouped) != 1:
            invalid_n += len(grouped)
            continue
        row = grouped[0]
        if _number(row.get("actual_value")) is None or _number(row.get("error_abs")) is None:
            invalid_n += 1
            continue
        verified.append(row)

    err_sum = sum(abs(_number(row.get("error_abs")) or 0.0) for row in verified)
    act_sum = sum(abs(_number(row.get("actual_value")) or 0.0) for row in verified)
    interval = [
        value for value in (_boolean(row.get("interval_hit")) for row in verified)
        if value is not None
    ]
    direction = [
        value for value in (_boolean(row.get("direction_hit")) for row in verified)
        if value is not None
    ]
    n = len(verified)
    claimable = n >= minimum
    return {
        "n": n,
        "invalid_n": invalid_n,
        "wape": round(err_sum / act_sum, 4) if n and act_sum > 0 else None,
        "interval_coverage": round(sum(interval) / len(interval), 4) if interval else None,
        "direction_hit_rate": round(sum(direction) / len(direction), 4) if direction else None,
        "interval_n": len(interval),
        "direction_n": len(direction),
        "claimable": claimable,
    }


__all__ = [
    "MIN_BINARY_CLAIMABLE_EVALS", "binary_brier_rollup", "verified_nonbinary_rollup",
]
