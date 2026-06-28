"""锁定 LLM 成本/定价核算行为(从 apify_jobs_worker 抽出后的首批单测)。

给定 provider usage_metadata + model → 断言美元成本,防定价被改坏而无告警。
"""
from app.workers.apify_jobs_cost import (
    _anthropic_cost,
    _gemini_cost,
    _gemini_input_cost_usd,
    _gemini_output_rate_usd_per_mtok,
    _openai_cost,
    _usage_count,
)


def test_usage_count_picks_first_present_key():
    assert _usage_count({"a": None, "b": 5}, "a", "b") == 5
    assert _usage_count({"prompt_token_count": "12"}, "prompt_token_count") == 12
    assert _usage_count({}, "x", "y") == 0


def test_openai_gpt55_rate():
    # gpt-5.5: input 5/Mtok, output 30/Mtok
    r = _openai_cost({"model": "gpt-5.5", "usage_metadata": {"input_tokens": 1000, "output_tokens": 500}}, 0.0)
    assert r[0] == round((1000 * 5.0 + 500 * 30.0) / 1_000_000, 6)  # 0.02
    assert r[2] == 1000 and r[3] == 500


def test_anthropic_opus_rate():
    # opus: input 15/Mtok, output 75/Mtok
    r = _anthropic_cost({"model": "claude-opus", "usage_metadata": {"input_tokens": 1000, "output_tokens": 500}}, 0.0)
    assert r[0] == round((1000 * 15.0 + 500 * 75.0) / 1_000_000, 6)  # 0.0525


def test_gemini_31_pro_under_and_over_200k():
    # <=200k: input 2/Mtok, output 12/Mtok
    under = _gemini_cost(
        {"model": "gemini-3.1-pro", "usage_metadata": {"prompt_token_count": 1000, "candidates_token_count": 500}}, 0.0)
    assert under[0] == round(1000 * 2.0 / 1_000_000 + 500 * 12.0 / 1_000_000, 6)  # 0.008
    # >200k: input 4/Mtok, output 18/Mtok
    over = _gemini_cost(
        {"model": "gemini-3.1-pro", "usage_metadata": {"prompt_token_count": 300_000, "candidates_token_count": 1000}}, 0.0)
    assert over[0] == round(300_000 * 4.0 / 1_000_000 + 1000 * 18.0 / 1_000_000, 6)  # 1.218


def test_gemini_output_rate_tiers():
    assert _gemini_output_rate_usd_per_mtok("gemini-3.1-pro", 1000) == 12.0
    assert _gemini_output_rate_usd_per_mtok("gemini-3.1-pro", 300_000) == 18.0
    assert _gemini_output_rate_usd_per_mtok("gemini-3-flash", 1000) == 3.0
    assert _gemini_output_rate_usd_per_mtok("gemini-2.5-flash", 1000) == 2.50


def test_gemini_flash_audio_modality_split():
    # gemini-2.5-flash: non-audio 0.30/Mtok, audio 1.00/Mtok
    meta = {"prompt_tokens_details": [{"modality": "AUDIO", "token_count": 400}]}
    cost = _gemini_input_cost_usd("gemini-2.5-flash", meta, 1000)
    assert cost == round((600 * 0.30 + 400 * 1.00) / 1_000_000, 12) or abs(cost - (600 * 0.30 + 400 * 1.00) / 1_000_000) < 1e-12


def test_fallback_when_no_usage_metadata():
    r = _openai_cost({"model": "gpt-4o-mini"}, 0.0123)
    assert r[0] == round(0.0123, 6) and r[1] == "llm_gateway_budget_preflight"
