from __future__ import annotations

import json
from datetime import datetime, timezone
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
