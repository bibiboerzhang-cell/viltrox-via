from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from app.domains.comments import intelligence, reply_queue, sentiment
from app.domains.content import pillars


class _OneRow:
    def __init__(self, row: Any = None) -> None:
        self.row = row

    def fetchone(self) -> Any:
        return self.row


def test_reply_draft_uses_one_exact_atomic_json_call(monkeypatch) -> None:
    monkeypatch.setenv("VKPI_OPENAI_MODEL", "gpt-test-exact")
    captured: dict[str, Any] = {}

    def fake_generate_json(prompt: str, **kwargs: Any) -> dict[str, Any]:
        captured.update({"prompt": prompt, **kwargs})
        payload = {"draft_reply": "Thanks! Tell us your camera body and we will confirm compatibility."}
        assert kwargs["validator"](payload) is True
        return {
            "status": "success",
            "provider": "openai",
            "model": "gpt-test-exact",
            "json": payload,
        }

    monkeypatch.setattr(reply_queue.llm_production, "generate_json", fake_generate_json)
    draft, provider = reply_queue._generate_reply_draft(
        reply_id=42,
        text="Will this fit my camera?",
        intent="compat",
        skus=[],
        lang="en",
        staff={"id": 7},
    )

    assert provider == "openai"
    assert draft.startswith("Thanks!")
    assert captured["model"] == "gpt-test-exact"
    assert captured["required_keys"] == ("draft_reply",)
    assert captured["metadata"] == {
        "surface": "reply_queue",
        "reply_id": 42,
        "intent": "compat",
        "lang": "en",
        "phase": "comment_response",
        "subphase": "draft_reply",
        "attempt_index": 1,
        "total": 1,
        "target_label": "reply:42",
    }
    assert captured["staff"] == {"id": 7}


def test_reply_draft_rejects_mismatched_model_and_keeps_template_fallback(monkeypatch) -> None:
    monkeypatch.setenv("VKPI_OPENAI_MODEL", "gpt-requested")
    monkeypatch.setattr(
        reply_queue.llm_production,
        "generate_json",
        lambda *_args, **_kwargs: {
            "status": "success",
            "provider": "openai",
            "model": "gpt-silent-fallback",
            "json": {"draft_reply": "unsafe"},
        },
    )
    assert reply_queue._generate_reply_draft(
        reply_id=1,
        text="price?",
        intent="price",
        skus=[],
        lang="en",
        staff=None,
    ) == ("", "")
    assert "DM us" in reply_queue._template_reply("price?", "price", [], "en")


def test_sentiment_uses_exact_preferred_binding_and_strict_contract(monkeypatch) -> None:
    monkeypatch.setenv("VKPI_SENTIMENT_PREFERRED_PROVIDER", "gemini")
    monkeypatch.setenv("VKPI_GEMINI_MODEL", "gemini-test-exact")
    captured: dict[str, Any] = {}
    payload = {
        "sentiment": "positive",
        "sentiment_confidence": 0.9,
        "emotion": "joy",
        "emotion_confidence": 0.8,
        "brand_attitude": "supportive",
        "brand_attitude_confidence": 0.7,
        "language_detected": "en",
    }

    def fake_generate_json(prompt: str, **kwargs: Any) -> dict[str, Any]:
        captured.update({"prompt": prompt, **kwargs})
        assert kwargs["validator"](payload) is True
        return {
            "status": "success",
            "provider": "google",
            "model": "gemini-test-exact",
            "json": payload,
            "input_tokens": 12,
            "output_tokens": 8,
            "cost_cents": 1,
        }

    monkeypatch.setattr(sentiment.llm_production, "generate_json", fake_generate_json)
    result = sentiment._run_sentiment_llm("prompt", comment_id=9, staff={"id": 3})

    assert result["json"] == payload
    assert captured["provider"] == "google"
    assert captured["model"] == "gemini-test-exact"
    assert captured["required_keys"] == (
        "sentiment",
        "sentiment_confidence",
        "emotion",
        "emotion_confidence",
        "brand_attitude",
        "brand_attitude_confidence",
        "language_detected",
    )
    assert captured["metadata"]["phase"] == "comment_intelligence"
    assert captured["metadata"]["subphase"] == "sentiment"
    assert captured["metadata"]["target_label"] == "comment:9"


