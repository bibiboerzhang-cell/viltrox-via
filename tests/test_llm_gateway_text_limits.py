"""Hermetic deadline, spend-attempt and reservation-boundary regressions."""
from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from app.platform import llm_gateway_invoke as target
from app.platform import llm_gateway_invoke_limits as limits
from app.platform import llm_gateway_providers as providers
from tests.test_llm_gateway_invoke_characterization import InvokeHarness


class Clock:
    value = 100.0

    def monotonic(self) -> float:
        return self.value

    def advance(self, seconds: float) -> None:
        self.value += seconds


@pytest.fixture
def harness(monkeypatch):
    item = InvokeHarness()
    item.install(monkeypatch)
    return item


def invoke(harness: InvokeHarness, clock: Clock, **kwargs: Any):
    namespace = harness.namespace()
    namespace["time"] = clock
    return target.invoke_impl(
        "hello", purpose="text-limits", namespace=namespace,
        enforce_atomic_reservation=True, **kwargs,
    )


def providers_called(harness: InvokeHarness) -> list[str]:
    return [event[1] for event in harness.events if event[0] == "provider"]


def delay(monkeypatch, obj, name: str, clock: Clock, seconds: float) -> None:
    original = getattr(obj, name)

    def delayed(*args, **kwargs):
        result = original(*args, **kwargs)
        clock.advance(seconds)
        return result

    monkeypatch.setattr(obj, name, delayed)


@pytest.mark.parametrize("stage", ["scope_budget", "budget_allows_provider", "reserve_llm_budget", "acquire_breaker", "mark_llm_provider_started"])
def test_preparation_is_inside_deadline_and_cannot_start_provider(harness, monkeypatch, stage):
    clock = Clock()
    delay(monkeypatch, harness, stage, clock, 2.0)

    result = invoke(harness, clock, deadline_seconds=1)

    assert result["reason"] == "deadline_exceeded"
    assert result["provider_attempts"] == 0
    assert providers_called(harness) == []
    assert not any(event[0] == "unknown" for event in harness.events)
    if stage in {"reserve_llm_budget", "acquire_breaker"}:
        assert ("release", "res-openai") in harness.events
        assert not any(event[0] == "started" for event in harness.events)
    if stage == "mark_llm_provider_started":
        assert ("settle", "res-openai", 0.0) in harness.events
        assert not any(event[0] == "record_reserved" for event in harness.events)
    if stage == "scope_budget":
        assert not any(event[0] == "ordered_candidates" for event in harness.events)


def test_zero_deadline_does_not_run_budget_or_provider(harness):
    result = invoke(harness, Clock(), deadline_seconds=0)
    assert result["reason"] == "deadline_exceeded"
    assert result["provider_attempts"] == 0
    assert not any(event[0] in {"scope_budget", "reserve", "provider"} for event in harness.events)


def test_late_success_is_audited_and_settled_but_not_cached_or_delivered(harness, monkeypatch):
    clock = Clock()
    original = harness.provider_caller

    def caller(provider):
        def late(*args, **kwargs):
            result = original(provider)(*args, **kwargs)
            clock.advance(2)
            return result
        return late

    monkeypatch.setattr(harness, "provider_caller", caller)
    result = invoke(harness, clock, deadline_seconds=1)

    assert result["reason"] == "deadline_exceeded"
    assert result["text"] == ""
    assert result["provider_attempts"] == 1
    assert result["elapsed_ms"] == 2000
    assert ("settle", "res-openai", 0.0014) in harness.events
    assert harness.records[0]["status"] == "success"
    assert harness.records[-1]["status"] == "deadline_exceeded"
    assert not any(event[0] in {"unknown", "cache_store"} for event in harness.events)
    assert limits.bounded_http_timeout(90) == 90


def set_chain(harness, status="provider_429"):
    harness.candidates = [(name, f"{name}-exact", True) for name in ("openai", "google", "anthropic")]
    harness.configured = {candidate[0] for candidate in harness.candidates}
    harness.provider_results = {name: {"status": status, "provider": name, "error": "http_429"} for name in harness.configured}


