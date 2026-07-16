from __future__ import annotations

import asyncio
import json
from typing import Any

import pytest

from app.services.intelligence import market


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

    monkeypatch.setattr(market.asyncio, "to_thread", run_inline)


def test_regenerate_gap_insights_calls_strict_production_entry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    generated = {
        "insights": [
            {
                "type": "opportunity",
                "severity": "green",
                "title": "35mm opening",
                "body": "Prioritize the underserved focal segment.",
            }
        ]
    }
    calls: list[dict[str, Any]] = []
    conn = _RecordingConn()
    persisted = {"insights": [{"title": "35mm opening"}]}

    _run_threads_inline(monkeypatch)
    monkeypatch.setattr(
        market,
        "generate_text",
        lambda prompt, **kwargs: calls.append({"prompt": prompt, **kwargs})
        or {"status": "success", "text": json.dumps(generated)},
    )
    monkeypatch.setattr(market, "build_category_heatmap", lambda: {"segments": [{"focal": "35mm"}]})
    monkeypatch.setattr(market, "list_benchmarks", lambda: {"genres": [{"name": "review"}]})
    monkeypatch.setattr(
        market,
        "list_observations",
        lambda *, limit: {"observations": [{"event_title": "Competitor launch"}]},
    )
    monkeypatch.setattr(market, "get_conn", lambda: conn)
    monkeypatch.setattr(market, "list_gap_insights", lambda: persisted)

    result = asyncio.run(market.regenerate_gap_insights())

    assert result == persisted
    assert len(calls) == 1
    assert calls[0]["provider"] == "anthropic"
    assert calls[0]["model"] == "claude-opus-4-7"
    assert calls[0]["purpose"] == "intelligence_market"
    assert calls[0]["max_output_tokens"] == 2048
    assert "35mm opening" in str(conn.executions[1][1])
    assert conn.committed is True


@pytest.mark.parametrize(
    "response_text",
    [
        "not-json",
        json.dumps({"insights": ["not-an-insight-object"]}),
    ],
)
def test_regenerate_gap_insights_parse_failure_uses_fallback_without_writes(
    monkeypatch: pytest.MonkeyPatch,
    response_text: str,
) -> None:
    fallback = {"insights": [{"title": "Local heatmap fallback", "severity": "info"}]}
    fallback_calls = 0

    def local_fallback() -> dict[str, Any]:
        nonlocal fallback_calls
        fallback_calls += 1
        return fallback

    def reject_database_write() -> None:
        raise AssertionError("parse failures must not open the write connection")

    _run_threads_inline(monkeypatch)
    monkeypatch.setattr(
        market,
        "generate_text",
        lambda *_a, **_k: {"status": "success", "text": response_text},
    )
    monkeypatch.setattr(market, "build_category_heatmap", lambda: {"segments": []})
    monkeypatch.setattr(market, "list_benchmarks", lambda: {"genres": []})
    monkeypatch.setattr(market, "list_observations", lambda *, limit: {"observations": []})
    monkeypatch.setattr(market, "get_conn", reject_database_write)
    monkeypatch.setattr(market, "list_gap_insights", local_fallback)

    assert asyncio.run(market.regenerate_gap_insights()) == fallback
    assert fallback_calls == 1


def test_regenerate_gap_insights_production_entry_failure_uses_fallback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fallback = {"insights": [{"title": "Local heatmap fallback"}]}

    _run_threads_inline(monkeypatch)
    monkeypatch.setattr(
        market,
        "generate_text",
        lambda *_a, **_k: (_ for _ in ()).throw(RuntimeError("fixture unavailable")),
    )
    monkeypatch.setattr(market, "build_category_heatmap", lambda: {"segments": []})
    monkeypatch.setattr(market, "list_benchmarks", lambda: {"genres": []})
    monkeypatch.setattr(market, "list_observations", lambda *, limit: {"observations": []})
    monkeypatch.setattr(market, "list_gap_insights", lambda: fallback)

    assert asyncio.run(market.regenerate_gap_insights()) == fallback
