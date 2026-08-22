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
    r = _anthropic_cost({"model": "claude-opus-4-7", "usage_metadata": {"input_tokens": 1000, "output_tokens": 500}}, 0.0)
    assert r[0] == round((1000 * 5.0 + 500 * 25.0) / 1_000_000, 6)  # 0.0175


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


# ── 2026-08-22 模型升级刀:新默认模型精确分支(旧价位用例保留给历史台账行)──


def test_gemini_36_flash_rates_with_cache_and_audio_parity():
    # gemini-3.6-flash 促销价:输入 0.75(音频同价)、缓存 0.075、输出 3.75
    meta = {
        "cached_content_token_count": 200,
        "prompt_tokens_details": [{"modality": "AUDIO", "token_count": 400}],
    }
    cost = _gemini_input_cost_usd("gemini-3.6-flash", meta, 1000)
    assert abs(cost - (800 * 0.75 + 200 * 0.075) / 1_000_000) < 1e-12
    assert _gemini_output_rate_usd_per_mtok("gemini-3.6-flash", 1000) == 3.75
    r = _gemini_cost(
        {"model": "gemini-3.6-flash", "usage_metadata": {"prompt_token_count": 1000, "candidates_token_count": 500}},
        0.0,
    )
    assert r[0] == round((1000 * 0.75 + 500 * 3.75) / 1_000_000, 6)
    assert r[1] == "gemini_usage_metadata_model_rate"


def test_gemini_35_flash_lite_is_not_priced_as_35_flash():
    # 'gemini-3.5-flash' 是 'gemini-3.5-flash-lite' 的子串:精确分支必须先命中。
    meta = {"prompt_tokens_details": [{"modality": "AUDIO", "token_count": 400}]}
    assert abs(_gemini_input_cost_usd("gemini-3.5-flash-lite", meta, 1000) - 1000 * 0.30 / 1_000_000) < 1e-12
    assert _gemini_output_rate_usd_per_mtok("gemini-3.5-flash-lite", 1000) == 2.50
    # 3.5-flash 本身 1.50/9.00(显式分支,网关默认已是 3.6 不能再靠兜底),不能被 lite 分支截胡
    assert _gemini_output_rate_usd_per_mtok("gemini-3.5-flash", 1000) == 9.0


def test_gemini_3_flash_legacy_branch_does_not_capture_36_flash():
    assert _gemini_output_rate_usd_per_mtok("gemini-3-flash-preview", 1000) == 3.0
    assert _gemini_output_rate_usd_per_mtok("gemini-3.6-flash", 1000) == 3.75


def test_openai_luna_and_gpt56_rates():
    luna = _openai_cost({"model": "gpt-5.6-luna", "usage_metadata": {"input_tokens": 1000, "output_tokens": 500}}, 0.0)
    assert luna[0] == round((1000 * 0.20 + 500 * 1.20) / 1_000_000, 6)
    full = _openai_cost({"model": "gpt-5.6", "usage_metadata": {"input_tokens": 1000, "output_tokens": 500}}, 0.0)
    assert full[0] == round((1000 * 5.0 + 500 * 30.0) / 1_000_000, 6)


def test_anthropic_sonnet5_and_opus5_rates():
    sonnet = _anthropic_cost({"model": "claude-sonnet-5", "usage_metadata": {"input_tokens": 1000, "output_tokens": 500}}, 0.0)
    assert sonnet[0] == round((1000 * 2.0 + 500 * 10.0) / 1_000_000, 6)
    opus = _anthropic_cost({"model": "claude-opus-5", "usage_metadata": {"input_tokens": 1000, "output_tokens": 500}}, 0.0)
    assert opus[0] == round((1000 * 5.0 + 500 * 25.0) / 1_000_000, 6)
