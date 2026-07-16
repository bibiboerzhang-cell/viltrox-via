from __future__ import annotations

import threading

import pytest

from app.api.routers import vkpi_intelligent


@pytest.fixture(autouse=True)
def _clear_ask_cache() -> None:
    with vkpi_intelligent._ASK_CACHE_LOCK:
        vkpi_intelligent._ASK_CACHE.clear()


def _search_answer() -> dict:
    return vkpi_intelligent._answer(
        answer="检索到 1 个候选。",
        mode="search",
        evidence=[{
            "kind": "search_results",
            "count": 1,
            "results": [{"id": 71, "handle": "proof_creator", "score": 0.93}],
        }],
        actions=[{"label": "查看", "route": "kol-pool"}],
    )


def _allow_budget(monkeypatch: pytest.MonkeyPatch) -> None:
    import app.domains.costs.budget_guard as budget_guard

    monkeypatch.setattr(budget_guard, "check_budget", lambda *args, **kwargs: True)


def test_synth_passes_search_evidence_and_deadline_without_background_thread(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _allow_budget(monkeypatch)
    import app.platform.llm_gateway as gateway

    captured: dict = {}

    def fake_invoke_json(prompt: str, **kwargs):
        captured["prompt"] = prompt
        captured["kwargs"] = kwargs
        return {
            "status": "success",
            "provider": "google",
            "model": "test-model",
            "json": {"answer": "证据显示应先联系 proof_creator。"},
            "fallback_used": False,
        }

    monkeypatch.setattr(gateway, "invoke_json", fake_invoke_json)

    class ForbiddenThread:
        def __init__(self, *args, **kwargs):
            raise AssertionError("synth must not create a background thread")

    monkeypatch.setattr(threading, "Thread", ForbiddenThread)
    out = vkpi_intelligent._try_synth("如何安排合作?", _search_answer(), {"id": 9})

    assert out["mode"] == "synth"
    assert out["status"] == "ready"
    assert out["fallback_used"] is False
    assert "proof_creator" in captured["prompt"]
    assert "检索证据" in captured["prompt"]
    assert captured["kwargs"]["deadline_seconds"] == vkpi_intelligent._SYNTH_TIMEOUT_S
    assert captured["kwargs"]["max_provider_attempts"] == 1
    assert captured["kwargs"]["staff"] == {"id": 9}
    assert captured["kwargs"]["required_keys"] == ("answer",)


@pytest.mark.parametrize(
    ("gateway_result", "expected_reason"),
    [
        (
            {
                "status": "fallback_to_rule",
                "provider": "rule_v0",
                "reason": "deadline_exceeded",
                "json": None,
            },
            "deadline_exceeded",
        ),
        (
            {
                "status": "fallback_to_rule",
                "provider": "rule_v0",
                "reason": "all_providers_failed",
                "json": None,
            },
            "rule_fallback",
        ),
        (
            {
                "status": "success",
                "provider": "google",
                "json": {"answer": ""},
            },
            "invalid_or_unavailable_result",
        ),
    ],
)
def test_synth_failures_are_explicit_degraded(
    monkeypatch: pytest.MonkeyPatch,
    gateway_result: dict,
    expected_reason: str,
) -> None:
    _allow_budget(monkeypatch)
    import app.platform.llm_gateway as gateway

    monkeypatch.setattr(gateway, "invoke_json", lambda *args, **kwargs: gateway_result)
    out = vkpi_intelligent._try_synth("请分析市场", _search_answer())

    assert out["mode"] == "degraded"
    assert out["status"] == "degraded"
    assert out["fallback_used"] is True
    assert out["degraded_reason"] == expected_reason
    assert out["answer"].endswith("检索到 1 个候选。")
    assert out["evidence"][-1] == {
        "kind": "synth_status",
        "status": "degraded",
        "fallback_used": True,
        "reason": expected_reason,
    }


def test_budget_failure_is_degraded_without_calling_gateway(monkeypatch: pytest.MonkeyPatch) -> None:
    import app.domains.costs.budget_guard as budget_guard
    import app.platform.llm_gateway as gateway

    monkeypatch.setattr(budget_guard, "check_budget", lambda *args, **kwargs: False)
    monkeypatch.setattr(
        gateway,
        "invoke_json",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("must not call gateway")),
    )

    out = vkpi_intelligent._try_synth("请分析市场", _search_answer())
    assert out["degraded_reason"] == "budget_unavailable"
    assert out["status"] == "degraded"


