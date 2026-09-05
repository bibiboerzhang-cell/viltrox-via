"""Read-only discovery seams, with fake providers and no persistence."""
from __future__ import annotations

import asyncio
import logging

import pytest

from app.domains.kol import profile_discovery_provider_flow as flow


def _plan():
    return flow.DiscoveryPlan(query="people", search_term="people", relevance_language="en",
                              resolved_platforms=["instagram", "youtube"], market="", safe_limit=30,
                              safe_per_platform=5, leg_limits={}, leg_cursors={}, auto_enroll=False,
                              exclude_chinese=False, exact_query=True)


async def _sequential(platforms, search):
    return [await search(platform) for platform in platforms]


def _run(provider):
    return asyncio.run(flow.search_provider_legs(
        _plan(), enrich_prefilter=None, search_platform=provider,
        annotate_platform_items=lambda rows, **_kw: rows,
        canonicalize_candidates=lambda rows, **_kw: rows,
        platform_signals=lambda _row: set(), run_legs=_sequential,
        deadline_seconds=lambda _platform: 10, logger=logging.getLogger("test"),
    ))


@pytest.mark.parametrize("status,items,error", [
    ("empty", [], False), ("done", [{"handle": "person"}], False),
    ("partial", [{"handle": "person"}], True), ("failed", [], True),
])
def test_result_status_is_not_erased_by_candidates(status, items, error):
    async def provider(*_args, **_kwargs):
        return {"status": status, "items": items}
    result = _run(provider)
    assert result[0]["error"] is error
    state = flow.DiscoveryState(errors=[{"status": status}] if error else [])
    assert flow._response_status(state, result[0]["annotated"]) == (
        "partial" if error and items else "failed" if error else "ready" if items else "empty"
    )


def test_unknown_stops_unstarted_legs_and_future_pages():
    calls = []
    async def provider(platform, *_args, **_kwargs):
        calls.append(platform)
        return {"status": "partial", "items": [{"handle": "person"}],
                "metadata": {"provider_outcome_unknown": True, "has_more": True}}
    outcomes = _run(provider)
    assert calls == ["instagram"]
    assert outcomes[1]["status"] == "blocked"
    state = flow.DiscoveryState(platform_results=outcomes, errors=[{"status": "partial"}])
    result = flow.project_discovery_response(
        _plan(), state, outcomes[0]["annotated"], flow.DiscoveryEffects(0, 0),
        enroll_skips={}, brand_official_skip_reason="brand",
        pagination={"next_page_cursors": {"youtube": "P2"}, "next_cursor": "P2", "has_more": True},
        build_funnel=lambda **_kwargs: {},
    )
    assert result["status"] == "partial"
    assert result["provider_outcome_unknown"] is True
    assert result["has_more"] is False
    assert result["next_cursor"] is None
    assert result["next_page_cursors"] == {}
    assert result["retry_safe"] is False


def test_unknown_never_triggers_auto_enroll_buildout():
    plan = _plan()
    plan.auto_enroll = True
    state = flow.DiscoveryState(platform_results=[{"metadata": {"provider_outcome_unknown": True}}])
    effects = flow.apply_discovery_effects(
        plan, state, [], reach_state=lambda _item: {},
        triage_existing=lambda rows: (rows, {"low_reach": 0, "analyzing": 0}),
        auto_enroll_discoveries=lambda _rows: pytest.fail("must not ignite buildout"),
        warm_avatar_cache=lambda _rows: pytest.fail("must not start further effects"),
        logger=logging.getLogger("test"),
    )
    assert effects.auto_enrolled_count == 0
