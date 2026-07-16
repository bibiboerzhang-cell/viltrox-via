from __future__ import annotations

import asyncio
import json
from typing import Any

import pytest

from app.services.intelligence import brand


class _RecordingConn:
    def __init__(self) -> None:
        self.executions: list[tuple[str, Any]] = []
        self.committed = False

    def execute(self, query: str, params: Any = None) -> None:
        self.executions.append((query, params))

    def commit(self) -> None:
        self.committed = True


def _run_threads_inline(monkeypatch: pytest.MonkeyPatch) -> None:
    async def run_inline(fn: Any) -> Any:
        return fn()

    monkeypatch.setattr(brand.asyncio, "to_thread", run_inline)


def test_regenerate_insights_calls_strict_production_entry(monkeypatch: pytest.MonkeyPatch) -> None:
    generated = {
        "insights": [
            {
                "type": "engagement",
                "severity": "green",
                "title": "Strong launch response",
                "body": "Keep the current product-led format.",
            }
        ]
    }
    calls: list[dict[str, Any]] = []
    conn = _RecordingConn()
    persisted = {"insights": [{"title": "Strong launch response"}]}

    _run_threads_inline(monkeypatch)
    monkeypatch.setattr(
        brand,
        "generate_text",
        lambda prompt, **kwargs: calls.append({"prompt": prompt, **kwargs})
        or {"status": "success", "text": json.dumps(generated)},
    )
    monkeypatch.setattr(brand, "get_matrix", lambda: {"accounts": [{"handle": "viltrox"}]})
    monkeypatch.setattr(brand, "list_posts", lambda *, limit: {"posts": [{"title": "Launch"}]})
    monkeypatch.setattr(brand, "get_conn", lambda: conn)
    monkeypatch.setattr(brand, "list_insights", lambda: persisted)

    result = asyncio.run(brand.regenerate_insights())

    assert result == persisted
    assert len(calls) == 1
    assert calls[0]["provider"] == "anthropic"
    assert calls[0]["model"] == "claude-opus-4-7"
    assert calls[0]["purpose"] == "intelligence_brand"
    assert calls[0]["max_output_tokens"] == 2048
    assert "Strong launch response" in str(conn.executions[1][1])
    assert conn.committed is True


@pytest.mark.parametrize(
    "response_text",
    [
        "not-json",
        json.dumps({"insights": ["not-an-insight-object"]}),
    ],
)
def test_regenerate_insights_parse_failure_uses_fallback_without_writes(
    monkeypatch: pytest.MonkeyPatch,
    response_text: str,
) -> None:
    fallback = {"insights": [{"title": "Local matrix fallback", "severity": "info"}]}
    fallback_calls = 0

    def local_fallback() -> dict[str, Any]:
        nonlocal fallback_calls
        fallback_calls += 1
        return fallback

    def reject_database_write() -> None:
        raise AssertionError("parse failures must not open the write connection")

    _run_threads_inline(monkeypatch)
    monkeypatch.setattr(
        brand,
        "generate_text",
        lambda *_a, **_k: {"status": "success", "text": response_text},
    )
    monkeypatch.setattr(brand, "get_matrix", lambda: {"accounts": []})
    monkeypatch.setattr(brand, "list_posts", lambda *, limit: {"posts": []})
    monkeypatch.setattr(brand, "get_conn", reject_database_write)
    monkeypatch.setattr(brand, "list_insights", local_fallback)

    assert asyncio.run(brand.regenerate_insights()) == fallback
    assert fallback_calls == 1


def test_regenerate_insights_production_entry_failure_uses_fallback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fallback = {"insights": [{"title": "Local matrix fallback"}]}

    _run_threads_inline(monkeypatch)
    monkeypatch.setattr(
        brand,
        "generate_text",
        lambda *_a, **_k: (_ for _ in ()).throw(RuntimeError("fixture unavailable")),
    )
    monkeypatch.setattr(brand, "get_matrix", lambda: {"accounts": []})
    monkeypatch.setattr(brand, "list_posts", lambda *, limit: {"posts": []})
    monkeypatch.setattr(brand, "list_insights", lambda: fallback)

    assert asyncio.run(brand.regenerate_insights()) == fallback
