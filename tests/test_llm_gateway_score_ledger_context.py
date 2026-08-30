from __future__ import annotations

from app.platform import llm_gateway


def test_score_preserves_default_purpose_and_accepts_marker_context(monkeypatch) -> None:
    calls: list[dict] = []
    monkeypatch.setattr(llm_gateway, "record_call", lambda **kwargs: calls.append(kwargs))

    default_result = llm_gateway.score({"followers": 10})
    marked_result = llm_gateway.score(
        {"followers": 10, "views": 20},
        purpose="smoke-marker-score",
        triggered_by="system:test",
        metadata={"marker": "smoke-marker"},
    )

    assert default_result["status"] == "not_configured"
    assert marked_result["fallback"] == "rule_v0"
    assert calls[0]["purpose"] == "score"
    assert calls[0]["metadata"] == {"feature_count": 1}
    assert calls[1]["purpose"] == "smoke-marker-score"
    assert calls[1]["triggered_by"] == "system:test"
    assert calls[1]["metadata"] == {"marker": "smoke-marker", "feature_count": 2}
