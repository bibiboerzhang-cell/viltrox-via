from __future__ import annotations

import app.domains.intelligence.ai_brief as ai_brief
from scripts import vkpi_ai_brief_acceptance


def _summary(refs: bool = True) -> dict:
    product_refs = [
        {
            "section": "product_fit",
            "source": "official_catalog",
            "source_table": "vkpi_products",
            "source_id": "AF-35MM",
            "evidence_id": "pf_1",
            "title": "AF 35mm",
            "confidence": 0.85,
        }
    ] if refs else []
    competitor_refs = [
        {
            "section": "competitors",
            "source": "competitor_signal",
            "source_table": "vkpi_competitor_relation",
            "source_id": "sigma",
            "evidence_id": "comp_1",
            "title": "Sigma comparison",
            "confidence": 0.8,
        }
    ] if refs else []
    return {
        "mode": "read_only_kol_evidence_summary_v0",
        "passed": True,
        "provider_calls": False,
        "llm_calls": False,
        "write_db": False,
        "policy": {"existing_evidence_only": True, "new_fact_generation": False},
        "item": {"id": 321, "platform": "youtube", "handle": "creatorone"},
        "decision_support": {"readiness": "ready", "ready_sections": 8, "total_sections": 8, "gaps": []},
        "summary_count": 2,
        "evidence_ref_count": len(product_refs) + len(competitor_refs),
        "checks": {"all_summaries_traceable": refs},
        "summaries": [
            {
                "summary_uid": "evsum_product_fit",
                "section": "product_fit",
                "label": "Product Fit",
                "status": "ready",
                "summary_text": "Product fit is ready; official_catalog=1; discovery=0.",
                "evidence_count": 1,
                "confidence": 0.85,
                "evidence_refs": product_refs,
            },
            {
                "summary_uid": "evsum_competitors",
                "section": "competitors",
                "label": "Competitors",
                "status": "ready",
                "summary_text": "Competitor relation is ready; top_brand=sigma; risk_tier=caution.",
                "evidence_count": 1,
                "confidence": 0.8,
                "evidence_refs": competitor_refs,
            },
        ],
    }


def test_ai_brief_uses_existing_evidence_only(monkeypatch) -> None:
    monkeypatch.setattr(
        ai_brief.evidence_summary,
        "build_kol_pool_evidence_summary",
        lambda *_args, **_kwargs: _summary(refs=True),
    )

    payload = ai_brief.build_kol_pool_ai_brief(321)

    assert payload["passed"] is True
    assert payload["provider_calls"] is False
    assert payload["llm_calls"] is False
    assert payload["write_db"] is False
    assert payload["policy"]["new_fact_generation"] is False
    assert payload["brief_item_count"] == 2
    assert payload["next_action_count"] == 2
    assert payload["checks"]["all_brief_items_traceable"] is True
    assert payload["checks"]["all_next_actions_traceable"] is True
    assert payload["checks"]["no_unsupported_recommendations"] is True
    for item in payload["brief_items"]:
        assert item["generation"]["method"] == "deterministic_evidence_brief_v0"
        assert item["evidence_refs"]
    for action in payload["next_actions"]:
        assert action["evidence_refs"]


def test_ai_brief_drops_untraceable_summaries(monkeypatch) -> None:
    monkeypatch.setattr(
        ai_brief.evidence_summary,
        "build_kol_pool_evidence_summary",
        lambda *_args, **_kwargs: _summary(refs=False),
    )

    payload = ai_brief.build_kol_pool_ai_brief(321)

    assert payload["passed"] is False
    assert payload["brief_item_count"] == 0
    assert payload["dropped_untraceable_summary_count"] == 2
    assert payload["checks"]["all_brief_items_traceable"] is False
    assert payload["next_action_count"] == 0


def test_ai_brief_acceptance_report_requires_traceability(monkeypatch) -> None:
    monkeypatch.setattr(
        vkpi_ai_brief_acceptance.natural_search,
        "search",
        lambda *_args, **_kwargs: {
            "total": 1,
            "items": [{"source_table": "vkpi_kol_pool", "source_id": 321, "title": "Creator One"}],
        },
    )
    monkeypatch.setattr(
        vkpi_ai_brief_acceptance.ai_brief,
        "build_kol_pool_ai_brief",
        lambda *_args, **_kwargs: {
            "passed": True,
            "provider_calls": False,
            "llm_calls": False,
            "write_db": False,
            "policy": {"new_fact_generation": False, "recommendations_require_evidence": True},
            "headline": "AI Brief v0 is anchored on product_fit evidence; readiness=ready.",
            "brief_item_count": 1,
            "next_action_count": 1,
            "evidence_backlink_count": 1,
            "dropped_untraceable_summary_count": 0,
            "brief_items": [{"section": "product_fit", "evidence_refs": [{"evidence_id": "pf_1"}]}],
            "next_actions": [{"section": "product_fit", "evidence_refs": [{"evidence_id": "pf_1"}]}],
        },
    )

    report = vkpi_ai_brief_acceptance.build_report(query="viltrox")

    assert report["passed"] is True
    assert report["checks"]["all_brief_items_traceable"] is True
    assert report["checks"]["all_next_actions_traceable"] is True
    assert report["checks"]["recommendations_require_evidence"] is True
    markdown = vkpi_ai_brief_acceptance.render_markdown(report)
    assert "V-KPI P4.60 AI Brief Acceptance" in markdown
