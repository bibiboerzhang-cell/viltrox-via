"""就绪门操作员确认书(VKPI_LLM_READINESS_OPERATOR_ACK)契约。

证据管线(独立信任根+签名30例评测)交付前的唯一放行口:
- 默认(env 缺失)门保持完全 fail-closed —— readiness_not_production_ready;
- 操作员按精确绑定逐个点名才放行,不支持通配;
- 未点名的绑定照常被拦;
- 放行只是免除就绪证据要求,不改变 readiness 目录的 production_ready 口径。
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from app.platform import llm_gateway  # noqa: E402
from app.platform.models.runtime import resolve_model_binding  # noqa: E402

_BINDING = "google/gemini-3.5-flash"
_ACK_ENV = "VKPI_LLM_READINESS_OPERATOR_ACK"


def _blocker_for(binding_str: str) -> str:
    provider, _, model_id = binding_str.partition("/")
    resolved = resolve_model_binding(provider, model_id, runtime_availability={})
    return llm_gateway._binding_call_blocker(
        resolved,
        explicit_model=True,
        require_runtime_verified=True,
    )


def test_gate_fail_closed_by_default(monkeypatch):
    monkeypatch.delenv(_ACK_ENV, raising=False)
    monkeypatch.delenv("VKPI_LLM_READINESS_EVIDENCE_JSON", raising=False)
    assert _blocker_for(_BINDING) == "readiness_not_production_ready"


def test_operator_ack_clears_named_binding(monkeypatch):
    monkeypatch.setenv(_ACK_ENV, _BINDING)
    assert _blocker_for(_BINDING) == ""


def test_operator_ack_does_not_clear_unlisted_binding(monkeypatch):
    monkeypatch.setenv(_ACK_ENV, _BINDING)
    assert _blocker_for("openai/gpt-5.4-mini") == "readiness_not_production_ready"


def test_operator_ack_rejects_wildcard(monkeypatch):
    monkeypatch.setenv(_ACK_ENV, "*")
    assert _blocker_for(_BINDING) == "readiness_not_production_ready"


def test_operator_ack_normalises_provider_aliases(monkeypatch):
    monkeypatch.setenv(_ACK_ENV, "gemini/gemini-3.5-flash; claude/claude-sonnet-4-6")
    assert _blocker_for(_BINDING) == ""
    assert _blocker_for("anthropic/claude-sonnet-4-6") == ""


def test_operator_ack_does_not_touch_readiness_catalog(monkeypatch):
    monkeypatch.setenv(_ACK_ENV, _BINDING)
    from app.platform.models.readiness import exact_binding_readiness_from_environment

    item, _source = exact_binding_readiness_from_environment(_BINDING)
    assert item.get("production_ready") is False
