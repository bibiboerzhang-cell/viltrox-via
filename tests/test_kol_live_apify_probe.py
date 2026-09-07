"""Synthetic transport + fake accounting only; canonical start wrapper is real."""
from __future__ import annotations

import copy
import json
from types import SimpleNamespace

import pytest

from app.platform import apify_budget as budget
from app.platform.apify_budget_contracts import ApifyBudgetDecision, _execution_context
from scripts.ops import kol_live_apify_probe as probe


PLAN = "a" * 64
QUERIES = ["US street photography creators", "night photography tutorial"]


@pytest.fixture
def harness(monkeypatch, tmp_path):
    run = {"id": "run_fixture", "status": "SUCCEEDED", "defaultDatasetId": "dataset_fixture",
           "usageTotalUsd": .04, "chargedEventCounts": {"result": 10},
           "pricingInfo": {"pricingModel": "PAY_PER_EVENT", "pricingPerEvent": {
               "actorChargeEvents": {"result": {"eventTieredPricingUsd": {
                   "FREE": {"tieredEventPriceUsd": .004}}}}}}}
    state = SimpleNamespace(now=1.0, run=run, requests=[], events=[], output=tmp_path,
                            fail=None, total="1", items=[{"id": "vid_fixture", "title": "Street walk",
                            "channelId": "UC_fixture", "channelUrl": "https://youtube.com/@fixture",
                            "email": "private@example.test", "description": "personal contact"}],
                            recorded=True, finalize=True)
    decision = ApifyBudgetDecision(True, "provider:apify", .10, "reserved",
                                    "kol_live_query_eval", probe.ACTOR_ID, "youtube",
                                    "manual_live_probe", reservation_key="reservation_fixture")

    def require(**kwargs):
        assert _execution_context.get() == (f"kol-live:{PLAN}", 1)
        assert kwargs["estimated_cost_usd"] == .10
        state.events.append("reserve")
        if state.fail == "budget":
            raise probe.ApifyBudgetBlocked(decision)
        return decision

    def http_call(**kwargs):
        state.requests.append(kwargs)
        state.now += 10
        path = kwargs["url"]
        if path.endswith("/runs"):
            assert state.events[:2] == ["reserve", "mark_started"]
            if state.fail == "start":
                raise TimeoutError("secret-not-for-output")
            return SimpleNamespace(json=lambda: {"data": {"id": "run_fixture", "status": "RUNNING"}})
        assert "provider_run_identified" in (tmp_path / "apify-events.jsonl").read_text()
        if path.endswith("/abort"):
            if state.fail == "abort":
                raise TimeoutError("secret-not-for-output")
            state.run["status"] = "ABORTED"
            return SimpleNamespace(json=lambda: {"data": copy.deepcopy(state.run)})
        if "/actor-runs/" in path:
            if state.fail == "poll":
                raise TimeoutError("secret-not-for-output")
            return SimpleNamespace(json=lambda: {"data": copy.deepcopy(state.run)})
        if state.fail == "read":
            raise OSError("secret-dataset-error")
        return SimpleNamespace(json=lambda: state.items,
                               headers={"x-apify-pagination-total": state.total})

    def create_client(token, **kwargs):
        assert token == "synthetic-token"
        assert kwargs == {"api_url": "https://api.apify.com", "max_retries": 0, "timeout_secs": 15}
        transports = [SimpleNamespace(follow_redirects=True, close=lambda: state.events.append("close"))
                      for _ in range(2)]
        state.sdk = SimpleNamespace(http_client=SimpleNamespace(call=http_call,
                                    httpx_client=transports[0], httpx_async_client=transports[1]))
        return state.sdk

    monkeypatch.setenv("APIFY_TOKEN", "synthetic-token")
    monkeypatch.setattr(probe.time, "monotonic", lambda: state.now)
    monkeypatch.setattr(probe, "ApifyClient", create_client)
    monkeypatch.setattr(probe, "acquire_provider_execution_claim", lambda *a, **k: 1)
    monkeypatch.setattr(probe, "finalize_provider_execution_claim", lambda *a: state.finalize)
    monkeypatch.setattr(probe, "record_apify_run", lambda *a, **k: {"recorded": state.recorded})
    monkeypatch.setattr(budget, "require_apify_budget", require)
    monkeypatch.setattr(budget, "_mark_provider_started", lambda *a: state.events.append("mark_started"))
    monkeypatch.setattr(budget, "_mark_provider_outcome", lambda *a, **k: state.events.append(("outcome", k)))
    monkeypatch.setattr(budget, "_renew_current_execution_claim", lambda: None)
    return state


