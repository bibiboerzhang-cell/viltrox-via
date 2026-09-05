"""Offline transport/receipt boundary checks. All HTTP uses MockTransport."""
from __future__ import annotations

import json
from contextvars import Context

import httpx
import pytest

from app.platform import llm_gateway_providers as providers
from app.platform.llm_canary_transport import (
    canary_active, consume_request_options, openai_response_evidence,
    single_request_canary,
)
from scripts.ops import vkpi_stage1_model_canary as canary
from tests.test_vkpi_stage1_model_canary_limits import _run
from tests.test_vkpi_stage1_model_canary import _success_invoker
from tests.test_vkpi_stage1_model_canary import _authorized_environment, _FakeReservations


def test_allowance_is_single_use_context_local_and_restored():
    assert consume_request_options() == {}
    with single_request_canary():
        assert canary_active()
        assert Context().run(canary_active) is False
        assert consume_request_options() == {"follow_redirects": False}
        with pytest.raises(RuntimeError, match="request_limit"):
            consume_request_options()
        with pytest.raises(RuntimeError, match="nested_canary"):
            with single_request_canary():
                pass
    assert consume_request_options() == {}
    with single_request_canary():
        assert consume_request_options() == {"follow_redirects": False}


def test_probe_redirect_is_one_http_request_normal_call_keeps_legacy_behavior(monkeypatch):
    requests = []

    def handle(request):
        requests.append(request)
        if request.url.path == "/probe":
            return httpx.Response(307, headers={"Location": "/redirected"})
        return httpx.Response(200, json={"ok": True})

    with httpx.Client(transport=httpx.MockTransport(handle), follow_redirects=True) as client:
        monkeypatch.setattr(providers, "_get_http_client", lambda: client)
        with single_request_canary():
            with pytest.raises(httpx.HTTPStatusError):
                providers._request_json("https://canary.invalid/probe", {}, {}, 5)
            with pytest.raises(RuntimeError, match="request_limit"):
                providers._request_json("https://canary.invalid/probe", {}, {}, 5)
        assert len(requests) == 1
        assert providers._request_json("https://canary.invalid/probe", {}, {}, 5) == {"ok": True}
        assert len(requests) == 3


@pytest.mark.parametrize("usage", [None, {}, {"input_tokens": 8}, {"output_tokens": 2},
    {"input_tokens": True, "output_tokens": 2}, {"input_tokens": "8", "output_tokens": 2},
    {"input_tokens": 8.1, "output_tokens": 2}, {"input_tokens": -1, "output_tokens": 2}])
def test_raw_usage_presence_is_not_inferred_from_normalized_zero(usage):
    assert openai_response_evidence({"usage": usage})["usage_complete"] is False


def test_explicit_zero_is_present_but_not_proof_of_a_free_call():
    evidence = openai_response_evidence({"usage": {"input_tokens": 0, "output_tokens": 0}})
    assert evidence["usage_complete"] is True
    row = canary.build_plan(only_bindings=("openai/gpt-5.6-luna",), max_calls=1).selected[0]
    assert canary._actual_cost_micro_usd(row, {**evidence, "input_tokens": 0, "output_tokens": 0}) is None


def test_default_probe_uses_nonstored_response_and_preserves_raw_identity(monkeypatch):
    seen = []

    def handle(request):
        seen.append(json.loads(request.content))
        return httpx.Response(200, json={"output_text": canary.CANARY_EXPECTED_RESPONSE,
            "status": "completed", "usage": {"input_tokens": 8, "output_tokens": 2}})

    with httpx.Client(transport=httpx.MockTransport(handle)) as client:
        monkeypatch.setattr(providers, "_get_http_client", lambda: client)
        monkeypatch.setattr(providers, "_get_api_key", lambda _: "synthetic-never-sent")
        monkeypatch.setitem(canary.llm_gateway._PROVIDER_CALLERS, "openai", providers._call_openai)
        raw = canary._default_live_invoker("openai/gpt-5.6-luna", canary.CANARY_PROMPT, 16, 5)
    assert len(seen) == 1 and seen[0]["store"] is False
    assert seen[0]["max_output_tokens"] == 16
    assert seen[0]["service_tier"] == "default"
    assert raw["usage_complete"] is True and raw["response_model_reported"] is False
    row = canary.build_plan(only_bindings=("openai/gpt-5.6-luna",), max_calls=1).selected[0]
    assert canary._safe_provider_result(row, raw, latency_ms=1)["status"] == "response_model_unreported"
    assert not canary_active()


