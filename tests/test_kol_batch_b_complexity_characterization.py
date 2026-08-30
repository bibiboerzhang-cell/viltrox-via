"""Behavior locks for the Batch-B KOL complexity refactors."""
from __future__ import annotations

import ast
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from app.domains.kol import account_dossier, pool_enrich
from scripts.vkpi_engineering_health_collect import collect_complexity


ROOT = Path(__file__).resolve().parents[1]


class _Rows:
    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self.rows = rows

    def fetchone(self) -> dict[str, Any] | None:
        return self.rows[0] if self.rows else None

    def fetchall(self) -> list[dict[str, Any]]:
        return self.rows


def test_pool_enrich_preserves_main_commit_best_effort_failure_and_followup_order(monkeypatch: pytest.MonkeyPatch) -> None:
    events: list[str] = []
    item = {"id": 7, "platform": "youtube", "handle": "creator", "profile_url": "https://youtube.com/@creator"}

    class Conn:
        selects = 0

        def execute(self, sql: str, _params: Any = None) -> _Rows:
            if sql.lstrip().startswith("SELECT *"):
                self.selects += 1
                events.append(f"select:{self.selects}")
                return _Rows([item if self.selects == 1 else {**item, "followers": 1200}])
            events.append("main_update")
            return _Rows([])

        def commit(self) -> None:
            events.append("commit")

        def rollback(self) -> None:
            events.append("rollback")

    class Crawler:
        configured = True

        def crawl_channel_profile(self, *_args: Any, **_kwargs: Any) -> dict[str, Any]:
            events.append("profile")
            return {"provider_status": "ready", "sync_status": "synced", "items": [{"id": "UC7", "title": "Creator"}]}

        def crawl_channel_videos(self, *_args: Any, **_kwargs: Any) -> dict[str, Any]:
            events.append("videos")
            return {"items": [{"id": "v1", "viewCount": 100}]}

    scoring = SimpleNamespace(score=88, strengths=["fit"], concerns=[], breakdown={"fit": 88})
    monkeypatch.setattr(pool_enrich, "ensure_vkpi_product_industry_schema", lambda: events.append("schema"))
    monkeypatch.setattr(pool_enrich, "get_conn", lambda: events.append("conn") or Conn())
    monkeypatch.setattr(pool_enrich, "get_crawler", lambda platform: events.append(f"crawler:{platform}") or Crawler())
    monkeypatch.setattr(pool_enrich, "calculate_kpis", lambda _raw: events.append("kpis") or {"followers": 1200, "posts": 1})
    monkeypatch.setattr(pool_enrich.ScoringRegistry, "get", lambda _name: SimpleNamespace(score=lambda *_a, **_k: events.append("score") or scoring))
    monkeypatch.setattr(pool_enrich, "_stamp_enrich_avatar", lambda *_a, **_k: events.append("avatar"))
    monkeypatch.setattr(pool_enrich, "apply_raw_fields", lambda *_a, **_k: events.append("raw_fields") or (_ for _ in ()).throw(RuntimeError("optional")))
    monkeypatch.setattr(pool_enrich, "_derive_enrich_topic", lambda *_a, **_k: events.append("topic"))
    monkeypatch.setattr(pool_enrich, "_regate_enriched_item", lambda *_a, **_k: events.append("regate"))
    monkeypatch.setattr(pool_enrich, "_clear_kol_pool_read_cache", lambda: events.append("clear"))

    result = pool_enrich.enrich_item(7, max_posts=99)

    assert events == [
        "schema", "conn", "select:1", "crawler:youtube", "profile", "videos", "kpis", "score",
        "avatar", "main_update", "commit", "raw_fields", "rollback", "topic", "regate", "clear", "select:2",
    ]
    assert result == {
        "item": {**item, "followers": 1200}, "sync_status": "synced", "provider_status": "ready",
        "posts_sampled": 1, "score_breakdown": {"fit": 88},
    }


def test_account_dossier_preserves_read_order_limits_and_missing_row_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    events: list[str] = []

    class Conn:
        def execute(self, sql: str, params: Any = None) -> _Rows:
            if "SELECT * FROM vkpi_kol_pool" in sql:
                events.append("profile")
                return _Rows([{"id": 9, "handle": "creator", "profile_backfilled_at": "2026-01-01"}])
            if "FROM vkpi_kol_video_evidence" in sql:
                events.append(f"videos:{params[1]}")
                return _Rows([])
            events.append("crawls")
            return _Rows([])

    monkeypatch.setattr(account_dossier, "get_conn", lambda: events.append("conn") or Conn())
    monkeypatch.setattr(account_dossier, "_video_cache_maps", lambda kol_id: events.append(f"cache:{kol_id}") or ({}, {}))
    monkeypatch.setattr(
        account_dossier, "get_kol_llm_deep_analysis",
        lambda kol_id, *, limit: events.append(f"deep:{kol_id}:{limit}") or {"status": "empty", "items": [], "count": 0},
    )
    result = account_dossier.get_kol_account_dossier(9, video_limit=999, event_limit=0, deep_limit=999)

    assert events == ["conn", "profile", "videos:200", "cache:9", "crawls", "deep:9:50"]
    assert result["status"] == "ready"
    assert result["events"] == []
    assert result["gaps"] == [
        "profile_crawl_history_missing", "video_evidence_missing", "llm_deep_result_missing",
    ]
    assert result["diagnostics"]["write_db"] is False

    monkeypatch.setattr(account_dossier, "get_conn", lambda: SimpleNamespace(execute=lambda *_a, **_k: _Rows([])))
    with pytest.raises(LookupError, match="kol pool item not found"):
        account_dossier.get_kol_account_dossier(404)


def test_completed_batch_b_functions_stay_below_complexity_and_file_limits() -> None:
    expected = {"pool_enrich.py": "enrich_item", "account_dossier.py": "get_kol_account_dossier"}
    for filename, function_name in expected.items():
        path = ROOT / "backend/app/domains/kol" / filename
        source = path.read_text(encoding="utf-8")
        assert len(source.splitlines()) < 800
        rows = collect_complexity({str(path): ast.parse(source)})
        focal = next(row for row in rows if row.qualified_name == function_name)
        assert focal.cc <= 30