def execute(harness):
    return probe.run_probe(QUERIES, harness.output, PLAN)


def test_single_canonical_start_hard_limits_early_journal_and_cleanup(harness):
    result = execute(harness)
    assert result["status"] == "partial" and result["reason"] == "cost_finality_unconfirmed"
    assert result["execution_state"] == "succeeded" and result["data_state"] == "complete"
    assert result["cost_state"] == result["cost_status"] == "provisional"
    assert result["provider_cost_final"] is False
    assert result["automatic_provider_retry_allowed"] is False
    assert result["execution_claim_status"] == "completed"
    assert result["next_action"] == "reconcile_existing_run_cost"
    assert result["run_id"] == "run_fixture" and result["item_count"] == 1
    assert result["cost_usd"] == .04 and result["accounting_status"] == "recorded"
    starts = [r for r in harness.requests if r["url"].endswith("/runs")]
    assert len(starts) == 1
    assert starts[0]["params"] == {"build": "0.0.290", "timeout": 120, "memory": 1024,
                                     "maxTotalChargeUsd": .10, "waitForFinish": 0, "restartOnError": False}
    assert starts[0]["json"] == probe._input(QUERIES)
    assert all("token" not in request["params"] for request in harness.requests)
    assert result["cleanup_status"] == "closed" and harness.events.count("close") == 2
    assert harness.sdk.http_client.httpx_client.follow_redirects is False
    assert _execution_context.get() is None
    assert "email" not in result["items"][0] and "description" not in result["items"][0]
    assert result["claim_status"] == "descriptive_only"
    assert json.loads((harness.output / "apify-result.json").read_text()) == result
    assert result["cost_evidence"]["pricingInfo"]["pricingPerEvent"]["actorChargeEvents"]["result"][
        "eventTieredPricingUsd"]["FREE"] == {"tieredEventPriceUsd": .004}


@pytest.mark.parametrize("queries", [[], ["a", "b", "c"], ["摄影"], ["x" * 121], ["\n"], ["123"]])
def test_invalid_queries_never_touch_provider(harness, queries):
    with pytest.raises(ValueError):
        probe.run_probe(queries, harness.output, PLAN)
    assert harness.requests == [] and harness.events == []


def test_missing_configuration_never_touches_budget(harness, monkeypatch):
    for key in ("APIFY_TOKEN", "APIFY_API_TOKEN", "APIFY_API_KEY"):
        monkeypatch.delenv(key, raising=False)
    result = execute(harness)
    assert result["reason"] == "apify_not_configured" and harness.events == []


@pytest.mark.parametrize("alias", ["APIFY_API_TOKEN", "APIFY_API_KEY"])
def test_credential_aliases(harness, monkeypatch, alias):
    for key in ("APIFY_TOKEN", "APIFY_API_TOKEN", "APIFY_API_KEY"):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv(alias, "synthetic-token")
    result = execute(harness)
    assert result["execution_state"] == "succeeded" and result["cost_status"] == "provisional"


def test_budget_rejection_is_before_start(harness):
    harness.fail = "budget"
    result = execute(harness)
    assert result["status"] == "blocked" and not harness.requests
    assert result["provider_start_attempts"] == 0


def test_uncertain_start_never_retries_or_settles(harness):
    harness.fail = "start"
    result = execute(harness)
    assert result["status"] == "unknown" and result["run_id"] is None
    assert result["provider_start_attempts"] == 1 and len(harness.requests) == 1
    assert result["cost_usd"] is None
    assert "secret" not in json.dumps(result)
    assert not any(isinstance(e, tuple) and e[0] == "settle" for e in harness.events)


