from __future__ import annotations

from copy import deepcopy

import pytest

from app.domains.kol.growth_candidate_scoring import (
    ACTIVATION_SIGNAL_WEIGHTS,
    CLAIM_STATUS,
    growth_candidate_sort_key,
    score_growth_candidates,
)
from app.domains.kol.targeted_search_contract import (
    build_locked_term_groups,
    build_query_cells,
)
from app.domains.kol.profile_recall_match_evidence import build_controlled_alias_evidence
from app.domains.kol.profile_query_cell_evidence import build_query_cell_match_evidence
from app.domains.kol.search_sessions_targeted import project_growth_candidate_context


SEARCH_BRIEF = {
    "objective": "prospective_growth",
    "market": "US",
    "languages": ["en"],
}
QUERY_CELL = {
    "query_cell_id": "segment_1_motorsport",
    "segment": "motorsport",
    "primary_query": "motorsport photographer on-camera flash",
    "locked_term_groups": build_locked_term_groups(
        capability="on-camera flash",
        segment="motorsport",
    ),
    "follower_filter": {
        "followers_min": 50_000,
        "followers_max": 500_000,
        "locked": True,
    },
}


def _proof() -> list[dict[str, object]]:
    return [
        {
            "field": "representative_evidence.title",
            "term": "speedlight",
            "source": "public_video_evidence",
        },
        {
            "field": "representative_evidence.transcript",
            "term": "racing",
            "source": "public_video_evidence",
        },
    ]


def _candidate(item_id: int, **overrides: object) -> dict[str, object]:
    item: dict[str, object] = {
        "kol_pool_id": item_id,
        "platform": "youtube",
        "followers": 25_000,
        "avg_views": 12_500,
        "engagement_rate": 0.08,
        "avg_comments": 150,
        "match_evidence": _proof(),
        "audience_fit_score": 80,
        "content_execution_score": 75,
        "production_quality_score": 85,
        "evidence_quality": {"video_evidence_count": 5, "deep_analysis_count": 3},
    }
    item.update(overrides)
    return item


def test_specific_product_use_and_scene_proof_produces_descriptive_scores() -> None:
    [scored] = score_growth_candidates(
        [
            _candidate(
                1,
                evidence_quality={"video_evidence_count": 1, "deep_analysis_count": 1},
            )
        ],
        SEARCH_BRIEF,
        QUERY_CELL,
    )

    assert scored["product_use_fit"] is not None
    assert scored["market_activation"] is not None
    assert scored["audience_fit"] == 80
    assert scored["content_execution"] is not None
    assert scored["evidence_confidence"] > 0
    assert scored["growth_candidate_score"] is not None
    assert scored["claim_status"] == CLAIM_STATUS == "descriptive_only"

    contract = scored["growth_candidate_scoring"]
    assert contract["evidence_contract"]["passed"] is True
    assert contract["evidence_contract"]["matched_product_terms"] == ["speedlight"]
    assert contract["evidence_contract"]["matched_scene_terms"] == ["racing"]
    assert contract["brand_history_weight"] == 0
    assert contract["real_outcome"]["included_in_score"] is False
    rationale = scored["selection_rationale"]
    assert rationale["schema"] == "prospective_candidate_rationale_v1"
    assert rationale["purpose"].startswith("寻找可能在motorsport中需要on camera flash")
    assert rationale["strict_gate_status"] == "blocked"
    assert rationale["next_action"]["code"] == "fetch_recent_3_5_video_metrics"
    assert any("公开内容同时支持产品用途" in reason for reason in rationale["why_find_this_creator"])


def test_accepts_transitional_search_brief_and_query_cell_contract() -> None:
    brief = {
        "objective": "prospective_growth",
        "product": {"capability": "on-camera flash", "resolved_sku": "Z1-PRO"},
    }
    cell = {
        "segment": "motorsport",
        "segment_label": "赛车",
        "primary_query": "motorsport photographer on-camera flash",
    }

    [scored] = score_growth_candidates([_candidate(1)], brief, cell)

    assert scored["product_scene_evidence_pass"] is True
    evidence = scored["growth_candidate_scoring"]["evidence_contract"]
    assert "on camera flash" in evidence["required_product_terms"]
    assert "motorsport" in evidence["required_scene_terms"]


