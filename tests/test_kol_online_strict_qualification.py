from __future__ import annotations

import asyncio
from datetime import datetime, timezone
import json
from typing import Any

import pytest

from app.domains.kol import (
    profile_discovery_provider,
    profile_discovery_session,
    profile_online_inventory,
    profile_online_qualification,
    profile_recall_qualification,
    search_sessions_online,
    targeted_search_contract,
)
from app.domains.kol.search_sessions_items import (
    _prune_authoritative_online_snapshot,
    _prune_authoritative_recall_snapshot,
)


AS_OF = datetime(2026, 8, 17, tzinfo=timezone.utc)


def _candidate(index: int, **overrides: Any) -> dict[str, Any]:
    item: dict[str, Any] = {
        "platform": "youtube",
        "handle": f"portrait{index}",
        "channel_id": f"UCstrict{index:04d}",
        "display_name": f"Portrait Lighting {index}",
        "profile_url": f"https://www.youtube.com/@portrait{index}",
        "avatar_url": f"https://images.example/{index}.jpg",
        "followers": 5_000 + index,
        "country": "US",
        "country_source": "platform_profile",
        "language": "en",
        "language_source": "platform_profile",
        "profile_type": "creator",
        "profile_type_source": "provider_declared",
        "activation_sample_count": 5,
        "activation_metrics_source": "fixture.recent_video_aggregate",
        "activation_metrics_scope": "recent_video_aggregate_45d",
        "bio": "portrait lighting studio tutorial creator",
        "latest_real_video": {
            "posted_at": "2026-08-01T00:00:00Z",
            "video_id": f"video{index:05d}",
            "platform": "youtube",
            "title": "portrait lighting studio tutorial",
            "source": "platform_video_api",
        },
    }
    item.update(overrides)
    return item


def _policy() -> dict[str, Any]:
    return profile_online_qualification.online_policy(
        market="US",
        platforms=["youtube"],
        languages=["en"],
        profile_types=["creator"],
    )


def _prospective_brief() -> dict[str, Any]:
    return {
        "objective": "prospective_growth",
        "product": {"capability": "on-camera flash"},
    }


def _query_cell(cell_id: str, segment: str) -> dict[str, Any]:
    return {
        "query_cell_id": cell_id,
        "objective": "prospective_growth",
        "segment": segment,
        "primary_query": f"{segment} photographer on-camera flash",
        "required_evidence_groups": [
            "product_use_fit",
            "segment_use_case",
            "market_activation",
        ],
        "locked_term_groups": targeted_search_contract.build_locked_term_groups(
            capability="on-camera flash",
            segment=segment,
            segment_label=segment,
        ),
    }


def _telephoto_query_cell(cell_id: str, segment: str) -> dict[str, Any]:
    return {
        "query_cell_id": cell_id,
        "objective": "prospective_growth",
        "segment": segment,
        "primary_query": f"{segment} photographer telephoto portrait lens",
        "required_evidence_groups": [
            "product_use_fit",
            "segment_use_case",
            "market_activation",
        ],
        "locked_term_groups": targeted_search_contract.build_locked_term_groups(
            capability="telephoto portrait lens",
            segment=segment,
            segment_label=segment,
        ),
    }


def test_online_candidate_must_pass_all_eight_server_gates_and_projects_no_raw_contact() -> None:
    raw = _candidate(1, email="private@example.com", phone="+1 555 555 1212")
    result = profile_online_qualification.qualify_online_candidates(
        [raw],
        query_text="portrait lighting",
        policy=_policy(),
        as_of=AS_OF,
    )

    assert result["counts"] == {"selected": 1}
    item = result["accepted"][0]
    proof = item["qualification_evidence"]
    assert proof["passed"] is True
    assert all(
        proof[field]["passed"] is True
        for field in (
            "account_quality", "followers", "activity", "market",
            "language", "profile_type", "platform", "relevance",
        )
    )
    assert item["kol_pool_id"] is None
    assert "bio" not in item and "email" not in item and "phone" not in item
    assert proof["followers"]["source"] == "online_provider.followers"


