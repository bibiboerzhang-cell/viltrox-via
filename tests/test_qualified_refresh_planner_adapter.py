from __future__ import annotations

from typing import Any

from app.domains.sync import qualified_refresh_planner_adapter


def test_adapter_forwards_the_complete_read_only_plan_contract(monkeypatch) -> None:
    calls: list[dict[str, Any]] = []

    def fake_plan(**kwargs: Any) -> dict[str, Any]:
        calls.append(kwargs)
        return {"mode": "plan_only", "execution_enabled": False}

    monkeypatch.setattr(
        qualified_refresh_planner_adapter.apify_batch_refresh,
        "qualified_apify_batch_plan",
        fake_plan,
    )
    result = qualified_refresh_planner_adapter.ApifyQualifiedRefreshPlanner().plan(
        limit=200,
        offset=7,
        stale_before="2026-08-01T00:00:00Z",
        stale_days=1,
        platforms={"youtube"},
        tiers={"hot"},
        max_posts=1,
        max_concurrent=2,
        chunk_overrides={"youtube": 25},
    )

    assert result == {"mode": "plan_only", "execution_enabled": False}
    assert calls == [
        {
            "limit": 200,
            "offset": 7,
            "stale_before": "2026-08-01T00:00:00Z",
            "stale_days": 1,
            "platforms": {"youtube"},
            "tiers": {"hot"},
            "max_posts": 1,
            "max_concurrent": 2,
            "chunk_overrides": {"youtube": 25},
        }
    ]