def test_ignores_flat_client_locked_term_lists() -> None:
    cell = {"locked_terms": ["Viltrox", "Sony", "speedlight", "racing"]}

    [scored] = score_growth_candidates([_candidate(1)], SEARCH_BRIEF, cell)

    evidence = scored["growth_candidate_scoring"]["evidence_contract"]
    assert evidence["required_product_terms"] == []
    assert evidence["required_scene_terms"] == []
    assert evidence["passed"] is False


def test_people_only_search_requires_scene_not_fake_creator_gear() -> None:
    locked = build_locked_term_groups(
        capability="", segment="food", role_terms=["content creator"],
    )
    evidence = build_controlled_alias_evidence(
        {"bio": "Food video creator making restaurant reviews and short recipe videos."},
        {"representative_evidence": [{"title": "Restaurant recipe production diary"}]},
        locked,
    )
    cell = {
        "query_cell_id": "segment_1_food",
        "segment": "food",
        "primary_query": "food content creator",
        "product_evidence_required": False,
        "product_evidence_basis": "none",
        "required_role_terms": ["content creator"],
        "locked_term_groups": locked,
    }

    [scored] = score_growth_candidates(
        [_candidate(1, match_evidence=evidence)],
        {"objective": "prospective_growth", "product": {"evidence_required": False}},
        cell,
    )

    contract = scored["growth_candidate_scoring"]["evidence_contract"]
    assert contract["passed"] is True
    assert contract["product_evidence_required"] is False
    assert contract["required_product_terms"] == []
    assert contract["required_role_terms"] == ["content creator"]
    assert contract["matched_role_terms"] == ["video creator"]
    assert "product_use_fit" not in contract["missing_groups"]
    assert scored["product_use_fit"] is None
    assert scored["product_scene_evidence_pass"] is True
    assert scored["selection_rationale"]["reason_cards"][0]["code"] == "people_and_scene"
    assert "目标产品能力" not in scored["selection_rationale"]["purpose"]


def test_intersection_cell_requires_every_requested_scene() -> None:
    locked = build_locked_term_groups(
        capability="",
        segment="street",
        scene_terms=["street", "night"],
        role_terms=["photographer"],
    )
    cell = {
        "query_cell_id": "segment_1_street",
        "segment": "street",
        "required_scene_terms": ["street", "night"],
        "scene_match_mode": "all",
        "required_role_terms": ["photographer"],
        "primary_query": "street night photographer",
        "product_evidence_required": False,
        "locked_term_groups": locked,
    }
    street_only = build_controlled_alias_evidence(
        {"bio": "Street photographer documenting city life."},
        {},
        locked,
    )
    both = build_controlled_alias_evidence(
        {"bio": "Street photographer specializing in night photography."},
        {},
        locked,
    )

    first, second = score_growth_candidates(
        [
            _candidate(1, match_evidence=street_only),
            _candidate(2, match_evidence=both),
        ],
        SEARCH_BRIEF,
        cell,
    )

    assert first["product_scene_evidence_pass"] is False
    assert first["growth_candidate_scoring"]["evidence_contract"]["missing_scene_terms"] == [
        "night"
    ]
    assert second["product_scene_evidence_pass"] is True