def test_multi_cell_candidate_uses_best_qualified_cell_not_first_cell() -> None:
    motorsport = _query_cell("cell-motorsport", "motorsport")
    food = _query_cell("cell-food", "food")
    raw = _candidate(
        101,
        bio="food creator using on-camera flash",
        avg_views=20_000,
        avg_comments=180,
        engagement_rate=0.08,
        query_cell_id=motorsport["query_cell_id"],
        query_cell_segment=motorsport["segment"],
        query_cell_query=motorsport["primary_query"],
        matched_query_cells=[motorsport, food],
    )

    result = profile_online_qualification.qualify_online_candidates(
        [raw],
        query_text=motorsport["primary_query"],
        policy=_policy(),
        search_brief=_prospective_brief(),
        as_of=AS_OF,
    )

    assert result["counts"] == {"selected": 1}
    assert result["unique_candidate_count"] == 1
    assert result["cell_evaluation_count"] == 2
    assert result["qualification_stats"]["qualified_cell_count"] == 1
    item = result["accepted"][0]
    assert item["query_cell_id"] == "cell-food"
    assert item["best_qualified_cell"]["query_cell_id"] == "cell-food"
    assert [entry["passed"] for entry in item["cell_qualification"]] == [False, True]
    assert item["cell_qualification"][0]["reasons"] == ["product_scene_evidence_missing"]
    assert item["growth_qualification_pass"] is True


def test_people_only_food_video_creator_is_not_rejected_by_fake_gear_gate() -> None:
    [food] = targeted_search_contract.build_query_cells(
        query="找美食短视频博主",
        body={},
        product=None,
        product_focus=["food content creator"],
        platforms=["youtube"],
    )
    raw = _candidate(
        150,
        bio="Food video creator making restaurant reviews and short recipe videos.",
        avg_views=24_000,
        avg_comments=180,
        engagement_rate=0.08,
        matched_query_cells=[food],
    )
    raw["latest_real_video"]["title"] = "Restaurant recipe production diary"

    result = profile_online_qualification.qualify_online_candidates(
        [raw],
        query_text=food["primary_query"],
        policy=_policy(),
        search_brief={
            "objective": "prospective_growth",
            "product": {"capability": None, "evidence_required": False},
        },
        as_of=AS_OF,
    )

    assert result["counts"] == {"selected": 1}
    accepted = result["accepted"][0]
    assert accepted["growth_qualification_pass"] is True
    assert accepted["best_qualified_cell"]["product_use_fit"] is None
    assert accepted["best_qualified_cell"]["product_scene_evidence_pass"] is True


def test_people_only_online_qualification_requires_role_and_scene() -> None:
    [wedding] = targeted_search_contract.build_query_cells(
        query="Find wedding photographers",
        body={},
        product=None,
        product_focus=[],
        platforms=["youtube"],
    )
    planner = _candidate(
        151,
        bio="Wedding planner coordinating bridal ceremonies and venues.",
        avg_views=24_000,
        avg_comments=180,
        engagement_rate=0.08,
        matched_query_cells=[wedding],
    )
    planner["latest_real_video"]["title"] = "How I planned this wedding ceremony"
    photographer = _candidate(
        152,
        bio="Wedding photographer documenting bridal ceremonies.",
        avg_views=24_000,
        avg_comments=180,
        engagement_rate=0.08,
        matched_query_cells=[wedding],
    )
    photographer["latest_real_video"]["title"] = "Wedding photography behind the scenes"

    result = profile_online_qualification.qualify_online_candidates(
        [planner, photographer],
        query_text=wedding["primary_query"],
        policy=_policy(),
        search_brief={
            "objective": "prospective_growth",
            "product": {"capability": None, "evidence_required": False},
        },
        as_of=AS_OF,
    )

    assert result["counts"] == {"selected": 1, "rejected": 1}, result
    assert result["qualification_stats"]["qualified_cell_count"] == 1
    assert [item["handle"] for item in result["accepted"]] == ["portrait152"]
    assert wedding["required_role_terms"] == ["photographer"]
    role_rows = [
        row
        for row in result["accepted"][0]["best_qualified_cell"]["match_evidence"]
        if row.get("evidence_group") == "people_role"
    ]
    assert {(row["canonical_term"], row["observed_term"]) for row in role_rows} == {
        ("photographer", "photographer")
    }


