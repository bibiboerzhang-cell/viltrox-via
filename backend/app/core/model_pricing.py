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


def _pricing_key(model: str) -> str:
    raw = str(model or "").strip().lower()
    if raw in PRICING_USD_PER_1M_TOKENS:
        return raw
    if "gpt-4o-mini" in raw:
        return "gpt-4o-mini"
    if "gpt-4o" in raw:
        return "gpt-4o"
    if "gemini" in raw and "pro" in raw:
        return "gemini-2.5-pro"
    if "gemini" in raw and "flash" in raw:
        return "gemini-2.5-flash"
    if "opus" in raw:
        return "claude-opus-4-7"
    if "haiku" in raw:
        return "claude-haiku-4-5"
    if "sonnet" in raw or "claude" in raw:
        return "claude-sonnet-4-6"
    return raw


def estimate_cost_usd(model: str, tokens_in: int = 0, tokens_out: int = 0) -> float:
    pricing = PRICING_USD_PER_1M_TOKENS.get(_pricing_key(model), {"input": 0.0, "output": 0.0})
    return round(
        (max(0, int(tokens_in or 0)) / 1_000_000 * float(pricing["input"]))
        + (max(0, int(tokens_out or 0)) / 1_000_000 * float(pricing["output"])),
        8,
    )