def test_degraded_answer_is_not_cached(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(vkpi_intelligent, "_try_intent", lambda question: None)
    monkeypatch.setattr(vkpi_intelligent, "_try_search", lambda question, staff: _search_answer())
    calls = {"n": 0}

    def degraded(question: str, fallback: dict, staff=None) -> dict:
        calls["n"] += 1
        return vkpi_intelligent._degraded_answer(
            fallback,
            reason="deadline_exceeded",
            prefix="超时。",
        )

    monkeypatch.setattr(vkpi_intelligent, "_try_synth", degraded)
    first = vkpi_intelligent.intelligent_ask({"question": "如何安排合作?"}, staff={"id": 1})
    second = vkpi_intelligent.intelligent_ask({"question": "如何安排合作?"}, staff={"id": 1})

    assert calls["n"] == 2
    assert first["cached"] is False
    assert second["cached"] is False


def test_successful_synth_answer_is_cached(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(vkpi_intelligent, "_try_intent", lambda question: None)
    monkeypatch.setattr(vkpi_intelligent, "_try_search", lambda question, staff: _search_answer())
    calls = {"n": 0}

    def success(question: str, fallback: dict, staff=None) -> dict:
        calls["n"] += 1
        return vkpi_intelligent._answer(answer="真实综合答案", mode="synth")

    monkeypatch.setattr(vkpi_intelligent, "_try_synth", success)
    first = vkpi_intelligent.intelligent_ask({"question": "如何安排合作?"}, staff={"id": 1})
    second = vkpi_intelligent.intelligent_ask({"question": "如何安排合作?"}, staff={"id": 1})

    assert calls["n"] == 1
    assert first["cached"] is False
    assert second["cached"] is True


def test_answer_contract_rejects_empty_and_oversized_text() -> None:
    assert vkpi_intelligent._valid_synth_json({"answer": "有证据的结论"}) == (True, "")
    assert vkpi_intelligent._valid_synth_json({"answer": ""})[0] is False
    assert vkpi_intelligent._valid_synth_json({"answer": "x" * 6001})[0] is False
    assert vkpi_intelligent._valid_synth_json([])[0] is False


def test_gateway_attempt_limit_prevents_a_second_billable_provider(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.platform import llm_gateway

    calls: list[str] = []
    monkeypatch.setenv(
        "VKPI_LLM_RUNTIME_VERIFIED_MODELS",
        "openai/gpt-5.4-mini,google/gemini-3.5-flash",
    )
    monkeypatch.setattr(
        llm_gateway,
        "_ordered_providers",
        lambda preferred=None: ["openai", "google", "rule_v0"],
    )
    monkeypatch.setattr(llm_gateway, "_is_provider_configured", lambda provider: provider != "rule_v0")
    # This is an attempt-limit unit test, not a model-readiness integration
    # test. Production now requires independently dual-signed readiness
    # evidence, so isolate that hard gate here instead of treating the legacy
    # verified-model environment variable as execution authority.
    monkeypatch.setattr(llm_gateway, "_binding_call_blocker", lambda *args, **kwargs: "")
    monkeypatch.setattr(llm_gateway, "_budget_allows_provider", lambda *args, **kwargs: (True, []))
    monkeypatch.setattr(llm_gateway, "_estimated_cost_usd", lambda *args, **kwargs: 0.001)
    monkeypatch.setattr(llm_gateway, "record_call", lambda **kwargs: kwargs)

    def first_provider(prompt: str, max_output_tokens: int) -> dict:
        calls.append("openai")
        return {"status": "success", "provider": "openai", "text": "not json"}

    def forbidden_second_provider(prompt: str, max_output_tokens: int) -> dict:
        calls.append("google")
        raise AssertionError("attempt limit must stop the second provider")

    monkeypatch.setitem(llm_gateway._PROVIDER_CALLERS, "openai", first_provider)
    monkeypatch.setitem(llm_gateway._PROVIDER_CALLERS, "google", forbidden_second_provider)

    result = llm_gateway.invoke_json(
        "Return JSON",
        skip_budget_check=True,
        max_provider_attempts=1,
    )

    assert calls == ["openai"]
    assert result["provider"] == "rule_v0"
    assert result["provider_attempts"] == 1
    assert any(error["status"] == "provider_attempt_limit" for error in result["errors"])
