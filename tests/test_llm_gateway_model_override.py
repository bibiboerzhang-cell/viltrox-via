"""Offline contracts for exact-model propagation through the LLM gateway."""

from __future__ import annotations

from typing import Any


def _authorize_readiness(monkeypatch, gateway, *bindings: str) -> None:
    allowed = set(bindings)
    monkeypatch.setattr(
        gateway,
        "exact_binding_readiness_from_environment",
        lambda binding: (
            {"binding": binding, "production_ready": binding in allowed},
            {"source": "test_signed_readiness_fixture"},
        ),
    )


def test_provider_adapters_use_explicit_model_without_network(monkeypatch) -> None:
    import app.platform.llm_gateway as gateway
    from app.platform import llm_gateway_providers as providers

    monkeypatch.setattr(providers, "_get_api_key", lambda _provider: "test-key")
    requests: list[tuple[str, dict[str, Any]]] = []

    def fake_request(url: str, payload: dict[str, Any], _headers: dict[str, str], _timeout: int) -> dict[str, Any]:
        requests.append((url, payload))
        if "/responses" in url:
            return {"model": payload["model"], "output_text": "ok", "usage": {}}
        if ":generateContent" in url:
            return {"candidates": [{"content": {"parts": [{"text": "ok"}]}}], "usageMetadata": {}}
        return {"model": payload["model"], "content": [{"type": "text", "text": "ok"}], "usage": {}}

    monkeypatch.setattr(providers, "_request_json", fake_request)

    assert providers._call_openai("x", 16, model_override="gpt-exact")["model"] == "gpt-exact"
    assert providers._call_google("x", 16, model_override="gemini-exact")["model"] == "gemini-exact"
    assert providers._call_anthropic("x", 16, model_override="claude-exact")["model"] == "claude-exact"

    assert requests[0][1]["model"] == "gpt-exact"
    assert "models/gemini-exact:generateContent" in requests[1][0]
    assert requests[2][1]["model"] == "claude-exact"
    # Importing the main gateway remains the public seam; this assertion also
    # guards the re-export contract used by existing tests/callers.
    assert gateway._call_openai is providers._call_openai


def test_openai_gpt5_omits_incompatible_temperature(monkeypatch) -> None:
    import app.platform.llm_gateway  # noqa: F401
    from app.platform import llm_gateway_providers as providers

    monkeypatch.setattr(providers, "_get_api_key", lambda _provider: "test-key")
    requests: list[dict[str, Any]] = []

    def fake_request(
        _url: str,
        payload: dict[str, Any],
        _headers: dict[str, str],
        _timeout: int,
    ) -> dict[str, Any]:
        requests.append(payload)
        return {"model": payload["model"], "output_text": "ok", "usage": {}}

    monkeypatch.setattr(providers, "_request_json", fake_request)

    assert providers._call_openai("x", 32, model_override="gpt-5.5")["status"] == "success"
    assert "temperature" not in requests[-1]
    assert providers._call_openai("x", 32, model_override="gpt-4o")["status"] == "success"
    assert requests[-1]["temperature"] == 0.2


def test_openai_reasoning_effort_is_keyed_by_exact_model_id(monkeypatch) -> None:
    """gpt-5.6-luna 必须 reasoning.effort='none'(目录实测);gpt-5.6 / gpt-5.5 /
    gpt-4o 保持 provider 默认(不按前缀泄漏);env JSON 可按精确 id 覆盖。"""
    import app.platform.llm_gateway  # noqa: F401
    from app.platform import llm_gateway_providers as providers

    monkeypatch.setattr(providers, "_get_api_key", lambda _provider: "test-key")
    monkeypatch.delenv("VKPI_OPENAI_REASONING_EFFORT_JSON", raising=False)
    requests: list[dict[str, Any]] = []

    def fake_request(_url, payload, _headers, _timeout):
        requests.append(payload)
        return {"model": payload["model"], "output_text": "ok", "usage": {}}

    monkeypatch.setattr(providers, "_request_json", fake_request)

    assert providers._call_openai("x", 32, model_override="gpt-5.6-luna")["status"] == "success"
    assert requests[-1]["reasoning"] == {"effort": "none"}
    assert "temperature" not in requests[-1]
    for model in ("gpt-5.6", "gpt-5.5", "gpt-4o", "gpt-5.6-luna-preview"):
        assert providers._call_openai("x", 32, model_override=model)["status"] == "success"
        assert "reasoning" not in requests[-1], model

    # env 覆盖:加新 id、改已有 id、空值=撤销注入
    monkeypatch.setenv(
        "VKPI_OPENAI_REASONING_EFFORT_JSON",
        '{"gpt-5.6": "low", "gpt-5.6-luna": ""}',
    )
    providers._call_openai("x", 32, model_override="gpt-5.6")
    assert requests[-1]["reasoning"] == {"effort": "low"}
    providers._call_openai("x", 32, model_override="gpt-5.6-luna")
    assert "reasoning" not in requests[-1]

    # 坏 JSON 不炸:回落内置表
    monkeypatch.setenv("VKPI_OPENAI_REASONING_EFFORT_JSON", "{not json")
    providers._call_openai("x", 32, model_override="gpt-5.6-luna")
    assert requests[-1]["reasoning"] == {"effort": "none"}


