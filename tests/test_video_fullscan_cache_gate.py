from __future__ import annotations

import sqlite3
from typing import Any

from app.domains.kol import video_fullscan


def _conn() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(
        """
        CREATE TABLE vkpi_kol_pool (
            id INTEGER PRIMARY KEY, posts_count INTEGER, profile_url TEXT
        );
        CREATE TABLE vkpi_kol_video_evidence (
            id INTEGER PRIMARY KEY, kol_pool_id INTEGER, content_url TEXT,
            platform TEXT, video_title TEXT, posted_at TEXT, view_count INTEGER,
            like_count INTEGER, comment_count INTEGER, is_active INTEGER
        );
        CREATE TABLE vkpi_analysis_cache (
            id INTEGER PRIMARY KEY, target_type TEXT, target_id TEXT,
            derive_method TEXT, model TEXT, prompt_version TEXT, result TEXT,
            status TEXT, updated_at TEXT
        );
        CREATE TABLE vkpi_kol_llm_deep_analysis_results (
            id INTEGER PRIMARY KEY, source_evidence_id INTEGER,
            source_cache_id INTEGER, analysis_kind TEXT, status TEXT
        );
        INSERT INTO vkpi_kol_pool VALUES (9, 10, 'https://example.test/creator');
        INSERT INTO vkpi_kol_video_evidence VALUES
          (1, 9, 'https://example.test/1', 'youtube', 'canonical', '2026-08-01', 400, 1, 1, 1),
          (2, 9, 'https://example.test/2', 'youtube', 'legacy cache', '2026-08-02', 300, 1, 1, 1),
          (3, 9, 'https://example.test/3', 'youtube', 'orphan projection', '2026-08-03', 200, 1, 1, 1),
          (4, 9, 'https://example.test/4', 'youtube', 'missing', '2026-08-04', 100, 1, 1, 1);
        INSERT INTO vkpi_analysis_cache VALUES
          (101, 'video', '1', 'video_analysis_final_v1', 'model-a', 'prompt-a', '{}', 'ready', '2026-08-20'),
          (102, 'video', '2', 'video_analysis_final_v1', 'model-a', 'legacy', '{}', 'ready', '2026-08-20');
        INSERT INTO vkpi_kol_llm_deep_analysis_results VALUES
          (201, 3, 999, 'video_final_v1', 'ready');
        """
    )
    return conn


def _classify(row: dict[str, Any], **_kwargs: Any) -> dict[str, Any]:
    reusable = int(row["id"]) == 101
    return {
        "exists": True,
        "reusable": reusable,
        "cache_id": int(row["id"]),
        "cache_reuse_status": "canonical" if reusable else "legacy_unverified",
        "revalidation_required": not reusable,
        "claim_status": "descriptive_only",
        "reasons": [] if reusable else ["result_prompt_contract_mismatch"],
    }


def test_fullscan_separates_canonical_legacy_and_missing(monkeypatch) -> None:
    conn = _conn()
    monkeypatch.setattr(video_fullscan, "get_conn", lambda: conn)
    monkeypatch.setattr(video_fullscan, "canonical_final_v1_cache_reuse", _classify)

    plan = video_fullscan.plan_kol_video_fullscan(9, top_n=5)

    assert plan["status"] == plan["state"] == "partial"
    assert plan["effective_status"] == "legacy_unverified"
    assert plan["terminal"] is True
    assert plan["revalidation_required"] is True
    assert plan["canonical_analyzed_count"] == plan["analyzed_count"] == 1
    assert plan["legacy_unverified_count"] == 2
    assert {item["evidence_id"] for item in plan["legacy_unverified"]} == {2, 3}
    assert plan["pending_count"] == 1
    assert [item["evidence_id"] for item in plan["top_candidates"]] == [4]


def test_fullscan_enqueue_never_includes_legacy_candidates(monkeypatch) -> None:
    from app.domains.kol import video_analysis_enqueue

    conn = _conn()
    captured: dict[str, Any] = {}
    monkeypatch.setattr(video_fullscan, "get_conn", lambda: conn)
    monkeypatch.setattr(video_fullscan, "canonical_final_v1_cache_reuse", _classify)
    monkeypatch.setattr(
        video_analysis_enqueue,
        "enqueue_final_v1_video_analysis_batch",
        lambda **kwargs: captured.update(kwargs)
        or {"status": "queued", "queued": 1, "write_db": True, "writes": ["apify_jobs"]},
    )

    result = video_fullscan.enqueue_kol_video_fullscan(9, top_n=5, staff={"id": 1})

    assert captured["items"] == [{"kol_pool_id": 9, "evidence_id": 4}]
    assert result["status"] == result["state"] == "partial"
    assert result["effective_status"] == "legacy_unverified"
    assert result["revalidation_required"] is True
    assert result["queued"] == 1
    assert result["evidence_ids"] == [4]


def test_fullscan_enqueue_reports_terminal_batch_cache_race(monkeypatch) -> None:
    from app.domains.kol import video_analysis_enqueue

    monkeypatch.setattr(
        video_fullscan,
        "plan_kol_video_fullscan",
        lambda *_args, **_kwargs: {
            "kol_pool_id": 9,
            "top_candidates": [{"evidence_id": 4}],
            "legacy_unverified_count": 0,
        },
    )
    monkeypatch.setattr(
        video_analysis_enqueue,
        "enqueue_final_v1_video_analysis_batch",
        lambda **_kwargs: {
            "status": "completed",
            "state": "completed",
            "terminal": True,
            "queued": 0,
            "write_db": False,
            "writes": [],
        },
    )

    result = video_fullscan.enqueue_kol_video_fullscan(9, top_n=1, staff={"id": 1})

    assert result["status"] == result["state"] == "completed"
    assert result["effective_status"] == "completed"
    assert result["terminal"] is True
    assert result["queued"] == 0
    assert result["write_db"] is False