def test_sentiment_ai_off_and_invalid_json_are_neutral_rule_fallback(monkeypatch) -> None:
    monkeypatch.setattr(
        sentiment.llm_production,
        "generate_json",
        lambda *_args, **_kwargs: {
            "status": "fallback_to_rule",
            "provider": "rule_v0",
            "model": "rule_v0",
            "json": None,
        },
    )
    response = sentiment._run_sentiment_llm("prompt", comment_id=1, staff=None)
    assert response["status"] == "fallback_to_rule"
    assert response["provider"] == "rule_v0"
    assert sentiment._validate_response(response.get("json") or {}) == {
        "sentiment": "neutral",
        "sentiment_confidence": 0.5,
        "emotion": "neutral",
        "emotion_confidence": 0.5,
        "brand_attitude": "neutral",
        "brand_attitude_confidence": 0.5,
        "language_detected": "unknown",
    }
    assert sentiment._valid_sentiment_payload(
        {
            "sentiment": "positive",
            "sentiment_confidence": "0.9",
            "emotion": "joy",
            "emotion_confidence": 0.8,
            "brand_attitude": "supportive",
            "brand_attitude_confidence": 0.7,
            "language_detected": "en",
        }
    ) is False


def test_sentiment_degraded_result_is_retryable_and_never_persisted(monkeypatch) -> None:
    class Conn:
        def execute(self, sql: str, _params: Any = None) -> _OneRow:
            if "FROM vkpi_sentiment_results" in sql:
                return _OneRow(None)
            if "FROM vkpi_comments" in sql:
                return _OneRow(
                    {
                        "id": 71,
                        "comment_text": "Does this lens support E mount?",
                        "platform": "youtube",
                        "language_detected": "en",
                        "account_id": 2,
                        "post_id": 3,
                    }
                )
            raise AssertionError(f"unexpected SQL: {sql}")

    monkeypatch.setattr(sentiment, "ensure_vkpi_sentiment_schema", lambda: None)
    monkeypatch.setattr(sentiment, "get_conn", lambda: Conn())
    monkeypatch.setattr(
        sentiment,
        "_run_sentiment_llm",
        lambda *_args, **_kwargs: {
            "status": "fallback_to_rule",
            "provider": "rule_v0",
            "model": "rule_v0",
            "reason": "binding_not_runtime_verified",
        },
    )
    monkeypatch.setattr(
        sentiment,
        "_persist_result",
        lambda **_kwargs: (_ for _ in ()).throw(AssertionError("degraded result must not persist")),
    )

    result = sentiment.analyze_comment(71)
    assert result == {
        "comment_id": 71,
        "status": "degraded",
        "method": "deterministic_fallback",
        "persisted": False,
        "retryable": True,
        "reason": "binding_not_runtime_verified",
        "sentiment": "neutral",
        "emotion": "neutral",
        "brand_attitude": "neutral",
        "confidence": {"sentiment": 0.5, "emotion": 0.5, "brand_attitude": 0.5},
        "language_detected": "unknown",
        "llm_provider": "rule_v0",
        "llm_model": "rule_v0",
        "cost_cents": 0,
    }


def test_sentiment_force_upsert_refreshes_full_llm_provenance(monkeypatch) -> None:
    class Conn:
        def __init__(self) -> None:
            self.calls: list[tuple[str, Any]] = []
            self.committed = False

        def execute(self, sql: str, params: Any = None) -> _OneRow:
            self.calls.append((sql, params))
            if "SELECT id FROM vkpi_sentiment_results" in sql:
                return _OneRow({"id": 99})
            return _OneRow(None)

        def commit(self) -> None:
            self.committed = True

    conn = Conn()
    monkeypatch.setattr(sentiment, "ensure_vkpi_sentiment_schema", lambda: None)
    monkeypatch.setattr(sentiment, "get_conn", lambda: conn)
    result = sentiment._persist_result(
        comment_id=8,
        result={
            "sentiment": "positive",
            "sentiment_confidence": 0.91,
            "emotion": "joy",
            "emotion_confidence": 0.82,
            "brand_attitude": "supportive",
            "brand_attitude_confidence": 0.73,
            "language_detected": "en",
        },
        llm_provider="google",
        llm_model="gemini-exact",
        input_tokens=101,
        output_tokens=22,
        cost_cents=3,
    )
    upsert = " ".join(conn.calls[0][0].split()).lower()
    for assignment in (
        "llm_provider = excluded.llm_provider",
        "llm_model = excluded.llm_model",
        "language_detected = excluded.language_detected",
        "input_tokens = excluded.input_tokens",
        "output_tokens = excluded.output_tokens",
        "cost_cents = excluded.cost_cents",
    ):
        assert assignment in upsert
    assert conn.calls[0][1][7:14] == (
        "google",
        "gemini-exact",
        sentiment.PROMPT_VERSION,
        "en",
        101,
        22,
        3,
    )
    assert conn.committed is True
    assert result["status"] == "ok"