@pytest.mark.parametrize(
    ("query", "wrong_profile", "right_profile"),
    [
        (
            "Find wedding photographers",
            "Wedding planner coordinating bridal ceremonies.",
            "Wedding photographer documenting bridal ceremonies.",
        ),
        (
            "Find sports videographers",
            "Football fan and sideline commentator covering every match.",
            "Sports videographer filming football matches from the sideline.",
        ),
        (
            "Find food photographers",
            "Food critic and recipe writer reviewing restaurants.",
            "Food photographer creating restaurant and recipe imagery.",
        ),
        (
            "Find chefs",
            "Food content creator reviewing restaurants.",
            "Chef sharing culinary and restaurant videos.",
        ),
        (
            "Find gear reviewers",
            "Photographer publishing camera gear reviews.",
            "Gear reviewer testing camera equipment.",
        ),
        (
            "Find independent film directors",
            "Independent film critic covering festivals.",
            "Independent film director making festival features.",
        ),
        (
            "Find wedding planners",
            "Wedding photographer documenting bridal ceremonies.",
            "Wedding planner coordinating bridal ceremonies.",
        ),
        (
            "Find sports commentators",
            "Sports photographer documenting every match.",
            "Sports commentator covering every match.",
        ),
    ],
)
def test_people_search_requires_requested_role_and_scene(
    query: str,
    wrong_profile: str,
    right_profile: str,
) -> None:
    [cell] = build_query_cells(
        query=query,
        body={},
        product={},
        product_focus=[],
        platforms=[],
    )
    locked = cell["locked_term_groups"]
    wrong_evidence = build_controlled_alias_evidence({"bio": wrong_profile}, {}, locked)
    right_evidence = build_controlled_alias_evidence({"bio": right_profile}, {}, locked)

    wrong, right = score_growth_candidates(
        [
            _candidate(1, match_evidence=wrong_evidence),
            _candidate(2, match_evidence=right_evidence),
        ],
        SEARCH_BRIEF,
        cell,
    )

    wrong_contract = wrong["growth_candidate_scoring"]["evidence_contract"]
    right_contract = right["growth_candidate_scoring"]["evidence_contract"]
    assert wrong_contract["passed"] is False
    assert wrong_contract["missing_role_terms"] == cell["required_role_terms"]
    assert "people_role" in wrong_contract["missing_groups"]
    assert right_contract["passed"] is True
    assert right_contract["matched_role_terms"]


def test_two_explicit_occupations_require_both_profile_identities() -> None:
    [cell] = build_query_cells(
        query="Find photographers who are also filmmakers",
        body={}, product={}, product_focus=[], platforms=[],
    )
    locked = cell["locked_term_groups"]
    one_role = build_controlled_alias_evidence(
        {"bio": "Photographer documenting real-world stories."}, {}, locked,
    )
    both_roles = build_controlled_alias_evidence(
        {"bio": "Photographer and filmmaker documenting real-world stories."}, {}, locked,
    )

    first, second = score_growth_candidates(
        [
            _candidate(1, match_evidence=one_role),
            _candidate(2, match_evidence=both_roles),
        ],
        SEARCH_BRIEF,
        cell,
    )

    first_contract = first["growth_candidate_scoring"]["evidence_contract"]
    second_contract = second["growth_candidate_scoring"]["evidence_contract"]
    assert cell["role_match_mode"] == "all"
    assert first_contract["passed"] is False
    assert first_contract["missing_role_terms"] == ["filmmaker"]
    assert second_contract["passed"] is True
    assert second_contract["missing_role_terms"] == []


def test_representative_content_mention_cannot_impersonate_requested_role() -> None:
    [cell] = build_query_cells(
        query="Find film directors",
        body={},
        product={},
        product_focus=[],
        platforms=[],
    )
    row = {"bio": "Film critic and festival interviewer"}
    evidence_payload = {
        "representative_evidence": [{"title": "Interview with a film director"}],
    }
    match_evidence = build_query_cell_match_evidence(
        row,
        evidence_payload,
        cell["primary_query"],
        query_cell=cell,
    )

    [scored] = score_growth_candidates(
        [_candidate(1, match_evidence=match_evidence)],
        SEARCH_BRIEF,
        cell,
    )

    contract = scored["growth_candidate_scoring"]["evidence_contract"]
    assert contract["passed"] is False
    assert contract["required_role_terms"] == ["director"]
    assert contract["matched_role_terms"] == []
    assert contract["missing_role_terms"] == ["director"]


def test_profile_text_video_aggregate_cannot_impersonate_requested_role() -> None:
    [cell] = build_query_cells(
        query="Find independent film directors",
        body={},
        product={},
        product_focus=[],
        platforms=[],
    )
    row = {
        "bio": "Film critic and festival interviewer",
        # The real profile-index builder appends video evidence titles to this
        # field.  It is useful scene evidence, but cannot establish occupation.
        "profile_text": (
            "KOL profile text for vector recall only. "
            "Evidence titles: Interview with an independent film director"
        ),
    }
    match_evidence = build_query_cell_match_evidence(
        row,
        {},
        cell["primary_query"],
        query_cell=cell,
    )

    assert not any(
        evidence.get("evidence_group") == "people_role"
        for evidence in match_evidence
    )
    [scored] = score_growth_candidates(
        [_candidate(1, match_evidence=match_evidence)],
        SEARCH_BRIEF,
        cell,
    )

    contract = scored["growth_candidate_scoring"]["evidence_contract"]
    assert contract["passed"] is False
    assert contract["matched_role_terms"] == []
    assert contract["missing_role_terms"] == ["director"]


