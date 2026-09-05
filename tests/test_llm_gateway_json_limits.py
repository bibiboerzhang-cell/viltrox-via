"""Provider-free parity checks for JSON deadlines and unknown-spend protection."""
from types import SimpleNamespace

import pytest

from app.platform import llm_gateway as gateway
from app.platform import llm_gateway_json as json_gateway
from app.platform import llm_gateway_invoke_limits as limits
from tests.test_llm_gateway_json_contract import _install_provider_mocks
from tests.test_llm_gateway_text_limits import Clock


@pytest.fixture
def runtime(monkeypatch):
    clock, events, delays = Clock(), [], {}
    responses = {
        provider: {"provider": provider, "status": "success", "text": '{"ok":true}',
                   "input_tokens": 5, "output_tokens": 2, "cost_micro_usd": 25}
        for provider in ("openai", "google", "anthropic")
    }
    calls, ledger = _install_provider_mocks(monkeypatch, responses)
    monkeypatch.setattr(gateway, "time", clock)
    monkeypatch.setattr(json_gateway, "serve_cached_result", lambda **kwargs: None)
    monkeypatch.setattr(json_gateway, "store_cached_result", lambda *args: events.append(("cache",)))

    def event(stage, *args):
        events.append((stage, *args))
        clock.advance(delays.get(stage, 0))

    class Reservations:
        def reserve_llm_budget(self, **kwargs):
            key = "res-" + kwargs["provider"]
            event("reserve", key)
            return SimpleNamespace(reservation_key=key)

        def mark_llm_provider_started(self, key):
            event("started", key)

        def settle_llm_reservation(self, key, actual):
            event("settle", key, actual)
            return {"settled": True}

        def release_llm_reservation(self, key):
            event("release", key)
            return True

        def mark_llm_provider_unknown(self, key):
            event("unknown", key)
            return True

    monkeypatch.setattr(gateway, "_llm_budget_reservations", Reservations)
    monkeypatch.setattr(gateway, "_acquire_strict_fleet_breaker", lambda **kwargs: event("acquire") or "permit")
    monkeypatch.setattr(gateway, "_abandon_strict_fleet_breaker", lambda permit: event("abandon"))
    monkeypatch.setattr(gateway, "_complete_strict_fleet_breaker", lambda *args: event("complete"))

    def invoke(**kwargs):
        return gateway.invoke_json("Return JSON", required_keys=["ok"],
                                   skip_budget_check=True, enforce_atomic_reservation=True, **kwargs)

    return SimpleNamespace(clock=clock, events=events, delays=delays, responses=responses,
                           calls=calls, ledger=ledger, invoke=invoke)


@pytest.mark.parametrize(("requested", "expected"), [(None, 2), (1, 1), (99, 3), (0, 1), ("bad", 2), (float("inf"), 2)])
def test_json_attempt_limit_matches_text_and_definitive_rejection_settles_zero(runtime, requested, expected):
    for result in runtime.responses.values():
        result.clear()
        result.update(status="provider_429")
    result = runtime.invoke(max_provider_attempts=requested)
    assert len(runtime.calls) == result["provider_attempts"] == expected
    assert result["max_provider_attempts"] == expected
    assert len([event for event in runtime.events if event[0] == "settle" and event[2] == 0]) == expected
    assert not any(event[0] == "unknown" for event in runtime.events)


@pytest.mark.parametrize("failure", ["timeout", "transport_error", "provider_5xx", "empty", "parse", "valid_unmetered", "exception", "non_object"])
def test_unknown_spend_never_settles_zero_or_falls_through(runtime, monkeypatch, failure):
    response = runtime.responses["openai"]
    response.clear()
    if failure in {"empty", "parse", "valid_unmetered"}:
        response.update(status="success", text={"empty": "", "parse": "not json", "valid_unmetered": '{"ok":true}'}[failure])
    elif failure == "exception":
        def raises(*args, **kwargs):
            runtime.calls.append("openai")
            raise TimeoutError("sensitive upstream body")
        monkeypatch.setitem(gateway._PROVIDER_CALLERS, "openai", raises)
    elif failure == "non_object":
        monkeypatch.setitem(gateway._PROVIDER_CALLERS, "openai", lambda *args, **kwargs: runtime.calls.append("openai") or [])
    else:
        response.update(status=failure)
    result = runtime.invoke()
    assert runtime.calls == ["openai"]
    assert result["reason"] == "provider_outcome_unknown"
    assert result["budget_reservation_key"] == "res-openai"
    assert ("unknown", "res-openai") in runtime.events
    assert not any(event[0] in {"settle", "release", "cache"} for event in runtime.events)
    assert "sensitive upstream body" not in str(runtime.ledger)


@pytest.mark.parametrize("stage", ["reserve", "acquire", "started"])
def test_pre_dispatch_preparation_cannot_outlive_json_deadline(runtime, stage):
    runtime.delays[stage] = 2
    result = runtime.invoke(deadline_seconds=1)
    assert result["reason"] == "deadline_exceeded"
    assert result["provider_attempts"] == 0
    assert runtime.calls == []
    assert not any(event[0] in {"unknown", "complete"} for event in runtime.events)
    if stage == "started":
        assert ("settle", "res-openai", 0.0) in runtime.events
    else:
        assert ("release", "res-openai") in runtime.events


@pytest.mark.parametrize("seconds", [float("inf"), float("-inf"), float("nan"), "invalid"])
def test_invalid_deadline_is_bounded(runtime, seconds):
    result = runtime.invoke(deadline_seconds=seconds)
    assert 0 <= result["deadline_seconds"] <= 90