def test_sentiment_legacy_rule_placeholder_does_not_block_real_retry(monkeypatch) -> None:
    class Conn:
        def execute(self, sql: str, _params: Any = None) -> _OneRow:
            if "FROM vkpi_sentiment_results" in sql:
                return _OneRow({"id": 4, "llm_provider": "rule_v0", "llm_model": "rule_v0"})
            if "FROM vkpi_comments" in sql:
                return _OneRow(
                    {
                        "id": 72,
                        "comment_text": "Great lens",
                        "platform": "instagram",
                        "language_detected": "en",
                        "account_id": 2,
                        "post_id": 3,
                    }
                )
            raise AssertionError(f"unexpected SQL: {sql}")

    payload = {
        "sentiment": "positive",
        "sentiment_confidence": 0.9,
        "emotion": "joy",
        "emotion_confidence": 0.8,
        "brand_attitude": "supportive",
        "brand_attitude_confidence": 0.7,
        "language_detected": "en",
    }
    persisted: dict[str, Any] = {}
    monkeypatch.setattr(sentiment, "ensure_vkpi_sentiment_schema", lambda: None)
    monkeypatch.setattr(sentiment, "get_conn", lambda: Conn())
    monkeypatch.setattr(
        sentiment,
        "_run_sentiment_llm",
        lambda *_args, **_kwargs: {
            "status": "success",
            "provider": "openai",
            "model": "gpt-exact",
            "json": payload,
            "input_tokens": 10,
            "output_tokens": 5,
            "cost_cents": 1,
        },
    )

    def persist(**kwargs: Any) -> dict[str, Any]:
        persisted.update(kwargs)
        return {"comment_id": kwargs["comment_id"], "status": "ok"}

    monkeypatch.setattr(sentiment, "_persist_result", persist)
    assert sentiment.analyze_comment(72) == {"comment_id": 72, "status": "ok"}
    assert persisted["llm_provider"] == "openai"
    assert persisted["llm_model"] == "gpt-exact"


def test_sentiment_explicit_unknown_provider_fails_closed_without_call(monkeypatch) -> None:
    monkeypatch.setenv("VKPI_SENTIMENT_PREFERRED_PROVIDER", "mystery-provider")

    def forbidden_provider_boundary(*_args: Any, **_kwargs: Any) -> dict[str, Any]:
        raise AssertionError("invalid binding must not reach the provider boundary")

    monkeypatch.setattr(
        sentiment.llm_production,
        "generate_json",
        forbidden_provider_boundary,
    )
    result = sentiment._run_sentiment_llm("prompt", comment_id=4, staff=None)
    assert result["status"] == "fallback_to_rule"
    assert result["reason"] == "binding_invalid"
    assert result["requested_provider"] == "mystery-provider"

    monkeypatch.delenv("VKPI_SENTIMENT_PREFERRED_PROVIDER")
    monkeypatch.setenv("VKPI_OPENAI_MODEL", "gpt-default-exact")
    assert sentiment._sentiment_llm_binding() == ("openai", "gpt-default-exact")


def test_sentiment_batch_reports_every_nonterminal_status_as_error(monkeypatch) -> None:
    outcomes: dict[int, Any] = {
        1: {"comment_id": 1, "status": "ok"},
        2: {"comment_id": 2, "status": "duplicate"},
        3: {"comment_id": 3, "status": "degraded", "reason": "budget_disabled"},
        4: {"comment_id": 4, "status": "fail", "error": "bad input"},
        5: {},
    }

    def analyze(comment_id: int, **_kwargs: Any) -> dict[str, Any]:
        if comment_id == 6:
            raise RuntimeError("boom")
        return outcomes[comment_id]

    monkeypatch.setattr(sentiment, "analyze_comment", analyze)
    summary = sentiment.analyze_batch([1, 2, 3, 4, 5, 6])
    assert summary["by_status"] == {
        "ok": 1,
        "duplicate": 1,
        "degraded": 1,
        "fail": 1,
        "unknown": 1,
        "exception": 1,
    }
    assert [item["status"] for item in summary["errors"]] == [
        "degraded",
        "fail",
        "unknown",
        "exception",
    ]
    assert summary["errors"][0]["error"] == "budget_disabled"


