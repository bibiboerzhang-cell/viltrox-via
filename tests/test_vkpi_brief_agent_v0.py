from __future__ import annotations

import json

from app.services.vkpi import brief_agent_v0


def _candidate(kol_pool_id: int = 101, *, refs: int = 4, decision: str = "contact_candidate") -> dict:
    evidence_refs = [
        {
            "section": "product_fit",
            "evidence_id": f"pf:{idx}",
            "source": "official_catalog",
            "source_table": "vkpi_products",
            "source_id": f"SKU-{idx}",
            "title": f"Product evidence {idx}",
            "confidence": 0.8,
        }
        for idx in range(refs)
    ]
    return {
        "kol_pool_id": kol_pool_id,
        "candidate_uid": f"p7-82:{kol_pool_id}:contact_kol_for_sku",
        "status": "candidate",
        "suggested_decision": decision,
        "recommendation_type": "human_review_candidate",
        "score": 82.5,
        "confidence": "high",
        "item": {
            "id": kol_pool_id,
            "platform": "youtube",
            "handle": "creator",
            "display_name": "Creator",
            "profile_url": "https://youtube.com/@creator",
        },
        "target": {
            "source": "p6_77_weekly_action_plan",
            "action_type": "contact_kol_for_sku",
            "priority": "high",
            "score": 72,
            "title": "Review creator",
            "reason": "qualified SKU candidate",
        },
        "evidence_quality": {
            "score": 25,
            "ready_sections": 5,
            "partial_sections": 0,
            "missing_count": 1,
            "missing_sections": [{"section": "comment_intelligence", "reason": "empty"}],
            "evidence_ref_count": len(evidence_refs),
            "claim_count": 3,
        },
        "competitor_context": {"risk_tier": "opportunity", "risk_score": 0.0, "brand": "", "source": "test"},
        "feedback_context": {"counts": {}, "score_adjustment": 0.0, "sentiment": "none", "source": "no_feedback"},
        "claims": [{"section": "product_fit", "claim_text": "Product fit is ready.", "new_fact_generated": False}],
        "evidence_refs": evidence_refs,
        "evidence_ref_count": len(evidence_refs),
        "generated_facts": False,
        "human_confirmation_required": True,
        "rank": 1,
    }


def _recommendation_report(candidates: list[dict]) -> dict:
    return {
        "mode": "p7_82_recommendation_agent_v0",
        "passed": True,
        "provider_calls": False,
        "llm_calls": False,
        "write_db": False,
        "sync_triggered": False,
        "task_enqueued": False,
        "checks": {"evidence_source_loaded": True},
        "summary": {"agent_status": "ready", "candidate_count": len(candidates)},
        "candidates": candidates,
        "blocked_candidates": [],
    }


def test_brief_agent_builds_traceable_items_from_explicit_ids(monkeypatch) -> None:
    monkeypatch.setattr(
        brief_agent_v0.recommendation_agent_v0,
        "build_recommendation_agent_v0",
        lambda **_kwargs: _recommendation_report([_candidate(101)]),
    )

    report = brief_agent_v0.build_brief_agent_v0(kol_pool_ids="101", min_evidence_refs=2)

    assert report["passed"] is True
    assert report["summary"]["agent_status"] == "ready"
    assert report["summary"]["candidate_count"] == 1
    assert report["summary"]["brief_item_count"] >= 2
    assert report["summary"]["next_action_count"] == 1
    assert report["provider_calls"] is False
    assert report["llm_calls"] is False
    assert report["write_db"] is False
    assert report["notification_written"] is False
    assert report["task_enqueued"] is False
    assert report["policy"]["new_fact_generation"] is False
    assert all(item["evidence_refs"] for item in report["brief_items"])
    assert all(action["evidence_refs"] for action in report["next_actions"])


def test_brief_agent_reads_latest_recommendation_artifact(tmp_path) -> None:
    artifact = _recommendation_report([_candidate(201)])
    (tmp_path / "latest-p7-82-recommendation-agent-v0.json").write_text(json.dumps(artifact), encoding="utf-8")

    report = brief_agent_v0.build_brief_agent_v0(ops_dir=str(tmp_path), min_evidence_refs=2)

    assert report["passed"] is True
    assert report["summary"]["recommendation_source"] == "latest_p7_82_recommendation_agent_artifact"
    assert report["recommendation_source"]["artifact"]["artifact_name"] == "latest-p7-82-recommendation-agent-v0.json"
    assert report["brief_items"][0]["identity"]["kol_pool_id"] == 201


def test_brief_agent_fails_when_source_missing(tmp_path) -> None:
    report = brief_agent_v0.build_brief_agent_v0(ops_dir=str(tmp_path))

    assert report["passed"] is False
    assert report["summary"]["agent_status"] == "source_missing"
    assert report["checks"]["recommendation_source_loaded"] is False
    assert report["provider_calls"] is False
    assert report["sync_triggered"] is False


def test_brief_agent_drops_untraceable_candidate_items(tmp_path) -> None:
    artifact = _recommendation_report([_candidate(301, refs=0)])
    (tmp_path / "latest-p7-82-recommendation-agent-v0.json").write_text(json.dumps(artifact), encoding="utf-8")

    report = brief_agent_v0.build_brief_agent_v0(ops_dir=str(tmp_path), min_evidence_refs=2)

    assert report["passed"] is True
    assert report["summary"]["dropped_untraceable_count"] == 1
    assert report["summary"]["brief_item_count"] == 0
    assert report["checks"]["brief_items_traceable_or_empty"] is True
