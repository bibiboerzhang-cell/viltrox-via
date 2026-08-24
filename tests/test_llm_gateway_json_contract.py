from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from app.platform import llm_gateway


def _verify_default_models(monkeypatch: pytest.MonkeyPatch, providers: set[str] | list[str]) -> None:
    bindings = [
        f"{provider}/{llm_gateway.PROVIDER_CONFIG[provider]['model']}"
        for provider in providers
        if provider in llm_gateway.PROVIDER_CONFIG
    ]
    monkeypatch.setenv("VKPI_LLM_RUNTIME_VERIFIED_MODELS", ",".join(bindings))
    allowed = set(bindings)
    monkeypatch.setattr(
        llm_gateway,
        "exact_binding_readiness_from_environment",
        lambda binding: (
            {"binding": binding, "production_ready": binding in allowed},
            {"source": "test_signed_readiness_fixture"},
        ),
    )


def _install_provider_mocks(
    monkeypatch: pytest.MonkeyPatch,
    responses: dict[str, dict[str, Any]],
) -> tuple[list[str], list[dict[str, Any]]]:
    calls: list[str] = []
    ledger: list[dict[str, Any]] = []
    providers = list(responses)
    _verify_default_models(monkeypatch, providers)

    monkeypatch.setattr(llm_gateway, "_ordered_providers", lambda _preferred=None: [*providers, "rule_v0"])
    monkeypatch.setattr(llm_gateway, "_is_provider_configured", lambda provider: provider in responses)
    monkeypatch.setattr(llm_gateway, "_budget_allows_provider", lambda *_args, **_kwargs: (True, []))
    monkeypatch.setattr(llm_gateway, "_estimated_cost_usd", lambda *_args, **_kwargs: 0.001)
    monkeypatch.setattr(llm_gateway, "record_call", lambda **kwargs: ledger.append(kwargs) or {"call": kwargs})

    for provider, response in responses.items():
        def caller(_prompt: str, _max_output_tokens: int, *, _provider: str = provider, _response: dict[str, Any] = response) -> dict[str, Any]:
            calls.append(_provider)
            return dict(_response)

        monkeypatch.setitem(llm_gateway._PROVIDER_CALLERS, provider, caller)
    return calls, ledger


@pytest.mark.parametrize(
    "response_text",
    [
        'Here is the result:\n```json\n{"answer": 42, "ok": true}\n```\nDone.',
        'Analysis complete. {"answer": 42, "ok": true} End of response.',
    ],
)
def test_invoke_json_parses_fenced_and_prose_wrapped_json(
    monkeypatch: pytest.MonkeyPatch,
    response_text: str,
) -> None:
    calls, ledger = _install_provider_mocks(
        monkeypatch,
        {
            "openai": {
                "status": "success",
                "provider": "openai",
                "model": "mock-openai",
                "text": response_text,
                "input_tokens": 10,
                "output_tokens": 8,
                "cost_micro_usd": 20,
            }
        },
    )

    result = llm_gateway.invoke_json(
        "Return JSON",
        required_keys=("answer", "ok"),
        skip_budget_check=True,
    )

    assert calls == ["openai"]
    assert result["provider"] == "openai"
    assert result["json"] == {"answer": 42, "ok": True}
    assert result["fallback_used"] is False
    assert [entry["status"] for entry in ledger] == ["success"]


