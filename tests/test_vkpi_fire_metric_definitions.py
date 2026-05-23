from __future__ import annotations

from app.services.vkpi import fire_metric_definitions


def test_fire_metric_definition_report_is_read_only_and_complete() -> None:
    report = fire_metric_definitions.build_fire_metric_definition_report()

    assert report["passed"] is True
    assert report["provider_calls"] is False
    assert report["llm_calls"] is False
    assert report["write_db"] is False
    assert report["sync_triggered"] is False
    assert len(report["metrics"]) >= 6
    assert all(item["formula"] for item in report["metrics"].values())
    assert all(item["required_sources"] for item in report["metrics"].values())
    assert any("cumulative" in item for item in report["score_contract"]["not_allowed"])
    assert any("baseline_protected" in item for item in report["score_contract"]["minimum_evidence_for_hot"])
