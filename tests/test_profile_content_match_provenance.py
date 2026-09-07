"""Public-work evidence stays separate, bounded and traceable without IO."""
from copy import deepcopy

import pytest

from app.domains.kol import profile_online_evidence as online
from app.domains.kol.profile_content_match_provenance import (
    attach_content_match_coordinates, content_coordinates, project_match_coordinates,
)
from app.domains.kol.profile_recall_match_evidence import build_match_evidence
from app.domains.kol.profile_recall_qualification_projection import _project_match_evidence


def _work(index, title, **extra):
    return {"platform": "youtube", "content_url": f"https://www.youtube.com/watch?v=fixture{index}",
            "title": title, "posted_at": "2026-09-01T00:00:00Z",
            "fetched_at": "2026-09-06T00:00:00Z", "source": "platform_video_api", **extra}


def test_separate_works_are_not_joined_into_latest_video_title_or_transcript():
    raw = {"platform": "youtube", "video_evidence": [
        _work(1, "Portrait lighting", transcript="studio subject"),
        _work(2, "Motorsport photography", transcript="racing at night"),
    ]}
    before = deepcopy(raw)
    records, status = online._representative_content_evidence(raw, latest={})
    assert len(records) == 2
    assert records[0]["title"] == "Portrait lighting"
    assert records[1]["title"] == "Motorsport photography"
    assert "racing" not in records[0]["transcript"]
    assert status["content_record_count"] == 2
    assert raw == before
    matches = build_match_evidence({}, {"representative_evidence": records}, "motorsport", min_intent_terms=1)
    proof = attach_content_match_coordinates(matches, records)
    assert proof[0]["content_url"].endswith("v=fixture2")
    assert proof[0]["content_trace_status"] == "traceable"
    assert "racing at night" not in str(proof)


def test_later_than_fifth_work_can_supply_evidence_with_same_total_text_budget():
    raw = {"video_evidence": [_work(index, "weekly update" if index < 7 else "motorsport photography",
                                    transcript="x" * 7000) for index in range(8)]}
    records, _ = online._representative_content_evidence(raw, latest={})
    assert len(records) == 8
    assert sum(len(row.get("transcript", "")) for row in records) <= 6000
    assert sum(len(row.get("title", "")) for row in records) <= 500
    matches = build_match_evidence({}, {"representative_evidence": records}, "motorsport", min_intent_terms=1)
    assert attach_content_match_coordinates(matches, records)[0]["content_url"].endswith("v=fixture7")


def test_public_projection_retains_safe_coordinates_and_no_provider_body():
    coordinates = content_coordinates(_work(2, "unused"))
    match = {"field": "representative_evidence.title", "term": "motorsport",
             "source": "server_profile_evidence", **coordinates, "transcript": "private body"}
    first = online._project_online_match_evidence([match])
    result = _project_match_evidence(first)[0]
    assert result["content_url"] == coordinates["content_url"]
    assert result["content_observed_at"] == "2026-09-06T00:00:00+00:00"
    assert "transcript" not in result


@pytest.mark.parametrize("url", [
    "https://www.youtube.com/watch?v=fixture&token=secret",
    "https://www.youtube.com.evil.test/watch?v=fixture",
    "https://user:password@www.youtube.com/watch?v=fixture",
    "https://example.test/contact?email=private@example.test",
])
def test_contact_and_credential_routes_never_become_public_coordinates(url):
    result = content_coordinates(_work(1, "unused", content_url=url))
    assert "content_url" not in result
    assert result["content_trace_status"] == "incomplete"


def test_missing_observation_time_is_not_replaced_by_today_or_published_date():
    work = _work(1, "unused")
    work.pop("fetched_at")
    result = content_coordinates(work)
    assert result["content_trace_status"] == "incomplete"
    assert "content_observed_at" not in result


def test_claimed_traceable_status_is_recomputed_and_profile_fields_cannot_copy_urls():
    assert project_match_coordinates({"field": "bio", "content_url": "https://youtu.be/fixture"}) == {}
    result = project_match_coordinates({"field": "representative_evidence.title", "content_trace_status": "traceable"})
    assert result == {"content_trace_status": "incomplete"}
