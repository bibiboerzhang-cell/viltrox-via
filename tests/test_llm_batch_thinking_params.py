"""Anthropic Message Batches 请求参数 / 估价契约(2026-08-22 模型升级刀)。

- 每条 params 与同步路径同一份思考策略(默认 thinking disabled;env adaptive 可开),
  保证「batch 输出 == 同步输出」的模块契约;永不带 temperature/top_p/budget_tokens;
- 估价不再写死 Sonnet 4.6 的 3/15,改从 app.core.model_pricing 按 CLAUDE_MODEL 精确查,
  查不到按 claude-sonnet-5 正式价 2/10 兜底;Batch 折扣 0.5 不变。

零网络零真库:_batches_api / ensure_schema / get_conn 一并 patch。
"""
from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from app.platform import llm_batch  # noqa: E402


class _Batches:
    def __init__(self) -> None:
        self.requests: list[dict[str, Any]] = []

    def create(self, *, requests: list[dict[str, Any]]):
        self.requests = requests
        return SimpleNamespace(id="msgbatch_unit")


class _Conn:
    def __init__(self) -> None:
        self.executed: list[tuple[str, Any]] = []

    def execute(self, sql: str, params: Any = None):
        self.executed.append((sql, params))
        return self

    def commit(self) -> None:
        pass


def _clear_policy_env(monkeypatch) -> None:
    for name in ("VKPI_ANTHROPIC_THINKING", "VKPI_ANTHROPIC_EFFORT", "VKPI_ANTHROPIC_MAX_TOKENS"):
        monkeypatch.delenv(name, raising=False)


def _submit(monkeypatch, items: list[dict[str, Any]]) -> tuple[str | None, _Batches, _Conn]:
    batches = _Batches()
    conn = _Conn()
    monkeypatch.setattr(llm_batch, "_batches_api", lambda: batches)
    monkeypatch.setattr(llm_batch, "ensure_schema", lambda: None)
    monkeypatch.setattr(llm_batch, "get_conn", lambda: conn)
    monkeypatch.setitem(llm_batch._CONSUMERS, "unit_consumer", lambda results, request_map: {})
    batch_id = llm_batch.submit_anthropic_batch(
        items, consumer="unit_consumer", purpose="unit", cost_scope="unit:scope"
    )
    return batch_id, batches, conn


def test_batch_params_carry_thinking_disabled_and_no_sampling_params(monkeypatch):
    _clear_policy_env(monkeypatch)
    monkeypatch.setattr(llm_batch, "CLAUDE_MODEL", "claude-sonnet-5")
    batch_id, batches, conn = _submit(
        monkeypatch,
        [
            {"custom_id": "a", "prompt": "hello", "max_output_tokens": 512, "meta": {"k": 1}},
            {"custom_id": "b", "prompt": "world", "max_output_tokens": 99_999},
            {"custom_id": "", "prompt": "dropped"},
            {"custom_id": "c", "prompt": "   "},
        ],
    )
    assert batch_id == "msgbatch_unit"
    assert [req["custom_id"] for req in batches.requests] == ["a", "b"]
    for req in batches.requests:
        params = req["params"]
        assert params["model"] == "claude-sonnet-5"
        assert params["thinking"] == {"type": "disabled"}
        assert set(params) == {"model", "max_tokens", "messages", "thinking"}
        for forbidden in ("temperature", "top_p", "top_k", "budget_tokens", "output_config"):
            assert forbidden not in params
    assert batches.requests[0]["params"]["max_tokens"] == 512
    assert batches.requests[0]["params"]["messages"] == [{"role": "user", "content": "hello"}]
    assert batches.requests[1]["params"]["max_tokens"] == 4096  # 上限钳制不变
    assert conn.executed, "submitted batch must be persisted"


def test_batch_params_follow_adaptive_env_like_sync_path(monkeypatch):
    _clear_policy_env(monkeypatch)
    monkeypatch.setenv("VKPI_ANTHROPIC_THINKING", "adaptive")
    monkeypatch.setenv("VKPI_ANTHROPIC_EFFORT", "high")
    monkeypatch.setattr(llm_batch, "CLAUDE_MODEL", "claude-sonnet-5")
    _, batches, _ = _submit(monkeypatch, [{"custom_id": "a", "prompt": "x"}])
    params = batches.requests[0]["params"]
    assert params["thinking"] == {"type": "adaptive"}
    assert params["output_config"] == {"effort": "high"}
    assert "budget_tokens" not in params["thinking"]


def test_batch_price_derives_from_model_pricing_with_sonnet5_fallback(monkeypatch):
    from app.core import model_pricing

    monkeypatch.setattr(llm_batch, "CLAUDE_MODEL", "claude-sonnet-5")
    expected = model_pricing.PRICING_USD_PER_1M_TOKENS.get(
        "claude-sonnet-5", {"input": 2.0, "output": 10.0}
    )
    assert llm_batch._model_price() == (float(expected["input"]), float(expected["output"]))
    # 价格表里没有的 id → claude-sonnet-5 正式价兜底(不再是 Sonnet 4.6 的 3/15)
    monkeypatch.setattr(llm_batch, "CLAUDE_MODEL", "claude-unlisted-future")
    assert llm_batch._model_price() == (2.0, 10.0)
    # 旧模型钉回(env 保留退路)仍按它自己的真价
    monkeypatch.setattr(llm_batch, "CLAUDE_MODEL", "claude-sonnet-4-6")
    assert llm_batch._model_price() == (3.0, 15.0)
    assert llm_batch._BATCH_DISCOUNT == 0.5


def test_record_cost_applies_batch_discount_on_looked_up_price(monkeypatch):
    monkeypatch.setattr(llm_batch, "CLAUDE_MODEL", "claude-unlisted-future")
    recorded: list[dict[str, Any]] = []
    from app.domains.costs import budget_guard

    monkeypatch.setattr(budget_guard, "record_cost", lambda **kwargs: recorded.append(kwargs))
    cost = llm_batch._record_cost("unit:scope", 1_000_000, 100_000)
    assert cost == pytest.approx((2.0 + 1.0) * 0.5)
    assert recorded and recorded[0]["ai_provider"] == "anthropic"
    assert recorded[0]["cost_usd"] == pytest.approx(1.5)


def test_module_docstring_names_current_default_model():
    assert "claude-sonnet-5" in (llm_batch.__doc__ or "")
    assert "claude-sonnet-4-6" not in (llm_batch.__doc__ or "")
