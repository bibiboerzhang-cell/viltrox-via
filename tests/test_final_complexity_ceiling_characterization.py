"""Behavior locks for the final five complexity-ceiling extractions."""
from __future__ import annotations

import ast
import asyncio
import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from app.platform.llm_production_anthropic_helpers import (
    anthropic_preflight_candidate,
    anthropic_provider_gate_reason,
)
from app.services.intelligence import market
from app.services.via import shadow_learning
from app.workers.tasks import analyze
from scripts.vkpi_engineering_health_collect import collect_complexity


class _Cursor:
    def __init__(self, rows: list[Any]) -> None:
        self._rows = rows

    def fetchall(self) -> list[Any]:
        return self._rows


class _MarketConn:
    def __init__(self, *, legacy: bool, rows: list[dict[str, Any]]) -> None:
        self.legacy = legacy
        self.rows = rows
        self.calls: list[tuple[str, Any]] = []

    def execute(self, sql: str, params: Any = None) -> _Cursor:
        self.calls.append((sql, params))
        if "PRAGMA table_info" in sql:
            name = "observation_type" if self.legacy else "event_kind"
            return _Cursor([{"name": name}])
        return _Cursor(self.rows)


def test_market_legacy_projection_preserves_query_and_json_contract(monkeypatch) -> None:
    conn = _MarketConn(
        legacy=True,
        rows=[
            {
                "id": 8,
                "observed_at": "2026-08-31T12:00:00Z",
                "source_platform": "youtube",
                "observation_type": "launch",
                "summary": "New lens",
                "subject_type": "product",
                "subject_key": "AF-TEST",
                "metrics_json": json.dumps({"views": 14}),
                "evidence_json": json.dumps([{"url": "https://example.test/e"}]),
                "region_code": "US",
            }
        ],
    )
    monkeypatch.setattr(market, "get_conn", lambda: conn)

    result = market.list_observations(
        kind="launch",
        impact="positive",
        from_date="2026-08-01",
        to_date="2026-08-31",
        limit=7,
    )

    assert result == {
        "observations": [
            {
                "id": 8,
                "observed_at": "2026-08-31T12:00:00Z",
                "event_kind": "launch",
                "event_title": "New lens",
                "impact": "positive",
                "source_url": "",
                "notes": "New lens",
                "source_platform": "youtube",
                "subject_type": "product",
                "subject_key": "AF-TEST",
                "metrics": {"views": 14},
                "evidence": [{"url": "https://example.test/e"}],
                "region_code": "US",
            }
        ]
    }
    sql, params = conn.calls[1]
    assert "observation_type = ?" in sql
    assert "impact = ?" not in sql
    assert params == ["launch", "2026-08-01", "2026-08-31", 7]


def test_market_modern_empty_rows_keep_bh_fallback_filters(monkeypatch) -> None:
    conn = _MarketConn(legacy=False, rows=[])
    monkeypatch.setattr(market, "get_conn", lambda: conn)
    monkeypatch.setattr(
        market,
        "_latest_bh_rows",
        lambda: [
            {
                "sku": "V-1",
                "title": "Viltrox 35mm",
                "snapshot_at": "2026-08-31T11:00:00Z",
                "url": "https://example.test/v",
                "rating": 4.8,
                "review_count": 12,
            },
            {
                "sku": "S-1",
                "title": "Sigma 35mm",
                "snapshot_at": "2026-08-31T11:00:00Z",
                "url": "https://example.test/s",
                "rating": 4.7,
                "review_count": 20,
            },
        ],
    )

    result = market.list_observations(kind="review", impact="positive", limit=5)

    assert [item["id"] for item in result["observations"]] == ["bh-V-1"]
    assert result["observations"][0]["impact"] == "positive"
    assert conn.calls[1][1] == ["review", "positive", 5]


def test_shadow_model_choice_keeps_preview_monkeypatch_and_route_order(monkeypatch) -> None:
    calls: list[dict[str, Any]] = []

    def preview(task: str, **kwargs: Any) -> list[dict[str, Any]]:
        calls.append({"task": task, **kwargs})
        return [
            {"provider": "anthropic", "model": "claude-shadow"},
            {"provider": "google", "model": "gemini-shadow"},
        ]

    monkeypatch.setattr(shadow_learning, "preview_via_routes", preview)
    result = shadow_learning.evaluate_shadow_model_choice(
        route_info={"use_deep_reasoning": False},
        live_policy={"policy_key": "via", "policy_version": "live-1"},
        shadow_policy={
            "providers": [" Anthropic ", "", "GOOGLE"],
            "execution_mode": "collab_preferred",
            "policy_version": "shadow-2",
            "staged_version_key": "candidate-a",
        },
        model_plan={
            "dialogue": {
                "mode": "single",
                "primary_provider": "google",
                "primary_model": "gemini-live",
            }
        },
    )

    assert calls == [
        {
            "task": "dialogue",
            "preferred_override": ["anthropic", "google"],
            "limit": 2,
        }
    ]
    assert result["shadow_primary_provider"] == "anthropic"
    assert result["shadow_primary_model"] == "claude-shadow"
    assert result["shadow_strategy"] == "collab"
    assert result["shadow_routes"] == [
        {"provider": "anthropic", "model": "claude-shadow"},
        {"provider": "google", "model": "gemini-shadow"},
    ]
    assert result["would_change"] is True