def test_controlled_aliases_prove_flash_and_motorsport_without_brand_mention() -> None:
    motorsport = _query_cell("cell-motorsport", "motorsport")
    raw = _candidate(
        111,
        bio="independent creator",
        sample_transcript="Behind the scenes of a racing night shoot using one speedlight",
        avg_views=22_000,
        avg_comments=190,
        engagement_rate=0.08,
        matched_query_cells=[motorsport],
    )
    raw["latest_real_video"]["title"] = "Track weekend setup"

    result = profile_online_qualification.qualify_online_candidates(
        [raw],
        query_text=motorsport["primary_query"],
        policy=_policy(),
        search_brief=_prospective_brief(),
        as_of=AS_OF,
    )

    assert result["counts"] == {"selected": 1}
    evidence = result["accepted"][0]["best_qualified_cell"]["match_evidence"]
    controlled = [
        item for item in evidence
        if item.get("source") == "server_allowlisted_alias_evidence"
    ]
    assert {
        (item["canonical_term"], item["observed_term"], item["field"])
        for item in controlled
    } >= {
        ("on-camera flash", "speedlight", "representative_evidence.transcript"),
        ("motorsport", "racing", "representative_evidence.transcript"),
    }
    assert "Behind the scenes" not in json.dumps(result, ensure_ascii=False)


def test_youtube_lifetime_average_is_display_only_and_cannot_pass_growth_gate() -> None:
    motorsport = _query_cell("cell-motorsport", "motorsport")
    raw = _candidate(
        115,
        bio="independent motorsport photographer",
        sample_description="Race-day portraits made with an on-camera flash",
        avg_views=25_000,
        avg_views_source="youtube_channel_lifetime_view_count_div_video_count",
        avg_views_scope="channel_lifetime_proxy",
        channel_total_views=5_000_000,
        channel_video_count=200,
        matched_query_cells=[motorsport],
    )
    raw["latest_real_video"]["title"] = "Track weekend portrait workflow"

    result = profile_online_qualification.qualify_online_candidates(
        [raw],
        query_text=motorsport["primary_query"],
        policy=_policy(),
        search_brief=_prospective_brief(),
        as_of=AS_OF,
    )

    assert result["counts"] == {"pending": 1}
    assert result["rejected_by_reason"]["market_activation_missing"] == 1


def test_exact_query_video_sample_is_descriptive_but_pending_strict_growth_gate() -> None:
    motorsport = _query_cell("cell-motorsport", "motorsport")
    raw = _candidate(
        117,
        bio="independent motorsport photographer",
        sample_description="Race-day portraits made with an on-camera flash",
        representative_video_views=42_000,
        representative_video_likes=2_100,
        representative_video_comments=85,
        activation_sample_count=1,
        activation_metrics_source="youtube_data_api.videos.list",
        activation_metrics_scope="exact_query_hit_45d",
        matched_query_cells=[motorsport],
    )
    raw["latest_real_video"]["title"] = "Track weekend portrait workflow"

    result = profile_online_qualification.qualify_online_candidates(
        [raw], query_text=motorsport["primary_query"], policy=_policy(),
        search_brief=_prospective_brief(), as_of=AS_OF,
    )

    assert result["counts"] == {"pending": 1}
    assert result["accepted"] == []
    assert result["rejected_by_reason"] == {"insufficient_sample": 1}
    assert result["qualification_stats"]["qualified_cell_count"] == 0


