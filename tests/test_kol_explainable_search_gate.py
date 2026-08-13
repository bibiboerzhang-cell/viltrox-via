from __future__ import annotations

import asyncio
import inspect
import json
from typing import Any

import pytest

from app.api.routers import vkpi_kol_pool_search
from app.domains.kol import (
    product_resolver,
    profile_discovery_candidates,
    profile_discovery_pipeline,
    profile_recall,
)
from app.domains.kol import profile_recall_match_evidence as match_evidence


def _row(
    item_id: int,
    *,
    handle: str,
    bio: str = "",
    platform: str = "youtube",
    country: str | None = "US",
    language: str | None = "en",
    profile_type: str = "creator",
    email: str | None = None,
    primary_topic: str = "",
    content_style: str = "",
) -> dict[str, Any]:
    return {
        "kol_pool_id": item_id,
        "handle": handle,
        "display_name": handle.replace("-", " ").title(),
        "platform": platform,
        "profile_url": f"https://example.test/{handle}",
        "followers": 10_000 + item_id,
        "country": country,
        "language": language,
        "profile_type": profile_type,
        "creator_type_score": 80 if profile_type == "creator" else 20,
        "reviewer_type_score": 80 if profile_type == "reviewer" else 20,
        "profile_text": bio,
        "type_reason": "",
        "bio": bio,
        "primary_topic": primary_topic,
        "content_style": content_style,
        "email": email,
    }


def _install_public_recall_fixture(
    monkeypatch: pytest.MonkeyPatch,
    *,
    query: str,
    hits: list[profile_recall.RecallHit],
    rows: dict[int, dict[str, Any]],
    evidence: dict[int, dict[str, Any]] | None = None,
) -> None:
    monkeypatch.setenv("RECALL_LLM_RERANK_ENABLED", "0")
    monkeypatch.setattr(
        profile_recall,
        "resolve_query_text",
        lambda **_kwargs: (
            query,
            {"query_profile": "", "query_text_provided": True},
        ),
    )
    monkeypatch.setattr(
        profile_recall,
        "_pool_text_fallback_hits",
        lambda *_args, **_kwargs: list(hits),
    )
    monkeypatch.setattr(
        profile_recall,
        "_entry_rows",
        lambda ids: {item_id: dict(rows[item_id]) for item_id in ids if item_id in rows},
    )
    monkeypatch.setattr(
        profile_recall,
        "_evidence_summaries",
        lambda ids: {
            item_id: dict((evidence or {}).get(item_id, {}))
            for item_id in ids
            if item_id in (evidence or {})
        },
    )
    monkeypatch.setattr(profile_recall, "_pool_rows_fallback", lambda _ids: {})
    monkeypatch.setattr(profile_recall, "_adoption_profile", lambda: {})


def _distribution_facet(distribution: dict[str, Any], name: str) -> dict[str, Any]:
    facets = distribution.get("facets")
    assert isinstance(facets, dict), "distribution must expose a stable facets object"
    counts = facets.get(name)
    assert isinstance(counts, dict), f"missing distribution facet: {name}"
    assert all(isinstance(count, int) for count in counts.values())
    return counts


class _Cursor:
    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self._rows = rows

    def fetchall(self) -> list[dict[str, Any]]:
        return list(self._rows)

    def fetchone(self) -> dict[str, Any] | None:
        return dict(self._rows[0]) if self._rows else None


class _FallbackProbeConn:
    """Return a popular unrelated row only for the follower-head backfill query."""

    def __init__(self) -> None:
        self.executed_sql: list[str] = []

    def execute(self, sql: str, _params: tuple[Any, ...] = ()) -> _Cursor:
        self.executed_sql.append(sql)
        # Legacy's last query has no LIKE at all. Both strict evidence queries do.
        is_unconditional_follower_head = " LIKE " not in sql.upper()
        return _Cursor([{"kol_pool_id": 999}] if is_unconditional_follower_head else [])


