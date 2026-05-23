from __future__ import annotations

from app.services.vkpi import evidence_summary
from scripts import vkpi_evidence_summary_acceptance


def _fake_card() -> dict:
    return {
        "mode": "read_only_kol_intelligence_card_v0",
        "generated_at": "2026-05-23T00:00:00Z",
        "provider_calls": False,
        "llm_calls": False,
        "write_db": False,
        "kol_pool_id": 321,
        "item": {"id": 321, "platform": "youtube", "handle": "creatorone", "display_name": "Creator One"},
        "freshness": {"tier": "hot", "tier_reason": "brand_signal_60d", "reason": "fresh", "last_refresh_at": "2026-05-23T00:00:00Z"},
        "dimensions11": {
            "status": "ready",
            "overall_score": 76,
            "confidence": {"block1_content": 0.8, "block2_performance": 0.7},
            "blocks": {"block1_content": {"topic": "lens review"}, "block2_performance": {"followers": 1200}},
        },
        "competitors": {
            "status": "ready",
            "summary": {"competitor_brand": "sigma", "risk_tier": "caution"},
            "evidence": [
                {
                    "evidence_id": "ev_comp_1",
                    "source_table": "vkpi_competitor_relation",
                    "source_id": "sigma",
                    "title": "Sigma comparison",
                    "confidence": 0.8,
                }
            ],
        },
        "brand_signal": {
            "status": "ready",
            "signal_count": 2,
            "cached_post_count": 3,
            "signals": [
                {
                    "signal_uid": "sig_1",
                    "source_table": "vkpi_kol_pool",
                    "source_id": 321,
                    "detail": "Viltrox mention",
                }
            ],
        },
        "comment_intelligence": {
            "status": "ready",
            "cached_comment_count": 4,
            "contract": {"declared": 20, "cached": 4, "cap": 12, "status": "cached_window"},
            "evidence": [
                {
                    "evidence_id": "ci_1",
                    "source_table": "vkpi_comment_intelligence_runs",
                    "source_id": 7,
                    "summary": "Question about mount compatibility",
                }
            ],
        },
        "video_analysis": {
            "status": "ready",
            "row_count": 1,
            "analyzed_count": 1,
            "evidence_count": 1,
            "field_counts": {"target_audience": 1, "production_quality": 1},
            "evidence": [
                {
                    "evidence_id": "va_1",
                    "source": "video_analysis",
                    "source_table": "submissions",
                    "source_id": 88,
                    "source_url": "https://youtube.com/watch?v=video1",
                    "title": "Viltrox video review",
                    "confidence": 0.82,
                    "fields": {"target_audience": "camera creators", "production_quality": "studio"},
                }
            ],
        },
        "memory_card": {
            "status": "ready",
            "history_match": {"cooperation_count": 1, "source_table": "vkpi_kol_pool", "source_id": 321},
            "competitor_memory": {"risk_tier": "opportunity"},
            "recent_posts": [{"id": "post_1", "title": "Viltrox 35mm review", "source_table": "vkpi_kol_pool"}],
        },
        "product_fit": {
            "status": "ready",
            "count": 2,
            "official_catalog_count": 1,
            "discovery_count": 1,
            "official_catalog": [{"sku": "AF-35MM", "title": "AF 35mm", "source_table": "vkpi_product_cost_catalog"}],
            "rule_evidence": [{"evidence_id": "pf_1", "detail": "11D fit", "source_table": "vkpi_kol_profile_deep"}],
        },
        "decision_support": {"readiness": "ready", "ready_sections": 7, "total_sections": 7, "gaps": []},
        "evidence_index": [
            {"section": "freshness", "label": "Freshness", "source": "vkpi_kol_refresh_tier", "status": "ready", "evidence_count": 1, "confidence": 1.0},
            {"section": "dimensions11", "label": "11D", "source": "vkpi_kol_pool", "status": "ready", "evidence_count": 2, "confidence": 0.7},
            {"section": "competitors", "label": "Competitors", "source": "vkpi_competitor_relation", "status": "ready", "evidence_count": 1, "confidence": 0.8},
            {"section": "brand_signal", "label": "Brand Signal", "source": "vkpi_kol_pool.raw_platform_data", "status": "ready", "evidence_count": 1, "confidence": 1.0},
            {"section": "comment_intelligence", "label": "Comment Intelligence", "source": "vkpi_comments", "status": "ready", "evidence_count": 1, "confidence": 1.0},
            {"section": "video_analysis", "label": "Video Analysis", "source": "submissions.video_analysis", "status": "ready", "evidence_count": 1, "confidence": 0.82},
            {"section": "memory_card", "label": "Memory Card", "source": "vkpi_kol_pool", "status": "ready", "evidence_count": 2, "confidence": 1.0},
            {"section": "product_fit", "label": "Product Fit", "source": "vkpi_memory_entities", "status": "ready", "evidence_count": 2, "confidence": 0.85},
        ],
    }