def test_night_scene_does_not_treat_posting_every_night_as_night_photography() -> None:
    [cell] = build_query_cells(
        query="Find street photographers who also shoot night photography",
        body={},
        product={},
        product_focus=[],
        platforms=[],
    )
    locked = cell["locked_term_groups"]
    posting_row = {"bio": "Street photographer. I publish a new post every night."}
    posting_evidence = build_query_cell_match_evidence(
        posting_row, {}, cell["primary_query"], query_cell=cell,
    )
    photography_row = {"bio": "Street photographer specializing in night photography."}
    photography_evidence = build_query_cell_match_evidence(
        photography_row, {}, cell["primary_query"], query_cell=cell,
    )

    posting, photography = score_growth_candidates(
        [
            _candidate(1, match_evidence=posting_evidence),
            _candidate(2, match_evidence=photography_evidence),
        ],
        SEARCH_BRIEF,
        cell,
    )

    assert posting["product_scene_evidence_pass"] is False
    assert posting["growth_candidate_scoring"]["evidence_contract"]["missing_scene_terms"] == ["night"]
    assert photography["product_scene_evidence_pass"] is True


def test_audience_distributions_use_target_market_and_language_aliases() -> None:
    item = _candidate(
        1,
        audience_fit_score=None,
        audience_market_distribution={"United States": 70, "Canada": 30},
        audience_language_distribution={"English": 0.8, "Spanish": 0.2},
    )
    brief = {"market": "US", "languages": ["en-US"]}

    [scored] = score_growth_candidates([item], brief, QUERY_CELL)

    # 70% market share and 80% language share, with observed weights
    # renormalised because no direct audience-fit score is available.
    assert scored["audience_fit"] == pytest.approx((70 * 0.28 + 80 * 0.12) / 0.40)


@pytest.mark.parametrize(
    "evidence",
    [
        [{"field": "bio", "term": "Viltrox"}],
        [{"field": "bio", "term": "Sony"}, {"field": "bio", "term": "camera"}],
        [
            {"field": "bio", "term": "Viltrox", "evidence_group": "product_use_fit"},
            {"field": "bio", "term": "photographer", "evidence_group": "segment_use_case"},
        ],
    ],
)
def test_brand_ecosystem_or_generic_camera_terms_cannot_pass_product_scene_gate(
    evidence: list[dict[str, str]],
) -> None:
    [scored] = score_growth_candidates(
        [_candidate(1, match_evidence=evidence)],
        SEARCH_BRIEF,
        QUERY_CELL,
    )

    assert scored["product_use_fit"] is None
    assert scored["growth_candidate_score"] is None
    assert scored["growth_candidate_scoring"]["confidence"]["decision_mode"] == (
        "not_rankable_product_scene_evidence"
    )
    assert scored["growth_candidate_scoring"]["evidence_contract"]["passed"] is False


def test_brand_history_and_viltrox_evidence_have_exactly_zero_weight() -> None:
    baseline = _candidate(1)
    brand_heavy = _candidate(
        2,
        viltrox_mention_count=900,
        viltrox_fit_score=100,
        brand_affinity_score=100,
        existing_viltrox_user=True,
        match_evidence=[*_proof(), {"field": "bio", "term": "Viltrox"}],
    )

    first, second = score_growth_candidates([baseline, brand_heavy], SEARCH_BRIEF, QUERY_CELL)

    for key in (
        "product_use_fit",
        "market_activation",
        "audience_fit",
        "content_execution",
        "evidence_confidence",
        "growth_candidate_score",
    ):
        assert first[key] == second[key]
    assert second["growth_candidate_scoring"]["ignored_brand_history_fields"] == [
        "brand_affinity_score",
        "existing_viltrox_user",
        "viltrox_fit_score",
        "viltrox_mention_count",
    ]
    assert second["growth_candidate_scoring"]["evidence_contract"][
        "ignored_generic_or_brand_terms"
    ] == ["viltrox"]