@pytest.mark.parametrize("tier,cached,writes,supported", [
    ("default", 0, 0, True), ("priority", 0, 0, False), (None, 0, 0, False),
    ("default", 8, 0, False), ("default", 0, 8, False), ("default", 0, None, False),
    ("default", False, 0, False),
])
def test_unsupported_billing_details_remain_unknown(tier, cached, writes, supported):
    evidence = openai_response_evidence({"service_tier": tier, "usage": {
        "input_tokens": 8, "output_tokens": 2,
        "input_tokens_details": {"cached_tokens": cached, "cache_write_tokens": writes},
    }})
    assert evidence["cost_usage_supported"] is supported
    row = canary.build_plan(only_bindings=("openai/gpt-5.6-luna",), max_calls=1).selected[0]
    actual = canary._actual_cost_micro_usd(row, {**evidence, "input_tokens": 8,
        "output_tokens": 2, "cost_micro_usd": 9, "model": row.model,
        "response_model_reported": True})
    assert actual == (9 if supported else None)


def test_missing_one_usage_field_retains_unknown_reservation_and_stops():
    def invoke(*args):
        return {**_success_invoker(*args), "usage_complete": False, "cost_micro_usd": 1}

    report, rows, reservations, _, calls = _run(invoker=invoke)
    assert len(calls) == 1
    assert rows[0]["status"] == "cost_accounting_failed"
    assert reservations.unknown == ["reservation-1"]
    assert reservations.settled == []
    assert report["accounting"]["observed_cost_micro_usd"] == 0


@pytest.mark.parametrize("status", ["incomplete", "failed", "", "in_progress"])
def test_nonterminal_or_unreported_response_accounts_known_usage_but_stops(status):
    def invoke(*args):
        return {**_success_invoker(*args), "provider_response_status": status}

    report, rows, reservations, _, calls = _run(invoker=invoke)
    assert len(calls) == len(reservations.settled) == 1
    assert rows[0]["status"] == "response_incomplete_or_unreported"
    assert rows[1]["status"] == "not_attempted_after_fail_closed"
    assert report["all_selected_bindings_succeeded"] is False


@pytest.mark.parametrize("reported,model,expected", [
    (False, "gpt-5.6-luna", "response_model_unreported"),
    (True, "", "response_model_unreported"),
    (True, "different-exact-model", "model_mismatch"),
])
def test_missing_or_mismatched_model_never_settles_at_requested_price(reported, model, expected):
    def invoke(*args):
        return {**_success_invoker(*args), "model": model,
            "response_model_reported": reported, "usage_complete": True,
            "cost_usage_supported": True, "cost_micro_usd": 9}

    report, rows, reservations, _, calls = _run(invoker=invoke)
    assert len(calls) == 1 and rows[0]["status"] == expected
    assert reservations.unknown == ["reservation-1"] and reservations.settled == []
    assert report["accounting"]["verified_calls"] == 0


@pytest.mark.parametrize("binding", ["google/gemini-3.6-flash", "anthropic/claude-sonnet-5"])
def test_real_adapters_without_strict_evidence_are_blocked_before_reservation(monkeypatch, binding):
    options = {"only_bindings": (binding,), "max_calls": 1}
    plan = canary.build_plan(**options)
    reservations = _FakeReservations()
    calls = []
    monkeypatch.setattr(canary, "_default_live_invoker", lambda *args: calls.append(args))
    report = canary.run_canary(live=True, **options, environment=_authorized_environment(plan),
        provider_configured=lambda _: True, budget_checker=lambda _: True,
        reservation_manager=reservations, is_production=False)
    assert calls == reservations.reserved == []
    assert report["provider_calls_performed"] == 0
    assert next(row for row in report["results"] if row["binding"] == binding)["status"] == "provider_evidence_unsupported"