def test_invoke_exact_override_without_exact_fallback_never_uses_global_default(monkeypatch) -> None:
    from app.platform import llm_gateway as gateway

    calls: list[tuple[str, str | None]] = []

    def openai(_prompt: str, _tokens: int, *, model_override: str | None = None) -> dict[str, Any]:
        calls.append(("openai", model_override))
        return {"status": "failed", "provider": "openai", "error": "fixture"}

    def google(_prompt: str, _tokens: int) -> dict[str, Any]:
        calls.append(("google", None))
        raise AssertionError("an exact request must not fall into a global provider default")

    monkeypatch.setattr(gateway, "_is_provider_configured", lambda provider: provider in {"openai", "google"})
    monkeypatch.setattr(gateway, "_budget_allows_provider", lambda *_args, **_kwargs: (True, []))
    monkeypatch.setattr(gateway, "record_call", lambda **_kwargs: None)
    monkeypatch.setenv("VKPI_LLM_RUNTIME_VERIFIED_MODELS", "openai/gpt-5.4-mini")
    _authorize_readiness(monkeypatch, gateway, "openai/gpt-5.4-mini")
    monkeypatch.setitem(gateway._PROVIDER_CALLERS, "openai", openai)
    monkeypatch.setitem(gateway._PROVIDER_CALLERS, "google", google)

    result = gateway.invoke(
        "hello",
        preferred_provider="openai",
        model_override="gpt-5.4-mini",
        skip_budget_check=True,
    )

    assert calls == [("openai", "gpt-5.4-mini")]
    assert result["provider"] == "rule_v0"
    assert result["reason"] == "all_providers_failed"


def test_invoke_without_override_keeps_two_argument_caller_contract(monkeypatch) -> None:
    from app.platform import llm_gateway as gateway

    calls: list[tuple[str, int]] = []

    def openai(prompt: str, tokens: int) -> dict[str, Any]:
        calls.append((prompt, tokens))
        return {"status": "success", "provider": "openai", "model": "configured-default", "text": "ok"}

    monkeypatch.setattr(gateway, "_is_provider_configured", lambda provider: provider == "openai")
    monkeypatch.setattr(gateway, "_budget_allows_provider", lambda *_args, **_kwargs: (True, []))
    monkeypatch.setattr(gateway, "record_call", lambda **_kwargs: None)
    monkeypatch.setitem(gateway._PROVIDER_CALLERS, "openai", openai)
    monkeypatch.setenv(
        "VKPI_LLM_RUNTIME_VERIFIED_MODELS",
        f"openai/{gateway.PROVIDER_CONFIG['openai']['model']}",
    )
    _authorize_readiness(
        monkeypatch,
        gateway,
        f"openai/{gateway.PROVIDER_CONFIG['openai']['model']}",
    )

    result = gateway.invoke("hello", preferred_provider="openai", skip_budget_check=True)

    assert calls == [("hello", 800)]
    assert result["model"] == "configured-default"


def test_gateway_split_keeps_public_reexports_and_chat_monkeypatch_seam(
    monkeypatch,
) -> None:
    from app.platform import llm_gateway as gateway
    from app.platform import llm_gateway_facade, llm_gateway_ledger

    assert gateway.record_call is llm_gateway_ledger.record_call
    assert gateway.chat is llm_gateway_facade.chat
    assert gateway.score is llm_gateway_facade.score
    assert gateway.stats is llm_gateway_facade.stats

    captured: dict[str, Any] = {}

    def fake_invoke(prompt: str, **kwargs):
        captured.update({"prompt": prompt, **kwargs})
        return {"status": "success", "text": "ok"}

    monkeypatch.setattr(gateway, "invoke", fake_invoke)
    result = gateway.chat(
        [{"role": "user", "content": "hello"}],
        purpose="split-compat",
    )

    assert result["status"] == "success"
    assert captured["prompt"] == "user: hello"
    assert captured["purpose"] == "split-compat"