def test_missing_signals_are_renormalised_and_reduce_confidence_not_imputed_as_zero() -> None:
    sparse = _candidate(
        1,
        followers=None,
        avg_views=None,
        engagement_rate=None,
        avg_comments=None,
        audience_fit_score=None,
        content_execution_score=None,
        production_quality_score=None,
        evidence_quality={},
        platform_percentiles={"avg_views": 0.80},
    )
    complete = _candidate(
        2,
        platform_percentiles={
            "avg_views": 0.80,
            "engagement": 0.75,
            "views_per_follower": 0.70,
            "comments_per_follower": 0.65,
            "followers_reach": 0.50,
        },
    )

    sparse_scored, complete_scored = score_growth_candidates(
        [sparse, complete], SEARCH_BRIEF, QUERY_CELL
    )

    # The observed percentile stays 80 after renormalisation; missing signals
    # do not turn into four zero-quality observations.
    assert sparse_scored["market_activation"] == 80
    assert sparse_scored["audience_fit"] is None
    assert sparse_scored["content_execution"] is None
    assert sparse_scored["growth_candidate_score"] > 0
    assert sparse_scored["evidence_confidence"] < complete_scored["evidence_confidence"]
    assert sparse_scored["growth_candidate_scoring"]["missing_value_policy"] == (
        "omit_and_renormalize_never_zero_impute"
    )


def test_raw_performance_is_calibrated_within_each_platform() -> None:
    candidates = [
        _candidate(1, platform="youtube", followers=1_000, avg_views=100, engagement_rate=0.01, avg_comments=1),
        _candidate(2, platform="youtube", followers=1_000, avg_views=1_000, engagement_rate=0.10, avg_comments=10),
        _candidate(3, platform="instagram", followers=1_000, avg_views=10, engagement_rate=0.01, avg_comments=1),
        _candidate(4, platform="instagram", followers=1_000, avg_views=100, engagement_rate=0.10, avg_comments=10),
    ]

    scored = score_growth_candidates(candidates, SEARCH_BRIEF, QUERY_CELL)
    youtube_high, instagram_high = scored[1], scored[3]
    youtube_low, instagram_low = scored[0], scored[2]

    assert youtube_high["market_activation"] == instagram_high["market_activation"]
    assert youtube_low["market_activation"] == instagram_low["market_activation"]
    assert youtube_high["market_activation"] > youtube_low["market_activation"]
    assert youtube_high["growth_candidate_scoring"]["platform_activation"]["values"] == {
        "avg_views": 1.0,
        "engagement": 1.0,
        "views_per_follower": 1.0,
        "comments_per_follower": 1.0,
        "followers_reach": 0.5,
    }


def test_upstream_platform_percentiles_are_accepted_without_raw_metrics() -> None:
    item = _candidate(
        1,
        followers=None,
        avg_views=None,
        engagement_rate=None,
        avg_comments=None,
        platform_calibration={
            "values": {
                "avg_views": 0.90,
                "engagement": 0.80,
                "view_rate": 0.70,
                "comment_rate": 0.60,
            }
        },
    )

    [scored] = score_growth_candidates([item], SEARCH_BRIEF, QUERY_CELL)

    values = scored["growth_candidate_scoring"]["platform_activation"]["values"]
    assert values == {
        "avg_views": 0.9,
        "engagement": 0.8,
        "views_per_follower": 0.7,
        "comments_per_follower": 0.6,
    }
    assert scored["market_activation"] is not None
    assert scored["market_activation_pass"] is False
    assert scored["market_activation_status"] == "market_activation_missing"