def test_comment_pipeline_marks_degraded_sentiment_batch_partial(monkeypatch) -> None:
    finished: dict[str, Any] = {}
    monkeypatch.setattr(intelligence, "ensure_vkpi_comment_intelligence_schema", lambda: None)
    monkeypatch.setattr(
        intelligence,
        "_start_run",
        lambda **_kwargs: {"id": 13, "run_uid": "ci_test"},
    )
    monkeypatch.setattr(intelligence, "_comment_ids_for_post", lambda *_args, **_kwargs: [7])
    monkeypatch.setattr(
        intelligence.sentiment,
        "analyze_batch",
        lambda *_args, **_kwargs: {
            "total": 1,
            "by_status": {"degraded": 1},
            "errors": [
                {
                    "comment_id": 7,
                    "status": "degraded",
                    "reason": "binding_not_runtime_verified",
                }
            ],
        },
    )

    def finish(run_id: int, **kwargs: Any) -> None:
        finished.update({"run_id": run_id, **kwargs})

    monkeypatch.setattr(intelligence, "_finish_run", finish)
    result = intelligence.process_post(
        3,
        collect_comments=False,
        classify_pillar=False,
        analyze_sentiment=True,
    )
    assert result["status"] == "partial"
    assert finished["status"] == "partial"


def test_normal_comment_queue_and_backfill_retry_legacy_rule_placeholders(monkeypatch) -> None:
    class Conn:
        def __init__(self) -> None:
            self.sql: list[str] = []

        def execute(self, sql: str, _params: Any = None) -> Any:
            self.sql.append(" ".join(sql.split()).lower())

            class Rows:
                def fetchall(self) -> list[dict[str, int]]:
                    return [{"id": 81}]

            return Rows()

    intelligence_conn = Conn()
    monkeypatch.setattr(intelligence.comments_collector, "ensure_vkpi_comments_schema", lambda: None)
    monkeypatch.setattr(intelligence.sentiment, "ensure_vkpi_sentiment_schema", lambda: None)
    monkeypatch.setattr(intelligence, "get_conn", lambda: intelligence_conn)
    assert intelligence._comment_ids_for_post(2, "industry_posts") == [81]
    intelligence_sql = intelligence_conn.sql[0]
    assert "s.id is null or" in intelligence_sql
    assert "coalesce(s.llm_provider, '') = 'rule_v0'" in intelligence_sql
    assert "coalesce(s.llm_model, '') = 'rule_v0'" in intelligence_sql

    backfill_conn = Conn()
    captured: dict[str, Any] = {}
    monkeypatch.setattr(sentiment, "ensure_vkpi_sentiment_schema", lambda: None)
    monkeypatch.setattr(sentiment, "get_conn", lambda: backfill_conn)
    monkeypatch.setattr(
        sentiment,
        "analyze_batch",
        lambda ids, **_kwargs: captured.update({"ids": ids}) or {"total": len(ids)},
    )
    assert sentiment.backfill_historical(days=1, limit=10) == {"total": 1}
    backfill_sql = backfill_conn.sql[0]
    assert "s.id is null or" in backfill_sql
    assert "coalesce(s.llm_provider, '') = 'rule_v0'" in backfill_sql
    assert "coalesce(s.llm_model, '') = 'rule_v0'" in backfill_sql
    assert captured["ids"] == [81]


