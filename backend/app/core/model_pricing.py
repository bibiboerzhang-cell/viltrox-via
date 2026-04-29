"""
core/model_pricing.py — estimated USD pricing per 1M tokens.

Values are local estimates for SystemTab cost visibility and should be checked
monthly against provider billing exports.
"""
from __future__ import annotations

PRICING_USD_PER_1M_TOKENS = {
    "claude-opus-4-7": {"input": 15.0, "output": 75.0},
    "claude-sonnet-4-6": {"input": 3.0, "output": 15.0},
    "claude-haiku-4-5": {"input": 1.0, "output": 5.0},
    "gpt-4o": {"input": 2.5, "output": 10.0},
    "gpt-4o-mini": {"input": 0.15, "output": 0.60},
    "gemini-2.5-flash": {"input": 0.075, "output": 0.30},
    "gemini-2.5-pro": {"input": 1.25, "output": 5.0},
}


def estimate_cost_usd(model: str, tokens_in: int = 0, tokens_out: int = 0) -> float:
    pricing = PRICING_USD_PER_1M_TOKENS.get(str(model or ""), {"input": 0.0, "output": 0.0})
    return round(
        (max(0, int(tokens_in or 0)) / 1_000_000 * float(pricing["input"]))
        + (max(0, int(tokens_out or 0)) / 1_000_000 * float(pricing["output"])),
        8,
    )
