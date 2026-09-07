"""One-call probe regression: synthetic HTTP, in-memory accounting, no services."""
from __future__ import annotations

import copy
import json
from decimal import Decimal
from types import SimpleNamespace

import httpx
import pytest

from app.platform import llm_gateway_providers as providers
from app.platform.llm_canary_transport import canary_active
from scripts.ops import kol_live_llm_probe as probe


PLAN_ID = "a" * 64
QUERY = "Find US portrait photographers reviewing 135mm lenses"
QUERY_PLAN = {"intent": "Find portrait lens reviewers", "platform": "youtube", "market": "US",
              "queries": ["135mm portrait lens review US"],
              "qualification_checks": ["Check actual country evidence"],
              "limitations": ["A search phrase is not evidence of creator relevance"]}


def response():
    return {"id": "resp_synthetic_123", "model": probe.MODEL, "status": "completed", "service_tier": "default",
            "usage": {"input_tokens": 1000, "output_tokens": 100, "total_tokens": 1100,
                      "input_tokens_details": {"cached_tokens": 200, "cache_write_tokens": 300}},
            "output": [{"type": "message", "status": "completed",
                        "content": [{"type": "output_text", "text": json.dumps(QUERY_PLAN)}]}]}


@pytest.fixture
def harness(monkeypatch, tmp_path):
    events, requests, records = [], [], []
    state = SimpleNamespace(body=response(), http_status=200, fail=None, now=100.0,
                            reserve_delay=0, start_delay=0, http_delay=0, settlement_bad=False,
                            unknown_ok=True, events=events, requests=requests, records=records,
                            output=tmp_path, remaining=100)
    scopes = ("monthly_total", "provider:openai", probe.COST_SCOPE)

    def reserve(**kwargs):
        events.append(("reserve", kwargs))
        state.now += state.reserve_delay
        if state.fail == "reserve":
            raise RuntimeError("synthetic-secret-reservation")
        return SimpleNamespace(reservation_key="llmres-fixture", cumulative_scopes=scopes)

    def start(key):
        events.append(("start", key))
        state.now += state.start_delay
        if state.fail == "start":
            raise RuntimeError("synthetic-secret-start")

    def unknown(key):
        events.append(("unknown", key))
        return state.unknown_ok

    def release(key):
        events.append(("release", key))
        return not any(event[0] == "start" for event in events)

    def settle(key, actual):
        events.append(("settle", actual))
        if state.fail == "settle":
            raise RuntimeError("synthetic-secret-settle")
        micro = int(actual * 1_000_000)
        return {"settled": True, "readback_verified": not state.settlement_bad,
                "actual_cost_micro_usd": micro, "scopes_updated": list(scopes),
                "scope_deltas_micro_usd": dict.fromkeys(scopes, micro)}

    def record(**kwargs):
        records.append(kwargs)
        events.append(("record", kwargs["cost_micro_usd"]))
        if state.fail == "record":
            raise RuntimeError("synthetic-secret-ledger")
        micro = kwargs["cost_micro_usd"]
        return {"call": {"call_uid": "call-fixture", "cost_micro_usd": micro},
                "cost_ledger": {"ledger_id": 1, "cost_micro_usd": micro}}

    def handle(request):
        requests.append(request)
        state.now += state.http_delay
        if state.fail == "http":
            raise httpx.ReadTimeout("synthetic-secret-key", request=request)
        return httpx.Response(state.http_status, json=state.body,
                              headers={"Location": "https://must-not-follow.invalid"})

    monkeypatch.setattr(probe, "IS_PRODUCTION", False)
    monkeypatch.delenv("VKPI_LLM_GATEWAY_FORCE_OFFLINE", raising=False)
    monkeypatch.setattr(probe.time, "monotonic", lambda: state.now)
    monkeypatch.setattr(probe.gateway, "_get_api_key", lambda _: "synthetic-never-sent-key")
    monkeypatch.setattr(probe.gateway, "_monthly_budget_cents", lambda: 100)
    monkeypatch.setattr(probe.gateway, "_budget_remaining_cents", lambda: state.remaining)
    monkeypatch.setattr(probe.gateway, "record_call", record)
    for name, implementation in (("reserve_llm_budget", reserve), ("mark_llm_provider_started", start),
        ("mark_llm_provider_unknown", unknown), ("release_llm_reservation", release), ("settle_llm_reservation", settle)):
        monkeypatch.setattr(probe.reservations, name, implementation)
    with httpx.Client(transport=httpx.MockTransport(handle), follow_redirects=True) as client:
        monkeypatch.setattr(providers, "_get_http_client", lambda: client)
        yield state