@pytest.mark.parametrize(("requested", "expected"), [(None, 2), (1, 1), (99, 3), (0, 1), (float("inf"), 2)])
def test_429_fallback_is_bounded_and_settles_zero(harness, requested, expected):
    set_chain(harness)
    result = invoke(harness, Clock(), max_provider_attempts=requested)

    assert result["provider_attempts"] == expected
    assert result["max_provider_attempts"] == expected
    assert len(providers_called(harness)) == expected
    assert len([event for event in harness.events if event[0] == "settle" and event[2] == 0]) == expected
    assert not any(event[0] == "unknown" for event in harness.events)


@pytest.mark.parametrize("failure", [TimeoutError("uncertain"), {"status": "timeout"}, {"status": "transport_error"}, {"status": "provider_5xx"}, {"status": "failed", "input_tokens": "invalid"}, ["invalid"]])
def test_unknown_provider_outcome_holds_budget_and_stops_paid_fallback(harness, failure):
    set_chain(harness)
    harness.provider_results["openai"] = failure

    result = invoke(harness, Clock())

    assert result["reason"] == "provider_outcome_unknown"
    assert providers_called(harness) == ["openai"]
    assert ("unknown", "res-openai") in harness.events
    assert not any(event[0] in {"settle", "release"} for event in harness.events)
    assert limits.bounded_http_timeout(90) == 90


def test_budget_block_is_not_counted_as_paid_attempt(harness):
    set_chain(harness)
    harness.budget_allowed["openai"] = False
    result = invoke(harness, Clock(), max_provider_attempts=1)
    assert providers_called(harness) == ["google"]
    assert result["provider_attempts"] == 1


@pytest.mark.parametrize("seconds", [float("inf"), float("-inf"), float("nan"), "invalid"])
def test_invalid_deadlines_cannot_create_unbounded_provider_time(harness, seconds):
    result = invoke(harness, Clock(), deadline_seconds=seconds)
    assert 0 <= result["deadline_seconds"] <= 90


def test_http_timeout_is_remaining_budget_and_context_is_restored(monkeypatch):
    clock = Clock()
    posts = []

    def make_client():
        clock.advance(2)
        return SimpleNamespace(post=lambda *args, **kwargs: posts.append(kwargs) or SimpleNamespace(raise_for_status=lambda: None, json=lambda: {}))

    monkeypatch.setattr(providers, "_get_http_client", make_client)
    with limits.provider_deadline(clock.monotonic() + 5, clock.monotonic):
        providers._request_json("https://provider.invalid", {}, {}, 90)
        assert posts[0]["timeout"].read == 3.0
        assert posts[0]["timeout"].connect == 3.0
    assert limits.bounded_http_timeout(90) == 90


@pytest.mark.parametrize("expired_during_prepare", [False, True])
def test_expired_http_deadline_never_posts(monkeypatch, expired_during_prepare):
    clock = Clock()
    posts = []

    def make_client():
        clock.advance(2)
        return SimpleNamespace(post=lambda *args, **kwargs: posts.append(kwargs))

    monkeypatch.setattr(providers, "_get_http_client", make_client)
    with pytest.raises(limits.GatewayDeadlineExceeded):
        with limits.provider_deadline(clock.monotonic() + int(expired_during_prepare), clock.monotonic):
            providers._request_json("https://provider.invalid", {}, {}, 90)
    assert not posts
    assert limits.bounded_http_timeout(90) == 90


def test_nested_deadline_cannot_extend_parent_and_resets_after_error():
    clock = Clock()
    with limits.provider_deadline(105, clock.monotonic):
        with pytest.raises(RuntimeError):
            with limits.provider_deadline(110, clock.monotonic):
                assert limits.bounded_http_timeout(90) == 5
                raise RuntimeError("fixture")
        assert limits.bounded_http_timeout(90) == 5
    assert limits.bounded_http_timeout(90) == 90


def test_provider_key_preparation_expires_before_http_and_settles_zero(harness, monkeypatch):
    clock = Clock()

    def slow_key(_provider):
        clock.advance(2)
        return "fixture-key"

    monkeypatch.setattr(providers, "_get_api_key", slow_key)
    monkeypatch.setattr(harness, "provider_caller", lambda _provider: providers._call_openai)
    monkeypatch.setattr(providers, "_get_http_client", lambda: pytest.fail("HTTP must not start"))

    result = invoke(harness, clock, deadline_seconds=1)

    assert result["reason"] == "deadline_exceeded"
    assert result["budget_reservation_key"] == "res-openai"
    assert ("settle", "res-openai", 0.0) in harness.events
    assert not any(event[0] in {"unknown", "breaker_complete"} for event in harness.events)
    assert any(event[0] == "breaker_abandon" for event in harness.events)
    assert limits.bounded_http_timeout(90) == 90