def test_one_play_zero_interaction_cannot_enter_strict_growth_results() -> None:
    motorsport = _query_cell("cell-motorsport", "motorsport")
    raw = _candidate(
        118,
        followers=50_000,
        bio="independent motorsport photographer",
        sample_description="Race-day portraits made with an on-camera flash",
        avg_views=1,
        avg_comments=0,
        engagement_rate=0,
        activation_sample_count=5,
        matched_query_cells=[motorsport],
    )
    raw["latest_real_video"]["title"] = "Track weekend portrait workflow"

    result = profile_online_qualification.qualify_online_candidates(
        [raw], query_text=motorsport["primary_query"], policy=_policy(),
        search_brief=_prospective_brief(), as_of=AS_OF,
    )

    assert result["counts"] == {"rejected": 1}
    assert result["accepted"] == []
    assert result["rejected_by_reason"] == {"below_floor": 1}


def test_three_recent_samples_can_pass_descriptive_activation_gate() -> None:
    motorsport = _query_cell("cell-motorsport", "motorsport")
    raw = _candidate(
        119,
        followers=80_000,
        bio="independent motorsport photographer",
        sample_description="Race-day portraits made with an on-camera flash",
        avg_views=8_000,
        avg_comments=80,
        engagement_rate=0.04,
        activation_sample_count=3,
        matched_query_cells=[motorsport],
    )
    raw["latest_real_video"]["title"] = "Track weekend portrait workflow"

    result = profile_online_qualification.qualify_online_candidates(
        [raw], query_text=motorsport["primary_query"], policy=_policy(),
        search_brief=_prospective_brief(), as_of=AS_OF,
    )

    assert result["counts"] == {"selected": 1}
    item = result["accepted"][0]
    assert item["market_activation_pass"] is True
    assert item["market_activation_status"] == "passed"
    assert item["claim_status"] == "descriptive_only"