def test_one_play_and_zero_interaction_is_below_absolute_activation_floor() -> None:
    item = _candidate(
        1,
        followers=50_000,
        avg_views=1,
        engagement_rate=0,
        avg_comments=0,
        activation_sample_count=5,
        activation_metrics_scope="recent_video_aggregate_45d",
    )

    [scored] = score_growth_candidates([item], SEARCH_BRIEF, QUERY_CELL)

    assert scored["market_activation_pass"] is False
    assert scored["market_activation_status"] == "below_floor"
    gate = scored["growth_candidate_scoring"]["platform_activation"]["strict_gate"]
    assert gate["sample_count"] == 5
    assert not any(gate["floor_results"].values())
    assert gate["conversion_claim"] is False
    rationale = scored["selection_rationale"]
    assert rationale["next_action"]["code"] == "deprioritize_below_activation_floor"
    assert not any(
        item["code"] == "below_floor" for item in rationale["missing_evidence"]
    )


def test_metric_with_one_observation_cannot_pass_three_sample_activation_gate() -> None:
    item = _candidate(
        1,
        followers=50_000,
        avg_views=80_000,
        engagement_rate=None,
        avg_comments=None,
        activation_sample_count=3,
        activation_metric_sample_counts={
            "avg_views": 1,
            "engagement": 0,
            "views_per_follower": 1,
            "comments_per_follower": 0,
        },
        activation_metrics_source="youtube_data_api.videos.list",
        activation_metrics_scope="exact_query_hits_45d_aggregate",
    )

    [scored] = score_growth_candidates([item], SEARCH_BRIEF, QUERY_CELL)

    assert scored["market_activation_pass"] is False
    assert scored["market_activation_status"] == "insufficient_metric_sample"
    gate = scored["growth_candidate_scoring"]["platform_activation"]["strict_gate"]
    assert gate["sample_count"] == 3
    assert gate["metric_sample_counts"]["avg_views"] == 1
    assert gate["metric_sample_sufficient"]["avg_views"] is False
    assert gate["floor_results"]["avg_views"] is False
    rationale = scored["selection_rationale"]
    assert rationale["strict_gate_status"] == "blocked"
    assert rationale["activation_evidence"]["metric_sample_counts"]["avg_views"] == 1
    assert "指标有效观测不足" in rationale["reason_cards"][1]["summary"]


def test_single_representative_video_is_provisional_not_strict_activation() -> None:
    item = _candidate(
        1,
        avg_views=None,
        engagement_rate=None,
        avg_comments=None,
        representative_video_views=80_000,
        representative_video_likes=4_000,
        representative_video_comments=120,
        activation_sample_count=1,
        activation_metrics_scope="exact_query_hit_45d",
    )

    [scored] = score_growth_candidates([item], SEARCH_BRIEF, QUERY_CELL)

    assert scored["market_activation"] is not None
    assert scored["market_activation_pass"] is False
    assert scored["market_activation_status"] == "insufficient_sample"


@pytest.mark.parametrize("sample_count", [3, 5])
def test_recent_aggregate_with_three_to_five_samples_and_floor_passes(
    sample_count: int,
) -> None:
    item = _candidate(
        1,
        followers=50_000,
        avg_views=4_000,
        engagement_rate=0.03,
        avg_comments=40,
        activation_sample_count=sample_count,
        activation_metrics_source="local_video_evidence.aggregate",
        activation_metrics_scope="recent_video_aggregate_45d",
    )

    [scored] = score_growth_candidates([item], SEARCH_BRIEF, QUERY_CELL)

    assert scored["market_activation_pass"] is True
    assert scored["market_activation_status"] == "passed"
    gate = scored["growth_candidate_scoring"]["platform_activation"]["strict_gate"]
    assert gate["sample_count"] == sample_count
    assert gate["floor_results"]["avg_views"] is True
    assert gate["claim_status"] == "descriptive_only"
    rationale = scored["selection_rationale"]
    assert rationale["strict_gate_status"] == "passed"
    assert rationale["decision_readiness"] == "decision_support_ready"
    assert rationale["next_action"]["code"] == "human_review_before_outreach"


