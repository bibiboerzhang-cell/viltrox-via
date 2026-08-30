from __future__ import annotations

import asyncio
import sqlite3
from pathlib import Path

import pytest
from fastapi import HTTPException

from app.services.kol import ops_persistence
from app.services.kol.platform_search_workflow import execute_platform_search


def _excluded_region(*values: object) -> bool:
    return any(str(value or "").strip().upper() in {"CN", "HK", "TW"} for value in values)


def test_zero_provider_validation_paths_are_stable() -> None:
    calls: list[str] = []

    async def search(*_args, **_kwargs):
        calls.append("provider")
        return {}

    common = {
        "staff": {"id": 7},
        "search_content": search,
        "country_in_excluded_region": _excluded_region,
    }

    with pytest.raises(HTTPException) as missing:
        asyncio.run(execute_platform_search({}, **common))
    assert missing.value.status_code == 400
    assert missing.value.detail == "query is required"

    unsupported = asyncio.run(
        execute_platform_search(
            {"query": "camera", "platform": "douyin"},
            **common,
        )
    )
    assert unsupported == {
        "status": "unsupported_platform",
        "items": [],
        "candidate_ids": [],
        "saved_candidates": 0,
        "message": "douyin is not a supported discovery platform",
        "platform": "douyin",
    }

    excluded = asyncio.run(
        execute_platform_search(
            {"query": "camera", "platform": "youtube", "market": "cn"},
            **common,
        )
    )
    assert excluded["status"] == "excluded_region"
    assert excluded["market"] == "CN"
    assert excluded["candidate_ids"] == []
    assert calls == []


def test_success_preserves_order_filtering_persistence_and_audit() -> None:
    events: list[tuple] = []

    async def search(platform, query, *, market, max_results):
        events.append(("provider", platform, query, market, max_results))
        return {
            "status": "done",
            "provider": "fixture-provider",
            "items": [
                {"channel_name": "keep", "country": "US"},
                {"channel_name": "drop", "region": "CN"},
            ],
        }

    def annotate(items, *, platform):
        events.append(("annotate", [item["channel_name"] for item in items], platform))
        return [
            {**items[0], "historical_match": True},
            {**items[1], "historical_match": False},
        ]

    async def db_write_fn(callback):
        events.append(("db_write",))
        return callback()

    def persist(items, body, platform, market):
        events.append(
            (
                "persist",
                [item["channel_name"] for item in items],
                body["query"],
                platform,
                market,
            )
        )
        return [41]

    def log_activity(staff, action, **kwargs):
        events.append(("audit", staff, action, kwargs))

    result = asyncio.run(
        execute_platform_search(
            {
                "query": "  portrait creator  ",
                "platform": "YouTube",
                "market": "us",
                "max_results": "12",
                "niche": "portrait",
            },
            staff={"id": 7, "name": "Operator"},
            search_content=search,
            annotate_items=annotate,
            db_write_fn=db_write_fn,
            persist_candidates=persist,
            log_activity=log_activity,
            country_in_excluded_region=_excluded_region,
        )
    )

    assert [item["channel_name"] for item in result["items"]] == ["keep"]
    assert result["candidate_ids"] == [41]
    assert result["saved_candidates"] == 1
    assert events[:5] == [
        ("provider", "youtube", "portrait creator", "US", 12),
        ("db_write",),
        ("annotate", ["keep", "drop"], "youtube"),
        ("db_write",),
        ("persist", ["keep"], "  portrait creator  ", "youtube", "US"),
    ]
    assert events[5] == ("db_write",)
    audit = events[6]
    assert audit[0:3] == ("audit", {"id": 7, "name": "Operator"}, "platform_search")
    assert audit[3]["query"] == "portrait creator"
    assert audit[3]["api_provider"] == "fixture-provider"
    assert audit[3]["result_count"] == 1
    assert audit[3]["metadata"] == {
        "saved_candidates": 1,
        "history_matches": 1,
        "niche": "portrait",
    }


