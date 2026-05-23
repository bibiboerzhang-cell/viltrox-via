from __future__ import annotations

from scripts import vkpi_video_analysis_evidence_acceptance


def _fake_card(video_status: str = "ready") -> dict:
    evidence = []
    field_counts = {}
    if video_status == "ready":
        evidence = [
            {
                "evidence_id": "ev_video_analysis_submission_88",
                "source": "video_analysis",
                "source_table": "submissions",
                "source_id": 88,
                "source_url": "https://youtube.com/watch?v=video1",
                "fields": {"target_audience": "camera creators", "production_quality": "studio"},
            }
        ]
        field_counts = {"target_audience": 1, "production_quality": 1}
    return {
        "mode": "read_only_kol_intelligence_card_v0",
        "provider_calls": False,
        "llm_calls": False,
        "write_db": False,
        "video_analysis": {
            "status": video_status,
            "source": "submissions.video_analysis",
            "row_count": 1 if video_status == "ready" else 0,
            "analyzed_count": 1 if video_status == "ready" else 0,
            "evidence_count": len(evidence),
            "field_counts": field_counts,
            "evidence": evidence,
            "provider_calls": False,
            "llm_calls": False,
            "write_db": False,
            "empty_reason": "" if video_status == "ready" else "no_stored_analyzed_video_rows",
        },
    }


def test_video_analysis_evidence_acceptance_requires_traceable_stored_rows(monkeypatch) -> None:
    monkeypatch.setattr(
        vkpi_video_analysis_evidence_acceptance.kol_intelligence_card,
        "build_kol_pool_intelligence_card",
        lambda *_args, **_kwargs: _fake_card("ready"),
    )

    report = vkpi_video_analysis_evidence_acceptance.build_report(kol_pool_id=321)

    assert report["passed"] is True
    assert report["checks"]["traceable_when_present"] is True
    assert report["checks"]["no_provider_calls"] is True
    assert report["summary"]["status"] == "ready"
    assert report["summary"]["field_names"] == ["production_quality", "target_audience"]
    assert "P4.59 Video Analysis Evidence Acceptance" in vkpi_video_analysis_evidence_acceptance.render_markdown(report)


def test_video_analysis_evidence_acceptance_allows_honest_empty_state(monkeypatch) -> None:
    monkeypatch.setattr(
        vkpi_video_analysis_evidence_acceptance.kol_intelligence_card,
        "build_kol_pool_intelligence_card",
        lambda *_args, **_kwargs: _fake_card("empty"),
    )

    report = vkpi_video_analysis_evidence_acceptance.build_report(kol_pool_id=321)

    assert report["passed"] is True
    assert report["summary"]["status"] == "empty"
    assert report["checks"]["empty_does_not_fake_fields"] is True


def test_video_analysis_evidence_acceptance_fails_untraceable_rows(monkeypatch) -> None:
    card = _fake_card("ready")
    card["video_analysis"]["evidence"][0]["source_table"] = "gemini_plan"
    monkeypatch.setattr(
        vkpi_video_analysis_evidence_acceptance.kol_intelligence_card,
        "build_kol_pool_intelligence_card",
        lambda *_args, **_kwargs: card,
    )

    report = vkpi_video_analysis_evidence_acceptance.build_report(kol_pool_id=321)

    assert report["passed"] is False
    assert report["checks"]["traceable_when_present"] is False
