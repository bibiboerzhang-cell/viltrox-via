from __future__ import annotations

import json

from app.domains.intelligence import recommendation_use_case as recommendation_agent_v0


def _chain(kol_pool_id: int = 101, *, refs: int = 4) -> dict:
    evidence_refs = [
        {
            "section": "dimensions11",
            "evidence_id": f"dimensions11:{idx}",
            "source": "dimensions11_rule_blocks",
            "source_table": "vkpi_kol_pool",
            "source_id": kol_pool_id,
            "title": f"Evidence {idx}",
            "confidence": 0.8,
        }
        for idx in range(refs)
    ]
    return {
        "kol_pool_id": kol_pool_id,
        "status": "ready",
        "target": {
            "source": "p6_77_weekly_action_plan",
            "action_type": "contact_kol_for_sku",
            "priority": "high",
            "score": 62,
            "title": "Review creator",
            "reason": "weekly plan candidate",
            "entity": {"kol_pool_id": kol_pool_id, "platform": "youtube", "handle": "creator"},
        },
        "item": {
            "id": kol_pool_id,
            "platform": "youtube",
            "handle": "creator",
            "display_name": "Creator",
            "profile_url": "https://youtube.com/@creator",
        },
        "evidence_ref_count": len(evidence_refs),
        "sections": [
            {"section": "dimensions11", "status": "ready", "evidence_ref_count": 2, "confidence": 0.8},
            {"section": "product_fit", "status": "ready", "evidence_ref_count": 2, "confidence": 0.75},
        ],
        "missing_sections": [{"section": "brand_signal", "reason": "empty"}],
        "claims": [
            {
                "section": "dimensions11",
                "claim_text": "11D profile is ready.",
                "evidence_ref_count": 2,
                "new_fact_generated": False,
            }
        ],
        "evidence_refs": evidence_refs,
        "provider_calls": False,
        "llm_calls": False,
        "write_db": False,
    }


def _evidence_report(chains: list[dict]) -> dict:
    return {
        "mode": "p7_81_evidence_agent_v0",
        "passed": True,
        "provider_calls": False,
        "llm_calls": False,
        "write_db": False,
        "sync_triggered": False,
        "task_enqueued": False,
        "checks": {"target_source_loaded": True},
        "summary": {"agent_status": "ready", "chain_count": len(chains)},
        "chains": chains,
    }


def test_recommendation_agent_generates_traceable_explicit_candidates(monkeypatch) -> None:
    monkeypatch.setattr(
        recommendation_agent_v0.evidence_agent_v0,
        "build_evidence_agent_v0",
        lambda **_kwargs: _evidence_report([_chain(101)]),
    )
    monkeypatch.setattr(
        recommendation_agent_v0,
        "_feedback_context",
        lambda *_args, **_kwargs: {
            "counts": {"shortlist": 1},
            "score_adjustment": 8.0,
            "sentiment": "positive",
            "source": "vkpi_recommendation_feedback",
        },
    )
    monkeypatch.setattr(
        recommendation_agent_v0,
        "_competitor_context",
        lambda *_args, **_kwargs: {"risk_tier": "opportunity", "risk_score": 0.0, "brand": "", "source": "no_persisted_relation"},
    )

    report = recommendation_agent_v0.build_recommendation_agent_v0(kol_pool_ids="101", min_evidence_refs=2)

    assert report["passed"] is True
    assert report["summary"]["agent_status"] == "ready"
    assert report["summary"]["candidate_count"] == 1
    assert report["summary"]["feedback_context_count"] == 1
    assert report["provider_calls"] is False
    assert report["llm_calls"] is False
    assert report["write_db"] is False
    assert report["recommendation_rows_written"] is False
    candidate = report["candidates"][0]
    assert candidate["suggested_decision"] in {"contact_candidate", "watch_candidate"}
    assert candidate["human_confirmation_required"] is True
    assert candidate["generated_facts"] is False
    assert candidate["evidence_refs"]


def test_recommendation_agent_reads_latest_evidence_artifact(tmp_path, monkeypatch) -> None:
    artifact = _evidence_report([_chain(201)])
    (tmp_path / "latest-p7-81-evidence-agent-v0.json").write_text(json.dumps(artifact), encoding="utf-8")
    monkeypatch.setattr(recommendation_agent_v0, "_feedback_context", lambda *_args, **_kwargs: {"counts": {}, "score_adjustment": 0.0, "sentiment": "none", "source": "no_feedback"})
    monkeypatch.setattr(recommendation_agent_v0, "_competitor_context", lambda *_args, **_kwargs: {"risk_tier": "safe", "risk_score": 0.0, "brand": "", "source": "test"})

    report = recommendation_agent_v0.build_recommendation_agent_v0(ops_dir=str(tmp_path), min_evidence_refs=2)

    assert report["passed"] is True
    assert report["summary"]["evidence_source"] == "latest_p7_81_evidence_agent_artifact"
    assert report["evidence_source"]["artifact"]["artifact_name"] == "latest-p7-81-evidence-agent-v0.json"
    assert report["candidates"][0]["kol_pool_id"] == 201


def test_recommendation_agent_blocks_untraceable_chains(tmp_path, monkeypatch) -> None:
    artifact = _evidence_report([_chain(301, refs=0)])
    (tmp_path / "latest-p7-81-evidence-agent-v0.json").write_text(json.dumps(artifact), encoding="utf-8")
    monkeypatch.setattr(recommendation_agent_v0, "_feedback_context", lambda *_args, **_kwargs: {})
    monkeypatch.setattr(recommendation_agent_v0, "_competitor_context", lambda *_args, **_kwargs: {})

    report = recommendation_agent_v0.build_recommendation_agent_v0(ops_dir=str(tmp_path), min_evidence_refs=2)

    assert report["passed"] is True
    assert report["summary"]["agent_status"] == "blocked_no_traceable_candidates"
    assert report["summary"]["candidate_count"] == 0
    assert report["blocked_candidates"][0]["reason"] == "insufficient_traceable_evidence"
    assert report["checks"]["candidates_traceable_or_blocked"] is True


def test_recommendation_agent_fails_when_evidence_source_missing(tmp_path) -> None:
    report = recommendation_agent_v0.build_recommendation_agent_v0(ops_dir=str(tmp_path))

    assert report["passed"] is False
    assert report["summary"]["agent_status"] == "source_missing"
    assert report["checks"]["evidence_source_loaded"] is False
    assert report["provider_calls"] is False
    assert report["sync_triggered"] is False