def test_timeout_aborts_only_the_known_run_and_settles_actual_cost(harness):
    harness.run["status"] = "RUNNING"
    result = execute(harness)
    assert result["status"] == "failed" and result["provider_status"] == "ABORTED"
    assert result["abort_status"] == "terminal_confirmed"
    assert len([r for r in harness.requests if r["url"].endswith("/abort")]) == 1
    assert result["provider_start_attempts"] == 1 and result["cost_usd"] == .04


def test_read_failure_still_accounts_terminal_actual_cost(harness):
    harness.fail = "read"
    result = execute(harness)
    assert result["status"] == "partial" and result["read_complete"] is False
    assert result["read_error_type"] == "OSError"
    assert result["cost_usd"] == .04 and result["accounting_status"] == "recorded"


@pytest.mark.parametrize("value", [None, 0, -1, float("nan"), float("inf"), True, "0.04"])
def test_unknown_total_never_uses_compute_or_table_estimate(harness, value):
    harness.run["usageTotalUsd"] = value
    harness.run["usageUsd"] = {"ACTOR_COMPUTE_UNITS": .0002}
    result = execute(harness)
    assert result["status"] == "partial" and result["cost_usd"] is None
    assert result["accounting_status"] == "not_settled"
    assert result["execution_state"] == "succeeded" and result["execution_claim_status"] == "completed"
    assert result["automatic_provider_retry_allowed"] is False
    assert result["next_action"] == "reconcile_existing_run_cost"
    assert not any(isinstance(e, tuple) and e[0] == "settle" for e in harness.events)


@pytest.mark.parametrize("total", ["", "11", "2"])
def test_unknown_or_truncated_dataset_is_partial_without_more_reads(harness, total):
    harness.total = total
    result = execute(harness)
    assert result["status"] == "partial" and not result["read_complete"]
    reads = [r for r in harness.requests if "/datasets/" in r["url"]]
    assert len(reads) == 1 and reads[0]["params"]["limit"] == 10


def test_ledger_failure_cannot_be_completed(harness):
    harness.recorded = False
    result = execute(harness)
    assert result["status"] == "partial" and result["accounting_status"] == "ledger_unconfirmed"


def test_same_output_cannot_start_twice(harness):
    execute(harness)
    with pytest.raises(FileExistsError):
        execute(harness)
    assert len([r for r in harness.requests if r["url"].endswith("/runs")]) == 1


def test_expired_deadline_retains_one_known_run_cleanup_window(harness):
    with (harness.output / "direct-journal").open("w") as journal:
        sdk = probe._client("synthetic-token")
        client = probe._BoundedClient(sdk, journal)
        client.run_id, client.latest = "run_fixture", {"status": "RUNNING"}
        (harness.output / "apify-events.jsonl").write_text("provider_run_identified")
        harness.now = client.deadline + 1
        client.abort_if_active()
        client.abort_if_active()
        assert client.abort_status == "terminal_confirmed"
        assert len(harness.requests) == 1 and harness.requests[0]["timeout_secs"] == 15


def test_public_projection_rejects_contact_and_tracking_urls():
    result = probe._public_item({"id": "12345678901", "title": "Contact x@example.test 123-456-7890",
        "channelUrl": "https://youtube.com/@person?email=x@example.test", "email": "x@example.test",
        "url": "https://youtube.com/watch?v=12345678901", "description": "private"})
    assert result["id"] == "12345678901" and result["url"].endswith("12345678901")
    assert "channelUrl" not in result and "email" not in result and "description" not in result
    assert "example.test" not in result["title"] and "123-456" not in result["title"]


def test_public_projection_preserves_source_date_without_inventing_timezone():
    for value in ("2026-09-04", "2026-09-04T12:34:56Z", "2026-09-04T12:34:56"):
        assert probe._public_item({"date": value}) == {"date": value}
    assert probe._public_item({"date": "2026-99-99"}) == {}
