"""LLM 成本核算与定价(从 apify_jobs_worker.py 抽出,行为不变)。

纯计算:给定 provider usage_metadata + model → 美元成本。无 conn/无 IO/无可变全局,
只读 llm_gateway.PROVIDER_CONFIG。被 apify_jobs_worker barrel re-export 回灌,调用点不变。
红线:纯成本核算,零触 viltrox_fit_score。
"""
from __future__ import annotations

from typing import Any

from app.platform import llm_gateway
from app.workers.apify_jobs_worker_helpers import _int_or_none


def _usage_count(metadata: dict[str, Any], *keys: str) -> int:
    for key in keys:
        value = metadata.get(key)
        if value is not None:
            parsed = _int_or_none(value)
            return parsed or 0
    return 0


def _gemini_input_cost_usd(model: str, metadata: dict[str, Any], tokens_in: int) -> float:
    model_key = str(model or "").lower()
    cached_tokens = _usage_count(metadata, "cached_content_token_count", "cachedContentTokenCount")
    uncached_tokens = max(0, tokens_in - cached_tokens)
    details = metadata.get("prompt_tokens_details")
    if not isinstance(details, list):
        details = []
    modality_counts: dict[str, int] = {}
    for item in details:
        if not isinstance(item, dict):
            continue
        modality = str(item.get("modality") or "").upper()
        modality_counts[modality] = modality_counts.get(modality, 0) + (_int_or_none(item.get("token_count")) or 0)
    if "gemini-3.1-pro" in model_key:
        rate = 4.0 if tokens_in > 200_000 else 2.0
        cached_rate = 0.40 if tokens_in > 200_000 else 0.20
        return ((uncached_tokens * rate) + (cached_tokens * cached_rate)) / 1_000_000
    if "gemini-3-flash" in model_key:
        audio = modality_counts.get("AUDIO", 0)
        return ((tokens_in - audio) * 0.50 + audio * 1.00) / 1_000_000
    if "gemini-2.5-flash" in model_key:
        audio = modality_counts.get("AUDIO", 0)
        return ((tokens_in - audio) * 0.30 + audio * 1.00) / 1_000_000
    config = llm_gateway.PROVIDER_CONFIG.get("google") or {}
    return tokens_in * float(config.get("input_cents_per_million") or 0) / 100_000_000


def _gemini_output_rate_usd_per_mtok(model: str, tokens_in: int) -> float:
    model_key = str(model or "").lower()
    if "gemini-3.1-pro" in model_key:
        return 18.0 if tokens_in > 200_000 else 12.0
    if "gemini-3-flash" in model_key:
        return 3.0
    if "gemini-2.5-flash" in model_key:
        return 2.50
    config = llm_gateway.PROVIDER_CONFIG.get("google") or {}
    return float(config.get("output_cents_per_million") or 0) / 100


def _gemini_cost(result: dict[str, Any], fallback_cost: float) -> tuple[float, str, int, int]:
    metadata = result.get("usage_metadata") if isinstance(result.get("usage_metadata"), dict) else {}
    tokens_in = _usage_count(metadata, "prompt_token_count", "promptTokenCount")
    tokens_out = _usage_count(metadata, "candidates_token_count", "candidatesTokenCount")
    tokens_out += _usage_count(metadata, "thoughts_token_count", "thoughtsTokenCount")
    if tokens_in or tokens_out:
        model = str(result.get("model") or result.get("method") or "")
        cost = _gemini_input_cost_usd(model, metadata, tokens_in)
        cost += tokens_out * _gemini_output_rate_usd_per_mtok(model, tokens_in) / 1_000_000
        return round(max(0.0, cost), 6), "gemini_usage_metadata_model_rate", tokens_in, tokens_out
    return round(max(0.0, float(fallback_cost or 0.0)), 6), "llm_gateway_budget_preflight", 0, 0


def _openai_cost(result: dict[str, Any], fallback_cost: float) -> tuple[float, str, int, int]:
    metadata = result.get("usage_metadata") if isinstance(result.get("usage_metadata"), dict) else {}
    tokens_in = _usage_count(metadata, "input_tokens", "prompt_tokens")
    tokens_out = _usage_count(metadata, "output_tokens", "completion_tokens")
    if tokens_in or tokens_out:
        model = str(result.get("model") or result.get("method") or "").lower()
        if "gpt-5.5" in model:
            cost = (tokens_in * 5.0 + tokens_out * 30.0) / 1_000_000
        else:
            config = llm_gateway.PROVIDER_CONFIG.get("openai") or {}
            input_cents = float(config.get("input_cents_per_million") or 0)
            output_cents = float(config.get("output_cents_per_million") or 0)
            cost = ((tokens_in * input_cents) + (tokens_out * output_cents)) / 100_000_000
        return round(max(0.0, cost), 6), "openai_usage_metadata_model_rate", tokens_in, tokens_out
    return round(max(0.0, float(fallback_cost or 0.0)), 6), "llm_gateway_budget_preflight", 0, 0


def _anthropic_cost(result: dict[str, Any], fallback_cost: float) -> tuple[float, str, int, int]:
    metadata = result.get("usage_metadata") if isinstance(result.get("usage_metadata"), dict) else {}
    tokens_in = _usage_count(metadata, "input_tokens")
    tokens_out = _usage_count(metadata, "output_tokens")
    if tokens_in or tokens_out:
        model = str(result.get("model") or result.get("method") or "").lower()
        if "opus" in model:
            cost = (tokens_in * 15.0 + tokens_out * 75.0) / 1_000_000
        else:
            config = llm_gateway.PROVIDER_CONFIG.get("anthropic") or {}
            input_cents = float(config.get("input_cents_per_million") or 0)
            output_cents = float(config.get("output_cents_per_million") or 0)
            cost = ((tokens_in * input_cents) + (tokens_out * output_cents)) / 100_000_000
        return round(max(0.0, cost), 6), "anthropic_usage_metadata_model_rate", tokens_in, tokens_out
    return round(max(0.0, float(fallback_cost or 0.0)), 6), "llm_gateway_budget_preflight", 0, 0