def test_invoke_json_atomic_reservation_covers_provider_and_settlement(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events: list[tuple[str, Any]] = []

    class Reservations:
        def reserve_llm_budget(self, **kwargs):
            events.append(("reserve", kwargs))
            return SimpleNamespace(reservation_key="llmres-json")

        def mark_llm_provider_started(self, key: str) -> None:
            events.append(("started", key))

        def settle_llm_reservation(self, key: str, actual: float) -> dict[str, Any]:
            events.append(("settled", (key, actual)))
            return {"settled": True}

        def mark_llm_provider_unknown(self, key: str) -> bool:
            events.append(("unknown", key))
            return True

        def release_llm_reservation(self, key: str) -> bool:
            events.append(("released", key))
            return True

    default_model = llm_gateway.PROVIDER_CONFIG["openai"]["model"]
    calls, ledger = _install_provider_mocks(
        monkeypatch,
        {
            "openai": {
                "status": "success",
                "provider": "openai",
                "model": default_model,
                "text": '{"answer":"ok"}',
                "input_tokens": 10,
                "output_tokens": 3,
                "cost_micro_usd": 50,
            }
        },
    )
    monkeypatch.setattr(llm_gateway, "_llm_budget_reservations", lambda: Reservations())

    def provider(prompt: str, max_tokens: int) -> dict[str, Any]:
        events.append(("provider", (prompt, max_tokens)))
        calls.append("openai")
        return {
            "status": "success",
            "provider": "openai",
            "model": default_model,
            "text": '{"answer":"ok"}',
            "input_tokens": 10,
            "output_tokens": 3,
            "cost_micro_usd": 50,
        }

    monkeypatch.setitem(llm_gateway._PROVIDER_CALLERS, "openai", provider)
    original_record_call = llm_gateway.record_call

    def record_call(**kwargs):
        events.append(("ledger", kwargs["status"]))
        return original_record_call(**kwargs)

    monkeypatch.setattr(llm_gateway, "record_call", record_call)
    result = llm_gateway.invoke_json(
        "Return JSON",
        required_keys=("answer",),
        skip_budget_check=True,
        require_configured_budget=True,
        enforce_atomic_reservation=True,
    )

    assert [name for name, _ in events] == [
        "reserve",
        "started",
        "provider",
        "ledger",
        "settled",
    ]
    assert result["json"] == {"answer": "ok"}
    assert result["budget_reservation_key"] == "llmres-json"
    assert events[0][1]["require_cost_scope"] is True
    assert ledger[0]["update_budget_scopes"] is False
    assert ledger[0]["force_cost_ledger"] is True
    assert ledger[0]["metadata"]["budget_gate"] == "atomic_reservation"


def test_invoke_json_atomic_validates_and_audits_parse_failure_before_settlement(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events: list[tuple[str, Any]] = []

    class Reservations:
        def reserve_llm_budget(self, **_kwargs):
            events.append(("reserve", None))
            return SimpleNamespace(reservation_key="llmres-invalid-json")

        def mark_llm_provider_started(self, key: str) -> None:
            events.append(("started", key))

        def settle_llm_reservation(self, key: str, actual: float) -> dict[str, Any]:
            events.append(("settled", (key, actual)))
            return {"settled": True}

    default_model = llm_gateway.PROVIDER_CONFIG["openai"]["model"]
    _calls, ledger = _install_provider_mocks(
        monkeypatch,
        {
            "openai": {
                "status": "success",
                "provider": "openai",
                "model": default_model,
                "text": "not valid json",
                "input_tokens": 7,
                "output_tokens": 2,
                "cost_micro_usd": 25,
            }
        },
    )
    monkeypatch.setattr(llm_gateway, "_llm_budget_reservations", lambda: Reservations())

    def provider(_prompt: str, _max_tokens: int) -> dict[str, Any]:
        events.append(("provider", None))
        return {
            "status": "success",
            "provider": "openai",
            "model": default_model,
            "text": "not valid json",
            "input_tokens": 7,
            "output_tokens": 2,
            "cost_micro_usd": 25,
        }

    monkeypatch.setitem(llm_gateway._PROVIDER_CALLERS, "openai", provider)
    original_record_call = llm_gateway.record_call

    def record_call(**kwargs):
        events.append(("ledger", kwargs["status"]))
        return original_record_call(**kwargs)

    monkeypatch.setattr(llm_gateway, "record_call", record_call)

    result = llm_gateway.invoke_json(
        "Return JSON",
        skip_budget_check=True,
        enforce_atomic_reservation=True,
    )

    assert [name for name, _ in events[:5]] == [
        "reserve",
        "started",
        "provider",
        "ledger",
        "settled",
    ]
    assert events[3] == ("ledger", "parse_failure")
    assert [entry["status"] for entry in ledger] == [
        "parse_failure",
        "all_providers_failed",
    ]
    assert result["provider"] == "rule_v0"


def test_invoke_json_atomic_ledger_failure_keeps_reservation_open_and_unsettled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events: list[tuple[str, Any]] = []
    _verify_default_models(monkeypatch, {"openai"})
    monkeypatch.setattr(
        llm_gateway,
        "_ordered_providers",
        lambda _preferred=None: ["openai", "rule_v0"],
    )
    monkeypatch.setattr(
        llm_gateway, "_is_provider_configured", lambda provider: provider == "openai"
    )
    monkeypatch.setattr(
        llm_gateway, "_budget_allows_provider", lambda *_args, **_kwargs: (True, [])
    )
    monkeypatch.setattr(
        llm_gateway, "_estimated_cost_usd", lambda *_args, **_kwargs: 0.001
    )

    class Reservations:
        def reserve_llm_budget(self, **_kwargs):
            events.append(("reserve", None))
            return SimpleNamespace(reservation_key="llmres-ledger-down")

        def mark_llm_provider_started(self, key: str) -> None:
            events.append(("started", key))

        def settle_llm_reservation(self, key: str, actual: float) -> dict[str, Any]:
            events.append(("settled", (key, actual)))
            return {"settled": True}

        def mark_llm_provider_unknown(self, key: str) -> bool:
            events.append(("unknown", key))
            return True

    monkeypatch.setattr(llm_gateway, "_llm_budget_reservations", lambda: Reservations())
    default_model = llm_gateway.PROVIDER_CONFIG["openai"]["model"]

    def provider(_prompt: str, _max_tokens: int) -> dict[str, Any]:
        events.append(("provider", None))
        return {
            "status": "success",
            "provider": "openai",
            "model": default_model,
            "text": '{"answer":"ok"}',
            "input_tokens": 10,
            "output_tokens": 3,
            "cost_micro_usd": 50,
        }

    def ledger_down(**_kwargs):
        events.append(("ledger", None))
        raise RuntimeError("secret ledger outage")

    monkeypatch.setitem(llm_gateway._PROVIDER_CALLERS, "openai", provider)
    monkeypatch.setattr(llm_gateway, "record_call", ledger_down)

    result = llm_gateway.invoke_json(
        "Return JSON",
        required_keys=("answer",),
        skip_budget_check=True,
        enforce_atomic_reservation=True,
    )

    assert [name for name, _ in events] == [
        "reserve",
        "started",
        "provider",
        "ledger",
        "unknown",
    ]
    assert result["provider"] == "rule_v0"
    assert result["reason"] == "audit_ledger_unavailable"
    assert result["json"] is None
    assert result["budget_reservation_key"] == "llmres-ledger-down"
    assert "secret ledger outage" not in str(result)


def test_invoke_json_atomic_settlement_failure_stops_second_provider(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events: list[tuple[str, Any]] = []

    class Reservations:
        def reserve_llm_budget(self, **_kwargs):
            events.append(("reserve", None))
            return SimpleNamespace(reservation_key="llmres-settlement-down")

        def mark_llm_provider_started(self, key: str) -> None:
            events.append(("started", key))

        def settle_llm_reservation(self, key: str, actual: float) -> dict[str, Any]:
            events.append(("settled", (key, actual)))
            raise RuntimeError("secret settlement outage")

        def mark_llm_provider_unknown(self, key: str) -> bool:
            events.append(("unknown", key))
            return True

    default_openai = llm_gateway.PROVIDER_CONFIG["openai"]["model"]
    default_google = llm_gateway.PROVIDER_CONFIG["google"]["model"]
    calls, ledger = _install_provider_mocks(
        monkeypatch,
        {
            "openai": {
                "status": "success",
                "provider": "openai",
                "model": default_openai,
                "text": '{"answer":"first"}',
                "input_tokens": 10,
                "output_tokens": 3,
                "cost_micro_usd": 50,
            },
            "google": {
                "status": "success",
                "provider": "google",
                "model": default_google,
                "text": '{"answer":"must-not-run"}',
                "input_tokens": 10,
                "output_tokens": 3,
                "cost_micro_usd": 50,
            },
        },
    )
    monkeypatch.setattr(llm_gateway, "_llm_budget_reservations", lambda: Reservations())

    result = llm_gateway.invoke_json(
        "Return JSON",
        required_keys=("answer",),
        skip_budget_check=True,
        enforce_atomic_reservation=True,
    )

    assert calls == ["openai"]
    assert [row["status"] for row in ledger] == ["success"]
    assert [name for name, _ in events] == [
        "reserve",
        "started",
        "settled",
        "unknown",
    ]
    assert result["provider"] == "rule_v0"
    assert result["reason"] == "reservation_settlement_failed"
    assert result["budget_reservation_key"] == "llmres-settlement-down"
    assert result["provider_attempts"] == 1
    assert "secret settlement outage" not in str(result)


def test_invoke_json_atomic_provider_exception_is_unknown_without_raw_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events: list[tuple[str, Any]] = []
    ledger: list[dict[str, Any]] = []
    _verify_default_models(monkeypatch, {"openai"})
    monkeypatch.setattr(llm_gateway, "_ordered_providers", lambda _preferred=None: ["openai", "rule_v0"])
    monkeypatch.setattr(llm_gateway, "_is_provider_configured", lambda provider: provider == "openai")
    monkeypatch.setattr(llm_gateway, "_budget_allows_provider", lambda *_args, **_kwargs: (True, []))
    monkeypatch.setattr(llm_gateway, "_estimated_cost_usd", lambda *_args, **_kwargs: 0.001)
    monkeypatch.setattr(llm_gateway, "record_call", lambda **kwargs: ledger.append(kwargs) or {})

    class Reservations:
        def reserve_llm_budget(self, **_kwargs):
            events.append(("reserve", None))
            return SimpleNamespace(reservation_key="llmres-error")

        def mark_llm_provider_started(self, key: str) -> None:
            events.append(("started", key))

        def mark_llm_provider_unknown(self, key: str) -> bool:
            events.append(("unknown", key))
            return True

    monkeypatch.setattr(llm_gateway, "_llm_budget_reservations", lambda: Reservations())

    def explode(*_args, **_kwargs):
        events.append(("provider", None))
        raise RuntimeError("secret upstream body")

    monkeypatch.setitem(llm_gateway._PROVIDER_CALLERS, "openai", explode)
    result = llm_gateway.invoke_json(
        "Return JSON",
        skip_budget_check=True,
        enforce_atomic_reservation=True,
    )

    assert [name for name, _ in events] == ["reserve", "started", "provider", "unknown"]
    assert result["provider"] == "rule_v0"
    provider_row = next(row for row in ledger if row["provider"] == "openai")
    assert provider_row["status"] == "provider_exception"
    assert "secret upstream body" not in str(provider_row)


def test_invoke_json_records_parse_failure_then_uses_next_provider(monkeypatch: pytest.MonkeyPatch) -> None:
    calls, ledger = _install_provider_mocks(
        monkeypatch,
        {
            "openai": {
                "status": "success",
                "provider": "openai",
                "model": "mock-openai",
                "text": '{"broken": nope, "nested": {"answer": "must not be accepted"}}',
                "input_tokens": 101,
                "output_tokens": 17,
                "cost_cents": 2,
                "cost_micro_usd": 1234,
            },
            "google": {
                "status": "success",
                "provider": "google",
                "model": "mock-google",
                "text": 'Result: {"answer": "fallback"}.',
                "input_tokens": 55,
                "output_tokens": 9,
                "cost_micro_usd": 88,
            },
        },
    )

    result = llm_gateway.invoke_json(
        "Return JSON",
        required_keys=["answer"],
        skip_budget_check=True,
    )

    assert calls == ["openai", "google"]
    assert result["provider"] == "google"
    assert result["json"] == {"answer": "fallback"}
    assert result["fallback_used"] is True
    assert [entry["status"] for entry in ledger] == ["parse_failure", "success"]
    assert ledger[0]["input_tokens"] == 101
    assert ledger[0]["output_tokens"] == 17
    assert ledger[0]["cost_cents"] == 2
    assert ledger[0]["cost_micro_usd"] == 1234


def test_invoke_json_required_keys_and_validator_continue_fallback(monkeypatch: pytest.MonkeyPatch) -> None:
    calls, ledger = _install_provider_mocks(
        monkeypatch,
        {
            "openai": {"status": "success", "provider": "openai", "text": '{"answer": 1}'},
            "google": {"status": "success", "provider": "google", "text": '{"answer": 1, "ok": false}'},
            "anthropic": {"status": "success", "provider": "anthropic", "text": '{"answer": 1, "ok": true}'},
        },
    )

    result = llm_gateway.invoke_json(
        "Return validated JSON",
        required_keys=["answer", "ok"],
        validator=lambda value: (value["ok"] is True, "ok must be true"),
        skip_budget_check=True,
    )

    assert calls == ["openai", "google", "anthropic"]
    assert result["provider"] == "anthropic"
    assert result["json"]["ok"] is True
    assert [entry["status"] for entry in ledger] == ["validation_failure", "validation_failure", "success"]
    assert ledger[0]["metadata"]["attempt_error"] == "missing required keys: ok"
    assert ledger[1]["metadata"]["attempt_error"] == "ok must be true"


def test_invoke_json_all_provider_failures_return_rule_v0_with_null_json(monkeypatch: pytest.MonkeyPatch) -> None:
    calls, ledger = _install_provider_mocks(
        monkeypatch,
        {
            "openai": {
                "status": "failed",
                "provider": "openai",
                "error": "transport failed",
                "input_tokens": 4,
                "output_tokens": 0,
                "cost_micro_usd": 7,
            },
            "google": {
                "status": "success",
                "provider": "google",
                "text": "still not json",
                "input_tokens": 5,
                "output_tokens": 3,
                "cost_micro_usd": 9,
            },
        },
    )

    result = llm_gateway.invoke_json("Return JSON", skip_budget_check=True)

    assert calls == ["openai", "google"]
    assert result["provider"] == "rule_v0"
    assert result["json"] is None
    assert result["reason"] == "all_providers_failed"
    assert [entry["status"] for entry in ledger] == ["failed", "parse_failure", "all_providers_failed"]
    assert ledger[0]["input_tokens"] == 4
    assert ledger[1]["output_tokens"] == 3


def test_invoke_json_can_require_every_budget_scope_to_be_configured(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls, _ledger = _install_provider_mocks(
        monkeypatch,
        {"openai": {"status": "success", "provider": "openai", "text": '{"ok":true}'}},
    )
    observed: list[bool] = []

    def budget_gate(*_args, **kwargs):
        observed.append(bool(kwargs.get("require_configured")))
        return False, [{"scope": "cron:marketing_advisor", "configured": False}]

    monkeypatch.setattr(llm_gateway, "_budget_allows_provider", budget_gate)

    result = llm_gateway.invoke_json(
        "Return JSON",
        required_keys=["ok"],
        skip_budget_check=True,
        require_configured_budget=True,
    )

    assert observed == [True]
    assert calls == []
    assert result["provider"] == "rule_v0"
    assert any(item.get("status") == "budget_blocked" for item in result["errors"])


def test_invoke_json_deadline_records_attempt_and_stops_next_provider(monkeypatch: pytest.MonkeyPatch) -> None:
    clock = {"now": 0.0}
    ledger: list[dict[str, Any]] = []
    calls: list[str] = []
    _verify_default_models(monkeypatch, {"openai", "google"})

    monkeypatch.setattr(llm_gateway, "time", SimpleNamespace(monotonic=lambda: clock["now"]))
    monkeypatch.setattr(llm_gateway, "_ordered_providers", lambda _preferred=None: ["openai", "google", "rule_v0"])
    monkeypatch.setattr(llm_gateway, "_is_provider_configured", lambda provider: provider in {"openai", "google"})
    monkeypatch.setattr(llm_gateway, "_budget_allows_provider", lambda *_args, **_kwargs: (True, []))
    monkeypatch.setattr(llm_gateway, "_estimated_cost_usd", lambda *_args, **_kwargs: 0.001)
    monkeypatch.setattr(llm_gateway, "record_call", lambda **kwargs: ledger.append(kwargs) or {"call": kwargs})

    def slow_invalid(_prompt: str, _max_output_tokens: int) -> dict[str, Any]:
        calls.append("openai")
        clock["now"] = 2.0
        return {
            "status": "success",
            "provider": "openai",
            "text": "not json",
            "input_tokens": 12,
            "output_tokens": 2,
            "cost_micro_usd": 15,
        }

    def must_not_run(_prompt: str, _max_output_tokens: int) -> dict[str, Any]:
        calls.append("google")
        raise AssertionError("deadline should prevent the next provider call")

    monkeypatch.setitem(llm_gateway._PROVIDER_CALLERS, "openai", slow_invalid)
    monkeypatch.setitem(llm_gateway._PROVIDER_CALLERS, "google", must_not_run)

    result = llm_gateway.invoke_json("Return JSON", skip_budget_check=True, deadline_seconds=1)

    assert calls == ["openai"]
    assert result["provider"] == "rule_v0"
    assert result["json"] is None
    assert result["reason"] == "deadline_exceeded"
    assert [entry["status"] for entry in ledger] == ["parse_failure", "deadline_exceeded"]
    assert any(error["status"] == "deadline_exceeded" for error in result["errors"])


def test_invoke_json_uses_configurable_default_deadline(monkeypatch: pytest.MonkeyPatch) -> None:
    calls, ledger = _install_provider_mocks(
        monkeypatch,
        {"openai": {"status": "success", "provider": "openai", "text": '{"ok": true}'}},
    )
    monkeypatch.setenv("VKPI_LLM_GATEWAY_DEADLINE_SECONDS", "0")
    monkeypatch.setattr(llm_gateway, "time", SimpleNamespace(monotonic=lambda: 10.0))

    result = llm_gateway.invoke_json("Return JSON", skip_budget_check=True)

    assert calls == []
    assert result["provider"] == "rule_v0"
    assert result["json"] is None
    assert result["reason"] == "deadline_exceeded"
    assert result["deadline_seconds"] == 0.0
    assert [entry["status"] for entry in ledger] == ["deadline_exceeded"]


def test_legacy_invoke_isolates_provider_exception_and_uses_next_provider(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[str] = []
    ledger: list[dict[str, Any]] = []
    _verify_default_models(monkeypatch, {"openai", "google"})
    monkeypatch.setattr(llm_gateway, "_ordered_providers", lambda _preferred=None: ["openai", "google", "rule_v0"])
    monkeypatch.setattr(llm_gateway, "_is_provider_configured", lambda provider: provider in {"openai", "google"})
    monkeypatch.setattr(llm_gateway, "_budget_allows_provider", lambda *_args, **_kwargs: (True, []))
    monkeypatch.setattr(llm_gateway, "_estimated_cost_usd", lambda *_args, **_kwargs: 0.001)
    monkeypatch.setattr(llm_gateway, "record_call", lambda **kwargs: ledger.append(kwargs) or kwargs)

    def broken_provider(_prompt: str, _max_output_tokens: int) -> dict[str, Any]:
        calls.append("openai")
        raise TimeoutError("provider timed out")

    def healthy_provider(_prompt: str, _max_output_tokens: int) -> dict[str, Any]:
        calls.append("google")
        return {
            "status": "success",
            "provider": "google",
            "model": "mock-google",
            "text": "usable fallback",
            "input_tokens": "bad-token-count",
            "output_tokens": 4,
        }

    monkeypatch.setitem(llm_gateway._PROVIDER_CALLERS, "openai", broken_provider)
    monkeypatch.setitem(llm_gateway._PROVIDER_CALLERS, "google", healthy_provider)

    result = llm_gateway.invoke("Analyze this", skip_budget_check=True)

    assert calls == ["openai", "google"]
    assert result["status"] == "success"
    assert result["provider"] == "google"
    assert result["fallback_used"] is True
    assert ledger[-1]["input_tokens"] == 0
    assert ledger[-1]["metadata"]["attempt_errors"][0]["status"] == "provider_exception"


def test_legacy_invoke_rejects_non_object_provider_result_without_raising(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _verify_default_models(monkeypatch, {"openai"})
    monkeypatch.setattr(llm_gateway, "_ordered_providers", lambda _preferred=None: ["openai", "rule_v0"])
    monkeypatch.setattr(llm_gateway, "_is_provider_configured", lambda provider: provider == "openai")
    monkeypatch.setattr(llm_gateway, "_budget_allows_provider", lambda *_args, **_kwargs: (True, []))
    monkeypatch.setattr(llm_gateway, "_estimated_cost_usd", lambda *_args, **_kwargs: 0.001)
    monkeypatch.setattr(llm_gateway, "record_call", lambda **kwargs: kwargs)
    monkeypatch.setitem(llm_gateway._PROVIDER_CALLERS, "openai", lambda *_args: None)

    result = llm_gateway.invoke("Analyze this", skip_budget_check=True)

    assert result["provider"] == "rule_v0"
    assert result["reason"] == "all_providers_failed"
    assert result["errors"][0]["status"] == "invalid_response"


@pytest.mark.parametrize("entrypoint", ["invoke", "invoke_json"])
def test_budget_check_exception_fails_closed_without_provider_call(
    monkeypatch: pytest.MonkeyPatch,
    entrypoint: str,
) -> None:
    provider_calls: list[str] = []
    _verify_default_models(monkeypatch, {"openai"})
    monkeypatch.setattr(llm_gateway, "_ordered_providers", lambda _preferred=None: ["openai", "rule_v0"])
    monkeypatch.setattr(llm_gateway, "_is_provider_configured", lambda provider: provider == "openai")
    monkeypatch.setattr(llm_gateway, "_estimated_cost_usd", lambda *_args, **_kwargs: 0.001)
    monkeypatch.setattr(
        llm_gateway,
        "_budget_allows_provider",
        lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("budget database unavailable")),
    )
    monkeypatch.setattr(llm_gateway, "record_call", lambda **kwargs: kwargs)
    monkeypatch.setitem(
        llm_gateway._PROVIDER_CALLERS,
        "openai",
        lambda *_args: provider_calls.append("openai") or {"status": "success", "text": "must not run"},
    )

    result = getattr(llm_gateway, entrypoint)("Analyze this", skip_budget_check=True)

    assert provider_calls == []
    assert result["provider"] == "rule_v0"
    assert result["reason"] == "all_providers_failed"
    assert result["errors"][0]["status"] == "budget_check_failed"