def test_provider_failure_is_stable_retryable_and_redacted(caplog) -> None:
    async def broken(*_args, **_kwargs):
        raise RuntimeError("secret-provider-token")

    with pytest.raises(HTTPException) as raised:
        asyncio.run(
            execute_platform_search(
                {"query": "camera", "platform": "youtube", "market": "US"},
                staff={"id": 7},
                search_content=broken,
                country_in_excluded_region=_excluded_region,
            )
        )

    assert raised.value.status_code == 503
    assert raised.value.detail == {
        "status": "unavailable",
        "reason": "platform_search_unavailable",
        "operation": "platform_search",
        "retryable": True,
    }
    assert "secret-provider-token" not in str(raised.value.detail)
    assert "secret-provider-token" not in caplog.text
    assert "exception_type=RuntimeError" in caplog.text


def _transaction_db() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(
        """
        CREATE TABLE kol_candidates (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            platform TEXT, channel_name TEXT, channel_url TEXT, handle TEXT,
            country TEXT, niche TEXT, source_url TEXT, sample_title TEXT,
            follower_count INTEGER, avg_views INTEGER, contact_email TEXT,
            status TEXT, search_query TEXT, market TEXT, notes TEXT,
            created_at TEXT, updated_at TEXT
        );
        CREATE TABLE kol_activity_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            staff_id INTEGER, user_id INTEGER, staff_name TEXT, action_type TEXT,
            target_type TEXT, target_id INTEGER, query TEXT, platform TEXT,
            market TEXT, api_provider TEXT, api_calls INTEGER,
            result_count INTEGER, metadata_json TEXT, created_at TEXT
        );
        """
    )
    conn.commit()
    return conn


def test_candidate_and_audit_persistence_is_one_transaction(monkeypatch) -> None:
    conn = _transaction_db()
    monkeypatch.setattr(ops_persistence, "get_conn", lambda: conn)

    ids = ops_persistence._persist_platform_search_result(
        [{"channel_name": "Creator", "source_url": "https://example.test/v/1"}],
        {"query": "camera", "niche": "portrait"},
        "youtube",
        "US",
        staff={"id": 7},
        query="camera",
        api_provider="fixture",
    )

    assert ids == [1]
    assert conn.execute("SELECT COUNT(*) FROM kol_candidates").fetchone()[0] == 1
    audit = conn.execute(
        "SELECT action_type, result_count, metadata_json FROM kol_activity_log"
    ).fetchone()
    assert dict(audit) == {
        "action_type": "platform_search",
        "result_count": 1,
        "metadata_json": '{"saved_candidates": 1, "history_matches": 0, "niche": "portrait"}',
    }


def test_audit_failure_rolls_back_candidate_persistence(monkeypatch) -> None:
    conn = _transaction_db()
    monkeypatch.setattr(ops_persistence, "get_conn", lambda: conn)
    monkeypatch.setattr(
        ops_persistence,
        "_log_activity",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("audit failed")),
    )

    with pytest.raises(RuntimeError, match="audit failed"):
        ops_persistence._persist_platform_search_result(
            [{"channel_name": "Creator", "source_url": "https://example.test/v/1"}],
            {"query": "camera"},
            "youtube",
            "US",
            staff={"id": 7},
            query="camera",
            api_provider="fixture",
        )

    assert conn.execute("SELECT COUNT(*) FROM kol_candidates").fetchone()[0] == 0
    assert conn.execute("SELECT COUNT(*) FROM kol_activity_log").fetchone()[0] == 0


def test_durable_worker_does_not_import_kol_http_router() -> None:
    source = (
        Path(__file__).resolve().parents[1]
        / "backend/app/workers/tasks/provider_workflows.py"
    ).read_text(encoding="utf-8")
    assert "from app.api.routers.kol_ops import _execute_platform_search" not in source
    assert "from app.services.kol.platform_search_workflow import execute_platform_search" in source