def test_evidence_summary_is_traceable_and_read_only(monkeypatch) -> None:
    monkeypatch.setattr(evidence_summary.kol_intelligence_card, "build_kol_pool_intelligence_card", lambda *_args, **_kwargs: _fake_card())
    monkeypatch.setattr(
        evidence_summary.llm_gateway,
        "budget_preflight",
        lambda *_args, **_kwargs: {"mode": "llm_gateway_budget_preflight_v0", "provider_calls_allowed": False, "provider_gate_reason": "force_offline"},
    )

    payload = evidence_summary.build_kol_pool_evidence_summary(321)

    assert payload["passed"] is True
    assert payload["provider_calls"] is False
    assert payload["llm_calls"] is False
    assert payload["write_db"] is False
    assert payload["policy"]["new_fact_generation"] is False
    assert payload["summary_count"] == 8
    assert payload["evidence_ref_count"] >= 7
    assert payload["checks"]["all_summaries_traceable"] is True
    for item in payload["summaries"]:
        assert item["generation"]["method"] == "deterministic_extract_v0"
        assert item["generation"]["provider_calls"] is False
        assert item["generation"]["llm_calls"] is False
        assert item["evidence_refs"]


def test_evidence_summary_acceptance_report_uses_existing_evidence(monkeypatch) -> None:
    monkeypatch.setattr(
        vkpi_evidence_summary_acceptance.natural_search,
        "search",
        lambda *_args, **_kwargs: {
            "provider_calls": False,
            "write_db": False,
            "total": 1,
            "items": [{"source_table": "vkpi_kol_pool", "source_id": 321, "title": "Creator One"}],
        },
    )
    monkeypatch.setattr(
        vkpi_evidence_summary_acceptance.evidence_summary,
        "build_kol_pool_evidence_summary",
        lambda *_args, **_kwargs: {
            "passed": True,
            "provider_calls": False,
            "llm_calls": False,
            "write_db": False,
            "policy": {"new_fact_generation": False},
            "summary_count": 7,
            "evidence_ref_count": 12,
            "summaries": [
                {"section": "freshness", "evidence_refs": [{"evidence_id": "section:freshness"}]},
                {"section": "dimensions11", "evidence_refs": [{"evidence_id": "dimensions11:block1"}]},
                {"section": "competitors", "evidence_refs": [{"evidence_id": "ev_comp_1"}]},
                {"section": "brand_signal", "evidence_refs": [{"evidence_id": "sig_1"}]},
            ],
            "llm_budget_preflight": {"provider_gate_reason": "force_offline"},
        },
    )

    report = vkpi_evidence_summary_acceptance.build_report(query="viltrox")

    assert report["passed"] is True
    assert report["checks"]["all_summaries_traceable"] is True
    assert report["checks"]["no_provider_calls"] is True
    assert report["checks"]["no_llm_calls"] is True
    assert report["checks"]["new_fact_generation_disabled"] is True
    markdown = vkpi_evidence_summary_acceptance.render_markdown(report)
    assert "V-KPI P4.54 Evidence Summary Acceptance" in markdown
    assert "Evidence refs" in markdown