def test_pool_text_no_evidence_does_not_top_fill_when_backfill_is_disabled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    strict_conn = _FallbackProbeConn()
    monkeypatch.setattr(profile_recall, "get_conn", lambda: strict_conn)

    strict_hits = profile_recall._pool_text_fallback_hits(
        "quantum underwater dental creator",
        30,
        allow_backfill=False,
    )

    assert strict_hits == []
    assert all(" LIKE " in sql.upper() for sql in strict_conn.executed_sql)

    legacy_conn = _FallbackProbeConn()
    monkeypatch.setattr(profile_recall, "get_conn", lambda: legacy_conn)
    legacy_hits = profile_recall._pool_text_fallback_hits(
        "quantum underwater dental creator",
        30,
    )

    assert [hit.kol_pool_id for hit in legacy_hits] == [999]
    assert any(" LIKE " not in sql.upper() for sql in legacy_conn.executed_sql)


def test_recall_legacy_default_remains_backfill_compatible() -> None:
    parameter = inspect.signature(profile_recall.recall_kol_profiles).parameters.get("allow_backfill")

    assert parameter is not None
    assert parameter.default is True


def test_evidence_gate_allows_short_result_without_fabricating_why_fit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    query = "35mm low-light portrait"
    rows = {
        1: _row(
            1,
            handle="grounded-photographer",
            bio="35mm low-light portrait photographer and lens reviewer",
        ),
        2: _row(
            2,
            handle="popular-but-unrelated",
            bio="Lifestyle routines, cooking and daily family vlogs",
        ),
    }
    hits = [
        profile_recall.RecallHit(1, 0.91, "vector-1"),
        profile_recall.RecallHit(2, 0.99, "vector-2"),
        profile_recall.RecallHit(2, 0.98, "vector-2-duplicate"),
    ]
    _install_public_recall_fixture(
        monkeypatch,
        query=query,
        hits=hits,
        rows=rows,
    )

    result = profile_recall.recall_kol_profiles(
        query_text=query,
        provider_free=True,
        allow_backfill=False,
        candidate_limit=30,
        limit=30,
        creator_quota=30,
        reviewer_quota=0,
    )

    assert [item["kol_pool_id"] for item in result["items"]] == [1]
    assert result["diagnostics"]["returned_count"] == 1
    assert result["query"]["limit"] == 30

    item = result["items"][0]
    match_evidence = item.get("match_evidence")
    assert match_evidence
    evidence_blob = json.dumps(match_evidence, ensure_ascii=False).lower()
    assert "bio" in evidence_blob
    assert any(term in evidence_blob for term in ("35mm", "low-light", "portrait"))
    assert item.get("why_fit")
    assert "画像与产品人群相近" not in str(item["why_fit"])