def test_out_of_follower_range_candidate_does_not_pollute_eligible_percentiles() -> None:
    motorsport = _query_cell("cell-motorsport", "motorsport")
    policy = profile_online_qualification.online_policy(
        market="US",
        platforms=["youtube"],
        languages=["en"],
        profile_types=["creator"],
        followers_min=50_000,
        followers_max=500_000,
    )

    def activation_candidate(index: int, *, followers: int, avg_views: int) -> dict[str, Any]:
        raw = _candidate(
            index,
            followers=followers,
            bio="independent motorsport photographer",
            sample_description="Race-day portraits made with an on-camera flash",
            avg_views=avg_views,
            avg_comments=max(20, avg_views // 100),
            engagement_rate=0.04,
            activation_sample_count=5,
            matched_query_cells=[motorsport],
        )
        raw["latest_real_video"]["title"] = "Track weekend portrait workflow"
        return raw

    in_range = [
        activation_candidate(120, followers=80_000, avg_views=8_000),
        activation_candidate(121, followers=120_000, avg_views=16_000),
    ]
    baseline = profile_online_qualification.qualify_online_candidates(
        in_range, query_text=motorsport["primary_query"], policy=policy,
        search_brief=_prospective_brief(), as_of=AS_OF,
    )
    with_outlier = profile_online_qualification.qualify_online_candidates(
        [
            *in_range,
            activation_candidate(122, followers=5_000_000, avg_views=10_000_000),
        ],
        query_text=motorsport["primary_query"], policy=policy,
        search_brief=_prospective_brief(), as_of=AS_OF,
    )

    baseline_scores = {
        item["handle"]: item["market_activation"] for item in baseline["accepted"]
    }
    outlier_scores = {
        item["handle"]: item["market_activation"]
        for item in with_outlier["accepted"]
    }
    assert baseline_scores == outlier_scores
    assert with_outlier["rejected_by_reason"]["followers_above_maximum"] == 1


def test_online_adapter_preserves_only_safe_audience_and_content_execution_aggregates() -> None:
    food = _query_cell("cell-food", "food")
    raw = _candidate(
        116,
        bio="food photographer using an on-camera flash",
        avg_views=40_000,
        audience_country_distribution={"US": 70, "CA": 30, "private@example.com": 99},
        audience_language_distribution={"en": 90, "es": 10},
        production_quality="high",
        posting_consistency_score=82,
        originality_score=77,
        matched_query_cells=[food],
    )
    raw["latest_real_video"]["title"] = "Food photography on-camera flash workflow"
    brief = {
        **_prospective_brief(),
        "target_markets": ["US"],
        "target_languages": ["en"],
    }

    result = profile_online_qualification.qualify_online_candidates(
        [raw],
        query_text=food["primary_query"],
        policy=_policy(),
        search_brief=brief,
        as_of=AS_OF,
    )

    assert result["counts"] == {"selected": 1}
    item = result["accepted"][0]
    assert item["audience_fit"] is not None
    assert item["content_execution"] is not None
    scoring = item["growth_candidate_scoring"]
    assert scoring["audience"]["target_markets"] == ["us"]
    assert scoring["audience"]["target_languages"] == ["en"]
    assert "private@example.com" not in json.dumps(result, ensure_ascii=False)


def test_controlled_aliases_keep_multi_cell_lineage_and_do_not_leak_to_other_cells() -> None:
    motorsport = _query_cell("cell-motorsport", "motorsport")
    food = _query_cell("cell-food", "food")
    raw = _candidate(
        114,
        bio="independent creator",
        sample_caption="Racing pit-lane portraits lit with a speedlight",
        avg_views=24_000,
        avg_comments=180,
        engagement_rate=0.08,
        matched_query_cells=[motorsport, food],
    )
    raw["latest_real_video"]["title"] = "Weekend production diary"

    result = profile_online_qualification.qualify_online_candidates(
        [raw],
        query_text=motorsport["primary_query"],
        policy=_policy(),
        search_brief=_prospective_brief(),
        as_of=AS_OF,
    )

    item = result["accepted"][0]
    assert item["best_qualified_cell"]["query_cell_id"] == "cell-motorsport"
    by_cell = {entry["query_cell_id"]: entry["passed"] for entry in item["cell_qualification"]}
    assert by_cell == {"cell-motorsport": True, "cell-food": False}
    assert result["cell_evaluation_count"] == 2


def test_unknown_or_client_injected_aliases_cannot_become_hard_evidence() -> None:
    motorsport = _query_cell("cell-motorsport", "motorsport")
    motorsport["locked_terms"] = {
        "product_use_fit": ["zoomlight"],
        "segment_use_case": ["trackshoot"],
    }
    # Even a correctly marked structure cannot add terms: projection rebuilds
    # the static aliases for these canonical groups.
    motorsport["locked_term_groups"]["groups"][0]["aliases"].append("zoomlight")
    motorsport["locked_term_groups"]["groups"][1]["aliases"].append("trackshoot")
    raw = _candidate(
        112,
        bio="zoomlight trackshoot creator",
        avg_views=22_000,
        engagement_rate=0.08,
        matched_query_cells=[motorsport],
    )
    raw["latest_real_video"]["title"] = "Weekly update"

    result = profile_online_qualification.qualify_online_candidates(
        [raw],
        query_text=motorsport["primary_query"],
        policy=_policy(),
        search_brief=_prospective_brief(),
        as_of=AS_OF,
    )

    assert result["accepted"] == []
    assert result["qualification_stats"]["qualified_cell_count"] == 0


def test_static_capability_use_map_proves_suitability_without_faking_product_mention() -> None:
    wedding = _query_cell("cell-wedding", "wedding")
    raw = _candidate(
        113,
        bio="documentary wedding photographer",
        avg_views=30_000,
        avg_comments=220,
        engagement_rate=0.09,
        matched_query_cells=[wedding],
    )
    raw["latest_real_video"]["title"] = "Wedding day documentary workflow"

    result = profile_online_qualification.qualify_online_candidates(
        [raw],
        query_text=wedding["primary_query"],
        policy=_policy(),
        search_brief=_prospective_brief(),
        as_of=AS_OF,
    )

    assert result["counts"] == {"selected": 1}
    evidence = result["accepted"][0]["best_qualified_cell"]["match_evidence"]
    suitability = [
        item for item in evidence
        if item.get("source") == "server_capability_use_map"
    ]
    assert suitability
    assert any(
        item["canonical_term"] == "on-camera flash"
        and item["observed_term"] == "wedding photographer"
        and item["evidence_relation"] == "capability_use_suitability"
        for item in suitability
    )


@pytest.mark.parametrize(
    ("segment", "profile_phrase"),
    [
        ("motorsport", "motorsport photographer"),
        ("motorsport", "automotive photographer"),
        ("food", "food photographer"),
        ("food", "restaurant photographer"),
        ("event", "event photographer"),
    ],
)
def test_flash_static_use_map_accepts_specific_requested_workflows(
    segment: str,
    profile_phrase: str,
) -> None:
    cell = _query_cell(f"cell-{segment}", segment)
    raw = _candidate(
        130,
        bio=f"Independent {profile_phrase}",
        avg_views=28_000,
        avg_comments=190,
        engagement_rate=0.08,
        matched_query_cells=[cell],
    )
    raw["latest_real_video"]["title"] = f"{profile_phrase} field workflow"

    result = profile_online_qualification.qualify_online_candidates(
        [raw],
        query_text=cell["primary_query"],
        policy=_policy(),
        search_brief=_prospective_brief(),
        as_of=AS_OF,
    )

    assert result["counts"] == {"selected": 1}
    selected = result["accepted"][0]
    assert selected["claim_status"] == "descriptive_only"
    assert selected["growth_candidate_scoring"]["real_outcome"]["included_in_score"] is False
    assert any(
        item.get("source") == "server_capability_use_map"
        and item.get("observed_term") == profile_phrase
        for item in selected["best_qualified_cell"]["match_evidence"]
    )


def test_chef_or_generic_camera_creator_alone_does_not_prove_flash_suitability() -> None:
    food = _query_cell("cell-food", "food")
    raw = _candidate(
        131,
        display_name="Kitchen Stories",
        bio="Chef, restaurant storyteller, camera creator and gear reviewer",
        avg_views=28_000,
        avg_comments=190,
        engagement_rate=0.08,
        matched_query_cells=[food],
    )
    raw["latest_real_video"]["title"] = "Cooking service for a restaurant event"

    result = profile_online_qualification.qualify_online_candidates(
        [raw],
        query_text=food["primary_query"],
        policy=_policy(),
        search_brief=_prospective_brief(),
        as_of=AS_OF,
    )

    assert result["accepted"] == []
    assert result["qualification_stats"]["qualified_cell_count"] == 0
    assert result["counts"] == {"rejected": 1}


@pytest.mark.parametrize(
    ("segment", "profile_phrase"),
    [
        ("sports", "sports photographer"),
        ("motorsport", "motorsport photographer"),
        ("stage", "concert photographer"),
        ("wedding", "wedding photographer"),
        ("wildlife", "wildlife photographer"),
        ("portrait", "portrait photographer"),
    ],
)
def test_135mm_static_use_map_accepts_specific_requested_workflows_descriptively(
    segment: str,
    profile_phrase: str,
) -> None:
    cell = _telephoto_query_cell(f"cell-{segment}", segment)
    raw = _candidate(
        132,
        bio=f"Independent {profile_phrase}",
        avg_views=32_000,
        avg_comments=210,
        engagement_rate=0.09,
        matched_query_cells=[cell],
    )
    raw["latest_real_video"]["title"] = f"{profile_phrase} field workflow"

    result = profile_online_qualification.qualify_online_candidates(
        [raw],
        query_text=cell["primary_query"],
        policy=_policy(),
        search_brief={
            "objective": "prospective_growth",
            "product": {"capability": "telephoto portrait lens"},
        },
        as_of=AS_OF,
    )

    assert result["counts"] == {"selected": 1}
    selected = result["accepted"][0]
    assert selected["claim_status"] == "descriptive_only"
    assert selected["growth_candidate_scoring"]["real_outcome"]["included_in_score"] is False
    assert any(
        item.get("source") == "server_capability_use_map"
        and item.get("observed_term") == profile_phrase
        for item in selected["best_qualified_cell"]["match_evidence"]
    )


def test_matched_query_cells_must_be_a_list_and_mapping_is_ignored() -> None:
    motorsport = _query_cell("cell-motorsport", "motorsport")
    food = _query_cell("cell-food", "food")
    raw = _candidate(
        102,
        bio="food creator using on-camera flash",
        avg_views=20_000,
        query_cell_id=motorsport["query_cell_id"],
        query_cell_segment=motorsport["segment"],
        query_cell_query=motorsport["primary_query"],
        targeted_search=motorsport,
        matched_query_cells=food,
    )

    result = profile_online_qualification.qualify_online_candidates(
        [raw],
        query_text=motorsport["primary_query"],
        policy=_policy(),
        search_brief=_prospective_brief(),
        as_of=AS_OF,
    )

    assert result["accepted"] == []
    assert result["counts"] == {"rejected": 1}
    assert result["cell_evaluation_count"] == 1
    assert result["qualification_stats"]["qualified_cell_count"] == 0


@pytest.mark.parametrize(
    ("content_field", "content_value", "expected_evidence_field"),
    [
        ("sample_description", "Food shoot workflow using an on-camera flash", "representative_evidence.description"),
        ("sample_caption", "Food shoot workflow using an on-camera flash", "representative_evidence.caption"),
        ("sample_transcript", "Food shoot workflow using an on-camera flash", "representative_evidence.transcript"),
        ("subtitles", [{"text": "Food shoot workflow using an on-camera flash"}], "representative_evidence.subtitles"),
    ],
)
def test_content_fields_can_prove_product_and_scene_when_title_does_not(
    content_field: str,
    content_value: Any,
    expected_evidence_field: str,
) -> None:
    food = _query_cell("cell-food", "food")
    raw = _candidate(
        103,
        bio="independent creator",
        avg_views=15_000,
        avg_comments=120,
        engagement_rate=0.07,
        matched_query_cells=[food],
        **{content_field: content_value},
    )
    raw["latest_real_video"]["title"] = "Weekly studio update"

    result = profile_online_qualification.qualify_online_candidates(
        [raw],
        query_text=food["primary_query"],
        policy=_policy(),
        search_brief=_prospective_brief(),
        as_of=AS_OF,
    )

    assert result["counts"] == {"selected": 1}
    item = result["accepted"][0]
    fields = {entry["field"] for entry in item["best_qualified_cell"]["match_evidence"]}
    assert expected_evidence_field in fields
    assert "Food shoot workflow" not in json.dumps(item, ensure_ascii=False)
    assert item["content_evidence_status"]["text_exposed"] is False


def test_missing_content_detail_with_video_locator_is_pending_not_low_relevance() -> None:
    food = _query_cell("cell-food", "food")
    raw = _candidate(
        104,
        bio="independent creator",
        avg_views=15_000,
        content_url="https://www.youtube.com/watch?v=pending104",
        matched_query_cells=[food],
    )
    raw["latest_real_video"]["title"] = "Weekly studio update"

    result = profile_online_qualification.qualify_online_candidates(
        [raw],
        query_text=food["primary_query"],
        policy=_policy(),
        search_brief=_prospective_brief(),
        as_of=AS_OF,
    )

    assert result["accepted"] == []
    assert result["counts"] == {"pending": 1}
    assert result["rejected_by_reason"] == {"pending_content_evidence": 1}
    assert "low_relevance" not in result["rejected_by_reason"]
