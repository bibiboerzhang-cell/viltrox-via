"""Bounded qualification for legacy online calls; every external boundary is stubbed."""
from __future__ import annotations

import asyncio
from copy import deepcopy
from types import SimpleNamespace

import pytest

from app.domains.kol import operator_search_spec as specs
from app.domains.kol import profile_discovery_pipeline_online as online


def _forbidden(*_args, **_kwargs):
    raise AssertionError("unqualified/extra-provider boundary crossed")


def _int(value, fallback=0):
    try:
        return int(value)
    except (TypeError, ValueError):
        return fallback


def _payload(body=None):
    payload = {"query_text": "street photographers", "_worker_planned": True, **deepcopy(body or {})}
    payload["operator_search_spec"] = specs.build_operator_search_spec(
        plan={}, body=payload, recall_filters={}, market="", platforms=["youtube"],
    )
    return payload


def _request(payload, *, cells=None):
    filters = (payload.get("operator_search_spec") or {}).get("filters") or {}
    return online.DiscoveryRequest(
        session_id=71, query="street photographers", payload=payload,
        operator_anchor={}, resolved_platforms=["youtube"], normalized_market="",
        followers_min=filters.get("followers_min"), followers_max=filters.get("followers_max"),
        follower_source="operator_filter" if any(key in filters for key in ("followers_min", "followers_max")) else "not_requested",
        follower_filter={"unknown_policy": "pending"}, query_cells=cells or [],
        query_cells_omitted=False, base_count=0, advance_limit=30,
    )


def _deps(collector=None, attached=None):
    return SimpleNamespace(
        text=lambda value: str(value or "").strip(), int_value=_int,
        load_persona=lambda _sku: {}, logger=SimpleNamespace(info=lambda *_args: None),
        recall_favorite_exclusion=SimpleNamespace(
            favorited_identity_keys=lambda: set(),
            exclude_favorited_online_candidates=lambda rows, **_kwargs: (rows, {}),
            merge_diagnostics=lambda *_args: {},
        ),
        profile_discovery_evidence=SimpleNamespace(
            planned_youtube_variants=lambda _query: ["street photographers"],
            observe_round=lambda **kwargs: {"round_no": kwargs["round_no"]},
        ),
        profile_discovery_rounds=SimpleNamespace(
            platforms_for_round=lambda *_args: ["youtube"],
            round_cost_forecast=lambda **kwargs: kwargs, forecast_line=lambda _forecast: "fixture",
        ),
        profile_discovery_targeted_batch=SimpleNamespace(
            fetch_targeted_round=_forbidden,
            build_pipeline_round_gate=lambda **_kwargs: None,
            exhaustion_reason=lambda _cells: "no_more_candidates",
            finalize_online_result=lambda result, **_kwargs: result,
        ),
        profile_online_qualification=SimpleNamespace(
            ONLINE_SUPPORTED_PLATFORMS={"youtube"}, ONLINE_MAX_PROVIDER_ROUNDS=3,
            online_policy=lambda **kwargs: kwargs,
            collect_strict_online_for_session=collector or _forbidden,
        ),
        search_sessions=SimpleNamespace(
            attach_online_qualified_result=lambda session_id, result: attached.append((session_id, deepcopy(result))) if attached is not None else None,
        ),
    )


@pytest.mark.parametrize("body", [
    {"filters": {"languages": ["en"]}},
    {"filters": {"profile_types": ["creator"]}},
    {"creator_countries": ["GB"]},
    {"audience_markets": ["US"]},
    {"filters": {"followers_min": 1000}},
    {"filters": {"followers_max": 50000}},
])
def test_worker_rebuilt_explicit_conditions_route_to_bounded_qualified_runner(monkeypatch, body):
    calls, diagnostics = [], []

    class Runner:
        def __init__(self, **kwargs):
            calls.append(kwargs)

        async def run(self):
            return {"status": "insufficient", "new_creators": []}, {"status": "insufficient"}

    monkeypatch.setattr(online, "_StrictOnlineRunner", Runner)
    monkeypatch.setattr(online, "_legacy_discovery", _forbidden)
    monkeypatch.setattr(online, "_attach_legacy_progress", _forbidden)
    monkeypatch.setattr(online, "_record_diagnostics", lambda **kwargs: diagnostics.append(kwargs))
    request = _request(_payload(body))
    before = deepcopy(request.payload)
    result = asyncio.run(online.run_discovery(request, discover=_forbidden, annotate_priority=_forbidden, deps=_deps()))
    assert len(calls) == 1
    assert calls[0]["bounded_legacy"] is True
    assert result.new_discovery["status"] == "insufficient"
    assert diagnostics[0]["strict_online"] is True
    assert request.payload == before