def test_json_caller_inherits_remaining_http_budget_and_restores_context(runtime, monkeypatch):
    observed = []
    original = gateway._PROVIDER_CALLERS["openai"]
    def caller(*args, **kwargs):
        runtime.clock.advance(2)
        observed.append(limits.bounded_http_timeout(90))
        return original(*args, **kwargs)
    monkeypatch.setitem(gateway._PROVIDER_CALLERS, "openai", caller)
    with limits.provider_deadline(runtime.clock.monotonic() + 5, runtime.clock.monotonic):
        result = runtime.invoke(deadline_seconds=90)
    assert result["deadline_seconds"] == 5
    assert observed == [3]
    assert limits.bounded_http_timeout(90) == 90


def test_expired_parent_never_reserves_or_calls(runtime):
    with limits.provider_deadline(runtime.clock.monotonic(), runtime.clock.monotonic):
        result = runtime.invoke()
    assert result["reason"] == "deadline_exceeded"
    assert runtime.calls == []
    assert runtime.events == []


def test_pre_http_deadline_exception_settles_zero_without_marking_breaker_failure(runtime, monkeypatch):
    def caller(*args, **kwargs):
        runtime.clock.advance(2)
        limits.bounded_http_timeout(90)
        raise AssertionError("must not reach provider I/O")
    monkeypatch.setitem(gateway._PROVIDER_CALLERS, "openai", caller)
    result = runtime.invoke(deadline_seconds=1)
    assert result["reason"] == "deadline_exceeded"
    assert ("settle", "res-openai", 0.0) in runtime.events
    assert not any(event[0] in {"complete", "unknown", "cache"} for event in runtime.events)


def test_metered_invalid_json_settles_actual_usage_and_allows_bounded_fallback(runtime):
    runtime.responses["openai"]["text"] = "not json"
    result = runtime.invoke()
    assert result["json"] == {"ok": True}
    assert runtime.calls == ["openai", "google"]
    assert ("settle", "res-openai", 0.000025) in runtime.events
    assert not any(event[0] == "unknown" for event in runtime.events)


@pytest.mark.parametrize("usage", [{}, {"input_tokens": 0, "output_tokens": 0},
                                 {"input_tokens": float("inf"), "output_tokens": "invalid"},
                                 {"input_tokens": -5, "output_tokens": -1}])
def test_invalid_or_zero_usage_cannot_release_strict_reservation(runtime, usage):
    runtime.responses["openai"].clear()
    runtime.responses["openai"].update(status="success", text='{"ok":true}', **usage)
    result = runtime.invoke()
    assert result["reason"] == "provider_outcome_unknown"
    assert runtime.calls == ["openai"]
    assert not any(event[0] in {"settle", "release", "cache"} for event in runtime.events)


def test_late_valid_json_is_audited_and_settled_but_not_delivered_or_cached(runtime, monkeypatch):
    original = gateway._PROVIDER_CALLERS["openai"]
    def caller(*args, **kwargs):
        response = original(*args, **kwargs)
        runtime.clock.advance(2)
        return response
    monkeypatch.setitem(gateway._PROVIDER_CALLERS, "openai", caller)
    result = runtime.invoke(deadline_seconds=1)
    assert result["reason"] == "deadline_exceeded"
    assert result["json"] is None
    assert runtime.calls == ["openai"]
    assert ("settle", "res-openai", 0.000025) in runtime.events
    assert not any(event[0] in {"unknown", "cache"} for event in runtime.events)


@pytest.mark.parametrize("stage", ["validator", "audit", "settle"])
def test_post_processing_stays_inside_deadline(runtime, monkeypatch, stage):
    kwargs = {}
    if stage == "validator":
        kwargs["validator"] = lambda value: runtime.clock.advance(2) or True
    elif stage == "audit":
        original = gateway.record_call
        def record(**kwargs):
            result = original(**kwargs)
            if kwargs.get("provider") != "rule_v0":
                runtime.clock.advance(2)
            return result
        monkeypatch.setattr(gateway, "record_call", record)
    else:
        runtime.delays["settle"] = 2
    result = runtime.invoke(deadline_seconds=1, **kwargs)
    assert result["reason"] == "deadline_exceeded"
    assert runtime.calls == ["openai"]
    assert ("settle", "res-openai", 0.000025) in runtime.events
    assert not any(event[0] == "cache" for event in runtime.events)


@pytest.mark.parametrize("cached", [{"json": {"wrong": 1}}, {"text": "bad JSON"}, {"json": {"ok": False}}])
def test_cache_must_satisfy_this_calls_keys_and_validator(runtime, monkeypatch, cached):
    monkeypatch.setattr(json_gateway, "serve_cached_result", lambda **kwargs: {"status": "success", **cached})
    result = runtime.invoke(validator=lambda value: value.get("ok") is True)
    assert result["json"] == {"ok": True}
    assert runtime.calls == ["openai"]
    assert any(error["status"] == "cache_contract_invalid" for error in result["errors"])


def test_slow_cache_validator_never_starts_provider(runtime, monkeypatch):
    monkeypatch.setattr(json_gateway, "serve_cached_result", lambda **kwargs: {"status": "success", "json": {"ok": True}})
    result = runtime.invoke(deadline_seconds=1, validator=lambda value: runtime.clock.advance(2) or True)
    assert result["reason"] == "deadline_exceeded"
    assert runtime.calls == []
    assert runtime.events == []