def test_shadow_model_choice_empty_policy_short_circuits_preview(monkeypatch) -> None:
    monkeypatch.setattr(
        shadow_learning,
        "preview_via_routes",
        lambda *_a, **_k: pytest.fail("empty policy must not preview routes"),
    )

    assert shadow_learning.evaluate_shadow_model_choice(
        route_info=None,
        live_policy=None,
        shadow_policy=None,
        model_plan=None,
    ) == {}


def _audit_job(**overrides: Any) -> SimpleNamespace:
    values = {
        "submission_id": 71,
        "url": "https://example.test/video",
        "title": "Creator review",
        "handle": "creator",
        "platform": "youtube",
        "caption": "caption",
        "scraped_text": "body",
        "og_image": "",
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def test_full_audit_ai_override_preserves_first_product_and_write_order(monkeypatch) -> None:
    events: list[Any] = []
    saved: list[tuple[int, dict[str, Any]]] = []

    async def scrape(_url: str) -> dict[str, Any]:
        events.append("scrape")
        return {
            "scraped_ok": True,
            "title": "scraped title",
            "caption": "scraped caption",
            "scraped_text": "scraped body",
            "metrics": {"views": 100, "likes": 10, "comments": 2, "shares": 1, "favorites": 3},
            "metrics_available": {"views": True, "likes": True, "comments": True, "shares": True, "favorites": True},
            "visible_comments": ["ok"],
            "og_image": "thumb",
        }

    async def analyze_url(**_kwargs: Any) -> dict[str, Any]:
        events.append("analyze")
        return {
            "analyzed": True,
            "viltrox_detected": True,
            "confidence": "high",
            "brand_elements": ["Viltrox"],
            "products_detected": ["unknown", "AF 35mm"],
            "brand_score_bonus": 5,
            "content_genre": "review",
            "tech_score": 80,
            "marketing_score": 70,
        }

    classify_calls: list[str] = []

    def classify(text: str) -> dict[str, Any]:
        classify_calls.append(text)
        if text == "AF 35mm":
            return {"confidence": "exact", "sku": "AF-35"}
        return {"confidence": "none"}

    monkeypatch.setattr(analyze, "logger", SimpleNamespace(exception=lambda *_a, **_k: None, info=lambda *_a, **_k: None))
    monkeypatch.setattr(analyze, "ANTHROPIC_AVAILABLE", True)
    monkeypatch.setattr(analyze, "valid_url", lambda _url: True)
    monkeypatch.setattr(analyze, "mark_submission_running", lambda sid: events.append(("running", sid)))
    monkeypatch.setattr(analyze, "scrape_url", scrape)
    monkeypatch.setattr(analyze, "analyze_url_content_smart", analyze_url)
    monkeypatch.setattr(analyze, "classify_product", classify)
    monkeypatch.setattr(analyze, "detect_gear_mentions", lambda _text: events.append("gear") or [])
    monkeypatch.setattr(
        analyze,
        "detect_viltrox",
        lambda _text, _context: events.append("brand")
        or {"status": "none", "confirmed": False, "evidence": ["base"], "content_types": []},
    )
    monkeypatch.setattr(analyze, "analyze_comments_for_spam", lambda _rows: events.append("comments") or {})
    monkeypatch.setattr(analyze, "compute_risk", lambda *_a: events.append("risk") or {"penalty": 0, "risk_score": 0})
    monkeypatch.setattr(analyze, "compute_creator_score", lambda *_a: events.append("creator_score") or 50)
    monkeypatch.setattr(
        analyze,
        "compute_campaign_score",
        lambda **_kwargs: events.append("campaign_score")
        or {"content_score": 30, "campaign_interaction_score": 20, "raw_score": 100},
    )
    monkeypatch.setattr(analyze, "update_creator_profile", lambda *_a: events.append("profile"))
    monkeypatch.setattr(
        analyze,
        "update_genre_benchmark",
        lambda *_a: events.append("benchmark") or {"percentile_tech": 90, "percentile_mkt": 80},
    )
    monkeypatch.setattr(
        analyze,
        "finalize_submission",
        lambda sid, result: events.append("finalize") or saved.append((sid, result)),
    )

    result = asyncio.run(analyze.process_full_audit(_audit_job()))

    assert classify_calls[1:] == ["unknown", "AF 35mm"]
    assert result["product_match"] == {"confidence": "exact", "sku": "AF-35"}
    assert result["detection_status"] == "confirmed"
    assert set(result["evidence"]) == {"base", "Viltrox"}
    assert result["scores"]["final_score"] == 105
    assert saved == [(71, result)]
    assert events == [
        ("running", 71),
        "scrape",
        "analyze",
        "gear",
        "brand",
        "comments",
        "risk",
        "creator_score",
        "campaign_score",
        "profile",
        "benchmark",
        "finalize",
    ]


def test_full_audit_preserves_running_failure_tolerance_and_terminal_failure(monkeypatch) -> None:
    failures: list[tuple[int, str]] = []
    monkeypatch.setattr(analyze, "logger", SimpleNamespace(exception=lambda *_a, **_k: None, info=lambda *_a, **_k: None))
    monkeypatch.setattr(
        analyze,
        "mark_submission_running",
        lambda _sid: (_ for _ in ()).throw(RuntimeError("running write unavailable")),
    )
    monkeypatch.setattr(
        analyze,
        "classify_product",
        lambda _text: (_ for _ in ()).throw(ValueError("invalid classifier state")),
    )
    monkeypatch.setattr(analyze, "mark_submission_failed", lambda sid, error: failures.append((sid, error)))
    monkeypatch.setattr(
        analyze,
        "finalize_submission",
        lambda *_a: pytest.fail("failed analysis must not finalize"),
    )

    with pytest.raises(ValueError, match="invalid classifier state"):
        asyncio.run(analyze.process_full_audit(_audit_job(url="")))

    assert failures[0][0] == 71
    assert "ValueError: invalid classifier state" in failures[0][1]


def test_anthropic_preflight_helpers_preserve_first_match_and_reason_precedence() -> None:
    preflight = {
        "provider_gate_detail": "detail",
        "provider_gate_reason": "global",
        "providers": [
            {"binding": "anthropic/other", "binding_gate_reason": "other"},
            {"binding": "anthropic/exact", "binding_gate_reason": "exact-first"},
            {"binding": "anthropic/exact", "binding_gate_reason": "exact-second"},
        ],
    }
    candidate = anthropic_preflight_candidate(preflight, "anthropic/exact")

    assert candidate is preflight["providers"][1]
    assert anthropic_provider_gate_reason(candidate, preflight) == "exact-first"
    assert anthropic_provider_gate_reason({}, preflight) == "detail"
    assert anthropic_provider_gate_reason({}, {}) == "provider_calls_blocked"


def test_final_five_entrypoints_and_helpers_stay_within_static_ceilings() -> None:
    root = Path(__file__).resolve().parents[1]
    relative_paths = [
        "backend/app/domains/recommendations/feature_store.py",
        "backend/app/domains/recommendations/feature_store_history.py",
        "backend/app/platform/llm_production_anthropic.py",
        "backend/app/platform/llm_production_anthropic_helpers.py",
        "backend/app/services/intelligence/market.py",
        "backend/app/services/intelligence/market_observation_projection.py",
        "backend/app/services/via/shadow_learning.py",
        "backend/app/services/via/shadow_learning_model_choice.py",
        "backend/app/workers/tasks/analyze.py",
        "backend/app/workers/tasks/analyze_detection.py",
    ]
    trees = {
        path: ast.parse((root / path).read_text(encoding="utf-8"), filename=path)
        for path in relative_paths
    }
    rows = collect_complexity(trees)
    cc = {(row.path, row.qualified_name): row.cc for row in rows}
    targets = {
        ("backend/app/domains/recommendations/feature_store.py", "get_features_at_time"),
        ("backend/app/platform/llm_production_anthropic.py", "generate_anthropic_messages"),
        ("backend/app/services/intelligence/market.py", "list_observations"),
        ("backend/app/services/via/shadow_learning.py", "evaluate_shadow_model_choice"),
        ("backend/app/workers/tasks/analyze.py", "process_full_audit"),
    }
    helper_paths = {
        "backend/app/domains/recommendations/feature_store_history.py",
        "backend/app/platform/llm_production_anthropic_helpers.py",
        "backend/app/services/intelligence/market_observation_projection.py",
        "backend/app/services/via/shadow_learning_model_choice.py",
        "backend/app/workers/tasks/analyze_detection.py",
    }

    assert all(cc[target] <= 40 for target in targets)
    assert all(row.cc <= 40 for row in rows if row.path in helper_paths)
    assert all(len((root / path).read_text(encoding="utf-8").splitlines()) < 700 for path in helper_paths)