def run(harness):
    return probe.run_probe(QUERY, harness.output, PLAN_ID)


def test_single_structured_request_four_rate_billing_and_verified_receipt(harness):
    result = run(harness)
    assert result["status"] == "success" and result["queries"] == QUERY_PLAN["queries"]
    assert result["billing"]["cost_micro_usd"] == 299  # 500*.20 + 200*.02 + 300*.25 + 100*1.20
    assert result["accounting_verified"] and result["reservation_state"] == "settled"
    assert result["response_id"] == "resp_synthetic_123"
    assert result["provider_response_status"] == "completed"
    assert result["transport_status"] == "success"
    assert result["schema_status"] == "success" and result["semantic_status"] == "qualified"
    assert len(harness.requests) == 1 and not canary_active()
    payload = json.loads(harness.requests[0].content)
    assert payload["model"] == probe.MODEL and payload["store"] is False
    assert payload["service_tier"] == "default" and payload["max_output_tokens"] == 1024
    assert payload["reasoning"] == {"effort": "none"}
    assert payload["text"]["format"]["strict"] is True and "tools" not in payload
    assert payload["prompt_cache_options"] == {"mode": "explicit"}
    assert len(harness.requests[0].content) <= probe.MAX_PAYLOAD_BYTES
    reserved = harness.events[0][1]
    assert reserved["require_cost_scope"] is True and reserved["local_evaluation_no_reap"] is True
    assert reserved["estimated_cost_usd"] == Decimal("0.05")
    assert reserved["cost_scope"] == "cron:kol_live_query_eval"
    assert probe.WORST_STANDARD_COST_USD < probe.RESERVE_USD < Decimal("0.10")
    assert harness.records[0]["prompt"] == "" and harness.records[0]["force_cost_ledger"] is True
    assert QUERY not in json.dumps(harness.records)
    saved = json.loads(next(harness.output.glob("llm-probe-*.json")).read_text())
    assert saved == result and "synthetic-never-sent-key" not in json.dumps(saved)
    assert run(harness)["status"] == "receipt_exists_or_unwritable"
    assert len(harness.requests) == 1


@pytest.mark.parametrize("mutation", ["missing_usage", "missing_write", "missing_cached", "zero", "bool", "tier", "model", "total", "overflow", "overlap", "nonterminal"])
def test_uncertain_billing_keeps_reservation_and_never_returns_queries(harness, mutation):
    body = harness.body
    if mutation == "missing_usage": body.pop("usage")
    elif mutation == "missing_write": body["usage"]["input_tokens_details"].pop("cache_write_tokens")
    elif mutation == "missing_cached": body["usage"]["input_tokens_details"].pop("cached_tokens")
    elif mutation == "zero": body["usage"].update(input_tokens=0, output_tokens=0, total_tokens=0, input_tokens_details={"cached_tokens": 0, "cache_write_tokens": 0})
    elif mutation == "bool": body["usage"]["input_tokens"] = True
    elif mutation == "tier": body["service_tier"] = "priority"
    elif mutation == "model": body["model"] = "another-model"
    elif mutation == "total": body["usage"]["total_tokens"] = 10
    elif mutation == "overflow": body["usage"].update(input_tokens=20_000, total_tokens=20_100)
    elif mutation == "overlap": body["usage"]["input_tokens_details"]["cache_write_tokens"] = 999
    elif mutation == "nonterminal": body["status"] = "in_progress"
    result = run(harness)
    assert result["status"] == "cost_accounting_unknown" and result["queries"] == []
    assert result["reservation_state"] == "unknown"
    assert not any(event[0] in {"settle", "release"} for event in harness.events)
    assert len(harness.requests) == 1
    assert harness.records[0]["metadata"]["cost_zero_is_placeholder"] is True


