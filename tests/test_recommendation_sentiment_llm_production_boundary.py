"""Focused offline contracts for migrated recommendation/sentiment LLM paths."""
from __future__ import annotations

from typing import Any

from app.domains.market import sentiment_annotate as sentiment
from app.domains.recommendations import new_launch_match, project_next_action


class _Rows:
    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self.rows = rows

    def fetchall(self) -> list[dict[str, Any]]:
        return self.rows

    def fetchone(self) -> dict[str, Any] | None:
        return self.rows[0] if self.rows else None


class _SentimentConn:
    def __init__(self) -> None:
        self.row = {"id": 7, "comment_text": "Sharp lens and quick autofocus", "language_detected": "en"}
        self.writes: list[tuple[str, tuple[Any, ...]]] = []
        self.committed = False

    def execute(self, sql: str, params: tuple[Any, ...] = ()) -> _Rows:
        normalized = " ".join(sql.split()).lower()
        if "count(*)" in normalized:
            return _Rows([{"n": 1}])
        if normalized.startswith("select") and "from vkpi_comments" in normalized:
            return _Rows([self.row])
        if normalized.startswith("select comment_id, id"):
            return _Rows([])
        if normalized.startswith("insert into vkpi_sentiment_results"):
            self.writes.append((normalized, params))
            return _Rows([])
        if normalized.startswith("select id from vkpi_sentiment_results"):
            return _Rows([{"id": 99}])
        if normalized.startswith("update vkpi_comments"):
            self.writes.append((normalized, params))
            return _Rows([])
        return _Rows([])

    def commit(self) -> None:
        self.committed = True


def test_preview_mode_truthfully_declares_provider_behavior() -> None:
    for module in (new_launch_match, project_next_action):
        dry = module._preview_execution_policy(
            with_llm_reasons=False,
            reason_limit=20,
            returned_count=5,
        )
        assert dry == {
            "mode": "dry_run",
            "provider_calls_allowed": False,
            "provider_calls_planned": 0,
            "provider_call_scope": "none",
            "deterministic_ranking": True,
            "business_actions_executed": False,
        }

        enriched = module._preview_execution_policy(
            with_llm_reasons=True,
            reason_limit=3,
            returned_count=5,
        )
        assert enriched["mode"] == "ai_enriched_preview"
        assert enriched["provider_calls_allowed"] is True
        assert enriched["provider_calls_planned"] == 3
        assert enriched["provider_call_scope"] == "recommendation_reason_only"
        assert enriched["business_actions_executed"] is False

        empty = module._preview_execution_policy(
            with_llm_reasons=True,
            reason_limit=3,
            returned_count=0,
        )
        assert empty["mode"] == "dry_run"
        assert empty["provider_calls_allowed"] is False
        assert empty["provider_calls_planned"] == 0


def test_sentiment_uses_exact_json_boundary_and_progress_metadata(monkeypatch) -> None:
    captured: dict[str, Any] = {}

    def generate_json(prompt: str, **kwargs: Any) -> dict[str, Any]:
        captured.update({"prompt": prompt, **kwargs})
        payload = {"items": [{"id": 7, "score": 0.8, "label": "pos", "aspects": ["autofocus"]}]}
        assert kwargs["validator"](payload) is True
        return {
            "status": "success",
            "provider": kwargs["provider"],
            "model": kwargs["model"],
            "json": payload,
            "input_tokens": 20,
            "output_tokens": 10,
            "cost_micro_usd": 100,
        }

    monkeypatch.setattr(sentiment, "_sentiment_binding", lambda: ("google", "gemini-3.6-flash"))
    monkeypatch.setattr(sentiment.llm_production, "generate_json", generate_json)
    conn = _SentimentConn()

    result = sentiment.annotate_batch(1, dry_run=False, conn=conn)

    assert result["annotated"] == 1
    assert captured["provider"] == "google"
    assert captured["model"] == "gemini-3.6-flash"
    assert captured["required_keys"] == ("items",)
    assert captured["metadata"]["phase"] == "analysis"
    assert captured["metadata"]["subphase"] == "sentiment_annotation"
    assert captured["metadata"]["attempt_index"] == 1
    assert captured["metadata"]["total"] == 1
    assert captured["metadata"]["target_label"] == "comments 7-7"
    assert conn.committed is True