@pytest.mark.parametrize("payload", [
    {}, {"filters": {"languages": ["en"]}},
    {"operator_search_spec": {"schema": "invalid"}},
    _payload(), _payload({"creator_countries": [], "audience_markets": []}),
])
def test_no_rebuilt_spec_or_no_conditions_keeps_existing_legacy_entry(monkeypatch, payload):
    calls = []

    async def legacy(**kwargs):
        calls.append(kwargs)
        return {"status": "ok", "new_creators": []}

    monkeypatch.setattr(online, "_StrictOnlineRunner", _forbidden)
    monkeypatch.setattr(online, "_legacy_discovery", legacy)
    monkeypatch.setattr(online, "_attach_legacy_progress", lambda **_kwargs: None)
    monkeypatch.setattr(online, "_record_diagnostics", lambda **_kwargs: None)
    asyncio.run(online.run_discovery(_request(payload), discover=_forbidden, annotate_priority=_forbidden, deps=_deps()))
    assert len(calls) == 1


@pytest.mark.parametrize("limits,expected,per_platform", [
    ({}, 15, 15),
    ({"new_discovery_limit": 7, "new_discovery_per_platform_limit": 4}, 7, 4),
    ({"new_discovery_limit": 1000, "new_discovery_per_platform_limit": 1000}, 50, 50),
    ({"new_discovery_limit": 0, "new_discovery_per_platform_limit": 0}, 1, 1),
])
@pytest.mark.parametrize("with_cells", [False, True])
def test_real_bounded_runner_uses_one_legacy_sized_batch_without_auto_enroll(monkeypatch, limits, expected, per_platform, with_cells):
    provider_calls, collector_calls, attached = [], [], []

    async def discover(**kwargs):
        provider_calls.append(kwargs)
        return {"status": "ok", "new_creators": [{"handle": "fixture"}], "has_more": True,
                "next_cursor": {"youtube": "more"}, "platforms": ["youtube"]}

    async def collect(**kwargs):
        collector_calls.append(kwargs)
        batch = await kwargs["fetch_batch"](round_no=1, limit=150, cursor=None)
        return {"status": "insufficient", "items": batch["new_creators"], "provider_calls_performed": True}

    # The bounded runner may reuse the legacy single-fetch helper, but its
    # provider call must explicitly disable auto-enrollment and be qualified.
    monkeypatch.setattr(online, "_attach_legacy_progress", _forbidden)
    monkeypatch.setattr(online, "_record_diagnostics", lambda **_kwargs: None)
    payload = _payload({"filters": {"languages": ["en"]}, **limits})
    cells = [{"query_cell_id": "must-not-fan-out", "primary_query": "street photo"}] if with_cells else []
    outcome = asyncio.run(online.run_discovery(_request(payload, cells=cells), discover=discover,
                                              annotate_priority=_forbidden, deps=_deps(collect, attached)))
    assert len(provider_calls) == len(collector_calls) == len(attached) == 1
    assert provider_calls[0]["limit"] == expected
    assert provider_calls[0]["per_platform_limit"] == per_platform
    assert provider_calls[0]["auto_enroll"] is False
    assert collector_calls[0]["candidate_budget"] == expected
    assert collector_calls[0]["max_provider_rounds"] == 1
    assert collector_calls[0]["policy"]["languages"] == ["en"]
    assert outcome.base_count == 1


@pytest.mark.parametrize("second_round", [1, 2])
def test_bounded_fetch_cannot_spend_again_even_if_callback_is_repeated(second_round):
    calls = []

    async def discover(**kwargs):
        calls.append(kwargs)
        return {"status": "ok", "new_creators": [], "has_more": True, "next_cursor": {"youtube": "more"}}

    runner = online._StrictOnlineRunner(
        request=_request(_payload({"filters": {"languages": ["en"]}})),
        discovery_kwargs={}, ledger=online._EvidenceLedger(), favorite_identity_keys=set(),
        discover=discover, deps=_deps(), bounded_legacy=True,
    )

    async def twice():
        await runner.fetch_batch(round_no=1, limit=150, cursor=None)
        return await runner.fetch_batch(round_no=second_round, limit=150, cursor={"youtube": "more"})

    second = asyncio.run(twice())
    assert len(calls) == 1
    assert second["has_more"] is False
    assert second["provider_calls"] is False
    assert second["new_creators"] == []