@pytest.mark.parametrize("failure", ["http", "record", "settle"])
def test_failures_are_redacted_and_never_retried(harness, failure):
    harness.fail = failure
    result = run(harness)
    assert result["status"] != "success" and result["queries"] == []
    assert result["reservation_state"] == "unknown" and len(harness.requests) == 1
    assert "synthetic-secret" not in json.dumps(result)
    assert not any(event[0] == "release" for event in harness.events)
    if failure == "http": assert result["transport_error_code"] == "ReadTimeout"


@pytest.mark.parametrize("http_status", [307, 429, 503])
def test_http_failure_does_not_redirect_or_retry(harness, http_status):
    harness.http_status = http_status
    result = run(harness)
    assert len(harness.requests) == 1 and result["http_status"] == http_status
    assert result["reservation_state"] == "unknown"


@pytest.mark.parametrize("change", ["incomplete", "invalid_json", "three_queries", "nonascii", "long_query", "tool"])
def test_known_charge_is_settled_even_when_business_output_is_rejected(harness, change):
    if change == "incomplete": harness.body["status"] = "incomplete"
    elif change == "invalid_json": harness.body["output"][0]["content"][0]["text"] = "invalid"
    elif change == "tool": harness.body["output"].insert(0, {"type": "web_search_call"})
    else:
        plan = copy.deepcopy(QUERY_PLAN)
        plan["queries"] = {"three_queries": ["one", "two", "three"], "nonascii": ["人像"], "long_query": ["x" * 121]}[change]
        harness.body["output"][0]["content"][0]["text"] = json.dumps(plan)
    result = run(harness)
    assert result["status"] != "success" and result["queries"] == []
    assert result["accounting_verified"] and result["reservation_state"] == "settled"


def test_reasoning_metadata_is_not_text_and_does_not_prevent_success(harness):
    harness.body["output"].insert(0, {"type": "reasoning", "summary": []})
    assert run(harness)["status"] == "success"


def test_late_success_accounts_before_reporting_timeout(harness):
    harness.http_delay = 31
    result = run(harness)
    assert result["status"] == "deadline_exceeded" and result["queries"] == []
    assert result["accounting_verified"] and result["reservation_state"] == "settled"
    assert result["query_plan"] == QUERY_PLAN  # Preserved for audit, never forwarded after timeout.


@pytest.mark.parametrize("stage", ["reserve", "start"])
def test_deadline_before_http_is_zero_call_and_does_not_invent_release(harness, stage):
    setattr(harness, f"{stage}_delay", 31)
    result = run(harness)
    assert not harness.requests and result["provider_calls_performed"] == 0
    assert result["reservation_state"] == ("released" if stage == "reserve" else "retained_unconfirmed")
    assert not harness.records


def test_readback_mismatch_never_claims_verified_or_delivers_queries(harness):
    harness.settlement_bad = True
    result = run(harness)
    assert result["status"] == "accounting_failed" and not result["accounting_verified"]
    assert result["queries"] == []


@pytest.mark.parametrize("block", ["production", "offline", "monthly", "reservation", "query", "plan"])
def test_preflight_blocks_without_http_or_paid_ledger(harness, monkeypatch, block):
    query, plan_id = QUERY, PLAN_ID
    if block == "production": monkeypatch.setattr(probe, "IS_PRODUCTION", True)
    elif block == "offline": monkeypatch.setenv("VKPI_LLM_GATEWAY_FORCE_OFFLINE", "1")
    elif block == "monthly": harness.remaining = 4
    elif block == "reservation": harness.fail = "reserve"
    elif block == "query": query = "x" * (probe.MAX_QUERY_BYTES + 1)
    elif block == "plan": plan_id = "not-an-authorized-plan"
    result = probe.run_probe(query, harness.output, plan_id)
    assert result["status"] != "success" and not harness.requests and not harness.records