def test_new_launch_reason_is_strict_or_deterministic(monkeypatch) -> None:
    captured: dict[str, Any] = {}
    item = {
        "rank": 2,
        "handle": "creator",
        "kol_entity_uid": "kol-7",
        "evidence_pro": [{"detail": "strong lens evidence"}],
        "evidence_con": [],
    }
    payload = {"product_query": "AF 35mm", "target_family_name": "AF"}
    expected = {
        "short_reason": "Strong fit.",
        "pitch_angle": "Lead with autofocus.",
        "caution_note": "Verify availability.",
    }

    def generate_json(prompt: str, **kwargs: Any) -> dict[str, Any]:
        captured.update({"prompt": prompt, **kwargs})
        assert kwargs["validator"](expected) is True
        return {
            "status": "success",
            "provider": kwargs["provider"],
            "model": kwargs["model"],
            "json": expected,
        }

    monkeypatch.setattr(new_launch_match, "_reason_binding", lambda: ("openai", "gpt-5.4-mini"))
    monkeypatch.setattr(new_launch_match.llm_production, "generate_json", generate_json)
    new_launch_match._attach_reason(payload, item, attempt_index=2, total=5)

    assert item["recommendation_reason"]["mode"] == "llm"
    assert captured["required_keys"] == ("short_reason", "pitch_angle", "caution_note")
    assert captured["metadata"]["attempt_index"] == 2
    assert captured["metadata"]["total"] == 5
    assert captured["metadata"]["target_label"] == "creator"

    monkeypatch.setattr(
        new_launch_match.llm_production,
        "generate_json",
        lambda *_a, **_k: {"status": "fallback_to_rule", "reason": "readiness_not_production_ready"},
    )
    new_launch_match._attach_reason(payload, item)
    assert item["recommendation_reason"]["mode"] == "deterministic_fallback"
    assert item["recommendation_reason"]["fallback_reason"] == "readiness_not_production_ready"


def test_project_next_action_reason_is_strict_or_deterministic(monkeypatch) -> None:
    captured: dict[str, Any] = {}
    item = {
        "project_id": 11,
        "project_uid": "project-11",
        "project_name": "Launch Project",
        "rank": 1,
        "suggested_action": "confirm_terms",
        "reason": "No terms are recorded",
        "evidence_pro": [{"detail": "project has a product"}],
        "evidence_con": [{"detail": "terms missing"}],
    }
    expected = {
        "short_reason": "Confirm terms next.",
        "execution_note": "Ask the owner to record terms.",
        "caution_note": "This is advice, not an executed action.",
    }

    def generate_json(prompt: str, **kwargs: Any) -> dict[str, Any]:
        captured.update({"prompt": prompt, **kwargs})
        assert kwargs["validator"](expected) is True
        return {
            "status": "success",
            "provider": kwargs["provider"],
            "model": kwargs["model"],
            "json": expected,
        }

    monkeypatch.setattr(project_next_action, "_reason_binding", lambda: ("openai", "gpt-5.4-mini"))
    monkeypatch.setattr(project_next_action.llm_production, "generate_json", generate_json)
    project_next_action._attach_reason(item, attempt_index=1, total=3)

    assert item["recommendation_reason"]["mode"] == "llm"
    assert captured["required_keys"] == ("short_reason", "execution_note", "caution_note")
    assert captured["metadata"]["phase"] == "recommendation"
    assert captured["metadata"]["target_label"] == "Launch Project"

    monkeypatch.setattr(
        project_next_action.llm_production,
        "generate_json",
        lambda *_a, **_k: (_ for _ in ()).throw(RuntimeError("AI disabled")),
    )
    project_next_action._attach_reason(item)
    assert item["recommendation_reason"]["mode"] == "deterministic_fallback"
    assert item["recommendation_reason"]["provider"] == "rule_v0"
    assert item["recommendation_reason"]["fallback_reason"] == "AI disabled"
