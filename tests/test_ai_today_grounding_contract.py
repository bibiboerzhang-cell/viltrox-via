from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from app.domains.market import ai_today


def _grounding_source(url: str = "https://example.com/current") -> dict[str, str]:
    return {
        "title": "Grounded source",
        "url": url,
        "provider": "google_search",
        "relation_type": "grounding",
    }


def test_extract_grounding_sources_supports_current_sdk_objects() -> None:
    response = SimpleNamespace(
        candidates=[
            SimpleNamespace(
                grounding_metadata=SimpleNamespace(
                    grounding_chunks=[
                        SimpleNamespace(web=SimpleNamespace(uri="https://example.com/current", title="Current SDK"))
                    ]
                )
            )
        ]
    )

    assert ai_today._extract_grounding_sources(response) == [
        {
            "title": "Current SDK",
            "url": "https://example.com/current",
            "provider": "google_search",
            "relation_type": "grounding",
        }
    ]


def test_extract_grounding_sources_supports_legacy_wrapped_response() -> None:
    response = SimpleNamespace(
        _result={
            "candidates": [
                {
                    "groundingAttributions": [
                        {"source": {"url": "https://example.org/legacy", "title": "Legacy SDK"}}
                    ]
                }
            ]
        }
    )

    assert ai_today._extract_grounding_sources(response) == [
        {
            "title": "Legacy SDK",
            "url": "https://example.org/legacy",
            "provider": "google_search",
            "relation_type": "grounding",
        }
    ]


def test_extract_grounding_sources_does_not_borrow_from_later_candidate() -> None:
    response = {
        "candidates": [
            {"groundingMetadata": {"groundingChunks": []}},
            {
                "groundingMetadata": {
                    "groundingChunks": [
                        {"web": {"uri": "https://example.com/wrong-candidate", "title": "Wrong candidate"}}
                    ]
                }
            },
        ]
    }

    assert ai_today._extract_grounding_sources(response) == []