def test_no_field_evidence_returns_explicit_empty_state(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    query = "quantum underwater dental creator"
    rows = {
        1: _row(
            1,
            handle="unrelated-camera-channel",
            bio="Camera reviews and street photography",
        ),
    }
    _install_public_recall_fixture(
        monkeypatch,
        query=query,
        hits=[profile_recall.RecallHit(1, 0.99, "vector-only")],
        rows=rows,
    )

    result = profile_recall.recall_kol_profiles(
        query_text=query,
        provider_free=True,
        allow_backfill=False,
        candidate_limit=30,
        limit=30,
        creator_quota=30,
        reviewer_quota=0,
    )

    assert result["items"] == []
    assert result["match_status"] == "empty"
    assert result["diagnostics"]["empty_reason"] == "no_evidence_match"
    assert result["diagnostics"]["returned_count"] == 0


def test_candidate_distribution_uses_returned_canonical_candidates_only(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    query = "optics"
    rows = {
        1: _row(
            1,
            handle="youtube-camera",
            bio="Optics educator",
            platform="youtube",
            country="US",
            language="en",
            profile_type="creator",
            email="creator@example.test",
        ),
        2: _row(
            2,
            handle="instagram-camera",
            primary_topic="optics reviews",
            platform="instagram",
            country=None,
            language=None,
            profile_type="reviewer",
        ),
        3: _row(
            3,
            handle="unknown-platform-camera",
            content_style="optics tutorials",
            platform="",
            country="DE",
            language="de",
            profile_type="",
        ),
    }
    hits = [
        profile_recall.RecallHit(1, 0.90, "vector-1"),
        profile_recall.RecallHit(1, 0.89, "vector-1-duplicate"),
        profile_recall.RecallHit(2, 0.88, "vector-2"),
        profile_recall.RecallHit(3, 0.87, "vector-3"),
    ]
    evidence = {
        1: {
            "representative_evidence": [
                {"title": "Optics tutorial", "content_url": "https://example.test/video/1"}
            ]
        }
    }
    _install_public_recall_fixture(
        monkeypatch,
        query=query,
        hits=hits,
        rows=rows,
        evidence=evidence,
    )

    result = profile_recall.recall_kol_profiles(
        query_text=query,
        provider_free=True,
        allow_backfill=False,
        candidate_limit=30,
        limit=3,
        creator_quota=1,
        reviewer_quota=2,
    )

    returned_ids = [item["kol_pool_id"] for item in result["items"]]
    assert len(returned_ids) == len(set(returned_ids)) == 3
    for bucket_items in result["buckets"].values():
        bucket_ids = [item["kol_pool_id"] for item in bucket_items]
        assert len(bucket_ids) == len(set(bucket_ids))

    distribution = result.get("candidate_set_distribution")
    assert isinstance(distribution, dict)
    assert distribution["denominator"] == len(returned_ids)
    assert distribution["claim_status"] == "descriptive_only"
    for facet_name in (
        "platform",
        "country",
        "language",
        "profile_type",
        "contact_available",
        "video_evidence",
    ):
        facet = _distribution_facet(distribution, facet_name)
        assert "unknown" in facet, f"{facet_name} must count unknown separately"
        assert sum(facet.values()) == len(returned_ids)

    platform = _distribution_facet(distribution, "platform")
    assert platform == {"instagram": 1, "unknown": 1, "youtube": 1}


def test_smart_preview_and_worker_explicitly_disable_backfill(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    preview_calls: list[dict[str, Any]] = []
    monkeypatch.setattr(
        vkpi_kol_pool_search.kol_smart_query_planner,
        "plan_text_query_provider_free",
        lambda *_args, **_kwargs: {"status": "ready", "search_query": "camera reviewer"},
    )

    def preview_recall(**kwargs: Any) -> dict[str, Any]:
        preview_calls.append(kwargs)
        return {
            "items": [],
            "buckets": {"creator": [], "reviewer": []},
            "diagnostics": {
                "returned_count": 0,
                "result_state": "empty",
                "empty_reason": "no_evidence_match",
            },
        }

    monkeypatch.setattr(
        vkpi_kol_pool_search.kol_profile_recall,
        "recall_kol_profiles",
        preview_recall,
    )
    monkeypatch.setattr(
        vkpi_kol_pool_search,
        "_attach_smart_recall_session",
        lambda **kwargs: kwargs["result"],
    )

    preview = asyncio.run(
        vkpi_kol_pool_search.smart_kol_search(
            {
                "input": "camera reviewer",
                "create_session": False,
                "dedupe": False,
            },
            staff={"id": 42},
        )
    )

    assert preview["status"] == "empty"
    assert preview_calls[0]["allow_backfill"] is False
    assert preview_calls[0]["dedupe"] is True

    worker_calls: list[dict[str, Any]] = []

    class _StopAfterRecall(RuntimeError):
        pass

    def worker_recall(**kwargs: Any) -> dict[str, Any]:
        worker_calls.append(kwargs)
        raise _StopAfterRecall

    monkeypatch.setattr(profile_discovery_pipeline.profile_recall, "recall_kol_profiles", worker_recall)

    with pytest.raises(_StopAfterRecall):
        asyncio.run(
            profile_discovery_pipeline.execute_smart_search_profile_advance_pipeline(
                session_id=321,
                payload={
                    "query_text": "camera reviewer",
                    "_worker_planned": True,
                    "dedupe": False,
                },
            )
        )

    assert worker_calls[0]["allow_backfill"] is False
    assert worker_calls[0]["dedupe"] is True


@pytest.mark.parametrize(
    ("query", "row", "expected_terms"),
    [
        (
            "35mm low-light portrait YouTube photographer",
            _row(
                11,
                handle="portrait-photographer",
                bio="35mm low-light portrait photographer",
            ),
            {"35mm", "low-light", "portrait"},
        ),
        (
            "DC-550 Pro II US wedding videographer",
            _row(
                12,
                handle="wedding-videographer",
                bio="DC-550 Pro II field monitor workflow for a wedding videographer",
            ),
            {"dc-550", "wedding"},
        ),
        (
            "EPIC 65mm macro anamorphic cinematographer",
            _row(
                13,
                handle="cine-dp",
                bio="EPIC 65mm macro anamorphic cinematographer",
            ),
            {"epic", "65mm", "anamorphic"},
        ),
        (
            "Instagram macro lens reviewer US",
            _row(
                14,
                handle="macro-reviewer",
                bio="Independent macro lens reviewer",
                platform="instagram",
            ),
            {"macro"},
        ),
    ],
)
def test_golden_relevant_concepts_have_field_level_proof(
    query: str,
    row: dict[str, Any],
    expected_terms: set[str],
) -> None:
    evidence = match_evidence.build_match_evidence(row, {}, query)
    matched_terms = {item["term"] for item in evidence}

    assert evidence
    assert expected_terms <= matched_terms
    assert match_evidence.why_fit_from_match_evidence(evidence)


def test_golden_impossible_concept_has_no_field_level_proof() -> None:
    row = _row(
        15,
        handle="ordinary-camera-reviewer",
        bio="Camera reviews and street photography",
    )

    assert match_evidence.build_match_evidence(
        row,
        {},
        "quantum underwater dental creator",
    ) == []


@pytest.mark.parametrize(
    ("query", "row", "expected_count"),
    [
        (
            "35mm low-light portrait YouTube photographer",
            _row(
                21,
                handle="portrait-photographer",
                bio="35mm low-light portrait photographer",
            ),
            1,
        ),
        (
            "DC-550 Pro II US wedding videographer",
            _row(
                22,
                handle="wedding-videographer",
                bio="DC-550 Pro II field monitor workflow for wedding films",
            ),
            1,
        ),
        (
            "EPIC 65mm macro anamorphic cinematographer",
            _row(
                23,
                handle="cine-dp",
                bio="EPIC 65mm macro anamorphic cinematographer",
            ),
            1,
        ),
        (
            "Instagram macro lens reviewer US",
            _row(
                24,
                handle="macro-reviewer",
                bio="Independent macro optics tests",
                platform="instagram",
            ),
            1,
        ),
        (
            "quantum underwater dental creator",
            _row(
                25,
                handle="ordinary-camera-reviewer",
                bio="Camera reviews and street photography",
            ),
            0,
        ),
    ],
)
def test_five_golden_concepts_enforce_recall_evidence_gate(
    monkeypatch: pytest.MonkeyPatch,
    query: str,
    row: dict[str, Any],
    expected_count: int,
) -> None:
    item_id = int(row["kol_pool_id"])
    _install_public_recall_fixture(
        monkeypatch,
        query=query,
        hits=[profile_recall.RecallHit(item_id, 0.99, f"vector-{item_id}")],
        rows={item_id: row},
    )

    result = profile_recall.recall_kol_profiles(
        query_text=query,
        provider_free=True,
        allow_backfill=False,
        limit=1,
        creator_quota=1,
        reviewer_quota=0,
    )

    assert len(result["items"]) == expected_count
    if expected_count:
        assert result["items"][0]["match_evidence"]
        assert result["items"][0]["why_fit"]
    else:
        assert result["match_status"] == "empty"
        assert result["diagnostics"]["empty_reason"] == "no_evidence_match"


@pytest.mark.parametrize(
    "query",
    [
        "camera creator", "photography creator", "video creator",
        "find best photographers", "top creators", "find good photographers",
        "show me new creators", "find relevant creators", "high quality influencers",
    ],
)
def test_generic_creator_terms_cannot_prove_a_match(query: str) -> None:
    row = _row(
        31,
        handle="generic-channel",
        bio="Camera photography and video creator",
    )

    assert match_evidence.query_evidence_terms(query) == []
    assert match_evidence.build_match_evidence(row, {}, query) == []


@pytest.mark.parametrize(
    ("query", "bio", "expected_term"),
    [
        ("United States wedding videographer", "Wedding films", "wedding"),
        ("Canada portrait photographer", "Portrait lighting", "portrait"),
        ("UK street photographer", "Street documentary", "street"),
    ],
)
def test_market_words_do_not_become_profile_content_requirements(
    query: str,
    bio: str,
    expected_term: str,
) -> None:
    evidence = match_evidence.build_match_evidence(
        _row(35, handle="grounded-market-creator", bio=bio),
        {},
        query,
    )
    assert {item["term"] for item in evidence} == {expected_term}


def test_search_instruction_fillers_do_not_block_a_real_street_match() -> None:
    row = _row(
        32,
        handle="street-photographer",
        bio="Documentary street photographer",
    )

    assert match_evidence.query_evidence_terms("find street photographers") == ["street"]
    evidence = match_evidence.build_match_evidence(
        row,
        {},
        "find street photographers",
    )
    assert {item["term"] for item in evidence} == {"street"}


def test_platform_filter_recomputes_distribution_from_filtered_items() -> None:
    youtube = {
        "kol_pool_id": 41,
        "platform": "youtube",
        "candidate_facets": {
            "platform": "youtube",
            "country": "us",
            "language": "en",
            "profile_type": "creator",
            "contact_available": "yes",
            "video_evidence": "yes",
        },
    }
    instagram = {
        "kol_pool_id": 42,
        "platform": "instagram",
        "candidate_facets": {
            "platform": "instagram",
            "country": "unknown",
            "language": "unknown",
            "profile_type": "reviewer",
            "contact_available": "no",
            "video_evidence": "no",
        },
    }
    before = match_evidence.candidate_set_distribution_from_items([youtube, instagram])
    result = {
        "items": [youtube, instagram],
        "buckets": {"creator": [youtube], "reviewer": [instagram]},
        "candidate_set_distribution": before,
        "diagnostics": {"evidence_gate_enabled": True, "returned_count": 2},
    }

    filtered = profile_discovery_candidates.filter_recall_result_platforms(
        result,
        ["instagram"],
    )

    assert [item["kol_pool_id"] for item in filtered["items"]] == [42]
    distribution = filtered["candidate_set_distribution"]
    assert distribution["denominator"] == 1
    assert distribution["facets"]["platform"] == {"instagram": 1, "unknown": 0}
    for facet in distribution["facets"].values():
        assert sum(facet.values()) == 1


@pytest.mark.parametrize(
    ("query", "planned_market", "expected"),
    [
        ("US wedding videographer", "US", "us"),
        ("wedding videographers in US", "US", "us"),
        ("USA wedding videographer", "US", "us"),
        ("United States wedding videographer", "US", "us"),
        ("美国婚礼摄像师", "US", "us"),
        ("wedding videographer", "US", ""),
        ("find creators for us", "US", ""),
        ("show us portrait creators", "US", ""),
        ("Australian wedding videographer", "US", ""),
        ("find creators for us", "US", ""),
        ("show us portrait photographers", "US", ""),
        ("find creators in USA", "US", "us"),
        ("UK portrait creators", "US", "gb"),
        ("Canada wedding videographer", "US", "ca"),
        ("德国摄影师", "US", "de"),
        ("photographes de mariage", "US", ""),
        ("créateurs de voyage", "US", ""),
        ("portrait au flash", "US", ""),
        ("film au ralenti", "US", ""),
        ("photographers in DE", "US", "de"),
        ("filmmakers from AU", "US", "au"),
        (
            "US and UK portrait creators",
            "US",
            profile_discovery_candidates.AMBIGUOUS_MARKET_CONSTRAINT,
        ),
    ],
)
def test_market_constraint_only_applies_when_operator_explicitly_states_it(
    query: str,
    planned_market: str,
    expected: str,
) -> None:
    assert profile_discovery_candidates.explicit_market_constraint(
        query,
        planned_market,
    ) == expected


def test_market_filter_is_hard_and_recomputes_distribution() -> None:
    us = {
        "kol_pool_id": 51,
        "platform": "youtube",
        "candidate_facets": {
            "platform": "youtube",
            "country": "us",
            "language": "en",
            "profile_type": "creator",
            "contact_available": "yes",
            "video_evidence": "yes",
        },
    }
    gb = {
        "kol_pool_id": 52,
        "platform": "youtube",
        "candidate_facets": {
            "platform": "youtube",
            "country": "gb",
            "language": "en",
            "profile_type": "reviewer",
            "contact_available": "no",
            "video_evidence": "no",
        },
    }
    result = {
        "items": [us, gb],
        "buckets": {"creator": [us], "reviewer": [gb]},
        "candidate_set_distribution": match_evidence.candidate_set_distribution_from_items([us, gb]),
        "diagnostics": {"evidence_gate_enabled": True, "returned_count": 2},
    }

    filtered = profile_discovery_candidates.filter_recall_result_market(result, "US")

    assert [item["kol_pool_id"] for item in filtered["items"]] == [51]
    assert filtered["diagnostics"]["market_filtered_out"] == 1
    assert filtered["candidate_set_distribution"]["denominator"] == 1
    assert filtered["candidate_set_distribution"]["facets"]["country"] == {
        "unknown": 0,
        "us": 1,
    }


def test_smart_preview_does_not_turn_planner_default_platforms_into_hard_filter(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    facebook = {
        "kol_pool_id": 61,
        "platform": "facebook",
        "candidate_facets": {
            "platform": "facebook",
            "country": "us",
            "language": "en",
            "profile_type": "creator",
            "contact_available": "unknown",
            "video_evidence": "no",
        },
    }
    monkeypatch.setattr(
        vkpi_kol_pool_search.kol_smart_query_planner,
        "plan_text_query_provider_free",
        lambda *_args, **_kwargs: {
            "status": "ready",
            "search_query": "macro optics",
            "platforms": ["youtube", "instagram", "tiktok"],
            "market": "US",
        },
    )
    monkeypatch.setattr(
        vkpi_kol_pool_search.kol_profile_recall,
        "recall_kol_profiles",
        lambda **_kwargs: {
            "items": [facebook],
            "buckets": {"creator": [facebook], "reviewer": []},
            "diagnostics": {"returned_count": 1, "evidence_gate_enabled": True},
        },
    )
    monkeypatch.setattr(
        vkpi_kol_pool_search,
        "_attach_smart_recall_session",
        lambda **kwargs: kwargs["result"],
    )

    result = asyncio.run(
        vkpi_kol_pool_search.smart_kol_search(
            {"input": "macro optics", "create_session": False},
            staff={"id": 42},
        )
    )

    assert result["status"] == "ready"
    assert [item["kol_pool_id"] for item in result["result"]["items"]] == [61]
    assert result["result"].get("platform_filter") is None


def test_smart_preview_hard_filters_platform_explicit_in_operator_query(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    instagram = {"kol_pool_id": 71, "platform": "instagram"}
    youtube = {"kol_pool_id": 72, "platform": "youtube"}
    monkeypatch.setattr(
        vkpi_kol_pool_search.kol_smart_query_planner,
        "plan_text_query_provider_free",
        lambda *_args, **_kwargs: {
            "status": "ready",
            "search_query": "macro optics",
            "platforms": ["instagram"],
        },
    )
    monkeypatch.setattr(
        vkpi_kol_pool_search.kol_profile_recall,
        "recall_kol_profiles",
        lambda **_kwargs: {
            "items": [instagram, youtube],
            "buckets": {"creator": [instagram, youtube], "reviewer": []},
            "diagnostics": {"returned_count": 2, "evidence_gate_enabled": True},
        },
    )
    monkeypatch.setattr(
        vkpi_kol_pool_search,
        "_attach_smart_recall_session",
        lambda **kwargs: kwargs["result"],
    )

    result = asyncio.run(
        vkpi_kol_pool_search.smart_kol_search(
            {"input": "Instagram macro optics", "create_session": False},
            staff={"id": 42},
        )
    )

    assert result["status"] == "ready"
    assert [item["kol_pool_id"] for item in result["result"]["items"]] == [71]
    assert result["result"]["platform_filter"]["requested"] == ["instagram"]


def test_worker_planner_default_platform_does_not_become_hard_constraint(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, Any] = {}
    monkeypatch.setattr(
        profile_discovery_pipeline.profile_recall,
        "recall_kol_profiles",
        lambda **_kwargs: {
            "items": [],
            "buckets": {"creator": [], "reviewer": []},
            "diagnostics": {"returned_count": 0, "evidence_gate_enabled": True},
        },
    )
    monkeypatch.setattr(
        profile_discovery_pipeline,
        "filter_recall_result_platforms",
        lambda result, platforms: captured.setdefault("platforms", platforms) or result,
    )
    monkeypatch.setattr(
        profile_discovery_pipeline,
        "filter_recall_result_market",
        lambda result, _market: result,
    )
    monkeypatch.setattr(
        profile_discovery_pipeline.search_sessions,
        "attach_recall_result",
        lambda _session_id, result: result,
    )
    monkeypatch.setattr(
        profile_discovery_pipeline.search_sessions,
        "update_session_result_summary",
        lambda *_args, **_kwargs: {},
    )
    monkeypatch.setattr(
        profile_discovery_pipeline,
        "advance_search_session_items",
        lambda **_kwargs: {"status": "empty", "selected": 0, "counts": {}},
    )

    result = asyncio.run(
        profile_discovery_pipeline.execute_smart_search_profile_advance_pipeline(
            session_id=321,
            payload={
                "query_text": "portrait creators",
                "_worker_planned": True,
                "platforms": [],
                "include_new_discovery": False,
            },
        )
    )

    assert result["status"] in {"empty", "partial", "done"}
    assert captured["platforms"] in (None, [], "")


def _epic_catalog_row() -> dict[str, Any]:
    return {
        "sku": "EPIC-65-MACRO-PL",
        "model_name": "EPIC 65mm Macro T2.8",
        "marketing_name": "EPIC 65mm Macro",
        "series": "EPIC",
        "category_main": "Lens",
        "category_detail": "Cine Macro",
        "mount": "PL-mount",
    }


def test_generic_macro_creator_request_does_not_bind_epic_product(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        product_resolver,
        "list_product_catalog",
        lambda **_kwargs: {"products": [_epic_catalog_row()]},
    )

    assert product_resolver.resolve_product("Instagram macro lens reviewer US") is None


def test_explicit_epic_65mm_macro_request_resolves_epic_product(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        product_resolver,
        "list_product_catalog",
        lambda **_kwargs: {"products": [_epic_catalog_row()]},
    )

    resolved = product_resolver.resolve_product(
        "EPIC 65mm macro anamorphic cinematographer"
    )

    assert resolved is not None
    assert resolved["sku"] == "EPIC-65-MACRO-PL"


@pytest.mark.parametrize(
    "query",
    [
        "Sony portrait photographer",
        "Fuji street photographer",
        "35mm portrait photographer",
        "find creators with 550 followers",
        "vintage portrait photographers",
        "epic travel filmmakers",
        "lab photographers",
        "collaboration macro creators",
        "epicenter macro creators",
        "laboratory macro photographers",
    ],
)
def test_generic_persona_or_count_does_not_bind_catalog_product(
    monkeypatch: pytest.MonkeyPatch,
    query: str,
) -> None:
    monkeypatch.setattr(
        product_resolver,
        "list_product_catalog",
        lambda **_kwargs: {"products": [_epic_catalog_row()]},
    )
    assert product_resolver.resolve_product(query) is None


def test_explicit_viltrox_af_model_remains_resolvable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    product = {
        "sku": "AF-35-F18-FE",
        "model_name": "Viltrox AF 35mm F1.8 FE",
        "marketing_name": "AF 35mm F1.8",
        "series": "Pro",
        "category_main": "Lens",
        "mount": "FE-mount",
    }
    monkeypatch.setattr(
        product_resolver,
        "list_product_catalog",
        lambda **_kwargs: {"products": [product]},
    )
    resolved = product_resolver.resolve_product("Viltrox AF 35mm F1.8 Sony lens")
    assert resolved is not None
    assert resolved["sku"] == "AF-35-F18-FE"
