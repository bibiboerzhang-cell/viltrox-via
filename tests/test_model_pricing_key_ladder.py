"""core/model_pricing._pricing_key 子串梯契约(2026-08-22 模型升级刀)。

梯子顺序是成本台账的正确性边界:'gemini-3.5-flash' 是 'gemini-3.5-flash-lite' 的
子串,'gpt-5.6' 是 'gpt-5.6-luna' 的子串——更长的精确 id 必须先命中;未知家族的
兜底指向当前默认档而非退役模型。价格表三处镜像(model_pricing / runtime 目录 /
PROVIDER_CONFIG)由 tests/test_model_registry_defaults.py 比对,这里只锁梯子。
"""
from __future__ import annotations

import inspect

from app.core import model_pricing
from app.core.model_pricing import PRICING_USD_PER_1M_TOKENS, _pricing_key, estimate_cost_usd


def test_exact_keys_resolve_to_themselves() -> None:
    for model_id in PRICING_USD_PER_1M_TOKENS:
        assert _pricing_key(model_id) == model_id
        assert _pricing_key(model_id.upper()) == model_id


def test_lite_is_never_priced_as_full_flash() -> None:
    assert _pricing_key("gemini-3.5-flash-lite") == "gemini-3.5-flash-lite"
    assert _pricing_key("gemini-3.5-flash-lite-001") == "gemini-3.5-flash-lite"
    assert _pricing_key("gemini-3.5-flash-001") == "gemini-3.5-flash"
    assert estimate_cost_usd("gemini-3.5-flash-lite", 1_000_000, 1_000_000) == 2.8
    assert estimate_cost_usd("gemini-3.5-flash", 1_000_000, 1_000_000) == 10.5


def test_luna_is_never_priced_as_full_gpt56() -> None:
    assert _pricing_key("gpt-5.6-luna") == "gpt-5.6-luna"
    assert _pricing_key("gpt-5.6-luna-2026-09-01") == "gpt-5.6-luna"
    assert _pricing_key("gpt-5.6-2026-09-01") == "gpt-5.6"
    assert estimate_cost_usd("gpt-5.6-luna", 1_000_000, 1_000_000) == 1.4
    assert estimate_cost_usd("gpt-5.6", 1_000_000, 1_000_000) == 35.0


def test_dated_snapshots_fold_into_their_family() -> None:
    assert _pricing_key("gemini-3.6-flash-001") == "gemini-3.6-flash"
    assert _pricing_key("claude-sonnet-5-20260901") == "claude-sonnet-5"
    assert _pricing_key("claude-opus-5-20260901") == "claude-opus-5"
    assert _pricing_key("claude-haiku-4-5-20251001") == "claude-haiku-4-5"


def test_unknown_families_fall_back_to_current_defaults_not_retired_ids() -> None:
    assert _pricing_key("gemini-9-flash") == "gemini-3.6-flash"
    assert _pricing_key("gemini-9-pro") == "gemini-2.5-pro"
    assert _pricing_key("gemini-9-ultra") == "gemini-3.6-flash"
    assert _pricing_key("gpt-9") == "gpt-5.6-luna"
    assert _pricing_key("o4-mini-high") == "gpt-5.6-luna"
    assert _pricing_key("claude-9") == "claude-sonnet-5"
    assert _pricing_key("claude-opus-9") == "claude-opus-5"
    assert _pricing_key("something-else") == "claude-sonnet-5"
    # 兜底永不落 0 价
    assert estimate_cost_usd("something-else", 1_000_000) > 0


def test_frozen_price_table_2026_08_22() -> None:
    expected = {
        "gemini-3.6-flash": (0.75, 3.75),
        "gemini-3.5-flash": (1.50, 9.0),
        "gemini-3.5-flash-lite": (0.30, 2.50),
        "gemini-2.5-flash": (0.30, 2.50),
        "gemini-2.5-pro": (1.25, 10.0),
        "claude-sonnet-5": (2.0, 10.0),
        "claude-opus-5": (5.0, 25.0),
        "claude-haiku-4-5": (1.0, 5.0),
        "gpt-5.6-luna": (0.20, 1.20),
        "gpt-5.6": (5.0, 30.0),
        "gpt-5.5": (5.0, 30.0),
    }
    for model_id, (usd_in, usd_out) in expected.items():
        row = PRICING_USD_PER_1M_TOKENS[model_id]
        assert (row["input"], row["output"]) == (usd_in, usd_out), model_id
    # 3.7-flash 无 minimal 思考档,禁止登记
    assert "gemini-3.7-flash" not in PRICING_USD_PER_1M_TOKENS


def test_promo_row_carries_its_expiry_note() -> None:
    source = inspect.getsource(model_pricing)
    assert "2026-12-31" in source  # gemini-3.6-flash 促销到期提醒
    assert "2026-10-15" in source  # haiku 退役提醒


def test_decision_ledger_rates_mirror_model_pricing() -> None:
    from app.services.via.decision_ledger import _MODEL_COST_RATES

    for model_id, (per_in, per_out) in _MODEL_COST_RATES.items():
        usd = PRICING_USD_PER_1M_TOKENS.get(_pricing_key(model_id))
        assert usd is not None, model_id
        if _pricing_key(model_id) != model_id:
            continue  # 家族折叠行(如 gemini-2.5-flash-lite)不要求逐字相等
        assert abs(per_in * 1_000_000 - usd["input"]) < 1e-9, model_id
        assert abs(per_out * 1_000_000 - usd["output"]) < 1e-9, model_id
