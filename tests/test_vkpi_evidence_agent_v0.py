from __future__ import annotations

import json

from app.domains.intelligence import evidence_agent_use_case as evidence_agent_v0


def _summary_payload(kol_pool_id: int = 101) -> dict:
    return {
        "passed": True,
        "provider_calls": False,
        "llm_calls": False,
        "write_db": False,
        "policy": {"new_fact_generation": False},
        "item": {
            "id": kol_pool_id,
            "platform": "youtube",
            "handle": "creator",
            "display_name": "Creator",
            "profile_url": "https://youtube.com/@creator",
        },
        "decision_support": {"readiness": "ready"},
        "summary_count": 2,
        "summaries": [
            {
                "section": "dimensions11",
                "label": "11D",
                "status": "ready",
                "evidence_count": 2,
                "confidence": 0.8,
                "source": "vkpi_kol_pool",
                "summary_text": "11D profile is ready; evidence_count=2.",
                "traceable": True,
                "evidence_refs": [
                    {
                        "evidence_id": "dimensions11:block1",
                        "source": "dimensions11_rule_blocks",
                        "source_table": "vkpi_kol_pool",
                        "source_id": kol_pool_id,
                        "title": "lens review evidence",
                        "confidence": 0.8,
                    }
                ],
            },
            {
                "section": "brand_signal",
                "label": "Brand Signal",
                "status": "ready",
                "evidence_count": 1,
                "confidence": 1.0,
                "source": "vkpi_kol_pool.raw_platform_data",
                "summary_text": "Brand signals are ready; signal_count=1.",
                "traceable": True,
                "evidence_refs": [
                    {
                        "evidence_id": "signal:1",
                        "source": "brand_signal",
                        "source_table": "vkpi_kol_pool",
                        "source_id": kol_pool_id,
                        "source_url": "https://youtube.com/watch?v=1",
                        "title": "Viltrox mention",
                        "confidence": 1.0,
                    }
                ],
            },
        ],
    }


def test_evidence_agent_organizes_explicit_kol_ids(monkeypatch) -> None:
    monkeypatch.setattr(
        evidence_agent_v0.evidence_summary,
        "build_kol_pool_evidence_summary",
        lambda kol_pool_id, **_kwargs: _summary_payload(int(kol_pool_id)),
    )

    report = evidence_agent_v0.build_evidence_agent_v0(kol_pool_ids="101,101,102", limit=5)

    assert report["passed"] is True
    assert report["summary"]["agent_status"] == "ready"
    assert report["summary"]["target_source"] == "explicit_kol_pool_ids"
    assert report["summary"]["target_count"] == 2
    assert report["summary"]["chain_count"] == 2
    assert report["summary"]["evidence_ref_count"] == 4
    assert report["provider_calls"] is False
    assert report["llm_calls"] is False
    assert report["write_db"] is False
    assert report["policy"]["new_fact_generation"] is False
    for chain in report["chains"]:
        assert chain["claims"]
        assert all(claim["new_fact_generated"] is False for claim in chain["claims"])
        assert chain["evidence_refs"]


def test_evidence_agent_reads_weekly_action_targets(tmp_path, monkeypatch) -> None:
    artifact = {
        "passed": True,
        "summary": {"action_count": 2},
        "actions": [
            {"action_type": "contact_kol_for_sku", "priority": "high", "entity": {"kol_pool_id": 201}, "title": "Review one"},
            {"action_type": "review_growth_post", "priority": "high", "entity": {"post_uid": "p1"}, "title": "Post only"},
            {"action_type": "contact_kol_for_sku", "priority": "medium", "entity": {"kol_pool_id": 202}, "title": "Review two"},
        ],
    }
    (tmp_path / "latest-p6-77-weekly-action-plan-v0.json").write_text(json.dumps(artifact), encoding="utf-8")
    monkeypatch.setattr(
        evidence_agent_v0.evidence_summary,
        "build_kol_pool_evidence_summary",
        lambda kol_pool_id, **_kwargs: _summary_payload(int(kol_pool_id)),
    )

    report = evidence_agent_v0.build_evidence_agent_v0(ops_dir=str(tmp_path), limit=5)

    assert report["passed"] is True
    assert report["summary"]["target_source"] == "p6_77_weekly_action_plan"
    assert report["summary"]["target_count"] == 2
    assert report["target_source"]["weekly_action_plan"]["loaded"] is True
    assert [chain["kol_pool_id"] for chain in report["chains"]] == [201, 202]


def test_evidence_agent_fails_when_default_target_source_missing(tmp_path) -> None:
    report = evidence_agent_v0.build_evidence_agent_v0(ops_dir=str(tmp_path))

    assert report["passed"] is False
    assert report["summary"]["agent_status"] == "source_missing"
    assert report["checks"]["target_source_loaded"] is False
    assert report["provider_calls"] is False
    assert report["sync_triggered"] is False
