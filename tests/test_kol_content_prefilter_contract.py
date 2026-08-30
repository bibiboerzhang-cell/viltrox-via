from __future__ import annotations

import pytest

from app.domains.kol.discovery_filters import _candidate_blob, _has_camera_signal
from app.domains.kol.discovery_persona_terms import _persona_relevance
from app.domains.kol.profile_recall_match_evidence import build_match_evidence
from app.domains.kol.search_sessions_attach import _safe_match_evidence


def test_provider_description_is_candidate_owned_prefilter_and_persona_evidence() -> None:
    candidate = {
        "sample_title": "A day at the circuit",
        "channel_name": "Alex Rivera",
        "handle": "alexrivera",
        "bio": "Independent creator",
        "sample_description": "Motorsport cinematography and race-day storytelling workflow",
        # Query text is deliberately tempting: it must never be copied into the candidate blob.
        "search_query": "wildlife photography",
    }

    blob = _candidate_blob(candidate)

    assert "motorsport cinematography" in blob
    assert "wildlife photography" not in blob
    assert _has_camera_signal(candidate) is True
    relevance = _persona_relevance(candidate, pos_terms=["motorsport"], neg_terms=[])
    assert relevance["relevance_hits"] == ["motorsport"]
    assert relevance["relevance_tier"] == "中"


@pytest.mark.parametrize("subtitle_field", ["subtitle", "subtitles"])
def test_subtitle_evidence_survives_session_attach_as_coordinates_only(subtitle_field: str) -> None:
    match_evidence = build_match_evidence(
        {},
        {
            "representative_evidence": [
                {subtitle_field: [{"text": "Motorsport production with an on-camera rig"}]}
            ]
        },
        "motorsport creator",
        min_intent_terms=1,
    )

    expected = {
        "field": f"representative_evidence.{subtitle_field}",
        "term": "motorsport",
        "source": "server_profile_evidence",
    }
    assert expected in match_evidence
    assert _safe_match_evidence(match_evidence, allowed_terms={"motorsport"}) == [expected]
    assert "production" not in _safe_match_evidence(
        match_evidence,
        allowed_terms={"motorsport"},
    )[0]