def test_pillar_uses_exact_json_contract_and_ai_off_other_fallback(monkeypatch) -> None:
    monkeypatch.setenv("VKPI_OPENAI_MODEL", "gpt-pillar-exact")
    captured: dict[str, Any] = {}
    valid_keys = {"other", "lens_review", "shooting_tutorial"}
    payload = {
        "primary_pillar": "lens_review",
        "primary_confidence": 0.93,
        "secondary_pillars": ["shooting_tutorial"],
        "secondary_confidences": [0.72],
        "reasoning": "The post reviews a lens and explains shooting technique.",
    }

    def fake_generate_json(prompt: str, **kwargs: Any) -> dict[str, Any]:
        captured.update({"prompt": prompt, **kwargs})
        assert kwargs["validator"](payload) is True
        return {
            "status": "success",
            "provider": "openai",
            "model": "gpt-pillar-exact",
            "json": payload,
        }

    monkeypatch.setattr(pillars.llm_production, "generate_json", fake_generate_json)
    response = pillars._run_pillar_llm(
        "prompt",
        post_id=17,
        post_table="industry_posts",
        valid_keys=valid_keys,
        staff=None,
    )
    assert response["json"] == payload
    assert captured["required_keys"] == (
        "primary_pillar",
        "primary_confidence",
        "secondary_pillars",
        "secondary_confidences",
        "reasoning",
    )
    assert captured["metadata"]["phase"] == "content_intelligence"
    assert captured["metadata"]["subphase"] == "pillar_classification"
    assert captured["metadata"]["target_label"] == "industry_posts:17"

    monkeypatch.setattr(
        pillars.llm_production,
        "generate_json",
        lambda *_args, **_kwargs: {
            "status": "fallback_to_rule",
            "provider": "rule_v0",
            "model": "rule_v0",
            "json": None,
        },
    )
    fallback = pillars._run_pillar_llm(
        "prompt",
        post_id=18,
        post_table="industry_posts",
        valid_keys=valid_keys,
        staff=None,
    )
    assert fallback["provider"] == "rule_v0"
    assert pillars._validate_response({}, valid_keys)["primary_pillar"] == "other"


def test_pillar_degraded_result_is_retryable_and_never_persisted(monkeypatch) -> None:
    class Conn:
        def execute(self, sql: str, _params: Any = None) -> _OneRow:
            if "SELECT COUNT(*)" in sql:
                return _OneRow({"n": 0})
            raise AssertionError(f"unexpected SQL: {sql}")

    monkeypatch.setattr(pillars, "ensure_vkpi_pillar_schema", lambda: None)
    monkeypatch.setattr(pillars, "get_conn", lambda: Conn())
    monkeypatch.setattr(
        pillars,
        "_resolve_post",
        lambda *_args: {
            "platform": "youtube",
            "title": "Lens review",
            "description": "A field test",
            "hashtags_json": "[]",
            "duration_seconds": 90,
        },
    )
    monkeypatch.setattr(pillars, "_load_active_pillar_keys", lambda: {"other", "lens_review"})
    monkeypatch.setattr(
        pillars,
        "_run_pillar_llm",
        lambda *_args, **_kwargs: {
            "status": "fallback_to_rule",
            "provider": "rule_v0",
            "model": "rule_v0",
            "reason": "response_contract_invalid",
        },
    )
    monkeypatch.setattr(
        pillars,
        "_persist_classification",
        lambda **_kwargs: (_ for _ in ()).throw(AssertionError("degraded result must not persist")),
    )

    result = pillars.classify_post(31, "industry_posts")
    assert result == {
        "post_id": 31,
        "post_table": "industry_posts",
        "status": "degraded",
        "method": "deterministic_fallback",
        "persisted": False,
        "retryable": True,
        "reason": "response_contract_invalid",
        "primary_pillar": "other",
        "primary_confidence": 0.5,
        "secondary_pillars": [],
        "llm_provider": "rule_v0",
        "llm_model": "rule_v0",
    }


def test_pillar_force_reclassification_atomically_replaces_old_rows(monkeypatch) -> None:
    class Conn:
        def __init__(self) -> None:
            self.calls: list[tuple[str, Any]] = []
            self.committed = False
            self.rolled_back = False

        def execute(self, sql: str, params: Any = None) -> _OneRow:
            self.calls.append((sql, params))
            return _OneRow(None)

        def commit(self) -> None:
            self.committed = True

        def rollback(self) -> None:
            self.rolled_back = True

    conn = Conn()
    ids = {"lens_review": 10, "shooting_tutorial": 11}
    monkeypatch.setattr(pillars, "ensure_vkpi_pillar_schema", lambda: None)
    monkeypatch.setattr(pillars, "_pillar_id_by_key", lambda key: ids.get(key))
    monkeypatch.setattr(pillars, "get_conn", lambda: conn)

    result = pillars._persist_classification(
        post_id=44,
        post_table="industry_posts",
        result={
            "primary_pillar": "lens_review",
            "primary_confidence": 0.92,
            "secondary_pillars": ["shooting_tutorial"],
            "secondary_confidences": [0.61],
        },
        llm_provider="openai",
        llm_model="gpt-exact",
        replace_existing=True,
    )

    normalized = [" ".join(sql.split()).lower() for sql, _params in conn.calls]
    assert normalized[0].startswith("delete from vkpi_post_pillars")
    assert conn.calls[0][1] == (44, "industry_posts", pillars.PROMPT_VERSION)
    assert len([sql for sql in normalized if sql.startswith("insert into vkpi_post_pillars")]) == 2
    assert all("do update set" in sql for sql in normalized[1:])
    assert all("do nothing" not in sql for sql in normalized)
    assert conn.committed is True
    assert conn.rolled_back is False
    assert result["primary_pillar"] == "lens_review"