def test_429_that_consumes_deadline_does_not_fall_through(harness, monkeypatch):
    clock = Clock()
    set_chain(harness)
    original = harness.provider_caller

    def caller(provider):
        def slow(*args, **kwargs):
            result = original(provider)(*args, **kwargs)
            clock.advance(2)
            return result
        return slow

    monkeypatch.setattr(harness, "provider_caller", caller)
    result = invoke(harness, clock, deadline_seconds=1)
    assert result["reason"] == "deadline_exceeded"
    assert providers_called(harness) == ["openai"]
    assert ("settle", "res-openai", 0.0) in harness.events


def test_nested_gateway_inherits_parent_remaining_deadline(harness):
    clock = Clock()
    with limits.provider_deadline(clock.monotonic(), clock.monotonic):
        result = invoke(harness, clock, deadline_seconds=90)
    assert result["deadline_seconds"] == 0
    assert providers_called(harness) == []
    assert result["reason"] == "deadline_exceeded"


def test_cache_lookup_time_is_inside_deadline(harness, monkeypatch):
    clock = Clock()

    def slow_cache(**_kwargs):
        clock.advance(2)
        return {"status": "success", "text": "cached"}

    monkeypatch.setattr(target, "serve_cached_result", slow_cache)
    result = invoke(harness, clock, deadline_seconds=1)
    assert result["reason"] == "deadline_exceeded"
    assert result["text"] == ""
    assert providers_called(harness) == []


def successful_response_chain(harness, failure, usage):
    harness.candidates = [("openai", "openai-exact", True), ("google", "google-exact", True)]
    harness.configured = {"openai", "google"}
    harness.provider_results = {
        "openai": {
            "status": "success", "provider": "openai",
            "model": "wrong-model" if failure == "model_mismatch" else "openai-exact",
            "text": "received" if failure == "model_mismatch" else "",
            **usage,
        },
        "google": {
            "status": "success", "provider": "google", "model": "google-exact",
            "text": "accepted", "input_tokens": 100, "output_tokens": 20,
        },
    }


@pytest.mark.parametrize("failure", ["empty_response", "model_mismatch"])
@pytest.mark.parametrize("usage", [{}, {"input_tokens": 0, "output_tokens": 0}, {"input_tokens": "invalid", "output_tokens": None}])
def test_unmetered_success_shape_failure_holds_budget_before_any_settlement(harness, failure, usage):
    successful_response_chain(harness, failure, usage)

    result = invoke(harness, Clock())

    assert result["reason"] == "provider_outcome_unknown"
    assert result["provider_attempts"] == 1
    assert result["budget_reservation_key"] == "res-openai"
    assert providers_called(harness) == ["openai"]
    assert ("unknown", "res-openai") in harness.events
    assert not any(event[0] in {"settle", "release", "cache_store"} for event in harness.events)
    assert any(error["status"] == failure for error in result["errors"])


@pytest.mark.parametrize("failure", ["empty_response", "model_mismatch"])
def test_metered_success_shape_failure_can_use_bounded_fallback(harness, failure):
    successful_response_chain(harness, failure, {"input_tokens": 100, "output_tokens": 2})

    result = invoke(harness, Clock())

    assert result["status"] == "success"
    assert result["provider"] == "google"
    assert result["provider_attempts"] == 2
    assert providers_called(harness) == ["openai", "google"]
    assert ("settle", "res-openai", 0.00104) in harness.events
    assert ("settle", "res-google", 0.0014) in harness.events
    assert not any(event[0] == "unknown" for event in harness.events)


@pytest.mark.parametrize("usage", [{}, {"input_tokens": 0, "output_tokens": 0}])
def test_unmetered_valid_text_cannot_settle_zero_or_enter_cache(harness, usage):
    harness.provider_results["openai"] = {
        "status": "success", "provider": "openai", "model": "gpt-exact",
        "text": "usable answer", **usage,
    }

    result = invoke(harness, Clock())

    assert result["reason"] == "provider_outcome_unknown"
    assert result["text"] == ""
    assert ("unknown", "res-openai") in harness.events
    assert not any(event[0] in {"settle", "release", "cache_store"} for event in harness.events)
    assert not any(row["status"] == "success" for row in harness.records)
