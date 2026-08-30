from __future__ import annotations

from app.domains.kol import product_fit


def test_penalty_evidence_keeps_stable_business_order() -> None:
    evidence: list[dict[str, object]] = []
    product_fit._append_penalty_fit_evidence(
        {
            "sync_status": "needs_human_review",
            "review_state": "ready",
            "decision": "escalate",
            "risk_count": 1,
            "facts": [
                {
                    "fact_type": "risk_flag",
                    "fact_value": "brand_safety",
                    "source_table": "vkpi_memory_facts",
                    "source_id": 9,
                }
            ],
        },
        evidence,
    )

    assert [item["type"] for item in evidence] == [
        "sync_needs_review",
        "resolution_escalate",
        "risk_flag",
    ]
    assert evidence[-1]["severity"] == "high"


def test_candidate_collection_keeps_hard_and_low_evidence_counters(monkeypatch) -> None:
    families = [{"id": 1}, {"id": 2}, {"id": 3}]
    monkeypatch.setattr(product_fit, "_candidate_product_families", lambda: families)

    def candidate(family, _context, *, include_low_evidence):
        assert include_low_evidence is False
        if family["id"] == 2:
            return None, True
        return {"product_family_uid": f"family-{family['id']}"}, False

    monkeypatch.setattr(product_fit, "_product_fit_candidate", candidate)
    eligible, hard_excluded, low_evidence, dimensions11_matched = (
        product_fit._collect_product_fit_candidates(
            {"member_counts": {1: 0, 2: 1, 3: 1}},
            include_low_evidence=False,
        )
    )

    assert eligible == [{"product_family_uid": "family-3"}]
    assert hard_excluded == 1
    assert low_evidence == 1
    assert dimensions11_matched == 1


def test_ranking_preserves_tie_order_and_applies_limit() -> None:
    rows = [
        {
            "product_family_uid": "first",
            "score": 80,
            "score_breakdown": {
                "historical_fit": 10,
                "adjacent_product_fit": 5,
                "market_activity": 2,
            },
        },
        {
            "product_family_uid": "second",
            "score": 80,
            "score_breakdown": {
                "historical_fit": 10,
                "adjacent_product_fit": 5,
                "market_activity": 2,
            },
        },
        {
            "product_family_uid": "lower",
            "score": 40,
            "score_breakdown": {
                "historical_fit": 0,
                "adjacent_product_fit": 0,
                "market_activity": 0,
            },
        },
    ]

    returned, median, markdown = product_fit._rank_product_fit_candidates(
        rows,
        safe_limit=2,
    )

    assert [item["product_family_uid"] for item in returned] == ["first", "second"]
    assert [item["rank"] for item in returned] == [1, 2]
    assert median == 80.0
    assert markdown == returned
