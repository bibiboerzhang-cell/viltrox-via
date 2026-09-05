"""Bounded deterministic campaign draft normalization; no financial approval.

Cents are integers within JSON's exact-integer range. Unknown budget is None,
never zero. Model allocations are proposals, not invoices or actual usage.
"""
from __future__ import annotations

from copy import deepcopy
from decimal import Decimal
import math
from typing import Any

MAX_CENTS = 9_007_199_254_740_991
MAX_ROWS = 32
SHARE_SUM_EPSILON = 1e-9


def exact_nonnegative_int(value: Any, *, maximum: int = MAX_CENTS) -> int | None:
    if type(value) is int:
        return value if 0 <= value <= maximum else None
    if isinstance(value, str):
        text = value.strip()
        if text.isascii() and text.isdigit() and len(text) <= 16:
            number = int(text)
            return number if number <= maximum else None
    return None


def parse_budget(value: Any) -> tuple[int | None, str]:
    if value is None or (isinstance(value, str) and not value.strip()):
        return None, "missing"
    amount = exact_nonnegative_int(value)
    return amount, "valid" if amount is not None else "invalid"


def rule_budget_allocation(budget: int | None, spec: list[tuple[str, float]]) -> list[dict[str, Any]]:
    """Largest remainder in integer cents; stable input order breaks ties."""
    weights = [int(Decimal(str(pct)) * 100) for _, pct in spec]
    denominator = sum(weights)
    amounts = [None] * len(spec)
    if budget is not None:
        amounts = [(budget * weight) // denominator for weight in weights]
        remainder = budget - sum(amounts)
        order = sorted(range(len(weights)), key=lambda i: (-(budget * weights[i] % denominator), i))
        for index in order[:remainder]:
            amounts[index] += 1
    return [{"bucket": bucket, "pct": pct, "amount_cents": amounts[index]}
            for index, (bucket, pct) in enumerate(spec)]


def _text(value: Any, limit: int = 1000) -> str | None:
    if not isinstance(value, str) or not value.strip() or len(value) > limit:
        return None
    return value.strip()


def _rows(value: Any) -> bool:
    return isinstance(value, list) and 0 < len(value) <= MAX_ROWS and all(isinstance(row, dict) for row in value)


def _model_budget(value: Any, budget: int) -> tuple[list[dict[str, Any]] | None, str | None]:
    if not _rows(value):
        return None, "model_budget_invalid"
    result = []
    seen = set()
    for row in value:
        bucket = _text(row.get("bucket"), 64)
        amount = exact_nonnegative_int(row.get("amount_cents"))
        if not bucket or bucket in seen or amount is None:
            return None, "model_budget_invalid"
        seen.add(bucket)
        result.append({"bucket": bucket, "amount_cents": amount, "pct": round(amount / budget, 8)})
    if sum(row["amount_cents"] for row in result) > budget:
        return None, "model_budget_over_limit"
    return result, None


def _pool_candidates(pool_items: list[dict[str, Any]]) -> dict[int, dict[str, Any]]:
    candidates = {}
    for row in pool_items:
        identity = exact_nonnegative_int(row.get("id"))
        if identity:
            candidates[identity] = {
                "id": row.get("id"),
                "handle": row.get("handle") or row.get("username") or row.get("name") or row.get("display_name"),
                "platform": row.get("platform"),
                "fit": row.get("viltrox_fit_score") if "viltrox_fit_score" in row else row.get("fit_score"),
            }
    return candidates


def _samples(value: Any, candidates: dict[int, dict[str, Any]]) -> tuple[list[dict[str, Any]], bool]:
    if not isinstance(value, list) or len(value) > MAX_ROWS:
        return [], True
    output = []
    rejected = False
    seen = set()
    for row in value:
        identity = exact_nonnegative_int(row.get("id")) if isinstance(row, dict) else None
        if identity not in candidates or identity in seen:
            rejected = True
            continue
        seen.add(identity)
        output.append(deepcopy(candidates[identity]))
    return output, rejected


def _model_mix(value: Any, candidates: dict[int, dict[str, Any]]) -> tuple[list[dict[str, Any]] | None, bool]:
    if not _rows(value):
        return None, False
    result = []
    rejected = False
    seen_tiers = set()
    for row in value:
        tier = row.get("tier")
        count = exact_nonnegative_int(row.get("count"), maximum=10_000)
        share = row.get("share")
        if not isinstance(tier, str) or tier not in {"mega", "mid", "micro", "nano"} or count is None:
            return None, rejected
        if tier in seen_tiers:
            return None, rejected
        seen_tiers.add(tier)
        if type(share) not in (int, float) or not 0 <= share <= 1 or not math.isfinite(share):
            return None, rejected
        samples, invalid = _samples(row.get("sample_creators"), candidates)
        rejected = rejected or invalid
        result.append({"tier": tier, "count": count, "share": share, "sample_creators": samples[:count]})
    # Stable tolerance permits floating-point representation, not over-allocation.
    if abs(math.fsum(row["share"] for row in result) - 1.0) > SHARE_SUM_EPSILON:
        return None, rejected
    return result, rejected


def _model_text_rows(value: Any, *, timeline: bool) -> list[dict[str, Any]] | None:
    if not _rows(value):
        return None
    fields = ("phase", "focus") if timeline else ("angle", "why", "market_signal")
    result = []
    for row in value:
        normalized = {field: _text(row.get(field)) for field in fields}
        if any(text is None for text in normalized.values()):
            return None
        if timeline:
            week = exact_nonnegative_int(row.get("week"), maximum=520)
            if not week:
                return None
            normalized["week"] = week
        result.append(normalized)
    return result


def normalize_strategy(strategy: dict[str, Any] | None, fallback: dict[str, Any], *,
                       budget: int | None, pool_items: list[dict[str, Any]]) -> tuple[dict[str, Any], str, list[str], list[str]]:
    """Only accept known fields and replace malformed fields, not the whole plan."""
    result = deepcopy(fallback)
    warnings: list[str] = []
    budget_warnings: list[str] = []
    allocation_status = "unavailable" if budget is None else "rule"
    if strategy is None:
        return result, allocation_status, budget_warnings, warnings
    if budget is not None and budget > 0:
        allocation, problem = _model_budget(strategy.get("budget_allocation"), budget)
        allocation_status = "rule_fallback" if problem else "model_validated"
        if problem:
            budget_warnings.append(problem)
        else:
            result["budget_allocation"] = allocation
            budget_warnings.append("model_budget_pct_recomputed")
    mix, rejected = _model_mix(strategy.get("creator_mix"), _pool_candidates(pool_items))
    if mix is None:
        warnings.append("model_creator_mix_invalid")
    else:
        result["creator_mix"] = mix
    if rejected:
        warnings.append("model_candidates_rejected")
    for key in ("timeline", "content_angles"):
        rows = _model_text_rows(strategy.get(key), timeline=key == "timeline")
        if rows is None:
            warnings.append(f"model_{key}_invalid")
        else:
            result[key] = rows
    return result, allocation_status, budget_warnings, warnings