def test_pillar_persist_keeps_write_error_and_logs_rollback_failure(monkeypatch, caplog) -> None:
    class Conn:
        def execute(self, _sql: str, _params: Any = None) -> None:
            raise RuntimeError("write failed")

        def rollback(self) -> None:
            raise RuntimeError("rollback failed")

    monkeypatch.setattr(pillars, "ensure_vkpi_pillar_schema", lambda: None)
    monkeypatch.setattr(pillars, "_pillar_id_by_key", lambda _key: 10)
    monkeypatch.setattr(pillars, "get_conn", lambda: Conn())

    with caplog.at_level("WARNING"), pytest.raises(RuntimeError, match="write failed"):
        pillars._persist_classification(
            post_id=45,
            post_table="industry_posts",
            result={
                "primary_pillar": "lens_review",
                "primary_confidence": 0.92,
                "secondary_pillars": [],
                "secondary_confidences": [],
            },
            llm_provider="openai",
            llm_model="gpt-exact",
        )

    assert "content.pillars.persist_rollback_failed" in caplog.text


def test_pillar_legacy_rule_placeholder_is_retried_and_replaced(monkeypatch) -> None:
    class Conn:
        def execute(self, sql: str, _params: Any = None) -> _OneRow:
            if "SELECT COUNT(*)" in sql:
                return _OneRow({"n": 2, "durable_n": 0})
            raise AssertionError(f"unexpected SQL: {sql}")

    valid_keys = {"other", "lens_review"}
    captured: dict[str, Any] = {}
    monkeypatch.setattr(pillars, "ensure_vkpi_pillar_schema", lambda: None)
    monkeypatch.setattr(pillars, "get_conn", lambda: Conn())
    monkeypatch.setattr(
        pillars,
        "_resolve_post",
        lambda *_args: {
            "platform": "youtube",
            "title": "Review",
            "description": "Lens field review",
            "hashtags_json": "[]",
            "duration_seconds": 120,
        },
    )
    monkeypatch.setattr(pillars, "_load_active_pillar_keys", lambda: valid_keys)
    monkeypatch.setattr(
        pillars,
        "_run_pillar_llm",
        lambda *_args, **_kwargs: {
            "status": "success",
            "provider": "openai",
            "model": "gpt-exact",
            "json": {
                "primary_pillar": "lens_review",
                "primary_confidence": 0.95,
                "secondary_pillars": [],
                "secondary_confidences": [],
                "reasoning": "A lens review.",
            },
        },
    )

    def persist(**kwargs: Any) -> dict[str, Any]:
        captured.update(kwargs)
        return {"post_id": kwargs["post_id"], "status": "ok"}

    monkeypatch.setattr(pillars, "_persist_classification", persist)
    assert pillars.classify_post(52, "industry_posts") == {"post_id": 52, "status": "ok"}
    assert captured["replace_existing"] is True
    assert captured["result"]["primary_pillar"] == "lens_review"


def test_migrated_modules_have_no_legacy_gateway_calls() -> None:
    root = Path(__file__).resolve().parents[1] / "backend" / "app"
    for relative in (
        "domains/comments/reply_queue.py",
        "domains/comments/sentiment.py",
        "domains/content/pillars.py",
    ):
        source = (root / relative).read_text(encoding="utf-8")
        assert "llm_gateway.invoke(" not in source
        assert "llm_gateway.invoke_json(" not in source
        assert "llm_production.generate_json(" in source
