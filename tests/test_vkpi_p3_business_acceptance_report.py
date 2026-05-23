from __future__ import annotations

from scripts import vkpi_p3_business_acceptance_report


def _search_payload(*, provider_calls: bool = False) -> dict:
    return {
        "query": "viltrox",
        "provider_calls": provider_calls,
        "write_db": False,
        "total": 2,
        "items": [
            {
                "result_type": "kol_pool",
                "source_table": "vkpi_kol_pool",
                "source_id": 321,
                "title": "Creator One",
                "platform": "youtube",
                "handle": "creatorone",
                "score": 42,
            },
            {
                "result_type": "competitor_signal",
                "source_table": "vkpi_competitor_signals",
                "source_id": 99,
                "title": "Sigma signal",
                "score": 12,
            },
        ],
    }


def _card_payload(*, provider_calls: bool = False) -> dict:
    return {
        "mode": "read_only_kol_intelligence_card_v0",
        "provider_calls": provider_calls,
        "llm_calls": False,
        "write_db": False,
        "kol_pool_id": 321,
        "item": {
            "display_name": "Creator One",
            "platform": "youtube",
            "handle": "creatorone",
        },
        "decision_support": {
            "readiness": "partial",
            "ready_sections": 6,
            "total_sections": 7,
            "gaps": ["product_fit_empty"],
        },
        "evidence_index": [
            {"section": "dimensions11", "status": "ready", "evidence_count": 4},
            {"section": "competitors", "status": "empty", "evidence_count": 0},
            {"section": "comment_intelligence", "status": "ready", "evidence_count": 3},
        ],
        "dimensions11": {"status": "ready"},
        "competitors": {"status": "empty"},
        "brand_signal": {"status": "ready"},
        "comment_intelligence": {"status": "ready"},
        "memory_card": {"status": "ready"},
        "product_fit": {"status": "empty"},
    }


def _decision_summary(*, schema_ready: bool = True) -> dict:
    return {
        "status": "ready" if schema_ready else "missing_schema",
        "write_db": False,
        "decision_options": {
            "contact": "可联系",
            "watch": "可观察",
            "caution": "谨慎",
            "avoid": "避开",
        },
        "followup_outcomes": {
            "effective": "判断有效",
            "ineffective": "判断无效",
            "unclear": "结果不明确",
            "snooze": "延后回访",
        },
        "decision_schema_ready": schema_ready,
        "followup_schema_ready": schema_ready,
        "decisions_total": 4,
        "candidate_decisions": 1,
        "followups_total": 2,
        "recent_decisions": [],
    }


def _feedback_summary(*, schema_ready: bool = True) -> dict:
    return {
        "status": "ready" if schema_ready else "missing_schema",
        "provider_calls": False,
        "write_db": False,
        "schema_ready": schema_ready,
        "tables": {
            "runs": schema_ready,
            "recommendations": schema_ready,
            "feedback": schema_ready,
            "outcomes": schema_ready,
        },
        "summary": {
            "recommendation_rows": 12,
            "missing_feedback_rows": 8,
            "with_feedback_rows": 4,
            "run_count": 2,
        },
        "runs": [
            {
                "run_uid": "run_1",
                "strategy_version": "recommendation_v1",
                "status": "completed",
                "recommendation_rows": 12,
                "feedback_rows": 4,
                "missing_feedback_rows": 8,
            }
        ],
        "csv_fields": [f"field_{idx}" for idx in range(24)],
    }


def test_p3_business_acceptance_report_passes_without_side_effects(monkeypatch) -> None:
    monkeypatch.setattr(vkpi_p3_business_acceptance_report.natural_search, "search", lambda *_args, **_kwargs: _search_payload())
    monkeypatch.setattr(
        vkpi_p3_business_acceptance_report.kol_intelligence_card,
        "build_kol_pool_intelligence_card",
        lambda *_args, **_kwargs: _card_payload(),
    )
    monkeypatch.setattr(vkpi_p3_business_acceptance_report, "_decision_audit_summary", lambda *_args, **_kwargs: _decision_summary())
    monkeypatch.setattr(vkpi_p3_business_acceptance_report, "_recommendation_feedback_summary", lambda **_kwargs: _feedback_summary())

    report = vkpi_p3_business_acceptance_report.build_report(query="viltrox", limit=20)

    assert report["passed"] is True
    assert report["provider_calls"] is False
    assert report["llm_calls"] is False
    assert report["write_db"] is False
    assert report["sync_triggered"] is False
    assert report["task_enqueued"] is False
    assert report["kol_pool_id"] == 321
    assert report["checks"]["search_has_kol_candidate"] is True
    assert report["checks"]["intelligence_decision_ready"] is True
    markdown = vkpi_p3_business_acceptance_report.render_markdown(report)
    assert "V-KPI P3 Business Acceptance Report" in markdown
    assert "missing_feedback=8" in markdown


def test_p3_business_acceptance_report_fails_closed_on_provider_or_missing_schema(monkeypatch) -> None:
    monkeypatch.setattr(
        vkpi_p3_business_acceptance_report.natural_search,
        "search",
        lambda *_args, **_kwargs: _search_payload(provider_calls=True),
    )
    monkeypatch.setattr(
        vkpi_p3_business_acceptance_report.kol_intelligence_card,
        "build_kol_pool_intelligence_card",
        lambda *_args, **_kwargs: _card_payload(provider_calls=True),
    )
    monkeypatch.setattr(
        vkpi_p3_business_acceptance_report,
        "_decision_audit_summary",
        lambda *_args, **_kwargs: _decision_summary(schema_ready=False),
    )
    monkeypatch.setattr(vkpi_p3_business_acceptance_report, "_recommendation_feedback_summary", lambda **_kwargs: _feedback_summary(schema_ready=False))

    report = vkpi_p3_business_acceptance_report.build_report(query="viltrox", limit=20)

    assert report["passed"] is False
    assert report["checks"]["search_no_provider_or_write"] is False
    assert report["checks"]["intelligence_no_provider_llm_or_write"] is False
    assert report["checks"]["decision_audit_schema_ready"] is False
    assert report["checks"]["recommendation_feedback_schema_ready"] is False
