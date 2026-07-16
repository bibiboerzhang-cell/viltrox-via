from __future__ import annotations

import asyncio
import json
from typing import Any

import pytest

from app.services.kol import content_scorer


class _Conn:
    def __init__(self, row: dict[str, Any]) -> None:
        self.row = row
        self.writes: list[tuple[str, tuple[Any, ...]]] = []
        self.commits = 0

    def execute(self, sql: str, params=()):
        if "SELECT co.*" in sql:
            return self
        self.writes.append((sql, tuple(params)))
        return self

    def fetchone(self):
        return self.row

    def commit(self) -> None:
        self.commits += 1


def _row() -> dict[str, Any]:
    return {
        "id": 17,
        "campaign_id": 4,
        "platform": "youtube",
        "views": 1000,
        "likes": 100,
        "comments": 10,
        "shares": 2,
        "product_sku": "AF-27",
        "campaign_notes": "lens launch",
        "channel_name": "Creator",
        "niche": "camera",
        "country": "US",
        "ai_analysis_json": json.dumps({"analysis": {"existing": True}}),
        "ai_quality_score": 91,
        "ai_summary": "prior verified model score",
        "ai_topics_json": '["prior"]',
    }


def test_metrics_fallback_has_explicit_rule_provenance() -> None:
    result = content_scorer._metrics_score(_row())

    assert result["method"] == "metrics_fallback"
    assert result["provenance"] == {
        "source_type": "deterministic_rule",
        "provider": "rule_v0",
        "model": "metrics_v1",
        "method": "metrics_fallback",
        "fallback": True,
        "input_fields": ["views", "likes", "comments", "shares", "platform"],
    }


@pytest.mark.parametrize("value", ("{broken", "[]", ["not", "json"]))
def test_invalid_existing_analysis_fails_closed_instead_of_being_overwritten(value: Any) -> None:
    with pytest.raises((TypeError, ValueError), match="ai_analysis_json"):
        content_scorer._json_object(value)


def test_invalid_existing_analysis_aborts_before_any_database_write(monkeypatch) -> None:
    row = _row()
    row["ai_analysis_json"] = "{broken"
    conn = _Conn(row)
    monkeypatch.setattr(content_scorer, "get_conn", lambda: conn)
    monkeypatch.setattr(
        content_scorer,
        "_score_with_claude",
        lambda *_args, **_kwargs: content_scorer._metrics_score(row),
    )

    with pytest.raises(ValueError, match="ai_analysis_json contains invalid JSON"):
        asyncio.run(content_scorer.score_kol_content(17))

    assert conn.writes == []
    assert conn.commits == 0


def test_claude_failure_keeps_rule_fallback_truth(monkeypatch) -> None:
    monkeypatch.setattr(
        content_scorer.llm_production,
        "generate_json",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("provider down")),
    )

    result = content_scorer._score_with_claude(_row())

    assert result["method"] == "metrics_fallback_after_claude_error"
    assert result["provenance"]["source_type"] == "deterministic_rule"
    assert result["provenance"]["provider"] == "rule_v0"
    assert result["provenance"]["failure_reason"] == "production_llm_unavailable"


def test_claude_score_requires_exact_binding_and_complete_json_contract(monkeypatch) -> None:
    captured: dict[str, Any] = {}
    payload = {
        "score": 87.6,
        "summary_zh": "内容与镜头产品高度相关。",
        "summary_en": "The content is highly relevant to the lens product.",
        "topics": ["lens", "review", "creator"],
        "method": "claude",
    }

    def generate_json(_prompt: str, **kwargs: Any) -> dict[str, Any]:
        captured.update(kwargs)
        assert kwargs["validator"](payload) is True
        return {
            "status": "success",
            "provider": "anthropic",
            "model": content_scorer.CLAUDE_MODEL,
            "json": payload,
        }

    monkeypatch.setattr(content_scorer.llm_production, "generate_json", generate_json)
    result = content_scorer._score_with_claude(_row())

    assert result["score"] == 88
    assert result["method"] == "claude"
    assert result["provenance"]["source_type"] == "llm"
    assert captured["required_keys"] == (
        "score",
        "summary_zh",
        "summary_en",
        "topics",
        "method",
    )
    assert captured["metadata"]["content_id"] == 17
    assert captured["metadata"]["attempt_index"] == 1
    assert captured["metadata"]["total"] == 1


def test_empty_or_partial_json_never_becomes_verified_ai_score(monkeypatch) -> None:
    monkeypatch.setattr(
        content_scorer.llm_production,
        "generate_json",
        lambda *_args, **_kwargs: {
            "status": "success",
            "provider": "anthropic",
            "model": content_scorer.CLAUDE_MODEL,
            "json": {},
        },
    )

    result = content_scorer._score_with_claude(_row())

    assert result["method"] == "metrics_fallback_after_claude_error"
    assert result["provenance"]["source_type"] == "deterministic_rule"
    assert result["provenance"]["failure_reason"] == "response_contract_invalid"
    assert content_scorer._valid_llm_score({}) is False


def test_rule_fallback_does_not_overwrite_ai_fields_and_persists_provenance(monkeypatch) -> None:
    conn = _Conn(_row())
    monkeypatch.setattr(content_scorer, "get_conn", lambda: conn)
    monkeypatch.setattr(
        content_scorer,
        "_score_with_claude",
        lambda *_args, **_kwargs: content_scorer._metrics_score(_row()),
    )

    result = asyncio.run(content_scorer.score_kol_content(17))

    assert result["persisted_to_ai_fields"] is False
    assert result["quality_score_status"] == "fallback_not_persisted_as_ai"
    assert result["provenance"]["source_type"] == "deterministic_rule"
    assert len(conn.writes) == 1
    sql, params = conn.writes[0]
    assert "SET ai_analysis_json = ?" in sql
    assert "ai_quality_score" not in sql
    persisted = json.loads(params[0])
    assert persisted["analysis"] == {"existing": True}
    score = persisted["content_score"]
    assert score["method"] == "metrics_fallback"
    assert score["persisted_to_ai_fields"] is False
    assert score["provenance"]["provider"] == "rule_v0"
    assert conn.commits == 1


def test_verified_llm_score_updates_ai_fields_with_same_provenance(monkeypatch) -> None:
    conn = _Conn(_row())
    monkeypatch.setattr(content_scorer, "get_conn", lambda: conn)
    monkeypatch.setattr(
        content_scorer,
        "_score_with_claude",
        lambda *_args, **_kwargs: {
            "score": 88,
            "summary_zh": "真实模型评分",
            "topics": ["camera", "review"],
            "method": "claude",
            "provenance": {
                "source_type": "llm",
                "provider": "anthropic",
                "model": "claude-sonnet-4-6",
                "method": "claude",
                "purpose": "kol_content_scorer",
                "fallback": False,
            },
        },
    )

    result = asyncio.run(content_scorer.score_kol_content(17))

    assert result["persisted_to_ai_fields"] is True
    assert result["quality_score_status"] == "ready"
    sql, params = conn.writes[0]
    assert "ai_quality_score = ?" in sql
    assert params[0] == 88
    persisted = json.loads(params[3])
    assert persisted["analysis"] == {"existing": True}
    assert persisted["content_score"]["provenance"]["source_type"] == "llm"
    assert persisted["content_score"]["persisted_to_ai_fields"] is True
