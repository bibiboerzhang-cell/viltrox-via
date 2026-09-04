from __future__ import annotations

import pytest

from app.domains.kol.growth_candidate_scoring import score_growth_candidates
from app.domains.kol.profile_recall_match_evidence import (
    CAPABILITY_USE_EVIDENCE_SOURCE,
    CONTROLLED_ALIAS_EVIDENCE_SOURCE,
    build_controlled_alias_evidence,
)
from app.domains.kol.targeted_search_terms import (
    build_locked_term_groups,
    controlled_capability_use_terms_for,
    project_locked_term_groups,
)


def _evaluate_public_role(
    *,
    capability: str,
    segment: str,
    bio: str,
    title: str,
) -> tuple[list[dict[str, str]], dict[str, object]]:
    locked = build_locked_term_groups(capability=capability, segment=segment)
    evidence = build_controlled_alias_evidence(
        {
            "bio": bio,
            "display_name": "Public creator",
        },
        {"representative_evidence": [{"title": title}]},
        locked,
    )
    [scored] = score_growth_candidates(
        [
            {
                "platform": "youtube",
                "followers": 80_000,
                "avg_views": 20_000,
                "avg_comments": 120,
                "engagement_rate": 0.06,
                "match_evidence": evidence,
            }
        ],
        {"objective": "prospective_growth"},
        {
            "query_cell_id": f"cell-{segment}",
            "segment": segment,
            "primary_query": f"{segment} visual production workflow",
            "locked_term_groups": locked,
        },
    )
    return evidence, scored


def _product_use_rows(evidence: list[dict[str, str]]) -> list[dict[str, str]]:
    return [
        row
        for row in evidence
        if row.get("source") == CAPABILITY_USE_EVIDENCE_SOURCE
        and row.get("evidence_group") == "product_use_fit"
    ]


def test_monitor_registry_is_role_bounded_and_rebuilt_server_side() -> None:
    terms = set(controlled_capability_use_terms_for("field monitor"))
    assert {
        "cinematographer",
        "camera operator",
        "solo filmmaker",
        "wedding videographer",
        "product filmmaker",
    } <= terms
    assert not {"creator", "streamer", "gamer", "gaming creator"}.intersection(terms)
    assert all("viltrox" not in term and not any(char.isdigit() for char in term) for term in terms)

    locked = build_locked_term_groups(capability="camera monitor", segment="wedding")
    locked["groups"][0]["use_suitability_terms"].append("gaming streamer")
    projected = project_locked_term_groups(locked)

    assert projected is not None
    assert "gaming streamer" not in projected["groups"][0]["use_suitability_terms"]


def test_camera_monitor_product_fit_can_use_public_video_profession_evidence() -> None:
    evidence, scored = _evaluate_public_role(
        capability="camera monitor",
        segment="wedding",
        bio="Wedding videographer and solo filmmaker documenting real celebrations.",
        title="Wedding ceremony camera workflow behind the scenes",
    )

    product_rows = _product_use_rows(evidence)
    assert product_rows
    assert product_rows[0]["canonical_term"] == "camera monitor"
    assert product_rows[0]["observed_term"] in {"solo filmmaker", "wedding videographer"}
    assert not any(
        row.get("source") == CONTROLLED_ALIAS_EVIDENCE_SOURCE
        and row.get("evidence_group") == "product_use_fit"
        for row in evidence
    )
    assert scored["product_use_fit"] is not None
    assert scored["product_scene_evidence_pass"] is True
    assert scored["claim_status"] == "descriptive_only"


def test_generic_camera_lens_product_fit_can_use_public_visual_role_evidence() -> None:
    evidence, scored = _evaluate_public_role(
        capability="camera lens",
        segment="motorsport",
        bio="Professional photographer covering race weekends and automotive stories.",
        title="Racing paddock production workflow",
    )

    product_rows = _product_use_rows(evidence)
    assert product_rows
    assert product_rows[0]["canonical_term"] == "camera lens"
    assert product_rows[0]["observed_term"] == "professional photographer"
    assert scored["product_use_fit"] is not None
    assert scored["product_scene_evidence_pass"] is True
    assert scored["growth_candidate_scoring"]["brand_history_weight"] == 0.0


def test_creator_gear_fallback_stays_on_controlled_visual_professions() -> None:
    terms = set(controlled_capability_use_terms_for("creator gear"))
    assert {"professional photographer", "professional filmmaker", "professional videographer"} <= terms
    assert not {"content creator", "influencer", "streamer", "gamer"}.intersection(terms)

    locked = build_locked_term_groups(capability="creator gear", segment="documentary")
    assert locked["groups"][0]["canonical_term"] == "creator gear"
    assert locked["groups"][0]["alias_policy"] == "static_allowlist"
    assert locked["groups"][0]["use_suitability_terms"]

    evidence, scored = _evaluate_public_role(
        capability="creator gear",
        segment="documentary",
        bio="Documentary filmmaker and camera operator working in the field.",
        title="Documentary production diary",
    )

    assert _product_use_rows(evidence)
    assert scored["product_use_fit"] is not None
    assert scored["product_scene_evidence_pass"] is True


@pytest.mark.parametrize(
    ("capability", "segment", "role"),
    [
        ("cinema lens", "filmmaking_role", "filmmaker"),
        ("cinema lens", "film_direction", "film director"),
        ("camera monitor", "film_direction", "movie director"),
    ],
)
def test_server_controlled_people_roles_can_prove_capability_and_scene(
    capability: str,
    segment: str,
    role: str,
) -> None:
    evidence, scored = _evaluate_public_role(
        capability=capability,
        segment=segment,
        bio=f"{role} creating feature films",
        title="Feature film production diary",
    )

    assert evidence
    assert _product_use_rows(evidence)
    assert scored["product_scene_evidence_pass"] is True


@pytest.mark.parametrize("capability", ["camera monitor", "camera lens", "creator gear"])
def test_non_visual_gaming_profile_cannot_pass_even_when_scene_text_matches(
    capability: str,
) -> None:
    evidence, scored = _evaluate_public_role(
        capability=capability,
        segment="motorsport",
        bio="Gaming streamer and esports commentator.",
        title="Racing simulator tournament highlights",
    )

    assert not _product_use_rows(evidence)
    assert any(row.get("evidence_group") == "segment_use_case" for row in evidence)
    assert scored["product_use_fit"] is None
    assert scored["product_scene_evidence_pass"] is False
    assert "product_use_fit" in scored["growth_candidate_scoring"]["evidence_contract"]["missing_groups"]