def test_session_projection_keeps_gate_status_and_safe_selection_rationale() -> None:
    [scored] = score_growth_candidates(
        [_candidate(
            1,
            activation_sample_count=3,
            activation_metrics_scope="recent_video_aggregate_45d",
        )],
        SEARCH_BRIEF,
        QUERY_CELL,
    )

    projected = project_growth_candidate_context(scored)

    assert projected["market_activation_pass"] is True
    assert projected["market_activation_status"] == "passed"
    gate = projected["growth_candidate_scoring"]["market_activation_gate"]
    assert gate["sample_count"] == 3
    assert gate["minimum_sample_count"] == 3
    assert gate["metric_sample_counts"]["avg_views"] == 3
    rationale = projected["selection_rationale"]
    assert rationale["schema"] == "prospective_candidate_rationale_v1"
    assert rationale["strict_gate_status"] == "passed"
    assert rationale["conversion_claim"] is False
    assert rationale["outreach_decision"] is False
    assert rationale["activation_evidence"]["metric_sample_counts"]["avg_views"] == 3
    assert "@" not in str(rationale)


def test_channel_lifetime_views_proxy_cannot_be_the_only_activation_signal() -> None:
    item = _candidate(
        1,
        avg_views=25_000,
        avg_views_source="youtube_channel_lifetime_view_count_div_video_count",
        avg_views_scope="channel_lifetime_proxy",
        engagement_rate=None,
        avg_comments=None,
        platform_percentiles={"avg_views": 0.95, "views_per_follower": 0.90},
    )

    [scored] = score_growth_candidates([item], SEARCH_BRIEF, QUERY_CELL)

    assert scored["market_activation"] is None
    assert scored["market_activation_pass"] is False
    assert scored["market_activation_status"] == "market_activation_missing"
    activation = scored["growth_candidate_scoring"]["platform_activation"]
    assert activation["channel_lifetime_proxy"] is True
    assert activation["followers_policy"] == "low_weight_reach_signal_never_eligibility_gate"


def test_followers_are_only_a_low_weight_signal_and_never_a_qualification_gate() -> None:
    below_operator_range = _candidate(
        1,
        followers=500,
        platform_percentiles={
            "avg_views": 0.7,
            "engagement": 0.7,
            "views_per_follower": 0.7,
            "comments_per_follower": 0.7,
            "followers_reach": 0.0,
        },
    )
    large = _candidate(
        2,
        followers=5_000_000,
        platform_percentiles={
            "avg_views": 0.7,
            "engagement": 0.7,
            "views_per_follower": 0.7,
            "comments_per_follower": 0.7,
            "followers_reach": 1.0,
        },
    )

    small_scored, large_scored = score_growth_candidates(
        [below_operator_range, large], SEARCH_BRIEF, QUERY_CELL
    )

    assert small_scored["growth_candidate_score"] is not None
    assert small_scored["growth_candidate_scoring"]["followers_policy"] == (
        "low_weight_reach_signal_never_eligibility_gate"
    )
    assert ACTIVATION_SIGNAL_WEIGHTS["followers_reach"] == 0.05
    assert large_scored["market_activation"] - small_scored["market_activation"] == pytest.approx(5.0)


def test_real_outcomes_are_reported_separately_and_do_not_change_proxy_score() -> None:
    baseline = _candidate(1)
    with_outcome = _candidate(
        2,
        verified_conversions=500,
        attributed_orders=300,
        outcome={"agreement": True, "content_published": True},
    )

    first, second = score_growth_candidates([baseline, with_outcome], SEARCH_BRIEF, QUERY_CELL)

    assert first["growth_candidate_score"] == second["growth_candidate_score"]
    assert first["evidence_confidence"] == second["evidence_confidence"]
    outcome = second["growth_candidate_scoring"]["real_outcome"]
    assert outcome["available"] is True
    assert outcome["included_in_score"] is False
    assert outcome["weight"] == 0
    assert outcome["fields"] == [
        "agreement",
        "attributed_orders",
        "content_published",
        "verified_conversions",
    ]


def test_scoring_is_pure_preserves_order_and_exposes_stable_sort_key() -> None:
    items = [_candidate(2), _candidate(1, audience_fit_score=20)]
    before = deepcopy(items)

    scored = score_growth_candidates(items, SEARCH_BRIEF, QUERY_CELL)

    assert items == before
    assert [item["kol_pool_id"] for item in scored] == [2, 1]
    assert growth_candidate_sort_key(scored[0]) > growth_candidate_sort_key(scored[1])