def test_claude_fallback_is_explicitly_ungrounded_and_does_not_write_latest(monkeypatch) -> None:
    monkeypatch.setattr(ai_today.budget_guard, "check_budget", lambda *_args, **_kwargs: True)
    monkeypatch.setattr(ai_today.budget_guard, "record_cost", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(ai_today, "_read_hot_brands", lambda: [])
    monkeypatch.setattr(
        ai_today,
        "_generate",
        lambda _prompt: (
            json.dumps({"headline": "Fallback", "shooting_plans": ["Plan"], "hot_topics": ["Topic"]}),
            "claude:test-model",
            [],
        ),
    )
    monkeypatch.setattr(
        ai_today,
        "_ensure_schema",
        lambda: (_ for _ in ()).throw(AssertionError("ungrounded fallback must not write latest")),
    )

    result = ai_today.generate_ai_today_hot()

    assert result["status"] == "ungrounded"
    assert result["grounding_status"] == "ungrounded"
    assert result["reason"] == "claude_fallback_without_grounding"
    assert result["sources"] == []


class _RowsResult:
    def __init__(self, rows: list[dict[str, object]]) -> None:
        self._rows = rows

    def fetchall(self) -> list[dict[str, object]]:
        return self._rows


class _ReadConn:
    def __init__(self, rows: list[dict[str, object]]) -> None:
        self._rows = rows

    def execute(self, _query: str) -> _RowsResult:
        return _RowsResult(self._rows)


class _WriteConn:
    def __init__(self) -> None:
        self.params: tuple[object, ...] | None = None
        self.committed = False

    def execute(self, _query: str, params: tuple[object, ...]) -> None:
        self.params = params

    def commit(self) -> None:
        self.committed = True


def test_grounded_generation_persists_provenance_metadata(monkeypatch) -> None:
    source = _grounding_source()
    conn = _WriteConn()
    monkeypatch.setattr(ai_today.budget_guard, "check_budget", lambda *_args, **_kwargs: True)
    monkeypatch.setattr(ai_today.budget_guard, "record_cost", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(ai_today, "_read_hot_brands", lambda: ["Sony"])
    monkeypatch.setattr(
        ai_today,
        "_generate",
        lambda _prompt: (
            json.dumps({"headline": "Grounded", "shooting_plans": ["Plan"], "hot_topics": ["Topic"]}),
            "gemini:test+google_search",
            [source],
        ),
    )
    monkeypatch.setattr(ai_today, "_ensure_schema", lambda: None)
    monkeypatch.setattr(ai_today, "get_conn", lambda: conn)

    result = ai_today.generate_ai_today_hot()

    assert result["status"] == "ok"
    assert conn.committed is True
    assert conn.params is not None
    payload = json.loads(str(conn.params[0]))
    assert payload["sources"] == [source]
    assert payload["grounding_status"] == "grounded"
    assert payload["status"] == "ready"
    assert payload["evidence"] == [source]
    assert payload["generated_at"].endswith("Z")


def test_latest_response_suppresses_claim_when_no_grounded_citation(monkeypatch) -> None:
    generated_at = datetime.now(tz=timezone.utc).isoformat()
    rows = [
        {
            "snapshot_date": "2026-07-10",
            "content_json": json.dumps(
                {
                    "headline": "Unsupported latest claim",
                    "shooting_plans": ["Plan"],
                    "sources": [],
                    "grounding_status": "ungrounded",
                    "generated_at": generated_at,
                }
            ),
            "model": "claude:test-model",
            "created_at": generated_at,
        }
    ]
    monkeypatch.setattr(ai_today, "_ensure_schema", lambda: None)
    monkeypatch.setattr(ai_today, "get_conn", lambda: _ReadConn(rows))

    result = ai_today.get_ai_today_hot()

    assert result["available"] is False
    assert result["reason"] == "no_grounded_latest"
    assert result["grounding_status"] == "ungrounded"
    assert result["sources"] == []
    assert "headline" not in result["content"]


def test_latest_response_exposes_grounding_and_freshness_compatibly(monkeypatch) -> None:
    generated_at = datetime.now(tz=timezone.utc).isoformat()
    source = _grounding_source()
    rows = [
        {
            "snapshot_date": "2026-07-10",
            "content_json": json.dumps(
                {
                    "headline": "Grounded latest claim",
                    "shooting_plans": ["Plan"],
                    "hot_topics": ["Topic"],
                    "sources": [source],
                    "grounding_status": "grounded",
                    "generated_at": generated_at,
                }
            ),
            "model": "gemini:test+google_search",
            "created_at": generated_at,
        }
    ]
    monkeypatch.setattr(ai_today, "_ensure_schema", lambda: None)
    monkeypatch.setattr(ai_today, "get_conn", lambda: _ReadConn(rows))
    monkeypatch.setattr(ai_today, "_market_sources", lambda *_args, **_kwargs: [])
    monkeypatch.setattr(ai_today, "_recommended_video_rows", lambda: [])

    result = ai_today.get_ai_today_hot()

    assert result["available"] is True
    assert result["grounding_status"] == "grounded"
    assert result["sources"] == [source]
    assert result["generated_at"].endswith("Z")
    assert result["freshness_status"] == "fresh"
    assert result["content"]["grounding_status"] == "grounded"
    assert result["content"]["sources"] == [source]
    assert result["content"]["freshness_status"] == "fresh"


def test_legacy_bare_source_is_grounding_but_brand_context_is_not() -> None:
    assert ai_today._stored_grounding_sources([{"url": "https://example.com/legacy"}]) == [
        {"url": "https://example.com/legacy", "relation_type": "grounding"}
    ]
    assert ai_today._stored_grounding_sources(
        [{"url": "https://example.com/context", "relation_type": "brand_context"}]
    ) == []


def test_generate_rejects_string_shooting_plans_without_character_coercion(monkeypatch) -> None:
    monkeypatch.setattr(ai_today.budget_guard, "check_budget", lambda *_args, **_kwargs: True)
    monkeypatch.setattr(ai_today.budget_guard, "record_cost", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(ai_today, "_read_hot_brands", lambda: [])
    monkeypatch.setattr(
        ai_today,
        "_generate",
        lambda _prompt: (
            json.dumps(
                {
                    "headline": "Typed contract",
                    "shooting_plans": "must-not-split",
                    "hot_topics": ["Topic"],
                }
            ),
            "gemini:test+google_search",
            [_grounding_source()],
        ),
    )
    monkeypatch.setattr(
        ai_today,
        "_ensure_schema",
        lambda: (_ for _ in ()).throw(AssertionError("invalid result must not write latest")),
    )

    result = ai_today.generate_ai_today_hot()

    assert result["status"] == "invalid"
    assert result["reason"] == "invalid_result_contract"
    assert "shooting_plans:expected_list" in result["validation_errors"]


def test_partial_ai_today_result_is_degraded_and_not_persisted(monkeypatch) -> None:
    monkeypatch.setattr(ai_today.budget_guard, "check_budget", lambda *_args, **_kwargs: True)
    monkeypatch.setattr(ai_today.budget_guard, "record_cost", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(ai_today, "_read_hot_brands", lambda: [])
    monkeypatch.setattr(
        ai_today,
        "_generate",
        lambda _prompt: (
            json.dumps({"headline": "Partial", "shooting_plans": ["Plan"]}),
            "gemini:test+google_search",
            [_grounding_source()],
        ),
    )
    monkeypatch.setattr(
        ai_today,
        "_ensure_schema",
        lambda: (_ for _ in ()).throw(AssertionError("partial result must not write latest")),
    )

    result = ai_today.generate_ai_today_hot()

    assert result["status"] == "degraded"
    assert result["result_status"] == "degraded"
    assert result["reason"] == "partial_result_contract"
    assert "hot_topics:missing" in result["validation_errors"]


def test_stale_grounded_ai_today_cannot_report_ready(monkeypatch) -> None:
    generated_at = (datetime.now(tz=timezone.utc) - timedelta(hours=48)).isoformat()
    source = _grounding_source()
    rows = [
        {
            "snapshot_date": generated_at[:10],
            "content_json": json.dumps(
                {
                    "headline": "Stale but grounded",
                    "shooting_plans": ["Plan"],
                    "hot_topics": ["Topic"],
                    "sources": [source],
                    "generated_at": generated_at,
                }
            ),
            "model": "gemini:test+google_search",
            "created_at": generated_at,
        }
    ]
    monkeypatch.setattr(ai_today, "_ensure_schema", lambda: None)
    monkeypatch.setattr(ai_today, "get_conn", lambda: _ReadConn(rows))
    monkeypatch.setattr(ai_today, "_market_sources", lambda *_args, **_kwargs: [])
    monkeypatch.setattr(ai_today, "_recommended_video_rows", lambda: [])

    result = ai_today.get_ai_today_hot()

    assert result["available"] is True
    assert result["freshness_status"] == "stale"
    assert result["status"] == "degraded"
    assert result["is_ready"] is False
    assert result["content"]["status"] == "degraded"


def test_direct_gemini_call_has_sdk_timeout_and_only_transient_retry(monkeypatch) -> None:
    import app.services.ai.clients.gemini_client as gemini_module

    calls: list[object] = []

    class _Models:
        def generate_content(self, *, model, contents, config):
            calls.append(config)
            if len(calls) == 1:
                raise RuntimeError("503 unavailable")
            return SimpleNamespace(
                text=json.dumps(
                    {
                        "headline": "Bounded call",
                        "shooting_plans": ["Plan"],
                        "hot_topics": ["Topic"],
                    }
                ),
                candidates=[
                    SimpleNamespace(
                        grounding_metadata=SimpleNamespace(
                            grounding_chunks=[
                                SimpleNamespace(
                                    web=SimpleNamespace(
                                        uri="https://example.com/bounded",
                                        title="Bounded source",
                                    )
                                )
                            ]
                        )
                    )
                ],
            )

    monkeypatch.setattr(gemini_module, "gemini_client", SimpleNamespace(models=_Models()))
    monkeypatch.setattr(ai_today.time, "sleep", lambda _seconds: None)

    raw, model, sources, provenance = ai_today._generate(
        "return JSON",
        validator=ai_today._validate_ai_today_content,
    )

    assert json.loads(raw)["headline"] == "Bounded call"
    assert model.startswith("gemini:")
    assert sources[0]["url"] == "https://example.com/bounded"
    assert len(calls) == 2
    for config in calls:
        assert 0 < config.http_options.timeout <= int(ai_today._PROVIDER_TIMEOUT_SECONDS * 1000)
        assert config.http_options.retry_options.attempts == 1
    assert [attempt["status"] for attempt in provenance["attempts"]] == ["transient_error", "success"]
    assert provenance["deadline_seconds"] == ai_today._GENERATION_DEADLINE_SECONDS


def test_video_evidence_requires_a_real_list_and_public_url() -> None:
    assert ai_today._validate_video_evidence("https://example.com/video")["status"] == "invalid"
    invalid_url = ai_today._validate_video_evidence(
        [{"title": "bad", "source_url": "javascript:alert(1)"}]
    )
    assert invalid_url["status"] == "invalid"
    assert ai_today._public_http_url("http://127.0.0.1/internal") == ""
    assert ai_today._public_http_url("http://localhost/internal") == ""


def test_fallback_to_older_valid_row_is_degraded_when_newer_row_is_invalid(monkeypatch) -> None:
    now = datetime.now(tz=timezone.utc)
    source = _grounding_source()
    rows = [
        {
            "snapshot_date": now.date().isoformat(),
            "content_json": json.dumps(
                {
                    "headline": "New but invalid",
                    "shooting_plans": "not-a-list",
                    "hot_topics": ["Topic"],
                    "sources": [source],
                    "generated_at": now.isoformat(),
                }
            ),
            "model": "gemini:new",
            "created_at": now.isoformat(),
        },
        {
            "snapshot_date": (now - timedelta(days=1)).date().isoformat(),
            "content_json": json.dumps(
                {
                    "headline": "Older valid row",
                    "shooting_plans": ["Plan"],
                    "hot_topics": ["Topic"],
                    "sources": [source],
                    "generated_at": (now - timedelta(hours=1)).isoformat(),
                }
            ),
            "model": "gemini:older",
            "created_at": (now - timedelta(hours=1)).isoformat(),
        },
    ]
    monkeypatch.setattr(ai_today, "_ensure_schema", lambda: None)
    monkeypatch.setattr(ai_today, "get_conn", lambda: _ReadConn(rows))
    monkeypatch.setattr(ai_today, "_market_sources", lambda *_args, **_kwargs: [])
    monkeypatch.setattr(ai_today, "_recommended_video_rows", lambda: [])

    result = ai_today.get_ai_today_hot()

    assert result["available"] is True
    assert result["freshness_status"] == "fresh"
    assert result["status"] == "degraded"
    assert result["is_ready"] is False
    assert "newer_rows_rejected" in result["content"]["validation_errors"]


def test_source_string_is_invalid_not_a_character_list(monkeypatch) -> None:
    monkeypatch.setattr(ai_today.budget_guard, "check_budget", lambda *_args, **_kwargs: True)
    monkeypatch.setattr(ai_today.budget_guard, "record_cost", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(ai_today, "_read_hot_brands", lambda: [])
    monkeypatch.setattr(
        ai_today,
        "_generate",
        lambda _prompt: (
            json.dumps(
                {
                    "headline": "Valid content",
                    "shooting_plans": ["Plan"],
                    "hot_topics": ["Topic"],
                }
            ),
            "gemini:test+google_search",
            "https://example.com/not-a-list",
        ),
    )
    monkeypatch.setattr(
        ai_today,
        "_ensure_schema",
        lambda: (_ for _ in ()).throw(AssertionError("invalid sources must not write latest")),
    )

    result = ai_today.generate_ai_today_hot()

    assert result["status"] == "ungrounded"
    assert result["result_status"] == "invalid"
    assert result["reason"] == "invalid_grounding_contract"
    assert result["validation_errors"] == ["sources:expected_list"]