@pytest.mark.parametrize("problem, expected_status", [
    ("duplicate", "invalid"), ("market_fragment", "invalid"), ("overlap", "needs_review"),
])
def test_semantic_rejection_settles_real_usage_before_stopping_discovery(harness, monkeypatch, problem, expected_status):
    query_plan = copy.deepcopy(QUERY_PLAN)
    if problem == "duplicate":
        query_plan["queries"] = ["street photography YouTube", "street photography creator"]
    elif problem == "market_fragment":
        query_plan["market"] = "US audience relevance; creator's"
    else:
        query_plan["queries"] = ["street night photography", "street low light photography"]
    assert probe._valid_plan(query_plan) is True  # JSON contract alone is insufficient.
    harness.body["output"][0]["content"][0]["text"] = json.dumps(query_plan)
    assess = probe.assess_search_plan_semantics

    def inspect_after_settlement(plan):
        assert any(event[0] == "settle" for event in harness.events)
        assert any(event[0] == "record" for event in harness.events)
        harness.events.append(("semantic", plan))
        return assess(plan)

    monkeypatch.setattr(probe, "assess_search_plan_semantics", inspect_after_settlement)
    result = run(harness)
    assert result["transport_status"] == "success" and result["schema_status"] == "success"
    assert result["status"] != "success" and result["semantic_status"] == expected_status
    assert result["query_plan"] == query_plan and result["queries"] == []
    assert result["semantic_review"]["original_plan"] == query_plan
    assert result["accounting_verified"] is True and result["reservation_state"] == "settled"
    assert result["billing"]["cost_micro_usd"] == 299
    assert [event[0] for event in harness.events].index("settle") < [event[0] for event in harness.events].index("semantic")
    assert len(harness.requests) == 1 and len(harness.records) == 1
    assert not any(event[0] in {"unknown", "release"} for event in harness.events)
    assert harness.records[0]["metadata"]["semantic_status"] == "not_evaluated"
    assert run(harness)["status"] == "receipt_exists_or_unwritable"
    assert len(harness.requests) == 1


def test_semantic_evaluator_failure_does_not_reclassify_settled_charge(harness, monkeypatch):
    def fail(_):
        raise RuntimeError("synthetic-secret-semantic")

    monkeypatch.setattr(probe, "assess_search_plan_semantics", fail)
    result = run(harness)
    assert result["status"] == "semantic_validation_failed"
    assert result["semantic_status"] == "evaluation_failed"
    assert result["accounting_verified"] is True and result["reservation_state"] == "settled"
    assert result["query_plan"] == QUERY_PLAN and result["queries"] == []
    assert len(harness.requests) == 1 and not any(event[0] == "unknown" for event in harness.events)
    assert "synthetic-secret" not in json.dumps(result)


def test_unknown_billing_never_invokes_semantic_qualification(harness, monkeypatch):
    harness.body.pop("usage")

    def forbidden(_):
        pytest.fail("Semantics must not run before confirmed usage settlement")

    monkeypatch.setattr(probe, "assess_search_plan_semantics", forbidden)
    result = run(harness)
    assert result["status"] == "cost_accounting_unknown"
    assert result["semantic_status"] == "not_evaluated"
    assert result["queries"] == []


@pytest.mark.parametrize("bad_review", [None, {}, {"status": "pretend_qualified"}])
def test_malformed_semantic_result_preserves_known_settlement(harness, monkeypatch, bad_review):
    monkeypatch.setattr(probe, "assess_search_plan_semantics", lambda _: bad_review)
    result = run(harness)
    assert result["status"] == "semantic_validation_failed"
    assert result["semantic_status"] == "evaluation_failed"
    assert result["accounting_verified"] is True and result["reservation_state"] == "settled"
    assert result["query_plan"] == QUERY_PLAN and result["queries"] == []
    assert len(harness.requests) == 1 and not any(event[0] == "unknown" for event in harness.events)
